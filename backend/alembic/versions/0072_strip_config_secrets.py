"""Remove credentials saved in plaintext in the portal's integration config.

The console's free-form config editor drops any key that names a credential, but it compared the
raw lowercase key against `api_key`/`apikey`, so "API Key" -- typed the way a person types it --
went straight through. A live Follow Up Boss API key was sitting unencrypted in a workspace's
`intranet_integration.config`, and the console API returned it in its responses. The filter now
compares with separators removed (routers/console._secret_config_key); this removes what the old
one let in.

Nothing reads these values. A provider the dashboard owns (Sisu, Follow Up Boss) keeps its real,
encrypted credential on the dashboard's `integration` row, and the portal's own providers keep
theirs in `credential_ref`. So this deletes copies, never the credential that does the work.

The rule is written out here rather than imported: a migration must mean the same thing when it
is replayed a year from now, whatever the router looks like then.

Revision ID: 0072_strip_config_secrets
Revises: 0071_support_access
"""
import json

from alembic import op
import sqlalchemy as sa

revision = "0072_strip_config_secrets"
down_revision = "0071_support_access"
branch_labels = None
depends_on = None

SECRET_PARTS = ("token", "secret", "password", "credential", "apikey", "accesskey", "privatekey")


def _fold(text) -> str:
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def secret_config_key(key) -> bool:
    folded = _fold(key)
    return any(part in folded for part in SECRET_PARTS)


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, config FROM intranet_integration")).all()
    stripped = 0
    for row_id, config in rows:
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except ValueError:
                continue
        if not isinstance(config, dict):
            continue
        kept = {k: v for k, v in config.items() if not secret_config_key(k)}
        if len(kept) == len(config):
            continue
        stripped += len(config) - len(kept)
        payload = json.dumps(kept)
        if bind.dialect.name == "postgresql":
            bind.execute(sa.text("UPDATE intranet_integration SET config = CAST(:c AS JSONB) "
                                 "WHERE id = :id"), {"c": payload, "id": row_id})
        else:
            bind.execute(sa.text("UPDATE intranet_integration SET config = :c WHERE id = :id"),
                         {"c": payload, "id": row_id})
    print(f"[0072] removed {stripped} plaintext credential key(s) from intranet_integration.config")


def downgrade() -> None:
    # Deliberately nothing: putting a plaintext credential back is not a state worth restoring.
    pass
