---
id: 0
slug: consumer-interface-contract
status: active
branch: feature/consumer-interface-contract
created: 2026-09-29T01:49:08-07:00
concluded:
pr:
---

# Declare and harden the interface a consuming repo builds on

## Plan

### Goal

A repo that adopts this package builds on more than the commands in the
README. It imports modules, links the skill's steps from its own skills,
copies default settings into its own table, pins a release tag, and runs the
status commands by hand after every upgrade. None of that is declared today,
so none of it is safe to build on: a rename, a renumbered step, a changed
default, or a moved tag breaks the repo with no warning and no changelog
line.

This plan declares that interface, tests it, and closes the places where a
consuming repo currently has to do by hand what the package already knows.
The package stays generic. Nothing here teaches it what a particular repo's
mail means.

### Two shapes of consuming repo

Every item below is judged against both.

| Shape | Uses | Depends on |
|---|---|---|
| **Operator only** | the CLI, the skill, and the capture hook, from an interactive session | command names, output lines, exit codes, settings keys and defaults, the skill's steps |
| **Importing** | all of the above, plus `import outlooks` in its own code and tests | the names and signatures of the modules it imports, and the archive's file format |

### Scope

#### A. Declare the Python API

1. Give `store`, `classify`, `detail`, and `config` an `__all__` that names
   what a consuming repo may import. The starting list is what reading an
   archive and refining a role needs:

   | Module | Public names |
   |---|---|
   | `outlooks.store` | `archive_root`, `id_hash`, `stable`, `load_hits`, `load_messages`, `newest_hit`, `StoreError` |
   | `outlooks.classify` | `Mail`, `Facts`, `view`, `classify`, `is_reply`, `thread_subject` |
   | `outlooks.detail` | `detail`, `details_by_id`, `body_text` |
   | `outlooks.config` | `Settings`, `load`, `settings`, `KEYS`, `DEFAULTS`, `ConfigError`, and the path and zone helpers |

   Settle the list by reading each module, not by copying this table. The
   write paths (`save_*`, `replace_*`) stay out: a consuming repo fills the
   archive through `outlooks import`, never by calling the store.
2. Every other module (`capture`, `coverage`, `sizing`, `window_plan`,
   `lookup`, `worklist`, `render`, `hook`, `host`, `cli`) is internal, and
   says so in its docstring. The CLI is their interface.
3. A contract test imports each public name and pins each public function's
   parameter names, so a rename fails a test before it reaches a release.
4. A "Python API" section in the README: the table above, one reading
   example, and one refining example (item B).

#### B. Show how a repo refines a role without the package learning its domain

`classify` reports the role a message plays (`arrival`, `followup`, `ours`,
`decision`, `system`) and stops there. What a system sender's notification
says is the repo's own business, and the right place for it is a thin wrapper
in the repo. The README example shows that shape with an invented system:

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

The example is the whole deliverable. The package gains no parser, no
registry of notification types, and no plug-in mechanism: a wrapper in the
repo is simpler than any hook the package could offer, and it keeps the
repo's vocabulary out of the package. Check that `Mail` exposes what such a
wrapper needs (subject, body text, sender, recipients) under public names,
and add what is missing.

#### C. One command that says the repo is wired correctly

After an upgrade a repo runs `outlooks config`, `outlooks hook`, and
`outlooks install --check` one by one, and its CI runs none of them. Add
`outlooks doctor`: read-only, no connector, one line per check, exit 1 if any
fails.

| Check | Fails when |
|---|---|
| settings | the table is missing or a key does not parse |
| profile | `profile` is set and the file does not exist |
| archive | `archive_dir` exists and `coverage.csv` or a hits file does not parse |
| hook script | `missing`, `stale`, or `differs` |
| hook settings entry | missing or pointing elsewhere |
| skill stub | not current for the installed version |

It reuses the functions behind the three commands, so there is one
definition of `ok`. The README's upgrade recipe ends with it, and its CI
example runs it.

#### D. Give the skill's steps names a repo can link

A repo's own skill says "run the sweep's step 1, then its steps 2 and 3". A
renumbering silently changes what that sentence means.

1. Each numbered step in the three modes keeps its number and gains a fixed
   name in its heading, which is what another skill should cite (for the
   sweep: `sweep`, `classify`, `match`, `worklist`, `file`, `ledger`,
   `report`).
2. A test pins the ordered list of step names per mode, so adding,
   removing, or reordering a step is a deliberate edit to the test.
3. `outlooks doc profile-template` tells a repo to cite steps by name.

#### E. Print the next sweep's start instead of leaving the arithmetic to the session

`outlooks coverage` prints the ledger's high-water mark and the end of
coverage in local time. The sweep then needs a UTC instant, five minutes
before the earlier of the two. That is a time zone conversion and a
subtraction done by a model, in the one place where a wrong value silently
skips mail.

1. `coverage` prints one more line, the instant to pass as `afterDateTime`:
   `next sweep from  2026-01-05T17:55:00Z`. It is the earlier of the ledger
   mark less the margin and the coverage end. With no ledger rows it prints
   the coverage end, and with neither it prints nothing.
2. The margin is one constant, used by `coverage` and named in the sweep's
   text.
3. The sweep's step 1 reads the line instead of computing it.

#### F. Say what differs when a capture disagrees with the stored copy

`import` prints `DIFFERS from the stored copy (--replace rewrites)` and the
path. Whether `--replace` is safe depends on which copy is right, and the
operator has to diff two JSON files by hand to find out.

1. Each `DIFFERS` line names the fields that differ, as dotted paths (for
   example `attachments[].uri`, `body.content`), capped at a handful.
2. The line stops advertising `--replace` as the fix. The README says when
   it is one (the stored copy was not written from a capture) and when it
   is not (the capture is the older of the two), and that `import <paths>
   --replace` limits the rewrite to the captures named.

#### G. Document the timeline file, and fail on a missing key by name

A timeline file is scratch, but a lookup resumed from an earlier run reads
it, and `check` decides `ok` from its keys.

1. The lookup skill's reference lists every key of a timeline file, which
   ones `check` reads, and which are required.
2. `check` reports a required key that is absent as its own finding
   (`missing key date_local`), not as a date mismatch, so a file written by
   another build is recognizable as such.
3. A change to a key `check` reads is a changelog entry under *Changed*.

#### H. A repeat lookup must not mix with the last one

Searchers write `{name}-*page-{n}.json` into the scratch directory, and
`split` merges every page file for that name. A second lookup of the same
name overwrites the pages it rewrites and inherits the ones it does not.

Add `outlooks lookup-reset <name>` (final name settled at implementation),
which moves that name's page and timeline files into
`{scratch_dir}/earlier/{timestamp}/` and prints what it moved. It deletes
nothing. The lookup skill runs it before launching searchers.

#### I. Report senders, so an unlisted system sender is visible

A notifier that is not in `system_senders` is classified as a person
writing in: its mail becomes an `arrival`, and its address looks like a
correspondent's. Nothing reports this, because nothing is wrong with any
single message.

Add `outlooks senders [--since YYYY-MM-DD]`: inbound hits counted by sender
address, most frequent first, each marked `own`, `system`, or unlisted.
Read-only over the hits tier. The profile template's *Who we are* heading
points at it as the way to find system senders in the first place.

#### J. README: install, upgrade, and trying it safely

1. **Install.** Replace the current advice, which assumes CI cannot clone
   the repository. Two recipes, one per shape: operator only (its own
   dependency group, so the consuming package cannot import it by
   accident), and importing (a plain dependency). Both pin a tag.
2. **Upgrade.** Bump the tag, `uv lock --upgrade-package outlooks`,
   `outlooks hook --apply`, `outlooks install`, `outlooks doctor`. Say that
   the lock file's commit hash is the pin that holds.
3. **Trying it without touching the archive.** `OUTLOOKS_ARCHIVE_DIR` and
   `OUTLOOKS_SCRATCH_DIR` pointed at a scratch copy run any command against
   the copy. One paragraph and an example.
4. **Testing against it.** A consuming repo's tests pin the settings
   through `OUTLOOKS_*` in a fixture, so they never depend on the repo's
   own table.

#### K. Release discipline

1. **A published tag never moves.** A fix ships as the next version, under
   its own changelog heading. Stated in the README's development section.
2. **What counts as a change a consumer must hear about**, listed under
   *Changed* or *Removed* with what to do: a public Python name or
   parameter, a settings key or its default, the archive's file format, a
   command's name or exit code, a line another tool is told to read (the
   `coverage` footer, `COVERED`, `INCOMPLETE`, `DIFFERS`), a skill step's
   name, and a timeline key. Defaults are on the list because a repo that
   leaves a key unset still has paths baked into its hook script and its
   ignore file.
3. **Publish to PyPI.** A git source cannot be resolved on a network that
   allows only the package index, and cannot be pinned by version range.
   The publish workflow is already in the repo, disabled. The index side is
   ready (see the Log): the name is reserved and a trusted publisher is
   registered for this repository. What is left is on this side: set
   `PUBLISH_ENABLED`, and confirm the workflow file name and the `pypi`
   environment match what the publisher was registered with. The README's
   recipes then name the index first and the git source second.

### Out of scope

Considered and left to the consuming repo, because the package cannot do
them without learning a domain:

- Parsing what a system sender's notifications say, and any vocabulary for
  it (item B shows the wrapper instead).
- Mapping a decision's label onto a repo's outcome words.
- Resolving a name or address against a repo's registries.
- Checking a repo's records against the mail.
- A `render` naming scheme beyond `render_label`, `--stage`, and `--label`.

Also out:

- Any list of names the package had before its first release. The
  changelog starts at 0.1.0.
- Changing the archive's format.
- A plug-in or entry-point mechanism for classification.

### Key decisions

- **Declare less, not more.** A name is public only if reading the archive
  or refining a role needs it. Widening the list later is cheap, and
  narrowing it is a breaking change.
- **New commands are read-only**, except `lookup-reset`, which moves
  scratch files and never deletes.
- **Lines that tools read are part of the interface.** Items E and F change
  such lines, so both land in one release with a *Changed* entry each.
- **Examples are invented.** Every address is `example.org`, every system
  is a generic one (a ticketing system, a web form), and no example is
  taken from a real mailbox or a real repo.

### Implementation order

Each step is one commit or a small group, with its tests and its changelog
line.

1. A, then B: `__all__`, the internal-module docstrings, the contract test,
   and the README's API section.
2. K.2: the changelog policy, so the following steps have somewhere to be
   recorded.
3. E and F: the `coverage` line and the `DIFFERS` detail, with the sweep
   text.
4. G and H: the timeline reference, the missing-key finding, and
   `lookup-reset`.
5. D: step names and the pinning test.
6. I: `senders`.
7. C: `doctor`, last of the commands because it checks what the others
   settle.
8. J and K.1: the README.
9. Gate: `ruff check`, `ruff format --check`, `pyrefly check`, `pytest`,
   and a scan of the tree for anything that is not an invented example.
10. K.3: set `PUBLISH_ENABLED` before the release that carries this plan,
    so its tag is the first upload. The index side is ready.

## Log

### 2026-09-29: the index side of publishing is ready

Logged 2026-09-29T01:58:07-07:00. The maintainer reserved the name `outlooks`
on PyPI and registered this repository as its trusted publisher, which was
the part of K.3 that could not be done from the repo.

Checked from the repo, read-only:

| Check | Result |
|---|---|
| Repository variables | none set, so `PUBLISH_ENABLED` is still off and a tag push publishes nothing |
| Repository environments | none; the workflow's `pypi` environment is created on its first run |
| The project's page on the index | not found, as expected before a first upload |

Not verified: that the publisher was registered with the workflow file name
`publish.yml` and the environment name `pypi`. A mismatch in either fails
the first upload at the token exchange, not before.

Nothing was enabled. K.3 and step 10 of the implementation order now say
what is left.
