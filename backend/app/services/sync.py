"""Per-provider sync into snapshots. Idempotent upserts keyed on
(tenant, source, external_id). QBO refresh-token rotation is persisted on every
call. Upserts target Postgres (prod); the worker does not run against SQLite.
"""
import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Integration, Transaction, Agent, Lead, PLSnapshot, SyncRun
from ..security import enc, dec
from ..integrations import fub, sisu, qbo


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
    "sisu_status_code",
]


async def sync_sisu(s: AsyncSession, tenant_id: uuid.UUID, business_id: uuid.UUID):
    """Stream the whole team's clients from Sisu and batch-upsert agents + transactions."""
    agents: dict[str, dict] = {}
    mapped: list[dict] = []      # slim transaction dicts (raw pages discarded)

    async for page, rows in sisu.iter_team_clients():
        for c in rows:
            a = sisu.map_agent(c)
            if a:
                agents[a["external_id"]] = a
            t = sisu.map_client(c)
            if t["external_id"] and t["external_id"] != "None":
                mapped.append(t)
        print(f"[sisu] fetched page {page} · {len(mapped)} rows, {len(agents)} agents", flush=True)

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


async def run_all(s: AsyncSession, tenant_id: uuid.UUID, period_start: str, period_end: str):
    integs = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id, Integration.status == "connected"))).scalars().all()
    for integ in integs:
        run = SyncRun(tenant_id=tenant_id, provider=integ.provider)
        s.add(run)
        await s.commit()
        try:
            if integ.provider == "sisu":
                await sync_sisu(s, tenant_id, integ.business_id)
            elif integ.provider == "fub":
                await sync_fub(s, tenant_id, integ.business_id)
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
