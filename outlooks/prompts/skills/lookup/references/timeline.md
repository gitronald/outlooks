# Timeline files (read-only Outlook lookup)

A lookup keeps one file per message in the scratch directory,
`{name}-{nn}.timeline.json`: `{name}` is the lookup's name and `{nn}` the
message's number, oldest first, two digits or more. The split
(`{cli} split`) writes the file for a message already in the archive, and a
reader writes it for a message it read. `{cli} check {name}` reads every one
of them back.

The files are scratch, but a lookup resumed from an earlier run reads them,
so their keys are fixed. A change to a key `check` reads is listed in the
changelog under *Changed*.

## Keys

| Key | Value | `check` reads it | Required |
|---|---|---|---|
| `file` | the archived message, `<archive_dir>/messages/<id-hash>.json` | no | no |
| `received_utc` | the message's `receivedDateTime`, as the connector gave it | no | no |
| `date_local` | that instant's date in the configured zone, `YYYY-MM-DD` | yes | yes |
| `direction` | `in` or `out` | yes | no |
| `sender` | the sender's address | no | no |
| `to` | the recipients' addresses, a list | no | no |
| `subject` | the subject | yes | no |
| `internet_message_id` | the angle-bracketed id, as returned | yes | yes |
| `weblink` | the message's `webLink` | no | no |
| `attachments` | the attachments' names, a list | no | no |
| `summary` | the start of the body as plain text | no | no |
| `system` | `true` when the sender is one of `system_senders` | yes | no |
| `source` | `archive`, in a file the split wrote; a reader writes none | no | no |

`check` finds the archived message from `internet_message_id` and compares
`date_local` with the date the archive's `receivedDateTime` implies. It
prints `direction`, `system`, and `subject` as it found them, and decides
nothing from them.

## What `check` reports

A row is `ok` or `BAD`, and a `BAD` row is followed by one line saying why:

| Finding | Means |
|---|---|
| `missing key date_local` | the file has no such key, or an empty one. It was written by another build or cut short: write it again with the keys above |
| `missing key internet_message_id` | the same, for the id |
| `not archived` | no `messages/<id-hash>.json` for the id: run `{cli} import`, or read the message |
| `date_local 2026-01-05, the archive says 2026-01-06` | the file's date is wrong: the archive's is the one to report |

`check` exits 1 when any row is `BAD`, and when there are no timeline files
at all.

## A repeat lookup

Page and timeline files from an earlier lookup of the same name are moved
aside, never reused: `{cli} lookup-reset {name}` moves them to
`<scratch_dir>/earlier/<timestamp>/` and prints what it moved. It deletes
nothing.
