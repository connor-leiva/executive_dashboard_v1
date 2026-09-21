# Acumyn → Axcion: rebrand and domain cutover

**Status:** in progress — see the phase ledger below
**Written:** 2026-09-21

| Phase | State |
|---|---|
| 0 · Pre-flight | partial — baseline 1899 passing, one Alembic head; **no DB backup taken yet (do this before Phase 9)** |
| 1 · DNS (GoDaddy) | **done** — axcion.io live, certs valid, apex 301s |
| 2 · Railway | **done** — `*.axcion.io` + `api.axcion.io` ACTIVE on port 8080 |
| 3 · External services | **not started** — needs Connor (Resend, Google Cloud, Intuit, Meta) |
| 4 · Backend code | **done** |
| 5 · Frontend code | **done** |
| 6 · Migrations | **done** — `0079_rebrand_stored_names` (2 values, not 3) + `scan_rebrand.py`; scan run against production (§1.4) |
| 7 · Docs | **done** |
| 8 · Verify | **done** — 1899 passing (baseline unchanged), 5 bundles build, browser-verified on the prod build |
| 9 · Cutover | **not started** — no Railway variable has been changed; both domains still serve |
| 10 · Retire acumyn.io | **not started** |
| 11 · Visual identity | deferred by D1 |

**Nothing user-facing has changed yet.** Every `acumyn.io` host still serves exactly what it
did, because the cutover is a Railway variable change (Phase 9) and none has been made.
**Old identity:** Acumyn · `acumyn.io`
**New identity:** Axcion · `axcion.io`

---

## 0. Decisions this spec is built on

Four questions were settled before this document was written. Every phase below assumes
these answers; changing one changes the shape of the plan, not just a detail.

| # | Decision | Chosen | Consequence |
|---|---|---|---|
| D1 | Visual identity scope | **Name only now, visual refresh later** | The aperture mark, the Cadet palette and the Space Grotesk / Instrument Sans / Archivo pairing all stay exactly as they are. Only the *word* changes. Phase 11 is a stub for the later visual work. |
| D2 | Fate of `acumyn.io` | **Hard cutover, then retire** | `axcion.io` becomes the only working host. `acumyn.io` stops serving. Every existing bookmark, invite link and share link dies. |
| D3 | DNS host for `axcion.io` | **Stay on GoDaddy** | GoDaddy still cannot point an apex at Railway (no ALIAS/ANAME/CNAME-flattening). `www.axcion.io` is canonical; the apex stays on GoDaddy forwarding. The limitation is inherited, not fixed. |
| D4 | Internal identifier keys | **Rename everything, with migrations** | The stored `typeface` value, the Win-the-Day playbook `format` string, the Stripe metadata keys and the support-account email alias all change, each with a migration or a documented no-op. |

### 0.1 One concern stated, then set aside

D2 (retire `acumyn.io`) is safe **only because every tenant is yours**. The production
database has five tenants — `springb`, `utah-life`, `acmerealty`, `testrealty`,
`testrealty2` — and no third-party customer. There are **12 live share links** whose
already-distributed URLs are `https://{slug}.acumyn.io/share/...`; those URLs die at
Phase 10. The links themselves survive (a `share_link` row stores only a token; the host
is rebuilt at render time from the tenant's `domain` row), so re-sending them from the
new host is a copy-paste, not a re-issue. Proceeding as decided.

D4 is cheaper than it looks, and one part of it should **not** be done — see
§A2, the exception register.

---

## 1. What the audit found

### 1.1 Repository inventory

`git grep -il acumyn` matches **155 tracked files**. By area:

| Area | Files | Lines |
|---|---|---|
| `backend/app` | 39 | 157 |
| `backend/tests` | 32 | 109 |
| `backend/alembic` | 8 | 13 |
| `backend/scripts` | 2 | 7 |
| `frontend/src` | 57 | 235 |
| `frontend/{marketing,operator,console}` + `index.html` | 2 | 5 |
| `docs/` | 2 | 4 |
| Root `*.md` | 3 | 51 |

The name appears in five distinct grammatical roles, and they need different treatment:

1. **The company/product word** — `Acumyn`, ~390 occurrences, overwhelmingly in prose,
   comments and user-visible copy. Mechanical.
2. **The domain** — `acumyn.io` and its hosts (`www.`, `app.`, `api.`, `admin.`,
   `mail.`, `{slug}.`). Configuration, mostly already parameterised.
3. **Code identifiers** — `AcumynMark`, `AcumynLockup`, `PoweredByAcumyn`,
   `ACUMYN_SITE`, `ACUMYN`, `ACUMYN_TYPE`, `ACUMYN_STAGES`, `seedsFromAcumyn`.
   Mechanical rename plus import-site updates.
4. **Stored values and wire contracts** — `typeface: "acumyn"`, `acumyn.wtd-playbook`,
   `acumyn_tenant_id`, `scope=acumyn`, `acumyn-workspace-metadata/1`,
   `+acumyn-support@`. **These are the dangerous ones.** Each is read back by
   something that already has the old value written down.
5. **Historical record** — `audit_log.actor_label` values reading `… (Acumyn)`.
   Immutable by intent. See §A2.

### 1.2 Live infrastructure, as it actually is today

Read from Railway on 2026-09-21. Project `glorious-wholeness`
(`b9bb14be-0951-4f7d-bc03-e8f3713af39e`), environment `production`.

| Service | Role | Custom domains |
|---|---|---|
| `executive_dashboard_v1` | FastAPI API **and** the in-process scheduler (`RUN_WORKER_IN_API=true`) | `api.acumyn.io` |
| `zippy-cat` | Caddy, serving all five frontend bundles | `*.acumyn.io`, `www.acumyn.io` |
| `Postgres` | database | — |

**There is no separate `worker` service.** One set of mail variables on
`executive_dashboard_v1` covers every send. (`DEPLOY.md` §Transactional email already
says this; re-confirmed here because Phase 3 touches those variables.)

**Three findings that change the plan:**

- **`PLATFORM_DOMAIN` is not set on Railway at all.** The API is running on the code
  default in `backend/app/config.py:221`. So the domain cannot be cut over by setting an
  environment variable alone — either the default changes in code, or the variable is
  added. This spec does **both** (belt and braces: the default is correct, and the
  variable makes it explicit).
- **None of `MARKETING_HOST`, `MARKETING_ALT_HOST`, `FRONTDOOR_HOST`, `OPERATOR_HOST`
  are set on `zippy-cat`.** Caddy is running entirely on the defaults baked into
  `frontend/Caddyfile`. Same treatment.
- **`GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are not set.** Google sign-in is
  currently **off in production**. `google_auth.configured()` returns false, and no
  workspace is offered the button. This makes Phase 3.2 much easier — there may be no
  existing OAuth client to re-register. Confirm before assuming.

`VITE_API_BASE=https://api.acumyn.io/api/v1` on `zippy-cat` is a **build-time** variable.
Changing it triggers a Railway rebuild; it is not a runtime flip.

### 1.3 Production database, as it actually is today

Alembic head: **`0078_sop_suggestions`** (single head — verified).

| Fact | Value |
|---|---|
| Tenants | `acmerealty`, `springb`, `testrealty`, `testrealty2`, `utah-life` (all `active`) |
| `domain` rows | 5, all `{slug}.acumyn.io`, all `is_primary = true` |
| `tenant.config->brand->>'typeface'` | `springb` = `classic`; `testrealty` = **`acumyn`**; the other three `NULL` (fall back to `acumyn`) |
| `platform_user` | 1 — `connor.leiva@gmail.com` |
| Support account | 1 — `connor.leiva+acumyn-support@gmail.com`, name `Connor Leiva (Acumyn support)` |
| `platform_billing_config` | **0 rows** |
| `platform_subscription` | **0 rows** |
| `platform_invoice` | **0 rows** |
| `intranet_wtd_playbook` | 1 row (`content` jsonb carries `format: "acumyn.wtd-playbook"`) |
| `share_link` | 12 rows |
| `binder_document` | 51 rows (blobs live in Cloudflare R2, bucket `acumyn-storage`) |
| `audit_log` rows with an `Acumyn` actor label | 10 |

**Stripe platform billing has never been connected.** That removes the single riskiest
item in D4: renaming the `acumyn_tenant_id` / `acumyn_slug` metadata keys is a pure code
change with **zero backfill** and no chance of orphaning a live subscription. It only
stays free if it is done *before* billing is connected — which is another reason to do
the rebrand now rather than later.

### 1.4 The scan this audit could not finish

A full sweep of every `text` / `varchar` / `json` / `jsonb` column in production for the
string `acumyn` was blocked by the session's production-read guard. The four tables most
likely to hold it were checked individually and are listed above, but **that is a
hand-kept list, and a hand-kept list is exactly what misses the next one.**

Run this before Phase 6 and treat its output as the authoritative migration scope:

```bash
cd backend && ./.venv/Scripts/python.exe -m scripts.scan_rebrand --show 2
```

**Written, shipped and RUN against production 2026-09-21** (`backend/scripts/scan_rebrand.py`).
Read-only; enumerates every text/json column from `information_schema` rather than from
anyone's memory, and exits 1 when it finds anything, so it can gate a cutover step.

**542 text/json columns scanned. 8 hold the old name. It changed the plan.**

| Column | Rows | Verdict |
|---|---|---|
| `tenant.config` | 1 | migration `0079` — `testrealty`'s typeface, confirmed by direct query |
| `user.email` | 1 | migration `0079` |
| `user.name` | 1 | migration `0079` |
| `domain.hostname` | 5 | Phase 9.4, via `scripts/tenant_domains.py` |
| `audit_log.actor_label` | 10 | exception §A2 — already registered |
| `audit_log.detail` | 13 | exception §A2 — **was not registered** |
| `platform_audit.detail` | 10 | exception §A2 — **was not registered** |
| `intranet_publish_batch.snapshot` | 4 | exception §A2 — **was nowhere in this spec** |

**Migration `0079` needed no change** — its three targets were right and complete. What was
wrong was the *exception register*, in three places. That matters for Phase 10.11, which
re-runs this scan and checks the leftovers against §A2: with the register as first written,
that check would have reported three false gaps and invited somebody to "fix" an audit trail.

The expected leftover set after Phase 10 is now exactly those four exception rows — 37 rows
across 4 columns, all archival.

### 1.5 The gap that proves the point

The audit's list of wire contracts (§1.1 item 4) was incomplete, and the miss was found by
scanning the *consumer* rather than re-reading the list. `backend/app/services/ads_funnel.py`
emits `zone: "acumyn"` on every funnel rung and `frontend/src/ads/Funnel.jsx` compares against
it — a second API-to-browser contract of exactly the same shape as `scope`, and nowhere in
this document's first draft.

It is worth stating what it would have cost, because it is the failure mode this whole class
of value shares: **nothing would have thrown.** A mismatch would have collapsed the
Meta/Axcion crossing that is the ads module's entire thesis, silently widened the leak
calculation to include boundaries it exists to exclude, and withdrawn every rung's
drill-down — on a tab that would still have rendered and still have looked plausible.

**A third turned up later, and it is the one that could not have been found by any scan.**
The Win-the-Day playbook's `format` is not stored in the database at all — it belongs to the
EXPORT ENVELOPE. `export_bundle()` writes it into a downloadable file and the importer reads
`bundle["content"]`, the inner document, so the string leaves the product entirely and lives
on somebody's disk. Accepting only the new spelling would have permanently rejected every
playbook exported before the rename, with a validation error naming a field the person never
wrote and cannot see. Handled in `wtd_playbook._Bundle`, which takes both; exports emit the
new one. The planned migration step for it was **removed** — it would have matched zero rows
for ever while reading as though it did something.

Treat §4.2's table as "the ones found so far", not as the set. Three were found, by three
different methods: reading the consumer, scanning production, and following the value out of
the system into a file.

---

## 2. Naming rules

Applied uniformly, so a reviewer can check the diff mechanically.

| Old | New | Notes |
|---|---|---|
| `Acumyn` | `Axcion` | Prose, copy, comments |
| `ACUMYN` | `AXCION` | Constants, SQL comments, headings |
| `acumyn` | `axcion` | Identifiers, slugs, logger names, file names |
| `acumyn.io` | `axcion.io` | |
| `www.acumyn.io` | `www.axcion.io` | Canonical marketing host (D3) |
| `app.acumyn.io` | `app.axcion.io` | Workspace finder |
| `api.acumyn.io` | `api.axcion.io` | API |
| `admin.acumyn.io` | `admin.axcion.io` | Operator console |
| `mail.acumyn.io` | `mail.axcion.io` | Resend sending subdomain |
| `hello@mail.acumyn.io` | `hello@mail.axcion.io` | `MAIL_FROM` |
| `hello@acumyn.io` | `hello@axcion.io` | Public contact — **now a real inbox**, see §3.3 |
| `{slug}.acumyn.io` | `{slug}.axcion.io` | Workspace hosts |
| `Acumyn Books` / `Acumyn Binder` | `Axcion Books` / `Axcion Binder` | Module names |
| `Acumyn support` / `Acumyn staff` / `Acumyn operator` | `Axcion …` | |

**Do not** change: the Railway project name (`glorious-wholeness`), the Railway service
names (`executive_dashboard_v1`, `zippy-cat`), the git repository name, the SQLite dev
filename (`command_center.db`), or `frontend/package.json`'s `"name"` field
(`spring-command-center-web` — already stale, and out of scope).

---

## Phase 0 — Pre-flight

Nothing below is reversible cheaply once Phase 9 starts. Do all of this first.

- [ ] **0.1** Take a Postgres backup. Railway → `Postgres` → Backups → create one
      manually, and confirm it completed. Note the backup id here: `____________`
- [ ] **0.2** Record the current Railway variable set for both services so a rollback has
      something to restore from:
      ```bash
      railway variables --service executive_dashboard_v1 > ~/rebrand-backup/api-vars.txt
      railway variables --service zippy-cat > ~/rebrand-backup/web-vars.txt
      ```
      **These files contain live secrets.** Keep them off the repo and delete them after
      Phase 10. `.gitignore` does not cover `~`.
- [ ] **0.3** Confirm access to every console that Phase 3 touches, *before* starting:
      GoDaddy (both domains), Railway, Resend, Google Cloud Console, Google Workspace
      admin, Intuit Developer, Stripe, Recall.ai, Meta for Developers, Cloudflare (R2
      only), Anthropic Console.
- [ ] **0.4** Confirm `axcion.io` is in the same GoDaddy account and that Google Workspace
      verification on it has completed (the MX records are live and mail is flowing).
      Send a test message to `hello@axcion.io` and confirm receipt. This is a hard
      dependency for §3.3.
- [ ] **0.5** Confirm the working tree is clean and on `main`, and that `alembic heads`
      prints exactly **one** head:
      ```bash
      cd backend && python -m alembic heads
      ```
      *(A second head crash-loops Railway on deploy and takes production down. This has
      happened before. Check it now and again at Phase 8.4.)*
- [ ] **0.6** Capture a baseline: full test suite green, and all five bundles build.
      ```bash
      cd backend && python -m pytest -q
      cd ../frontend && npm run build:all
      ```
      Record the test count here so Phase 8 can compare: `____ passed`

---

## Phase 1 — DNS for `axcion.io` (GoDaddy)

Goal: `axcion.io` resolves to Railway for every host the product uses, with valid
certificates, **while `acumyn.io` is still live**. Nothing user-facing changes in this
phase.

Order matters: Railway will not issue a certificate until the CNAME resolves, and
Railway gives you the CNAME *target* only after you add the domain to the service. So
Phase 1 and Phase 2.1 interleave — add the domain in Railway, then create the record
here, then wait for Railway to report the domain healthy.

### 1.1 Records to create

At GoDaddy → `axcion.io` → DNS → Records. Leave the Google Workspace MX records alone.

| Type | Name | Value | Purpose |
|---|---|---|---|
| CNAME | `*` | `4py3r2q2.up.railway.app` | **Every** axcion.io host — `www`, `app`, `admin` and every workspace |
| CNAME | `_acme-challenge` | `4py3r2q2.authorize.railwaydns.net` | **Wildcard certificate issuance.** Without it the `*.axcion.io` cert cannot be issued or renewed. |
| TXT | `_railway-verify` | `railway-verify=6a5980e874a3c30913f7f62a369063236ef702d38d05945a6a8454b3679c0088` | Wildcard domain ownership |
| CNAME | `api` | `klglty5y.up.railway.app` | API |
| TXT | `_railway-verify.api` | `railway-verify=e461291159cb1f19b369e7485e3d646a81bc3ab04a6ba83adae3f6d5fbf153a5` | API domain ownership |
| Forwarding | `@` (apex) | `https://www.axcion.io`, 301, masking **off** | GoDaddy cannot CNAME an apex (D3) |

**Delete:** the pre-existing `CNAME www → axcion.io.` (GoDaddy's default parking). An explicit
record always beats a wildcard, so while it exists `www.axcion.io` never reaches Railway.
Deleting it is what makes the single `*` record serve `www`.

**No `www` CNAME is created.** The original draft of this spec said to add one, mirroring the
acumyn.io setup. That was wrong — see §2.1.

- [x] **1.1** Created the four CNAMEs/TXTs above. *(done 2026-09-21)*
- [x] **1.2** Apex forwarding → `https://www.axcion.io`, Permanent (301), masking off.
      *(done — GoDaddy's UI reports "Not set up" until reloaded; verify by reloading, not by
      reading the panel straight after saving.)*
- [x] **1.3** Google Workspace records verified untouched: 5 Google MX, the
      `google-site-verification` TXT, `google._domainkey` DKIM, and SPF via GoDaddy's
      `dc-aa8e722993._spfm` indirection. `_dmarc` is GoDaddy's default
      (`p=quarantine; adkim=r; aspf=r`) — **relaxed alignment, so Resend's DKIM on
      `mail.axcion.io` will align with the apex.** Nothing to change for Phase 3.

### 1.2 Records for Resend (added in Phase 3.3, listed here for completeness)

Resend will supply exact values when `mail.axcion.io` is added as a domain. Expect:

| Type | Name | Purpose |
|---|---|---|
| MX | `mail` | Bounce and complaint handling for the sending subdomain |
| TXT | `mail` | SPF for the sending subdomain |
| TXT | `resend._domainkey.mail` | DKIM |
| TXT | `_dmarc` | DMARC policy (apex-level; may already exist from Workspace setup) |

> **The subdomain MX does not conflict with Workspace.** `mail.axcion.io` is a separate
> DNS name from `axcion.io`. Google's MX stays on the apex and keeps `hello@axcion.io`
> working; Resend's MX sits on `mail.` and handles bounces for outbound. Confusing these
> two is the most common way this step goes wrong.

### 1.3 Verification gate — **PASSED 2026-09-21**

Every host resolves through Railway's edge (`69.46.46.x`) with `tls_verify=0`:

| Host | HTTP | TLS | Serves today |
|---|---|---|---|
| `www.axcion.io` | 200 | ok | dashboard *(see note)* |
| `app.axcion.io` | 200 | ok | dashboard *(see note)* |
| `admin.axcion.io` | 200 | ok | dashboard *(see note)* |
| `springb.axcion.io` | 200 | ok | dashboard ✓ |
| `utah-life.axcion.io` | 200 | ok | dashboard ✓ |
| `api.axcion.io` | 404 at `/`, **200 `{"ok":true}` at `/health`** | ok | the API ✓ |
| `axcion.io` | 301 → `https://www.axcion.io/` | ok | apex forwarding ✓ |

All six `acumyn.io` equivalents re-checked and unchanged.

> **`www`, `app` and `admin` on axcion.io serve the DASHBOARD, not the marketing site,
> finder or operator console.** That is correct right now and not a fault: Caddy's
> `MARKETING_HOST` / `FRONTDOOR_HOST` / `OPERATOR_HOST` still name the `acumyn.io` hosts, so
> every axcion.io host falls through to the dashboard catch-all. Content routing flips in
> **Phase 9.5**, when those variables are set. Verified by page `<title>`: axcion.io hosts
> all return `Command Center`, while `www.acumyn.io` returns `Acumyn` and
> `admin.acumyn.io` returns `Acumyn Operator`. A 200 alone would have hidden this — check
> what a host *serves*, not just that it answers.

### 1.4 Original verification gate (reference)

- [ ] **1.4** Each of these resolves and serves a valid certificate:
      ```bash
      for h in www.axcion.io api.axcion.io app.axcion.io admin.axcion.io springb.axcion.io; do
        echo "--- $h"; curl -sS -o /dev/null -w '%{http_code} %{ssl_verify_result}\n' "https://$h/"
      done
      ```
      Expect HTTP 200/301/404 and `ssl_verify_result` `0` on every line. A TLS error here
      means the `_acme-challenge` CNAME is wrong or has not propagated — **stop and fix
      it.** Everything after this phase assumes working certificates.
- [ ] **1.5** `curl -sSI https://axcion.io/` returns a 301 to `https://www.axcion.io/`.

---

## Phase 2 — Railway

### 2.1 Domains

> **Railway allows only 2 custom domains per service on this plan.** `zippy-cat` was at its
> limit (`*.acumyn.io` + `www.acumyn.io`), so `*.axcion.io` was refused. Establishing this
> changed the plan — see the two corrections below.

**Correction 1: a Railway wildcard domain covers `www`, so no `www` domain is needed.**
Proved rather than assumed: `zzz-wildcard-test-9471.acumyn.io` — a host reachable only via
the wildcard — returned HTTP 200 with a valid certificate. So one slot serves `www`, `app`,
`admin` and every workspace. `axcion.io` needs **one** custom domain on `zippy-cat`, not two.

**Correction 2: each Railway custom domain has its own edge target.** `www.acumyn.io`
CNAME'd to `mxiw7ddz.up.railway.app`; the wildcard CNAMEs to `ob6vl1xb.up.railway.app`.
So removing a Railway domain **also requires deleting its DNS record**, or the host points
at a target Railway no longer routes and the site 502s. Deleting the record lets the `*`
record catch the host instead.

- [x] **2.1** `executive_dashboard_v1` → added `api.axcion.io`. `ACTIVE`, `Verified: yes`,
      certificate `VALID`.
- [x] **2.2** Freed a slot on `zippy-cat` by removing the `www.acumyn.io` custom domain.
      **No plan upgrade needed.**

      > **This step also caused the one outage of the rebrand, and the lesson is specific.**
      > Its GoDaddy `www` CNAME was deleted on the reasoning that the `*` record would catch
      > the host. A check immediately afterwards returned HTTP 200 and the step was recorded
      > as done. That 200 came from the *old* record still in cache; an hour later the cache
      > expired and `www.acumyn.io` went dark.
      >
      > **GoDaddy's wildcard DNS record does not cover `www`** — it special-cases that label,
      > confirmed authoritative against three independent resolvers. Railway's wildcard
      > *domain* does route `www`; the two layers behave differently and only the DNS one
      > bites. Fixed by giving `www` its own CNAME pointing at the wildcard's target
      > (`ob6vl1xb.up.railway.app`), which Railway's `*.acumyn.io` then serves.
      >
      > **A DNS change that relies on a fallback must be re-checked after the old TTL has
      > expired, not immediately.** An immediate check reads the thing you just removed.
- [x] **2.3** `zippy-cat` → added `*.axcion.io`. `ACTIVE`, `Verified: yes`, certificate
      `VALID` (wildcard DNS-01 took ~10 minutes after `_acme-challenge` resolved).
- [x] **2.4** Both new domains pinned to **target port 8080**. Railway created them with no
      port while the working domains were on 8080; an unpinned port is the kind of
      difference that routes fine in testing and fails under a real deploy. Check
      `railway domain list` shows a port on every row, not just `ACTIVE`.
- [x] **2.5** Every `acumyn.io` domain left in place except `www.acumyn.io` (above).
      Removed in Phase 10.

### 2.2 Variables — `executive_dashboard_v1` (API)

Set these at Phase 9, not now. Listed here so the change set is reviewable in one place.

| Variable | Current | New |
|---|---|---|
| `PLATFORM_DOMAIN` | *(unset — defaults to `acumyn.io`)* | `axcion.io` |
| `APP_PUBLIC_URL` | `https://app.acumyn.io` | `https://app.axcion.io` |
| `ALLOWED_ORIGINS` | `https://www.acumyn.io,https://acumyn.io` | `https://www.axcion.io,https://axcion.io` |
| `GOOGLE_REDIRECT_URI` | `https://api.acumyn.io/api/v1/auth/google/callback` | `https://api.axcion.io/api/v1/auth/google/callback` |
| `QBO_REDIRECT_URI` | `https://api.acumyn.io/api/v1/integrations/qbo/callback` | `https://api.axcion.io/api/v1/integrations/qbo/callback` |
| `MAIL_FROM` | `Acumyn <hello@mail.acumyn.io>` | `Axcion <hello@mail.axcion.io>` |
| `MAIL_REPLY_TO` | *(empty)* | `hello@axcion.io` — **newly possible**, see §3.3 |
| `R2_BUCKET` | `acumyn-storage` | **unchanged** — see §A2 |

`ALLOWED_ORIGINS` is belt-and-braces only: `config.allowed_origin_regex` already admits
every `https://*.PLATFORM_DOMAIN` by pattern, which is what makes provisioning zero-ops.
Setting `PLATFORM_DOMAIN` is what actually moves CORS.

### 2.3 Variables — `zippy-cat` (web)

| Variable | Current | New |
|---|---|---|
> **These four are load-bearing, not cosmetic.** Because none of them is set, Caddy runs on
> the defaults compiled into `frontend/Caddyfile` — so those defaults *are* production's
> content routing, and they were deliberately left naming `acumyn.io` when the rest of the
> repo was renamed. Renaming them in code would have cut the domain over on the next deploy
> of `web` for any reason at all, with none of the API, domain-row or mail work beside it.
> Setting these variables is therefore the actual cutover switch, and the moment they are set
> the code defaults stop being reachable.

| `MARKETING_HOST` | *(unset — Caddy default `www.acumyn.io`)* | `www.axcion.io` |
| `MARKETING_ALT_HOST` | *(unset — default `acumyn.io`)* | `axcion.io` |
| `FRONTDOOR_HOST` | *(unset — default `app.acumyn.io`)* | `app.axcion.io` |
| `OPERATOR_HOST` | *(unset — default `admin.acumyn.io`)* | `admin.axcion.io` |
| `VITE_API_BASE` | `https://api.acumyn.io/api/v1` | `https://api.axcion.io/api/v1` |

> `MARKETING_ALT_HOST=axcion.io` is inert while the apex is on GoDaddy forwarding — the
> apex never reaches Railway, so Caddy's 308 never fires. Set it anyway: it costs
> nothing and it is correct the day the apex does point here.

> `VITE_API_BASE` is compiled into all five bundles. Changing it triggers a full Railway
> rebuild of `zippy-cat`, not a restart. Budget the deploy time.

---

## Phase 3 — External services

Every one of these is a place the old name is written down **outside this repo**. Several
allow two values registered at once — use that, so the old host keeps working until
Phase 10.

### 3.1 Cloudflare R2 (object storage) — **no change**

Bucket `acumyn-storage` holds 51 Binder documents plus SOP documents, workspace logos and
marketing attachments. R2 buckets **cannot be renamed**; the only path is create-and-copy,
and a partial copy is silent data loss of customer documents.

- [ ] **3.1** Confirm `R2_BUCKET` stays `acumyn-storage`. Record it in the exception
      register (§A2). The bucket name is never shown to a user and never appears in a URL
      the product hands out.

### 3.2 Google Cloud — OAuth client and consent screen

**First check whether this exists at all.** `GOOGLE_CLIENT_ID` is unset in production,
so Google sign-in is currently disabled and there may be no client to migrate.

- [ ] **3.2** In Google Cloud Console, find the project holding the OAuth client (if any).
- [ ] **3.3** If a client exists: Credentials → the Web application client → **add**
      `https://api.axcion.io/api/v1/auth/google/callback` to Authorised redirect URIs.
      Leave the `acumyn.io` one in place until Phase 10.
- [ ] **3.4** OAuth consent screen → App name `Acumyn` → `Axcion`. Update the support
      email, the application home page (`https://www.axcion.io`), the privacy policy
      (`https://www.axcion.io/privacy`) and the terms (`https://www.axcion.io/terms`).
- [ ] **3.5** Authorised domains → add `axcion.io`.
- [ ] **3.6** If the consent screen is in "Testing", note that changing the app name may
      require re-verification if it is ever published. Not blocking today.

> The consent screen is what a user reads when they sign in. It says the platform's name
> to every workspace at once — this is the single most visible external rename.

### 3.3 Resend — the sending domain

A subdomain is a **separate domain** to an ESP. `mail.axcion.io` must be verified from
scratch; verifying `axcion.io` would not do it, and neither does owning the registrar
record.

- [ ] **3.7** Resend → Domains → Add `mail.axcion.io`.
- [ ] **3.8** Create the MX, SPF and DKIM records it gives you at GoDaddy (see §1.2).
- [ ] **3.9** Wait for Resend to show the domain **Verified**. Do not proceed on "pending".
- [ ] **3.10** Resend → API Keys. Keys are scoped per domain. Either confirm the existing
      key covers `mail.axcion.io`, or issue a new one. **Resend shows a key once** — a
      partial paste is the usual cause of `400 validation_error: API key is invalid`.
      Reissue rather than retry.
- [ ] **3.11** Update the Resend account/team name and any sender display name from
      Acumyn to Axcion.

**`MAIL_REPLY_TO` can finally be set.** `frontend/src/marketing/content.js:20` carries a
standing warning that `hello@acumyn.io` cannot receive mail — `acumyn.io` has no MX and
`mail.acumyn.io` is send-only. Google Workspace on `axcion.io` fixes that.
Set `MAIL_REPLY_TO=hello@axcion.io` (Phase 2.2) and **delete the warning comment** in
Phase 5. A password reset deliberately sets no per-message reply-to, so with neither set
those replies currently bounce; this closes that.

- [ ] **3.12** After Phase 9, verify a real send end to end:
      ```bash
      railway ssh --service executive_dashboard_v1 "python -m scripts.check_mail"
      railway ssh --service executive_dashboard_v1 "python -m scripts.check_mail you@example.com"
      ```
      With no address it reports the key's shape, never its value — enough to tell "not
      set" from "truncated on paste". With an address it sends one real message and prints
      Resend's exact answer.
- [ ] **3.13** **Accepted is not delivered.** Check `resend.com/emails` for the delivery
      event *and* check the inbox. First sends from a new domain are the most likely to be
      filtered. Reply to the message and confirm the reply lands in `hello@axcion.io`.

### 3.4 Intuit Developer (QuickBooks Online)

QBO is connected and in production use.

- [ ] **3.14** Intuit Developer → the app → Keys & credentials (Production) → **add**
      `https://api.axcion.io/api/v1/integrations/qbo/callback` to Redirect URIs. Keep the
      `acumyn.io` URI registered until Phase 10.
- [ ] **3.15** Update the app's display name, host domain, launch URL, EULA
      (`https://www.axcion.io/terms`) and privacy policy (`https://www.axcion.io/privacy`).
- [ ] **3.16** `frontend/Caddyfile` currently 308s the legacy `/privacy.html` and
      `/eula.html` paths to the marketing site, for exactly this reason (an app listing
      that still links the old files). Keep those redirects when the hosts change.

> **Existing QBO tokens are not affected.** A refresh does not send `redirect_uri`; only a
> fresh authorisation does. So the connection keeps syncing across the cutover, and the new
> URI matters the first time someone reconnects. Do not let that lull you — an unregistered
> URI fails at the worst moment, months later, with `invalid_redirect_uri`.

### 3.5 Stripe

- [ ] **3.17** Confirm again that `platform_billing_config`, `platform_subscription` and
      `platform_invoice` are all still **0 rows**. If they are, the `acumyn_tenant_id` /
      `acumyn_slug` metadata rename is a code-only change with no backfill.
- [ ] **3.18** If billing *has* been connected since this audit: the metadata rename needs
      a dual-read window. `platform_billing._tenant_for()` already falls back to the
      mirrored customer id when metadata is missing, so nothing breaks — but backfill the
      new key onto existing subscriptions and invoices before removing the old read.
- [ ] **3.19** Stripe → Settings → Business → update the public business name, support
      email, support URL and the statement descriptor customers see on a card charge.
- [ ] **3.20** Webhook endpoint: when billing is connected, register
      `https://api.axcion.io/api/v1/platform/webhooks/stripe`. It is documented in
      `DEPLOY.md` §Platform billing.
- [ ] **3.21** The three price lookup keys (`price_team_monthly`, `price_business_monthly`,
      `price_portfolio_monthly`) carry no brand name. **No change.**

### 3.6 Recall.ai

`RECALL_WEBHOOK_SECRET` is set and the endpoint is `POST /api/v1/webhooks/recall`.

- [ ] **3.22** Recall.ai dashboard → webhook endpoint → change to
      `https://api.axcion.io/api/v1/webhooks/recall`. If Recall allows two endpoints,
      register both and remove the old at Phase 10; if not, change it during the Phase 9
      window and accept a short gap.
- [ ] **3.23** `RECALL_BOT_NAME` is `beCollective — Call Notetaker` — a customer-facing
      name, not a platform one. **No change.**

### 3.7 Meta for Developers

The Meta Ads integration uses a long-lived token and has no OAuth redirect, so there is
no callback to re-register. The app's *metadata* still names the old brand.

- [ ] **3.24** Meta app → Settings → Basic: display name, App Domains (`axcion.io`),
      Privacy Policy URL, Terms of Service URL, contact email.
- [ ] **3.25** The app is on the **Development** access tier (~100 calls/hour) and
      Advanced Access has not been requested. Renaming the app mid-review would restart
      it — so if an Advanced Access request is in flight, **rename first, then submit**.

### 3.8 Everything else — verify, probably no change

- [ ] **3.26** Sisu, Follow Up Boss, GoHighLevel, Arive: all outbound, API-key or
      token-based, with no callback into this app. Confirm none has a registered webhook
      pointing at `api.acumyn.io`.
- [ ] **3.27** Anthropic Console: the API key is not brand-scoped. Rename the workspace or
      key label if it says Acumyn. Cosmetic.
- [ ] **3.28** GoDaddy: confirm `acumyn.io` auto-renew is **on** until Phase 10 completes,
      then decide separately whether to let it lapse. Letting a retired domain expire hands
      it to whoever buys it next, along with any residual trust in old links.

---

## Phase 4 — Backend code

One commit per numbered group, straight to `main`.

### 4.1 Configuration defaults

| File | Line | Change |
|---|---|---|
| `backend/app/config.py` | 221 | `PLATFORM_DOMAIN` default `"acumyn.io"` → `"axcion.io"` |
| `backend/app/config.py` | 211 | `MAIL_FROM` default → `"Axcion <hello@mail.axcion.io>"` |
| `backend/app/config.py` | 59, 64–71, 102–107, 146, 153 | Comment prose |
| `backend/.env.example` | throughout | Every `acumyn.io`, `Acumyn`, and the `hello@mail.` address |

- [ ] **4.1** Apply. `PLATFORM_DOMAIN` is the single value that moves tenant-host
      resolution, the CORS origin regex, and `{slug}.` link building. Get it right and
      most of the backend follows.

### 4.2 Stored values and wire contracts — the careful ones

These are read back by something that already holds the old value.

| File | Line | Old | New | Migration needed |
|---|---|---|---|---|
| `backend/app/routers/users.py` | 81 | `TYPEFACES = ("acumyn", …)` | `("axcion", …)` | **Yes** — 6.1 |
| `backend/app/routers/users.py` | 100 | `or "acumyn"` | `or "axcion"` | ↑ |
| `backend/app/services/wtd_playbook.py` | 27 | `FORMAT = "acumyn.wtd-playbook"` | `"axcion.wtd-playbook"` | **Yes** — 6.2 |
| `backend/app/services/wtd_playbook.py` | 650 | `Literal["acumyn.wtd-playbook"]` | matching literal | ↑ |
| `backend/app/routers/platform.py` | 1559 | `+acumyn-support@` | `+axcion-support@` | **Yes** — 6.3 |
| `backend/app/routers/platform.py` | 1589 | `"(Acumyn support)"` | `"(Axcion support)"` | ↑ |
| `backend/app/routers/platform.py` | 991, 998, 1008 | `scope` value `"acumyn"` | `"axcion"` | **No — but see below** |
| `backend/app/services/ads_funnel.py` | 265–285 | rung `zone` value `"acumyn"` | `"axcion"` | **No — but see below.** Not in this spec's first draft; see §1.5 |
| `backend/app/services/wtd_playbook.py` | 27, 662 | export `format` `"acumyn.wtd-playbook"` | `"axcion.wtd-playbook"` | **Impossible.** The value lives in exported FILES outside the product; `_Bundle` accepts both on import for ever, exports emit the new one. See §1.5 |
| `backend/app/routers/platform.py` | 1427, 1445, 1502 | `acumyn_tenant_id`, `acumyn_slug` | `axcion_…` | No (0 Stripe rows) |
| `backend/app/services/platform_billing.py` | 167, 313, 321 | `acumyn_tenant_id` | `axcion_tenant_id` | No (0 Stripe rows) |
| `backend/app/routers/platform.py` | 1751, 1771 | `acumyn-workspace-metadata/1`, `acumyn-{slug}-…json` | `axcion-…` | No (export format, write-only) |

- [ ] **4.2** Apply the table above.

> **`scope=acumyn` is a live wire contract between two separately-deployed services.**
> `frontend/src/operator/views/Fleet.jsx:111` calls `api.audit({ scope: "acumyn" })` and
> `Audit.jsx:75,97` branch on the response's `scope` field. The operator console ships on
> `zippy-cat`; the API ships on `executive_dashboard_v1`. They deploy at different times
> from the same commit, so there **is** a window where one side is new and the other old.
>
> **Both wire contracts are handled the same way, and the alias lives on the READER.** The
> two services deploy from one commit but not at one instant, so neither order is safe to
> assume. Rather than sequencing the deploy, each reader accepts both spellings:
>
> | Value | Written by | Read by | Alias added to |
> |---|---|---|---|
> | `scope` | the API (`platform.py`) + the console (sends it) | both | **both** — API coerces `acumyn`→`axcion` on input; `Audit.jsx` `isOperatorTrail()` on output |
> | `zone` | the API (`ads_funnel.py`) | `Funnel.jsx` | the browser — `ours()` |
>
> Both aliases are removed at **Phase 10.3**, and both carry a comment saying so.
>
> The API's input coercion:
> ```python
> if scope not in ("all", "acumyn", "axcion", "tenants"):
>     raise HTTPException(400, "scope must be all, axcion or tenants")
> if scope == "acumyn":
>     scope = "axcion"          # transitional; remove after Phase 10
> ```
> and emit only `"axcion"` in the response. Deploy the **API first**, the console second.
> Remove the alias at Phase 10.3.

### 4.3 Identity and second factor

- [ ] **4.3** `backend/app/security.py:120` — `TOTP_ISSUER = "Acumyn"` → `"Axcion"`.

> **This does not break anyone's authenticator.** The issuer is a display label baked into
> the QR code at enrolment time; the secret is unchanged and existing codes keep
> validating. But every already-enrolled user will keep seeing "Acumyn" in their
> authenticator app forever unless they re-enrol. Only Binder's step-up uses TOTP, and the
> enrolled population is small — accept it, and mention it in the Phase 10 announcement.

### 4.4 Mechanical prose and identifiers

- [ ] **4.4** `backend/app/services/mail_templates.py` — ~30 user-visible strings
      (invites, resets, workspace-ready, support access, integration-stalled, the
      workspace finder's three variants, the invoice mail). **Every one of these is read
      by a customer.** Review the diff by reading the rendered copy, not just the diff.
- [ ] **4.5** `backend/app/services/roles.py:203` — `BRAND_DEFAULTS` override
      `display_name` / `product_name` `"Acumyn"` → `"Axcion"`. This is what an unresolved
      host and every un-branded surface wears.
- [ ] **4.6** `backend/app/services/sop_drafting.py:23` — logger name
      `"acumyn.sop_drafting"` → `"axcion.sop_drafting"`.
- [ ] **4.7** `backend/app/services/binder_storage.py:62` — local-fallback temp directory
      `acumyn_binder_storage` → `axcion_binder_storage`. Dev-only path; R2 is used in
      every deployment.
- [ ] **4.8** `backend/app/services/sunburst.py:58` — the `b"acumyn-sunburst"` signing-key
      fallback. Only reached when `APP_SECRET` is empty, which a deployed server refuses
      to start with. Rename it; note that doing so invalidates anything signed with the
      fallback, which in practice is nothing outside a laptop.
- [ ] **4.9** `backend/app/worker.py:360` and
      `backend/app/services/operator_audit.py:51` — the actor labels written on **new**
      audit rows (`"Acumyn (expiry)"`, `"… (Acumyn)"`). Change the label being written.
      **Do not rewrite existing rows** — see §A2.
- [ ] **4.10** Remaining prose across `backend/app/**` (39 files): `models.py`, `deps.py`,
      `tenancy.py`, `security.py`, the routers, `services/`, `alembic/versions/*`. Comment
      and docstring text only.
- [ ] **4.11** `backend/scripts/tenant_domains.py` (lines 15, 46, 80, 86, 108) and
      `backend/scripts/check_mail.py` — help text and error messages naming the domain.

### 4.5 Migration file prose

- [ ] **4.12** `0013`, `0015`, `0049`, `0050`, `0052`, `0069`, `0070`, `0071` — docstring
      prose only. **Do not touch any migration's executable body**; these have already run
      in production and their logic is history.

---

## Phase 5 — Frontend code

### 5.1 The brand module — file rename plus 14 import sites

- [ ] **5.1** `git mv frontend/src/brand/acumyn.jsx frontend/src/brand/axcion.jsx`
- [ ] **5.2** Rename its exports: `ACUMYN_SITE` → `AXCION_SITE` (and its value to
      `"https://www.axcion.io"`), `AcumynMark` → `AxcionMark`, `AcumynLockup` →
      `AxcionLockup`. Leave `CORE`, `CADET`, `NEUTRAL`, `TYPE` and all geometry constants
      **exactly as they are** (D1).
- [ ] **5.3** Update all 14 importers:
      `auth/AuthShell.jsx`, `brand/PoweredBy.jsx`, `marketing/Sequence.jsx`,
      `marketing/Site.jsx`, `marketing/pages/{About,Features,FrontDoor,Home,Legal,Pricing}.jsx`,
      `marketing/ui.jsx`, `operator/App.jsx`, `operator/primitives.jsx`,
      `operator/tokens.js`.
- [ ] **5.4** `frontend/src/brand/PoweredBy.jsx` — `PoweredByAcumyn` → `PoweredByAxcion`,
      and the rendered string `"Acumyn"` → `"Axcion"`. Update its three consumers:
      `CommandCenter.jsx:28,1439`, `ShareDesk.jsx:9,201`, `auth/AuthShell.jsx:26,109`.

### 5.2 Palette, type and assets

- [ ] **5.5** `frontend/src/palette.js` — `ACUMYN` → `AXCION` (line 63), `ACUMYN_TYPE` →
      `AXCION_TYPE` (98), `seedsFromAcumyn` → `seedsFromAxcion` (148, 158, 168) and its
      callers in `Appearance.jsx:17,147,175,241`. **Colour values unchanged** (D1).
- [ ] **5.6** `frontend/src/palette.js:314` — `const BOKEH = "/brand/acumyn/"` →
      `"/brand/axcion/"`, and `git mv frontend/public/brand/acumyn frontend/public/brand/axcion`
      (three bokeh JPEGs). **Check the rename and the constant land in the same commit** —
      split across two and every un-branded workspace's sign-in loses its background.
- [ ] **5.7** `frontend/src/typefaces.js` — pairing key `acumyn:` → `axcion:` (25),
      `label: "Acumyn"` → `"Axcion"` (26), `DEFAULT_PAIRING = "acumyn"` → `"axcion"` (55).
      **Must ship with migration 6.1 in the same deploy**, or a workspace with the stored
      value `acumyn` silently falls through to `PAIRINGS[DEFAULT_PAIRING]` and re-renders
      in a different typeface.

### 5.3 Copy and hosts

- [ ] **5.8** `frontend/src/marketing/content.js` — `signupUrl`, `contactEmail`, every
      `acumyn.io` host, the six page `<title>`s, the sign-in FAQ answer naming
      `your-team.acumyn.io` and `app.acumyn.io`, and all body copy.
      **Delete the `hello@acumyn.io CANNOT RECEIVE MAIL YET` warning block at lines 20–22**
      — Google Workspace on `axcion.io` makes it false (§3.3).
- [ ] **5.9** `frontend/src/marketing/legal.js` — every `Acumyn` in the Privacy Policy and
      Terms, plus the host list in the opening paragraph
      (`acumyn.io, app.acumyn.io … your-team.acumyn.io`). The `entity` and `contactEmail`
      facts are still **unfilled** (`value: ""`); this is the moment to fill them with the
      real operating company name and `hello@axcion.io`.
- [ ] **5.10** `frontend/src/marketing/pages/*.jsx` — remaining copy, including
      `FrontDoor.jsx:118` ("Back to acumyn.io") and the two `© Acumyn` footers
      (`FrontDoor.jsx:181`, `Site.jsx:206`).
- [ ] **5.11** `frontend/src/console/constants.js` — `poweredBy: "Powered by Acumyn"`
      (247) and `DEFAULT_TENANT_HOST = "utah-life.acumyn.io"` (516).
- [ ] **5.12** `frontend/index.html`, `frontend/marketing/index.html` (title + meta
      description), `frontend/operator/index.html` (`Acumyn Operator`),
      `frontend/console/index.html`, `frontend/intranet/index.html`.
- [ ] **5.13** Remaining `frontend/src/**` prose (57 files): `CommandCenter.jsx:1088–1093`
      (the demo user `demo@acumyn.io` and the `Acumyn` brand fallback), `Settings.jsx:903`
      ("Acumyn Books"), `SalesDeskSection.jsx`, `ForumView.jsx`, `auth.jsx`,
      `intranet/IntranetApp.jsx:716,1086` (two "connect it in the Acumyn dashboard"
      messages), `intranet/ui.css`, `ads/*`, `books/*`, `console/pages/*`,
      `operator/**`, `ulrg/ShareScorecard.jsx`.
- [ ] **5.14** `frontend/scripts/gen_favicons.py` and `frontend/scripts/console-smoke.mjs`
      — prose and any hardcoded host. **Do not re-run `gen_favicons.py`**; the mark is
      unchanged (D1) and the four PNGs stay byte-identical.
- [ ] **5.15** `frontend/src/marketing/assets/GENERATOR.py`,
      `frontend/src/marketing/Sequence.jsx`, `frontend/mockups/*` — prose.

### 5.4 Caddy

- [ ] **5.16** `frontend/Caddyfile` — the four host defaults (`MARKETING_ALT_HOST`,
      `MARKETING_HOST`, `FRONTDOOR_HOST`, `OPERATOR_HOST`) and all comment prose.
      **`backend/tests/test_operator_console.py:462` asserts the literal string**
      `@operator host {$OPERATOR_HOST:admin.acumyn.io}` — that test must change in the
      same commit or the suite goes red.
- [ ] **5.17** Keep the `/privacy.html` and `/eula.html` 308s, retargeted at
      `www.axcion.io`. They exist for external listings that still link the old files
      (§3.16).
- [ ] **5.18** Re-check ordering with `caddy adapt`, not by reading: the operator-host
      handle must stay ahead of `/intranet/*` and `/console/*`, and the dashboard
      catch-all must still sort last.

---

## Phase 6 — Data migrations

New Alembic revisions on top of `0078_sop_suggestions`. **One head. Always.**

> **Shipped as ONE revision, `0079_rebrand_stored_names`, not three.** It is one logical
> change, and atomicity is worth more here than granularity: nobody wants the typeface half
> applied and the support account not because of a unique-constraint collision. One revision
> is also one head, and a fork in this history crash-loops the API on deploy. Covered by
> `backend/tests/test_migration_0079_rebrand.py`, including the up-then-down round trip —
> a downgrade that has never run is not a rollback plan.

- [x] **6.0** *(run against production 2026-09-21 — see §1.4 for the result)* Write `backend/scripts/scan_rebrand.py` — enumerate every `text`,
      `character varying`, `json` and `jsonb` column from `information_schema.columns`,
      count rows matching `ilike '%acumyn%'` in each, and print `table.column: n rows`.
      Enumerate from the schema, never from a list anyone typed. Run it against production
      read-only and **reconcile its output against 6.1–6.4 before writing any migration.**
      Anything it finds that is not in this spec is a gap in this spec.

- [ ] **6.1** `0079_rebrand_typeface` — `tenant.config -> 'brand' ->> 'typeface'`:
      `'acumyn'` → `'axcion'`. Known scope: **1 row** (`testrealty`). Rows with `NULL`
      need nothing — they resolve through the new default. Write a working `downgrade()`.
- [x] **6.2** ~~`0080_rebrand_playbook_format`~~ — **cancelled, and the reason is the point.**
      `format` is never stored: it belongs to the export envelope, and the importer keeps only
      `bundle["content"]`. Confirmed against production — the single playbook row has no
      `format` key at all. This step would have matched zero rows for ever while reading like
      real work. The genuine exposure is an exported file, which no migration can reach; it is
      handled in `wtd_playbook._Bundle` (§1.5).
- [ ] **6.3** `0081_rebrand_support_account` — the support `User`:
      `email` `…+acumyn-support@…` → `…+axcion-support@…`, `name`
      `'Connor Leiva (Acumyn support)'` → `'(Axcion support)'`. Known scope: **1 row**.
      `User.email` is unique per tenant — assert no collision before updating.
- [ ] **6.4** Stripe metadata: **no migration.** 0 rows across
      `platform_billing_config`, `platform_subscription`, `platform_invoice` (§3.17).
      Record the no-op in the migration comment so the next reader knows it was
      considered rather than missed.
- [ ] **6.5** `domain` rows — **not a migration.** Five hostnames change, and
      `scripts/tenant_domains.py` refuses the mistakes that raw SQL allows. Run at
      Phase 9, after DNS is verified:
      ```bash
      for t in springb utah-life acmerealty testrealty testrealty2; do
        railway ssh --service executive_dashboard_v1 \
          "python -m scripts.tenant_domains --tenant $t --add $t.axcion.io --primary"
      done
      ```
      Then confirm each with `--list`. **Leave the old rows in place through Phase 9** —
      they are the rollback. Remove them at Phase 10.
- [ ] **6.6** Verify string lengths against the real column, not against SQLite.
      `Domain.hostname` is `String(255)` and `User.email` is `String(255)`; the new values
      are all *shorter* than the old, so this is clear — but check it, because an
      over-long string passes SQLite and the whole test suite and then truncation-errors
      on Postgres.
- [ ] **6.7** `cd backend && python -m alembic heads` → **exactly one line.** A fork
      crash-loops the API on deploy and takes production down.
- [ ] **6.8** Test the migration against a *copy* of production, not against a fresh
      SQLite database. Restore the Phase 0.1 backup locally, run `alembic upgrade head`,
      then `alembic downgrade -3` and back up again.

---

## Phase 7 — Documentation

- [ ] **7.1** `DEPLOY.md` (23 hits) — §Transactional email, §Custom domains,
      §"Acumyn's own hosts", §Platform billing. The GoDaddy apex explanation stays true
      under D3 and should be **updated to name `axcion.io`, not deleted**.
- [ ] **7.2** `OPERATOR-CONSOLE.md` (23 hits) — the console's own name and `admin.` host.
- [ ] **7.3** `README.md`, `utah-life-production-handoff-spec.md` (5),
      `docs/qbo-cutover-runbook.md` (2), `docs/books-coa-phase2-handoff.md` (2).
- [ ] **7.4** The remaining root specs (`FUB-FOLLOW-UPS-SPEC.md`, `SOP-LIBRARY-SPEC.md`,
      `WHOS-WHO-WIN-THE-DAY-SPEC.md`, `LAUNCH-TAB-UI-BRIEF.md`, `docs-forum-spec.md`) —
      only where they name the platform. These are historical build records; renaming the
      product inside a record of what was built on a given date is optional, and arguably
      wrong. Decide once and be consistent.
- [ ] **7.5** Add a short "Renamed from Acumyn, 2026-09" note at the top of `DEPLOY.md`,
      so someone reading a stale bookmark or an old log line can orient.

---

## Phase 8 — Verify before deploying

- [ ] **8.1** `git grep -il acumyn` returns **only** the files in the exception register
      (§A2). Anything else is a miss.
- [ ] **8.2** `cd backend && python -m pytest -q` — green, and the count matches the
      Phase 0.6 baseline. ~32 test files change; `test_operator_console.py` (23 hits),
      `test_tenancy.py` (14) and `test_brand_rules.py` (9) carry the most.
- [ ] **8.3** `cd frontend && npm run build:all` — all five bundles build with no
      unresolved import. The brand-module rename (5.1) is the likely failure here.
- [ ] **8.4** `cd backend && python -m alembic heads` → one head. Again. It is the
      cheapest check in this document and the most expensive one to skip.
> **The backend suite reads frontend source.** `test_startup_checks.py` evaluates
> `seedsFromAxcion()` and cross-checks the typeface name list across the two languages;
> `test_operator_console.py` reads `frontend/Caddyfile`; `test_storage_config.py` reads
> `backend/.env.example`; and a test asserts `gen_favicons.py`'s geometry against
> `brand/axcion.jsx`. So "the frontend is safe to edit while the backend suite runs" is
> **false** — editing `palette.js` and `typefaces.js` mid-run produced two failures that
> looked like real regressions and were contamination. Let a run finish before editing
> either tree, and re-run anything that crosses the boundary.
>
> Baseline before any rename work: **1899 passed**.

- [ ] **8.5** **Verify on the production build, not the dev server.** Dev-verified and
      prod-broken has happened twice before. In a fresh terminal:
      ```bash
      cd frontend && npm run build:all && npm run preview
      ```
      Then walk each surface at `*.localhost`:
      - [ ] dashboard sign-in — the plate, the mark, "Powered by Axcion", the Privacy and
            Terms links
      - [ ] marketing home, features, about, pricing, privacy, terms — titles and footers
      - [ ] workspace finder — the "Back to axcion.io" link and the `© Axcion` footer
      - [ ] operator console — tab title, the audit Scope chip reading "Axcion"
      - [ ] intranet portal — the two "connect it in the Axcion dashboard" messages
      - [ ] Appearance panel — "Use Axcion's" resets to the same colours as before
      - [ ] a share link — the Powered-by footer
- [ ] **8.6** Click through it rather than reading the diff. Most of what breaks in a
      rename of this size is invisible to review and obvious on first use.
- [ ] **8.7** Commit to `main` in reviewed batches (4.x, 5.x, 6.x, 7.x). Deploys come from
      `main`.

---

## Phase 9 — Cutover

Both domains are live and serving through this phase. Pick a low-traffic window.

- [ ] **9.1** Deploy the **API** (`executive_dashboard_v1`) first — it carries the
      transitional `scope` alias (§4.2) that lets the old console keep working.
- [ ] **9.2** Set the API variables from §2.2. Railway redeploys on a variable change.
- [ ] **9.3** Watch the boot: `railway logs --service executive_dashboard_v1`. Confirm
      `in-API scheduler started` and no migration error. *(`railway logs` returns the last
      500 lines only — check promptly.)*
- [ ] **9.4** Add the five new `domain` rows (§6.5). Confirm each with `--list`.
      **Leave the `acumyn.io` rows in place.**
- [ ] **9.5** Set the `zippy-cat` variables from §2.3 and let it rebuild (`VITE_API_BASE`
      is build-time — this is a full rebuild, not a restart).
- [ ] **9.6** Smoke every host against production:
      | Host | Expect |
      |---|---|
      | `https://www.axcion.io` | marketing home, title "Axcion" |
      | `https://axcion.io` | 301 → `www.axcion.io` |
      | `https://app.axcion.io` | workspace finder |
      | `https://admin.axcion.io` | operator console sign-in |
      | `https://api.axcion.io/api/v1/health` | 200 |
      | `https://springb.axcion.io` | dashboard sign-in, "Powered by Axcion" |
      | `https://utah-life.axcion.io/intranet/` | portal |
- [ ] **9.7** Sign in to a workspace on the new host. Confirm the session holds, the
      brand renders, and the typeface is unchanged from before the cutover.
- [ ] **9.8** Send a real invite from the new host. Confirm the email arrives from
      `hello@mail.axcion.io`, that its link points at `{slug}.axcion.io`, that the link
      completes, and that **a reply reaches `hello@axcion.io`**.
- [ ] **9.9** Run `scripts/check_mail` against production (§3.12) and check the Resend
      delivery event (§3.13).
- [ ] **9.10** Operator console: open the audit view and confirm the Scope chip reads
      "Axcion" and the trail still loads.
- [ ] **9.11** Open support access to a workspace and confirm the new
      `+axcion-support@` account is created, the owners are emailed, and both audit trails
      record it.
- [ ] **9.12** Confirm the QBO connection is still syncing (its token survives — §3.4) and
      that the Sisu / FUB / GHL / Arive syncs ran on schedule after the redeploy.
- [ ] **9.13** Soak for **at least 48 hours** with both domains live before Phase 10.

---

## Phase 10 — Retire `acumyn.io`

Only after Phase 9 has soaked clean. This is the irreversible step.

- [ ] **10.1** Announce it to every workspace owner: the new addresses, the date old links
      stop working, and the note that an already-enrolled authenticator will keep showing
      "Acumyn" until re-enrolled (§4.3).
- [ ] **10.2** Re-send the 12 live share links from their new hosts before the old ones
      die. The tokens are unchanged — only the host in the URL differs.
- [ ] **10.3** Remove the transitional `scope == "acumyn"` alias (§4.2) and deploy.
- [ ] **10.4** Remove the five `acumyn.io` `domain` rows:
      ```bash
      railway ssh --service executive_dashboard_v1 \
        "python -m scripts.tenant_domains --tenant <slug> --list"
      ```
      then remove each old hostname once the new one is confirmed primary.
- [ ] **10.5** Remove the old redirect URIs: Google OAuth (§3.3), Intuit (§3.14), and the
      Recall endpoint if both were registered.
- [ ] **10.6** Railway → `zippy-cat` → remove `*.acumyn.io` and `www.acumyn.io`.
      Railway → `executive_dashboard_v1` → remove `api.acumyn.io`.
- [ ] **10.7** GoDaddy → `acumyn.io` → remove the Railway CNAMEs. Point the apex and `www`
      at a 301 to `https://www.axcion.io` if you want old bookmarks to land somewhere;
      otherwise remove the forwarding too.
- [ ] **10.8** Resend → remove `mail.acumyn.io` once no send has used it for a full
      billing cycle.
- [ ] **10.9** Delete the Phase 0.2 variable backups. They contain live secrets.
- [ ] **10.10** Decide on the registration. **Renew `acumyn.io` for at least one more
      year.** A lapsed domain with residual trust in old invite and share links is worth
      more to someone else than the renewal fee is to you.
- [ ] **10.11** Re-run `python backend/scripts/scan_rebrand.py` and confirm the only
      remaining hits are the exception register (§A2).

---

## Phase 11 — Visual identity (deferred)

Stub, per D1. Nothing here is scheduled.

When the visual refresh happens, it touches: `frontend/src/brand/axcion.jsx` (the derived
mark geometry), `frontend/src/palette.js` (the Cadet and Neutral ramps, and the 30 token
slots), `frontend/src/typefaces.js` (the `axcion` pairing), `frontend/scripts/gen_favicons.py`
(re-run to regenerate the four PNGs), `frontend/public/brand/axcion/*` (the bokeh
artwork), and `frontend/src/marketing/assets/*`.

Two constraints carry forward from the current identity guide and should survive any
redraw: the three gaps stay equal, and the blade weight stays 12.5% of the artboard. Both
are derived rather than drawn precisely so they cannot drift.

**The hard part is not the code.** Every workspace that never chose its own colours
inherits the platform defaults, so changing them re-skins those workspaces without them
asking. Migrations `0049`, `0050` and `0052` are the scar tissue from the last time that
went wrong. Any future palette change needs the same grandfathering treatment: pin the
current values onto existing workspaces, and let only new ones start from the new defaults.

---

## Appendix A — Registers

### A1 Verification matrix

| Check | Phase | Command |
|---|---|---|
| DNS + TLS on all five hosts | 1.4 | `curl -w '%{http_code} %{ssl_verify_result}'` |
| Apex redirect | 1.5 | `curl -sSI https://axcion.io/` |
| Single Alembic head | 0.5, 6.7, 8.4 | `python -m alembic heads` |
| Backend suite | 8.2 | `python -m pytest -q` |
| All bundles build | 8.3 | `npm run build:all` |
| Prod-build walkthrough | 8.5 | `npm run build:all && npm run preview` |
| No stray hits | 8.1, 10.11 | `git grep -il acumyn` + `scan_rebrand.py` |
| Mail end to end | 9.8, 9.9 | `scripts.check_mail` + inbox + reply |
| Caddy host ordering | 5.18 | `caddy adapt` |

### A2 Exception register — what keeps the old name, and why

Each of these is a deliberate decision, not a miss. Phase 8.1 and Phase 10.11 check the
repo and the database against **this list**.

| Item | Where | Why it stays |
|---|---|---|
| `audit_log.actor_label` values reading `… (Acumyn)` | 10 production rows | **Rewriting an audit trail is the one change an audit trail exists to prevent.** These rows record what was done, by whom, under the name the platform had at the time. New rows get the new label (§4.9); old rows are history. This is the one place D4's "rename everything" should not apply. |
| `audit_log.detail` | 13 production rows | Same trail, same reasoning — the register originally named only `actor_label`, and the scan showed the name is in the JSON payload too: `{"by": "Acumyn (expiry)"}`, and the support account's old address on every support-access entry. Found by §6.0's scan, not by reading the code. |
| `platform_audit.detail` | 10 production rows | The operator's cross-tenant trail, carrying the same support-account address. Same reasoning. |
| `intranet_publish_batch.snapshot` | 4 production rows | `"inherited_from": "Acumyn dashboard"`, a **display string** from `console.py` frozen into a publish snapshot. Not in this spec at any point before the scan found it. It stays because the snapshot is an archival record of what the intranet looked like at that moment: `_batch()` never serializes it to a browser, and **nothing in the codebase reads it back** — verified, not assumed. `inherited_from` is recomputed per request and never persisted anywhere else, so even a future rollback restoring it changes nothing a user sees. |
| R2 bucket `acumyn-storage` | Cloudflare R2, `R2_BUCKET` | R2 buckets cannot be renamed. The only path is create-and-copy across 51 Binder documents plus SOP documents and workspace logos, where a partial copy is silent loss of customer files. The name is never shown to a user and never appears in a handed-out URL. |
| `platform_audit` historical rows | production | Same reasoning as the audit log. |
| Railway service names `executive_dashboard_v1`, `zippy-cat`; project `glorious-wholeness` | Railway | Never carried the brand. Renaming churns internal variable references (`RAILWAY_SERVICE_ZIPPY_CAT_URL`) for no gain. |
| `frontend/package.json` `"name": "spring-command-center-web"` | repo | Already stale, predates Acumyn, out of scope. |
| `RECALL_BOT_NAME` = `beCollective — Call Notetaker` | Railway | A customer-facing name, not a platform one. |
| Stripe price lookup keys | Stripe | Carry no brand name. |

### A3 Rollback

Rollback is cheap through Phase 9 and expensive after Phase 10.

**Through Phase 9** — both domains are live and both sets of `domain` rows exist:
1. Revert the Railway variables on both services from the Phase 0.2 backups.
2. Redeploy the previous commit on both services.
3. `alembic downgrade` the three rebrand revisions (6.1–6.3).
4. `acumyn.io` never stopped serving. Nothing user-facing was lost.

**After Phase 10** — DNS, domain rows and external redirect URIs are gone. Rollback means
re-doing Phases 1–3 in reverse against `acumyn.io`, with a DNS propagation delay and a
fresh certificate issuance in the middle. **Treat Phase 10 as the point of no return, and
do not start it until Phase 9 has soaked for 48 hours.**

### A4 Order dependencies

```
Phase 0 (pre-flight)
   ↓
Phase 1 (DNS) ⇄ Phase 2.1 (Railway domains)     ← interleaved
   ↓
Phase 3 (external services — dual-register where allowed)
   ↓
Phase 4 (backend) ─┬─ Phase 6 (migrations)      ← 5.7 and 6.1 ship together
Phase 5 (frontend) ─┘                             5.6 and the asset rename ship together
   ↓
Phase 7 (docs)
   ↓
Phase 8 (verify — prod build, not dev server)
   ↓
Phase 9 (cutover — API first, then web)
   ↓
   soak 48h
   ↓
Phase 10 (retire) ← irreversible
   ↓
Phase 11 (visual identity — unscheduled)
```
