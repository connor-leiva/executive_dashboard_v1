from __future__ import annotations

import uuid
from datetime import datetime, date
from decimal import Decimal

from sqlalchemy import (
    String, Text, ForeignKey, Numeric, Integer, Boolean, DateTime, Date,
    UniqueConstraint, Index, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .dbtypes import GUID, JSONType


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Tenant(Base):
    __tablename__ = "tenant"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Domain(Base):
    __tablename__ = "domain"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    hostname: Mapped[str] = mapped_column(String(255), unique=True)   # e.g. cmd.springb.com
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)


class User(Base):
    __tablename__ = "user"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(32), default="owner")   # owner | admin | viewer
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_user_tenant_email"),)


class Business(Base):
    """The profit centers. Brand config is data so each tab can later adopt its own brand."""
    __tablename__ = "business"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(32))           # ulrg | springb | sympli
    name: Mapped[str] = mapped_column(String(120))
    tag: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), default="healthy")  # healthy|watch|opportunity
    accent: Mapped[str] = mapped_column(String(9), default="#61835E")   # bright (borders/dots)
    ink: Mapped[str] = mapped_column(String(9), default="#4F6A4D")      # readable text accent
    is_jv: Mapped[bool] = mapped_column(Boolean, default=False)
    jv_share: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("1.0"))  # 0.5 = 50%
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # Non-secret per-business config: sparkline trend, manual ops tiles, manual
    # funnel, and scorecard contributions (used until a live source connects).
    config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_business_tenant_key"),)


class Integration(Base):
    """One row per connected source. For QBO, one row per entity (per realmId)."""
    __tablename__ = "integration"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(24))     # fub | sisu | qbo | arive
    business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="disconnected")  # connected|disconnected|error
    realm_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # QBO company id
    access_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # non-secret per-provider config
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Agent(Base):
    __tablename__ = "agent"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business.id"), nullable=True)
    source: Mapped[str] = mapped_column(String(24))          # sisu | fub
    external_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("tenant_id", "source", "external_id", name="uq_agent_src_ext"),)


class Transaction(Base):
    """Real estate deals from Sisu. Drives units, volume, GCI, pipeline, funnel."""
    __tablename__ = "transaction"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id"))
    source: Mapped[str] = mapped_column(String(24), default="sisu")
    external_id: Mapped[str] = mapped_column(String(64))
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)   # buy | sell
    status: Mapped[str] = mapped_column(String(16))     # active | pending | closed | dead
    gci: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    buyer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    buyer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent.id"), nullable=True)
    contract_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        UniqueConstraint("tenant_id", "source", "external_id", name="uq_txn_src_ext"),
        Index("ix_txn_business_status", "tenant_id", "business_id", "status"),
    )


class Lead(Base):
    """FUB people/leads for the top of the funnel."""
    __tablename__ = "lead"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id"))
    source: Mapped[str] = mapped_column(String(24), default="fub")
    external_id: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str | None] = mapped_column(String(80), nullable=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent.id"), nullable=True)
    created_at_src: Mapped[date | None] = mapped_column(Date, nullable=True)
    __table_args__ = (UniqueConstraint("tenant_id", "source", "external_id", name="uq_lead_src_ext"),)


class PLSnapshot(Base):
    """One QuickBooks ProfitAndLoss summary per business per period."""
    __tablename__ = "pl_snapshot"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id"))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    revenue: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    cogs: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    gross_profit: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    opex: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    noi: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    net_income: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    source: Mapped[str] = mapped_column(String(16), default="qbo")
    realm_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pulled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("tenant_id", "business_id", "period_start", "period_end", name="uq_pl_period"),
    )


class CashSnapshot(Base):
    """Cash on hand from the QBO BalanceSheet (bank accounts)."""
    __tablename__ = "cash_snapshot"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business.id"), nullable=True)
    as_of: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)


class SyncRun(Base):
    __tablename__ = "sync_run"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(24))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|ok|error
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── Phase 3 stub (Arive / flywheel) — create the table, do not populate yet ──
class LoanRecord(Base):
    __tablename__ = "loan_record"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(24), default="arive")
    external_id: Mapped[str] = mapped_column(String(64))
    stage: Mapped[str | None] = mapped_column(String(40), nullable=True)  # preapproval|application|locked|funded
    borrower_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    borrower_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    loan_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    referring_agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent.id"), nullable=True)
    matched_transaction_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("transaction.id"), nullable=True)
    funded_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    __table_args__ = (UniqueConstraint("tenant_id", "source", "external_id", name="uq_loan_src_ext"),)
