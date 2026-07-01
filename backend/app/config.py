from pydantic_settings import BaseSettings, SettingsConfigDict


def _normalize_async(url: str) -> str:
    """Coerce a plain Postgres URL to the asyncpg driver for the async engine.

    Railway's Postgres plugin exposes DATABASE_URL as `postgresql://...`, which
    SQLAlchemy's async engine rejects ("asyncio extension requires an async
    driver"). SQLite / already-qualified URLs pass through unchanged.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


def _normalize_sync(url: str) -> str:
    """Coerce the same URL to a sync driver (psycopg) for Alembic."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url.replace("+asyncpg", "+psycopg").replace("+aiosqlite", "")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Default to a local SQLite file so the app runs with zero infra in dev.
    # In prod set DATABASE_URL to postgresql+asyncpg://...
    DATABASE_URL: str = "sqlite+aiosqlite:///./command_center.db"
    APP_SECRET: str = "dev-insecure-secret-change-me-please-0123456789abcdef"
    # A valid dev Fernet key (32 url-safe base64 bytes). Override in prod.
    FERNET_KEY: str = "ioZZk-alzy9XSx8YtiFHOJyUldLmcz4SkPKrWwIR4xE="
    ENV: str = "development"
    ALLOWED_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    PUBLIC_API_BASE: str = "http://localhost:8000"

    # QuickBooks
    QBO_CLIENT_ID: str = ""
    QBO_CLIENT_SECRET: str = ""
    QBO_ENV: str = "production"
    QBO_REDIRECT_URI: str = "http://localhost:8000/api/v1/integrations/qbo/callback"
    APP_PUBLIC_URL: str = "http://localhost:5173"  # where the QBO callback redirects back to

    # Sisu (real estate production — team-wide clients feed, Basic auth)
    SISU_USERNAME: str = ""
    SISU_API_TOKEN: str = ""
    SISU_BASE_URL: str = "https://api.sisu.co/api"
    SISU_TEAM_ID: str = "621"
    SISU_EXTERNAL_SERVICE: str = "fub"
    # Deep-link template for a Sisu transaction (audit drawer). {id} = client_id.
    # CONFIRM against the live Sisu app URL and adjust if needed.
    SISU_TXN_URL: str = "https://app.sisu.co/transactions/{id}"
    # A source is "stale" if not synced within this many minutes (freshness tint).
    STALE_AFTER_MINUTES: int = 180
    SISU_MAX_PAGES: int = 0            # 0 = pull all pages; >0 caps for faster syncs
    # "Current" window for pending pipeline + active listings. Sisu holds ~20yrs
    # of history; a deal under contract or a listing older than this is stale
    # data, not live pipeline. Tune against Sisu's own current counts.
    SISU_CURRENT_WINDOW_DAYS: int = 180
    # Follow Up Boss (lead-source layer — Phase 1b, Basic auth: key as username)
    FUB_API_KEY: str = ""
    FUB_API_BASE: str = "https://api.followupboss.com/v1"

    # worker
    SYNC_INTERVAL_MINUTES: int = 30

    # Single-tenant fallback: when a request Host doesn't match a `domain` row,
    # resolve to this tenant slug. Safe while there is one tenant (Spring); set
    # SINGLE_TENANT_FALLBACK=false once real multitenancy + custom domains land.
    DEV_TENANT_SLUG: str = "springb"
    SINGLE_TENANT_FALLBACK: bool = True

    # Seed representative ULRG operational data (fake transactions/agents/leads).
    # Keep true for local demos; set false in production so the real Sisu sync
    # is the sole source of ULRG operational metrics.
    SEED_SAMPLE_OPS: bool = True

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    @property
    def async_database_url(self) -> str:
        return _normalize_async(self.DATABASE_URL)

    @property
    def sync_database_url(self) -> str:
        return _normalize_sync(self.DATABASE_URL)


settings = Settings()
