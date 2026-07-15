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
    # Portfolio-scoped, non-secret config (e.g. Books intercompany elimination account
    # list `books_elim_accounts`). Portfolio-level because eliminations span businesses.
    config: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
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
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)   # null while invited
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(32), default="owner")   # owner | admin | member
    # ── multi-user platform (accounts, roles, tab grants) ──
    status: Mapped[str] = mapped_column(String(16), default="active")       # active | invited | disabled
    tab_access: Mapped[list | None] = mapped_column(JSONType, nullable=True)  # member grants; NULL for owner/admin
    token_version: Mapped[int] = mapped_column(Integer, default=0)          # bump to revoke all outstanding tokens
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    action_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)     # sha256 of invite/reset token
    action_token_purpose: Mapped[str | None] = mapped_column(String(16), nullable=True)  # invite | reset
    action_token_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_user_tenant_email"),)


class AuditLog(Base):
    """Every user-management + integration mutation, for the sell-to-teams trail."""
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(48))       # user.invited | user.role_changed | integration.connected …
    target_type: Mapped[str | None] = mapped_column(String(24), nullable=True)   # user | integration | business
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONType, nullable=True)          # {"from": "member", "to": "admin"}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Business(Base):
    """The profit centers. Brand config is data so each tab can later adopt its own brand."""
    __tablename__ = "business"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(32))           # ulrg | springb | sympli
    name: Mapped[str] = mapped_column(String(120))
    tag: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), default="healthy")  # healthy|watch|opportunity
    # Financial-entity model + page routing (QBO entity routing). `kind` dispatches the
    # compute_financials shape so a non-real-estate entity never renders a Sisu-shaped P&L.
    # `display_tab` is the nav-tab key this entity's FINANCIAL area renders on — the
    # user-configurable routing target; NULL means "use this entity's own key". A business
    # is left out of portfolio/consolidation totals when include_in_portfolio is False
    # (e.g. the operational-only springb holder after the QBO account split).
    kind: Mapped[str] = mapped_column(String(24), default="real_estate")  # real_estate|commission_jv|membership|holding
    display_tab: Mapped[str | None] = mapped_column(String(32), nullable=True)
    include_in_portfolio: Mapped[bool] = mapped_column(Boolean, default=True)
    accent: Mapped[str] = mapped_column(String(9), default="#61835E")   # bright (borders/dots)
    ink: Mapped[str] = mapped_column(String(9), default="#4F6A4D")      # readable text accent
    is_jv: Mapped[bool] = mapped_column(Boolean, default=False)
    jv_share: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("1.0"))  # 0.5 = 50%
    # Flag "watch" when the period margin falls below this (data-driven status).
    watch_margin_below: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    # Spring's avg JV revenue per funded loan (drives the flywheel gap, Part 4).
    per_loan_share: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # Attach-rate goal for the flywheel (percent; default 60).
    capture_target: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("60"))
    # Three-lens financials: monthly expense run-rate + commission fallback.
    expense_run_rate_mode: Mapped[str] = mapped_column(String(16), default="trailing_3mo")  # trailing_3mo|last_month|manual
    expense_run_rate_manual: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    default_agent_split: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)  # e.g. 0.60 fallback
    # Sympli JV economics (reverse-engineered from the QBO P&L, tunable in Settings):
    # the loan-officer split (cost of sale) and the operating-cost ratio, both as a
    # fraction of commission revenue. Drive the calculated Live/Projection lenses.
    lo_comp_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)   # 0.55 = 55%
    opex_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)      # 0.29 = 29%
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
    agent_commission: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)  # cost of sale
    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    buyer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    buyer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Attachment-flywheel join keys (Sisu vendor pick + extra borrower contacts).
    mortgage_vid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    buyer_email2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    buyer_phone: Mapped[str | None] = mapped_column(String(16), nullable=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent.id"), nullable=True)
    contract_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_close_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # pending → projection
    # Sisu funnel/leading-indicator dates + raw stage code.
    appt_set_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    lead_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    listing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sisu_status_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
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
    books_closed: Mapped[bool] = mapped_column(Boolean, default=False)  # clears the Booked "close in progress" flag
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
    stats: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # {"records": 412, "seconds": 3.1}


class MetricRecord(Base):
    """Flexible records behind non-real-estate metrics (Go High Level: members,
    subscriptions, event registrations). Also feeds the audit drawer."""
    __tablename__ = "metric_record"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id"))
    source: Mapped[str] = mapped_column(String(24))          # ghl
    kind: Mapped[str] = mapped_column(String(32))            # member | subscription | registration
    external_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)   # active | cancelled | registered
    segment: Mapped[str | None] = mapped_column(String(32), nullable=True)  # becollective | forum
    occurred_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    __table_args__ = (UniqueConstraint("tenant_id", "source", "kind", "external_id", name="uq_metricrec"),)


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


# ══ Acumyn Books module (SPEC-books-module Part 1) ══════════════════════════
# Additive: Books runs the bookkeeping the Command Center reads. The scan pipeline
# and review draft write ONLY to these tables; QuickBooks write-back is a separate,
# feature-flagged, human-approved action. Nothing here replaces PLSnapshot (the
# dashboard's summary source of truth) — PLLine is the detail underneath it.

class BookTxn(Base):
    """One ledger transaction pulled from QBO — the unit the scan pipeline and
    approval queue operate on."""
    __tablename__ = "book_txn"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id"))
    realm_id: Mapped[str] = mapped_column(String(64))                 # QBO company id (matches Integration.realm_id)
    qbo_type: Mapped[str] = mapped_column(String(24))                 # Purchase|Deposit|JournalEntry|Transfer|Bill|BillPayment
    qbo_id: Mapped[str] = mapped_column(String(32))
    sync_token: Mapped[str | None] = mapped_column(String(16), nullable=True)   # sparse-update write-back needs it
    txn_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    payee: Mapped[str | None] = mapped_column(String(200), nullable=True)
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_label: Mapped[str | None] = mapped_column(String(200), nullable=True)   # current category
    account_qbo_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bank_account_label: Mapped[str | None] = mapped_column(String(200), nullable=True)  # feed it rode in on
    came_categorized: Mapped[bool] = mapped_column(Boolean, default=False)  # arrived on a real (non-suspense) account
    scan_state: Mapped[str] = mapped_column(String(16), default="pending")
        # pending -> (cleared | needs_approval | escalated) -> approved -> posted
    suggestion: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
        # {"category", "account_qbo_id", "confidence", "reason"}
    flags: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
        # {"anomaly", "intercompany", "first_vendor", "over_band", "possible_1099", "multi_line"}
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
        # {"action": "approve"|"recategorize"|"escalate"|"ic_characterized", "category", ...}
    posted_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # write-back only
    __table_args__ = (UniqueConstraint("tenant_id", "realm_id", "qbo_type", "qbo_id", name="uq_booktxn_src"),)


class PLLine(Base):
    """Account-level P&L detail behind PLSnapshot group totals — feeds the P&L page.
    The (business, period) sum of section lines ties to the snapshot (Part 7 invariant)."""
    __tablename__ = "pl_line"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id"))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    section: Mapped[str] = mapped_column(String(16))                  # income | cogs | expense | other
    parent: Mapped[str | None] = mapped_column(String(200), nullable=True)   # report group header path (e.g. Marketing)
    label: Mapped[str] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    position: Mapped[int] = mapped_column(Integer, default=0)         # preserve report row order
    __table_args__ = (
        UniqueConstraint("tenant_id", "business_id", "period_start", "period_end",
                         "section", "parent", "label", name="uq_pl_line"),
    )


class ICRule(Base):
    """CFO-set intercompany policy. Transfers matching a rule flow through; everything
    else stops and escalates (never guessed)."""
    __tablename__ = "ic_rule"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(200))                  # "Office rent to holding LLC"
    from_business_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("business.id"), nullable=True)  # null = any
    to_business_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("business.id"), nullable=True)    # null = any
    characterization: Mapped[str] = mapped_column(String(24))        # loan|distribution|contribution|shared_expense|rent|payroll_alloc
    monthly_cap: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)  # null = no cap; over cap escalates
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ICLink(Base):
    """One intercompany movement — two BookTxn sides tied together, or one side awaiting
    its match / a human characterization. Open (non-tied) links block the close."""
    __tablename__ = "ic_link"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    from_business_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("business.id"))
    to_business_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("business.id"))
    from_txn_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("book_txn.id"), nullable=True)
    to_txn_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("book_txn.id"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    occurred_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="unmatched")
        # unmatched -> matched -> (auto_tied | escalated) -> characterized -> tied
    characterization: Mapped[str | None] = mapped_column(String(24), nullable=True)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("ic_rule.id"), nullable=True)  # set when a rule auto-tied it
    decided_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ClosePeriod(Base):
    """Month-end close checklist state, one row per business per month."""
    __tablename__ = "close_period"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business.id"))
    period: Mapped[date] = mapped_column(Date)                       # month start
    steps: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
        # {"bank_rec", "card_rec", "intercompany", "accruals", "statements"} bools
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | closed
    closed_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("tenant_id", "business_id", "period", name="uq_close_period"),)


class BooksReview(Base):
    """Claude's drafted monthly narrative. Draft until the CFO signs off; Claude never
    posts to the official record."""
    __tablename__ = "books_review"
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenant.id", ondelete="CASCADE"), index=True)
    period: Mapped[date] = mapped_column(Date)                       # month start
    body: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | signed
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    signed_by: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("user.id"), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("tenant_id", "period", name="uq_books_review"),)
