"""Write down the defaults that were being applied invisibly, before they stop being applied

Three values were hardcoded as fallbacks in the compute layer, and all three were one
customer's answer being used as everybody's:

  * metrics._arive_states defaulted to {"UT"}. Arive is a lender's full multi-state loan
    system; the first customer only wants their own market out of it. For anyone lending
    elsewhere that is not a default, it is a silent erasure — connect Arive, the sync reports
    success, every loan is dropped by a filter nothing in the UI mentions, pipeline reads zero.

  * sync.py setdefault("referral_domains", ["liveutah.com"]) wrote the first customer's domain
    into the config of whoever synced next, as the thing identifying THEIR referrals. Their
    flywheel would then reconcile to zero forever with no screen explaining it.

  * The GHL member split defaulted to literal tag names ("the forum active", "inner circle
    active") that only one workspace uses, with no field in the connect form to change them.

The code now defaults to unfiltered / unset / no-sub-segmentation, which are the versions that
cannot quietly hide correct data. That change would move the FIRST customer's numbers, because
production config was verified to carry none of these keys — an empty forum_tags list, no
`states`, no `referral_domains`. She is running entirely on the fallbacks being removed.

So this migration writes the implicit value onto every integration that exists right now. Any
row present at this moment is by definition running on the old default, so grandfathering all of
them preserves today's behaviour exactly; rows created afterwards get the honest defaults.

Deliberately additive and idempotent: only ever fills in a key that is absent or empty, never
overwrites a value somebody chose. Safe to run twice, and safe if a config was edited between
deploy and migration.

Revision ID: 0046_grandfather_implicit_defaults
Revises: 0045_platform_operators
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0046_grandfather_implicit_defaults"
down_revision: Union[str, None] = "0045_platform_operators"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# provider -> {config key: the value the code used to supply when the key was missing}
IMPLICIT = {
    "arive": {"states": ["UT"]},
    "sisu": {"referral_domains": ["liveutah.com"]},
    "ghl": {"forum_tags": ["the forum active", "member: secondary", "forumadmin"],
            "innercircle_tags": ["inner circle active", "inner circle active add on"]},
    "ghl_bc": {"forum_tags": ["the forum active", "member: secondary", "forumadmin"],
               "innercircle_tags": ["inner circle active", "inner circle active add on"]},
}


def _load(raw):
    """config comes back as a dict on Postgres (JSONB) and as text on SQLite."""
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
        "SELECT id, provider, config FROM integration WHERE provider IN "
        "('arive','sisu','ghl','ghl_bc')")).fetchall()

    changed = 0
    for row_id, provider, raw in rows:
        cfg = _load(raw)
        wanted = IMPLICIT.get(provider) or {}
        # `or []` in the old code meant an EMPTY list also fell through to the default, so an
        # empty value counts as absent here too — production has exactly that for forum_tags.
        patch = {k: v for k, v in wanted.items() if not cfg.get(k)}
        if not patch:
            continue
        cfg.update(patch)
        payload = json.dumps(cfg)
        bind.execute(
            sa.text(f"UPDATE integration SET config = :cfg{'::jsonb' if is_pg else ''} "
                    f"WHERE id = :id"),
            {"cfg": payload, "id": row_id})
        changed += 1
        print(f"[0046] {provider} {row_id}: pinned {sorted(patch)}", flush=True)

    print(f"[0046] grandfathered {changed} of {len(rows)} integrations", flush=True)


def downgrade() -> None:
    """Deliberately does nothing.

    Removing these keys would hand the rows back to defaults that no longer exist in the code,
    which is not the state they came from — the old behaviour lived in Python, not in the
    database. Leaving the values in place is both harmless and more honest: they are now simply
    explicit configuration, which is what they should always have been.
    """
