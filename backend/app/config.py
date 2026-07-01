from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # dev convenience: bypass Host->tenant lookup with a single-tenant slug
    DEV_TENANT_SLUG: str = "springb"

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


settings = Settings()
