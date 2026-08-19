"""beCollective Launch section — read + CRUD (SPEC-becollective-launch §8 + §11).

View is gated on the program tab (becollective); edits are owner/admin and audited.
Reroute/pricing edits never touch synced opp data — they only reprice on the next read."""
import uuid
import datetime as dt
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import secrets

from ..config import settings
from ..db import get_session
from ..deps import current_user, require_role, assert_tab
from ..models import User, Business, Launch, SalesCall, SalesRep, ShareLink
from ..schemas import LaunchResponse, LaunchUpsert
from ..services.audit import audit
from ..services.launch import (
    compute_launch, active_launch_for, config_out, drill_launch,
    DEFAULT_STAGE_MAP, DEFAULT_PAYMENT_PLAN_MAP,
)
from ..services.tabs import PROGRAM_TABS

router = APIRouter(tags=["launches"])

_DATE_FIELDS = {"event_start", "event_end", "window_start", "window_end", "shift_event_date"}
_NUM_FIELDS = {"goal_arr", "ticket_pif", "ticket_plan", "mix_pif", "pace_tolerance",
               "shift_pace_tolerance"}
_REQUIRED = {"name", "window_start", "window_end", "goal_arr",
             "ticket_pif", "ticket_plan", "pipeline_match"}


def _launch_tab(b: Business) -> str:
    """The nav tab this business's launch renders under (beCollective, else own key)."""
    tabs = PROGRAM_TABS.get(b.key)
    return "becollective" if (tabs and "becollective" in tabs) else b.key


async def _biz(s: AsyncSession, tenant_id, key: str) -> Business:
    b = (await s.execute(select(Business).where(
        Business.tenant_id == tenant_id, Business.key == key))).scalar_one_or_none()
    if not b:
        raise HTTPException(404, "Unknown business")
    return b


def _pdate(v):
    if v in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        raise HTTPException(400, f"Bad date: {v!r}")


def _apply(launch: Launch, fields: dict) -> None:
    for name, val in fields.items():
        if name in _DATE_FIELDS:
            val = _pdate(val)
        elif name in _NUM_FIELDS and val is not None:
            try:
                val = Decimal(str(val))
            except (InvalidOperation, ValueError):
                raise HTTPException(400, f"{name} must be a number")
        setattr(launch, name, val)


async def _find_launch(s, tenant_id, b: Business, launch_id: str) -> Launch:
    launch = (await s.execute(select(Launch).where(   # GUID() coerces the string id
        Launch.tenant_id == tenant_id, Launch.business_id == b.id,
        Launch.id == launch_id))).scalar_one_or_none()
    if not launch:
        raise HTTPException(404, "Unknown launch")
    return launch


@router.get("/businesses/{key}/launches/active", response_model=LaunchResponse)
async def active_launch(key: str, user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session)):
    """The one launch to render in the section. 404 when none is active (section absent)."""
    b = await _biz(s, user.tenant_id, key)
    await assert_tab(user, s, _launch_tab(b))
    launch = await active_launch_for(s, user.tenant_id, b.id)
    if not launch:
        raise HTTPException(404, "No active launch")
    return await compute_launch(s, user.tenant_id, launch)


@router.get("/businesses/{key}/launches/active/sales-desk")
async def active_sales_desk(key: str, user: User = Depends(current_user),
                            s: AsyncSession = Depends(get_session)):
    """The Sales Desk payload (SPEC-becollective-salesdesk §8) — rep throughput + schedule,
    all computed from the SalesCall event log. 404 when no launch is active (section absent)."""
    from ..services.sales_desk import compute_sales_desk
    b = await _biz(s, user.tenant_id, key)
    await assert_tab(user, s, _launch_tab(b))
    launch = await active_launch_for(s, user.tenant_id, b.id)
    if not launch:
        raise HTTPException(404, "No active launch")
    return await compute_sales_desk(s, user.tenant_id, launch)


@router.get("/businesses/{key}/launches/active/sales-desk/drill/{metric}")
async def drill_active_sales_desk(key: str, metric: str, rep: str | None = None,
                                  user: User = Depends(current_user),
                                  s: AsyncSession = Depends(get_session)):
    """What's behind a number on the Sales Desk — its calls/opps or its calculation. `rep`
    scopes call-backed metrics to one leaderboard row ('__unassigned__' = the null-rep bucket)."""
    from ..services.sales_desk import drill_sales_desk
    b = await _biz(s, user.tenant_id, key)
    await assert_tab(user, s, _launch_tab(b))
    launch = await active_launch_for(s, user.tenant_id, b.id)
    if not launch:
        raise HTTPException(404, "No active launch")
    return await drill_sales_desk(s, user.tenant_id, launch, metric, rep=rep)


@router.get("/businesses/{key}/launches/active/sales-desk/recording/{call_id}")
async def sales_desk_recording(key: str, call_id: uuid.UUID,
                               user: User = Depends(current_user),
                               s: AsyncSession = Depends(get_session)):
    """A playable link for one call's recording, minted on demand.

    Recall's media URLs are signed and expire after 5 hours, so we deliberately do NOT hand
    the stored one to the browser - it would be a link that works this afternoon and dies
    overnight. The dashboard asks for a fresh one at the moment somebody clicks. Access is
    gated by the same tab permission as the rest of the Desk, so a rep can watch their own
    calls without anyone sharing the single Recall login.
    """
    from ..services.recall import fresh_recording_url
    b = await _biz(s, user.tenant_id, key)
    await assert_tab(user, s, _launch_tab(b))
    sc = (await s.execute(select(SalesCall).where(
        SalesCall.id == call_id, SalesCall.tenant_id == user.tenant_id))).scalar_one_or_none()
    if sc is None:
        raise HTTPException(404, "No such call")
    if not sc.recall_bot_id:
        raise HTTPException(404, "No recording for this call")
    url = await fresh_recording_url(sc.recall_bot_id)
    if not url:
        # Bot exists but no media: still in the waiting room, or the recording aged out of
        # Recall's retention window. Say which rather than returning a dead link.
        raise HTTPException(409, f"No playable recording yet (status: {sc.recording_status or 'unknown'})")
    return {"url": url}


@router.get("/businesses/{key}/sales-desk/reps")
async def list_sales_reps(key: str, user: User = Depends(current_user),
                          s: AsyncSession = Depends(get_session)):
    """The rep roster (email → display name). Seeded from the GHL directory, but the roster is
    the UNION of that directory and every email seen on a call for the active launch — the
    `Sales Rep` field is free-form, so it routinely carries reps the directory doesn't have.
    Those surface here `unmapped` (no display name yet) so they can be named; naming one upserts
    a SalesRep row. Viewable with the tab; edited only by owner/admin (§11.7)."""
    b = await _biz(s, user.tenant_id, key)
    await assert_tab(user, s, _launch_tab(b))
    reps = (await s.execute(select(SalesRep).where(
        SalesRep.tenant_id == user.tenant_id))).scalars().all()

    # Every rep actually on a call for the active launch, so non-directory reps can be mapped.
    call_emails: list[str] = []
    launch = await active_launch_for(s, user.tenant_id, b.id)
    if launch:
        call_emails = [e for e in (await s.execute(select(SalesCall.rep_email).where(
            SalesCall.tenant_id == user.tenant_id, SalesCall.launch_id == launch.id,
            SalesCall.rep_email.is_not(None)).distinct())).scalars().all() if e]
    on_calls = {e.lower() for e in call_emails}

    out = [{"email": r.email, "display_name": r.display_name, "is_active": r.is_active,
            "on_calls": (r.email or "").lower() in on_calls, "unmapped": False} for r in reps]
    seen = {(r["email"] or "").lower() for r in out}
    for e in call_emails:                                  # call-reps with no roster row yet
        if e.lower() not in seen:
            seen.add(e.lower())
            out.append({"email": e, "display_name": None, "is_active": True,
                        "on_calls": True, "unmapped": True})
    # Needs-a-name first (on a call, not yet named), then the rest alphabetically.
    out.sort(key=lambda r: (not r["unmapped"], (r["display_name"] or r["email"] or "").lower()))
    return out


@router.put("/businesses/{key}/sales-desk/reps")
async def update_sales_reps(key: str, body: dict, user: User = Depends(require_role("owner", "admin")),
                            s: AsyncSession = Depends(get_session)):
    """Bulk upsert rep display names / active flags. Presentation only — never call data.
    Owner/admin, audited (§11.7 / §12 roles+audit)."""
    await _biz(s, user.tenant_id, key)
    existing = {(r.email or "").lower(): r for r in
                (await s.execute(select(SalesRep).where(SalesRep.tenant_id == user.tenant_id))).scalars()}
    changed = []
    for item in (body.get("reps") or []):
        email = (item.get("email") or "").strip()
        if not email:
            continue
        raw = (item.get("display_name") or "").strip()
        cur = existing.get(email.lower())
        if cur is None:
            if not raw:                          # unmapped rep left unnamed — don't create a ghost row
                continue
            rep = SalesRep(tenant_id=user.tenant_id, email=email, display_name=raw[:80],
                           is_active=bool(item.get("is_active", True)))
            s.add(rep)
            existing[email.lower()] = rep        # a repeat of this email in the same body now updates it
            changed.append(email)
        else:
            name = (raw or cur.display_name or email)[:80]                       # a blank keeps the name
            active = bool(item["is_active"]) if "is_active" in item else cur.is_active  # omitted → unchanged
            if cur.display_name != name or cur.is_active != active:
                cur.display_name, cur.is_active = name, active
                changed.append(email)
    if changed:
        audit(s, user.tenant_id, user.id, "sales_rep.roster_updated", "sales_rep", None,
              {"emails": sorted(changed)[:50]})
    await s.commit()
    return {"updated": len(changed)}


def _desk_share_url(token: str) -> str:
    return f"{settings.APP_PUBLIC_URL.rstrip('/')}/desk/{token}"


@router.get("/businesses/{key}/sales-desk/reps/share")
async def list_rep_shares(key: str, user: User = Depends(require_role("owner", "admin")),
                          s: AsyncSession = Depends(get_session)):
    """Live per-rep share links (email → url), owner/admin. Feeds the manage-reps drawer."""
    await _biz(s, user.tenant_id, key)
    rows = (await s.execute(select(ShareLink).where(
        ShareLink.tenant_id == user.tenant_id, ShareLink.scope == "sd_rep",
        ShareLink.revoked_at.is_(None)))).scalars().all()
    return {(l.scope_ref or "").lower(): _desk_share_url(l.token) for l in rows if l.scope_ref}


@router.post("/businesses/{key}/sales-desk/reps/share", status_code=201)
async def create_rep_share(key: str, body: dict, user: User = Depends(require_role("owner", "admin")),
                           s: AsyncSession = Depends(get_session)):
    """Mint (or return the live) personal share link for one rep — their own numbers only.
    Owner/admin, audited. Body: {email}."""
    await _biz(s, user.tenant_id, key)
    email = (body.get("email") or "").strip()
    if not email:
        raise HTTPException(400, "email required")
    live = (await s.execute(select(ShareLink).where(
        ShareLink.tenant_id == user.tenant_id, ShareLink.scope == "sd_rep",
        ShareLink.scope_ref == email.lower(), ShareLink.revoked_at.is_(None)))).scalars().first()
    if live:
        return {"email": email, "url": _desk_share_url(live.token), "existing": True}
    link = ShareLink(tenant_id=user.tenant_id, scope="sd_rep", scope_ref=email.lower(),
                     token=secrets.token_urlsafe(24), created_by=user.id)
    s.add(link)
    audit(s, user.tenant_id, user.id, "sales_rep.share_created", "share_link", None, {"email": email})
    await s.commit()
    return {"email": email, "url": _desk_share_url(link.token), "existing": False}


@router.delete("/businesses/{key}/sales-desk/reps/share", status_code=204)
async def revoke_rep_share(key: str, email: str, user: User = Depends(require_role("owner", "admin")),
                           s: AsyncSession = Depends(get_session)):
    """Revoke a rep's share link — the URL then 404s everywhere. Owner/admin, audited."""
    import datetime as _dt
    await _biz(s, user.tenant_id, key)
    rows = (await s.execute(select(ShareLink).where(
        ShareLink.tenant_id == user.tenant_id, ShareLink.scope == "sd_rep",
        ShareLink.scope_ref == email.strip().lower(), ShareLink.revoked_at.is_(None)))).scalars().all()
    for l in rows:
        l.revoked_at = _dt.datetime.now(_dt.timezone.utc)
    if rows:
        audit(s, user.tenant_id, user.id, "sales_rep.share_revoked", "share_link", None, {"email": email})
    await s.commit()


@router.get("/businesses/{key}/launches/active/drill/{metric}")
async def drill_active_launch(key: str, metric: str, user: User = Depends(current_user),
                              s: AsyncSession = Depends(get_session)):
    """What's behind a number on the Launch tab — its records or its calculation."""
    b = await _biz(s, user.tenant_id, key)
    await assert_tab(user, s, _launch_tab(b))
    launch = await active_launch_for(s, user.tenant_id, b.id)
    if not launch:
        raise HTTPException(404, "No active launch")
    return await drill_launch(s, user.tenant_id, launch, metric)


@router.get("/businesses/{key}/launches")
async def list_launches(key: str, user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session)):
    b = await _biz(s, user.tenant_id, key)
    await assert_tab(user, s, _launch_tab(b))
    rows = (await s.execute(select(Launch).where(
        Launch.tenant_id == user.tenant_id, Launch.business_id == b.id)
        .order_by(Launch.window_start.desc()))).scalars().all()
    return [{"id": str(l.id), "is_active": l.is_active, **config_out(l)} for l in rows]


@router.post("/businesses/{key}/launches", response_model=LaunchResponse, status_code=201)
async def create_launch(key: str, body: LaunchUpsert,
                        user: User = Depends(require_role("owner", "admin")),
                        s: AsyncSession = Depends(get_session)):
    b = await _biz(s, user.tenant_id, key)
    fields = body.model_dump(exclude_unset=True)
    missing = _REQUIRED - set(fields)
    if missing:
        raise HTTPException(400, f"Missing required: {sorted(missing)}")
    launch = Launch(
        tenant_id=user.tenant_id, business_id=b.id,
        stage_map=fields.pop("stage_map", None) or DEFAULT_STAGE_MAP,
        payment_plan_map=fields.pop("payment_plan_map", None) or DEFAULT_PAYMENT_PLAN_MAP,
    )
    _apply(launch, fields)
    s.add(launch)
    await s.flush()
    audit(s, user.tenant_id, user.id, "launch.create", "launch", launch.id, {"name": launch.name})
    await s.commit()
    return await compute_launch(s, user.tenant_id, launch)


@router.put("/businesses/{key}/launches/{launch_id}", response_model=LaunchResponse)
async def update_launch(key: str, launch_id: str, body: LaunchUpsert,
                        user: User = Depends(require_role("owner", "admin")),
                        s: AsyncSession = Depends(get_session)):
    b = await _biz(s, user.tenant_id, key)
    launch = await _find_launch(s, user.tenant_id, b, launch_id)
    fields = body.model_dump(exclude_unset=True)
    _apply(launch, fields)
    audit(s, user.tenant_id, user.id, "launch.update", "launch", launch.id, {"fields": sorted(fields)})
    await s.commit()
    return await compute_launch(s, user.tenant_id, launch)
