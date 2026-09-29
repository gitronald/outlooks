# Reader brief (read-only Outlook lookup)

Work from the repo root your launch prompt names; run every command there.

Run `{cli} config` first. It prints `own_addresses`, `system_senders`,
`notice_senders`, `timezone`, `scratch_dir` (where your timeline files go; called `<scratch>`
below), and `archive_dir`.

Load the tool schema: ToolSearch query "select:mcp__claude_ai_Microsoft_365__read_resource".

Your worklist file lists lines `NAME-NN <uri>`. For each line:

1. `mcp__claude_ai_Microsoft_365__read_resource` on the uri, copied from the
   worklist exactly. Do not write the result to a file: a hook captures every
   read verbatim, and the main session archives the captures after you
   finish.
2. Attachments: go through the message's `attachments` list one entry at a
   time; check that entry's own `contentType` and `isInline`, and read that
   same entry's own `uri` only if it carries text (a document, PDF, or text
   file); the hook captures that too. Never read an image attachment (inline
   signature logo, banner, `image/*` content type, `isInline: true`), and
   never write a caption, description, or placeholder for one. Don't pair
   names with uris by position or by eye: a uri taken from the neighbouring
   entry is how an image gets read. A hit's `hasAttachments` is true for an
   inline logo alone, so it does not predict a text file. If reading an
   attachment's uri returns the parent message JSON again instead of text,
   the uri's percent-encoded `=` (`%3D`) is the cause: decode it to a literal
   `=` in both the message-id and attachment-id segments and read again
   before concluding the attachment has no text. Change nothing else in the
   uri (`mail:///messages/{message-id}/attachments/{attachment-id}?owner=...`):
   a uri missing its `/attachments/` segment returns the parent message too.
   An attached email (`message/rfc822`) is text-bearing: read it like a
   document; if it comes back as a note that its content is not exposed,
   count it as no text and move on. A calendar invite (`text/calendar`, or a name ending `.ics`) always comes
   back as a "Binary attachment" note: skip it like an image.
3. If a call returns HTTP 429 / throttling, run `sleep $((65 + RANDOM % 30))`
   in Bash and retry the same uri; if that retry is throttled too,
   `sleep $((120 + RANDOM % 30))` (pass the Bash tool a `timeout` of 200000:
   the default two minutes is shorter than the sleep) and retry once more.
   The random part matters: other agents share the limit, and agents that
   all wake at the same second trip it again. Make one call at a time. If a
   result is too large and comes back as a note naming a saved file, check
   that file's `attachments` entries with `jq` or `uv run python` (never bare
   `python3`); the hook already captured the result.
4. Write `<scratch>/NAME-NN.timeline.json` with these keys, named exactly
   as here (`{cli} doc lookup/timeline` is the full list; `date_local` and
   `internet_message_id` are required):
   - `file`: `<archive_dir>/messages/<hash>.json`, where `<hash>` is printed
     by `{cli} hash <internetMessageId>`; the file appears once the main
     session imports;
   - `received_utc`: the message's receivedDateTime;
   - `date_local`: that instant converted to the configured `timezone`,
     YYYY-MM-DD;
   - `direction`: "out" when the sender is one of `own_addresses`, or when
     the sender is one of `system_senders`, is not one of `notice_senders`,
     and none of `own_addresses` is among the recipients; "in" otherwise
     (in either list, `*` in an entry stands for any run of characters);
   - `sender` (address), `to` (list of recipient addresses), `subject`,
     `internet_message_id`, `weblink` (the message's webLink), `attachments`
     (list of names);
   - `summary`: the first 600 characters of the body as plain text, quoted
     reply chain excluded;
   - `system`: true when the sender is one of `system_senders` or
     `notice_senders`.

Rules: any Python you run is `uv run python`, never bare `python3`. Write
nothing under the archive, and do not run `{cli} import` or `{cli} save`;
never touch the ledger or any of the repo's records. Your timeline file is a
report, not a decision: `direction` and `system` are the only judgments you
make.

Return only: for each NAME-NN, the file path from its timeline, date_local,
and a two-line summary of what the message says (what was asked or decided,
any deadline or outcome named). Nothing else.
