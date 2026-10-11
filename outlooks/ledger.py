"""The sweep's ledger read back: counts by outcome, and the rows of some outcomes.

The ledger is the sweep's (step ``ledger`` of the sweep skill): one row per
message it classified, keyed by ``internet_message_id``, with ``received`` and
``outcome`` beside it and the profile's own columns after. The outcome words
are the profile's too, so a word is matched as given, never mapped.

Read-only: the sweep owns every write to the ledger, after the operator's
approval.

Internal: not part of the Python API a consuming repo may import (the
README's "Python API" lists what is). The CLI is this module's interface.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from outlooks import classify, store
from outlooks.coverage import ledger_rows, local_time
from outlooks.detail import printable
from outlooks.timestamps import parse_ts

# The columns the package relies on; every other column is the profile's.
CORE = ("internet_message_id", "received", "outcome")
# How many characters of the archive key a row shows (``outlooks hash``).
KEY_CHARS = 8
# The narrowest a column is cut to when the rows don't fit the terminal.
MIN_WIDTH = 8
GAP = "  "
ELLIPSIS = "…"


def select(
    rows: list[dict[str, str]],
    mailbox: str,
    since: datetime | None = None,
) -> list[dict[str, str]]:
    """``mailbox``'s rows received at or after ``since``, oldest first."""
    kept = [
        r
        for r in ledger_rows(rows, mailbox)
        if since is None or (r.get("received") and parse_ts(r["received"]) >= since)
    ]
    return sorted(kept, key=lambda r: r.get("received") or "")


def counts(rows: list[dict[str, str]]) -> list[tuple[str, int]]:
    """Rows by ``outcome``, most first; a blank outcome counts as ``(none)``."""
    found = Counter(r.get("outcome") or "(none)" for r in rows)
    return sorted(found.items(), key=lambda item: (-item[1], item[0]))


@dataclass(frozen=True)
class Table:
    """The listed rows as display columns."""

    header: list[str]
    rows: list[list[str]]
    # The leading columns that are never cut (received, key, outcome).
    fixed: int


def fallback(columns: list[str]) -> list[str]:
    """``sender`` and ``subject``, whichever the ledger's ``columns`` lack."""
    return [c for c in ("sender", "subject") if c not in columns]


def table(
    rows: list[dict[str, str]],
    columns: list[str],
    archive: dict[str, dict[str, Any]],
    show_outcome: bool = False,
) -> Table:
    """``rows`` as the ``ledger`` command lists them.

    ``columns`` is the ledger's header. A row shows its ``received`` in the
    configured zone, its archive key, and every column beyond the core three
    in the ledger's order (``mailbox`` aside: the rows are one mailbox's).
    ``sender`` and ``subject`` come from ``archive`` (payloads by
    ``internetMessageId``) when the ledger has no such column. ``show_outcome``
    adds the outcome, for a listing of more than one.
    """
    extra = [c for c in columns if c not in CORE and c != "mailbox"]
    missing = fallback(columns)
    header = [
        "received",
        "key",
        *(["outcome"] if show_outcome else []),
        *missing,
        *extra,
    ]
    out: list[list[str]] = []
    for r in rows:
        mid = r.get("internet_message_id") or ""
        mail = classify.view(archive[mid]) if missing and mid in archive else None
        known = {
            "sender": _one_line(mail.sender) if mail else "",
            "subject": _one_line(mail.subject) if mail else "",
        }
        out.append(
            [
                local_time(parse_ts(r["received"])) if r.get("received") else "",
                store.id_hash(mid)[:KEY_CHARS] if mid else "",
                *([r.get("outcome") or ""] if show_outcome else []),
                *(known[c] for c in missing),
                *(_one_line(r.get(c) or "") for c in extra),
            ]
        )
    return Table(header, out, fixed=3 if show_outcome else 2)


def _one_line(value: str) -> str:
    """``value`` on one line, safe to print (:func:`~outlooks.detail.printable`)."""
    return printable(" ".join(value.split()))


def cut(value: str, width: int) -> str:
    """``value`` cut to ``width`` characters, the last one an ellipsis."""
    if len(value) <= width:
        return value
    return value[: max(width - 1, 0)] + ELLIPSIS


def widths(t: Table, limit: int | None) -> list[int]:
    """Each column's width; with a ``limit``, the widest cut until a line fits.

    Only the columns after ``t.fixed`` are cut, and none below
    :data:`MIN_WIDTH`, so a line can still run past a very narrow ``limit``.
    """
    found = [
        max(len(cell) for cell in column)
        for column in zip(t.header, *t.rows, strict=True)
    ]
    if limit is None:
        return found
    while sum(found) + len(GAP) * (len(found) - 1) > limit:
        cuttable = range(t.fixed, len(found))
        widest = max(cuttable, key=lambda i: found[i], default=None)
        if widest is None or found[widest] <= MIN_WIDTH:
            break
        found[widest] -= 1
    return found


def render(t: Table, limit: int | None) -> list[str]:
    """The table's lines, columns aligned; cut to ``limit`` characters if given."""
    sizes = widths(t, limit)
    return [
        GAP.join(
            cut(cell, w).ljust(w) for cell, w in zip(row, sizes, strict=True)
        ).rstrip()
        for row in [t.header, *t.rows]
    ]
