"""The per-entity chart-of-accounts map: sync, subtree rules, and the mapping screen's data
(SPEC-coa-mapping-provenance section 5, Phase 2).

Three ideas, in order of importance.

**Identity is the QBO account ID, always.** Names change, duplicate across entities, and QBO
happily holds two accounts with the same number at different levels — ULRG carries "69000
Other Expense" and "69000 Insurance", both with the number embedded in the NAME rather than in
AcctNum. Every write here keys on `qbo_account_id`.

**A new account is never silently dropped.** It lands unmapped and surfaces for a decision.
From Phase 3 an unmapped account *with activity* refuses to render rather than quietly
omitting itself, because a statement that leaves accounts out is worse than no statement: it
looks right.

**Rules exist because people are accounts.** Phase 0 found the humans concentrated in a few
subtrees rather than scattered — ULRG's "61300 Contract Labor:Virtual Assistants:*", Spring B's
"Contract Labor:*". One prefix rule maps the subtree and every future hire inside it. Without
that, the next VA hired trips the unmapped guard and blocks the close.
"""
from __future__ import annotations

import datetime as dt
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..integrations import qbo
from ..models import Business, CoaMap, CoaMapRule, Integration, StandardAccount, SyncRun
from .audit import audit
from .sync import _valid_access_token


# ── matching ──────────────────────────────────────────────────────────────────────────────

def normalize_fqn(s: str | None) -> str:
    """Fold an account path for matching: lowercase, collapse runs of whitespace, strip.

    Case-insensitive on purpose. A bookkeeper writing the rule `contract labor:` means the
    subtree QBO spells `Contract Labor:`, and making them match the capitalization exactly is
    a trap with no upside.
    """
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def match_rule(fqn: str | None, rules) -> CoaMapRule | None:
    """The rule that governs this account: the LONGEST matching prefix.

    Longest-wins is what a hierarchy needs and it is fully deterministic without a priority
    column — two different prefixes of equal length cannot both be prefixes of one string, so
    there is never a tie to break. `61300 Contract Labor:Virtual Assistants:` therefore beats
    `61300 Contract Labor:` on a VA, with nothing to configure.
    """
    target = normalize_fqn(fqn)
    if not target:
        return None
    best, best_len = None, -1
    for r in rules:
        if not r.is_active or r.match_type != "prefix":
            continue
        pat = normalize_fqn(r.pattern)
        if pat and target.startswith(pat) and len(pat) > best_len:
            best, best_len = r, len(pat)
    return best


async def _rules_for(s: AsyncSession, tenant_id, business_id) -> list[CoaMapRule]:
    """Active rules that can govern this entity: its own, plus the tenant-wide ones."""
    return list((await s.execute(
        select(CoaMapRule).where(
            CoaMapRule.tenant_id == tenant_id,
            CoaMapRule.is_active.is_(True),
            (CoaMapRule.business_id == business_id) | (CoaMapRule.business_id.is_(None)),
        ))).scalars())


async def apply_rules(s: AsyncSession, tenant_id, business_id, rows=None) -> dict:
    """(Re)apply the rule set to an entity's map. Returns {mapped, remapped, cleared}.

    Only rows whose `mapped_via` is `rule` or NULL are touched. A human decision outranks a
    pattern permanently: once somebody maps an account by hand, no later rule edit moves it.
    That asymmetry is the point — rules are for the accounts nobody wants to think about.

    `cleared` counts rows a rule used to own and no longer matches. They go back to unmapped
    rather than keeping a stale answer, so the guard can see them.
    """
    rules = await _rules_for(s, tenant_id, business_id)
    if rows is None:
        rows = list((await s.execute(select(CoaMap).where(
            CoaMap.tenant_id == tenant_id, CoaMap.business_id == business_id))).scalars())
    now = dt.datetime.now(dt.timezone.utc)
    stat = {"mapped": 0, "remapped": 0, "cleared": 0}
    for row in rows:
        if row.mapped_via == "manual" or row.is_ignored:
            continue
        rule = match_rule(row.qbo_account_fqn or row.qbo_account_name, rules)
        if rule is None:
            if row.mapped_via == "rule":                  # the rule that owned it is gone
                row.standard_account_id = row.rule_id = row.mapped_via = None
                row.mapped_at = now
                stat["cleared"] += 1
            continue
        if row.standard_account_id == rule.standard_account_id and row.rule_id == rule.id:
            continue
        stat["remapped" if row.standard_account_id else "mapped"] += 1
        row.standard_account_id = rule.standard_account_id
        row.rule_id, row.mapped_via, row.mapped_at = rule.id, "rule", now
    return stat


# ── sync ──────────────────────────────────────────────────────────────────────────────────

def _account_row(acct: dict) -> dict:
    """A QBO Account object reduced to the source-owned columns of coa_map."""
    fqn = acct.get("FullyQualifiedName") or acct.get("Name") or ""
    atype = acct.get("AccountType") or ""
    return {
        "qbo_account_id": str(acct.get("Id") or ""),
        "qbo_account_name": (acct.get("Name") or "")[:200],
        "qbo_account_fqn": fqn[:400] or None,
        "qbo_account_type": atype[:60] or None,
        "qbo_active": bool(acct.get("Active", True)),
    }


async def sync_coa_accounts(s: AsyncSession, tenant_id, integ: Integration) -> int:
    """Pull one entity's full chart from QBO into coa_map, then run the rules (SPEC 5.1).

    READ-ONLY against QuickBooks, in this phase and every later one.

    Upserted in Python rather than through a dialect INSERT..ON CONFLICT: a chart is a few
    hundred rows, this runs once per pass, and staying dialect-neutral is what lets the whole
    path be tested on SQLite instead of only observed in prod.

    Only the source-owned columns refresh on a re-sync. `standard_account_id`, `mapped_via`,
    `is_ignored` and the rest belong to the human and to the rules, and a rename in QuickBooks
    must never quietly discard a mapping decision.
    """
    token = await _valid_access_token(s, integ)
    # Inactive accounts included: a deactivated account keeps its balance, so a historical
    # trial balance still names it. Without them it lands as "not yet synced" and blocks a
    # statement it cannot be mapped out of, because it never reaches the mapping screen.
    accounts = await qbo.accounts(integ.realm_id, token, include_inactive=True)

    have = {r.qbo_account_id: r for r in (await s.execute(select(CoaMap).where(
        CoaMap.tenant_id == tenant_id, CoaMap.business_id == integ.business_id))).scalars()}
    seen: list[CoaMap] = []
    fresh = 0
    for acct in accounts:
        row = _account_row(acct)
        if not row["qbo_account_id"]:
            continue
        existing = have.get(row["qbo_account_id"])
        if existing is None:
            existing = CoaMap(tenant_id=tenant_id, business_id=integ.business_id, **row)
            s.add(existing)
            fresh += 1
        else:
            for k, v in row.items():
                setattr(existing, k, v)
        seen.append(existing)

    await s.flush()                       # new rows need ids before apply_rules touches them
    stat = await apply_rules(s, tenant_id, integ.business_id, rows=seen)
    audit(s, tenant_id, None, "coa.sync_accounts", target_type="business",
          target_id=integ.business_id, detail={"accounts": len(seen), "new": fresh, **stat})
    await s.commit()
    print(f"[coa_sync] realm={integ.realm_id} accounts={len(seen)} new={fresh} "
          f"rules={stat}", flush=True)
    return len(seen)


async def run_coa_sync(s: AsyncSession, tenant_id, integ: Integration) -> None:
    """Worker entry point. Its own SyncRun so a chart-pull failure is recorded and surfaced
    without flipping the qbo integration to error — the dashboard's P&L snapshot rides a
    different run and stays healthy on its own."""
    run = SyncRun(tenant_id=tenant_id, provider="qbo_coa")
    s.add(run)
    await s.commit()
    started = dt.datetime.utcnow()
    try:
        records = await sync_coa_accounts(s, tenant_id, integ)
        run.status, run.stats = "ok", {
            "records": records,
            "seconds": round((dt.datetime.utcnow() - started).total_seconds(), 1)}
    except Exception as e:  # noqa: BLE001 — record + surface, never abort the pass
        run.status, run.detail = "error", str(e)
        run.stats = {"records": None,
                     "seconds": round((dt.datetime.utcnow() - started).total_seconds(), 1)}
        print(f"[coa_sync] failed: {e}", flush=True)
    run.finished_at = dt.datetime.utcnow()
    await s.commit()


# ── suggestions ───────────────────────────────────────────────────────────────────────────
# A first pass, never applied automatically. 254 accounts carry activity across the portfolio
# and somebody has to look at every one of them; the job of this table is to make most of those
# looks a confirmation rather than a decision.
#
# Keyed to the CURRENT chart (SPEC-chart-of-accounts). The Phase 0 discovery script carries its
# own copy against the OLD numbering — that one is frozen with the report it produced and is
# not the source for this.

_HINTS: dict[str, tuple[str, ...]] = {
    # Cost of sale
    "5010": ("listing side", "listing cos", "listing commission"),
    "5020": ("buyer side", "buyer cos", "buyer commission"),
    "5030": ("loan officer", "lo comp", "originator comp"),
    "5040": ("special forces", "empirebuilders", "empire builders", "closer commission",
             "sales commission"),
    "5050": ("referral fee", "referral cos", "referral paid"),
    "5110": ("broker fee", "franchise fee", "place fee", "royalty fee"),
    "5120": ("transaction coordinat", "tc fee"),
    "5130": ("credit report", "verification"),
    "5140": ("appraisal", "inspection"),
    "5210": ("venue", "facility rental", "event space"),
    "5220": ("food", "beverage", "catering"),
    "5230": ("speaker", "talent fee"),
    "5240": ("production", "staging", "audio visual", " av "),
    "5250": ("event staff", "event labor"),
    "5260": ("swag", "attendee material", "workbook"),
    "5290": ("event expense", "program expense", "delivery cost"),
    "5270": ("delivery platform", "member platform"),
    "5310": ("merchant fee", "merchant processing", "stripe fee", "card processing"),
    "5320": ("payment platform", "paypal fee"),
    "5330": ("financing cost", "installment", "affirm", "klarna"),
    "5340": ("chargeback fee",),
    "5490": ("cost of goods sold", "cost of sales", "cost of sale"),
    # Advertising
    "6010": ("print", "direct mail", "mailer"),
    "6020": ("billboard",),
    "6030": ("radio",),
    "6040": ("seo", "google ads", "adwords", "ppc"),
    "6050": ("facebook", "instagram", "social media", "meta ads"),
    "6060": ("lead buy", "zillow", "realtor com"),
    "6061": ("lead source", "internet lead", "lead generation"),
    "6070": ("sign purchase",),
    "6080": ("sign install",),
    "6090": ("advertising", "promotion", "marketing"),
    # Sales promotion
    "6510": ("travel", "airfare", "flight"),
    "6520": ("lodging", "hotel"),
    "6530": ("meals", "entertainment", "dining", "restaurant"),
    "6540": ("gift",),
    "6550": ("coaching", "training", "education", "mastermind", "tuition"),
    "6560": ("conference", "convention", "summit", "event registration"),
    "6570": ("charitable", "donation"),
    # Occupancy
    "7010": ("rent", "lease"),
    "7020": ("utilit", "electric", "water", "gas bill"),
    "7030": ("repair", "maintenance", "janitorial", "cleaning"),
    "7040": ("commercial property tax", "real property tax"),
    "7090": ("occupancy",),
    # Office
    "7510": ("telephone", "phone line", "landline"),
    "7520": ("internet", "broadband", "wifi"),
    "7530": ("mobile", "cell phone", "cellular"),
    "7540": ("communication equipment", "communication", "telecom"),
    "7550": ("office supplies", "supplies"),
    "7560": ("postage", "shipping", "fedex", "ups store"),
    "7570": ("equipment rental", "copier", "printer lease"),
    "7580": ("computer hardware", "laptop", "monitor"),
    "7590": ("office expense", "office cost"),
    # People
    "8010": ("salary", "salaries", "wages", "payroll expense", "compensation"),
    "8020": ("payroll tax", "employer tax", "fica", "futa", "suta"),
    "8030": ("benefit", "health insurance", "401k", "retirement"),
    "8040": ("gusto", "adp", "payroll fee", "payroll processing"),
    "8050": ("contract labor", "contractor", "1099", "outside services"),
    "8060": ("virtual assistant", "offshore"),
    # G&A
    "8510": ("dues", "subscription", "membership fee"),
    "8520": ("software", "saas", "technology subscription", "app subscription"),
    "8530": ("vehicle", "auto", "mileage", "fuel"),
    "8540": ("accounting", "bookkeeping", "tax prep", "cpa"),
    "8550": ("legal", "attorney", "lawyer"),
    "8560": ("consulting", "professional service"),
    "8570": ("personal property tax",),
    "8580": ("business license", "permit"),
    "8590": ("use tax",),
    "8600": ("e and o insurance", "e o insurance", "errors and omissions"),
    "8610": ("insurance",),
    "8620": ("bank charge", "bank fee", "service charge", "wire fee"),
    "8630": ("bad debt", "write off", "write-off"),
    "8690": ("general business", "general admin", "miscellaneous"),
    # Revenue
    "4010": ("listing income", "listing side income"),
    "4020": ("buyer income", "buyer side income"),
    "4030": ("referral income", "referral fee income"),
    "4040": ("loan origination", "origination commission"),
    "4050": ("admin fee", "transaction fee income", "compliance fee"),
    "4210": ("membership income", "membership revenue", "becollective", "be collective"),
    "4220": ("mastermind", "forum income"),
    "4230": ("coaching income", "consulting income"),
    "4240": ("course", "digital product"),
    "4290": ("program income", "program revenue"),
    "4410": ("ticket", "general admission"),
    "4420": ("vip", "upgrade"),
    "4430": ("sponsorship", "sponsor income"),
    "4440": ("merchandise", "onsite sales"),
    "4490": ("event income", "event revenue"),
    "4510": ("rental income",),
    "4610": ("joint venture", "jv"),
    "4620": ("revenue share",),
    "4630": ("royalty", "licensing income"),
    "4910": ("refund", "cancellation"),
    "4920": ("chargeback",),
    "4930": ("discount", "scholarship"),
    "4940": ("failed payment", "written off payment", "uncollected"),
    # Below the line
    "9010": ("interest income",),
    "9020": ("gain on", "loss on disposal", "asset disposal"),
    "9030": ("place expense reimbursement", "place reimbursement"),
    "9040": ("place profit share", "place 328", "profit share"),
    "9110": ("interest expense", "mortgage interest", "loan interest", "interest paid"),
    "9210": ("depreciation",),
    "9220": ("amortization",),
    "9910": ("other expense", "misc expense"),
    "9310": ("franchise tax", "state income tax"),
    "9410": ("charitable", "donation", "contribution to"),
    # Balance sheet
    "1000": ("checking", "operating cash", "savings", "money market"),
    "1050": ("undeposited",),
    "1100": ("accounts receivable",),
    "1200": ("prepaid",),
    "1300": ("due from", "receivable from affiliate", "intercompany receivable"),
    "1500": ("fixed asset", "equipment at cost", "leasehold improvement"),
    "1590": ("accumulated depreciation",),
    "2000": ("accounts payable",),
    "2100": ("credit card", "amex", "visa "),
    "2200": ("accrued", "payroll liabilit", "wages payable", "tax to pay"),
    "2300": ("deferred revenue", "unearned revenue"),
    "2400": ("due to", "payable to affiliate", "intercompany payable"),
    "2500": ("loan payable", "note payable", "line of credit"),
    "3000": ("member equity", "owner equity", "capital contribution"),
    "3100": ("draw", "distribution"),
    "3200": ("retained earnings",),
    "3900": ("opening balance equity",),
}

# QBO AccountType -> the standard sections it can legitimately land in. Narrows the search so
# an expense account is never suggested a revenue code, whatever the words happen to say.
_TYPE_SECTIONS: dict[str, tuple[str, ...]] = {
    "Bank": ("asset",), "Other Current Asset": ("asset",), "Fixed Asset": ("asset",),
    "Accounts Receivable": ("asset",), "Other Asset": ("asset",),
    "Credit Card": ("liability",), "Accounts Payable": ("liability",),
    "Other Current Liability": ("liability",), "Long Term Liability": ("liability",),
    "Equity": ("equity",),
    "Income": ("revenue",), "Other Income": ("revenue", "other_income"),
    "Cost of Goods Sold": ("cogs",),
    "Expense": ("opex", "cogs"), "Other Expense": ("other_expense", "opex"),
}


def _fold(s: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())).strip()


_HINT_RE: dict[str, "re.Pattern"] = {}


def _hint_pattern(hint: str) -> "re.Pattern":
    """A hint matches at the START of a word, and may run on into a longer one.

    Plain substring matching read "66000 Automobile" as Mobile Phone, because "mobile" is
    inside "automobile". Requiring a word boundary at the front fixes that. Allowing letters
    to follow keeps the prefix hints working — "utilit" still finds Utilities, "benefit" still
    finds Benefits — which strict whole-word matching would have broken.

    Hints of three characters or less are pinned at both ends, or "jv" would claim every
    account with "valuation" in the name.
    """
    p = _HINT_RE.get(hint)
    if p is None:
        tail = r"\b" if len(hint) <= 3 else r"[a-z]*\b"
        p = _HINT_RE[hint] = re.compile(r"\b" + re.escape(hint) + tail)
    return p


def suggest_for(row: CoaMap, chart: list[StandardAccount]) -> dict | None:
    """A best-guess standard account for one QBO account: {code, standard_account_id,
    confidence, why} or None.

    Confidence is coarse on purpose — three tiers, not a continuous score. A number like 0.63
    invites a threshold nobody can justify, whereas "exact name / keyword / weak" is a claim a
    reviewer can check at a glance. Nothing here is ever written without a human.

    **The leaf wins.** A hit on the account's own name beats a hit anywhere in the path above
    it, because the leaf is the specific thing and the ancestors are its context. Without that,
    "61300 Contract Labor:Virtual Assistants:Ana Ruiz" reads as Contract Labor and lands on
    8050 instead of 8060 — the ancestor drowning out the only word that mattered. Ancestor
    matches are still kept, and are the whole reason "…:Referral COS:HomeLight" resolves at all.
    """
    if row.standard_account_id or row.is_ignored:
        return None
    allowed = _TYPE_SECTIONS.get(row.qbo_account_type or "")
    leaf_raw = _fold((row.qbo_account_name or "").split(":")[-1])
    leaf = " " + leaf_raw + " "
    full = " " + _fold(row.qbo_account_fqn or row.qbo_account_name) + " "
    from_leaf = None
    from_path, path_at = None, -1
    for acct in chart:
        if not acct.is_active:
            continue
        if allowed and acct.section not in allowed:
            continue
        name = _fold(acct.name)
        if leaf_raw and leaf_raw == name:
            return {"code": acct.code, "name": acct.name,
                    "standard_account_id": str(acct.id),
                    "confidence": "exact", "why": "name matches exactly"}
        for hint in _HINTS.get(acct.code, ()):
            pat = _hint_pattern(hint)
            if from_leaf is None and pat.search(leaf):
                from_leaf = {"code": acct.code, "name": acct.name,
                             "standard_account_id": str(acct.id),
                             "confidence": "keyword", "why": f"contains “{hint}”"}
                break
            m = pat.search(full)
            # The DEEPEST match in the path wins, for the same reason the longest rule prefix
            # does: "…Contract Labor:Virtual Assistants:Ana Ruiz" is a VA, and reading only the
            # first ancestor that matched would file every one of them as generic contract
            # labor. Later in the string means further down the tree means more specific.
            if m and m.start() > path_at:
                from_path, path_at = {
                    "code": acct.code, "name": acct.name,
                    "standard_account_id": str(acct.id),
                    "confidence": "keyword", "why": f"the path says “{hint}”"}, m.start()
                break
        if from_path is None:
            head = name.split(",")[0].strip()
            m = _hint_pattern(head).search(full) if len(head) > 4 else None
            if m:
                from_path, path_at = {
                    "code": acct.code, "name": acct.name,
                    "standard_account_id": str(acct.id),
                    "confidence": "weak", "why": f"contains “{head}”"}, m.start()
    return from_leaf or from_path


# ── the mapping screen ────────────────────────────────────────────────────────────────────

async def _business(s: AsyncSession, tenant_id, business_id) -> Business:
    biz = await s.get(Business, business_id)
    if biz is None or biz.tenant_id != tenant_id:
        raise ValueError("Unknown business")
    return biz


async def standard_chart(s: AsyncSession, tenant_id) -> list[StandardAccount]:
    return list((await s.execute(select(StandardAccount).where(
        StandardAccount.tenant_id == tenant_id).order_by(StandardAccount.sort_order))).scalars())


def _rule_payload(r: CoaMapRule, code: str | None, biz_key: str | None, covers: int) -> dict:
    return {"id": str(r.id), "pattern": r.pattern, "match_type": r.match_type,
            "business_id": str(r.business_id) if r.business_id else None,
            "business_key": biz_key, "standard_account_id": str(r.standard_account_id),
            "code": code, "is_active": r.is_active, "note": r.note, "covers": covers}


async def mapping_overview(s: AsyncSession, tenant_id, business_id) -> dict:
    """Everything the mapping screen needs, in one request: the entity's accounts with their
    current mapping and a suggestion, the standard chart, the rules in force, and the counts
    that go in the persistent header (SPEC 5.5)."""
    biz = await _business(s, tenant_id, business_id)
    chart = await standard_chart(s, tenant_id)
    by_id = {a.id: a for a in chart}
    rows = list((await s.execute(select(CoaMap).where(
        CoaMap.tenant_id == tenant_id, CoaMap.business_id == business_id
    ).order_by(CoaMap.qbo_account_fqn))).scalars())
    rules = await _rules_for(s, tenant_id, business_id)

    accounts = []
    for r in rows:
        std = by_id.get(r.standard_account_id) if r.standard_account_id else None
        accounts.append({
            "qbo_account_id": r.qbo_account_id,
            "name": r.qbo_account_name,
            "fqn": r.qbo_account_fqn or r.qbo_account_name,
            "type": r.qbo_account_type,
            "qbo_active": r.qbo_active,
            "depth": len((r.qbo_account_fqn or r.qbo_account_name or "").split(":")),
            "standard_account_id": str(std.id) if std else None,
            "code": std.code if std else None,
            "standard_name": std.name if std else None,
            "mapped_via": r.mapped_via,
            "rule_id": str(r.rule_id) if r.rule_id else None,
            "is_ignored": r.is_ignored,
            "ignore_reason": r.ignore_reason,
            "suggestion": suggest_for(r, chart),
        })

    covers: dict = {}
    for r in rows:
        if r.rule_id:
            covers[r.rule_id] = covers.get(r.rule_id, 0) + 1
    keys = {b.id: b.key for b in (await s.execute(
        select(Business).where(Business.tenant_id == tenant_id))).scalars()}

    mapped = sum(1 for a in accounts if a["standard_account_id"])
    ignored = sum(1 for a in accounts if a["is_ignored"])
    return {
        "business": {"id": str(biz.id), "key": biz.key, "name": biz.name,
                     "archetype": biz.archetype},
        "counts": {"total": len(accounts), "mapped": mapped, "ignored": ignored,
                   "unmapped": len(accounts) - mapped - ignored,
                   "by_rule": sum(1 for a in accounts if a["mapped_via"] == "rule")},
        "accounts": accounts,
        "standard": [{"id": str(a.id), "code": a.code, "name": a.name, "bucket": a.bucket,
                      "statement": a.statement, "section": a.section,
                      "is_active": a.is_active, "definition": a.definition}
                     for a in chart],
        "rules": [_rule_payload(r, by_id[r.standard_account_id].code
                                if r.standard_account_id in by_id else None,
                                keys.get(r.business_id), covers.get(r.id, 0))
                  for r in sorted(rules, key=lambda x: x.pattern.lower())],
    }


# ── mutations ─────────────────────────────────────────────────────────────────────────────

async def set_mapping(s: AsyncSession, tenant_id, actor, business_id,
                      qbo_account_ids: list[str], standard_account_id) -> int:
    """Map (or, with a null target, unmap) a set of accounts by hand.

    One entry point for the single row, the multi-select, and the bulk-map-by-type first pass —
    the screen filters, the server just applies the decision to the ids it was handed.
    """
    await _business(s, tenant_id, business_id)
    std = None
    if standard_account_id is not None:
        std = await s.get(StandardAccount, standard_account_id)
        if std is None or std.tenant_id != tenant_id:
            raise ValueError("Unknown standard account")
    rows = list((await s.execute(select(CoaMap).where(
        CoaMap.tenant_id == tenant_id, CoaMap.business_id == business_id,
        CoaMap.qbo_account_id.in_(qbo_account_ids)))).scalars())
    now = dt.datetime.now(dt.timezone.utc)
    for r in rows:
        r.standard_account_id = std.id if std else None
        # A hand mapping is marked `manual` so no later rule pass overrides it; clearing one
        # hands the account back to the rules, which is what "unmap" should mean.
        r.mapped_via = "manual" if std else None
        r.rule_id = None
        r.mapped_by = getattr(actor, "id", None)
        r.mapped_at = now
        r.is_ignored = False
        r.ignore_reason = None
    if std is None:
        # Unmapping hands the account back to the rules rather than parking it as an
        # exception — otherwise "undo" would quietly mean "exempt forever", and the next
        # sync would be the first thing to notice.
        await apply_rules(s, tenant_id, business_id, rows=rows)
    audit(s, tenant_id, getattr(actor, "id", None), "coa.set_mapping",
          target_type="business", target_id=business_id,
          detail={"accounts": [r.qbo_account_id for r in rows],
                  "code": std.code if std else None})
    await s.commit()
    return len(rows)


async def set_ignored(s: AsyncSession, tenant_id, actor, business_id,
                      qbo_account_ids: list[str], reason: str) -> int:
    """Exclude accounts from the statement. A reason is REQUIRED (SPEC 5.5).

    Ignoring is the one action here that makes a number disappear, so it is the one action that
    has to say why in writing. Six months later "why is this not in the P&L" needs an answer
    that is not somebody's memory.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("A reason is required to ignore an account")
    await _business(s, tenant_id, business_id)
    rows = list((await s.execute(select(CoaMap).where(
        CoaMap.tenant_id == tenant_id, CoaMap.business_id == business_id,
        CoaMap.qbo_account_id.in_(qbo_account_ids)))).scalars())
    now = dt.datetime.now(dt.timezone.utc)
    for r in rows:
        r.is_ignored, r.ignore_reason = True, reason[:200]
        r.standard_account_id = r.rule_id = r.mapped_via = None
        r.mapped_by, r.mapped_at = getattr(actor, "id", None), now
    audit(s, tenant_id, getattr(actor, "id", None), "coa.set_ignored",
          target_type="business", target_id=business_id,
          detail={"accounts": [r.qbo_account_id for r in rows], "reason": reason})
    await s.commit()
    return len(rows)


async def create_rule(s: AsyncSession, tenant_id, actor, pattern: str, standard_account_id,
                      business_id=None, note: str | None = None) -> CoaMapRule:
    """Add a subtree rule and immediately apply it. Returns the rule."""
    pattern = re.sub(r"\s+", " ", (pattern or "").strip())
    if len(pattern) < 3:
        raise ValueError("A rule pattern needs at least three characters")
    std = await s.get(StandardAccount, standard_account_id)
    if std is None or std.tenant_id != tenant_id:
        raise ValueError("Unknown standard account")
    if business_id is not None:
        await _business(s, tenant_id, business_id)
    existing = await _rules_for(s, tenant_id, business_id)
    if any(normalize_fqn(r.pattern) == normalize_fqn(pattern) and r.business_id == business_id
           for r in existing):
        raise ValueError("A rule with that pattern already exists for this scope")

    rule = CoaMapRule(tenant_id=tenant_id, business_id=business_id, match_type="prefix",
                      pattern=pattern[:300], standard_account_id=std.id, is_active=True,
                      note=note, created_by=getattr(actor, "id", None))
    s.add(rule)
    await s.flush()
    stat = await _apply_everywhere(s, tenant_id, business_id)
    audit(s, tenant_id, getattr(actor, "id", None), "coa.create_rule",
          target_type="coa_map_rule", target_id=rule.id,
          detail={"pattern": rule.pattern, "code": std.code,
                  "business_id": str(business_id) if business_id else None, **stat})
    await s.commit()
    return rule


async def update_rule(s: AsyncSession, tenant_id, actor, rule_id, **fields) -> CoaMapRule:
    """Edit a rule, then re-apply. Deactivating one releases the accounts it owned back to
    unmapped rather than freezing its last answer in place."""
    rule = await s.get(CoaMapRule, rule_id)
    if rule is None or rule.tenant_id != tenant_id:
        raise ValueError("Unknown rule")
    if "standard_account_id" in fields and fields["standard_account_id"] is not None:
        std = await s.get(StandardAccount, fields["standard_account_id"])
        if std is None or std.tenant_id != tenant_id:
            raise ValueError("Unknown standard account")
    if "pattern" in fields and fields["pattern"] is not None:
        fields["pattern"] = re.sub(r"\s+", " ", fields["pattern"].strip())[:300]
        if len(fields["pattern"]) < 3:
            raise ValueError("A rule pattern needs at least three characters")
    for k, v in fields.items():
        if v is not None:
            setattr(rule, k, v)
    await s.flush()
    stat = await _apply_everywhere(s, tenant_id, rule.business_id)
    audit(s, tenant_id, getattr(actor, "id", None), "coa.update_rule",
          target_type="coa_map_rule", target_id=rule.id, detail={**fields, **stat})
    await s.commit()
    return rule


async def delete_rule(s: AsyncSession, tenant_id, actor, rule_id) -> None:
    rule = await s.get(CoaMapRule, rule_id)
    if rule is None or rule.tenant_id != tenant_id:
        raise ValueError("Unknown rule")
    scope, pattern = rule.business_id, rule.pattern
    await s.delete(rule)
    await s.flush()
    stat = await _apply_everywhere(s, tenant_id, scope)
    audit(s, tenant_id, getattr(actor, "id", None), "coa.delete_rule",
          target_type="coa_map_rule", target_id=rule_id, detail={"pattern": pattern, **stat})
    await s.commit()


async def _apply_everywhere(s: AsyncSession, tenant_id, business_id) -> dict:
    """Re-apply rules to the entity a rule is scoped to — or to every entity in the tenant
    when it is scoped to none, since a tenant-wide rule reaches all of them."""
    targets = [business_id] if business_id else list((await s.execute(
        select(Business.id).where(Business.tenant_id == tenant_id))).scalars())
    total = {"mapped": 0, "remapped": 0, "cleared": 0}
    for bid in targets:
        for k, v in (await apply_rules(s, tenant_id, bid)).items():
            total[k] += v
    return total


async def mapping_entities(s: AsyncSession, tenant_id) -> list[dict]:
    """The entity picker for the mapping screen: every business, its counts, and whether a
    QBO connection has ever delivered a chart. An entity with zero accounts has not synced
    yet — that is a different problem from an entity with 271 unmapped ones, and the screen
    should say which."""
    counts: dict = {}
    rows = (await s.execute(select(
        CoaMap.business_id, CoaMap.standard_account_id, CoaMap.is_ignored
    ).where(CoaMap.tenant_id == tenant_id))).all()
    for bid, std, ignored in rows:
        c = counts.setdefault(bid, {"total": 0, "mapped": 0, "ignored": 0})
        c["total"] += 1
        if ignored:
            c["ignored"] += 1
        elif std:
            c["mapped"] += 1
    connected = set((await s.execute(select(Integration.business_id).where(
        Integration.tenant_id == tenant_id, Integration.provider == "qbo",
        Integration.status == "connected"))).scalars())
    out = []
    for b in (await s.execute(select(Business).where(
            Business.tenant_id == tenant_id).order_by(Business.sort_order))).scalars():
        c = counts.get(b.id, {"total": 0, "mapped": 0, "ignored": 0})
        out.append({"id": str(b.id), "key": b.key, "name": b.name, "archetype": b.archetype,
                    "qbo_connected": b.id in connected,
                    "counts": {**c, "unmapped": c["total"] - c["mapped"] - c["ignored"]}})
    return out
