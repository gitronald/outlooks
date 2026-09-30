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

## [0.4.0] - 2026-09-29

### Added

- `outlooks import` imports one capture per call (the same `tool_use_id`),
  the first that kept its output, and prints how many it skipped
  (`N DUPLICATE captures of a call already captured`), so a call captured
  by two hooks is filed, counted, and reported once.

### Changed

- `outlooks hook` and `outlooks doctor` read `.claude/settings.local.json`
  and the user's own `settings.json` as well as `.claude/settings.json`,
  and report any other PostToolUse hook whose matcher names
  `outlook_email_search` or `read_resource` (or a catch-all hook whose
  command names the captures directory) as `duplicate`, naming its file:
  Claude Code runs every matching hook, so a leftover inline capture hook
  captures each call twice. Either command exits 1 on one, as for
  `differs`, and `outlooks hook` exits 1 with a message, not a traceback,
  on a settings file that does not parse. A repo whose CI
  runs `outlooks doctor` removes the entry the command prints;
  `outlooks hook --apply` does not edit those files.

## [0.3.0] - 2026-09-29

### Added

- `classify.is_auto_reply(subject)` says whether a subject opens as an
  automatic reply's does. It is the test behind a `followup`'s
  `extra["auto"]`, for a repo that has only the subject in hand. A repo
  that keeps a copy of the package's internal pattern can drop the copy.
- A `notice_senders` setting (`OUTLOOKS_NOTICE_SENDERS`), empty by default:
  automated senders that write to us, such as a mail system's bounce
  address. Mail from one is `direction: in` whoever it is addressed to,
  where mail from one of `system_senders` is `out` unless it is addressed
  to us. `classify` gives it the `system` role with `extra["notice"]` true,
  `outlooks senders` marks its addresses `notice`, and a lookup pairs
  nothing with it. An address both lists match is a notice sender, in a
  lookup's pairing as everywhere else.
- An entry of `system_senders` or `notice_senders` may be a pattern in
  which `*` stands for any run of characters (`postmaster@*`,
  `*@bounces.example.net`), for a sender that writes from many addresses.
  An entry without `*` matches as before.
- `config.is_system_sender(address)` and `config.is_notice_sender(address)`
  say whether an address is a sender of each kind, patterns included. An
  address is of one kind at most: one of `own_addresses` is neither, and
  one both lists match is a notice sender only.

### Changed

- `config.Settings` has one more field, `notice_senders`, after
  `system_senders`, and `config.KEYS` one more key in the same place. A
  repo that builds a `Settings` itself passes the new field. A repo whose
  test fixture pins every setting pins `OUTLOOKS_NOTICE_SENDERS` too (the
  README's fixture does).
- A repo that tests `address in settings().system_senders` keeps working
  while every entry is an address. Once an entry is a pattern it calls
  `config.is_system_sender(address)`.
- Mail from one of `own_addresses` is ours even when `system_senders` lists
  the address too. `classify` gave it the `system` role, where a lookup's
  `direction` and `outlooks senders` already read it as ours, and now gives
  it `ours` or `decision`. A lookup's timeline key `system` is false for
  it, and was true. A repo that lists one of its own addresses in
  `system_senders` computes its stored roles again.
- A lookup's timeline key `system` is true for a sender in either list. It
  was true for one of `system_senders` only, and is unchanged for a repo
  that sets no `notice_senders`.
- `outlooks senders` marks the hits that have no sender `none` and leaves
  them out of the unlisted count. They were counted as one unlisted
  address, which no setting could list. Its closing line now names both
  lists: `system_senders` for a notifier that speaks for us, and
  `notice_senders` for one that only writes to us.
- The reply test and the automatic-reply test read one list of phrases, so
  a subject that reads as automatic always reads as a reply. `Auto-reply`,
  `Auto reply`, `Autoreply`, `Auto-response`, and `Out of the office` are
  recognized beside `Automatic reply`, `Automatic response`, and `Out of
  office`. A colon ends the phrase, as does a run of dashes with a space
  after it (`Auto Reply - ...`), or the end of the subject. Words after the
  bare phrase do not: `Out of office coverage schedule` is a person's
  subject and stays an `arrival`. A message with one of the newly
  recognized subjects was an `arrival` and is now a `followup` with
  `extra["auto"]` true, `is_reply` is true of its subject, and
  `thread_subject` drops the phrase. An `[External]` tag ahead of the phrase
  no longer hides it: `extra["auto"]` was false there and is now true. A
  repo that stored roles or thread subjects computed by an earlier release
  computes them again.
- `outlooks doctor` names the release that stamped the skill stub when it
  is not the installed one: `content current for outlooks X.Y.Z, stamped by
  W.V.U`. It said `current for outlooks X.Y.Z` of a stub whose header named
  an earlier release. The check still passes, because the stub's content is
  what decides it, and `outlooks install` restamps the stub. Nothing to do
  for a repo that follows the upgrade recipe.

## [0.2.0] - 2026-09-29

### Added

- A declared Python API: `store`, `classify`, `detail`, and `config` each
  name what a consuming repo may import in `__all__`, listed in the README's
  "Python API" section with a reading example and an example of refining a
  `system` role in the repo's own wrapper. Every other module says in its
  docstring that it is internal. A contract test pins the names, and each
  function's parameter names, which of them have a default, and how each
  may be passed.
- `outlooks coverage --ledger <path>` reads a ledger kept somewhere other
  than `ledger.csv` in `archive_dir`. A path that is not a file is an error
  (exit 1), never an empty ledger.
- `outlooks lookup-reset <name>` moves a lookup's page and timeline files
  to `earlier/{timestamp}/` under `scratch_dir` and prints what it moved, so
  a repeat lookup of a name never merges the last one's pages. It deletes
  nothing, and the lookup skill runs it before launching searchers.
- `outlooks doc lookup/timeline` lists every key of a lookup's timeline
  file, which of them `check` reads, and which are required.
- `outlooks doctor` checks in one read-only pass that a repo is wired
  correctly, one line per check, and exits 1 if any fails: the settings
  (the table is there, every key is a setting, and each parses), the
  profile (the file exists when `profile` is set), the archive
  (`coverage.csv` and every hits file parse), the hook script, the hook's
  settings entry, and the skill stub. It makes no connector call, so a
  consuming repo's CI can run it.
- `outlooks senders [--since YYYY-MM-DD]` counts the archived inbound hits
  by sender address, most frequent first, and marks each `own`, `system`, or
  `unlisted`, so a notifier missing from `system_senders` is visible.
  Read-only. The profile template's *Who we are* points at it.
- Every step of the three modes has a fixed name, given where the step
  starts (the sweep's are `setup`, `sweep`, `classify`, `match`, `worklist`,
  `file`, `ledger`, and `report`). A repo's own skill or profile cites a step
  by name, which a renumbering does not change; `outlooks doc
  profile-template` says so. A test pins the names and their order per mode.
- The README gives one install recipe for a repo that only runs the
  commands and one for a repo that imports the package, an upgrade recipe
  that ends in `outlooks doctor`, a CI example, how to run any command
  against a copy of the archive, and the fixture a consuming repo's tests
  pin the settings with.

### Changed

- `outlooks coverage` ends with one more line, `next sweep from`, giving the
  instant the next sweep passes as `afterDateTime`, in UTC: the earlier of
  the ledger's high-water mark less five minutes and the end of recorded
  coverage. The sweep reads it, where it used to compute it. With an empty
  ledger the line is the end of recorded coverage, and the sweep still asks
  the operator where to start, because the archived mail before that instant
  has never been classified. A tool that reads the footer should find each
  line by its label, not its position.
- `outlooks import` names the fields that differ on a `DIFFERS` line (`DIFFERS
  from the stored copy in body.content, attachments[].uri: <path>`) and no
  longer says `(--replace rewrites)`. A tool that matches the line should
  match on `DIFFERS from the stored copy` and take the path after the last
  `: `. The line names no field, `DIFFERS from the stored copy: <path>`,
  when the stored copy is not JSON. The README says when `--replace` is the
  fix.
- `outlooks check` prints why a row is `BAD` on a line of its own under the
  row: `missing key date_local` (or `internet_message_id`), `not archived`,
  or the two dates that disagree. A timeline file with no
  `internet_message_id` used to stop the command with a traceback, and one
  with no `date_local` read as a date mismatch. The exit code is unchanged.

### Fixed

- `outlooks split <name>` no longer merges the page files of another lookup
  whose name starts with `<name>-` (`smith` and `smith-jones`). A page file
  is the name, one of the searchers' kinds (`sender`, `title`, `sent`) or
  none, and `page-{n}.json`.

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
