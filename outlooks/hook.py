"""The PostToolUse capture hook: the archive's only source.

Every ``outlook_email_search`` and ``read_resource`` result is written verbatim
to the captures directory by a shell script Claude Code runs after the call;
``outlooks import`` files those captures. :func:`status` reports whether a repo
has the script and the ``.claude/settings.json`` entry that runs it, and
:func:`apply` writes whichever is missing, never touching other hooks.

The script is written for the repo's ``captured_dir`` (:func:`script`), so the
hook writes where ``outlooks import`` reads; one written for another directory,
or by an earlier build, is ``stale``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from outlooks import config

SCRIPT_PATH = Path(".claude/hooks/outlook-capture.sh")
SETTINGS_PATH = Path(".claude/settings.json")
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
class Status:
    # "ok", "missing", "stale" (written for another captured_dir or by an
    # earlier build), or "differs" (carries a change this package never wrote)
    script: str
    settings: str  # "ok" or "missing"

    @property
    def ok(self) -> bool:
        return self.script == "ok" and self.settings == "ok"

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
        return "" if self.ok else "run `uv run outlooks hook --apply`"


def _entries(settings: dict[str, Any]) -> list[dict[str, Any]]:
    return (settings.get("hooks") or {}).get("PostToolUse") or []


def _wired(settings: dict[str, Any]) -> bool:
    return any(
        entry.get("matcher") == MATCHER
        and any(
            "outlook-capture.sh" in (h.get("command") or "")
            for h in entry.get("hooks", [])
        )
        for entry in _entries(settings)
    )


def _read_settings(root: Path) -> dict[str, Any]:
    path = root / SETTINGS_PATH
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def status(root: Path, start: Path | None = None) -> Status:
    """Whether ``root`` has the capture script and the settings entry."""
    path = root / SCRIPT_PATH
    if not path.is_file():
        state = "missing"
    else:
        state = _script_state(path.read_text(encoding="utf-8"), root, start)
    return Status(state, "ok" if _wired(_read_settings(root)) else "missing")


def apply(root: Path, *, force: bool = False, start: Path | None = None) -> list[str]:
    """Write the script and the settings entry where missing; list what changed.

    A stale script, this build's but for the directory it names or an
    earlier build's, is rewritten. One that differs otherwise is left alone
    unless ``force``: it may carry a local change, and a capture hook that
    silently changes is how an archive loses its source.
    """
    done: list[str] = []
    now = status(root, start)
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
