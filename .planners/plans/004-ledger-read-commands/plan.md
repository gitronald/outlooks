---
id: 4
slug: ledger-read-commands
status: done
branch: feature/ledger-read-commands
created: 2026-10-11T15:21:09-07:00
concluded: 2026-10-11T15:33:00-07:00
pr: https://github.com/gitronald/outlooks/pull/13
---

# Add read-only commands for the ledger and a message's thread

## Plan

### Goal

Let an operator see what the sweep has left them without writing a script.
The ledger records every classified message and its outcome, and the archive
holds every thread, but no command reads either back: `coverage` and `due`
print counts and instants only. Picking up deferred rows today means opening
the CSV, then searching the archive by hand for each row's thread to see
whether anyone has answered since.

Two read-only commands cover that loop: list the rows, then look at one
row's thread. Both read only the committed archive and the ledger, make no
connector call, and write nothing.

### Commands

1. **`outlooks ledger`**, read-only, with the same `--mailbox` and `--ledger`
   options as `coverage` and `due`.
   - With no filter it prints the counts by `outcome`, then the total, so the
     operator sees at a glance how many rows are deferred.
   - `--outcome WORD` lists the matching rows, oldest first. `WORD` is taken
     literally, because the outcome words are the profile's (step `setup`
     of the sweep) and the package does not know them. Repeatable.
   - `--since YYYY-MM-DD` keeps rows received on or after that date in the
     configured zone.
   - Each listed row shows: `received` in the configured zone (the format
     `coverage` uses), the message's archive key (`outlooks hash`, the first
     8 characters), and every column the ledger has beyond the three core
     ones, in the ledger's own order. When the ledger has no sender or
     subject column, the sender and subject come from the archive.
   - Long values are cut to the terminal width with an ellipsis; `--wide`
     prints them in full.
2. **`outlooks thread <id>`**, read-only. `<id>` is an `internetMessageId`,
   or a prefix of its archive key of at least 6 characters (refused when
   two keys share it).
   - It prints the archived messages of that message's thread, oldest
     first: received in the configured zone, direction (`in` or `out`, as
     `classify` decides it), sender, subject, and the first line of the
     body, with the message itself marked.
   - A thread is the full messages with the same `conversationId`. Hits
     that were never read in full have no `conversationId`, so they join
     by `classify.thread_subject` and are marked as matched by subject.
   - A last line names who wrote last and when, which answers "has anyone
     replied since?" directly.
   - Exit 1 with `Error:` when the id is not in the archive.

### Skill text

- The sweep's step `sweep` names `outlooks ledger --outcome <deferred word>`
  as the way to see the rows it brings back, and step `approval` names
  `outlooks thread` for checking a candidate's thread before the operator
  decides.
- The scheduled-run section's report names the command that lists what it
  deferred.

### Considered and left out

- **Writing the ledger from the CLI** (`ledger set <id> <outcome>`). The
  sweep owns every ledger write, after the operator's approval; a command
  that changes an outcome would bypass that gate.
- **An "unanswered threads" report** across the whole archive. Whether a
  thread is waiting on us is a judgment the sweep makes per message, with
  the profile's classes; a mechanical "last message was inbound" rule would
  flag every thank-you. `thread` shows the last writer for one thread,
  which is the part a command can say truthfully.
- **JSON or CSV output.** The ledger is already a CSV; a script reads it.
  Add a format when a consumer asks for one.
- **A per-person view.** The lookup mode answers that, with the connector,
  and an offline version would only see what earlier lookups archived.

### Out of scope

- Any change to the ledger's columns or vocabulary.
- Any connector call.

### Checks

- Command tests for both: counts with no filter, `--outcome` (one word,
  repeated, a word no row has), `--since`, archive fallback for sender and
  subject, truncation and `--wide`; `thread` by full id, by key prefix, an
  ambiguous prefix, an unknown id, a subject-matched hit, and the last-writer
  line. Fixtures use invented senders and the suite's synthetic settings.
- `tests/test_contract.py` pins the two command names; the README's command
  table lists both.
- `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run pyrefly check`, `uv run pytest`.
- Changelog entries under *Added*.

## Log

- **2026-10-11T15:25:33-07:00** — Activated.
  - Branch: `feature/ledger-read-commands`
  - Base: `dev` at `02db059`
  - Worktree: `.worktrees/ledger-read-commands`
  - PR: pending
- **2026-10-11T15:29:27-07:00** — PR opened: https://github.com/gitronald/outlooks/pull/13
- **2026-10-11T15:29:53-07:00** — Implemented both commands (`outlooks/ledger.py`,
  `outlooks/thread.py`, wired in `cli.py`), the sweep skill text, the README
  rows, the contract pins, and the changelog. Checks: ruff, ruff format,
  pyrefly, and the full suite (532 passed) ran green in the worktree.
  Decisions the spec left open:
  - `--since` without `--outcome` narrows the counts; only `--outcome` lists rows.
  - A ledger's `mailbox` column is not listed: the rows are already one
    mailbox's (`coverage.ledger_rows`, made package-public for this).
  - With more than one `--outcome` word the listing adds an `outcome` column,
    since the rows would otherwise not say which word each has.
  - Truncation never cuts `received`, the key, or `outcome`; the other columns
    are cut widest first, to no less than 8 characters.
  - When the named message has no `conversationId` (a hit never read in full),
    every message joins by subject. A blank subject joins nothing.
  - A word no row has prints the ledger's outcome words, exit 0.
- **2026-10-11T15:30:04-07:00** — Implemented: 4 commits ahead of `dev`.
- **2026-10-11T15:39:11-07:00** — Review gate passed (`/code-review` at medium,
  posted to the PR); fixes in `050a6b1`. Full gate green: ruff, ruff format,
  pyrefly, pytest (535 passed).
  - Review follow-up:
    - Piped output was cut to 80 columns (`shutil.get_terminal_size` falls back
      when stdout is not a terminal), so an agent running the sweep saw
      ellipsized values. Fixed: values are cut only on a terminal or when
      `COLUMNS` is set; tests for `ledger` and `thread` with `COLUMNS` unset.
    - `ledger --outcome` read the whole archive even when the ledger has
      `sender` and `subject`. Fixed: the archive is read only for a missing
      column (`ledger.fallback`); a test fails if it is read.
    - `--since` parsing duplicated `worklist.bound`. Fixed by calling it; the
      bad-date test covers it. The same inline copy in `coverage` predates
      this plan and was left.
    - Conscious no-op: `thread` would fail on a full message archived without
      `receivedDateTime`. Every archive reader assumes that field; it is the
      archive's contract, not this plan's to change.

## Retrospective

- The spec held up; the gaps were in what "the terminal width" means. A
  command written for an operator is also read by an agent through a pipe,
  and the review caught that truncation silently hid the values the sweep
  skill sends the agent to read.
- Joining hits by subject needed two rules the plan did not state: a target
  with no `conversationId` joins everything by subject, and a blank subject
  joins nothing, or every subject-less message becomes one thread.
- Keeping the received time and key uncut, and cutting the widest of the
  rest, was the difference between a readable narrow listing and one where
  every column was cut to the same stub.
- Next time a plan adds terminal output, say in the spec how it behaves when
  piped.
