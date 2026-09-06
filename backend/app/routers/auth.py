import datetime as dt
import uuid

from fastapi import (APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request,
                     Response)
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import current_user
from ..models import IntranetWorkspace, Tenant, User
from ..schemas import (LoginRequest, LoginResponse, MeResponse, ChangePasswordRequest,
                       AcceptInviteRequest, ForgotPasswordRequest, ResetPasswordRequest,
                       ActionLinkRequest, ActionLinkInfo)
from ..config import settings
from ..security import (verify_pw, make_token, hash_pw, hash_action_token, new_action_token,
                        make_capability, read_capability,
                        MIN_PASSWORD_LEN)
from .. import plans
from ..services.audit import audit
from ..services import binder_storage, google_auth, mail_templates, mailer, roles
from ..services.tabs import tenant_tabs, tenant_tab_descriptors, effective_tabs
from ..services.users import INVITE_DAYS, RESET_HOURS, link_base
from ..tenancy import current_tenant_id, tenant_app_url

router = APIRouter(tags=["auth"])

LOCK_THRESHOLD = 10
LOCK_MINUTES = 15

# Minimum gap between self-service reset emails for one account. Anybody who knows an address
# can ask for a reset on it — that is what makes the feature work at all — so without this
# the endpoint is a button for flooding somebody's inbox. A minute still lets a real person
# who did not receive the first one press Resend and get a second.
RESET_THROTTLE_SECONDS = 60


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _aware(d):
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


async def _tenant_apps(s: AsyncSession, tenant: Tenant) -> list[dict]:
    """The apps this workspace can open.

    ENTITLEMENT IS NOT ENOUGH; the portal has to actually EXIST. Those are two different
    questions and collapsing them caused a real problem: moving the portal from a per-workspace
    flag to a plan feature meant every workspace on a plan that includes it would have been shown
    an "Intranet" link overnight -- leading to a portal with no roles, no tiles and no content,
    and a console that 403s because nobody is on its roster. A new app appearing and not working
    reads as a bug, not as an upsell.

    So the link appears once the workspace has been bootstrapped, which provisioning now does.
    Existing workspaces that predate that see no change until somebody sets theirs up.
    """
    apps = [{"id": "dashboard", "name": "Dashboard", "href": "/"}]
    if plans.allows(tenant, "intranet"):
        exists = (await s.execute(select(IntranetWorkspace.tenant_id).where(
            IntranetWorkspace.tenant_id == tenant.id).limit(1))).first()
        if exists:
            apps.append({"id": "intranet", "name": "Intranet", "href": "/intranet/"})
    return apps


@router.get("/public/brand")
async def public_brand(s: AsyncSession = Depends(get_session)):
    """The workspace's identity, BEFORE anyone signs in. No auth, by necessity.

    The login screen has no session, so it cannot ask /me who it belongs to — which is why it
    used to render one customer's photograph and wordmark to everybody who ever reached it. This
    resolves the workspace from the host the browser is already sending and hands back only the
    chrome: name, marks, colours, type.

    A HOST THAT RESOLVES TO NOTHING GETS THE PLATFORM'S OWN IDENTITY, not a 404. That is
    deliberate on two counts. It is the honest answer — before you are signed in you are at
    Acumyn, not inside a workspace — and it means this endpoint cannot be used to ask "does a
    workspace exist at this address", which a 404 would answer for anyone who cared to iterate.

    Nothing here is private: it is the same branding painted on the page a moment later.
    """
    tid = None
    try:
        tid = current_tenant_id()
    except Exception:                       # unresolved host: fall through to the platform
        tid = None
    tenant = await s.get(Tenant, tid) if tid else None
    if tenant is not None and tenant.status != "active":
        tenant = None                       # a suspended workspace shows nothing of itself
    return roles.brand(tenant) if tenant is not None else roles.platform_brand()


@router.get("/public/brand/{slug}/{kind}")
async def public_brand_asset(slug: str, kind: str, s: AsyncSession = Depends(get_session)):
    """Stream a workspace's mark. Public, and the workspace is named in the PATH.

    It used to resolve the tenant from X-Tenant-Host, like every other route. That cannot work
    here and the reason is worth writing down: this URL is fetched by the browser as an image —
    a CSS mask, an <img> — and those requests carry no custom headers. So the API saw only
    `Host: api.<platform>`, which is a platform host that deliberately resolves to no tenant, and
    answered 404 every time. Verifying it with a curl that DID send the header made it look fine.

    The slug in the path is not a disclosure: it is already the subdomain the browser typed to
    get here. What matters is that the caller still cannot name the OBJECT — the storage ref is
    read out of that workspace's own config, so this cannot be pointed at anything else.
    """
    if kind not in ("logo", "logomark"):
        raise HTTPException(404, "Not found")
    tenant = (await s.execute(select(Tenant).where(Tenant.slug == slug.lower()))).scalar_one_or_none()
    ref = ((tenant.config or {}).get("brand") or {}).get(f"{kind}_ref") if tenant else None
    if not ref or not binder_storage.exists(ref):
        raise HTTPException(404, "Not found")
    data = binder_storage.read(ref)
    media = {"png": "image/png", "svg": "image/svg+xml", "jpg": "image/jpeg",
             "webp": "image/webp"}.get(ref.rsplit(".", 1)[-1].lower(), "application/octet-stream")
    return Response(content=data, media_type=media,
                    headers={"Cache-Control": "public, max-age=31536000, immutable",
                             # Fetched cross-origin from every workspace subdomain.
                             "Access-Control-Allow-Origin": "*"})


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, s: AsyncSession = Depends(get_session)):
    tid = current_tenant_id()
    # Refuse before touching credentials: a suspended workspace should not be probeable for
    # which passwords are right, and the lockout counter should not tick for people who could
    # not sign in either way.
    tenant = (await s.execute(select(Tenant).where(Tenant.id == tid))).scalar_one_or_none()
    if tenant is not None and tenant.status == "suspended":
        audit(s, tid, None, "auth.login_blocked", "tenant", tid, {"reason": "suspended"})
        await s.commit()
        raise HTTPException(403, "This workspace is suspended. Contact your administrator.")
    user = (await s.execute(
        select(User).where(User.tenant_id == tid, User.email == body.email.lower())
    )).scalar_one_or_none()
    # Neutral error for missing user / invited (no password yet) / disabled — no enumeration.
    # Every branch below writes an audit row: a password spray leaves no other trace, and
    # `last_login_at` alone cannot tell a successful compromise from ordinary use. The email
    # is recorded (not the password) because for an unknown address there is no user id to
    # attribute the attempt to — which is exactly the case worth seeing.
    if not user or not user.password_hash or user.status != "active":
        audit(s, tid, None, "auth.login_failed", "user", None,
              {"email": body.email.lower()[:160], "reason": "no_such_login"})
        await s.commit()
        raise HTTPException(401, "Invalid email or password")
    if user.locked_until and _aware(user.locked_until) > _now():
        audit(s, tid, user.id, "auth.login_blocked", "user", user.id, {"reason": "locked"})
        await s.commit()
        raise HTTPException(423, "Account temporarily locked. Try again shortly.")
    if not verify_pw(body.password, user.password_hash):
        user.failed_logins = (user.failed_logins or 0) + 1
        locked = user.failed_logins >= LOCK_THRESHOLD
        if locked:
            user.locked_until = _now() + dt.timedelta(minutes=LOCK_MINUTES)
        audit(s, tid, user.id, "auth.login_failed", "user", user.id,
              {"reason": "bad_password", "failed_logins": user.failed_logins, "locked": locked})
        await s.commit()
        raise HTTPException(401, "Invalid email or password")
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = _now()
    audit(s, tid, user.id, "auth.login", "user", user.id)
    await s.commit()
    return LoginResponse(token=make_token(user.id, user.tenant_id, user.token_version or 0,
                                          remember=body.remember))


@router.post("/auth/logout")
async def logout():
    # JWTs are stateless; the client clearing its token is the logout. Hard revocation
    # is via token_version (bumped on disable / password change), checked in current_user.
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
async def me(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    tenant = (await s.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one()
    descriptors = await tenant_tab_descriptors(s, user.tenant_id)
    tabs = effective_tabs(user, [d["key"] for d in descriptors], tenant=tenant)
    granted = set(tabs)
    return MeResponse(id=str(user.id), email=user.email, name=user.name, role=user.role,
                      status=user.status, tenant=tenant.slug, tenant_name=tenant.name,
                      tabs=tabs, brand=roles.brand(tenant),
                      apps=await _tenant_apps(s, tenant),
                      # Filtered to what this user may see, so the rail cannot render a tab
                      # the API would refuse — the nav and the grant come from one source.
                      nav=[d for d in descriptors if d["key"] in granted])


@router.post("/auth/change-password", response_model=LoginResponse)
async def change_password(body: ChangePasswordRequest, user: User = Depends(current_user),
                          s: AsyncSession = Depends(get_session)):
    if not user.password_hash or not verify_pw(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    if len(body.new_password) < MIN_PASSWORD_LEN:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LEN} characters")
    user.password_hash = hash_pw(body.new_password)
    user.token_version = (user.token_version or 0) + 1     # kill other sessions
    audit(s, user.tenant_id, user.id, "auth.password_changed", "user", user.id)
    await s.commit()
    return LoginResponse(token=make_token(user.id, user.tenant_id, user.token_version))


async def _consume_action_token(s, tid, token: str, purpose: str) -> User:
    """Resolve an invite/reset token within the current tenant, or 400."""
    th = hash_action_token(token)
    u = (await s.execute(select(User).where(
        User.tenant_id == tid, User.action_token_hash == th,
        User.action_token_purpose == purpose))).scalar_one_or_none()
    if not u or not u.action_token_expires or _aware(u.action_token_expires) < _now():
        raise HTTPException(400, "This link is invalid or expired. Ask your admin to resend it.")
    return u


# The link purposes that may be read back. Enumerated rather than passed through, so this
# cannot be aimed at some later token purpose that was never meant to be legible.
LINK_PURPOSES = ("invite", "reset")


# ── Google sign-in ────────────────────────────────────────────────────────────────────────
# Three endpoints, all public: whoever is using them has no session yet -- that is the point.
# The tenant therefore comes from the Host on the way out and from the signed `state` on the way
# back, because the callback arrives from Google with no Host that identifies the workspace.


@router.get("/auth/google/config")
async def google_config(s: AsyncSession = Depends(get_session)):
    """Whether this workspace offers Google sign-in, for the login page to decide on a button.

    Deliberately returns one boolean. The client id is not secret -- it travels in the authorize
    URL -- but an unauthenticated endpoint that hands out a workspace's configuration invites
    exactly the kind of enumeration there is no reason to allow. The login page needs to know
    whether to draw a button, and nothing else.
    """
    cfg = await google_auth.workspace_config(s, current_tenant_id())
    return {"enabled": bool(cfg and cfg["enabled"])}


@router.get("/auth/google/start")
async def google_start(request: Request, s: AsyncSession = Depends(get_session)):
    tid = current_tenant_id()
    cfg = await google_auth.workspace_config(s, tid)
    if not cfg or not cfg["enabled"]:
        raise HTTPException(404, "This workspace does not use Google sign-in.")
    # A CAPABILITY, not a session token -- the same trap QuickBooks fell into and the reason
    # make_capability exists. This string is handed to Google, sits in their logs and comes back
    # as a URL query parameter, so it must not be usable as a credential here. read_token
    # rejects it because it carries a purpose claim.
    state = make_capability("google_signin", minutes=15, tid=str(tid))
    audit(s, tid, None, "auth.google_start", "tenant", tid)
    await s.commit()
    return {"url": google_auth.authorize_url(
        cfg["client_id"], settings.GOOGLE_REDIRECT_URI, state, cfg["allowed_domains"])}


@router.get("/auth/google/callback")
async def google_callback(code: str = Query(None), state: str = Query(None),
                          error: str = Query(None), s: AsyncSession = Depends(get_session)):
    """Where Google returns. Ends in a redirect, never a JSON error.

    Whoever lands here is a person in a browser who clicked "Sign in with Google", so every
    failure has to become a page they can read. The reason travels as a short code in the query
    string rather than a message: this URL is shared infrastructure and the specifics -- which
    address was refused, whether an account exists -- are exactly what must not be echoed back.
    """
    async def fail(reason: str, tenant_id=None) -> RedirectResponse:
        base = await tenant_app_url(s, tenant_id) if tenant_id else settings.APP_PUBLIC_URL
        return RedirectResponse(f"{base}/?google_error={reason}")

    if error or not code or not state:
        return await fail("cancelled")
    try:
        tid = uuid.UUID(read_capability(state, "google_signin")["tid"])
    except Exception:                                            # noqa: BLE001
        return await fail("expired")

    tenant = (await s.execute(select(Tenant).where(Tenant.id == tid))).scalar_one_or_none()
    if tenant is None or tenant.status == "suspended":
        return await fail("unavailable", tid)
    cfg = await google_auth.workspace_config(s, tid)
    if not cfg or not cfg["enabled"]:
        return await fail("unavailable", tid)

    try:
        tokens = await google_auth.exchange_code(
            cfg["client_id"], cfg["client_secret"], code, settings.GOOGLE_REDIRECT_URI)
        claims = google_auth.read_id_token(tokens.get("id_token") or "", cfg["client_id"])
    except google_auth.GoogleAuthError as exc:
        audit(s, tid, None, "auth.google_failed", "tenant", tid, {"reason": str(exc)[:300]})
        await s.commit()
        return await fail("google", tid)

    email = (claims.get("email") or "").lower()
    if not google_auth.domain_allowed(claims, cfg["allowed_domains"]):
        audit(s, tid, None, "auth.google_denied", "tenant", tid,
              {"reason": "domain", "email": email[:160]})
        await s.commit()
        return await fail("domain", tid)

    user = (await s.execute(select(User).where(
        User.tenant_id == tid, User.email == email))).scalar_one_or_none()
    # MATCHED, NEVER CREATED. No row means nobody invited this person to this workspace, and a
    # verified address at an allowed domain is not an invitation -- see services/google_auth.
    if user is None or user.status == "disabled":
        audit(s, tid, None, "auth.google_denied", "tenant", tid,
              {"reason": "no_account" if user is None else "disabled", "email": email[:160]})
        await s.commit()
        return await fail("no_account", tid)

    if user.status == "invited":
        # Proving control of the invited address IS accepting the invite. Requiring the emailed
        # link as well would mean an agent who has just authenticated with the very account the
        # invite was sent to gets told to go and find an email. The link is burned either way,
        # so it cannot be replayed afterwards.
        user.status = "active"
        user.name = user.name or (claims.get("name") or email.split("@")[0])[:200]
        user.action_token_hash = user.action_token_purpose = user.action_token_expires = None
        audit(s, tid, user.id, "user.accepted_invite", "user", user.id, {"via": "google"})

    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = _now()
    audit(s, tid, user.id, "auth.login", "user", user.id, {"via": "google"})
    await s.commit()
    token = make_token(user.id, user.tenant_id, user.token_version or 0, remember=True)
    # The session travels in the fragment, not the query string: a fragment is never sent to a
    # server and never lands in an access log or a Referer header. The app reads it on load and
    # replaces the URL.
    return RedirectResponse(f"{await tenant_app_url(s, tid)}/#google_token={token}")


@router.post("/auth/link-info", response_model=ActionLinkInfo)
async def link_info(body: ActionLinkRequest, s: AsyncSession = Depends(get_session)):
    """Which account a one-time link is for, WITHOUT spending it.

    The invite screen asked for a name and a password and never said which email address the
    invite was issued to; the reset screen was the same. Two things broke because of that. The
    loud one: somebody with more than one address chooses a password, comes back the next day,
    and cannot work out which address to type -- and login answers "Invalid email or password"
    for every guess, correctly, because it must not confirm which addresses exist. The quiet
    one: a password form with no username field gives the browser's password manager nothing to
    file the credential under, so autofill has nothing to offer either. Nothing was wrong with
    the account. The only copy of the address was in an email nobody re-reads.

    This is not a disclosure. Whoever holds the token can already set the password and sign in
    as that account, so naming the address grants nothing they did not have -- and it is what
    the invite email said in the first place. POST rather than GET keeps the secret out of
    access logs and Referer headers, unlike the page URL it was read from.
    """
    if body.purpose not in LINK_PURPOSES:
        raise HTTPException(400, "This link is invalid or expired. Ask your admin to resend it.")
    tid = current_tenant_id()
    # Resolves and validates expiry; despite the name it does not spend the token -- its callers
    # below do that. Reusing it means an expired link answers here exactly as it would on submit.
    u = await _consume_action_token(s, tid, body.token, body.purpose)
    tenant = await s.get(Tenant, tid)
    return ActionLinkInfo(email=u.email, name=u.name or None,
                          workspace=(tenant.name if tenant is not None else None))


@router.post("/auth/accept-invite", response_model=LoginResponse)
async def accept_invite(body: AcceptInviteRequest, s: AsyncSession = Depends(get_session)):
    tid = current_tenant_id()
    u = await _consume_action_token(s, tid, body.token, "invite")
    if u.status != "invited":
        raise HTTPException(400, "This invite has already been used.")
    if len(body.password) < MIN_PASSWORD_LEN:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LEN} characters")
    u.name = body.name.strip()[:200] or u.name
    u.password_hash = hash_pw(body.password)
    u.status = "active"
    u.action_token_hash = u.action_token_purpose = u.action_token_expires = None
    u.token_version = (u.token_version or 0)
    audit(s, tid, u.id, "user.accepted_invite", "user", u.id)
    await s.commit()
    # Remembered, with no checkbox to ask: somebody who has just chosen a password on this
    # machine has said as plainly as they can that it is theirs, and signing them out twelve
    # hours into their first day would read as the account not working.
    return LoginResponse(token=make_token(u.id, u.tenant_id, u.token_version or 0,
                                          remember=True))


@router.post("/auth/reset-password", response_model=LoginResponse)
async def reset_password(body: ResetPasswordRequest, s: AsyncSession = Depends(get_session)):
    tid = current_tenant_id()
    u = await _consume_action_token(s, tid, body.token, "reset")
    # A disabled account must never be resurrected by a link that predates the disable.
    # `u.status = "active"` below exists for the ordinary case (a reset completes an account
    # that was mid-invite); without this guard it also silently undid an admin's emergency
    # disable and handed the link holder a live session. accept_invite has always had the
    # equivalent guard; reset never did. The token is already consumed above, so a stale
    # link is spent either way.
    if u.status == "disabled":
        raise HTTPException(400, "This account is disabled. Ask an administrator.")
    if len(body.new_password) < MIN_PASSWORD_LEN:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD_LEN} characters")
    u.password_hash = hash_pw(body.new_password)
    u.status = "active"
    u.action_token_hash = u.action_token_purpose = u.action_token_expires = None
    u.token_version = (u.token_version or 0) + 1            # kill old sessions
    u.failed_logins = 0
    u.locked_until = None
    audit(s, tid, u.id, "auth.password_reset", "user", u.id)
    await s.commit()
    return LoginResponse(token=make_token(u.id, u.tenant_id, u.token_version, remember=True))


@router.post("/auth/forgot-password")
async def forgot_password(body: ForgotPasswordRequest, request: Request, bg: BackgroundTasks,
                          s: AsyncSession = Depends(get_session)):
    """Send a reset link to an address, if it belongs to somebody who can use one.

    THE RESPONSE NEVER VARIES. Not on whether the address exists, not on whether that account is
    active, disabled or still invited, not on whether an email was actually sent. A sign-in page
    is reachable by anyone, so a form that answers differently for a real address is a way to
    read off the customer list one guess at a time — and the addresses here are work emails at a
    named brokerage, which makes the list worth having. The 200 below is the entire API surface.

    What varies is what happens behind it:

      active         a reset link, which is the ordinary case.
      invited        their INVITE is resent instead. They have no password to reset, and the
                     honest reading of "I can't get in" from somebody who never finished signing
                     up is that they lost the first email. Sending a reset link would work — it
                     sets a password — but it would skip the name they are asked for on the
                     invite screen, and it would mean two different links doing one job.
      disabled       nothing. An administrator turned this account off; a form on the public
                     internet must not be able to hand it a way back in.
      suspended      nothing, for the whole workspace. Same reasoning as login.
      no such user   nothing.

    Both branches write an audit row before returning, so the work done inside the request is
    comparable whether or not the address was real, and so a spray against this endpoint leaves
    a trace. `last_login_at` cannot tell you it happened.
    """
    tid = current_tenant_id()
    email = (body.email or "").strip().lower()[:320]
    ok = {"ok": True}

    tenant = (await s.execute(select(Tenant).where(Tenant.id == tid))).scalar_one_or_none()
    if tenant is not None and tenant.status == "suspended":
        audit(s, tid, None, "auth.forgot_password", "tenant", tid, {"result": "suspended"})
        await s.commit()
        return ok

    user = (await s.execute(select(User).where(
        User.tenant_id == tid, User.email == email))).scalar_one_or_none()
    if user is None or user.status == "disabled":
        audit(s, tid, None, "auth.forgot_password", "user", None,
              {"email": email[:160], "result": "no_such_login"})
        await s.commit()
        return ok

    # Throttle on the account, not the caller: the address is what gets flooded, and it is the
    # one thing an attacker cannot vary. Issue time is the expiry minus the lifetime, so this
    # needs no extra column.
    window = dt.timedelta(hours=RESET_HOURS if user.status == "active" else INVITE_DAYS * 24)
    expires = _aware(user.action_token_expires)
    if expires is not None and (expires - window) > _now() - dt.timedelta(
            seconds=RESET_THROTTLE_SECONDS):
        audit(s, tid, user.id, "auth.forgot_password", "user", user.id, {"result": "throttled"})
        await s.commit()
        return ok

    raw, token_hash = new_action_token()
    user.action_token_hash = token_hash
    invited = user.status == "invited"
    user.action_token_purpose = "invite" if invited else "reset"
    user.action_token_expires = _now() + (dt.timedelta(days=INVITE_DAYS) if invited
                                          else dt.timedelta(hours=RESET_HOURS))
    base = await link_base(request, s, tid)
    workspace = (tenant.name if tenant else None) or "your workspace"
    if invited:
        url = f"{base}/accept-invite?token={raw}"
        template = mail_templates.invite(url, None, workspace, INVITE_DAYS)
    else:
        url = f"{base}/reset-password?token={raw}"
        template = mail_templates.reset(url, workspace, RESET_HOURS)
    audit(s, tid, user.id, "auth.forgot_password", "user", user.id,
          {"result": "invite_resent" if invited else "sent"})
    await s.commit()
    # After the commit and in the background, for the reason mailer's docstring gives: the token
    # is already saved, so an outage that propagated would report failure for a link that works.
    bg.add_task(mailer.send, user.email, *template)
    return ok
