"""Finding a business by what it IS, not by what it is called.

The compute layer used to locate businesses with literal comparisons — `Business.key ==
"ulrg"`, `bmap.get("sympli")`, `if b.key == "springb"` — about thirty of them across eleven
files. Those strings are one customer's names for their own companies, so every one of them was
a place a second tenant's dashboard would come up empty: not an error, just a panel with
nothing in it and no explanation.

`Business.kind` is the axis those comparisons were really reaching for, and it already exists.
Migration 0014_business_routing added it precisely to remove "the hardcoded springb→forum/
becollective synthesis" (its own words) and backfilled ulrg→real_estate,
sympli→commission_jv, springb→membership. The column landed; the dispatch was never moved onto
it. This module is that move.

  ulrg    -> real_estate    the brokerage: transactions, agents, GCI, listings
  sympli  -> commission_jv  a joint venture paid per closing, with its own cost-of-sale split
  springb -> membership     programs and cohorts sold as memberships

PRIMARY, not THE. Nothing stops a tenant having two brokerages, and the old code silently
assumed exactly one of each. `primary()` returns the lowest `sort_order` — the same business
the old lookup would have found for Spring, and a defined answer for anyone else. Where a
caller genuinely wants all of them, `of_kind()` returns the list.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Business

REAL_ESTATE = "real_estate"
COMMISSION_JV = "commission_jv"
MEMBERSHIP = "membership"
HOLDING = "holding"

KINDS = (REAL_ESTATE, COMMISSION_JV, MEMBERSHIP, HOLDING)


async def all_businesses(s: AsyncSession, tenant_id) -> list[Business]:
    """Every business for a tenant, in nav order. One query, so callers that need several
    lookups can do them off one list instead of a query per kind."""
    return list((await s.execute(select(Business).where(
        Business.tenant_id == tenant_id).order_by(Business.sort_order))).scalars().all())


def pick(businesses: list[Business], kind: str) -> Business | None:
    """The primary business of `kind` from an already-loaded list, or None."""
    for b in businesses:
        if b.kind == kind:
            return b
    return None


def group(businesses: list[Business]) -> dict[str, list[Business]]:
    out: dict[str, list[Business]] = {}
    for b in businesses:
        out.setdefault(b.kind, []).append(b)
    return out


async def of_kind(s: AsyncSession, tenant_id, kind: str) -> list[Business]:
    return [b for b in await all_businesses(s, tenant_id) if b.kind == kind]


async def primary(s: AsyncSession, tenant_id, kind: str) -> Business | None:
    """The business of `kind` this tenant's dashboard means when it says "the brokerage".

    Returns None when the tenant has none — which is a normal state, not an error: a
    membership-only customer has no brokerage, and the panels that need one should render
    empty rather than raise.
    """
    return (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id, Business.kind == kind)
        .order_by(Business.sort_order).limit(1))).scalars().first()


async def primary_id(s: AsyncSession, tenant_id, kind: str) -> uuid.UUID | None:
    b = await primary(s, tenant_id, kind)
    return b.id if b else None


async def real_estate(s: AsyncSession, tenant_id) -> Business | None:
    return await primary(s, tenant_id, REAL_ESTATE)


async def membership(s: AsyncSession, tenant_id) -> Business | None:
    return await primary(s, tenant_id, MEMBERSHIP)


async def commission_jv(s: AsyncSession, tenant_id) -> Business | None:
    return await primary(s, tenant_id, COMMISSION_JV)


async def flywheel_pair(s: AsyncSession, tenant_id) -> tuple[Business | None, Business | None]:
    """(brokerage, JV) — the referral flywheel's two halves.

    Returned together because the flywheel is meaningless with only one of them, and every
    caller needs to make the same "both or nothing" decision.
    """
    biz = await all_businesses(s, tenant_id)
    return pick(biz, REAL_ESTATE), pick(biz, COMMISSION_JV)


# Words that describe what a company IS rather than who it is. Dropping a trailing one leaves
# the distinguishing part of the name, which is what prose wants: "Sympli Mortgage" -> "Sympli".
_GENERIC_SUFFIXES = {
    "mortgage", "lending", "realty", "team", "group", "holdings", "partners", "capital",
    "collective", "brokerage", "co", "llc", "inc", "corp", "ltd", "company",
}


def short_name(b) -> str:
    """A business's name as prose uses it — "ULRG" where the full name is "ULRG + Team".

    Copy that names two businesses in one sentence (the referral flywheel does it a dozen
    times) reads badly with full legal-ish names: "Sympli Mortgage logged us, no ULRG + Team
    deal". So this shortens, in three steps, each of which fails safe:

      1. `config["short_name"]` — an explicit answer always wins.
      2. Drop anything after " + ", which joins a name to a qualifier ("ULRG + Team").
      3. Drop ONE trailing generic word ("Sympli Mortgage" -> "Sympli", "Coastal Realty" ->
         "Coastal") — but only when something meaningful is left.

    Never guesses beyond that. "The Guild" keeps both words, because the alternative rule that
    would shorten it ("take the first word") returns "The". A name it does not recognise comes
    back whole, which is always correct if occasionally wordy.
    """
    if b is None:
        return ""
    explicit = ((b.config or {}).get("short_name") or "").strip()
    if explicit:
        return explicit
    name = (b.name or "").strip()
    base = name.split(" + ")[0].strip() or name
    parts = base.split()
    if len(parts) > 1 and parts[-1].lower().strip(".,") in _GENERIC_SUFFIXES:
        trimmed = " ".join(parts[:-1]).strip()
        if trimmed and trimmed.lower() not in {"the", "a", "an"}:
            return trimmed
    return base or name


# ── tenant brand ──────────────────────────────────────────────────────────────────────
# The chrome — the page title, the wordmark in the rail, the login screen — was one
# customer's identity compiled into the bundle. It is tenant data now.
#
# NOTE ON ASSETS: a logo is a FILE, and this platform has no per-tenant asset upload yet.
# So a tenant with no logo configured does not inherit somebody else's: it renders its own
# NAME as a wordmark. That degrades honestly and needs no upload feature to be correct.
BRAND_DEFAULTS = {
    "product_name": "Command Center",
    "logo": None,          # URL of a transparent-ground mark, tinted via CSS mask
    "logomark": None,      # the square/bare form, for tight spaces
    # token name -> hex, and font slot -> stack. EMPTY means "use the platform's own identity",
    # which is the honest default: a workspace that has not chosen colours should look like
    # Acumyn, never like whichever customer happened to be built first.
    "palette": {},
    # The five a workspace chose. Undeclared keys are DROPPED by brand(), which is how typeface
    # went missing and how this went missing with it — the browser received neither an explicit
    # palette nor the seeds to derive one, so it fell back to the platform's colours while the
    # database held the workspace's.
    "seeds": {},
    # The pairing NAME. Without this key brand() silently drops it — every key not declared here
    # is dropped — so a workspace's stored typeface never reached the browser and the SPA fell
    # back to fetching the platform's fonts while naming the workspace's. The variables said
    # Poppins; nothing had downloaded Poppins.
    "typeface": None,
    # Legacy: three raw font stacks, from before pairings existed. Kept so an old workspace still
    # resolves, but the browser maps it to a pairing rather than applying it raw — naming a family
    # and fetching it are different jobs, and applying a stack does only the first.
    "type": {},
    # The sign-in screen, which renders before there is a session and so cannot read any of the
    # above from /me. None means the neutral ribbed hero tinted by the palette — which is a real
    # answer for any workspace, not a placeholder. These used to be two hardcoded file paths, so
    # every workspace's sign-in screen showed the first customer's photograph.
    "hero_image": None,    # full-bleed background behind the sign-in card
    "photo": None,         # the masked panel on the right at >900px
    # Ground-colour slot -> image URL, for the hero BANDS inside the product (the dark plate
    # behind the headline number on Portfolio, Books, Ads, the Forum). Migration 0050 wrote eight
    # of these for the workspace that owns them and the browser never saw one, because this key
    # was not declared here: the FOURTH setting lost to that, after `typeface`, `seeds` and the
    # marks. Empty means the platform's own bokeh plate, tinted to whatever colours the workspace
    # configured -- a real answer for any brand rather than a placeholder.
    "hero_plates": {},
    # ── the sign-in screen's own settings. It renders with no session, so these ride on
    # /public/brand rather than /me, and every one of them is a thing the sign-in design
    # exposed as a knob. Undeclared keys are dropped silently by brand(), which has now cost
    # four settings; these are declared before anything writes them.
    "tagline": None,          # the line on the dark plate. None renders no line rather than
                              # a claim invented on the workspace's behalf.
    "plate_side": "left",     # left | right
    "button_shape": "pill",   # pill | square
    "remember_me": True,      # show the checkbox at all
}


def platform_brand() -> dict:
    """Acumyn's own identity — what an unresolved host, or a surface with no workspace behind it
    yet, is honestly branded as. Empty palette/type means the SPA keeps its compiled-in defaults,
    which ARE Acumyn's; sending them again over the wire would be a second copy to keep in step."""
    return {**BRAND_DEFAULTS, "display_name": "Acumyn", "product_name": "Acumyn",
            "palette": {}, "type": {}, "is_platform": True}


def brand(tenant) -> dict:
    """This tenant's identity for the UI chrome. Never falls back to another tenant's."""
    cfg = ((tenant.config or {}).get("brand") or {}) if tenant is not None else {}
    out = dict(BRAND_DEFAULTS)
    for k in out:
        v = cfg.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()
        elif v is not None and k not in ("logo", "logomark"):
            out[k] = v
    # The wordmark text: what the app calls itself for THIS customer.
    out["display_name"] = (cfg.get("display_name") or (tenant.name if tenant else "") or "").strip()
    return out
