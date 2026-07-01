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
SINGLE_TENANT_FALLBACK=true        # resolves the Railway domain to Spring's tenant
ALLOWED_ORIGINS=https://<web domain>
QBO_CLIENT_ID=...                  # when wiring QuickBooks
QBO_CLIENT_SECRET=...
QBO_ENV=production
QBO_REDIRECT_URI=https://<api domain>/api/v1/integrations/qbo/callback
SISU_API_BASE=...  SISU_API_TOKEN=...   # when wiring Sisu
FUB_API_KEY=...                          # when wiring Follow Up Boss
```
**`api` also:**
```
PUBLIC_API_BASE=https://<api domain>
APP_PUBLIC_URL=https://<web domain>      # QBO callback redirects back here
```
**`worker` also:** `SYNC_INTERVAL_MINUTES=30`

**`web`:** `VITE_API_BASE=https://<api domain>/api/v1`  (build-time only; non-secret)

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
