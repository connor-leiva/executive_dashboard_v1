# Operator console — build record

The operator console is Axcion staff administering workspaces, served at `admin.axcion.io`.
It is built from `OPERATOR-CONSOLE-SPEC.md` (2026-09-17) and its design file
`axcion-operator-console.jsx`, in the spec's phase order. This file records what shipped,
every decision the spec left open, and every place the build departs from the spec because the
repo said something different. It is the first thing to read before changing the console.

Not to be confused with the **tenant console** (`frontend/src/console/`, `routers/console.py`),
which is each workspace's own team-portal admin.

## Where things live

| Concern | Path |
| --- | --- |
| Frontend entry | `frontend/operator/`, `frontend/vite.operator.config.js` |
| Frontend source | `frontend/src/operator/` |
| API | `backend/app/routers/platform.py`, mounted at `/api/v1/platform` |
| Fleet health derivation | `backend/app/services/fleet_health.py` |
| Provider rollup and incidents | `backend/app/services/fleet_rollup.py` |
| Sync jobs shared with the workspace's own buttons | `backend/app/services/sync_jobs.py` |
| The operator trail | `backend/app/services/operator_audit.py`, table `platform_audit` |
| Scheduled jobs and their heartbeats | `backend/app/services/jobs.py`, table `job_heartbeat` |
| Platform billing (Axcion's own Stripe) | `backend/app/services/platform_billing.py`; setup in `DEPLOY.md` |
| Tests | `backend/tests/test_operator_console.py`, `test_platform_operators.py`, `test_brand_rules.py` |
| Hosting | the existing `web` service; Caddy routes `OPERATOR_HOST` (default `admin.axcion.io`) |

## Phase status

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Operator frontend shell, Fleet and Workspaces, provisioning, suspend and resume | shipped |
| 2 | People counts, per-workspace panes, derived triage | shipped |
| 3 | Write actions | shipped |
| 4 | `platform_audit`, Audit view, System view | shipped |
| 5 | Stripe platform billing | shipped, off until connected |
| 6 | Support access, export, transfer ownership, delete | shipped |
| 6+ | Support access extended into the portal: view it as one of the workspace's people | shipped 2026-09-18 |

## What each phase shipped

**Phase 1.** The console at `admin.axcion.io`: sign-in, the ink shell, Fleet (tiles and the
"Needs you now" queue), Workspaces (search, filter, sort), New workspace, and a workspace's
Overview, Activity and Danger panes. Server side:

- `services/fleet_health.py` derives every workspace's state, reasons and signals on read. The
  spec puts derived triage in Phase 2; it moved here because the fleet list cannot honestly show
  a state without it, and deriving it in the browser first would have meant writing it twice.
- `GET /platform/plans` serves `plans.PLANS`, the gated modules, the platform domain and both
  reserved-host sets. The spec imagined a mirror in `tokens.js` held to `plans.py` by a test;
  serving the table removes the mirror instead.
- Each workspace row gains the applied plan and whether it was actually set (C4), people counts
  (C17), stale-source counts, frozen state, live share links and AI token use against the budget.
- Operator sessions refuse capability tokens, as tenant sessions already did.
- Provisioning refuses a slug that cannot be a hostname, a plan with more businesses than it
  allows (at creation, §11), and, from the console, a missing plan.
- Suspending needs a non-empty reason, recorded in the workspace's audit log and shown on the
  fleet list. A suspended workspace is left out of the scheduled sync; before this the tick
  synced every tenant regardless of status, so the console's "stops all scheduled syncs" was not
  yet true.

**Phase 2.** The fleet in one read: `GET /fleet` returns the rollup and the triage queue from
one pass over every workspace, so the tiles and the queue cannot disagree, and `/fleet/triage`,
`/fleet/providers` and `/fleet/incidents` serve the parts on their own. `services/fleet_rollup.py`
groups errors by cause: every connection erroring with the same provider and the same normalised
message is one incident, however many runs failed, with the known causes (an expired QuickBooks
grant, a rejected key, rate limiting, a provider outage) named with what clears them. A workspace
gains People, Sources, Modules, Usage and Access panes, each from its own read-only endpoint, and
the console gains the Incidents view.

- Modules come back in the spec's two groups. `gated` is exactly the tier's `extra_tabs`, each
  entry naming the lowest tier that includes it and carrying no switch; `PATCH` is refused with
  403 and says the plan decides. `own` always includes `portfolio`.
- Nothing returned carries a credential: no password hash, action token or second-factor secret,
  and a share link is listed without its token.
- Documents are counted, never sized (C14): `binder_document` has no byte column, so the Usage
  and Overview tiles use the unsourced treatment rather than a guessed figure.
- Usage reads every cap from `plans.PLANS`. A zero token budget is unlimited everywhere, and the
  fleet's token tile totals only the capped workspaces and says how many of the fleet that is.

**Phase 3.** The write actions. Every one is recorded in the workspace's own audit log, naming
the operator in `detail.by`, with an actor label ending "(Axcion)", so the customer can see what
Axcion did.

> Audit rows written before the 2026-09 rename end "(Acumyn)". They are deliberately left
> alone — an audit trail records what happened under the name the platform had at the time,
> and rewriting it is the one change it exists to prevent. Expect both labels in old trails.

- Sync now, for a workspace (`POST /tenants/{slug}/sync`) or one source
  (`/sources/{id}/sync`), on the same jobs the workspace's own Sync buttons run. Those jobs moved
  from the integrations router into `services/sync_jobs.py` so both surfaces call one path. A full
  sync is refused while another started in the last 15 minutes is still running, and both refuse
  a suspended or frozen workspace.
- Reconnect links (`/sources/{id}/reconnect-link`) and setup links (`/sources/setup-link`),
  emailed to the workspace's active owners and admins.
- Freeze and unfreeze syncs (`/freeze-syncs`, `/unfreeze-syncs`). A frozen workspace is skipped by
  the scheduled sync, the daily Sisu roster job and the ads funnel job, and the workspace's own
  Sync buttons answer 409. Who froze it, when and why appear wherever its state does.
- Per person: resend an invite, unlock a password lockout, send a password reset link. Resending
  every idle invite at once (`/people/resend-idle`) reads the same rule as the fleet's idle-invites
  row, so pressing it clears that row.
- Revoke every live share link (`/share-links/revoke-all`), after a confirmation that names how
  many links and which kinds.
- In the console, every triage button now does what its label says. People, Sources, Access and
  Danger carry their actions, and Workspaces gains row selection with bulk sync and bulk suspend
  (one reason, recorded on each workspace, with each refusal reported in the server's words).

**Phase 4.** The operator's own trail and the platform's health.

- `platform_audit` (migration `0069_platform_audit`). Every operator change now writes two rows
  through one function, `services/operator_audit.record`: the workspace's own audit log, as before,
  and `platform_audit`, with the operator, the workspace's id and slug, the reason and the address
  the change came from. `tenant_id` is not a foreign key, so the row outlives the workspace. Entries
  older than 400 days are deleted by a monthly job.
- `GET /audit` lists every change across the platform, newest first, in three scopes: Axcion staff
  (from `platform_audit`), workspace teams (from each workspace's audit log, sign-ins and
  second-factor checks left out because they are not changes), or both. An operator's change also
  sits in the workspace's log with no actor and is only read from `platform_audit`, so it is listed
  once. The Audit view pages through it, filters by scope and exports the rows on screen as CSV.
- `GET /system` reports the release (Railway's commit, branch and message), the migration heads in
  the deployed code against the version the database is at, the database's latency, size and
  connections, and whether each scheduled job is reporting. `GET /system/flags` lists the settings
  that change every workspace at once, each with why it is shown and whether its value is a risk.
- `job_heartbeat` (same migration). Every scheduled job is registered through
  `services/jobs.heartbeat`, which records when it last started, finished cleanly and failed. The
  System view says a worker is healthy only from those rows.
- Fleet gains "What you did", the signed-in operator's own recent changes.

**Phase 5.** Axcion charging workspaces, through Axcion's own Stripe account (§6). Built, tested
against a mocked Stripe, and switched off until an operator connects the account.

- The mirror (migration `0070_platform_billing`): `platform_subscription` and `platform_invoice`,
  in cents and Stripe's own status words, corrected from Stripe and never edited by hand.
- `POST /webhooks/stripe`, not operator-gated: the `Stripe-Signature` header is verified against
  the raw body with a five-minute tolerance before anything is parsed. Each event is applied once
  (`platform_stripe_event`), an event older than the state already applied is ignored, and the
  lifetime collected total is recomputed from the invoices, so a replayed `invoice.paid` cannot
  count twice. Transitions into `past_due`, `canceled` and `incomplete` are written to the operator
  trail.
- A workspace's Billing pane: the Stripe mirror (read-only) and its invoices, what Axcion enforces
  (plan, token budget, billing contact, PO), and the actions: create the Stripe customer and a
  subscription on the plan's price (found by `lookup_key`), send Stripe's own payment page for the
  open invoice to the billing contact, retry the charge, and sync from Stripe now. Changing the plan
  never calls Stripe, and the pane says when Stripe charges a different amount from the tier's list
  price.
- Fleet health reads the mirror: past due is broken, a trial ending within 7 days is watch (C11).
  The fleet's MRR tile totals active subscriptions, a yearly price spread over twelve months.
- An hourly `platform_billing_reconcile` job pulls every mirrored subscription from Stripe and logs
  each field it had to correct.

**Phase 6.** The rest of a workspace's lifecycle.

- Support access, C8 option (c) (migration `0071_support_access`, `user.expires_at`). Opening it
  needs a reason and a length (15, 30 or 60 minutes). It makes or reuses one real account in the
  workspace, named "<operator> (Axcion support)" at the operator's address tagged
  `+axcion-support`, with every tab and an `expires_at`, emails the workspace's owners with the
  reason, and records it in both trails. The console opens the workspace in a new tab signed in as
  that account, through the same fragment hand-off Google sign-in uses. `deps.current_user`
  refuses every request that is not a read from such an account and refuses it entirely once
  `expires_at` passes; `expire_support_access` disables it within five minutes after. It can be
  ended early. The Access pane lists past sessions from the operator trail.
- **Viewing the portal as one of its people** (added 2026-09-18, at Connor's direction: "extend
  the masquerade precedent to the intranet"). Inside an open support session only, the Access pane
  offers the workspace's portal roster (`GET …/support-access/roster`: name, address, role,
  status) and **Open portal** (`POST …/support-access/view-as`). That mints a token for the same
  support account carrying `vam` (the member), expiring with the session, and opens
  `/intranet/#view-as=…`. `deps._viewing_as` then answers portal reads *as that member*. It uses
  their own account where they have one (their real progress shows), and otherwise a stand-in
  built from the roster entry. It refuses every write, every path outside `/api/v1/intranet/` and
  `/me`, and any `vam` token whose account is not a support account. Sunburst's links are withheld
  in a view, because they open the member's own coaching conversation in Sisu. The member is not
  emailed, and nothing about them changes. The view is recorded as `support.viewed_as` in both
  trails and listed in the pane's history. The portal keeps the view token in that tab's own
  storage, so it never replaces or clears anybody's real session on the machine: ending the view,
  or the session behind it, leaves a "This view has ended" screen and nothing else.
- Export: a JSON metadata archive (people, businesses, connections and their status, share links,
  the audit log). No business data and no credentials: no password hash, token, TOTP secret, share
  link token or integration configuration.
- Transfer ownership to another active person; every current owner becomes an admin.
- Delete, with `?confirm=` equal to the slug and the blast radius counted first
  (`GET /tenants/{slug}/blast-radius`). The `tenant.deleted` row is written to the operator trail
  first and survives; the tenant row goes, its cascade and a sweep of every table with a
  `tenant_id` remove everything scoped to it, including its domain rows, and stored files are
  removed afterwards, best effort, from every table that points at stored bytes.

## Decisions made during the build

Choices the spec did not make, taken so the build could continue. Each is reversible.

**Links that sign somebody in go to that person, never to the operator.** The invite, reset and
idle-invite endpoints return the address and the expiry, not the URL. Handing an operator a link
that signs in as a workspace's user would be a path from the operator realm into tenant data,
which is what the two realms exist to prevent. The owner invite made at provisioning, and its
resend, still return their URL as they did before the console: nobody has entered that workspace
yet, and the operator needs the link when the first email does not arrive.

**Reconnect and setup links carry no token.** They point at the workspace's Settings,
Integrations page and the reader signs in as themselves, so a forwarded email opens nothing.
Operators cannot reauthorise a provider on a tenant's behalf (§5.3).

**Unlock clears the password lockout only.** A second-factor lockout stays, because clearing it
would give whoever tripped it fresh guesses at somebody's second factor.

**Freezing needs a reason, like suspension.** The spec requires a reason only for suspension and
support access. A frozen workspace with no recorded reason is one nobody can safely unfreeze.

**Three endpoints the spec's list does not name.** `unfreeze-syncs`, because a freeze needs an
undo; `people/resend-idle`, because the idle-invites triage row had an action and no endpoint;
`sources/setup-link`, because the "Nothing is connected" row had nothing to perform.

**Not built from the design file.** "Invite someone" and "Enable" on People: inviting people into
a workspace and re-enabling their accounts are the workspace's decisions, and §5 lists neither.
"Export metadata" in the bulk bar waits for Phase 6's export.

**Confirmation before revoking.** Revoke all asks once, naming the count and kinds of link.
Type-to-confirm is kept for deleting a workspace (Phase 6), as the design file does.

**`platform_audit.operator_id` and `operator_email` are nullable (§4.2 has them required).**
Stripe's webhooks write rows with no operator (§6.3), and the foreign key is `SET NULL` so removing
an operator keeps the record of what they did.

**A heartbeat table the spec does not list.** §5.4 asks the System view for worker health, and the
only other evidence is the newest sync run, which cannot tell a stopped scheduler from a fleet with
nothing connected. `job_heartbeat` holds one row per job, overwritten on each run.

**One migration per phase, not the spec's single `0069_operator_console`.** Each phase ships its own
schema: `0069_platform_audit` here, with platform billing and support access following in theirs.

**The Audit view's "workspace teams" scope leaves out sign-ins and second-factor checks.** The view
lists changes; those are reads and attempts, and they stay on each workspace's Activity pane.

**Stripe keys are entered in the console, not set as environment variables (§6.1 names
`STRIPE_PLATFORM_SECRET_KEY`, `STRIPE_PLATFORM_WEBHOOK_SECRET` and `STRIPE_PLATFORM_ENABLED`).** Your
standing instruction is that credentials are configured in the product rather than stored in
Railway. An operator pastes the secret key and the webhook signing secret on System, the key is
verified with Stripe before it is saved, both are stored encrypted with `FERNET_KEY` like every
workspace credential, and neither is ever returned. "Charging on" is a switch on the same card. The
startup secret guard §6.1 asks for has nothing to guard as a result; `FERNET_KEY`, which it already
guards, protects the stored keys.

**Two tables and a column the spec does not list.** `platform_stripe_event` records each webhook
event applied, so a retried delivery changes nothing. `platform_subscription.stripe_event_at` records
when the state last applied was true in Stripe, so an event arriving out of order is ignored rather
than written over newer state. `platform_billing_config` holds the keys.

**The lifetime collected total is derived, not incremented.** It is recomputed from the mirrored
invoices on every invoice event, so no replay or duplicate event can count a payment twice.

**A new subscription starts incomplete, or trialing, with Stripe's own invoice page as the way to
pay.** Creating a customer creates the subscription on the plan's price with
`payment_behavior=default_incomplete`; "Send payment link" emails the billing contact the open
invoice's hosted page. No card detail passes through Axcion, and no Checkout or Billing Portal
configuration is needed in Stripe. A trialing subscription with no open invoice has no payment link
to send yet; the button appears once Stripe raises one.

**MRR counts active subscriptions only**, a yearly price spread over twelve months. Past due is
excluded: it is money not being collected.

**A support account is addressed as the operator, tagged.** `connor@axcion.io` opens support access
as `connor+axcion-support@axcion.io`, named "Connor Leiva (Axcion support)". The workspace's Team
page shows a real person at Axcion; no email is sent to the tagged address; reopening reuses the
same account and bumps its token version, so an earlier session never comes back.

**The session reaches the workspace in the URL fragment,** the same hand-off Google sign-in already
uses (`#session=` beside `#google_token=` in `auth.jsx`). A fragment never reaches a server log or a
Referer header, and the console opens it in a new tab and never displays it. It replaces any
session that browser already holds on that workspace, and the console says so.

**An open support session counts as a seat for its length (an hour at most).** Both invite paths
count every account that is not disabled, and one of them is `routers/console.py`, which the spec
puts off limits. Exempting support accounts in only the other would make the two disagree, so
neither does; an invite at the seat cap during that hour is refused until the session ends.

**Transferring ownership makes every current owner an admin,** not only one, so the workspace ends
with exactly one owner and nobody loses access.

**Deletion does not rely on the cascade alone.** §5.5 deletes the tenant and lets ON DELETE CASCADE
take the rest. That still happens first (it is also what removes `business` and `legal_entity`,
which reference each other, in one statement), and then every table with a `tenant_id` is swept for
anything left, so deletion is complete whatever a migration did to a constraint and on a database
that does not enforce foreign keys. It is one transaction: if anything refuses, nothing is deleted
and the console shows the database's reason. Stored files are then removed best effort from every
table that points at stored bytes.

**Not built:** "Export metadata" in the Workspaces bulk bar. Export is one workspace at a time, from
its Danger pane.

**Suspension and freezing stop every scheduled pull, not only the sync tick.** The daily Sisu
roster job and the ads funnel job now skip a paused workspace too.

## Decisions the spec left to Connor

Made during the build, on the spec's recommendation unless noted. Each is reversible.

**C8, support access: option (c).** Opening support access creates a real, time-boxed `User`
in the workspace: role `member`, every tab, `expires_at` set, a non-empty reason required,
the owner emailed, and an audit row in the workspace and in `platform_audit`. There is no new
path into tenant data; the session goes through `deps.current_user` like any other.
Refinements: the account is **read-only, enforced by the server** (any request other than
GET/HEAD/OPTIONS from a support account is refused), and expiry is enforced on the request
itself as well as by the five-minute job, so a session ends on time rather than up to five
minutes late. The design's "Read and write" option is not offered.

**C11, trials: Stripe owns them.** A trial is `platform_subscription.status == "trialing"`
with `trial_end`. No column on `Tenant`.

**C13, internal workspaces: derived.** A workspace with no `platform_subscription` row is
internal (not billed). No boolean on `Tenant`.

**C17, people counts: extend `_tenant_row`.** Active, invited, disabled, locked and two-factor
counts per workspace, from grouped counts.

**C3, prices: shown on the operator billing surface only.** `plans.describe()` and every
tenant-facing response stay price-free.

## What needs you

- **An operator account in production**, if you do not already have one. `DEPLOY.md` has the
  `railway ssh` command; the password is read from the environment, never an argument.
- **Platform billing**, before any workspace can be charged: three Stripe prices with the lookup
  keys, a webhook endpoint, and the keys pasted on System. `DEPLOY.md`, "Platform billing".
- **`SINGLE_TENANT_FALLBACK` is `true` in production.** The System view flags it. It closes itself
  once a second workspace exists, and there are several, but it has to be `false` before any
  workspace moves to a custom domain.
- **Read the decisions above.** Each was made on the spec's recommendation or to follow an
  instruction you had already given, and each can be reversed.

## Where the build departs from the spec, and why

**An operator console already existed.** The spec says `platform.py` has no frontend. It has
one: `frontend/src/platform/PlatformConsole.jsx`, lazy-loaded inside the dashboard bundle on
`admin.*` since 2026-08-25. The new console replaces it and the old one is removed, so there is
one operator UI.

**The old console was greyscale on purpose, and the new one is not.** Commit `c220caf` made it
greyscale so an operator could never mistake the screen that suspends customers for a customer's
dashboard, and `test_operator_console_is_greyscale.py` enforced it. The spec prescribes Axcion's
brand instead, so the spec wins. The cue survives in a stronger form: the console is its own
origin and its own bundle, and every screen sits under an ink header reading
"Axcion | Operator" that no workspace surface has. The greyscale tests are replaced by the
spec's brand tests (§11).

**Hosting uses the existing `web` service, not a fourth Railway service (§10).** `*.axcion.io`
already routes `admin.axcion.io` to `web`. Caddy serves the operator bundle on that host, in the
same way it serves the marketing site on `www` and the finder on `app`. A separate service would
run a second copy of Caddy and static files and add no isolation, because the isolation is the
token realm on the API.

**The existing favicons are Axcion's, not Spring's (§10).** They live at
`frontend/public/brand/axcion/favicon-*` — moved out of `/brand/logo/`, which is where a
WORKSPACE's own logo goes. They were generated from the mark's geometry until the designed mark
was delivered on 2026-09-22; now `scripts/brand_assets.py` copies the designer's own favicon
files there byte for byte, and a test in `test_brand_rules.py` holds the copy to the delivery.
The operator entry references them; nothing is drawn.

**Amber text is `#855C00`, not `#9A6B00`.** The spec's amber measures 4.09:1 on its own chip
background and 4.25:1 on the canvas, which fails the AA contrast its §11 requires. `#855C00`
measures 5.19:1 on the chip. The chip and rule tints are unchanged.

**`theme.js` lost one block despite being on the do-not-touch list.** The only change is deleting
`OPS`, the old console's greyscale palette, whose one consumer was the console this build removed.
Tenant theming is untouched.

**C16's throttle already existed, but could be bypassed.** `/api/v1/platform/login` has had a
10-per-5-minutes bucket, keyed on IP and `X-Tenant-Host`. That header is written by the caller,
so a new value per request reset the budget. The bucket now keys on IP alone.
