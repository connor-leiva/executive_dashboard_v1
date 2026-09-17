"""What is wrong with each workspace, and why — derived on read, never stored.

THE CONSOLE'S LAW: no status without its reason. Every state this module returns carries the
plain-English lines that produced it and the action that clears it. Nothing here is written
anywhere: a workspace's state is recomputed from facts on every request, so fixing a cause clears
the label on the next load, and no row can get stuck wearing a status whose cause was fixed
weeks ago. There is deliberately no cache and no precompute job.

One severity ladder, one sort key. The fleet list, a workspace's header and the triage queue all
read `evaluate()`, so they cannot disagree:

    broken    0  a source erroring; a subscription past due
    stalled   1  the owner never signed in (invite older than 3 days); the owner's invite
                 expired; nothing connected more than 2 days after creation
    watch     2  a source stale beyond twice its sync interval; a trial ending within 7 days;
                 invites idle more than 14 days; share links live on a suspended workspace;
                 AI token use at 90% of the month's budget
    trial     3  a subscription trialing, and nothing above
    healthy   4  none of the above
    suspended 5  the workspace is suspended (its signals still reach the triage queue)

Operational metadata only. The facts gathered are who was invited and signed in, what is
connected and whether it errors, counts and timestamps. Never a figure from a tenant's books.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import (AuditLog, Business, Integration, PlatformSubscription, ShareLink, SyncRun,
                      Tenant, User)

RANK = {"broken": 0, "stalled": 1, "watch": 2, "trial": 3, "healthy": 4, "suspended": 5}

INVITE_DAYS = 7
OWNER_SILENT_DAYS = 3
EMPTY_WORKSPACE_DAYS = 2
IDLE_INVITE_DAYS = 14
TRIAL_WARNING_DAYS = 7
BUDGET_WARNING = 0.9

PROVIDER_NAMES = {
    "qbo": "QuickBooks", "sisu": "Sisu", "fub": "Follow Up Boss", "ghl": "Go High Level",
    "ghl_bc": "Go High Level (second account)", "ghl_legacy": "Go High Level (legacy)",
    "stripe_legacy": "Stripe", "stripe_bc": "Stripe (second account)", "arive": "Arive",
    "meta_ads": "Meta Marketing",
}


def provider_name(key: str) -> str:
    return PROVIDER_NAMES.get(key, key)


def _aware(d: dt.datetime | None) -> dt.datetime | None:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def _days(delta: dt.timedelta) -> int:
    return max(0, delta.days)


def _span(delta: dt.timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''}"


def stale_after(provider: str) -> dt.timedelta:
    """Twice the cadence a source actually syncs on. Ads sync on their own, slower schedule."""
    minutes = settings.ADS_SYNC_INTERVAL_MINUTES if provider == "meta_ads" \
        else settings.SYNC_INTERVAL_MINUTES
    return dt.timedelta(minutes=2 * minutes)


# ── facts ─────────────────────────────────────────────────────────────────────────────────
@dataclass
class SourceFact:
    id: str
    provider: str
    status: str                       # connected | disconnected | error
    business: str | None = None
    last_synced_at: dt.datetime | None = None
    last_error: str | None = None
    failed_runs_7d: int = 0

    @property
    def label(self) -> str:
        return provider_name(self.provider) + (f" · {self.business}" if self.business else "")

    @property
    def configured(self) -> bool:
        return self.status in ("connected", "error")


@dataclass
class PersonFact:
    id: str
    email: str
    name: str
    role: str
    status: str                       # active | invited | disabled
    last_login_at: dt.datetime | None = None
    created_at: dt.datetime | None = None
    invite_expires: dt.datetime | None = None
    locked_until: dt.datetime | None = None
    two_factor: bool = False
    expires_at: dt.datetime | None = None

    def invited_at(self) -> dt.datetime | None:
        """When the live invite was issued: its expiry minus the lifetime, else the account's
        creation. Reissuing an invite restarts the clock, which is the point of reissuing it."""
        if self.invite_expires is not None:
            return self.invite_expires - dt.timedelta(days=INVITE_DAYS)
        return self.created_at


@dataclass
class WorkspaceFacts:
    slug: str
    name: str
    status: str
    created_at: dt.datetime | None
    sources: list[SourceFact] = field(default_factory=list)
    people: list[PersonFact] = field(default_factory=list)
    share_links_live: int = 0
    tokens_used: int = 0
    token_budget: int | None = None   # None is unlimited
    subscription: dict | None = None  # {"status", "trial_end"} once platform billing exists
    suspended_at: dt.datetime | None = None
    suspended_by: str | None = None
    suspended_reason: str | None = None
    syncs_frozen: bool = False
    frozen_at: dt.datetime | None = None
    frozen_by: str | None = None
    frozen_reason: str | None = None


@dataclass
class Signal:
    severity: str
    key: str
    title: str
    detail: str
    meta: str
    derivation: str
    primary_action: dict | None = None
    secondary_action: dict | None = None

    def out(self, slug: str, name: str) -> dict:
        return {"severity": self.severity, "key": f"{slug}:{self.key}", "tenant_slug": slug,
                "tenant_name": name, "title": self.title, "detail": self.detail,
                "meta": self.meta, "derivation": self.derivation,
                "primary_action": self.primary_action, "secondary_action": self.secondary_action}


def _action(label: str, action: str, **params) -> dict:
    return {"label": label, "action": action, **params}


def idle_invites(people: list[PersonFact], now: dt.datetime) -> list[PersonFact]:
    """Invites nobody has accepted in more than IDLE_INVITE_DAYS, the owner's excepted: the owner's
    invite has signals of its own. The triage row and the action that clears it both read this."""
    return [p for p in people if p.role != "owner" and p.status == "invited"
            and p.invited_at() is not None
            and now - _aware(p.invited_at()) > dt.timedelta(days=IDLE_INVITE_DAYS)]


# ── rules ─────────────────────────────────────────────────────────────────────────────────
def signals(f: WorkspaceFacts, now: dt.datetime) -> list[Signal]:
    out: list[Signal] = []
    created = _aware(f.created_at)
    age = (now - created) if created else dt.timedelta(0)

    # BROKEN: a source erroring. One row per source, however many runs it failed: nine failed
    # runs from one expired credential are one cause, and clearing it clears all nine.
    for src in f.sources:
        if src.status != "error":
            continue
        runs = f"{src.failed_runs_7d} failed run{'s' if src.failed_runs_7d != 1 else ''} in 7 days"
        last = _aware(src.last_synced_at)
        out.append(Signal(
            "broken", f"source:{src.id}", f"{src.label} is failing",
            (src.last_error or "The last sync failed without an error message.")[:300],
            f"provider {src.provider} · {runs} · last good sync "
            + (f"{_span(now - last)} ago" if last else "never"),
            "Derived on read from integration.status and integration.last_error, which the sync "
            "writes on every run. Nothing is stored here, so a successful sync clears this row.",
            _action("Send reconnect link", "reconnect_link", source_id=src.id),
            _action("Open sources", "open", pane="sources")))

    sub = f.subscription or {}
    if sub.get("status") == "past_due":
        out.append(Signal(
            "broken", "billing:past_due", "Subscription past due",
            "Stripe could not collect the last invoice. Access is unaffected until someone decides "
            "otherwise, but nothing will be collected until the payment method is fixed.",
            "stripe · status past_due",
            "Derived on read from the mirrored Stripe subscription status.",
            _action("Send payment link", "payment_link"), _action("Open billing", "open", pane="billing")))

    # STALLED: onboarding that is not going to finish by itself.
    owners = [p for p in f.people if p.role == "owner"]
    active_owner = any(p.status == "active" for p in owners)
    if owners and not active_owner:
        owner = owners[0]
        expires = _aware(owner.invite_expires)
        invited_at = _aware(owner.invited_at())
        if owner.status == "invited" and expires is not None and expires <= now:
            out.append(Signal(
                "stalled", "owner:expired", "The owner's invite expired",
                f"It expired {_span(now - expires)} ago and nobody can administer this workspace "
                "until a new one is accepted.",
                f"{owner.email} · invite expired",
                "Derived on read from the owner's invite expiry and last sign-in.",
                _action("Reissue invite", "resend_owner_invite"),
                _action("Open people", "open", pane="people")))
        elif owner.status == "invited" and invited_at is not None \
                and now - invited_at > dt.timedelta(days=OWNER_SILENT_DAYS):
            left = (expires - now) if expires else None
            out.append(Signal(
                "stalled", "owner:silent", "The owner has never signed in",
                f"Invited {_span(now - invited_at)} ago. Until the invite is accepted, nobody can "
                "administer the workspace or connect anything.",
                f"{owner.email}" + (f" · invite valid {_span(left)} more" if left else ""),
                "Derived on read from the owner's invite date and last sign-in.",
                _action("Resend invite", "resend_owner_invite"),
                _action("Open people", "open", pane="people")))

    if not any(src.configured for src in f.sources) \
            and age > dt.timedelta(days=EMPTY_WORKSPACE_DAYS) and f.status != "suspended":
        out.append(Signal(
            "stalled", "sources:none", "Nothing is connected",
            f"Created {_span(age)} ago with no source connected, so every panel in the workspace "
            "is empty.",
            f"0 sources · {len(f.people)} account{'s' if len(f.people) != 1 else ''}",
            "Derived on read from the workspace's integration rows and its creation date.",
            _action("Send setup link", "send_setup_link"),
            _action("Open sources", "open", pane="sources")))

    # WATCH: things going wrong slowly.
    if not f.syncs_frozen and f.status != "suspended":
        for src in f.sources:
            if src.status != "connected":
                continue
            last = _aware(src.last_synced_at)
            limit = stale_after(src.provider)
            if last is None or now - last > limit:
                out.append(Signal(
                    "watch", f"stale:{src.id}", f"{src.label} is stale",
                    ("It has never completed a sync." if last is None
                     else f"Last synced {_span(now - last)} ago. The schedule would have run it "
                          f"at least {int((now - last) / (limit / 2))} times since."),
                    f"provider {src.provider} · stale after {_span(limit)}",
                    "Derived on read from integration.last_synced_at against twice the provider's "
                    "sync interval.",
                    _action("Run sync now", "sync_source", source_id=src.id),
                    _action("Open sources", "open", pane="sources")))

    trial_end = _aware(sub.get("trial_end"))
    if sub.get("status") == "trialing" and trial_end is not None \
            and dt.timedelta(0) <= trial_end - now <= dt.timedelta(days=TRIAL_WARNING_DAYS):
        out.append(Signal(
            "watch", "billing:trial_ending", f"Trial ends in {_span(trial_end - now)}",
            "The trial converts to a paid subscription on its end date.",
            "stripe · status trialing",
            "Derived on read from the mirrored Stripe subscription's trial_end.",
            _action("Open billing", "open", pane="billing"), None))

    idle = idle_invites(f.people, now)
    if idle:
        out.append(Signal(
            "watch", "people:idle_invites",
            f"{len(idle)} invite{'s' if len(idle) != 1 else ''} idle more than {IDLE_INVITE_DAYS} days",
            "Invited people who never set a password still count against the plan's seats.",
            " · ".join(p.email for p in idle[:3]) + (" · …" if len(idle) > 3 else ""),
            "Derived on read from each invited account's invite date.",
            _action("Resend invites", "resend_idle_invites"),
            _action("Open people", "open", pane="people")))

    if f.status == "suspended" and f.share_links_live:
        out.append(Signal(
            "watch", "share:live_while_suspended",
            f"{f.share_links_live} share link{'s' if f.share_links_live != 1 else ''} still live on a "
            "suspended workspace",
            "Suspension blocks sign-in, but public share links keep serving until they are revoked.",
            f"{f.share_links_live} live",
            "Derived on read from share_link rows with no revoked_at and no past expiry.",
            _action("Revoke all", "revoke_share_links"),
            _action("Open access", "open", pane="access")))

    if f.token_budget and f.tokens_used >= BUDGET_WARNING * f.token_budget:
        used = round(100 * f.tokens_used / f.token_budget)
        out.append(Signal(
            "watch", "ai:budget", f"AI token use at {used}% of this month's budget",
            "Over budget, AI employee runs are skipped rather than failing, until the month rolls "
            "over or the budget rises.",
            f"{f.tokens_used:,} of {f.token_budget:,} tokens",
            "Derived on read from this month's ai_run token counts against the workspace's budget.",
            _action("Open billing", "open", pane="billing"), None))

    if sub.get("status") == "trialing" and not out:
        out.append(Signal(
            "trial", "billing:trialing", "On a trial", "Nothing else needs attention.",
            "stripe · status trialing", "Derived on read from the mirrored Stripe subscription.",
            _action("Open billing", "open", pane="billing"), None))

    out.sort(key=lambda sig: RANK[sig.severity])
    return out


def evaluate(f: WorkspaceFacts, now: dt.datetime | None = None) -> dict:
    """The workspace's state, the reasons for it, and every signal behind it."""
    now = now or dt.datetime.now(dt.timezone.utc)
    sigs = signals(f, now)

    if f.status == "suspended":
        why = []
        if f.suspended_at:
            who = f" by {f.suspended_by}" if f.suspended_by else ""
            why.append(f"Suspended {_span(now - _aware(f.suspended_at))} ago{who}")
        else:
            why.append("Suspended")
        if f.suspended_reason:
            why.append(f"Reason: {f.suspended_reason}")
        state = "suspended"
        derivation = ("Derived on read from tenant.status and the tenant's own audit log. "
                      "Sign-in is refused and scheduled syncs are skipped while suspended.")
    elif sigs:
        state = sigs[0].severity
        why = [sig.title for sig in sigs if sig.severity == state][:3]
        derivation = sigs[0].derivation
    else:
        state = "healthy"
        configured = [src for src in f.sources if src.configured]
        why = [f"All {len(configured)} source{'s' if len(configured) != 1 else ''} syncing"
               if configured else "Nothing connected yet"]
        last_login = max((_aware(p.last_login_at) for p in f.people if p.last_login_at), default=None)
        why.append(f"Last sign-in {_span(now - last_login)} ago" if last_login
                   else "Nobody has signed in yet")
        derivation = ("Derived on read: no source is erroring or stale, onboarding is not stuck, "
                      "and nothing else in the ladder applies.")
    if f.syncs_frozen and state != "suspended":
        frozen = ["Syncs are frozen"]
        if f.frozen_at:
            who = f" by {f.frozen_by}" if f.frozen_by else ""
            frozen = [f"Syncs frozen {_span(now - _aware(f.frozen_at))} ago{who}"]
        if f.frozen_reason:
            frozen.append(f"Freeze reason: {f.frozen_reason}")
        why = frozen + why
    return {"state": state, "rank": RANK[state], "why": why, "derivation": derivation,
            "signals": [sig.out(f.slug, f.name) for sig in sigs]}


# ── gathering ─────────────────────────────────────────────────────────────────────────────
def month_start(now: dt.datetime) -> dt.datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def token_budget_for(tenant: Tenant) -> int | None:
    """The monthly AI token budget this workspace is held to, or None for unlimited.

    ZERO MEANS UNLIMITED, and it is normalised to None here, once. Zero is falsy but present, and
    the design once read it four places as "no AI at all", the opposite fact. A workspace's own
    override (config.ai_token_budget, set from the operator console) wins over the platform default.
    """
    cfg = tenant.config or {}
    raw = cfg.get("ai_token_budget", settings.AI_EMPLOYEES_TOKEN_BUDGET)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = settings.AI_EMPLOYEES_TOKEN_BUDGET
    return value or None


async def gather(s: AsyncSession, tenant: Tenant, now: dt.datetime | None = None) -> WorkspaceFacts:
    from ..models import AIRun

    now = now or dt.datetime.now(dt.timezone.utc)
    week_ago = now - dt.timedelta(days=7)

    rows = (await s.execute(
        select(Integration, Business.name).outerjoin(Business, Business.id == Integration.business_id)
        .where(Integration.tenant_id == tenant.id))).all()
    fails = dict((await s.execute(
        select(SyncRun.provider, func.count()).where(
            SyncRun.tenant_id == tenant.id, SyncRun.status == "error", SyncRun.started_at >= week_ago)
        .group_by(SyncRun.provider))).all())
    sources = [SourceFact(id=str(i.id), provider=i.provider, status=i.status, business=biz,
                          last_synced_at=i.last_synced_at, last_error=i.last_error,
                          failed_runs_7d=int(fails.get(i.provider, 0)))
               for i, biz in rows]

    users = (await s.execute(select(User).where(User.tenant_id == tenant.id)
                             .order_by(User.created_at))).scalars().all()
    people = [PersonFact(
        id=str(u.id), email=u.email, name=u.name, role=u.role, status=u.status,
        last_login_at=u.last_login_at, created_at=u.created_at,
        invite_expires=u.action_token_expires if u.action_token_purpose == "invite" else None,
        locked_until=u.locked_until, two_factor=u.totp_confirmed_at is not None,
        expires_at=getattr(u, "expires_at", None)) for u in users]

    live_links = (await s.execute(select(func.count()).select_from(ShareLink).where(
        ShareLink.tenant_id == tenant.id, ShareLink.revoked_at.is_(None)))).scalar_one()
    expired_links = (await s.execute(select(func.count()).select_from(ShareLink).where(
        ShareLink.tenant_id == tenant.id, ShareLink.revoked_at.is_(None),
        ShareLink.expires_at.is_not(None), ShareLink.expires_at <= now))).scalar_one()

    tokens = (await s.execute(select(func.coalesce(func.sum(AIRun.tokens_in + AIRun.tokens_out), 0))
                              .where(AIRun.tenant_id == tenant.id,
                                     AIRun.created_at >= month_start(now)))).scalar_one()

    facts = WorkspaceFacts(
        slug=tenant.slug, name=tenant.name, status=tenant.status, created_at=tenant.created_at,
        sources=sources, people=people, share_links_live=int(live_links) - int(expired_links),
        tokens_used=int(tokens or 0), token_budget=token_budget_for(tenant),
        syncs_frozen=bool((tenant.config or {}).get("syncs_frozen")))

    # C11: a trial is Stripe's `trialing`, read from the mirror; no column on the tenant says so.
    sub = await s.get(PlatformSubscription, tenant.id)
    if sub is not None:
        facts.subscription = {"status": sub.status, "trial_end": sub.trial_end}

    # Who suspended or froze it, when and why, from the audit row the action wrote. The state lives
    # on the tenant; the reason for it lives in the trail, where it cannot drift from what happened.
    if tenant.status == "suspended":
        last = await _last_audit(s, tenant, "tenant.suspended")
        if last is not None:
            facts.suspended_at = last.created_at
            facts.suspended_by = (last.detail or {}).get("by")
            facts.suspended_reason = (last.detail or {}).get("reason")
    if facts.syncs_frozen:
        last = await _last_audit(s, tenant, "tenant.syncs_frozen")
        if last is not None:
            facts.frozen_at = last.created_at
            facts.frozen_by = (last.detail or {}).get("by")
            facts.frozen_reason = (last.detail or {}).get("reason")
    return facts


async def _last_audit(s: AsyncSession, tenant: Tenant, action: str) -> AuditLog | None:
    return (await s.execute(select(AuditLog).where(
        AuditLog.tenant_id == tenant.id, AuditLog.action == action)
        .order_by(AuditLog.created_at.desc()).limit(1))).scalar_one_or_none()
