"""Which mailbox windows the archive holds completely.

A lookup (a ``query``, ``sender``, or ``folderName`` search) returns only the
mail it matched, so a hit in the archive says nothing about what else arrived
that day. A **window pull** does: a filter-mode search with only
``afterDateTime``/``beforeDateTime`` returns every message in the window,
newest first, and each page reports the window's ``totalResultCount``.

``windows`` groups the captured window pages; a window is complete when its
captured offsets run from 0 to ``total - 1``. ``record`` appends each complete
window to ``coverage.csv`` beside the archive once every message it listed is
in the archive, so a row is evidence rather than a claim. ``report`` merges the
rows and lists the gaps between them.

``total`` counts folder copies (a message the mailbox sent to itself sits in
Sent Items and in another folder), while the archive keys on
``internetMessageId``, so a row carries both ``total`` and ``messages``, the
distinct count.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeAlias

from outlooks import config, store
from outlooks.capture import Capture

FIELDS = ["mailbox", "after", "before", "total", "messages", "pulled"]
# The inputs a window pull sets; any other (query, sender, folderName, ...)
# narrows the search to a lookup.
WINDOW_KEYS = {
    "mailboxOwnerEmail",
    "afterDateTime",
    "beforeDateTime",
    "limit",
    "offset",
}


class CoverageError(Exception):
    """A window that cannot be recorded as covered."""


def coverage_csv(root: Path | None = None) -> Path:
    """``coverage.csv`` beside the archive's ``hits/`` and ``messages/``."""
    return (store.archive_root() if root is None else root) / "coverage.csv"


def ledger_csv(root: Path | None = None) -> Path:
    """The sweep's ``ledger.csv``, beside the archive."""
    return (store.archive_root() if root is None else root) / "ledger.csv"


def mass_sends_csv(root: Path | None = None) -> Path:
    """``mass-sends.csv``: the bulk sends read once, not once per copy (hand-owned).

    A bulk send (a mail-merge, an announcement) leaves one copy per recipient,
    differing only in the recipient, so one full read stands for all of them.
    Each row names the ``sender``, the exact ``subject``, the ``after`` and
    ``before`` UTC timestamps around the send (both inclusive, unlike a
    window's bounds: they are typed by hand), and the ``exemplar``, the
    ``internetMessageId`` of the copy read in full.
    """
    return (store.archive_root() if root is None else root) / "mass-sends.csv"


def mass_copies(
    hits: dict[str, dict[str, Any]], sends: list[dict[str, str]]
) -> set[str]:
    """The hits that are copies of a listed mass send, its exemplar excluded."""
    out: set[str] = set()
    if not sends:
        return out
    received = {mid: _received(h) for mid, h in hits.items()}
    for send in sends:
        lo, hi = parse_ts(send["after"]), parse_ts(send["before"])
        sender = send["sender"].lower()
        for mid, h in hits.items():
            ts = received[mid]
            if (
                mid != send["exemplar"]
                and (h.get("sender") or "").lower() == sender
                and h.get("subject") == send["subject"]
                and ts is not None
                and lo <= ts <= hi
            ):
                out.add(mid)
    return out


def parse_ts(value: str) -> datetime:
    """An ISO timestamp (``Z`` or an offset) as an aware UTC datetime."""
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def fmt_ts(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Window:
    """The captured pages of one window pull."""

    mailbox: str
    after: datetime
    before: datetime | None  # None: open-ended, up to when it was pulled
    # None: the pull reports no total and its last page is not captured.
    total: int | None
    offsets: set[int] = field(default_factory=set)
    messages: set[str] = field(default_factory=set)
    first: datetime | None = None
    last: datetime | None = None

    @property
    def complete(self) -> bool:
        return self.total is not None and set(range(self.total)) <= self.offsets

    @property
    def end(self) -> datetime:
        """The window's exclusive end; an open window ends at its first page.

        Every page of an open window reported the same total, so nothing
        arrived while it was paged, but mail after the first page may have.
        """
        if self.before is not None:
            return self.before
        assert self.first is not None
        return self.first

    def row(self) -> dict[str, str]:
        return {
            "mailbox": self.mailbox,
            "after": fmt_ts(self.after),
            "before": fmt_ts(self.end),
            "total": str(self.total),
            "messages": str(len(self.messages)),
            "pulled": self.last.astimezone(config.zone()).isoformat(timespec="seconds")
            if self.last
            else "",
        }


def _is_pull(capture: Capture) -> bool:
    return (
        capture.tool == "search"
        and "afterDateTime" in capture.args
        and set(capture.args) <= WINDOW_KEYS
    )


def is_window(capture: Capture) -> bool:
    # A page with no hits is an empty window, the page after the last of a pull
    # that reports no total (``is_end_page``, which ``windows`` reads), or an
    # offset past the end, which the connector answers with a bare
    # totalResultCount equal to the offset asked for: an echo, not a pull.
    return _is_pull(capture) and (bool(capture.hits) or is_empty_window(capture))


def is_empty_window(capture: Capture) -> bool:
    """An offset-0 page that came back with nothing at all: the window holds no mail.

    The hook is PostToolUse, so a throttled or failed call leaves no capture;
    an empty capture is the connector's own answer, and a complete window of 0.
    A lost one (its spilled output gone) has no blocks either, and is not.
    """
    return not capture.blocks and not capture.lost and not capture.args.get("offset")


def is_end_page(capture: Capture) -> bool:
    """A page past offset 0 that came back with nothing at all.

    A search of the signed-in account's own mailbox reports no total: a full
    page says more follows, and the pull ends at a short page or at this one.
    """
    return (
        _is_pull(capture)
        and not capture.blocks
        and not capture.lost
        and bool(capture.args.get("offset"))
    )


def page_end(capture: Capture) -> int | None:
    """Where the results end, when this page of a pull with no total says so.

    A page of hits that names no next offset is the last one; so is the empty
    page after a full one. A page that says more follows does not know.
    """
    if is_end_page(capture):
        return int(capture.args["offset"])
    if capture.hits and not capture.more:
        return max(hit["offset"] for hit in capture.hits) + 1
    return None


Bounds: TypeAlias = tuple[datetime, datetime | None]  # before None: open-ended


def window_bounds(capture: Capture) -> Bounds:
    """A window capture's ``(after, before)``; ``before`` None when it set none."""
    before = capture.args.get("beforeDateTime")
    return parse_ts(capture.args["afterDateTime"]), parse_ts(before) if before else None


def windows(captures: list[Capture], mailbox: str) -> list[Window]:
    """The window pulls among ``captures`` for ``mailbox``, complete or not.

    Pages group by window and total: a later pull of the same window that
    reports a different total (mail moved or deleted since) is a separate pull.
    Pages that report no total group by window alone, and their total is where
    the newest last page ends (``page_end``); until one is captured the total
    is unknown and the window incomplete.
    """
    found: dict[tuple[datetime, datetime | None, int | None], Window] = {}
    for c in captures:
        if not c.is_of(mailbox) or not (is_window(c) or is_end_page(c)):
            continue
        after, before = window_bounds(c)
        total = 0 if is_empty_window(c) else c.total
        key = (after, before, total)
        w = found.setdefault(key, Window(mailbox, after, before, total))
        for hit in c.hits:
            w.offsets.add(hit["offset"])
            w.messages.add(hit["internetMessageId"])
        if total is None and (end := page_end(c)) is not None:
            w.total = end
        w.first = min(w.first or c.at, c.at)
        w.last = max(w.last or c.at, c.at)
    # An end page with no page of hits before it is a pull of nothing known.
    pulls = [w for (_, _, total), w in found.items() if total == 0 or w.offsets]
    return sorted(pulls, key=lambda w: (w.after, w.end))


def read_rows(path: Path | None = None) -> list[dict[str, str]]:
    path = coverage_csv() if path is None else path
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def record(found: list[Window], root: Path | None = None) -> list[dict[str, str]]:
    """Append each complete window not yet recorded; returns the new rows.

    Refuses (``CoverageError``) a complete window whose messages are not all in
    the archive: import the captures first.
    """
    path = coverage_csv(root)
    rows = read_rows(path)
    have = {(r["mailbox"], r["after"], r["before"], r["total"]) for r in rows}
    archived = set(store.load_hits(root))
    new: list[dict[str, str]] = []
    for w in found:
        if not w.complete:
            continue
        row = w.row()
        if (row["mailbox"], row["after"], row["before"], row["total"]) in have:
            continue
        missing = w.messages - archived
        if missing:
            raise CoverageError(
                f"window {row['after']} to {row['before']}: {len(missing)} of its "
                f"messages are not in the archive; import the captures first"
            )
        new.append(row)
    if new:
        rows = sorted(rows + new, key=lambda r: (r["mailbox"], r["after"], r["before"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    return new


# --- report -----------------------------------------------------------------------


def merge(rows: list[dict[str, str]]) -> list[tuple[datetime, datetime]]:
    """The covered intervals, overlapping and touching rows merged."""
    spans = sorted((parse_ts(r["after"]), parse_ts(r["before"])) for r in rows)
    merged: list[tuple[datetime, datetime]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def gaps(
    covered: list[tuple[datetime, datetime]], since: datetime, now: datetime
) -> list[tuple[datetime, datetime]]:
    """The spans of ``[since, now)`` no covered interval reaches."""
    out: list[tuple[datetime, datetime]] = []
    cursor = since
    for start, end in covered:
        if start > cursor:
            out.append((cursor, min(start, now)))
        cursor = max(cursor, end)
        if cursor >= now:
            break
    if cursor < now:
        out.append((cursor, now))
    return [(a, b) for a, b in out if a < b]


def _received(hit: dict[str, Any]) -> datetime | None:
    value = hit.get("receivedDateTime")
    return parse_ts(value) if value else None


def _within(ts: datetime | None, spans: list[tuple[datetime, datetime]]) -> bool:
    return ts is not None and any(a <= ts < b for a, b in spans)


def _pt(ts: datetime) -> str:
    return ts.astimezone(config.zone()).strftime("%Y-%m-%d %H:%M")


def report(
    mailbox: str,
    rows: list[dict[str, str]],
    hits: dict[str, dict[str, Any]],
    read: set[str],
    ledger: list[dict[str, str]],
    since: datetime | None = None,
    now: datetime | None = None,
    skipped: set[str] | None = None,
) -> list[str]:
    """The coverage report's lines (times in the configured zone).

    ``ledger`` rows are the sweep's. A ledger with a ``mailbox`` column counts
    only this mailbox's rows; one without (a repo with one mailbox) counts all.
    ``skipped`` are the copies of a mass send (:func:`mass_copies`), unread by
    design: counted on their own line, not as covered mail with no full read.
    """
    skipped = skipped or set()
    now = now or datetime.now(UTC)
    rows = [r for r in rows if r["mailbox"] == mailbox]
    hits = {k: h for k, h in hits.items() if h.get("mailbox") == mailbox}
    covered = merge(rows)
    lines = [f"coverage for {mailbox} ({config.zone_label(now)})"]
    if not covered:
        lines.append("no windows recorded")
    for a, b in covered:
        n = sum(_within(_received(h), [(a, b)]) for h in hits.values())
        lines.append(f"  covered  {_pt(a)} to {_pt(b)}  {n} archived")
    start = since or (covered[0][0] if covered else None)
    if start is not None:
        for a, b in gaps(covered, start, now):
            n = sum(_within(_received(h), [(a, b)]) for h in hits.values())
            note = f"  {n} archived (lookup only)" if n else ""
            lines.append(f"  GAP      {_pt(a)} to {_pt(b)}{note}")
    unread: Counter[str] = Counter()
    by_design = 0
    for mid, h in hits.items():
        ts = _received(h)
        if mid not in read and _within(ts, covered):
            assert ts is not None
            if mid in skipped:
                by_design += 1
                continue
            unread[ts.astimezone(config.zone()).strftime("%Y-%m")] += 1
    if unread:
        lines.append("covered hits with no full read, by month:")
        lines += [f"  {month}  {n}" for month, n in sorted(unread.items())]
    if by_design:
        lines.append(f"mass-send copies left unread by design  {by_design}")
    marks = [
        r["received"]
        for r in ledger
        if r.get("received") and r.get("mailbox", mailbox).lower() == mailbox
    ]
    if marks:
        lines.append(f"ledger high-water mark  {_pt(parse_ts(max(marks)))}")
    if covered:
        lines.append(f"coverage ends           {_pt(covered[-1][1])}")
        if marks and parse_ts(max(marks)) < covered[-1][1]:
            lines.append("  the ledger lags coverage: archived mail not yet classified")
    return lines
