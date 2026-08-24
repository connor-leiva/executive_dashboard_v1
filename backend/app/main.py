import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from .db import engine
from .models import Base
from .tenancy import resolve_tenant
from .throttle import enforce
from .routers import recall as recall_router, auth, dashboard, businesses, integrations, users, assistant, books, binder, launches, ai_employees, ulrg, share, totp

log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev convenience: auto-create tables on SQLite so the app runs with zero
    # infra. In prod (Postgres) schema is managed by Alembic migrations.
    if settings.is_sqlite:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    # Single-service deployments (no separate `worker` service) run the scheduler here so the
    # sync tick + AI dispatch/execute jobs actually run. Off by default.
    sched = None
    if settings.RUN_WORKER_IN_API:
        from .worker import build_scheduler
        sched = build_scheduler()
        sched.start()
        log.info("in-API scheduler started (sync%s)",
                 " + ai" if settings.AI_EMPLOYEES_ENABLED else "")
    try:
        yield
    finally:
        if sched:
            sched.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(title=f"{settings.PRODUCT_NAME} API", lifespan=lifespan)

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
        # Throttle the unauthenticated surface. AFTER tenant resolution (the limiter keys on the
        # tenant host so one realm cannot spend another's budget) and BEFORE the handler, so a
        # spray costs a dict lookup rather than a bcrypt verify. A 429 raised here is an
        # HTTPException, which the try below must not swallow into a 500.
        try:
            await enforce(request)
        except HTTPException as e:
            return JSONResponse({"detail": e.detail}, status_code=e.status_code,
                                headers=e.headers or None)
        try:
            return await call_next(request)
        except Exception:
            log.exception("Unhandled error on %s %s", request.method, request.url.path)
            return JSONResponse({"detail": "Internal Server Error"}, status_code=500)

    # Added LAST so it is the OUTERMOST user middleware — it must wrap the handler
    # above so CORS headers land on error responses too (see the note above).
    #
    # allow_origin_regex, not just a list: every tenant is served from its own
    # {slug}.PLATFORM_DOMAIN origin, so a static list would mean editing an env var and
    # redeploying the API to onboard a customer — which is exactly what provisioning
    # promises it does NOT require. The explicit list stays for localhost and for
    # tenants on custom domains (see settings.origins).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_origin_regex=settings.origin_regex,
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    )

    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(totp.router, prefix="/api/v1")
    app.include_router(dashboard.router, prefix="/api/v1")
    app.include_router(businesses.router, prefix="/api/v1")
    app.include_router(integrations.router, prefix="/api/v1")
    app.include_router(users.router, prefix="/api/v1")
    app.include_router(assistant.router, prefix="/api/v1")
    app.include_router(books.router, prefix="/api/v1")
    app.include_router(binder.router, prefix="/api/v1")
    app.include_router(launches.router, prefix="/api/v1")
    app.include_router(ai_employees.router, prefix="/api/v1")
    app.include_router(ulrg.router, prefix="/api/v1")
    app.include_router(recall_router.router, prefix="/api/v1")   # /api/v1/webhooks/recall (secret-gated)
    app.include_router(share.router, prefix="/api/v1")   # /api/v1/share/{token}/scorecard (no auth)
    app.include_router(share.page_router)                 # /share/{token} — embeddable page + CSP

    @app.get("/health")
    async def health():
        return {"ok": True}

    return app


app = create_app()
