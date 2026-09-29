---
name: sweep
description: Sweep the configured Outlook mailbox for everything new since the last run, archive it, classify every message, and present a worklist for the operator's approval before anything is filed.
metadata:
  version: "1.0.0"
---

# Outlook: sweep the mailbox

Finds what is new since the last run, classifies every message, and — once
the operator approves a worklist — hands each approved candidate to the
repo's own filing step and records every message in the dedup ledger. Writes
to shared state after the approval gate, and to the archive throughout.

The other modes: `{cli} skill lookup` (the full correspondence with one
person) and `{cli} skill window` (page or read a whole date range). A sweep
often ends in a lookup, because deciding whether a sender is new is exactly
the lookup question.

Every step has a fixed name, given where the step starts as step `name`.
Another skill or a profile that refers to a step cites it by that name
(the sweep's `classify` step), which stays the same when steps are added or renumbered.

## 0. Before anything else (step `setup`)

1. Run `{cli} config` and read the mailbox, own addresses, system senders,
   zone, and paths it prints.
2. Run `{cli} doc connector` and read it: the fixed inputs (the ledger is
   there), what the archive keeps, and the connector's parameter shapes and
   constraints.
3. **Repo profile.** Read the file `{cli} config` names under `profile` (or
   `{cli} doc profile-template` when none is set) before classifying,
   matching, or filing. The sweep's classes, the registries a sender is
   matched against, the ledger's extra columns, and what filing a candidate
   means all come from it. With no profile, classify with the generic shape
   in step 2 and ask the operator what filing means before step 5.
4. **Ledger vocabulary.** The classification column's name and the three
   outcome words are the profile's when its *Ledger columns* gives them,
   and `kind`, `filed`, `skipped`, and `deferred` otherwise. Wherever this
   skill uses those words, write the profile's instead, so one ledger never
   mixes two vocabularies. Whichever word the profile maps to `deferred`
   keeps its meaning: its rows come back into the next worklist.

## 1. Sweep (step `sweep`)

Run `{cli} coverage` and read its last line, `next sweep from`: the
instant to pass as `afterDateTime`, already in UTC. Pass it as printed.
Don't work it out from the lines above it, which are in the configured
zone. When the profile's *Ledger columns* puts the ledger somewhere other
than `ledger.csv` in `archive_dir`, run `{cli} coverage --ledger <path>`,
or the line is computed without the ledger.

Run the date-window sweep in **filter mode**: `afterDateTime` set to that
instant, with no `query` and no `sender`, and `limit: 25`. Page with
`nextOffset` until `totalResultCount` is covered. No `next sweep from` line
means an empty ledger and no recorded coverage: ask the operator where to
start.

The instant is the ledger's latest `received` **minus five minutes**, or
the end of recorded coverage when that is earlier. The margin is there
because a window with zero overlap drops anything that arrived in the same
second as the last recorded message but wasn't returned in that sweep's
pages, and nothing would ever show it was missed. Re-listing a handful of
already-known messages costs nothing, because the next step drops every
`internetMessageId` already in the ledger.

Use filter mode rather than a text `query`. Text search is relevance-ranked
and can silently skip matching messages (see `{cli} doc queries`), while
the date filter returns every message in the window. It covers inbox and
sent together.

**Archive the window before classifying.** Once the last page is in, run
`{cli} import`. It files every page and records the window in
`coverage.csv`; check that it printed `COVERED` for the window, not
`INCOMPLETE` (a page is missing: page it and import again). Every message
the later steps read in full is captured the same way: step 5 runs `import`
again before it files anything from those reads, and step 7 once more at
the end, so the sweep never discards what it read (see
`{cli} doc connector`).

Drop every hit whose `internetMessageId` is already in the ledger. The
window start is inclusive, so the newest ledger message usually comes back
once. Then add the ledger's `deferred` rows back as open candidates. They
sit before the window, so the sweep itself won't return them.

## 2. Classify (step `classify`)

Sort each remaining hit into one `kind` (step 0 names the column). The search hit's metadata is
usually enough; do a full `read_resource` only when the summary can't settle
it.

The classes, their rules, and each class's default outcome (`candidate` or
`skipped`) are the profile's *Sweep classes* section. Whatever the repo's
classes are, the shape is the same, and these rules hold under any profile:

- mail from a **system sender** and mail from one of the **own addresses**
  (our own sent mail) get their own classes, skipped by default;
- every other **inbound first message** is sorted by the profile's rules
  into the classes that become candidates and the ones that don't.

A **reply**, forward, or auto-reply, by subject prefix (`Re:`, `RE:`,
`AW:`, `Fwd:`, `Automatic reply:`, ...), is skipped by default. That default
is the profile's to override: its *Sweep classes* may say replies are
classified by content like any other inbound message, as for a mailbox
where most of the mail that needs action answers a thread it started.

A skipped reply can still matter. It may be a follow-up on a candidate's
thread, or carry a revised file. List any reply from a candidate's thread
(same `conversationId` or normalized subject) under that candidate in the
worklist.

When the profile has a **new-sender rule** (flag any inbound first message
from a sender its registries don't know), a flagged message outside the
candidate classes is shown to the operator, not filed on its own say-so.
**Before treating a sender as new, look them up** — a new-sender rule fires
on a stale registry address as readily as on a genuine first contact, and
one `query: <surname or address local-part>` call settles it in both
directions (`{cli} skill lookup`, step 2). It also answers the other
question a candidate raises: whether we have already replied.

## 3. Match candidates (step `match`)

For each candidate, do a full `read_resource` on its `uri` and on every
text-bearing attachment's `uri` — the hook captures each read, and step 5's
`import` archives them, so nothing read here is read twice — and match it
to what the repo already holds, using the registries and the
matching rules in the profile. A hit in a registry usually means an existing
person or matter, so the message is a follow-up, not a new entry. Note
anything the profile asks to be checked or proposed for each candidate
(where it would be filed, how it would be named, which of the repo's
required questions it leaves unanswered). Don't fill a gap yourself.

## 4. Worklist — stop here for approval (step `worklist`)

Present one table before anything is written: every candidate, plus a
one-line count of skipped messages by kind (so the operator can spot a
misfile). For each candidate, show:

- sender, received date (in the configured zone), subject, attachments
  (names and types)
- proposed kind, and whatever the profile's filing step needs decided
  (where it goes, under what name, what record it creates or changes)
- any questions the sender asked that need a reply, and any gaps the
  profile's checks found

Wait for the operator's go-ahead, and follow any redirects ("skip this
one", "file it as the other kind", a different destination). Everything
after this writes to shared state.

## 5. File each approved candidate (step `file`)

Filing is **the profile's filing step** (*Filing an approved candidate*):
the repo's own skill or procedure, which this skill does not replace. Follow
it for each approved candidate, one at a time, in order. Whatever it says,
these hold:

- A failure stops that candidate and gets reported; don't retry it with
  changed arguments without telling the operator.
- The message and **every** text-bearing attachment are read (step 3 has
  usually done it; read only what it did not) and archived with
  `{cli} import` before anything is rendered or filed from them. The
  message lands in `messages/{id-hash}.json` under `archive_dir` and
  attachment `n`'s text beside it as `{id-hash}.{n}.txt`;
  `{cli} hash <internetMessageId>` prints the hash. An `UNMATCHED
  attachment read` line means the message itself was not read.
- Anything transcribed is rendered from the archived copy (`{cli} render`,
  with the naming the profile's *Transcription* section gives), and read
  back to check it is complete before it goes anywhere.
- The connector returns attachments as text only, never file bytes (and
  forwarding messages with attachments is refused), so originals can't be
  filed from here — the extracted text in the archive is what we keep of
  them. Tell the operator which file to drag in by hand, with its target
  name, and give the message's `webLink`.
- The candidate's ledger row is written as soon as it is filed, before the
  next candidate starts (step 6 says why).

With no filing step in the profile, stop at the worklist: record the
approved candidates in the ledger as the operator directs, and list them in
the report for the operator to act on.

## 6. Ledger (step `ledger`)

Append each candidate's row to the ledger **as soon as that candidate is
filed** — inside step 5, not after the whole batch. A filing step that
refuses to repeat itself (a render that won't overwrite, an upload that
won't take a used name, a record already written) leaves nothing to say
candidates 1 and 2 were done when a sweep fails on candidate 3: the next
sweep re-lists them and every retry hits a hard refusal. Write the row for
each filed candidate before starting the next one, then add the skipped
messages' rows at the end of the sweep.

A row is written for **every** message classified in this sweep, skipped
ones included, so a rerun never shows them again. The package relies on
three columns; the rest are the profile's *Ledger columns*:

| Column | Value |
|---|---|
| `internet_message_id` | the angle-bracketed id, as returned |
| `received` | `receivedDateTime` in UTC, `YYYY-MM-DDTHH:MM:SSZ` (no milliseconds) |
| `outcome` | `filed`, `skipped`, or `deferred` (or the profile's words, step 0) |

A candidate the operator defers ("not yet", "ask me next time") gets
`outcome` `deferred`. The high-water mark moves past it either way, so step
1 brings `deferred` rows back into the worklist. When it's filed or dropped
later, update that row's `outcome` rather than adding a second one.

Keep the ledger sorted by `received`, write it with LF line endings, and
quote fields that contain commas.

## 7. Report (step `report`)

Summarize what was filed (with the links the filing step returned), what
was skipped (counts by kind), what the operator still has to do by hand
(original attachments, questions to answer, gaps to raise), and any registry
hygiene the matching turned up, such as a stale address. Flag hygiene
issues; don't fix them unasked. Run `{cli} import` once more, so anything
read during filing is archived, and say how much mail the sweep archived
(pages and full messages). Offer to commit the ledger, the archive, and
whatever the filing step changed.
