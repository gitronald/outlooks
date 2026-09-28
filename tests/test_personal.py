"""The signed-in account's own mailbox: ``mailbox`` unset, ``own_addresses`` set.

Its searches pass no ``mailboxOwnerEmail``, so a capture may name no mailbox at
all (an empty page, a uri with no ``owner``). The archive files under the first
own address. Its pages report no ``totalResultCount`` and its hits no
recipients: a full page says more follows, and a pull ends at a short page or
at the empty page after a full one.
"""

import json

import pytest
from typer.testing import CliRunner

from outlooks import capture as cp
from outlooks import config, store
from outlooks import coverage as cv
from outlooks import lookup as lk
from outlooks import sizing as sz
from outlooks.cli import app

ME = "me@example.org"
ALIAS = "me.alias@example.org"
SEARCH = "mcp__claude_ai_Microsoft_365__outlook_email_search"
READ = "mcp__claude_ai_Microsoft_365__read_resource"
WINDOW = {
    "afterDateTime": "2026-03-01T08:00:00Z",
    "beforeDateTime": "2026-04-01T07:00:00Z",
    "limit": 25,
    "offset": 0,
}

runner = CliRunner()


@pytest.fixture
def personal(monkeypatch):
    monkeypatch.delenv("OUTLOOKS_MAILBOX")
    monkeypatch.setenv("OUTLOOKS_OWN_ADDRESSES", f"{ME},{ALIAS}")


def hit(n, owner=None):
    query = f"?owner={owner.replace('@', '%40')}" if owner else ""
    return {
        "uri": f"mail:///messages/ID{n}{query}",
        "id": f"ID{n}",
        "subject": f"message {n}",
        "sender": "someone@example.com",
        "recipients": None,
        "receivedDateTime": "2026-03-10T12:00:00.000Z",
        "summary": "text",
        "internetMessageId": f"<m{n}@example.com>",
        "offset": n - 1,
    }


def message(n):
    return {
        "id": f"ID{n}",
        "subject": f"message {n}",
        "body": {"contentType": "html", "content": f"<p>body {n}</p>"},
        "sender": {"name": "Someone", "address": "someone@example.com"},
        "receivedDateTime": "2026-03-10T12:00:00.000Z",
        "attachments": [],
        "internetMessageId": f"<m{n}@example.com>",
    }


def write_capture(folder, stamp, tool, args, *items):
    folder.mkdir(parents=True, exist_ok=True)
    response = [{"type": "text", "text": json.dumps(i)} for i in items]
    (folder / f"{stamp}-1.json").write_text(
        json.dumps(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": tool,
                "tool_input": args,
                "tool_response": response,
            }
        ),
        encoding="utf-8",
    )


def total(n):
    return {"moreResults": False, "nextOffset": None, "totalResultCount": n}


def test_the_archive_files_under_the_first_own_address(personal, monkeypatch):
    assert config.settings().mailbox is None
    assert config.mailbox() == ME
    assert ("mailbox", f"(unset: the signed-in account, archived as {ME})") in [
        (key, value) for key, value, _ in config.describe()
    ]
    monkeypatch.delenv("OUTLOOKS_OWN_ADDRESSES")
    with pytest.raises(config.ConfigError, match="own_addresses"):
        config.mailbox()


def test_import_files_a_pull_that_names_no_mailbox(personal, tmp_path):
    folder = tmp_path / "captured"
    write_capture(folder, "20260913T100000", SEARCH, WINDOW, hit(1), hit(2), total(2))
    write_capture(
        folder, "20260913T100100", READ, {"uri": "mail:///messages/ID1"}, message(1)
    )
    captures, _ = cp.load_captures([folder])
    assert [c.mailbox for c in captures] == [None, None]

    done = cp.import_captures(captures)
    assert (done.hits_added, done.messages_written, done.other_mailbox) == (2, 1, 0)
    assert {h["mailbox"] for h in store.load_hits().values()} == {ME}
    [w] = cv.windows(captures, config.mailbox())
    assert w.complete and w.mailbox == ME
    [row] = cv.record([w])
    assert (row["mailbox"], row["total"]) == (ME, "2")


def test_import_files_a_pull_that_names_any_own_address(personal, tmp_path):
    folder = tmp_path / "captured"
    write_capture(folder, "20260913T100000", SEARCH, WINDOW, hit(1, ALIAS), total(1))
    write_capture(
        folder,
        "20260913T100100",
        SEARCH,
        WINDOW,
        {**hit(2, "else@example.org"), "offset": 0},
        total(1),
    )
    captures, _ = cp.load_captures([folder])
    done = cp.import_captures(captures)
    assert (done.hits_added, done.other_mailbox) == (1, 1)
    assert list(store.load_hits()) == ["<m1@example.com>"]


def test_an_empty_pull_that_names_no_mailbox_is_covered(personal, tmp_path):
    folder = tmp_path / "captured"
    write_capture(folder, "20260913T100000", SEARCH, WINDOW)
    captures, _ = cp.load_captures([folder])
    [w] = cv.windows(captures, config.mailbox())
    assert w.complete and w.total == 0


def test_a_configured_mailbox_takes_no_capture_that_names_none(tmp_path):
    # A search that left out mailboxOwnerEmail read the operator's own account,
    # not the configured mailbox: it is never filed under it.
    folder = tmp_path / "captured"
    write_capture(folder, "20260913T100000", SEARCH, WINDOW, hit(1), total(1))
    captures, _ = cp.load_captures([folder])
    done = cp.import_captures(captures)
    assert (done.hits_added, done.other_mailbox) == (0, 1)
    assert cv.windows(captures, config.mailbox()) == []


def test_the_commands_run_with_no_mailbox_configured(personal, tmp_path):
    folder = tmp_path / "captured"
    write_capture(folder, "20260913T100000", SEARCH, WINDOW, hit(1), total(1))

    result = runner.invoke(app, ["import"])
    assert result.exit_code == 0, result.output
    assert "COVERED" in result.output

    result = runner.invoke(app, ["coverage"])
    assert result.exit_code == 0, result.output
    assert f"coverage for {ME}" in result.output


# --- pulls that report no total -------------------------------------------------


def more(next_offset):
    return {"moreResults": True, "nextOffset": next_offset}


def page(offset, limit=2):
    return {**WINDOW, "limit": limit, "offset": offset}


def pull(folder, *pages):
    for n, (args, items) in enumerate(pages):
        write_capture(folder, f"20260913T1000{n:02}", SEARCH, args, *items)
    captures, _ = cp.load_captures([folder])
    return captures


def test_a_pull_with_no_total_ends_at_a_short_page(personal, tmp_path):
    captures = pull(
        tmp_path / "captured",
        (page(0), [hit(1), hit(2), more(2)]),
        (page(2), [hit(3)]),
    )
    [w] = cv.windows(captures, ME)
    assert (w.total, w.complete, len(w.messages)) == (3, True, 3)
    assert sz.window_sizes(captures, ME) == {(w.after, w.before): 3}


def test_a_pull_with_no_total_ends_at_the_empty_page_after_a_full_one(
    personal, tmp_path
):
    captures = pull(
        tmp_path / "captured",
        (page(0), [hit(1), hit(2), more(2)]),
        (page(2), []),
    )
    [w] = cv.windows(captures, ME)
    assert (w.total, w.complete) == (2, True)


def test_a_pull_with_no_total_is_incomplete_until_its_last_page(personal, tmp_path):
    captures = pull(tmp_path / "captured", (page(0), [hit(1), hit(2), more(2)]))
    [w] = cv.windows(captures, ME)
    assert (w.total, w.complete) == (None, False)
    # One page of a window says nothing about its size.
    assert sz.window_sizes(captures, ME) == {}

    result = runner.invoke(app, ["import"])
    assert result.exit_code == 0, result.output
    assert "2 offsets captured, its last page missing" in result.output
    assert "COVERED" not in result.output


def test_a_pull_with_no_total_and_a_page_missing_is_incomplete(personal, tmp_path):
    captures = pull(
        tmp_path / "captured",
        (page(0), [hit(1), hit(2), more(2)]),
        (page(4), [hit(5)]),
    )
    [w] = cv.windows(captures, ME)
    assert (w.total, w.complete) == (5, False)


def test_split_takes_hits_that_list_no_recipients(personal, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    system = {**hit(3), "sender": "notices@system.example.org"}
    (scratch / "someone-page-01.json").write_text(
        json.dumps([hit(1), {**hit(2), "sender": "other@example.com"}, system]),
        encoding="utf-8",
    )
    result = lk.split("someone", ["someone"], match_summary=False)
    assert (result.pages, result.total) == (1, 1)
