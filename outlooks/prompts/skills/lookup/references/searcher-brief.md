# Searcher brief (read-only Outlook lookup)

Work from the repo root your launch prompt names; run every command there.

Run `{cli} config` first. It prints `mailbox` (the address to pass as
`mailboxOwnerEmail`; if it is unset, omit `mailboxOwnerEmail` everywhere
below) and `scratch_dir` (where your page files go; called `<scratch>`
below).

Load the tool schema: ToolSearch query "select:mcp__claude_ai_Microsoft_365__outlook_email_search".

Your worklist file has one line: `NAME KIND TERM [FALLBACK]`, where KIND is
`query`, `sender`, `title`, or `sent`.

Call `mcp__claude_ai_Microsoft_365__outlook_email_search` with
`mailboxOwnerEmail: <mailbox>` and `limit: 25`, plus:

- `query`: `query: TERM` — nothing else. Page with `nextCursor` while
  `moreResults` is set. If the first page is empty and a FALLBACK term is
  given, run once more with `query: FALLBACK`.
- `sender`: `sender: TERM` — no query, no dates, no folderName. Page with
  `nextOffset` until `totalResultCount` is covered (or, when no total is
  reported, until a page comes back short or empty). If the connector rejects
  TERM, retry once with FALLBACK (the full address).
- `title`: `query: "TERM"` (quoted phrase) — nothing else. Page with
  `nextCursor`.
- `sent`: `folderName: "Sent Items"` + `query: TERM` — nothing else. Page
  with `nextCursor` while `moreResults` is set.

If a call returns HTTP 429 / throttling, run `sleep $((65 + RANDOM % 30))` in
Bash and retry the same page; if that retry is throttled too,
`sleep $((120 + RANDOM % 30))` (pass the Bash tool a `timeout` of 200000: the
default two minutes is shorter than the sleep) and retry once more. The
random part matters: other agents share the limit, and agents that all wake
at the same second trip it again.

Never pass `recipient` or `order`, and never combine `query` with `sender` or
dates.

For each page n (from 1) that returns one or more hits, write the raw hit
objects, unmodified, as a bare JSON array `[{...}, {...}]` (not wrapped in an
object) to `<scratch>/NAME-page-{n}.json` (query),
`<scratch>/NAME-sender-page-{n}.json` (sender),
`<scratch>/NAME-title-page-{n}.json` (title), or
`<scratch>/NAME-sent-page-{n}.json` (sent) via Bash (`mkdir -p <scratch>`
first). These are working files for the split step, not the archive: a hook
captures every search you run, and the main session archives those
captures. Leave the page files in place: the split step reads them after you
finish.

An empty result is a valid answer: report it once, do not re-run the call to
confirm, and write no page file. Do not call read_resource. Write nothing
under the archive (`archive_dir` in `{cli} config`), and do not run
`{cli} import` or `{cli} save`; never touch the ledger or any of the repo's
records. Any Python you run is `uv run python`, never bare `python3`.

Return only: the number of pages and hits, and for each hit its
internetMessageId, receivedDateTime, sender address, and subject. No uris, no
bodies.
