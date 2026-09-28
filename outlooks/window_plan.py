"""Plan a mailbox window backfill: size, split oversized windows, and page them.

A backfill covers one fixed range ``[after, before)`` (see the ``outlooks``
skill's window mode). The connector's per-window offset paging misses bursts
above roughly ``LIMIT`` messages, so a plan starts with one piece per month and
splits any piece that comes back oversized, first along local day boundaries
(midnights in ``config.zone()``) and, once a piece is too short to hold one,
along plain seconds. Each piece is eventually small enough to page in full.

State lives in ``windows.json`` in a working directory (``save``/``load``): the
run's mailbox, its fixed ``[after, before)``, the whole range's own size (from a
probe of exactly those bounds), and the ordered, contiguous pieces. Sizes come
from ``sizing.window_sizes`` (``fill``), never invented for a piece with no
capture -- a throttled or failed call leaves no capture, which is not the same
as an empty window.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from outlooks import config
from outlooks.coverage import Bounds, fmt_ts, merge, parse_ts

LIMIT = 175  # above this a window risks the connector's per-window offset ceiling
DAY_TARGET = 150  # messages aimed for per day-split piece, with headroom under LIMIT
# Messages aimed for per second-aligned piece: mail is bursty, so aim low.
SLICE_TARGET = 110
PAGE = 25  # messages returned per search page
MAX_PAGES = 7  # pages bundled into one pager batch


class PlanError(Exception):
    """A plan operation that cannot proceed: bad bounds, or pieces not ready to page."""


@dataclass
class Piece:
    """One contiguous window slice; ``total`` is its message count, once sized."""

    after: datetime
    before: datetime
    total: int | None = None


@dataclass
class Plan:
    """A backfill plan: the run's fixed range, cut into contiguous pieces."""

    mailbox: str
    after: datetime
    before: datetime  # the run's fixed cutoff: no open-ended windows
    total: int | None  # the whole range's size, from a probe of exactly [after, before]
    # Contiguous and in order: pieces[i].before == pieces[i + 1].after.
    pieces: list[Piece]


_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_bound(value: str) -> datetime:
    """A bare ``YYYY-MM-DD`` date is that day's local midnight, as UTC.

    Anything else is a timestamp and must carry ``Z`` or an offset -- a naive
    timestamp is ambiguous (which zone?) and refused. So is one with a fraction
    of a second: the state file and ``coverage.csv`` hold whole seconds, so the
    fraction would be dropped on the first save.
    """
    try:
        if _DATE.fullmatch(value):
            year, month, day = (int(p) for p in value.split("-"))
            return datetime(year, month, day, tzinfo=config.zone()).astimezone(UTC)
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise PlanError(f"{value!r} is not a YYYY-MM-DD date or a timestamp") from None
    if ts.tzinfo is None:
        raise PlanError(f"{value!r} is a naive timestamp; add a Z or an offset")
    if ts.microsecond:
        raise PlanError(f"{value!r} has a fraction of a second; bounds are whole")
    return parse_ts(value)


def _month_starts(after: datetime, before: datetime) -> list[datetime]:
    """Local first-of-month midnights strictly inside ``(after, before)``, as UTC."""
    local = after.astimezone(config.zone())
    year, month = local.year, local.month
    cuts: list[datetime] = []
    while True:
        month += 1
        if month > 12:
            month, year = 1, year + 1
        cut = datetime(year, month, 1, tzinfo=config.zone()).astimezone(UTC)
        if cut >= before:
            return cuts
        cuts.append(cut)


def init(mailbox: str, after: datetime, before: datetime) -> Plan:
    """A month-cut plan over ``[after, before)``; raises on a reversed/empty range."""
    if after >= before:
        raise PlanError(f"after ({after}) must be before before ({before})")
    bounds = [after, *_month_starts(after, before), before]
    pieces = [Piece(a, b) for a, b in zip(bounds, bounds[1:])]
    return Plan(mailbox=mailbox, after=after, before=before, total=None, pieces=pieces)


def fill(plan: Plan, sizes: dict[Bounds, int]) -> None:
    """Apply every size ``sizes`` has; a piece absent from it keeps what it had.

    An absent capture is never evidence of 0: a failed or throttled call leaves
    no capture, so ``window_sizes`` never invents one. A piece already sized
    takes the new value too -- ``window_sizes`` already kept the newest capture.
    """
    for piece in plan.pieces:
        size = sizes.get((piece.after, piece.before))
        if size is not None:
            piece.total = size
    total = sizes.get((plan.after, plan.before))
    if total is not None:
        plan.total = total


def _interior_midnights(after: datetime, before: datetime) -> list[datetime]:
    """Local midnights strictly inside ``(after, before)``, as UTC, in order."""
    zone = config.zone()
    day = after.astimezone(zone).date()
    first = datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(UTC)
    if first <= after:
        day += timedelta(days=1)
    midnights: list[datetime] = []
    while True:
        cut = datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(UTC)
        if cut >= before:
            return midnights
        midnights.append(cut)
        day += timedelta(days=1)


def _spread(candidates: list[datetime], count: int) -> list[datetime]:
    """``count`` of ``candidates``, spread evenly; all of them when ``count >= len``."""
    n = len(candidates)
    if count >= n:
        return list(candidates)
    if count <= 0:
        return []
    step = n / (count + 1)
    indices = sorted(
        {min(n - 1, max(0, round(step * (i + 1)) - 1)) for i in range(count)}
    )
    return [candidates[i] for i in indices]


def split_piece(piece: Piece) -> list[Piece]:
    """A sized, oversized piece cut into children; ``[piece]`` when it cannot be.

    A day split when the piece has an interior local midnight to cut at,
    otherwise a second-aligned split. Non-midnight endpoints are kept exactly as
    given. Children share exact endpoints with each other and the parent --
    window bounds are inclusive on both ends, so a boundary message is listed
    twice, never missed -- and their totals are unknown (``None``). A piece
    under two seconds has no interior second to cut at and comes back unchanged,
    for the caller to report as unsplittable.
    """
    assert piece.total is not None
    total = piece.total
    midnights = _interior_midnights(piece.after, piece.before)
    if midnights:
        k = math.ceil(total / DAY_TARGET)
        cuts = _spread(midnights, k - 1)
    else:
        secs = int((piece.before - piece.after).total_seconds())
        if secs < 2:
            return [piece]
        k = min(math.ceil(total / SLICE_TARGET), secs)
        cuts = sorted(
            {piece.after + timedelta(seconds=round(i * secs / k)) for i in range(1, k)}
        )
    bounds = [piece.after, *cuts, piece.before]
    return [Piece(a, b) for a, b in zip(bounds, bounds[1:])]


def split(plan: Plan) -> tuple[list[Piece], list[Piece]]:
    """Replace every piece over ``LIMIT`` with its children; mutates ``plan.pieces``.

    Returns (new pieces still needing a size, unsplittable pieces left as they
    were). Pieces at or under ``LIMIT``, and unsized ones, are untouched.
    """
    kept: list[Piece] = []
    new: list[Piece] = []
    unsplittable: list[Piece] = []
    for piece in plan.pieces:
        if piece.total is None or piece.total <= LIMIT:
            kept.append(piece)
            continue
        children = split_piece(piece)
        if len(children) == 1:
            unsplittable.append(piece)
            kept.append(piece)
            continue
        kept.extend(children)
        new.extend(children)
    plan.pieces = kept
    return new, unsplittable


def remaining(plan: Plan, rows: list[dict[str, str]]) -> list[Piece]:
    """Pieces not contained in a single merged coverage span, for this mailbox.

    ``rows`` are ``coverage.read_rows()`` dicts; a piece counts as covered only
    when one merged span's bounds contain it (containment, not a literal-endpoint
    match), so a piece covered by two differently split rows still reads as done.
    """
    own = [r for r in rows if r["mailbox"] == plan.mailbox]
    covered = merge(own)
    return [
        piece
        for piece in plan.pieces
        if not any(
            start <= piece.after and piece.before <= end for start, end in covered
        )
    ]


def pages(piece: Piece) -> int:
    """The search pages a sized piece takes to page in full."""
    assert piece.total is not None
    return math.ceil(piece.total / PAGE)


def batches(plan: Plan, rows: list[dict[str, str]]) -> list[list[Piece]]:
    """Uncovered pieces packed into pager batches of at most ``MAX_PAGES`` pages.

    Raises ``PlanError`` naming any uncovered piece that isn't sized or is still
    over ``LIMIT`` -- size or split it first. Skips 0- and 1-message pieces: the
    sizing probe (``limit: 1``) already listed their one hit, so they need no page.
    """
    left = remaining(plan, rows)
    bad = [p for p in left if p.total is None or p.total > LIMIT]
    if bad:
        names = ", ".join(f"{fmt_ts(p.after)} to {fmt_ts(p.before)}" for p in bad)
        raise PlanError(f"not ready to page (size or split first): {names}")
    grouped: list[list[Piece]] = []
    current: list[Piece] = []
    used = 0
    for piece in left:
        if piece.total in (0, 1):
            continue
        need = pages(piece)
        if current and used + need > MAX_PAGES:
            grouped.append(current)
            current = []
            used = 0
        current.append(piece)
        used += need
    if current:
        grouped.append(current)
    return grouped


def write_batches(groups: list[list[Piece]], folder: Path) -> list[Path]:
    """Write ``pager-{nn}.txt`` files, clearing stale ones from a prior round first."""
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("pager-*.txt"):
        old.unlink()
    written: list[Path] = []
    for n, group in enumerate(groups, start=1):
        path = folder / f"pager-{n:02d}.txt"
        lines = "".join(
            f"{fmt_ts(p.after)} {fmt_ts(p.before)} {p.total}\n" for p in group
        )
        path.write_text(lines, encoding="utf-8")
        written.append(path)
    return written


def save(plan: Plan, folder: Path) -> None:
    """Write ``windows.json`` under ``folder``."""
    folder.mkdir(parents=True, exist_ok=True)
    data = {
        "mailbox": plan.mailbox,
        "after": fmt_ts(plan.after),
        "before": fmt_ts(plan.before),
        "total": plan.total,
        "pieces": [
            {"after": fmt_ts(p.after), "before": fmt_ts(p.before), "total": p.total}
            for p in plan.pieces
        ],
    }
    (folder / "windows.json").write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )


def load(folder: Path) -> Plan:
    """Read ``windows.json`` from ``folder``; raises when no plan was started."""
    path = folder / "windows.json"
    if not path.is_file():
        raise PlanError(f"no plan at {path}: start one with `windows init`")
    data = json.loads(path.read_text(encoding="utf-8"))
    return Plan(
        mailbox=data["mailbox"],
        after=parse_ts(data["after"]),
        before=parse_ts(data["before"]),
        total=data["total"],
        pieces=[
            Piece(parse_ts(p["after"]), parse_ts(p["before"]), p["total"])
            for p in data["pieces"]
        ],
    )
