"""Lead-source groups: which CRM source strings are one channel, and who pays for it.

Ported from the legacy reporting app's `lead_sources.py` (docs/specs/ulrg-reporting-dashboard-port.md
§6.2), with three differences that are Axcion's rather than the legacy app's:

  * A group belongs to a BUSINESS as well as a tenant. A tenant here has several companies, and the
    source strings are one CRM account's vocabulary.
  * There is no typed monthly cost. Spend comes from QuickBooks through the spend rules and is
    reduced by the cost shares (services/lead_source_spend.py, §4.7).
  * Nothing Utah Life-specific runs for any other tenant. `seed_ulrg` writes Utah Life's groups only
    where a business keyed `ulrg` exists, and does nothing anywhere else.

MATCHING, case-insensitive, an empty variant list matching nothing:
  exact     the stripped source equals a variant
  contains  a variant is a substring of the source (the default)
  regex     a variant, compiled with IGNORECASE, `search`es the source
"""
from __future__ import annotations

import datetime as dt
import re
import uuid
from decimal import Decimal
from typing import Callable

from sqlalchemy import select

from ..models import Business, LeadSourceCostShare, LeadSourceGroup, LeadSourceSpendRule

MATCH_MODES = ("exact", "contains", "regex")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
MAX_VARIANTS = 50
MAX_VARIANT_LEN = 120          # the width of lead.origin, which is what a variant is matched against
DELETED_PREFIX = "deleted_"
UNASSIGNED = {"key": "unassigned", "label": "Unassigned"}


class InvalidGroup(ValueError):
    """A group or rule the API refuses, with the sentence to show the admin."""


# ── matching ─────────────────────────────────────────────────────────────────────────────────

def build_matcher(variants, match_mode: str = "contains") -> Callable[[str | None], bool]:
    """A predicate over one source string. Regexes are compiled once, not per lead."""
    cleaned = [str(v).strip() for v in (variants or []) if v is not None and str(v).strip()]
    if not cleaned:
        return lambda _src: False
    if match_mode == "exact":
        wanted = {v.lower() for v in cleaned}
        return lambda src: (src or "").strip().lower() in wanted
    if match_mode == "regex":
        compiled = [re.compile(v, re.IGNORECASE) for v in cleaned]
        return lambda src: any(p.search(src or "") for p in compiled)
    lowered = [v.lower() for v in cleaned]
    return lambda src: any(v in (src or "").lower() for v in lowered)


def matcher_for(group: LeadSourceGroup) -> Callable[[str | None], bool]:
    return build_matcher(group.variants, group.match_mode)


def group_for(origin: str | None, lead_type: str | None, groups: list[LeadSourceGroup]) -> dict:
    """Which active group a deal belongs to (spec phase 7, shared with the cohort report).

    The linked lead's Follow Up Boss source first; Sisu's free-text `lead_type` only when there is
    no lead to read. Two groups matching go to the lower `display_order`. Nothing matching is
    `unassigned`, which the drawer shows as its own chip rather than guessing.
    """
    ordered = sorted((g for g in groups if g.is_active), key=lambda g: (g.display_order, g.display_name))
    text = origin if origin else lead_type
    if text:
        for g in ordered:
            if matcher_for(g)(text):
                return {"key": g.key, "label": g.display_name}
    return dict(UNASSIGNED)


# ── validation ───────────────────────────────────────────────────────────────────────────────

def clean_key(key) -> str:
    k = str(key or "").strip()
    if not (2 <= len(k) <= 64) or not KEY_RE.match(k):
        raise InvalidGroup("The key is 2 to 64 characters: lowercase letters, digits and underscores, "
                           "starting with a letter. For example realtor_com.")
    if k.startswith(DELETED_PREFIX):
        raise InvalidGroup(f"A key cannot start with {DELETED_PREFIX}, which marks a deleted group.")
    return k


def clean_variants(variants, match_mode: str) -> list[str]:
    """Trimmed, non-empty, each spelling once. Spellings that differ only in CASE are both kept:
    matching ignores case, but the FUB sync asks for each variant as `source=<variant>`, and the
    legacy dashboard listed "market vip via opcity bla" beside "Market VIP via Opcity BLA" on
    purpose -- whether FUB's filter ignores case has not been confirmed on a live account."""
    if variants is None:
        return []
    if not isinstance(variants, list):
        raise InvalidGroup("Variants are a list of source strings.")
    out, seen = [], set()
    for v in variants:
        s = str(v or "").strip()
        if not s or s in seen:
            continue
        if len(s) > MAX_VARIANT_LEN:
            raise InvalidGroup(f"A variant is at most {MAX_VARIANT_LEN} characters: {s[:40]}…")
        if match_mode == "regex":
            try:
                re.compile(s)
            except re.error as e:
                raise InvalidGroup(f"“{s}” is not a valid pattern ({e}).") from None
        seen.add(s)
        out.append(s)
    if len(out) > MAX_VARIANTS:
        raise InvalidGroup(f"A group holds at most {MAX_VARIANTS} variants.")
    return out


def clean_mode(mode) -> str:
    m = str(mode or "contains").strip().lower()
    if m not in MATCH_MODES:
        raise InvalidGroup("Match mode is exact, contains or regex.")
    return m


def clean_money(value, name: str) -> Decimal:
    try:
        d = Decimal(str(value))
    except Exception:  # noqa: BLE001 -- anything that is not a number is the same refusal
        raise InvalidGroup(f"{name} must be a number.") from None
    if d < 0 or d > Decimal(10000):
        raise InvalidGroup(f"{name} is between $0 and $10,000.")
    return d.quantize(Decimal("0.01"))


def clean_rule(rule: dict) -> dict:
    """One spend rule, or the refusal. At least one of account, vendor, class."""
    acct = str(rule.get("account_qbo_id") or "").strip() or None
    vendor = str(rule.get("vendor_qbo_id") or "").strip() or None
    klass = str(rule.get("class_label") or "").strip() or None
    if not (acct or vendor or klass):
        raise InvalidGroup("A spend rule needs an account, a vendor or a class. A rule with none of "
                           "them would count every expense.")
    for v, n in ((acct, "Account"), (vendor, "Vendor")):
        if v and len(v) > 32:
            raise InvalidGroup(f"{n} id is at most 32 characters.")
    return {"account_qbo_id": acct, "vendor_qbo_id": vendor, "class_label": klass}


def clean_shares(shares: list[dict]) -> list[dict]:
    out, seen, total = [], set(), Decimal(0)
    for sh in shares or []:
        try:
            pct = Decimal(str(sh.get("share_pct")))
        except Exception:  # noqa: BLE001
            raise InvalidGroup("A cost share is a percent between 0 and 100.") from None
        if pct < 0 or pct > 100:
            raise InvalidGroup("A cost share is a percent between 0 and 100.")
        bearer = str(sh.get("bearer_business_id") or "").strip()
        if not bearer:
            raise InvalidGroup("A cost share names the business that bears it.")
        if bearer in seen:
            raise InvalidGroup("Each business bears one share of a group.")
        seen.add(bearer)
        total += pct
        out.append({"bearer_business_id": bearer, "share_pct": pct.quantize(Decimal("0.01"))})
    if total > 100:
        raise InvalidGroup(f"The shares add up to {total.normalize():f}%. Together they can be at "
                           "most 100%.")
    return out


# ── reads ────────────────────────────────────────────────────────────────────────────────────

async def groups_for_business(s, tenant_id, business_id, *, include_inactive=False,
                              include_deleted=False) -> list[LeadSourceGroup]:
    q = select(LeadSourceGroup).where(LeadSourceGroup.tenant_id == tenant_id,
                                      LeadSourceGroup.business_id == business_id)
    if not include_inactive:
        q = q.where(LeadSourceGroup.is_active.is_(True))
    rows = (await s.execute(q.order_by(LeadSourceGroup.display_order,
                                       LeadSourceGroup.display_name))).scalars().all()
    if not include_deleted:
        rows = [g for g in rows if not g.key.startswith(DELETED_PREFIX)]
    return list(rows)


async def matching_origins(s, tenant_id, business_id, group: LeadSourceGroup) -> list[str]:
    """The distinct Follow Up Boss source strings on this business that the group matches.

    Matched in Python over DISTINCT origins rather than per lead: an account holds a few hundred
    source strings and over a hundred thousand people, and this is what lets exact, contains and
    regex all run the same way without a regex in SQL.
    """
    from ..models import Lead
    m = matcher_for(group)
    rows = (await s.execute(select(Lead.origin).where(
        Lead.tenant_id == tenant_id, Lead.business_id == business_id,
        Lead.origin.is_not(None)).distinct())).scalars().all()
    return sorted(o for o in rows if m(o))


async def scope_filter(s, tenant_id, business_id, groups=None):
    """A WHERE clause for the leads inside ANY active group on this business, from each group's
    program start -- the only people the activity walk and the Sisu lookups ever touch. None when
    nobody is in scope, so a caller can skip the query entirely."""
    from sqlalchemy import and_, or_
    from ..models import Lead
    if groups is None:
        groups = await groups_for_business(s, tenant_id, business_id)
    parts = []
    for g in groups:
        if not g.is_active:
            continue
        origins = await matching_origins(s, tenant_id, business_id, g)
        if not origins:
            continue
        cond = Lead.origin.in_(origins)
        if g.program_start:
            cond = and_(cond, Lead.created_at_src >= g.program_start)
        parts.append(cond)
    if not parts:
        return None
    return and_(Lead.tenant_id == tenant_id, Lead.business_id == business_id, or_(*parts))


def group_dict(g: LeadSourceGroup, *, rules=None, shares=None, businesses=None) -> dict:
    names = {b.id: b.name for b in (businesses or [])}
    out = {
        "id": str(g.id), "key": g.key, "display_name": g.display_name,
        "variants": list(g.variants or []), "match_mode": g.match_mode,
        "program_start": g.program_start.isoformat() if g.program_start else None,
        "is_active": bool(g.is_active), "display_order": int(g.display_order or 0),
        "exp_risk_team_default": float(g.exp_risk_team_default or 0),
        "exp_risk_agent_default": float(g.exp_risk_agent_default or 0),
        # Active with no variants matches nobody. The report shows that as an empty state, and
        # Settings says it beside the group, rather than either one treating it as an error.
        "matches_nothing": not any(str(v).strip() for v in (g.variants or [])),
    }
    if rules is not None:
        out["spend_rules"] = [rule_dict(r) for r in rules]
        out["spend_mapped"] = bool(rules)
    if shares is not None:
        out["cost_shares"] = [{"bearer_business_id": str(sh.bearer_business_id),
                               "bearer_name": names.get(sh.bearer_business_id),
                               "share_pct": float(sh.share_pct)} for sh in shares]
    return out


def rule_dict(r: LeadSourceSpendRule) -> dict:
    return {"id": str(r.id), "business_id": str(r.business_id), "account_qbo_id": r.account_qbo_id,
            "vendor_qbo_id": r.vendor_qbo_id, "class_label": r.class_label}


# ── the Utah Life seed (§6.2, §4.7, §8) ──────────────────────────────────────────────────────

ULRG_KEY = "ulrg"
SYMPLI_KEY = "sympli"
REALTOR_VARIANTS = [
    "Realtor.com", "Realtor.com MVIP", "Realtor MVIP", "Market VIP via Opcity BLA",
    "market vip via opcity bla", "realtor.com mvip",
]
# Account 365 (Realtor.com, under 62130 Internet Lead Generation), vendor 962 (Realtor.com) and
# vendor 1568 (Opcity, Inc). A line matching more than one counts once.
REALTOR_SPEND_RULES = [{"account_qbo_id": "365"}, {"vendor_qbo_id": "962"},
                       {"vendor_qbo_id": "1568"}]
REALTOR_PROGRAM_START = dt.date(2026, 2, 1)
SYMPLI_SHARE = Decimal("50.00")


def seed_ulrg(sess, tenant_id=None) -> int:
    """Write Utah Life's lead-source rows where a business keyed `ulrg` exists. Idempotent.

    SYNC on purpose: the migration calls it with `Session(bind=op.get_bind())`, and an async caller
    passes it through `AsyncSession.run_sync`. A group that already exists is left exactly as an
    admin last saved it -- the seed fills in what is missing and never overwrites a decision.

    Only the brokerage. Not the other tenants, not a dollar amount, not a class, and not a rule on
    Sympli's books: Sympli's QuickBooks is not connected, and the 50% is the agreement, not a pull.
    Returns the number of rows written.
    """
    q = select(Business).where(Business.key == ULRG_KEY)
    if tenant_id is not None:
        q = q.where(Business.tenant_id == tenant_id)
    written = 0
    for ulrg in sess.execute(q).scalars().all():
        tid = ulrg.tenant_id
        existing = {g.key: g for g in sess.execute(select(LeadSourceGroup).where(
            LeadSourceGroup.tenant_id == tid, LeadSourceGroup.business_id == ulrg.id)).scalars()}
        realtor = existing.get("realtor_com")
        if realtor is None:
            realtor = LeadSourceGroup(
                id=uuid.uuid4(), tenant_id=tid, business_id=ulrg.id, key="realtor_com",
                display_name="Realtor.com", variants=list(REALTOR_VARIANTS), match_mode="contains",
                program_start=REALTOR_PROGRAM_START, is_active=True, display_order=0,
                exp_risk_team_default=Decimal(69), exp_risk_agent_default=Decimal(49))
            sess.add(realtor)
            sess.flush()
            written += 1
            for rule in REALTOR_SPEND_RULES:
                sess.add(LeadSourceSpendRule(id=uuid.uuid4(), tenant_id=tid,
                                             lead_source_group_id=realtor.id, business_id=ulrg.id,
                                             **rule))
                written += 1
            sympli = sess.execute(select(Business).where(
                Business.tenant_id == tid, Business.key == SYMPLI_KEY)).scalars().first()
            if sympli is not None:
                sess.add(LeadSourceCostShare(id=uuid.uuid4(), tenant_id=tid,
                                             lead_source_group_id=realtor.id,
                                             bearer_business_id=sympli.id, share_pct=SYMPLI_SHARE))
                written += 1
        if "realty_com" not in existing:
            # Inactive and empty, on purpose (§12.3): nobody has recorded which Follow Up Boss strings
            # are Realty.com rather than Realtor.com, so none are invented, and its QuickBooks rule
            # (account 579, vendor 1311) waits until somebody activates the group.
            sess.add(LeadSourceGroup(
                id=uuid.uuid4(), tenant_id=tid, business_id=ulrg.id, key="realty_com",
                display_name="Realty.com", variants=[], match_mode="contains", program_start=None,
                is_active=False, display_order=10, exp_risk_team_default=Decimal(69),
                exp_risk_agent_default=Decimal(49)))
            written += 1
    return written
