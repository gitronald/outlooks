# Bulk reader brief (window-mode full read)

Work from the repo root your launch prompt names; run every command there.

Load the tool schema first: ToolSearch query "select:mcp__claude_ai_Microsoft_365__read_resource".

Your worklist file has lines `NAME <uri>`. Count them first
(`wc -l <worklist>`): your return has exactly one line per worklist line,
and a NAME missing from the middle of it is taken as done. Work through them
one at a time, in order:

1. `mcp__claude_ai_Microsoft_365__read_resource` on the uri, copied from the
   worklist exactly (never retyped: a one-character slip reads a
   neighbouring message). Do not write the result anywhere: a hook captures
   every read verbatim, and the main session archives the captures
   afterwards.
2. Attachments: go through the message's `attachments` list one entry at a
   time. For each entry, check its own `contentType` and `isInline` first,
   and read that same entry's own `uri` only if it carries text (a document,
   PDF, spreadsheet, or text file). Skip every image (`image/*`, or
   `isInline: true`), whether it is a signature logo, a banner, or a
   screenshot. Don't match attachments to uris by position or by eye: taking
   the uri from a neighbouring entry is how an image gets read. Never write a
   caption or placeholder for an image. If an attachment uri returns the
   parent message JSON again instead of text, decode `%3D` to a literal `=`
   in both the message-id and attachment-id segments of the uri and read
   once more. Change nothing else: the uri keeps its shape,
   `mail:///messages/{message-id}/attachments/{attachment-id}?owner=...`.
   Dropping the `/attachments/` segment, or putting the attachment id where
   the message id goes, also returns the parent message, so it looks like the
   same failure. If that returns "Attachment not found", the uri's message
   segment is the attachment id repeated: read
   `mail:///messages/{the message's own id}/attachments/{the decoded attachment id}?owner=...`
   instead (import matches it by the attachment id).
   An attached email (`message/rfc822`) is text-bearing: read its `uri` like
   a document. It sometimes returns the email's text and sometimes a note
   saying its content is not exposed; only after reading it and getting the
   note, count it as 0 texts and move on. A calendar invite (`text/calendar`, or a name ending `.ics`) always comes
   back as a "Binary attachment" note: skip it like an image.
3. If a call returns HTTP 429 / throttling, run `sleep $((65 + RANDOM % 30))`
   in Bash and retry the same uri; if that retry is throttled too,
   `sleep $((120 + RANDOM % 30))` (pass the Bash tool a `timeout` of 200000:
   the default two minutes is shorter than the sleep) and retry once more
   before marking it `failed`. The random part matters: other readers share
   the limit, and readers that all wake at the same second trip it again.
   Make one call at a time, never in parallel. `failed` means the uri was
   called and throttled three times; a line not yet called is not failed,
   so call it before returning.
4. If a result is too large it comes back as a note that reads
   `Error: result (N characters ...) exceeds maximum allowed tokens. Output
   has been saved to <file>`. Despite the word "Error", the read succeeded:
   the NAME is `read`. Check that file's `attachments` entries with `jq` or `uv run python`
   (never bare `python3`), and read only the text-bearing ones as in step 2.
   The hook already captured the result.
5. If a message comes back "not found", note it and move on; do not retry
   more than once.

If your launch prompt says the lines are attachment uris (an attachment
sweep), read each uri directly as in step 2, and still skip any that turns
out to be an image.

Rules: any Python you run is `uv run python`, never bare `python3`. Write no
files at all, and nothing under the archive. Do not run `{cli} import` or
`{cli} save`. Do not summarize message contents.

Return only: one line per worklist line, in order, with its NAME, one word,
`read`, `notfound`, or `failed`, and the number of attachment texts you read
for it. Nothing else.
