"""What an archived Outlook message is evidence of.

Works on either tier of the archive: a thin search hit (sender and recipients
as address strings, a truncated ``summary``) or a full ``read_resource`` message
(sender/recipient objects, the HTML body, ``conversationId``). :func:`view`
flattens both into one :class:`Mail`; :func:`classify` reads the role the
message plays.

Roles: ``arrival`` (a correspondent's first message), ``followup`` (a reply or
forward from outside), ``ours`` (mail from one of ``own_addresses``),
``decision`` (ours, with the configured ``decision_tag`` in the subject), and
``system`` (mail from one of ``system_senders``). What a system sender's
notifications say is the repo's own business: the package parses none of it.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from outlooks import config

REPLY_PREFIX = re.compile(
    r"^\s*(?:(?:re|aw|fw|fwd|wg|sv|antw|automatic reply|auto(?:matic)? response"
    r"|out of office|undeliverable)\s*:|\[external\])\s*",
    re.I,
)
AUTO_REPLY = re.compile(r"^\s*(?:automatic reply|out of office|auto)", re.I)
TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class Mail:
    """One message, whichever tier it came from."""

    mid: str
    received: datetime
    sender: str
    sender_name: str
    recipients: tuple[str, ...]
    subject: str
    text: str
    conversation_id: str | None = None
    has_attachments: bool = False
    full: bool = False
    mailbox: str | None = None

    @property
    def day(self) -> date:
        """The received date in the configured zone, the one records are kept in."""
        return self.received.astimezone(config.zone()).date()


@dataclass(frozen=True)
class Facts:
    """The role a message plays, and a decision's label."""

    role: str
    outcome: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def body_text(body: dict[str, Any] | None) -> str:
    """A message body as plain text (tags dropped, entities decoded)."""
    if not body:
        return ""
    content = body.get("content") or ""
    if body.get("contentType") == "html":
        content = TAG.sub(" ", re.sub(r"<br\s*/?>", "\n", content, flags=re.I))
        content = html.unescape(content)
    return re.sub(r"[ \t\r\f\v]+", " ", content)


def _address(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("address") or value.get("emailAddress", {}).get("address")
    return (value or "").strip().lower()


def view(payload: dict[str, Any], mailbox: str | None = None) -> Mail:
    """Flatten a hit or a full message into a :class:`Mail`."""
    full = "body" in payload
    sender = payload.get("sender")
    if full:
        recipients = [
            _address(r)
            for key in ("toRecipients", "ccRecipients", "bccRecipients")
            for r in payload.get(key) or []
        ]
    else:
        recipients = [_address(r) for r in payload.get("recipients") or []]
    received = payload["receivedDateTime"].replace("Z", "+00:00")
    return Mail(
        mid=payload["internetMessageId"],
        received=datetime.fromisoformat(received),
        sender=_address(sender),
        sender_name=(sender.get("name") or "") if isinstance(sender, dict) else "",
        recipients=tuple(r for r in recipients if r),
        subject=payload.get("subject") or "",
        text=body_text(payload["body"]) if full else payload.get("summary") or "",
        conversation_id=payload.get("conversationId"),
        has_attachments=bool(payload.get("hasAttachments")),
        full=full,
        mailbox=mailbox or payload.get("mailbox"),
    )


def is_reply(subject: str) -> bool:
    return bool(REPLY_PREFIX.match(subject))


def thread_subject(subject: str) -> str:
    """The subject without reply/forward/external prefixes, lowercased."""
    previous = None
    subject = subject.strip()
    while previous != subject:
        previous = subject
        subject = REPLY_PREFIX.sub("", subject).strip()
    return re.sub(r"\s+", " ", subject).lower()


def decision_tag() -> re.Pattern[str] | None:
    """The configured ``decision_tag`` as a subject pattern (None when unset)."""
    tag = config.settings().decision_tag
    if not tag:
        return None
    return re.compile(re.escape(tag) + r"\s*:?\s*(.*)$", re.I)


def classify(mail: Mail) -> Facts:
    """The role ``mail`` plays; a decision carries its label as ``outcome``."""
    subject = mail.subject
    settings = config.settings()
    if mail.sender in settings.system_senders:
        return Facts("system")
    if mail.sender in settings.own_addresses:
        pattern = decision_tag()
        tag = pattern.search(subject) if pattern else None
        if tag and not is_reply(subject):
            label = re.sub(r"\s+", " ", tag.group(1)).strip()
            return Facts("decision", outcome=label or None)
        return Facts("ours")
    if is_reply(subject):
        return Facts("followup", extra={"auto": bool(AUTO_REPLY.match(subject))})
    return Facts("arrival")
