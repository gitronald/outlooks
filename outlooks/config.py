"""Which mailbox the archive tooling reads, and where it keeps what it reads.

Nothing else in the package names a mailbox, a path, a zone, or a sender:
every repo-specific value comes from ``[tool.outlooks]`` in the nearest
``pyproject.toml`` (walking up from the working directory) that holds the
table, each key overridable by an ``OUTLOOKS_{KEY}`` environment variable (a
list key takes a comma-separated value):

- ``mailbox``: the shared or delegated mailbox every search passes as
  ``mailboxOwnerEmail``. Unset means the signed-in account's own mailbox,
  which is archived under the first of ``own_addresses``.
- ``own_addresses`` (``[mailbox]``): the addresses whose mail is ours
  (``direction: out``).
- ``system_senders`` (none): automated senders, such as a web system's
  notification address, whose mail is ours unless the mailbox is a recipient.
- ``decision_tag`` (none): the subject tag our decision letters carry.
- ``archive_dir`` (``data/outlook``): the committed archive.
- ``captured_dir`` (``temp/outlook/captured``): where the capture hook writes.
- ``scratch_dir`` (``temp/outlook``): worklists and subagent reports.
- ``timezone`` (``America/Los_Angeles``): the zone dates are reported in.
- ``profile`` (none): the repo's profile, the prose the skill reads for what
  this package cannot know (registries, sweep classes, filing, cross-checks).

Relative paths resolve against the directory of that ``pyproject.toml`` (the
working directory when no file holds the table), so a command run from a
subdirectory finds the same archive and the same captures.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

# The names a consuming repo may import; a change to one is a changelog entry.
__all__ = [
    "DEFAULTS",
    "KEYS",
    "ConfigError",
    "Settings",
    "archive_dir",
    "captured_dir",
    "load",
    "mailbox",
    "scratch_dir",
    "settings",
    "zone",
    "zone_label",
]

DEFAULTS = {
    "archive_dir": "data/outlook",
    "captured_dir": "temp/outlook/captured",
    "scratch_dir": "temp/outlook",
    "timezone": "America/Los_Angeles",
    "render_label": "Message",
}
KEYS = (
    "mailbox",
    "own_addresses",
    "system_senders",
    "decision_tag",
    "archive_dir",
    "captured_dir",
    "scratch_dir",
    "timezone",
    "render_label",
    "profile",
)


class ConfigError(Exception):
    """A setting the tooling needs and cannot find."""


@dataclass(frozen=True)
class Settings:
    root: Path  # the directory relative paths resolve against
    source: Path | None  # the pyproject.toml holding the table, if any
    mailbox: str | None
    own_addresses: tuple[str, ...]
    system_senders: tuple[str, ...]
    decision_tag: str | None
    archive_dir: Path
    captured_dir: Path
    scratch_dir: Path
    timezone: ZoneInfo
    render_label: str  # the label `render` gives a file when --label is not passed
    profile: Path | None
    origins: dict[str, str]  # key -> "env", "pyproject.toml", or "default"


def _table(start: Path) -> tuple[Path, Path | None, dict[str, object]]:
    for folder in (start, *start.parents):
        path = folder / "pyproject.toml"
        if not path.is_file():
            continue
        with path.open("rb") as f:
            table = tomllib.load(f).get("tool", {}).get("outlooks")
        if isinstance(table, dict):
            return folder, path, table
    return start, None, {}


def load(start: Path | None = None) -> Settings:
    """Resolve the settings from ``start`` (default: the working directory)."""
    root, source, table = _table((start or Path.cwd()).resolve())
    origins: dict[str, str] = {}

    def raw(key: str) -> object:
        if env := os.environ.get(f"OUTLOOKS_{key.upper()}"):
            origins[key] = "env"
            return env
        if key in table:
            origins[key] = "pyproject.toml"
            return table[key]
        origins[key] = "default"
        return DEFAULTS.get(key)

    def get(key: str) -> str | None:
        value = raw(key)
        return None if value is None or value == "" else str(value)

    def addresses(key: str) -> tuple[str, ...]:
        value = raw(key)
        if value is None:
            return ()
        items = value.split(",") if isinstance(value, str) else value
        if not isinstance(items, list):
            raise ConfigError(f"[tool.outlooks] {key} must be a list of addresses")
        return tuple(str(a).strip().lower() for a in items if str(a).strip())

    def path(key: str) -> Path | None:
        value = get(key)
        if value is None:
            return None
        p = Path(value)
        return p if p.is_absolute() else root / p

    def required_path(key: str) -> Path:
        return path(key) or root / DEFAULTS[key]

    mailbox = get("mailbox")
    mailbox = mailbox.lower() if mailbox else None
    own = addresses("own_addresses")
    if not own and mailbox:
        own = (mailbox,)
    return Settings(
        root=root,
        source=source,
        mailbox=mailbox,
        own_addresses=own,
        system_senders=addresses("system_senders"),
        decision_tag=get("decision_tag"),
        archive_dir=required_path("archive_dir"),
        captured_dir=required_path("captured_dir"),
        scratch_dir=required_path("scratch_dir"),
        timezone=ZoneInfo(get("timezone") or DEFAULTS["timezone"]),
        render_label=get("render_label") or DEFAULTS["render_label"],
        profile=path("profile"),
        origins=origins,
    )


def declared_path(key: str, start: Path | None = None) -> Path:
    """A path key as ``pyproject.toml`` (or its default) gives it, environment aside.

    For a value written into a file that reads the environment itself: the
    capture hook script takes ``OUTLOOKS_CAPTURED_DIR`` when it runs, so what
    it falls back to is the table's value, not this process's override.
    """
    root, _, table = _table((start or Path.cwd()).resolve())
    p = Path(str(table.get(key) or DEFAULTS[key]))
    return p if p.is_absolute() else root / p


def settings() -> Settings:
    """The settings for the working directory and ``OUTLOOKS_*`` environment.

    Resolved once per (directory, environment) pair, so a test or a command that
    changes either gets fresh settings, never another directory's archive.
    """
    env = tuple(
        sorted((k, v) for k, v in os.environ.items() if k.startswith("OUTLOOKS_"))
    )
    return _settings(Path.cwd(), env)


@cache
def _settings(cwd: Path, env: tuple[tuple[str, str], ...]) -> Settings:
    return load(cwd)


def mailbox() -> str:
    """The address the archive files under; refuses when there is none.

    The configured ``mailbox``, or, for the signed-in account's own mailbox
    (``mailbox`` unset), the first of ``own_addresses``.
    """
    s = settings()
    box = s.mailbox or next(iter(s.own_addresses), None)
    if not box:
        raise ConfigError(
            "no mailbox configured: set mailbox in [tool.outlooks] "
            "(pyproject.toml) or OUTLOOKS_MAILBOX, or, for the signed-in "
            "account's own mailbox, own_addresses"
        )
    return box


def archive_dir() -> Path:
    """The committed archive: ``hits/``, ``messages/``, ``coverage.csv``."""
    return settings().archive_dir


def captured_dir() -> Path:
    """Where the capture hook writes the connector's output."""
    return settings().captured_dir


def scratch_dir() -> Path:
    """Where worklists, window plans, and subagent reports go (gitignored)."""
    return settings().scratch_dir


def zone() -> ZoneInfo:
    """The zone dates and times are reported in (the connector reports UTC)."""
    return settings().timezone


def zone_label(when: datetime | None = None) -> str:
    """A short label for the zone: ``PT`` for Pacific, else its abbreviation.

    A three-letter standard/daylight abbreviation (``PST``/``PDT``) loses its
    middle letter, so a label reads the same all year.
    """
    at = when or datetime.now(zone())
    name = at.astimezone(zone()).tzname() or ""
    if len(name) == 3 and name[1:] in ("ST", "DT"):
        return name[0] + "T"
    return name


def describe() -> list[tuple[str, str, str]]:
    """(key, effective value, where it came from) for every key."""
    s = settings()
    own = next(iter(s.own_addresses), None)
    account = f"archived as {own}" if own else "set own_addresses"
    values: dict[str, object] = {
        "mailbox": s.mailbox or f"(unset: the signed-in account, {account})",
        "own_addresses": ", ".join(s.own_addresses) or "(none)",
        "system_senders": ", ".join(s.system_senders) or "(none)",
        "decision_tag": s.decision_tag or "(unset)",
        "archive_dir": s.archive_dir,
        "captured_dir": s.captured_dir,
        "scratch_dir": s.scratch_dir,
        "timezone": s.timezone.key,
        "render_label": s.render_label,
        "profile": s.profile or "(unset)",
    }
    return [(k, str(values[k]), s.origins.get(k, "default")) for k in KEYS]
