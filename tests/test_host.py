"""The skill declaration: spec conformance and resolvable commands."""

import pytest
from pkgskills.testing import assert_prompt_commands, assert_spec_conformant

from outlooks import hook as hk
from outlooks import host
from outlooks.cli import app
from outlooks.host import HOST


def test_host_is_spec_conformant():
    assert_spec_conformant(HOST)


def test_every_prompt_command_resolves():
    assert_prompt_commands(HOST, app)


def _wire(root, script):
    hk.apply(root)
    path = root / hk.SCRIPT_PATH
    if script == "missing":
        path.unlink()
    elif script == "stale":
        lines = hk.script(root).splitlines(keepends=True)
        body = [
            f"{hk.DIR_LINE}/elsewhere\n" if s.startswith(hk.DIR_LINE) else s
            for s in lines
        ]
        path.write_text("".join(body), encoding="utf-8")
    elif script == "differs":
        path.write_text(hk.script(root) + "# a local tweak\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("script", "note"),
    [
        ("ok", ""),
        ("missing", "run `uv run outlooks hook --apply`"),
        ("stale", "run `uv run outlooks hook --apply`"),
        ("differs", "`uv run outlooks hook --apply --force` overwrites it"),
    ],
)
def test_install_check_notes_what_each_script_state_needs(tmp_path, script, note):
    _wire(tmp_path, script)
    [check] = host._extra_checks(HOST, tmp_path, None)
    assert hk.status(tmp_path).script == script
    assert not check.gates
    assert check.note.endswith(note) if note else check.note == ""
    if script == "differs":
        assert "`uv run outlooks hook --apply` leaves it alone" in check.note


def test_after_install_says_force_for_a_script_that_differs(tmp_path, capsys):
    _wire(tmp_path, "differs")
    host._after_install(type("Report", (), {"root": tmp_path})())  # type: ignore[arg-type]
    err = capsys.readouterr().err
    assert "(script differs, settings ok)" in err
    assert "--apply --force` overwrites it" in err
