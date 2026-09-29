"""The ``pkgskills`` host declaration: the skill and documents this package ships.

``pkgskills`` owns printing the bundled bodies (``outlooks skill``, ``outlooks
doc``), installing the version-stamped ``/outlooks`` dispatcher stub, and the
drift check. This module declares what it acts on, plus the one piece of
per-repo wiring it cannot see: the capture hook, reported by ``install
--check`` and never gated on.

Internal: not part of the Python API a consuming repo may import (the
README's "Python API" lists what is). The CLI is this module's interface.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import typer
from pkgskills import Doc, ExtraCheck, Host, InstallReport, Level, Mode, Skill

from outlooks import hook

MODES = ("lookup", "sweep", "window")

DESCRIPTION = (
    "Read an Outlook mailbox (a shared, delegated, or personal one) through the "
    "Microsoft 365 connector, and keep everything it returns in a committed "
    "archive. Three modes: look up the full correspondence with one or more "
    "people, both directions (lookup); find and classify what is new since the "
    "last run, then file what the operator approves (sweep); page every message "
    "in a date range into the archive and record it as covered, optionally "
    "reading all of it in full (window). Use whenever the user asks to check "
    'the inbox, the mailbox, or Outlook; asks "anything new in the inbox?", '
    '"sweep the mailbox", or to pull what came in from Outlook; '
    "asks to archive, backfill, or download the mail for a date range, or what "
    "the archive covers; asks what was sent to or received from someone, when a "
    "message went out, or whether someone was ever replied to."
)


def _after_install(report: InstallReport) -> None:
    """Say whether the capture hook is wired; ``outlooks hook --apply`` wires it."""
    now = hook.status(report.root, Path.cwd())
    if not now.ok:
        typer.echo(
            "note: the capture hook is not fully wired "
            f"(script {now.script}, settings {now.settings}); {now.advice}",
            err=True,
        )


def _extra_checks(host: Host, root: Path, mode: Mode | None) -> Sequence[ExtraCheck]:
    """Report (never gate on) the capture hook: the archive's only source."""
    now = hook.status(root, Path.cwd())
    return (
        ExtraCheck(
            label="capture hook",
            status="ok" if now.ok else f"script {now.script}, settings {now.settings}",
            gates=False,
            note=now.advice,
        ),
    )


HOST = Host(
    dist="outlooks",
    cli="outlooks",
    prompts="outlooks.prompts",
    artifacts=(
        Skill(
            name="outlooks",
            sources=tuple(f"skills/{mode}/SKILL.md" for mode in MODES),
            description=DESCRIPTION,
        ),
    ),
    docs=(
        Doc(name="connector", source="references/connector.md"),
        Doc(name="queries", source="references/queries.md"),
        Doc(name="profile-template", source="references/profile-template.md"),
        Doc(
            name="lookup/searcher-brief",
            source="skills/lookup/references/searcher-brief.md",
        ),
        Doc(
            name="lookup/reader-brief",
            source="skills/lookup/references/reader-brief.md",
        ),
        Doc(
            name="lookup/timeline",
            source="skills/lookup/references/timeline.md",
        ),
        Doc(
            name="window/pager-brief", source="skills/window/references/pager-brief.md"
        ),
        Doc(
            name="window/multi-pager", source="skills/window/references/multi-pager.md"
        ),
        Doc(
            name="window/bulk-reader-brief",
            source="skills/window/references/bulk-reader-brief.md",
        ),
    ),
    render_cli=True,
    # The skill reads this repo's archive and config, so a global stub would
    # point every repo at nothing.
    modes=("local",),
    after_install=_after_install,
    extra_checks=_extra_checks,
    permissions={Level.assist: ("Bash(uv run:*)",)},
)
