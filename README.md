# outlooks

Archive and read an Outlook mailbox through the claude.ai Microsoft 365
connector, and ship the Claude Code skill that drives it.

The connector is only reachable from an interactive Claude Code session, so
this package never talks to Microsoft itself. A PostToolUse hook writes every
`outlook_email_search` and `read_resource` result verbatim to a captures
directory; `outlooks import` files those captures into a committed, write-once
archive, and records every date window whose pages were all captured as
covered. The skill (`/outlooks`) tells an agent how to search, page, and read
the mailbox so that the archive fills itself.

## Install

```bash
uv add git+https://github.com/gitronald/outlooks
uv run outlooks install     # the /outlooks skill stub, under .claude/skills/
uv run outlooks hook --apply  # the capture hook script + its settings entry
```

Python 3.11 or later. `render` needs [pandoc](https://pandoc.org) on the PATH.

`uv add git+...` adds an unpinned source to the main dependencies, and a CI
job with no access to this repository cannot clone it. To pin a release and
keep it out of CI, put it in its own dependency group:

```toml
[dependency-groups]
mailbox = ["outlooks"]

[tool.uv]
default-groups = ["dev", "mailbox"]

[tool.uv.sources]
outlooks = { git = "https://github.com/gitronald/outlooks", tag = "vX.Y.Z" }
```

In CI, sync with `uv sync --frozen --no-group mailbox` and set `UV_NO_SYNC=1`
for the job, or a later `uv run` re-syncs the default groups and fails on the
clone. CI never installs `outlooks` under this setup, so nothing in the
consuming package may import it: only the operator's session runs it.

## Configure

Everything repo-specific comes from `[tool.outlooks]` in the nearest
`pyproject.toml` holding the table, each key overridable by an
`OUTLOOKS_{KEY}` environment variable. Paths are relative to that file.

```toml
[tool.outlooks]
mailbox = "team@example.org"            # unset: the signed-in account's own mailbox
own_addresses = ["team@example.org"]    # default: [mailbox]; required when mailbox is unset
system_senders = ["notices@system.example.org"]
decision_tag = "[Decision]"
archive_dir = "data/outlook"
captured_dir = "temp/outlook/captured"
scratch_dir = "temp/outlook"
timezone = "America/Los_Angeles"
render_label = "Message"                # render's --label when none is passed
profile = "docs/outlook-profile.md"
```

For the signed-in account's own mailbox, leave `mailbox` unset and list the
account's addresses in `own_addresses`: the archive files under the first of
them. Its searches report no totals, so a window counts as covered once its
last page is captured, and the window planner does not apply.

`uv run outlooks config` prints the effective values and where each came from.
The `profile` is repo-owned prose the skill reads for what this package cannot
know: where a name resolves, how the sweep classifies, what filing a candidate
means, and which of the repo's own records a message can contradict. `uv run
outlooks doc profile-template` prints the headings it should answer.

## The archive

| Path | Holds |
|---|---|
| `hits/{mailbox}/{YYYY-MM}.jsonl` | every search hit ever listed, deduped by `internetMessageId` |
| `messages/{id-hash}.json` | full `read_resource` payloads, bodies raw |
| `messages/{id-hash}.{n}.txt` | attachment `n`'s extracted text |
| `coverage.csv` | one row per window pull that ran to completion |
| `mass-sends.csv` | optional, hand-owned: bulk sends whose copies are read once |

`{id-hash}` is the first 16 hex digits of the SHA-256 of the
`internetMessageId` (`outlooks hash <id>`). Both tiers are write-once: a second
import of an unchanged message is a no-op, and one that differs is listed, not
rewritten:

```
  DIFFERS from the stored copy in body.content, attachments[].uri: temp/outlook/captured/20260105T101500-4242.json
```

The line names the fields that differ, so the two copies need no diffing by
hand. Which copy is right decides what to do:

- **The stored copy was not written from a capture** (it was saved by hand
  with `outlooks save`, or typed out): the capture is the connector's own
  output, and `outlooks import --replace` rewrites the stored copy from it.
- **The capture is the older of the two** (the message changed after the
  capture was written, and the stored copy came from a later read): the
  stored copy is right. Leave it, and do not pass `--replace`.

`outlooks import <paths> --replace` limits the rewrite to the captures named,
so one repair never rewrites anything else that differs.

## Commands

| Command | Does |
|---|---|
| `outlooks import [paths] [--replace]` | file the hook's captures; record complete windows |
| `outlooks coverage [--since] [--ledger]` | covered windows, gaps, unread counts, ledger lag, and the next sweep's start |
| `outlooks senders [--since]` | inbound hits counted by sender, each marked `own`, `system`, or `unlisted` |
| `outlooks split <name> --match ...` | a lookup's hits: archived (timeline written) vs to-read |
| `outlooks check <name>` | a lookup's timeline files against the archive |
| `outlooks lookup-reset <name>` | move a lookup's page and timeline files aside before a repeat lookup |
| `outlooks worklist --after --before --out` | reader worklists for a full read of a range |
| `outlooks totals [prefix ...]` / `--check AFTER BEFORE` | window sizes; the item-id proof |
| `outlooks windows init/fill/split/batches/remaining` | the backfill planner |
| `outlooks audit <prefix> [--sweep]` | attachment-text audit for a period |
| `outlooks render <message.json>` | a message as `{Surname} - {render_label}.docx` (pandoc, sandboxed) |
| `outlooks save <payload>` | archive a hand-saved payload (sessions use `import`) |
| `outlooks hash <id>` | a message's archive file stem |
| `outlooks hook [--apply]` | check or wire the capture hook |
| `outlooks config` | the effective settings |
| `outlooks doctor` | settings, profile, archive, hook, and skill stub in one read-only pass; exit 1 if any check fails |
| `outlooks skill`, `outlooks doc`, `outlooks install`, `outlooks permissions` | the skill, its documents, the stub, and its permission profile ([pkgskills](https://github.com/gitronald/pkgskills)) |

## Python API

A repo that imports this package may import the names below, and nothing
else. Every other module is internal, and the commands are its interface. A
change to one of these names, or to a parameter of one, is listed in the
changelog under *Changed* or *Removed*.

| Module | Public names |
|---|---|
| `outlooks.store` | `archive_root`, `id_hash`, `stable`, `load_hits`, `load_messages`, `newest_hit`, `StoreError` |
| `outlooks.classify` | `Mail`, `Facts`, `view`, `classify`, `is_reply`, `thread_subject` |
| `outlooks.detail` | `detail`, `details_by_id`, `body_text` |
| `outlooks.config` | `Settings`, `load`, `settings`, `KEYS`, `DEFAULTS`, `ConfigError`, `mailbox`, `archive_dir`, `captured_dir`, `scratch_dir`, `zone`, `zone_label` |

The API reads the archive and never writes it: a repo fills the archive
through `outlooks import`.

Reading the archive:

```python
from outlooks import classify, detail, store

hits = store.load_hits()  # every message ever listed, by internetMessageId
messages = store.load_messages()  # the ones read in full

for mid, hit in hits.items():
    mail = classify.view(messages.get(mid, hit))
    role = classify.classify(mail).role
    print(mail.day, role, mail.sender, mail.subject)

for mid, message in messages.items():
    fields = detail.detail(message)  # from, to, cc, bcc, attachments, body
```

`classify` reports the role a message plays (`arrival`, `followup`, `ours`,
`decision`, `system`) and stops there. What a system sender's notification
says is the repo's own business, and the place for it is a thin wrapper in
the repo. For a ticketing system whose notices read "Ticket #12 was opened
by ...":

```python
import re

from outlooks import classify as cl

OPENED = re.compile(r"Ticket #(\d+) was opened by (.+?)\.")


def facts(payload):
    mail = cl.view(payload)
    base = cl.classify(mail)
    if base.role != "system":
        return base.role, None
    found = OPENED.search(mail.text)
    return ("system-opened", int(found[1])) if found else ("system-other", None)
```

`Mail` carries what such a wrapper reads: `sender`, `recipients`, `subject`,
and `text` (the body as plain text for a message read in full, the summary
for a search hit).

## Development

```bash
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyrefly check
```

The tests use synthetic fixtures only (`example.org` addresses and an invented
cast), and every test runs in its own temporary directory with its settings
pinned through `OUTLOOKS_*`, so the suite never reads a real archive.
