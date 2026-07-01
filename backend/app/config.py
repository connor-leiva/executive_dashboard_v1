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

    # Sisu + Follow Up Boss (reuse Realtor.com dashboard values)
    SISU_API_BASE: str = ""
    SISU_API_TOKEN: str = ""
    FUB_API_KEY: str = ""
    FUB_API_BASE: str = "https://api.followupboss.com/v1"

    # worker
    SYNC_INTERVAL_MINUTES: int = 30

    # Single-tenant fallback: when a request Host doesn't match a `domain` row,
    # resolve to this tenant slug. Safe while there is one tenant (Spring); set
    # SINGLE_TENANT_FALLBACK=false once real multitenancy + custom domains land.
    DEV_TENANT_SLUG: str = "springb"
    SINGLE_TENANT_FALLBACK: bool = True

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
