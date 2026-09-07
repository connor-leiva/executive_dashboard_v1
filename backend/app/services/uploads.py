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
