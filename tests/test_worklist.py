"""Tests for the window-mode bulk-read worklist and the attachment-text audit.

Both read config's own paths (``config.captured_dir()``, ``store.archive_root()``)
rather than taking overrides, so captures and archived messages are written
directly under those conftest-provided, per-test directories.
"""

import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from outlooks import config, store
from outlooks import worklist as wl

BOX = "desk@example.org"
SEARCH = "mcp__claude_ai_Microsoft_365__outlook_email_search"
READ = "mcp__claude_ai_Microsoft_365__read_resource"


def blocks(*items):
    return [
        {"type": "text", "text": i if isinstance(i, str) else json.dumps(i)}
        for i in items
    ]


def write_capture(stamp, tool, args, *items):
    folder = config.captured_dir()
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
    at_stamp(path, stamp)
    return path


def at_stamp(path, stamp):
    """Date the file as the hook would have written it: at its stamp."""
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


def hit(n, received="2026-03-10T12:00:00.000Z", offset=0):
    return {
        "uri": f"mail:///messages/ID{n}?owner=desk%40example.org",
        "id": f"ID{n}",
        "subject": f"message {n}",
        "sender": "someone@example.com",
        "recipients": [BOX],
        "receivedDateTime": received,
        "summary": "text",
        "internetMessageId": f"<m{n}@example.com>",
        "isRead": True,
        "offset": offset,
    }


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


def trailer(total, next_offset=None):
    return {
        "moreResults": next_offset is not None,
        "nextOffset": next_offset,
        "totalResultCount": total,
    }


# --- read_worklist ---------------------------------------------------------------


def test_read_worklist_lists_unread_unarchived_messages_and_batches_them(tmp_path):
    write_capture(
        "20260901T090000", SEARCH, window_args(0), hit(1, offset=0), trailer(2, 1)
    )
    write_capture(
        "20260901T090010", SEARCH, window_args(1), hit(2, offset=1), trailer(2)
    )
    out = tmp_path / "out"
    result = wl.read_worklist("2026-03-10", "2026-03-12", out, per=1)

    assert result.listed == 2
    assert [h["internetMessageId"] for h in result.todo] == [
        "<m1@example.com>",
        "<m2@example.com>",
    ]
    assert [p.name for p in result.batches] == ["batch-01.txt", "batch-02.txt"]
    assert (out / "batch-01.txt").read_text().startswith("msg-001 ")
    assert (out / "batch-02.txt").read_text().startswith("msg-002 ")


def test_read_worklist_skips_a_captured_read_and_an_archived_message(tmp_path):
    write_capture(
        "20260901T090000", SEARCH, window_args(0), hit(1, offset=0), trailer(2, 1)
    )
    write_capture(
        "20260901T090010", SEARCH, window_args(1), hit(2, offset=1), trailer(2)
    )
    # Message 1 was already read (a capture of its full body exists)...
    write_capture(
        "20260901T090020",
        READ,
        {"uri": f"mail:///messages/ID1?owner={BOX}"},
        message(1),
    )
    # ...and message 2 is already in the archive.
    store.save_message(message(2))

    out = tmp_path / "out"
    result = wl.read_worklist("2026-03-10", "2026-03-12", out, per=10)
    assert result.listed == 2
    assert result.todo == []
    assert result.batches == []


def test_read_worklist_counts_archived_orphans_outside_any_window_listing(tmp_path):
    write_capture(
        "20260901T090000", SEARCH, window_args(0), hit(1, offset=0), trailer(1)
    )
    # An archived hit in range that the window pull above never listed.
    orphan = hit(9, received="2026-03-11T00:00:00.000Z")
    store.save_hits([orphan], mailbox=BOX)

    out = tmp_path / "out"
    result = wl.read_worklist("2026-03-10", "2026-03-12", out)
    assert result.orphans == 1


def test_read_worklist_refuses_a_short_repull_of_a_covered_window(tmp_path):
    # A first pull that ran to completion (both offsets captured)...
    write_capture(
        "20260901T090000", SEARCH, window_args(0), hit(1, offset=0), trailer(2, 1)
    )
    write_capture(
        "20260901T090010", SEARCH, window_args(1), hit(2, offset=1), trailer(2)
    )
    # ...then a later re-pull of the same bounds that comes back short (a
    # different total, and only its first page captured).
    write_capture(
        "20260902T090000", SEARCH, window_args(0), hit(1, offset=0), trailer(5, 1)
    )

    out = tmp_path / "out"
    try:
        wl.read_worklist("2026-03-10", "2026-03-12", out)
        raise AssertionError("expected a WorklistError")
    except wl.WorklistError as e:
        assert "INCOMPLETE pull" in str(e)
        assert "re-page these windows" in str(e)
    assert not out.exists()


def test_read_worklist_replaces_earlier_batch_files(tmp_path):
    write_capture(
        "20260901T090000", SEARCH, window_args(0), hit(1, offset=0), trailer(1)
    )
    out = tmp_path / "out"
    out.mkdir()
    (out / "batch-01.txt").write_text("stale\n", encoding="utf-8")
    (out / "batch-02.txt").write_text("stale\n", encoding="utf-8")

    result = wl.read_worklist("2026-03-10", "2026-03-12", out, per=10)
    assert [p.name for p in result.batches] == ["batch-01.txt"]
    assert not (out / "batch-02.txt").exists()


# --- attempted_ids and audit -----------------------------------------------------


def test_audit_counts_texts_missing_untried_and_bad_files():
    archive = store.archive_root()
    messages = archive / "messages"
    messages.mkdir(parents=True, exist_ok=True)

    owner = f"?owner={BOX}"
    atts = [
        {  # has stored text
            "name": "with-text.pdf",
            "contentType": "application/pdf",
            "uri": f"mail:///messages/M1%3D/attachments/A1{owner}",
        },
        {  # attempted (a capture asked for it) but no text was ever stored
            "name": "attempted.pdf",
            "contentType": "application/pdf",
            "uri": f"mail:///messages/M1%3D/attachments/A2{owner}",
        },
        {  # never attempted at all
            "name": "untried.docx",
            "contentType": "application/msword",
            "uri": f"mail:///messages/M1%3D/attachments/A3{owner}",
        },
        {  # a zip is expected to have no text, so it is not "listed"
            "name": "archive.zip",
            "contentType": "application/zip",
            "uri": f"mail:///messages/M1%3D/attachments/A4{owner}",
        },
        {  # inline image, but a text file sits in its slot anyway
            "name": "logo.png",
            "contentType": "image/png",
            "isInline": True,
            "uri": f"mail:///messages/M1%3D/attachments/A5{owner}",
        },
    ]
    m1 = {**message(1, atts), "receivedDateTime": "2026-03-15T00:00:00.000Z"}
    key = store.id_hash(m1["internetMessageId"])
    store.save_message(m1)
    (messages / f"{key}.1.txt").write_text("the text", encoding="utf-8")
    (messages / f"{key}.5.txt").write_text("stray text in an image slot", "utf-8")

    # A message outside the audited period.
    m2 = {**message(2), "receivedDateTime": "2025-01-01T00:00:00.000Z"}
    store.save_message(m2)

    # An unparseable message file.
    (messages / "badfile.json").write_text("{not json", encoding="utf-8")

    write_capture(
        "20260901T090000",
        READ,
        {"uri": f"mail:///messages/M1%3D/attachments/A2{owner}"},
        "attempted but the result went nowhere",
    )

    result = wl.audit("2026-03")

    assert result.counts["messages"] == 1
    assert result.counts["text-bearing"] == 4  # not the inline image
    assert result.counts["with text"] == 1
    assert result.missing == {
        "application/pdf": 1,
        "application/msword": 1,
        "application/zip": 1,
    }
    listed_names = [line.split()[0] for line in result.listed]
    assert f"{key}.2" in listed_names  # attempted.pdf
    assert f"{key}.3" in listed_names  # untried.docx
    assert f"{key}.4" not in listed_names  # a zip is never listed
    untried_names = [name for name, _, _ in result.untried]
    # A zip is never "listed" as missing, but it is still untried (only a
    # signature or a calendar invite is exempt from the untried check).
    assert untried_names == [f"{key}.3", f"{key}.4"]
    assert result.image_slots == [f"{key}.5"]
    assert result.bad == ["badfile.json"]


def test_attempted_ids_reads_the_attachment_id_from_a_read_capture():
    write_capture(
        "20260901T090000",
        READ,
        {"uri": f"mail:///messages/M1%3D/attachments/A9?owner={BOX}"},
        "some text",
    )
    assert wl.attempted_ids() == {"A9"}


@pytest.mark.parametrize("ctype", wl.CALENDAR)
def test_audit_counts_a_calendar_invite_but_never_lists_it(ctype):
    # An invite always comes back as a binary-attachment note, so a re-read
    # would not fill it: counted as missing, never listed or reported untried.
    invite = {
        "name": "invite.ics",
        "contentType": ctype,
        "uri": f"mail:///messages/M1%3D/attachments/A1?owner={BOX}",
    }
    store.save_message(
        {**message(1, [invite]), "receivedDateTime": "2026-03-15T00:00:00.000Z"}
    )
    result = wl.audit("2026-03")
    assert result.missing == {ctype: 1}
    assert result.listed == []
    assert result.untried == []


# --- the quiet period ------------------------------------------------------------


def last_written(path):
    return datetime.fromtimestamp(path.stat().st_mtime, UTC)


def test_read_worklist_refuses_while_a_capture_is_fresh(tmp_path):
    path = write_capture(
        "20260901T090000", SEARCH, window_args(0), hit(1, offset=0), trailer(1)
    )
    out = tmp_path / "out"
    out.mkdir()
    (out / "batch-01.txt").write_text("a reader's batch\n", encoding="utf-8")
    at = last_written(path)

    with pytest.raises(wl.WorklistError, match="last capture 89s ago \\(search\\)"):
        wl.read_worklist(
            "2026-03-10", "2026-03-12", out, now=at + timedelta(seconds=89)
        )
    assert (out / "batch-01.txt").read_text(encoding="utf-8") == "a reader's batch\n"

    result = wl.read_worklist(
        "2026-03-10", "2026-03-12", out, now=at + timedelta(seconds=90)
    )
    assert [p.name for p in result.batches] == ["batch-01.txt"]
    assert (out / "batch-01.txt").read_text(encoding="utf-8").startswith("msg-001 ")


def test_the_quiet_period_counts_any_mailbox_and_any_tool(tmp_path):
    write_capture(
        "20260901T090000", SEARCH, window_args(0), hit(1, offset=0), trailer(1)
    )
    # A read of another mailbox, written after the pull: still a live reader.
    read = write_capture(
        "20260901T100000",
        READ,
        {"uri": "mail:///messages/X?owner=events%40example.org"},
        message(7),
    )
    with pytest.raises(wl.WorklistError, match=r"\(read\)"):
        wl.read_worklist(
            "2026-03-10",
            "2026-03-12",
            tmp_path / "out",
            now=last_written(read) + timedelta(seconds=10),
        )
    assert not (tmp_path / "out").exists()


def test_an_empty_captures_directory_does_not_refuse(tmp_path):
    config.captured_dir().mkdir(parents=True, exist_ok=True)
    result = wl.read_worklist("2026-03-10", "2026-03-12", tmp_path / "out")
    assert (result.listed, result.todo, result.batches) == (0, [], [])


# --- mass sends ------------------------------------------------------------------


def test_read_worklist_reads_one_copy_of_a_mass_send(tmp_path):
    copies = [
        {
            **hit(n, offset=n - 1),
            "sender": "events@example.org",
            "subject": "Spring open house",
        }
        for n in (1, 2, 3)
    ]
    write_capture(
        "20260901T090000",
        SEARCH,
        window_args(0, before="2026-03-12T07:00:00Z"),
        *copies,
        hit(4, offset=3),
        trailer(4),
    )
    out = tmp_path / "out"
    unlisted = wl.read_worklist("2026-03-10", "2026-03-12", out)
    assert (len(unlisted.todo), unlisted.mass_copies) == (4, 0)

    (store.archive_root()).mkdir(parents=True, exist_ok=True)
    (store.archive_root() / "mass-sends.csv").write_text(
        "sender,subject,after,before,exemplar\n"
        "Events@Example.org,Spring open house,2026-03-10T12:00:00Z,"
        "2026-03-10T12:00:00Z,<m2@example.com>\n",
        encoding="utf-8",
    )
    result = wl.read_worklist("2026-03-10", "2026-03-12", out)
    assert result.listed == 4
    assert result.mass_copies == 2
    assert sorted(h["internetMessageId"] for h in result.todo) == [
        "<m2@example.com>",
        "<m4@example.com>",
    ]


@pytest.mark.parametrize("day", ["2026-3-10", "2026-13-01", "march"])
def test_read_worklist_refuses_a_malformed_bound(tmp_path, day):
    with pytest.raises(wl.WorklistError, match="not a YYYY-MM-DD date"):
        wl.read_worklist(day, "2026-03-12", tmp_path / "out")
    assert not (tmp_path / "out").exists()
