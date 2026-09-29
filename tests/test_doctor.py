"""Tests for ``outlooks doctor``: one read-only pass over a repo's wiring."""

import json

import pkgskills
import pytest
from typer.testing import CliRunner

from outlooks import doctor as dr
from outlooks import hook as hk
from outlooks import store
from outlooks.cli import app
from outlooks.host import HOST

runner = CliRunner()

TABLE = """\
[tool.outlooks]
mailbox = "desk@example.org"
system_senders = ["notices@system.example.org"]
"""


def hit(n):
    return {
        "uri": f"mail:///messages/ID{n}?owner=desk%40example.org",
        "id": f"ID{n}",
        "subject": f"message {n}",
        "sender": "someone@example.com",
        "recipients": ["desk@example.org"],
        "receivedDateTime": "2026-03-10T12:00:00.000Z",
        "internetMessageId": f"<m{n}@example.com>",
    }


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A wired repo in the working directory: table, hook, stub, and an archive."""
    for key in ("ARCHIVE_DIR", "CAPTURED_DIR", "SCRATCH_DIR"):
        monkeypatch.delenv(f"OUTLOOKS_{key}")
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text(TABLE, encoding="utf-8")
    hk.apply(tmp_path)
    pkgskills.install(HOST, tmp_path, "local")
    store.save_hits([hit(1), hit(2)], "desk@example.org")
    (tmp_path / "data" / "outlook" / "coverage.csv").write_text(
        "mailbox,after,before,total,messages,pulled\n"
        "desk@example.org,2026-03-10T07:00:00Z,2026-03-12T07:00:00Z,2,2,"
        "2026-09-01T09:00:10-07:00\n",
        encoding="utf-8",
    )
    return tmp_path


def failed(results):
    return {r.check: r.note for r in results if not r.ok}


def files(root):
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_a_wired_repo_passes_every_check_and_nothing_is_written(repo):
    before = files(repo)
    results = dr.run()
    assert [r.check for r in results] == list(dr.CHECKS)
    assert failed(results) == {}
    assert files(repo) == before
    notes = {r.check: r.note for r in results}
    assert notes["profile"] == "(unset)"
    assert notes["archive"].endswith("1 hits files, 1 coverage rows")


def test_cli_doctor_prints_one_line_per_check_and_exits_zero(repo):
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert [line[:26].rstrip() for line in lines[:-1]] == [
        f"{name:20} ok" for name in dr.CHECKS
    ]
    assert lines[-1] == "6 checks, 0 failed"


def test_cli_doctor_exits_one_when_a_check_fails(repo):
    (repo / hk.SCRIPT_PATH).unlink()
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "hook script          FAIL  missing: " in result.output
    assert result.output.splitlines()[-1] == "6 checks, 1 failed"


def test_settings_fail_without_a_table(repo):
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    assert "settings" in failed(dr.run())


def test_settings_fail_on_a_key_that_is_not_a_setting(repo):
    (repo / "pyproject.toml").write_text(
        TABLE + 'archve_dir = "data/mail"\n', encoding="utf-8"
    )
    assert failed(dr.run())["settings"].endswith("not a setting: archve_dir")


def test_settings_fail_on_a_key_that_does_not_parse(repo, monkeypatch):
    monkeypatch.setenv("OUTLOOKS_TIMEZONE", "Nowhere/Land")
    notes = failed(dr.run())
    assert "timezone does not name a zone" in notes["settings"]
    # What needs the settings is reported as not checked, never as ok.
    assert notes["profile"] == notes["archive"]
    assert notes["archive"] == "not checked: the settings do not load"
    assert "hook script" not in notes and "skill stub" not in notes


def test_settings_fail_with_no_mailbox_at_all(repo, monkeypatch):
    monkeypatch.delenv("OUTLOOKS_MAILBOX")
    monkeypatch.delenv("OUTLOOKS_OWN_ADDRESSES")
    (repo / "pyproject.toml").write_text(
        '[tool.outlooks]\ntimezone = "UTC"\n', encoding="utf-8"
    )
    assert "neither mailbox nor own_addresses" in failed(dr.run())["settings"]


def test_profile_fails_when_the_file_it_names_is_missing(repo, monkeypatch):
    monkeypatch.setenv("OUTLOOKS_PROFILE", "docs/outlook-profile.md")
    assert failed(dr.run())["profile"].endswith("outlook-profile.md does not exist")
    (repo / "docs").mkdir()
    (repo / "docs" / "outlook-profile.md").write_text("# Profile\n", encoding="utf-8")
    assert failed(dr.run()) == {}


def test_archive_passes_before_anything_is_archived(repo, monkeypatch, tmp_path):
    monkeypatch.setenv("OUTLOOKS_ARCHIVE_DIR", str(tmp_path / "nothing-yet"))
    assert failed(dr.run()) == {}


def test_archive_fails_on_a_coverage_row_that_does_not_parse(repo):
    table = repo / "data" / "outlook" / "coverage.csv"
    table.write_text(
        table.read_text(encoding="utf-8")
        + "desk@example.org,yesterday,2026-03-12T07:00:00Z,2,2,\n",
        encoding="utf-8",
    )
    assert "coverage.csv, row 2" in failed(dr.run())["archive"]


@pytest.mark.parametrize(
    "row",
    ["desk@example.org,2026-03-12T07:00:00Z\n", "desk@example.org,,,2,2,,extra\n"],
)
def test_archive_fails_on_a_coverage_row_of_another_length(repo, row):
    table = repo / "data" / "outlook" / "coverage.csv"
    table.write_text(table.read_text(encoding="utf-8") + row, encoding="utf-8")
    note = failed(dr.run())["archive"]
    assert "coverage.csv, row 2: it does not have the header's 6 cells" in note


def test_archive_fails_on_coverage_columns_it_does_not_know(repo):
    table = repo / "data" / "outlook" / "coverage.csv"
    table.write_text("mailbox,after,before\n", encoding="utf-8")
    assert "its columns are" in failed(dr.run())["archive"]


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ("{not json", "line 3"),
        (json.dumps({"internetMessageId": "<x>"}), "no 'receivedDateTime'"),
        (json.dumps({"receivedDateTime": "2026-03-10T12:00:00Z"}), "no 'internetMess"),
        (json.dumps({"receivedDateTime": None}), "receivedDateTime is not a string"),
        (json.dumps(["a", "list"]), "not an object"),
    ],
)
def test_archive_fails_on_a_hits_line_that_does_not_parse(repo, line, why):
    path = repo / "data" / "outlook" / "hits" / "desk@example.org" / "2026-03.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + line + "\n", encoding="utf-8")
    note = failed(dr.run())["archive"]
    assert "2026-03.jsonl, line 3" in note and why in note


@pytest.mark.parametrize("state", ["missing", "stale", "differs"])
def test_hook_script_fails_in_every_state_but_ok(repo, state):
    path = repo / hk.SCRIPT_PATH
    if state == "missing":
        path.unlink()
    elif state == "stale":
        body = [
            f"{hk.DIR_LINE}/elsewhere\n" if s.startswith(hk.DIR_LINE) else s
            for s in hk.script(repo).splitlines(keepends=True)
        ]
        path.write_text("".join(body), encoding="utf-8")
    else:
        path.write_text(hk.script(repo) + "# a local tweak\n", encoding="utf-8")
    notes = failed(dr.run())
    assert list(notes) == ["hook script"]
    assert notes["hook script"].startswith(f"{state}: ")


def test_hook_settings_entry_fails_when_missing_or_pointing_elsewhere(repo):
    path = repo / hk.SETTINGS_PATH
    settings = json.loads(path.read_text(encoding="utf-8"))
    settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"] = "./other.sh"
    path.write_text(json.dumps(settings), encoding="utf-8")
    assert list(failed(dr.run())) == ["hook settings entry"]
    path.write_text("{}", encoding="utf-8")
    assert list(failed(dr.run())) == ["hook settings entry"]
    path.write_text("{not json", encoding="utf-8")
    notes = failed(dr.run())
    assert list(notes) == ["hook settings entry"]
    assert "does not parse" in notes["hook settings entry"]


@pytest.mark.parametrize(
    "settings",
    [
        [],
        None,
        {"hooks": []},
        {"hooks": {"PostToolUse": {}}},
        {"hooks": {"PostToolUse": [1]}},
    ],
)
def test_hook_settings_entry_fails_on_settings_of_another_shape(repo, settings):
    (repo / hk.SETTINGS_PATH).write_text(json.dumps(settings), encoding="utf-8")
    notes = failed(dr.run())
    assert list(notes) == ["hook settings entry"]
    assert "does not parse" in notes["hook settings entry"]


def test_hook_settings_entry_is_checked_when_the_settings_do_not_load(repo):
    (repo / "pyproject.toml").write_text("[tool.outlooks\n", encoding="utf-8")
    notes = failed(dr.run())
    assert "settings" in notes and "hook settings entry" not in notes
    (repo / hk.SETTINGS_PATH).write_text("{}", encoding="utf-8")
    assert "no PostToolUse entry" in failed(dr.run())["hook settings entry"]


def test_skill_stub_fails_when_missing_or_edited(repo):
    [stub] = (repo / ".claude" / "skills").rglob("SKILL.md")
    stub.write_text(stub.read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8")
    assert list(failed(dr.run())) == ["skill stub"]
    stub.unlink()
    notes = failed(dr.run())
    assert list(notes) == ["skill stub"]
    assert notes["skill stub"].endswith("run `uv run outlooks install`")


def test_skill_stub_note_names_the_release_that_stamped_it(repo):
    version = HOST.resolved_version()
    notes = {r.check: r.note for r in dr.run()}
    assert notes["skill stub"] == f"current for outlooks {version}"
    [stub] = (repo / ".claude" / "skills").rglob("SKILL.md")
    text = stub.read_text(encoding="utf-8")
    assert f"generated by outlooks {version} " in text
    stub.write_text(
        text.replace(
            f"generated by outlooks {version} ", "generated by outlooks 0.0.1 "
        ),
        encoding="utf-8",
    )
    results = dr.run()
    assert failed(results) == {}
    notes = {r.check: r.note for r in results}
    assert notes["skill stub"] == (
        f"content current for outlooks {version}, stamped by 0.0.1; "
        "`uv run outlooks install` restamps it"
    )
