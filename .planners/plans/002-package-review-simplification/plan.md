---
id: 2
slug: package-review-simplification
status: draft
branch:
created: 2026-10-10T11:46:13-07:00
concluded:
pr:
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
