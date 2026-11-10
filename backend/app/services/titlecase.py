"""Title case for scorecard names and headings.

Small words stay lower unless they open or close the label: a, an, the, and, or, of,
in, to, vs., at, by, for. Acronyms (VIP, GHL, MRR) and brand spellings (ClickUp,
beCollective, RevShare) stay as themselves. A sentence in a note is not run through this.
"""
import re

_SMALL = {"a", "an", "the", "and", "or", "of", "in", "to", "vs.", "vs", "at", "by", "for"}
_KEEP = {
    "vip": "VIP", "ghl": "GHL", "cpl": "CPL", "ids": "IDS", "qtd": "QTD", "eos": "EOS",
    "clickup": "ClickUp", "meta": "Meta", "stripe": "Stripe", "forum": "Forum",
    "becollective": "beCollective", "quickbooks": "QuickBooks", "zoom": "Zoom",
    "axcion": "Axcion", "mighty": "Mighty", "dallas": "Dallas", "scottsdale": "Scottsdale",
    "sb": "SB", "ga": "GA", "mrr": "MRR", "ytd": "YTD", "cac": "CAC", "revshare": "RevShare",
}


def title_case(text):
    if text is None:
        return None
    s = str(text)
    if not s.strip():
        return s
    parts = re.split(r"(\s+)", s)
    words = [p for p in parts if p and not p.isspace()]
    last = len(words) - 1
    n = 0
    out = []
    for part in parts:
        if not part or part.isspace():
            out.append(part)
            continue
        out.append(_word(part, n == 0 or n == last))
        n += 1
    return "".join(out)


def _word(raw, force):
    if "-" in raw.strip("-"):
        bits = raw.split("-")
        return "-".join(_token(bit, True if i else force) for i, bit in enumerate(bits))
    return _token(raw, force)


def _token(raw, force):
    if re.search(r"[a-z]", raw) and re.search(r"[A-Z]", raw[1:]):
        return raw
    if re.fullmatch(r"[A-Z][A-Z0-9]{1,}", raw):
        return raw
    if re.fullmatch(r"\$[\d,.]+[KMB]?", raw, re.I):
        return raw
    if re.search(r"\d", raw):
        return raw
    key = re.sub(r"^[^A-Za-z]+|[^A-Za-z.]+$", "", raw)
    if not key:
        return raw
    lower = key.lower()
    if lower in _KEEP:
        return raw.replace(key, _KEEP[lower], 1)
    if not force and lower in _SMALL:
        return raw.replace(key, lower, 1)
    capped = lower[:1].upper() + lower[1:]
    return raw.replace(key, capped, 1)
