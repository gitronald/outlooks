---
id: 2
slug: package-review-simplification
status: implemented
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

- **2026-10-10T12:31:48-07:00** — The owner decided the six interface
  findings (questions 1-6 in the earlier Handoff), and they are done.
  - 1: `store.archive_root()` removed; `config.archive_dir()` is the one
    name. Internal `archive=` parameters renamed `root=` to match `store`.
  - 2: `config.mailbox()` renamed `config.archive_mailbox()`; what a capture
    or uri names is the "owner" (`Capture.owner`, `store.hit_owner`).
  - 3: sweep step `worklist` renamed `approval`, lookup step `gaps` renamed
    `widen`; a searcher's one-line file is a "search task file".
  - 4: the timeline key `system` renamed `automated` (the owner picked the
    recommended option over splitting the key or documenting it).
  - 5: "window" is a pull's bounds, "span" a merged covered interval or a
    gap. Codex reported that a hit at a span's end instant was counted
    "lookup only" in the next gap; the main session fixed it by treating
    spans as closed, like the pulls they come from, with a test that fails
    on the old code.
  - 6: `doctor`'s archive check parses message files and checks each is
    stored under its id's hash.
  - Codex pass 3 did 1, 2, 3, and 5 and was partway through 6 when it hit its
    usage limit, leaving no report. The main session reviewed the partial
    diff, finished 6, did 4, fixed wording and wrapping in the prompts,
    restored the `Window.end` rationale Codex had rewritten, and rewrote the
    changelog entries. 481 tests; ruff and pyrefly strict clean.
- **2026-10-10T12:32:02-07:00** — Implemented: 13 commits ahead of `dev`.

## Handoff

All work for this plan is committed on
`feature/package-review-simplification` (PR #9). The six interface findings
from the first pass are settled (see the Log entry of 12:31).

Open question 7 (raised 2026-10-10): the `classify` role `system` covers
notice senders too (`Facts("system", extra={"notice": True})`), like the
timeline key now named `automated`. The role names are pinned
(`tests/test_contract.py` `ROLES`), so this is left as is unless the owner
wants the role renamed as well.
