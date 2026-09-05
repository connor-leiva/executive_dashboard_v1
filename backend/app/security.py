import datetime as dt
import hashlib
import secrets
import uuid

import bcrypt
from jose import jwt
from cryptography.fernet import Fernet

from .config import settings

MIN_PASSWORD_LEN = 10

_fernet = Fernet(settings.FERNET_KEY.encode())
ALGO = "HS256"


def hash_pw(p: str) -> str:
    # bcrypt operates on the first 72 bytes; truncate explicitly to avoid errors.
    return bcrypt.hashpw(p.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_pw(p: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(p.encode("utf-8")[:72], h.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# How long a session lasts, and the whole reason "Remember me" is not decoration. Unchecked,
# a session should not outlive the working day on a machine somebody might share; checked, it
# should not ask again for a month. The browser matches this: an unremembered token goes in
# sessionStorage and dies with the tab, so a short expiry here and a short life there are two
# halves of one promise rather than a server-side detail nobody sees.
SESSION_HOURS = 12
SESSION_REMEMBERED_DAYS = 30


def make_token(user_id: uuid.UUID, tenant_id: uuid.UUID, ver: int = 0,
               remember: bool = False) -> str:
    payload = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "ver": ver,                    # token_version — bumping it revokes outstanding tokens
        "exp": dt.datetime.utcnow() + (dt.timedelta(days=SESSION_REMEMBERED_DAYS) if remember
                                       else dt.timedelta(hours=SESSION_HOURS)),
    }
    return jwt.encode(payload, settings.APP_SECRET, algorithm=ALGO)


def make_platform_token(platform_user_id: uuid.UUID, ver: int = 0) -> str:
    """A PLATFORM operator's session. Deliberately a different shape from a tenant session.

    It carries `pu` and NO `tid`, which is what makes the two realms mutually unusable rather
    than merely separated by a check somebody has to remember: deps.current_user compares
    `payload.get("tid")` to the resolved tenant, so this token fails that comparison against
    every tenant; and require_platform_admin requires `pu`, which no tenant session has.
    """
    payload = {
        "pu": str(platform_user_id),
        "ver": ver,
        "exp": dt.datetime.utcnow() + dt.timedelta(days=1),   # shorter than a tenant session
    }
    return jwt.encode(payload, settings.APP_SECRET, algorithm=ALGO)


def read_token(token: str) -> dict:
    return jwt.decode(token, settings.APP_SECRET, algorithms=[ALGO])


def make_capability(purpose: str, minutes: int = 90, **claims) -> str:
    """A short-lived, signed capability token for a narrow one-shot action (e.g. a Cowork task
    posting an audit result back). Carries its own purpose + scope claims; not a login token."""
    body = {"cap": purpose, **claims,
            "exp": dt.datetime.utcnow() + dt.timedelta(minutes=minutes)}
    return jwt.encode(body, settings.APP_SECRET, algorithm=ALGO)


def read_capability(token: str, purpose: str) -> dict:
    """Decode + verify a capability token, asserting its purpose. Raises on bad sig/expiry/purpose."""
    data = jwt.decode(token, settings.APP_SECRET, algorithms=[ALGO])
    if data.get("cap") != purpose:
        raise ValueError("wrong capability purpose")
    return data


def new_action_token() -> tuple[str, str]:
    """(raw urlsafe token returned once, sha256 hash stored). For invites + resets."""
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def hash_action_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ── TOTP second factor (RFC 6238) — used for the Binder step-up unlock ────────────────────
TOTP_ISSUER = "Acumyn"
RECOVERY_CODE_COUNT = 10


def new_totp_secret() -> str:
    """A fresh base32 secret. Store it ENCRYPTED (enc()); never log or return it twice."""
    import pyotp
    return pyotp.random_base32()


def totp_uri(secret: str, email: str) -> str:
    """otpauth:// provisioning URI — the client renders the QR, so no server-side QR dep."""
    import pyotp
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=TOTP_ISSUER)


def verify_totp(secret: str, code: str) -> bool:
    """Check a 6-digit code, allowing ±1 step (30s) for clock drift."""
    import pyotp
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit():
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def new_recovery_codes(n: int = RECOVERY_CODE_COUNT) -> tuple[list[str], list[str]]:
    """(codes shown to the user ONCE, sha256 hashes to store). Without these, a lost phone
    means nobody can open the section again without a database edit."""
    raw = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(n)]
    return raw, [recovery_hash(c) for c in raw]


def recovery_hash(code: str) -> str:
    """Normalize before hashing so a user can retype a code with stray case/spaces."""
    return hashlib.sha256((code or "").strip().lower().encode()).hexdigest()


def enc(s: str) -> str:
    return _fernet.encrypt(s.encode()).decode()


def dec(s: str) -> str:
    return _fernet.decrypt(s.encode()).decode()
