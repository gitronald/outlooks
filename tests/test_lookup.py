"""Tests for splitting a lookup's hits into archived and to-read, and check().

Hits are built directly here (a lookup's page files), matching the shape
``outlook_email_search`` returns; the archive is a bare ``messages/`` folder
under a scratch ``tmp_path``, not the shared conftest fixture's archive dir,
so the tests can pass explicit ``scratch``/``archive`` paths per the module's
own contract.
"""

import json

from outlooks import lookup as lk
from outlooks import store

RECEIVED = "2026-01-01T00:00:00.000Z"


def hit(
    mid, sender, recipients, subject="subject", summary="summary", received=RECEIVED
):
    return {
        "uri": f"mail:///messages/{mid.strip('<>')}?owner=desk%40example.org",
        "internetMessageId": mid,
        "sender": sender,
        "recipients": recipients,
        "subject": subject,
        "summary": summary,
        "receivedDateTime": received,
    }


def write_page(scratch, name, n, hits):
    scratch.mkdir(parents=True, exist_ok=True)
    path = scratch / f"{name}-page-{n:02d}.json"
    path.write_text(json.dumps(hits), encoding="utf-8")
    return path


def archive_message(archive, h, **extra):
    """Store a stub full message for ``h`` so :func:`lookup.split` finds it archived."""
    messages = archive / "messages"
    messages.mkdir(parents=True, exist_ok=True)
    message = {
        "internetMessageId": h["internetMessageId"],
        "receivedDateTime": h["receivedDateTime"],
        "subject": h["subject"],
        "sender": {"address": h["sender"]},
        "toRecipients": [{"address": r} for r in h["recipients"]],
        "bodyPreview": h.get("summary", ""),
        "webLink": "https://outlook.office365.com/owa/?ItemID=x",
        "attachments": [],
        **extra,
    }
    path = messages / f"{store.id_hash(h['internetMessageId'])}.json"
    path.write_text(json.dumps(message), encoding="utf-8")
    return path


# --- matching -----------------------------------------------------------------


def test_split_matches_on_sender_recipients_and_subject(tmp_path):
    scratch, archive = tmp_path / "scratch", tmp_path / "archive"
    by_sender = hit("<a1>", "priya.nakamura@example.com", ["desk@example.org"])
    by_recipient = hit("<a2>", "someone@example.com", ["priya.nakamura@example.com"])
    by_subject = hit(
        "<a3>", "x@example.com", ["desk@example.org"], subject="Priya's notes"
    )
    no_match = hit("<a4>", "x@example.com", ["desk@example.org"], subject="unrelated")
    write_page(scratch, "priya", 1, [by_sender, by_recipient, by_subject, no_match])

    result = lk.split("priya", ["priya"], scratch=scratch, archive=archive)
    ids = {h["internetMessageId"] for _, h in [*result.known, *result.to_read]}
    assert ids == {"<a1>", "<a2>", "<a3>"}


def test_split_matches_the_summary_only_with_match_summary(tmp_path):
    scratch, archive = tmp_path / "scratch", tmp_path / "archive"
    named_in_summary = hit(
        "<b1>",
        "someone@example.com",
        ["desk@example.org"],
        subject="unrelated",
        summary="a note about Priya's proposal",
    )
    write_page(scratch, "priya", 1, [named_in_summary])

    with_summary = lk.split("priya", ["priya"], scratch=scratch, archive=archive)
    assert with_summary.total == 1

    without_summary = lk.split(
        "priya", ["priya"], scratch=scratch, archive=archive, match_summary=False
    )
    assert without_summary.total == 0


# --- system-sender pairing ------------------------------------------------------


def test_split_pairs_a_system_notice_to_us_near_a_matched_system_hit(tmp_path):
    scratch, archive = tmp_path / "scratch", tmp_path / "archive"
    matched_system = hit(
        "<s1>",
        "notices@system.example.org",
        ["priya.nakamura@example.com"],  # matches on the recipient address itself
        subject="New request received",
        summary="generic notice",
        received="2026-01-03T10:00:00.000Z",
    )
    paired_to_us = hit(
        "<s2>",
        "notices@system.example.org",
        ["desk@example.org"],  # to us, no term anywhere, but 30s after <s1>
        subject="New request received",
        summary="generic notice",
        received="2026-01-03T10:00:30.000Z",
    )
    too_late_to_us = hit(
        "<s3>",
        "notices@system.example.org",
        ["desk@example.org"],
        subject="New request received",
        summary="generic notice",
        received="2026-01-03T10:05:00.000Z",  # 5 minutes later: outside the pair window
    )
    write_page(scratch, "priya", 1, [matched_system, paired_to_us, too_late_to_us])

    result = lk.split("priya", ["priya"], scratch=scratch, archive=archive)
    ids = {h["internetMessageId"] for _, h in [*result.known, *result.to_read]}
    assert ids == {"<s1>", "<s2>"}


# --- archived vs to-read, and batches -------------------------------------------


def test_split_writes_timelines_for_archived_hits_and_batches_the_rest(tmp_path):
    scratch, archive = tmp_path / "scratch", tmp_path / "archive"
    archived_hit = hit("<c1>", "priya.nakamura@example.com", ["desk@example.org"])
    archive_message(archive, archived_hit)
    unread = [
        hit(f"<c{n}>", "priya.nakamura@example.com", ["desk@example.org"])
        for n in (2, 3, 4)
    ]
    write_page(scratch, "priya", 1, [archived_hit, *unread])

    batches = tmp_path / "batches"
    result = lk.split(
        "priya", ["priya"], scratch=scratch, archive=archive, batches=batches, per=2
    )

    assert result.pages == 1
    assert result.total == 4
    assert [nn for nn, h in result.known] == ["01"]
    assert [h["internetMessageId"] for _, h in result.to_read] == [
        "<c2>",
        "<c3>",
        "<c4>",
    ]
    timeline = json.loads((scratch / "priya-01.timeline.json").read_text())
    assert timeline["internet_message_id"] == "<c1>"
    assert timeline["source"] == "archive"

    assert [p.name for p in result.batches] == [
        "priya-batch-01.txt",
        "priya-batch-02.txt",
    ]
    first_batch = (batches / "priya-batch-01.txt").read_text().splitlines()
    assert len(first_batch) == 2
    assert first_batch[0].startswith("priya-02 ")
    assert first_batch[1].startswith("priya-03 ")
    second_batch = (batches / "priya-batch-02.txt").read_text().splitlines()
    assert len(second_batch) == 1 and second_batch[0].startswith("priya-04 ")


def test_split_removes_stale_timeline_files_before_renumbering(tmp_path):
    scratch, archive = tmp_path / "scratch", tmp_path / "archive"
    stale = scratch / "priya-07.timeline.json"
    scratch.mkdir(parents=True)
    stale.write_text("{}", encoding="utf-8")
    write_page(scratch, "priya", 1, [])

    lk.split("priya", ["priya"], scratch=scratch, archive=archive)
    assert not stale.exists()


def test_a_lookup_of_100_or_more_hits_is_renumbered_and_checked_in_full(tmp_path):
    scratch, archive = tmp_path / "scratch", tmp_path / "archive"
    hits = [
        hit(
            f"<e{n}>",
            "priya.nakamura@example.com",
            ["desk@example.org"],
            received=f"2026-01-01T00:{n // 60:02d}:{n % 60:02d}.000Z",
        )
        for n in range(1, 101)
    ]
    for h in hits:
        archive_message(archive, h)
    write_page(scratch, "priya", 1, hits)
    # A three-digit file from an earlier run, and files that are not this
    # lookup's timelines: another lookup's, and this one's result page.
    (scratch / "priya-101.timeline.json").write_text("{}", encoding="utf-8")
    other = scratch / "priya-2024-01.timeline.json"
    other.write_text("{}", encoding="utf-8")

    result = lk.split("priya", ["priya"], scratch=scratch, archive=archive)
    assert [nn for nn, _ in result.known][-2:] == ["99", "100"]
    assert not (scratch / "priya-101.timeline.json").exists()
    assert other.exists() and (scratch / "priya-page-01.json").exists()

    rows = lk.check("priya", scratch=scratch, archive=archive)
    assert [r.nn for r in rows] == [f"{n:02d}" for n in range(1, 101)]
    assert all(r.ok for r in rows)


def test_split_filters_by_since(tmp_path):
    scratch, archive = tmp_path / "scratch", tmp_path / "archive"
    early = hit(
        "<d1>",
        "priya.nakamura@example.com",
        ["desk@example.org"],
        received="2026-01-01T00:00:00.000Z",
    )
    late = hit(
        "<d2>",
        "priya.nakamura@example.com",
        ["desk@example.org"],
        received="2026-02-01T00:00:00.000Z",
    )
    write_page(scratch, "priya", 1, [early, late])

    result = lk.split(
        "priya", ["priya"], scratch=scratch, archive=archive, since="2026-01-15"
    )
    assert [h["internetMessageId"] for _, h in result.to_read] == ["<d2>"]


# --- check ----------------------------------------------------------------------


def test_check_reports_ok_and_bad_rows(tmp_path):
    scratch, archive = tmp_path / "scratch", tmp_path / "archive"
    good_hit = hit("<e1>", "priya.nakamura@example.com", ["desk@example.org"])
    archive_message(archive, good_hit)
    write_page(scratch, "priya", 1, [good_hit])
    lk.split("priya", ["priya"], scratch=scratch, archive=archive)

    # A second timeline claiming a message the archive never received.
    bad = {
        "file": "nowhere",
        "received_utc": "2026-01-01T00:00:00.000Z",
        "date_local": "2025-12-31",  # deliberately wrong
        "direction": "in",
        "sender": "nobody@example.com",
        "to": ["desk@example.org"],
        "subject": "missing",
        "internet_message_id": "<not-archived>",
        "weblink": "",
        "attachments": [],
        "summary": "",
        "system": False,
        "source": "manual",
    }
    (scratch / "priya-02.timeline.json").write_text(json.dumps(bad), encoding="utf-8")

    rows = lk.check("priya", scratch=scratch, archive=archive)
    by_nn = {r.nn: r for r in rows}
    assert by_nn["01"].ok is True
    assert by_nn["02"].ok is False


def test_is_system_and_direction():
    from outlooks import config

    assert lk.is_system("Notices@SYSTEM.Example.Org")
    assert not lk.is_system("priya.nakamura@example.com")
    assert lk.direction("desk@example.org", ["someone@example.com"]) == "out"
    assert lk.direction("priya.nakamura@example.com", ["desk@example.org"]) == "in"
    # System mail not addressed to us is outbound (a notice to a correspondent).
    own = config.settings().own_addresses
    assert "desk@example.org" in own
    assert (
        lk.direction("notices@system.example.org", ["priya.nakamura@example.com"])
        == "out"
    )
    assert lk.direction("notices@system.example.org", ["desk@example.org"]) == "in"


# --- timeline keys ------------------------------------------------------------


def timeline(scratch, name, nn, **changes):
    """A reader's timeline file for an archived message, with ``changes``."""
    record = {
        "file": "messages/x.json",
        "received_utc": RECEIVED,
        "date_local": "2025-12-31",
        "direction": "in",
        "sender": "priya.nakamura@example.com",
        "to": ["desk@example.org"],
        "subject": "subject",
        "internet_message_id": "<e1>",
        "weblink": "",
        "attachments": [],
        "summary": "",
        "system": False,
        **changes,
    }
    record = {k: v for k, v in record.items() if v is not None}
    scratch.mkdir(parents=True, exist_ok=True)
    path = scratch / f"{name}-{nn}.timeline.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_check_says_why_a_row_is_bad(tmp_path):
    scratch, archive = tmp_path / "scratch", tmp_path / "archive"
    archive_message(
        archive, hit("<e1>", "priya.nakamura@example.com", ["desk@example.org"])
    )
    timeline(scratch, "priya", "01")
    timeline(scratch, "priya", "02", date_local="2026-01-01")
    timeline(scratch, "priya", "03", internet_message_id="<never-read>")
    timeline(scratch, "priya", "04", date_local=None)
    timeline(scratch, "priya", "05", internet_message_id=None, date_local="")
    rows = lk.check("priya", scratch=scratch, archive=archive)
    assert [(r.nn, r.ok, r.finding) for r in rows] == [
        ("01", True, ""),
        ("02", False, "date_local 2026-01-01, the archive says 2025-12-31"),
        ("03", False, "not archived"),
        ("04", False, "missing key date_local"),
        ("05", False, "missing key internet_message_id, date_local"),
    ]
    # A file missing a key is still placed: the archive's date is reported.
    assert rows[3].day == "2025-12-31" and rows[4].day == ""


def test_the_split_writes_exactly_the_documented_keys(tmp_path):
    scratch, archive = tmp_path / "scratch", tmp_path / "archive"
    h = hit("<e1>", "priya.nakamura@example.com", ["desk@example.org"])
    archive_message(archive, h)
    write_page(scratch, "priya", 1, [h])
    lk.split("priya", ["priya"], scratch=scratch, archive=archive)
    written = json.loads((scratch / "priya-01.timeline.json").read_text("utf-8"))
    assert tuple(written) == lk.TIMELINE_KEYS
    assert set(lk.REQUIRED_KEYS) <= set(lk.CHECK_READS) <= set(lk.TIMELINE_KEYS)


def test_the_timeline_reference_lists_every_key_as_the_code_reads_it():
    from importlib.resources import files

    folder = files("outlooks.prompts") / "skills/lookup/references"
    rows = {}
    for line in (folder / "timeline.md").read_text("utf-8").splitlines():
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == 4 and cells[0].startswith("`"):
            rows[cells[0].strip("`")] = (cells[2] == "yes", cells[3] == "yes")
    assert tuple(rows) == lk.TIMELINE_KEYS
    assert {k for k, (reads, _) in rows.items() if reads} == set(lk.CHECK_READS)
    assert {k for k, (_, needs) in rows.items() if needs} == set(lk.REQUIRED_KEYS)
    # The reader's brief names every key a reader writes.
    brief = (folder / "reader-brief.md").read_text("utf-8")
    for key in lk.TIMELINE_KEYS:
        assert f"`{key}`" in brief or key == "source", key


# --- reset --------------------------------------------------------------------


def test_reset_moves_a_names_pages_and_timelines_and_nothing_else(tmp_path):
    from datetime import datetime

    scratch = tmp_path / "scratch"
    h = hit("<e1>", "priya.nakamura@example.com", ["desk@example.org"])
    mine = [
        write_page(scratch, "priya", 1, [h]),
        write_page(scratch, "priya-sender", 1, [h]),
        write_page(scratch, "priya-sent", 2, [h]),
        timeline(scratch, "priya", "01"),
    ]
    others = [
        write_page(scratch, "morgan", 1, [h]),
        timeline(scratch, "morgan", "01"),
        timeline(scratch, "priya-n", "01"),
        # Another lookup, whose name starts with this one's.
        write_page(scratch, "priya-n", 1, [h]),
        write_page(scratch, "priya-n-sent", 1, [h]),
    ]
    now = datetime(2026, 1, 5, 10, 15, 0)
    folder, moved = lk.reset("priya", scratch=scratch, now=now)
    assert folder == scratch / "earlier" / "20260105T101500"
    assert sorted(p.name for p in moved) == sorted(p.name for p in mine)
    assert all(p.parent == folder and p.exists() for p in moved)
    assert not any(p.exists() for p in mine)
    assert all(p.exists() for p in others)
    # The split now finds nothing of the earlier lookup.
    assert lk.split("priya", ["priya"], scratch=scratch, archive=tmp_path).pages == 0


def test_reset_never_overwrites_an_earlier_reset(tmp_path):
    from datetime import datetime

    scratch = tmp_path / "scratch"
    now = datetime(2026, 1, 5, 10, 15, 0)
    h = hit("<e1>", "priya.nakamura@example.com", ["desk@example.org"])
    write_page(scratch, "priya", 1, [h])
    first, _ = lk.reset("priya", scratch=scratch, now=now)
    write_page(scratch, "priya", 1, [])
    second, moved = lk.reset("priya", scratch=scratch, now=now)
    assert second == scratch / "earlier" / "20260105T101500-2"
    assert json.loads((first / "priya-page-01.json").read_text("utf-8")) == [h]
    assert json.loads(moved[0].read_text("utf-8")) == []


def test_reset_with_nothing_to_move_creates_nothing(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _, moved = lk.reset("priya", scratch=scratch)
    assert moved == [] and list(scratch.iterdir()) == []


def test_page_files_are_the_names_own_and_no_longer_names(tmp_path):
    scratch = tmp_path / "scratch"
    h = hit("<e1>", "priya.nakamura@example.com", ["desk@example.org"])
    mine = [write_page(scratch, f"priya{kind}", 1, [h]) for kind in ("", "-sender")]
    mine += [write_page(scratch, f"priya-{kind}", 12, []) for kind in lk.PAGE_KINDS]
    write_page(scratch, "priya-n", 1, [h])
    write_page(scratch, "priya-n-title", 1, [h])
    (scratch / "priya-page-notes.json").write_text("[]", encoding="utf-8")
    assert lk.page_files(scratch, "priya") == sorted(set(mine))
    assert len(lk.page_files(scratch, "priya-n")) == 2


def test_split_leaves_out_the_pages_of_a_longer_name(tmp_path):
    scratch = tmp_path / "scratch"
    mine = hit("<e1>", "priya.nakamura@example.com", ["desk@example.org"])
    other = hit("<e2>", "priya.nakamura@example.com", ["desk@example.org"])
    write_page(scratch, "priya", 1, [mine])
    write_page(scratch, "priya-n", 1, [other])
    out = lk.split("priya", ["priya"], scratch=scratch, archive=tmp_path)
    assert out.pages == 1
