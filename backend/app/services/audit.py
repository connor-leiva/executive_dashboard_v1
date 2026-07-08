"""Audit trail — one row per user-management + integration mutation (SPEC-platform
§0.8). Table stakes for selling to teams. The caller owns the commit."""
from __future__ import annotations

from ..models import AuditLog


def audit(s, tenant_id, actor_user_id, action: str,
          target_type: str | None = None, target_id: str | None = None,
          detail: dict | None = None) -> None:
    """Record an action on the current session (flushed with the caller's commit)."""
    s.add(AuditLog(
        tenant_id=tenant_id, actor_user_id=actor_user_id, action=action,
        target_type=target_type, target_id=(str(target_id) if target_id is not None else None),
        detail=detail,
    ))
