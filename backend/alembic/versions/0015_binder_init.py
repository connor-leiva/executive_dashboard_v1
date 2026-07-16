"""Acumyn Binder module — initial tables + Business.legal_entity_id

Additive per SPEC-binder-module Part 1: the document-driven obligation engine's tables
(legal entities, stored documents, proposed vs. confirmed obligations, and the jurisdiction
rules engine), plus a nullable Business.legal_entity_id tie so an operating business can be
linked to its legal entity for the tax lifecycle. No existing table is restructured.

Two invariants this schema is designed to protect (both enforced in the service layer, not
by the DB): (1) an Obligation is created only by human confirmation — obligation.confirmed_by
is always set in practice; the extraction pipeline writes proposed_obligation only. (2) No
LegalEntity rows are ever seeded — legal entities are user-created data, so this migration
creates the table but never inserts into it. Only jurisdiction_rule is seeded (Part 4), and
that happens in app code (seed_jurisdiction_rules), not here.

Idempotent (guards) so it is safe to re-run; local/SQLite builds its schema from create_all,
so this migration matters for Postgres (prod).

Revision ID: 0015_binder_init
Revises: 0014_business_routing
Create Date: 2026-07-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.dbtypes import GUID, JSONType

revision: str = "0015_binder_init"
down_revision: Union[str, None] = "0014_business_routing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return column in {c["name"] for c in insp.get_columns(table)}


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Legal entities — the Binder's unit. User-created data; NEVER seeded.
    if not _has_table(bind, "legal_entity"):
        op.create_table(
            "legal_entity",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), index=True),
            sa.Column("legal_name", sa.String(200), nullable=False),
            sa.Column("nickname", sa.String(120), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("entity_type", sa.String(24), nullable=True),
            sa.Column("jurisdiction", sa.String(2), nullable=True),
            sa.Column("formation_date", sa.Date(), nullable=True),
            sa.Column("ein", sa.String(32), nullable=True),
            sa.Column("entity_group", sa.String(8), nullable=False, server_default="operating"),
            sa.Column("ownership", sa.String(16), nullable=True),
            sa.Column("business_id", GUID(), sa.ForeignKey("business.id"), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("tenant_id", "legal_name", name="uq_legal_entity"),
        )

    # 2. Business → legal entity tie (tax-lifecycle only; nullable, nothing else changes).
    if not _has_column(bind, "business", "legal_entity_id"):
        op.add_column("business", sa.Column(
            "legal_entity_id", GUID(), sa.ForeignKey("legal_entity.id"), nullable=True))

    # 3. Jurisdiction rules — reference data (tenant_id NULL = shared system rule). Seeded
    #    by seed_jurisdiction_rules in app code, not here.
    if not _has_table(bind, "jurisdiction_rule"):
        op.create_table(
            "jurisdiction_rule",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), nullable=True, index=True),
            sa.Column("jurisdiction", sa.String(2), nullable=True),
            sa.Column("entity_type", sa.String(24), nullable=True),
            sa.Column("kind", sa.String(24), nullable=False),
            sa.Column("cadence", sa.String(16), nullable=False),
            sa.Column("derivation", sa.String(24), nullable=False),
            sa.Column("params", JSONType, nullable=True),
            sa.Column("lead_days_default", sa.Integer(), nullable=False, server_default="45"),
            sa.Column("last_verified", sa.Date(), server_default=sa.func.current_date()),
            sa.Column("source_note", sa.Text(), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        )

    # 4. Stored documents — evidence behind obligations, or filed on their own.
    if not _has_table(bind, "binder_document"):
        op.create_table(
            "binder_document",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), index=True),
            sa.Column("entity_id", GUID(), sa.ForeignKey("legal_entity.id"), nullable=True),
            sa.Column("filename", sa.String(300), nullable=False),
            sa.Column("category", sa.String(24), nullable=False, server_default="other"),
            sa.Column("storage_ref", sa.String(500), nullable=True),
            sa.Column("content_hash", sa.String(64), nullable=False),
            sa.Column("uploaded_via", sa.String(16), nullable=False, server_default="upload"),
            sa.Column("extracted", JSONType, nullable=True),
            sa.Column("uploaded_by", GUID(), sa.ForeignKey("user.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("tenant_id", "content_hash", name="uq_binder_doc_hash"),
        )

    # 5. Obligations — tracked, matrix-feeding. Created ONLY by human confirm (invariant 1).
    if not _has_table(bind, "obligation"):
        op.create_table(
            "obligation",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), index=True),
            sa.Column("entity_id", GUID(), sa.ForeignKey("legal_entity.id"), nullable=False),
            sa.Column("kind", sa.String(24), nullable=False),
            sa.Column("jurisdiction", sa.String(2), nullable=True),
            sa.Column("applicable", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("due_date", sa.Date(), nullable=True),
            sa.Column("cadence", sa.String(16), nullable=False, server_default="annual"),
            sa.Column("lead_days", sa.Integer(), nullable=False, server_default="45"),
            sa.Column("source_document_id", GUID(), sa.ForeignKey("binder_document.id"), nullable=True),
            sa.Column("rule_id", GUID(), sa.ForeignKey("jurisdiction_rule.id"), nullable=True),
            sa.Column("last_completed", sa.Date(), nullable=True),
            sa.Column("last_confirmed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("confirmed_by", GUID(), sa.ForeignKey("user.id"), nullable=True),
            sa.Column("last_reminded_stage", sa.String(12), nullable=True),
            sa.Column("last_reminded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.UniqueConstraint("tenant_id", "entity_id", "kind", name="uq_obligation"),
        )

    # 6. Proposed obligations — Claude's derivation; the ONLY thing extraction writes.
    #    Never a tracked obligation until a human confirms it (invariant 1).
    if not _has_table(bind, "proposed_obligation"):
        op.create_table(
            "proposed_obligation",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("tenant_id", GUID(), sa.ForeignKey("tenant.id", ondelete="CASCADE"), index=True),
            sa.Column("document_id", GUID(), sa.ForeignKey("binder_document.id"), nullable=False),
            sa.Column("entity_id", GUID(), sa.ForeignKey("legal_entity.id"), nullable=True),
            sa.Column("entity_confidence", sa.Float(), nullable=True),
            sa.Column("entity_candidates", JSONType, nullable=True),
            sa.Column("kind", sa.String(24), nullable=False),
            sa.Column("method", sa.String(8), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("proposed", JSONType, nullable=True),
            sa.Column("basis", sa.Text(), nullable=True),
            sa.Column("flavor", sa.String(12), nullable=False, server_default="normal"),
            sa.Column("renewal_of_id", GUID(), sa.ForeignKey("obligation.id"), nullable=True),
            sa.Column("state", sa.String(12), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("resolved_by", GUID(), sa.ForeignKey("user.id"), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "business", "legal_entity_id"):
        op.drop_column("business", "legal_entity_id")
    for table in ("proposed_obligation", "obligation", "binder_document",
                  "jurisdiction_rule", "legal_entity"):
        if _has_table(bind, table):
            op.drop_table(table)
