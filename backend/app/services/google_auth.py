"""Google sign-in for a team portal, with each workspace using its OWN Google app.

WHY THE CREDENTIALS ARE NOT IN ENV, unlike QuickBooks. QBO reads one client id and secret from
settings, which is the single-tenant shape: every workspace would authenticate through Acumyn's
Google project, Acumyn would appear on every team's consent screen, and one revoked app would
sign out every customer at once. A team that buys this registers their own OAuth client in their
own Google Cloud project and pastes it into their own console. The consent screen then says
their name, because it is their app.

WHAT IS STILL SHARED, deliberately: the redirect URI. The API runs on one host, so the callback
is one URL that every workspace registers in their own Google project, and the tenant travels in
the signed `state` exactly as it does for QuickBooks -- the callback arrives with no Host header
that could identify the workspace.

MATCHING, NEVER PROVISIONING. A successful Google sign-in does not create anybody. It finds a
user somebody already invited to this workspace, or it fails. Auto-provisioning from a verified
domain sounds equivalent and is not: it would let anyone with an address at that domain into a
workspace they were never added to, and "we restrict by domain" is a policy the *domain's* owner
controls, not one we can see changes to.
"""
from __future__ import annotations

import datetime as dt
from urllib.parse import urlencode

import httpx
from jose import jwt

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "openid email profile"
ISSUERS = ("accounts.google.com", "https://accounts.google.com")


class GoogleAuthError(Exception):
    """Anything that should end as 'we could not sign you in', with a reason for the log."""


def authorize_url(client_id: str, redirect_uri: str, state: str,
                  allowed_domains: list[str] | None = None) -> str:
    params = {
        "client_id": client_id,
        "response_type": "code",
        "scope": SCOPE,
        "redirect_uri": redirect_uri,
        "state": state,
        # Google will not return an account outside this domain. A UI hint, not a control --
        # `hd` is checked again on the claims below, because a parameter in a URL the user can
        # edit is a suggestion.
        **({"hd": allowed_domains[0]} if allowed_domains and len(allowed_domains) == 1 else {}),
        # Always show the chooser: a shared machine that silently reuses the last Google session
        # is how one agent ends up signed in as another.
        "prompt": "select_account",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(TOKEN_URL, data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        }, headers={"Accept": "application/json"})
    if r.status_code >= 400:
        # Google's body names the actual fault (redirect_uri_mismatch, invalid_client). Kept for
        # the log because "sign-in failed" is unactionable for whoever set the app up.
        raise GoogleAuthError(f"token exchange failed: {r.status_code} {r.text[:300]}")
    return r.json()


def read_id_token(id_token: str, client_id: str) -> dict:
    """The verified claims of an ID token THIS SERVER just received from Google's token endpoint.

    The signature is not re-checked, and that is a decision rather than an omission. Provenance
    here comes from TLS: we posted our own client secret to oauth2.googleapis.com and read the
    token off that authenticated response, so a forged token would require forging Google's
    certificate -- at which point checking a signature against keys fetched over the same TLS
    proves nothing. Google documents this exact case as not needing local verification.

    The condition that makes it safe is narrow: the token must have come from our own
    `exchange_code` call. An ID token that arrived any other way -- posted by a browser, passed
    in a query string -- has no such provenance and must never reach this function. There is
    deliberately no endpoint that accepts one.

    Everything that does NOT follow from transport is still checked here, because TLS says the
    token is Google's and says nothing about who it is for.
    """
    try:
        claims = jwt.get_unverified_claims(id_token)
    except Exception as exc:                                    # noqa: BLE001
        raise GoogleAuthError(f"unreadable id_token: {exc}") from exc

    if claims.get("aud") != client_id:
        # Without this, a token minted for a DIFFERENT Google app would be accepted -- the
        # classic confused-deputy in OAuth sign-in.
        raise GoogleAuthError("id_token audience is not this workspace's client id")
    if claims.get("iss") not in ISSUERS:
        raise GoogleAuthError(f"unexpected issuer {claims.get('iss')!r}")
    exp = claims.get("exp")
    if not exp or dt.datetime.now(dt.timezone.utc).timestamp() > float(exp):
        raise GoogleAuthError("id_token has expired")
    if not claims.get("email"):
        raise GoogleAuthError("id_token carries no email")
    # An unverified address is one somebody typed, not one they proved they hold. Matching on it
    # would let a Google account claim any invited address.
    if claims.get("email_verified") not in (True, "true"):
        raise GoogleAuthError("google has not verified that address")
    return claims


def domain_allowed(claims: dict, allowed_domains: list[str]) -> bool:
    """Whether these claims satisfy the workspace's domain restriction.

    Empty list means no restriction. `hd` is preferred where Google supplies it (it is the
    Workspace domain Google itself asserts); otherwise the address's own domain is used, which
    is what a workspace using ordinary gmail accounts will have.
    """
    if not allowed_domains:
        return True
    wanted = {d.strip().lower().lstrip("@") for d in allowed_domains if d and d.strip()}
    if not wanted:
        return True
    hd = (claims.get("hd") or "").strip().lower()
    email_domain = (claims.get("email") or "").rsplit("@", 1)[-1].strip().lower()
    return hd in wanted or email_domain in wanted


# ── where a workspace's own Google app lives ──────────────────────────────────────────────
# The portal's integration row for this provider. `config` holds the non-secret half; the
# client secret is Fernet-encrypted into `credential_ref`, which is the column that has existed
# for exactly this since the table was created and had never been written to.
PROVIDER_KEY = "google_workspace"


async def workspace_config(s, tenant_id) -> dict | None:
    """This workspace's Google sign-in settings, or None if it has not set one up.

    Returns the DECRYPTED secret, so callers must not hand the result to a serializer. The two
    public-facing readers (`/auth/google/config` and the console) each pick their own fields.
    """
    from sqlalchemy import select

    from ..models import IntranetIntegration
    from ..security import dec

    row = (await s.execute(select(IntranetIntegration).where(
        IntranetIntegration.tenant_id == tenant_id,
        IntranetIntegration.provider_key == PROVIDER_KEY))).scalar_one_or_none()
    if row is None:
        return None
    cfg = row.config or {}
    client_id = (cfg.get("client_id") or "").strip()
    if not client_id or not row.credential_ref:
        return None
    try:
        secret = dec(row.credential_ref)
    except Exception:                                            # noqa: BLE001
        # A secret encrypted under a FERNET_KEY that has since been rotated. Reported as
        # "not configured" rather than crashing the sign-in page for everybody.
        return None
    domains = cfg.get("allowed_domains") or []
    return {
        "row": row,
        "client_id": client_id,
        "client_secret": secret,
        "allowed_domains": [str(d) for d in domains if str(d).strip()],
        "enabled": bool(cfg.get("enabled", True)),
    }
