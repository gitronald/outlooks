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


# --- attachment_reads and audit --------------------------------------------------


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


def test_attachment_reads_takes_the_attachment_id_from_a_read_capture():
    write_capture(
        "20260901T090000",
        READ,
        {"uri": f"mail:///messages/M1%3D/attachments/A9?owner={BOX}"},
        "some text",
    )
    assert wl.attachment_reads() == ({"A9"}, set())


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


@pytest.mark.parametrize(
    "name",
    ["PNG image", "JPEG image", "img-3f2a9c1e-77b0-4c1d-9e1a-0d5b6c7e8f90", "x.ai"],
)
def test_audit_skips_an_image_sent_as_octet_stream(name):
    # The connector sends some images as octet-stream under a placeholder
    # name; only a read would show they are images, so the sweep never lists
    # them, and a text file in their slot is reported like any image's.
    image = {
        "name": name,
        "contentType": "application/octet-stream",
        "uri": f"mail:///messages/M1%3D/attachments/A1?owner={BOX}",
    }
    m1 = {**message(1, [image]), "receivedDateTime": "2026-03-15T00:00:00.000Z"}
    store.save_message(m1)
    result = wl.audit("2026-03")
    assert result.counts["text-bearing"] == 0
    assert (result.missing, result.listed, result.untried) == ({}, [], [])

    key = store.id_hash(m1["internetMessageId"])
    (store.archive_root() / "messages" / f"{key}.1.txt").write_text("x", "utf-8")
    assert wl.audit("2026-03").image_slots == [f"{key}.1"]


@pytest.mark.parametrize(
    "ctype", ["application/pdf", "application/octet-stream", "text/calendar"]
)
def test_audit_counts_a_binary_note_apart_from_missing(ctype):
    # A read that got the connector's binary note gets it again on a re-read,
    # so the attachment is counted as binary, never listed as missing.
    owner = f"?owner={BOX}"
    report = {
        "name": "report.pdf",
        "contentType": ctype,
        "uri": f"mail:///messages/M1%3D/attachments/A1%3D{owner}",
    }
    store.save_message(
        {**message(1, [report]), "receivedDateTime": "2026-03-15T00:00:00.000Z"}
    )
    note = {
        "note": "Binary attachment; content is not available as text.",
        "attachment": {"name": "report.pdf", "contentType": ctype},
    }
    write_capture(
        "20260901T090000",
        READ,
        {"uri": f"mail:///messages/M1%3D/attachments/A1%3D{owner}"},
        note,
    )
    result = wl.audit("2026-03")
    assert result.binary == {ctype: 1}
    assert (result.missing, result.listed, result.untried) == ({}, [], [])


def test_attachment_reads_takes_only_the_connector_s_note_as_binary():
    # Text that mentions the note, or the parent message read back in place of
    # the attachment, is not the note: those reads are tried, not binary.
    owner = f"?owner={BOX}"
    reads = {
        "A1": "the letter",
        "A2": {
            "note": "Binary attachment; content is not available as text.",
            "attachment": {"name": "report.pdf", "contentType": "application/pdf"},
        },
        "A3": "Note: a binary attachment was removed from this message.",
        "A4": {**message(1), "bodyPreview": "Binary attachment: see below"},
    }
    for n, (att, response) in enumerate(reads.items()):
        write_capture(
            f"20260901T09000{n}",
            READ,
            {"uri": f"mail:///messages/M1%3D/attachments/{att}{owner}"},
            response,
        )
    assert wl.attachment_reads() == (set(reads), {"A2"})


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


# --- the last read round ---------------------------------------------------------

T0 = datetime(2026, 9, 1, 16, 0, tzinfo=UTC)


def reader_read(n, agent, ends, ms=2000.0, call=None):
    """A read by subagent ``agent`` that finished ``ends`` seconds after T0."""
    folder = config.captured_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"r{n:03d}-1.json"
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": READ,
        "tool_input": {"uri": f"mail:///messages/ID{n}?owner={BOX}"},
        "tool_response": blocks(message(n)),
        "tool_use_id": call or f"call-{n}",
        "agent_id": agent,
        "agent_type": "general-purpose",
    }
    if ms is not None:
        payload["duration_ms"] = ms
    path.write_text(json.dumps(payload), encoding="utf-8")
    when = (T0 + timedelta(seconds=ends)).timestamp()
    os.utime(path, (when, when))


def captures():
    from outlooks.capture import load_captures

    return load_captures([config.captured_dir()])[0]


def test_last_round_counts_the_newest_readers_and_their_rate():
    # An earlier round, over before the last one starts.
    reader_read(1, "old", 0)
    reader_read(2, "old", 10)
    # The last round: two readers whose spans overlap, one call at a time.
    reader_read(3, "a", 100)
    reader_read(4, "a", 110)
    reader_read(5, "b", 105)
    reader_read(6, "b", 158)
    found = wl.last_round(captures())
    assert found is not None
    assert (found.readers, found.reads, found.parallel) == (2, 4, {})
    assert found.end - found.start == timedelta(seconds=60)
    assert found.per_minute == 4


def test_last_round_names_a_reader_that_read_in_parallel():
    reader_read(1, "a", 100, ms=3000)
    reader_read(2, "a", 101, ms=3000)  # started at 98, before 1 ended
    reader_read(3, "a", 110, ms=3000)
    reader_read(4, "b", 104, ms=3000)
    found = wl.last_round(captures())
    assert found is not None
    assert found.parallel == {"a": 1}


def test_last_round_without_durations_and_with_a_doubled_capture():
    # Untimed reads under a second apart were made together; a call captured
    # by two hooks is one read, not two in parallel.
    reader_read(1, "a", 100, ms=None)
    reader_read(2, "a", 100.5, ms=None)
    reader_read(3, "a", 110, ms=None)
    reader_read(4, "a", 110, ms=None, call="call-3")
    found = wl.last_round(captures())
    assert found is not None
    assert (found.reads, found.parallel) == (3, {"a": 1})


def test_last_round_leaves_out_the_main_session_s_reads():
    write_capture("20260901T090000", READ, {"uri": "mail:///messages/ID1"}, "x")
    assert wl.last_round(captures()) is None


def test_import_reports_the_last_round_and_its_parallel_reads():
    from typer.testing import CliRunner

    from outlooks.cli import app

    reader_read(1, "a", 100, ms=3000)
    reader_read(2, "a", 101, ms=3000)
    reader_read(3, "b", 102, ms=3000)
    reader_read(4, "b", 160, ms=3000)
    result = CliRunner().invoke(app, ["import"])
    assert result.exit_code == 0, result.output
    assert "last read round: 2 readers, 4 reads in 1m03s (4 a minute)" in (
        result.output
    )
    assert "PARALLEL reads by 1 of 2 readers (1 made while" in result.output


@pytest.mark.parametrize(
    "payload",
    [None, [], 17, {"receivedDateTime": "2026-03-01", "attachments": [None]}],
)
def test_audit_reports_malformed_message_shapes(tmp_path, payload):
    messages = tmp_path / "messages"
    messages.mkdir()
    (messages / "bad.json").write_text(json.dumps(payload), encoding="utf-8")
    result = wl.audit("2026-03", archive=tmp_path)
    assert result.bad == ["bad.json"]
    assert result.counts == {}


def test_attachment_reads_decodes_the_resource_path_before_matching():
    write_capture(
        "20260901T090000",
        READ,
        {"uri": "mail:///messages/M1%2Fattachments%2FA9%3D?owner=desk%40example.org"},
        "attachment text",
    )
    assert wl.attachment_reads() == ({"A9="}, set())


def test_read_worklist_refuses_a_nonpositive_per_before_clearing(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "batch-01.txt").write_text("a reader's batch\n", encoding="utf-8")
    with pytest.raises(wl.WorklistError, match="per must be at least 1"):
        wl.read_worklist("2026-03-10", "2026-03-12", out, per=0)
    assert (out / "batch-01.txt").read_text(encoding="utf-8") == "a reader's batch\n"
