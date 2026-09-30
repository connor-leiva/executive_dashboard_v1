"""Onboarding — the schema, the payload and the authorization around it.

The module arrived as a hand-built HTML page whose every check-off lived in one browser's
localStorage. Two things follow from moving that into the database, and both are what these
tests are about:

  * The PLAN is now data, so a seed has to reproduce it exactly -- the weekend that is one card
    spanning two dates, the three days spent somewhere else, and the scoreboard rows that have
    no number and are answered yes or no.
  * The PROGRESS is now shared, so who may read and write it is a real question with a wrong
    answer. A tab grant puts Onboarding in somebody's rail; it must not hand them a colleague's
    day-by-day account of a month that went badly.
"""
import datetime as dt
import json
import pathlib
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import (OnboardingBlock, OnboardingDay, OnboardingPlan, OnboardingReader,
                        OnboardingScript, OnboardingTarget, Tenant, User)
from app.security import hash_pw, make_token
from app.services import onboarding as eng

TRANSPORT = ASGITransport(app=app)
SPEC_PATH = (pathlib.Path(__file__).resolve().parents[1] / "scripts" / "data"
             / "onboarding_matt_griner.json")


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token: str, host: str) -> dict:
    return {"Authorization": f"Bearer {token}", "x-tenant-host": host}


def _spec() -> dict:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
async def ctx():
    """One workspace, a plan seeded into it, its subject, a colleague, and an owner."""
    from app.seed import seed
    await seed()

    async with SessionLocal() as s:
        tenant = Tenant(slug="onbco", name="Onboarding Co")
        s.add(tenant)
        await s.flush()

        def mk(email, role, tabs=None):
            return User(tenant_id=tenant.id, email=email, name=email.split("@")[0], role=role,
                        password_hash=hash_pw("x" * 12), status="active",
                        tab_access=tabs or [])

        owner = mk("owner@onbco.test", "owner")
        subject = mk("matt@onbco.test", "member", ["onboarding"])
        colleague = mk("other@onbco.test", "member", ["onboarding"])
        # The person who RUNS the month. A member like any other, which is the whole problem
        # this fixture exists to pin.
        coach = mk("coach@onbco.test", "member", ["onboarding"])
        s.add_all([owner, subject, colleague, coach])
        await s.flush()

        plan = await eng.seed_plan(s, tenant.id, _spec(), user_id=subject.id)
        # A second plan, belonging to nobody, so "a member sees only their own" has something
        # to actually exclude rather than passing on an empty set.
        other_spec = {**_spec(), "subject_name": "Someone Else"}
        other = await eng.seed_plan(s, tenant.id, other_spec, user_id=colleague.id)
        s.add(OnboardingReader(tenant_id=tenant.id, plan_id=plan.id, user_id=coach.id))
        await s.commit()

        return {
            "tenant": tenant.id, "host": "onbco.localhost", "plan": plan.id, "other": other.id,
            "owner": make_token(owner.id, tenant.id, 0),
            "subject": make_token(subject.id, tenant.id, 0),
            "colleague": make_token(colleague.id, tenant.id, 0),
            "coach": make_token(coach.id, tenant.id, 0),
            "subject_id": subject.id, "coach_id": coach.id,
        }


# ── the plan, as data ─────────────────────────────────────────────────────────────────────

async def test_the_seed_reproduces_the_written_programme(ctx):
    """Every count from the source, and the three shapes a naive seed would flatten."""
    spec = _spec()
    async with SessionLocal() as s:
        days = (await s.execute(select(OnboardingDay).where(
            OnboardingDay.plan_id == ctx["plan"]).order_by(OnboardingDay.sort_order))).scalars().all()
        blocks = (await s.execute(select(OnboardingBlock).where(
            OnboardingBlock.day_id.in_([d.id for d in days])))).scalars().all()
        targets = (await s.execute(select(OnboardingTarget).where(
            OnboardingTarget.plan_id == ctx["plan"]))).scalars().all()

    assert len(days) == sum(len(w["days"]) for w in spec["weeks"]) == 23
    assert len(blocks) == 100
    assert len(targets) == 9

    # A weekend is ONE card over two dates. Stored as two rows it would appear twice in the
    # ledger and double the day count everything else is measured against.
    spans = [d for d in days if d.end_date]
    assert len(spans) == 1 and (spans[0].day_date, spans[0].end_date) == (
        dt.date(2026, 10, 3), dt.date(2026, 10, 4))

    # The conference days are somewhere else, and the UI draws them differently.
    assert sum(1 for d in days if d.offsite) == 3

    # A scoreboard row with no number is answered yes or no, not counted to zero.
    assert sum(1 for t in targets if t.target is None) == 1
    assert sorted(t.target for t in targets if t.target is not None) == [2, 2, 2, 5, 5, 5, 5, 15]


async def test_a_weekend_reads_as_one_span_and_a_weekday_as_a_date():
    """The title is built from the dates rather than stored, so a plan crossing a month reads
    correctly without its author having written the label out."""
    one = OnboardingDay(day_date=dt.date(2026, 10, 1), dow="Thursday")
    span = OnboardingDay(day_date=dt.date(2026, 10, 3), end_date=dt.date(2026, 10, 4),
                         dow="Weekend")
    across = OnboardingDay(day_date=dt.date(2026, 10, 31), end_date=dt.date(2026, 11, 1),
                           dow="Weekend")
    assert eng._day_title(one) == "Thursday, October 1"
    assert eng._day_title(span) == "Weekend, October 3 to 4"
    assert eng._day_title(across) == "Weekend, October 31 to November 1"


# ── the payload ───────────────────────────────────────────────────────────────────────────

async def test_the_payload_counts_progress_rather_than_storing_it(ctx):
    """Ticking a block moves the day, the week and the plan totals in one read.

    No counter is stored anywhere. That is the point: a stored total is a second source of
    truth, and the first thing that edits a row without updating it makes the tab lie.
    """
    async with SessionLocal() as s:
        plan = await s.get(OnboardingPlan, ctx["plan"])
        day = (await s.execute(select(OnboardingDay).where(
            OnboardingDay.plan_id == plan.id).order_by(OnboardingDay.sort_order))).scalars().first()
        blocks = (await s.execute(select(OnboardingBlock).where(
            OnboardingBlock.day_id == day.id).order_by(OnboardingBlock.sort_order))).scalars().all()
        blocks[0].done = True
        blocks[1].done = True
        blocks[1].outcome_state = "hit"
        blocks[2].outcome_state = "miss"
        await s.commit()

        user = await s.get(User, ctx["subject_id"])
        payload = await eng.plan_payload(s, plan, user, today=dt.date(2026, 10, 1))

    d0 = payload["days"][0]
    assert (d0["done"], d0["hits"], d0["misses"]) == (2, 1, 1)
    assert d0["total"] == 6
    week0 = next(w for w in payload["weeks"] if d0["id"] in w["day_ids"])
    assert (week0["done"], week0["hits"], week0["misses"]) == (2, 1, 1)
    assert payload["totals"]["blocks"] == 100
    assert payload["totals"]["blocks_done"] == 2
    assert payload["totals"]["hits"] == 1 and payload["totals"]["misses"] == 1


async def test_a_target_is_not_missed_until_its_date_has_gone_by(ctx):
    """`missed` is what turns a scoreboard red, and a target that is merely outstanding must not
    trip it -- a Monday that says five things were missed by Friday is not information."""
    async with SessionLocal() as s:
        plan = await s.get(OnboardingPlan, ctx["plan"])
        user = await s.get(User, ctx["subject_id"])
        before = await eng.plan_payload(s, plan, user, today=dt.date(2026, 10, 1))
        after = await eng.plan_payload(s, plan, user, today=dt.date(2026, 10, 30))

    due_oct2 = [t for t in before["targets"] if t["due_on"] == "2026-10-02"]
    assert due_oct2 and not any(t["missed"] for t in due_oct2), "outstanding, not missed"
    assert before["totals"]["targets_missed"] == 0
    # By the last day everything unmet has a date behind it.
    assert after["totals"]["targets_missed"] == after["totals"]["targets"] - after["totals"]["targets_met"]


async def test_before_the_first_day_the_plan_says_so(ctx):
    async with SessionLocal() as s:
        plan = await s.get(OnboardingPlan, ctx["plan"])
        user = await s.get(User, ctx["subject_id"])
        early = await eng.plan_payload(s, plan, user, today=dt.date(2026, 9, 20))
        started = await eng.plan_payload(s, plan, user, today=dt.date(2026, 10, 2))
    assert early["not_started"] is True
    assert started["not_started"] is False


# ── who may read, and who may write ───────────────────────────────────────────────────────

async def test_a_member_sees_only_their_own_plan(ctx):
    """The tab grant decides whether Onboarding is in somebody's rail. It does not decide
    whether they may read a colleague's account of their month."""
    async with _client() as c:
        mine = await c.get("/api/v1/onboarding", headers=_H(ctx["subject"], ctx["host"]))
        theirs = await c.get("/api/v1/onboarding", headers=_H(ctx["owner"], ctx["host"]))
    assert mine.status_code == 200, mine.text
    assert [p["subject_name"] for p in mine.json()["plans"]] == ["Matt Griner"]
    assert mine.json()["plan"]["subject_name"] == "Matt Griner"
    # An owner sees the workspace.
    assert {p["subject_name"] for p in theirs.json()["plans"]} == {"Matt Griner", "Someone Else"}


async def test_a_member_cannot_open_a_plan_by_id_that_is_not_theirs(ctx):
    """The id is a uuid, so this is not a guessing attack -- it is a link pasted into a chat,
    which is exactly how it would happen."""
    async with _client() as c:
        r = await c.get(f"/api/v1/onboarding?plan_id={ctx['other']}",
                        headers=_H(ctx["subject"], ctx["host"]))
    assert r.status_code == 404, r.text


async def test_a_member_cannot_tick_off_someone_elses_block(ctx):
    """And is not told whether it exists."""
    async with SessionLocal() as s:
        day = (await s.execute(select(OnboardingDay).where(
            OnboardingDay.plan_id == ctx["other"]))).scalars().first()
        block = (await s.execute(select(OnboardingBlock).where(
            OnboardingBlock.day_id == day.id))).scalars().first()
        block_id = block.id

    async with _client() as c:
        mine = await c.patch(f"/api/v1/onboarding/blocks/{block_id}", json={"done": True},
                             headers=_H(ctx["subject"], ctx["host"]))
        owner = await c.patch(f"/api/v1/onboarding/blocks/{block_id}", json={"done": True},
                              headers=_H(ctx["owner"], ctx["host"]))
    # 404, not 403. The write path resolves the plan through the same reader that limits a
    # member to their own, so a plan they may not read is not found rather than refused -- which
    # also means the response does not confirm that the id names a real plan. The test asked for
    # 403 first; 404 is the better answer and the code is left as it is.
    assert mine.status_code == 404, mine.text
    # An owner may: a plan is run WITH somebody, and the seeded one ends in a review with two.
    assert owner.status_code == 200, owner.text


async def test_an_id_from_another_workspace_is_not_found(ctx):
    """Tenant-scoped on the row itself, so a foreign id 404s here rather than walking up to a
    plan check that would also have refused it. One is enough; both is what makes it not matter
    which is read first."""
    async with SessionLocal() as s:
        springb = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        stray = OnboardingPlan(tenant_id=springb.id, subject_name="Not Yours",
                               title="30-Day", starts_on=dt.date(2026, 10, 1),
                               ends_on=dt.date(2026, 10, 30))
        s.add(stray)
        await s.flush()
        day = OnboardingDay(tenant_id=springb.id, plan_id=stray.id, week_id=stray.id,
                            day_date=dt.date(2026, 10, 1), dow="Thursday")
        # week_id points at the plan's id, which no week has: this row is never read, only
        # addressed, and the seed path is covered above.
        s.add(day)
        await s.flush()
        block = OnboardingBlock(tenant_id=springb.id, day_id=day.id, time_label="8:30",
                                task="t", outcome="o")
        s.add(block)
        await s.commit()
        foreign_block = block.id

    async with _client() as c:
        r = await c.patch(f"/api/v1/onboarding/blocks/{foreign_block}", json={"done": True},
                          headers=_H(ctx["owner"], ctx["host"]))
    assert r.status_code == 404, r.text


async def test_an_outcome_is_hit_miss_or_cleared_and_nothing_else(ctx):
    async with SessionLocal() as s:
        day = (await s.execute(select(OnboardingDay).where(
            OnboardingDay.plan_id == ctx["plan"]).order_by(OnboardingDay.sort_order))).scalars().first()
        block = (await s.execute(select(OnboardingBlock).where(
            OnboardingBlock.day_id == day.id).order_by(OnboardingBlock.sort_order))).scalars().all()[3]
        bid = block.id

    async with _client() as c:
        h = _H(ctx["subject"], ctx["host"])
        bad = await c.patch(f"/api/v1/onboarding/blocks/{bid}", json={"outcome_state": "great"},
                            headers=h)
        good = await c.patch(f"/api/v1/onboarding/blocks/{bid}", json={"outcome_state": "hit"},
                             headers=h)
        clear = await c.patch(f"/api/v1/onboarding/blocks/{bid}", json={"outcome_state": ""},
                              headers=h)
    assert bad.status_code == 400
    assert good.json()["outcome_state"] == "hit"
    # Cleared is NULL, not "miss". A block nobody has judged yet is not a block that went wrong.
    assert clear.json()["outcome_state"] is None


async def test_a_script_must_name_a_motion_the_plan_declares(ctx):
    """Free text would let a typo open a fifth column holding one line nobody finds again."""
    async with _client() as c:
        h = _H(ctx["subject"], ctx["host"])
        ok = await c.post(f"/api/v1/onboarding/plans/{ctx['plan']}/scripts",
                          json={"motion": "Recruiting", "text": "What he opens with."}, headers=h)
        bad = await c.post(f"/api/v1/onboarding/plans/{ctx['plan']}/scripts",
                           json={"motion": "Recruting", "text": "typo"}, headers=h)
    assert ok.status_code == 201, ok.text
    assert bad.status_code == 400

    async with SessionLocal() as s:
        rows = (await s.execute(select(OnboardingScript).where(
            OnboardingScript.plan_id == ctx["plan"]))).scalars().all()
    assert [r.motion for r in rows] == ["Recruiting"]


async def test_a_conversation_is_logged_rated_and_removable(ctx):
    async with _client() as c:
        h = _H(ctx["subject"], ctx["host"])
        made = await c.post(f"/api/v1/onboarding/plans/{ctx['plan']}/conversations",
                            json={"name": "Dana", "team": "Summit Group",
                                  "context": "Met at the mixer", "next_step": "Coffee Tuesday"},
                            headers=h)
        cid = made.json()["id"]
        rated = await c.patch(f"/api/v1/onboarding/conversations/{cid}",
                              json={"heat": "warm", "appointment_set": True}, headers=h)
        bad = await c.patch(f"/api/v1/onboarding/conversations/{cid}",
                            json={"heat": "lukewarm"}, headers=h)
        payload = await c.get("/api/v1/onboarding", headers=h)
        gone = await c.delete(f"/api/v1/onboarding/conversations/{cid}", headers=h)
        after = await c.get("/api/v1/onboarding", headers=h)

    assert made.status_code == 201, made.text
    assert rated.json() == {"id": cid, "heat": "warm", "appointment_set": True}
    assert bad.status_code == 400
    assert payload.json()["plan"]["totals"]["appointments"] == 1
    assert gone.status_code == 200 and gone.json() == {"ok": True}
    assert after.json()["plan"]["totals"]["conversations"] == 0


async def test_a_debrief_is_not_copied_into_the_audit_log(ctx):
    """An audit log is read by more people than a plan is, and a debrief is somebody's account
    of their own day. The log records that one was written and how long it was."""
    from app.models import AuditLog

    async with SessionLocal() as s:
        day = (await s.execute(select(OnboardingDay).where(
            OnboardingDay.plan_id == ctx["plan"]).order_by(OnboardingDay.sort_order))).scalars().first()
        did = day.id

    secret = "Struggled with the afternoon block and did not want to say so on the huddle."
    async with _client() as c:
        r = await c.patch(f"/api/v1/onboarding/days/{did}/debrief", json={"debrief": secret},
                          headers=_H(ctx["subject"], ctx["host"]))
    assert r.status_code == 200 and r.json()["debrief"] == secret

    async with SessionLocal() as s:
        rows = (await s.execute(select(AuditLog).where(
            AuditLog.action == "onboarding.debrief"))).scalars().all()
        stored = await s.get(OnboardingDay, did)
    assert rows and all(secret not in json.dumps(r.detail or {}) for r in rows)
    assert rows[-1].detail["chars"] == len(secret)
    assert stored.debrief == secret and stored.debrief_at is not None


# ── the tab itself ────────────────────────────────────────────────────────────────────────

async def test_onboarding_is_in_the_nav_for_every_plan():
    """Absent from plans._PORTFOLIO_TABS on purpose, so no tier gates it. A workspace finding
    out on the Monday a new hire starts that onboarding is an upgrade is a bad way to learn
    what they bought."""
    from app import plans
    from app.services.tabs import PLATFORM_TABS

    assert "onboarding" in PLATFORM_TABS
    assert "onboarding" not in plans._PORTFOLIO_TABS
    for tier in plans.ORDER:
        class _T:
            plan = tier
        assert "onboarding" in plans.plan_tabs(_T(), ["portfolio", "onboarding", "binder"])


async def test_the_nav_descriptor_carries_the_tab(ctx):
    from app.services.tabs import tenant_tab_descriptors

    async with SessionLocal() as s:
        keys = [d["key"] for d in await tenant_tab_descriptors(s, ctx["tenant"])]
    assert "onboarding" in keys
    # After the other platform modules, which is where Connor asked for it.
    assert keys.index("onboarding") > keys.index("binder")


# ── coaching: read without write ──────────────────────────────────────────────────────────

async def test_a_coach_reads_the_plan_they_run(ctx):
    """The rule without this is "your own plan, unless you are an owner or admin", which is the
    right default and wrong for the one person the plan is built around. The seeded plan has its
    subject in a huddle with Justin at 8:30 on day one, trained by him at nine, debriefing with
    him at 4:45 and reviewing the month with him on day thirty -- and Justin, a member, could see
    none of it. He was granted the tab and got an empty page. That is how this was found."""
    async with _client() as c:
        r = await c.get("/api/v1/onboarding", headers=_H(ctx["coach"], ctx["host"]))
    body = r.json()
    assert r.status_code == 200, r.text
    assert [p["subject_name"] for p in body["plans"]] == ["Matt Griner"]
    assert body["plan"]["subject_name"] == "Matt Griner"
    assert body["plan"]["relationship"] == "coach"
    assert body["plan"]["can_write"] is False


async def test_a_coach_cannot_write_anything(ctx):
    """Read access, and only read access. Somebody ticking off another person's blocks for them
    makes the record of what happened less true rather than more, and the record being true is
    the whole value of the month. Every write route is checked, not a sample -- a permission that
    holds on four endpoints and leaks on the fifth is not a permission."""
    async with SessionLocal() as s:
        day = (await s.execute(select(OnboardingDay).where(
            OnboardingDay.plan_id == ctx["plan"]).order_by(OnboardingDay.sort_order))).scalars().first()
        block = (await s.execute(select(OnboardingBlock).where(
            OnboardingBlock.day_id == day.id))).scalars().first()
        target = (await s.execute(select(OnboardingTarget).where(
            OnboardingTarget.plan_id == ctx["plan"]))).scalars().first()
        script = (await s.execute(select(OnboardingScript).where(
            OnboardingScript.plan_id == ctx["plan"]))).scalars().first()
        day_id, block_id, target_id = day.id, block.id, target.id
        script_id = script.id if script else None
        # Captured BEFORE, and compared to after. Earlier tests in this module legitimately tick
        # blocks off on this plan, so asserting a literal False here would be asserting their
        # state rather than the coach's lack of effect.
        before = (block.done, block.outcome_state, day.debrief, target.actual, target.done)

    h = _H(ctx["coach"], ctx["host"])
    async with _client() as c:
        calls = [
            await c.patch(f"/api/v1/onboarding/blocks/{block_id}", json={"done": True}, headers=h),
            await c.patch(f"/api/v1/onboarding/days/{day_id}/debrief",
                          json={"debrief": "not mine to write"}, headers=h),
            await c.patch(f"/api/v1/onboarding/targets/{target_id}", json={"actual": 99}, headers=h),
            await c.post(f"/api/v1/onboarding/plans/{ctx['plan']}/scripts",
                         json={"motion": "Recruiting", "text": "no"}, headers=h),
            await c.post(f"/api/v1/onboarding/plans/{ctx['plan']}/conversations",
                         json={"name": "no"}, headers=h),
        ]
        if script_id:
            calls.append(await c.delete(f"/api/v1/onboarding/scripts/{script_id}", headers=h))

    assert all(r.status_code == 403 for r in calls), [r.status_code for r in calls]

    # And nothing moved.
    async with SessionLocal() as s:
        b = await s.get(OnboardingBlock, block_id)
        d = await s.get(OnboardingDay, day_id)
        t = await s.get(OnboardingTarget, target_id)
        assert (b.done, b.outcome_state, d.debrief, t.actual, t.done) == before


async def test_coaching_one_plan_does_not_open_another(ctx):
    """The grant is per plan, not a role. A coach on one person's month must not acquire a view
    of everybody's -- which is what a `coach` ROLE would have quietly meant."""
    async with _client() as c:
        r = await c.get(f"/api/v1/onboarding?plan_id={ctx['other']}",
                        headers=_H(ctx["coach"], ctx["host"]))
    assert r.status_code == 404, r.text


async def test_the_payload_says_which_of_the_three_you_are(ctx):
    """A read-only page is indistinguishable from a broken one unless it says why."""
    async with _client() as c:
        h = {"subject": ctx["subject"], "coach": ctx["coach"], "owner": ctx["owner"]}
        got = {}
        for who, tok in h.items():
            body = (await c.get(f"/api/v1/onboarding?plan_id={ctx['plan']}",
                                headers=_H(tok, ctx["host"]))).json()
            got[who] = (body["plan"]["relationship"], body["plan"]["can_write"])
    assert got == {"subject": ("subject", True), "coach": ("coach", False),
                   "owner": ("manager", True)}
