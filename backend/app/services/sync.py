"""Per-provider sync into snapshots. Idempotent upserts keyed on
(tenant, source, external_id). QBO refresh-token rotation is persisted on every
call. Upserts target Postgres (prod); the worker does not run against SQLite.
"""
import datetime as dt
import uuid

from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Integration, Transaction, Agent, Lead, PLSnapshot, SyncRun, MetricRecord
from ..security import enc, dec
from ..integrations import fub, sisu, qbo, ghl


def _parse_ghl_dt(v) -> dt.date | None:
    """GHL timestamps arrive as ISO strings or epoch-ms; return the calendar date."""
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
            return dt.datetime.utcfromtimestamp(int(v) / 1000).date()
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
    except (ValueError, OverflowError, OSError):
        return None


async def _valid_access_token(s: AsyncSession, integ: Integration) -> str:
    now = dt.datetime.now(dt.timezone.utc)                 # tz-aware
    exp = integ.token_expires_at
    if exp is not None and exp.tzinfo is None:             # Postgres returns aware, SQLite naive
        exp = exp.replace(tzinfo=dt.timezone.utc)
    if exp and exp - now > dt.timedelta(minutes=2):
        return dec(integ.access_token_enc)
    tok = await qbo.refresh(dec(integ.refresh_token_enc))
    integ.access_token_enc = enc(tok["access_token"])
    integ.refresh_token_enc = enc(tok["refresh_token"])   # rotation: persist the new one
    integ.token_expires_at = now + dt.timedelta(seconds=int(tok["expires_in"]))
    await s.commit()
    return tok["access_token"]


async def _upsert_many(s: AsyncSession, model, rows: list[dict], index_elements, update_keys, chunk=500):
    """Batched INSERT ... ON CONFLICT DO UPDATE (uses `excluded` for the SET)."""
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        stmt = pg_insert(model).values(part)
        stmt = stmt.on_conflict_do_update(
            index_elements=index_elements,
            set_={k: stmt.excluded[k] for k in update_keys},
        )
        await s.execute(stmt)
    await s.commit()


_TXN_UPDATE_KEYS = [
    "side", "status", "gci", "agent_commission", "sale_price", "address", "buyer_name",
    "buyer_email", "agent_id", "contract_date", "close_date", "expected_close_date",
    "appt_set_date", "lead_date", "listing_date", "sisu_status_code",
]


async def sync_sisu(s: AsyncSession, tenant_id: uuid.UUID, business_id: uuid.UUID):
    """Fetch the whole team's clients from Sisu (concurrently) and batch-upsert."""
    def _prog(done, total, n):
        print(f"[sisu] page {done}/{total} · {n} rows", flush=True)

    mapped, agents = await sisu.fetch_all_clients(progress=_prog)
    print(f"[sisu] fetched {len(mapped)} transactions, {len(agents)} agents", flush=True)

    # Enrich agent_commission (= GCI − company dollar) for the financials-relevant
    # subset via the per-deal commission-info endpoint. Best-effort.
    try:
        def _cprog(done, total):
            print(f"[sisu] commissions {done}/{total}", flush=True)
        n = await sisu.enrich_commissions(mapped, progress=_cprog)
        print(f"[sisu] enriched {n} commissions", flush=True)
    except Exception as e:  # noqa: BLE001 — never fail the sync on commission enrichment
        print(f"[sisu] commission enrichment skipped: {e}", flush=True)

    # 1) Batch-upsert the agent roster.
    agent_rows = [
        dict(tenant_id=tenant_id, business_id=business_id, source="sisu",
             external_id=a["external_id"], name=a["name"], email=a.get("email"),
             is_active=a.get("is_active", True))
        for a in agents.values()
    ]
    await _upsert_many(s, Agent, agent_rows, ["tenant_id", "source", "external_id"],
                       ["name", "email", "is_active"])
    print(f"[sisu] upserted {len(agent_rows)} agents", flush=True)

    agent_map = {
        a.external_id: a.id
        for a in (await s.execute(
            select(Agent).where(Agent.tenant_id == tenant_id, Agent.source == "sisu"))
        ).scalars().all()
    }

    # 2) Batch-upsert transactions.
    txn_rows = [
        dict(tenant_id=tenant_id, business_id=business_id, source="sisu",
             external_id=t["external_id"], side=t.get("side"), status=t["status"],
             gci=t.get("gci"), agent_commission=t.get("agent_commission"),
             sale_price=t.get("sale_price"), address=t.get("address"),
             buyer_name=t.get("buyer_name"), buyer_email=t.get("buyer_email"),
             agent_id=agent_map.get(t.get("agent_external_id")),
             contract_date=t.get("contract_date"), close_date=t.get("close_date"),
             expected_close_date=t.get("expected_close_date"),
             appt_set_date=t.get("appt_set_date"), lead_date=t.get("lead_date"),
             listing_date=t.get("listing_date"),
             sisu_status_code=t.get("sisu_status_code"))
        for t in mapped
    ]
    await _upsert_many(s, Transaction, txn_rows, ["tenant_id", "source", "external_id"],
                       _TXN_UPDATE_KEYS)
    print(f"[sisu] upserted {len(txn_rows)} transactions", flush=True)


async def sync_fub(s: AsyncSession, tenant_id: uuid.UUID, business_id: uuid.UUID):
    # Agents (FUB users) first.
    for raw in await fub.fub_users():
        u = fub.map_user(raw)
        await s.execute(pg_insert(Agent).values(
            tenant_id=tenant_id, business_id=business_id, source="fub",
            external_id=u["external_id"], name=u["name"], email=u.get("email"),
            is_active=u.get("is_active", True),
        ).on_conflict_do_update(
            index_elements=["tenant_id", "source", "external_id"],
            set_={"name": u["name"], "email": u.get("email"), "is_active": u.get("is_active", True)},
        ))
    await s.commit()

    agent_map = {
        a.external_id: a.id
        for a in (await s.execute(
            select(Agent).where(Agent.tenant_id == tenant_id, Agent.source == "fub"))
        ).scalars().all()
    }
    # Leads (FUB people).
    for raw in await fub.fub_people():
        p = fub.map_person(raw)
        await s.execute(pg_insert(Lead).values(
            tenant_id=tenant_id, business_id=business_id, source="fub",
            external_id=p["external_id"], stage=p.get("stage"),
            agent_id=agent_map.get(p.get("agent_external_id")),
            created_at_src=p.get("created_at_src"),
        ).on_conflict_do_update(
            index_elements=["tenant_id", "source", "external_id"],
            set_={"stage": p.get("stage"), "agent_id": agent_map.get(p.get("agent_external_id"))},
        ))
    await s.commit()


async def _ghl_snapshot(s: AsyncSession, tenant_id, business_id, kind: str, rows: list[dict]):
    """Replace the prior GHL record set of this kind (so drops fall out)."""
    await s.execute(delete(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == kind))
    for i in range(0, len(rows), 500):
        await s.execute(pg_insert(MetricRecord).values(rows[i:i + 500]))
    await s.commit()


async def sync_ghl(s: AsyncSession, tenant_id: uuid.UUID, integ: Integration):
    """Snapshot The Forum from Go High Level into metric_record, across the three
    surfaces the team actually uses (see the reference audit):
      • members      — union of member_tags (the official 70), segmented Forum/IC
      • registration — contacts tagged for the next event (event_tag)
      • membership   — open opps in the renewals pipeline → ARR + renewal month
      • onboarded    — sales-funnel opps in the "Won: Onboarded" stage → new members
      • subscription — active GHL subscriptions → MRR (monthly-payer subset)
    Members/registration/opps are the contract; subscriptions degrade to a skip if
    the payments scope is missing, so that never fails the whole sync."""
    cfg = integ.config or {}
    location_id = cfg.get("location_id")
    member_tags = {t.lower() for t in cfg.get("member_tags", [])}
    # Segmentation defaults so an already-connected integration (whose stored config
    # predates these keys) still splits Forum vs Inner Circle without a reconfig.
    forum_tags = {t.lower() for t in (cfg.get("forum_tags") or
                  ["the forum active", "member: secondary", "forumadmin"])}
    ic_tags = {t.lower() for t in (cfg.get("innercircle_tags") or
               ["inner circle active", "inner circle active add on"])}
    event_tag = (cfg.get("event_tag") or "").lower().strip()
    renewals_match = (cfg.get("renewals_pipeline_match") or "renewals").lower()
    onboarded_match = (cfg.get("onboarded_stage_match") or "won: onboarded").lower()
    token = dec(integ.access_token_enc) if integ.access_token_enc else None
    if not (location_id and member_tags and token):
        raise ValueError("Go High Level needs a token, location_id, and member_tags in config.")

    biz = integ.business_id

    # 1) Contacts → members (tag union, segmented) + event registrations (tag).
    contacts = await ghl.get_contacts(token, location_id)
    members, regs = [], []
    for c in contacts:
        tset = set(ghl.contact_tags(c))
        base = dict(tenant_id=tenant_id, business_id=biz, source="ghl",
                    external_id=str(c.get("id")), name=ghl.contact_name(c)[:200],
                    email=(c.get("email") or None),
                    source_url=ghl.contact_url(location_id, c.get("id")))
        if tset & member_tags:
            members.append({**base, "kind": "member", "status": "active",
                            "segment": ghl.member_segment(tset, forum_tags, ic_tags)})
        if event_tag and event_tag in tset:
            regs.append({**base, "kind": "registration", "status": "registered",
                         "meta": {"event_tag": event_tag}})
    await _ghl_snapshot(s, tenant_id, biz, "member", members)
    await _ghl_snapshot(s, tenant_id, biz, "registration", regs)
    print(f"[ghl] {len(members)} members, {len(regs)} registered for '{event_tag}' "
          f"(from {len(contacts)} contacts)", flush=True)

    # 2) Opportunities → memberships (renewals pipeline) + onboarded (sales funnel).
    try:
        pipelines = await ghl.get_pipelines(token, location_id)
        stage_name = {st.get("id"): st.get("name") for p in pipelines for st in (p.get("stages") or [])}
        ren_ids = {p.get("id") for p in pipelines if renewals_match in (p.get("name") or "").lower()}
        opps = await ghl.get_opportunities(token, location_id)
        memberships, onboarded = [], []
        for o in opps:
            stage = (stage_name.get(o.get("pipelineStageId")) or "").strip()
            base = dict(tenant_id=tenant_id, business_id=biz, source="ghl",
                        external_id=str(o.get("id")), name=ghl.opp_name(o)[:200],
                        source_url=ghl.contact_url(location_id, o.get("contactId")))
            if o.get("pipelineId") in ren_ids and o.get("status") == "open":
                memberships.append({**base, "kind": "membership", "status": "active",
                                    "amount": float(o.get("monetaryValue") or 0),
                                    "meta": {"renewal_month": stage}})
            elif onboarded_match in stage.lower():
                onboarded.append({**base, "kind": "onboarded", "status": o.get("status") or "won",
                                  "occurred_on": _parse_ghl_dt(o.get("lastStatusChangeAt")),
                                  "meta": {"stage": stage}})
        await _ghl_snapshot(s, tenant_id, biz, "membership", memberships)
        await _ghl_snapshot(s, tenant_id, biz, "onboarded", onboarded)
        arr = sum(m["amount"] for m in memberships)
        print(f"[ghl] {len(memberships)} renewals (ARR ${arr:,.0f}), {len(onboarded)} onboarded", flush=True)
    except Exception as e:  # noqa: BLE001 — opportunities scope optional
        print(f"[ghl] opportunities skipped: {e}", flush=True)

    # 3) Subscriptions → MRR (best effort; needs a payments scope).
    try:
        subs = await ghl.get_subscriptions(token, location_id)
        sub_rows = [dict(
            tenant_id=tenant_id, business_id=biz, source="ghl", kind="subscription",
            external_id=str(sub.get("_id") or sub.get("subscriptionId") or sub.get("id")),
            name=(sub.get("contactName") or (sub.get("contact") or {}).get("name") or "Subscription")[:200],
            email=(sub.get("contactEmail") or None),
            amount=ghl.sub_monthly_amount(sub),
            status="active" if ghl.sub_is_active(sub) else (sub.get("status") or "inactive").lower(),
            meta={"raw_status": sub.get("status")},
        ) for sub in subs]
        await _ghl_snapshot(s, tenant_id, biz, "subscription", sub_rows)
        active_n = sum(1 for r in sub_rows if r["status"] == "active")
        print(f"[ghl] {active_n}/{len(sub_rows)} active subscriptions", flush=True)
    except Exception as e:  # noqa: BLE001 — scope/endpoint optional
        print(f"[ghl] subscriptions skipped: {e}", flush=True)


async def sync_qbo_pl(s: AsyncSession, tenant_id, integ: Integration, start: str, end: str):
    token = await _valid_access_token(s, integ)
    report = await qbo.profit_and_loss(integ.realm_id, token, start, end)
    nums = qbo.parse_pl(report)
    stmt = pg_insert(PLSnapshot).values(
        tenant_id=tenant_id, business_id=integ.business_id, period_start=start, period_end=end,
        source="qbo", realm_id=integ.realm_id, **nums,
    ).on_conflict_do_update(
        index_elements=["tenant_id", "business_id", "period_start", "period_end"],
        set_={**nums, "pulled_at": dt.datetime.utcnow()},
    )
    await s.execute(stmt)
    integ.last_synced_at = dt.datetime.utcnow()
    await s.commit()


async def _sync_integration(s: AsyncSession, tenant_id, integ: Integration, period_start, period_end):
    """Sync one integration, recording a SyncRun and updating its status."""
    run = SyncRun(tenant_id=tenant_id, provider=integ.provider)
    s.add(run)
    await s.commit()
    try:
        if integ.provider == "sisu":
            await sync_sisu(s, tenant_id, integ.business_id)
        elif integ.provider == "fub":
            await sync_fub(s, tenant_id, integ.business_id)
        elif integ.provider == "ghl":
            await sync_ghl(s, tenant_id, integ)
        elif integ.provider == "qbo":
            await sync_qbo_pl(s, tenant_id, integ, period_start, period_end)
        run.status, run.finished_at = "ok", dt.datetime.utcnow()
        # Clear any prior error and mark the source healthy again.
        integ.status, integ.last_error = "connected", None
        integ.last_synced_at = dt.datetime.utcnow()
    except Exception as e:  # noqa: BLE001 — surface error on the integration
        run.status, run.detail, run.finished_at = "error", str(e), dt.datetime.utcnow()
        integ.status, integ.last_error = "error", str(e)
    await s.commit()


async def run_all(s: AsyncSession, tenant_id: uuid.UUID, period_start: str, period_end: str):
    # Include "error" so a previously-failed sync is retried (a stuck error would
    # otherwise silently skip the source). Disconnected sources are left alone.
    integs = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id,
        Integration.status.in_(("connected", "error"))))).scalars().all()
    for integ in integs:
        await _sync_integration(s, tenant_id, integ, period_start, period_end)


async def run_one(s: AsyncSession, tenant_id: uuid.UUID, integ_id, period_start: str, period_end: str):
    integ = (await s.execute(select(Integration).where(
        Integration.id == integ_id, Integration.tenant_id == tenant_id))).scalar_one_or_none()
    if integ:
        await _sync_integration(s, tenant_id, integ, period_start, period_end)
