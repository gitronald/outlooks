"""Tests for the sender report: who writes to the mailbox, and how each is listed."""

from datetime import date

from typer.testing import CliRunner

from outlooks import senders as sd
from outlooks import store
from outlooks.cli import app

BOX = "desk@example.org"
runner = CliRunner()


def hit(n, sender, recipients=(BOX,), received="2026-03-10T12:00:00.000Z"):
    return {
        "uri": f"mail:///messages/ID{n}?owner=desk%40example.org",
        "id": f"ID{n}",
        "subject": f"message {n}",
        "sender": sender,
        "recipients": list(recipients),
        "receivedDateTime": received,
        "summary": "text",
        "internetMessageId": f"<m{n}@example.com>",
    }


def archive(*hits):
    store.save_hits(list(hits), BOX)


def test_senders_are_counted_marked_and_ordered():
    archive(
        hit(1, "alerts@tickets.example.net"),
        hit(2, "alerts@tickets.example.net"),
        hit(3, "Alerts@Tickets.example.net"),
        hit(4, "notices@system.example.org"),
        hit(5, "priya.nakamura@example.com"),
        hit(6, "morgan.ellery@example.net"),
        # Our own mail to someone else is not inbound; to ourselves, it is.
        hit(7, "desk@example.org", ["priya.nakamura@example.com"]),
        hit(8, "events@example.org", ["desk@example.org"]),
    )
    assert sd.senders(BOX) == [
        sd.Sender("alerts@tickets.example.net", 3, "unlisted"),
        sd.Sender("events@example.org", 1, "own"),
        sd.Sender("morgan.ellery@example.net", 1, "unlisted"),
        sd.Sender("notices@system.example.org", 1, "system"),
        sd.Sender("priya.nakamura@example.com", 1, "unlisted"),
    ]


def test_a_system_senders_mail_to_someone_else_is_counted():
    archive(hit(1, "notices@system.example.org", ["priya.nakamura@example.com"]))
    assert sd.senders(BOX) == [sd.Sender("notices@system.example.org", 1, "system")]


def test_since_is_a_date_in_the_configured_zone():
    archive(
        # 2026-03-09 23:30 Pacific, 2026-03-10 in UTC.
        hit(1, "early@example.com", received="2026-03-10T06:30:00.000Z"),
        hit(2, "late@example.com", received="2026-03-10T07:30:00.000Z"),
    )
    found = sd.senders(BOX, date(2026, 3, 10))
    assert [s.address for s in found] == ["late@example.com"]


def test_another_mailboxes_hits_are_not_counted():
    store.save_hits([hit(1, "someone@example.com")], "other@example.org")
    assert sd.senders(BOX) == []
    assert [s.address for s in sd.senders("other@example.org")] == [
        "someone@example.com"
    ]


def test_cli_senders_prints_the_counts_and_writes_nothing(tmp_path):
    archive(
        hit(1, "alerts@tickets.example.net"),
        hit(2, "alerts@tickets.example.net"),
        hit(3, "notices@system.example.org"),
    )
    before = sorted(p for p in tmp_path.rglob("*") if p.is_file())
    result = runner.invoke(app, ["senders", "--since", "2026-01-01"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        f"senders for {BOX} since 2026-01-01: 3 inbound hits",
        "     2  unlisted  alerts@tickets.example.net",
        "     1  system    notices@system.example.org",
        "1 unlisted; an address that is a notifier, not a person, "
        "belongs in system_senders",
    ]
    assert sorted(p for p in tmp_path.rglob("*") if p.is_file()) == before


def test_cli_senders_refuses_a_malformed_since():
    result = runner.invoke(app, ["senders", "--since", "nope"])
    assert result.exit_code == 1
    assert "Error: 'nope' is not a YYYY-MM-DD date" in result.output
