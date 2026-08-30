"""Settings › Integrations accordion view (spec v3 Part 1). Reshapes the flat
integration rows into grouped sources with real status semantics: QuickBooks
collapses to one source with per-entity rows; every other provider is one card;
ghl/arive appear even without a row (connectable). Status is one of
ok | stale | attention | disconnected, with server-humanized freshness."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Integration, Business, SyncRun
from ..schemas import EntityRow, SourceOut, IntegrationsOut
from . import roles

# Provider order + static metadata (matches the settings mockup).
ORDER = ["qbo", "sisu", "fub", "ghl", "ghl_bc", "arive", "stripe_legacy", "stripe_bc",
         "ghl_legacy", "meta_ads"]
META = {
    "qbo": {"name": "QuickBooks", "provides": ["Profit & Loss", "Balance Sheet"], "feeds": ["ulrg", "springb", "sympli"],
            "desc": "Financial source of truth · one connection per entity"},
    "sisu": {"name": "Sisu", "provides": ["Transactions", "Agents", "GCI"], "feeds": ["ulrg"],
             "desc": "Real estate production — transactions, agents, GCI"},
    "fub": {"name": "Follow Up Boss", "provides": ["Leads", "Agents"], "feeds": ["ulrg"],
            "desc": "CRM — leads and agent activity"},
    "ghl": {"name": "Go High Level · The Forum", "provides": ["Members", "Subscriptions", "Events"], "feeds": ["forum"],
            "desc": "The Forum — members, renewals, subscriptions, events"},
    "ghl_bc": {"name": "Go High Level · beCollective", "provides": ["Members", "Onboarding", "Events"], "feeds": ["becollective"],
               "desc": "beCollective — its own GHL location; members, cohort onboarding, events"},
    "arive": {"name": "Arive", "provides": ["Loans", "Pipeline"], "feeds": ["sympli"],
              "desc": "Lights up Sympli's pipeline and the referral flywheel"},
    "stripe_legacy": {"name": "Legacy Stripe · The Forum", "provides": ["Legacy charges", "Recurring dues"],
                      "feeds": ["forum"],
                      "desc": "Spring's original Stripe account — legacy Forum dues still billing outside "
                              "the new sub-account. Read-only; also feeds the GHL delta-import CSV"},
    "stripe_bc": {"name": "Stripe · beCollective", "provides": ["Membership payments", "Financed plans"],
                  "feeds": ["becollective"],
                  "desc": "beCollective's own dedicated Stripe account — membership payments only (event "
                          "tickets and other products filtered out). Read-only; powers the Cash & Billing view"},
    "meta_ads": {"name": "Meta Ads", "provides": ["Spend", "Impressions", "Link clicks", "Leads"],
                 "feeds": ["ads"],
                 "desc": "Paid social delivery, joined through to registrations and enrollments. "
                         "Reporting only - a System User token with ads_read; never ads_management"},
    "ghl_legacy": {"name": "Old GHL · Charge labels", "provides": ["Charge labels", "Invoice line items"],
                   "feeds": ["forum"],
                   "desc": "The old Spring B GHL (where legacy Stripe is wired) — read-only. Supplies the "
                           "real label for each legacy charge (join by Stripe id) so the classifier knows "
                           "what each is for (Forum sponsorship vs Spring Break, membership vs The Edge, …)"},
}
# Which KIND of business each source feeds. It used to be which business KEY — literally
# {"ghl": "springb", "arive": "sympli"} — and that is one customer's names for her own
# companies. A tenant provisioned normally gets a business keyed "main", so the frontend asked
# to connect against "springb", the API found no such business for that tenant, and every
# source except QuickBooks answered 404 "Unknown business". QuickBooks worked only because it
# creates its business row on the way through.
#
# The role is the durable fact: Arive reports loans, so it belongs to whichever business is
# this tenant's lending JV, whatever they call it. Resolved per tenant through roles.py.
CONNECTABLE_KIND = {
    # Ad spend is booked by the entity that runs the campaigns. For a programme business that is
    # the membership entity; a brokerage running its own ads would attach to real_estate. Resolved
    # per workspace like every other source rather than pinned to one customer's business key.
    "meta_ads": roles.MEMBERSHIP,
    "sisu": roles.REAL_ESTATE,          # transactions, agents, GCI — the brokerage
    "fub": roles.REAL_ESTATE,           # CRM leads and agent activity
    "ghl": roles.MEMBERSHIP,            # members, renewals, subscriptions
    "ghl_bc": roles.MEMBERSHIP,         # a second GHL location for another programme
    "ghl_legacy": roles.MEMBERSHIP,     # read-only charge labels for the same programmes
    "stripe_legacy": roles.MEMBERSHIP,  # legacy dues
    "stripe_bc": roles.MEMBERSHIP,      # cohort payments
    "arive": roles.COMMISSION_JV,       # loans and pipeline — the lending JV
}


def _aware(ts: dt.datetime) -> dt.datetime:
    return ts.replace(tzinfo=dt.timezone.utc) if ts.tzinfo is None else ts


def _humanize(ts, now, prefix="Synced") -> str | None:
    if not ts:
        return None
    mins = int((now - _aware(ts)).total_seconds() // 60)
    if mins < 1:
        return f"{prefix} just now"
    if mins < 60:
        return f"{prefix} {mins} min ago"
    hrs = mins // 60
    if hrs < 24:
        return f"{prefix} {hrs} hour{'s' if hrs != 1 else ''} ago"
    days = hrs // 24
    return f"{prefix} {days} day{'s' if days != 1 else ''} ago"


def _last_run(run: SyncRun | None, status: str, interval: int, now) -> str | None:
    if status == "stale" and run and run.finished_at:
        missed = int((now - _aware(run.finished_at)).total_seconds() // 60 // max(interval, 1))
        if missed > 0:
            return f"Auto-sync has missed its last {missed} runs — check the connection"
    if run and run.stats:
        st = run.stats
        parts = []
        if st.get("records") is not None:
            parts.append(f"{st['records']} records")
        if st.get("seconds") is not None:
            parts.append(f"{st['seconds']}s")
        if parts:
            return "Last run · " + " · ".join(parts)
    if run:
        return f"Last run · {run.status}"
    return None


def _ghl_config_summary(cfg: dict) -> list[list[str]]:
    loc = cfg.get("location_id") or ""
    loc_disp = (loc[:4] + "…" + loc[-3:]) if len(loc) > 10 else (loc or "—")
    tags = cfg.get("member_tags") or []
    event = cfg.get("event_name") or cfg.get("event_tag") or "—"
    return [["Location ID", loc_disp], ["Member tags", f"{len(tags)} tags" if tags else "—"], ["Next event", event]]


async def build_integrations_view(s: AsyncSession, tenant_id) -> IntegrationsOut:
    interval = settings.SYNC_INTERVAL_MINUTES
    now = dt.datetime.now(dt.timezone.utc)

    integs = (await s.execute(select(Integration).where(Integration.tenant_id == tenant_id))).scalars().all()
    businesses = (await s.execute(select(Business).where(Business.tenant_id == tenant_id))).scalars().all()
    biz_by_id = {b.id: b for b in businesses}
    sort_of = {b.id: b.sort_order for b in businesses}
    # kind -> the business a source of that kind attaches to, for THIS tenant.
    by_kind = {k: roles.pick(sorted(businesses, key=lambda x: x.sort_order or 0), k)
               for k in roles.KINDS}

    # Latest SyncRun per provider (and latest successful finish for next_sync).
    runs = (await s.execute(select(SyncRun).where(SyncRun.tenant_id == tenant_id)
            .order_by(SyncRun.started_at.desc()))).scalars().all()
    latest_run: dict[str, SyncRun] = {}
    latest_ok_finish: dt.datetime | None = None
    for r in runs:
        latest_run.setdefault(r.provider, r)
        if r.status == "ok" and r.finished_at and (latest_ok_finish is None or _aware(r.finished_at) > latest_ok_finish):
            latest_ok_finish = _aware(r.finished_at)

    sources: list[SourceOut] = []
    healthy = 0

    for prov in ORDER:
        meta = META[prov]
        rows = [i for i in integs if i.provider == prov]
        run = latest_run.get(prov)

        if prov == "qbo":
            rows = sorted(rows, key=lambda i: sort_of.get(i.business_id, 99))
            entities, feeds, freshest, err = [], [], None, 0
            for i in rows:
                b = biz_by_id.get(i.business_id)
                if not b:
                    continue
                state = ("error" if i.status == "error"
                         else "disconnected" if i.status == "disconnected" else "ok")
                err += 1 if state == "error" else 0
                if i.status == "connected" or state == "error":
                    feeds.append(b.key)
                if i.last_synced_at and (freshest is None or _aware(i.last_synced_at) > freshest):
                    freshest = _aware(i.last_synced_at)
                entities.append(EntityRow(
                    integration_id=str(i.id), business_key=b.key, business_name=b.name, state=state,
                    last_synced_at=i.last_synced_at.isoformat() if i.last_synced_at else None,
                    realm_id=i.realm_id,
                    detail=(i.last_error or "Re-authorize to resume syncing") if state == "error" else None,
                    display_tab=(b.display_tab or b.key),
                    books_enabled=bool((b.config or {}).get("books_enabled", True))))
            connected = [i for i in rows if i.status in ("connected", "error")]
            if not connected:
                status, note = "disconnected", None
            elif err:
                status, note = "attention", f"{err} of {len(entities)} entit{'y' if len(entities) == 1 else 'ies'} needs reconnect"
            elif freshest and (now - freshest).total_seconds() > 2 * interval * 60:
                status, note = "stale", None
            else:
                status, note = "ok", None
            sources.append(SourceOut(
                provider=prov, name=meta["name"], status=status, status_note=note,
                fresh=_humanize(freshest, now), feeds=feeds, provides=meta["provides"],
                last_run=_last_run(run, status, interval, now), entities=entities))
        else:
            row = rows[0] if rows else None
            # The business this source would attach to. None when the tenant runs no
            # business of that role — a membership-only customer has no lending JV — in
            # which case the card says what is missing instead of offering a dead button.
            target = by_kind.get(CONNECTABLE_KIND.get(prov))
            connected = bool(row) and row.status in ("connected", "error")
            if not connected:
                status = "disconnected"
            elif row.status == "error":
                status = "attention"
            elif row.last_synced_at and (now - _aware(row.last_synced_at)).total_seconds() > 2 * interval * 60:
                status = "stale"
            else:
                status = "ok"
            cfg = (row.config if row else None) or {}
            sources.append(SourceOut(
                provider=prov, name=meta["name"], status=status,
                status_note=(row.last_error if row and status == "attention" else None),
                fresh=_humanize(row.last_synced_at if row else None, now),
                feeds=meta["feeds"] if connected else [], provides=meta["provides"],
                last_run=_last_run(run, status, interval, now),
                config_summary=_ghl_config_summary(cfg) if prov in ("ghl", "ghl_bc") and connected else [],
                config=cfg if prov in ("ghl", "ghl_bc") else None,
                integration_id=str(row.id) if row else None,
                business_key=(biz_by_id.get(row.business_id).key if row and row.business_id in biz_by_id
                              else (target.key if target else None)),
                needs_kind=(None if (row and row.business_id in biz_by_id) or target
                            else CONNECTABLE_KIND.get(prov))))

        if sources[-1].status in ("ok", "stale"):
            healthy += 1 if sources[-1].status == "ok" else 0

    next_sync = None
    if latest_ok_finish:
        elapsed_min = (now - latest_ok_finish).total_seconds() / 60
        next_sync = max(0, min(interval, round(interval - elapsed_min)))

    return IntegrationsOut(sources=sources, healthy=healthy, total=len(sources), next_sync_in_min=next_sync)
