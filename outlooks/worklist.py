"""Reader worklists for a full read of a range, and the attachment-text audit.

:func:`read_worklist` is window mode's bulk read: every message a window pull
listed in the range that has neither a captured read nor an archived file,
numbered oldest-first and written ``per`` to a batch file. :func:`audit` checks
the archived messages of a period for missing attachment texts. Neither writes
anything under the archive.

Internal: not part of the Python API a consuming repo may import (the
README's "Python API" lists what is). The CLI is this module's interface.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from outlooks import config, store
from outlooks.capture import Capture, captured_dir, load_captures
from outlooks.coverage import (
    Window,
    is_window,
    mass_copies,
    mass_sends_csv,
    parse_ts,
    read_rows,
    windows,
)

# Calendar invites always come back as a binary-attachment note.
CALENDAR = ("text/calendar", "application/ics")
NO_TEXT = ("message/rfc822", "application/zip", "multipart/signed", *CALENDAR)
ATTACHMENT_ID = re.compile(r"/attachments/([^?]+)")
# How long the connector must have been quiet before worklists are rebuilt.
QUIET = timedelta(seconds=90)


class WorklistError(Exception):
    """A range that cannot be worked yet (a short re-pull, a malformed bound)."""


def bound(day: str) -> datetime:
    """A ``YYYY-MM-DD`` as that day's local midnight."""
    try:
        return datetime.fromisoformat(day).replace(tzinfo=config.zone())
    except ValueError:
        raise WorklistError(f"{day!r} is not a YYYY-MM-DD date") from None


def _received(hit: dict[str, Any]) -> datetime:
    return parse_ts(hit["receivedDateTime"])


@dataclass
class Worklist:
    listed: int = 0
    todo: list[dict[str, Any]] = field(default_factory=list)
    batches: list[Path] = field(default_factory=list)
    orphans: int = 0
    mass_copies: int = 0  # copies of a mass send, left out of todo


def short_pulls(captures: list[Capture], mailbox: str, lo: datetime, hi: datetime):
    """Bounds once pulled complete whose newest pull in the range is short.

    ``import`` says a re-pull's bounds are "already recorded", not that this pull
    got every page; a first pull that came back short is ``import``'s INCOMPLETE
    line, and a one-page probe never counts.
    """
    newest: dict[tuple[datetime, datetime | None], Window] = {}
    once_complete: set[tuple[datetime, datetime | None]] = set()
    for w in windows(captures, mailbox):
        if w.after < hi and (w.before is None or w.before > lo):
            key = (w.after, w.before)
            if w.complete:
                once_complete.add(key)
            if key not in newest or (w.last or w.after) > (newest[key].last or w.after):
                newest[key] = w
    return [w for k, w in newest.items() if k in once_complete and not w.complete]


def read_worklist(
    after: str,
    before: str,
    out: Path,
    *,
    per: int = 10,
    name: str = "msg",
    mailbox: str | None = None,
    now: datetime | None = None,
) -> Worklist:
    """Write ``out/batch-{bb}.txt`` worklists for the unread mail in the range.

    Bounds are local dates, ``before`` exclusive. Takes every hit in the range
    from window-pull captures only (a lookup page is not a listing of the range),
    keeps each message's uri from its newest capture (a uri goes stale when mail
    is moved), and drops every message with a captured read or an archived file,
    and every copy of a mass send in ``mass-sends.csv`` but its exemplar.
    Earlier batch files in ``out`` are replaced. Refuses, writing nothing, when a
    re-pull of a covered window is short, or when the newest capture was written
    under ``QUIET`` before ``now``.
    """
    lo, hi = bound(after), bound(before)
    box = mailbox or config.mailbox()
    captures, _ = load_captures(sorted(captured_dir().glob("*.json")))
    listed: dict[str, dict[str, Any]] = {}
    read: set[str] = set()
    for c in captures:
        if not c.is_of(box):
            continue
        if c.message is not None:
            read.add(c.message["internetMessageId"])
        if not is_window(c):
            continue
        for hit in c.hits:
            if lo <= _received(hit) < hi:
                listed[hit["internetMessageId"]] = hit  # newest wins

    short = short_pulls(captures, box, lo, hi)
    if short:
        lines = [
            f"INCOMPLETE pull {w.after} to {w.before}: "
            + (
                "its last page is missing"
                if w.total is None
                else f"offsets {sorted(set(range(w.total)) - w.offsets)[:3]}... missing"
            )
            for w in short
        ]
        raise WorklistError(
            "\n".join(
                [
                    *lines,
                    "re-page these windows, then import and rerun; "
                    "no worklists written",
                ]
            )
        )

    # A capture this fresh means a reader (or another session) is still calling
    # the connector; rebuilding now would replace batch files it may still be
    # working from, and count its unfinished reads as still to do. The command
    # cannot tell which session it runs in, so a finished lookup of this one
    # looks the same: any call counts, of any mailbox and any tool. Its age is
    # from the file's write time: the stamp in its name is the machine's local
    # time, which need not be the configured zone.
    written = [(c.path.stat().st_mtime, c) for c in captures]
    if written:
        mtime, newest = max(written, key=lambda pair: pair[0])
        age = (now or datetime.now(UTC)) - datetime.fromtimestamp(mtime, UTC)
        if age < QUIET:
            raise WorklistError(
                f"last capture {int(age.total_seconds())}s ago ({newest.tool}): a "
                "reader or another session called the connector; wait until every "
                f"reader has returned and {QUIET.seconds}s have passed, then "
                "import and rerun; no worklists written"
            )

    messages = store.archive_root() / "messages"
    result = Worklist(listed=len(listed))
    skipped = mass_copies(listed, read_rows(mass_sends_csv()))
    result.mass_copies = len(skipped)
    result.todo = sorted(
        (
            hit
            for key, hit in listed.items()
            if key not in read
            and key not in skipped
            and not (messages / f"{store.id_hash(key)}.json").exists()
        ),
        key=_received,
    )
    archived = {
        key
        for key, hit in store.load_hits().items()
        if hit["mailbox"] == box and lo <= _received(hit) < hi
    }
    result.orphans = len(archived - listed.keys())

    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("batch-*.txt"):
        old.unlink()
    for i in range(0, len(result.todo), per):
        lines = [
            f"{name}-{i + j + 1:03d} {hit['uri']}\n"
            for j, hit in enumerate(result.todo[i : i + per])
        ]
        path = out / f"batch-{i // per + 1:02d}.txt"
        path.write_text("".join(lines), encoding="utf-8")
        result.batches.append(path)
    return result


def attempted_ids(folder: Path | None = None) -> set[str]:
    """Attachment ids any captured ``read_resource`` call asked for."""
    ids: set[str] = set()
    for path in (folder or captured_dir()).glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            uri = payload.get("tool_input", {}).get("uri", "")
        except (ValueError, AttributeError):
            continue
        match = ATTACHMENT_ID.search(uri or "")
        if match:
            ids.add(unquote(match.group(1)))
    return ids


@dataclass
class Audit:
    counts: Counter[str] = field(default_factory=Counter)
    missing: Counter[str] = field(default_factory=Counter)
    listed: list[str] = field(default_factory=list)
    untried: list[tuple[str, str, str]] = field(default_factory=list)
    image_slots: list[str] = field(default_factory=list)
    bad: list[str] = field(default_factory=list)


def audit(prefix: str, *, archive: Path | None = None) -> Audit:
    """Attachment texts for the archived messages received in ``prefix``.

    Counts the text-bearing attachments (not inline, not ``image/*``) and how
    many have their ``{id-hash}.{n}.txt``. A missing one that is not an attached
    email, a zip, an S/MIME signature, or a calendar invite is listed by name,
    since those are the ones a re-read might fill; one no capture ever tried to
    read (signatures and invites aside) is a reader's skip, not the connector's
    refusal. Also lists a text
    file sitting in an image's slot and any message file that does not parse.
    """
    messages = (archive or store.archive_root()) / "messages"
    tried = attempted_ids()
    result = Audit()
    for path in sorted(messages.glob("*.json")):
        try:
            message = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            result.bad.append(path.name)
            continue
        if not str(message.get("receivedDateTime", "")).startswith(prefix):
            continue
        result.counts["messages"] += 1
        for n, att in enumerate(message.get("attachments") or [], 1):
            ctype = str(att.get("contentType", ""))
            has_text = (messages / f"{path.stem}.{n}.txt").exists()
            if att.get("isInline") or ctype.startswith("image/"):
                if has_text:
                    result.image_slots.append(f"{path.stem}.{n}")
                continue
            result.counts["text-bearing"] += 1
            if has_text:
                result.counts["with text"] += 1
                continue
            result.missing[ctype or "?"] += 1
            if ctype not in NO_TEXT:
                result.listed.append(f"{path.stem}.{n}  {ctype}  {att.get('name')}")
            match = ATTACHMENT_ID.search(att.get("uri") or "")
            if ctype not in ("multipart/signed", *CALENDAR) and not (
                match and unquote(match.group(1)) in tried
            ):
                result.untried.append((f"{path.stem}.{n}", ctype, att.get("uri")))
    return result
