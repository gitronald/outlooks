# The connector and the archive

Shared by every mode of the `outlooks` skill: what is fixed, what is kept, and
how the claude.ai Microsoft 365 connector behaves. The connector runs only in
an interactive session, never in CI. The query shapes and the connector's
quirks are in `{cli} doc queries`; read it when a search behaves
unexpectedly.

## Fixed inputs

Every value here comes from `{cli} config`, which prints the effective
settings (`[tool.outlooks]` in the nearest `pyproject.toml`, each key
overridable by an `OUTLOOKS_*` env var) and where each came from. The Python
tooling reads nothing else. Run it first; the names below refer to its keys.

- **Mailbox:** `mailbox`. The connector signs in as the operator's own
  account and can't list shared mailboxes, so when `mailbox` is set every
  `outlook_email_search` call passes it as `mailboxOwnerEmail`. When it is
  unset, see [Personal mailbox](#personal-mailbox).
- **Own addresses:** `own_addresses`, the addresses that count as *us*: a
  message from one of them is `direction: out`.
- **System senders:** `system_senders`, automated senders that speak for us
  (a web system's notification address, say). A message from one is
  `direction: out` unless one of the own addresses is among its recipients, in
  which case it is a notice *to* us and `in`. A repo with none lists none.
- **Decision tag:** `decision_tag`, the subject tag the repo's decision
  letters carry, when it has one.
- **Ledger:** `ledger.csv` in `archive_dir`, one row per message the sweep
  classified, keyed by `internet_message_id`. It is both the dedup list and
  the high-water mark: the latest `received` in it is the next sweep's start,
  and `{cli} coverage` reports when it lags the archive.
- **Archive:** `archive_dir`, holding `hits/{mailbox}/{YYYY-MM}.jsonl` (every
  search hit), `messages/{id-hash}.json` (+ `{id-hash}.{n}.txt` per
  attachment text), and `coverage.csv` (the window pulls that ran to
  completion). Committed. See [Keep what the connector
  returns](#keep-what-the-connector-returns). `{cli} hash <internetMessageId>`
  prints a message's `{id-hash}`.
- **Captures:** `captured_dir` (gitignored). A PostToolUse hook
  (`.claude/hooks/outlook-capture.sh`, wired in `.claude/settings.json`;
  `{cli} hook` checks it and `{cli} hook --apply` wires it) writes every
  `outlook_email_search` and `read_resource` result verbatim there,
  subagents' calls included. That is the archive's only source. A result too
  large for the context reaches the hook as a note naming the file Claude
  Code saved it to; the hook copies that file beside the capture as
  `{same name}.saved.txt`, so the capture survives the original's cleanup.
- **Scratch:** `scratch_dir` (gitignored) for renders, page files, timeline
  files, and worklists; don't commit them.
- **Zone:** `timezone`. Every date shown to the operator is in this zone.
- **Profile:** `profile`, the repo's own prose on who it is, where names
  resolve, how the sweep classifies, what filing means, and what its records
  assert. When it is unset, `{cli} doc profile-template` lists what a profile
  would answer; ask the operator rather than guessing those answers.

## Keep what the connector returns

Every search page and every full read goes into the archive, unmodified,
before anything else happens with it. The hook has already written each one
to the captures directory; one command files them all:

```bash
{cli} import      # captures -> hits, messages, attachment texts, coverage.csv
{cli} coverage    # which windows are complete, and the gaps (read-only)
```

**Never type connector output into a file for the archive.** A retyped copy
drifts from what the connector returned: a date lands a year off, no-break
spaces are flattened, an attachment uri loses its `/attachments/` segment.
`import` reads the connector's own output, so there is no copy to get
wrong. It files only the configured mailbox, is idempotent
(run it as often as you like), and prints any stored message that differs
from its capture; those are left alone (`--replace` is the one-time repair,
the operator's call). `{cli} save` still exists for a payload that never
passed through the hook, which should not happen in a session.

`import` also records **coverage**: a filter-mode search (only
`afterDateTime`/`beforeDateTime`, no `query`, `sender`, or `folderName`)
whose captured pages cover every offset from 0 to `totalResultCount - 1`
becomes a row in `coverage.csv`, once every message it listed is archived.
It lists a window whose pages are not all captured as `INCOMPLETE`; page the
rest and import again. A lookup never makes a coverage row: it only finds the
mail it matched. `LOST capture` is a page or read whose output the hook saw
but `import` cannot recover (a spilled result whose file and copy are both
gone, or a response of no known shape): it is never read as an empty window,
so make that call again.

Whatever the connector hands back is the only copy of it we will ever have:
it is the evidence behind every date on a record, and re-reading a message
months later depends on the mailbox still holding it. Bodies go in raw — no
cleaning, no trimming, no summarizing into the ledger — and each attachment's
extracted text goes in beside its message, in attachment order. The store
drops only the fields that change between two reads of the same message (read
state, flag, categories, folder, page offset), so a second import of an
unchanged message is a no-op and one that contradicts what is stored is
listed rather than rewritten.

Save more than the message you came for: the whole window's hits (they are
free — one page is one tool call), every candidate's full read, the replies
on its thread, and the system senders' notifications (identifiers and titles
live in the body, which the hit's summary truncates away). Mail that
identifies a confidential party is archived like
anything else, but never rendered or filed anywhere shared; the profile says
which mail that is.

The connector returns attachments as **text only, never bytes**, so the text
is what the archive keeps. Don't try to reconstruct the original file or ask
the operator to download items to get one — an original that has to be filed
is named in the report for the operator to drag in by hand.

An attachment the connector returns as an image rather than text — an inline
signature logo, an institution banner (`isInline: true`, `image/png`) — gets
**no text file at all**. Never write a caption, a transcription of the
visible words, or a note explaining that the image had no text: the `.txt`
beside a message is what the connector extracted, and a hand-written
stand-in in that slot is indistinguishable from real attachment text on a
later read. Skip the `read_resource` on that attachment's uri, so no text is
captured for it; the message JSON still lists it. If one is read anyway,
`import` refuses it (`REFUSED attachment read (an image attachment)`: the
connector returns a note about the image, not text) and files nothing.

An attached email or calendar item (`message/rfc822`) sometimes reads as text
and sometimes comes back as a JSON note ("nested item content is not
exposed"), and so does a PDF with no extractable text (a scan: "PDF text
extraction failed"). The note is not attachment text: `import` refuses it
(`a connector note, not text`), like an image read.

A calendar invite (`text/calendar`, `application/ics`, a `.ics` file) always
comes back as a "Binary attachment" note, never text: skip its read like an
image's. `{cli} audit` counts it as missing but never lists it for a re-read.

A result too large for the context (a long thread, about 55k characters or
more) reaches the hook only as a note naming the file Claude Code saved it
to; the hook copies that file beside the capture and `import` reads the copy
(falling back to the original), so nothing needs re-reading. A spill whose
copy and original are both gone imports as `LOST capture`: read it again.

## Using the connector

`outlook_email_search` returns thin hits (subject, sender, `recipients`,
dates, `hasAttachments`, `internetMessageId`, `uri`, a truncated summary).
Bodies, `conversationId`, and the attachment list need `read_resource(uri)`.

**Four parameter shapes, and what each is for.** Every call passes
`mailboxOwnerEmail` (when `mailbox` is set) and `limit: 25`.

| Goal | Parameters |
|---|---|
| Everything with one person, both directions | `query: <surname or address local-part>` — nothing else |
| Only what we sent them | `folderName: "Sent Items"` + the same `query` |
| Only what they sent us | `sender: <their address>` |
| Everything in a date window (the sweep, a window pull) | `afterDateTime` (+ optional `beforeDateTime`), no `query`, no `sender` |

**The one that surprises people:** a bare `query` matches **recipients**, not
just subject and body, so a surname finds *our* mail to that person as well
as theirs. That is what makes a one-call, two-directional lookup possible.
The `recipient` parameter cannot be used with a shared mailbox at all — don't
reach for it.

**Hard constraints** (the connector rejects these combinations outright):

- `recipient` with `mailboxOwnerEmail`, `folderName`, or `order`
- `query` with `order`
- `query` with `sender` / `afterDateTime` / `beforeDateTime` when
  `mailboxOwnerEmail` or `folderName` is set — put KQL (`from:`, `subject:`,
  `received>=`) inside the `query` string instead

`folderName` *does* combine with `mailboxOwnerEmail`, with either a `query`
or a date window. `folderName: "Sent Items"` is the only clean way to isolate
outbound mail.

**Two paging modes.** A `query` search pages by `nextCursor` and reports no
total; a filter-only search pages by `nextOffset`, sorts newest-first, and
reports `totalResultCount`. A `query` search is also relevance-ranked and
*can silently omit matches* — fine for a lookup, never trust it for a sweep.
Offsets stop at 1,000: a window holding more than that can't be paged to the
end, which is why window mode splits a range until every piece fits.

**Dates.** Use each message's own `receivedDateTime` (UTC) and convert to the
configured `timezone` for anything shown to the operator. Do **not** date a
message from the `From:`/`Sent:` header inside a quoted reply chain: those
render in the *sender's* timezone and can be a day off.

**`conversationId` is not a lookup key.** It isn't searchable (no parameter,
and KQL `conversationid:` returns nothing), search hits don't carry it, and
it tracks the subject-thread rather than the correspondence — a correspondent
who starts a fresh subject instead of replying gets a new id, so one person's
exchange splits across several. Use it only to group messages already in
hand. `{cli} doc queries` has the detail.

**Uris go stale.** A message's `uri` names its current folder copy; when mail
is moved, the old uri comes back "not found". Re-pull the window it came
from to get a fresh one.

**Throttling.** The connector limits the *rate* of calls across every agent
and every session on the account together, searches and reads alike. A 429
carries a 62-second retry-after; sleep 65 seconds **plus a random 0-30**
before retrying, because agents that all sleep exactly 65 wake together and
trip it again, and wait 120-150 seconds after a second 429 on the same call.
Window mode (`{cli} skill window`) has the measured rates.

**The allow rules.** The connector has two read-only tools,
`outlook_email_search` and `read_resource`, and they are the only two a repo
may allow in `.claude/settings.json`. An allow rule keeps the auto-mode
classifier from blocking a subagent's reads (without one, some are refused
as "PII" or "exfiltration"); window mode's bulk readers call `read_resource`
from subagents and need its rule. Never widen either rule to the
connector's write tools (send, forward, reply, delete, draft, label): a
pattern that matches the whole connector matches those too.

### Personal mailbox

With `mailbox` unset the skill reads the signed-in account's own mailbox.
Set `own_addresses` to the account's addresses: the archive files under the
first of them (`hits/{address}/`, the `mailbox` column of `coverage.csv`),
and every command refuses until it is set. Searches then **omit
`mailboxOwnerEmail`** (wherever a mode or brief says to pass it, leave it
out), and the `recipient` parameter becomes usable (the connector rejects it
only alongside `mailboxOwnerEmail`, `folderName`, or `order`).

`import` takes a capture as this mailbox's when it names no mailbox at all
(an empty page, a uri with no `owner`) or names any of `own_addresses`. A
capture that names another mailbox is counted as `other mailbox` and left
alone. With `mailbox` set, a capture that names none is *not* taken: a
search that left out `mailboxOwnerEmail` read the operator's own account,
not the configured mailbox.

The connector answers differently for the account's own mailbox:

- **A uri carries no `owner`.** Hits, messages, and attachments all read
  by the uri as returned.
- **A hit lists no recipients** (`recipients` is null), so `{cli} split`
  matches our own mail to a person only through its subject or summary.
  The `recipient` parameter is accepted here, and is the way to list what
  was sent to someone.
- **No page reports `totalResultCount`.** A full page ends with
  `moreResults` and a `nextOffset`; the pull ends at a page that comes back
  short, or at the empty page after a full one. Page until then. `import`
  records the window as covered once that last page is captured, and lists
  it as `INCOMPLETE` (`its last page missing`) until it is.
- **A one-hit probe sizes nothing**, since it carries no total. The window
  planner (`{cli} windows`) and the id proof (`{cli} totals --check`) need
  sizes before paging, so they do not apply: pull fixed windows (a month, a
  week) directly, and halve one whose paging reaches offset 1,000.
- A message read and an attachment read have the same shape as in a shared
  mailbox, and an empty window comes back the same way, as nothing at all.
