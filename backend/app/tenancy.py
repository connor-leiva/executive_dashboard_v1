from contextvars import ContextVar
import uuid

from fastapi import Request, HTTPException
from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .models import Domain, Tenant

_current_tenant: ContextVar[uuid.UUID | None] = ContextVar("current_tenant", default=None)


def current_tenant_id() -> uuid.UUID:
    tid = _current_tenant.get()
    if tid is None:
        raise HTTPException(400, "No tenant in context")
    return tid


def set_tenant(tid: uuid.UUID | None) -> None:
    _current_tenant.set(tid)


async def resolve_tenant(request: Request) -> uuid.UUID:
    """Resolve the tenant from the Host header against the domain table.

    Dev fallbacks (in order): an explicit `x-tenant-host` header, then — when the
    host is unknown and ENV is development — the single seeded DEV_TENANT_SLUG.
    """
    host = request.headers.get("x-tenant-host") or request.headers.get("host", "")
    host = host.split(":")[0].lower()
    async with SessionLocal() as s:
        row = (await s.execute(select(Domain).where(Domain.hostname == host))).scalar_one_or_none()
        if row:
            _current_tenant.set(row.tenant_id)
            return row.tenant_id
        # Dev convenience: fall back to the single seeded tenant by slug.
        if settings.ENV == "development":
            t = (await s.execute(
                select(Tenant).where(Tenant.slug == settings.DEV_TENANT_SLUG)
            )).scalar_one_or_none()
            if t:
                _current_tenant.set(t.id)
                return t.id
        raise HTTPException(404, f"Unknown host: {host}")
