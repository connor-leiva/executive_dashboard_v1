"""AI Employees — Step 1 (models + skill seed + demo fixture) + Steps 2-3
(worker dispatch/execute + Anthropic call/validate seam).

Later steps add router / lifecycle tests here.
"""
import datetime as dt
import json

import pytest
from sqlalchemy import select

from app.seed import seed
from app.db import SessionLocal
from app.config import settings
from app.models import (Tenant, Business, Launch, AISkill, AIEmployee, AIRun, AIArtifact,
                        AIEmployeeSkill)
from app.services import ai_employees
from app.services.ai_skills import SKILL_KEYS, SKILLS
from app.services.launch import DEFAULT_SHIFT_CURVE


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def test_skill_catalog_seeded():
    async with SessionLocal() as s:
        skills = {sk.key: sk for sk in (await s.execute(select(AISkill))).scalars().all()}
    assert set(skills) == set(SKILL_KEYS) == {
        "audit", "trend_brief", "strategy", "design_carousel", "reel_script", "measure"}
    # each skill carries a prompt, an artifact-kind list, and a run output_contract
    for sk in skills.values():
        assert sk.default_prompt and sk.artifact_kinds
        assert sk.output_contract.get("required") == ["reads", "summary", "artifacts"]


def test_skill_prompts_instruct_the_full_envelope():
    """Each prompt must tell the model to emit the artifact WRAPPER (title + payload), not
    just the payload shape — else a real run fails output_contract validation on the missing
    'title' (caught live: the model returns payload-only unless the envelope is spelled out)."""
    for sk in SKILLS:
        p = sk["default_prompt"]
        assert '"title"' in p and '"artifacts"' in p and '"payload"' in p, sk["key"]


async def test_demo_fixture_loads_summer_shipped_run():
    from app.demo_fixture_ai import load_demo_ai
    async with SessionLocal() as s:
        tid = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id
        emp = await load_demo_ai(s, tid)
        eid = emp.id
    async with SessionLocal() as s:
        emp = (await s.execute(select(AIEmployee).where(AIEmployee.id == eid))).scalar_one()
        assert emp.name == "Summer" and emp.role_title == "Social Media Manager"
        skills = (await s.execute(select(AIEmployeeSkill).where(AIEmployeeSkill.employee_id == eid))).scalars().all()
        assert len(skills) == 6
        runs = (await s.execute(select(AIRun).where(AIRun.employee_id == eid))).scalars().all()
        assert len(runs) == 1 and runs[0].status == "shipped" and runs[0].trigger == "condition"
        arts = (await s.execute(select(AIArtifact).where(AIArtifact.run_id == runs[0].id))).scalars().all()
        assert {a.kind for a in arts} == {"audit", "trend", "strategy", "design", "script", "measure"}
        assert {a.lane for a in arts} == {"Intel", "Strategy", "Creative", "Tracking"}
        assert all(a.dest_label for a in arts)          # lane + dest_label carried on the artifact
    # idempotent — a second load leaves exactly one Summer
    async with SessionLocal() as s:
        tid = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id
        await load_demo_ai(s, tid)
    async with SessionLocal() as s:
        assert len((await s.execute(select(AIEmployee).where(AIEmployee.name == "Summer"))).scalars().all()) == 1


# ── Steps 2-3: worker dispatch / execute + the Anthropic call/validate seam ───
async def _tid(s) -> "uuid.UUID":
    return (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id


async def _mk_employee(s, tid, **cfg) -> AIEmployee:
    emp = AIEmployee(tenant_id=tid, name="Tester", role_title="QA", config=cfg or {})
    s.add(emp)
    await s.flush()
    return emp


_VALID_STRATEGY = {
    "reads": ["Registrations trailing the curve", "Reflection hooks are outperforming"],
    "summary": "3-day pivot to close the pace gap",
    "artifacts": [{"title": "Strategy pivot", "payload": {
        "kind": "strategy", "shift": "Lead with reflection hooks",
        "plan": [{"day": "Day 4", "what": "Carousel: the cost of waiting", "cta": "Register"}],
        "why": "Reflection hooks convert at 3x"}}],
}


def _enable(monkeypatch, **over):
    monkeypatch.setattr(settings, "AI_EMPLOYEES_ENABLED", True)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(ai_employees, "_client", lambda: object())   # never build a real client
    for k, v in over.items():
        monkeypatch.setattr(settings, k, v)


async def test_execute_one_drafts_awaiting_approval(monkeypatch):
    """A queued run → one Anthropic call → validated draft artifact + awaiting_approval + tokens."""
    _enable(monkeypatch)

    async def fake_call(client, model, system, user):
        return json.dumps(_VALID_STRATEGY), 111, 222
    monkeypatch.setattr(ai_employees, "_claude_call", fake_call)

    async with SessionLocal() as s:
        tid = await _tid(s)
        emp = await _mk_employee(s, tid)
        run = AIRun(tenant_id=tid, employee_id=emp.id, skill_key="strategy",
                    trigger="manual", status="queued")
        s.add(run)
        await s.commit()
        rid = run.id

        picked = await ai_employees.execute_one(s, tid)
        assert picked is True
    async with SessionLocal() as s:
        run = (await s.execute(select(AIRun).where(AIRun.id == rid))).scalar_one()
        assert run.status == "awaiting_approval"
        assert run.tokens_in == 111 and run.tokens_out == 222
        assert run.summary == _VALID_STRATEGY["summary"] and run.reads
        arts = (await s.execute(select(AIArtifact).where(AIArtifact.run_id == rid))).scalars().all()
        assert len(arts) == 1
        a = arts[0]
        assert a.state == "draft" and a.kind == "strategy" and a.lane == "Strategy"
        assert a.dest_label == "Strategy memo"          # lane/dest derived from KIND_META


async def test_execute_one_invalid_output_fails_after_retry(monkeypatch):
    """Two bad replies (invalid JSON) → the run fails, tokens still counted, no artifacts."""
    _enable(monkeypatch)
    calls = {"n": 0}

    async def bad_call(client, model, system, user):
        calls["n"] += 1
        return "not json at all", 5, 6
    monkeypatch.setattr(ai_employees, "_claude_call", bad_call)

    async with SessionLocal() as s:
        tid = await _tid(s)
        emp = await _mk_employee(s, tid)
        run = AIRun(tenant_id=tid, employee_id=emp.id, skill_key="strategy",
                    trigger="manual", status="queued")
        s.add(run)
        await s.commit()
        rid = run.id
        assert await ai_employees.execute_one(s, tid) is True
    assert calls["n"] == 2                               # one retry on invalid output
    async with SessionLocal() as s:
        run = (await s.execute(select(AIRun).where(AIRun.id == rid))).scalar_one()
        assert run.status == "failed" and run.error
        assert (await s.execute(select(AIArtifact).where(AIArtifact.run_id == rid))).scalars().first() is None


async def test_over_budget_queues_skipped(monkeypatch):
    """When the month's token spend has hit the budget, a queued run lands skipped_budget."""
    _enable(monkeypatch, AI_EMPLOYEES_TOKEN_BUDGET=100)
    async with SessionLocal() as s:
        tid = await _tid(s)
        emp = await _mk_employee(s, tid)
        s.add(AIRun(tenant_id=tid, employee_id=emp.id, skill_key="strategy", trigger="manual",
                    status="failed", tokens_in=80, tokens_out=40))          # 120 ≥ 100
        await s.commit()
        assert await ai_employees._over_budget(s, tid) is True
        run = await ai_employees._queue_run(s, tid, emp, "strategy", "scheduled", None,
                                            over_budget=True)
        await s.commit()
        assert run.status == "skipped_budget"


async def test_cron_due_fires_once_per_slot(monkeypatch):
    """_cron_due is true when no scheduled run exists for the current cron slot, then false
    once a scheduled run has been recorded past that slot."""
    async with SessionLocal() as s:
        tid = await _tid(s)
        emp = await _mk_employee(s, tid)
        es = AIEmployeeSkill(tenant_id=tid, employee_id=emp.id, skill_key="trend_brief")
        s.add(es)
        await s.commit()
        now_local = dt.datetime.now(dt.timezone.utc)
        assert await ai_employees._cron_due(s, es, "0 7 * * 1", now_local) is True
        # record a scheduled run (created_at = now, past the most-recent Monday-7am fire)
        s.add(AIRun(tenant_id=tid, employee_id=emp.id, skill_key="trend_brief",
                    trigger="scheduled", status="queued"))
        await s.commit()
        assert await ai_employees._cron_due(s, es, "0 7 * * 1", now_local) is False


async def test_condition_debounce_max_per_day(monkeypatch):
    async with SessionLocal() as s:
        tid = await _tid(s)
        emp = await _mk_employee(s, tid)
        s.add(AIRun(tenant_id=tid, employee_id=emp.id, skill_key=ai_employees.PACE_RESPONSE,
                    trigger="condition", status="awaiting_approval"))
        await s.commit()
        # debounce counts today's condition runs for the EMPLOYEE (the pace check fires a
        # pace_response run, not the carrier skill)
        assert await ai_employees._condition_fired_today(s, emp.id, max_per_day=1) is True
        assert await ai_employees._condition_fired_today(s, emp.id, max_per_day=2) is False


def test_build_scheduler_registers_ai_jobs(monkeypatch):
    """The scheduler (shared by the standalone worker and RUN_WORKER_IN_API) registers the two
    AI jobs when the flag is on — the queue only drains if something runs them."""
    from app import worker
    monkeypatch.setattr(settings, "AI_EMPLOYEES_ENABLED", True)
    on = {j.func.__name__ for j in worker.build_scheduler().get_jobs()}
    assert {"tick", "ai_dispatch", "ai_execute"} <= on
    monkeypatch.setattr(settings, "AI_EMPLOYEES_ENABLED", False)
    off = {j.func.__name__ for j in worker.build_scheduler().get_jobs()}
    assert "ai_execute" not in off and "tick" in off


async def test_pace_response_cascade_drafts_all_skills(monkeypatch):
    """One pace_response run → diagnose + the six skills in order → all six draft artifacts on
    one run, awaiting_approval (the coordinated 'AI employee in action')."""
    _enable(monkeypatch)

    async def fake_claude(client, model, system, user):   # only _diagnose hits this
        return json.dumps({"reads": ["Behind the curve", "Reels are winning"]}), 5, 5
    monkeypatch.setattr(ai_employees, "_claude_call", fake_claude)

    async def fake_call_skill(skill_def, prompt, *, client=None, model=None):
        kind = (skill_def["artifact_kinds"] or ["audit"])[0]
        return ({"reads": [], "summary": "ok",
                 "artifacts": [{"title": f"{kind} draft", "payload": {"kind": kind}}]}, 10, 12, None)
    monkeypatch.setattr(ai_employees, "call_skill", fake_call_skill)

    async with SessionLocal() as s:
        tid = await _tid(s)
        emp = await _mk_employee(s, tid)
        await ai_employees.seed_employee_skills(s, emp)
        run = AIRun(tenant_id=tid, employee_id=emp.id, skill_key=ai_employees.PACE_RESPONSE,
                    trigger="condition", status="running",
                    trigger_context={"source": "GoHighLevel", "title": "behind", "facts": ["14% under curve"]})
        s.add(run)
        await s.commit()
        rid = run.id
        # call the cascade directly (execute_one's oldest-queued picker races with leftover
        # queued runs from earlier tests sharing this DB)
        await ai_employees.execute_pace_response(s, tid, run)
    async with SessionLocal() as s:
        run = (await s.execute(select(AIRun).where(AIRun.id == rid))).scalar_one()
        assert run.status == "awaiting_approval" and run.reads and run.tokens_in > 0
        arts = (await s.execute(select(AIArtifact).where(AIArtifact.run_id == rid))).scalars().all()
        assert {a.kind for a in arts} == {"audit", "trend", "strategy", "design", "script", "measure"}
        assert {a.lane for a in arts} == {"Intel", "Strategy", "Creative", "Tracking"}


async def test_cowork_audit_feeds_cascade_context():
    """A Cowork audit artifact flows into the cascade's audit/trend context as recent_audits —
    wiring the autonomous audit into the full response so it no longer runs blind."""
    async with SessionLocal() as s:
        tid = await _tid(s)
        emp = await _mk_employee(s, tid)
        run = AIRun(tenant_id=tid, employee_id=emp.id, skill_key="audit", trigger="manual",
                    status="awaiting_approval")
        s.add(run)
        await s.flush()
        s.add(AIArtifact(tenant_id=tid, run_id=run.id, kind="audit", lane="Intel", title="t",
                         dest_label="Instagram · Cowork", state="draft",
                         payload={"kind": "audit", "handle": "@rival",
                                  "top": [{"name": "Reel", "val": "6.6x", "w": "100%"}],
                                  "mechanics": ["curiosity-gap hook"]}))
        await s.commit()
        assert (await ai_employees.recent_cowork_audits(s, emp.id))[0]["handle"] == "@rival"
        crun = AIRun(tenant_id=tid, employee_id=emp.id, skill_key=ai_employees.PACE_RESPONSE,
                     trigger="condition", status="running")
        s.add(crun)
        await s.flush()
        _, ctx = await ai_employees.build_context(s, tid, emp, None, crun, skill_key="audit")
        assert "recent_audits" in ctx and ctx["recent_audits"][0]["handle"] == "@rival"
        _, ctx2 = await ai_employees.build_context(s, tid, emp, None, crun, skill_key="strategy")
        assert "recent_audits" not in ctx2         # only the audit/trend/diagnose slice pulls it


async def test_pace_check_fires_when_behind_curve(monkeypatch):
    """With registrations far under the empirical curve, eval_pace_check returns a trigger_context."""
    async with SessionLocal() as s:
        tid = await _tid(s)
        biz = (await s.execute(select(Business).where(Business.tenant_id == tid).limit(1))).scalar_one()
        today = dt.date.today()
        launch = Launch(
            tenant_id=tid, business_id=biz.id, name="Pace Test", window_start=today - dt.timedelta(days=5),
            window_end=today + dt.timedelta(days=20), goal_arr=0, ticket_pif=0, ticket_plan=0,
            pipeline_match="n/a", stage_map={}, payment_plan_map={}, is_active=True,
            shift_name="The Shift", shift_event_date=today + dt.timedelta(days=6),
            shift_goal=2000, shift_pace_curve=DEFAULT_SHIFT_CURVE, seat_goal=100)
        s.add(launch)
        await s.commit()

        cond = {"type": "pace_check", "threshold_pct_under_curve": 10}
        tc = await ai_employees.eval_pace_check(s, tid, cond, today)
        assert tc is not None                                # registrants trail the dte=6 curve point
        assert tc["source"] == "GoHighLevel" and tc["shortfall_pct"] >= 10
        assert tc["expected"] > 0 and tc["facts"]

        # a threshold above the actual shortfall does not fire (debounce is separate, in dispatch)
        assert await ai_employees.eval_pace_check(
            s, tid, {"type": "pace_check", "threshold_pct_under_curve": tc["shortfall_pct"] + 1},
            today) is None
