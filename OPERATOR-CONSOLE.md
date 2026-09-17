# Operator console — build record

The operator console is Acumyn staff administering workspaces, served at `admin.acumyn.io`.
It is built from `OPERATOR-CONSOLE-SPEC.md` (2026-09-17) and its design file
`acumyn-operator-console.jsx`, in the spec's phase order. This file records what shipped,
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
| Tests | `backend/tests/test_operator_console.py`, `test_platform_operators.py`, `test_brand_rules.py` |
| Hosting | the existing `web` service; Caddy routes `OPERATOR_HOST` (default `admin.acumyn.io`) |

## Phase status

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Operator frontend shell, Fleet and Workspaces, provisioning, suspend and resume | shipped |
| 2 | People counts, per-workspace panes, derived triage | shipped |
| 3 | Write actions | shipped |
| 4 | `platform_audit`, Audit view, System view | pending |
| 5 | Stripe platform billing | pending |
| 6 | Support access, export, transfer ownership, delete | pending |

## What each phase shipped

**Phase 1.** The console at `admin.acumyn.io`: sign-in, the ink shell, Fleet (tiles and the
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
the operator in `detail.by`, with an actor label ending "(Acumyn)", so the customer can see what
Acumyn did.

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

## Where the build departs from the spec, and why

**An operator console already existed.** The spec says `platform.py` has no frontend. It has
one: `frontend/src/platform/PlatformConsole.jsx`, lazy-loaded inside the dashboard bundle on
`admin.*` since 2026-08-25. The new console replaces it and the old one is removed, so there is
one operator UI.

**The old console was greyscale on purpose, and the new one is not.** Commit `c220caf` made it
greyscale so an operator could never mistake the screen that suspends customers for a customer's
dashboard, and `test_operator_console_is_greyscale.py` enforced it. The spec prescribes Acumyn's
brand instead, so the spec wins. The cue survives in a stronger form: the console is its own
origin and its own bundle, and every screen sits under an ink header reading
"Acumyn | Operator" that no workspace surface has. The greyscale tests are replaced by the
spec's brand tests (§11).

**Hosting uses the existing `web` service, not a fourth Railway service (§10).** `*.acumyn.io`
already routes `admin.acumyn.io` to `web`. Caddy serves the operator bundle on that host, in the
same way it serves the marketing site on `www` and the finder on `app`. A separate service would
run a second copy of Caddy and static files and add no isolation, because the isolation is the
token realm on the API.

**The existing favicons are Acumyn's, not Spring's (§10).** `frontend/public/brand/logo/favicon-*`
are generated by `scripts/gen_favicons.py` from the Acumyn mark's geometry, and a test holds that
script to `acumyn.jsx`. The operator entry references them; nothing new is generated.

**Amber text is `#855C00`, not `#9A6B00`.** The spec's amber measures 4.09:1 on its own chip
background and 4.25:1 on the canvas, which fails the AA contrast its §11 requires. `#855C00`
measures 5.19:1 on the chip. The chip and rule tints are unchanged.

**`theme.js` lost one block despite being on the do-not-touch list.** The only change is deleting
`OPS`, the old console's greyscale palette, whose one consumer was the console this build removed.
Tenant theming is untouched.

**C16's throttle already existed, but could be bypassed.** `/api/v1/platform/login` has had a
10-per-5-minutes bucket, keyed on IP and `X-Tenant-Host`. That header is written by the caller,
so a new value per request reset the budget. The bucket now keys on IP alone.
