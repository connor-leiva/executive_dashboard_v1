# ULRG Recruiting — Sourcing Discovery (RECRUITING-SPEC §9, Phase 0)

**Status: NOT STARTED — waiting on the Private Integration Token.** Nothing below is filled in,
and nothing in Phase 1 may hardcode a value that belongs here. Run
`backend/probe_ghl_recruiting.py` (read-only) and paste its answers in.

This closes the open question `ulrg-scorecard-sourcing.md` has carried since the Scorecard was
built: *"Confirm whether recruiting lives in a GHL pipeline."* Two Scorecard rows depend on the
answer — **Recruiting Appts Met** and **New Recruitment Leads** — and both are hand-entered today.

## What only a person can do first

1. A **location admin on the recruiting location** creates a Private Integration Token with the
   **read scopes only** (least privilege, RECRUITING-SPEC §5.1). Write scopes are added at Phase 4,
   not now.
   - `contacts.readonly`, `opportunities.readonly`, `conversations.readonly`,
     `conversations/message.readonly`, `calendars.readonly`, `calendars/events.readonly`,
     `users.readonly`, `locations.readonly`, `locations/customFields.readonly`
   - **The exact scope names in the picker are a VERIFY item.** Record what they actually say.
2. Add two keys to `backend/.probe.env` (git-ignored, never committed, never opened by Claude):
   ```
   GHL_RECRUITING_TOKEN=pit-...
   GHL_RECRUITING_LOCATION=...
   ```
3. Run the probe:
   ```
   cd backend && ./.venv/Scripts/python.exe probe_ghl_recruiting.py
   ```

It prints a report and writes `backend/ghl_recruiting_probe.json` (PII masked, no message bodies).

> **This is a different GHL account from the Forum / Spring B one.** `reference-ghl-forum` describes
> a membership location; recruiting is the brokerage's own. Reusing the Spring B token here would
> probe the wrong location and the numbers would look plausible, which is the worst failure mode.

## D1–D6, D8 — to be answered by the probe plus Connor

| # | Decision | Answer | Evidence |
|---|---|---|---|
| D1 | Recruiting location id and pipeline id | _pending_ | probe `pipelines` + `stage_counts` |
| D2 | Stage id → group map (≈6 groups + nurture) | _pending_ | probe stage ids; **map by id, never by name** |
| D2b | Trailing-GCI and brokerage custom field ids | _pending_ | probe `custom_fields` (it flags likely ones) |
| D3 | Which GHL users are the 3 Team Leaders and the 1 SDR | _pending_ | probe `users`; match by email, override per seat |
| D4 | One calendar per Team Leader, or one round-robin | _pending_ | probe `calendars` + `teamMembers` |
| D5 | SMS sender: per-seat number or location default | _pending_ | Connor / location admin |
| D6 | Is the location A2P 10DLC registered? | _pending_ | **no SMS write ships until this is yes** |
| D8 | "Held" = `appointmentStatus: showed`, or reaching *Met* | _pending_ | probe `appointment_statuses` |

## VERIFY items — the four the spec refuses to let code depend on

| # | Question | Why it matters | Answer |
|---|---|---|---|
| V1 | Do PIT responses carry `X-RateLimit-*` headers? | §5.3's token bucket reads them. If not, it needs a fixed local budget instead | _pending_ |
| V2 | `free-slots` parameter names and response shape | Booking, "Where to book" and "Next up" all read it | _pending_ |
| V3 | `conversations/search` params and message `type` values | Dials and Conversations are counted off these | _pending_ |
| V4 | Do the calendar's own automations send confirmations? | The drawer says "the invite goes out by text and email". If they don't, that copy is a lie and Phase 4b sends an SMS instead | _pending_ |

Two more VERIFY items in the spec **cannot** be settled read-only, because they are properties of
a write. They carry into Phase 4's dry-run week:

- Does `2021-07-28` require `status` on `POST /conversations/messages`? (§5.4)
- Does `scheduledTimestamp` work on that version, so a quiet-hours send can be held rather than
  refused? (§5.4, §5.5)

## Done when

The table above has no `_pending_`, Connor has signed off D1–D6, and the scope list here matches
what the PIT picker actually offered. Phase 1 starts then, and not before: every value above is
configuration (`Integration(provider="ghl_recruiting").config`), so Phase 1's *code* can be built
in parallel — but it cannot be pointed at a real pipeline until this page is filled in.
