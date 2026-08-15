"""New bc pipeline (2026-08-14): post-call dispositions + rep share links.

Connor rebuilt the sales pipeline: "Appointment Complete - Likely Yes / Likely No /
Payment Link Sent" replace "Needs Decision" and the three "Payment Sent" stages. Stored
stage_maps don't match any of the new stage names, so without this every post-call opp
goes UNCATEGORIZED on the next sync. Also: the apps-in sub-signal is retired (No App vs
App Submitted combined), and the Sales Desk gains disposition sub-signal keys.

Data changes per stored launch stage_map (idempotent):
  deciding    += "appointment complete"      (catches all three new dispositions)
  booked_app   = []                          (apps-in tag retired)
  likely_yes  = ["likely yes"]        \
  likely_no   = ["likely no"]          >  new §8 leaderboard sub-signals (only if absent)
  link_sent   = ["payment link", "link sent"] /

Schema: share_link.scope_ref widens 40 → 160 (per-rep desk links store the rep EMAIL).

Revision ID: 0033_new_pipeline_dispositions
Revises: 0032_stage_map_committed_paid
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0033_new_pipeline_dispositions"
down_revision: Union[str, None] = "0032_stage_map_committed_paid"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _transform(m: dict) -> dict:
    out = dict(m)
    deciding = list(out.get("deciding") or [])
    if "appointment complete" not in deciding:
        deciding.insert(0, "appointment complete")
    out["deciding"] = deciding
    out["booked_app"] = []
    out.setdefault("likely_yes", ["likely yes"])
    out.setdefault("likely_no", ["likely no"])
    out.setdefault("link_sent", ["payment link", "link sent"])
    return out


def upgrade() -> None:
    bind = op.get_bind()
    tables = sa.inspect(bind).get_table_names()
    if "share_link" in tables and bind.dialect.name == "postgresql":
        op.alter_column("share_link", "scope_ref", type_=sa.String(160),
                        existing_type=sa.String(40))
    if "launch" not in tables:
        return
    for lid, raw in bind.execute(sa.text("SELECT id, stage_map FROM launch")).fetchall():
        m = raw if isinstance(raw, dict) else (json.loads(raw) if raw else None)
        if not m:
            continue                        # NULL/empty → code default already correct
        new = _transform(m)
        if new != m:
            bind.execute(sa.text("UPDATE launch SET stage_map = :m WHERE id = :id"),
                         {"m": json.dumps(new), "id": lid})


def downgrade() -> None:
    pass                                    # data-only + widening; no safe automatic reverse
