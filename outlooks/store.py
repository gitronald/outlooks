"""The committed Outlook archive (``config.archive_dir()``, ``data/outlook/`` here).

Two immutable tiers, filled from the Microsoft 365 connector's output:

- ``hits/{mailbox}/{YYYY-MM}.jsonl``: every search hit ever listed, one JSON
  object per line, deduped on ``internetMessageId``. Hits are cheap (25 to a
  tool call) and already carry what a timeline needs.
- ``messages/{id-hash}.json``: full ``read_resource`` payloads, plus
  ``{id-hash}.{n}.txt`` for each attachment's extracted text. Only the messages
  that matter are read in full.

The connector reports some fields that change between two reads of the same
message (read state, flag, folder), so those are dropped before saving. A second
save of a message is then a no-op when nothing else differs, and a refusal when
something does: the store never rewrites what it holds.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from outlooks import config

# The names a consuming repo may import; a change to one is a changelog
# entry. The write paths (``save_*``, ``replace_*``) are not: a repo fills the
# archive through ``outlooks import``, never by calling the store.
__all__ = [
    "StoreError",
    "archive_root",
    "id_hash",
    "load_hits",
    "load_messages",
    "newest_hit",
    "stable",
]

# Fields that change between reads of an unchanged message. `offset` is the
# hit's position in the page it came from.
VOLATILE = ("isRead", "flag", "categories", "parentFolderId", "offset")


class StoreError(Exception):
    """A payload the store cannot take, or one that contradicts what it holds."""


def archive_root() -> Path:
    """The committed mailbox archive, from ``[tool.outlooks] archive_dir``."""
    return config.archive_dir()


def _root(root: Path | None) -> Path:
    return archive_root() if root is None else root


def id_hash(internet_message_id: str) -> str:
    """A short, stable, filename-safe key for an ``internetMessageId``."""
    return hashlib.sha256(internet_message_id.encode("utf-8")).hexdigest()[:16]


def stable(payload: dict[str, Any]) -> dict[str, Any]:
    """The payload without the fields that change between reads."""
    return {k: v for k, v in payload.items() if k not in VOLATILE}


def _message_id(payload: dict[str, Any]) -> str:
    mid = payload.get("internetMessageId")
    if not mid:
        raise StoreError("the payload has no internetMessageId")
    return mid


def _dump(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _write_once(path: Path, content: str) -> bool:
    """Write ``content`` unless ``path`` holds it already; refuse a different one.

    Line endings don't count as a difference: a second read of an attachment can
    come back with CRLF where the first had LF (``read_text`` already reads the
    stored side with universal newlines).
    """
    if path.exists():
        if path.read_text(encoding="utf-8") == _lf(content):
            return False
        raise StoreError(f"{path} already holds a different version; not rewriting it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def save_message(
    message: dict[str, Any], texts: list[str] | None = None, root: Path | None = None
) -> tuple[Path, bool]:
    """Store one full message and its attachment texts; returns (path, written).

    ``texts`` are in the order of the message's ``attachments`` list, saved as
    ``{id-hash}.1.txt``, ``{id-hash}.2.txt``, ...
    """
    _require_full(message)
    key = id_hash(_message_id(message))
    path = _root(root) / "messages" / f"{key}.json"
    written = _write_once(path, _dump(stable(message)))
    for n, text in enumerate(texts or [], start=1):
        written |= save_attachment_text(message, n, text, root)
    return path, written


def _require_full(message: Any) -> None:
    if (
        not isinstance(message, dict)
        or "body" not in message
        or not isinstance(message.get("sender"), dict)
    ):
        raise StoreError(
            "not a full read_resource message (no body or sender); "
            "save search hits with --hits"
        )


def save_attachment_text(
    message: dict[str, Any], n: int, text: str, root: Path | None = None
) -> bool:
    """Store the extracted text of a message's ``n``-th attachment (from 1)."""
    if not text.strip():
        raise StoreError(f"attachment text {n} is empty; not saving it")
    key = id_hash(_message_id(message))
    return _write_once(_root(root) / "messages" / f"{key}.{n}.txt", text)


def replace_message(message: dict[str, Any], root: Path | None = None) -> bool:
    """Rewrite a stored message with the connector's own copy (the repair path).

    The one exception to write-once, for files a model typed out and got wrong:
    ``message`` must be the connector's output (a hook capture), never a copy.
    Returns whether the stored file changed.
    """
    _require_full(message)
    path = _root(root) / "messages" / f"{id_hash(_message_id(message))}.json"
    content = _dump(stable(message))
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def uri_owner(uri: str) -> str | None:
    """The mailbox a connector uri names, from its ``owner`` parameter."""
    owner = parse_qs(urlparse(uri).query).get("owner")
    return owner[0].lower() if owner else None


def hit_mailbox(hit: dict[str, Any]) -> str | None:
    """The mailbox a hit was listed from, from its uri's ``owner`` parameter."""
    return uri_owner(hit.get("uri", ""))


def save_hits(
    page: list[dict[str, Any]], mailbox: str | None = None, root: Path | None = None
) -> dict[str, int]:
    """Append a search page to tier 1; returns counts of ``added`` and ``known``.

    The page's trailing paging item (``nextOffset``/``nextCursor``) is skipped. A
    hit already in the store is left as first saved.
    """
    base = _root(root)
    added = known = 0
    by_file: dict[Path, list[dict[str, Any]]] = {}
    for hit in page:
        if "internetMessageId" not in hit:
            continue  # the paging trailer
        box = (mailbox or hit_mailbox(hit) or "").lower()
        if not box:
            raise StoreError(
                f"no mailbox for hit {hit['internetMessageId']}; pass --mailbox"
            )
        month = hit.get("receivedDateTime", "")[:7]
        if len(month) != 7:
            raise StoreError(f"hit {hit['internetMessageId']} has no receivedDateTime")
        by_file.setdefault(base / "hits" / box / f"{month}.jsonl", []).append(hit)
    # A message is known if any month file of its mailbox holds it: a stored
    # hit can sit in another month than a later copy's receivedDateTime says.
    # Each month file of those mailboxes is read once, for both uses.
    stored = {
        path: list(_read_jsonl(path))
        for box in {path.parent.name for path in by_file}
        for path in sorted((base / "hits" / box).glob("*.jsonl"))
    }
    seen: dict[str, set[str]] = {path.parent.name: set() for path in by_file}
    for path, rows in stored.items():
        seen[path.parent.name].update(r["internetMessageId"] for r in rows)
    for path, hits in by_file.items():
        rows = stored.get(path, [])
        box_seen = seen[path.parent.name]
        added_here = 0
        for hit in hits:
            if hit["internetMessageId"] in box_seen:
                known += 1
                continue
            box_seen.add(hit["internetMessageId"])
            rows.append(stable(hit))
            added_here += 1
        added += added_here
        if not added_here:
            continue
        _write_jsonl(path, rows)
    return {"added": added, "known": known}


def replace_hits(
    page: list[dict[str, Any]], mailbox: str, root: Path | None = None
) -> list[tuple[str, Path, Path]]:
    """Rewrite stored hits that differ from the connector's copy (the repair path).

    Like ``replace_message``, only for hook captures. A stored hit is replaced
    only by a capture of the same copy (same ``id``): two folder copies of one
    message differ legitimately. The corrected hit lands in the month file of
    its ``receivedDateTime``, and a stored row filed under a wrong month is
    dropped, as is a duplicate of it. Returns (internetMessageId, old file, new
    file) per row rewritten or dropped.
    """
    box = _root(root) / "hits" / mailbox.lower()
    files = {path: list(_read_jsonl(path)) for path in sorted(box.glob("*.jsonl"))}
    # Rows are indexed by message id and dropped by identity, so a large repair
    # never rescans a month file per hit.
    where: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    for path, rows in files.items():
        for r in rows:
            where.setdefault(r["internetMessageId"], []).append((path, r))
    changes: list[tuple[str, Path, Path]] = []
    dirty: set[Path] = set()
    dropped: set[int] = set()
    for hit in page:
        mid = hit.get("internetMessageId")
        if mid not in where:
            continue
        new = stable(hit)
        home = box / f"{hit['receivedDateTime'][:7]}.jsonl"
        stale = [
            (path, r)
            for path, r in where[mid]
            if r.get("id") == hit.get("id") and not (path == home and r == new)
        ]
        if not stale:
            continue
        for path, r in stale:
            dropped.add(id(r))
            dirty.add(path)
            changes.append((mid, path, home))
        kept = [(path, r) for path, r in where[mid] if id(r) not in dropped]
        if not any(path == home and r == new for path, r in kept):
            files.setdefault(home, []).append(new)
            kept.append((home, new))
            dirty.add(home)
        where[mid] = kept
    for path in dirty:
        rows = [r for r in files[path] if id(r) not in dropped]
        if not rows:
            path.unlink(missing_ok=True)
            continue
        _write_jsonl(path, rows)
    return changes


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    rows.sort(key=lambda r: (r.get("receivedDateTime", ""), r["internetMessageId"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def load_hits(root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Every stored hit by ``internetMessageId``, each tagged with its ``mailbox``."""
    hits: dict[str, dict[str, Any]] = {}
    for path in sorted((_root(root) / "hits").glob("*/*.jsonl")):
        for hit in _read_jsonl(path):
            hits.setdefault(
                hit["internetMessageId"], {**hit, "mailbox": path.parent.name}
            )
    return hits


def load_messages(root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Every stored full message by ``internetMessageId``."""
    messages: dict[str, dict[str, Any]] = {}
    for path in sorted((_root(root) / "messages").glob("*.json")):
        message = json.loads(path.read_text(encoding="utf-8"))
        messages[message["internetMessageId"]] = message
    return messages


def newest_hit(root: Path | None = None, mailbox: str | None = None) -> str | None:
    """The latest ``receivedDateTime`` in tier 1 (for one mailbox, if given)."""
    latest = None
    for hit in load_hits(root).values():
        if mailbox and hit["mailbox"] != mailbox.lower():
            continue
        received = hit.get("receivedDateTime")
        if received and (latest is None or received > latest):
            latest = received
    return latest
