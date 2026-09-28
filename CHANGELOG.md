# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-09-27

### Added

- The mailbox archive: the two-tier, write-once store (`store`), the capture
  import (`capture`), window coverage (`coverage`), window sizing and the
  item-id proof (`sizing`), the backfill planner (`window_plan`), message
  classification (`classify`), the message detail view (`detail`), and the
  `.docx` transcription (`render`).
- `[tool.outlooks]` configuration with `OUTLOOKS_*` overrides, printed by
  `outlooks config`: `mailbox`, `own_addresses`, `system_senders`,
  `decision_tag`, `archive_dir`, `captured_dir`, `scratch_dir`, `timezone`,
  `render_label` (the label `render` gives a file when `--label` is not
  passed, default `Message`), and `profile`.
- The signed-in account's own mailbox: with `mailbox` unset the archive files
  under the first of `own_addresses`, and `import` takes the captures that
  name no mailbox or name any own address. Its pulls report no total, so a
  window is covered once its last page is captured.
- The skill helpers as commands: `split`, `check`, `worklist`, `totals`,
  `audit`, `windows`, and `hash`; `hook` writes and checks the capture hook,
  whose script writes to the repo's `captured_dir`.
- The `outlooks` skill (lookup, sweep, window) and its reference documents,
  installed as a version-stamped stub through pkgskills. The sweep takes the
  ledger's classification column name and outcome words from the profile
  when it gives them, and skipping replies is a default the profile can
  override.
- Message roles from `classify`: `arrival`, `followup`, `ours`, `decision`
  (our mail with the `decision_tag` in its subject, reported with the label
  after the tag), and `system` (mail from one of `system_senders`).
- Mass sends: a hand-owned `mass-sends.csv` beside the archive (`sender`,
  `subject`, `after`, `before`, `exemplar`) lists the bulk sends whose copies
  differ only in the recipient. `worklist` reads the exemplar and skips the
  other copies; `coverage` counts them on their own line rather than as
  covered mail with no full read.
- `worklist` refuses, writing nothing, while the newest capture was written
  under 90 seconds ago, so a reader still running never has its batch files
  replaced.
- `outlooks hook` reads a script written by a pre-release build as `stale`,
  so `--apply` rewrites it; for a script with a local change it says that
  `--apply --force` overwrites it.

### Security

- The capture hook and `outlooks import` follow the saved-file path in an
  oversized-result note only into Claude Code's projects directory
  (`~/.claude/projects/`, or under `CLAUDE_CONFIG_DIR`), comparing folders as
  the filesystem resolves them and refusing a file that is itself a link, so
  neither a `..` nor a link leads out of it. A test or tool that feeds the
  hook a spill note must put the saved file, as a regular file, under that
  directory; anywhere else, nothing is copied.
