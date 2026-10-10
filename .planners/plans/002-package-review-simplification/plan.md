---
id: 2
slug: package-review-simplification
status: active
branch: feature/package-review-simplification
created: 2026-10-10T11:46:13-07:00
concluded:
pr: https://github.com/gitronald/outlooks/pull/9
---

# Review the package ontology and simplify the code

## Plan

### Goal

One pass over the whole package, in three parts, with Codex drafting and the
main session verifying and committing.

1. **Ontology review.** Check that each concept the package deals in (a hit, a
   message, a capture, a window, a role, a worklist, a lookup, the archive's
   tiers) has one name, one owning module, and one definition, and that module
   boundaries follow those concepts. Find synonyms used for one thing, one word
   used for two things, a concept defined in two modules, and helpers living in
   a module that does not own their concept.
2. **Code review.** Correctness bugs, unhandled edge cases, and error paths
   that print the wrong thing or exit with the wrong code.
3. **Simplification.** Duplicated logic, dead code (traced by consumers, not by
   its own tests), needless indirection, and over-long functions.

### Constraints

- The declared interface does not change: the public names and parameters
  pinned in `tests/test_contract.py`, the CLI's command names, output lines,
  and exit codes, the settings keys and defaults, the archive's file format,
  and the skill's step names. A naming problem at that level is reported, not
  fixed, and decided separately.
- Internal names (non-`__all__` functions, private helpers, internal modules'
  contents) may be renamed or moved when the ontology review says so.
- Every change keeps `uv run pytest`, `ruff`, and `pyrefly` (strict) green, and
  behaviour is pinned by the existing tests; a fix for a real bug gets a test.

### Order

1. Codex pass 1: the review report plus the fixes and simplifications inside
   the constraints, as one uncommitted diff in the worktree.
2. Main session: read the diff in full, re-run the checks, review for the known
   failure modes (a fresh copy of a helper that should have moved, `assert`s
   around nullable fields, private-name imports, deleted rationale comments).
3. Codex revision pass if needed, with a numbered list.
4. Commit in logical chunks; raise contract-level naming findings with the user.

## Log

- **2026-10-10T11:46:24-07:00** — Activated.
  - Branch: `feature/package-review-simplification`
  - Base: `dev` at `0d75e8d`
  - Worktree: `.worktrees/package-review-simplification`
  - PR: pending
- **2026-10-10T12:04:05-07:00** — Codex drafted, the main session verified
  and committed.
  - Pass 1 (one brief, about 20 minutes): an ontology inventory, 8 internal
    moves and renames, 9 bug fixes each with a test that failed first, one
    inlined wrapper, and 14 findings reported without change. The brief's
    frozen-interface list held: `test_contract.py` passed unmodified, and no
    output line, setting, or on-disk format changed.
  - The review caught what the brief's failure-mode list predicted, in new
    forms: a re-export alias (`decode_v3 as decode_v3`) kept so old test
    imports still resolved; the gateway decoding moved into the public
    `detail` module, which does not own it; the homonym `is_system` moved but
    not renamed; `lookup._ts` left beside `parse_ts`, and reading a naive
    `received_utc` in the machine zone; the message path rebuilt by hand in
    five places; and `capture.captured_dir` left as a pure alias.
  - Pass 2 (a numbered list of those seven) fixed every one, with no third
    pass needed. The main session then fixed two crashes pass 1 had only
    reported (`--per 0` removing batches before failing, a corrupt
    `windows.json` raising a traceback) and moved `cli.py`'s remaining
    `cv.fmt_ts` calls to `timestamps`.
  - New internal modules: `gateway` (URL-defense and banner removal),
    `attachments` (image kind and attachment id), `timestamps` (UTC parse and
    format). New internal helpers: `store.message_path`,
    `classify.direction` and `classify.is_automated` (moved from `lookup`).
  - 443 tests at the start, 470 at the end; ruff and pyrefly strict clean.
- **2026-10-10T12:04:47-07:00** — PR opened: https://github.com/gitronald/outlooks/pull/9
- **2026-10-10T12:04:48-07:00** — Implemented: 7 commits ahead of `dev`.
- **2026-10-10T12:14:33-07:00** — Reactivated: back to active after review.

## Handoff

The pass is committed on `feature/package-review-simplification`. What is
left is the findings that touch the declared interface, which this plan was
not allowed to change. Each needs a decision before any change.

1. `store.archive_root()` and `config.archive_dir()` are two public names for
   one location.
2. "mailbox" means three things: the configured connector owner, the address
   the archive files under, and the owner a capture names.
3. "worklist" means both the sweep's approval list and a reader's uri list;
   "gap" means both an uncovered interval (coverage) and missing
   correspondence (the lookup skill's step).
4. A timeline file's `system` key covers notice senders too, while
   `config.is_system_sender` excludes them.
5. Window bounds from the connector are inclusive on both ends; coverage
   intervals are half-open; both are called windows.
6. `outlooks doctor` does not parse message files.

CI passed on Python 3.12, 3.13, and 3.14 for PR #9.
