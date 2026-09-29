---
name: window
description: Archive every message in a date range of the configured Outlook mailbox, record the range as covered, and on request read every message in it in full.
metadata:
  version: "1.0.0"
---

# Outlook: archive a window

"Archive September", "backfill 2025", or a `GAP` line in `{cli} coverage`:
the archive does not hold every message in a range, and the fix is a window
pull. This mode pages every message in the range into the archive and
records the range as covered in `coverage.csv`, with no classification. It
writes only the archive and `coverage.csv`, never the ledger, so the next
sweep still classifies whatever this pulled. On request it also reads every
message in the range in full.

The other modes: `{cli} skill lookup` (the full correspondence with one
person) and `{cli} skill sweep` (what is new since the last run).

Every step has a fixed name, given where the step starts as step `name`.
Another skill or a profile that refers to a step cites it by that name
(the window's `pull-import` step), which stays the same when steps are added or renumbered.

## 0. Before anything else (step `setup`)

1. Run `{cli} config` and read the mailbox, zone, and paths it prints
   (`archive_dir`, `captured_dir`, `scratch_dir`).
2. Run `{cli} doc connector` and read it: what the archive keeps, coverage,
   `LOST capture`, spilled results, and the connector's parameter shapes.
3. **Repo profile.** Read the file `{cli} config` names under `profile` (or
   `{cli} doc profile-template` when none is set). This mode classifies and
   files nothing, so the profile matters here only for *Who we are* (the
   mailbox and zone) and for any mail the repo keeps out of shared places.

## Pull a window

With no `mailbox` configured (the signed-in account's own), read "Personal
mailbox" in `{cli} doc connector` first: its pages report no total, so the
sizing in step 2 and the planner below do not apply.

1. **Step `pull-gaps`.** `{cli} coverage` to see the gaps, unless the operator named the range.
2. **Step `pull-size`.** Split the range into windows of at most about 175 messages (seven pages,
   one pager's budget; see step 3): the search sorts newest first and pages
   by `nextOffset`, and a small window keeps one coverage row cheap to
   re-pull. For a range never pulled before, **size it first**: one `haiku`
   agent calls each month once with `limit: 1` and reports its
   `totalResultCount` (a one-hit page carries it). Import lists each probe
   as an `INCOMPLETE` window until its month is paged, which is expected.
   That is a dozen cheap calls for a year, it shows how far back the mailbox
   holds mail, and it tells you which months to halve. Read the sizes back
   with `{cli} totals <year> ...`, not from the sizing agent's report, which
   can put a month's count on its neighbour.

   A window that ends before the mailbox's first message (from 2000 to
   then, say) returns an empty result, which `import` records as a covered
   window of 0 (an empty offset-0 capture is the connector's own answer; a
   failed call leaves no capture at all). A page asked for past the end
   comes back as a bare `{"totalResultCount": <that offset>}` with no hits,
   which is not a count: the pager brief says to stop at the first empty
   page. Mail is bursty within a month, so a calendar half can still run
   past seven pages.

   Write bounds as UTC instants in `Z` form, the local midnight converted
   (`2026-09-01T07:00:00Z`, `2026-03-01T08:00:00Z` before the DST switch,
   for a Pacific zone), the form every `coverage.csv` row carries, so a
   re-pull's bounds match the recorded ones exactly. The last window of an
   open-ended range may omit `beforeDateTime` (its row then ends at the
   first page's capture time); [Collect a whole range's
   hits](#collect-a-whole-ranges-hits) below fixes a cutoff instead and
   leaves no window open.

   Pagers alone do not trip the throttle, so they launch together, but
   search calls count toward the same limit as reads: do not page while a
   read round is running (see the throttle notes under [What is settled
   about the readers](#what-is-settled-about-the-readers)). Paging beside
   another session's read round puts a 429 on most pagers of four pages or
   more; each sleeps once and finishes, so it is slower, not lossy.

   **Beside another session.** The captures directory is shared, so
   `import` in either session files both sessions' pages and reads
   (harmless: it is idempotent). Commit only your own range — the
   `hits/{mailbox}/YYYY-MM.jsonl` files for your months and, after checking
   that `git diff` of `coverage.csv` holds only your rows, the coverage
   file — and leave the archive's `messages/` to the session that read
   them. Split the work by year, so no month is paged or read by both.
3. **Step `pull-page`.** For each window, call `outlook_email_search` with `mailboxOwnerEmail`,
   `afterDateTime`, `beforeDateTime`, `limit: 25`, and `offset` — **nothing
   else**: a `query`, `sender`, or `folderName` makes it a lookup, which
   never counts as coverage. Page with `nextOffset` until
   `totalResultCount` is covered. Don't restate the hits; the hook has them.
   Fan out one `haiku` subagent per month for a long range: each only
   pages, and needs no brief beyond the pager brief and its two bounds. The
   launch prompt is one sentence: run `{cli} doc window/pager-brief` and
   follow it; AFTER is `<bound>`, BEFORE is `<bound>` (or "none"); work from
   the repo root.

   Keep a pager to about seven pages as a margin. For a range with a month
   over that, [Collect a whole range's
   hits](#collect-a-whole-ranges-hits) below splits it by day or second
   instead of halving it by hand; `import` flags a first pull that ran out
   mid-window as `INCOMPLETE` and `{cli} worklist` a short re-pull, so never
   trust a pager's own count.
4. **Step `pull-import`.** `{cli} import`. A new window prints `COVERED`; one already in
   `coverage.csv` (a re-pull of the same bounds and total) is only counted
   in the closing `complete windows already recorded` line, which is also
   success. `INCOMPLETE` is listed only for a window outside the recorded
   coverage: if it is the one you just paged, a page was lost, so page it
   again and re-import. An incomplete window inside recorded coverage (a
   one-page probe, a superseded pull) is counted, not listed, and needs
   nothing. `LOST capture` is a page whose output the hook saw but `import`
   cannot recover (a spilled result whose file and copy are both gone, or a
   response of no known shape): it is never read as an empty window, so
   call that page again.
5. **Step `pull-report`.** `{cli} coverage` and report what closed and what remains. Commit the
   archive (hits and `coverage.csv`).

Full reads are not part of this mode unless the operator asks for them
("download all of August"; see [Read a whole range in
full](#read-a-whole-range-in-full)): the report counts covered hits with no
read, and otherwise reading them is a lookup decision.

## Collect a whole range's hits

Pulling every hit in a long range (a year, the whole mailbox) is the same
window pull as above, driven in a loop by `{cli} windows` instead of by
hand. Its state lives in `windows/windows.json` under `scratch_dir`, beside
the captures.

1. **Step `collect-hook`.** **Check the hook first.** `{cli} hook` confirms the script and settings
   entry are in place; then make one search call and confirm a new file
   appeared in the captures directory before paging anything. A change to
   the hook takes effect mid-session, no restart needed. The allow rules
   for the two read-only tools, `outlook_email_search` and `read_resource`
   (see `{cli} doc connector`), keep the auto-mode classifier from blocking
   subagents' reads; never widen either to a write tool.

2. **Step `collect-init`.** **Start the plan.**

   ```bash
   {cli} windows init AFTER [BEFORE]
   ```

   Bounds are a bare `YYYY-MM-DD` (that day's local midnight) or a
   timestamp with `Z` or an offset, in whole seconds. `BEFORE` omitted
   resolves to now, a fixed cutoff written into the state: the planner
   never uses open-ended windows, because `coverage.csv` records an open
   pull's end as its first page's capture time, so a planned window could
   never match it, and the id check (step 7) excludes it. Where mail
   starts: one `limit: 1` probe from `2000-01-01` to `AFTER` proves nothing
   is earlier; an empty result is a covered window of 0 once `import` runs.

3. **Step `collect-size`.** **Size.** Probe the `range` line and every month `init` printed with
   the one-`haiku`-agent sizing call described above (`limit: 1` each),
   pointed at these bounds. Then

   ```bash
   {cli} import
   {cli} windows fill
   ```

   so 0- and 1-message windows land in `coverage.csv` (a 1-message window
   is complete from its probe) and the rest get their sizes. `fill` reads
   sizes from the captures only: a piece it still reports unknown had no
   capture (a failed or throttled call leaves none), so probe it again,
   never assume 0.

4. **Step `collect-split`.** **Split until everything fits.**

   ```bash
   {cli} windows split
   ```

   prints the new pieces to size; probe them, `import`, `fill`, and repeat
   until `fill` reports `unknown 0 over-LIMIT 0`. This is the connector's
   1,000-offset ceiling forcing the issue: a mailbox can take more than a
   thousand messages within minutes, and the connector honors second-level
   bounds, so `split` cuts at interior local midnights first, then at
   seconds. An `unsplittable` line on stderr is a window under two seconds
   still over 175: report it, don't page it.

5. **Step `collect-page`.** **Page in rounds.**

   ```bash
   {cli} windows batches
   ```

   writes `pager-NN.txt` files, at most seven pages each, skipping covered
   windows and windows of 0 or 1 messages; it refuses while any uncovered
   window is still unsized or over 175. Launch one `haiku` pager per file,
   in rounds of about twelve, each with the one-sentence prompt: run
   `{cli} doc window/multi-pager` and follow it; your batch file is
   `<path>`; cat it first; work from the repo root. A pager takes about a
   minute, now and then far longer. Search calls share the throttle with
   reads, so never page during a read round.

6. **Step `collect-import`.** **Import and diff, never count reports.** After each round,

   ```bash
   {cli} import
   {cli} windows remaining
   ```

   Progress is what `remaining` shows, never a pager's report, which can
   cover one window of a batch of several. Run `batches` again for the next
   round (it replaces the old pager files) until `remaining` prints nothing.

7. **Step `collect-prove`.** **Prove it.**

   ```bash
   {cli} totals --check AFTER BEFORE
   ```

   with the plan's own `range` bounds; it must print `ok`: unique item ids
   across the window captures equal the range's own total. Ids, not a sum,
   because window bounds are inclusive on both ends, so adjacent windows
   share boundary messages and totals sum past the range, and
   `totalResultCount` counts folder copies. The proof needs the captures
   (gitignored); `coverage.csv` and `{cli} coverage` are the durable
   record. Then commit the archive as in step 5 of [Pull a
   window](#pull-a-window).

The planner is for mail not yet covered. A **re-pull** of a covered range
(to refresh stale uris before a bulk read, step 1 below) pages its windows
directly with the pager brief, because `batches` skips covered windows.

## Read a whole range in full

"Download all of August" is a separate fan-out:

1. **Step `read-repull`.** **Re-pull the range first** (steps 3 and 4 of [Pull a
   window](#pull-a-window), one `haiku` pager per month), even when it is
   already covered. A uri goes stale when mail is moved, and a stale one
   comes back "not found"; a fresh pull costs a couple of minutes and
   leaves none to miss.
2. **Step `read-worklist`.** **Build the worklists** from the captures:

   ```bash
   {cli} worklist --after 2026-08-01 --before 2026-09-01 --out <scratch>/aug --name aug
   ```

   It takes each message's newest uri from the window captures, skips every
   message already read or archived, and writes `batch-{bb}.txt` files of
   ten (`--per`). It also skips every copy of a mass send listed in
   `mass-sends.csv` in `archive_dir` but the one read in full, and says how
   many: a bulk send (a mail-merge, an announcement) leaves one copy per
   recipient, differing only in the recipient. The file is hand-owned, one
   row per send: `sender`, the exact `subject`, `after` and `before` (UTC
   timestamps around the send, both inclusive), and `exemplar` (the
   `internetMessageId` of the copy to read). Before reading a range that
   holds a new send, add its row; `{cli} coverage` then counts its copies
   as unread by design rather than as mail with no full read. It also counts archived hits no window capture lists,
   which no read can fill (deleted since, or written into the archive by
   hand rather than imported). It refuses to write worklists when the
   newest pull of a window that was once pulled complete is short (a
   re-pull that lost a page, which `import` only reports as "already
   recorded"): re-page it and import first. A short re-pull that reports
   the same total as the old one merges with it and is not caught.
3. **Step `read-launch`.** **Launch at most eight `sonnet` readers at once, one per batch, in one
   message**, each with the one-sentence prompt: run
   `{cli} doc window/bulk-reader-brief` and follow it; your worklist is
   `<path>`; cat it first; work from the repo root. The bulk brief writes no
   timeline files and returns one status word per message; the lookup's
   reader brief is for lookups. A range of more than eight batches runs in
   rounds: launch the next round only when the last reader of the current
   one has returned, so the throttle's window has drained. A round of eight
   takes about two minutes, while twenty at once are throttled within two
   minutes and take five, so more readers is slower, not faster. Budget
   about fourteen rounds per thousand reads.

   Before a read round, check whether another session is using the
   connector: every capture carries its `session_id`, so captures in the
   last minute from a session other than this one mean the budget is
   already shared; run fewer readers or wait for it to finish.
4. **Step `read-import`.** **Import, then re-run `{cli} worklist`**: what it prints is the next
   round. Count from the captures, never from the readers' reports.
   Rebuild only between rounds, and only once every reader's task has
   *completed*: a reader's report can arrive before it stops, and one that
   is still running will pick up the rebuilt files and re-read someone
   else's batch. The command replaces every `batch-*.txt` in `--out`, and
   batch 01 of the new list is not batch 01 of the old one. It refuses,
   writing nothing, while the newest capture is under 90 seconds old, and
   names the tool that wrote it: any connector call counts, this session's
   included. When it refuses, wait until every reader has returned and 90
   seconds have passed with no call, then import and run it again. When the
   remainder is just over eight batches, rebuild it with `--per 11` or 12
   so it fits in one round rather than leaving a second round of
   stragglers (still well under the twenty reads that fill a reader).
   Commit the archive's `messages/` after each round's import, so a stopped
   run loses nothing.
5. **Step `read-audit`.** **Check the period's attachments** with
   `{cli} audit <year> --sweep <file>`: it lists attachments no capture
   ever tried (a reader skipped it, or stopped partway through a message)
   and writes them as a worklist for one sweep reader, launched on the bulk
   reader brief and told the lines are attachment uris to read directly.
   `import` refuses a read of an image attachment (`REFUSED attachment read
   (an image attachment)`); that line means a reader broke the brief, and
   nothing was filed for it. Commit the archive's `messages/`.

### What is settled about the readers

- **Ten reads per agent, on `sonnet`.** A reader's context fills with
  bodies after about twenty reads; it then stops and reports the list as
  done, and a `haiku` reader miscounts what it read. Sonnet readers of ten
  each finish and count correctly.
- **The connector throttles, across all agents together.** The limit is on
  the rate, not the count. Eight readers run at about 45 calls a minute
  (message and attachment reads together) with no 429, and twenty are
  throttled inside two minutes, so eight is near the ceiling, not a floor
  to raise. Search calls count too, and so does every other session on the
  same account: a session paging in the same minute as a read round
  throttles it. Page first, then read; don't overlap them. A 429
  carries a 62-second retry-after; readers sleep 65 seconds **plus a random
  0-30** before retrying, because readers that all sleep exactly 65 wake
  together and trip it again. A second 429 on the same uri waits 120-150
  seconds. The capture hook is PostToolUse, so a throttled call leaves no
  capture: the captures per minute are the successful reads, and the way to
  measure a round's rate.
- **Attachments are picked by their own entry.** A reader that takes the
  neighbouring entry's uri reads an inline logo in place of a document. The
  briefs say to check each entry's `contentType`/`isInline` and read that
  entry's `uri`, and `import` refuses what slips through.
- **An attached email or calendar item** (`message/rfc822`) sometimes
  reads as text and sometimes comes back as a JSON note ("nested item
  content is not exposed"). The note is not attachment text: `import`
  refuses it (`a connector note, not text`), like an image read.
- **A "not found"** means the message moved since its uri was listed:
  re-pull its window and rebuild the worklist.
- **A result too large for the context** (a long thread, about 55k
  characters or more) reaches the hook only as a note naming the file
  Claude Code saved it to; the hook copies that file beside the capture and
  `import` reads the copy (falling back to the original), so nothing needs
  re-reading. A spill whose copy and original are both gone imports as
  `LOST capture`: read it again.
