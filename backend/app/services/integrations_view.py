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

# Provider order + static metadata (matches the settings mockup).
ORDER = ["qbo", "sisu", "fub", "ghl", "ghl_bc", "arive"]
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
}
CONNECTABLE = {"ghl": "springb", "ghl_bc": "springb", "arive": "sympli"}   # offered even without a row


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
                state = "error" if i.status == "error" else "ok"
                err += 1 if state == "error" else 0
                if i.status == "connected" or state == "error":
                    feeds.append(b.key)
                if i.last_synced_at and (freshest is None or _aware(i.last_synced_at) > freshest):
                    freshest = _aware(i.last_synced_at)
                entities.append(EntityRow(
                    integration_id=str(i.id), business_key=b.key, business_name=b.name, state=state,
                    last_synced_at=i.last_synced_at.isoformat() if i.last_synced_at else None,
                    realm_id=i.realm_id,
                    detail=(i.last_error or "Re-authorize to resume syncing") if state == "error" else None))
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
                              else CONNECTABLE.get(prov))))

        if sources[-1].status in ("ok", "stale"):
            healthy += 1 if sources[-1].status == "ok" else 0

    next_sync = None
    if latest_ok_finish:
        elapsed_min = (now - latest_ok_finish).total_seconds() / 60
        next_sync = max(0, min(interval, round(interval - elapsed_min)))

    return IntegrationsOut(sources=sources, healthy=healthy, total=len(sources), next_sync_in_min=next_sync)
