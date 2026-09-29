# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

A repo that builds on this package depends on more than its commands. A
change to any of the following is listed under *Changed* or *Removed*, with
what a consuming repo has to do about it:

- a public Python name or one of its parameters (the README's "Python API");
- a settings key or its default. A default counts because a repo that leaves
  a key unset still has the default's path in its hook script and its ignore
  file;
- the archive's file format;
- a command's name or exit code;
- a line another tool is told to read: the `coverage` footer (`ledger
  high-water mark`, `coverage ends`, `next sweep from`), and `COVERED`,
  `INCOMPLETE`, and `DIFFERS` from `import`;
- a skill step's name;
- a key of a lookup's timeline file.

## [Unreleased]

### Added

- A declared Python API: `store`, `classify`, `detail`, and `config` each
  name what a consuming repo may import in `__all__`, listed in the README's
  "Python API" section with a reading example and an example of refining a
  `system` role in the repo's own wrapper. Every other module says in its
  docstring that it is internal. A contract test pins the names and each
  function's parameter names.
- `outlooks coverage --ledger <path>` reads a ledger kept somewhere other
  than `ledger.csv` in `archive_dir`.
- `outlooks lookup-reset <name>` moves a lookup's page and timeline files
  to `earlier/{timestamp}/` under `scratch_dir` and prints what it moved, so
  a repeat lookup of a name never merges the last one's pages. It deletes
  nothing, and the lookup skill runs it before launching searchers.
- `outlooks doc lookup/timeline` lists every key of a lookup's timeline
  file, which of them `check` reads, and which are required.
- Every step of the three modes has a fixed name, given where the step
  starts (the sweep's are `setup`, `sweep`, `classify`, `match`, `worklist`,
  `file`, `ledger`, and `report`). A repo's own skill or profile cites a step
  by name, which a renumbering does not change; `outlooks doc
  profile-template` says so. A test pins the names and their order per mode.

### Changed

- `outlooks coverage` ends with one more line, `next sweep from`, giving the
  instant the next sweep passes as `afterDateTime`, in UTC: the earlier of
  the ledger's high-water mark less five minutes and the end of recorded
  coverage. The sweep reads it, where it used to compute it. A tool that
  reads the footer should find each line by its label, not its position.
- `outlooks import` names the fields that differ on a `DIFFERS` line (`DIFFERS
  from the stored copy in body.content, attachments[].uri: <path>`) and no
  longer says `(--replace rewrites)`. A tool that matches the line should
  match on `DIFFERS from the stored copy` and take the path after the last
  `: `. The README says when `--replace` is the fix.
- `outlooks check` prints why a row is `BAD` on a line of its own under the
  row: `missing key date_local` (or `internet_message_id`), `not archived`,
  or the two dates that disagree. A timeline file with no
  `internet_message_id` used to stop the command with a traceback, and one
  with no `date_local` read as a date mismatch. The exit code is unchanged.

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
