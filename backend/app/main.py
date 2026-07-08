import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from .db import engine
from .models import Base
from .tenancy import resolve_tenant
from .routers import auth, dashboard, businesses, integrations, users

log = logging.getLogger("app")


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

    # Inner middleware: resolve the tenant, then guarantee every response — even an
    # unhandled 500 — is a real HTTP response. Without this, an exception raised in a
    # handler bypasses the CORS middleware entirely, so the 500 comes back with NO
    # CORS headers; the browser then reports it as a CORS/network failure with no
    # status or body, and the frontend can only show a generic "try again". Catching
    # here (and logging the traceback) lets the OUTER CORS middleware decorate the
    # error so the real detail reaches the client and the actual cause hits the logs.
    @app.middleware("http")
    async def tenant_and_errors(request: Request, call_next):
        # OAuth callback carries tenant in `state`, not Host; skip global resolve there.
        if not request.url.path.startswith("/api/v1/integrations/qbo/callback"):
            try:
                await resolve_tenant(request)
            except Exception:
                pass  # routers that need a tenant enforce it via deps
        try:
            return await call_next(request)
        except Exception:
            log.exception("Unhandled error on %s %s", request.method, request.url.path)
            return JSONResponse({"detail": "Internal Server Error"}, status_code=500)

    # Added LAST so it is the OUTERMOST user middleware — it must wrap the handler
    # above so CORS headers land on error responses too (see the note above).
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.origins,
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    )

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
