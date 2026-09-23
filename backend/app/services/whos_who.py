"""WHO'S WHO: the team as its console describes it (WHOS-WHO-WIN-THE-DAY-SPEC.md, phases 4-5).

The directory was a flat list of whoever had signed in. The mockup is a page: a featured leader
with the team's numbers, a Leadership section, the agents, and a profile page for anyone. This
module is the rules behind it -- what a profile may say, who goes where, what the stats read -- so
the console that writes them and the portal that draws them cannot disagree.

EVERYONE ON THE ROSTER, NOT EVERYONE WHO SIGNED IN (D1). A colleague is a colleague before they
accept a portal invite, and with held invites a team added quietly would otherwise show nobody.
Removed people are gone and Hidden people are left out on purpose -- the workspace's owner, when
the owner is not on the team.

PHOTOS ARE RE-ENCODED (D7). Whatever arrives -- a phone photo with GPS in its EXIF, a 20 MB PNG, a
transparent cut-out -- leaves as a plain JPEG of at most 2000px on the long edge with no metadata,
transparency flattened onto white so a cut-out still melts into the featured band's multiply.
"""
from __future__ import annotations

import hashlib
import io
import re
from typing import Any

from .wtd_playbook import fill, number_words  # noqa: F401 -- number_words is re-exported for tests

PRONOUNS = {
    "she": {"bring": "Bring Her", "reach": "Reach Her", "owns": "She Owns"},
    "he": {"bring": "Bring Him", "reach": "Reach Him", "owns": "He Owns"},
    "they": {"bring": "Bring Them", "reach": "Reach Them", "owns": "They Own"},
}
PLACEMENTS = {"auto", "leadership", "agents", "hidden"}
STAT_SOURCES = {"manual", "team_size", "sisu_units_ytd", "sisu_volume_ytd"}
SISU_SOURCES = {"sisu_units_ytd", "sisu_volume_ytd"}

FOCUS_RE = re.compile(r"^(\d{1,3})% (\d{1,3})%$")
WEB_RE = re.compile(r"^https?://[^\s<>\"']+$", re.I)
MESSAGE_RE = re.compile(r"^(https?://[^\s<>\"']+|mailto:[^\s<>\"']+|sms:[+0-9 ()-]{3,}|"
                        r"tel:[+0-9 ()-]{3,}|slack://[^\s<>\"']+)$", re.I)
PORTAL_PATH_RE = re.compile(r"^/[A-Za-z0-9/_\-#?=&.%]*$")

MAX_PHOTO_BYTES = 15 * 1024 * 1024
# A 50-megapixel phone photo. Measured from the file's header, BEFORE it is decoded: 15 MB of
# PNG can describe an image that decodes into gigabytes.
MAX_PHOTO_PIXELS = 50_000_000
PHOTO_EDGE = 2000
PREVIEW_MAX = 60

TEXT_LIMITS = {"headline": 120, "tag": 24, "help_line": 140, "quote": 240, "office": 200}


class ProfileError(ValueError):
    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


# ── what a profile may say ───────────────────────────────────────────────────────────────────

def clean_field(field: str, value: Any):
    """One profile field, checked and normalised; ProfileError names what is wrong."""
    if field in TEXT_LIMITS:
        text = str(value or "").strip()
        if len(text) > TEXT_LIMITS[field]:
            raise ProfileError(field, f"At most {TEXT_LIMITS[field]} characters.")
        return text or None
    if field == "bring":
        if value in (None, ""):
            return None
        if not isinstance(value, list) or len(value) > 6:
            raise ProfileError(field, "Up to six lines.")
        lines = []
        for i, line in enumerate(value):
            text = str(line or "").strip()
            if len(text) > 140:
                raise ProfileError(f"bring.{i}", "At most 140 characters.")
            if text:
                lines.append(text)
        return lines or None
    if field == "pronoun":
        value = str(value or "they").strip().lower()
        if value not in PRONOUNS:
            raise ProfileError(field, "One of she, he or they.")
        return value
    if field == "message_url":
        text = str(value or "").strip()
        if not text:
            return None
        if len(text) > 500 or not MESSAGE_RE.match(text):
            raise ProfileError(field, "A web, mailto:, sms:, tel: or slack:// address.")
        return text
    if field == "owns_items":
        if value in (None, ""):
            return None
        if not isinstance(value, list) or len(value) > 8:
            raise ProfileError(field, "Up to eight items.")
        items = []
        for i, item in enumerate(value):
            if not isinstance(item, dict):
                raise ProfileError(f"owns_items.{i}", "Expected a label and an optional link.")
            label = str(item.get("label") or "").strip()
            url = str(item.get("url") or "").strip() or None
            if not label:
                continue
            if len(label) > 120:
                raise ProfileError(f"owns_items.{i}.label", "At most 120 characters.")
            if url and (len(url) > 500 or not (WEB_RE.match(url) or PORTAL_PATH_RE.match(url))):
                raise ProfileError(f"owns_items.{i}.url",
                                   "A web address, or a portal page like /calendar.")
            items.append({"label": label, "url": url})
        return items or None
    if field == "photo_focus":
        text = str(value or "").strip()
        if not text:
            return None
        m = FOCUS_RE.match(text)
        if not m or int(m.group(1)) > 100 or int(m.group(2)) > 100:
            raise ProfileError(field, 'Two percentages, like "50% 12%".')
        return text
    if field == "directory_placement":
        value = str(value or "auto").strip().lower()
        if value not in PLACEMENTS:
            raise ProfileError(field, "One of auto, leadership, agents or hidden.")
        return value
    if field == "directory_order":
        if value in (None, ""):
            return None
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 999:
            raise ProfileError(field, "A whole number from 0 to 999.")
        return value
    raise ProfileError(field, "Unknown field.")


PROFILE_FIELDS = set(TEXT_LIMITS) | {"bring", "pronoun", "message_url", "owns_items",
                                     "photo_focus", "directory_placement", "directory_order"}

# ── what a person may change about THEMSELVES ────────────────────────────────────────────────
#
# Split by consequence, not by convenience. A profile field describes a colleague, and the person
# it describes is the best source for it -- an agent should not have to ask an admin to fix their
# own pronoun or a headline that is out of date. Anything that decides WHERE somebody appears,
# what they may reach, or who a CRM believes they are is the workspace's call, not theirs.
#
# An ALLOWLIST rather than PROFILE_FIELDS minus a deny set, so a field added later is admin-only
# until somebody decides otherwise: the safe direction to be wrong in. A test holds every field in
# PROFILE_FIELDS to being in exactly one of these two, so adding one forces the decision rather
# than defaulting it silently.
SELF_SERVICE_FIELDS = set(TEXT_LIMITS) | {"bring", "pronoun", "message_url", "owns_items",
                                          "photo_focus"}
# Where you sit in the directory, and in what order. A workspace decides how it presents itself.
ADMIN_ONLY_FIELDS = {"directory_placement", "directory_order"}

# Columns on the member row itself (not part of PROFILE_FIELDS) that a person owns about
# themselves. `full_name` is here deliberately: names change, getting someone's name wrong is not
# a small thing, and nothing keys off it -- services/member_identity matches a person to a CRM by
# agent_links, agent_email and email, never by name. `email` is NOT here: it is how they sign in.
SELF_SERVICE_COLUMNS = {"full_name", "title", "bio", "phone", "owns"}
COLUMN_LIMITS = {"full_name": 200, "title": 200, "phone": 200, "owns": 200, "bio": 4000}


def clean_settings(body: dict, *, sisu_connected: bool) -> dict:
    """The page's own settings. `featured_member_id` is checked by the router, which can see the
    roster; everything else here."""
    out = {}
    if "intro" in body:
        text = str(body["intro"] or "").strip()
        if len(text) > 300:
            raise ProfileError("intro", "At most 300 characters.")
        out["intro"] = text or None
    if "featured_label" in body:
        text = str(body["featured_label"] or "").strip()
        if len(text) > 60:
            raise ProfileError("featured_label", "At most 60 characters.")
        out["featured_label"] = text or None
    if "preview_count" in body:
        n = body["preview_count"]
        if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= PREVIEW_MAX:
            raise ProfileError("preview_count", f"A whole number from 0 (everyone) to {PREVIEW_MAX}.")
        out["preview_count"] = n
    if "stats" in body:
        stats = body["stats"] or []
        if not isinstance(stats, list) or len(stats) > 3:
            raise ProfileError("stats", "Up to three stats.")
        clean = []
        for i, stat in enumerate(stats):
            if not isinstance(stat, dict):
                raise ProfileError(f"stats.{i}", "Expected a label, a source and a value.")
            label = str(stat.get("label") or "").strip()
            source = str(stat.get("source") or "manual").strip()
            value = str(stat.get("value") or "").strip()
            if not label:
                raise ProfileError(f"stats.{i}.label", "Required.")
            if len(label) > 40:
                raise ProfileError(f"stats.{i}.label", "At most 40 characters.")
            if source not in STAT_SOURCES:
                raise ProfileError(f"stats.{i}.source", "Typed, team size, or a Sisu total.")
            if source in SISU_SOURCES and not sisu_connected:
                raise ProfileError(f"stats.{i}.source", "Sisu is not connected to this workspace.")
            if source == "manual" and not value:
                raise ProfileError(f"stats.{i}.value", "Required for a typed stat.")
            if len(value) > 24:
                raise ProfileError(f"stats.{i}.value", "At most 24 characters.")
            clean.append({"label": label, "source": source,
                          "value": value if source == "manual" else None})
        out["stats"] = clean
    return out


# ── who goes where ───────────────────────────────────────────────────────────────────────────

def placement(member, leadership_roles: set) -> str:
    """Where somebody sits: what an admin chose, or -- on auto -- Leadership for a leadership role
    and Agents for everyone else."""
    chosen = (member.directory_placement or "auto").lower()
    if chosen != "auto":
        return chosen
    return "leadership" if member.role_id in leadership_roles else "agents"


def listed(member, leadership_roles: set) -> bool:
    return member.status != "Removed" and placement(member, leadership_roles) != "hidden"


def money_short(value: float | int | None) -> str:
    """$268M, $9.4M, $241k: a figure on a band, not a ledger."""
    v = float(value or 0)
    if v >= 1e9:
        return f"${v / 1e9:.1f}B".replace(".0B", "B")
    if v >= 1e8:
        return f"${v / 1e6:.0f}M"
    if v >= 1e6:
        return f"${v / 1e6:.1f}M".replace(".0M", "M")
    if v >= 1e3:
        return f"${v / 1e3:.0f}k"
    return f"${v:.0f}"


def resolve_stats(stats: list, *, team_size: int, team: dict | None) -> list[dict]:
    """Each stat with its value filled in: typed, the listed team's size, or Sisu's totals."""
    out = []
    for stat in stats or []:
        source = stat.get("source") or "manual"
        if source == "team_size":
            value = f"{team_size:,}"
        elif source == "sisu_units_ytd":
            value = f"{int((team or {}).get('closed_units_ytd') or 0):,}" if team else None
        elif source == "sisu_volume_ytd":
            value = money_short((team or {}).get("closed_volume_ytd")) if team else None
        else:
            value = stat.get("value")
        if value:
            out.append({"label": stat.get("label"), "value": value})
    return out


def photo_version(key: str) -> str:
    """Changes whenever the stored photo does. The photo is cached for a few minutes, so a
    replaced one would otherwise be served from the cache under the same address."""
    return hashlib.sha1(key.encode()).hexdigest()[:10]


def card(member, *, role_name: str | None, you: bool) -> dict:
    """What every place a person appears needs: the agents grid, a leadership card, the band."""
    return {
        "id": str(member.id), "name": member.full_name, "title": member.title or role_name,
        "market": member.market, "headline": member.headline, "tag": member.tag,
        "help_line": member.help_line, "quote": member.quote,
        "photo_url": (f"/intranet/directory/{member.id}/photo?v={photo_version(member.photo_key)}"
                      if member.photo_key else None),
        "photo_focus": member.photo_focus or "50% 12%",
        # How to reach them rides on the card, as it always did: search finds people by address,
        # and the assistant answers "what's Justin's number". The bio is the profile's alone.
        "email": member.email, "phone": member.phone,
        "is_you": you,
        # What search and the assistant answer "who handles X" from.
        "owns": [item.get("label") for item in (member.owns_items or []) if item.get("label")],
    }


def profile(member, *, role_name: str | None, you: bool, owned_sops: list[dict]) -> dict:
    """The profile page: the card, and what only the profile shows."""
    pronoun = member.pronoun if member.pronoun in PRONOUNS else "they"
    email = member.email
    return {
        **card(member, role_name=role_name, you=you),
        "bio": member.bio, "bring": member.bring or [], "pronoun": pronoun,
        "headings": PRONOUNS[pronoun],
        "phone": member.phone, "email": email, "office": member.office,
        "message_url": member.message_url or (f"mailto:{email}" if email else None),
        "owns_items": [
            *({"label": f"{sop['title']}" + (f" · SOP {sop['version']}" if sop.get("version") else ""),
               "url": f"/sops#sop-{sop['id']}", "kind": "sop"} for sop in owned_sops),
            *({"label": item.get("label"), "url": item.get("url"), "kind": "link"}
              for item in (member.owns_items or []) if item.get("label")),
        ],
    }


# ── photos ───────────────────────────────────────────────────────────────────────────────────

def process_photo(data: bytes) -> bytes:
    """An upload, made safe to serve: checked by its bytes, turned upright, flattened onto white,
    at most PHOTO_EDGE on the long edge, re-encoded as a JPEG with no metadata at all."""
    if not data:
        raise ProfileError("photo", "The file is empty.")
    if len(data) > MAX_PHOTO_BYTES:
        raise ProfileError("photo", "At most 15 MB.")
    from PIL import Image, ImageOps, UnidentifiedImageError
    not_an_image = ProfileError("photo", "That file is not a JPEG, PNG or WebP image.")
    try:
        img = Image.open(io.BytesIO(data))       # reads the header only
    except Image.DecompressionBombError:
        raise ProfileError("photo", "That image is too large. At most 50 megapixels.")
    except (UnidentifiedImageError, OSError, ValueError):
        raise not_an_image
    fmt = img.format
    if fmt not in ("JPEG", "PNG", "WEBP"):
        raise not_an_image
    if img.size[0] * img.size[1] > MAX_PHOTO_PIXELS:
        raise ProfileError("photo", "That image is too large. At most 50 megapixels.")
    if fmt == "JPEG":
        # Decode at a reduced scale (1/2, 1/4 or 1/8, never below PHOTO_EDGE): a 48-megapixel
        # phone photo then costs a fraction of the memory on its way to 2000px.
        img.draft("RGB", (PHOTO_EDGE, PHOTO_EDGE))
    try:
        img.load()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise not_an_image
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        ground = Image.new("RGB", rgba.size, (255, 255, 255))
        ground.paste(rgba, mask=rgba.getchannel("A"))
        img = ground
    else:
        img = img.convert("RGB")
    img.thumbnail((PHOTO_EDGE, PHOTO_EDGE), Image.LANCZOS)
    out = io.BytesIO()
    # A new image carries no EXIF, ICC or comments: nothing of the original file's metadata.
    img.save(out, "JPEG", quality=86, optimize=True, progressive=True)
    return out.getvalue()
