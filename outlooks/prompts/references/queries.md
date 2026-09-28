# Outlook mailbox queries

The query approach for a shared mailbox, run through the claude.ai Microsoft
365 connector (`outlook_email_search`, then `read_resource` for full bodies).
This file covers strategy only: the query shapes, how the connector behaves,
and how results get filtered. It holds no message content. The query marked
*untested* has not been run.

Placeholders: `<mailbox>` is the shared mailbox (`mailbox` in `{cli} config`)
and `<system-sender>` is a notification sender (`system_senders`). `<since>`
is the high-water date from the last run, `<word>` and `"<phrase>"` stand for
the wording the repo's requests use, and `"<template phrase>"` for fixed text
in a system sender's notification. The repo's profile has the classes and the
wording.

Every query passes `mailboxOwnerEmail: <mailbox>` and `limit: 25` (the
maximum). For a personal mailbox, omit `mailboxOwnerEmail` (see
`{cli} doc connector`).

## Connector behavior

- **KQL goes in `query`.** `subject:`, `from:`, quoted phrases, `OR`,
  parentheses, and `received>=YYYY-MM-DD` all work inside the `query`
  string. That is the way around the tool's rule that a shared-mailbox
  search takes a free-text `query` or the `sender`/`afterDateTime`
  parameters, not both.
- **Two paging modes.** A `query` search pages by `nextCursor` (pass it back
  as `cursor`, stop when `moreResults` is absent) and returns no total. A
  filter-only search (`sender` + `afterDateTime`) pages by `nextOffset`,
  returns newest-first, and reports `totalResultCount`.
- **Search hits are thin.** A hit carries metadata (subject, sender address,
  recipients, dates, `hasAttachments`, `internetMessageId`, `uri`) and a
  short, often truncated summary. Parse fields from `read_resource(uri)`,
  which adds the full body, the sender display name, `conversationId`, and
  the attachment list.
- Links in message bodies may be rewritten by a URL-defense wrapper; the
  anchor text keeps the original URL, so match on that.
- **Attachments come back as text.** Each entry in a full read's
  `attachments` list has its own `uri`; reading it returns the attachment's
  extracted text (.docx and .pdf both confirmed), never the file bytes. The
  content is complete, but formatting such as list numbering is lost.
- **Text search misses matches.** A `query` search is relevance-ranked and
  can leave out messages that match, even inbound requests whose subjects
  clearly match A1. A filter-only search over the same dates returns them.
  Treat text search as a fast check, not a complete sweep (see [What the
  text queries miss](#what-the-text-queries-miss)).
- **`query` matches recipients, not just subject/body/attachments.** The
  tool's own description lists only the latter three, but a bare surname or
  address local-part returns *our* mail to that person as well as theirs.
  It holds for a fresh compose too: no quoted history, a generic
  salutation, the search term appearing nowhere but the `To` header. This
  is what makes section C work without the `recipient` parameter, which a
  shared-mailbox search cannot use.
- **`folderName` combines with `mailboxOwnerEmail`.** The rejected
  combinations are `recipient` with either one, `query` with `order`, and
  `query` with `sender`/dates when either is set — but `folderName` +
  `mailboxOwnerEmail` is fine, with *either* a `query` or a date window.
  `folderName: "Sent Items"` is therefore the way to isolate outbound mail in
  the shared mailbox.
- **Use one date bound per KQL query.** A single `received>=` bound works,
  but a pair (`received>=… received<=…`) is ignored and comes back
  unfiltered. For a bounded window, use filter mode (`afterDateTime` +
  `beforeDateTime`).
- **Offsets stop at 1,000.** A filter-mode window holding more than that
  cannot be paged to the end; split it (window mode's planner cuts at local
  midnights, then at seconds, which the connector honors).

## Primary: date-window sweep

```
afterDateTime: <since>
beforeDateTime: <until>   (optional)
```

No `query` and no `sender`, so this is filter mode: a deterministic date
filter that pages by offset and reports a total. It covers every folder
(inbox and sent), and a mailbox taking a few dozen messages a day fills only
a few pages a week. Classify each message after the search:

1. From `<system-sender>`: classify with the section B table.
2. From `<mailbox>`: our own mail. Keep it only to link threads (as in A2).
3. Reply, forward, or auto-reply by subject prefix: attach it to its thread
   by normalized subject or `conversationId`.
4. Everything left is an inbound first message. Flag it as a candidate when
   any of these hold:
   - the subject or summary has the wording the profile lists for a request
   - it has an attachment
   - the sender isn't yet in the repo's registries (the new-sender rule)

Queries A and B below still work as quick lookups and for backfilling older
periods. The sweep is what a routine run should use.

## A. Requests (unstructured)

Requests written by people have no fixed subject line. They use several
phrasings, with or without a title, and a few arrive under free-form
subjects. Some attach a document; most are body-only. So detection takes
several queries plus client-side filtering.

### A1. Subject sweep (fast check)

```
query: (subject:<word> OR subject:<word> OR subject:"<phrase>") received>=<since>
```

Drop the `received>=` clause for a full historical sweep, and page by cursor
to the end.

### A2. Our replies (recall backstop)

When the team answers every request, its own sent mail finds the requests
whose subjects miss A1.

```
query: from:<mailbox> ("<phrase>" OR "<phrase>") received>=<since>
```

Each hit's subject, minus the reply prefix, names the original thread. Find
the inbound original by subject or by `conversationId`. Expect some noise
from later threads that mention an earlier request.

### A3. Body-phrase fallback (*untested*)

For requests that neither A1 nor A2 catches, such as ones that were never
answered:

```
query: ("<phrase>" OR "<phrase>") -from:<mailbox> received>=<since>
```

### Client-side filtering

1. Drop messages sent by `<mailbox>` (use them only for A2 thread discovery)
   or by `<system-sender>`.
2. Drop replies, forwards, and auto-replies, identified by subject prefix.
3. Dedupe on `internetMessageId`.
4. Group by normalized subject. The earliest inbound message in each group is
   the request; later inbound messages are follow-ups to file alongside it.

## B. System notifications (structured)

A system sender sends templated mail from `<system-sender>`. Each
notification type has a fixed subject and body template, so a message can be
classified by its subject. The package reads none of the body: what each
notice carries, and how to pull it out, belongs in the profile.

### B1. New items (primary)

```
query: from:<system-sender> "<template phrase>" received>=<since>
```

Returns only the notices to us of a new item. Each body has the item's title
and a link to it in the system.

### B2. Acknowledgements (the other side)

```
query: from:<system-sender> subject:"<template subject>" received>=<since>
```

Sent to the person within a minute of B1. It has their name and address,
which B1 can lack. Join to B1 on title and send time.

### B3. Full feed

```
sender: <system-sender>
afterDateTime: <since>
```

Filter-only mode, so it pages by offset and reports a total. List the
notification types the system sends and the fields a parser can pull from
each (title, id, the person's name and address) in the profile, one row per
type, as in this invented example for a request-tracking system:

| Notice (subject) | Parsed from it | Confidential |
|---|---|---|
| `New request received` | title; request id from the link | no |
| `Request closed` | request id; the closing note | no |
| `Complaint filed by <name>` | request id; the complainant's name and address | yes: the complainant |

A notice that identifies a confidential party stays out of anything filed
where the other parties can see it.

## C. One correspondent's whole thread (audits, not sweeps)

For "what did we actually say to this person, and when" — the question
behind a date that looks wrong on a record. The sweep answers *what
arrived*; this answers *what passed between us*.

### C1. Everything, both directions (start here)

```
query: <surname or address local-part>
```

No `sender`, no dates, no `folderName`. One call returns the full
correspondence, inbound and outbound, because `query` matches recipients
too, including the messages a `sender` + date search buries pages deep.

Prefer a distinctive term. A surname that is also a common word will pull in
unrelated threads; an address local-part is the sharpest key.

### C2. Only our side

```
folderName: "Sent Items"
query: <surname or address local-part>
```

The outbound half on its own — the acknowledgements, requests for detail,
and replies. Use it when the inbound side is already known from the
record and the question is what we sent back.

### C3. Everything that went out in a window

```
folderName: "Sent Items"
afterDateTime: <from>
beforeDateTime: <to>
```

No `query`, so this is a deterministic filter rather than a ranked search.
Use it to check what a given day's outbound looked like — a batch of
acknowledgements sent together, say, which is a common shape and explains
several records sharing one response date.

### What not to do

Searching `sender: <mailbox>` with a date window and **no** `folderName`
scans every folder and interleaves our sent mail with everything received in
the same period. A single day can fill several pages, so a specific outbound
message hides behind messages that have nothing to do with it. Use C1.

### Why not `conversationId`

It is the obvious key and it does not work, for three separate reasons:

1. **It is not searchable.** There is no `conversationId` parameter, KQL
   `conversationid:<id>` returns nothing, and the raw id as a quoted phrase
   returns nothing. `read_resource` rejects `mail:///conversations/{id}` —
   only `mail:///messages/` and `mail:///folders/` exist.
2. **Search hits don't carry it.** Only a full `read_resource` returns
   `conversationId`, so grouping by it costs one read per message — and you
   can only read messages you already found, which takes the search that
   would have answered the question anyway.
3. **It tracks the subject-thread, not the correspondence.** This is the one
   that matters. Someone who starts a new message rather than replying gets
   a new conversation, so one person's exchange spans several ids: an
   opening message, the main thread, and a later question under a fresh
   subject are three. Keying on one id returns part of the exchange and
   silently misses the first contact and the most recent question.

So `conversationId` is for *grouping messages already in hand* — which is
what the sweep's classify step uses it for, correctly — never for finding
them. C1 is the retrieval key.

### Quoted history as a fallback

When a thread's earlier messages are missing or ambiguous, one
`read_resource` on its *latest* reply often settles everything: a
Gmail-style reply quotes the whole chain, each part with its own
`From`/`Sent` header. Two cautions — the quoted headers render in the
**sender's** timezone, so date them from the message's own
`receivedDateTime` rather than the quoted line, and a quoted copy is not
proof the message was sent from this mailbox. Use C1 to confirm.

## Incremental runs

Store the received timestamp of the newest message processed and pass it
back as `<since>`. In the date-window sweep, `afterDateTime` takes a full
timestamp, so runs don't overlap (the sweep still starts five minutes early
on purpose; see `{cli} skill sweep`). KQL `received>=` takes only a date, so
text-search runs overlap by up to a day. Either way, dedupe on
`internetMessageId` (the ledger).

## What the text queries miss

Queries A and B together find most of what a period's records hold, not all
of it. What slips past them:

- **A request with a matching subject that text search doesn't return.**
  The date-window sweep finds it.
- **A document emailed under an unexpected subject**, with none of the
  request wording in the subject or in our reply. The sweep flags it through
  the attachment rule.
- **A matter handled entirely outside the system**, with a hand-written
  reply and a stale address in the registry. Only the new-sender rule or a
  registry fix catches this one.

Two kinds match only in part: a document sent as an attachment on its
request thread (the thread is found, but not the document as its own item),
and a system acknowledgement addressed to someone other than the person on
record (matched on title only). In the other direction, the queries also
surface recent requests that aren't in the records yet.
