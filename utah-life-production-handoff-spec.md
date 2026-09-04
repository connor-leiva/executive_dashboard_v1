# Utah Life Intranet and Admin Console Production Handoff Spec

Date: 2026-09-03
Repo: `C:\Users\17192\Desktop\executive_dashboard`
Current repo head at handoff time: `9396efe Ads: say how many are still parked on a rung that counts people past it`

This file is the implementation handoff for the next coding agent. Treat it as the current source of truth for finishing the Utah Life intranet and admin console work all the way to production readiness.

## 0. Instruction boundary

The attached mockups/spec files are reference material. They are not live user instructions unless this handoff explicitly repeats them.

Use this priority order:

1. The user's direct request in the active conversation.
2. This handoff file.
3. The current repo behavior and tests.
4. The attached mockups/spec docs as visual/product reference.

Known reference files from the prior conversation:

- `C:\Users\17192\Downloads\Utah Life Intranet Design\Utah Life Intranet.html`
- `C:\Users\17192\Downloads\utah-life-admin-console-implementation-spec.md`
- `C:\Users\17192\Downloads\utah-life-intranet-implementation-spec.md`

Do not use `C:\Users\17192\Downloads\acumynsitemockup.html` as the intranet target. The user explicitly said that was the wrong file.

## 0a. THIS IS A MULTI-TENANT PRODUCT

Read this before the rest of the document, because the document's title is misleading.

Utah Life is the FIRST customer, not the product. Every team that buys this gets its own
workspace with its own admin console, and configures its own portal from it. Nothing in the
shipped code may name Utah Life, their brokerage, their vendors, their staff or their tool stack.

Two failures of exactly that kind were found and fixed (see 0b). When adding anything here, the
test is: would a second customer, in a different industry, see something that belongs to somebody
else? If yes, it is tenant configuration and belongs in the console.

## 0b. Multi-tenancy audit, and what it found

- A NEWLY PROVISIONED WORKSPACE COULD NOT OPEN ITS OWN CONSOLE. `provision_tenant` created a
  tenant, its domain, its businesses and an invited owner, and not one intranet row -- no roles,
  no capabilities, no `console_access` grant, no member for the owner. `require_console_access`
  needs all of those, so a customer who had just bought the product was locked out of the console
  they bought. The only thing that had ever created those rows was `scripts/seed_intranet.py`,
  which is Utah Life's real staff names and email addresses, their courses and their tools;
  running it against a paying customer would have filled their workspace with another company's
  people. `app/services/intranet_bootstrap.py` now creates the GENERIC structure at provisioning
  time -- Owner/Manager/Member roles they rename, capabilities they grant, the owner as first
  member, an empty setup checklist -- and it is idempotent so a retried provision cannot
  overwrite an admin's work.

- THE CONSOLE WAS CONFIGURING TABLES THE INTRANET NEVER READ. Roles, launchpad tiles, Win the Day
  lists, courses and SOPs were all written per tenant by the console and all ignored by the
  intranet, which rendered a compiled-in `constants.js` shaped around the first customer. Every
  workspace would have seen Utah Life's navigation, roles and tool stack regardless of what their
  own admin configured. `_published_content()` now serves the workspace's own published rows,
  with tile role-audience applied server-side, and there is a test asserting no other customer's
  content can appear.

- Smaller, same theme: the workspace names itself in the rail, the tab title, the assistant button
  and the sign-in screen (all were `"Utah Life"` constants); `POWERED BY PLACE | exp` is gone;
  the Sunburst panel and nav item appear only where that workspace has Sisu connected, because a
  coaching product one customer buys is not a feature of the platform; and "Pulled From Follow Up
  Boss" only claims that when a CRM is actually connected.

STILL TENANT-SHAPED, and worth a pass: `scripts/seed_intranet.py` is Utah Life's data end to end
and should be renamed to make that obvious (it is a demo fixture, used only by tests). The
integration catalogue seeded per tenant is a real-estate stack, and one description names the
`utahlife-agents` Google group. The intranet's nav STRUCTURE is still fixed in `constants.js`;
the labels and content are now the workspace's, but which pages exist is not yet configurable.

## 0c. What the portal INHERITS from the Acumyn dashboard

CONNECTIONS ARE INHERITED. APPEARANCE IS NOT. That split is deliberate and worth stating, because
the obvious instinct is to unify both.

A workspace is ONE customer, and they connect Sisu once. `Integration` (dashboard: sisu, fub,
qbo, arive, ghl) and `IntranetIntegration` (portal: sisu, follow_up_boss, google_workspace, slack,
skool, brivity, skyslope, canva) BOTH cover Sisu and Follow Up Boss. A tenant with Sisu connected
on the dashboard still saw "Not Connected" in their portal, and the portal's numbers read zero
beside a dashboard showing live production. The two even spell their states differently --
`connected` against `Connected` -- so nothing would have matched by accident.

BRAND IS NOT INHERITED, on purpose. The dashboard and the portal are two different-looking
products: the portal is a warm, dark-railed team space and the dashboard is an executive surface.
Pulling the dashboard's palette across would fight the portal's design rather than unify
anything. A workspace sets its portal's appearance in the portal's own console. Connections are
the opposite case -- there is one Sisu account and it either works or it does not.

THE DASHBOARD WINS for the connections it owns, because that is where the integration actually
does work: it syncs, it holds the encrypted tokens, it is where the numbers come from. A portal
that disagreed with it would be the one that was wrong. Providers the dashboard has never heard
of (Slack, Skool, Canva, Brivity, SkySlope) stay the portal's own and are configured in its
console as before.

`app/services/inheritance.py` owns it:

- `dashboard_connections()` maps portal provider keys to dashboard providers and normalises the
  status vocabularies in ONE place, so neither side has to know the other's words and a new
  dashboard state cannot silently read as connected. A provider with several dashboard rows
  (QuickBooks has one per company) counts as connected when any row is -- reading the first row
  would make a workspace's portal depend on insertion order.
- The console REFUSES credentials for an inherited provider (422, naming where to connect it).
  Accepting them would write to a row nothing reads while telling the admin they had connected
  something, which is worse than refusing because it looks like it worked.

## 1. Product goal

Finish the Utah Life intranet and admin console so the product is:

- visually faithful to the provided Utah Life mockups,
- tenant-safe for a multi-tenant architecture,
- fully configurable through the admin UI,
- wired to real production integrations and data sources,
- ready for production deployment, monitoring, support, and real daily use.

The user is not asking for a marketing site. The first screen must be the usable intranet or console experience.

## 2. Current implementation baseline

The admin console foundation and most configuration screens have been built. The work is committed through the following relevant commits:

- `8469f0b` - Build Utah Life intranet admin foundation, Phases 0-4
- `768407d` - Build console roster screen, Phase 5.1
- `707378c` - Build console permissions matrix, Phase 5.2
- `7084e55` - Build console launchpad screen, Phase 5.3
- `eac2a07` - Build console Win the Day screen, Phase 5.4
- `3824fe8` - Build console training library screen, Phase 5.5
- `dd2ad74` - Build console SOP library screen, Phase 5.6
- `aa14dae` - Build console brand identity screen, Phase 5.7
- `5a864e0` - Build console team calendar screen, Phase 5.8
- `2402f27` - Build console integrations screen, Phase 5.9
- `26b8f1a` - Build console AI assistant screen, Phase 5.10
- `b6de17b` - Build console audit log screen, Phase 5.11

- `8e3b03c` - Intranet: the viewer's own date, and the mockup's actual neutrals (Phase 6, partial)
- `<this pass>` - Marketing Requests configuration, console screen + API (Phase 7, partial)

Later ads-related commits exist on top of those. Do not assume they are part of this
intranet/admin-console effort.

Last known verification before this handoff:

- `pytest tests/test_console_api.py -q` passed with `264 passed, 5 warnings`
- `npm run build:all` passed from `frontend/`
- `bash frontend/scripts/check-no-mock-data.sh` passed
- Browser smoke checks passed for `/console/brand`, `/console/calendar`, `/console/integrations`, `/console/assistant`, and `/console/audit`
- `git diff --check` passed for the touched console files

Always re-run the relevant checks after changing code. The current repo may have changed since this handoff was written.

## 3. Local development URLs

Backend API:

- `http://127.0.0.1:8000`

Intranet app:

- `http://localhost:5174/intranet/`

Admin console app:

- `http://localhost:5175/console/`

The repo root does not have a `package.json`. Frontend scripts are in `frontend/`.

Typical local commands:

```powershell
cd C:\Users\17192\Desktop\executive_dashboard\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```powershell
cd C:\Users\17192\Desktop\executive_dashboard\frontend
npm run dev:intranet -- --host 127.0.0.1 --port 5174
npm run dev:console -- --host 127.0.0.1 --port 5175
```

If those ports are busy, check active listeners before starting new servers:

```powershell
Get-NetTCPConnection -LocalPort 8000,5174,5175 -State Listen | Select-Object LocalPort,OwningProcess
```

## 4. User decisions already made

These decisions came from prior user answers and should be treated as settled unless the user changes them:

- Use a close proxy for the final fonts until the real font files are provided.
- Choose conservative tenant-protection defaults for multi-tenant architecture.
- The intranet is an additional Acumyn plan feature.
- Every user gets access to the intranet by default.
- Role/audience tightening is configurable.
- Use generic avatars until real profile photos are connected.
- Some pages may ship initially as shells, but only when they are honest empty/config-required states.
- Win the Day state resets per user, per local day, based on the user's local time.
- Win the Day progress must persist.
- Numeric business metrics should render as `0` until real API integrations are configured.
- Do not display fake data or representative production-looking numbers.
- Google Calendar must be configurable in the UI and may ship empty until connected.
- Marketing Requests must be configurable in the UI and may ship empty until connected.
- The user does not want hardwired integrations or destinations. Admin-configurable wiring is required.
- TT Drugs and TT Norms Pro are the target close-proxy font direction until final assets arrive.
- Asset access should be proxied.
- The final Utah Life mark is still needed.
- Do not include items the user explicitly said to omit in prior Q&A.
- Wire to real integrations before production use.

## 5. Product principles for the remaining work

The most important correction from prior feedback: the intranet must look like the provided Utah Life mockup. It should not feel like a separate generic workspace product.

Production behavior rules:

- No fake operational data.
- No seeded demo metrics outside explicitly demo-only tenants.
- No secrets returned to browsers.
- No secret-shaped values in audit logs.
- No cross-tenant reads, writes, asset access, search results, or integration state.
- No uploaded asset file paths or data URIs exposed as public truth.
- All tenant-specific content and destinations must be configurable through the admin UI.
- All writes must be audited.
- Publish/preview/live behavior must be clear and testable.
- Empty states must say what is not connected or configured without pretending to be live.
- Console users without `console_access=Full` must fail closed.

Visual rules:

- Intranet visual language must follow the Utah Life mockup: dark left rail, Utah Life identity, muted warm canvas, compact top utility bar, role switcher, metric cards, goal/progress card, dark Sunburst coaching panel, Needs You Today, Quick Launch, and floating Ask button.
- Do not keep the earlier generic "Workspace Intranet" styling as the final product.
- The admin console can remain a dense operational configuration surface, but it must feel coherent with the Utah Life product.
- Avoid landing-page treatment, oversized hero marketing layout, decorative blobs, and fake illustrative content.

## 6. Architecture overview

Backend:

- FastAPI app in `backend/app/main.py`.
- SQLAlchemy async models in `backend/app/models.py`.
- Tenant resolution through host headers in `backend/app/tenancy.py`.
- Console API router in `backend/app/routers/console.py`, mounted at `/api/console`.
- Intranet API router in `backend/app/routers/intranet.py`, mounted at `/api/v1/intranet`.
- Console auth/authorization uses `ConsolePrincipal` and `require_console_access`.
- Seed data for the intranet lives in `backend/scripts/seed_intranet.py`.
- Uploaded logo/SOP bytes use `backend/app/services/binder_storage.py`.

Frontend:

- Vite React app in `frontend/`.
- Intranet routes live under `frontend/src/intranet/`.
- Console routes live under `frontend/src/console/`.
- Console navigation and copy live in `frontend/src/console/constants.js`.
- Build scripts live in `frontend/package.json`.

Storage:

- `binder_storage` supports remote object storage when configured and local storage in dev.
- Production must use durable object storage with private keys, scoped access, content-type metadata, size limits, and signed/proxied downloads.

Publishing:

- Admin changes are tracked in pending-change/publish-batch models.
- Published config is consumed by the intranet.
- Preserve draft/live separation and make rollback exact.

## 7. Current completed phase state

Use this as the starting point, then update as work is finished.

- [x] Phase 0 - Repo and local app orientation.
- [x] Phase 1 - Tenant-scoped intranet module foundation.
- [x] Phase 2 - Published config endpoint and per-user Win the Day state.
- [x] Phase 3 - Intranet app shell and mockup-proxy routes.
- [x] Phase 4 - Admin console foundation, overview, pending changes, publish controls, preview.
- [x] Phase 5.1 - People and Roster admin screen.
- [x] Phase 5.2 - Roles and Permissions admin screen.
- [x] Phase 5.3 - Tool Launchpad admin screen.
- [x] Phase 5.4 - Win the Day admin screen.
- [x] Phase 5.5 - Training Library admin screen.
- [x] Phase 5.6 - SOP Library admin screen.
- [x] Phase 5.7 - Brand and Identity admin screen.
- [x] Phase 5.8 - Team Calendar admin screen.
- [x] Phase 5.9 - Integrations admin screen.
- [x] Phase 5.10 - AI Assistant admin screen.
- [x] Phase 5.11 - Audit Log admin screen.
- [~] Phase 6 - Intranet visual parity and production polish. PARTIAL, see section 8a.
- [~] Phase 7 - Admin console completion and configuration coverage. PARTIAL, see section 9a.
- [ ] Phase 8 - Production authentication, identity, and tenant access.
- [ ] Phase 9 - Real integration wiring and sync jobs.
- [~] Phase 10 - Marketing Requests production workflow. PARTIAL, see section 12a.
- [ ] Phase 11 - AI Assistant production implementation.
- [ ] Phase 12 - Data model, migrations, and seed hardening.
- [ ] Phase 13 - Publish/runtime config hardening.
- [ ] Phase 14 - Observability, operations, and deployment.
- [ ] Phase 15 - Security, privacy, and tenant-isolation review.
- [ ] Phase 16 - QA, UAT, and release readiness.
- [ ] Phase 17 - Production launch and first-week support.

## 8. Phase 6 - Intranet visual parity and production polish

Goal: make the live intranet app visually and conceptually match the provided Utah Life mockup.

Primary files:

- `frontend/src/intranet/IntranetApp.jsx`
- `frontend/src/intranet/IntranetApp.css`
- `frontend/src/intranet/constants.js`
- `frontend/src/intranet/api.js`
- `frontend/vite.intranet.config.js`

Required work:

- Re-check the provided Utah Life intranet HTML mockup before editing.
- Align the left rail with the mockup:
  - Utah Life logo lockup at the top.
  - "Powered by PLACE" supporting line.
  - "Team Intranet" label.
  - Grouped nav: Workspace, Learn, Team, Marketing, Partners.
  - Active Home treatment matching the mockup.
- Align the top utility bar:
  - Search input.
  - Ask Utah Life button.
  - Role switcher with Buyer Agent, Listing Agent, Ops/Admin, Team Leader.
  - User identity chip.
- Align Home content:
  - Date line.
  - Greeting: `Good Morning, Jordan.` until real user first name is wired.
  - Supporting priority sentence based on real/empty state, not fake data.
  - `Open My Tools` and `My Numbers` actions.
  - Four metric cards matching the mockup's visual language.
  - Annual goal/progress bar.
  - Team YTD block.
  - Dark Sunburst coaching panel.
  - Needs You Today.
  - Quick Launch.
  - Floating Ask button.
- Replace current generic "Workspace Intranet" card treatment where it conflicts with the mockup.
- Keep placeholders honest:
  - numbers are `0` or visibly unconfigured if the integration is not connected,
  - calendar says disconnected until configured,
  - marketing request destination says disconnected until configured,
  - AI assistant says not connected until the real service exists.
- Use close-proxy fonts now. Switch to the final brand fonts when the user provides them.
- Ensure responsive behavior at desktop, tablet, and mobile widths.

Acceptance criteria:

- At `http://localhost:5174/intranet/`, the first viewport reads as the same product as the Utah Life mockup.
- There are no fake production-looking metrics.
- Calendar and Marketing Requests can be configured in UI and render empty/not-connected before setup.
- Win the Day progress persists per user per local day.
- All intranet routes still work after visual refactor.
- Screenshots pass at:
  - `1920x1080`
  - `1440x900`
  - `390x844`

Suggested tests:

```powershell
cd C:\Users\17192\Desktop\executive_dashboard\backend
.\.venv\Scripts\python.exe -m pytest tests/test_intranet.py tests/test_console_api.py -q
```

```powershell
cd C:\Users\17192\Desktop\executive_dashboard\frontend
npm run build:intranet
```

## 8a. Phase 6 progress and what is left

Done in this pass:

- The date line and the greeting now come from the VIEWER'S clock. Both were the mockup's frozen
  instant -- a `MOCK_DATE` constant and a hardcoded "Good Morning" -- rendered on every screen. The
  app was announcing a Monday last August. A one-minute tick keeps a tab left open overnight right.
- The search box no longer ships pre-filled with the mockup's sample query; that text is a
  placeholder now. Every user was opening the intranet with somebody else's search already typed.
- Colour tokens measured against the mockup and corrected. Every neutral had drifted: rail
  `#10191E` -> `#171E22` (the rail is the same ink as the body text, which is the trick of it),
  canvas `#E9E6E3` -> `#EAE7E6`, border `#D7D2CF` -> `#DED9D7` in ~160 places, hairline
  `#E2DEDB` -> `#EFEBEA`, panels -> flat `#FFFFFF`. The Sunburst accent was two different
  invented magentas for the mockup's one `#C04BD1`. A body-copy step the mockup uses 91 times had
  no token, so that copy rendered at `--muted`.
- Type scale corrected. It ran 2-4px large and one weight heavy at every step: h1 42/500 ->
  40/400 with -0.6px tracking, hero copy 19 -> 16, stat figures 36/500 -> 34/400, nav 16 -> 14.5.
- Rail: width 293 -> 266; the active marker is now the mockup's 3x16 tick that EVERY item carries
  (transparent when inactive), which is what keeps labels on one vertical line.
- `check-no-mock-data.sh` gained a frozen-date rule and the path to it is corrected above.
- Verified: no horizontal scroll at 390x844, off-canvas rail works.

Second pass:

- Rail footer added. The mockup ends the rail with a "Need A Hand?" block and the app had no
  counterpart -- it is the one place that tells a new agent what to do when the intranet does not
  answer their question. `#help` renders as emphasised text rather than an `<a href="#">`,
  because no help channel is configured and a link that goes nowhere is a link that lies about
  being one. It becomes a real link when there is somewhere to point it.
- I WAS WRONG THAT THE TWO SOURCES DISAGREED about the rail. "Powered by PLACE" sits under the
  wordmark at the TOP of the rail; the mockup's "Need A Hand?" block sits at the BOTTOM. Different
  positions, no conflict -- the app now has both, and no decision is needed.
- Content frame aligned to the mockup: `max-width` 1268 -> 1240, padding -> `38px 44px 90px`.
  Only visible above ~1300px, which is exactly what a 1920 check is for.
- Checked at `1920x1080`: no horizontal scroll, nothing overflowing, rail 266px, frame capped.
  All three required viewports have now been checked (1920x1080, 1440x900, 390x844).

Left for Phase 6:

- PANEL HEADINGS: 19 vs 17, and why it was left alone. Reading every `h2` in the mockup, the
  rule is not "sidebar panels are smaller" -- it is that the WIDE column's panel is 19px and the
  NARROW column stacked beside it is 17px. Home ("Needs You Today" 19 / "Quick Launch",
  "From Leadership", "This Week" 17) and My Numbers ("Closings by Month" 19 / "Training
  Completed", "Connected Accounts" 17) both follow it.

  The app's Home lower row is a different layout: two EQUAL columns, Needs You Today beside Quick
  Launch, with no stacked third panel and no "From Leadership" or "This Week" to stack. In equal
  columns, 19/19 is the internally consistent answer; setting one to 17 would import the number
  without the narrowness that justifies it. Matching the mockup here means adopting its
  wide-plus-stack structure, which is a layout decision with real content behind it (two panels
  this product does not have yet), not a font size.
- Needs You Today and Quick Launch match on structure and on the tokens they inherit, but their
  internal rows were not compared field by field against the mockup.
- Final TT Drugs / TT Norms Pro files. DM Sans + Archivo are the close proxies and are what the
  mockup itself uses; the swap point is the `--font-*` block at the top of `ui.css`.

## 9a. Phase 7 progress and what is left

Done in this pass -- Marketing Requests configuration:

- New table `intranet_marketing_setting`, migration `0056_marketing_requests_config`. A per-tenant
  singleton shaped like `intranet_ai_setting`, NOT columns on `intranet_workspace`: the workspace
  row is the tenant's identity and a delivery destination is not, and Phase 10's request table
  can foreign-key this without dragging the workspace in.
- `GET /api/console/marketing` and `PATCH /api/console/marketing`. Registered in the test file's
  route tables, so they inherit the auth-401, buyer-403 and exactly-one-audit-row coverage.
- Console screen at `/console/marketing`, listed under Connections.
- Validation is per destination type, because "destination" means three different things: a
  Slack channel (`#`), an email address (`@`), or an https URL. A webhook is stored normalised.
- Enabling with no destination is REFUSED rather than saved, so the intranet cannot show a
  working request form over a destination that does not exist.
- `required_fields` is a fixed vocabulary. These keys drive the intranet form, so an unknown one
  would be a required field with no input behind it.

WHAT THIS DELIBERATELY DOES NOT DO. There is no `connected` flag and no test-delivery button.
Configuration completeness and delivery health are reported as two separate facts, because a
saved form proves only that somebody typed a destination. Section 23 still lists the production
Marketing Requests destination as an open decision, and building a delivery path against a
guess would have meant either dead code or a console claiming a connection nobody has made. The
screen says "configuration saved, runtime connection pending" in as many words, which is what
section 9 of this document asks for.

Second pass:

- THE SETUP CHECKLIST NO LONGER TAKES ITS OWN WORD FOR IT. It was eleven manual checkboxes:
  `completed` was whatever somebody clicked, so a workspace could show "SOPs uploaded" complete
  with no SOPs. That matters because the checklist is what an operator reads to decide whether a
  workspace is ready for real users. `_setup_evidence()` derives, per task, whether the
  underlying configuration exists, and a VERIFIABLE task cannot be ticked past it (422). The
  console disables the box and says "nothing configured yet" rather than letting somebody click
  into a server error, and flags the reverse case -- ticked, but the config has since gone.
  Against the seeded workspace this correctly reports `calendar` as unsatisfied: categories
  exist, none has an address.
- A NOTE ON TESTING THIS. The first version of the gate's test searched the checklist for
  any unconfigured task. It passed alone and failed in the full suite, because other tests
  configure that workspace and by the time it ran there was nothing unconfigured left. A
  test that depends on how much of the workspace its neighbours happened to fill in is
  testing the neighbours. It now clears one specific field, asserts, and restores in a
  `finally`. Worth knowing: this suite shares one SQLite database and randomises order, so
  any test that reads ambient state is a future flake.
- `satisfied: null` is a real answer for the three that cannot be derived (permissions reviewed,
  onboarding path assigned, announcement channel). Whether somebody has genuinely reviewed
  permissions is not visible in a row count, and a proxy invented for it would be a checkbox
  claiming more than it knows with extra steps. Those stay a human judgement and say so.
- The intranet's Requests page reads the console config now, shows the honest unavailable state,
  and no longer carries its own editor.
- THE DEV ENVIRONMENT REACHES THE API. Two things were wrong, and the first hid the second:
  `<slug>.localhost` was not an allowed CORS origin, and -- once it was -- the tenancy layer did
  not resolve it either, so the request fell through to the single-tenant fallback and returned a
  DIFFERENT tenant, which 401s and reads as a bad token. `is_local_host()`'s docstring had
  claimed dev subdomain resolution worked since it was written; nothing implemented it. Both are
  gated on `is_deployed()` and both have tests, including that a deployed config never gets the
  localhost rule and that a reserved name cannot be claimed through `.localhost`.

  To use it: browse `http://<slug>.localhost:<port>` and the tenant resolves by slug, exactly as
  `<slug>.PLATFORM_DOMAIN` does in production. This is how the connected states are now verified.

Left for Phase 7:

- Google Calendar config was reviewed and is largely covered already (per-category
  `calendar_address`, role audience, plus workspace `timezone` / `week_starts_on` /
  `default_calendar_view`). A distinct Google connection status is the gap, and it belongs with
  the Phase 9 integration work rather than here.
- Feature/plan gating: DECIDED AND BUILT. Team has no portal; Business includes it; Portfolio
  adds the AI assistant, which sits a tier higher because it answers from a workspace's own
  documents and costs real money per question. Marketing Requests is deliberately NOT a plan
  feature at any tier -- it is how a team routes work to its own marketing people, so it ships
  with the portal. The portal used to be switched on per workspace in `config.features`; that
  flag can now GRANT but never revoke, so grandfathered workspaces keep working while the plan
  becomes the real answer. Removable once they are on a paying tier.
- The three non-derivable checklist items could become derivable if the product gains a way to
  record the underlying decision. Worth revisiting rather than treating as permanently manual.

Incidental fix found on the way: `_https_url` in `console.py` accepted `"not a url"`, because it
prepends a scheme and the netloc then parses as `not`. That validator also guards launchpad tile
URLs and AI source base URLs, so a tile could be saved pointing at something that is not an
address. It now rejects raw whitespace. Encoded `%20` is untouched.

Two more things found on the way, both worth knowing before the next pass:

- `_set_publish_state` was a hand-written tuple of seventeen model classes, and a publishable
  table added without being added to it is never marked published -- `draft_dirty` stays true
  forever and the live intranet keeps serving pre-publish state, with nothing raising. The new
  table was in exactly that position. It now derives the set from the mapper registry (any model
  with `tenant_id` + `published_at` + `draft_dirty`), verified to reproduce all seventeen and add
  only the new one, with a test asserting the derivation still matches the schema.

- THE INTRANET CANNOT REACH THE API FROM `localhost` IN DEV. `src/api.js` sends
  `X-Tenant-Host: window.location.hostname` with no override, so a browser on `localhost:5174`
  identifies as tenant `localhost` and every call 401s; the dev server sidesteps this by running
  with no `VITE_API_BASE` at all, in a demo fallback with a hardcoded "Jordan Hale". Browsing via
  `utah-life.localhost` fixes the header (and `vite.intranet.config.js` allows `.localhost` hosts
  for what looks like that reason) but that origin is not in `ALLOWED_ORIGINS`, so CORS blocks it
  instead. Consequence: the intranet's CONNECTED states cannot currently be seen in a browser
  locally -- only the demo fallback and the API payload itself. Adding `http://*.localhost:PORT`
  to the dev CORS list would close this, and it is worth doing before Phase 6's remaining
  screenshot work.

## 9. Phase 7 - Admin console completion and configuration coverage

Goal: every tenant-visible intranet behavior must be configurable in the admin console, including items currently shipped as honest empty states.

Primary files:

- `frontend/src/console/Console.jsx`
- `frontend/src/console/pages/*.jsx`
- `frontend/src/console/constants.js`
- `frontend/src/console/api.js`
- `backend/app/routers/console.py`
- `backend/app/models.py`
- `backend/scripts/seed_intranet.py`
- `backend/tests/test_console_api.py`

Required work:

- Finish any missing config fields from the user's admin-console mockup.
- Add a Google Calendar configuration surface:
  - tenant calendar URL,
  - optional Google calendar ID,
  - timezone,
  - default view,
  - category color/audience,
  - connection status,
  - no fake event preview when disconnected.
- Add Marketing Requests configuration:
  - enabled flag,
  - request destination type,
  - request destination URL/channel/email,
  - default assignee or role,
  - required fields,
  - notification routing,
  - connection status.
- Add any missing workspace identity fields:
  - final mark/logo slots,
  - brand font selection/proxy,
  - custom domain settings,
  - domain verification state,
  - workspace address if required by the latest mockup.
- Add feature/plan gating config:
  - intranet enabled flag,
  - AI assistant enabled flag,
  - Marketing Requests enabled flag,
  - integrations enabled by plan,
  - no client-only gating for privileged behavior.
- Finish setup checklist mapping so checklist items close only when the underlying config is truly complete.
- Make disabled buttons and "not yet available" labels accurate. If a feature is still future-work, it should clearly say configuration is saved but runtime connection is pending.

Acceptance criteria:

- A console admin can configure all current intranet empty states without editing code or seed files.
- All config writes are tenant-scoped, validated, audited, publish-aware, and reflected in preview/live.
- Buyer-agent or non-console users receive 403 on console reads/writes.
- Cross-tenant IDs return 404/403 and never leak existence.

Suggested tests:

```powershell
cd C:\Users\17192\Desktop\executive_dashboard\backend
.\.venv\Scripts\python.exe -m pytest tests/test_console_api.py -q
```

```powershell
cd C:\Users\17192\Desktop\executive_dashboard\frontend
npm run build:console
```

## 10. Phase 8 - Production authentication, identity, and tenant access

Goal: replace local/dev auth assumptions with production-grade identity and access control.

Required work:

- Decide and implement production SSO. Likely Google Workspace for Utah Life.
- Support manual invite/reset flows for users not covered by SSO.
- Enforce tenant by verified host/custom domain and authenticated user tenant.
- Ensure all app tokens include tenant, user, and role claims.
- Ensure all console routes require a role with `console_access=Full`.
- Implement secure session lifecycle:
  - short-lived access tokens,
  - refresh token rotation or equivalent,
  - logout invalidation,
  - secure cookie/token storage decision,
  - CSRF protection if cookies are used,
  - MFA/TOTP policy where required.
- Add profile data:
  - first name,
  - last name,
  - display name,
  - avatar URL or initials,
  - role,
  - timezone.
- Add admin UI for invite resend, deactivate, reactivate, and role changes if any are missing.

Acceptance criteria:

- A real Utah Life user can sign in via the chosen provider.
- A deactivated user loses access.
- A user from tenant A cannot access tenant B by host spoofing, token replay, asset key, or API ID.
- A console-less user cannot load console data.
- Auth failures are observable but do not leak secret details.

## 11. Phase 9 - Real integration wiring and sync jobs

Goal: connect the product to real systems, using admin-configured credentials and destinations.

General integration rules:

- Do not hardcode provider credentials or tenant destinations.
- Store secrets encrypted or in the production secret manager, not in generic JSON config.
- Return only masked/safe connection metadata to the browser.
- Audit connect, disconnect, config update, test, and sync state changes.
- Include retry/backoff, rate-limit handling, and last-sync status.
- Sync jobs must be idempotent.
- If a provider is disconnected, UI renders empty state or `0`, not fake data.

Required providers and likely mappings:

### Sisu

Purpose:

- My Numbers cards.
- YTD units, pending, appointments held, GCI.
- Goal progress.
- Team YTD block.

Admin config:

- API credential reference.
- Tenant/team ID.
- User matching field.
- Metric mapping.
- Sync schedule.

Runtime behavior:

- Sync user-level and team-level metrics.
- Use `0` while disconnected.
- Show stale status when sync is old or failed.

### Follow Up Boss

Purpose:

- Needs You Today.
- Win the Day inputs/lists where relevant.
- Lead/call/task follow-up items.

Admin config:

- API credential reference.
- Smart list IDs.
- User matching field.
- Follow-up rules.

Runtime behavior:

- Pull actionable items.
- Preserve per-user completion state.
- Do not reset completed state until the user's local day changes.

### Google Workspace and Google Calendar

Purpose:

- SSO.
- Roster/group sync if chosen.
- Team calendar display.

Admin config:

- OAuth or service account credential reference.
- Calendar IDs.
- Role/audience mapping.
- Timezone and week-start defaults.

Runtime behavior:

- Calendar renders real events after connection.
- Disconnected calendar renders empty state.
- Respect role/audience filters.

### Slack

Purpose:

- Announcements.
- Marketing request notifications or destination.

Admin config:

- Workspace connection.
- Channel IDs.
- Bot/user token reference.
- Notification rules.

Runtime behavior:

- Test connection verifies channel access.
- Marketing request events can post to configured channel when enabled.

### Training/content sources

Likely providers:

- Skool.
- Loom.
- PLACE.
- eXp.
- Google Drive or other document source if selected.

Purpose:

- Training Library.
- First 30 Days.
- SOP/source documents.
- AI Assistant sources.

Runtime behavior:

- Source content syncs into tenant-scoped records.
- All imported content has source metadata and freshness.
- Broken links are surfaced in console health.

### Other launchpad providers

Examples from current UX:

- Brivity.
- Canva.
- SkySlope.
- Meraki Title.
- TT Drugs.
- TT Norms Pro.

Purpose:

- Launchpad tiles and deep links.
- Optional integration status where relevant.

Runtime behavior:

- Configurable tile destination, auth type, role audience, and active state.
- No provider-specific hardcoding unless there is a real integration contract.

Acceptance criteria:

- Every connected provider has:
  - config UI,
  - secure credential storage,
  - test endpoint,
  - sync endpoint/job,
  - status display,
  - audit trail,
  - tenant isolation tests,
  - failure-state UI.

## 12. Phase 10 - Marketing Requests production workflow

Goal: replace the empty/configurable shell with a real request workflow.

Required work:

- Define request model:
  - tenant ID,
  - requester member/user,
  - listing/client context,
  - request type,
  - title,
  - description,
  - due date,
  - priority,
  - attachments,
  - status,
  - assignee/owner,
  - destination delivery metadata.
- Define admin config:
  - enabled,
  - destination type,
  - Slack channel/email/webhook/project board,
  - required fields,
  - default assignee,
  - SLA/due-date defaults,
  - file attachment policy.
- Build intranet UI:
  - create request,
  - view my requests,
  - see status,
  - upload attachments where allowed.
- Build console UI:
  - workflow configuration,
  - request list,
  - status management,
  - export/audit if needed.
- Wire destination delivery:
  - Slack post,
  - email,
  - webhook,
  - or project-management provider once selected.

Acceptance criteria:

- A user can submit a real marketing request from the intranet.
- The request persists and is visible to permitted admins.
- Destination delivery either succeeds or shows an actionable failure.
- A disconnected destination keeps requests saved locally and does not drop user input.
- All writes and status changes are audited.

Open decision:

- The exact production destination for Marketing Requests is TBD.

## 12a. Phase 10 progress and what is left

Built, and the ordering deserves an explanation. Section 23 still lists the production
destination as an open decision, so the obvious reading is to wait. That has it backwards: this
document's own acceptance criteria say a disconnected destination must keep requests saved and
must not drop user input. A request typed up and lost because nothing was listening is worse
than no form at all, and the agent who wrote it is the one who pays. So the RECORD shipped and
delivery is the later piece.

- `intranet_marketing_request` (migration `0057`). `delivered_at` is null on every row, which is
  the truth rather than a placeholder and needs no backfill when delivery ships. Requester is
  denormalised alongside the member FK so a request still names who asked after somebody leaves
  the roster; both member FKs are `SET NULL`, never CASCADE, so removing a person cannot delete
  outstanding work.
- Intranet: `POST /api/v1/intranet/marketing/requests` and `GET` for the submitter's OWN requests.
  Not the workspace queue -- an agent has no reason to read what colleagues asked for, and there
  is a test on that.
- Console: `GET /api/console/marketing/requests` and `PATCH .../{id}` for status and assignee,
  both registered in the shared route tables so they inherit the auth / 403 / one-audit-row
  coverage.
- REQUIRED FIELDS ARE ENFORCED SERVER-SIDE, not only in the form. A required field checked in the
  browser only is a suggestion, and the console's setting would mean nothing to anything posting
  directly.
- Submission is REFUSED (409) while the workspace has requests switched off, because accepting
  into an unconfigured feature collects work nobody is watching for.
- STATUS DOES NOT JOIN THE PUBLISH BATCH, and this is the one console write where that is right.
  Everything else there is configuration -- a draft of how the workspace should behave. A
  request's status is operational fact: somebody either started the work or they did not, and
  holding it in a draft until a publish means an agent sees "New" on something already finished.
  Verified in the browser: moving a status left the pending count unchanged, and the submitter
  saw "IN PROGRESS" on their own page immediately.

`attachments` was REMOVED from the requirable field vocabulary. The console could require it and
the submit form cannot collect a file, which makes a request impossible to file and impossible to
diagnose -- the workspace would have switched requests on and nobody could send one. A test now
asserts the console's requirable set stays a subset of what the intranet accepts, so the two
cannot drift apart again. It goes back the moment the upload exists.

Attachments, second pass (migration `0058`):

- SUBMISSION IS NOW MULTIPART AND ATOMIC. The files arrive in the same request as the rest of the
  form and are written in one transaction. For a listing flyer the photograph often IS the
  request; an upload-after-create flow whose second step fails leaves a record that reads as
  complete with the point of it missing -- silently, and on the agent who did the work. It is
  also the ONLY shape in which `attachments` can be a required field, because the requirement and
  the file have to arrive together, so `attachments` is back in the requirable vocabulary.
- FILES ARE JUDGED BY THEIR BYTES. `sniff_attachment()` reads the magic number and the declared
  Content-Type is ignored entirely -- a browser sends whatever it is told to, and if a download
  later echoes that back, an HTML file labelled `image/png` is stored XSS against the next person
  who opens it. PNG, JPEG, WebP and PDF only; SVG is excluded even though the logo uploader takes
  it, because an SVG is a script host and these files are opened by other people in the workspace.
- The stored extension comes from the SNIFFED type, not the uploaded filename.
- Every file is validated BEFORE anything is written, so a rejected second file cannot leave a
  saved request and one orphaned upload behind. There is a test asserting nothing survives.
- Downloads are proxied, never a storage URL, and served `Content-Disposition: attachment` with
  the sniffed type. Two routes rather than a flag, because the authorisation differs: the intranet
  route serves the requester's OWN files, the console route serves the workspace's.
- Limits: 5 files, 10 MB each.

Left for Phase 10:
- DELIVERY ITSELF: posting to the Slack channel, email address or webhook, with retry, failure
  surfacing and `delivered_at` written by a real attempt. Blocked on section 23's destination
  decision, and on an egress policy for webhooks (section 17 lists SSRF through configurable URLs).
- An admin view of a single request. The queue is a list; there is no detail page.
- Notifying the requester when status changes. Today they see it by looking.

## 13. Phase 11 - AI Assistant production implementation

Goal: turn Ask Utah Life from a shell into a sourced, tenant-safe assistant.

Required work:

- Select production provider/model and retrieval stack.
- Add tenant-scoped content indexing:
  - SOP versions,
  - training lessons,
  - First 30 Days content,
  - Win the Day docs/config,
  - calendar policy docs if available,
  - brand kit docs,
  - directory metadata where appropriate.
- Add vector storage or equivalent retrieval index.
- Add source freshness and crawl status.
- Add answer endpoint:
  - authenticated,
  - tenant-scoped,
  - role-aware,
  - cites sources,
  - refuses when no source is available if guardrail is enabled,
  - offers escalation if enabled.
- Add content gap workflow:
  - unanswered or low-confidence question creates a content gap,
  - console can assign/resolve/no-action,
  - resolved gaps can trigger re-index.
- Add usage/rate limits:
  - per user,
  - per tenant,
  - reset by user's local day where relevant.
- Add privacy/logging controls:
  - redact sensitive values,
  - avoid storing full prompts if policy says not to,
  - no tenant data used for training unless explicitly allowed.

Acceptance criteria:

- The intranet Ask button and Ask page call the real answer endpoint.
- Answers include citations to tenant-owned content.
- If no relevant source exists, the assistant does not hallucinate.
- User can see a graceful unavailable state if AI is disabled.
- Console AI source health reflects real crawl/index state.

## 14. Phase 12 - Data model, migrations, and seed hardening

Goal: make database shape production-safe and migration-managed.

Required work:

- Audit every intranet/console model for `tenant_id` and indexes.
- Add missing constraints:
  - uniqueness by tenant where appropriate,
  - foreign key cascade rules,
  - enum/value validation,
  - non-null fields for production-critical data.
- Move schema evolution to Alembic migrations for production.
- Keep SQLite dev convenience only for local development.
- Make `seed_intranet.py` idempotent and safe to re-run.
- Separate demo seed data from production tenant bootstrap.
- Remove or quarantine representative mock numbers outside demo tenants.
- Add migration tests.
- Add data-backfill scripts for new fields.

Potential model/config additions:

- Workspace address and legal/company fields if required by the final admin mockup.
- Domain verification status and timestamps.
- Marketing request destination config.
- Integration credential references.
- Calendar source IDs.
- AI source index metadata.
- User timezone and locale.
- Asset metadata table if object storage needs signing/expiry rules.

Acceptance criteria:

- A blank production database can migrate and provision a tenant.
- Existing dev database can migrate without manual cleanup.
- All tenant-scoped tables have tenant isolation tests.
- Seeds do not overwrite admin-configured values.

## 15. Phase 13 - Publish and runtime config hardening

Goal: make draft, preview, publish, rollback, and live runtime behavior exact.

Required work:

- Ensure every admin-configurable field participates in draft/pending state or has an explicit immediate-write reason.
- Preview must show draft state.
- Live intranet must show only published state unless the route is explicitly preview.
- Add ETag/cache behavior for published config.
- Add cache invalidation on publish/rollback.
- Add rollback tests for every configurable domain:
  - workspace/brand,
  - roles/permissions,
  - roster,
  - launchpad,
  - Win the Day,
  - training,
  - SOPs,
  - calendar,
  - integrations,
  - AI settings,
  - marketing requests.
- Make publish history readable in console.
- Prevent stale client overwrites where possible.

Acceptance criteria:

- A console admin can preview draft changes, publish them, and roll back to a prior published version.
- The intranet does not show unpublished draft changes to normal users.
- Rollback restores the expected previous live state.

## 16. Phase 14 - Observability, operations, and deployment

Goal: run the product like a production system.

Required work:

- Add structured logging with request IDs and tenant IDs.
- Add metrics:
  - API latency/error rate,
  - sync job success/failure,
  - integration stale counts,
  - AI usage/errors,
  - publish events,
  - auth failures,
  - upload failures.
- Add health checks:
  - API health,
  - DB connectivity,
  - object storage connectivity,
  - worker/scheduler health.
- Add background worker deployment plan.
- Add backup and restore plan:
  - database,
  - object storage,
  - secrets.
- Add CI/CD:
  - backend tests,
  - frontend builds,
  - lint/type checks if adopted,
  - migration checks,
  - smoke tests.
- Add staging environment.
- Add production environment.
- Document env vars.
- Document incident and rollback runbooks.

Acceptance criteria:

- Production deploy can be reproduced from docs.
- A failed deploy can be rolled back.
- Operators can see whether integrations, workers, and AI are healthy.
- Backups are tested.

## 17. Phase 15 - Security, privacy, and tenant isolation

Goal: complete a launch-grade security pass.

Required work:

- Review OWASP basics:
  - auth bypass,
  - broken access control,
  - injection,
  - XSS,
  - CSRF if cookie auth,
  - insecure direct object references,
  - upload vulnerabilities,
  - SSRF through configurable URLs,
  - CORS origin mistakes.
- Harden uploads:
  - MIME sniffing,
  - file size limits,
  - extension policy,
  - malware scanning if available,
  - private storage,
  - signed/proxied delivery.
- Harden secret handling:
  - no secrets in JSON config,
  - no secrets in logs,
  - no secrets in audit details,
  - no secrets in browser local storage,
  - credential rotation path.
- Add tenant isolation tests:
  - API ID probing,
  - asset key probing,
  - preview/publish probing,
  - integration status probing,
  - AI retrieval probing.
- Add rate limiting:
  - auth,
  - assistant,
  - uploads,
  - sync/test endpoints,
  - marketing request submit.
- Add security headers/CSP for frontend deployment.
- Verify custom-domain onboarding cannot hijack another tenant's host.

Acceptance criteria:

- A scripted cross-tenant probe cannot read or mutate another tenant's data.
- Secret-shaped values never appear in browser responses or audit rows.
- Uploads cannot escape storage boundaries or execute in browser context.
- Security tests are part of CI.

## 18. Phase 16 - QA, UAT, and release readiness

Goal: verify the entire product against real user workflows.

Required QA matrix:

- Browser coverage:
  - Chrome,
  - Safari,
  - Edge.
- Viewports:
  - `390x844`,
  - `768x1024`,
  - `1440x900`,
  - `1920x1080`.
- User roles:
  - Buyer Agent,
  - Listing Agent,
  - Ops/Admin,
  - Team Leader,
  - JV Partner if still included.
- Intranet screens:
  - Home,
  - Ask Utah Life,
  - Win the Day,
  - Sunburst Coaching,
  - Team Calendar,
  - Tool Launchpad,
  - My Numbers,
  - First 30 Days,
  - Training Library,
  - SOPs,
  - Who's Who,
  - On The Phone,
  - Brand Kit,
  - Listing Marketing,
  - Requests,
  - JV Partners.
- Admin screens:
  - Overview,
  - Brand and Identity,
  - People and Roster,
  - Roles and Permissions,
  - Training Library,
  - SOP Library,
  - Win the Day,
  - Tool Launchpad,
  - Team Calendar,
  - Integrations,
  - AI Assistant,
  - Audit Log,
  - Marketing Requests config if added as separate screen.

Automated checks to run before handoff:

```powershell
cd C:\Users\17192\Desktop\executive_dashboard\backend
.\.venv\Scripts\python.exe -m pytest -q
```

```powershell
cd C:\Users\17192\Desktop\executive_dashboard\frontend
npm run build:all
```

```powershell
cd C:\Users\17192\Desktop\executive_dashboard
bash frontend/scripts/check-no-mock-data.sh
git diff --check -- .
```

If `git diff --check -- .` reports whitespace in unrelated files, narrow it to the files touched by the current pass and call out the unrelated failures.

Acceptance criteria:

- User acceptance testing approves visual parity with the Utah Life mockup.
- All required integrations work in staging with sandbox or real test credentials.
- All production launch roles can perform their daily workflows.
- No required screen is a dead shell unless the user explicitly approves it as post-launch.

## 19. Phase 17 - Production launch and first-week support

Goal: launch for real users and keep it healthy.

Required launch tasks:

- Provision production tenant.
- Configure production custom domain and SSL.
- Upload final Utah Life mark and font assets.
- Configure all production integrations.
- Import or sync real roster.
- Confirm role/audience mappings.
- Publish live configuration.
- Invite initial users.
- Run production smoke test.
- Monitor logs, metrics, sync jobs, and user support for the first week.
- Keep rollback plan ready.

Launch checklist:

- [ ] Production env vars set.
- [ ] Database migrated.
- [ ] Object storage configured.
- [ ] Secrets manager configured.
- [ ] Custom domain verified.
- [ ] SSO configured.
- [ ] Final brand assets uploaded.
- [ ] Roster synced/imported.
- [ ] Sisu connected and metrics verified.
- [ ] Follow Up Boss connected and Needs You Today verified.
- [ ] Google Calendar connected and event visibility verified.
- [ ] Marketing Requests destination connected and test request delivered.
- [ ] Slack or notification provider connected if used.
- [ ] AI indexed tenant content and answered cited test questions.
- [ ] Audit log verified.
- [ ] Backup job verified.
- [ ] Monitoring alerts active.
- [ ] UAT signoff recorded.

## 20. Screen acceptance matrix

### Intranet Home

Must match the Utah Life mockup in structure and mood. It must show real identity/config, real or zero metrics, real quick-launch tools, honest disconnected states, and the floating Ask entry point.

### Ask Utah Life

Before AI is wired: honest disabled/unavailable shell. After Phase 11: sourced answers with citations, tenant-safe retrieval, rate limits, and escalation/content-gap workflow.

### Win the Day

Configurable lists, daily targets, role/audience availability, persisted completion state, reset by user local day, and no cross-user leakage.

### Sunburst Coaching

Initially a configured shell matching the mockup's dark panel. Production version should use real weekly performance inputs from Sisu/FUB or another selected source, then produce actionable coaching only from available data.

### Team Calendar

Admin-configured calendar settings. Empty before Google Calendar connection. Real events after connection, filtered by audience/role where configured.

### Tool Launchpad

Admin-configured tiles, groups, auth type, destination, role audience, ordering, active state. No hardcoded provider links.

### My Numbers

Sisu-backed or configured integration-backed metrics. Render `0` or unavailable until connected. Do not seed fake business numbers.

### First 30 Days

Configurable onboarding path with persisted per-user progress. Content comes from admin training/SOP configuration or connected sources.

### Training Library

Admin-managed courses/lessons, source metadata, role audience, progress where implemented, source health in console.

### SOPs

Admin-managed categories/SOPs/versions, downloads through proxied storage, acknowledgements if required, source health in console.

### Who's Who

Real roster from admin/manual/SSO sync. Generic avatars allowed until photos are connected. No fake staff outside seed/demo context.

### Brand Kit

Live brand assets from tenant config. Show honest missing-mark state until final assets are uploaded.

### Marketing Requests

Production workflow from Phase 10. Until connected, render local configuration state and do not pretend a destination exists.

### Admin Overview

Real counts, setup checklist, pending changes, recent audit, preview, publish, discard, rollback where applicable.

### Admin Brand and Identity

Tenant brand config, palette validation, logo uploads, domain settings, font/proxy settings, live preview.

### Admin People and Roster

Invite/manual users, role assignment, auth source, active/invited/removed filters, sync status when identity provider is connected.

### Admin Roles and Permissions

Role capabilities, required `console_access=Full` invariant, fail-closed permissions, role/audience usage in content and tools.

### Admin Training Library

CRUD courses/lessons, source type, source URL, state, audience, ordering, health.

### Admin SOP Library

CRUD categories/SOPs, upload/download versions, status, audience if needed, health.

### Admin Win the Day

CRUD/config lists, daily target, source mapping, sort order, active state, validation.

### Admin Tool Launchpad

CRUD/config tiles, groups, auth type, URLs, audience, ordering, active state.

### Admin Team Calendar

Calendar categories, role audience, colors, timezone/week-start/default view, calendar URL/ID, status.

### Admin Integrations

Provider list, credential/config editor, status, connect/disconnect/test/sync, no secrets returned, clear unavailable states.

### Admin AI Assistant

Guardrails, sources, crawl/index state, content gaps, assignments, rates/limits if added.

### Admin Audit Log

Read-only filterable/paginated table covering every write and meaningful system event. No secret leakage.

## 21. API and data implementation notes

Before adding endpoints:

- Check existing `backend/app/routers/console.py` patterns.
- Reuse helper validation functions where possible.
- Use tenant-scoped queries anchored by `p.user.tenant_id`.
- For cross-tenant IDs, return 404/403 without leaking object existence.
- Add tests in `backend/tests/test_console_api.py` or a focused new test file.
- Audit mutating endpoints through `backend/app/services/audit.py`.

Before adding models:

- Add tenant ID and useful indexes.
- Add Alembic migrations if production schema is active.
- Backfill existing SQLite/dev data if needed.
- Update seed scripts idempotently.
- Update schema/serializer tests.

Before adding frontend config:

- Extend API helpers in `frontend/src/console/api.js` or `frontend/src/intranet/api.js`.
- Keep state refresh/query invalidation consistent with existing React Query patterns.
- Prefer existing console UI primitives in `frontend/src/console/ui.jsx`.
- Keep text compact and operational.
- Build full empty/loading/error states.

## 22. Production environment checklist

Minimum env/config categories:

- API base URL.
- Frontend public API URL.
- Database URL.
- Session/JWT signing secret.
- Encryption key for provider credentials.
- Platform/custom domain settings.
- CORS allowed origins and/or origin regex.
- Object storage bucket/account/access keys.
- Email provider credentials.
- Google OAuth/client/service account config.
- Sisu credentials.
- Follow Up Boss credentials.
- Slack app credentials.
- AI provider credentials.
- Vector store configuration if separate.
- Worker/scheduler flags.
- Logging/metrics/tracing endpoints.

Production startup must fail closed if required secrets are unset or known dev defaults are present.

## 23. Open decisions before final production launch

These are not blockers for code organization, but they are blockers for real launch:

- Final Utah Life mark and exact brand assets.
- Final font files and licensing.
- Exact SSO provider and whether Google Workspace groups drive roles.
- Which users/roles get console access at launch.
- Exact Marketing Requests destination and workflow owner.
- Exact Google Calendar account/calendar IDs and whether event writes are needed.
- Sisu account/team/user mapping details.
- Follow Up Boss smart list IDs and task/list mapping.
- Slack workspace/channel/app details if Slack is used.
- AI provider/model/vector store/retention policy.
- Whether prompt/question logs may be retained.
- Custom domain verification flow and owner.
- Production hosting target.
- Backup retention policy.
- Support/on-call owner after launch.

## 24. Definition of done

This build is not completely done until all of the following are true:

- The intranet visually matches the Utah Life mockup closely enough for user approval.
- The admin console can configure every tenant-specific intranet behavior.
- All required production integrations are connected, tested, and observable.
- The AI assistant gives sourced, tenant-safe answers or refuses gracefully.
- Marketing Requests works end to end.
- No required workflow depends on seed edits or code changes.
- No fake data appears in production tenant screens.
- Tenant isolation is covered by automated tests and manual probes.
- Auth, secrets, uploads, and custom domains have passed security review.
- Database schema is migration-managed.
- Production deploy, backup, rollback, and monitoring are documented and tested.
- Backend tests pass.
- Frontend builds pass.
- Browser smoke and visual checks pass.
- Utah Life UAT signoff is complete.

