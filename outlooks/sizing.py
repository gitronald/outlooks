"""Window sizes and the item-id proof, read from the hook's captures.

A window pull's offset-0 page reports the window's ``totalResultCount``, and an
empty window comes back as a capture with nothing in it (``is_empty_window``),
so the captures alone say how big every probed window is. A window with no
capture is unknown, never 0: a throttled or failed call leaves no capture.

The final proof that a backfill missed nothing is an item-id count, not a sum
of totals: the connector's ``totalResultCount`` counts folder copies (a
message the mailbox sent to itself sits in both Sent Items and the inbox), so
summed totals overcount. Window bounds are also inclusive on both ends, so a
message that lands on a boundary is listed in both adjacent windows; a set of
unique hit ``id``s dedupes that overlap, while unique ``internetMessageId``s
give the distinct-message count, the gap between the two being folder copies.
The proof is only reproducible while the captures under
the captures directory (gitignored) still exist — once they're gone,
``coverage.csv`` is the durable record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from outlooks.capture import Capture
from outlooks.coverage import (
    Bounds,
    is_empty_window,
    is_window,
    window_bounds,
    windows,
)


def window_sizes(captures: list[Capture], mailbox: str) -> dict[Bounds, int]:
    """Each captured window's size, from its newest capture; an empty window is 0.

    ``captures`` in capture order, as ``load_captures`` returns them, so a later
    pull of the same bounds replaces an earlier one. A page that reports no
    total (the signed-in account's own mailbox) sizes nothing on its own: such
    a window's size is known only once a pull of it is complete.
    """
    sized: list[tuple[datetime, Bounds, int]] = []
    for c in captures:
        if not c.is_of(mailbox) or not is_window(c):
            continue
        if c.total is not None or is_empty_window(c):
            sized.append((c.at, window_bounds(c), c.total or 0))
    for w in windows(captures, mailbox):
        if w.complete and w.total is not None and w.last is not None:
            sized.append((w.last, (w.after, w.before), w.total))
    return {bounds: size for _, bounds, size in sorted(sized, key=lambda s: s[0])}


@dataclass(frozen=True)
class IdCheck:
    """The item-id proof for one range."""

    after: datetime
    before: datetime
    baseline: int | None  # the range's own size; None when never probed
    ids: int  # unique hit ids across the range's window captures
    messages: int  # unique internetMessageIds across the same captures
    windows: int  # window captures counted

    @property
    def ok(self) -> bool:
        return (
            self.baseline is not None and self.windows > 0 and self.ids == self.baseline
        )


def id_check(
    captures: list[Capture], mailbox: str, after: datetime, before: datetime
) -> IdCheck:
    """The item-id proof: unique ids across the range's windows vs. its own size.

    Counts only bounded window captures (``is_window``, this ``mailbox``, a
    ``beforeDateTime``) fully inside ``[after, before]``; an open-ended capture or
    one reaching outside the range is excluded, and a lookup is already excluded by
    ``is_window``. The baseline comes from ``window_sizes`` at exactly these bounds,
    never from the archived ``hits/*.jsonl`` (the archive dedupes on
    ``internetMessageId``, so folder-copy item ids are gone from it by the time it's
    written).
    """
    baseline = window_sizes(captures, mailbox).get((after, before))
    ids: set[str] = set()
    messages: set[str] = set()
    windows = 0
    for c in captures:
        if not c.is_of(mailbox) or not is_window(c):
            continue
        c_after, c_before = window_bounds(c)
        if c_before is None or c_after < after or c_before > before:
            continue
        windows += 1
        for hit in c.hits:
            ids.add(hit["id"])
            messages.add(hit["internetMessageId"])
    return IdCheck(after, before, baseline, len(ids), len(messages), windows)
