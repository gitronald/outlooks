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
rewritten (`import --replace` is the deliberate repair).

## Commands

| Command | Does |
|---|---|
| `outlooks import [paths] [--replace]` | file the hook's captures; record complete windows |
| `outlooks coverage [--since]` | covered windows, gaps, unread counts, ledger lag |
| `outlooks split <name> --match ...` | a lookup's hits: archived (timeline written) vs to-read |
| `outlooks check <name>` | a lookup's timeline files against the archive |
| `outlooks worklist --after --before --out` | reader worklists for a full read of a range |
| `outlooks totals [prefix ...]` / `--check AFTER BEFORE` | window sizes; the item-id proof |
| `outlooks windows init/fill/split/batches/remaining` | the backfill planner |
| `outlooks audit <prefix> [--sweep]` | attachment-text audit for a period |
| `outlooks render <message.json>` | a message as `{Surname} - {render_label}.docx` (pandoc, sandboxed) |
| `outlooks save <payload>` | archive a hand-saved payload (sessions use `import`) |
| `outlooks hash <id>` | a message's archive file stem |
| `outlooks hook [--apply]` | check or wire the capture hook |
| `outlooks config` | the effective settings |
| `outlooks skill`, `outlooks doc`, `outlooks install`, `outlooks permissions` | the skill, its documents, the stub, and its permission profile ([pkgskills](https://github.com/gitronald/pkgskills)) |

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
