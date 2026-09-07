"""Audit trail — one row per user-management + integration mutation (SPEC-platform
§0.8). Table stakes for selling to teams. The caller owns the commit."""
from __future__ import annotations

from ..models import AuditLog


# THE ONE PLACE THIS VOCABULARY IS DEFINED. `audit_log.category` carries a CHECK constraint, and
# it used to allow six values while the console wrote fifteen -- so thirteen of the console's
# screens raised a 500 the moment they tried to save. Only on Postgres, and only in production:
# the constraint is created under `if _dialect() == "postgresql"` and the test suite runs on
# SQLite, so a green suite said nothing about it.
#
# The first six are the original audit taxonomy; the rest are the console's own screen names,
# which is what its Audit Log groups by. Both are legitimate -- they are just different axes --
# and a constraint that knew about one of them took the other down silently.
#
# Adding a category here is the whole job: migration 0060 builds the constraint from this set,
# and test_audit_categories asserts every category the code writes appears in it.
AUDIT_CATEGORIES = frozenset({
    "Publish", "Config", "Access", "Read", "Content", "System",
    "AI", "Calendar", "Integrations", "Launchpad", "Marketing", "People",
    "Roles", "SOPs", "Setup", "Sign-in", "Training", "Win the Day", "Workspace",
})


def audit(s, tenant_id, actor_user_id, action: str,
          target_type: str | None = None, target_id: str | None = None,
          detail: dict | None = None, *, category: str = "System",
          summary: str = "", actor_member_id=None, actor_label: str | None = None,
          actor_type: str = "user", metadata: dict | None = None) -> None:
    """Record an action on the current session (flushed with the caller's commit)."""
    s.add(AuditLog(
        tenant_id=tenant_id, actor_user_id=actor_user_id, action=action,
        target_type=target_type, target_id=(str(target_id) if target_id is not None else None),
        detail=detail, category=category, summary=summary,
        actor_member_id=actor_member_id, actor_label=actor_label or "Unknown",
        actor_type=actor_type, event_metadata=metadata or {},
    ))
