# Repo profile template

The `outlooks` skill is the same in every repo; the profile is how a repo tunes
it without forking it. A profile is repo-owned prose (usually under `docs/`),
named by `profile` in `[tool.outlooks]` so `{cli} config` prints its path.
Every mode reads it at its "Repo profile" step, before classifying,
resolving names, cross-checking, or filing.

When the profile, or a skill of the repo's own, refers to a step of this
skill, cite the step by its name, not its number: "run the sweep's `sweep`
step, then its `classify` and `match` steps". Every step's name is given
where the step starts, as step `name`, and it stays the same when steps are
added or renumbered. A number does not.

Write it against the seven headings below, in this order. Each heading lists
what the profile must answer. Say "none" where a heading does not apply
(a repo that never files anything has no filing step), so an agent can tell
a deliberate absence from a gap. Keep real names, addresses, and precedents
here rather than in the skill: the profile is where a repo's own history
belongs.

## 1. Who we are

- The mailbox (the same value as `mailbox` in `{cli} config`), and whose it
  is: a shared, delegated, or personal mailbox.
- The own addresses, and why each counts as *us* (an old address still
  receiving replies, a sibling mailbox the team sends from).
- The system senders and what each one sends (a web form, a
  ticketing system, a billing service). For each, the notification types
  it sends, what can be parsed from each (a title, an id, a person's name
  and address), and which of them identify a party who must stay
  confidential (`{cli} doc queries`, B3, shows the shape).
- The time zone every date is reported in.
- Who the operator is, in the repo's own words, and any mail that must
  never be filed or rendered anywhere shared (a party whose
  involvement is confidential).

## 2. Registries

- Where a name or address resolves: each file or table, the column that
  holds the address, and what a match means (an existing person, an
  existing matter, a past role).
- The command or procedure that resolves one, if the repo has one.
- Which registry wins when two disagree, and the known stale spots.

## 3. Sweep classes

- The class table: each `kind`, the rule that assigns it, and its default
  outcome (`candidate` or `skipped`). The package's generic shape has a
  system-sender class, an own-mail class, a reply class, and the inbound
  first-message classes; name them as the repo does.
- Whether a reply, forward, or auto-reply is skipped (the default) or
  classified by content like any other inbound message.
- The new-sender rule, if any: which registries a sender must be absent
  from, and what a flagged message becomes.
- What the matching step checks for each candidate: the fields to propose
  (destination, name, type), how to pick each, and any required questions
  the message must answer (link the repo's checklist).

## 4. Ledger columns

- The classification column's name (default `kind`) and the word for each
  of the three outcomes (defaults `filed`, `skipped`, and `deferred`), when
  the ledger already uses its own.
- The columns beyond the core three (`internet_message_id`, `received`,
  `outcome`), in order, with what goes in each for a filed, skipped, and
  deferred row.
- The ledger's path, if it is not `ledger.csv` in `archive_dir`.

## 5. Filing an approved candidate

- The skill or procedure that files one (by name and section), or "none:
  the operator acts by hand".
- The order of its steps, what each refuses to repeat, and when the ledger
  row is written relative to them.
- What has to be rebuilt or refreshed after the last candidate is filed,
  and what gets committed with the ledger and the archive.

## 6. Cross-checks

- What the repo's own records assert that the mail can confirm or
  contradict (arrival dates, reply dates, decisions, addresses, titles).
- For each recurring disagreement: which source wins, the ruling the
  operator has already made, the precedent that set it, and the one-line
  command that applies the correction (for the operator to run or approve).
- Whether the mailbox sends bulk mail (a mail-merge, an announcement), and
  who adds its rows to `mass-sends.csv` in `archive_dir`, so a full read of
  a range reads one copy of each (`{cli} skill window`).
- Where the repo keeps per-record mailbox history, if it does: the file, its
  columns and vocabulary, and the rules for a row (the lookup's `keep` step
  offers rows in this shape).

## 7. Transcription

- Whether `{cli} render` is used, and for which kinds.
- The file naming (`{Surname} - {Stage} - {Label}` or the repo's own), the
  `--stage` and `--label` values per kind, and where the file goes. A label
  used for most files belongs in `render_label` in `[tool.outlooks]`, so a
  forgotten `--label` still files under the repo's naming.
- How an original attachment the operator drags in by hand is named.
