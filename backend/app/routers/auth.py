from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import current_user
from ..models import User, Tenant
from ..schemas import LoginRequest, LoginResponse, MeResponse
from ..security import verify_pw, make_token
from ..tenancy import current_tenant_id

router = APIRouter(tags=["auth"])


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, s: AsyncSession = Depends(get_session)):
    tid = current_tenant_id()
    user = (await s.execute(
        select(User).where(User.tenant_id == tid, User.email == body.email.lower())
    )).scalar_one_or_none()
    if not user or not verify_pw(body.password, user.password_hash):
        raise HTTPException(401, "Invalid email or password")
    return LoginResponse(token=make_token(user.id, user.tenant_id))


@router.post("/auth/logout")
async def logout():
    # JWTs are stateless — the client clearing its token is the real logout.
    # TODO: add a denylist table + check in current_user for hard revocation.
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
async def me(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    tenant = (await s.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one()
    return MeResponse(
        id=str(user.id), email=user.email, name=user.name, role=user.role, tenant=tenant.slug
    )
