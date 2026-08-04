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


def make_token(user_id: uuid.UUID, tenant_id: uuid.UUID, ver: int = 0) -> str:
    payload = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "ver": ver,                    # token_version — bumping it revokes outstanding tokens
        "exp": dt.datetime.utcnow() + dt.timedelta(days=7),
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


def enc(s: str) -> str:
    return _fernet.encrypt(s.encode()).decode()


def dec(s: str) -> str:
    return _fernet.decrypt(s.encode()).decode()
