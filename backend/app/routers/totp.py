"""TOTP second factor + the step-up unlock for sensitive sections (Binder).

Two separate things live here:
  • ENROLLMENT — a user binds an authenticator app to their account (once).
      POST /me/totp/start    -> secret + otpauth URI (client renders the QR)
      POST /me/totp/confirm  -> verifies the first code, activates it, returns recovery codes ONCE
      POST   /me/totp/disable-> turns it off (password re-entry required)
  • STEP-UP  — proving it again to open a locked section, for a short window.
      POST /step-up/{scope}  -> verifies a code (or a recovery code) -> short-lived grant

The grant is the existing capability token (purpose "stepup:<scope>"), bound to the user AND
their token_version, so disabling the account or changing the password kills outstanding
unlocks too. The secret is Fernet-encrypted at rest and never returned after enrollment.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import current_user, STEP_UP_SCOPES
from ..models import User
from ..security import (dec, enc, hash_action_token, make_capability, new_recovery_codes,
                        new_totp_secret, recovery_hash, totp_uri, verify_pw, verify_totp)
from ..services.audit import audit

router = APIRouter(tags=["totp"])

TOTP_LOCK_THRESHOLD = 5                  # wrong codes before a cooldown
TOTP_LOCK_MINUTES = 15


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _aware(d):
    return None if d is None else (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc))


def totp_active(user: User) -> bool:
    return bool(user.totp_secret_enc and user.totp_confirmed_at)


@router.get("/me/totp")
async def totp_status(user: User = Depends(current_user)):
    """Whether this account has a live authenticator, and how many recovery codes remain."""
    return {"enabled": totp_active(user),
            "pending": bool(user.totp_secret_enc and not user.totp_confirmed_at),
            "recovery_remaining": len(user.totp_recovery or []),
            "step_up_scopes": sorted(STEP_UP_SCOPES)}


@router.post("/me/totp/start")
async def totp_start(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Begin enrollment: mint a secret and return its provisioning URI. Not active until
    confirmed, so an abandoned start can never unlock anything."""
    if totp_active(user):
        raise HTTPException(400, "Authenticator already set up — remove it first to re-enroll")
    secret = new_totp_secret()
    user.totp_secret_enc = enc(secret)
    user.totp_confirmed_at = None
    await s.commit()
    return {"secret": secret, "otpauth_uri": totp_uri(secret, user.email)}


@router.post("/me/totp/confirm")
async def totp_confirm(body: dict, user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session)):
    """Verify the first code, activate, and return the recovery codes — the ONLY time they
    are ever shown. Without them a lost phone locks the section until a DB edit."""
    if totp_active(user):
        raise HTTPException(400, "Authenticator already set up")
    if not user.totp_secret_enc:
        raise HTTPException(400, "Start enrollment first")
    if not verify_totp(dec(user.totp_secret_enc), str(body.get("code", ""))):
        raise HTTPException(400, "That code didn't match — check the time on your phone")
    raw, hashes = new_recovery_codes()
    user.totp_confirmed_at = _now()
    user.totp_recovery = hashes
    user.totp_failed = 0
    user.totp_locked_until = None
    audit(s, user.tenant_id, user.id, "totp.enabled", "user", user.id, {})
    await s.commit()
    return {"enabled": True, "recovery_codes": raw}


@router.post("/me/totp/disable")
async def totp_disable(body: dict, user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session)):
    """Turn the second factor off. Requires the account password so a hijacked open session
    can't quietly strip it."""
    if not user.password_hash or not verify_pw(str(body.get("password", "")), user.password_hash):
        raise HTTPException(400, "Password is incorrect")
    user.totp_secret_enc = None
    user.totp_confirmed_at = None
    user.totp_recovery = None
    user.totp_last_used = None
    user.totp_failed = 0
    user.totp_locked_until = None
    audit(s, user.tenant_id, user.id, "totp.disabled", "user", user.id, {})
    await s.commit()
    return {"enabled": False}


@router.post("/step-up/{scope}")
async def step_up(scope: str, body: dict, user: User = Depends(current_user),
                  s: AsyncSession = Depends(get_session)):
    """Prove the second factor to unlock a section for a short window. Accepts a TOTP code or
    one single-use recovery code. Throttled after repeated failures."""
    minutes = STEP_UP_SCOPES.get(scope)
    if minutes is None:
        raise HTTPException(404, "Unknown scope")
    if not totp_active(user):
        # 409: the user must enroll before the section will open for them.
        raise HTTPException(409, "Set up your authenticator app first")

    locked = _aware(user.totp_locked_until)
    if locked and locked > _now():
        raise HTTPException(429, "Too many attempts — try again in a few minutes")

    code = str(body.get("code", "")).strip()
    secret = dec(user.totp_secret_enc)
    used_recovery = False

    if verify_totp(secret, code):
        if user.totp_last_used == code:              # replay guard within the same 30s step
            raise HTTPException(400, "That code was already used — wait for the next one")
    else:
        h = recovery_hash(code)
        remaining = list(user.totp_recovery or [])
        if h in remaining:
            remaining.remove(h)                      # single use
            user.totp_recovery = remaining
            used_recovery = True
        else:
            user.totp_failed = (user.totp_failed or 0) + 1
            if user.totp_failed >= TOTP_LOCK_THRESHOLD:
                user.totp_locked_until = _now() + dt.timedelta(minutes=TOTP_LOCK_MINUTES)
                user.totp_failed = 0
            audit(s, user.tenant_id, user.id, "step_up.failed", "user", user.id, {"scope": scope})
            await s.commit()
            raise HTTPException(400, "Incorrect code")

    user.totp_failed = 0
    user.totp_locked_until = None
    if not used_recovery:
        user.totp_last_used = code
    audit(s, user.tenant_id, user.id, "step_up.granted", "user", user.id,
          {"scope": scope, "recovery": used_recovery})
    await s.commit()

    grant = make_capability(f"stepup:{scope}", minutes=minutes,
                            sub=str(user.id), ver=int(user.token_version or 0))
    return {"token": grant, "expires_in": minutes * 60, "scope": scope,
            "used_recovery": used_recovery,
            "recovery_remaining": len(user.totp_recovery or [])}
