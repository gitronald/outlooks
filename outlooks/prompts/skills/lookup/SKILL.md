---
name: lookup
description: Read-only lookup of the full correspondence with one or more people in the configured Outlook mailbox, both directions, reported as a dated timeline and archived.
metadata:
  version: "1.0.0"
---

# Outlook: look up a correspondence

Read-only. Answers "what passed between us and this person, and when": the
full correspondence with one or more people, every message in both
directions, ours and theirs — to audit a date on a record, check whether a
sender is new, or find out if a message was ever answered. Writes nothing
shared: no ledger, no filing, no records. It does archive what it read.

The other modes: `{cli} skill sweep` (what is new since the last run) and
`{cli} skill window` (page or read a whole date range). A sweep often ends in
a lookup, because deciding whether a sender is new is exactly the lookup
question.

## 0. Before anything else

1. Run `{cli} config` and read the mailbox, own addresses, system senders,
   zone, and paths it prints. Every path below (`archive_dir`,
   `scratch_dir`, `captured_dir`) is one of its values.
2. Run `{cli} doc connector` and read it: the fixed inputs, what the archive
   keeps, and the connector's parameter shapes and constraints. The steps
   below assume it.
3. **Repo profile.** Read the file `{cli} config` names under `profile` (or
   `{cli} doc profile-template` when none is set) before resolving names or
   cross-checking. Its *Registries* section says where a name or address
   resolves, and its *Cross-checks* section says what the repo's records
   assert and which source wins when the mail disagrees.

## 1. Resolve who

Turn each name the operator gives into a search term. Prefer the **address
local-part** (the part before `@`) — it is the sharpest key and immune to
name-spelling variants. A surname works when it is distinctive; a surname
that is also a common word will drag in unrelated threads. Expect that anyway
on a name that also appears in other mail: the pages are archived
regardless, and the split step's `--match` on the address local-part is what
keeps the strays out of the read list. Match the local-part alone when the
surname is what strayed.

Addresses come from the registries the profile names. If the operator names
someone with no address on file, search the surname and confirm from the
hits.

## 2. Pull each person's mail

One call per person:

```
mailboxOwnerEmail: <mailbox>
query: <surname or address local-part>
limit: 25
```

No `sender`, no dates, no `folderName` — those would either be rejected or
narrow the result to one direction. Page by `nextCursor` while `moreResults`
is set.

**Several people at once:** run one call per person, in parallel, and keep
the results separate. A single KQL `OR` query does work, but the ranking is
applied across the whole result set, so a correspondent with one message can
be crowded out by one with twenty — and you lose the per-person page budget.
Separate calls also make "no mail at all from this person" an unambiguous
answer.

In practice the calls go through subagents (see [Running a lookup through
subagents](#running-a-lookup-through-subagents)); the shapes are the same.

## 3. Fill gaps only if the picture is incomplete

- Missing our side, or the thread looks truncated →
  `folderName: "Sent Items"` + the same `query`.
- A specific day's outbound (e.g. several records sharing one response date)
  → `folderName: "Sent Items"` + `afterDateTime`/`beforeDateTime`, no
  `query`.
- Thread history still ambiguous → `read_resource` on the **latest**
  message. A reply usually quotes the whole chain, so one read can expose
  messages the search didn't surface. Treat a quoted copy as a lead, not
  proof it was sent from this mailbox — confirm it with a search.

## 4. Report a timeline

One row per message, oldest first, in the configured **zone**:

| Date | Direction | Subject / what it was | Notes |
|---|---|---|---|

Then say what it means for the question that was asked, and flag anything
the operator should act on: an inbound message with no reply after it, a
decision we sent that the record doesn't carry, a date on the record that
the mail contradicts.

Cross-check against the repo's own record when the question is about a date
or a decision. The record's dates come from the repo's own tooling; the
mailbox is the independent witness. When they disagree, say which is which
and let the operator decide — correcting a record is a write, so it belongs
to the operator, not this mode. The profile's *Cross-checks* section lists
the disagreements that recur and how the operator has already ruled on them,
including which source dates an arrival; name the source that wins before
proposing any correction, and report a correction as the one-line command
the profile gives rather than running it.

If a record shows a reply or acknowledgement date with no matching message
anywhere in the mailbox search, don't treat that as the date being wrong —
people sometimes reply from a personal account outside this mailbox, which
this skill has no access to. Say the mailbox has no record of it rather than
casting doubt on the date itself.

**Touches no shared state on its own.** No filing, no records, and in
particular no ledger rows: the ledger is the sweep's dedup list and
high-water mark, so a lookup writing to it would make the next sweep skip
messages it never classified.

**Archive what it read, though.** Run `{cli} import` once the reads are done
(see `{cli} doc connector`). That is additive and immutable — it adds no
ledger row and changes nothing the sweep reads — and it means a lookup run
for one question leaves the evidence behind for the next one. Mention the
archived files when offering to commit.

## Running a lookup through subagents

The connector calls are the expensive part of a lookup: every page and every
full read lands in the context of whoever makes the call, and none of it is
needed once the hook has captured it. Delegating that part keeps the main
session's context for the row decisions and the record cross-check, which
stay here. The main session makes **no connector call of its own**: a search
page it runs itself puts the whole page into its context, which is exactly
what the fan-out exists to avoid.

The clock is the connector, not the model: a read is roughly fifteen to
twenty seconds of wall time, and a subagent runs its calls one after
another, so a reader given seven messages can take ten minutes where a
searcher's single page takes half a minute. The fan-out is about **how many
agents run at once**, not about how many hits there are.

**Before the searchers, clear the name.** Run `{cli} lookup-reset {name}`
for each name. The split merges every page file it finds for a name, so a
second lookup of the same name would mix this run's pages with whichever of
the last run's it did not rewrite. The command moves that name's page and
timeline files to `earlier/` under the scratch directory and prints what it
moved. It deletes nothing.

The split:

1. **Search** — up to four `sonnet` subagents per person, launched together
   in one message:
   - the **query** searcher: the `query: <local-part or surname>` call of
     step 2 above, paged by `nextCursor`;
   - the **sender** searcher: `sender: <their address local-part>` with no
     `query` (filter mode, so it is complete rather than relevance-ranked),
     paged by `nextOffset`. It is the independent check that the query
     search dropped nothing inbound, and it answers "did they ever reply" on
     its own;
   - the **sent** searcher: `folderName: "Sent Items"` + the same `query`,
     paged by `nextCursor`, pages saved as `{name}-sent-page-{n}.json`. It is
     the outbound counterpart of the sender searcher: the query search
     matches our replies through their recipients, but it is
     relevance-ranked, and this is the check that it dropped none of them.
     Run it in the first round with the others rather than as a fallback
     under step 3 above — a lookup that skips it has to be re-run to answer
     "did we get everything". A short or common term stems loosely here and
     returns unrelated older messages; the pages are archived regardless,
     and the split's `--match` keeps them out of the read list;
   - the **title** searcher, when the person's request went
     through a system that notifies us from one of the system senders:
     `query: "<a distinctive three-to-four-word phrase from the title>"`,
     paged by `nextCursor`, pages saved as `{name}-title-page-{n}.json`. A
     system's notice to us can name the person nowhere — subject and
     recipients are generic, and only the body carries the title — so the
     surname query never returns it, and the split's same-minute rule can
     only pull it in once some searcher's page holds it. Without this
     searcher an arrival that came through the system alone shows just the
     acknowledgement. Pick a phrase unlikely to appear in other mail (not
     the field's generic two-word name); a quoted phrase is fine in KQL. The
     profile says when a title search applies.

   The searcher's instructions are fixed, so they live in a brief: write
   each searcher a one-line worklist file in the scratch directory,
   `{name} query|sender|title|sent <term> [<fallback>]`, and launch it with
   the one-sentence prompt: run `{cli} doc lookup/searcher-brief` and
   follow it; your worklist is `<path>`; cat it first; work from the repo
   root. It keeps its page files in place: the split step reads them.

   Several people: all searchers per person, all in parallel. Each writes
   every page to a scratch file for the split (the query searcher writes
   `{name}-page-{n}.json`, the sender searcher `{name}-sender-page-{n}.json`,
   the title searcher `{name}-title-page-{n}.json`, the sent searcher
   `{name}-sent-page-{n}.json`) and returns the hit list (id,
   `receivedDateTime`, sender, subject) — nothing else. A hit only the sender
   search returned is the reason it ran. An empty result is an answer (the
   person only ever wrote through a web form, say): the searcher
   reports it once, does not re-run the call to confirm, and has no page to
   save. The brief says so, or it would retry.
2. **Split known from unread** (main session): import the searchers'
   captures, then split.

   ```bash
   {cli} import
   {cli} split {name} --match <local-part> [--match <surname>] [--since YYYY-MM-DD] [--batches <dir>]
   ```

   `--since` is the read scope (see "Scope the reads" under **Read**,
   below); `--batches` writes the reader worklists, two hits per file, so
   the launch is one prompt per file. The match covers each hit's summary
   as well as its addresses and subject (the people a message is *about*);
   `--no-match-summary` turns that off when a common term floods it.

   It merges every `{name}-*page-*.json` on `internetMessageId`, keeps the
   hits that match the terms (plus a system sender's notice to us that
   arrives in the same minute as an acknowledgement to the person, which
   names them only in its body — the title searcher is what gets that hit
   onto a page), numbers them oldest-first, and checks each against the
   archive's `messages/`. A message already archived needs no reader: its
   `{name}-{nn}.timeline.json` in the scratch directory is written here from
   the stored JSON (summary = the stored preview). The rest are printed with
   their `uri` as the reader worklist. A repeat lookup on someone already
   archived therefore launches no readers at all. The archive is never a
   substitute for the search, though: it can only say what was seen before,
   not what arrived since.
3. **Read** — fan out on `sonnet` in **batches of two messages**, one
   subagent per batch, over the `READ` lines only, at most eight at once
   (one limit covers reads and searches from every agent and session on the
   account; the measured rates are in `{cli} skill window`): a lookup with
   more than sixteen unread hits runs its readers in rounds, and the search
   round before it keeps to eight searchers at once too. Do this for
   every lookup with more than two unread hits; do not fold the hits into
   one reader to "save agents", because that serializes the slow part. Each
   reader reads its messages and their text attachments (the hook captures
   each read) and writes `{name}-{nn}.timeline.json` in the scratch
   directory per message: `file, received_utc, date_local, direction,
   sender, to, subject, internet_message_id, weblink, attachments, summary,
   system` (`{cli} doc lookup/timeline` lists every key, and which of them
   `check` reads). Readers archive nothing themselves: the main session runs one
   `import` after the round, so parallel agents never write the archive at
   once. Give each reader the `nn` from the worklist, so its files slot in
   beside the ones the split wrote.

   **Brief by file, not by prompt.** The reader's instructions are fixed, so
   they live in a brief; each `--batches` file holds its two `{name}-{nn}
   <uri>` lines, and the launch prompt is one sentence: run
   `{cli} doc lookup/reader-brief` and follow it; your worklist is `<path>`;
   cat it first; work from the repo root. A seven-person lookup is fourteen
   readers; pasting the brief into each launch would cost the main session
   more context than the reads it was delegating.

   **Scope the reads to the record's era.** A correspondent with an older
   role (an earlier matter, a past position) has years of older mail under
   the same address. Their hits are archived by the searchers (a page is
   free), but only messages from the current matter's window are read in
   full — the older threads answer a different question. Say in the report
   that they exist and were left unread.
4. **Decide** (main session): run `{cli} import` to archive the readers'
   captures, then merge the timeline files, recompute each `date_local` from
   the archived `receivedDateTime` rather than trusting the reader's, check
   every archived file exists, write the report ("Report a timeline",
   above), and make the offer to keep it ("Offer to keep it", at the end)
   yourself. The check is one command, `{cli} check {name}`, which reads every
   `{name}-{nn}.timeline.json`, recomputes the local date from the raw
   file's `receivedDateTime`, confirms the archived `messages/{id-hash}.json`
   exists, and prints the `.txt` count beside it (which must be zero when
   the attachments are all images). It exits non-zero on any `BAD` row,
   and says under each one why: a `missing key` line means the file was
   written by another build or cut short, not that its date is wrong
   (`{cli} doc lookup/timeline`).

   Then **sweep the archived bodies for people the searches did not
   cover**: the `cc` recipients of every message in hand, and every address
   in the quoted chains. A colleague copied on the first
   message can write the next one under their own name, and nothing in the
   first round would find that message. Run a `sender` searcher per new address
   (an empty result is the answer, and costs one call), and a `query`
   searcher on the surname when it is distinctive. Reading the quoted
   headers of the latest reply in each thread is the same check from the
   other side: the chain it quotes should be exactly the messages in hand.

The searchers must finish before the split and the readers, so a lookup is
two rounds of parallel agents: searchers, then readers. Nothing else in it
should wait on anything. Do not `sleep` between rounds: a subagent's
completion arrives as a notification, and a `sleep` long enough to matter
trips the Bash timeout while the notifications queue up behind it. Launch,
then do main-session work (the record cross-check) until they land.

The brief for a reader says, in this order: which hits it owns; that it
archives nothing itself; that an image attachment is not read (a reader that
is told to extract "every attachment" will invent text for a logo; and a
hit's `hasAttachments` is `true` for an inline signature logo, so it does not
predict a text file); that it writes nothing under the archive (the main
session imports its captures), and never the ledger or any repo record; and
that its timeline file is a report, not a decision — `direction` and
`system` are the only judgments it makes. Check its work before using it:
the local dates, the file list, and that no `.txt` sits beside a message
whose attachments are all images.

## 5. Offer to keep it (optional)

A lookup that turned up history worth keeping ends with an offer, not a
write. If the profile names a place the repo keeps per-record mailbox history
(its *Cross-checks* or *Registries* section says where and in what columns),
propose the rows in that shape and wait for the operator: it is a repo write,
so ask first — and note it records *what was said and when*, never an
outcome, which still belongs to the repo's own records. Write any CSV with
LF line endings (Python's `csv.writer` defaults to CRLF; pass
`lineterminator="\n"`). With no such place in the profile, the timeline in
the report and the archived messages are the whole result.
