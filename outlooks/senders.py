"""Who writes to the mailbox, counted from the archive's hits.

A notifier that is in neither ``system_senders`` nor ``notice_senders`` is
classified as a person writing in: its mail becomes an ``arrival``, and its
address reads as a correspondent's. No single message shows that, so
:func:`senders` counts the inbound hits by sender address and marks each
address ``own``, ``system``, ``notice``, or ``unlisted``. A frequent unlisted
address that no person writes from is the one to list: in ``system_senders``
when it speaks for us, in ``notice_senders`` when it only writes to us. A
hit with no sender has no address to list, and is marked ``none``.
Read-only, over the hits tier.

Internal: not part of the Python API a consuming repo may import (the
README's "Python API" lists what is). The CLI is this module's interface.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any

from outlooks import config, store
from outlooks.coverage import parse_ts


@dataclass(frozen=True)
class Sender:
    address: str
    count: int
    mark: str  # "own", "system", "notice", "unlisted", or "none" (no sender)


def is_inbound(hit: dict[str, Any], own: tuple[str, ...]) -> bool:
    """Whether a hit is mail to the mailbox: all of it but our own mail to others."""
    if (hit.get("sender") or "").lower() not in own:
        return True
    return any((r or "").lower() in own for r in hit.get("recipients") or [])


def senders(
    mailbox: str,
    since: date | None = None,
    hits: dict[str, dict[str, Any]] | None = None,
) -> list[Sender]:
    """The senders of ``mailbox``'s inbound hits, most frequent first.

    ``since`` keeps the hits received on or after that date in the configured
    zone. Senders with the same count are in address order.
    """
    settings = config.settings()
    counts: Counter[str] = Counter()
    for hit in (store.load_hits() if hits is None else hits).values():
        if hit.get("mailbox") != mailbox or not is_inbound(hit, settings.own_addresses):
            continue
        received = hit.get("receivedDateTime")
        if since and (
            not received
            or parse_ts(received).astimezone(settings.timezone).date() < since
        ):
            continue
        counts[(hit.get("sender") or "").lower()] += 1
    found = []
    for address, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        if not address:
            mark = "none"
        elif address in settings.own_addresses:
            mark = "own"
        elif config.is_notice_sender(address):
            mark = "notice"
        elif config.is_system_sender(address):
            mark = "system"
        else:
            mark = "unlisted"
        found.append(Sender(address, count, mark))
    return found
