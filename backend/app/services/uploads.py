"""What the bytes actually are, for every file this product accepts from a browser.

Lifted out of routers/intranet, unchanged, once a SECOND router needed it: the console uploads
lesson handouts and the portal uploads marketing attachments, and both must answer "what is this
file" exactly the same way. Two copies of an allowlist is how one of them quietly gains an
extension the other refuses.

UPLOAD POLICY. Four types, and no more.

SVG is absent deliberately even though the logo uploader accepts it: an SVG is a script host, and
these files are fetched by other people in the workspace. HTML for the same reason. Downloads are
served as attachments with the SNIFFED type, so nothing renders inline, but the allowlist is the
primary control rather than the header.
"""
from __future__ import annotations

# Magic numbers as hex, not escapes: a byte literal written through a shell heredoc gets its
# escapes eaten, which is how this arrived as real control characters the first time.
ATTACHMENT_TYPES = {
    "image/png": (bytes.fromhex("89504e470d0a1a0a"), ".png"),
    "image/jpeg": (bytes.fromhex("ffd8ff"), ".jpg"),
    "application/pdf": (b"%PDF-", ".pdf"),
    "image/webp": (None, ".webp"),          # RIFF....WEBP, checked separately below
}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS = 5


# Inline images in a lesson body: the handout list plus GIF, minus PDF. A body image is rendered
# in the page rather than downloaded, so a PDF has no meaning there and an animation does.
IMAGE_TYPES = {
    "image/png": ((ATTACHMENT_TYPES["image/png"][0],), ".png"),
    "image/jpeg": ((ATTACHMENT_TYPES["image/jpeg"][0],), ".jpg"),
    "image/webp": ((), ".webp"),                       # RIFF....WEBP, checked separately
    "image/gif": ((b"GIF87a", b"GIF89a"), ".gif"),
}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def sniff_image(data: bytes) -> str | None:
    """Same rule as sniff_attachment, over the image allowlist. The bytes decide, not the name."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    for content_type, (magics, _ext) in IMAGE_TYPES.items():
        if any(magic and data.startswith(magic) for magic in magics):
            return content_type
    return None


def image_size(data: bytes) -> tuple[int, int] | None:
    """Pixel dimensions, read from the file header.

    HAND-PARSED RATHER THAN VIA PILLOW, which is not a dependency of this project. It happens to
    be installed in some local environments as somebody else's leftover, and code that used it
    would pass every test here and 500 on Railway. Four formats, each of which states its size in
    the first few dozen bytes.

    Returns None rather than guessing: the caller stores NULL, and a missing dimension is a
    smaller problem than a wrong one.
    """
    try:
        if data.startswith(ATTACHMENT_TYPES["image/png"][0]) and data[12:16] == b"IHDR":
            return (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return (int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little"))
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return _webp_size(data)
        if data.startswith(ATTACHMENT_TYPES["image/jpeg"][0]):
            return _jpeg_size(data)
    except (IndexError, ValueError):
        return None
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    chunk = data[12:16]
    if chunk == b"VP8X":                     # extended: 24-bit width-1, height-1, little-endian
        return (int.from_bytes(data[24:27], "little") + 1,
                int.from_bytes(data[27:30], "little") + 1)
    if chunk == b"VP8 ":                     # lossy: 14-bit fields after the start code
        return (int.from_bytes(data[26:28], "little") & 0x3FFF,
                int.from_bytes(data[28:30], "little") & 0x3FFF)
    if chunk == b"VP8L":                     # lossless: 14 bits each, packed across four bytes
        bits = int.from_bytes(data[21:25], "little")
        return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    return None


# Frames that carry dimensions. DHT/DAC/RST/SOS are absent: they are not frame headers, and
# reading four bytes at +5 of one of them yields a plausible-looking wrong answer.
_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
             0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in _JPEG_SOF:
            return (int.from_bytes(data[i + 7:i + 9], "big"),
                    int.from_bytes(data[i + 5:i + 7], "big"))
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2                            # standalone markers carry no length
            continue
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return None


def sniff_attachment(data: bytes) -> str | None:
    """The type the BYTES claim, ignoring the filename and the browser's Content-Type entirely.

    A client controls both of those. If a download later echoes a declared type back, an uploaded
    HTML file labelled `image/png` becomes stored XSS against everyone who opens it. Sniffing is
    what makes the allowlist mean anything.
    """
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    for content_type, (magic, _ext) in ATTACHMENT_TYPES.items():
        if magic and data.startswith(magic):
            return content_type
    return None
