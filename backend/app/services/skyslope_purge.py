"""Destroy everything SkySlope-sourced for a workspace, and record that it happened.

SkySlope's API terms require returning or destroying its data on termination or on request, and an
officer has to certify that in writing. So this has to be COMPLETE, and it has to be able to say
when it ran. Three things make that hold up:

THE TABLE LIST IS DERIVED. A hand-kept list of tables is only as current as the last person who
remembered it, and a purge that quietly skips the newest table is worse than no purge, because
somebody signs a certificate on the strength of it. Anything in the schema named `skyslope_*` is
purged by being named that.

SkySlope data also lives outside those tables, and each of these places is named here on purpose:
the access keys and sync state on `integration`, and the run history on `sync_run`, whose `detail`
and `stats` were written while reading SkySlope. Those rows are deleted rather than blanked. A
workspace that purges and carries on reconnects from nothing, which is the correct state anyway:
its watermark would claim it holds data it no longer holds.

THE RECORD SURVIVES THE PURGE. It is an `audit_log` row holding counts and a timestamp, with no
SkySlope data in it, so it answers "when was this destroyed, and how much" after the data is gone.

THE PROJECTION (SKYSLOPE-SPEC.md §7, services/skyslope_project.py) writes SkySlope's files into
`transaction` and `agent` with `source = "skyslope"`. Those rows are SkySlope-sourced too, so they are
named here and deleted, transactions first because they point at the agents.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Agent, Base, Integration, SyncRun, Transaction
from .audit import audit

PROVIDER = "skyslope"
PREFIX = "skyslope_"


def tables() -> list:
    """Every table this integration owns. Order does not matter: none has a foreign key to another."""
    return [t for name, t in sorted(Base.metadata.tables.items()) if name.startswith(PREFIX)]


async def counts(s: AsyncSession, tenant_id) -> dict[str, int]:
    """What a purge of this workspace would remove. Reads only."""
    out: dict[str, int] = {}
    for table in tables():
        out[table.name] = (await s.execute(select(func.count()).select_from(table).where(
            table.c.tenant_id == tenant_id))).scalar_one()
    out["integration"] = (await s.execute(select(func.count()).select_from(Integration).where(
        Integration.tenant_id == tenant_id, Integration.provider == PROVIDER))).scalar_one()
    out["sync_run"] = (await s.execute(select(func.count()).select_from(SyncRun).where(
        SyncRun.tenant_id == tenant_id, SyncRun.provider == PROVIDER))).scalar_one()
    for model in (Transaction, Agent):
        out[model.__tablename__] = (await s.execute(select(func.count()).select_from(model).where(
            model.tenant_id == tenant_id, model.source == PROVIDER))).scalar_one()
    return out


async def purge_tenant(s: AsyncSession, tenant_id, *, actor: str = "purge command") -> dict:
    """Delete it all for one workspace and write the record, in one transaction: either the data is
    gone and the record says so, or neither happened. The caller commits."""
    removed = await counts(s, tenant_id)
    for table in tables():
        await s.execute(delete(table).where(table.c.tenant_id == tenant_id))
    for model in (Transaction, Agent):                 # this order: transactions point at agents
        await s.execute(delete(model).where(model.tenant_id == tenant_id, model.source == PROVIDER))
    await s.execute(delete(Integration).where(
        Integration.tenant_id == tenant_id, Integration.provider == PROVIDER))
    await s.execute(delete(SyncRun).where(
        SyncRun.tenant_id == tenant_id, SyncRun.provider == PROVIDER))
    at = dt.datetime.now(dt.timezone.utc)
    audit(s, tenant_id, None, "skyslope.purged", "tenant", tenant_id,
          {"removed": removed, "at": at.isoformat()},
          category="Integrations", actor_label=actor, actor_type="system",
          summary=f"Destroyed all SkySlope-sourced data ({sum(removed.values())} rows)")
    return {"at": at, "removed": removed}
