# Deploying to Railway

This is a **monorepo** with three deployables. Railway does **not** split a repo
into services automatically — you create one service per app and scope each with
a **Root Directory**. (That's why a single root-level service failed with
"Railpack could not determine how to build the app".)

Target: one Railway project = **Postgres + api + worker + web**.

| Service | Root Directory | Builder | Notes |
|---|---|---|---|
| Postgres | — | plugin | New → Database → PostgreSQL |
| `api` | `backend` | auto-detects `backend/Dockerfile` | public domain; runs migrations |
| `worker` | `backend` | `Dockerfile.worker` via a variable | **no** domain |
| `web` | `frontend` | auto-detects `frontend/Dockerfile` | public domain; needs a build var |

## Step by step

### 1. Postgres
Project → **New → Database → PostgreSQL**. Note the service name (default `Postgres`).

### 2. `api` service
- New → GitHub Repo → select this repo.
- **Settings → Root Directory:** `backend` (Railway then auto-detects `backend/Dockerfile`).
- **Settings → Deploy → Pre-Deploy Command:** `alembic upgrade head`
- **Variables** (see the matrix below). Reference the DB with `${{Postgres.DATABASE_URL}}`.
- **Settings → Networking → Generate Domain** (this is your API URL).

### 3. `worker` service
- New → GitHub Repo → **same repo**.
- **Settings → Root Directory:** `backend`
- **Variables → New Variable:** `RAILWAY_DOCKERFILE_PATH` = `backend/Dockerfile.worker`
  (a non-default Dockerfile name is never auto-detected; this points the worker at it).
- Same DB + secret + integration variables as `api`. **No domain, no pre-deploy.**

### 4. `web` service
- New → GitHub Repo → **same repo**.
- **Settings → Root Directory:** `frontend`
- **Variables:** `VITE_API_BASE` = `https://<your api domain>/api/v1`
  (this is consumed as a **build arg** — the Dockerfile declares `ARG VITE_API_BASE`).
- **Settings → Networking → Generate Domain** (this is the URL Spring visits).

> After the `web` domain exists, go back and make sure the `api` service's
> `ALLOWED_ORIGINS` and `APP_PUBLIC_URL` point at the `web` domain, then redeploy `api`.

### 5. Deploy order
Deploy **api** first (its pre-deploy runs the migrations), then **worker**, then **web**.

### 6. Seed tenant #1 (one-time)
The database is empty after migration. Seed Spring's tenant, businesses, and owner login:
- Easiest: install the Railway CLI, then from `backend/`:
  `railway run --service api python -m app.seed`
  (runs the seed against the live DB using the api service's env).
- The seed currently includes **representative** operational/financial data so the
  dashboard renders fully. Trim `app/seed.py` to just tenant/businesses/owner when
  you want a clean production start.

Owner login after seeding: `spring@springb.com` / `springtime` — **change it**.

## Environment variables

**`api` + `worker`** (shared):
```
DATABASE_URL=${{Postgres.DATABASE_URL}}
APP_SECRET=<64-hex>                # python -c "import secrets;print(secrets.token_hex(32))"
FERNET_KEY=<fernet key>            # python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
ENV=production
PLATFORM_DOMAIN=acumyn.io          # tenants live at {slug}.PLATFORM_DOMAIN
SINGLE_TENANT_FALLBACK=true        # unknown host -> Spring; SELF-CLOSES at tenant #2 (see below)
ALLOWED_ORIGINS=https://<web domain>     # localhost + CUSTOM domains only; *.PLATFORM_DOMAIN
                                          # is admitted by regex, so a new tenant needs no edit
QBO_CLIENT_ID=...                  # when wiring QuickBooks
QBO_CLIENT_SECRET=...
QBO_ENV=production
QBO_REDIRECT_URI=https://<api domain>/api/v1/integrations/qbo/callback
```
> **Sisu and Follow Up Boss are no longer environment variables.** Both are connected
> per tenant in Settings -> Integrations and stored encrypted on the `integration` row,
> like GHL/Arive/Stripe/QBO. The old `SISU_USERNAME` / `SISU_API_TOKEN` / `FUB_API_KEY`
> vars are ignored — a shared key would have synced one customer's book of business
> into another's dashboard.
**`api` also:**
```
PUBLIC_API_BASE=https://<api domain>
APP_PUBLIC_URL=https://<web domain>      # QBO callback redirects back here
```
**`worker` also:** `SYNC_INTERVAL_MINUTES=30`

**`web`:** `VITE_API_BASE=https://<api domain>/api/v1`  (build-time only; non-secret)

> One API domain serves every tenant. The browser tells the API which tenant it is by
> sending `X-Tenant-Host: <its own hostname>` on every request (see `frontend/src/api.js`),
> because the API's own `Host` header names the API, not the customer. This is why a single
> `web` build can serve all tenants.

## Adding a tenant

```
railway run --service api python -m scripts.create_tenant   --slug acme --name "Acme Co" --owner-email owner@acme.com
```
Prints a one-time invite link. The tenant is live at `acme.<PLATFORM_DOMAIN>` immediately:
DNS is the existing wildcard, CORS matches by regex, and provisioning seeds the catalogs
(Binder jurisdiction rules, AI skills, the standard chart of accounts).

**The moment a second tenant exists, the single-tenant fallback turns itself off** — a host
matching no `domain` row stops resolving instead of quietly returning Spring's dashboard.
That is enforced on the tenant COUNT, not on `SINGLE_TENANT_FALLBACK`, so there is no
config change to remember. Point each customer's domain at the `web` service and add a
`domain` row for anything that is not a `{slug}.PLATFORM_DOMAIN` subdomain.

## Why these matter (all verified against Railway docs)
- **`DATABASE_URL`** from Railway is `postgresql://…`; the app rewrites it to
  `+asyncpg` (engine) and `+psycopg` (Alembic) automatically — no manual driver suffix.
- **`$PORT`**: the api/web containers bind Railway's injected `$PORT` (Dockerfiles handle this).
- **`VITE_API_BASE`** must be a **build** variable; it's baked into the bundle. If it's
  missing, the app quietly serves bundled sample data instead of live numbers.
- **`RAILWAY_DOCKERFILE_PATH`** is required only for the worker (alternate Dockerfile name).

## Custom domains (later)
Add `cmd.springb.com` to `web` and `api.springb.com` to `api` (Settings → Networking),
point the CNAMEs, then insert a `domain` row per hostname and set
`SINGLE_TENANT_FALLBACK=false` once you have real per-tenant domains.
