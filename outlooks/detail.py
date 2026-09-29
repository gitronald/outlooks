"""Read an archived message back for display.

``outlooks import`` keeps the connector's payload as it came, bodies
and all. This turns one of those payloads into the fields a reader wants beside
a correspondence row — who it went to, what it carried, and the message itself —
without the caller having to know the connector's JSON shape.

The body comes back as **text**, never HTML: the archive holds an unknown
sender's markup, and a page that pasted it into its own DOM would run their
scripts and load their tracking pixels. The gateway banner and the URL-defense
wrappers are cut first (``render.clean_body``), so what is left is the message
as it was written, quoted chain included — that chain is often the only record
of a thread whose earlier messages were never archived.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from outlooks import store
from outlooks.render import clean_body, unwrap_text_links

# The names a consuming repo may import; a change to one is a changelog entry.
__all__ = [
    "body_text",
    "detail",
    "details_by_id",
]

# A block boundary is a line break, opening tag as well as closing: Outlook and
# Gmail both nest a message's paragraphs as sibling <div>s inside one wrapper
# <div>, so breaking only on </div> ran the salutation into the first line.
BLOCK = re.compile(r"</?(?:div|p|li|tr|h[1-6])\b[^>]*>|<br\s*/?>", re.I)
TAG = re.compile(r"<[^>]+>")
BLANK_RUN = re.compile(r"\n{3,}")


def body_text(message: dict[str, Any]) -> str:
    """The message body as plain text, gateway additions removed."""
    body = message.get("body") or {}
    content = body.get("content") or ""
    if body.get("contentType") == "text":
        return unwrap_text_links(content).strip()
    # A block-level tag is a line break; every other tag just goes.
    text = BLOCK.sub("\n", clean_body(content))
    text = html.unescape(TAG.sub("", text))
    lines = [line.rstrip() for line in text.splitlines()]
    return BLANK_RUN.sub("\n\n", "\n".join(lines)).strip()


def _people(recipients: list[dict[str, Any]] | None) -> str:
    out = []
    for r in recipients or []:
        address = r.get("address") or ""
        name = r.get("name")
        out.append(f"{name} <{address}>" if name and name != address else address)
    return ", ".join(out)


def detail(message: dict[str, Any]) -> dict[str, Any]:
    """One archived message as ``{from, to, cc, bcc, attachments, body}``."""
    return {
        "from": _people([message["sender"]]) if message.get("sender") else "",
        "to": _people(message.get("toRecipients")),
        "cc": _people(message.get("ccRecipients")),
        "bcc": _people(message.get("bccRecipients")),
        # Files the sender attached. Inline ones are the images embedded in
        # HTML signatures and gateway banners (`image.png`, `Outlook-*.png`),
        # not attachments in any sense the reader means, so they are dropped.
        "attachments": [
            a.get("name", "")
            for a in message.get("attachments") or []
            if a.get("name") and not a.get("isInline")
        ],
        "body": body_text(message),
    }


def details_by_id(root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Every archived message's detail, keyed by ``internetMessageId``.

    Returns an empty mapping when nothing is archived yet, so a caller that
    joins on it degrades to the columns it already had.
    """
    return {mid: detail(m) for mid, m in store.load_messages(root).items()}
