---
id: 1
slug: bulk-reader-slips
status: draft
branch:
created: 2026-10-10T00:34:31-07:00
concluded:
pr:
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
