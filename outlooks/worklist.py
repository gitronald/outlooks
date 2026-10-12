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
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from outlooks import config, store
from outlooks.attachments import attachment_id, is_image
from outlooks.capture import (
    Capture,
    load_captures,
    read_capture,
)
from outlooks.coverage import (
    Window,
    is_window,
    mass_copies,
    mass_sends_csv,
    read_rows,
    windows,
)
from outlooks.detail import printable
from outlooks.timestamps import parse_ts

# Calendar invites always come back as a binary-attachment note.
CALENDAR = ("text/calendar", "application/ics")
NO_TEXT = ("message/rfc822", "application/zip", "multipart/signed", *CALENDAR)
# What the connector returns, lowercased, for an attachment it has no text for.
BINARY_NOTE = "binary attachment"
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
    if per < 1:
        raise WorklistError(f"per must be at least 1, not {per}")
    lo, hi = bound(after), bound(before)
    box = mailbox or config.archive_mailbox()
    captures, _ = load_captures(sorted(config.captured_dir().glob("*.json")))
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

    result = Worklist(listed=len(listed))
    skipped = mass_copies(listed, read_rows(mass_sends_csv()))
    result.mass_copies = len(skipped)
    result.todo = sorted(
        (
            hit
            for key, hit in listed.items()
            if key not in read
            and key not in skipped
            and not store.message_path(key).exists()
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


@dataclass
class Round:
    """The last round of reader subagents, from their captured reads."""

    reads: int
    start: datetime
    end: datetime
    # agent id -> the reads it made while another of its own was in flight
    parallel: dict[str, int]
    readers: int

    @property
    def per_minute(self) -> float:
        minutes = (self.end - self.start).total_seconds() / 60
        return self.reads / minutes if minutes > 0 else float(self.reads)


# A call's start, when the hook was not told how long it ran: a read finishing
# this soon after another of the same reader's was made beside it, since a
# reader that waits for each result takes seconds to make its next call.
UNTIMED = timedelta(seconds=1)


def last_round(captures: list[Capture]) -> Round | None:
    """The newest round of reads by subagents, and which of them ran in parallel.

    Each read runs from its capture's write time back by its ``duration_ms``. A
    round is the readers whose spans overlap, one after another, ending with the
    newest; a read that starts before another of the same reader's has ended
    was made in parallel with it. Reads by the main session, which carry no
    ``agent_id``, are left out, and a call captured twice counts once. None
    when no read carries an ``agent_id``.
    """
    calls: dict[str, list[tuple[datetime, datetime]]] = {}
    seen: set[str] = set()
    for c in captures:
        if c.tool != "read" or c.agent_id is None:
            continue
        if c.tool_use_id is not None:
            if c.tool_use_id in seen:
                continue
            seen.add(c.tool_use_id)
        end = datetime.fromtimestamp(c.path.stat().st_mtime, UTC)
        ran = (
            UNTIMED if c.duration_ms is None else timedelta(milliseconds=c.duration_ms)
        )
        calls.setdefault(c.agent_id, []).append((end - ran, end))
    if not calls:
        return None
    spans = sorted(
        (min(s for s, _ in own), max(e for _, e in own), agent)
        for agent, own in calls.items()
    )
    group: list[str] = []
    until = spans[0][1]
    for start, end, agent in spans:
        if start > until:
            group = []
        group.append(agent)
        until = max(until, end)
    reads = [span for agent in group for span in calls[agent]]
    parallel: dict[str, int] = {}
    for agent in group:
        busy = None
        for start, end in sorted(calls[agent], key=lambda span: span[1]):
            if busy is not None and start < busy:
                parallel[agent] = parallel.get(agent, 0) + 1
            busy = end if busy is None else max(busy, end)
    return Round(
        reads=len(reads),
        start=min(s for s, _ in reads),
        end=max(e for _, e in reads),
        parallel=parallel,
        readers=len(group),
    )


def line_uri(worklist: Path, name: str) -> str:
    """The uri on the worklist line named ``name``, exactly as written.

    A reader passes this to ``read_resource`` instead of copying the uri out of
    a listing of the whole file: the ids are long and differ near the end, so a
    retyped one reads a neighbouring message. A name not in the file is refused
    with the names it does hold, so a call past the last line reads nothing.
    """
    try:
        text = worklist.read_text(encoding="utf-8")
    except OSError as e:
        raise WorklistError(f"{worklist}: {e.strerror or e}") from None
    names: list[str] = []
    for line in text.splitlines():
        key, _, uri = line.strip().partition(" ")
        if key == name and uri.strip():
            return uri.strip()
        if key:
            names.append(key)
    held = f"{names[0]} to {names[-1]}" if names else "none"
    raise WorklistError(f"no line {name} in {worklist} (its names: {held})")


def _is_binary_note(block: Any) -> bool:
    """Whether a result block is the connector's note for a binary attachment."""
    return (
        isinstance(block, dict)
        and "attachment" in block
        and str(block.get("note") or "").lower().startswith(BINARY_NOTE)
    )


def attachment_reads(folder: Path | None = None) -> tuple[set[str], set[str]]:
    """Attachment ids captured reads asked for, and those that got the binary note.

    A re-read of one that got the note returns the note again, so it never gets
    text. The note is matched as the connector's block, not as a phrase, so
    text that mentions it (or the parent message read back instead) is not one.
    """
    tried: set[str] = set()
    binary: set[str] = set()
    for path in (folder or config.captured_dir()).glob("*.json"):
        c = read_capture(path)
        uri = c.args.get("uri") if c and isinstance(c.args, dict) else None
        att_id = attachment_id(uri) if isinstance(uri, str) else None
        if c is None or att_id is None:
            continue
        tried.add(att_id)
        if any(_is_binary_note(b) for b in c.blocks):
            binary.add(att_id)
    return tried, binary


@dataclass
class Audit:
    counts: Counter[str] = field(default_factory=Counter)
    missing: Counter[str] = field(default_factory=Counter)
    binary: Counter[str] = field(default_factory=Counter)
    listed: list[str] = field(default_factory=list)
    untried: list[tuple[str, str, str]] = field(default_factory=list)
    image_slots: list[str] = field(default_factory=list)
    bad: list[str] = field(default_factory=list)


def audit(prefix: str, *, root: Path | None = None) -> Audit:
    """Attachment texts for the archived messages received in ``prefix``.

    Counts the text-bearing attachments (not inline, not an image by
    :func:`~outlooks.attachments.is_image`) and how many have their
    ``{id-hash}.{n}.txt``. One without text that a read got the connector's
    binary note for is counted under ``binary``, by type, and not as missing: a
    re-read returns the same note. Any other missing one that is not an
    attached email, a zip, an S/MIME signature, or a calendar invite is listed
    by name, since those are the ones a re-read might fill; one no capture ever
    tried to read (signatures and invites aside) is a reader's skip, not the
    connector's refusal. Also lists a text file sitting in an image's slot and
    any message file that does not parse.
    """
    messages = (root or config.archive_dir()) / "messages"
    tried, binary = attachment_reads()
    result = Audit()
    for path in sorted(messages.glob("*.json")):
        try:
            message = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(message, dict):
                raise ValueError("not a message object")
            attachments = message.get("attachments") or []
            if not isinstance(attachments, list) or not all(
                isinstance(att, dict) for att in attachments
            ):
                raise ValueError("attachments is not a list of objects")
        except ValueError:
            result.bad.append(path.name)
            continue
        if not str(message.get("receivedDateTime", "")).startswith(prefix):
            continue
        result.counts["messages"] += 1
        for n, att in enumerate(attachments, 1):
            ctype = str(att.get("contentType", ""))
            has_text = (messages / f"{path.stem}.{n}.txt").exists()
            if att.get("isInline") or is_image(att):
                if has_text:
                    result.image_slots.append(f"{path.stem}.{n}")
                continue
            result.counts["text-bearing"] += 1
            if has_text:
                result.counts["with text"] += 1
                continue
            att_id = attachment_id(att.get("uri") or "")
            if att_id in binary:
                result.binary[ctype or "?"] += 1
                continue
            result.missing[ctype or "?"] += 1
            if ctype not in NO_TEXT:
                name = printable(str(att.get("name")))
                result.listed.append(f"{path.stem}.{n}  {printable(ctype)}  {name}")
            if ctype not in ("multipart/signed", *CALENDAR) and att_id not in tried:
                result.untried.append((f"{path.stem}.{n}", ctype, att.get("uri")))
    return result
