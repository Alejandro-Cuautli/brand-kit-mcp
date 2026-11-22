"""Small, dependency-free helpers for asset files: type sniffing and image dimensions.

Nothing here decodes pixels or parses XML; we only read headers.
"""

from __future__ import annotations

import re
import struct

EXTENSION_MIME = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
}

#: Raster formats that `get_asset` embeds as MCP image content.
EMBEDDABLE_MIME = frozenset({"image/png", "image/jpeg", "image/webp"})

_SVG_TAG = re.compile(rb"<svg\b[^>]*>", re.IGNORECASE | re.DOTALL)
_SVG_ACTIVE = re.compile(
    rb"<script\b|<foreignObject\b|\son[a-z]+\s*=|javascript:|<iframe\b|<embed\b|<object\b",
    re.IGNORECASE,
)


def sniff_mime(data: bytes) -> str | None:
    """Detect the media type from the file's leading bytes."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    if head.startswith(b"<") and _SVG_TAG.search(data[:65536]):
        return "image/svg+xml"
    return None


def svg_has_active_content(data: bytes) -> bool:
    """True if an SVG contains scripts, event handlers or embedded documents."""
    return bool(_SVG_ACTIVE.search(data))


def image_size(data: bytes, mime: str) -> tuple[int, int] | None:
    """Return ``(width, height)`` in pixels when it can be read from the header."""
    try:
        if mime == "image/png":
            if data[12:16] == b"IHDR":
                width, height = struct.unpack(">II", data[16:24])
                return width, height
        elif mime == "image/gif":
            width, height = struct.unpack("<HH", data[6:10])
            return width, height
        elif mime == "image/webp":
            return _webp_size(data)
        elif mime == "image/jpeg":
            return _jpeg_size(data)
        elif mime == "image/svg+xml":
            return _svg_size(data)
    except (struct.error, ValueError, IndexError):
        return None
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    chunk = data[12:16]
    if chunk == b"VP8X":
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if chunk == b"VP8 ":
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if chunk == b"VP8L":
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker in _JPEG_SOF:
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        (length,) = struct.unpack(">H", data[i + 2 : i + 4])
        if length < 2:
            return None
        i += 2 + length
    return None


def _svg_size(data: bytes) -> tuple[int, int] | None:
    match = _SVG_TAG.search(data[:65536])
    if not match:
        return None
    tag = match.group(0).decode("utf-8", "replace")

    def attr(name: str) -> str | None:
        found = re.search(rf"\s{name}\s*=\s*[\"']([^\"']*)[\"']", tag)
        return found.group(1).strip() if found else None

    width, height = attr("width"), attr("height")
    number = re.compile(r"^(\d+(?:\.\d+)?)(?:px)?$")
    if width and height and number.match(width) and number.match(height):
        return round(float(number.match(width).group(1))), round(float(number.match(height).group(1)))
    view_box = attr("viewBox")
    if view_box:
        parts = re.split(r"[\s,]+", view_box)
        if len(parts) == 4:
            return round(float(parts[2])), round(float(parts[3]))
    return None
