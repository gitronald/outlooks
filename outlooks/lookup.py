"""A lookup's hits, split into archived and to-read, and its timelines checked.

A lookup (the skill's read-only mode) runs searchers that save their result
pages as ``{scratch}/{name}-*page-*.json``. :func:`split` merges those pages on
``internetMessageId``, keeps the hits that match the lookup's terms, numbers
them oldest-first, and writes ``{scratch}/{name}-{nn}.timeline.json`` for every
hit already in the archive; the rest are the readers' worklist. :func:`check`
then compares every timeline file (written here or by a reader) with the
archive. Neither writes anything under the archive.

Internal: not part of the Python API a consuming repo may import (the
README's "Python API" lists what is). The CLI is this module's interface.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from outlooks import config, store

SYSTEM_PAIR_SECONDS = 60


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def local_day(received_utc: str) -> str:
    """A UTC timestamp's date in the configured zone, ``YYYY-MM-DD``."""
    return _ts(received_utc).astimezone(config.zone()).date().isoformat()


def is_system(sender: str) -> bool:
    return sender.lower() in config.settings().system_senders


def direction(sender: str, to: list[str]) -> str:
    """``out`` for our own mail and for system mail not sent to us, else ``in``."""
    own = config.settings().own_addresses
    s = sender.lower()
    if s in own:
        return "out"
    if is_system(s):
        return "in" if any(t.lower() in own for t in to) else "out"
    return "in"


def timeline_from_archive(path: Path) -> dict[str, Any]:
    """The timeline record a reader would write, taken from an archived message."""
    m = json.loads(path.read_text(encoding="utf-8"))
    sender = (m.get("sender") or {}).get("address", "")
    to = [r.get("address", "") for r in m.get("toRecipients", [])]
    return {
        "file": str(path),
        "received_utc": m["receivedDateTime"],
        "date_local": local_day(m["receivedDateTime"]),
        "direction": direction(sender, to),
        "sender": sender,
        "to": to,
        "subject": m.get("subject", ""),
        "internet_message_id": m["internetMessageId"],
        "weblink": m.get("webLink", ""),
        "attachments": [a.get("name", "") for a in m.get("attachments", [])],
        "summary": m.get("bodyPreview", ""),
        "system": is_system(sender),
        "source": "archive",
    }


@dataclass
class Split:
    pages: int = 0
    known: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    to_read: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    batches: list[Path] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.known) + len(self.to_read)


def _matches(hit: dict[str, Any], terms: list[str], summary: bool) -> bool:
    fields = [hit.get("sender", ""), *(hit.get("recipients") or []), hit.get("subject")]
    if summary:
        fields.append(hit.get("summary"))
    hay = " ".join(f or "" for f in fields).lower()
    return any(t in hay for t in terms)


def split(
    name: str,
    terms: list[str],
    *,
    since: str | None = None,
    match_summary: bool = True,
    batches: Path | None = None,
    per: int = 2,
    scratch: Path | None = None,
    archive: Path | None = None,
) -> Split:
    """Merge a lookup's pages, keep the matching hits, and split them.

    A hit matches when a term (case-insensitive) is in its sender, recipients,
    or subject, and with ``match_summary`` its summary too (people who are
    written *about*, not only those writing). A query search's pages drift into
    unrelated mail after the real matches, which is why the terms filter at all.

    A system sender's notification to the mailbox often names the person only in
    its body, but goes out within a minute of the matching notice to them; any
    such hit to us within :data:`SYSTEM_PAIR_SECONDS` of a matched system hit is
    kept too.
    """
    scratch = scratch or config.scratch_dir()
    archive = archive or store.archive_root()
    own = config.settings().own_addresses
    terms = [t.lower() for t in terms]
    for _, stale in timelines(scratch, name):
        stale.unlink()  # numbering is recomputed below; an old file would mislead

    out = Split()
    files = sorted(scratch.glob(f"{name}-*page-*.json"))
    out.pages = len(files)
    hits: dict[str, dict[str, Any]] = {}
    seen: list[dict[str, Any]] = []
    for f in files:
        for h in json.loads(f.read_text(encoding="utf-8")):
            seen.append(h)
            if _matches(h, terms, match_summary):
                hits.setdefault(h["internetMessageId"], h)
    system_times = [
        _ts(h["receivedDateTime"])
        for h in hits.values()
        if is_system(h.get("sender") or "")
    ]
    for h in seen:
        if not is_system(h.get("sender") or "") or h["internetMessageId"] in hits:
            continue
        if not any(r.lower() in own for r in h.get("recipients") or []):
            continue
        t = _ts(h["receivedDateTime"])
        if any(
            abs((t - p).total_seconds()) <= SYSTEM_PAIR_SECONDS for p in system_times
        ):
            hits[h["internetMessageId"]] = h

    ordered = sorted(hits.values(), key=lambda h: h["receivedDateTime"])
    if since:
        ordered = [h for h in ordered if h["receivedDateTime"][:10] >= since]
    messages = archive / "messages"
    read_lines: list[str] = []
    for i, h in enumerate(ordered, 1):
        nn = f"{i:02d}"
        path = messages / f"{store.id_hash(h['internetMessageId'])}.json"
        if path.exists():
            timeline = timeline_from_archive(path)
            target = scratch / f"{name}-{nn}.timeline.json"
            target.write_text(json.dumps(timeline, indent=2), encoding="utf-8")
            out.known.append((nn, h))
        else:
            out.to_read.append((nn, h))
            read_lines.append(f"{name}-{nn} {h['uri']}")
    if batches and read_lines:
        batches.mkdir(parents=True, exist_ok=True)
        for b, i in enumerate(range(0, len(read_lines), per), 1):
            path = batches / f"{name}-batch-{b:02d}.txt"
            path.write_text("\n".join(read_lines[i : i + per]) + "\n", "utf-8")
            out.batches.append(path)
    return out


@dataclass(frozen=True)
class Checked:
    nn: str
    received: str
    day: str
    ok: bool
    direction: str
    system: bool
    texts: int
    subject: str


def timelines(scratch: Path, name: str) -> list[tuple[str, Path]]:
    """``(nn, path)`` for each ``{name}-{nn}.timeline.json``, in number order.

    ``nn`` is two digits, or more past 99; a longer name that starts with
    ``{name}-`` (another lookup's) is not one of them.
    """
    own = re.compile(re.escape(name) + r"-(\d+)\.timeline\.json")
    found = [
        (m.group(1), f)
        for f in scratch.glob(f"{name}-*.timeline.json")
        if (m := own.fullmatch(f.name))
    ]
    return sorted(found, key=lambda pair: int(pair[0]))


def check(
    name: str, *, scratch: Path | None = None, archive: Path | None = None
) -> list[Checked]:
    """Every ``{name}-{nn}.timeline.json`` against the archive.

    A row is ok when its message is archived and its local date matches the
    one the archive's ``receivedDateTime`` implies.
    """
    scratch = scratch or config.scratch_dir()
    messages = (archive or store.archive_root()) / "messages"
    rows = []
    for nn, f in timelines(scratch, name):
        t = json.loads(f.read_text(encoding="utf-8"))
        h = store.id_hash(t["internet_message_id"])
        stored = messages / f"{h}.json"
        received = ""
        day = ""
        if stored.exists():
            raw = json.loads(stored.read_text(encoding="utf-8"))
            received = raw.get("receivedDateTime") or ""
            day = local_day(received) if received else ""
        claimed = t.get("date_local")
        rows.append(
            Checked(
                nn=nn,
                received=received,
                day=day,
                ok=stored.exists() and day == claimed,
                direction=t.get("direction", ""),
                system=bool(t.get("system")),
                texts=len(list(messages.glob(f"{h}.*.txt"))),
                subject=t.get("subject", ""),
            )
        )
    return rows
