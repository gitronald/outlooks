"""Attachment kinds shared by capture ingestion and reader audits.

Internal: not part of the Python API a consuming repo may import (the
README's "Python API" lists what is). The CLI is this module's interface.
"""

import re
from typing import Any
from urllib.parse import unquote

# The connector sends some images as ``application/octet-stream`` under a
# placeholder name (``PNG image``, ``img-<uuid>``), so the content type alone
# misses them. Design files carry no text worth reading either. Only a whole
# uuid follows ``img-``, so a document named ``img-1234abcd-report.pdf`` is not
# taken for one.
IMAGE_NAME = re.compile(
    r"^(?:(?:png|jpe?g|gif|bmp|tiff?|webp|heic) image"
    r"|img-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$",
    re.I,
)
IMAGE_EXT = tuple(
    f".{ext}"
    for ext in "png jpg jpeg gif bmp tif tiff webp heic svg ai psd eps".split()
)


def is_image(att: dict[str, Any]) -> bool:
    """Whether an attachment entry is an image, by its type, name, or extension."""
    name = str(att.get("name") or "").strip()
    return (
        str(att.get("contentType") or "").lower().startswith("image/")
        or bool(IMAGE_NAME.match(name))
        or name.lower().endswith(IMAGE_EXT)
    )


def uri_path(uri: str) -> str:
    """A resource uri without its query, percent-decoded, for matching."""
    return unquote(uri.split("?", 1)[0])


def attachment_id(uri: str) -> str | None:
    """The decoded attachment id of an attachment uri, as a slot key."""
    _, sep, rest = uri_path(uri).partition("/attachments/")
    return rest if sep and rest else None
