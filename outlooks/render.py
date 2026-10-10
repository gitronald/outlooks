"""Render an Outlook message as a ``{Surname} - Message.docx`` transcription.

The Microsoft 365 connector returns a message as JSON (``read_resource`` on its
``mail:///messages/...`` uri) and an attachment only as extracted text, never
the original file, so a filed message is a transcription: a header block
(From/Sent/To/Cc/Subject, with the send time in the configured zone), a rule, then the
body. The body is the message HTML with the mail gateway's additions removed
(the hidden preheader and the "untrusted sender" banner) and URL-defense links
pointed back at their original URLs; the sender's text is left as it is. For a
letter sent as an attachment, ``text`` (the attachment's extracted text) replaces
the body. pandoc renders the result to .docx.

Internal: not part of the Python API a consuming repo may import (the
README's "Python API" lists what is). The CLI is this module's interface.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from outlooks import config
from outlooks.gateway import BANNER, clean_body, unwrap_text_links

# A plain-text line at least this long is assumed to have been hard-wrapped, so
# the line after it continues the same paragraph.
WRAP_WIDTH = 60


def text_html(text: str) -> str:
    """Plain text as HTML, rejoining lines that were hard-wrapped.

    The gateway rewrites plain-text bodies too, so the banner and the URL-defense
    wrappers are stripped here as well; skipping it transcribed "This Message Is
    From an Untrusted Sender" and raw urldefense links straight into the filed
    transcription.

    A plain-text message is usually hard-wrapped near 72 columns, and one
    paragraph per *line* turned every wrapped line into its own paragraph. A
    line is treated as the continuation of the one above only when that line ran
    close to the wrap width, so wrapped prose rejoins while a salutation,
    address block, or signature — all short lines — keeps its own lines.
    """
    text = BANNER.sub("", text)
    text = unwrap_text_links(text)
    paragraphs: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        joined = [lines[0]]
        for line in lines[1:]:
            if len(joined[-1]) >= WRAP_WIDTH:
                joined[-1] += f" {line}"
            else:
                joined.append(line)
        paragraphs += [f"<p>{html.escape(x, quote=False)}</p>" for x in joined]
    return "\n".join(paragraphs)


def sent_local(message: dict[str, Any]) -> str:
    """The send time as Outlook shows it, in the configured zone."""
    sent = datetime.fromisoformat(message["sentDateTime"]).astimezone(config.zone())
    hour = sent.hour % 12 or 12
    day = f"{sent:%A}, {sent:%B} {sent.day}, {sent.year}"
    return f"{day} {hour}:{sent:%M} {sent:%p} ({config.zone_label(sent)})"


def _addresses(recipients: list[dict[str, Any]]) -> str:
    return ", ".join(r["address"] for r in recipients)


def header_html(message: dict[str, Any]) -> str:
    """The From/Sent/To/Cc/Subject block, one line each."""
    sender = message["sender"]
    who = sender["address"]
    if sender.get("name"):
        who = f"{sender['name']} <{who}>"
    rows = [
        ("From", who),
        ("Sent", sent_local(message)),
        ("To", _addresses(message.get("toRecipients", []))),
    ]
    if cc := _addresses(message.get("ccRecipients", [])):
        rows.append(("Cc", cc))
    rows.append(("Subject", message["subject"]))
    lines = [f"<strong>{k}:</strong> {html.escape(v, quote=False)}" for k, v in rows]
    return "<p>" + "<br />\n".join(lines) + "</p>"


def default_surname(display_name: str) -> str:
    """The filing surname implied by a sender's display name.

    Handles the two shapes that otherwise file under the wrong name: Exchange's
    directory form (``Doe, Jane`` -> ``Doe``, not ``Jane``) and a trailing
    parenthetical (``Jane Doe (she/her)`` -> ``Doe``, not ``(she/her)``).
    """
    name = re.sub(r"\([^)]*\)", " ", display_name).strip()
    if "," in name:  # "Doe, Jane" — the surname leads
        name = name.split(",")[0]
    parts = name.split()
    if not parts:
        raise ValueError("the sender has no usable display name; pass a surname")
    return parts[-1]


def check_surname(surname: str) -> str:
    """A surname safe to use as a file name, or a ValueError naming the problem."""
    surname = surname.strip()
    if not surname:
        raise ValueError("the surname is empty; pass a surname")
    if surname in (".", "..") or any(sep in surname for sep in ("/", "\\", "\0")):
        raise ValueError(
            f"{surname!r} is not usable as a file name (it would change the path); "
            "pass a surname"
        )
    return surname


def docx_name(
    message: dict[str, Any],
    surname: str | None = None,
    *,
    label: str | None = None,
    stage: str | None = None,
) -> str:
    """The file name for this transcription.

    ``{Surname} - {Label}.docx`` for a standalone label, and
    ``{Surname} - {Stage} - {Label}.docx`` when a stage applies. The label
    defaults to the configured ``render_label`` (``Message``).
    """
    label = label or config.settings().render_label
    surname = check_surname(
        surname if surname else default_surname(message["sender"].get("name", ""))
    )
    middle = f" - {stage}" if stage else ""
    return f"{surname}{middle} - {label}.docx"


def render(
    message: dict[str, Any],
    out_dir: Path,
    *,
    surname: str | None = None,
    text: str | None = None,
    label: str | None = None,
    stage: str | None = None,
) -> Path:
    """Write the transcription into ``out_dir``; refuse to overwrite a file."""
    out = Path(out_dir) / docx_name(message, surname, label=label, stage=stage)
    if out.exists():
        raise ValueError(f"{out} already exists; not overwriting it")
    body = message["body"]
    if text is not None:
        content = text_html(text)
    elif body.get("contentType") == "text":
        content = text_html(body["content"])
    else:
        content = clean_body(body["content"])
    if not content.strip():
        # A header-only .docx that reports success would be filed as though it
        # were the sender's message.
        raise ValueError(
            "the message has no body text to transcribe "
            "(an empty body, or an attachment that extracted to nothing)"
        )
    if shutil.which("pandoc") is None:
        raise ValueError("pandoc is not on the PATH")
    doc = f"{header_html(message)}\n<hr />\n{content}\n"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            # --sandbox: the body is attacker-controlled HTML from an unknown
            # sender, and the .docx it produces is filed where that sender can
            # read it. Without it an <img src="/path/to/.env"> makes pandoc
            # read a local file and embed its bytes in the document, handing the
            # sender whatever it points at; a remote src also fires a tracking
            # pixel. The sandbox blocks pandoc's file and network access.
            ["pandoc", "--sandbox", "-f", "html", "-t", "docx", "-o", str(out)],
            input=doc,
            text=True,
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        out.unlink(missing_ok=True)
        raise ValueError(f"pandoc failed: {e.stderr.strip()}") from None
    return out
