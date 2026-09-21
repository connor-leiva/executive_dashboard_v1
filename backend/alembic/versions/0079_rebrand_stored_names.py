"""Carry the two stored occurrences of the old name across to Axcion

Acumyn became Axcion in September 2026 (AXCION-REBRAND-SPEC.md). Most of that rename is
text in source files. Two values are different: they were WRITTEN INTO THE DATABASE under
the old name and are read back by code that now spells it the new way. Renaming the code
without these would not raise anything — each one fails by quietly falling back.

  * `tenant.config -> brand -> typeface`. The API validates this against a name list
    (routers/users.py TYPEFACES) and the browser resolves it through PAIRINGS
    (frontend/src/typefaces.js). Both now say "axcion". A workspace still holding "acumyn"
    falls through to PAIRINGS[DEFAULT_PAIRING] and silently re-renders in a different
    typeface — the same set of fonts today, because acumyn WAS the default, but the row
    would no longer mean what it says and the next default change would move it.

  * There WAS going to be a third: the Win-the-Day playbook's `format`. There is not, and the
    reason is worth keeping. `format` belongs to the EXPORT ENVELOPE, not to the stored
    document -- `export_bundle()` writes it into a file, and the importer reads
    `bundle["content"]`, the inner document, so the string never reaches this table. A
    migration here would have matched zero rows for ever while reading as though it were
    doing something. Confirmed against production: the one playbook row has no `format` key.

    The real exposure is a FILE somebody exported before the rename and imports afterwards,
    which no migration can reach. That is handled where it actually lives, by accepting both
    spellings in `wtd_playbook._Bundle`.

  * The support account (routers/platform._support_email and the name beside it). An
    operator's time-boxed read-only account, addressed `<operator>+acumyn-support@…` and
    named "… (Acumyn support)". The address is how open_support_access finds an existing
    support account rather than creating a second one, so leaving it stale would strand the
    old row and mint a new account next to it.

WHY ONE MIGRATION AND NOT THREE. The spec sketched these as 0079/0080/0081. They are one
logical change — the rename — and an atomic one is better here: if the support-account
update fails on a collision, nobody wants the typeface half applied and the rest not. One
revision is also one head, and a fork in this file's history crash-loops the API on deploy.
(One of the three then turned out not to exist at all; see above.)

WHAT THIS DOES NOT TOUCH, deliberately:

  * `audit_log.actor_label` and `platform_audit`. Rows recording what was done under the
    name the platform had at the time. Rewriting an audit trail is the one change an audit
    trail exists to prevent, so both keep saying "(Acumyn)" and new rows say "(Axcion)".
  * Stripe metadata (`acumyn_tenant_id`, `acumyn_slug`). Platform billing has never been
    connected — platform_billing_config, platform_subscription and platform_invoice are all
    empty — so there is nothing in Stripe carrying the old key and nothing to backfill. The
    code writes the new key. If billing HAS been connected since this was written, the
    reader falls back to the mirrored customer id, so nothing breaks, but the new key wants
    backfilling onto existing subscriptions.
  * The R2 bucket `acumyn-storage`. R2 cannot rename a bucket and a partial copy of 51
    Binder documents is silent loss of customer files. The name is never shown to anyone.

Expected scope in production, confirmed by scripts/scan_rebrand.py: 1 tenant row
(testrealty) and 1 support account. Small enough to verify by eye afterwards, which is worth doing.

Revision ID: 0079_rebrand_stored_names
Revises: 0078_sop_suggestions
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0079_rebrand_stored_names"
down_revision: Union[str, None] = "0078_sop_suggestions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_TYPEFACE, NEW_TYPEFACE = "acumyn", "axcion"
OLD_TAG, NEW_TAG = "+acumyn-support@", "+axcion-support@"
OLD_SUFFIX, NEW_SUFFIX = "(Acumyn support)", "(Axcion support)"


def _load(raw):
    """A JSON column reads back as a dict on Postgres and as a string on SQLite."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        return dict(json.loads(raw) or {})
    except (TypeError, ValueError):
        return {}


def _set_json(table: str, column: str, is_pg: bool) -> str:
    """See 0046: `:param::jsonb` does not bind. CAST keeps the parameter delimited."""
    value = f"CAST(:val AS jsonb)" if is_pg else ":val"
    return f"UPDATE {table} SET {column} = {value} WHERE id = :id"


def _swap_typeface(bind, is_pg, frm: str, to: str) -> int:
    rows = bind.execute(sa.text("SELECT id, slug, config FROM tenant")).fetchall()
    moved = 0
    for row_id, slug, raw in rows:
        cfg = _load(raw)
        brand = cfg.get("brand")
        if not isinstance(brand, dict) or brand.get("typeface") != frm:
            continue
        brand = dict(brand)
        brand["typeface"] = to
        cfg["brand"] = brand
        bind.execute(sa.text(_set_json("tenant", "config", is_pg)),
                     {"val": json.dumps(cfg), "id": row_id})
        moved += 1
        print(f"[0079] tenant {slug}: typeface {frm} -> {to}", flush=True)
    return moved


def _swap_support_accounts(bind, frm_tag: str, to_tag: str,
                           frm_suffix: str, to_suffix: str) -> int:
    """Re-address the operator support accounts.

    `(tenant_id, email)` is unique, so a row is skipped rather than updated if the target
    address is already taken in that workspace — an integrity error here would roll back the
    typeface work above for a collision that is almost certainly a support account that has
    already been re-addressed.
    """
    rows = bind.execute(sa.text(
        'SELECT id, tenant_id, email, name FROM "user" WHERE email LIKE :pat'
    ), {"pat": f"%{frm_tag}%"}).fetchall()
    moved = 0
    for row_id, tenant_id, email, name in rows:
        new_email = email.replace(frm_tag, to_tag)
        taken = bind.execute(sa.text(
            'SELECT 1 FROM "user" WHERE tenant_id = :t AND email = :e AND id <> :id'
        ), {"t": tenant_id, "e": new_email, "id": row_id}).first()
        if taken:
            print(f"[0079] SKIPPED {email}: {new_email} already exists in that workspace",
                  flush=True)
            continue
        new_name = (name or "").replace(frm_suffix, to_suffix) or None
        bind.execute(sa.text('UPDATE "user" SET email = :e, name = :n WHERE id = :id'),
                     {"e": new_email, "n": new_name, "id": row_id})
        moved += 1
        print(f"[0079] support account {email} -> {new_email}", flush=True)
    return moved


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    t = _swap_typeface(bind, is_pg, OLD_TYPEFACE, NEW_TYPEFACE)
    s = _swap_support_accounts(bind, OLD_TAG, NEW_TAG, OLD_SUFFIX, NEW_SUFFIX)
    print(f"[0079] {t} typeface, {s} support account(s) renamed", flush=True)


def downgrade() -> None:
    """Exactly the inverse. Worth having: this runs against a restored production copy as
    the spec's Phase 6.8 check, and a one-way migration cannot be rehearsed."""
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    t = _swap_typeface(bind, is_pg, NEW_TYPEFACE, OLD_TYPEFACE)
    s = _swap_support_accounts(bind, NEW_TAG, OLD_TAG, NEW_SUFFIX, OLD_SUFFIX)
    print(f"[0079] reverted {t} typeface, {s} support account(s)", flush=True)
