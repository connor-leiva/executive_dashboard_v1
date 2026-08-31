import re

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
    # 5173 = vite dev, 4173 = vite preview. The preview serves the PRODUCTION build, which is
    # where UI work actually gets verified, so leaving it out meant every such check died on a
    # CORS preflight before showing anything.
    ALLOWED_ORIGINS: str = ("http://localhost:5173,http://127.0.0.1:5173,"
                            "http://localhost:4173,http://127.0.0.1:4173")
    PUBLIC_API_BASE: str = "http://localhost:8000"

    # QuickBooks
    QBO_CLIENT_ID: str = ""
    QBO_CLIENT_SECRET: str = ""
    QBO_ENV: str = "production"
    QBO_REDIRECT_URI: str = "http://localhost:8000/api/v1/integrations/qbo/callback"
    APP_PUBLIC_URL: str = "http://localhost:5173"  # where the QBO callback redirects back to
    # Base for QuickBooks Online transaction deep links (Books "open in QuickBooks").
    # Opens in the user's active QBO company; the route is derived from the txn type.
    QBO_APP_BASE: str = "https://app.qbo.intuit.com/app"

    # Sisu (real estate production — team-wide clients feed, Basic auth).
    # The CREDENTIALS are per tenant, on the integration row — never here: a process-wide
    # username/token meant every tenant synced the first tenant's team. What remains is the
    # non-secret default API base (a tenant's integration config can override it).
    SISU_BASE_URL: str = "https://api.sisu.co/api"
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
    # Follow Up Boss (lead-source layer — Basic auth: key as username). Key is per tenant,
    # on the integration row, for the same reason as Sisu above.
    FUB_API_BASE: str = "https://api.followupboss.com/v1"

    # worker
    SYNC_INTERVAL_MINUTES: int = 30
    # Run the background scheduler (syncs + the AI Employees dispatch/execute jobs) INSIDE the
    # API process instead of a separate `python -m app.worker` service. For single-service
    # deployments that don't run a dedicated worker. Keep the API to ONE process when on
    # (the run picker also uses SELECT … FOR UPDATE SKIP LOCKED on Postgres as a backstop).
    RUN_WORKER_IN_API: bool = False

    # GHL location timezone — GHL stores UTC but records/displays the location's local
    # date, so GHL transaction dates (createdAt/fulfilledAt) resolve here.
    BILLING_TIMEZONE: str = "America/Denver"
    # Legacy Stripe ACCOUNT timezone — Stripe renders charge dates in the account's tz
    # (Connor's is UTC), so legacy-Stripe `created` resolves here. Matching each source
    # to its own system's tz makes the dashboard agree with both AND lines the two copies
    # of a charge onto the same day so the dedupe collapses them.
    STRIPE_TIMEZONE: str = "UTC"

    # Dashboard assistant (Claude). Set ANTHROPIC_API_KEY to enable the "Ask" panel;
    # the key stays server-side and is never sent to the browser. Model + budget are
    # overridable without a code change.
    ANTHROPIC_API_KEY: str = ""
    ASSISTANT_MODEL: str = "claude-sonnet-5"
    ASSISTANT_MAX_TOKENS: int = 2048

    # Books scan — Pass 3 (Claude categorization of the ambiguous remainder). Reuses
    # ANTHROPIC_API_KEY but is OFF by default: flip BOOKS_SCAN_CLAUDE_ENABLED on only after
    # watching a week of live batches in the logs (SPEC Part 10 Step 4). BOOKS_SCAN_MODEL
    # falls back to ASSISTANT_MODEL when blank. Claude NEVER posts to QuickBooks.
    BOOKS_SCAN_CLAUDE_ENABLED: bool = False
    BOOKS_SCAN_MODEL: str = ""
    BOOKS_SCAN_BATCH: int = 20
    BOOKS_SCAN_MAX_TOKENS: int = 4096
    BOOKS_CONF_THRESHOLD: float = 0.9
    BOOKS_WRITEBACK_ENABLED: bool = False

    # Acumyn Binder — document-driven entity-compliance engine (SPEC-binder-module Part 15).
    # Extraction reuses ANTHROPIC_API_KEY; BINDER_EXTRACT_MODEL falls back to ASSISTANT_MODEL
    # when blank. The extraction pipeline only ever proposes obligations — a human confirms
    # each before it is tracked. RULE_STALE_MONTHS drives the admin "stale rules" view (BOI's
    # 2024-2025 turbulence is why rules carry last_verified). Storage/email/ingest are later
    # steps; the keys are declared now so the module's config surface is stable.
    BINDER_EXTRACT_MODEL: str = ""            # default: falls back to ASSISTANT_MODEL
    BINDER_INGEST_EMAIL_DOMAIN: str = ""      # for binder@{slug}.acumyn.io (Part 3)
    BINDER_STORAGE_BUCKET: str = ""           # filesystem base (dev) / Railway volume path (Part 2)
    BINDER_REMINDER_DIGEST: str = "daily"     # daily | weekly (Part 9)
    BINDER_RULE_STALE_MONTHS: int = 12
    # Cloudflare R2 (S3-compatible) object storage for binder documents. When all four are set,
    # blobs live in R2 (durable, shared across the api + worker, survives redeploys) instead of
    # the local filesystem. Unset -> filesystem fallback (local dev + tests).
    R2_ACCOUNT_ID: str = ""
    R2_BUCKET: str = ""
    R2_ACCESS_KEY_ID: str = ""
    R2_SECRET_ACCESS_KEY: str = ""
    # Shared secret the inbound-email webhook must present (X-Ingest-Secret). Empty = the
    # forwarding-address channel is DISABLED (the endpoint 404s). The email provider's
    # inbound-parse routing to the webhook is external infra to configure separately.
    BINDER_INGEST_SECRET: str = ""

    # AI Employees — agentic employees (v1 archetype: Social Media Manager). OFF by default:
    # AI_EMPLOYEES_ENABLED guards the routers, the two worker jobs, and the rail item.
    # Writeback (measure→GHL) needs BOTH AI_EMPLOYEES_WRITEBACK_ENABLED and the per-employee
    # writeback_enabled flag (the Books pattern) — v1 ships approve+export, so the GHL write
    # path stays off. AI_EMPLOYEES_MODEL falls back to ASSISTANT_MODEL when blank. The monthly
    # per-tenant token budget (in+out) hard-stops scheduled runs (→ skipped_budget); 0 = unlimited.
    AI_EMPLOYEES_ENABLED: bool = False
    AI_EMPLOYEES_WRITEBACK_ENABLED: bool = False
    AI_EMPLOYEES_MODEL: str = ""
    AI_EMPLOYEES_MAX_TOKENS: int = 4096
    AI_EMPLOYEES_TOKEN_BUDGET: int = 2_000_000

    # ── Recall.ai call recording. Empty API key = the whole feature is inert, which is the
    # right default: no key, no bots, no spend, no consent exposure.
    RECALL_API_KEY: str = ""
    RECALL_REGION: str = "us-west-2"
    RECALL_BOT_NAME: str = "Call Notetaker"   # attendees SEE this - it is the disclosure surface
    RECALL_LEAD_MINUTES: int = 5              # how early the bot joins
    RECALL_TICK_MINUTES: int = 5              # scheduler cadence; also sets the lookahead window
    RECALL_WEBHOOK_SECRET: str = ""           # shared secret on the status-change callback
    RECALL_URL_OVERRIDES: str = ""            # "vanity.com/zoom=https://real,..." for redirect links
    RECALL_ADOPT_LOOKBACK_DAYS: int = 14      # link bots created outside the app to PAST calls too
    RECALL_ADOPT_LOOKAHEAD_DAYS: int = 30     # ...and to calls further out than the scheduling window
    # Transcripts are STORED (Connor, 2026-08-19) so the corpus can be searched and analysed.
    # Retention is a deliberate policy, not an oversight - one year, enforced by a purge job.
    RECALL_TRANSCRIPT_RETAIN_DAYS: int = 365
    RECALL_TRANSCRIPT_BATCH: int = 25         # per tick, so a backlog never stalls the worker
    # Chapters (topic segmentation for the review player). Reuses ANTHROPIC_API_KEY and is a
    # no-op without it. Haiku by default: chaptering is a well-bounded task and this runs once
    # per call, so the cheaper model is the right one — set to "" to fall back to
    # ASSISTANT_MODEL, or name any model to override.
    RECALL_CHAPTER_MODEL: str = "claude-haiku-4-5-20251001"
    RECALL_CHAPTER_BATCH: int = 15            # per tick; one model call each

    # The platform's own domain. Tenants live at {slug}.PLATFORM_DOMAIN unless they bring a
    # custom domain (a `domain` row). Drives tenant-host resolution, the CORS origin regex,
    # and the invite links provisioning hands out — so it is set in ONE place, not three.
    PLATFORM_DOMAIN: str = "acumyn.io"
    # What the PLATFORM calls itself, in the API title and as the default product name for a
    # tenant that has not set its own. Not a customer's name.
    PRODUCT_NAME: str = "Command Center"

    DEV_TENANT_SLUG: str = "springb"
    # When a request Host matches no `domain` row, resolve to DEV_TENANT_SLUG. Safe only
    # while there is exactly ONE tenant — with two, an unrecognized host would silently
    # serve the wrong customer's data. resolve_tenant enforces that: the fallback refuses
    # the moment a second tenant exists, whatever this flag says.
    SINGLE_TENANT_FALLBACK: bool = True

    # Seed representative ULRG operational data (fake transactions/agents/leads).
    # Keep true for local demos; set false in production so the real Sisu sync
    # is the sole source of ULRG operational metrics.
    SEED_SAMPLE_OPS: bool = True

    # ── Meta Ads (SPEC-ads-module.md Part 6.7). Reporting only - the System User token lives
    # Fernet-encrypted on the Integration row, never here and never in a browser.
    #
    # The version is pinned in ONE place. Expired Marketing API versions do not error: Meta
    # silently executes the call as a later version, so the failure mode is a number that changed
    # rather than an exception anybody notices. Graph and Marketing run separate clocks for the
    # same version number - v24.0 dies on the Marketing clock 2026-10-06 and lives on the Graph
    # clock to 2028.
    META_GRAPH_VERSION: str = "v26.0"
    ADS_REFRESH_DAYS: int = 7          # rolling restatement window; Meta revises recent days
    ADS_BACKFILL_MONTHS: int = 13      # matches Meta's unique-metric retention
    ADS_MAX_CREATIVE_HOPS: int = 40
    # How long creative detail (headline, body, thumbnail, url_tags) may go unrefreshed. One
    # request per ad, so this number is the difference between a few dozen calls a week and a
    # few thousand. Ads change rarely; the quota they share does not.
    ADS_CREATIVE_TTL_DAYS: int = 7
    ADS_BUC_BACKOFF_PCT: int = 70
    # Funnel and attribution.
    #
    # 90 is the spec's default and it is currently UNVALIDATED for any live tenant: Phase 0 could
    # not measure the real lag because no registration carried a date. Re-measure with ads_probe
    # once dated registrations have accumulated; if the p90 exceeds this, either the window widens
    # or genuine closes go uncredited, and that is a decision rather than a default.
    ADS_ATTRIBUTION_WINDOW_DAYS: int = 90
    ADS_COHORT_MIN_MATURITY: float = 0.5   # below this a cohort renders "too early to read"
    ADS_CURVE_MIN_COHORTS: int = 6         # fewer complete cohorts -> no curve, no projection

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def origin_regex(self) -> str:
        """Every https origin under the platform domain, at any subdomain depth.

        This is what makes provisioning zero-ops: a tenant at {slug}.PLATFORM_DOMAIN passes
        CORS the moment it exists, with no env edit and no redeploy. Anchored at both ends
        and with a literal-dot suffix so `notacumyn.io` and `acumyn.io.evil.com` do not
        match. Tenants on their own custom domain are still added to ALLOWED_ORIGINS.
        """
        return r"https://([A-Za-z0-9-]+\.)*" + re.escape(self.PLATFORM_DOMAIN) + r"$"

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
