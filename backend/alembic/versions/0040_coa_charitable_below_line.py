"""Books COA: move Charitable Donations below the operating line, and refresh the chart.

Connor resolved Decision 4 on 2026-08-20 — charitable giving is not a cost of running the
business, so it leaves Sales Promotion (6570) for the below-the-line range (9410). Left inside
an operating bucket it makes Net Operating Income move with a discretionary choice, and two
entities with identical operations show different operating margins.

The row is REPOINTED IN PLACE rather than dropped and recreated. `standard_account.id` is what
`coa_map.standard_account_id` points at, so recreating it would silently unmap every account
somebody had already mapped to charitable donations. Renumbering a chart must never cost a
mapping decision.

Written to be correct whether or not this database has already run 0038: if 6570 was never
seeded, the repoint finds nothing and the chart refresh seeds 9410 directly.

Revision ID: 0040_coa_charitable_below_line
Revises: 0039_coa_map
"""
from typing import Sequence, Union
import uuid

import sqlalchemy as sa
from alembic import op

from app.dbtypes import GUID, JSONType

revision: str = "0040_coa_charitable_below_line"
down_revision: Union[str, None] = "0039_coa_map"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD, _NEW = "6570", "9410"


def _tables(bind) -> set:
    return set(sa.inspect(bind).get_table_names())


def _chart_table() -> sa.Table:
    return sa.table(
        "standard_account",
        sa.column("id", GUID()), sa.column("tenant_id", GUID()),
        sa.column("code", sa.String), sa.column("name", sa.String),
        sa.column("bucket", sa.String), sa.column("statement", sa.String),
        sa.column("section", sa.String), sa.column("normal_balance", sa.String),
        sa.column("sort_order", sa.Integer), sa.column("is_active", sa.Boolean),
        sa.column("is_intercompany_account", sa.Boolean),
        sa.column("recognition", sa.String), sa.column("archetypes", JSONType),
        sa.column("definition", sa.Text),
    )


def _refresh_chart(bind, rows: list[dict]) -> None:
    """Upsert the chart for every tenant: insert what is missing, refresh the policy fields on
    what is present. `is_active` is deliberately not touched — a deliberate deactivation has to
    survive, exactly as it does in services.coa.seed_standard_chart."""
    tbl = _chart_table()
    policy = ("name", "bucket", "statement", "section", "normal_balance", "sort_order",
              "is_intercompany_account", "recognition", "archetypes", "definition")
    for (tenant_id,) in bind.execute(sa.text("SELECT id FROM tenant")).fetchall():
        have = {r[0] for r in bind.execute(
            sa.text("SELECT code FROM standard_account WHERE tenant_id = :t"),
            {"t": tenant_id}).fetchall()}
        missing = [{**r, "id": uuid.uuid4(), "tenant_id": tenant_id, "is_active": True}
                   for r in rows if r["code"] not in have]
        if missing:
            op.bulk_insert(tbl, missing)
        for r in rows:
            if r["code"] in have:
                bind.execute(
                    tbl.update()
                    .where(sa.and_(tbl.c.tenant_id == tenant_id, tbl.c.code == r["code"]))
                    .values(**{k: r[k] for k in policy}))


def upgrade() -> None:
    bind = op.get_bind()
    if "standard_account" not in _tables(bind):
        return                                   # 0038 has not run here; nothing to correct

    from app.services.coa import chart_rows      # local: keeps alembic's import light
    rows = chart_rows()
    new = next(r for r in rows if r["code"] == _NEW)

    # Repoint 6570 -> 9410 in place, keeping the id (and therefore every coa_map row that
    # already points at it). The "does this tenant already hold 9410" guard is done in Python
    # rather than as a correlated EXISTS: it runs once over a handful of tenants, and a
    # renumbering migration is the wrong place to be clever about SQL.
    tbl = _chart_table()
    holders = bind.execute(sa.text(
        "SELECT tenant_id, code FROM standard_account WHERE code IN (:old, :new)"),
        {"old": _OLD, "new": _NEW}).fetchall()
    by_tenant: dict = {}
    for tenant_id, code in holders:
        by_tenant.setdefault(tenant_id, set()).add(code)
    for tenant_id, codes in by_tenant.items():
        if _OLD not in codes or _NEW in codes:
            continue                     # nothing to move, or the target already exists
        bind.execute(
            tbl.update()
            .where(sa.and_(tbl.c.tenant_id == tenant_id, tbl.c.code == _OLD))
            .values(code=_NEW, name=new["name"], bucket=new["bucket"],
                    statement=new["statement"], section=new["section"],
                    normal_balance=new["normal_balance"], sort_order=new["sort_order"],
                    recognition=new["recognition"], archetypes=new["archetypes"],
                    definition=new["definition"]))

    # Everything else in the chart moved by one sort_order slot when 6570 left the 6500s, and
    # the gross-presentation decision rewrote some definitions. Refresh them all.
    _refresh_chart(bind, rows)


def downgrade() -> None:
    """Put charitable donations back in Sales Promotion, again in place."""
    bind = op.get_bind()
    if "standard_account" not in _tables(bind):
        return
    tbl = _chart_table()
    bind.execute(
        tbl.update().where(tbl.c.code == _NEW).values(
            code=_OLD, name="Charitable Donations", bucket="sales_promotion",
            statement="pl", section="opex",
            definition="Placement and tax treatment still open with Acuity "
                       "(Decision 4 in the mapping spec)."))
