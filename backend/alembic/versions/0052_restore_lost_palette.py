"""Put back a palette the appearance panel overwrote

The Appearance panel opened on ACUMYN's five colours rather than the workspace's own, and saving
replaced a hand-built thirty-token palette with those five plus the twenty-five derived from
them. The workspace's dashboard changed colour because somebody opened a settings page and
pressed Save — which is the one thing a settings page must never do.

Two faults, and the second is why the first mattered:

  * The panel pre-filled with the platform defaults when a workspace had no `seeds`, instead of
    reading five back out of the palette it already had. So the form was never showing what the
    workspace looked like; it was showing what it would look like afterwards.
  * The endpoint dropped `palette` outright on save, because storing both a derived and an
    explicit palette would be two sources for one answer. Correct in general, destructive here.

Both are fixed in the code. This restores what was lost.

WHAT IT WILL NOT DO. It only restores a workspace whose palette is EMPTY and whose seeds are
exactly the platform defaults — the fingerprint of the accidental save. A workspace that has
since chosen its own five colours has made a real decision, and putting the old palette back over
it would be this migration repeating the mistake it exists to undo.

The seeds are also set, read back out of the restored palette, so the panel now opens on those
colours instead of the platform's. The palette stays authoritative until somebody deliberately
saves over it.

Revision ID: 0052_restore_lost_palette
Revises: 0051_tenant_plan
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0052_restore_lost_palette"
down_revision: Union[str, None] = "0051_tenant_plan"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Byte-identical to 0049's LEGACY_PALETTE. Repeated rather than imported because a migration must
# keep working when the file beside it is edited or deleted.
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

# The platform defaults the panel wrongly offered. Their presence with an empty palette is the
# fingerprint of the accidental save.
PLATFORM_SEEDS = {"brand": "#3F6B66", "surface": "#F5F6F3", "ink": "#16201F",
                  "positive": "#1E5C48", "negative": "#93413A"}

# The same five slots seedsFromPalette() reads, so the panel opens on these colours.
def _seeds_from(palette: dict) -> dict:
    return {"brand": palette["poppy"], "surface": palette["page"], "ink": palette["ink"],
            "positive": palette["meadow"], "negative": palette["poppyText"]}


def update_sql(is_pg: bool) -> str:
    """See 0046: `:cfg::jsonb` does not bind. CAST keeps the parameter delimited."""
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

    owner_id = rows[0][0]                     # the workspace that predates the picker
    restored = 0
    for row_id, slug, raw, _created in rows:
        if row_id != owner_id:
            continue
        cfg = _load(raw)
        brand = dict(cfg.get("brand") or {})
        if brand.get("palette"):
            print(f"[0052] {slug}: already has a palette, left alone", flush=True)
            continue
        if brand.get("seeds") and brand["seeds"] != PLATFORM_SEEDS:
            print(f"[0052] {slug}: has chosen its own colours, left alone", flush=True)
            continue

        brand["palette"] = dict(LEGACY_PALETTE)
        brand["seeds"] = _seeds_from(LEGACY_PALETTE)
        # Also normalise a mark stored with the API prefix baked in: the browser resolves a
        # server-relative path through fileUrl(), and the doubled prefix silently 404s as a CSS
        # mask — no broken-image icon, no console error, just no logo.
        for key in ("logo", "logomark"):
            value = brand.get(key)
            if isinstance(value, str) and value.startswith("/api/v1/public/"):
                brand[key] = value.replace("/api/v1", "", 1)
        cfg["brand"] = brand
        bind.execute(sa.text(update_sql(is_pg)), {"cfg": json.dumps(cfg), "id": row_id})
        restored += 1
        print(f"[0052] {slug}: restored {len(LEGACY_PALETTE)} colours and set matching seeds",
              flush=True)

    print(f"[0052] {restored} workspace(s) restored", flush=True)


def downgrade() -> None:
    """Nothing. Taking the palette away again is the state this exists to undo."""
