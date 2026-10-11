---
id: 3
slug: sweep-cadence
status: active
branch: feature/sweep-cadence
created: 2026-10-11T04:57:33-07:00
concluded:
pr:
---

# Schedule recurring sweeps with a cadence setting and a due check

## Plan

### Goal

Make the sweep something a repo can run on a schedule, not only when an
operator remembers to. Today the sweep is attended: it stops at step
`approval` and waits, and nothing says when the next sweep is due. A mailbox
that matters around a dated event (a deadline, a meeting, a launch) wants a
cadence that tightens near the date and stops after it, and a run nobody is
watching has to leave the mailbox in a state the operator can pick up.

### Pieces

1. **A `sweep_cadence` setting** in `[tool.outlooks]`, overridable by
   `OUTLOOKS_SWEEP_CADENCE` (comma-separated, like the other list keys). A
   list of entries, each `"<every>"` or `"<YYYY-MM-DD> <every>"`:
   - `<every>` is `<n>d`, `<n>h`, or `off`.
   - An undated entry is the cadence from the start; a dated entry takes over
     from midnight of that date in the configured zone. Entries are in date
     order, at most one undated, and it comes first.
   - Example: `["7d", "2027-03-08 1d", "2027-03-15 off"]` is weekly, daily
     from the 8th, and no scheduled sweeps from the 15th.
   - Unset means no cadence: `due` says so, and a scheduled run does nothing.
   - A malformed entry is a `ConfigError` naming the entry.
2. **`outlooks due`**, read-only. It reports:
   - the cadence in force now, and the next change point;
   - the last sweep: the end of recorded coverage (a sweep's window ends at
     the instant it was pulled);
   - the ledger's high-water mark, and whether it lags coverage;
   - a last line another tool reads: `next sweep due  now`, `next sweep due
     <UTC instant>`, or `next sweep due  never`.

   A sweep is due **now** when the ledger lags coverage (archived mail not yet
   classified), when there is no coverage or no ledger yet, or when the
   interval in force has passed since the last sweep. It is due at the next
   change point when the cadence is `off` now and turns on later, and never
   when it stays `off` or is unset. Exit 0 when due now, 1 otherwise, so a
   shell test can gate on it. It takes `--ledger` as `coverage` does.
3. **A scheduled run in the sweep skill.** A new section, after step
   `report`, for a run started by a scheduler (`/loop`, a cron, a routine) with
   nobody at the approval step:
   - Start with `outlooks due`. When not due, report the due instant (for a
     self-paced loop, as its next wakeup) and stop.
   - When due, run steps `setup` to `match` as written.
   - Step `approval` does not wait. Every candidate is recorded with the
     profile's word for `deferred`, so the next sweep brings it back; skipped
     messages get their default outcome as usual. Nothing is filed: step
     `file` is never run unattended.
   - The worklist goes to `{scratch_dir}/sweep-<YYYY-MM-DD>.md` (a later run the
     same day appends), with the candidates and the skipped counts by kind.
   - Nothing is committed. The report names the uncommitted files and the
     worklist file.
   - An attended sweep later picks up the deferred rows through step `sweep`'s
     existing reopen rule, so the operator approves them there.
   - How to start one: `/loop /outlooks sweep scheduled` (self-paced, waking
     at the due instant) or a fixed interval; the profile template gains a
     *Cadence* heading saying which the repo uses.
4. **Docs and contract.** The README's settings table and Python API list,
   `config.describe()` and `KEYS`, the profile template, and `CHANGELOG.md`
   under *Added*. `next sweep due` is a line another tool reads, so the
   changelog's contract list names it. `doctor` checks that the setting
   parses.

### Out of scope

- Running anything unattended beyond the read-only steps and the ledger rows
  above: no filing, no sending, no commits.
- A scheduler of its own. The package says when a sweep is due; the harness
  (`/loop`, cron, a hosted routine) does the waking.
- Lookups and windows on a schedule.

### Checks

- Unit tests for the cadence parser (valid forms, each malformed form, order
  rules) and for the due computation (ledger lag, interval passed, interval not
  passed, `off` now with a later change point, `off` for good, unset, empty
  archive). The clock is passed in, never read in a test.
- A command test for `outlooks due`'s lines and exit codes.
- `tests/test_contract.py` pins the new public names, the setting key, and the
  `next sweep due` line; `tests/test_steps.py` still passes with the new
  section (it adds no numbered step).
- `uv run pytest`, `ruff check`, `ruff format --check`, `pyrefly check`.
- Release as a minor version with `stanza release minor`, after the changelog
  is filled.

## Log

- **2026-10-11T04:57:59-07:00** — Activated.
  - Branch: `feature/sweep-cadence`
  - Base: `dev` at `46c626f`
  - Worktree: `.worktrees/sweep-cadence`
  - PR: pending
