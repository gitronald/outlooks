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
