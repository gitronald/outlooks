---
id: 1
slug: bulk-reader-slips
status: active
branch: feature/bulk-reader-slips
created: 2026-10-10T00:34:31-07:00
concluded:
pr: https://github.com/gitronald/outlooks/pull/6
---

# Harden bulk readers against parallel calls and retyped uris

## Plan

### Goal

A full read of a long range runs a few hundred bulk readers, eight at a time,
each on a ten-line worklist. A recent run of that size finished correctly
(the rebuilt worklist came back empty), but the readers broke the brief in
the same few ways again and again. None of the slips lost data, because
`import` and `worklist` are forgiving. They cost throttle budget and
orchestrator attention, and two of them show that the audit's sweep list is
built from the wrong signal. This plan fixes the mechanism behind each slip
rather than repeating the existing rules louder: the bulk reader brief
already says "copied from the worklist exactly (never retyped)" and "one
call at a time, never in parallel", and readers broke both anyway.

### What went wrong

1. **Parallel reads.** Roughly a third of readers made at least one pair of
   `read_resource` calls in a single turn, usually lines 2 and 3, or 3 and
   4. Every reader that did so disclosed it unprompted. No pair was
   throttled at eight readers, but the throttle budget assumes one call per
   reader at a time.
2. **Retyped uris.** Several readers read one uri past their last worklist
   line (an eleventh call), or the next id after a line, with a note that
   they had retyped the uri. The uris are long base64 ids that differ near
   the end, so a retyped one lands on a real neighbouring message. One
   reader's slip returned "not found" instead.
3. **Double reads.** A few readers read the same uri twice. This is harmless
   (captures dedupe), but it is wasted budget.
4. **Return format.** Readers often appended process notes after the
   required one-line-per-NAME return. The orchestrator wants exactly the
   lines (the notes were useful, but in the wrong place).
5. **Audit sweep includes images.** `audit()` treats an attachment as
   text-bearing unless `isInline` is set or `contentType` starts with
   `image/`. Images sent as `application/octet-stream` with a name like
   `PNG image` or `img-<uuid>` pass that test. They then land in the
   `--sweep` worklist, and the sweep reader can only find out by reading
   them. All ten lines of one sweep were images or a design file.
6. **Audit re-lists binary attachments.** An attachment that was read and
   came back as the connector's "Binary attachment" note (a scheduled report
   PDF, for example) stays in the MISSING list on every audit. The
   docstring says these are listed "since those are the ones a re-read might
   fill", but a binary note never fills on re-read. A periodic report sent
   dozens of times then fills the MISSING list with noise.

### Approach

**Retyping (2, 3).** Stop asking readers to copy uris out of a `cat` of the
whole file.

- Add `{cli} worklist next <file> <NAME>` (or a `--line N` flag on an
  existing command), which prints exactly one uri. The brief then says: for
  each NAME, run it and pass its output to `read_resource` verbatim. The uri
  is copied from output printed immediately before the call, never from
  memory of a long listing.
- Weigh this against the "no flags nothing calls" rule: the brief is the
  caller.
- The brief also says that the return has exactly N lines and that there is
  no call after the last NAME.

**Parallelism (1).** Rewording alone has failed, so make it observable.

- Captures already carry `session_id`. Check whether subagent captures can
  be told apart (agent id, or a reader tag passed through the hook). If
  they can, `import` can report "N reads in the same second by one reader"
  per round, so the orchestrator sees the rate.
- If they can't, keep the rule and move it to the top of the brief, before
  the numbered steps, with the reason stated in a single sentence (the
  shared rate limit). Today it sits in the middle of the throttle step.

**Return format (4).** The brief ends with an explicit separator: notes go
after a line `---`, and the orchestrator's parser ignores everything after
it. The orchestrator stops seeing malformed returns, and readers still have
a place for the disclosures that proved useful.

**Audit images (5).** In `audit()` (and anywhere else that decides text vs
image, such as `worklist.py`'s counting), treat an attachment as an image
when its `name` matches the connector's image placeholders (`PNG image`,
`JPEG image`, `img-<uuid>`), or its extension is an image or design format
(`.png`, `.jpg`, `.gif`, `.ai`, `.psd`, `.svg`), whatever its
`contentType`. Keep these out of the sweep. Add tests with
octet-stream image fixtures.

**Audit binaries (6).** Record a binary-note outcome per attachment id. The
capture already exists, so derive it from `attempted_ids()` plus the
capture's note text. Report these as `binary` (counted, not listed) instead
of MISSING. MISSING then means "tried and empty for a reason a re-read
might fix", or "never tried".

### Order

1. Audit fixes (5, 6) with tests. These are pure code and change no brief.
2. `worklist next` (or equivalent) plus the brief rewrite for 2, 3, and 4.
3. Parallel-read visibility (1): investigate capture metadata first, then
   implement or fall back to the brief-only change.
4. Run a small read round (one or two batches) against a test mailbox, or a
   recorded capture set, to check the new brief end to end.

### Out of scope

- Changing the read round size or the throttle limits.
- Automating round orchestration (launching the next round when the last
  finishes). That is the host's job, not this package's.

## Log

- **2026-10-10T00:46:54-07:00** — Activated.
  - Branch: `feature/bulk-reader-slips`
  - Base: `dev` at `5ccc35e`
  - Worktree: `.worktrees/bulk-reader-slips`
  - PR: pending
- **2026-10-10T00:49:47-07:00** — PR opened: https://github.com/gitronald/outlooks/pull/6
- **2026-10-10T00:56:53-07:00** — Steps 1 to 3 implemented and pushed;
  step 4 not run.
  - Audit (5, 6): `capture.is_image` treats an attachment as an image by
    `image/*` type, a placeholder name (`PNG image`, `img-<uuid>`), or an
    image or design extension. `audit` and `import` both use it, so an
    octet-stream image is neither swept nor filed. `worklist.binary_ids`
    finds attachment reads that got the "Binary attachment" note; `audit`
    counts those under `binary` by type, not as MISSING or UNTRIED.
  - Retyping (2, 3) and return format (4): new command `outlooks uri
    <worklist> <NAME>` prints one line's uri and refuses a NAME the file
    does not hold, naming the range it does. A new top-level command was
    chosen over `worklist next`: `worklist` is a command with required
    options, so it cannot also be a group. The bulk reader brief now opens
    with both rules, lists NAMEs with `cut` instead of `cat`, fetches each
    uri with `{cli} uri` right before the call, and fences notes below a
    `---` line. The window skill's `read-launch` prompt drops "cat it first".
  - Parallelism (1): the Claude Code hooks reference documents `agent_id`
    and `agent_type` in a hook's input when it fires inside a subagent, plus
    `duration_ms` on PostToolUse. The capture hook writes its input verbatim,
    so captures already carry them; no hook change was needed. `Capture`
    parses both; `worklist.last_round` groups subagent reads into the newest
    round (readers whose spans overlap) and counts, per reader, reads that
    started before another of its own had ended. `import` prints `last read
    round:` and, when any, `PARALLEL reads by N of M readers`.
  - Not verified: that live captures carry `agent_id` and `duration_ms` (it
    rests on the docs, not on a recorded capture), and the new brief end to
    end (step 4).
  - Checks: `uv run pytest` 437 passed; ruff check, ruff format, and pyrefly
    clean.

- **2026-10-10T11:21:25-07:00** — Step 4: a live read round was run.
  - Setup: one day's window was pulled and imported into a scratch archive
    outside the repo, and worklists were built with `--per 4`. Two `sonnet`
    readers ran batches 01 and 02 at once, launched with the `read-launch`
    prompt as now worded. The capture hook was installed for the run only and
    removed afterwards.
  - Retyping: all 9 captured reads (8 messages, 1 attachment) match a
    worklist line exactly. No call came after a reader's last NAME. Each
    reader ran `outlooks uri` for every NAME and never `cat`ted the
    worklist.
  - Parallelism: every capture carries `agent_id`, `agent_type`, and
    `duration_ms`, which confirms the docs. `import` printed `last read
    round: 2 readers, 9 reads in 0m22s (24 a minute)`, with no PARALLEL
    line. The captures confirm it: each reader's calls ran one at a time.
  - Return format: both readers returned exactly four status lines, then a
    `---` and their notes below it.
  - Binary note: an octet-stream attachment (not an image) came back as the
    "Binary attachment" note. `import` refused it as a connector note, and
    `audit` counted it under `binary (no text to read)`, with nothing
    missing and nothing untried.
  - Not covered: an octet-stream image (the run's mail had none), and a
    throttled round.

## Handoff

- Steps 1 to 4 are done, committed, and pushed on `feature/bulk-reader-slips`
  (PR #6). Nothing is uncommitted.
- Open questions:
  1. Should the plan be marked implemented without step 4? Settled
     2026-10-10: the live round was run instead (see Log).
- Not verified live: an octet-stream image attachment, and a throttled
  round. Unit tests cover the first.
- Remaining: review of PR #6, then `/planners close`.
