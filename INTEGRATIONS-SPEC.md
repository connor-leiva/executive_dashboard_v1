# Settings › Integrations — audit and phased build

Reference mockup: `Integrations-reference.html` (Claude Design export, currently in `~/Downloads`).
It is a **template with `{{ }}` bindings**, so its markup is the structure and its `<script>` is the
sample data. Decoded copies used for this audit: the markup is the `<x-dc>` block of the bundle's
`__bundler/template`, the data model is the `Component` class beneath it.

Current implementation:

| Piece | Where |
|---|---|
| The whole page | `frontend/src/Settings.jsx` → `IntegrationsPage` (1094), `SourceCard` (964), `EntityRow` (927) |
| Per-provider UI data | `MONO` (1042), `DESC` (1043), `SAMPLE_VIEW` (1068) |
| Shell + nav | `SettingsShell` (65), `subnavFor` (50) |
| Payload builder | `backend/app/services/integrations_view.py` → `ORDER` (19), `META` (21), `CONNECTABLE_KIND` (61), `build_integrations_view` (123) |
| Wire format | `backend/app/schemas.py` → `EntityRow`, `SourceOut`, `IntegrationsOut` |
| Endpoints | `backend/app/routers/integrations.py` (`/settings/integrations`, `/integrations/*`, `/sync/*`) |
| Guard that ties the two halves together | `backend/tests/test_provider_ui_is_complete.py` |

---

## 1. Audit — what the mockup changes

### 1.1 Structure

| # | Mockup | Today | Size |
|---|---|---|---|
| 1 | Real vendor logos in 34px tiles | Two-letter monograms (`MONO`) | medium |
| 2 | Stat strip: *healthy · needs attention · entities mapped* + "Auto-sync in 23 min" | One right-aligned line, "N of M sources healthy" | small |
| 3 | Attention banner naming the failing entity, with **Reconnect** | Nothing above the list; the note hides inside the card | medium |
| 4 | Filter pills **All / Needs attention / Healthy** | None | small |
| 5 | **Connected · N** and **Available · N** as separate sections; available rows are dashed cards | One flat list, disconnected sources inline | medium |
| 6 | **+ Add source** button | None | small (needs a decision) |
| 7 | Row: logo · name · tag chip (`5 entities`, `Legacy`) · category meta · status pill · relative age · caret | Monogram · name · feed dots · collapsed line · status pill · chevron | medium |
| 8 | Status words: **Healthy / Degraded / N needs action** | `ok / stale / attention / disconnected` | small |
| 9 | Expanded: bordered entity table — accent swatch, name, `ID <realm>`, state, **Sync/Reconnect**, **⋯** | Entity rows for QBO only, no overflow menu | large |
| 10 | **Feeds** chips + `Last run · 4 records · 4.1s · 7 min ago` + **+ Connect another entity** | `Provides` chips + `last_run` + a Connect-another button | small |
| 11 | Left nav badges (Businesses 6, Integrations 7) | No badges | small |
| 12 | Top bar: `← Command Center · Settings / Integrations` + workspace chip | `← Command Center · Settings` only | small (chip needs a decision) |

### 1.2 The one structural disagreement: vendor vs provider

The mockup shows **six rows**, grouping by vendor:

```
QuickBooks        5 entities   ULRG + Team, Spring B, Sympli Mortgage, The Forum, beCollective
Sisu                           ULRG + Team
Go High Level     2 entities   The Forum, beCollective
Arive                          Sympli Mortgage
Stripe            2 entities   The Forum, beCollective
GHL charge labels Legacy       Shared mapping
```

The repo models these as **ten providers**, one card each: `qbo, sisu, fub, ghl, ghl_bc, arive,
stripe_legacy, stripe_bc, ghl_legacy, meta_ads`. So the mockup's "Go High Level · 2 entities" is our
`ghl` + `ghl_bc`, and its "Stripe · 2 entities" is `stripe_legacy` + `stripe_bc`.

**This is presentational, and it should stay presentational.** Those providers have separate tokens,
separate configs and separate sync paths; merging them in the data model would be a real regression.
The grouping belongs in the view builder: give every provider a `family`, and render a family with
more than one member as one row whose sub-rows are its members.

The payoff is that QuickBooks stops being a special case. Today `entities[]` is populated for `qbo`
only and every other provider is a bare card; after this, **the entity list is how every source
renders**, and a single-connection source is simply a family of one.

### 1.3 Findings the mockup did not ask about

**a. One customer's businesses are named to every workspace.** `META` (integrations_view.py:21)
hardcodes `"Go High Level · The Forum"`, `"Stripe · beCollective"`, `"Legacy Stripe · The Forum"`,
`"Old GHL · Charge labels"`. `ORDER` is iterated for *every* tenant, so a brand-new workspace's
Integrations page already lists Spring's programme names. Today that is buried in a long flat list;
the moment sources split into **Available · N**, it becomes a tidy catalogue of another customer's
companies. This is the same defect class as the `DEFAULT_BIZ` and `CONNECTABLE` maps that were
removed earlier — the role is durable, the customer's name for it is not.

Fix with the family work: a source's display name is `vendor + the resolved business's name`
(`roles.pick` already resolves the business per tenant), and a legacy/secondary provider with no row
for this tenant is not offered at all.

**b. "Feeds" means two different things.** The mockup's **Feeds** chips are our `provides`
(*Profit & Loss, Balance Sheet, Scorecard*). Our `feeds` is a list of **business keys** rendered as
coloured dots (`FeedDots`, 803). Once the entity table lists the businesses by name with their accent
swatch, the dots are redundant — drop them, and let the UI label `provides` as "Feeds". Do not rename
the API field in the same change; one meaning per commit.

**c. The mockup's palette and fonts are not ours.** It hardcodes `#0e2b22 / #c2603f / #f7efe8` and
`Outfit / DM Mono`. This product is themed per workspace through CSS variables (`theme.js` tokens,
`--font-display/text/data`, the Appearance panel). **Copy the structure, not the values.** Mapping:

| Mockup | Use |
|---|---|
| `#f7efe8` page, `#fdfbf7` rail | `T.parchment`, `T.white` |
| `#e8e0d3 / #e6ded1 / #efe7da` rules | `T.line` |
| `#0e2b22` ink, `#2b4a3d` | `T.ink` / `T.evergreen`, `T.secondary` |
| `#5c7367 / #6d8579` | `T.slate` |
| `#8b9c92 / #a4b1a8 / #9aa9a0` | `T.muted` |
| `#5f9c82` healthy, `#eef4f0` its ground | `T.meadow`, `T.meadowBg` |
| `#d8a93a` degraded, `#fdf6e8` | `T.daffodil`, `T.daffodilBg` |
| `#c2603f` accent, `#fdf1ec` | `T.poppy` / `T.poppyText`, petal wash |
| `DM Mono` (ids, ages, eyebrows) | `var(--font-data)` — Archivo, tabular figures |
| `Outfit` | `var(--font-display)` / `var(--font-text)` |

Entity swatches take `Business.accent`, which already exists per workspace and is already editable.

**d. `healthy` counts oddly.** integrations_view.py:217 reads
`if status in ("ok", "stale"): healthy += 1 if status == "ok" else 0` — equivalent to counting `ok`,
written as though it meant something else. The mockup's "sources healthy" is *all − needs attention*,
which counts `stale` as healthy. Pick one and name it in the schema (§2).

**e. Preview parity is load-bearing.** `SAMPLE_VIEW` (1068) carries a comment explaining that a
provider missing from it is why a white screen could not be caught locally. Every phase that changes
the payload changes the sample in the same commit.

---

## 2. Payload changes (one place, additive)

`SourceOut` gains:

```python
family: str            # vendor key: "qbo" | "sisu" | "ghl" | "stripe" | "arive" | "fub" | "meta"
category: str          # "Financials" | "Production" | "Marketing" | "Mortgage" | "Payments" | "Mapping"
meta: str              # short line under the name: "Financials · profit & loss, balance sheet"
tag: str | None        # "5 entities" | "Legacy" | None  — server-side, so it can never disagree
ago: str | None        # compact "7 min" for the row's right column (fresh stays for the drawer)
```

`EntityRow` gains:

```python
accent: str | None     # Business.accent — the swatch
provider: str          # which provider this sub-row belongs to (a family row mixes them)
actions: list[str]     # what ⋯ offers here: "sync" | "reconnect" | "edit" | "disconnect" | "remove"
```

`IntegrationsOut` gains:

```python
entities_mapped: int         # connected integration rows across all sources
needs_attention: int         # sources whose status is attention or stale
alerts: list[Alert]          # [{title, detail, action: {kind, provider, business_key}}]
```

`alerts` is server-built on purpose: the banner names a specific entity ("Spring B lost its
QuickBooks connection") and a specific button. The client should not be re-deriving that sentence
from a status enum, which is how the collapsed-line logic in `SourceCard` (966) already drifted.

Nothing is removed in this pass. `feeds`, `provides`, `fresh`, `status` all keep working, so the
current page renders unchanged against the new payload — which is what makes Phase 1 shippable alone.

---

## 3. Decisions needed before Phase 3

1. **"+ Add source" — what does it do?** Every provider we support is already on the page, so the
   button has no catalogue to open. Options: (a) a modal picker listing the not-connected providers,
   (b) scroll to and flash the **Available** section, (c) drop it. *Recommend (a)* — it is the only
   reading that survives a long Available list, and it gives somewhere to put a "request an
   integration" line later.
2. **The workspace chip.** A user belongs to one workspace on its own subdomain, so a switcher has
   nothing to switch. *Recommend*: render the workspace's logomark + name as identity, linking to
   `app.axcion.io` (the workspace finder) rather than a dropdown.
3. **The Integrations nav badge.** The mockup shows total sources (7). *Recommend*: the
   needs-attention count, shown only when non-zero — a badge that is always there is furniture.
4. **Legacy/secondary providers for other workspaces.** `stripe_legacy`, `ghl_legacy`, `ghl_bc` are
   Spring's. *Recommend*: offer them only where a row already exists, or behind "+ Add source →
   Show legacy connectors". Needs a yes before Phase 3, because the Available section makes them loud.
5. **Vendor logos.** *Resolved 2026-09-22 — a set was supplied (`logo-preview (1).html`).*
   - **Shipping:** QuickBooks + Stripe (simple-icons, CC0-1.0, inline SVG paths), Follow Up Boss,
     Sisu, Arive (PNG). Sisu and Follow Up Boss were checked against the marks already in
     `frontend/src/intranet/assets/logos/` and match — the new ones are icon-only, which is what a
     21px tile wants.
   - **Held back — Go High Level.** Its own preview notes that the brand kit lists Space Blue and
     White as the approved logo, so the supplied file "may be the product icon", and that **their
     terms require written permission before a commercial product displays their mark**. It keeps
     the monogram until both are resolved. `ghl_legacy` shares the vendor and the same hold.
   - **Not supplied — Meta Ads.** Keeps its monogram.
   - The preview embeds 60px copies, not the shipped assets (it cites a 173x180 master for Sisu).
     Fine at 1x and 2x, 3px short of a 21px tile at 3x. Swapping in the masters is a file copy;
     no code changes.

---

## Phase 1 — The payload learns the new shape

**Backend only. Nothing visible changes.**

- Add the fields in §2 to `schemas.py`.
- In `integrations_view.py`: add `FAMILY`, `CATEGORY` and short `META["meta"]` per provider; compute
  `tag`, `ago`, `entities_mapped`, `needs_attention`, `alerts`; put `accent`, `provider` and
  `actions` on every `EntityRow`.
- **Moved to Phase 4:** giving single-connection sources a one-row `entities[]`. `SourceCard`
  (Settings.jsx:1023) keys its buttons off `s.entities?.length` — a Sisu row with one entity would
  swap "Sync now" for "Connect another entity" before the drawer that renders it exists. It lands
  with the drawer.
- **Moved to Phase 3:** the per-tenant display names (§1.3a). Phase 1 leaves `name` untouched so
  the page renders identically against the new payload, and adds `vendor` + `secondary` beside it;
  Phase 3 switches the UI to those and drops the customer names with the same commit.
- Decide and document `healthy` (§1.3d).

**Done when:** `GET /settings/integrations` carries every new field; the existing page still renders
untouched against it; `test_integrations_view.py` covers `tag`, `alerts`, `entities_mapped` and a
two-provider family collapsing to one source.

**New guard:** every provider in `ORDER` has a `family`, `category` and `meta`, asserted from `ORDER`
itself — the shape that `test_provider_ui_is_complete.py` already uses for `DESC` and `MONO`.

---

## Phase 2 — The shell

- Breadcrumb `Settings / Integrations` in `SettingsShell` (65); the page title comes from the route.
- Nav badges in `subnavFor` (50) — Businesses count, Integrations attention count (decision 3).
- Content width 1000 → 1100 to match the mockup's measure.
- Workspace identity chip (decision 2).
- **Keep Appearance in the nav.** The mockup's nav omits it; the mockup is older than that panel.

**Done when:** every Settings route shows the right breadcrumb, badges reflect real counts, and the
page still fits at 1280 and scrolls cleanly at 375 (`project-mobile`).

---

## Phase 3 — The list

- `vendor-logos.js` moves from `frontend/src/intranet/` to `frontend/src/vendor-logos.js` (shared;
  the intranet keeps importing it from there). Add a **provider → logo** map for integrations rather
  than reusing `logoFor(name)`: our names are `"Legacy Stripe · The Forum"`, whose first word is not
  the vendor, so name matching would silently miss. Monogram stays as the fallback.
- Stat strip, attention banner, filter pills, **Connected / Available** split, and the new row anatomy.
- Status vocabulary: `ok → Healthy`, `stale → Degraded`, `attention → N needs action`,
  `disconnected → moves to Available` (no pill).
- Drop `FeedDots` (§1.3b).
- Update `SAMPLE_VIEW` in the same commit (§1.3e).

**Done when:** measured on the production build (`npm run build` + preview, per
`feedback-verify-prod-build`) — logos resolve for every connected provider, the filter pills change
the visible set, the banner names a real entity, and the columns line up row to row.

---

## Phase 4 — The expanded panel

- Entity table: accent swatch, name, `ID <realm>`, state, primary action (**Sync** / **Reconnect**),
  **⋯** menu driven by `EntityRow.actions`.
- Feeds chips from `provides`; `Last run · N records · Ns · ago`; **+ Connect another entity**.
- One renderer for every source, QuickBooks included (§1.2).

**Done when:** a QBO entity, a GHL family sub-row and a single-connection source all render through
the same component, and ⋯ offers only the actions the server listed.

---

## Phase 5 — The actions behind the new controls

- **+ Add source** (decision 1).
- ⋯ wiring: reconnect → existing OAuth start; edit → the provider's existing form; disconnect and
  remove → existing endpoints, keeping the current confirm copy, which is careful about what
  disconnect keeps versus what remove destroys.
- Banner **Reconnect** → the same path as the entity's own button.

**Done when:** no control is decorative; `test_provider_ui_is_complete.py` extends to assert every
`EntityRow.actions` value the server can emit has a handler in the UI — the same dead-button class
that shipped three times in one week.

---

## Phase 6 — Parity and guards

- Sample payload matches the live shape field for field, every provider present.
- Guard tests: provider→logo coverage; family grouping; actions coverage; no raw hex in the new code.
- Verify against real rows on prod (read-only) before and after: source count, attention count,
  entities mapped.
- Re-check 1280 and 375.

---

## Built — and one deliberate deviation

Phases 1-6 shipped 2026-09-22 (`b93c64f`, `091e316`, `b1a1150`, `b4d7534`, and this one).

**The vendor MERGE was not built, and should not be.** §1.2 proposed rendering a family with more
than one member as a single row ("Go High Level · 2 entities"). Building the rest of it showed why
that is wrong here: the mockup's one row assumes ONE credential serving two locations, and ours are
two separate connections with separate tokens, separate configs and independent failure. Merging
them would claim they are one thing, and it would have to hide each location's own configuration
summary behind a second level of disclosure the mockup does not have. What shipped instead is the
row title disambiguating by the business each serves — "Go High Level · The Forum" and
"· beCollective" — which says the true thing: same vendor, two connections. Say the word if you
would rather have the drawing.

Everything else in §1.1 is in, plus three findings the mockup did not ask about:

* `META`'s hardcoded customer names are gone; a row is titled by vendor and by the business it
  serves, which is tenant data.
* `CORE_ENTITIES` is gone. It listed one customer's three business keys to decide what "Remove
  entirely" may touch, while `delete_qbo_entity` already enforced the real rule per tenant.
  Checked against production: for springb the rule reproduces that hardcoded set exactly
  (becollective and the_forum are removable, the other three are not, because a non-QBO source is
  attached). For **utah-life**, one business and no protection under the old rule — the menu was
  offering to remove the only business holding that workspace's dashboard.
* The alert banner was drawn in `T.poppy`, which is the platform's ACCENT and a cadet green, so a
  failure read as reassurance. The palette's error token is `poppyText`.

## Not in the mockup, keep anyway

- `needs_kind` and its "add a lending business first" message — it is what stops a dead Connect
  button in a workspace with no business of that role.
- `LegacyDeltaPanel`, the QBO routing editor (`QboEntityForm`), the GHL config summary.
- The confirm dialogs' wording on disconnect vs remove.
- The no-fall-through modal chain (Settings.jsx:1267) — an unhandled provider must render nothing
  rather than the wrong form.
