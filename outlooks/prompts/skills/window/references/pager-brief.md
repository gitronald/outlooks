# Window pager brief

Work from the repo root your launch prompt names. Run `{cli} config` first:
it prints `mailbox`, the address to pass as `mailboxOwnerEmail` (if it is
unset, omit that parameter).

Load the tool schema: ToolSearch query "select:mcp__claude_ai_Microsoft_365__outlook_email_search".

Call `mcp__claude_ai_Microsoft_365__outlook_email_search` with exactly these
parameters and nothing else (no query, no sender, no folderName, no
recipient, no order):

- mailboxOwnerEmail: `<mailbox>`
- afterDateTime: AFTER (given in your prompt)
- beforeDateTime: BEFORE (given in your prompt; omit it if the prompt says
  "none")
- limit: 25
- offset: 0, then the returned `nextOffset`, until offsets cover
  `totalResultCount`

If the pages report no `totalResultCount` (the signed-in account's own
mailbox), keep following `nextOffset` until a page comes back with fewer
messages than `limit` or with none, and report the number of messages you
paged in place of the total.

Stop as soon as a page lists no messages. An empty window returns an empty
result at offset 0 (the window holds no mail: report `totalResultCount: 0`),
and a page past the end returns only
`{"totalResultCount": <the offset you asked for>}` with no hits, which is
not a count and not a reason to keep going. Take `totalResultCount` from the
offset-0 page, and never call an offset that is not a returned `nextOffset`.

One call at a time. If a call returns HTTP 429 / throttling, run
`sleep $((65 + RANDOM % 30))` in Bash and retry the same offset; if that
retry is throttled too, `sleep $((120 + RANDOM % 30))` (pass the Bash tool a
`timeout` of 200000: the default two minutes is shorter than the sleep) and
retry once more. The random part matters: other agents share the limit, and
agents that all wake at the same second trip it again.

A hook captures every page; write no files and do not restate or summarize
the hits.

Return only: totalResultCount, the number of pages fetched, and the list of
offsets you called.
