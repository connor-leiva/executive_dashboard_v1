import uuid

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_session
from .security import read_token
from .models import User
from .tenancy import current_tenant_id

bearer = HTTPBearer()


async def current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    s: AsyncSession = Depends(get_session),
) -> User:
    try:
        payload = read_token(creds.credentials)
    except Exception:
        raise HTTPException(401, "Invalid token")
    tid = current_tenant_id()
    if payload.get("tid") != str(tid):
        raise HTTPException(403, "Token/tenant mismatch")
    user = (await s.execute(
        select(User).where(User.id == uuid.UUID(payload["sub"]), User.tenant_id == tid)
    )).scalar_one_or_none()
    if not user:
        raise HTTPException(401, "User not found")
    return user
