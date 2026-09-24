# Handoff: ULRG · Recruiting tab

## Overview
The Recruiting tab is a new sub-tab of **ULRG + Team** (`Overview | Scorecard | Recruiting | <team rooms>`). It turns the GHL recruiting pipeline into three things:
- a daily list of what to do next
- pace to the monthly goal for each person
- weekly commitments that feed the L10 Scorecard

It has three role views:
- **Owner:** everything, plus source attribution.
- **Team Leader:** there are three. They close recruits in appointments and own candidates from *Met* onward.
- **SDR:** there is one. The SDR books appointments onto Team Leader calendars and owns candidates up to *Appointment set*.

Text, call, email, book and stage moves write back to GoHighLevel.

**Read `RECRUITING-SPEC.md` (repo root) next.** It is the repo audit, API contract, data model, GHL write-back mechanics and phased build plan. This README covers the screens.

## About the design files
`recruiting-reference-mockup.html` (beside this file, with `recruiting-reference-mockup.support.js`) is a **design reference built in HTML**, a prototype of the intended look and behaviour. It is not production code. Recreate it in the existing frontend (`frontend/src/ulrg/`, React + Vite) using that directory's patterns:
- `Parts.jsx` for Card, Chip, Eyebrow, Avatar and Bar.
- `scorecardMath.js` for the `C` palette, `FD`/`FB`/`FM` and the `band()` and `verdictStyle()` helpers.
- `SubTabs.jsx` for the sub-tab strip.

All figures in the prototype are invented. All arithmetic in the prototype happens client-side, but **in production it is server-side** (IMPLEMENTATION-SPEC §3, §7).

To open the prototype, keep `recruiting-reference-mockup.support.js` next to it and open the HTML file in a browser. The **role** tweak switches between the owner, Team Leader (Jenna) and SDR (Cole) views.

## Fidelity
**High-fidelity.** Colours, type, spacing and interactions are final. They were taken from the repo's own ULRG palette and type pairing, so the page should match pixel for pixel when built from `Parts.jsx` and `C`.

---

## Layout (all roles)
- The page ground is `#F2EDE6`. The content column has `max-width: 1240px`, is centred, and is laid out as a vertical stack with `gap: 16px`.
- In the product, CommandCenter owns the page padding and the ULRG eyebrow and `SubTabs`. Only the panel below the tabs is new.
- **Card:** background `#FFFFFF`, border `1px solid #ECE6DC`, radius `14px`, shadow `0 1px 2px rgba(0,46,44,.04), 0 8px 24px -18px rgba(0,46,44,.35)`. This is `Parts.Card`.
- **Section title:** `--font-display` (Space Grotesk) 16px/600, `#002E2C`. The meta line beside it is `--font-data` 11px `#93A099`.
- **Column header:** `--font-data` 9px, letter-spacing `.07em`, uppercase, `#93A099`, with `1px solid #ECE6DC` below.
- **Row rule:** `1px solid #F6F2EB`.

The page reads top to bottom:
1. **Hero:** a full-width card with two columns, `repeat(auto-fit, minmax(min(100%,460px),1fr))`. The right column is `#F8F5F2` with `border-left: 1px solid #ECE6DC`.
2. **Work row:** `display:flex; flex-wrap:wrap; gap:16px`.
   - Do next: `flex: 2 1 580px`.
   - Right column: `flex: 1 1 340px`. It holds the SDR card, then Team Leader commitments.
3. **Report row:** grid `repeat(auto-fit, minmax(min(100%,460px),1fr))`. Leaderboard, then Pipeline.
4. **Where signings come from:** owner only, and can be hidden.

## Screens and sections

### 1a. Hero — close view (owner and Team Leader)
**Left column** (padding `22px 26px 24px`):
- **Eyebrow:** "SEPTEMBER · TEAM · 7 DAYS LEFT" (or "YOUR SEPTEMBER · …" for a Team Leader). `--font-data` 9.5px, `.15em`, uppercase, `#93A099`, nowrap. It is followed by the verdict chip, which uses `verdictStyle()`, e.g. STRETCH on `#FBF0D8`/`#8A6414`, 8.5px, radius 4.
- **Number:** "5" in display 60px/700, line-height 1, letter-spacing `-.02em`, `#002E2C`. Then "of 9 signed" in display 20px/500, `#5C6B62`, nowrap.
- **Goal tiles:** one per goal unit, 38×44, radius 6.
  - Signed: `#002E2C` with initials in data 11px `#F4EFE7`. Click opens the drawer. Hover lifts it `translateY(-2px)`.
  - Open: `#F6F2EB` with a `1px dashed #DCD2C2` border.
  - A ▲ caret in the pace colour sits under the tile at the pace position.
- **Pace bar:** 180×9, track `#F6F2EB`, fill = `band(pace/goal).bar`, width = `min(125,pct)/125`. It has a 1px tick at 80% (`#5C6B62`, 40% opacity). This is exactly `Parts.Bar`.
- **Pace line:** "**On pace for 7 of 9.** 4 more needed by Sep 30." 13px, line-height 1.45. The bold part is data 600 in `band().ink`.
- **Split line:** "Goal split: Jenna 3, Priya 3, Marcus 3. Cole books, Team Leaders close." 12px `#5C6B62`.

**Right column, "Path to 9":**
- Eyebrow, then the line (display 16px/600) from the server, e.g. "Closing every offer out gets you to 8. 1 more from Met gets you to 9."
- The rows are buttons laid out as a grid `minmax(0,1fr) auto 24px`, padding `9px 4px`, with a bottom rule `#ECE6DC`. Hover sets the background to `#FFFFFF`. Each row has:
  - Name: display 13px/600.
  - Meta: data 10.5px `#93A099`, e.g. "Offer out · 7 days · Summit Ridge Group".
  - Status chip: data 10px, padding `3px 7px`, radius 4. The tones are:

    | Tone | Background | Text |
    |---|---|---|
    | late | `#FBE7E1` | `#B85434` |
    | today | `#FBF0D8` | `#8A6414` |
    | ok | `#E7EFE5` | `#4F6A4D` |
    | mute | `#F6F2EB` | `#5C6B62` |

  - Owner avatar: 22px circle, `#DCE7E9`/`#1F6E72`, data 9px.
- Clicking a row opens the drawer.

### 1b. Hero — SDR view
**Left column:**
- Eyebrow "YOUR SEPTEMBER · 7 DAYS LEFT" and a verdict chip.
- "17" at 60px, then "of 24 booked".
- Two labelled bars, `max-width 440px`, 9px tall, with the 80% tick:
  - "Booked 17 of 24 · pace 22"
  - "Held 12 of 17 · 71% show · goal 75%"
- Line: "**On pace for 22 of 24.** 7 more to book by Sep 30, about 2 a day."

**Right column, "Where to book":**
- Line: "10 open slots this week. Marcus has the most room."
- One row per Team Leader: 24px avatar, name, a "NEXT UP" chip (`#E7EFE5`/`#4F6A4D`, data 9px) and "N open" on the right.
- Under that, the open-slot pills: data 11px, padding `4px 8px`, white, `1px solid #ECE6DC`, radius 6, indented 33px.

### 2. Do next (queue)
- **Title:** "Do next" ("Your list today" for a Team Leader or the SDR).
- **Subtitle:** "Built each morning from the rules in Settings. Texts, calls, emails and bookings sent from here are logged to GHL." 12.5px `#5C6B62`.
- **Progress, right:** 110×6 bar in `#5F7D5A` and "3 of 9 cleared" in data 12px.
- **Owner filter chips (owner only):** "Everyone 9 · Jenna 2 · Priya 1 · Marcus 2 · Cole · SDR 4". Pills 12px, padding `5px 10px`, nowrap. Active is `#002E2C` with `#F4EFE7` text; inactive is white, `#ECE6DC` border, `#3B4B44` text.
- **Row:** a grid `92px minmax(0,1fr) auto`, gap 14, padding `13px 20px`, hover `#F8F5F2`. Clicking the row opens the drawer.
  - **Due chip:** data 10px, uses the tone table above.
  - **Name** (display 14px/600) + **meta** "Offer out · $241k GCI" (data 10.5px `#93A099`, nowrap).
  - **Why:** 12.5px `#3B4B44`, `text-wrap: pretty`.
  - **Rule label:** data 9.5px, `.06em`, uppercase, `#1F6E72`, nowrap.
  - **Owner chip (owner only):** 18px avatar plus first name. The SDR avatar is `#FFF8D4`/`#8A6414`; Team Leader avatars are `#DCE7E9`/`#1F6E72`.
  - **Actions:**
    - Primary (Text / Call / Email / Book): 12.5px/600, padding `7px 13px`, radius 7, `#002E2C` with `#F4EFE7` text, hover `#1F6E72`.
    - "Tomorrow" (snooze): white, `#ECE6DC` border, `#5C6B62` text.
    - Done "✓": a 32px circle, `#5F7D5A` text, hover `#E7EFE5`.
  - Action buttons `stopPropagation` so they don't also open the drawer.
- **Empty state:** "Queue cleared." (display 15px/600 `#4F6A4D`) and "Tomorrow's list builds at 6:00 am."
- **Cleared today:** a footer band `#F8F5F2` with rows "✓ Name · how · Undo".

### 3. SDR card (right column, every role)
- **Header:** 30px avatar (`#FFF8D4`/`#8A6414`), title "Cole Whittaker" ("Your week" in the SDR view) and "SDR · books for Team Leaders".
- **Four metric rows:**
  - Appointments booked (this week)
  - Appointments held (this week)
  - Dials (today)
  - Conversations (today)
- Each row shows "actual / commit". The actual is 600 in `band().ink`, and the commit has −/+ steppers (16px, radius 4) for the SDR or owner. A 4px bar spans the full width.
- The band is `actual / (commit × weekday/5)`.
- **Speed to lead:** "median, this month", with the value in `#B85434` when over goal and "goal 15 min".
- **"Booked this week, by calendar":** one row per Team Leader, grid `64px 1fr auto`. It shows 6 slot cells (14px tall, radius 3):

  | Cell | Fill |
  |---|---|
  | held | `#5F7D5A` |
  | coming up | `#DCE7E9` |
  | no-show | `#FBE7E1` |
  | open | dashed `#DCD2C2` border |

  Each row ends with "3 held · 4 booked". A legend sits below.

### 4. Team Leader commitments
- **Title:** "Team Leader commitments". Meta: "Week of Sep 21 · Wednesday, day 3 of 5".
- **Grid** `minmax(0,1fr) 96px 96px` with columns Team Leader / Appts held / Offers sent. The cells work the same way as the SDR rows, with a 72×4 bar.
- **Editable:** the owner can edit every row; a Team Leader can edit only their own, marked "(you)" in 600.
- **Footer band** (`#F8F5F2`, radius 9): "**5 held, 5 booked this week.** Both roll into the L10 Scorecard on Monday as Recruiting Appts Met and Appts Booked." Then an "Open Scorecard" link.

### 5. Team Leader leaderboard
- **Title:** "Team Leader leaderboard". Meta: "September to date".
- **Grid** `22px minmax(150px,1fr) 128px 48px 64px 70px`, `min-width 560px`, `overflow-x: auto`.
- **Columns:**
  - `#`: display 14px/700. Rank 1 is `#002E2C`; the rest are `#93A099`.
  - Team Leader: 26px avatar, name 13px/600, title 11px.
  - Signed vs goal: "2 of 3" plus "pace 2.6" (nowrap, `band().ink`), over a 110×6 bar with the 80% tick.
  - Held
  - Close: shown in `#B85434` when under 25%.
  - Queue: "0 of 2", shown in `#4F6A4D` when complete.
- The current user's row is tinted `#FFF8D4`.
- **Footnote:** "Close is signed ÷ held, last 90 days. Queue is today's items cleared."

### 6. Pipeline
- **Title** "Pipeline". Meta: "Active now · 22 people · 3 in nurture". Link "Open in GHL" on the right.
- **Rows:** grid `120px minmax(0,1fr) 54px 84px`, with these parts:
  - **Stage name** (display 13px/600) and an owner tag below it. The tag reads "SDR" (`#8A6414`) or "TEAM LEADERS" (`#1F6E72`), data 9.5px uppercase.
  - **Bar:** 18px tall, radius 4, width proportional to count. The fill is `#FFF8D4` for SDR stages, `#DCE7E9` for Team Leader stages and `#5F7D5A` for Signed.
  - **Count and GCI:** the count in data 12.5px/600, then the GCI.
  - **"71% →"** conversion to the next stage.
  - **"2 stuck":** `#8A6414`.
- **Footnote:** "The SDR owns candidates through Appointment set. Team Leaders own them from the meeting on. Arrows show the 90-day rate into the next stage."
- **This is deliberately not a kanban.** The user rejected "a retelling of the GHL pipeline".

### 7. Where signings come from (owner only)
- **Columns:** Source · Candidates · Signed · Rate (inline bar in `#5F7D5A`) · Cost each ("Time only" in `#93A099` when there's no spend) · GCI added.
- **Grid** `minmax(170px,1.6fr) repeat(5,minmax(70px,1fr))`, `min-width 640px`, and it scrolls horizontally.

### 8. Candidate drawer
- **Frame:** fixed on the right, `width: min(500px, 94vw)`, background `#F2EDE6`, shadow `-8px 0 30px -12px rgba(20,35,28,.45)`. The scrim is `rgba(18,41,31,.35)` and closes the drawer on click.
- **Header** (white):
  - Name: display 19px/700.
  - "Brokerage, City · phone": 12.5px `#5C6B62`.
  - × close button.
  - A fact row with labels in data 9px uppercase `#93A099`: Stage · days / Trailing GCI / Owner / Booked by / Source.
- **"On today's list" banner** (when opened from the queue): `#FFF8D4` background, `1px solid #F3E39A` border, radius 10, showing the rule label and the why line.
- **Next step:**
  - A text input, 13px, padding `9px 11px`, radius 8.
  - DUE chips: Today / Tomorrow / Friday / Next Mon, in pill style.
  - "Saved as a GHL task" on the right in `#4F6A4D`.
- **Reach out:** a segmented control of Text / Call / Email / Book. The track is `#ECE6DC` and the active segment is white.
  - **Text:** "To <phone>", textarea (5 rows).
  - **Email:** subject input, then the textarea.
  - **Call:** the phone number (data 15px), "Calls from your GHL number and records to the contact.", and outcome chips: Connected / Left voicemail / No answer. In production the Call button opens the dialer; the API can't dial (SPEC §5.4).
  - **Book:**
    - If the candidate is owned by the SDR, a "Team Leader calendar" chip row appears first, showing "N open" per Team Leader and defaulting to Next up.
    - Copy: "45 min at the Sandy office with <TL>. The invite goes out by text and email."
    - Slot buttons in a grid `repeat(auto-fill,minmax(104px,1fr))`, data 12px.
  - **Send row:** a note on the left, "Sending clears it from today's list." / "Pick an outcome to log it.". The button is on the right: Send text / Send email / Log call / Book Thu 9:00. It's `#002E2C` when ready and `#93A099` when disabled.
- **Stage:** a select plus "Open contact in GHL".
- **Activity:** a timeline with 8px dots (`#5F7D5A` for our sends just now, `#1F6E72` for stage moves, `#DCD2C2` for history). Each entry has a title 12.5px/500, a detail 12px `#5C6B62` (`white-space: pre-line`), and the time in data 10.5px.

### 9. Toast
Fixed at the bottom centre, 26px from the bottom. `#002E2C` background, `#F4EFE7` text, 13px, radius 9, padding `10px 16px`, with a leading ✓ in `#9FC79A`. It lasts 2.6 s. Examples:
- "Text sent to Brandon · logged to GHL"
- "Brandon booked on Marcus's calendar · Thu 9:00"
- "Brandon moved to tomorrow's list"

## Interactions and behaviour
- **Row click** opens the drawer in the rule's primary mode. The primary button opens the drawer on that mode.
- **Send, book or connected call:**
  - writes to GHL through the outbox (SPEC §5.3)
  - adds an activity entry
  - shows a toast
  - clears the queue item when the workspace auto-clear setting is on (the default)
- **Voicemail or no answer** logs the call but does **not** clear the item.
- **Booking:**
  - fills that slot and removes it from "Where to book"
  - adds a "coming up" cell to that Team Leader's strip
  - increments the SDR's Booked count
  - sets the next step to "Meet <TL> <slot>"
- **Tomorrow** snoozes the item to the next business day at 06:00. **Done** clears it. **Undo** restores it.
- **Stage select** moves the opportunity in GHL, adds a timeline entry and shows a toast "<First> moved to <Stage> in GHL". A 409 from the concurrency check refreshes the drawer instead.
- **Commit steppers:** −/+ with a minimum of 1. Dials step by 5.
- **Responsive:** every text row that can wrap has `nowrap` only on its short labels. The hero stacks below about 920px. The work row wraps the right column under the queue. Wide tables scroll horizontally inside their card.
- **Loading:** skeleton cards the same height as each section. **Errors and empties** name their cause: "GHL not connected", "Not synced yet", "Sending is off for this workspace", and so on (SPEC §1.6).

## State (client)
Server data comes from `GET /ulrg/recruiting`. Local UI state:
- `ownerFilter`
- `openCandidateId` and `openQueueItemId`
- the drawer `mode`, plus per-`candidate:mode` drafts and the email subject
- `slot`, `calendarSeat`, `callOutcome`
- in-flight idempotency keys
- the toast

After each successful write, **re-fetch** the payload, or patch it from the action response. Never compute pace or bands on the client.

## Design tokens (from `frontend/src/ulrg/scorecardMath.js` `C`)
**Colours:**

| Group | Tokens |
|---|---|
| Ink and text | ink `#002E2C`, body `#3B4B44`, slate `#5C6B62`, muted `#93A099` |
| Surfaces and rules | hair `#ECE6DC`, hairSoft `#F6F2EB`, page `#F2EDE6`, surface `#FFFFFF`, parchment `#F8F5F2` |
| Meadow | meadow `#5F7D5A`, meadowInk `#4F6A4D`, meadowBg `#E7EFE5` |
| Teal | teal `#1F6E72`, mist `#DCE7E9` |
| Amber | amber `#D9A227`, amberInk `#8A6414`, amberBg `#FBF0D8` |
| Poppy | poppy `#E8836A`, poppyInk `#B85434`, poppyBg `#FBE7E1` |
| Daffodil | daffodil `#FFDD1F`, daffodilBg `#FFF8D4` |
| On dark | onDark `#F4EFE7` |

Non-token hexes used once: `#DCD2C2` (dashed open tile), `#F3E39A` (banner border), `#9FC79A` (toast tick).

**Colour law:** use `band(pct)` only. At 100 or more it's meadow, 80–99 is amber, and under 80 is poppy.

**Type:**
- `--font-display` Space Grotesk 500/600/700
- `--font-text` Instrument Sans 400–700
- `--font-data` Archivo 400–700, for every figure and label

These come from the Axcion pairing in `typefaces.js`.

**Radii:** card 14 · banner 10 / 9 · button 7 · tile 6 · chip 4 · pill 99.

**Spacing:**
- Section gap 16.
- Card padding `18px 20px`; the hero is `22px 26px 24px`.
- Queue row padding `13px 20px`.

## Assets
There are no images or icons. Avatars are initials. Glyphs are text: ✓ ▲ × −.

## Files
- `docs/recruiting-reference-mockup.html`: the design reference (open it with
  `docs/recruiting-reference-mockup.support.js` beside it).
- `RECRUITING-SPEC.md` (repo root): the repo audit, API contract, data model, GHL write-back,
  rules, metrics and phases.
