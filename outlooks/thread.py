"""One message's thread, read back from the archive.

A thread is the full messages that share the message's ``conversationId``. A
hit never read in full has none, so it joins by its normalized subject
(``classify.thread_subject``) and is marked as matched by subject; so does
every message of the thread when the message itself was never read in full.

Read-only, and offline: only the committed archive.

Internal: not part of the Python API a consuming repo may import (the
README's "Python API" lists what is). The CLI is this module's interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from outlooks import classify, store
from outlooks.detail import body_text

# The shortest archive-key prefix a message may be named by.
MIN_PREFIX = 6


class ThreadError(Exception):
    """An id the archive cannot resolve to one message."""


@dataclass(frozen=True)
class Entry:
    """One archived message of the thread, as ``thread`` prints it."""

    mid: str
    received: datetime
    direction: str
    sender: str
    subject: str
    first_line: str
    by_subject: bool
    is_target: bool


def archive(root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Every archived payload by ``internetMessageId``; a full read wins over a hit."""
    return {**store.load_hits(root), **store.load_messages(root)}


def resolve(given: str, payloads: dict[str, dict[str, Any]]) -> str:
    """The ``internetMessageId`` that ``given`` names.

    ``given`` is an ``internetMessageId``, or a prefix of at least
    :data:`MIN_PREFIX` characters of its archive key (``outlooks hash``).
    """
    if given in payloads:
        return given
    prefix = given.lower()
    if len(prefix) < MIN_PREFIX or any(c not in "0123456789abcdef" for c in prefix):
        raise ThreadError(
            f"{given!r} is not in the archive (give an internetMessageId or at "
            f"least {MIN_PREFIX} characters of its archive key)"
        )
    found = sorted(mid for mid in payloads if store.id_hash(mid).startswith(prefix))
    if not found:
        raise ThreadError(f"no archived message has a key starting {prefix!r}")
    if len(found) > 1:
        keys = ", ".join(store.id_hash(mid) for mid in found)
        raise ThreadError(f"{prefix!r} starts {len(found)} keys ({keys}); give more")
    return found[0]


def _first_line(payload: dict[str, Any], mail: classify.Mail) -> str:
    text = body_text(payload) if mail.full else mail.text
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def thread(mid: str, payloads: dict[str, dict[str, Any]]) -> list[Entry]:
    """The archived messages of ``mid``'s thread, oldest first."""
    mails = {k: classify.view(p) for k, p in payloads.items()}
    target = mails[mid]
    subject = classify.thread_subject(target.subject)
    conversation = target.conversation_id
    entries: list[Entry] = []
    for k, mail in mails.items():
        if conversation and mail.conversation_id:
            by_subject = False
            joined = mail.conversation_id == conversation
        else:
            by_subject = k != mid
            joined = k == mid or (
                bool(subject) and classify.thread_subject(mail.subject) == subject
            )
        if not joined:
            continue
        entries.append(
            Entry(
                mid=k,
                received=mail.received,
                direction=classify.direction(mail.sender, list(mail.recipients)),
                sender=mail.sender,
                subject=mail.subject,
                first_line=_first_line(payloads[k], mail),
                by_subject=by_subject,
                is_target=k == mid,
            )
        )
    return sorted(entries, key=lambda e: (e.received, e.mid))
