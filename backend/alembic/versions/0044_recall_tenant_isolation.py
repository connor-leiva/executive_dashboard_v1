"""Recall isolation: one bot belongs to exactly one call, and a stuck row stops blocking

Three additive changes behind the Phase 2 tenant-boundary work.

1. A partial UNIQUE index on sales_call.recall_bot_id. The column was plain-indexed and
   nullable, so nothing at the storage layer stopped two rows holding the same bot id — and
   `apply_bot_status` resolves the webhook's bot id with `.first()`, so a duplicate meant one
   client's Watch link could play another's conversation. Making it unique turns every
   remaining adoption bug in this area from silent corruption into a caught error.

   Existing duplicates are REPAIRED, not left to fail the deploy. A duplicate row's link was
   already wrong (it points at a bot some other row owns), so the winner is the row actually
   serving media — recording_url set, then the earliest call — and every loser's
   recall_bot_id is cleared. The counts are printed so the repair is visible in the deploy log
   rather than silent. Failing the migration instead would crash-loop Railway, which this
   project has learned the hard way.

2. sales_call.transcript_attempts, and 3. call_transcript.chapter_attempts. Both batch loops
   (`store_transcripts`, `generate_pending`) selected rows with no record of having TRIED, so a
   row whose fetch or model call never succeeds occupied a batch slot on every tick forever —
   permanently starving everything behind it and, for chapters, re-billing the same call to
   Anthropic every 15 minutes. This exists today with one tenant; per-tenant batching does not
   fix it, because the block is per-row, not per-tenant.

Revision ID: 0044_recall_tenant_isolation
Revises: 0043_allocations
"""
import datetime as dt
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0044_recall_tenant_isolation"
down_revision: Union[str, None] = "0043_allocations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

IX_UNIQUE = "uq_sales_call_recall_bot"


def _cols(table: str) -> set[str]:
    insp = sa.inspect(op.get_bind())
    return {c["name"] for c in insp.get_columns(table)}


def _indexes(table: str) -> set[str]:
    insp = sa.inspect(op.get_bind())
    return {i["name"] for i in insp.get_indexes(table)}


def _repair_duplicate_bots(conn) -> None:
    """Clear recall_bot_id on every row but the rightful owner, so the unique index can build."""
    dupes = conn.execute(sa.text("""
        SELECT recall_bot_id FROM sales_call
        WHERE recall_bot_id IS NOT NULL
        GROUP BY recall_bot_id HAVING COUNT(*) > 1
    """)).scalars().all()
    if not dupes:
        print("[0044] no duplicate recall_bot_id rows", flush=True)
        return
    cleared = 0
    for bot_id in dupes:
        rows = conn.execute(sa.text("""
            SELECT id, recording_url, call_time_utc FROM sales_call
            WHERE recall_bot_id = :b
            ORDER BY (CASE WHEN recording_url IS NULL THEN 1 ELSE 0 END),
                     call_time_utc NULLS LAST
        """), {"b": bot_id}).all()
        for r in rows[1:]:                      # rows[0] is the keeper
            conn.execute(sa.text(
                "UPDATE sales_call SET recall_bot_id = NULL, recording_status = NULL, "
                "recording_url = NULL, recording_at = NULL WHERE id = :i"), {"i": r[0]})
            cleared += 1
    print(f"[0044] repaired {len(dupes)} duplicated bot id(s); cleared {cleared} row(s). "
          f"Those calls will re-adopt or re-book on the next tick.", flush=True)


def _grandfather_recording_opt_in(conn) -> None:
    """Give the opt-in to any tenant that is ALREADY recording, and to no one else.

    Recording is now opt-in per tenant and fails closed (services/recall.recording_enabled).
    That is the right default — an attendee-visible bot joining a customer's client calls,
    under a retention period chosen for somebody else, is not something to inherit silently.
    But a fail-closed gate applied to a live tenant would just stop their bots.

    So the rule is behavioural, not by name: a tenant that already has recall bots on its calls
    was already recording, and keeps doing so. A tenant with none — which is every tenant
    provisioned from here — starts closed and opts in deliberately. `legacy_adopt_before` is
    stamped at the same time because all of an existing tenant's URL-less calls predate this
    deploy, which preserves their adoption behaviour exactly while denying it to new tenants.
    """
    rows = conn.execute(sa.text("""
        SELECT t.id, t.config FROM tenant t
        WHERE EXISTS (SELECT 1 FROM sales_call sc
                      WHERE sc.tenant_id = t.id AND sc.recall_bot_id IS NOT NULL)
    """)).all()
    if not rows:
        print("[0044] no tenant is recording yet — recording stays opt-in for all", flush=True)
        return
    today = dt.date.today().isoformat()
    for tid, cfg in rows:
        current = {}
        if cfg:
            try:
                current = json.loads(cfg) if isinstance(cfg, str) else dict(cfg)
            except (ValueError, TypeError):
                current = {}
        current.setdefault("recall_enabled", True)
        current.setdefault("recall_legacy_adopt_before", today)
        conn.execute(sa.text("UPDATE tenant SET config = :c WHERE id = :i"),
                     {"c": json.dumps(current), "i": tid})
    print(f"[0044] carried the recording opt-in forward for {len(rows)} already-recording "
          f"tenant(s); every other tenant starts closed", flush=True)


def upgrade() -> None:
    conn = op.get_bind()

    if "transcript_attempts" not in _cols("sales_call"):
        op.add_column("sales_call", sa.Column("transcript_attempts", sa.Integer(),
                                              nullable=False, server_default="0"))
    if "chapter_attempts" not in _cols("call_transcript"):
        op.add_column("call_transcript", sa.Column("chapter_attempts", sa.Integer(),
                                                   nullable=False, server_default="0"))

    if IX_UNIQUE not in _indexes("sales_call"):
        _repair_duplicate_bots(conn)
        # Partial so the many NULLs stay unconstrained. Supported by both Postgres and
        # SQLite (>= 3.8), which is why one statement covers prod and the test database.
        op.create_index(IX_UNIQUE, "sales_call", ["recall_bot_id"], unique=True,
                        sqlite_where=sa.text("recall_bot_id IS NOT NULL"),
                        postgresql_where=sa.text("recall_bot_id IS NOT NULL"))

    _grandfather_recording_opt_in(conn)


def downgrade() -> None:
    if IX_UNIQUE in _indexes("sales_call"):
        op.drop_index(IX_UNIQUE, table_name="sales_call")
    if "chapter_attempts" in _cols("call_transcript"):
        op.drop_column("call_transcript", "chapter_attempts")
    if "transcript_attempts" in _cols("sales_call"):
        op.drop_column("sales_call", "transcript_attempts")
