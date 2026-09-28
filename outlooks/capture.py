"""Hook captures of the Microsoft 365 connector's output.

The PostToolUse hook (``.claude/hooks/outlook-capture.sh``) writes every
``outlook_email_search`` and ``read_resource`` result verbatim to
``{captured_dir}/{YYYYMMDDTHHMMSS}-{pid}.json``: the call's
``tool_input`` and the connector's ``tool_response``, as text blocks. These are
the only copies of the connector's output that no model has retyped, so the
archive is filled from them (``import_captures``) rather than from files a
model wrote out. A result too large for the context arrives as a note naming
the file it was saved to; the hook copies that file beside the capture as
``{same name}.saved.txt``.

A search capture is one page: hit objects, then a trailer carrying
``moreResults``, ``nextOffset``, and ``totalResultCount``. A read capture is one
message, or the extracted text of one attachment when the uri names one.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from outlooks import config, store

# The hook names files by `date` on the capturing machine, which is taken to run
# in the configured zone (``config.zone()``), the one everything is reported in.


def captured_dir() -> Path:
    """Where the hook writes, from ``[tool.outlooks] captured_dir``."""
    return config.captured_dir()


@dataclass(frozen=True)
class Capture:
    """One hook capture: a search page or a read."""

    path: Path
    tool: str  # "search" or "read"
    args: dict[str, Any]
    blocks: list[Any]  # each text block, parsed as JSON where it is JSON
    at: datetime  # when the hook wrote it (aware)
    # The hook saw output that can't be recovered here (a spilled result whose
    # file is gone, a response of no known shape): no blocks, but not empty.
    lost: bool = False

    @property
    def mailbox(self) -> str | None:
        if self.tool == "search":
            box = self.args.get("mailboxOwnerEmail")
            if box:
                return box.lower()
            return next((store.hit_mailbox(h) for h in self.hits), None)
        return store.uri_owner(self.args.get("uri", ""))

    def is_of(self, mailbox: str) -> bool:
        """Whether this capture is of ``mailbox``.

        With no ``mailbox`` configured the searches are of the signed-in
        account's own mailbox, so a capture that names none (an empty page, a
        uri with no ``owner``) or names any of ``own_addresses`` is of it. With
        one configured, a capture is of the mailbox it names and no other.
        """
        named = self.mailbox
        if named == mailbox.lower():
            return True
        s = config.settings()
        return s.mailbox is None and (named is None or named in s.own_addresses)

    @property
    def hits(self) -> list[dict[str, Any]]:
        return [b for b in self.blocks if isinstance(b, dict) and _is_message(b)]

    @property
    def total(self) -> int | None:
        return next(
            (
                b["totalResultCount"]
                for b in self.blocks
                if isinstance(b, dict) and "totalResultCount" in b
            ),
            None,
        )

    @property
    def more(self) -> bool:
        """Whether the page says more results follow it."""
        return any(
            isinstance(b, dict) and (b.get("moreResults") or b.get("nextOffset"))
            for b in self.blocks
        )

    @property
    def attachment(self) -> bool:
        """A read of one attachment's text rather than of a message."""
        return self.tool == "read" and "/attachments/" in unquote(
            self.args.get("uri", "")
        )

    @property
    def message(self) -> dict[str, Any] | None:
        if self.tool != "read" or self.attachment:
            return None
        return next(
            (b for b in self.blocks if isinstance(b, dict) and _is_message(b)), None
        )

    @property
    def text(self) -> str:
        """An attachment read's text, the blocks joined as the connector sent them."""
        return "".join(b if isinstance(b, str) else json.dumps(b) for b in self.blocks)


def _is_message(block: dict[str, Any]) -> bool:
    return "internetMessageId" in block


def uri_path(uri: str) -> str:
    """A resource uri without its query, percent-decoded, for matching."""
    return unquote(uri.split("?", 1)[0])


def _captured_at(path: Path) -> datetime:
    try:
        stamp = datetime.strptime(path.stem.split("-", 1)[0], "%Y%m%dT%H%M%S")
    except ValueError:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=config.zone())
    return stamp.replace(tzinfo=config.zone())


# Claude Code replaces a result too large for the context with this note and
# saves the connector's output, verbatim, to the named file.
SPILLED = re.compile(r"Output has been saved to (\S+?\.txt)")


def _saved_copy(capture: Path) -> Path:
    """Where the hook copies a spilled result's saved file, beside its capture."""
    return capture.with_name(f"{capture.stem}.saved.txt")


def saved_root() -> Path:
    """Where Claude Code saves an oversized result: its projects directory."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude"
    return (Path(base) / "projects").resolve()


def _saved_file(named: str) -> Path | None:
    """The file a spill note names, when it is one Claude Code saved.

    The note is text a capture carries, so the path in it is followed only
    into :func:`saved_root`; anywhere else it is not the connector's output.
    """
    path = Path(named).resolve()
    return path if path.is_relative_to(saved_root()) else None


def _spilled(response: str, copy: Path) -> list[Any] | None:
    """The blocks of an oversized result: the hook's copy, else the saved file.

    The saved file lives outside the repo and is not kept, so the hook's
    ``copy`` is read first. None when the note names no file of Claude Code's
    and neither is left: the output is lost.
    """
    match = SPILLED.search(response)
    if not match:
        return None
    named = _saved_file(match.group(1))
    source = next((f for f in (copy, named) if f is not None and f.exists()), None)
    if source is None:
        return None
    text = source.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except ValueError:
        return [text]
    if isinstance(data, list) and all(
        isinstance(b, dict) and "text" in b for b in data
    ):
        return _blocks(data, copy)
    return [data]


def _blocks(response: Any, copy: Path) -> list[Any] | None:
    """The response's text blocks; None when its output can't be recovered.

    Only a list of blocks is the connector's answer, so only ``[]`` is an empty
    one: a lost result read as ``[]`` would record an unpaged window as empty.
    """
    if isinstance(response, str):
        return _spilled(response, copy)
    if isinstance(response, dict):
        response = response.get("content")
    if not isinstance(response, list):
        return None
    blocks: list[Any] = []
    for block in response:
        text = block.get("text") if isinstance(block, dict) else None
        if text is None:
            continue
        try:
            blocks.append(json.loads(text))
        except ValueError:
            blocks.append(text)
    return blocks


def read_capture(path: Path) -> Capture | None:
    """Parse one hook capture; None for a file that isn't one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if not isinstance(data, dict) or "tool_input" not in data:
        return None
    name = data.get("tool_name", "")
    if name.endswith("outlook_email_search"):
        tool = "search"
    elif name.endswith("read_resource"):
        tool = "read"
    else:
        return None
    blocks = _blocks(data.get("tool_response"), _saved_copy(path))
    return Capture(
        path,
        tool,
        data["tool_input"],
        blocks or [],
        _captured_at(path),
        lost=blocks is None,
    )


def load_captures(paths: Iterable[Path]) -> tuple[list[Capture], list[Path]]:
    """Every capture under ``paths`` (files or directories) in capture order.

    Returns the captures and the files that were not captures.
    """
    captures: list[Capture] = []
    other: list[Path] = []
    for path in _files(paths):
        capture = read_capture(path)
        if capture is None:
            other.append(path)
        else:
            captures.append(capture)
    captures.sort(key=lambda c: (c.at, c.path.name))
    return captures, other


def _files(paths: Iterable[Path]) -> Iterator[Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(path.rglob("*.json"))
        elif path.exists():
            yield path


@dataclass
class Imported:
    """What ``import_captures`` did, for the command's report."""

    other_mailbox: int = 0
    lost: list[Path] = field(default_factory=list)
    hits_added: int = 0
    hits_known: int = 0
    hits_replaced: list[tuple[str, Path, Path]] = field(default_factory=list)
    messages_written: int = 0
    messages_unchanged: int = 0
    messages_replaced: list[Path] = field(default_factory=list)
    messages_differ: list[Path] = field(default_factory=list)
    attachments_written: int = 0
    attachments_unmatched: list[Path] = field(default_factory=list)
    attachments_refused: list[tuple[Path, str]] = field(default_factory=list)


def import_captures(
    captures: list[Capture],
    mailbox: str | None = None,
    root: Path | None = None,
    replace: bool = False,
) -> Imported:
    """Put every capture from ``mailbox`` (default: the configured one) in the archive.

    Search hits go to tier 1 and reads to tier 2, through the store's write-once
    saves, so re-importing the same captures is a no-op. A read that differs from
    the stored message is listed in ``messages_differ``; with ``replace`` it, and
    any stored hit that differs from a capture of the same copy, is rewritten
    from the capture instead (the one-time repair of model-typed files).
    """
    mailbox = (mailbox or config.mailbox()).lower()
    done = Imported()
    ours = [c for c in captures if c.is_of(mailbox)]
    done.other_mailbox = len(captures) - len(ours)
    done.lost = [c.path for c in ours if c.lost]
    # One save for every page, in capture order, so the first capture of a
    # message is the one kept and the archive is scanned once, not per page.
    found = [hit for c in ours if c.tool == "search" for hit in c.hits]
    if found:
        counts = store.save_hits(found, mailbox=mailbox, root=root)
        done.hits_added, done.hits_known = counts["added"], counts["known"]
    latest = {(hit["internetMessageId"], hit.get("id", "")): hit for hit in found}
    if replace:
        # One pass with the latest capture of each copy, so two captures that
        # disagree don't overwrite each other on every run.
        done.hits_replaced = store.replace_hits(list(latest.values()), mailbox, root)
    messages: dict[str, tuple[Capture, dict[str, Any]]] = {}
    for c in ours:
        if c.message is not None:
            messages[c.message["internetMessageId"]] = (c, c.message)
    for c, message in messages.values():
        try:
            path, written = store.save_message(message, root=root)
        except store.StoreError:
            try:
                if not replace:
                    raise
                store.replace_message(message, root)
                done.messages_replaced.append(c.path)
            except store.StoreError:
                done.messages_differ.append(c.path)
            continue
        if written:
            done.messages_written += 1
        else:
            done.messages_unchanged += 1
    reads = [c for c in ours if c.attachment]
    if not reads:
        return done
    slots = _attachment_slots(
        [*store.load_messages(root).values(), *(m for _, m in messages.values())]
    )
    for c in reads:
        slot = slots.get(uri_path(c.args["uri"])) or slots.get(
            _attachment_id(c.args["uri"]) or ""
        )
        if slot is None:
            done.attachments_unmatched.append(c.path)
            continue
        if any(isinstance(b, dict) and _is_message(b) for b in c.blocks):
            # The connector returned the parent message, not the attachment
            # (its %3D-encoded uri bug): no text to keep.
            done.attachments_refused.append((c.path, "returned the parent message"))
            continue
        message, n = slot
        kind = (message["attachments"][n - 1].get("contentType") or "").lower()
        if kind.startswith("image/"):
            # An image has no text; what came back is the connector's note
            # about it, which must not sit where attachment text goes.
            done.attachments_refused.append((c.path, "an image attachment"))
            continue
        if _is_connector_note(c):
            # An attached email or calendar item whose content the connector
            # does not expose comes back as a note about it, not as text.
            done.attachments_refused.append((c.path, "a connector note, not text"))
            continue
        try:
            done.attachments_written += store.save_attachment_text(
                message, n, c.text, root
            )
        except store.StoreError as e:
            done.attachments_refused.append((c.path, str(e)))
    return done


def _is_connector_note(c: Capture) -> bool:
    """Whether an attachment read returned the connector's note, not the text."""
    blocks = [*c.blocks]
    try:
        blocks.append(json.loads(c.text))
    except (TypeError, ValueError):
        pass
    return any(
        isinstance(b, dict) and "note" in b and "attachment" in b for b in blocks
    )


def _attachment_slots(
    messages: Iterable[dict[str, Any]],
) -> dict[str, tuple[dict[str, Any], int]]:
    """Each attachment uri -> (its message, its position from 1).

    Also keyed by the attachment id alone (``_attachment_id``): the listed uri
    can carry the attachment id in its message segment too, so a reader that
    rebuilt the uri from the message's own id reads the same attachment by a
    different path.
    """
    slots: dict[str, tuple[dict[str, Any], int]] = {}
    for message in messages:
        for n, att in enumerate(message.get("attachments") or [], start=1):
            if att.get("uri"):
                slots[uri_path(att["uri"])] = (message, n)
                if key := _attachment_id(att["uri"]):
                    slots.setdefault(key, (message, n))
    return slots


def _attachment_id(uri: str) -> str | None:
    """The decoded attachment id of an attachment uri, as a slot key."""
    _, sep, rest = uri_path(uri).partition("/attachments/")
    return f"attachment:{rest}" if sep and rest else None
