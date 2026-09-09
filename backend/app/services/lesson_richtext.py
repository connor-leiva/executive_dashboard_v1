"""Lesson bodies: what an author may write, and what a member is served.

This is the first rich text in the product, which makes it the first place authored markup reaches
another person's browser. Everything here exists because of that one sentence.

TWO PASSES, AND ONLY THE SECOND IS A SECURITY BOUNDARY.

  `_normalise` is cosmetic. It rewrites <b> to <strong>, flattens nested lists, drops empty blocks
  and unwraps the div/span scaffolding Word and Google Docs paste in. A bug in it is an ugly
  paragraph, not a vulnerability, and it runs FIRST so the sanitizer sees ordinary markup rather
  than a nest of wrappers.

  `nh3.clean` is the boundary. It is the Rust `ammonia` parser, it works on a real DOM rather than
  a regex, and it is what actually decides. If the two ever disagree, nh3 wins by construction --
  which is the point of running it last.

SANITIZED ON WRITE *AND* ON READ. On write because a stored body is served to everyone who opens
the lesson; on read because the write-side rule can change, and a body stored under yesterday's
allowlist should not keep whatever yesterday allowed. The read pass is a few microseconds on a
document nobody notices.

THE CLIENT IS NOT A TRUST BOUNDARY. The console's editor is configured down to this same
allowlist so it cannot easily produce something that would be stripped -- but that is a courtesy
to the author, not a control. Anything can POST to the API.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

import nh3

# The whole vocabulary. A tag absent here is unwrapped -- its text survives, its element does not.
TAGS = {"p", "h2", "h3", "strong", "em", "a", "ul", "ol", "li",
        "blockquote", "br", "hr", "img", "figure", "figcaption"}

ATTRIBUTES = {
    "a": {"href"},
    # No width/height/title/srcset: every one of them is a way to smuggle layout or a second URL.
    "img": {"src", "alt"},
}

# `data:` is absent deliberately -- a data: URL is an inline document, and an inline document in an
# href is a phishing page hosted on our own origin.
URL_SCHEMES = {"http", "https", "mailto"}

# Blocks that mean nothing when they hold nothing. <br>, <hr> and <img> are self-closing and are
# never "empty" in this sense.
DROP_IF_EMPTY = {"p", "h2", "h3", "blockquote", "li", "figcaption", "figure"}

VOID = {"br", "hr", "img"}

# Blocks that hold text directly. Text arriving while none of these is open is loose -- what a
# `<div>para</div><div>para</div>` paste unwraps to -- and two loose runs would otherwise
# concatenate into one paragraph with no space between them. Each gets its own <p>, which also
# gives the portal's lead-paragraph rule something to match.
TEXT_BLOCKS = {"p", "h2", "h3", "li", "blockquote", "figcaption", "figure"}

# Tags that end a paragraph even though we unwrap them: a <div> boundary is a paragraph boundary
# to the person who wrote it.
BREAKS_PARAGRAPH = {"div", "section", "article", "main", "tr", "td", "th", "table", "aside",
                    "header", "footer", "pre", "h1", "h4", "h5", "h6", "dl", "dt", "dd"}

# Word and Docs paste `<b>` for bold and `<i>` for italic, and older editors emit them too. They
# mean the same thing as the semantic tags and there is no reason to store two spellings.
REWRITE = {"b": "strong", "i": "em", "strike": "em", "u": "em"}

# Anything whose CONTENT is not text: dropping the element is not enough, the text inside has to
# go with it or a <script> body ends up as a paragraph of JavaScript.
DISCARD_WITH_CONTENT = {"script", "style", "head", "title", "noscript", "template", "iframe",
                        "object", "embed", "svg", "math"}

WORDS_PER_MINUTE = 220


class _Normalise(HTMLParser):
    """Cosmetic pass. See the module docstring for why this is not the security boundary."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._list_depth = 0
        self._discard_depth = 0
        # (tag, index into self.out) for each open element that we might have to erase.
        self._open: list[tuple[str, int]] = []
        self._implicit = -1          # index of an implicitly opened <p>, or -1

    # -- helpers ---------------------------------------------------------------------------
    def _emit(self, text: str) -> None:
        if self._discard_depth:
            return
        self.out.append(text)

    def _in_text_block(self) -> bool:
        return any(t in TEXT_BLOCKS for t, _ in self._open)

    def _open_implicit(self) -> None:
        if self._implicit >= 0 or self._in_text_block():
            return
        self._implicit = len(self.out)
        self.out.append("<p>")

    def _close_implicit(self) -> None:
        if self._implicit < 0:
            return
        inner = "".join(self.out[self._implicit + 1:])
        if _has_content(inner):
            self.out.append("</p>")
        else:
            del self.out[self._implicit:]
        self._implicit = -1

    def handle_starttag(self, tag, attrs):
        tag = REWRITE.get(tag, tag)
        if tag in DISCARD_WITH_CONTENT:
            self._discard_depth += 1
            return
        if self._discard_depth:
            return
        # A real block, or a wrapper we unwrap, both end whatever loose run preceded them.
        if tag in TEXT_BLOCKS or tag in BREAKS_PARAGRAPH or tag in ("ul", "ol", "figure", "hr"):
            self._close_implicit()

        if tag in ("ul", "ol"):
            self._list_depth += 1
            # A list inside a list is unwrapped: its items continue in the outer one. Keeping the
            # nesting would mean a second indent level the portal has no style for, and the spec
            # calls for one level.
            if self._list_depth > 1:
                self._open.append((tag, -1))
                return

        if tag not in TAGS:
            # Unwrapped, not dropped: this is the div/span scaffolding a paste arrives wrapped in,
            # and the words inside it are the author's.
            self._open.append((tag, -1))
            return

        if tag in ("strong", "em", "a", "img", "br"):
            self._open_implicit()
        attr_text = ""
        for name, value in attrs:
            if tag in ATTRIBUTES and name in ATTRIBUTES[tag] and value:
                attr_text += f' {name}="{_escape_attr(value)}"'
        if tag in VOID:
            self._emit(f"<{tag}{attr_text}>")
            return
        self._open.append((tag, len(self.out)))
        self._emit(f"<{tag}{attr_text}>")

    def handle_endtag(self, tag):
        tag = REWRITE.get(tag, tag)
        if tag in DISCARD_WITH_CONTENT:
            self._discard_depth = max(0, self._discard_depth - 1)
            return
        if self._discard_depth:
            return
        if tag in ("ul", "ol"):
            self._list_depth = max(0, self._list_depth - 1)
        if tag in BREAKS_PARAGRAPH:
            self._close_implicit()

        for i in range(len(self._open) - 1, -1, -1):
            open_tag, start = self._open[i]
            if open_tag != tag:
                continue
            del self._open[i]
            if start < 0:            # an unwrapped element: nothing was emitted for it
                return
            inner = "".join(self.out[start + 1:])
            if tag in DROP_IF_EMPTY and not _has_content(inner):
                del self.out[start:]
                return
            self._emit(f"</{tag}>")
            return

    def handle_data(self, data):
        if self._discard_depth:
            return
        if data.strip():
            self._open_implicit()
        elif self._implicit < 0 and not self._in_text_block():
            return               # whitespace between blocks is not a paragraph
        self._emit(_escape_text(data))

    def result(self) -> str:
        # Close anything the author left open, so nh3 is not the thing repairing the tree.
        for tag, _ in reversed(self._open):
            if tag in TAGS and tag not in VOID:
                self.out.append(f"</{tag}>")
        self._close_implicit()
        return "".join(self.out)


def _has_content(inner: str) -> bool:
    """Whether a block holds anything a reader would see. An <img> or <br> counts."""
    if re.search(r"<(img|br|hr)\b", inner):
        return True
    return bool(re.sub(r"<[^>]*>", "", inner).replace(" ", " ").strip())


def _escape_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(value: str) -> str:
    return _escape_text(value).replace('"', "&quot;")


def _normalise(html: str) -> str:
    parser = _Normalise()
    parser.feed(html or "")
    parser.close()
    return parser.result()


# ── images: one stored spelling, two served ones ──────────────────────────────────────────
#
# THE STORAGE KEY IS WHAT IS STORED. A signed URL expires and a body is kept for years, so an
# article would fill with broken images at whatever hour the signature ran out.
#
# But an <img> in a browser needs a URL, so the key is rewritten to a fetchable route on the way
# out -- a different route for the console and the portal, because they authenticate differently
# and an admin who is not a portal member must still see their own images while editing.
#
# Which means the editor POSTS BACK the rewritten URL, so the sanitizer has to recognise it and
# fold it home again. Without that, saving a lesson twice turns every image into an unowned URL
# the second save strips, and the images vanish on the third.
CONSOLE_IMAGE_ROUTE = "/api/console/lessons/{lesson}/images/{name}"
PORTAL_IMAGE_ROUTE = "/api/v1/intranet/lessons/{lesson}/images/{name}"

_SERVED_IMAGE = re.compile(
    r"^/api/(?:console|v1/intranet)/lessons/([0-9a-fA-F-]{36})/images/([^/?#]+)$")
_STORED_IMAGE = re.compile(r"^intranet/([0-9a-fA-F-]{36})/lessons/([0-9a-fA-F-]{36})/([^/]+)$")


def _to_key(src: str, tenant_id) -> str:
    """A served image URL folded back to the key it came from. Anything else is returned as-is."""
    found = _SERVED_IMAGE.match((src or "").strip())
    if not found:
        return src
    return f"intranet/{tenant_id}/lessons/{found.group(1)}/{found.group(2)}"


def image_urls(html: str | None, route: str) -> str:
    """Storage keys in `src` rewritten to a route the browser can actually fetch.

    Called on the way OUT, after sanitizing. `route` is CONSOLE_IMAGE_ROUTE or
    PORTAL_IMAGE_ROUTE -- the caller knows which session is going to make the request.
    """
    if not html:
        return ""

    def one(match: re.Match) -> str:
        found = _STORED_IMAGE.match(match.group(1))
        if not found:
            return match.group(0)
        return 'src="{}"'.format(route.format(lesson=found.group(2), name=found.group(3)))

    return re.sub(r'src="([^"]*)"', one, html)


def _image_ok(src: str, tenant_id) -> bool:
    """An image is either ours or an https URL.

    The tenant check is the point: without it one workspace could embed another's storage key and
    read a private image through our own resolver.
    """
    src = (src or "").strip()
    if src.startswith("https://"):
        return True
    return src.startswith(f"intranet/{tenant_id}/")


def sanitize(html: str | None, *, tenant_id) -> str:
    """The only way a lesson body is allowed to become stored or served markup."""
    if not html:
        return ""
    cleaned = nh3.clean(
        _normalise(html),
        tags=TAGS,
        attributes={k: set(v) for k, v in ATTRIBUTES.items()},
        url_schemes=URL_SCHEMES,
        # No link_rel. Whether an anchor opens in a new tab -- and therefore whether it needs
        # `rel` -- is decided at RENDER by the portal (spec 3.4: a site-relative href routes
        # client-side, everything else opens externally). Storing a rel here would be a second
        # place that decision lives, and the second pass strips it anyway.
        strip_comments=True,
    )
    # nh3 has already dropped javascript:/data: hrefs. This second pass is about OWNERSHIP, which
    # is a question about our tenants rather than about URLs, so nh3 cannot answer it. A URL the
    # editor was served is folded back to its key first, so a re-save does not strip the images
    # the last save put there.
    def _image(match: re.Match) -> str:
        tag = match.group(0)
        src = _to_key(_src_of(tag), tenant_id)
        if not _image_ok(src, tenant_id):
            return ""
        return re.sub(r'src="[^"]*"', 'src="{}"'.format(src.replace("\\", "")), tag, count=1)

    cleaned = re.sub(r'<img\b[^>]*>', _image, cleaned)
    # An anchor nh3 stripped the href from is no longer a link. Unwrap it: the spec keeps the
    # words and drops the element, and a bare <a> is neither one nor the other.
    cleaned = re.sub(r'<a(?![^>]*\shref=)[^>]*>(.*?)</a>', r"\1", cleaned, flags=re.S)
    # Second tidy: the image filter above can empty a paragraph, and the first pass ran before it.
    # _normalise is idempotent on already-clean markup, so this costs a parse and changes nothing
    # else.
    return _normalise(cleaned)


def _src_of(tag_html: str) -> str:
    found = re.search(r'src="([^"]*)"', tag_html)
    return found.group(1) if found else ""


def to_text(html: str | None) -> str:
    """The body as prose, for search and for the assistant.

    Both of them index `description` today, so a reading lesson -- whose whole content is the body
    -- would be invisible to a member searching for a phrase inside it.
    """
    if not html:
        return ""
    # Block ends become spaces, or "end.<p>Next" concatenates into "end.Next".
    spaced = re.sub(r"</(p|h2|h3|li|blockquote|figcaption)>", " ", html)
    spaced = re.sub(r"<(br|hr)\b[^>]*>", " ", spaced)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", "", spaced)).strip()


def word_count(html: str | None) -> int:
    text = to_text(html)
    return len(text.split()) if text else 0


def read_minutes(html: str | None) -> int | None:
    """Minutes at a reading pace, or None for an empty body.

    None rather than 0: a lesson with no body yet has an unknown length, and 0 would render as
    "0 min read" on a page somebody is about to write.
    """
    words = word_count(html)
    if not words:
        return None
    return max(1, round(words / WORDS_PER_MINUTE))
