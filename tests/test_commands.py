"""CliRunner tests for the commands, through the typer app.

``render``, ``import``, and ``coverage`` are exercised in depth in
test_render.py and test_coverage.py; this covers every other command, and the
``Error:`` line each gives for input its module refuses.
"""

import json
import os
from datetime import datetime

from typer.testing import CliRunner

from outlooks import config, store
from outlooks import hook as hk
from outlooks.cli import app

runner = CliRunner()

BOX = "desk@example.org"
SEARCH = "mcp__claude_ai_Microsoft_365__outlook_email_search"
READ = "mcp__claude_ai_Microsoft_365__read_resource"


def write_capture(stamp, tool, args, *items):
    folder = config.captured_dir()
    folder.mkdir(parents=True, exist_ok=True)
    blocks = [
        {"type": "text", "text": i if isinstance(i, str) else json.dumps(i)}
        for i in items
    ]
    path = folder / f"{stamp}-1.json"
    path.write_text(
        json.dumps(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": tool,
                "tool_input": args,
                "tool_response": blocks,
            }
        ),
        encoding="utf-8",
    )
    # Dated as the hook would have written it: at its stamp.
    when = datetime.strptime(stamp, "%Y%m%dT%H%M%S").replace(tzinfo=config.zone())
    os.utime(path, (when.timestamp(), when.timestamp()))


def window_args(offset=0, after="2026-03-10T07:00:00Z", before="2026-03-12T07:00:00Z"):
    return {
        "mailboxOwnerEmail": BOX,
        "afterDateTime": after,
        "beforeDateTime": before,
        "limit": 2,
        "offset": offset,
    }


def hit(n, offset=0):
    return {
        "uri": f"mail:///messages/ID{n}?owner=desk%40example.org",
        "id": f"ID{n}",
        "subject": f"message {n}",
        "sender": "someone@example.com",
        "recipients": [BOX],
        "receivedDateTime": "2026-03-10T12:00:00.000Z",
        "summary": "text",
        "internetMessageId": f"<m{n}@example.com>",
        "isRead": True,
        "offset": offset,
    }


def trailer(total, next_offset=None):
    return {
        "moreResults": next_offset is not None,
        "nextOffset": next_offset,
        "totalResultCount": total,
    }


def pull_the_window():
    """A complete two-page pull of 2026-03-10 to 2026-03-12."""
    write_capture("20260901T090000", SEARCH, window_args(0), hit(1, 0), trailer(2, 1))
    write_capture("20260901T090010", SEARCH, window_args(1), hit(2, 1), trailer(2))


def test_cli_hash_matches_store_id_hash():
    mid = "<abc@example.com>"
    result = runner.invoke(app, ["hash", mid])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == store.id_hash(mid)


def test_cli_config_prints_the_effective_settings():
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0, result.output
    assert "mailbox" in result.output
    assert "desk@example.org" in result.output
    assert "render_label    Message  [default]" in result.output


def test_cli_split_and_check_round_trip(tmp_path):
    scratch = config.scratch_dir()
    scratch.mkdir(parents=True, exist_ok=True)
    hit = {
        "uri": "mail:///messages/M1?owner=desk%40example.org",
        "internetMessageId": "<cli-1@example.com>",
        "sender": "priya.nakamura@example.com",
        "recipients": ["desk@example.org"],
        "subject": "Inquiry",
        "summary": "hello",
        "receivedDateTime": "2026-01-01T00:00:00.000Z",
    }
    (scratch / "cli-page-01.json").write_text(json.dumps([hit]), encoding="utf-8")
    # Archive the message so split writes a timeline file check() can read back.
    store.save_message(
        {
            "internetMessageId": hit["internetMessageId"],
            "receivedDateTime": hit["receivedDateTime"],
            "subject": hit["subject"],
            "sender": {"address": hit["sender"]},
            "toRecipients": [{"address": r} for r in hit["recipients"]],
            "body": {"contentType": "text", "content": "hello"},
            "attachments": [],
        }
    )

    split_result = runner.invoke(app, ["split", "cli", "--match", "priya"])
    assert split_result.exit_code == 0, split_result.output
    assert "1 hits matched" in split_result.output
    assert (scratch / "cli-01.timeline.json").exists()

    check_result = runner.invoke(app, ["check", "cli"])
    assert check_result.exit_code == 0, check_result.output
    assert "01" in check_result.output and "ok" in check_result.output


def test_cli_check_exits_nonzero_with_no_timeline_files():
    result = runner.invoke(app, ["check", "never-run"])
    assert result.exit_code == 1
    assert "no never-run-NN.timeline.json files" in result.output


# --- worklist, totals, audit, save ------------------------------------------------


def test_cli_worklist_writes_the_batches_and_lists_what_to_read(tmp_path):
    pull_the_window()
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "worklist",
            "--after",
            "2026-03-10",
            "--before",
            "2026-03-12",
            "--out",
            str(out),
            "--per",
            "1",
            "--name",
            "mar",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "2 messages listed by window captures, 2 to read" in result.output
    assert f"2 batch files in {out}" in result.output
    assert (out / "batch-01.txt").read_text(encoding="utf-8").startswith("mar-001 ")


def test_cli_worklist_refuses_a_malformed_date(tmp_path):
    result = runner.invoke(
        app,
        ["worklist", "--after", "2026-3-10", "--before", "2026-03-12", "--out", "out"],
    )
    assert result.exit_code == 1
    assert "Error: '2026-3-10' is not a YYYY-MM-DD date" in result.output
    assert not (tmp_path / "out").exists()


def test_cli_totals_lists_window_sizes_by_prefix():
    pull_the_window()
    result = runner.invoke(app, ["totals", "2026-03"])
    assert result.exit_code == 0, result.output
    assert result.output.split() == [
        "2026-03-10T07:00:00Z",
        "2026-03-12T07:00:00Z",
        "2",
    ]
    assert runner.invoke(app, ["totals", "2025"]).output == ""


def test_cli_totals_check_runs_the_item_id_proof():
    pull_the_window()
    bounds = ["2026-03-10T07:00:00Z", "2026-03-12T07:00:00Z"]
    result = runner.invoke(app, ["totals", "--check", *bounds])
    assert result.exit_code == 0, result.output
    # windows counts the captures: both pages of the one pull
    assert "baseline 2 ids 2 messages 2 windows 2" in result.output
    assert result.output.rstrip().endswith("ok")


def test_cli_totals_check_without_a_baseline_exits_nonzero():
    result = runner.invoke(
        app, ["totals", "--check", "2026-03-10T07:00:00Z", "2026-03-12T07:00:00Z"]
    )
    assert result.exit_code == 1
    assert "no capture of the whole range" in result.output


def test_cli_totals_check_refuses_a_malformed_timestamp():
    result = runner.invoke(
        app, ["totals", "--check", "2025-13-01T00:00:00Z", "2026-01-01T00:00:00Z"]
    )
    assert result.exit_code == 1
    assert result.output.startswith("Error: ")


def test_cli_coverage_refuses_a_malformed_since():
    result = runner.invoke(app, ["coverage", "--since", "nope"])
    assert result.exit_code == 1
    assert "Error: 'nope' is not a YYYY-MM-DD date" in result.output


def message(n, attachments=()):
    return {
        "id": f"ID{n}",
        "subject": f"message {n}",
        "body": {"contentType": "html", "content": f"<p>body {n}</p>"},
        "sender": {"name": "Someone", "address": "someone@example.com"},
        "receivedDateTime": "2026-03-10T12:00:00.000Z",
        "attachments": list(attachments),
        "internetMessageId": f"<m{n}@example.com>",
        "isRead": False,
    }


def test_cli_audit_lists_the_missing_texts_and_writes_the_sweep(tmp_path):
    uri = f"mail:///messages/M1%3D/attachments/A1?owner={BOX}"
    attachment = {"name": "notes.pdf", "contentType": "application/pdf", "uri": uri}
    store.save_message(message(1, [attachment]))
    key = store.id_hash("<m1@example.com>")
    sweep = tmp_path / "sweep.txt"

    result = runner.invoke(app, ["audit", "2026-03", "--sweep", str(sweep)])

    assert result.exit_code == 0, result.output
    assert "missing by type: {'application/pdf': 1}" in result.output
    assert f"MISSING {key}.1" in result.output
    assert "never attempted: 1" in result.output
    assert f"UNTRIED {key}.1 application/pdf" in result.output
    assert "text in an image slot: none" in result.output
    assert sweep.read_text(encoding="utf-8") == f"att-{key}.1 {uri}\n"


def test_cli_save_archives_a_message_once_and_a_page_of_hits(tmp_path):
    payload = tmp_path / "message.json"
    payload.write_text(json.dumps(message(1)), encoding="utf-8")
    first = runner.invoke(app, ["save", str(payload)])
    assert first.exit_code == 0, first.output
    assert first.output.startswith("Wrote ")
    assert runner.invoke(app, ["save", str(payload)]).output.startswith("Unchanged ")

    page = tmp_path / "hits.json"
    page.write_text(json.dumps([hit(1), hit(2)]), encoding="utf-8")
    saved = runner.invoke(app, ["save", str(page), "--hits"])
    assert saved.exit_code == 0, saved.output
    assert "hits: 2 added, 0 known" in saved.output
    assert len(store.load_hits()) == 2


def test_cli_save_refuses_a_message_where_hits_are_expected(tmp_path):
    payload = tmp_path / "message.json"
    payload.write_text(json.dumps(message(1)), encoding="utf-8")
    result = runner.invoke(app, ["save", str(payload), "--hits"])
    assert result.exit_code == 1
    assert "Error: --hits takes a JSON list of search hits" in result.output


# --- hook -------------------------------------------------------------------------


def test_cli_hook_checks_then_applies_in_the_working_repo(tmp_path):
    # The command takes its root from the working directory, which conftest
    # sets to tmp_path; the .git makes that the repo root, never a parent.
    (tmp_path / ".git").mkdir()

    missing = runner.invoke(app, ["hook"])
    assert missing.exit_code == 1
    assert "script    missing" in missing.output
    assert not (tmp_path / hk.SCRIPT_PATH).exists()

    applied = runner.invoke(app, ["hook", "--apply"])
    assert applied.exit_code == 0, applied.output
    assert f"wrote {hk.SCRIPT_PATH}" in applied.output
    assert "settings  ok" in applied.output
    assert hk.status(tmp_path).ok

    again = runner.invoke(app, ["hook", "--apply"])
    assert again.exit_code == 0, again.output
    assert "nothing to do" in again.output


def test_cli_hook_says_how_to_replace_a_script_that_differs(tmp_path):
    (tmp_path / ".git").mkdir()
    hk.apply(tmp_path)
    script = tmp_path / hk.SCRIPT_PATH
    script.write_text(hk.script(tmp_path) + "# a local tweak\n", encoding="utf-8")

    kept = runner.invoke(app, ["hook", "--apply"])
    assert kept.exit_code == 1
    assert "nothing to do" in kept.output
    assert "--apply --force` overwrites it" in kept.output

    forced = runner.invoke(app, ["hook", "--apply", "--force"])
    assert forced.exit_code == 0, forced.output
    assert "force" not in forced.output.replace(f"wrote {hk.SCRIPT_PATH}", "")


# --- windows ----------------------------------------------------------------------


def test_cli_windows_plans_sizes_and_batches_a_range():
    init = runner.invoke(
        app, ["windows", "init", "2026-03-10T07:00:00Z", "2026-03-12T07:00:00Z"]
    )
    assert init.exit_code == 0, init.output
    assert init.output.splitlines() == [
        "range 2026-03-10T07:00:00Z 2026-03-12T07:00:00Z",
        "2026-03-10T07:00:00Z 2026-03-12T07:00:00Z",
    ]
    assert (config.scratch_dir() / "windows" / "windows.json").is_file()

    unsized = runner.invoke(app, ["windows", "fill"])
    assert unsized.exit_code == 0, unsized.output
    assert "pieces 1 sum 0 unknown 1 over-LIMIT 0" in unsized.output

    not_ready = runner.invoke(app, ["windows", "batches"])
    assert not_ready.exit_code == 1
    assert "Error: not ready to page" in not_ready.output

    pull_the_window()
    sized = runner.invoke(app, ["windows", "fill"])
    assert sized.exit_code == 0, sized.output
    assert "range 2026-03-10T07:00:00Z 2026-03-12T07:00:00Z 2" in sized.output
    assert "pieces 1 sum 2 unknown 0 over-LIMIT 0" in sized.output

    split = runner.invoke(app, ["windows", "split"])
    assert split.exit_code == 0, split.output
    assert split.output == ""  # nothing over the limit

    batches = runner.invoke(app, ["windows", "batches"])
    assert batches.exit_code == 0, batches.output
    assert "1 batches, 1 windows, 1 pages" in batches.output
    assert (config.scratch_dir() / "windows" / "pager-01.txt").is_file()

    remaining = runner.invoke(app, ["windows", "remaining"])
    assert remaining.exit_code == 0, remaining.output
    assert "2026-03-10T07:00:00Z 2026-03-12T07:00:00Z 2" in remaining.output
    assert "1 pieces not covered" in remaining.output


def test_cli_windows_init_refuses_a_second_plan_and_a_malformed_bound():
    bad = runner.invoke(app, ["windows", "init", "2026-3-10"])
    assert bad.exit_code == 1
    assert "Error: '2026-3-10' is not a YYYY-MM-DD date or a timestamp" in bad.output
    assert not (config.scratch_dir() / "windows").exists()

    reversed_range = runner.invoke(app, ["windows", "init", "2026-03-12", "2026-03-10"])
    assert reversed_range.exit_code == 1
    assert "Error: after" in reversed_range.output

    assert runner.invoke(app, ["windows", "init", "2026-03-10"]).exit_code == 0
    second = runner.invoke(app, ["windows", "init", "2026-03-10"])
    assert second.exit_code == 1
    assert "windows.json already exists" in second.output


def test_cli_windows_commands_refuse_when_no_plan_was_started():
    for command in ("fill", "split", "batches", "remaining"):
        result = runner.invoke(app, ["windows", command])
        assert result.exit_code == 1, command
        assert "Error: no plan at" in result.output, command
