"""CliRunner tests for the read-only ``ledger`` and ``thread`` commands."""

import csv

import pytest
from typer.testing import CliRunner

from outlooks import config, store
from outlooks.cli import app

runner = CliRunner()

BOX = "desk@example.org"


def write_ledger(rows, columns=("internet_message_id", "received", "outcome")):
    path = config.archive_dir() / "ledger.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return path


def row(n, received, outcome, **extra):
    return {
        "internet_message_id": f"<m{n}@example.com>",
        "received": received,
        "outcome": outcome,
        **extra,
    }


def message(mid, received, sender, to, subject, body, conversation="conv-1"):
    payload = {
        "internetMessageId": mid,
        "receivedDateTime": received,
        "subject": subject,
        "sender": {"name": sender.split("@")[0], "address": sender},
        "toRecipients": [{"address": a} for a in to],
        "body": {"contentType": "text", "content": body},
    }
    if conversation:
        payload["conversationId"] = conversation
    store.save_message(payload)
    return payload


def hit(mid, received, sender, subject, summary="a summary"):
    store.save_hits(
        [
            {
                "uri": f"mail:///messages/{mid}?owner=desk%40example.org",
                "id": mid,
                "internetMessageId": mid,
                "receivedDateTime": received,
                "sender": sender,
                "recipients": [BOX],
                "subject": subject,
                "summary": summary,
            }
        ]
    )


@pytest.fixture(autouse=True)
def wide_terminal(monkeypatch):
    """A terminal wide enough that no test row is cut unless a test narrows it."""
    monkeypatch.setenv("COLUMNS", "200")


def invoke(*args):
    return runner.invoke(app, list(args))


# --- ledger ---------------------------------------------------------------------


@pytest.fixture
def three_outcomes():
    return write_ledger(
        [
            row(1, "2026-03-01T17:00:00Z", "filed"),
            row(2, "2026-03-02T17:00:00Z", "deferred"),
            row(3, "2026-03-03T17:00:00Z", "skipped"),
            row(4, "2026-03-04T17:00:00Z", "skipped"),
        ]
    )


def test_ledger_with_no_filter_counts_rows_by_outcome(three_outcomes):
    result = invoke("ledger")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == f"ledger {three_outcomes} for {BOX}"
    assert lines[1:] == [
        "     2  skipped",
        "     1  deferred",
        "     1  filed",
        "     4  total",
    ]


def test_ledger_since_keeps_rows_on_or_after_the_local_date(three_outcomes):
    # 2026-03-03T17:00Z is 09:00 on the 3rd in Los Angeles.
    result = invoke("ledger", "--since", "2026-03-03")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[1:] == ["     2  skipped", "     2  total"]


def test_ledger_since_refuses_a_bad_date(three_outcomes):
    result = invoke("ledger", "--since", "March")
    assert result.exit_code == 1
    assert "Error: 'March' is not a YYYY-MM-DD date" in result.output


def test_ledger_outcome_lists_the_rows_oldest_first(three_outcomes):
    hit("<m3@example.com>", "2026-03-03T17:00:00Z", "ada@example.com", "Hello")
    result = invoke("ledger", "--outcome", "skipped")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split() == ["received", "key", "sender", "subject"]
    key3 = store.id_hash("<m3@example.com>")[:8]
    key4 = store.id_hash("<m4@example.com>")[:8]
    assert lines[1].split() == ["2026-03-03", "09:00", key3, "ada@example.com", "Hello"]
    # Not in the archive: no sender or subject to show.
    assert lines[2].split() == ["2026-03-04", "09:00", key4]
    assert lines[3] == "2 rows"


def test_ledger_repeated_outcome_lists_both_and_names_each_rows_outcome(
    three_outcomes,
):
    result = invoke("ledger", "--outcome", "filed", "--outcome", "deferred")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split()[:3] == ["received", "key", "outcome"]
    assert [line.split()[3] for line in lines[1:3]] == ["filed", "deferred"]
    assert lines[3] == "2 rows"


def test_ledger_outcome_no_row_has_names_the_words_it_does_have(three_outcomes):
    result = invoke("ledger", "--outcome", "parked")
    assert result.exit_code == 0, result.output
    assert result.output.strip() == (
        "no rows with outcome parked; the ledger's outcomes: skipped, deferred, filed"
    )


def test_ledger_takes_the_outcome_word_literally(three_outcomes):
    result = invoke("ledger", "--outcome", "Deferred")
    assert "no rows with outcome Deferred" in result.output


def test_ledger_shows_the_profiles_columns_in_the_ledgers_order():
    columns = (
        "internet_message_id",
        "received",
        "kind",
        "outcome",
        "subject",
        "note",
    )
    write_ledger(
        [
            row(
                1,
                "2026-03-01T17:00:00Z",
                "deferred",
                kind="inquiry",
                subject="From the ledger",
                note="ask about dates",
            )
        ],
        columns,
    )
    hit("<m1@example.com>", "2026-03-01T17:00:00Z", "ada@example.com", "Archived")
    result = invoke("ledger", "--outcome", "deferred")
    assert result.exit_code == 0, result.output
    header, line = result.output.splitlines()[:2]
    # The ledger has a subject, so only the sender comes from the archive.
    assert header.split() == ["received", "key", "sender", "kind", "subject", "note"]
    assert "From the ledger" in line
    assert "Archived" not in line
    assert line.rstrip().endswith("ask about dates")


def test_ledger_rows_of_another_mailbox_are_left_out():
    write_ledger(
        [
            row(1, "2026-03-01T17:00:00Z", "deferred", mailbox=BOX),
            row(2, "2026-03-02T17:00:00Z", "deferred", mailbox="other@example.org"),
        ],
        ("mailbox", "internet_message_id", "received", "outcome"),
    )
    result = invoke("ledger", "--outcome", "deferred")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    # mailbox is not shown: every row listed is the one mailbox's.
    assert lines[0].split() == ["received", "key", "sender", "subject"]
    assert lines[-1] == "1 row"


def test_ledger_cuts_long_values_to_the_terminal_and_wide_prints_them(
    monkeypatch,
):
    note = "a long note " * 20
    write_ledger(
        [row(1, "2026-03-01T17:00:00Z", "deferred", note=note.strip())],
        ("internet_message_id", "received", "outcome", "note"),
    )
    monkeypatch.setenv("COLUMNS", "80")
    result = invoke("ledger", "--outcome", "deferred")
    assert result.exit_code == 0, result.output
    line = result.output.splitlines()[1]
    assert len(line) == 80
    assert line.endswith("…")
    wide = invoke("ledger", "--outcome", "deferred", "--wide")
    assert wide.output.splitlines()[1].endswith(note.strip())


def test_ledger_names_a_missing_ledger_file():
    result = invoke("ledger", "--ledger", "nowhere.csv")
    assert result.exit_code == 1
    assert "Error: nowhere.csv is not a file" in result.output


def test_ledger_with_no_ledger_yet_counts_nothing():
    result = invoke("ledger")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[-1] == "     0  total"


# --- thread ---------------------------------------------------------------------


@pytest.fixture
def conversation():
    first = message(
        "<t1@example.com>",
        "2026-03-01T17:00:00Z",
        "ada@example.com",
        [BOX],
        "Workshop dates",
        "Hello there,\nAre the dates fixed?",
    )
    message(
        "<t2@example.com>",
        "2026-03-02T17:00:00Z",
        BOX,
        ["ada@example.com"],
        "RE: Workshop dates",
        "\n\nNot yet; next week.",
    )
    # Never read in full: joins by subject.
    hit(
        "<t3@example.com>",
        "2026-03-03T17:00:00Z",
        "ada@example.com",
        "Re: Workshop dates",
        "Thanks, I'll wait.",
    )
    # Another conversation with the same subject: not joined.
    message(
        "<t4@example.com>",
        "2026-03-04T17:00:00Z",
        "bo@example.com",
        [BOX],
        "Workshop dates",
        "Unrelated.",
        conversation="conv-2",
    )
    return first


def test_thread_by_full_id_lists_the_conversation_oldest_first(conversation):
    result = invoke("thread", "<t1@example.com>")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == f"thread of {store.id_hash('<t1@example.com>')} (3 messages, PT)"
    assert lines[1] == "* 2026-03-01 09:00  in   ada@example.com  Workshop dates"
    assert lines[2] == "      Hello there,"
    assert lines[3] == f"  2026-03-02 09:00  out  {BOX}  RE: Workshop dates"
    assert lines[4] == "      Not yet; next week."
    assert lines[5] == (
        "  2026-03-03 09:00  in   ada@example.com  Re: Workshop dates"
        "  (matched by subject)"
    )
    assert lines[6] == "      Thanks, I'll wait."
    assert lines[7] == "last written by ada@example.com (in) at 2026-03-03 09:00"
    assert "Unrelated" not in result.output


def test_thread_by_key_prefix_marks_the_message_named(conversation):
    key = store.id_hash("<t1@example.com>")
    second = store.id_hash("<t2@example.com>")
    result = invoke("thread", second[:6].upper())
    assert result.exit_code == 0, result.output
    assert f"thread of {second}" in result.output
    assert f"thread of {key}" not in result.output
    marked = [line for line in result.output.splitlines() if line.startswith("*")]
    assert marked == [f"* 2026-03-02 09:00  out  {BOX}  RE: Workshop dates"]


def test_thread_of_a_hit_never_read_joins_everything_by_subject(conversation):
    result = invoke("thread", "<t3@example.com>")
    assert result.exit_code == 0, result.output
    # With no conversationId to go on, the other subject match joins too.
    assert "(4 messages," in result.output
    assert result.output.count("(matched by subject)") == 3
    assert result.output.splitlines()[-1] == (
        "last written by bo@example.com (in) at 2026-03-04 09:00"
    )


def test_thread_refuses_an_ambiguous_prefix():
    # Two ids whose archive keys share their first six characters.
    hit("<n2465@example.com>", "2026-03-01T17:00:00Z", "ada@example.com", "One")
    hit("<n4057@example.com>", "2026-03-02T17:00:00Z", "ada@example.com", "Two")
    result = invoke("thread", "230ed6")
    assert result.exit_code == 1
    assert "Error: '230ed6' starts 2 keys" in result.output


def test_thread_refuses_an_unknown_id(conversation):
    result = invoke("thread", "<nobody@example.com>")
    assert result.exit_code == 1
    assert "Error: '<nobody@example.com>' is not in the archive" in result.output


def test_thread_refuses_a_prefix_no_key_has(conversation):
    taken = {store.id_hash(f"<t{n}@example.com>")[:6] for n in range(1, 5)}
    prefix = next(p for p in ("000000", "ffffff") if p not in taken)
    result = invoke("thread", prefix)
    assert result.exit_code == 1
    assert f"Error: no archived message has a key starting '{prefix}'" in (
        result.output
    )


def test_thread_refuses_a_prefix_too_short(conversation):
    key = store.id_hash("<t1@example.com>")
    result = invoke("thread", key[:5])
    assert result.exit_code == 1
    assert "at least 6 characters" in result.output


def test_thread_with_a_blank_subject_does_not_join_other_blank_subjects():
    hit("<b1@example.com>", "2026-03-01T17:00:00Z", "ada@example.com", "")
    hit("<b2@example.com>", "2026-03-02T17:00:00Z", "bo@example.com", "")
    result = invoke("thread", "<b1@example.com>")
    assert result.exit_code == 0, result.output
    assert "(1 message," in result.output
    assert "bo@example.com" not in result.output
