# UI audit: an unslop pass over the whole product

Date: 2026-11-09. This started as a recommendations pass.

**Status.** Most of it is now applied on this branch, in one commit per area: the dashboard type sweep, the dashboard (Portfolio, shell, heroes, motion, bugs 1, 2, 11 and 12), the intranet (bugs 6 and 7), the console (bugs 3, 4, 5, 8 and 9), and the marketing site (bug 10). The Portfolio's source tags come from the API (`Scorecard.source`). After review, the in-app hero cards keep their bokeh or workspace background images, so that part of Pass 2 is not applied. Still open:

- The Forum restructure in Pass 7: one lead chart, tiles without mini charts, and the renewal calendar as a table.
- Moving the source tag onto the business pages' tiles, which still show the `↗` arrow.
- Settings › Appearance's sticky preview column.
- The operator console's stat labels.
- The three bigger suggestions at the end.

**What was looked at.** All five frontends ran locally:

- The dashboard on its bundled sample data: 11 rail tabs, Settings, and a phone-width view.
- The intranet in preview mode: 13 pages.
- The admin console, signed in to a seeded workspace: 15 pages.
- The operator console: 5 views.
- The marketing site: Home, Features, About and Pricing, plus a phone-width view.

That is about 50 screens, each checked against the unslop signs list. Each finding below cites a `file:line` and gives a value to change it to.

Screenshots from the run are in this folder:
[portfolio](dashboard-portfolio.png), [books](dashboard-books.png), [forum](dashboard-forum.png),
[mobile](dashboard-mobile.png), [intranet](intranet-home.png), [console](console-overview.png),
[marketing hero](marketing-hero.png), [pricing](marketing-pricing.png).

---

## Verdict

**Light slop, so this is a polish pass and not a redesign.** Axcion is a real brand:

- Colours: Cadet `#3F6B66`, Ink `#16201F`, Sage, Haze and Paper, plus a derived 30-token palette.
- Type: Space Grotesk, Instrument Sans and Archivo for tabular figures.
- The code carries a lot of deliberate reasoning about contrast and type.

None of the usual slop is there: no purple gradients, no emoji icons, no sparkles, no gradient text.

What is left falls into three groups:

1. **Too many small decisions made one at a time.** The frontend uses 34 font sizes, with about 1,000 uses of fractional ones (12.5, 11.5, 10.5, 13.5). There are about 440 uses of text at 11px or smaller, 283 uppercase tracked labels, 224 headings at weight 700 to 900, and padding values like 26, 22, 15, 9, 7 and 5.
2. **Decoration in the chrome.** Blurred bokeh plates sit behind data, cards lift on hover, sparklines are tucked into stat cards, and a red "error" colour is used to decorate section labels.
3. **Five products that look like three.** The dashboard, operator and marketing site use Axcion. The intranet uses slate blue, a warm taupe page, DM Sans and Title Case. The console uses warm paper, black, DM Sans and ALL CAPS. A person who opens the app switcher crosses between them.

There are also about ten real bugs. They are listed first because each one is small and visible.

---

## Direction

- **Product.** One board where the owners of a multi-entity real estate group see revenue, cash and production from QuickBooks, Sisu and Follow Up Boss, with every number traceable to the record it came from.
- **Character.** Exact, accountable, unhurried.
- **World.** The ledger and the closing binder: ruled paper, dark green-black ink, a figure that has been checked. The Books Statement already lives here (`LEDGER` in `theme.js`).
- **Moments.** (1) The Portfolio composition bar, revenue split by entity, as the one rich chart on the main screen, where each colour means one business. (2) The drill from a figure into its source rows. That is the product's promise ("Every number Axcion shows you came from somewhere"), and the marketing site already animates it.
- **Signature.** The **source tag**: a small muted mono tag after a figure (`QBO`, `Sisu`, `FUB`) that opens the audit drawer. It looks the same on every screen. Today that role is spread across the `↗` corner arrow, grey source pills on panel headers ("Sisu", "Follow Up Boss") and dotted underlines. Pick one form and use it everywhere.

The Axcion palette and type stay. Nothing below asks for new colours or new families.

---

## Fix first: bugs found during the pass

| # | Where | What is wrong | Fix |
|---|---|---|---|
| 1 | `frontend/index.html:30` | `body` keeps the browser's default 8px margin, so the whole dashboard sits inside a white frame and the dark rail does not touch the window edge (visible in every dashboard screenshot) | Add `body { margin: 0; }` next to the existing rule |
| 2 | `src/books/BooksHome.jsx:116-118` | The "Draft · awaiting CFO sign-off" pill sets `background: T.daffodil` and `color: T.daffodilText`. Both resolve to `#7E5A1C`, so it renders as an empty gold bar | `background: T.daffodilBg` |
| 3 | `src/console/constants.js:504` | `ask: "Ask Utah Life"` is hard-coded, so the Spring workspace's console says "Ask Utah Life" | Build it from the workspace name, or use `Ask` |
| 4 | `src/console/constants.js:518` and the console sign-in | The tenant field defaults to `utah-life.axcion.io`, another customer's host | Leave it empty with a placeholder `team.axcion.io` |
| 5 | Console top bar | The "Viewing as" role switcher runs under the Tenant field, so "Listing Agent" is cut to "Listin" | Let the role group scroll or collapse into a select. Move Tenant and Use tenant into a dev-only menu |
| 6 | Intranet › My Settings | Shows `Unexpected token '<', "<!doctype "... is not valid JSON` | In preview mode, render the empty state. In live mode, map a non-JSON response to "Settings could not load. Try again." (`src/intranet/MySettings.jsx:95`) |
| 7 | Intranet › Brand Kit, `src/intranet/IntranetApp.jsx:2644-2645` | Shows "TT Drugs proxy" and "TT Norms Pro proxy" to end users | Show the real family names, or the workspace's pairing |
| 8 | Console › AI Assistant and Calendar Categories | A spinner keeps turning above "Nobody has asked the assistant anything yet." Loading and empty show at the same time | Render one or the other |
| 9 | Console, no-access screen | Same problem: a spinner keeps turning under "You don't have access to this console." | Drop the spinner once access is denied |
| 10 | `src/marketing/content.js:39` and Pricing | The public page shows "Placeholder pricing… switch `PRICING_IS_PLACEHOLDER` to false", with `$—` prices | Gate the notice on `import.meta.env.DEV`. Until prices are final, have the tiers say "Talk to us" instead of `$—` |
| 11 | `src/Onboarding.jsx:636` | The empty state tells an end user to run `python -m scripts.seed_onboarding --tenant <slug> --spec <plan.json>` | "No onboarding plan yet. An admin creates one in Settings." Keep the command in the operator docs |
| 12 | `src/sampleData.js:51,109,139` | Sample mode still uses `#FFDD1F` and `#FFBA9F`, the 1.4:1 yellow `palette.js` was written to retire. It is the first thing a demo shows (the bright yellow bar in the Portfolio hero). The hero also says "Portfolio · March 2026" and ULRG says "July 2026" while the period bar says Nov 2026 | Use palette tokens for sample accents and build sample period labels from the selected window |

---

## Pass 1: Spacing

**Findings**
- Off-scale values throughout: main padding `26` (`CommandCenter.jsx:1471`), card padding 22, 18 and 15, nav padding `10px 10px` with `marginTop: 2`, and gaps of 7, 9 and 14.
- On the Forum and beCollective pages, sibling tiles in one row have different paddings and heights (the four "pulse" tiles, and the four Cash & Billing tiles where one is raised and white while three are flat).
- Intranet rail: the section labels (Workspace, Learn, Team) start at x=38 while their items start at x=51, so the column breaks. "Team Intranet" sits 8px above "Workspace", the same distance as between items, so the header reads as part of the list.
- Console rail: 44px rows with 28px side padding. It is the loosest rail of the three.

**Recommend**
- Main content padding `32` desktop, `16` phone. Card padding `20` for compact tiles and `24` for panels. Gaps of `12` between tiles and `24` between panels.
- Dashboard rail rows: 36 tall (`padding: 8px 12px`) with a 2px gap. Divider block: `24` above, `8` below.
- Intranet rail: section labels on the same x as item text, `24` above each label and `8` below. Push "Team Intranet" `24` clear of the first section.
- Console rail: rows 36 tall, side padding 16. That matches the dashboard so the app switcher does not visibly jump.
- Replace odd values with steps from the scale `4 8 12 16 20 24 32 40 48`. A codemod over `padding:`, `gap:` and `margin` literals catches most of them.

## Pass 2: Colour

**Findings**
- **A semantic colour used as decoration.** `Eyebrow` (`CommandCenter.jsx:129-136`) colours every section label with `T.poppyText`, which is `SEMANTIC.error #93413A`. "At a glance" is brick red, so the label reads as an alert.
- **Bokeh behind data.** `ribbedHero` lays Axcion's bokeh plate (`palette.js:294-364`) under the Portfolio hero, the ULRG projection card, the Forum and beCollective cash cards, Books, Ads and the flywheel callout. It lowers contrast on the figures and puts the same texture on every page. The "Axcion" `HeroMark` watermark in the hero corner collides with the period label at phone width.
- **Low-contrast figures and captions.**
  - "Revenue left on the table **$438,900**" and the flywheel "**~$5,266,800**" are Cadet on Ink, **2.78:1** (`CommandCenter.jsx:845`, `color: T.poppy`).
  - Muted captions ("19% margin", "month to date", `NEUTRAL[400]`) are **3.2:1** on the page, at 11px.
  - The intranet's eyebrow and placeholder colour `--faint #9aa1a5` is **2.1:1** on its page.
  - The intranet rail section labels are **3.1:1**.
- The beCollective cash card is a mint plate where Forum's is ink. The same component is shown in two tones depending on the accent.
- The Ads hero, Books hero and Portfolio hero are three different dark plates on three screens. There is nothing to choose between them.

**Recommend**
- `Eyebrow` (and `PanelLabel` where it borrows the same colour): `T.tertiary` on light and `T.onDarkMute` on dark. Keep `poppyText` for real negatives only.
- Bokeh: keep it for **the sign-in plate and the marketing CTA band**, where it is brand. In the app, the dark heroes become a flat `T.evergreen` surface with a 1px hairline at 8% white. Drop `HeroMark` from in-app heroes.
- Large figures on ink: `T.onDark`, with the accent kept for the one changed number. Muted caption text: use `NEUTRAL[500] #5F645C` (5.6:1) instead of `NEUTRAL[400]`. Intranet `--faint`: `#5f676c` (4.7:1 on its page). Its current `--muted #6e767b` is only 3.8:1, so darken that to the same value and merge the two.
- One plate tone per component. A cash card is ink everywhere, and the programme accent shows in the top hairline and the series colour.

## Pass 3: Icons

**Findings.** The icons are clean: one `ProductIcon` set, 18px, taking the label colour. The rail already separates modules (icons) from businesses (colour squares), and that is a good rule. The remaining issues:
- The "Referral Flywheel" icon renders as `( )`, which reads as a missing glyph.
- The intranet "Ask" buttons use a plain grey circle as the icon, both in the top bar and in the floating button. It reads as a missing avatar.
- The Sunburst card's arrows are magenta, the only magenta in the product.
- The Ads "ads themselves" cards use two-letter monograms ("KW", "KS") as placeholder images.

**Recommend**
- Draw a real flywheel mark: two arcs with arrowheads, at the same stroke.
- Replace the grey dot with a line icon from the existing `ProductIcon` set, or drop it and keep the label.
- Sunburst arrows take `--muted`.
- Where the ad creative exists, show its thumbnail. Otherwise use a neutral frame with the format ("1080×1080 · static") and no monogram.

## Pass 4: Copy

**Findings**
- **Intranet Title Case and trailing periods**: "Good Evening, Preview.", "Your Weekly Check-in is Ready.", "Open My Tools", "All Tools ->".
- **A greeting headline** on the intranet home (`IntranetApp.jsx:73-75`).
- The admin console shows raw event keys as titles: `auth.login`, `coa.seed_standard_chart`, with actor "Unknown".
- Long engineering explanations sit in places where a user needs one line:
  - "Booked lights up once QuickBooks connects (split by class). Until then the cash lens to the right is the real-time truth…"
  - "Sunburst opens a conversation for a person on the roster, and this account is not one yet."
  - The Ads "What to trust" section.
- Figures in one screen use mixed formats: `$109K`, `$420,000`, `$84.0K`, `$1.23M` and `$14.2M` all appear on the Portfolio and Forum pages. "+8% MoM" is the only change on the Portfolio hero that says what it compares to. The tiles show changes ("+2", "+4 pts") without a reference.
- Pricing has a "Most teams" badge with no source.

**Recommend**
- Intranet: sentence case everywhere and no periods on headings. Title is the page or date ("Monday, November 9"), not a greeting. "Your weekly check-in is ready". "All tools →".
- Console audit log: map event keys to sentences ("Signed in", "Portal created by Local Ops", "Standard chart of accounts loaded"). Show "System" when there is no human actor.
- Cut each explanatory paragraph to one sentence under the figure. Move the rest behind an info tooltip or into the audit drawer, which is where "how was this computed" belongs.
- One money format per screen: whole dollars (`$420,000`) in hero figures and tables, and compact (`$420K`) only in tiles narrower than about 200px, with no trailing `.0`. Every delta names its base: "+2 vs last month", "+4 pts vs last year".
- "Most teams": keep it if it is true from real sign-ups, otherwise change it to "Recommended".

## Pass 5: Type

**Findings**
- 34 font sizes. The common ones are 12.5, 12, 11.5, 13, 11, 10.5 and 13.5. About 440 uses are at 11px or smaller, including 9 and 9.5. The worst files are `Settings.jsx`, `AIEmployeeDetail.jsx`, `ForumView.jsx`, `CommandCenter.jsx` and `ulrg/Recruiting.jsx`.
- 283 uppercase tracked labels: `letterSpacing` from 0.08 to 0.18em at weight 700 ("COMMAND CENTER", "AT A GLANCE", "PROJECTION", "HOW EVERY NUMBER TRAVELS", "ADMIN CONSOLE", "WORKSPACE"). Most are in `intranet/ui.css` (62) and `console/console.css` (55).
- 224 uses of weight 700 to 900. Marketing display type is 700 at -0.042em (`marketing/ui.jsx:114-125`).
- Two type systems outside Axcion: the intranet uses Playfair, DM Sans and Archivo, and the console uses DM Sans.

**Recommend**
- One scale: **12 / 13 / 14 / 16 / 20 / 28 / 40**. Map 9 to 11.5 → 12, 12.5 and 13.5 → 13, and 14.5 and 15.5 → 14 or 16. Nothing under 12 except the mono source tag (11, `tabular-nums`, +0.01em).
- Sentence-case labels at 12–13px, weight 500, `T.tertiary`, letter spacing 0. "Command Center" under the wordmark becomes 12px, weight 400, `onDarkMute`.
- Weights 400 and 500, with 600 for figures only. Marketing display goes to 500 at -0.03em. That is a brand-guide decision (§07 currently says 700), so ask before changing it.
- Move the intranet and console to the workspace pairing (`--font-display`, `--font-text`, `--font-data` from `typefaces.js`). The intranet's comment already calls DM Sans a proxy.

## Pass 6: Layout

**Findings**
- Dashboard content is capped at `maxWidth: 1100` (`CommandCenter.jsx:1471`) and left-aligned. At 1440 that leaves about 120px empty on the right, and at 1920 about 600px.
- The Portfolio page has 8 equal stat tiles, then 3 equal business cards, then Sympli alone on a second row. The grid has no rhythm and the orphan reads as an error.
- On a phone, `.cc-score` drops to one column at 560px (`CommandCenter.jsx:1338`), so 8 tiles stack into about 900px of scrolling before the first business.
- Settings › Appearance puts a 320px form column next to a mostly empty right column.
- The marketing hero is centred and stacked: eyebrow, two-tone headline, subhead, button, then a huge free-floating number.
- The sign-in plate (console, and the dashboard via `AuthShell`) is a plain dark half with only "Powered by Axcion" in its corner.

**Recommend**
- Raise the cap to `maxWidth: 1440` and keep it left-aligned beside the rail.
- Portfolio: 4 tiles in one row (Combined profit, Total GCI, Closed units, Under contract). The other 4 move into the business cards they belong to (Loans funded to Sympli, Active members to Forum). The 4 business cards go in a 2×2 grid, or a 4-up row at ≥1280.
- Phone: `.cc-score` stays at 2 columns down to 360px.
- Appearance: put the live preview (the derived swatches and contrast table) beside the form and make it sticky, so the right column is in use.
- Marketing hero: split it, with the headline left-aligned on the left and the product preview (already built for the scroll sequence) on the right at ≥50% width. Keep the scroll sequence below it as section two. One primary CTA (Book a demo) plus "Start free trial" as a text link. The nav keeps one button.
- Sign-in plate: this is where bokeh belongs, so fill it. If a tagline is set, it sits on the calm part of the image.

## Pass 7: Details

**Findings**
- Sparklines inside the Portfolio business cards (`Spark`, `CommandCenter.jsx:84`, used at `:429`). The Forum card's sparkline is a yellow zig-zag that reads as noise.
- The Forum and beCollective tiles carry mini-charts too: a sparkline on Active Members, a stack of bars on Recruiting Pipeline, a progress bar on Renewals and a ring on Event Readiness.
- The Forum page has three charts of similar weight: Growth, Cash Flow bars and Renewal Calendar. None of them leads.
- Cash Flow bars are tall rounded pills with a gradient fill (`linear-gradient` in `ForumView.jsx`).
- Coloured 3px top borders on the business cards, coloured 3px left borders on the active nav and the Forum P&L card, and coloured left borders on the Binder chips. Three accent devices sit on one screen.
- Every chip is a full pill ("Booked Lens", "QuickBooks", "Live · Go High Level"), and button shape is a workspace setting (Pill or Square).

**Recommend**
- Remove `Spark` from the business cards. Each card becomes name, status, Revenue, and NOI with margin.
- Tiles are numbers only: label, figure, and change against its base. The one place a mini-graphic earns its spot is Event Readiness (days to go), as a plain "75 days · 40% ready".
- Forum: Members & Growth is the main chart, full width and 320px tall, with the mirrored joined/lost bars kept. Cash Flow becomes a narrower bar chart with square-topped bars, `T.evergreen` actuals and outlined projections, and no gradient. Renewal Calendar becomes a short table (month, count, $).
- Use one accent device per surface: the 3px top border on business cards stays, and the left border on the active nav goes (use the surface step and 600 weight).
- Radii: controls 8, chips 6, cards 14, panels 16. Inner = outer − padding, never below 4.
- Make the source tag from the Direction the one way a figure links to its audit. Retire the scattered `↗` corner arrows.

## Pass 8: Visuals

The visuals are mostly right. The app does not need generated images. The moments are the composition bar and the drill. Recommendations:
- Draw the **Portfolio composition bar** with more care: 16px tall, 2px gaps between segments, the label inside each segment only when it is ≥ 64px wide, and the legend as a row of name, figure and share with tabular figures. This is the one chart that gets rich colour. Every other chart uses the evergreen ramp plus one accent.
- **Marketing scroll sequence**: keep the number landing in the table (it is the product's promise, shown). Remove the blurred blob that floats behind the number while it travels.
- **The photo on Home** ("Built by operators") is real and earns its place. Keep it.

## Pass 9: Motion

**Findings**
- Hover lifts on cards and buttons:
  - `CommandCenter.jsx:1324` (`.cc-card:hover { translateY(-2px) }` plus a big shadow)
  - `ForumView.jsx:594, 704, 761` (−2 to −3px)
  - `ForumView.jsx:689` (`.chip` scale 1.06)
  - `auth/AuthShell.jsx:51`
  - `Assistant.jsx:122`
  - `marketing/ui.jsx:357`
- The easings are a mix of `ease`, `linear` and defaults, at durations of .12s, .15s and .22s.

**Recommend**
- Hover becomes a surface step: a white card goes to `T.page` and its border goes up one step. No translate, no scale, no shadow growth. Press: `transform: scale(.98)` for 120ms.
- One curve, `cubic-bezier(0.22, 1, 0.36, 1)`, at 150ms for hover and 200ms for menus and the drawer. The skeleton shimmer can stay, since loading is live.

---

## Per app, in one line each

- **Dashboard.** The strongest of the five. Fix bugs 1, 2, 11 and 12, the eyebrow colour, the bokeh in heroes, the tile rhythm and the type scale.
- **Intranet.** It looks like a different product. Bring it onto the Axcion tokens and type, fix the copy pass (Title Case, greeting, periods), and fix bugs 6 and 7.
- **Admin console.** A third look. The top bar needs the most work: tenant and role controls belong in a dev menu, and fix bugs 3, 4, 5, 8 and 9. Make audit events readable. Sentence-case the labels.
- **Operator.** Closest to done. Sentence-case the six stat labels and let the cards size to their content: the MRR "—" card is as wide as the others with nothing in it.
- **Marketing.** Fix bug 10, split the hero, keep the bokeh in the CTA band, and settle the "Most teams" claim.

## Bigger suggestions (not part of the polish)

1. **One token set for all five apps.** `palette.js` already derives 30 tokens from 5 seeds and `typefaces.js` already has pairings. If the intranet's `ui.css` and the console's `console.css` read the same `--t-*` and `--font-*` variables, a workspace that sets its brand in Settings › Appearance gets it everywhere. Today it reaches only the dashboard.
2. **The source tag as a component.** One `<Source of="qbo" metric="…" />` that renders the mono tag and opens `AuditDrawer`. Every figure on every page gets the same affordance.
3. **A rail per app that matches.** The dashboard, intranet and console rails have three widths (224, 266 and 292px), three row heights and three active states. Use one rail component.

## Suggested order

1. Bugs 1 to 12, each a one-line or one-component change.
2. Eyebrow colour, the type scale codemod, and sentence-case labels. This is the largest visible change for the least risk.
3. Portfolio layout and removing sparklines and mini-charts from tiles.
4. Bokeh out of in-app heroes, the hover motion, the radii.
5. The intranet and console onto shared tokens (suggestion 1).
6. The marketing hero split.

---

The audit method is unslop by Damir Sokolovsky ([@heydamir](https://x.com/heydamir)), CC BY 4.0.
