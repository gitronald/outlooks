"""Whether a repo is wired correctly: one read-only pass over what an upgrade touches.

After an upgrade a repo checks its settings (``outlooks config``), its capture
hook (``outlooks hook``), and its skill stub (``outlooks install --check``).
:func:`run` makes the same checks in one pass, and adds the two a command
cannot show: that the profile the settings name exists, and that the archive
still parses. It calls the functions behind those commands, so there is one
definition of ``ok``. It writes nothing and makes no connector call.

Internal: not part of the Python API a consuming repo may import (the
README's "Python API" lists what is). The CLI is this module's interface.
"""

from __future__ import annotations

import csv
import json
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

import pkgskills
from pkgskills.stamp import PREFIX

from outlooks import config, coverage, hook
from outlooks.host import HOST

# The checks, in the order they are printed.
CHECKS = (
    "settings",
    "profile",
    "archive",
    "hook script",
    "hook settings entry",
    "skill stub",
)

# The version a stub's stamp line names: the release that wrote the stub.
STAMPED = re.compile(rf"(?m)^{re.escape(PREFIX)}{re.escape(HOST.dist)} (\S+) ")


@dataclass(frozen=True)
class Result:
    check: str
    ok: bool
    note: str


class _Failed(Exception):
    """A check that did not pass; the message is its note."""


def _settings(start: Path) -> str:
    try:
        found = config.load(start)
        unknown = config.unknown_keys(start)
    except (config.ConfigError, tomllib.TOMLDecodeError, ValueError) as e:
        raise _Failed(str(e)) from None
    except ZoneInfoNotFoundError as e:
        raise _Failed(f"timezone does not name a zone: {e}") from None
    if found.source is None:
        raise _Failed("no pyproject.toml here or above holds a [tool.outlooks] table")
    if unknown:
        raise _Failed(f"{found.source}: not a setting: {', '.join(unknown)}")
    if not (found.mailbox or found.own_addresses):
        raise _Failed(f"{found.source}: neither mailbox nor own_addresses is set")
    return str(found.source)


def _profile(start: Path) -> str:
    profile = config.load(start).profile
    if profile is None:
        return "(unset)"
    if not profile.is_file():
        raise _Failed(f"{profile} does not exist")
    return str(profile)


def _archive(start: Path) -> str:
    root = config.load(start).archive_dir
    if not root.exists():
        return f"{root} (not written yet)"
    rows = 0
    table = coverage.coverage_csv(root)
    if table.exists():
        try:
            with table.open(newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames != coverage.FIELDS:
                    raise ValueError(f"its columns are {reader.fieldnames}")
                for row in reader:
                    rows += 1
                    if None in row or None in row.values():
                        cells = len(coverage.FIELDS)
                        raise ValueError(f"it does not have the header's {cells} cells")
                    coverage.parse_ts(row["after"])
                    coverage.parse_ts(row["before"])
                    int(row["total"])
                    int(row["messages"])
        except (ValueError, TypeError, KeyError, OSError) as e:
            raise _Failed(f"{table}, row {rows}: {e}") from None
    files = sorted((root / "hits").glob("*/*.jsonl"))
    for path in files:
        n = 0
        try:
            for n, line in enumerate(path.read_text("utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                hit = json.loads(line)
                if not isinstance(hit, dict):
                    raise ValueError("not an object")
                if not isinstance(hit["receivedDateTime"], str):
                    raise ValueError("receivedDateTime is not a string")
                coverage.parse_ts(hit["receivedDateTime"])
                if not hit["internetMessageId"]:
                    raise ValueError("an empty internetMessageId")
        except (ValueError, TypeError, KeyError, OSError) as e:
            what = f"no {e}" if isinstance(e, KeyError) else str(e)
            raise _Failed(f"{path}, line {n}: {what}") from None
    return f"{root}: {len(files)} hits files, {rows} coverage rows"


def _hook_script(root: Path, start: Path) -> str:
    state = hook.script_state(root, start)
    if state != "ok":
        advice = hook.Status(state, "ok").advice
        raise _Failed(f"{state}: {hook.SCRIPT_PATH}; {advice}")
    return str(hook.SCRIPT_PATH)


def _hook_settings(root: Path) -> str:
    try:
        state = hook.settings_state(root)
    except ValueError as e:
        raise _Failed(f"{hook.SETTINGS_PATH} does not parse: {e}") from None
    if state != "ok":
        raise _Failed(
            f"{hook.SETTINGS_PATH} has no PostToolUse entry running "
            f"{hook.SCRIPT_PATH.name}; run `uv run outlooks hook --apply`"
        )
    return str(hook.SETTINGS_PATH)


def _stub(root: Path) -> str:
    rows = pkgskills.check(HOST, root, "local")
    bad = [f"{row.status}: {row.path}" for row in rows if not row.ok]
    bad += [
        f"{line.status}: {line.path}"
        for line in pkgskills.check_lines(HOST, root)
        if not line.ok
    ]
    if bad:
        raise _Failed("; ".join(bad) + "; run `uv run outlooks install`")
    # The drift check passes over the stamp's version, so a stub an earlier
    # release wrote is ok while its content is this release's. The note says
    # which release stamped it, never that the stamp is this one's.
    version = HOST.resolved_version()
    text = "\n".join(row.path.read_text(encoding="utf-8") for row in rows)
    stamped = sorted(set(STAMPED.findall(text)) - {version})
    if stamped:
        return (
            f"content current for {HOST.dist} {version}, stamped by "
            f"{', '.join(stamped)}; `uv run outlooks install` restamps it"
        )
    return f"current for {HOST.dist} {version}"


def run(start: Path | None = None) -> list[Result]:
    """Every check's result, in :data:`CHECKS` order.

    A check that needs the settings fails with the settings when they do not
    load: it is reported, never skipped.
    """
    start = (start or Path.cwd()).resolve()
    root = pkgskills.find_repo_root(start)
    checks: tuple[Callable[[], str], ...] = (
        lambda: _settings(start),
        lambda: _profile(start),
        lambda: _archive(start),
        lambda: _hook_script(root, start),
        lambda: _hook_settings(root),
        lambda: _stub(root),
    )
    results = []
    for name, check in zip(CHECKS, checks, strict=True):
        try:
            results.append(Result(name, True, check()))
        except _Failed as e:
            results.append(Result(name, False, str(e)))
        except (
            config.ConfigError,
            tomllib.TOMLDecodeError,
            ZoneInfoNotFoundError,
            ValueError,
        ):
            results.append(Result(name, False, "not checked: the settings do not load"))
    return results
