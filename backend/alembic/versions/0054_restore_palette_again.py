"""Restore the hand-built palette a second time, and make the restore repeatable

0052 restored it once. It was destroyed again the same week, by a different route: saving a
TYPEFACE change. The appearance endpoint popped `palette` on every save regardless of what had
actually moved, so a workspace changing its font gave up thirty hand-chosen colours. Both the
endpoint and the panel are fixed; this puts the colours back.

WHY THIS IS A SEPARATE MIGRATION RATHER THAN RE-RUNNING 0052. That one only restores a workspace
whose seeds are the PLATFORM defaults — the fingerprint of the first accident. After 0052 ran,
the workspace's seeds were its own five, so the second loss looks nothing like the first and 0052
would correctly decline to touch it.

The condition here is different and, I think, the durable one: restore when the palette is absent
AND the seeds are exactly the five that would be read back out of the palette being restored. That
is true after any loss that preserved the seeds, which is what every version of this bug has done
— and it is false the moment somebody picks colours of their own, because then the seeds are no
longer the ones the old palette implies.

So this is safe to run again if it happens a third time.

Revision ID: 0054_restore_palette_again
Revises: 0053_intranet_user_state
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0054_restore_palette_again"
down_revision: Union[str, None] = "0053_intranet_user_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_PALETTE = {
    "evergreen": "#002E2C", "ink": "#002E2C", "secondary": "#334733", "tertiary": "#4D6A4D",
    "slate": "#334733", "muted": "#89A989", "meadow": "#61835E", "meadowInk": "#4D6A4D",
    "meadowBg": "#E9EFE7", "sprout": "#B8CCB8", "parchment": "#F6F0E9", "page": "#F6F0E9",
    "line": "#EAE1D6", "white": "#FFFFFF", "petal": "#FFBA9F", "petalDeep": "#E08863",
    "poppy": "#FA8069", "poppyActive": "#F74926", "poppyText": "#D92B08", "gapText": "#D92B08",
    "mist": "#DCE7E9", "teal": "#227175", "edge": "#B26248", "daffodil": "#FFDD1F",
    "daffodilBg": "#FFF9D6", "daffodilText": "#6D5336", "amber": "#6D5336", "amberBg": "#FFF9D6",
    "onDark": "#F3EEE7", "onDarkMute": "#9CB0AB",
}

PLATFORM_SEEDS = {"brand": "#3F6B66", "surface": "#F5F6F3", "ink": "#16201F",
                  "positive": "#1E5C48", "negative": "#93413A"}


def _seeds_from(palette: dict) -> dict:
    """The same five slots the browser reads back, so a restore and a re-derive agree."""
    return {"brand": palette["poppy"], "surface": palette["page"], "ink": palette["ink"],
            "positive": palette["meadow"], "negative": palette["poppyText"]}


def update_sql(is_pg: bool) -> str:
    value = "CAST(:cfg AS jsonb)" if is_pg else ":cfg"
    return f"UPDATE tenant SET config = {value} WHERE id = :id"


def _load(raw):
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        return dict(json.loads(raw) or {})
    except (TypeError, ValueError):
        return {}


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    rows = bind.execute(sa.text(
        "SELECT id, slug, config, created_at FROM tenant ORDER BY created_at")).fetchall()
    if not rows:
        return

    owner_id = rows[0][0]
    implied = _seeds_from(LEGACY_PALETTE)
    restored = 0
    for row_id, slug, raw, _created in rows:
        if row_id != owner_id:
            continue
        cfg = _load(raw)
        brand = dict(cfg.get("brand") or {})
        if brand.get("palette"):
            print(f"[0054] {slug}: palette present, nothing to do", flush=True)
            continue
        seeds = brand.get("seeds") or {}
        # Either fingerprint: the first accident left the platform's seeds, the second left the
        # ones implied by the palette it discarded.
        if seeds and seeds != implied and seeds != PLATFORM_SEEDS:
            print(f"[0054] {slug}: has chosen its own colours, left alone", flush=True)
            continue

        brand["palette"] = dict(LEGACY_PALETTE)
        brand["seeds"] = dict(implied)
        cfg["brand"] = brand
        bind.execute(sa.text(update_sql(is_pg)), {"cfg": json.dumps(cfg), "id": row_id})
        restored += 1
        print(f"[0054] {slug}: restored {len(LEGACY_PALETTE)} colours", flush=True)

    print(f"[0054] {restored} workspace(s) restored", flush=True)


def downgrade() -> None:
    """Nothing. Removing the palette is the state this exists to undo — twice now."""
