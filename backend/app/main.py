from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import engine
from .models import Base
from .tenancy import resolve_tenant
from .routers import auth, dashboard, businesses, integrations, users


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev convenience: auto-create tables on SQLite so the app runs with zero
    # infra. In prod (Postgres) schema is managed by Alembic migrations.
    if settings.is_sqlite:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Spring Command Center API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.origins,
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    )

    @app.middleware("http")
    async def tenant_mw(request: Request, call_next):
        # OAuth callback carries tenant in `state`, not Host; skip global resolve there.
        if not request.url.path.startswith("/api/v1/integrations/qbo/callback"):
            try:
                await resolve_tenant(request)
            except Exception:
                pass  # routers that need a tenant enforce it via deps
        return await call_next(request)

    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(dashboard.router, prefix="/api/v1")
    app.include_router(businesses.router, prefix="/api/v1")
    app.include_router(integrations.router, prefix="/api/v1")
    app.include_router(users.router, prefix="/api/v1")

    @app.get("/health")
    async def health():
        return {"ok": True}

    return app


app = create_app()
