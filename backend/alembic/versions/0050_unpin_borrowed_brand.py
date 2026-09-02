"""Un-pin the first customer's brand from workspaces it was never theirs to be on

0049 wrote the compiled-in palette, type and sign-in imagery onto EVERY workspace that existed,
reasoning that anything present at that moment was rendering those values and pinning them would
preserve what its users saw. The first half is true. The conclusion was wrong.

What the other workspaces saw was the defect being fixed. `acmerealty` and `testrealty` were
rendering one customer's colours and her photograph on their sign-in screens because the palette
was compiled into the bundle — that is the single-tenant assumption leaking, not a preference
anybody expressed. 0049 preserved it, which turned a bug into stored configuration.

Grandfathering is right when the current behaviour is correct FOR THAT ROW. Here it was correct
for exactly one row.

So: keep it on the workspace whose brand it actually is — the one that predates multi-tenancy by
two months — and clear it everywhere else, so those workspaces fall back to Acumyn's identity
until they choose their own.

ONLY CLEARS WHAT 0049 WROTE. Each value is compared against 0049's literals before being removed,
so a workspace that has since chosen its own colours keeps them. A repair migration that also
discards somebody's deliberate choice is a second bug wearing the first one's clothes.

Revision ID: 0050_unpin_borrowed_brand
Revises: 0049_grandfather_palette
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0050_unpin_borrowed_brand"
down_revision: Union[str, None] = "0049_grandfather_palette"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

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

LEGACY_TYPE = {
    "display": "Poppins,sans-serif",
    "text": "Inter,sans-serif",
    "data": "Archivo,sans-serif",
}

LEGACY_LOGIN = {
    "hero_image": "/brand/photos/gradient_1.jpg",
    "photo": "/brand/photos/spring_pic_10.jpg",
}

# The eight ribbed gradients are part of that customer's delivered visual identity, so they are
# addressed as HER configuration rather than as a platform asset. Everyone else gets Acumyn's
# bokeh, which is Acumyn's own artwork.
HERO_PLATES = {
    "evergreen": "/brand/RibbedGradient_Evergreen.jpg",
    "meadow": "/brand/RibbedGradient_Meadow.jpg",
    "poppy": "/brand/RibbedGradient_Poppy.jpg",
    "mist": "/brand/RibbedGradient_Mist.jpg",
    "parchment": "/brand/RibbedGradient_Parchment.jpg",
    "petal": "/brand/RibbedGradient_Petal.jpg",
    "daffodil": "/brand/RibbedGradient_Daffodil.jpg",
    "sprout": "/brand/RibbedGradient_Sprout.jpg",
}


def update_sql(is_pg: bool) -> str:
    """See 0046. `:cfg::jsonb` does not bind; CAST keeps the parameter delimited."""
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

    # The workspace this brand belongs to: the one that existed before there was such a thing as
    # a second workspace. Identified by age rather than by name so the migration does not encode
    # a customer into the schema's history.
    owner_id = rows[0][0]

    cleared = 0
    for row_id, slug, raw, _created in rows:
        cfg = _load(raw)
        brand = dict(cfg.get("brand") or {})
        if not brand:
            continue

        if row_id == owner_id:
            if brand.get("hero_plates"):
                continue
            brand["hero_plates"] = dict(HERO_PLATES)
            cfg["brand"] = brand
            bind.execute(sa.text(update_sql(is_pg)), {"cfg": json.dumps(cfg), "id": row_id})
            print(f"[0050] {slug}: kept its own brand, added {len(HERO_PLATES)} hero plates",
                  flush=True)
            continue

        touched = []
        if brand.get("palette") == LEGACY_PALETTE:
            brand["palette"] = {}
            touched.append("palette")
        if brand.get("type") == LEGACY_TYPE:
            brand["type"] = {}
            touched.append("type")
        for key, value in LEGACY_LOGIN.items():
            if brand.get(key) == value:
                brand[key] = None
                touched.append(key)
        if not touched:
            continue
        cfg["brand"] = brand
        bind.execute(sa.text(update_sql(is_pg)), {"cfg": json.dumps(cfg), "id": row_id})
        cleared += 1
        print(f"[0050] {slug}: released borrowed {', '.join(touched)}", flush=True)

    print(f"[0050] {cleared} workspace(s) returned to the platform's identity", flush=True)


def downgrade() -> None:
    """Nothing. Putting another customer's brand back on these workspaces is not a state worth
    being able to return to."""
