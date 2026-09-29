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

Python 3.11 or later. `render` needs [pandoc](https://pandoc.org) on the PATH.

There are two recipes, one for each way a repo uses the package. Both pin a
release.

### Operator only

The repo runs the commands, the skill, and the capture hook from an
interactive session, and none of its own code imports `outlooks`. Put the
package in a dependency group of its own, so it is installed for the
operator and the repo's package cannot come to depend on it by accident:

```toml
[dependency-groups]
mailbox = ["outlooks==X.Y.Z"]

[tool.uv]
default-groups = ["dev", "mailbox"]
```

### Importing

The repo's own code or tests `import outlooks` (see [Python API](#python-api)).
It is a plain dependency:

```bash
uv add "outlooks==X.Y.Z"
```

### Either way

```bash
uv sync
uv run outlooks install       # the /outlooks skill stub, under .claude/skills/
uv run outlooks hook --apply  # the capture hook script and its settings entry
uv run outlooks doctor        # every check reads ok
```

Where the package index cannot be used, the same release installs from its
tag. Keep the dependency as above and add a source:

```toml
[tool.uv.sources]
outlooks = { git = "https://github.com/gitronald/outlooks", tag = "vX.Y.Z" }
```

A git source cannot be resolved on a network that allows only the package
index, and it pins one tag, not a version range, so the index is the first
choice.

### In CI

`outlooks doctor` makes no connector call, so CI can run it. It checks what
the repo commits: the `[tool.outlooks]` table, the profile, the archive, and,
under `.claude/`, the hook script, `settings.json`, and the skill stub.

```yaml
- run: uv sync --frozen
- run: uv run outlooks doctor
```

## Upgrade

```bash
# 1. change the version (or the tag) in pyproject.toml, then
uv lock --upgrade-package outlooks
uv sync
uv run outlooks hook --apply   # rewrites a hook script an earlier release wrote
uv run outlooks install        # rewrites the skill stub, or only its stamp
uv run outlooks doctor
```

`install` rewrites the stub's content when the new release changed it.
When it did not, only the stamp line changes, to name the new version.

Read the changelog's *Changed* and *Removed* entries for every release
between the two versions first: they say what a consuming repo has to do.

The lock file is the pin that holds. For a release from the index it records
the version and the hashes of its files, and for a git source the commit the
tag resolved to, so `uv sync --frozen` installs the same code whatever
happens upstream.

## Configure

Everything repo-specific comes from `[tool.outlooks]` in the nearest
`pyproject.toml` holding the table, each key overridable by an
`OUTLOOKS_{KEY}` environment variable. Paths are relative to that file.

```toml
[tool.outlooks]
mailbox = "team@example.org"            # unset: the signed-in account's own mailbox
own_addresses = ["team@example.org"]    # default: [mailbox]; required when mailbox is unset
system_senders = ["notices@system.example.org"]  # automated, speaks for us
notice_senders = ["postmaster@*"]       # automated, writes to us: a bounce, say
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

The two sender lists differ in direction. Mail from one of
`system_senders` is ours (`out`) unless one of the own addresses is among
its recipients. Mail from one of `notice_senders` is a notice to us (`in`)
whoever it is addressed to: a bounce names the address that failed, not
ours. An entry of either list is an address, or a pattern in which `*`
stands for any run of characters, for a sender that writes from many
addresses. An address that both lists match is a notice sender.

`uv run outlooks config` prints the effective values and where each came from.
The `profile` is repo-owned prose the skill reads for what this package cannot
know: where a name resolves, how the sweep classifies, what filing a candidate
means, and which of the repo's own records a message can contradict. `uv run
outlooks doc profile-template` prints the headings it should answer.

## Trying it without touching the archive

Every path is a setting, and every setting has an `OUTLOOKS_*` override, so
any command runs against a copy. To see what `import --replace` would
rewrite before it rewrites anything:

```bash
cp -r data/outlook temp/outlook-trial
OUTLOOKS_ARCHIVE_DIR=temp/outlook-trial \
OUTLOOKS_SCRATCH_DIR=temp/outlook-trial-scratch \
  uv run outlooks import --replace
git diff --no-index data/outlook temp/outlook-trial
```

The captures are only read, so `captured_dir` can stay as it is.
`outlooks config` run with the same variables shows each path and that it
came from the environment.

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
hand. It names none when the stored copy is not JSON. Which copy is right
decides what to do:

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
| `outlooks senders [--since]` | inbound hits counted by sender, each marked `own`, `system`, `notice`, or `unlisted` |
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
| `outlooks.classify` | `Mail`, `Facts`, `view`, `classify`, `is_reply`, `is_auto_reply`, `thread_subject` |
| `outlooks.detail` | `detail`, `details_by_id`, `body_text` |
| `outlooks.config` | `Settings`, `load`, `settings`, `KEYS`, `DEFAULTS`, `ConfigError`, `is_system_sender`, `is_notice_sender`, `mailbox`, `archive_dir`, `captured_dir`, `scratch_dir`, `zone`, `zone_label` |

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

A `followup` carries `extra["auto"]`, true for an automatic reply.
`is_auto_reply(subject)` is the same test for a repo that has only the
subject in hand. A `system` message from one of `notice_senders` carries
`extra["notice"]`, true. `config.is_system_sender(address)` and
`config.is_notice_sender(address)` say which list an address is in,
patterns included, which `address in settings().system_senders` does not.

`Mail` carries what such a wrapper reads: `sender`, `recipients`, `subject`,
and `text` (the body as plain text for a message read in full, the summary
for a search hit).

## Testing against it

A consuming repo's tests pin every setting through `OUTLOOKS_*` in a
fixture, so they never read the repo's own `[tool.outlooks]` table, its
archive, or the zone of the machine they run on:

```python
import pytest


@pytest.fixture(autouse=True)
def outlooks_settings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OUTLOOKS_MAILBOX", "desk@example.org")
    monkeypatch.setenv("OUTLOOKS_OWN_ADDRESSES", "desk@example.org")
    monkeypatch.setenv("OUTLOOKS_SYSTEM_SENDERS", "notices@system.example.org")
    monkeypatch.setenv("OUTLOOKS_NOTICE_SENDERS", "postmaster@*")
    monkeypatch.setenv("OUTLOOKS_DECISION_TAG", "[Decision]")
    monkeypatch.setenv("OUTLOOKS_TIMEZONE", "America/Los_Angeles")
    monkeypatch.setenv("OUTLOOKS_ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setenv("OUTLOOKS_CAPTURED_DIR", str(tmp_path / "captured"))
    monkeypatch.setenv("OUTLOOKS_SCRATCH_DIR", str(tmp_path / "scratch"))
    monkeypatch.delenv("OUTLOOKS_PROFILE", raising=False)
    monkeypatch.delenv("OUTLOOKS_RENDER_LABEL", raising=False)
```

The settings are resolved again whenever the working directory or an
`OUTLOOKS_*` variable changes, so a test that sets one more variable gets
it. A test that needs an archive writes its own messages under `tmp_path`
in the connector's shape, with invented senders.

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

### Releases

A published tag never moves. A fix ships as the next version, under its own
changelog heading, because a consuming repo's lock file names the commit a
tag resolved to, and a tag that moved would leave two repos on the same
version running different code.

The changelog's preamble lists what counts as a change a consuming repo has
to hear about. Each such change is entered under *Changed* or *Removed*,
with what to do.
