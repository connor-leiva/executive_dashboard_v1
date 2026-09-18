# Follow Up Boss → the portal: audit, spec and build guide

Written 2026-09-18, after Connor connected Follow Up Boss on the `utah-life` dashboard (Settings →
Integrations) and the portal still said **"No CRM connected"**.

> **The ask:** wire Follow Up Boss up correctly, usable **per workspace and per agent**, and make the
> home page's *Needs You Today* section show the leads that need a follow-up.

This document is the build record as well as the plan. Each phase ends with what shipped and how it
was verified.

---

## 1. Audit: what was actually wrong

Found by reading the code and the production database (read-only; counts and field names only, no
lead data). Ordered by how much each one blocks the feature.

| # | Finding | Evidence | Effect |
|---|---|---|---|
| A1 | **Every production FUB sync has failed.** `map_person` hands `created_at_src` to a `Date` column as the string `'2024-10-09'`. asyncpg (production) refuses it. SQLite would have refused it too, but no test ever ran the sync: its upsert was Postgres-only, so the suite couldn't. | `sync_run`: utah-life fub, **26 runs, 26 errors, 0 ok**. `DataError: invalid input for query argument $8: '2024-10-09' ('str' object has no attribute 'toordinal')` | Integration status `error`. **0 leads** stored. 100 agents stored, because users are written before people. |
| A2 | **Each sync reads the whole CRM with offset paging, then fails.** `fub_people` walks every person, with every field, using `offset`. FUB says to use its `next` cursor, and offset paging degrades deep into a result set. | Each failing run takes **476–489 s**, every 30 min | 8 minutes of API load per half hour, and nothing kept from it. |
| A3 | **Users stop at 100.** `fub_users` asks for `limit=100` and never pages. | Exactly **100** FUB agents stored (100 distinct emails, 96 active) | Agents after the first 100 can never be matched to a portal member. |
| A4 | **The portal checks the wrong spelling.** `connected()` in `IntranetApp.jsx` compares to `"connected"`; the payload says `"Connected"` (and `"Error"` for a failing sync, which is not a portal status at all). | `inheritance._DASHBOARD_TO_PORTAL`, `IntranetApp.jsx` `connected()` | "No CRM connected" whatever the connection is doing. |
| A5 | **"Needs You Today" is three hardcoded rows.** `PRIORITY_ITEMS` in `constants.js`. No endpoint serves follow-ups and no lead data reaches the portal. | `NeedsYouToday` | Even a working sync would show nothing. |
| A6 | **The `lead` table cannot answer "who needs a follow-up".** It holds id, stage, agent and a date: no name, no contacted flag, no activity time. Tasks are not synced at all. | `models.Lead` | No rule can be written against it. |
| A7 | **Agent matching cannot be fixed from the UI.** A member is matched to an agent by email; `intranet_member.agent_email` is the override, but **nothing in the console reads or writes it**, although the portal tells agents "an admin can set your Sisu address on the roster". | `grep agent_email` finds only the model and `member_identity` | Neither utah-life roster member matches a FUB user today, and there's no way to fix that from the UI. |
| A8 | **A FUB API key is stored in plaintext.** The console's free-form config editor on the portal's own Follow Up Boss row saved a key under `"API Key"`. The secret filter looks for `api_key`/`apikey`, so the space got past it. The console API returns the key in its response. | Portal row config: one key, 38 chars, `fka_` prefix; `credential_ref` empty | A live credential, unencrypted in the database and sent to the browser. |
| A9 | **Connecting accepts any string.** The dashboard's FUB form stores whatever it is given; a wrong key only shows up as a failed sync half an hour later. | `create_integration` generic path | Mistakes don't show up when they're made. |
| A10 | **The dashboard funnel would count every lead ever.** `metrics._funnel` counts all FUB leads for the business with no date window (the Sisu fallback beside it is windowed). | `_funnel` | As soon as A1 is fixed, *Leads* on the ULRG card jumps to the account's lifetime total. |
| A11 | **Win the Day list links need a hand-typed base URL**, and the portal row's is empty in production. | Portal row `base_url` = null | Configured lists would show "No list ID configured". |
| A12 | **The sync was never tested.** No test ran `sync_fub`, which is how A1 got to production. | `tests/` | — |

Not in scope, noted: the annual unit goal (`numbers.annual_unit_goal`) is read by the portal but no
screen sets it; the dashboard's invite still doesn't create a roster entry (open question from
`fb368f3`).

---

## 2. What "needs a follow-up" means

Three rules come from Follow Up Boss's own model of follow-ups. A fourth is optional. Every rule is
per agent: the lead or task is assigned to that agent's FUB user.

| Rule | A person appears when… | Why |
|---|---|---|
| **New lead** | not yet contacted (`contacted = false`), created within the last **N days** (default **7**), not in Trash | Response speed matters most with a new lead. |
| **Overdue** | an open task assigned to the agent was due before today, and not more than **M days** ago (default **30**; 0 = no limit). Older ones are counted, not listed. | A missed follow-up. The age limit keeps years-old action-plan tasks from burying yesterday's missed call. |
| **Due today** | an open task assigned to the agent is due today (the workspace's day) | The day's plan. |
| **Going cold** *(off by default)* | in one of the stages an admin picks, and no activity for **K days** (default **14**) | Active clients who've gone quiet. Off until someone picks stages, because stage names differ by account and a guess would be wrong. |

**One row per person.** A person who is a new lead *and* has a task due today is listed once, under
the more urgent reason, with the other one shown as a tag. **Order:** new leads (newest first),
overdue (most recently due first, so yesterday's miss comes before last month's), due today
(earliest first), going cold (most recently gone quiet first). The counts are distinct people per
rule.

**Every row links to the person in Follow Up Boss** (`https://{account}.followupboss.com/2/people/view/{id}`).
The call or text happens in FUB, where it gets logged and where `contacted` flips. A `tel:` link
would skip that logging. So the row stores and shows **the name only**: no phone, no email.

---

## 3. Per workspace and per agent

**The connection belongs to the workspace.** It's made once, on the dashboard's Integrations page, and
the portal inherits it (`services/inheritance`). One FUB account per workspace (see §9).

**Each member is tied to one FUB user**, resolved in this order:

1. **An explicit link** an admin picked in the console (People & Roster → CRM), stored in
   `intranet_member.agent_links = {"fub": "<FUB user id>", "sisu": "<Sisu agent id>"}`.
2. `agent_email`, the existing override.
3. The member's own email.

The console shows how each member was matched (linked / by email / not matched), and lets an admin
pick from the actual FUB users (and Sisu agents) instead of typing an address. Sisu gets the same
picker because the same gap (A7) breaks *My Numbers*.

**Who sees what** (enforced on the server, like everything else in the portal):

| Viewer | Sees |
|---|---|
| A member matched to a FUB user, whose role allows **Win the Day** (`wtd` ≠ None) | Their own queue. |
| A viewer allowed the team (`_may_see_team`: `team_production` ≠ None, or an owner/admin with no roster entry) | The team summary (counts per agent, plus **unassigned new leads**), and can open any one agent's queue. |
| Both | Their own queue first, the team below it. |
| Anyone else | One sentence saying why, never an empty box. |

**Why each queue is empty**, checked in this order. This is the same pattern as `member_numbers`:
different reasons get different messages, because each one needs someone different to act.

1. `not_connected`: the workspace has no FUB connection. Admins are told where to connect it.
2. `not_synced`: connected, but no sync has finished yet.
3. `not_on_roster`: this account has no roster entry. Owners and admins still get the team view.
4. `unmatched`: on the roster, but no FUB user matches. Admins are told where to link one.
5. `denied`: their role's Win the Day level is None.
6. **Caught up**: matched and nothing is due. This is a real result, shown as one.

A failing sync is not an empty state. The last good data stays up with "Last sync failed …; showing
Follow Up Boss as of 9:30 AM".

---

## 4. Data model (migration `0073_fub_follow_ups`)

**`lead`**, which already holds FUB people, gets the columns the rules need:

| column | type | from FUB |
|---|---|---|
| `name` | String(200) | `name` (or first + last) |
| `origin` | String(120) | `source` (e.g. "Zillow"); `source` is taken by `fub` |
| `contacted` | Boolean | `contacted` |
| `src_created_at` | DateTime tz | `created` (`created_at_src` stays the Date the funnel reads) |
| `src_updated_at` | DateTime tz | `updated` |
| `last_activity_at` | DateTime tz | `lastActivity` |
| `synced_at` | DateTime tz | when this row was last written |

Index `(tenant_id, agent_id, contacted)` for the rules.

**`crm_task`** (new) holds the open tasks, replaced as a snapshot each run: `tenant_id`, `source`
(`fub`), `external_id`, `person_external_id`, `agent_id` (FK agent, nullable), `name` String(300),
`task_type` String(40), `due_on` Date, `due_at` DateTime tz nullable, `synced_at`. Unique
`(tenant_id, source, external_id)`, index `(tenant_id, agent_id, due_on)`.

**`intranet_member.agent_links`**: JSON, nullable. See §3.

**Sync bookkeeping** lives in `integration.config["fub_state"]`: account id and domain, the activity
watermark, backfill cursor and progress, and the result of the last quick refresh. It's server-owned.
The connect route keeps it, and resets it when the key belongs to a *different* FUB account. In that
case the old account's leads, tasks and agents are deleted first, because FUB ids from two accounts
would collide on the same unique key.

---

## 5. Sync design

**Sized for the real account.** The first successful sync (Phase 0, below) found about **130,000
people and 135 users** in the live FUB account, and reading all of them took **252 seconds**. So no
pass that runs every few minutes reads the CRM whole: each one is bounded by a window, a watermark
or a list of ids.

Documented FUB features are used as documented: the `next` cursor, `fields`, `includeTrash`,
`lastActivityAfter`, `id=1,2,3`, `/identity`, and tasks' `isCompleted`/`due`/`dueStart`. Two are
less certain: `dueStart`'s format, and `-created` as a descending sort. Both are checked against
what comes back, fall back instead of failing, and are named in each run's stats. The documented
rate limit is 125 requests / 10 s without a system key. A 429 is retried after its `Retry-After`.

**Every run (the 30-minute tick and "Sync now")**
1. `/identity`: account id and domain, for links. Detects an account change.
2. `/users`, **all pages**, into `agent`.
3. **Newest people first** (`sort=-created`), walked back to the start of the New Lead window. This
   catches a lead that arrived a minute ago whether or not FUB has logged activity on it yet. The
   order is verified page by page. If FUB answers unsorted (or refuses the sort), one page is read and
   the stats say `newest_order: unsorted|refused`.
4. **People changed since the watermark:** `lastActivityAfter = watermark − 10 min`,
   `includeTrash=true`, only the fields in §4. Contacts, calls and changes arrive this way.
5. **Every uncontacted new lead we hold, re-read by id** (100 per request). Refreshes the stage and
   owner of exactly the people the *New lead* rule reads, so a reassigned new lead moves to its new
   owner on the next pass. (The plan used `contacted=false` + `lastActivityAfter`. At 130,000 people
   that filter also returns old leads browsing the website, so it was replaced before shipping.)
6. **Open tasks:** `isCompleted=false` with `due=today`, and `due=overdue` limited by `dueStart` to
   the M-day window. If FUB rejects `dueStart`, fetch everything overdue up to a page cap and note it
   in the run's stats. Stored as a snapshot: tasks that no longer come back are deleted (mark and
   sweep on `synced_at`, because a big team's task ids would overflow a `NOT IN`).
7. **People a task points at that aren't stored yet:** fetched with `id=…`, 100 per request.
8. **Backfill** (the whole CRM, for the funnel's history and *Going cold*): resumable,
   `FUB_BACKFILL_PAGES_PER_RUN` = 300 pages (about a minute) per run, cursor saved between runs, so
   130,000 people take five runs. Once finished it's repeated every 24 h, which catches reassignments
   and stage changes that came with no activity.

**The quick refresh, every 5 minutes** (`fub_followups_tick`): steps 3 to 7, for workspaces whose
full sync has read the account at least once. No `sync_run` row for this one: the half-hourly run
owns status and history. An in-process lock per integration means the two never run together, and a
refresh that finds the lock held skips its turn. A person or task naming a user we have never seen
re-reads the users first, so a new hire's first lead isn't left unassigned.

**Freshness, stated plainly.** New leads, contacts, calls and tasks: within 5 minutes. A stage change
or reassignment that FUB records as *no activity* (on a lead that isn't new): up to a day, via the
re-walk. A test holds that bound.

**Writes** are batched upserts through a dialect-aware helper (Postgres `ON CONFLICT` in production,
SQLite's in tests), so the test suite runs the same code production runs. Mapping always produces
real `date`/`datetime` objects and cuts strings to their column's length. A test holds the mapping to
the model's own column types and lengths, read from the model rather than from a list someone has
to keep up to date.

**Stats** on each `sync_run`: users, people changed, uncontacted refreshed, tasks (today / overdue /
older overdue), backfill pages and whether it finished, and whether `dueStart` was accepted. That's
how the design gets checked against the real account after deploy.

**Connecting checks the key.** `POST /integrations` for `fub` calls `/identity`. A 401/403 is
refused on the spot ("Follow Up Boss rejected that API key…"); FUB being unreachable is not the
key's fault, so the key is kept and the sync says more. If the key belongs to an *agent* rather than
an owner or admin ("Broker" in FUB's terms), the key is saved and the form says so: FUB shows an
agent only their own leads, so the rest of the team's follow-ups would be missing with no error to
explain it. A successful connect starts the first sync right away (not for a paused workspace).

---

## 6. API

`GET /api/v1/intranet/follow-ups[?agent=<agent id>]` (portal; any signed-in member of a workspace
with the portal):

```jsonc
{
  "connection": {"state": "ready|not_connected|not_synced", "status": "connected|error",
                 "synced_at": "…", "sync_failed": false, "error": "… (admins only)",
                 "account_domain": "liveutah1"},
  "rules": {"new_lead_days": 7, "overdue_max_days": 30,
            "cold_enabled": false, "cold_days": 14, "cold_stages": []},
  "own": {"agent": {"id": "…", "name": "…", "matched_by": "link|agent_email|email"},
          "counts": {"new_lead": 2, "overdue": 3, "due_today": 5, "going_cold": 0,
                     "total": 10},
          "items": [{"kind": "new_lead|overdue|due_today|going_cold", "also": ["due_today"],
                     "person": {"id": "123", "name": "…", "stage": "Lead", "origin": "Zillow"},
                     "task": {"name": "Call back", "type": "Call", "due_on": "…", "due_at": null},
                     "at": "…", "url": "https://…/2/people/view/123"}],
          "truncated": false} | null,
  "own_reason": "not_on_roster|unmatched|denied|null",
  "team": {"counts": {…}, "unassigned_new_leads": 4, "unassigned_total": 5,
           "by_agent": [{"agent_id": "…", "name": "…", "new_lead": 1, "overdue": 4,
                         "due_today": 2, "going_cold": 0, "total": 7}]} | null,
  "viewing": {"agent_id": "…", "name": "…"} | null
}
```

`?agent=` (a leader opening one agent's queue) needs the team permission and an agent in this
workspace; `?agent=unassigned` lists the unassigned new leads. Each item list is capped at 200
(the home panel shows the first 6).

Console (`console_access`):
- `GET /console/crm-agents?source=fub|sisu`: the users for the roster picker.
- `PATCH /console/members/{id}` accepts `agent_links`; `GET /console/members` returns each member's
  match (`crm.fub`, `crm.sisu`: who, and how).
- `GET/PATCH /console/follow-ups`: the §2 settings, plus the stages actually present in the synced
  leads (with counts) for the *Going cold* picker, and how many overdue tasks across the account
  the window leaves out.
- `POST /console/wtd-lists`: a new Win the Day list (a draft until published). The console could
  edit lists but never create one, so a workspace with none could never have any.
- `GET /console/fub-smart-lists`: the account's smart lists, read live from FUB, so a list is picked
  by name. A failure falls back to typing the id.

---

## 7. UI

- **Portal home, *Needs You Today*:** counts as chips, the first six rows with their reason and
  timing, each opening the person in FUB, "See all" going to the new page, and "as of" from the last
  refresh. A viewer with no queue of their own but team access sees the team: the busiest agents and
  unassigned new leads. Otherwise, the §3 reason.
- **Portal `/follow-ups` page** (nav, beside Win the Day): the full list, grouped by reason; a Team tab
  for leaders with per-agent counts, where clicking an agent opens their list.
- **Console → People & Roster:** a CRM column showing each member's FUB/Sisu match and how it was
  made, and an editor to pick the FUB user and Sisu agent.
- **Console → Win the Day:** a *Needs You Today* settings panel (the §2 numbers; the *Going cold*
  switch, days and stage picker), and an *Add a call list* form with the smart-list picker.
- **Console → Integrations:** rows owned by the dashboard (Sisu, FUB) drop the free-form config
  editor that let a key get saved in plaintext, show the dashboard's real status and last error, and
  say that the list base URL defaults to the account's own address.
- **Win the Day lists:** with no base URL set, links build from the FUB account's domain
  (`https://{domain}.followupboss.com/2/people/list/{id}`).
- **Dashboard → Integrations:** the FUB form reports a rejected key immediately, and warns (while
  still saving) when the key only sees one agent's leads.

---

## 8. Phases

Each phase ends green (`pytest`, both frontend builds, `alembic heads` = one) and is committed to
`main`. Phases 0 and 4 end with a push and a production check.

### Phase 0: stop the failing sync, fix the wrong labels, close the plaintext key
- `map_person` returns real dates; dialect-aware upsert helper; `sync_fub` runs in tests against a
  fake FUB, including a type/length test against the model's columns.
- `/users` paged.
- The dashboard's `error` reads as **Action Needed** in the portal (a real portal status), not
  `Error`. The portal's `connected()` fix moves to Phase 3: flipping the kicker to "Pulled From
  Follow Up Boss" above the placeholder rows would have been a second wrong label.
- Secret filter compares after removing spaces, dashes and underscores (`"API Key"` → `apikey`),
  and never returns such a key; migration `0072_strip_config_secrets` strips secret-looking keys
  already stored in `intranet_integration.config`.
- `_funnel` counts FUB leads created inside the period (Trash excluded).
- **Verify in production:** a `sync_run` with status ok, leads > 0, integration `connected`, the
  stored key gone.

### Phase 1: a sync that can answer "who needs a follow-up"
Migration 0073; FUB client (`next`, fields, 429, identity); passes 1–7 of §5; the 5-minute quick
refresh with its lock; key check on connect; account-change reset. Tests: each pass against a fake
FUB (paging, a 429, `dueStart` rejected → fallback, account change, backfill resuming across runs).

### Phase 2: who's who, and the follow-up API
`agent_links` and the new match order in `member_identity`; `services/follow_ups` (rules,
one-row-per-person, order, counts, the empty-state ladder, settings with defaults);
`GET /intranet/follow-ups`; the console endpoints; Win the Day link from the domain; console
Integrations rows owned by the dashboard. Tests: a member sees only their own; the team needs the
permission; `?agent=` refuses another workspace's agent; each empty state; each rule's edges (window
boundaries, Trash, completed tasks, the overdue age limit); link beats email.

### Phase 3: the screens
Portal home panel + `/follow-ups` page + nav; console roster CRM column + editor; console Win the Day
settings panel; console Integrations changes; dashboard FUB form error. Checked on the **production
build** (`npm run build` + preview, fresh session), at 375 px, clicking every link.

### Phase 4: ship and check in production
Push; watch the deploy and `alembic upgrade`; read the first run's `sync_run.stats` (read-only) to
see how the account behaves (size, `dueStart`, backfill pace); check the portal bundle serves the new
home panel; update memory. Connor then links his agents in People & Roster.

### Phase 5 (next, not in this build): calls → conversations
FUB `/calls` (newest first, walked back to a stored id) → `conversations_logged` on My Numbers (it's
an em dash today, "from the Follow Up Boss call sync, which does not exist yet") and the Win the Day
*calls* and *conversations* tallies, which are typed in by hand today. Needs one decision: how long a
call has to be to count as a conversation (see §9).

---

## 9. Decisions made for Connor (change any of them)

| Decision | Chosen | Why | To change |
|---|---|---|---|
| What "needs a follow-up" is | New uncontacted leads (7 d), overdue tasks (≤ 30 d), due today; *Going cold* off | These are FUB's own follow-up signals; *Going cold* needs stage names only the workspace knows | Console → Win the Day → Needs You Today |
| Who sees the team's follow-ups | Whoever may see team production (`team_production`) | It's the permission that already means "everyone else's numbers"; no new permission to set up | Console → Roles & Permissions |
| Who sees their own | Anyone whose role has Win the Day | Follow-ups are part of the daily routine | Same |
| Tap-to-call | No; rows open the person in FUB | Calls made through FUB get logged there, and that's what marks a lead contacted | — |
| Lead data stored | Name, stage, lead source, contacted, timestamps. **No phones or emails.** | The least personal data that still gives a usable list | — |
| One FUB account per workspace | Yes | Person ids from two accounts would collide; a second account needs rows keyed by connection (§10) | — |
| Plaintext key in the portal row | Deleted by migration | It's a copy of a credential the dashboard holds encrypted, and nothing reads it | Re-enter on the dashboard if ever needed |

## 10. Not built, designed for

- **Agents connecting their *own* FUB account** (for example, members of a community where each agent
  has their own CRM, rather than a team on one account). This would need a per-member connection and
  lead/task rows keyed by connection rather than by workspace. The identity step goes away, because
  the key already says who its owner is. If "per agent" in the request meant this, it's the next
  build.
- **Webhooks** (real-time instead of 5-minute refresh). They need FUB system registration
  (`X-System`) and a public callback.
- **FUB teams** (`teamLeaderOf`) to limit a team leader to their own team rather than the whole
  workspace.

## 11. Only Connor can do

- Link any agent whose FUB email differs from their portal email (People & Roster → CRM). Nothing
  matches until then: neither utah-life roster member matches a FUB user today.
- Put agents on the roster (People & Roster). A dashboard invite alone doesn't create a roster entry.
- Optionally turn on *Going cold* and pick its stages.

---

## 12. Build record

**Phase 0: shipped `acab560`, verified in production 2026-09-18.** Migration 0072 ran. The portal row's
plaintext key is gone (its config is empty). The first sync on the new code, 16:39 UTC, finished
**`ok` in 252 s**: 129,944 records, **129,809 leads** (the first ever stored; created 2012-07-23 to
today, every one assigned), **135 FUB users** (the old code stopped at 100), integration `connected`.
With users paged, **Spring's roster entry now matches a FUB user**. Connor's doesn't: he isn't a FUB
user, so his portal shows the team view.

**Phases 1–3: shipped `72aee64`, verified in production 2026-09-18.** Migration 0073 ran. Both
bundles serve the new screens. `GET /intranet/follow-ups` and the console routes answer 401 without a
session, so they exist. The first full sync on the new code, 17:15 UTC, finished **`ok` in 75.5 s**:
- 135 users;
- 300 newest people, 6,327 with activity in the last 8 days, 147 uncontacted new leads re-read by id;
- 233 tasks due today and 1,125 overdue within 30 days;
- the first 30,000-person backfill slice (of about 132,000).

**Both open questions settled on the real account:** `dueStart` was accepted (`tasks_window:
dueStart`), and `sort=-created` came back newest first (no `newest_order` flag). The 5-minute refresh
ran on its own at 17:17 and took about 4 seconds. What the queues would show, computed read-only from
the synced rows:
- **Spring:** 7 new leads, 14 people due today, **353 people with overdue tasks** in the last 30
  days.
- **Connor:** team view (he isn't a FUB user).
- **The account:** 120 uncontacted new leads this week, and 8,813 older overdue tasks the window
  leaves out.

Spring's overdue count is worth a look. As the team owner she likely holds many action-plan tasks.
If the list is too long to be useful, the overdue window can come down in Console → Win the Day →
Needs You Today.

**Phases 1–3, how they were built and tested.** Once Phase 0 gave real numbers, the quick passes were
changed before shipping (the newest-first walk and the by-id uncontacted refresh in §5; backfill
slices of 300 pages). Tests: `tests/test_fub_sync.py` and `tests/test_fub_follow_ups.py` run the
real sync against a fake FUB served over httpx (`tests/fub_fake.py`), plus the rules at their edges,
permissions, the six empties and the console endpoints.

Checked on production builds of the portal and console, against a local API with local-only demo
data, by clicking through (see §8):
- the home panel;
- the Follow-ups page: Mine, Team, one agent's queue and back;
- rows that open `…followupboss.com/2/people/view/{id}` in a new tab;
- 375 px with no horizontal scroll;
- roster link and unlink, from the editor under the table;
- adding a call list, and its link built from the account domain;
- saving the settings and seeing *Going cold* reach the portal;
- the Integrations row with no config editor.

Three things found by using the screens, and fixed:
- A new lead with a task due today read "Intro text today · also today". It now reads "Text due
  today".
- The roster's link editor opened inside the table's horizontal scroll, half out of view. It now sits
  under the table, named for whom it edits.
- The console's Win the Day list showed "Disconnected" beside a working FUB connection, because the
  workspace's portal provider rows only got created by the Integrations page. The Win the Day
  endpoint now creates them the same way.

Also changed along the way:
- `tests/test_tenancy.py`'s FUB connect test now uses the fake FUB, since connecting asks FUB first.
- The portal's own-numbers source scan (`tests/test_member_numbers.py`) flags any `own.`/`team.` read
  in the portal source, so the follow-up code names its locals `queue` and `crew`.
