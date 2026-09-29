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

import base64
import html
import re
import shutil
import string
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from outlooks import config

# Proofpoint brackets its banner with hidden marker text. Cut from the marker
# itself (taking the wrapper div when there is one), never from the start of the
# document: the end marker also appears inside a quoted reply, and cutting from
# the top would silently delete the sender's own text above it. The preheader —
# a hidden copy of the opening lines — is a separate div, removed by HIDDEN_DIV.
BANNER = re.compile(
    r"(?:<div\b[^>]*>\s*)?ZjQcmQRYFpfptBannerStart"
    r".*?ZjQcmQRYFpfptBannerEnd(?:\s*</div>)?",
    re.S,
)
HIDDEN_DIV = re.compile(r'<div style="[^"]*display:\s*none[^"]*">[^<]*</div>', re.I)
# href is not always the first attribute and not always double-quoted (Thunderbird
# emits `class=... href=...`, other clients emit target="_blank" first), so the
# tag is matched attribute-wise rather than positionally.
DEFENDED_LINK = re.compile(
    r"<a\b([^>]*?)\bhref=(\"|')"
    r"(https://urldefense\.(?:com|proofpoint\.com)/[^\"']*)\2"
    r"([^>]*)>(.*?)</a>",
    re.S | re.I,
)
# Both gateway hostnames rewrite to the same v3 format; matching only one left
# the other's links wrapped in the filed transcription.
V3_TARGET = re.compile(
    r"^https://urldefense\.(?:com|proofpoint\.com)/v3/__(.+?)__;([^!]*)!"
)
# The same wrappers in a plain-text body, where there is no <a> tag to match.
# Stops at a tag bracket or quote as well as whitespace, so the same pattern can
# run over an HTML body (a bare URL inside a <pre> block ends at `</pre>`).
BARE_DEFENDED = re.compile(
    r"https://urldefense\.(?:com|proofpoint\.com)/v3/[^\s<>\"']+"
)
# In a v3 wrapper, `*` stands for one replaced byte and `**X` for a run of them,
# X's position in this alphabet plus 2 bytes long.
RUN_LENGTHS = {
    c: i + 2
    for i, c in enumerate(
        string.ascii_uppercase + string.ascii_lowercase + string.digits + "-_"
    )
}
ESCAPE = re.compile(r"\*(?:\*(.))?")
URL = re.compile(r"https?://\S+$")
# A plain-text line at least this long is assumed to have been hard-wrapped, so
# the line after it continues the same paragraph.
WRAP_WIDTH = 60


# Only these are decoded in an href — see unescape_href.
HREF_ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'"}


def unescape_href(href: str) -> str:
    """Decode the real entities in an href, and only those.

    ``html.unescape`` also resolves HTML5's legacy semicolon-less references, so
    an ordinary query string came back corrupted — ``?user=x&region=us`` as
    ``?user=x®ion=us``, ``&copy=`` as ``©=``, ``&times=`` as
    ``×=`` — and that mangled URL is what lands in the permanent filed
    transcription.
    """
    for entity, char in HREF_ENTITIES.items():
        href = href.replace(entity, char)
    return href


def decode_v3(href: str) -> str | None:
    """The original URL inside a Proofpoint v3 wrapper; None if it can't be decoded.

    Proofpoint swaps the characters it won't leave in a URL for ``*`` escapes
    and stores the swapped-out bytes, base64url-encoded, after ``__;``.
    """
    match = V3_TARGET.match(unescape_href(href))
    if not match:
        return None
    url, token = match.groups()
    try:
        stored = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except ValueError:
        return None
    out = bytearray()
    pos = used = 0
    for escape in ESCAPE.finditer(url):
        run = escape.group(1)
        size = 1 if run is None else RUN_LENGTHS.get(run, 0)
        if not size or used + size > len(stored):
            return None
        out += url[pos : escape.start()].encode() + stored[used : used + size]
        used += size
        pos = escape.end()
    out += url[pos:].encode()
    try:
        return out.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _unwrap(match: re.Match[str]) -> str:
    """Point one URL-defense link back at its original URL.

    The link text usually is the original URL, so it wins. Otherwise a v3
    wrapper is decoded; a link that can't be decoded is left wrapped rather
    than guessed at.
    """
    before, _quote, href, after, text = match.groups()
    shown = html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
    if URL.match(shown):
        target = shown
    elif (decoded := decode_v3(href)) is not None:
        target = decoded
    else:
        return match.group(0)
    attrs = f"{before.rstrip()} {after.lstrip()}".strip()
    space = f" {attrs}" if attrs else ""
    return f'<a href="{html.escape(target)}"{space}>{text}</a>'


def unwrap_text_links(text: str) -> str:
    """Point bare URL-defense URLs in a plain-text body back at their originals."""

    def sub(match: re.Match[str]) -> str:
        # Trailing sentence punctuation is not part of the wrapper.
        url = match.group(0).rstrip(".,;:)]}>\"'")
        tail = match.group(0)[len(url) :]
        decoded = decode_v3(url)
        return (decoded if decoded is not None else url) + tail

    return BARE_DEFENDED.sub(sub, text)


def clean_body(body: str) -> str:
    """The message HTML without the gateway banner, preheader, or link wrappers."""
    body = BANNER.sub("", body, count=1)
    body = HIDDEN_DIV.sub("", body)
    body = DEFENDED_LINK.sub(_unwrap, body)
    # A bare wrapper in a text node (a <pre> body, a signature typed as plain
    # text) has no <a> tag for DEFENDED_LINK to match.
    body = unwrap_text_links(body)
    return body.strip()


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


def sent_pt(message: dict[str, Any]) -> str:
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
        ("Sent", sent_pt(message)),
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
        raise ValueError(f"pandoc failed: {e.stderr.strip()}") from None
    return out
