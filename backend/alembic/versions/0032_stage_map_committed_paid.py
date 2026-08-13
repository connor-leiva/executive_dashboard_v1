"""Stage map: Committed = cash received (Connor's rule, 2026-08-13).

Data-only migration. Stored launches carried the old grouping — "payment received"
in Enrolled and "payment sent" in Committed — so a paid-but-unsigned member showed
as Won. Transform every launch's stage_map to the corrected semantics:

  deciding  += "payment sent"   (a SENT link isn't cash — closers still own it)
  committed  = cash received, contract unsigned  ("payment received", "custom payment")
  enrolled   = signed + onboarded only           ("won: onboarded", "onboarded")

Idempotent: launches already matching are untouched; empty/NULL maps are skipped
(they fall back to the code DEFAULT_STAGE_MAP, which already encodes this).

Revision ID: 0032_stage_map_committed_paid
Revises: 0031_salescall_payment_type
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032_stage_map_committed_paid"
down_revision: Union[str, None] = "0031_salescall_payment_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _transform(m: dict) -> dict:
    """The corrected grouping, applied to one stored stage_map."""
    out = dict(m)
    deciding = list(out.get("deciding") or [])
    if "payment sent" not in deciding:
        deciding.append("payment sent")
    out["deciding"] = deciding
    out["committed"] = ["payment received", "custom payment"]
    out["enrolled"] = ["won: onboarded", "onboarded"]
    return out


def upgrade() -> None:
    bind = op.get_bind()
    if "launch" not in sa.inspect(bind).get_table_names():
        return
    rows = bind.execute(sa.text("SELECT id, stage_map FROM launch")).fetchall()
    for lid, raw in rows:
        m = raw if isinstance(raw, dict) else (json.loads(raw) if raw else None)
        if not m:
            continue                      # NULL/empty → code default already correct
        new = _transform(m)
        if new != m:
            bind.execute(sa.text("UPDATE launch SET stage_map = :m WHERE id = :id"),
                         {"m": json.dumps(new), "id": lid})


def downgrade() -> None:
    pass                                  # data-only; no safe automatic reverse
