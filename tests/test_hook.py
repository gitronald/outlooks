"""Tests for the capture hook's status check and installer."""

import json
import os
import subprocess
from pathlib import Path

from outlooks import hook as hk


def test_status_is_missing_when_nothing_is_installed(tmp_path):
    status = hk.status(tmp_path)
    assert status == hk.Status("missing", "missing")
    assert not status.ok


def test_apply_writes_the_script_executable_and_the_settings_entry(tmp_path):
    done = hk.apply(tmp_path)
    assert any("wrote" in line for line in done)
    assert any("settings" in line for line in done)

    script = tmp_path / hk.SCRIPT_PATH
    assert script.read_text(encoding="utf-8") == hk.script(tmp_path)
    assert os.access(script, os.X_OK)

    settings = json.loads((tmp_path / hk.SETTINGS_PATH).read_text(encoding="utf-8"))
    [entry] = settings["hooks"]["PostToolUse"]
    assert entry["matcher"] == hk.MATCHER
    assert entry["hooks"] == [{"type": "command", "command": hk.COMMAND}]

    status = hk.status(tmp_path)
    assert status == hk.Status("ok", "ok")
    assert status.ok


def test_apply_merges_the_entry_without_clobbering_other_hooks(tmp_path):
    settings_path = tmp_path / hk.SETTINGS_PATH
    settings_path.parent.mkdir(parents=True)
    existing = {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "SomeOtherTool",
                    "hooks": [{"type": "command", "command": "echo other"}],
                }
            ],
            "Stop": [{"hooks": [{"type": "command", "command": "echo stop"}]}],
        }
    }
    settings_path.write_text(json.dumps(existing), encoding="utf-8")

    hk.apply(tmp_path)

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings["hooks"]["Stop"] == existing["hooks"]["Stop"]
    matchers = [e["matcher"] for e in settings["hooks"]["PostToolUse"]]
    assert matchers == ["SomeOtherTool", hk.MATCHER]


def test_apply_is_idempotent(tmp_path):
    first = hk.apply(tmp_path)
    assert first  # something was done the first time
    second = hk.apply(tmp_path)
    assert second == []
    assert hk.status(tmp_path).ok


def test_status_differs_when_the_script_was_edited_and_force_rewrites_it(tmp_path):
    hk.apply(tmp_path)
    script = tmp_path / hk.SCRIPT_PATH
    script.write_text(hk.script(tmp_path) + "\n# a local tweak\n", encoding="utf-8")

    status = hk.status(tmp_path)
    assert status.script == "differs"
    assert not status.ok

    # Without --force, a script that differs is left alone (it may carry a
    # local change the operator wants kept).
    assert hk.apply(tmp_path) == []
    assert hk.status(tmp_path).script == "differs"

    done = hk.apply(tmp_path, force=True)
    assert any("wrote" in line for line in done)
    assert script.read_text(encoding="utf-8") == hk.script(tmp_path)
    assert hk.status(tmp_path).ok


def test_apply_keeps_the_settings_files_own_characters(tmp_path):
    settings_path = tmp_path / hk.SETTINGS_PATH
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({"env": {"GREETING": "café"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    hk.apply(tmp_path)

    text = settings_path.read_text(encoding="utf-8")
    assert "café" in text and "\\u00e9" not in text
    assert hk.status(tmp_path).ok


def test_apply_fills_a_null_hook_list(tmp_path):
    settings_path = tmp_path / hk.SETTINGS_PATH
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({"hooks": {"PostToolUse": None, "Stop": []}}), encoding="utf-8"
    )
    assert hk.status(tmp_path).settings == "missing"

    hk.apply(tmp_path)

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert [e["matcher"] for e in settings["hooks"]["PostToolUse"]] == [hk.MATCHER]
    assert settings["hooks"]["Stop"] == []


def _configure(tmp_path, captured_dir):
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.outlooks]\ncaptured_dir = "{captured_dir}"\n', encoding="utf-8"
    )


def _run(script, tmp_path, monkeypatch, payload='{"tool_name": "x"}'):
    monkeypatch.delenv("OUTLOOKS_CAPTURED_DIR")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    subprocess.run(["bash", str(script)], input=payload, text=True, check=True)


def test_script_writes_to_the_default_directory(tmp_path, monkeypatch):
    hk.apply(tmp_path)
    _run(tmp_path / hk.SCRIPT_PATH, tmp_path, monkeypatch)
    [capture] = (tmp_path / "temp/outlook/captured").glob("*.json")
    assert capture.read_text(encoding="utf-8") == '{"tool_name": "x"}'


def test_script_writes_to_the_configured_directory(tmp_path, monkeypatch):
    _configure(tmp_path, "scratch/it's here/captured")
    hk.apply(tmp_path)
    _run(tmp_path / hk.SCRIPT_PATH, tmp_path, monkeypatch)
    assert len(list((tmp_path / "scratch/it's here/captured").glob("*.json"))) == 1
    assert not (tmp_path / "temp").exists()


def test_script_names_a_directory_outside_the_repo_in_full(tmp_path):
    outside = tmp_path.parent / "elsewhere" / "captured"
    repo = tmp_path / "repo"
    repo.mkdir()
    _configure(tmp_path, str(outside))
    assert f"{hk.DIR_LINE}{outside}\n" in hk.script(repo)


def test_script_takes_the_table_value_not_the_environment(tmp_path, monkeypatch):
    # The script reads OUTLOOKS_CAPTURED_DIR itself when it runs.
    monkeypatch.setenv("OUTLOOKS_CAPTURED_DIR", str(tmp_path / "from-env"))
    assert "from-env" not in hk.script(tmp_path)


def test_a_script_written_for_another_directory_is_stale_and_rewritten(tmp_path):
    hk.apply(tmp_path)
    _configure(tmp_path, "scratch/captured")

    status = hk.status(tmp_path)
    assert status.script == "stale"
    assert not status.ok

    done = hk.apply(tmp_path)
    assert done == [f"wrote {hk.SCRIPT_PATH}"]
    assert hk.status(tmp_path).ok


def test_an_earlier_build_script_is_stale_and_rewritten(tmp_path):
    # A pre-release build's script, for any directory, carries no local change.
    earlier = Path(__file__).parent / "fixtures" / "hook-prerelease.sh"
    hk.apply(tmp_path)
    script = tmp_path / hk.SCRIPT_PATH
    for target in ('"${CLAUDE_PROJECT_DIR:-.}"/.archive/captured', "/elsewhere"):
        text = earlier.read_text(encoding="utf-8").replace("@CAPTURED_DIR@", target)
        script.write_text(text, encoding="utf-8")
        assert hk.status(tmp_path).script == "stale"
        assert hk.apply(tmp_path) == [f"wrote {hk.SCRIPT_PATH}"]
        assert hk.status(tmp_path).ok
    # Edited after it was written, it differs like any other.
    script.write_text(text + "\n# a local tweak\n", encoding="utf-8")
    assert hk.status(tmp_path).script == "differs"


def test_advice_for_a_differing_script_says_what_apply_still_does(tmp_path):
    hk.apply(tmp_path)
    script = tmp_path / hk.SCRIPT_PATH
    script.write_text(hk.script(tmp_path) + "# a local tweak\n", encoding="utf-8")
    (tmp_path / hk.SETTINGS_PATH).unlink()

    status = hk.status(tmp_path)
    assert status == hk.Status("differs", "missing")
    assert "--apply` adds the missing settings entry but leaves it alone" in (
        status.advice
    )
    assert "--apply --force` overwrites it" in status.advice
    assert hk.apply(tmp_path) == [f"added the PostToolUse entry to {hk.SETTINGS_PATH}"]
    assert "--apply` leaves it alone" in hk.status(tmp_path).advice


def test_a_stale_script_with_a_local_change_differs(tmp_path):
    hk.apply(tmp_path)
    script = tmp_path / hk.SCRIPT_PATH
    script.write_text(hk.script(tmp_path) + "\n# a local tweak\n", encoding="utf-8")
    _configure(tmp_path, "scratch/captured")
    assert hk.status(tmp_path).script == "differs"
    assert hk.apply(tmp_path) == []
