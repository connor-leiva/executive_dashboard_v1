# Onboarding — a person's first thirty days

**Status: built, 2026-09-30.** Backend, frontend and seed are in; the plan editor is not (§9).

## 1. Where it came from

A hand-built HTML page — *Matt Griner — 30-Day Checklist* — delivered as a Claude Design bundle.
Five views over one programme: the day, the week, the scoreboard, a script library and
conversation log, and three standing rules. Twenty-three days from Thursday 1 October to Friday
30 October 2026, a hundred timed blocks, nine scoreboard targets, four "motions".

Two things about it decided the shape of this module.

**The plan was in the bundle.** The days, the blocks and the outcomes were JavaScript literals, so
a second hire meant editing and redeploying a page.

**The progress was in `localStorage`, under `mg30:v1`.** Every check-off, every note, every saved
script and every logged conversation lived in one browser on one machine. That is why the page
had a *"Copy report for Connor & Justin"* button that assembled a text summary for the clipboard:
inside the page there was no other way for anybody else to see how the month was going. Day 30 of
the plan is a review with both of them.

So: the plan is rows, and the progress is rows. The copy-report button does not exist here,
because the people it was written for can open the tab.

## 2. Decisions

| # | Decision | Why |
| --- | --- | --- |
| D1 | **Server-backed, plan as data.** Connor's call, asked explicitly. | The alternative — port the page, keep localStorage — leaves the review reading a pasted message. |
| D2 | **One plan, one person.** Progress lives on the plan's own rows, not a join table keyed by user. | A plan is issued *to* somebody. Two hires on the same programme get two plans. Makes every read one scoped query. |
| D3 | **Not plan-gated.** Absent from `plans._PORTFOLIO_TABS`, so every tier has it. | Discovering on the Monday a new hire starts that onboarding is an upgrade is a bad way to learn what you bought. Revisit if it ever becomes a selling point. |
| D4 | **No plan editor yet.** The write surface is progress only. | Shipping one now would let the first person using the module rewrite the programme they are measured against. §9. |
| D5 | **Portfolio-level, not under a business.** | A new hire is hired by the workspace, and the seeded plan spans recruiting, The Forum, The Edge and VIP tickets in one week. Filing it under one would be arbitrary. |

## 3. Schema — migration `0088_onboarding`

Seven tables, nothing existing altered.

```
onboarding_plan      subject_name, title, starts_on, ends_on, status, motions[], rules[]
  └ onboarding_week  label, date_range, subtitle, intro[], outro[], show_rules
      └ onboarding_day    day_date, end_date?, dow, location, tag, offsite, notes[]
          │                + debrief, debrief_at            ← the subject writes these
          └ onboarding_block  time_label, task, outcome
                               + done, done_at, outcome_state ← and these
  ├ onboarding_target        label, target?, due_on  + actual, done
  ├ onboarding_script        motion, text
  └ onboarding_conversation  name, team, context, next_step  + heat, appointment_set
```

Three shapes a naive schema would flatten, each asserted in `test_onboarding.py`:

* **`end_date`** — a weekend is ONE card over two dates. Two rows would double the day count
  everything else is measured against.
* **`target` nullable** — a scoreboard row with no number ("all members contacted") is answered
  yes or no by `done`. Storing it as a target of 0 or 1 makes a bar out of a question.
* **`outcome_state` nullable** — `hit`, `miss`, or **not answered**, which is not the same as a
  miss. A day that has not happened must not read as a day that went wrong.

`done` and `outcome_state` are separate columns because they answer separate questions: a block
can be worked through and still miss its outcome. A schema recording only attendance would hide
exactly what a thirty-day review is for.

## 4. Service — `services/onboarding.py`

`plan_payload` assembles everything the tab draws in one query set. **No derived number is
stored** — blocks done, outcomes hit, targets met, the per-week and per-day rollups are all
computed per read. A plan is a hundred rows; a stored counter is a second source of truth that
goes wrong the first time something edits a row without updating it.

`missed` is `not met and due_on < today` — the date has to have passed. `not_started` is
`today < starts_on`, so a plan nobody has begun says so instead of showing every counter at zero
as though they were failures.

`seed_plan` writes a spec out as rows and does **not** commit, so a caller can seed several or
roll back.

## 5. Seeding

The spec shape is JSON; the delivered programme is committed at
`backend/scripts/data/onboarding_matt_griner.json` (5 weeks, 23 days, 100 blocks, 9 targets).

```bash
python -m scripts.seed_onboarding --tenant springb --spec scripts/data/onboarding_matt_griner.json --email matt@example.com
```

`--email` links the plan to a user, which is what makes it theirs: it appears in their own tab
and they may write to it. Without it the plan is readable by owners and admins only and can be
linked later. Re-running **refuses** by default — seeding twice is how somebody ends up with two
copies of their month and half their ticks on each — and `--replace` deletes the earlier plan and
everything under it.

## 6. API — `routers/onboarding.py`

Every route requires the `onboarding` tab. Beyond that:

* **Reads** are scoped: a member sees only plans where `user_id` is theirs. A tab grant decides
  whether Onboarding is in somebody's rail; it does not hand them a colleague's day-by-day
  account of a month that went badly. Owners and admins see the workspace.
* **Writes** require the subject, or an owner/admin — admins because a plan is run *with*
  somebody, and because a plan whose subject has no login yet would otherwise be unwritable.
* A plan a member may not read is **404, not 403**, so the response does not confirm the id names
  a real plan.
* Every write is audited with the actor. **The debrief text is not in the audit payload** — only
  its length. An audit log is read by more people than a plan is.

| | |
| --- | --- |
| `GET /onboarding?plan_id=` | the plans this user may open + the full detail of one |
| `PATCH /onboarding/blocks/{id}` | `done`, `outcome_state` (`hit`/`miss`/`""` to clear) |
| `PATCH /onboarding/days/{id}/debrief` | the day's own account |
| `PATCH /onboarding/targets/{id}` | `actual`, `done` |
| `POST/DELETE .../scripts` | the library; the motion must be one the plan declares |
| `POST/PATCH/DELETE .../conversations` | the log, `heat`, `appointment_set` |

DELETEs return `{"ok": true}` rather than 204: the SPA's `delJSON` parses the response, and an
empty body throws in the client rather than anywhere a server log would show it.

### 6.1 Coaching — migration `0089_onboarding_reader`

**Added after the first plan shipped, because granting the tab exposed the gap.** The rule in
§6 — your own plan, unless you are an owner or admin — is the right default and is wrong for the
one person the plan is built around. Matt's plan has him in a huddle with Justin at 8:30 on day
one, trained by him at nine, debriefing with him at 4:45, and reviewing the month with him on day
thirty. Justin is a member. He was granted the tab and got an empty page.

`onboarding_reader` names, per plan, who may read it: one row per person per plan, CASCADE on
both sides, unique on `(plan_id, user_id)`. Seeded with `--coach EMAIL`, repeatable.

**Read only, and `may_write` does not consult the table at all.** Somebody ticking off another
person's blocks for them makes the record of what happened less true rather than more, and the
record being true is the whole value of the month. Owners and admins keep write access, which is
how a correction gets made.

**Per plan, not a role.** A coach on one person's month does not acquire a view of everybody's —
which is what a `coach` role would have quietly meant. Asserted.

The payload carries `relationship`: `subject` | `coach` | `manager`. A coach sees every control
disabled, and without being told which of the three they are, a read-only page is
indistinguishable from a broken one.

## 7. Frontend — `Onboarding.jsx`, `useOnboarding.js`

The five views, rebuilt in the app's own system. The bundle's palette, type and left sidebar did
not come with it: colours are `theme.js` tokens so a workspace that chose its own gets them here,
type is `--font-display/text/data`, and the page's sidebar is gone because this app already has
one.

**Nothing in the browser computes a total.** A click patches the row it touched so the control
responds at once, the write is sent, and the payload is refetched **once the write has
committed**.

> That ordering is the module's one real bug so far, found by clicking rather than by review.
> The hook reloaded *before* sending the write, so ticking a block and judging its outcome in
> quick succession left the screen reading "2 hit, 0 missed" over a database holding one of each,
> and it stayed wrong until something else refetched. Fixed twice over: writes reload on
> completion, and the hook drops out-of-order responses by sequence number, because working
> through a day means several reads in flight and whichever returns last is not the newest.

## 8. Registration

`PLATFORM_TABS["onboarding"]` in `services/tabs.py`, appended to the descriptor order after
`ads`; a glyph in `productIcons.jsx` (a calendar with one day filled — deliberately not a
checkbox, since `books` and `agents` already carry checks); `NAV` in `CommandCenter.jsx` as the
offline fallback.

> **`tenant_tabs` and `tenant_tab_descriptors` were the same computation written twice**, and
> they drifted the moment a module was added: `onboarding` reached the nav and never reached the
> authorization vocabulary, so the rail offered the tab and `assert_tab` refused it with "No
> access to this view" — for members only, because owners pass the grant check implicitly.
> `tenant_tabs` now derives from the descriptors. The mark guard in `test_brand_rules.py` was
> rederived from `PLATFORM_TABS` at the same time, for the same reason: it listed `ads` as a
> hand-kept exception, and `onboarding` would have been the second.

## 9. Not built

1. **A plan editor** (D4). Creating and assigning plans, editing days and blocks. Until then a
   plan is a JSON file and a seed command.
2. **Linking a plan to a user, or naming a coach, from the UI.** Today both are flags at seed
   time (`--email`, `--coach`).
3. **Reminders.** The plan says to log a conversation the same day; nothing nudges.
4. **Anything written back to recruiting.** A logged conversation stays inside the plan; the
   pipeline is somebody else's system of record.
