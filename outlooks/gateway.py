"""Remove mail-gateway banners and URL-defense rewriting.

Internal: not part of the Python API a consuming repo may import (the
README's "Python API" lists what is). The CLI is this module's interface.
"""

import base64
import html
import re
import string

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
