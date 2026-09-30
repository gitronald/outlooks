"""The PostToolUse capture hook: the archive's only source.

Every ``outlook_email_search`` and ``read_resource`` result is written verbatim
to the captures directory by a shell script Claude Code runs after the call;
``outlooks import`` files those captures. :func:`status` reports whether a repo
has the script and the ``.claude/settings.json`` entry that runs it, and
:func:`apply` writes whichever is missing, never touching other hooks.

Claude Code runs every hook whose matcher covers a call, from the project's
settings, its ``settings.local.json``, and the user's own settings alike, so a
second hook for the connector tools in any of them (an inline ``cat >`` from
before the script, say) captures each call twice. :func:`status` lists those as
``duplicates``; :func:`apply` never edits them: the local and user files are the
operator's, and another hook in the project file is left as every other is.

The script is written for the repo's ``captured_dir`` (:func:`script`), so the
hook writes where ``outlooks import`` reads; one written for another directory,
or by an earlier build, is ``stale``.

Internal: not part of the Python API a consuming repo may import (the
README's "Python API" lists what is). The CLI is this module's interface.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from outlooks import config

SCRIPT_PATH = Path(".claude/hooks/outlook-capture.sh")
SETTINGS_PATH = Path(".claude/settings.json")
LOCAL_PATH = Path(".claude/settings.local.json")
TOOLS = (
    "mcp__claude_ai_Microsoft_365__outlook_email_search",
    "mcp__claude_ai_Microsoft_365__read_resource",
)
MATCHER = "mcp__claude_ai_Microsoft_365__(read_resource|outlook_email_search)"
COMMAND = '"${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/outlook-capture.sh"'

SCRIPT = """#!/usr/bin/env bash
# Capture hook for the Microsoft 365 connector (PostToolUse on
# outlook_email_search and read_resource), written by `outlooks hook --apply`.
# Writes the hook payload verbatim to {YYYYMMDDTHHMMSS}-{pid}.json under
# $OUTLOOKS_CAPTURED_DIR, else under the captured_dir of [tool.outlooks] as it
# was when this script was written: the copy of the connector's output that
# `outlooks import` files. `outlooks hook` reports the script stale once
# captured_dir changes, and `outlooks hook --apply` rewrites it.
#
# A result too large for the context reaches the hook only as a note naming the
# file Claude Code saved it to, under ~/.claude/projects/, which the repo does
# not keep. Copy that file beside the capture as {same name}.saved.txt, so the
# capture never depends on it: without the copy, a capture imported after the
# file is gone has lost its output.
#
# Always exits 0: a capture problem must not fail the connector call.
d="${OUTLOOKS_CAPTURED_DIR:-}"
[ -n "$d" ] || d=@CAPTURED_DIR@
mkdir -p "$d"
out="$d/$(date +%Y%m%dT%H%M%S)-$$.json"
cat > "$out"

# Only a string tool_response is a spill note; a hit's summary quoting the
# phrase sits in a list of blocks and never matches.
saved=$(grep -oE \\
  '"tool_response": ?"[^"]*Output has been saved to [^ "\\\\]+\\.txt' "$out" |
  sed -E 's/.*Output has been saved to //')
# Claude Code saves those files under its projects directory; a note naming a
# file anywhere else is not one of its own, and nothing is copied for it. Both
# folders are compared as the filesystem resolves them, so neither a .. nor a
# link inside the projects directory leads out of it; a file that is itself a
# link is not one Claude Code saved.
root=$(cd "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects" 2>/dev/null && pwd -P)
if [ -n "$saved" ]; then
  name=$(basename "$saved")
  at=$(cd "$(dirname "$saved")" 2>/dev/null && pwd -P)
  saved=""
  if [ -n "$root" ] && [ -n "$at" ] && [ ! -L "$at/$name" ]; then
    case "$at/" in
      "$root"/*) saved="$at/$name" ;;
    esac
  fi
fi
if [ -n "$saved" ] && [ -f "$saved" ]; then
  cp "$saved" "${out%.json}.saved.txt"
fi
exit 0
"""


# The line of the script that names the captures directory.
DIR_LINE = '[ -n "$d" ] || d='

# Earlier builds' scripts, as the sha256 of their text without DIR_LINE: one
# of these carries no local change, so it is stale and safe to rewrite.
EARLIER = frozenset(
    {
        "b027c5446ec87859e89848145dff2de0572d24621e21558c3efa27e7b1aad9a2",
    }
)


def _rest(body: str) -> list[str]:
    """The script's lines, less the one naming the captures directory."""
    return [line for line in body.splitlines() if not line.startswith(DIR_LINE)]


def script(root: Path, start: Path | None = None) -> str:
    """The capture script for ``root``, writing to the repo's ``captured_dir``.

    The settings are those found from ``start`` (default: ``root``); a command
    passes its working directory, the one ``outlooks import`` reads them from.
    A directory inside ``root`` is named from ``CLAUDE_PROJECT_DIR``, so the
    script reads the same in every clone; one outside it is named in full.
    """
    folder = config.declared_path("captured_dir", start or root)
    try:
        inside = folder.relative_to(root.resolve())
    except ValueError:
        target = shlex.quote(str(folder))
    else:
        target = '"${CLAUDE_PROJECT_DIR:-.}"/' + shlex.quote(inside.as_posix())
    return SCRIPT.replace("@CAPTURED_DIR@", target)


def _script_state(text: str, root: Path, start: Path | None) -> str:
    want = script(root, start)
    if text == want:
        return "ok"
    rest = _rest(text)
    earlier = hashlib.sha256("\n".join(rest).encode()).hexdigest() in EARLIER
    return "stale" if rest == _rest(want) or earlier else "differs"


@dataclass(frozen=True)
class Duplicate:
    """Another PostToolUse hook that runs on a connector call."""

    file: str  # the settings file that holds it, as shown to the operator
    command: str
    entry: str  # its entry in ``file``'s ``hooks.PostToolUse``, as JSON
    whole: bool  # every hook in the entry is a duplicate: remove the entry

    @property
    def removal(self) -> str:
        """What to remove from ``file``, for the operator."""
        if self.whole:
            return f"remove {self.entry}"
        return f"remove the hook running `{self.command}` from {self.entry}"


@dataclass(frozen=True)
class Status:
    # "ok", "missing", "stale" (written for another captured_dir or by an
    # earlier build), or "differs" (carries a change this package never wrote)
    script: str
    settings: str  # "ok" or "missing"
    duplicates: tuple[Duplicate, ...] = ()

    @property
    def ok(self) -> bool:
        return self.script == "ok" and self.settings == "ok" and not self.duplicates

    @property
    def advice(self) -> str:
        """What to run to wire the hook, or "" when it is wired."""
        if self.script == "differs":
            wires = (
                "adds the missing settings entry but leaves it alone"
                if self.settings == "missing"
                else "leaves it alone"
            )
            return (
                "the script carries a change this package never wrote; "
                f"`uv run outlooks hook --apply` {wires}, and "
                "`uv run outlooks hook --apply --force` overwrites it"
            )
        if self.script == "ok" and self.settings == "ok":
            return DUPLICATE_ADVICE if self.duplicates else ""
        advice = "run `uv run outlooks hook --apply`"
        return f"{advice}; {DUPLICATE_ADVICE}" if self.duplicates else advice


DUPLICATE_ADVICE = (
    "remove each duplicate entry from its file by hand: every call is captured "
    "once per hook, and `outlooks hook --apply` does not edit it"
)


def _entries(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """The ``PostToolUse`` entries; ``ValueError`` when they are not a list of them."""
    hooks = settings.get("hooks")
    if hooks is None:
        return []
    if not isinstance(hooks, dict):
        raise ValueError("`hooks` is not an object")
    entries = hooks.get("PostToolUse")
    if entries is None:
        return []
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise ValueError("`hooks.PostToolUse` is not a list of objects")
    return entries


def _commands(entry: dict[str, Any]) -> list[str]:
    return [
        h.get("command") or ""
        for h in entry.get("hooks") or []
        if isinstance(h, dict) and h.get("type", "command") == "command"
    ]


def _wiring(settings: dict[str, Any]) -> list[str]:
    """The commands of the entries that run the capture script."""
    return [
        command
        for entry in _entries(settings)
        if entry.get("matcher") == MATCHER
        for command in _commands(entry)
        if "outlook-capture.sh" in command
    ]


def _wired(settings: dict[str, Any]) -> bool:
    return bool(_wiring(settings))


# Matchers that run their hooks on every tool: a hook under one is there for
# every call (a logger, a notifier), so it is a duplicate only when its command
# writes to the captures directory.
CATCH_ALL = (None, "", "*")


def _covers(matcher: Any) -> bool:
    """Whether a PostToolUse matcher runs its hooks on either connector tool.

    As Claude Code reads one: none, ``""``, or ``*`` matches every tool (see
    :data:`CATCH_ALL`), a matcher of plain names separated by ``|`` matches
    those names exactly, and any other is a regular expression searched for in
    the tool's name.
    """
    if matcher in CATCH_ALL:
        return True
    if not isinstance(matcher, str):
        return False
    if re.fullmatch(r"[\w|-]+", matcher):
        return any(tool in matcher.split("|") for tool in TOOLS)
    try:
        return any(re.search(matcher, tool) for tool in TOOLS)
    except re.error:
        return False


def user_settings() -> Path:
    """The user's own Claude Code settings, which apply in every repo."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude"
    return Path(base) / "settings.json"


def duplicates(root: Path) -> tuple[Duplicate, ...]:
    """Every other PostToolUse hook that runs on a connector call.

    The project, local, and user settings are read; a hook whose matcher names
    either connector tool, or a catch-all hook whose command names the captures
    directory, is a duplicate unless its command is one that wires the capture
    script in ``.claude/settings.json``. Claude Code runs an identical command
    once however many files list it, so that one is not.
    ``ValueError`` when a settings file does not parse.
    """
    wired = set(_wiring(_read_settings(root)))
    folder = config.captured_dir().name
    files = (
        (str(SETTINGS_PATH), root / SETTINGS_PATH),
        (str(LOCAL_PATH), root / LOCAL_PATH),
        (str(user_settings()), user_settings()),
    )
    found: list[Duplicate] = []
    for shown, path in files:
        try:
            entries = _entries(_read_file(path))
        except ValueError as e:
            raise ValueError(f"{shown}: {e}") from None
        for entry in entries:
            matcher = entry.get("matcher")
            if not _covers(matcher):
                continue
            commands = _commands(entry)
            extra = [
                c
                for c in commands
                if c not in wired and (matcher not in CATCH_ALL or folder in c)
            ]
            whole = len(extra) == len(entry.get("hooks") or [])
            found += [Duplicate(shown, c, json.dumps(entry), whole) for c in extra]
    return tuple(found)


def _read_settings(root: Path) -> dict[str, Any]:
    return _read_file(root / SETTINGS_PATH)


def _read_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    settings = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(settings, dict):
        raise ValueError("it is not an object")
    return settings


def script_state(root: Path, start: Path | None = None) -> str:
    """The state of ``root``'s capture script, as :class:`Status` names it."""
    path = root / SCRIPT_PATH
    if not path.is_file():
        return "missing"
    return _script_state(path.read_text(encoding="utf-8"), root, start)


def settings_state(root: Path) -> str:
    """The state of ``root``'s settings entry, as :class:`Status` names it.

    ``ValueError`` when the settings file does not parse, or holds something
    other than hook entries where they belong.
    """
    return "ok" if _wired(_read_settings(root)) else "missing"


def status(root: Path, start: Path | None = None) -> Status:
    """Whether ``root`` has the capture script, the settings entry, and no other."""
    return Status(script_state(root, start), settings_state(root), duplicates(root))


def apply(root: Path, *, force: bool = False, start: Path | None = None) -> list[str]:
    """Write the script and the settings entry where missing; list what changed.

    A stale script, this build's but for the directory it names or an
    earlier build's, is rewritten. One that differs otherwise is left alone
    unless ``force``: it may carry a local change, and a capture hook that
    silently changes is how an archive loses its source.
    """
    done: list[str] = []
    # Not status(): the duplicates are only reported, so a settings file this
    # never writes does not stop it.
    now = Status(script_state(root, start), settings_state(root))
    path = root / SCRIPT_PATH
    if now.script in ("missing", "stale") or (now.script == "differs" and force):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(script(root, start), encoding="utf-8")
        os.chmod(path, 0o755)
        done.append(f"wrote {SCRIPT_PATH}")
    if now.settings == "missing":
        settings = _read_settings(root)
        hooks = settings.get("hooks") or {}
        entries = hooks.get("PostToolUse") or []
        entries.append(
            {"matcher": MATCHER, "hooks": [{"type": "command", "command": COMMAND}]}
        )
        hooks["PostToolUse"] = entries
        settings["hooks"] = hooks
        target = root / SETTINGS_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        done.append(f"added the PostToolUse entry to {SETTINGS_PATH}")
    return done
