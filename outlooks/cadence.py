"""When the next sweep is due: the ``sweep_cadence`` setting and the rule that reads it.

``sweep_cadence`` is a list of entries, each ``"<every>"`` or ``"<YYYY-MM-DD>
<every>"``, where ``<every>`` is ``<n>d``, ``<n>h``, or ``off``. An undated
entry is the cadence from the start; a dated one takes over from midnight of
that date in the configured zone. So ``["7d", "2027-03-08 1d", "2027-03-15
off"]`` sweeps weekly, then daily from the 8th, and not at all from the 15th.

A sweep is due once the interval in force has passed since the **last sweep**,
the end of recorded coverage (a sweep's window ends at the instant it was
pulled). It is due at once when there is no coverage yet, or when the archive
holds mail the ledger does not list (:func:`outlooks.coverage.unclassified`),
whatever the interval says, so a run that archived and stopped short of
classifying is finished first. ``off`` and an unset cadence schedule nothing.

Internal: not part of the Python API a consuming repo may import (the
README's "Python API" lists what is). The CLI is this module's interface.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from outlooks import config

EVERY = re.compile(r"(?:(\d+)([dh])|off)")
UNITS = {"d": "days", "h": "hours"}


@dataclass(frozen=True)
class Step:
    """One entry: from ``start`` (``None``: from the start), sweep ``every``."""

    start: date | None
    every: timedelta | None  # None: off

    def describe(self) -> str:
        every = "off" if self.every is None else f"every {_span(self.every)}"
        return every if self.start is None else f"from {self.start} {every}"


def _span(every: timedelta) -> str:
    hours = int(every.total_seconds()) // 3600
    return f"{hours // 24}d" if hours % 24 == 0 else f"{hours}h"


def _every(text: str, entry: str) -> timedelta | None:
    found = EVERY.fullmatch(text)
    if found is None:
        raise config.ConfigError(
            f"sweep_cadence entry {entry!r}: {text!r} is not <n>d, <n>h, or off"
        )
    if found.group(0) == "off":
        return None
    n, unit = int(found.group(1)), found.group(2)
    if n == 0:
        raise config.ConfigError(f"sweep_cadence entry {entry!r}: an interval of 0")
    return timedelta(**{UNITS[unit]: n})


def parse(entries: Iterable[str]) -> tuple[Step, ...]:
    """The steps of ``entries``; :class:`~outlooks.config.ConfigError` names a bad one.

    At most one entry is undated, and it comes first; the dated ones follow in
    strictly increasing date order.
    """
    steps: list[Step] = []
    for entry in entries:
        parts = entry.split()
        if len(parts) == 1:
            if steps:
                raise config.ConfigError(
                    f"sweep_cadence entry {entry!r}: an undated entry comes first"
                )
            steps.append(Step(None, _every(parts[0], entry)))
            continue
        if len(parts) != 2:
            raise config.ConfigError(
                f"sweep_cadence entry {entry!r}: not <every> or <YYYY-MM-DD> <every>"
            )
        try:
            start = date.fromisoformat(parts[0])
        except ValueError:
            raise config.ConfigError(
                f"sweep_cadence entry {entry!r}: {parts[0]!r} is not a YYYY-MM-DD date"
            ) from None
        last = steps[-1].start if steps else None
        if last is not None and start <= last:
            raise config.ConfigError(
                f"sweep_cadence entry {entry!r}: dates must increase ({last} first)"
            )
        steps.append(Step(start, _every(parts[1], entry)))
    return tuple(steps)


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=config.zone())


def segments(
    steps: tuple[Step, ...], now: datetime
) -> list[tuple[datetime, datetime | None, Step]]:
    """The spans from ``now`` on, each with the step in force (``None`` end: open).

    The time before the first dated entry, when there is no undated one, has no
    step and is left out: nothing is scheduled then.
    """
    out: list[tuple[datetime, datetime | None, Step]] = []
    for i, step in enumerate(steps):
        start = now if step.start is None else max(now, _midnight(step.start))
        nxt = steps[i + 1].start if i + 1 < len(steps) else None
        end = None if nxt is None else _midnight(nxt)
        if end is not None and end <= now:
            continue
        out.append((start, end, step))
    return out


def in_force(steps: tuple[Step, ...], now: datetime) -> Step | None:
    """The step in force at ``now``, or ``None`` before the first one starts."""
    spans = segments(steps, now)
    return spans[0][2] if spans and spans[0][0] <= now else None


def next_due(
    steps: tuple[Step, ...],
    last: datetime | None,
    unclassified: int,
    now: datetime,
) -> datetime | None:
    """When the next sweep is due: ``now``, a later instant, or ``None`` (never).

    ``last`` is the last sweep (the end of coverage), ``unclassified`` the
    archived messages the ledger does not list. Due at once with no coverage or
    with unclassified mail, but only while a cadence is on: the first instant a
    step that is not ``off`` is in force.
    """
    for start, end, step in segments(steps, now):
        if step.every is None:
            continue
        if last is None or unclassified:
            return start
        due = max(start, last + step.every)
        if end is None or due < end:
            return due
    return None
