from pydantic import BaseModel


# ── auth ──────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str


class MeResponse(BaseModel):
    id: str
    email: str
    name: str
    role: str
    tenant: str


# ── dashboard (mirrors the mockup) ────────────────────────────────
class PLRow(BaseModel):
    label: str
    value: float
    kind: str           # rev | ded | sub | tot | share
    note: str | None = None


class OpTile(BaseModel):
    label: str
    value: str
    sub: str | None = None
    key: str | None = None      # metric key for the audit drawer (drill-down)


class FunnelRow(BaseModel):
    label: str
    v: int


class AreaPayload(BaseModel):
    id: str
    key: str
    name: str
    tag: str
    status: str         # healthy | watch | opportunity
    accent: str
    ink: str
    sources: list[str]
    revenue: float | None      # null until QBO connected (Phase 1)
    noi: float | None
    margin: float | None
    trend: list[float]
    pl: list[PLRow]            # empty until QBO connected
    ops: list[OpTile]
    funnel: list[FunnelRow] | None = None


class CompositionSeg(BaseModel):
    key: str
    name: str
    revenue: float
    pct: float
    accent: str


class Portfolio(BaseModel):
    revenue: float | None
    noi: float | None
    margin: float | None
    mom: float | None = None       # revenue % change vs prior month (mtd only)
    cash: float | None
    composition: list[CompositionSeg]


class Scorecard(BaseModel):
    label: str
    value: str
    sub: str | None = None
    business_key: str          # drives the dot color on the frontend
    key: str | None = None     # metric key for the audit drawer (drill-down)


class FlywheelAgent(BaseModel):
    name: str
    refs: int
    gap: bool = False


class Flywheel(BaseModel):
    available: bool            # False until Phase 3 (Arive)
    buyer_closings: int | None = None
    captured: int | None = None
    capture_pct: int | None = None
    per_loan_share: float | None = None
    monthly_gap: float | None = None
    annual_gap: float | None = None
    agents: list[FlywheelAgent] = []


class SourceStatus(BaseModel):
    name: str
    status: str                # connected | disconnected | error
    last_synced: str | None = None


class DashboardResponse(BaseModel):
    period: dict               # {label, as_of, start, end}
    portfolio: Portfolio
    scorecards: list[Scorecard]
    areas: dict[str, AreaPayload]   # keyed ulrg|springb|sympli
    flywheel: Flywheel
    sources: list[SourceStatus]


# ── integrations ──────────────────────────────────────────────────
class IntegrationStatus(BaseModel):
    id: str
    provider: str
    business_key: str | None = None
    status: str
    realm_id: str | None = None
    last_synced_at: str | None = None
    last_error: str | None = None
    config: dict | None = None      # non-secret per-provider config (pre-fills the edit form)


# ── businesses ────────────────────────────────────────────────────
class BusinessUpdate(BaseModel):
    """Editable brand + health config. All fields optional (partial update)."""
    name: str | None = None
    tag: str | None = None
    status: str | None = None            # healthy | watch | opportunity
    accent: str | None = None
    ink: str | None = None
    is_jv: bool | None = None
    jv_share: float | None = None        # 0.5 = 50%
    watch_margin_below: float | None = None
    per_loan_share: float | None = None
    expense_run_rate_mode: str | None = None       # trailing_3mo | last_month | manual
    expense_run_rate_manual: float | None = None
    default_agent_split: float | None = None       # 0.60 = 60%


# ── three-lens financials ─────────────────────────────────────────
class FinRow(BaseModel):
    l: str
    v: float
    kind: str            # rev | ded | sub | tot
    est: bool = False
    key: str | None = None   # audit drill-down key (opens the drawer)


class LiveLens(BaseModel):
    profit: float
    units: int
    rows: list[FinRow]


class ProjectionLens(BaseModel):
    profit: float
    units: int
    closed_units: int
    pending_units: int
    closed_gci: float
    pending_gci: float
    gci: float
    rows: list[FinRow]


class BookedLens(BaseModel):
    profit: float
    units: int | None = None
    flag: str | None = None
    rows: list[FinRow]


class Reconciliation(BaseModel):
    sisu_closed: float
    qbo_booked: float
    gap_gci: float
    gap_profit: float


class FinancialsResponse(BaseModel):
    period: dict                 # {label, start, end, is_current}
    expense_run_rate: float
    expense_run_rate_source: str
    lenses: dict                 # {"live": LiveLens, "projection": ProjectionLens, "booked": BookedLens}
    reconciliation: Reconciliation
