"""Pin the existing workspace's colours before the palette stops being compiled in

Every colour in the SPA read `T` from theme.js, which held one customer's 30 hexes as literals.
Those are now CSS variables whose defaults are Acumyn's brand identity, overridden per workspace
at sign-in. Good for every workspace that comes next; a visible, unrequested redesign for the one
that already exists, whose dashboard would simply come up in somebody else's colours.

Same reasoning as 0046. Any workspace present at this moment is by definition running on the
compiled-in palette, so writing that palette onto it preserves exactly what its users see today,
while anything created afterwards starts from Acumyn's identity.

Additive and idempotent: only fills `brand.palette` when it is absent or empty, and never
overwrites a workspace that has chosen its own.

Revision ID: 0049_grandfather_palette
Revises: 0048_ads_contract_value
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0049_grandfather_palette"
down_revision: Union[str, None] = "0048_ads_contract_value"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The palette as it was compiled into the bundle, token for token.
LEGACY_PALETTE = {
    "evergreen": "#002E2C",
    "ink": "#002E2C",
    "secondary": "#334733",
    "tertiary": "#4D6A4D",
    "slate": "#334733",
    "muted": "#89A989",
    "meadow": "#61835E",
    "meadowInk": "#4D6A4D",
    "meadowBg": "#E9EFE7",
    "sprout": "#B8CCB8",
    "parchment": "#F6F0E9",
    "page": "#F6F0E9",
    "line": "#EAE1D6",
    "white": "#FFFFFF",
    "petal": "#FFBA9F",
    "petalDeep": "#E08863",
    "poppy": "#FA8069",
    "poppyActive": "#F74926",
    "poppyText": "#D92B08",
    "gapText": "#D92B08",
    "mist": "#DCE7E9",
    "teal": "#227175",
    "edge": "#B26248",
    "daffodil": "#FFDD1F",
    "daffodilBg": "#FFF9D6",
    "daffodilText": "#6D5336",
    "amber": "#6D5336",
    "amberBg": "#FFF9D6",
    "onDark": "#F3EEE7",
    "onDarkMute": "#9CB0AB",
}

# Poppins/Inter is what those components were set in; the new default is Acumyn's Space Grotesk
# and Instrument Sans, so the type is pinned for the same reason the colour is.
LEGACY_TYPE = {
    "display": "Poppins,sans-serif",
    "text": "Inter,sans-serif",
    "data": "Archivo,sans-serif",
}

# The sign-in screen's imagery was two hardcoded paths in auth.jsx, so it is pinned for the same
# reason as the colours: the files still exist, they are simply addressed as this workspace's
# choice now rather than as everybody's default.
LEGACY_LOGIN = {
    "hero_image": "/brand/photos/gradient_1.jpg",
    "photo": "/brand/photos/spring_pic_10.jpg",
}


def update_sql(is_pg: bool) -> str:
    """See 0046: `:cfg::jsonb` does not bind — text() will not read `:cfg` as a parameter when a
    colon follows it. CAST keeps the parameter delimited."""
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
    rows = bind.execute(sa.text("SELECT id, slug, config FROM tenant")).fetchall()

    changed = 0
    for row_id, slug, raw in rows:
        cfg = _load(raw)
        brand = dict(cfg.get("brand") or {})
        if brand.get("palette") and brand.get("type") and brand.get("hero_image"):
            continue
        brand.setdefault("palette", {})
        brand.setdefault("type", {})
        if not brand["palette"]:
            brand["palette"] = dict(LEGACY_PALETTE)
        if not brand["type"]:
            brand["type"] = dict(LEGACY_TYPE)
        for key, value in LEGACY_LOGIN.items():
            brand.setdefault(key, value)
        cfg["brand"] = brand
        bind.execute(sa.text(update_sql(is_pg)), {"cfg": json.dumps(cfg), "id": row_id})
        changed += 1
        print(f"[0047] {slug}: pinned {len(brand['palette'])} colours + type", flush=True)

    print(f"[0047] grandfathered {changed} of {len(rows)} workspaces", flush=True)


def downgrade() -> None:
    """Nothing. Removing the palette would hand a live workspace back to a default that is not
    the state it came from — the old palette lived in the bundle, not in the database."""
