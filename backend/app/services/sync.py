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


async def _valid_access_token(s: AsyncSession, integ: Integration) -> str:
    now = dt.datetime.utcnow()
    if integ.token_expires_at and integ.token_expires_at - now > dt.timedelta(minutes=2):
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
    "side", "status", "gci", "sale_price", "address", "buyer_name", "buyer_email",
    "agent_id", "contract_date", "close_date", "appt_set_date", "lead_date",
    "listing_date", "sisu_status_code",
]


async def sync_sisu(s: AsyncSession, tenant_id: uuid.UUID, business_id: uuid.UUID):
    """Fetch the whole team's clients from Sisu (concurrently) and batch-upsert."""
    def _prog(done, total, n):
        print(f"[sisu] page {done}/{total} · {n} rows", flush=True)

    mapped, agents = await sisu.fetch_all_clients(progress=_prog)
    print(f"[sisu] fetched {len(mapped)} transactions, {len(agents)} agents", flush=True)

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
             gci=t.get("gci"), sale_price=t.get("sale_price"), address=t.get("address"),
             buyer_name=t.get("buyer_name"), buyer_email=t.get("buyer_email"),
             agent_id=agent_map.get(t.get("agent_external_id")),
             contract_date=t.get("contract_date"), close_date=t.get("close_date"),
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


async def sync_ghl(s: AsyncSession, tenant_id: uuid.UUID, integ: Integration):
    """Snapshot Spring B members from Go High Level, tag-driven (config)."""
    cfg = integ.config or {}
    location_id = cfg.get("location_id")
    member_tags = {t.lower() for t in cfg.get("member_tags", [])}
    forum_tags = {t.lower() for t in cfg.get("forum_tags", [])}
    becoll_tags = {t.lower() for t in cfg.get("becollective_tags", [])}
    token = dec(integ.access_token_enc) if integ.access_token_enc else None
    if not (location_id and member_tags and token):
        raise ValueError("Go High Level needs a token, location_id, and member_tags in config.")

    contacts = await ghl.get_contacts(token, location_id)
    rows = []
    for c in contacts:
        is_member, segment = ghl.classify_member(
            ghl.contact_tags(c), member_tags, forum_tags, becoll_tags)
        if is_member:
            rows.append(dict(
                tenant_id=tenant_id, business_id=integ.business_id, source="ghl", kind="member",
                external_id=str(c.get("id")), name=ghl.contact_name(c)[:200],
                email=(c.get("email") or None), status="active", segment=segment,
                source_url=ghl.contact_url(location_id, c.get("id")),
            ))

    # Snapshot: replace the prior member set so churned contacts drop out.
    await s.execute(delete(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == integ.business_id,
        MetricRecord.source == "ghl", MetricRecord.kind == "member"))
    for i in range(0, len(rows), 500):
        await s.execute(pg_insert(MetricRecord).values(rows[i:i + 500]))
    await s.commit()
    print(f"[ghl] {len(rows)} active members from {len(contacts)} contacts", flush=True)


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
