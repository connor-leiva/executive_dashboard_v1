import datetime as dt
import uuid

import bcrypt
from jose import jwt
from cryptography.fernet import Fernet

from .config import settings

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


def make_token(user_id: uuid.UUID, tenant_id: uuid.UUID) -> str:
    payload = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "exp": dt.datetime.utcnow() + dt.timedelta(days=7),
    }
    return jwt.encode(payload, settings.APP_SECRET, algorithm=ALGO)


def read_token(token: str) -> dict:
    return jwt.decode(token, settings.APP_SECRET, algorithms=[ALGO])


def enc(s: str) -> str:
    return _fernet.encrypt(s.encode()).decode()


def dec(s: str) -> str:
    return _fernet.decrypt(s.encode()).decode()
