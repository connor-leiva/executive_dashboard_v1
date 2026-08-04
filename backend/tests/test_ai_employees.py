"""AI Employees — Step 1 (models + skill seed + demo fixture).

Later steps add worker / service / router / lifecycle tests here.
"""
import pytest
from sqlalchemy import select

from app.seed import seed
from app.db import SessionLocal
from app.models import Tenant, AISkill, AIEmployee, AIRun, AIArtifact, AIEmployeeSkill
from app.services.ai_skills import SKILL_KEYS


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
