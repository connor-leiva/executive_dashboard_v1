"""The operator's own trail, across every workspace (OPERATOR-CONSOLE-SPEC §4.2 and §9).

Every change an Acumyn operator makes writes TWO rows, through `record()` and nowhere else:

  1. `audit_log`, in the workspace's own trail, so a customer can see what Acumyn did to their
     workspace. actor_user_id stays null, because the actor is not a user of that tenant; `by`
     names the operator.
  2. `platform_audit`, the cross-tenant trail an operator reads on the Audit view. Deriving that view
     from every tenant's log instead would cost one scan per workspace on every page load, over a
     JSON column nothing indexes.
"""
from __future__ import annotations

import datetime as dt

from fastapi import Request
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import PlatformAudit, PlatformUser
from ..throttle import client_ip
from .audit import audit

# What the console tells an operator the trail keeps, and what the monthly prune enforces.
RETENTION_DAYS = 400

# Entries in a workspace's own log that record a read or an attempt rather than a change. The Audit
# view lists changes, so these stay on each workspace's Activity pane and out of the fleet trail.
NOT_CHANGES = frozenset({
    "auth.login", "auth.login_failed", "auth.login_blocked", "auth.google_denied",
    "auth.find_workspace", "auth.forgot_password", "step_up.granted", "step_up.failed",
})


# Entries whose summary quotes what somebody in the workspace wrote, not what they did. An operator
# sees that the assistant was asked something; never the question, which can hold anything.
CONTENT_SUMMARIES = frozenset({"assistant.asked"})


def operator_summary(action: str, summary: str | None) -> str:
    """A workspace audit entry's summary as an operator may read it."""
    return "" if action in CONTENT_SUMMARIES else (summary or "")


def record(s: AsyncSession, op: PlatformUser, t, action: str, target_type: str | None = None,
           target_id=None, *, request: Request | None = None, category: str = "Workspace",
           **detail) -> None:
    """Write both rows for one operator change. `t` is the workspace: anything with `id` and `slug`.
    The caller owns the commit, so the two rows land together or not at all."""
    audit(s, t.id, None, action, target_type, target_id, {"by": op.email, **detail},
          category=category, actor_label=f"{op.name or op.email} (Acumyn)"[:255])
    reason = detail.get("reason")
    s.add(PlatformAudit(
        operator_id=op.id, operator_email=op.email, action=action,
        tenant_id=t.id, tenant_slug=t.slug, target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        detail=detail, reason=reason if isinstance(reason, str) else None,
        ip=client_ip(request)[:45] if request is not None else None))


async def prune(s: AsyncSession, now: dt.datetime | None = None) -> int:
    """Delete operator entries older than the retention period. Returns how many went."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=RETENTION_DAYS)
    result = await s.execute(delete(PlatformAudit).where(PlatformAudit.created_at < cutoff))
    await s.commit()
    return result.rowcount or 0
