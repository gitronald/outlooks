"""Tests for importing hook captures and recording window coverage.

Captures are built here in the hook's own shape: ``tool_input`` plus a
``tool_response`` of text blocks, one hit per block and a paging trailer last.
"""

import csv
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from outlooks import capture as cp
from outlooks import config, store
from outlooks import coverage as cv
from outlooks import hook as hk
from outlooks.cli import app

BOX = "desk@example.org"
PACIFIC = ZoneInfo("America/Los_Angeles")
SEARCH = "mcp__claude_ai_Microsoft_365__outlook_email_search"
READ = "mcp__claude_ai_Microsoft_365__read_resource"


def saved_file(tmp_path, name, content):
    """A file where Claude Code saves an oversized result, holding ``content``."""
    saved = tmp_path / "claude" / "projects" / "p" / "tool-results" / name
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(json.dumps(content), encoding="utf-8")
    return saved


def hit(n, received="2026-03-10T12:00:00.000Z", offset=0, copy="a", **extra):
    return {
        "uri": f"mail:///messages/ID{n}{copy}?owner=desk%40example.org",
        "id": f"ID{n}{copy}",
        "subject": f"message {n}",
        "sender": "someone@example.com",
        "recipients": [BOX],
        "receivedDateTime": received,
        "summary": "text",
        "internetMessageId": f"<m{n}@example.com>",
        "isRead": True,
        "offset": offset,
        **extra,
    }


def message(n, attachments=()):
    return {
        "id": f"ID{n}a",
        "subject": f"message {n}",
        "body": {"contentType": "html", "content": f"<p>body {n}</p>"},
        "sender": {"name": "Someone", "address": "someone@example.com"},
        "receivedDateTime": "2026-03-10T12:00:00.000Z",
        "attachments": list(attachments),
        "internetMessageId": f"<m{n}@example.com>",
        "isRead": False,
    }


def blocks(*items):
    return [
        {"type": "text", "text": i if isinstance(i, str) else json.dumps(i)}
        for i in items
    ]


def write_capture(folder, stamp, tool, args, *items):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stamp}-1.json"
    path.write_text(
        json.dumps(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": tool,
                "tool_input": args,
                "tool_response": blocks(*items),
            }
        ),
        encoding="utf-8",
    )
    return path


def window_args(offset=0, **extra):
    return {
        "mailboxOwnerEmail": BOX,
        "afterDateTime": "2026-03-01T00:00:00-08:00",
        "beforeDateTime": "2026-04-01T00:00:00-07:00",
        "limit": 2,
        "offset": offset,
        **extra,
    }


def trailer(total, next_offset=None):
    return {
        "moreResults": next_offset is not None,
        "nextOffset": next_offset,
        "totalResultCount": total,
    }


@pytest.fixture
def pulled(tmp_path):
    """A complete three-message window pull in two pages."""
    folder = tmp_path / "captured"
    write_capture(
        folder,
        "20260913T100000",
        SEARCH,
        window_args(0),
        hit(1, offset=0),
        hit(2, offset=1),
        trailer(3, 2),
    )
    write_capture(
        folder, "20260913T100010", SEARCH, window_args(2), hit(3, offset=2), trailer(3)
    )
    return folder


# --- reading captures -----------------------------------------------------------


def test_read_capture_parses_a_search_page(pulled):
    c = cp.read_capture(sorted(pulled.glob("*.json"))[0])
    assert c is not None
    assert c.tool == "search" and c.mailbox == BOX
    assert [h["offset"] for h in c.hits] == [0, 1] and c.total == 3
    assert c.at == datetime(2026, 9, 13, 10, 0, tzinfo=PACIFIC)


def test_load_captures_skips_files_that_are_not_captures(pulled):
    (pulled / "note.json").write_text('{"id": "x"}', encoding="utf-8")
    captures, other = cp.load_captures([pulled])
    assert len(captures) == 2 and [p.name for p in other] == ["note.json"]


def test_read_mailbox_comes_from_the_uri_owner(tmp_path):
    uri = f"mail:///messages/ID1a?owner={BOX}"
    c = cp.read_capture(
        write_capture(tmp_path, "20260913T1", READ, {"uri": uri}, message(1))
    )
    assert c is not None
    assert c.message is not None
    assert c.mailbox == BOX and c.message["subject"] == "message 1"
    mine = cp.read_capture(
        write_capture(
            tmp_path, "20260913T2", READ, {"uri": "mail:///messages/X"}, message(2)
        )
    )
    assert mine is not None
    assert mine.mailbox is None


def test_an_oversized_read_is_taken_from_the_file_it_was_saved_to(tmp_path):
    saved = saved_file(tmp_path, "read_resource-1.txt", message(1))
    note = (
        "Error: result (58,173 characters across 1 line) exceeds maximum allowed "
        f"tokens. Output has been saved to {saved}.\nFormat: Plain text"
    )
    path = tmp_path / "20260925T1-1.json"
    path.write_text(
        json.dumps(
            {
                "tool_name": READ,
                "tool_input": {"uri": f"mail:///messages/X?owner={BOX}"},
                "tool_response": note,
            }
        ),
        encoding="utf-8",
    )
    read1 = cp.read_capture(path)
    assert read1 is not None
    assert read1.message is not None
    assert read1.message["subject"] == "message 1"
    saved.unlink()
    read2 = cp.read_capture(path)
    assert read2 is not None
    assert read2.message is None


# --- import ---------------------------------------------------------------------


def test_import_archives_hits_and_reads_and_is_idempotent(pulled, tmp_path):
    root = tmp_path / "archive"
    uri = f"mail:///messages/ID1a?owner={BOX}"
    write_capture(pulled, "20260913T100020", READ, {"uri": uri}, message(1))
    other = {**window_args(0), "mailboxOwnerEmail": "me@example.com"}
    write_capture(pulled, "20260913T100030", SEARCH, other, hit(9), trailer(1))
    captures, _ = cp.load_captures([pulled])

    done = cp.import_captures(captures, root=root)
    assert (done.hits_added, done.messages_written, done.other_mailbox) == (3, 1, 1)
    assert "<m9@example.com>" not in store.load_hits(root)

    again = cp.import_captures(captures, root=root)
    assert (again.hits_added, again.hits_known, again.messages_unchanged) == (0, 3, 1)


def test_two_captures_of_one_call_import_once(tmp_path):
    folder = config.captured_dir()
    paths = [
        write_capture(folder, stamp, SEARCH, window_args(0), hit(1), trailer(1))
        for stamp in ("20260913T100000", "20260913T100001")
    ]
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**data, "tool_use_id": "toolu_1"}), "utf-8")
    captures, _ = cp.load_captures([folder])

    done = cp.import_captures(captures)
    assert done.duplicates == [paths[1]]
    assert (done.hits_added, done.hits_known) == (1, 0)

    result = CliRunner().invoke(app, ["import"])
    assert result.exit_code == 0, result.output
    assert "  1 DUPLICATE captures of a call already captured" in result.output


def test_save_hits_knows_a_message_filed_under_another_month(tmp_path):
    store.save_hits([hit(1, received="2025-03-10T12:00:00.000Z")], BOX, root=tmp_path)
    counts = store.save_hits([hit(1)], BOX, root=tmp_path)
    assert counts == {"added": 0, "known": 1}
    assert not (tmp_path / "hits" / BOX / "2026-03.jsonl").exists()


def test_import_lists_a_differing_read_and_replace_rewrites_it(tmp_path):
    root = tmp_path / "archive"
    typed = {**message(1), "body": {"contentType": "html", "content": "retyped"}}
    store.save_message(typed, root=root)
    uri = f"mail:///messages/ID1a?owner={BOX}"
    path = write_capture(tmp_path / "c", "20260913T1", READ, {"uri": uri}, message(1))
    captures, _ = cp.load_captures([path])

    assert cp.import_captures(captures, root=root).messages_differ == [
        (path, ["body.content"])
    ]
    assert cp.import_captures(captures, root=root, replace=True).messages_replaced == [
        path
    ]
    stored = store.load_messages(root)["<m1@example.com>"]
    assert stored["body"]["content"] == "<p>body 1</p>"


def test_differing_names_fields_as_dotted_paths():
    stored = {
        "subject": "one",
        "body": {"contentType": "html", "content": "typed"},
        "attachments": [{"name": "a.pdf", "uri": "u1"}, {"name": "b.pdf", "uri": "u2"}],
        "sender": {"address": "someone@example.com"},
        "webLink": "w",
    }
    new = {
        "subject": "one",
        "body": {"contentType": "html", "content": "<p>sent</p>"},
        "attachments": [{"name": "a.pdf", "uri": "v1"}, {"name": "b.pdf", "uri": "v2"}],
        "sender": {"address": "someone@example.com"},
        "conversationId": "c",
    }
    # A field that differs in two items is named once; a field only one copy
    # has is named too.
    assert cp.differing(stored, new) == [
        "attachments[].uri",
        "body.content",
        "conversationId",
        "webLink",
    ]
    assert cp.differing(stored, stored) == []


def test_differing_names_a_list_whose_length_changed():
    stored = {"attachments": [{"name": "a.pdf"}], "categories": ["x"]}
    new = {"attachments": [{"name": "a.pdf"}, {"name": "b.pdf"}], "categories": ["y"]}
    assert cp.differing(stored, new) == ["attachments[]", "categories[]"]


def test_import_names_the_differing_fields_and_caps_the_list(tmp_path):
    root = config.archive_dir()
    typed = {
        **message(1),
        "subject": "retyped",
        "body": {"contentType": "text", "content": "retyped"},
        "sender": {"name": "S", "address": "other@example.com"},
        "receivedDateTime": "2026-03-10T12:00:01.000Z",
    }
    store.save_message(typed, root=root)
    uri = f"mail:///messages/ID1a?owner={BOX}"
    path = write_capture(
        config.captured_dir(), "20260913T1", READ, {"uri": uri}, message(1)
    )
    result = CliRunner().invoke(app, ["import"])
    assert result.exit_code == 0
    [line] = [s for s in result.output.splitlines() if "DIFFERS" in s]
    assert line == (
        "  DIFFERS from the stored copy in body.content, body.contentType, "
        f"receivedDateTime, sender.address, sender.name (+1 more): {path}"
    )
    assert "--replace" not in result.output


def test_import_lists_a_stored_copy_that_is_not_json_and_goes_on(tmp_path):
    root = config.archive_dir()
    stored, _ = store.save_message(message(1), root=root)
    stored.write_text(stored.read_text(encoding="utf-8")[:40], encoding="utf-8")
    captured = config.captured_dir()
    first = write_capture(
        captured,
        "20260913T1",
        READ,
        {"uri": f"mail:///messages/ID1a?owner={BOX}"},
        message(1),
    )
    write_capture(
        captured,
        "20260913T2",
        READ,
        {"uri": f"mail:///messages/ID2a?owner={BOX}"},
        message(2),
    )
    result = CliRunner().invoke(app, ["import"])
    assert result.exit_code == 0, result.output
    [line] = [s for s in result.output.splitlines() if "DIFFERS" in s]
    assert line == f"  DIFFERS from the stored copy: {first}"
    key = store.id_hash("<m2@example.com>")
    assert (root / "messages" / f"{key}.json").is_file()


def test_replace_moves_a_wrong_month_hit_and_drops_its_duplicate(tmp_path):
    root = tmp_path / "archive"
    wrong = hit(1, received="2025-03-10T12:00:00.000Z", summary="typed")
    store.save_hits([wrong], BOX, root=root)
    # The duplicate an older per-file dedup let in, under the right month.
    (root / "hits" / BOX / "2026-03.jsonl").write_text(
        json.dumps(store.stable(hit(1))) + "\n", encoding="utf-8"
    )
    folder = tmp_path / "c"
    write_capture(folder, "20260913T1", SEARCH, window_args(), hit(1), trailer(1))
    captures, _ = cp.load_captures([folder])

    done = cp.import_captures(captures, root=root, replace=True)
    assert [(o.name, n.name) for _, o, n in done.hits_replaced] == [
        ("2025-03.jsonl", "2026-03.jsonl")
    ]
    assert not (root / "hits" / BOX / "2025-03.jsonl").exists()
    rows = (root / "hits" / BOX / "2026-03.jsonl").read_text().splitlines()
    assert len(rows) == 1 and json.loads(rows[0])["summary"] == "text"
    assert cp.import_captures(captures, root=root, replace=True).hits_replaced == []


def test_replace_leaves_another_folder_copy_alone(tmp_path):
    root = tmp_path / "archive"
    store.save_hits([hit(1, copy="a", summary="archive copy")], BOX, root=root)
    folder = tmp_path / "c"
    write_capture(
        folder, "20260913T1", SEARCH, window_args(), hit(1, copy="b"), trailer(1)
    )
    captures, _ = cp.load_captures([folder])
    assert cp.import_captures(captures, root=root, replace=True).hits_replaced == []
    assert store.load_hits(root)["<m1@example.com>"]["summary"] == "archive copy"


def test_import_files_an_attachment_read_by_its_position(tmp_path):
    root = tmp_path / "archive"
    base = "mail:///messages/ID1a%3D/attachments/"
    atts = [
        {"uri": base + "A1?owner=x", "name": "logo.png"},
        {"uri": base + "A2?owner=x", "name": "letter.docx"},
    ]
    folder = tmp_path / "c"
    owner = f"?owner={BOX}"
    write_capture(
        folder,
        "20260913T1",
        READ,
        {"uri": "mail:///messages/ID1a" + owner},
        message(1, atts),
    )
    write_capture(
        folder, "20260913T2", READ, {"uri": base + "A2" + owner}, "the letter"
    )
    write_capture(folder, "20260913T3", READ, {"uri": base + "ZZ" + owner}, "stray")
    captures, _ = cp.load_captures([folder])

    done = cp.import_captures(captures, root=root)
    assert done.attachments_written == 1 and len(done.attachments_unmatched) == 1
    key = store.id_hash("<m1@example.com>")
    assert (root / "messages" / f"{key}.2.txt").read_text() == "the letter"


def test_an_attachment_read_that_differs_or_echoes_the_message_is_refused(tmp_path):
    root = tmp_path / "archive"
    base = "mail:///messages/ID1a%3D/attachments/"
    owner = f"?owner={BOX}"
    atts = [{"uri": base + "A1" + owner, "name": "letter.docx"}]
    store.save_message(message(1, atts), ["typed text"], root=root)
    folder = tmp_path / "c"
    write_capture(
        folder, "20260913T1", READ, {"uri": base + "A1" + owner}, "the letter"
    )
    write_capture(
        folder, "20260913T2", READ, {"uri": base + "A1" + owner}, message(1, atts)
    )
    captures, _ = cp.load_captures([folder])

    done = cp.import_captures(captures, root=root)
    reasons = [why for _, why in done.attachments_refused]
    assert len(reasons) == 2 and "different version" in reasons[0]
    assert reasons[1] == "returned the parent message"
    key = store.id_hash("<m1@example.com>")
    assert (root / "messages" / f"{key}.1.txt").read_text() == "typed text"


def test_a_reread_differing_only_in_line_endings_is_unchanged(tmp_path):
    root = tmp_path / "archive"
    base = "mail:///messages/ID1a%3D/attachments/"
    owner = f"?owner={BOX}"
    atts = [{"uri": base + "A1" + owner, "name": "letter.docx"}]
    store.save_message(message(1, atts), ["line one\nline two\n"], root=root)
    folder = tmp_path / "c"
    write_capture(
        folder,
        "20260913T1",
        READ,
        {"uri": base + "A1" + owner},
        "line one\r\nline two\r\n",
    )
    captures, _ = cp.load_captures([folder])

    done = cp.import_captures(captures, root=root)
    assert done.attachments_refused == [] and done.attachments_written == 0
    key = store.id_hash("<m1@example.com>")
    assert (root / "messages" / f"{key}.1.txt").read_text() == "line one\nline two\n"


def test_an_image_attachment_read_is_refused(tmp_path):
    root = tmp_path / "archive"
    base = "mail:///messages/ID1a%3D/attachments/"
    owner = f"?owner={BOX}"
    atts = [
        {"uri": base + "A1" + owner, "name": "image.png", "contentType": "image/png"},
        {"uri": base + "A2" + owner, "name": "letter.docx"},
    ]
    folder = tmp_path / "c"
    write_capture(
        folder,
        "20260913T1",
        READ,
        {"uri": "mail:///messages/ID1a" + owner},
        message(1, atts),
    )
    write_capture(
        folder, "20260913T2", READ, {"uri": base + "A1" + owner}, "[Resource ...]"
    )
    write_capture(
        folder, "20260913T3", READ, {"uri": base + "A2" + owner}, "the letter"
    )
    captures, _ = cp.load_captures([folder])

    done = cp.import_captures(captures, root=root)
    assert [why for _, why in done.attachments_refused] == ["an image attachment"]
    key = store.id_hash("<m1@example.com>")
    assert not (root / "messages" / f"{key}.1.txt").exists()
    assert (root / "messages" / f"{key}.2.txt").read_text() == "the letter"


def test_a_connector_note_on_an_attached_item_is_refused(tmp_path):
    root = tmp_path / "archive"
    base = "mail:///messages/ID1a%3D/attachments/"
    owner = f"?owner={BOX}"
    kind = "message/rfc822"
    atts = [
        {"uri": base + "A1" + owner, "name": "Fw: hello", "contentType": kind},
        {"uri": base + "A2" + owner, "name": "Fw: text", "contentType": kind},
    ]
    note = json.dumps(
        {
            "note": "This is an attached Outlook item, not a file; "
            "nested item content is not exposed.",
            "attachment": {"name": "Fw: hello", "contentType": kind},
        }
    )
    folder = tmp_path / "c"
    write_capture(
        folder,
        "20260913T1",
        READ,
        {"uri": "mail:///messages/ID1a" + owner},
        message(1, atts),
    )
    write_capture(folder, "20260913T2", READ, {"uri": base + "A1" + owner}, note)
    write_capture(
        folder, "20260913T3", READ, {"uri": base + "A2" + owner}, "forwarded text"
    )
    captures, _ = cp.load_captures([folder])

    done = cp.import_captures(captures, root=root)
    reasons = [why for _, why in done.attachments_refused]
    assert reasons == ["a connector note, not text"]
    key = store.id_hash("<m1@example.com>")
    assert not (root / "messages" / f"{key}.1.txt").exists()
    assert (root / "messages" / f"{key}.2.txt").read_text() == "forwarded text"


def test_an_attachment_read_by_a_rebuilt_uri_is_matched(tmp_path):
    root = tmp_path / "archive"
    owner = f"?owner={BOX}"
    listed = "mail:///messages/ATT1%3D/attachments/ATT1%3D" + owner
    rebuilt = "mail:///messages/ID1a/attachments/ATT1=" + owner
    atts = [{"uri": listed, "name": "notes.pdf", "contentType": "application/pdf"}]
    folder = tmp_path / "c"
    write_capture(
        folder,
        "20260913T1",
        READ,
        {"uri": "mail:///messages/ID1a" + owner},
        message(1, atts),
    )
    write_capture(folder, "20260913T2", READ, {"uri": rebuilt}, "the notes")
    captures, _ = cp.load_captures([folder])

    done = cp.import_captures(captures, root=root)
    assert done.attachments_unmatched == []
    key = store.id_hash("<m1@example.com>")
    assert (root / "messages" / f"{key}.1.txt").read_text() == "the notes"


def test_replace_never_overwrites_with_a_partial_message(tmp_path):
    root = tmp_path / "archive"
    store.save_message(message(1), root=root)
    partial = {k: v for k, v in message(1).items() if k != "body"}
    with pytest.raises(store.StoreError, match="not a full"):
        store.replace_message(partial, root)
    folder = tmp_path / "c"
    uri = f"mail:///messages/ID1a?owner={BOX}"
    path = write_capture(folder, "20260913T1", READ, {"uri": uri}, partial)
    captures, _ = cp.load_captures([folder])
    done = cp.import_captures(captures, root=root, replace=True)
    assert done.messages_differ == [(path, ["body"])]
    assert done.messages_replaced == []
    assert "body" in store.load_messages(root)["<m1@example.com>"]


def test_the_first_capture_of_a_message_is_the_one_kept(tmp_path):
    folder = tmp_path / "c"
    write_capture(
        folder, "20260913T1", SEARCH, window_args(), hit(1, summary="first"), trailer(1)
    )
    write_capture(
        folder,
        "20260913T2",
        SEARCH,
        window_args(),
        hit(1, summary="second"),
        trailer(1),
    )
    captures, _ = cp.load_captures([folder])
    done = cp.import_captures(captures, root=tmp_path / "archive")
    assert (done.hits_added, done.hits_known) == (1, 1)
    assert (
        store.load_hits(tmp_path / "archive")["<m1@example.com>"]["summary"] == "first"
    )


# --- windows and coverage.csv ---------------------------------------------------


def test_a_window_is_complete_when_every_offset_is_captured(pulled):
    captures, _ = cp.load_captures([pulled])
    [w] = cv.windows(captures, BOX)
    assert w.complete and w.total == 3 and len(w.messages) == 3
    assert cv.fmt_ts(w.after) == "2026-03-01T08:00:00Z"
    assert cv.fmt_ts(w.end) == "2026-04-01T07:00:00Z"


def test_a_missing_page_leaves_the_window_incomplete(pulled):
    sorted(pulled.glob("*.json"))[1].unlink()
    captures, _ = cp.load_captures([pulled])
    [w] = cv.windows(captures, BOX)
    assert not w.complete


def test_lookups_are_not_windows(tmp_path):
    folder = tmp_path / "c"
    write_capture(
        folder, "20260913T1", SEARCH, window_args(query="inquiry"), hit(1), trailer(1)
    )
    write_capture(
        folder,
        "20260913T2",
        SEARCH,
        window_args(folderName="Inbox"),
        hit(2),
        trailer(1),
    )
    captures, _ = cp.load_captures([folder])
    assert cv.windows(captures, BOX) == []


def test_a_page_past_the_end_is_not_a_window(pulled):
    # The connector answers an offset past the end with no hits and a bare
    # totalResultCount equal to that offset: an echo, not a pull.
    write_capture(
        pulled, "20260913T9", SEARCH, window_args(offset=25), {"totalResultCount": 25}
    )
    captures, _ = cp.load_captures([pulled])
    [w] = cv.windows(captures, BOX)
    assert w.complete and w.total == 3


def test_an_empty_window_is_recorded_as_covered(tmp_path):
    # A window with no mail comes back as an empty result: no hits, no total.
    folder = tmp_path / "c"
    args = {"mailboxOwnerEmail": BOX, "afterDateTime": "2026-09-26T05:55:23Z"}
    write_capture(folder, "20260925T234356", SEARCH, {**args, "offset": 0})
    write_capture(folder, "20260925T234400", SEARCH, window_args(offset=25))
    captures, _ = cp.load_captures([folder])
    [w] = cv.windows(captures, BOX)
    assert w.complete and w.total == 0 and not w.messages
    [row] = cv.record([w], root=tmp_path / "archive")
    assert (row["after"], row["before"]) == (
        "2026-09-26T05:55:23Z",
        "2026-09-26T06:43:56Z",
    )
    assert (row["total"], row["messages"]) == ("0", "0")


def test_an_open_window_ends_at_its_first_page(tmp_path):
    folder = tmp_path / "c"
    args = {"mailboxOwnerEmail": BOX, "afterDateTime": "2026-09-22T00:00:00Z"}
    write_capture(
        folder, "20260925T090000", SEARCH, {**args, "offset": 0}, hit(1), trailer(2, 1)
    )
    write_capture(
        folder,
        "20260925T090100",
        SEARCH,
        {**args, "offset": 1},
        hit(2, offset=1),
        trailer(2),
    )
    captures, _ = cp.load_captures([folder])
    [w] = cv.windows(captures, BOX)
    assert w.complete and w.before is None
    assert w.end == datetime(2026, 9, 25, 9, 0, tzinfo=PACIFIC)
    assert w.row()["pulled"] == "2026-09-25T09:01:00-07:00"


def test_record_appends_once_and_refuses_an_unarchived_window(pulled, tmp_path):
    root = tmp_path / "archive"
    captures, _ = cp.load_captures([pulled])
    found = cv.windows(captures, BOX)
    with pytest.raises(cv.CoverageError, match="import the captures first"):
        cv.record(found, root=root)
    cp.import_captures(captures, root=root)
    [row] = cv.record(found, root=root)
    assert row == {
        "mailbox": BOX,
        "after": "2026-03-01T08:00:00Z",
        "before": "2026-04-01T07:00:00Z",
        "total": "3",
        "messages": "3",
        "pulled": "2026-09-13T10:00:10-07:00",
    }
    assert cv.record(found, root=root) == []
    assert len(cv.read_rows(root / "coverage.csv")) == 1


# --- report ---------------------------------------------------------------------


def ts(value):
    return cv.parse_ts(value)


def test_merge_joins_overlapping_and_touching_rows():
    rows = [
        {"after": "2026-01-01T00:00:00Z", "before": "2026-02-01T00:00:00Z"},
        {"after": "2026-02-01T00:00:00Z", "before": "2026-03-01T00:00:00Z"},
        {"after": "2026-02-20T00:00:00Z", "before": "2026-03-05T00:00:00Z"},
        {"after": "2026-04-01T00:00:00Z", "before": "2026-05-01T00:00:00Z"},
    ]
    assert cv.merge(rows) == [
        (ts("2026-01-01T00:00:00Z"), ts("2026-03-05T00:00:00Z")),
        (ts("2026-04-01T00:00:00Z"), ts("2026-05-01T00:00:00Z")),
    ]


def test_gaps_between_and_after_the_covered_spans():
    covered = [
        (ts("2026-01-01T00:00:00Z"), ts("2026-02-01T00:00:00Z")),
        (ts("2026-03-01T00:00:00Z"), ts("2026-04-01T00:00:00Z")),
    ]
    now = ts("2026-05-01T00:00:00Z")
    assert cv.gaps(covered, ts("2025-12-01T00:00:00Z"), now) == [
        (ts("2025-12-01T00:00:00Z"), ts("2026-01-01T00:00:00Z")),
        (ts("2026-02-01T00:00:00Z"), ts("2026-03-01T00:00:00Z")),
        (ts("2026-04-01T00:00:00Z"), now),
    ]


def test_report_counts_lookup_hits_in_gaps_unread_hits_and_ledger_lag():
    rows = [
        {
            "mailbox": BOX,
            "after": "2026-03-01T08:00:00Z",
            "before": "2026-04-01T07:00:00Z",
        }
    ]
    hits = {
        "<a>": {"mailbox": BOX, "receivedDateTime": "2026-03-10T12:00:00Z"},
        "<b>": {"mailbox": BOX, "receivedDateTime": "2026-03-11T12:00:00Z"},
        "<c>": {"mailbox": BOX, "receivedDateTime": "2026-04-10T12:00:00Z"},
    }
    ledger = [{"mailbox": BOX, "received": "2026-03-20T00:00:00Z"}]
    lines = cv.report(BOX, rows, hits, {"<a>"}, ledger, now=ts("2026-05-01T07:00:00Z"))
    assert "  covered  2026-03-01 00:00 to 2026-04-01 00:00  2 archived" in lines
    assert (
        "  GAP      2026-04-01 00:00 to 2026-05-01 00:00  1 archived (lookup only)"
        in lines
    )
    assert "  2026-03  1" in lines
    assert "  the ledger lags coverage: archived mail not yet classified" in lines


ROW = {
    "mailbox": BOX,
    "after": "2026-03-01T08:00:00Z",
    "before": "2026-04-01T07:00:00Z",
}


def footer(lines):
    return [s for s in lines if s.startswith("next sweep from")]


def test_next_sweep_is_the_ledger_mark_less_the_margin():
    ledger = [
        {"received": "2026-03-19T00:00:00Z"},
        {"received": "2026-03-20T00:02:00Z"},
    ]
    lines = cv.report(BOX, [ROW], {}, set(), ledger, now=ts("2026-05-01T07:00:00Z"))
    assert lines[-1] == "next sweep from         2026-03-19T23:57:00Z"
    assert cv.SWEEP_MARGIN.total_seconds() == 300


def test_next_sweep_is_the_coverage_end_when_that_is_earlier():
    ledger = [{"received": "2026-04-02T00:00:00Z"}]
    lines = cv.report(BOX, [ROW], {}, set(), ledger, now=ts("2026-05-01T07:00:00Z"))
    assert footer(lines) == ["next sweep from         2026-04-01T07:00:00Z"]
    # Within the margin of the end, the margin still applies.
    ledger = [{"received": "2026-04-01T07:03:00Z"}]
    lines = cv.report(BOX, [ROW], {}, set(), ledger, now=ts("2026-05-01T07:00:00Z"))
    assert footer(lines) == ["next sweep from         2026-04-01T06:58:00Z"]


def test_next_sweep_without_a_ledger_without_coverage_and_without_either():
    now = ts("2026-05-01T07:00:00Z")
    lines = cv.report(BOX, [ROW], {}, set(), [], now=now)
    assert footer(lines) == ["next sweep from         2026-04-01T07:00:00Z"]
    ledger = [{"received": "2026-03-20T00:00:00Z"}]
    lines = cv.report(BOX, [], {}, set(), ledger, now=now)
    assert footer(lines) == ["next sweep from         2026-03-19T23:55:00Z"]
    assert footer(cv.report(BOX, [], {}, set(), [], now=now)) == []


def test_next_sweep_ignores_another_mailbox_in_the_ledger():
    ledger = [
        {"mailbox": BOX, "received": "2026-03-20T00:00:00Z"},
        {"mailbox": "other@example.org", "received": "2026-03-25T00:00:00Z"},
    ]
    lines = cv.report(BOX, [], {}, set(), ledger, now=ts("2026-05-01T07:00:00Z"))
    assert footer(lines) == ["next sweep from         2026-03-19T23:55:00Z"]


def test_cli_coverage_reads_a_ledger_kept_elsewhere(tmp_path):
    elsewhere = tmp_path / "records" / "sweep-ledger.csv"
    elsewhere.parent.mkdir()
    elsewhere.write_text(
        "internet_message_id,received,outcome\n"
        "<m1@example.com>,2026-03-20T00:00:00Z,filed\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    assert "next sweep from" not in runner.invoke(app, ["coverage"]).output
    result = runner.invoke(app, ["coverage", "--ledger", str(elsewhere)])
    assert result.exit_code == 0
    assert result.output.splitlines()[-1] == (
        "next sweep from         2026-03-19T23:55:00Z"
    )


def test_cli_coverage_refuses_a_ledger_that_is_not_a_file(tmp_path):
    result = CliRunner().invoke(
        app, ["coverage", "--ledger", str(tmp_path / "typo.csv")]
    )
    assert result.exit_code == 1
    assert "typo.csv is not a file" in result.output
    assert "next sweep from" not in result.output


def test_the_sweep_asks_where_to_start_on_an_empty_ledger():
    from importlib.resources import files

    sweep = (files("outlooks.prompts") / "skills/sweep/SKILL.md").read_text("utf-8")
    step = sweep.split("## 1. Sweep (step `sweep`)", 1)[1].split("\n## ", 1)[0]
    asks = step.split("An empty ledger has no high-water mark", 1)[1]
    assert "Ask the operator where to start, in both cases" in asks
    assert "has never been classified" in asks


def test_the_sweep_names_the_margin_coverage_uses():
    from importlib.resources import files

    sweep = (files("outlooks.prompts") / "skills/sweep/SKILL.md").read_text("utf-8")
    assert cv.SWEEP_MARGIN.total_seconds() == 5 * 60
    assert "**minus five minutes**" in sweep
    assert "`next sweep from`" in sweep


def test_report_names_the_zone_by_its_short_label():
    lines = cv.report(BOX, [], {}, set(), [], now=ts("2026-05-01T07:00:00Z"))
    assert lines[0] == f"coverage for {BOX} (PT)"


# --- mass sends -----------------------------------------------------------------


SEND = {
    "sender": "events@example.org",
    "subject": "Spring open house",
    "after": "2026-03-10T12:00:00Z",
    "before": "2026-03-10T12:05:00Z",
    "exemplar": "<m1@example.com>",
}


def copy(n, received="2026-03-10T12:02:00Z", **extra):
    return {
        "mailbox": BOX,
        "sender": "Events@Example.org",
        "subject": "Spring open house",
        "receivedDateTime": received,
        **extra,
    }


def test_mass_copies_are_the_copies_but_not_the_exemplar():
    hits = {
        "<m1@example.com>": copy(1),
        "<m2@example.com>": copy(2),
        "<m3@example.com>": copy(3, received="2026-03-10T12:00:00Z"),  # at after
        "<m4@example.com>": copy(4, received="2026-03-10T12:05:00Z"),  # at before
        "<m5@example.com>": copy(5, sender="someone@example.com"),
        "<m6@example.com>": copy(6, subject="Spring open house (updated)"),
        "<m7@example.com>": copy(7, received="2026-03-10T11:59:59Z"),
        "<m8@example.com>": copy(8, received="2026-03-10T12:05:01Z"),
        "<m9@example.com>": {k: v for k, v in copy(9).items() if k != "sender"},
    }
    assert cv.mass_copies(hits, [SEND]) == {
        "<m2@example.com>",
        "<m3@example.com>",
        "<m4@example.com>",
    }


def test_a_missing_mass_sends_file_is_an_empty_one(tmp_path):
    rows = cv.read_rows(cv.mass_sends_csv(tmp_path))
    assert rows == []
    assert cv.mass_copies({"<m2@example.com>": copy(2)}, rows) == set()


def test_report_counts_mass_send_copies_apart_from_unread_mail():
    rows = [
        {
            "mailbox": BOX,
            "after": "2026-03-01T08:00:00Z",
            "before": "2026-04-01T07:00:00Z",
        }
    ]
    hits = {f"<m{n}@example.com>": copy(n) for n in (1, 2, 3)}
    skipped = cv.mass_copies(hits, [SEND])
    now = ts("2026-04-01T07:00:00Z")
    lines = cv.report(BOX, rows, hits, set(), [], now=now, skipped=skipped)
    assert "  2026-03  1" in lines  # the exemplar, still to read
    assert "mass-send copies left unread by design  2" in lines
    plain = cv.report(BOX, rows, hits, set(), [], now=now)
    assert "  2026-03  3" in plain
    assert not any("by design" in line for line in plain)


# --- commands -------------------------------------------------------------------


def use_project(folder, monkeypatch):
    """Run from ``folder``, a project whose ``[tool.outlooks]`` names ``BOX``.

    Clears the conftest fixture's env overrides for the keys this table sets,
    so the table (not the shared test defaults) decides the effective paths —
    matching what a real repo with its own ``[tool.outlooks]`` would resolve.
    """
    for key in ("OUTLOOKS_MAILBOX", "OUTLOOKS_ARCHIVE_DIR", "OUTLOOKS_CAPTURED_DIR"):
        monkeypatch.delenv(key, raising=False)
    (folder / "pyproject.toml").write_text(
        f'[tool.outlooks]\nmailbox = "{BOX}"\n', encoding="utf-8"
    )
    monkeypatch.chdir(folder)


def test_import_and_coverage_commands(pulled, tmp_path, monkeypatch):
    use_project(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(app, ["import", str(pulled)])
    assert result.exit_code == 0, result.output
    assert (
        "hits: 3 added" in result.output
        and "coverage: 1 windows recorded" in result.output
    )
    with open("data/outlook/coverage.csv", newline="") as f:
        assert len(list(csv.DictReader(f))) == 1

    # A re-import counts the recorded window; a one-page probe inside it is
    # counted too, not listed as a window to page.
    write_capture(
        pulled,
        "20260914T100000",
        SEARCH,
        window_args(0, afterDateTime="2026-03-05T00:00:00-08:00"),
        hit(1, offset=0),
        trailer(9, 1),
    )
    result = runner.invoke(app, ["import", str(pulled)])
    assert result.exit_code == 0, result.output
    assert "INCOMPLETE" not in result.output
    assert "(1 complete windows already recorded; 1 incomplete" in result.output

    result = runner.invoke(app, ["coverage", "--since", "2026-02-01"])
    assert result.exit_code == 0, result.output
    assert "GAP      2026-02-01 00:00 to 2026-03-01 00:00" in result.output


def test_worklist_and_coverage_skip_the_same_mass_send_copies(tmp_path, monkeypatch):
    use_project(tmp_path, monkeypatch)
    folder = tmp_path / "captured"
    copies = [
        hit(n, offset=n - 1, sender="events@example.org", subject="Spring open house")
        for n in (1, 2, 3)
    ]
    path = write_capture(
        folder,
        "20260913T100000",
        SEARCH,
        window_args(0, limit=4),
        *copies,
        hit(4, offset=3),
        trailer(4),
    )
    stamp = datetime(2026, 9, 13, 10, tzinfo=PACIFIC).timestamp()
    os.utime(path, (stamp, stamp))
    runner = CliRunner()
    result = runner.invoke(app, ["import", str(folder)])
    assert result.exit_code == 0, result.output
    Path("data/outlook/mass-sends.csv").write_text(
        "sender,subject,after,before,exemplar\n"
        "events@example.org,Spring open house,2026-03-10T12:00:00Z,"
        "2026-03-10T12:00:00Z,<m3@example.com>\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OUTLOOKS_CAPTURED_DIR", str(folder))

    covered = runner.invoke(app, ["coverage"])
    assert covered.exit_code == 0, covered.output
    assert "mass-send copies left unread by design  2" in covered.output
    assert "  2026-03  2" in covered.output  # the exemplar and message 4

    out = tmp_path / "out"
    listed = runner.invoke(
        app,
        [
            "worklist",
            "--after",
            "2026-03-01",
            "--before",
            "2026-04-01",
            "--out",
            str(out),
        ],
    )
    assert listed.exit_code == 0, listed.output
    assert "4 messages listed by window captures, 2 to read" in listed.output
    assert "2 mass-send copies skipped (mass-sends.csv)" in listed.output
    batch = (out / "batch-01.txt").read_text(encoding="utf-8")
    assert "ID3a" in batch and "ID4a" in batch


def test_import_lists_a_lost_capture_and_records_no_window(tmp_path, monkeypatch):
    use_project(tmp_path, monkeypatch)
    folder = tmp_path / "c"
    path = spilled_page(folder, tmp_path / "gone.txt", **window_args())
    result = CliRunner().invoke(app, ["import", str(folder)])
    assert result.exit_code == 0, result.output
    assert f"LOST capture (its output can't be recovered; call again): {path}" in (
        result.output
    )
    assert "coverage: 0 windows recorded" in result.output
    assert not (tmp_path / "data" / "outlook" / "coverage.csv").exists()


# --- configuration --------------------------------------------------------------


def test_paths_resolve_from_the_project_root_in_any_subdirectory(tmp_path, monkeypatch):
    for key in ("OUTLOOKS_MAILBOX", "OUTLOOKS_ARCHIVE_DIR", "OUTLOOKS_CAPTURED_DIR"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.outlooks]\nmailbox = "Box@Example.org"\narchive_dir = "mail"\n',
        encoding="utf-8",
    )
    # A nearer pyproject.toml without the table (a workspace member) is skipped.
    member = tmp_path / "pkg"
    (member / "src").mkdir(parents=True)
    (member / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    monkeypatch.chdir(member / "src")
    root = tmp_path.resolve()
    assert cp.captured_dir() == root / "temp" / "outlook" / "captured"
    assert store.archive_root() == root / "mail"
    assert cv.coverage_csv() == root / "mail" / "coverage.csv"
    assert config.mailbox() == "box@example.org"


def test_env_overrides_the_table_and_a_missing_mailbox_is_refused(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OUTLOOKS_MAILBOX", raising=False)
    monkeypatch.delenv("OUTLOOKS_OWN_ADDRESSES", raising=False)
    with pytest.raises(config.ConfigError):
        config.mailbox()
    monkeypatch.setenv("OUTLOOKS_MAILBOX", "other@example.org")
    monkeypatch.setenv("OUTLOOKS_ARCHIVE_DIR", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("OUTLOOKS_TIMEZONE", "America/New_York")
    assert config.mailbox() == "other@example.org"
    assert store.archive_root() == tmp_path / "elsewhere"
    assert config.zone() == ZoneInfo("America/New_York")


def test_config_reads_address_lists_from_the_pyproject_table(tmp_path, monkeypatch):
    for key in (
        "OUTLOOKS_OWN_ADDRESSES",
        "OUTLOOKS_SYSTEM_SENDERS",
        "OUTLOOKS_MAILBOX",
    ):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.outlooks]\n"
        'own_addresses = ["Desk@Example.org", "Events@Example.org"]\n'
        'system_senders = ["Notices@System.Example.org"]\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    settings = config.settings()
    assert settings.own_addresses == ("desk@example.org", "events@example.org")
    assert settings.system_senders == ("notices@system.example.org",)


def test_config_reads_address_lists_from_comma_separated_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OUTLOOKS_OWN_ADDRESSES", "Desk@Example.org, Events@Example.org")
    monkeypatch.setenv("OUTLOOKS_SYSTEM_SENDERS", "Notices@System.Example.org")
    settings = config.settings()
    assert settings.own_addresses == ("desk@example.org", "events@example.org")
    assert settings.system_senders == ("notices@system.example.org",)


# --- lost captures and the hook -------------------------------------------------


def spilled_page(folder, saved, **args):
    """A search page the hook caught as the note naming the file it was saved to."""
    folder.mkdir(parents=True, exist_ok=True)
    note = f"Error: result exceeds allowed tokens. Output has been saved to {saved}."
    path = folder / "20260925T1-1.json"
    path.write_text(
        json.dumps({"tool_name": SEARCH, "tool_input": args, "tool_response": note}),
        encoding="utf-8",
    )
    return path


def test_a_lost_search_page_is_not_an_empty_window(tmp_path):
    # A spilled offset-0 page whose saved file is gone has no blocks, like an
    # empty window, but it is a window nobody has seen: never a covered 0.
    saved = saved_file(tmp_path, "search-1.txt", blocks(hit(1), trailer(1)))
    folder = tmp_path / "c"
    spilled_page(folder, saved, **window_args())
    captures, _ = cp.load_captures([folder])
    [w] = cv.windows(captures, BOX)
    assert w.complete and w.total == 1
    saved.unlink()
    captures, _ = cp.load_captures([folder])
    [lost] = captures
    assert lost.lost and not lost.blocks
    assert not cv.is_empty_window(lost) and not cv.is_window(lost)
    assert cv.windows(captures, BOX) == []


def test_a_capture_with_no_known_response_shape_is_lost(tmp_path):
    folder = tmp_path / "c"
    folder.mkdir()
    base = {"tool_name": SEARCH, "tool_input": window_args()}
    payloads = [
        base,  # no response at all
        {**base, "tool_response": {"isError": True}},
        {**base, "tool_response": "plain text, no saved file named"},
    ]
    for n, payload in enumerate(payloads):
        path = folder / f"20260925T00000{n}-1.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
    captures, _ = cp.load_captures([folder])
    assert len(captures) == 3 and all(c.lost for c in captures)
    assert cv.windows(captures, BOX) == []
    # The connector's own empty answer, by contrast, is not lost.
    empty = write_capture(folder, "20260926T000000", SEARCH, window_args())
    captures, _ = cp.load_captures([folder])
    assert {c.path: c.lost for c in captures}[empty] is False


def run_hook(project, payload):
    """Install the capture hook into ``project``, run it on one payload as Claude
    Code does, and return the capture's path.

    Writing the script with ``hook.apply`` (rather than reading a checked-in
    copy) keeps this test independent of the directory the suite runs in.
    """
    hk.apply(project)
    env = {k: v for k, v in os.environ.items() if k != "OUTLOOKS_CAPTURED_DIR"}
    subprocess.run(
        [str(project / hk.SCRIPT_PATH)],
        input=json.dumps(payload),
        text=True,
        check=True,
        env={**env, "CLAUDE_PROJECT_DIR": str(project)},
    )
    [path] = (project / "temp" / "outlook" / "captured").glob("*.json")
    return path


def test_the_hook_keeps_a_copy_of_a_spilled_result(tmp_path):
    # The saved file is outside the repo and not kept; the hook's copy is.
    saved = saved_file(tmp_path, "read_resource-1.txt", message(1))
    payload = {
        "tool_name": READ,
        "tool_input": {"uri": f"mail:///messages/X?owner={BOX}"},
        "tool_response": f"Error: result exceeds allowed tokens. Output has been "
        f"saved to {saved}.\nFormat: Plain text",
    }
    path = run_hook(tmp_path / "project", payload)
    assert json.loads(path.read_text(encoding="utf-8")) == payload
    copy = path.with_name(f"{path.stem}.saved.txt")
    assert copy.read_text(encoding="utf-8") == saved.read_text(encoding="utf-8")
    saved.unlink()
    capture = cp.read_capture(path)
    assert capture is not None and not capture.lost
    assert capture.message is not None and capture.message["subject"] == "message 1"


def test_a_spill_note_is_followed_only_into_the_saved_results(tmp_path):
    # The note is text in a capture: a file it names outside Claude Code's
    # projects directory is neither copied by the hook nor read by the import.
    named = tmp_path / "elsewhere" / "notes.txt"
    named.parent.mkdir()
    named.write_text(json.dumps(message(1)), encoding="utf-8")
    inside = saved_file(tmp_path, "read_resource-1.txt", message(1))
    climbing = inside.parent / ".." / ".." / ".." / ".." / "elsewhere" / "notes.txt"
    # Nor is a link inside that directory a way out of it, as a folder or a file.
    (inside.parent / "folder").symlink_to(named.parent, target_is_directory=True)
    (inside.parent / "linked.txt").symlink_to(named)
    linked = [inside.parent / "folder" / "notes.txt", inside.parent / "linked.txt"]
    for n, path in enumerate([named, climbing, *linked]):
        payload = {
            "tool_name": READ,
            "tool_input": {"uri": f"mail:///messages/X?owner={BOX}"},
            "tool_response": f"Output has been saved to {path}.\nFormat: Plain text",
        }
        captured = run_hook(tmp_path / f"project{n}", payload)
        assert list(captured.parent.glob("*.saved.txt")) == []
        capture = cp.read_capture(captured)
        assert capture is not None and capture.lost


def test_the_hook_copies_nothing_for_an_ordinary_result(tmp_path):
    # A hit whose summary quotes the note's words is in a list of blocks, not a
    # spill note, so no file it names is copied.
    named = tmp_path / "named.txt"
    named.write_text("not the output", encoding="utf-8")
    payload = {
        "tool_name": SEARCH,
        "tool_input": window_args(),
        "tool_response": blocks(
            hit(1, summary=f"Output has been saved to {named}"), trailer(1)
        ),
    }
    path = run_hook(tmp_path / "project", payload)
    assert list(path.parent.glob("*.saved.txt")) == []
    capture = cp.read_capture(path)
    assert capture is not None and capture.hits and not capture.lost


def test_window_bounds_are_utc_and_an_unset_before_is_open(tmp_path):
    # The one reading of a capture's bounds, shared by windows, the sizes, and
    # the id check: an absent or empty beforeDateTime is an open window.
    folder = tmp_path / "c"
    after = {"mailboxOwnerEmail": BOX, "afterDateTime": "2026-09-22T00:00:00Z"}
    write_capture(folder, "20260925T090000", SEARCH, window_args(), hit(1), trailer(1))
    write_capture(folder, "20260925T090100", SEARCH, after, hit(2), trailer(1))
    write_capture(
        folder,
        "20260925T090200",
        SEARCH,
        {**after, "beforeDateTime": ""},
        hit(3),
        trailer(1),
    )
    captures, _ = cp.load_captures([folder])
    bounded, *unbounded = (cv.window_bounds(c) for c in captures)
    assert [cv.fmt_ts(bounded[0]), cv.fmt_ts(bounded[1] or bounded[0])] == [
        "2026-03-01T08:00:00Z",
        "2026-04-01T07:00:00Z",
    ]
    assert [b[1] for b in unbounded] == [None, None]
    assert len(cv.windows(captures, BOX)) == 2


# --- store ----------------------------------------------------------------------


def test_save_hits_reads_each_month_file_once(tmp_path, monkeypatch):
    store.save_hits(
        [hit(1), hit(2, received="2026-04-10T12:00:00.000Z")], BOX, root=tmp_path
    )
    reads = []
    real = store._read_jsonl
    monkeypatch.setattr(store, "_read_jsonl", lambda p: reads.append(p) or real(p))
    counts = store.save_hits([hit(1), hit(3)], BOX, root=tmp_path)
    assert counts == {"added": 1, "known": 1}
    assert sorted(p.name for p in reads) == ["2026-03.jsonl", "2026-04.jsonl"]


def test_replace_rewrites_several_hits_in_one_month(tmp_path):
    root = tmp_path / "archive"
    typed = [hit(n, summary="typed") for n in (1, 2, 3)]
    store.save_hits([*typed, hit(4, summary="kept")], BOX, root=root)
    folder = tmp_path / "c"
    write_capture(
        folder,
        "20260913T1",
        SEARCH,
        window_args(),
        hit(1),
        hit(2),
        hit(3),
        trailer(3),
    )
    captures, _ = cp.load_captures([folder])

    done = cp.import_captures(captures, mailbox=BOX, root=root, replace=True)
    assert sorted(mid for mid, _, _ in done.hits_replaced) == [
        "<m1@example.com>",
        "<m2@example.com>",
        "<m3@example.com>",
    ]
    rows = [
        json.loads(r)
        for r in (root / "hits" / BOX / "2026-03.jsonl").read_text().splitlines()
    ]
    assert {r["internetMessageId"]: r["summary"] for r in rows} == {
        "<m1@example.com>": "text",
        "<m2@example.com>": "text",
        "<m3@example.com>": "text",
        "<m4@example.com>": "kept",
    }
    replayed = cp.import_captures(captures, mailbox=BOX, root=root, replace=True)
    assert replayed.hits_replaced == []


def test_relative_scratch_dir_and_profile_resolve_from_the_table(tmp_path, monkeypatch):
    monkeypatch.delenv("OUTLOOKS_SCRATCH_DIR", raising=False)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.outlooks]\nscratch_dir = "work/mail"\nprofile = "docs/profile.md"\n',
        encoding="utf-8",
    )
    (tmp_path / "pkg").mkdir()
    monkeypatch.chdir(tmp_path / "pkg")
    root = tmp_path.resolve()
    settings = config.settings()
    assert settings.scratch_dir == root / "work" / "mail"
    assert settings.profile == root / "docs" / "profile.md"
    assert settings.origins["profile"] == "pyproject.toml"
    monkeypatch.setenv("OUTLOOKS_PROFILE", str(tmp_path / "elsewhere.md"))
    assert config.settings().profile == tmp_path / "elsewhere.md"


def test_no_setting_comes_from_the_directory_the_suite_runs_in():
    settings = config.settings()
    assert settings.source is None
    assert settings.profile is None
    assert settings.origins["profile"] == "default"
