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
    status: str
    tenant: str
    tenant_name: str = ""         # the tenant's own display name, for the rail header
    tabs: list[str] = []          # effective nav tabs, in order
    # The same tabs as {key, label, accent}. The SPA builds its rail from this rather than
    # from a compile-time list of one customer's business names. `tabs` stays because every
    # permission check in the client is a key membership test.
    nav: list[dict] = []
    # Top-level applications on the tenant origin. These are not dashboard tabs and should not
    # be filtered through tab_access; a module can be enabled for the whole workspace.
    apps: list[dict] = []
    # Chrome identity — product name, wordmark text, logo URLs. The SPA had these compiled
    # in, so every tenant's tab title and sidebar showed the first customer's brand.
    brand: dict = {}


# ── multi-user platform (accounts, roles, tab grants) ──────────────
class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class AcceptInviteRequest(BaseModel):
    token: str
    name: str
    password: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class InviteRequest(BaseModel):
    email: str
    role: str                     # admin | member (owner via script/other owner)
    tab_access: list[str] | None = None


class UserUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    tab_access: list[str] | None = None


class UserOut(BaseModel):
    id: str
    name: str
    email: str
    role: str
    status: str                   # active | invited | disabled
    tabs: list[str] = []          # effective tabs (["*"] semantics handled by frontend for owner/admin)
    all_tabs: bool = False        # owner/admin implicitly see everything
    last_login_at: str | None = None


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


class LoanOfficer(BaseModel):
    email: str
    name: str
    funded: int           # loans funded this period
    volume: float         # funded loan volume
    avg_loan: float
    revenue: float        # gross commission this period
    pull_through: int     # funded / (funded + dead), % — all-time


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
    loan_officers: list[LoanOfficer] = []   # Sympli only — per-LO performance
    loan_pipeline: dict | None = None       # Sympli only — {stages, cells} pivot for the pipeline toggle


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
    id: str = ""
    name: str
    refs: int
    gap: bool = False


class FlywheelLender(BaseModel):
    name: str
    count: int


class Flywheel(BaseModel):
    available: bool            # False until Arive is synced
    # The two businesses this flywheel is between, by NAME. The UI wrote "ULRG -> Sympli" into
    # a dozen strings; those are one tenant's company names, so every other tenant read a
    # panel about businesses they do not own.
    source_name: str | None = None      # the brokerage sending referrals
    partner_name: str | None = None     # the JV receiving them
    period_label: str | None = None        # "this month" | "this quarter" | "this year" | "last month"
    buyer_closings: int | None = None      # financeable buy-side closings (cash excluded)
    captured: int | None = None
    lost: int | None = None                # buyer_closings − captured
    capture_pct: int | None = None
    capture_target: float | None = None
    attach_delta_pts: float | None = None  # capture_pct − prior comparable period
    per_loan_share: float | None = None    # from Business; null when unset (NULL or 0)
    gap_dollars: float | None = None       # lost × per_loan_share (period); null when share unset
    gap_at_target: float | None = None     # round(closings × (1 − target/100)) × share
    per_point_value: float | None = None   # closings/100 × share — every attach point ≈ $X
    monthly_gap: float | None = None       # kept for the overview teaser card
    annual_gap: float | None = None
    zero_ref_agents: int | None = None     # buyer-agents who sent 0 to Sympli this period
    agents: list[FlywheelAgent] = []       # (legacy) top referrers
    referrers: list[FlywheelAgent] = []    # ALL referring agents, refs desc (sums to captured)
    zero_agents: list[FlywheelAgent] = []  # producing agents with zero referrals
    # Three-signal attribution extras (the audit cards).
    lost_to: list[FlywheelLender] = []     # competitors winning the uncaptured deals
    sympli_referred: int | None = None     # funded Sympli loans referred by Utah Life (Arive side)
    sympli_referred_linked: int | None = None   # …of those, matched back to a ULRG closing
    vendor_no_loan: int | None = None      # ULRG picked Sympli, but no matching funded loan (gap)
    referral_no_deal: int | None = None    # Sympli logged Utah Life, but no matching ULRG closing (gap)


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


# ── Settings › Integrations (accordion revamp, spec v3 Part 1) ──
class EntityRow(BaseModel):
    integration_id: str
    business_key: str
    business_name: str
    state: str                    # ok | error
    last_synced_at: str | None = None
    realm_id: str | None = None
    detail: str | None = None     # e.g. "Token expired Jun 29"
    display_tab: str | None = None   # the page this entity's P&L routes to (for the edit UI)
    books_enabled: bool = True       # whether it flows into the Books module


class SourceOut(BaseModel):
    provider: str                 # qbo | sisu | fub | ghl | arive
    name: str
    status: str                   # ok | stale | attention | disconnected
    status_note: str | None = None
    fresh: str | None = None      # humanized "Synced 26 min ago"
    feeds: list[str] = []         # business keys → dots
    provides: list[str] = []      # chips
    last_run: str | None = None
    entities: list[EntityRow] = []          # qbo only
    config_summary: list[list[str]] = []    # ghl: [["Location ID","LqK4…f82"], …]
    config: dict | None = None              # raw, for the edit modal (ghl)
    integration_id: str | None = None       # single-row sources
    business_key: str | None = None         # single-row sources (connect wiring)
    # Set only when the tenant has NO business of the role this source feeds, so the card
    # can say "add a lending business first" rather than offering a button that 404s.
    needs_kind: str | None = None


class IntegrationsOut(BaseModel):
    sources: list[SourceOut]
    healthy: int
    total: int
    next_sync_in_min: int | None = None


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
    capture_target: float | None = None            # flywheel attach-rate goal (%)
    expense_run_rate_mode: str | None = None       # trailing_3mo | last_month | manual
    expense_run_rate_manual: float | None = None
    default_agent_split: float | None = None       # 0.60 = 60%
    lo_comp_rate: float | None = None              # Sympli LO split (cost of sale), 0.55 = 55%
    opex_rate: float | None = None                 # Sympli operating-cost ratio, 0.29 = 29%


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
    source: str | None = None      # "Sisu" (ULRG) | "Arive" (Sympli commissions)
    metric: str | None = None      # "closed" | "in commissions"


class FinancialsResponse(BaseModel):
    period: dict                 # {label, start, end, is_current}
    expense_run_rate: float
    expense_run_rate_source: str
    lenses: dict                 # {"live": LiveLens, "projection": ProjectionLens, "booked": BookedLens}
    reconciliation: Reconciliation


# ── beCollective Launch section (SPEC-becollective-launch §8) ────────────────
class LaunchConfigOut(BaseModel):
    name: str
    program: str
    event_start: str | None = None
    event_end: str | None = None
    window_start: str
    window_end: str
    goal_arr: float
    ticket_pif: float
    ticket_plan: float
    plan_installments: int
    mix_pif: float
    price_map: dict = {}             # §5 four-type pricing {type:{acv,upfront,monthly,months,provisional}}
    default_tz: str = "America/Denver"
    stage_map: dict = {}             # group -> stage-name substrings (the funnel's source of truth)
    pipeline_match: str
    cohort_value: str | None = None
    pace_model: str
    pace_tolerance: float
    goal_basis: str = "arr"
    seat_goal: int | None = None
    shift_name: str | None = None
    shift_event_date: str | None = None
    shift_goal: int | None = None
    shift_reg_tag: str | None = None
    shift_reg_tags: list = []
    shift_campaign_match: str | None = None
    shift_actual: int | None = None
    shift_pace_curve: dict = {}
    shift_pace_tolerance: float = 0.08


class ShiftCurvePoint(BaseModel):
    d: int
    pct: float
    count: int


class ShiftChannel(BaseModel):
    key: str
    label: str
    count: int
    pct: int
    paid: bool


class ShiftSources(BaseModel):
    total: int
    paid: int
    organic: int
    comped: int
    channels: list[ShiftChannel]


class ShiftOut(BaseModel):
    name: str
    event_date: str | None = None
    goal: int
    registrants: int
    pct_to_goal: float
    days_to_event: int | None = None
    expected: int
    expected_pct: float
    gap: int
    state: str
    source: str
    reg_to_member: float
    projected_members: int
    members_at_goal: int
    curve: list[ShiftCurvePoint]
    sources: ShiftSources | None = None


class FunnelStage(BaseModel):
    key: str
    label: str
    owner: str
    count: int
    tag: str | None = None


class LaunchGroup(BaseModel):
    pif: int
    plan: int
    seats: int
    arr: float
    mix: dict = {}          # four-type counts {PIF, Financed, Monthly, Custom} (§9.3); {} on the legacy path


class DecidingGroup(BaseModel):
    count: int
    arr: float


class PaceOut(BaseModel):
    expected_arr: float
    gap_arr: float
    state: str


class CashOut(BaseModel):
    collected: float
    source: str


class MomentumOut(BaseModel):
    optins: list[int]
    calls: list[int]
    closes: list[int]
    calls_source: str


class SideOut(BaseModel):
    no_show: int
    nurture: int


class LaunchResponse(BaseModel):
    id: str
    launch: LaunchConfigOut
    goal_basis: str = "arr"
    shift: ShiftOut | None = None
    status: str
    as_of: str
    window_days: int
    days_elapsed: int
    days_remaining: int
    days_to_open: int
    blended_seat: float
    seat_target: int
    enrolled: LaunchGroup
    committed: LaunchGroup
    deciding: DecidingGroup
    funnel: list[FunnelStage]
    side: SideOut
    pace: PaceOut
    pct_to_goal: float
    pct_to_goal_seats: float = 0.0
    seats_remaining: int
    arr_remaining: float
    derived_goal_arr: float = 0.0    # §9.5 — seat_goal × blended (the real goal, ≈ $1.3M), not hardcoded
    cash: CashOut
    momentum: MomentumOut
    warnings: list[str]


class LaunchUpsert(BaseModel):
    """Create/edit a launch. All optional so PUT is a partial patch; POST validates the
    required set in the route."""
    name: str | None = None
    program: str | None = None
    event_start: str | None = None
    event_end: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    goal_arr: float | None = None
    ticket_pif: float | None = None
    ticket_plan: float | None = None
    plan_installments: int | None = None
    mix_pif: float | None = None
    price_map: dict | None = None
    default_tz: str | None = None
    pipeline_match: str | None = None
    cohort_value: str | None = None
    pace_model: str | None = None
    pace_tolerance: float | None = None
    won_grace_days: int | None = None
    goal_basis: str | None = None
    seat_goal: int | None = None
    shift_name: str | None = None
    shift_event_date: str | None = None
    shift_goal: int | None = None
    shift_reg_tag: str | None = None
    shift_reg_tags: list | None = None
    shift_campaign_match: str | None = None
    shift_actual: int | None = None
    shift_pace_curve: dict | None = None
    shift_pace_tolerance: float | None = None
    stage_map: dict | None = None
    payment_plan_map: dict | None = None
    is_active: bool | None = None
