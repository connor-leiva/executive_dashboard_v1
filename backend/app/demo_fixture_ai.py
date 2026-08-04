"""AI Employees — DEMO FIXTURE (SPEC-ai-employees-tab §8).

============================ DEMO DATA — DO NOT AUTO-LOAD ============================
This is the ONLY file where "Summer" / "The Shift" / "Spring" may appear (acceptance
§10). It creates a Social-Media-Manager employee named Summer plus one historical
`shipped` run carrying the mockup's six artifacts, for DEMO tenants only. It is never
called from seed() or the app lifespan — load it explicitly:

    python -m app.demo_fixture_ai [tenant_slug]     # default: springb

The run's artifact payloads are the mockup's OUTPUTS[n].preview objects verbatim (demo
content). Real tenants create their own employees; nothing here is product behavior.
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select, delete

from .db import SessionLocal
from .models import Tenant, AIEmployee, AIEmployeeSkill, AIRun, AIArtifact
from .services.ai_skills import SKILLS, KIND_META

_EVENT = {"source": "GoHighLevel", "label": "Daily pace check, 6:00 AM",
          "title": "The launch is trailing the registration curve",
          "facts": ["Day 4 of 8 to webinar", "512 of 2,000", "14% under curve"]}

_READS = [
    "512 registered, but curve-adjusted we should be near 600 by now",
    "@thelaunchcoach is winning on reflection-hook Reels, not talking-head",
    "Our one carousel outperformed everything this week, and we've barely shipped any",
]

# (kind, title, payload) — payloads are the mockup OUTPUTS[n].preview objects.
_ARTIFACTS = [
    ("audit", "Audited @thelaunchcoach's launch, here's what's converting", {
        "kind": "audit", "handle": "@thelaunchcoach",
        "top": [{"name": "Reflection-hook Reel", "val": "4.2x", "w": "100%"},
                {"name": "'5 signs' carousel", "val": "3.1x", "w": "74%"},
                {"name": "Founder GRWM Story", "val": "2.4x", "w": "57%"}],
        "mechanics": ["Hooks name a hidden problem, not the offer", "Carousels teach, Reels convict",
                      "Posts every day in the final week"],
        "note": "Pulled from her last 3 weeks. Nothing is copied, the mechanics shape the brand's own posts."}),
    ("trend", "Scanned the roster, filed this week's trend brief", {
        "kind": "trend", "scanned": "12 accounts · 3 in-launch",
        "patterns": [{"p": "Reflection hooks beat question hooks", "d": "3.1x"},
                     {"p": "Carousels are resurging across the niche", "d": "2.2x"},
                     {"p": "Final-week cadence is daily, everywhere", "d": "7/7"}],
        "timely": ["Rate-cut chatter is peaking in her audience", "AI-employee content is breaking out of tech circles"],
        "change": "Reframe the Day 6 proof carousel around the rate conversation, and add one build-in-public AI post.",
        "note": "Filed to Competitor Intel. Every brief ends with the change."}),
    ("strategy", "Re-architected the next 4 days around what's working", {
        "kind": "strategy", "title": "Strategy pivot, Day 4 to 8",
        "shift": "From talking-head and static posts to reflection-hook Reels and teaching carousels, every day.",
        "plan": [{"day": "Day 4", "what": "Reflection carousel (this one)", "cta": "Register"},
                 {"day": "Day 5", "what": "Reel, reflection hook", "cta": "Register"},
                 {"day": "Day 6", "what": "Proof carousel, the numbers", "cta": "Urgency"},
                 {"day": "Day 7", "what": "Founder Story sequence", "cta": "Last call"}],
        "why": "The curve says we need about 120 registrations a day now. The audit says reflection hooks and carousels move this audience."}),
    ("design", "Designed a 5-slide carousel in the brand", {
        "kind": "design",
        "slides": [{"bg": "#002E2C", "fg": "#F6F0E9", "h": "Are you building a business, or running on autopilot?"},
                   {"bg": "#F6F0E9", "fg": "#002E2C", "h": "1,000 women just chose differently."},
                   {"bg": "#61835E", "fg": "#F6F0E9", "h": "The launch is 4 days out.", "sub": "512 registered, on the way to 2,000"},
                   {"bg": "#FFC6AF", "fg": "#5A2E22", "h": "This is the room where it changes."},
                   {"bg": "#FFDD1F", "fg": "#3A2E00", "h": "Save your seat.", "sub": "Link in bio"}],
        "caption": "The hook is lifted from the audit, then rewritten in the brand's voice. Drafted, not posted.",
        "note": "Drafted in the brand. Nothing posts until you approve."}),
    ("script", "Scripted a reflection-hook Reel with shotlist", {
        "kind": "script", "hookType": "reflection hook", "hook": "You're not behind. You're on autopilot.",
        "shots": [{"vis": "To camera, natural light, no set", "vo": "You're not behind. You're on autopilot."},
                  {"vis": "B-roll: hands closing a laptop", "vo": "Busy is not the same as building."},
                  {"vis": "Text on screen: 1,000 women. 4 days left.", "vo": "There is another way, and the room is filling."},
                  {"vis": "Back to camera, warm", "vo": "Comment SHIFT and I'll send you the link."}]}),
    ("measure", "UTM-tagged every asset so registrations trace back", {
        "kind": "measure",
        "tags": [{"asset": "Carousel", "utm": "theshift / ig-carousel-reflection-d4"},
                 {"asset": "Reel", "utm": "theshift / ig-reel-hook-d5"}],
        "note": "GHL's source field can't tell channels apart, so every asset is tagged."}),
]


async def load_demo_ai(s, tenant_id) -> AIEmployee:
    """Create (or replace) the demo Summer employee + one shipped run for a tenant."""
    old = (await s.execute(select(AIEmployee).where(
        AIEmployee.tenant_id == tenant_id, AIEmployee.name == "Summer"))).scalars().all()
    for e in old:                                        # idempotent: clear a prior demo load
        run_ids = [r.id for r in (await s.execute(select(AIRun).where(AIRun.employee_id == e.id))).scalars()]
        if run_ids:
            await s.execute(delete(AIArtifact).where(AIArtifact.run_id.in_(run_ids)))
        await s.execute(delete(AIRun).where(AIRun.employee_id == e.id))
        await s.execute(delete(AIEmployeeSkill).where(AIEmployeeSkill.employee_id == e.id))
        await s.execute(delete(AIEmployee).where(AIEmployee.id == e.id))

    emp = AIEmployee(tenant_id=tenant_id, name="Summer", role_title="Social Media Manager",
                     avatar_color="#227175", status="active", writeback_enabled=False,
                     config={"timezone": "America/Denver"})
    s.add(emp)
    await s.flush()
    for sk in SKILLS:
        s.add(AIEmployeeSkill(tenant_id=tenant_id, employee_id=emp.id, skill_key=sk["key"], enabled=True))

    run = AIRun(tenant_id=tenant_id, employee_id=emp.id, skill_key="pace_response", trigger="condition",
                status="shipped", trigger_context=_EVENT, reads=_READS,
                summary="Approved 6 items · audit, trend, strategy, carousel, reel, UTM tags.")
    s.add(run)
    await s.flush()
    for kind, title, payload in _ARTIFACTS:
        meta = KIND_META[kind]
        s.add(AIArtifact(tenant_id=tenant_id, run_id=run.id, kind=kind, lane=meta["lane"],
                         title=title, dest_label=meta["dest_label"], payload=payload, state="shipped"))
    await s.commit()
    return emp


async def _main(slug: str):
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
        if not t:
            print(f"[demo_fixture_ai] no tenant '{slug}'"); return
        await load_demo_ai(s, t.id)
        print(f"[demo_fixture_ai] loaded Summer + 1 shipped run for tenant '{slug}' (DEMO DATA)")


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1] if len(sys.argv) > 1 else "springb"))
