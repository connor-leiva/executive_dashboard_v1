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

### 6. Create the first workspace (one-time)
The database is empty after migration. Create the workspace with the supported path — it invites
the owner to choose their own password and destroys nothing:

```
railway ssh --service executive_dashboard_v1 "python -m scripts.create_tenant     --slug <slug> --name '<Name>' --owner-email <owner@example.com>"
```

It prints a one-time invite URL. Send that; the owner sets their own password.

**Do not run `python -m app.seed` against a deployed database.** This step used to say exactly
that, and it was wrong twice over. `app.seed` is a DEVELOPMENT FIXTURE: it is idempotent by
WIPING, so a second run deletes the workspace of that slug and everything in it, and it creates
the owner — the highest-privilege account there is — with a password published in this
repository. It now refuses to run on a real database rather than relying on this warning.

If a live owner's password may ever have been the seeded one, rotate it and check the audit
trail:

```
railway ssh --service executive_dashboard_v1 "python -m scripts.user_access     --tenant <slug> --email <owner@example.com> --reset-link"
```

### Running a one-off command against production

Every `scripts/*.py` tool needs `DATABASE_URL` pointing at the production database. Two ways,
and the difference matters:

```powershell
cd C:\...\executive_dashboardackend

# A. Explicit — the reliable one. Copy DATABASE_PUBLIC_URL from the Postgres service's
#    Variables tab (NOT DATABASE_URL: that is postgres.railway.internal, which only resolves
#    inside Railway and will hang or refuse from a laptop).
$env:DATABASE_URL="postgresql://...@monorail.proxy.rlwy.net:PORT/railway"
.\.venv\Scripts\python.exe -m scripts.user_access --tenant springb --email you@example.com --show

# B. Via the CLI, which injects the api service's variables into a LOCAL process.
#    Same caveat: it injects the internal URL, so it only works if you have added a public one.
railway run --service api .\.venv\Scripts\python.exe -m scripts.user_access --tenant springb --email you@example.com --show
```

With no `DATABASE_URL` set, these read the local SQLite file instead and report on data that
has nothing to do with production. Every tool prints which database it is talking to on its
first line for exactly that reason — check it before believing the output.

### Administering tenants

Tenant administration is a SEPARATE login from any customer's dashboard. Bootstrap the first
operator once (it cannot be created through the API — the API is gated by an operator session):

```
railway run --service api python -m scripts.create_operator --email you@example.com --name "You"
```

The password is generated and printed once, or taken from `PLATFORM_OPERATOR_PASSWORD`. Never
pass it as an argument — arguments land in shell history and the process list.

That account then works against `/api/v1/platform/*`:

| | |
|---|---|
| `POST /platform/login` | operator session (24h, throttled to 10 per 5 min) |
| `GET /platform/tenants` | every tenant with health: hosts, users, sources in error, last sync |
| `GET /platform/tenants/{slug}` | the above plus its owners |
| `POST /platform/tenants` | provision — the same `provision_tenant` the CLI calls |
| `POST /platform/tenants/{slug}/suspend` \| `/resume` | blocks new logins AND ends live sessions |
| `POST /platform/tenants/{slug}/resend-invite` | a fresh owner invite when the first expired |
| `GET /platform/tenants/{slug}/audit` | that tenant's own trail |

An operator administers tenants; they are **not** a user of one. A platform token cannot open a
customer's dashboard and a customer's token cannot reach these routes — the two carry different
claims, so neither can satisfy the other's guard. To see a customer's data, be invited into
their tenant as a user, which leaves a record in their audit trail rather than a silent read.

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

## Transactional email (Resend)

Invites, password resets and owner invites send from **`api`**. Binder reminder digests send
from **`worker`**. Railway variables are per-service, so these must be set on BOTH — set on
`api` alone and every invite works while digests silently log and never appear in Resend's
dashboard, which reads as a Binder bug rather than a missing variable. The cleanest fix is a
project **Shared Variable** referenced from both as `${{shared.RESEND_API_KEY}}`, so the two
cannot drift.

Empty key = no send, no error: every endpoint still returns its copy-paste link and the digest
still logs. That is the local-dev and test state, and it is also the safe state to deploy into.

| variable | default | why it exists |
|---|---|---|
| `RESEND_API_KEY` | *(empty)* | Empty disables sending without breaking anything. Resend shows a key ONCE at creation, so a partial paste is the usual cause of `400 validation_error: API key is invalid` — reissue rather than retry. The key must belong to the same Resend team as the verified domain. |
| `MAIL_FROM` | `Acumyn <mail@acumyn.io>` | Must be an address on a domain **verified in Resend**, or nothing is delivered. A key that is valid for a domain it may not send from fails differently: "not allowed to send from". |
| `MAIL_REPLY_TO` | *(empty)* | Fallback only. An invite overrides it with the inviter's own address, so a reply reaches the colleague who sent it. A password reset deliberately does not. |

To find out which of those is wrong without another deploy cycle, ask the service itself:

```
railway ssh --service executive_dashboard_v1 "python -m scripts.check_mail"
railway ssh --service worker "python -m scripts.check_mail you@example.com"
```

With no address it reports the key's length and shape (never its value) — enough to tell "not
set" from "truncated on paste". With an address it sends one real email and prints Resend's
exact answer, translated into what to go and change. Run it in the service you are asking about;
the two have different environments and "it works" on one says nothing about the other.

**Accepted is not delivered.** Resend accepting a message means the API call succeeded. Check
resend.com/emails for the delivery event, and check the inbox — the first sends from a new
domain are the ones most likely to be filtered.

## Meta Ads module

None of these are secret; the System User token is Fernet-encrypted on the Integration row and
is never an environment variable. Set on both the api and worker blocks.

| variable | default | why it exists |
|---|---|---|
| `META_GRAPH_VERSION` | `v26.0` | Pinned in one place. An EXPIRED Marketing API version does not error - Meta silently runs the call as a later version, so the failure mode is a number that changed rather than an exception. Graph and Marketing run separate clocks for the same version number. |
| `ADS_REFRESH_DAYS` | `7` | Meta restates recent days as attribution settles. A sync that writes only today freezes every prior day at its first, understated value. |
| `ADS_BACKFILL_MONTHS` | `13` | Matches Meta's retention on unique metrics. Older than that, `reach` and `frequency` come back null - null is not zero. |
| `ADS_MAX_CREATIVE_HOPS` | `40` | Cap on per-ad creative fetches per sync, ordered by spend. |
| `ADS_BUC_BACKOFF_PCT` | `70` | Business Use Case usage percentage above which the client backs off. |
| `ADS_ATTRIBUTION_WINDOW_DAYS` | `90` | Max lag from first touch to a countable close. **Currently unvalidated** - Phase 0 could not measure the real lag because no registration carried a date. Re-measure with `ads_probe` once dated registrations accumulate. |
| `ADS_COHORT_MIN_MATURITY` | `0.5` | Below this share of expected closes, a cohort renders "too early to read" rather than a confident small ROAS. |
| `ADS_CURVE_MIN_COHORTS` | `6` | Fewer complete cohorts than this and no curve is fitted, so no projection appears anywhere. A guessed curve is worse than an absent one, because it looks like a number. |

Run the Phase 0 probe against any deployment before trusting the funnel numbers - it is
read-only and tenant-scoped:

```
railway ssh --service executive_dashboard_v1 "python -m ads_probe --tenant <slug>"
```

## Secrets that must be set (the app refuses to start without them)

| variable | what it protects |
|---|---|
| `APP_SECRET` | signs every session token. On the committed default, anyone who can read the source can mint a valid session for any user in any workspace. |
| `FERNET_KEY` | encrypts every stored integration credential. On the committed default, "encrypted at rest" means encrypted with a key that is in the git history. |

Both carry working defaults so the app runs locally with no setup, which means a missing
variable in production does not fail — it succeeds quietly on a published value, and nothing
looks wrong. `app/startup_checks.py` refuses to boot in that state.

It does NOT key that decision on `ENV`, because `ENV` itself defaults to `development`: an
ENV-gated check is skipped by exactly the mistake it exists to catch. It enforces whenever the
app is talking to a real database.

Check a deployment BEFORE shipping a release that enforces this — a failing guard is a crash
loop, so confirm it passes first:

```
railway ssh --service executive_dashboard_v1 "python -m scripts.check_config"
```

Exit 0 means it will start. It never prints a secret's value, only whether it is absent, still
the committed default, or set to something of its own.

Rotating `FERNET_KEY` is not a config change: existing credentials were encrypted with the old
key and become unreadable, so every tenant has to reconnect every integration. Rotating
`APP_SECRET` signs everyone out. Neither is dangerous, but neither is free.

## Custom domains
Two things have to agree, and only the first is in Railway:

1. **Railway** (Settings → Networking): the customer-facing host on `web`, the API host on
   `api`, and the CNAMEs pointed at them.
2. **A `domain` row** for that host. This is what `tenancy.resolve_tenant` matches a request
   against, AND what `tenancy.tenant_app_url` builds every human-facing link from — password
   resets, invites, share links, the QuickBooks return. Use the script rather than SQL; it
   refuses the mistakes:

```
python -m scripts.tenant_domains --tenant <slug> --list
python -m scripts.tenant_domains --tenant <slug> --add app.example.com --primary
```

Without step 2 a tenant is reachable only through the single-tenant fallback, which closes
by itself the moment a second tenant exists — so a missing row is not cosmetic, it is a
lockout waiting for the next customer. `SINGLE_TENANT_FALLBACK=false` turns it off earlier.

`admin.`, `api.`, `auth.`, `static.` and `assets.` under `PLATFORM_DOMAIN` belong to the
platform and never resolve to a tenant; `--add` refuses them.

`www.`, `app.` and `staging.` are different: no tenant can claim one by its SLUG, but you can
point one at a tenant deliberately with `--add`, and you should. `www` was briefly in the list
above, which took production down — www.acumyn.io is the host the dashboard is actually served
from, so every API call failed tenant resolution before it reached authentication.

**A tenant needs a row for the host it is really served from, even while the fallback is open.**
The fallback makes a missing row invisible: everything works, right up until you provision a
second tenant, at which point it closes and the incumbent's front door stops resolving. So the
row is not optional bookkeeping — it is what stops onboarding your next customer from taking
your current one offline.
