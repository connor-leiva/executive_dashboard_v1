"""The lending scorecard's resolvers: Arive loans, plus Sisu for what only the brokerage knows.

Same contract as every other board (scorecard_resolvers): a number, None (looked, nothing there), or
UNAVAILABLE (could not look, so nothing stored is touched). Every figure here is computed AS OF the
week it's asked about, from `meta.timeline` (arive.loan_timeline): each loan's own status history,
not its current state. That's what lets a pipeline count have real history, and what keeps a funded
loan in the week it funded when its broker check arrives later.

Scoping comes from the row's group, like the brokerage board: `lo_email` narrows Arive figures to one
loan officer, `sisu_group_id` narrows Sisu figures to one office.

COMPLIANCE: nothing in this module ranks agents. The one agent-level list here is the coaching gap
list (producing agents with no introductions), alphabetical, and the router limits every agent-level
drill on this board to leadership (AGENT_LEVEL_KEYS). No introduction is ever scored per agent.
"""
from __future__ import annotations

import datetime as dt
import re

from sqlalchemy import or_, select

from ..integrations.arive import DEAD_STATUSES, FUNDED_STATUSES
from ..models import Agent, Integration, MetricRecord, Transaction
from . import roles
from .scorecard_resolvers import (UNAVAILABLE, _agent_names, _office_agent_ids, _sisu_live,
                                  resolver)

# A file with no status change in this long is not being worked, whatever its status says. Arive
# rarely archives them: on 2026-11-02 most of Utah's "open" files hadn't moved in 90+ days.
ACTIVE_DAYS = 90
CTC_TARGET_DAYS = 30        # default days from application to clear to close; `ctc_target_days` overrides
INTRO_WINDOW_DAYS = 90      # an agent "introduces" if they sent a buyer within this window
PRODUCING_WINDOW_DAYS = 365
PULL_COHORT_DAYS = 30       # the application-month cohort
PULL_WINDOW_DAYS = 90       # ...measured once it's had this long to fund

# The brief's stages over Arive's status vocabulary, in lifecycle order.
STAGES = {
    "pre": {"APPLICATION_INTAKE", "QUALIFICATION", "PREAPPROVED"},   # inquiries, not yet an application
    "application": {"LOAN_SETUP"},
    "processing": {"DISCLOSURE_SENT"},
    "underwriting": {"UNDERWRITING_SUBMITTED", "RE_SUBMITTAL", "RE_SUBMISSION"},
    "conditional": {"APPROVED_WITH_CONDITION"},
    "ctc": {"CLEAR_TO_CLOSE", "DOCS_OUT", "DOCS_SENT", "DOCS_SIGNED"},
}
_STAGE_OF = {st: stage for stage, sts in STAGES.items() for st in sts}
_OPEN = ("application", "processing", "underwriting", "conditional", "ctc")
_BEFORE_CTC = ("application", "processing", "underwriting", "conditional")
# Tie-break for statuses stamped on the same day (docs out and CTC often are).
_RANK = {st: i for i, st in enumerate((
    "APPLICATION_INTAKE", "QUALIFICATION", "PREAPPROVED", "LOAN_SETUP", "DISCLOSURE_SENT",
    "UNDERWRITING_SUBMITTED", "RE_SUBMITTAL", "RE_SUBMISSION", "APPROVED_WITH_CONDITION",
    "CLEAR_TO_CLOSE", "DOCS_OUT", "DOCS_SENT", "DOCS_SIGNED", "LOAN_FUNDED", "FUNDED",
    "LOAN_FUNDED_ED", "BROKER_CHECK_RECEIVED", "COMMISSION_PAID", "LOAN_FINALIZED", "COMPLETED",
    "CLOSED", "ADVERSE", "DENIED", "WITHDRAWN", "LOAN_ARCHIVED", "ARCHIVED"))}

# Sisu's lead_type is free-text per team (Zillow Preferred, realtor.com mvip, Opcity, SOI...).
_LEAD_BUCKETS = (("zillow", re.compile(r"zillow", re.I)),
                 ("realtor", re.compile(r"realtor|realty\.com|opcity|market vip", re.I)))


def lead_bucket(lead_type: str | None) -> str:
    return next((name for name, rx in _LEAD_BUCKETS if rx.search(lead_type or "")), "sphere")


def _day(v) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _tl(loan) -> dict:
    return (loan.meta or {}).get("timeline") or {}


def _funded_on(loan) -> dt.date | None:
    """The real funding date; the current-status date only when a detail fetch missed this loan."""
    if loan.segment != "funded":
        return None
    return _day(_tl(loan).get("funded_on")) or loan.occurred_on


def _status_asof(loan, day: dt.date):
    """(status, date) the loan was in at the end of `day`, or None if it didn't exist yet."""
    iso = day.isoformat()
    best = None
    for st, d in (_tl(loan).get("history") or {}).items():
        if d <= iso:
            key = (d, _RANK.get(st, -1))
            if best is None or key > best[0]:
                best = (key, st, d)
    return (best[1], _day(best[2])) if best else None


def _stage_asof(loan, day: dt.date):
    """(stage, status date) if the loan was an open file at the end of `day`, else None."""
    found = _status_asof(loan, day)
    if not found:
        return None
    status, when = found
    funded = _funded_on(loan)
    if status in FUNDED_STATUSES or status in DEAD_STATUSES or (funded and funded <= day):
        return None
    if loan.segment == "dead" and loan.occurred_on and loan.occurred_on <= day:
        return None                                     # dead with no matching history entry
    stage = _STAGE_OF.get(status)
    return (stage, when) if stage else None


def _active(when: dt.date, day: dt.date) -> bool:
    return (day - when).days <= ACTIVE_DAYS


async def _market_loans(s, tenant_id, business_id, group=None):
    """This board's loans in the workspace's market (narrowed to the row's LO), or UNAVAILABLE when
    Arive isn't live or no loan carries a timeline yet: reading an un-enriched snapshot as "no
    loans" would write real-looking zeros."""
    from .metrics import _arive_states, _in_states
    live = await s.scalar(select(Integration.id).where(
        Integration.tenant_id == tenant_id, Integration.provider == "arive",
        Integration.status == "connected", Integration.last_synced_at.is_not(None)).limit(1))
    if live is None:
        return UNAVAILABLE
    states = await _arive_states(s, tenant_id)
    loans = (await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "arive", MetricRecord.kind == "loan"))).scalars().all()
    loans = [l for l in loans if _in_states(l, states)]
    if not any(_tl(l).get("history") for l in loans):
        return UNAVAILABLE
    lo = ((group or {}).get("lo_email") or "").lower()
    if lo:
        loans = [l for l in loans if ((l.meta or {}).get("lo_email") or "").lower() == lo]
    return loans


async def _ctc_target(s, tenant_id) -> int:
    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id, Integration.provider == "arive"))).scalars().first()
    try:
        return int(((integ.config if integ else None) or {}).get("ctc_target_days") or CTC_TARGET_DAYS)
    except (TypeError, ValueError):
        return CTC_TARGET_DAYS


def _weighted_pricing(loans) -> float | None:
    """Loan-amount-weighted revenue as a % of loan amount (2.03 = 2.03%)."""
    rev = amt = 0.0
    for l in loans:
        g, a = _tl(l).get("gross_revenue") or (l.meta or {}).get("gross_revenue"), float(l.amount or 0)
        if g and a > 0:
            rev += float(g)
            amt += a
    return round(rev / amt * 100, 2) if amt else None


def _funded_in(loans, ws, we):
    return [l for l in loans if (d := _funded_on(l)) and ws <= d <= we]


def _locked_in(loans, ws, we):
    return [l for l in loans if l.segment != "dead" and (d := _day(_tl(l).get("lock_on"))) and ws <= d <= we]


async def _outside_in(s, tenant_id, loans, ws, we):
    """Funded in the window and NOT sourced from Utah Life, by the tab's own attribution logic."""
    from .metrics import _loan_source_map
    funded = _funded_in(loans, ws, we)
    source = await _loan_source_map(s, tenant_id, funded)
    return [l for l in funded if source.get(l.id) != "ULRG"]


def _pull_cohort(loans, we):
    """The application-month cohort that's had PULL_WINDOW_DAYS to fund, as of week end."""
    hi = we - dt.timedelta(days=PULL_WINDOW_DAYS)
    lo = hi - dt.timedelta(days=PULL_COHORT_DAYS)
    return [(l, a) for l in loans if (a := _day(_tl(l).get("applied_on"))) and lo < a <= hi]


def _past_ctc(loans, we, target):
    out = []
    for l in loans:
        found = _stage_asof(l, we)
        applied = _day(_tl(l).get("applied_on"))
        if found and found[0] in _BEFORE_CTC and _active(found[1], we) and applied \
                and (we - applied).days > target:
            out.append(l)
    return out


def _in_stage(loans, stage, we):
    return [l for l in loans if (f := _stage_asof(l, we)) and f[0] == stage and _active(f[1], we)]


def _open_files(loans, we):
    return [l for l in loans if (f := _stage_asof(l, we)) and f[0] in _OPEN and _active(f[1], we)]


def _stale_files(loans, we):
    return [l for l in loans if (f := _stage_asof(l, we)) and f[0] in _OPEN and not _active(f[1], we)]


def _kdollars(loans) -> float:
    return float(round(sum(float(l.amount or 0) for l in loans) / 1000))


# ── loan figures ──────────────────────────────────────────────────────────────────────────────────

def _loan_resolver(key, compute):
    @resolver(key)
    async def fn(s, tenant_id, business_id, week_start, week_end, group=None):
        loans = await _market_loans(s, tenant_id, business_id, group)
        if loans is UNAVAILABLE:
            return UNAVAILABLE
        return compute(loans, week_start, week_end)
    return fn


_loan_resolver("sympli_funded", lambda ls, ws, we: float(len(_funded_in(ls, ws, we))))
_loan_resolver("sympli_margin_locks", lambda ls, ws, we: _weighted_pricing(_locked_in(ls, ws, we)))
_loan_resolver("sympli_margin_funded", lambda ls, ws, we: _weighted_pricing(_funded_in(ls, ws, we)))
_loan_resolver("sympli_open_count", lambda ls, ws, we: float(len(_open_files(ls, we))))
_loan_resolver("sympli_open_volume", lambda ls, ws, we: _kdollars(_open_files(ls, we)))
_loan_resolver("sympli_stale_files", lambda ls, ws, we: float(len(_stale_files(ls, we))))
for _stage in ("application", "processing", "underwriting", "conditional", "ctc"):
    _loan_resolver(f"sympli_stage_{_stage}",
                   lambda ls, ws, we, st=_stage: float(len(_in_stage(ls, st, we))))
    _loan_resolver(f"sympli_stage_{_stage}_volume",
                   lambda ls, ws, we, st=_stage: _kdollars(_in_stage(ls, st, we)))
_loan_resolver("sympli_stage_pre", lambda ls, ws, we: float(len(_in_stage(ls, "pre", we))))


@resolver("sympli_pull_through")
async def pull_through(s, tenant_id, business_id, week_start, week_end, group=None):
    """Of the applications taken in the 30 days that ended 90 days before week end, the share that
    funded within 90 days of applying. None when that cohort is empty: a rate needs a denominator."""
    loans = await _market_loans(s, tenant_id, business_id, group)
    if loans is UNAVAILABLE:
        return UNAVAILABLE
    cohort = _pull_cohort(loans, week_end)
    if not cohort:
        return None
    won = sum(1 for l, a in cohort
              if (f := _funded_on(l)) and (f - a).days <= PULL_WINDOW_DAYS)
    return round(won / len(cohort) * 100, 1)


@resolver("sympli_past_ctc")
async def past_ctc(s, tenant_id, business_id, week_start, week_end, group=None):
    """Active files short of clear to close, more than the target days past application."""
    loans = await _market_loans(s, tenant_id, business_id, group)
    if loans is UNAVAILABLE:
        return UNAVAILABLE
    return float(len(_past_ctc(loans, week_end, await _ctc_target(s, tenant_id))))


@resolver("sympli_outside")
async def outside_volume(s, tenant_id, business_id, week_start, week_end, group=None):
    """Funded loans not sourced from a Utah Life agent: the LOs' own book."""
    loans = await _market_loans(s, tenant_id, business_id, group)
    if loans is UNAVAILABLE:
        return UNAVAILABLE
    return float(len(await _outside_in(s, tenant_id, loans, week_start, week_end)))


# ── brokerage figures (Sisu lives on the brokerage's business, not the lender's) ─────────────────

async def _brokerage(s, tenant_id):
    """The brokerage business if its Sisu feed is live, else None (could not look)."""
    b = await roles.real_estate(s, tenant_id)
    return b if b and await _sisu_live(s, tenant_id, b.id) else None


async def _office_ids(s, tenant_id, group):
    """(True, agent ids or None for everyone) or (False, None) when the office can't be resolved."""
    sgid = (group or {}).get("sisu_group_id")
    if sgid is None:
        return True, None
    ids = await _office_agent_ids(s, tenant_id, sgid)
    return (True, ids) if ids else (False, None)


async def _capture(s, tenant_id, week_start, week_end, group):
    """The flywheel's own capture over the window, or UNAVAILABLE. Sharing it is what keeps these
    rows reconciled with the Flywheel tab and the brokerage board's attach row."""
    from .metrics import build_sympli_ctx, sympli_capture
    if not await _brokerage(s, tenant_id):
        return UNAVAILABLE
    ctx = await build_sympli_ctx(s, tenant_id)
    if ctx is None:
        return UNAVAILABLE
    ok, ids = await _office_ids(s, tenant_id, group)
    if not ok:
        return UNAVAILABLE
    return await sympli_capture(ctx, s, tenant_id, week_start, week_end, agent_ids=ids)


@resolver("sympli_attach")
async def attach(s, tenant_id, business_id, week_start, week_end, group=None):
    """Utah Life financed buyer closings with Sympli as lender, over all financed buyer closings."""
    cap = await _capture(s, tenant_id, week_start, week_end, group)
    if cap is UNAVAILABLE:
        return UNAVAILABLE
    fin = len(cap["fin"])
    return round(len(cap["cap_ids"]) / fin * 100, 1) if fin else None


def _lead_attach_resolver(bucket):
    @resolver(f"sympli_attach_{bucket}")
    async def fn(s, tenant_id, business_id, week_start, week_end, group=None):
        cap = await _capture(s, tenant_id, week_start, week_end, group)
        if cap is UNAVAILABLE:
            return UNAVAILABLE
        if not any(t.lead_type for t in cap["fin"]) and cap["fin"]:
            return UNAVAILABLE                          # Sisu hasn't re-synced lead_type yet
        fin = [t for t in cap["fin"] if lead_bucket(t.lead_type) == bucket]
        return round(sum(1 for t in fin if t.id in cap["cap_ids"]) / len(fin) * 100, 1) if fin else None
    return fn


for _bucket in ("realtor", "zillow", "sphere"):
    _lead_attach_resolver(_bucket)


async def _contracts(s, tenant_id, brokerage_id, cash_vids, week_start, week_end, ids):
    """New buy-side contracts in the window a lender could be recorded on (cash deals have none)."""
    q = select(Transaction).where(
        Transaction.tenant_id == tenant_id, Transaction.business_id == brokerage_id,
        Transaction.side == "buy", Transaction.contract_date >= week_start,
        Transaction.contract_date <= week_end,
        or_(Transaction.sale_price > 0, Transaction.sale_price.is_(None)))
    if ids is not None:
        q = q.where(Transaction.agent_id.in_(ids))
    return [t for t in (await s.execute(q)).scalars().all() if t.mortgage_vid not in cash_vids]


async def _sisu_cfg(s, tenant_id) -> dict:
    integ = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id, Integration.provider == "sisu"))).scalars().first()
    return (integ.config or {}) if integ else {}


@resolver("sympli_lender_recorded")
async def lender_recorded(s, tenant_id, business_id, week_start, week_end, group=None):
    """% of new Utah Life buyer contracts with a lender recorded in Sisu."""
    b = await _brokerage(s, tenant_id)
    if not b:
        return UNAVAILABLE
    ok, ids = await _office_ids(s, tenant_id, group)
    if not ok:
        return UNAVAILABLE
    cash = set((await _sisu_cfg(s, tenant_id)).get("cash_vids") or [])
    deals = await _contracts(s, tenant_id, b.id, cash, week_start, week_end, ids)
    if not deals:
        return None
    return round(sum(1 for t in deals if t.mortgage_vid is not None) / len(deals) * 100, 1)


async def _intro_split(s, tenant_id, business_id, week_end, group):
    """(introducing agents, agents with no introductions) among producing agents on the team, as of
    week end; UNAVAILABLE when it can't look. An introduction is a Sympli loan opened in the window
    naming the agent (referral source or buyer's agent), or a Sisu buy contract naming Sympli."""
    b = await _brokerage(s, tenant_id)
    if not b:
        return UNAVAILABLE
    loans = await _market_loans(s, tenant_id, business_id, None)
    if loans is UNAVAILABLE:
        return UNAVAILABLE
    ok, ids = await _office_ids(s, tenant_id, group)
    if not ok:
        return UNAVAILABLE
    since_prod = week_end - dt.timedelta(days=PRODUCING_WINDOW_DAYS)
    producing = set((await s.execute(select(Transaction.agent_id).where(
        Transaction.tenant_id == tenant_id, Transaction.business_id == b.id,
        Transaction.status == "closed", Transaction.sale_price > 0,
        Transaction.close_date > since_prod, Transaction.close_date <= week_end,
        Transaction.agent_id.is_not(None)).distinct())).scalars().all())
    if ids is not None:
        producing &= set(ids)
    agents = (await s.execute(select(Agent).where(Agent.id.in_(producing)))).scalars().all() if producing else []
    roster = [a for a in agents if a.is_active or (a.ended_date and a.ended_date > week_end)]

    since = week_end - dt.timedelta(days=INTRO_WINDOW_DAYS)
    emails = set()
    for l in loans:
        created = _day((l.meta or {}).get("created_on"))
        if created and since < created <= week_end:
            tl = _tl(l)
            emails |= {str(tl[k]).lower() for k in ("referral_email", "buyer_agent_email") if tl.get(k)}
    vids = set((await _sisu_cfg(s, tenant_id)).get("sympli_mortgage_vids") or [])
    by_vid = set()
    if vids:
        by_vid = set((await s.execute(select(Transaction.agent_id).where(
            Transaction.tenant_id == tenant_id, Transaction.business_id == b.id,
            Transaction.side == "buy", Transaction.mortgage_vid.in_(vids),
            Transaction.contract_date > since, Transaction.contract_date <= week_end))).scalars().all())
    yes, no = [], []
    for a in roster:
        (yes if a.id in by_vid or (a.email or "").lower() in emails else no).append(a)
    return yes, no


@resolver("sympli_agents_introducing")
async def agents_introducing(s, tenant_id, business_id, week_start, week_end, group=None):
    """% of producing Utah Life agents with an introduction in the last 90 days. A participation
    measure for finding friction; never reported or sorted per agent as a score."""
    split = await _intro_split(s, tenant_id, business_id, week_end, group)
    if split is UNAVAILABLE:
        return UNAVAILABLE
    yes, no = split
    total = len(yes) + len(no)
    return round(len(yes) / total * 100, 1) if total else None


# ── drill-downs ───────────────────────────────────────────────────────────────────────────────────
# Rows that name agents or Utah Life's clients: leadership only, enforced in the router.
AGENT_LEVEL_KEYS = frozenset({"sympli_attach", "sympli_attach_realtor", "sympli_attach_zillow",
                              "sympli_attach_sphere", "sympli_lender_recorded",
                              "sympli_agents_introducing"})
SISU_KEYS = AGENT_LEVEL_KEYS


def is_sympli_key(key: str | None) -> bool:
    return bool(key) and key.startswith("sympli_")


def source_label(key: str) -> str:
    return "Sisu" if key in SISU_KEYS else "Arive"


def _loan_row(l, when, detail=None):
    m = l.meta or {}
    return {"id": l.external_id, "client": l.name, "agent": _tl(l).get("lo_name") or m.get("lo_name"),
            "date": when.isoformat() if when else None, "address": None, "side": None,
            "sale_price": float(l.amount) if l.amount is not None else None,
            "detail": detail or (l.status or "").replace("_", " ").capitalize()}


def _deal_row(t, names, when, **extra):
    return {"id": t.external_id, "client": t.buyer_name, "agent": names.get(t.agent_id),
            "date": when.isoformat() if when else None, "address": t.address, "side": t.side,
            "sale_price": float(t.sale_price) if t.sale_price is not None else None, **extra}


async def records(key, s, tenant_id, business_id, ws, we, group=None, limit=500) -> list[dict] | None:
    """What a figure counted, in the drawer's record shape. As-of figures read the window's last day.
    None for an unknown key; [] when it can't look."""
    if key in ("sympli_attach", "sympli_attach_realtor", "sympli_attach_zillow", "sympli_attach_sphere"):
        cap = await _capture(s, tenant_id, ws, we, group)
        if cap is UNAVAILABLE:
            return []
        bucket = key.removeprefix("sympli_attach").lstrip("_")
        fin = [t for t in cap["fin"] if not bucket or lead_bucket(t.lead_type) == bucket]
        fin = sorted(fin, key=lambda t: t.close_date or dt.date.min, reverse=True)[:limit]
        names = await _agent_names(s, tenant_id, {t.agent_id for t in fin if t.agent_id})
        return [_deal_row(t, names, t.close_date, captured=t.id in cap["cap_ids"]) for t in fin]

    if key == "sympli_lender_recorded":
        b = await _brokerage(s, tenant_id)
        ok, ids = await _office_ids(s, tenant_id, group)
        if not b or not ok:
            return []
        cash = set((await _sisu_cfg(s, tenant_id)).get("cash_vids") or [])
        deals = (await _contracts(s, tenant_id, b.id, cash, ws, we, ids))[:limit]
        names = await _agent_names(s, tenant_id, {t.agent_id for t in deals if t.agent_id})
        return [_deal_row(t, names, t.contract_date,
                          detail="Lender recorded" if t.mortgage_vid is not None else "No lender recorded")
                for t in deals]

    if key == "sympli_agents_introducing":          # the coaching gap list, alphabetical, never ranked
        split = await _intro_split(s, tenant_id, business_id, we, group)
        if split is UNAVAILABLE:
            return []
        return [{"id": str(a.id), "client": a.name, "date": None,
                 "detail": "Producing, no introductions in the last 90 days"}
                for a in sorted(split[1], key=lambda a: a.name or "")[:limit]]

    if not is_sympli_key(key):
        return None
    loans = await _market_loans(s, tenant_id, business_id, group)
    if loans is UNAVAILABLE:
        return []
    if key == "sympli_funded":
        rows = [(l, _funded_on(l)) for l in _funded_in(loans, ws, we)]
    elif key == "sympli_outside":
        rows = [(l, _funded_on(l)) for l in await _outside_in(s, tenant_id, loans, ws, we)]
    elif key == "sympli_margin_funded":
        rows = [(l, _funded_on(l)) for l in _funded_in(loans, ws, we)]
    elif key == "sympli_margin_locks":
        rows = [(l, _day(_tl(l).get("lock_on"))) for l in _locked_in(loans, ws, we)]
    elif key in ("sympli_open_count", "sympli_open_volume"):
        rows = [(l, _stage_asof(l, we)[1]) for l in _open_files(loans, we)]
    elif key == "sympli_stale_files":
        rows = [(l, _stage_asof(l, we)[1]) for l in _stale_files(loans, we)]
    elif key == "sympli_past_ctc":
        rows = [(l, _day(_tl(l).get("applied_on"))) for l in _past_ctc(loans, we, await _ctc_target(s, tenant_id))]
    elif key == "sympli_pull_through":
        out = []
        for l, a in _pull_cohort(loans, we)[:limit]:
            f = _funded_on(l)
            won = f is not None and (f - a).days <= PULL_WINDOW_DAYS
            out.append(_loan_row(l, a, "Funded within 90 days" if won else f"Not funded within 90 days ({l.status})"))
        return out
    elif key.startswith("sympli_stage_"):
        stage = key.removeprefix("sympli_stage_").removesuffix("_volume")
        rows = [(l, _stage_asof(l, we)[1]) for l in _in_stage(loans, stage, we)]
    else:
        return None
    rows = sorted(rows, key=lambda x: x[1] or dt.date.min, reverse=True)[:limit]
    return [_loan_row(l, when) for l, when in rows]
