"""Images as the model servers can take them.

A file's extension is not its format: the benchmark has JPEGs named ``.webp``.
``sniff`` reads the format from the bytes.  Local servers are stricter than the
Claude API: llama.cpp decodes PNG and JPEG only and silently drops anything
else, so the model answers from the filename.  ``for_local_server`` converts
every other format to PNG.
"""
from __future__ import annotations

import io

MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
LOCAL_FORMATS = ("image/png", "image/jpeg")


def sniff(data: bytes) -> str | None:
    """The media type the bytes actually are, or None when unrecognised."""
    for magic, media_type in MAGIC:
        if data.startswith(magic):
            return media_type
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def for_local_server(data: bytes, declared: str | None = None) -> tuple[bytes, str]:
    """PNG or JPEG bytes and their media type; anything else is converted to PNG."""
    actual = sniff(data) or declared
    if actual in LOCAL_FORMATS:
        return data, actual
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        out = io.BytesIO()
        img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB").save(out, format="PNG")
    return out.getvalue(), "image/png"
