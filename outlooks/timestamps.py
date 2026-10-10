"""Parsing and formatting archive timestamps in UTC.

Internal: not part of the Python API a consuming repo may import (the
README's "Python API" lists what is). The CLI is this module's interface.
"""

from datetime import UTC, datetime


def parse_ts(value: str) -> datetime:
    """An ISO timestamp (``Z`` or an offset) as an aware UTC datetime."""
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def fmt_ts(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
