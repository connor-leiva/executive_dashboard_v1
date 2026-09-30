"""What the AP Clerk is allowed to know, and what it is never handed.

Two properties carry the weight here. The clerk's context is built from READS only, so there is
no path from a prompt to a bill being coded, approved or paid. And it never silently truncates:
the prompt filler cuts the serialised context at 12000 characters, so an uncapped list means the
model is handed JSON that stops mid-object — it will answer anyway, and the answer will look
like an answer.
"""
import ast
import datetime as dt
import inspect
import json
import pathlib
import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Business, Tenant, User
from app.seed import seed
from app.services import ai_context_ap
from app.services import payables as pay
from app.services import payables_vendor as pv
from app.services.ai_context_ap import AP_SKILLS, build_ap_context


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _ctx():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        u = (await s.execute(select(User).where(User.tenant_id == t.id))).scalars().first()
        b = (await s.execute(select(Business).where(
            Business.tenant_id == t.id, Business.key == "ulrg"))).scalar_one()
        return t.id, u, b.id


# ── it only reads ─────────────────────────────────────────────────────────────────────────

_WRITERS = {"create_payable", "update_coding", "submit_for_approval", "decide",
            "replace_policies", "create_run", "hold_line", "override_line", "release_run",
            "reconcile_run", "create_vendor", "update_vendor", "add_bank_account"}


def test_the_clerks_context_never_calls_a_writer():
    """Structural, not behavioural, and deliberately so: a test that merely ran the builders and
    checked nothing changed would pass for a writer guarded behind a condition that happened not
    to fire. This asks what the module can CALL AT ALL.

    require_human already refuses a machine at each of those writers, so this is the second lock
    rather than the only one — but the second lock is what makes the first one's failure
    survivable.
    """
    tree = ast.parse(pathlib.Path(inspect.getsourcefile(ai_context_ap)).read_text(encoding="utf-8"))
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            called.add(getattr(f, "attr", None) or getattr(f, "id", None))
    leaked = _WRITERS & called
    assert not leaked, f"the AP context can reach {sorted(leaked)} — it is supposed to only read"


def test_every_ap_skill_has_a_context_path():
    """Derived from the skill catalog, so a sixth AP skill added without a context slice fails
    here rather than shipping a clerk that is handed nothing but the date."""
    from app.services.ai_skills import skills_for, AP
    assert {sk["key"] for sk in skills_for(AP)} == AP_SKILLS


# ── each skill gets what it needs ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("skill,expected", [
    ("ap_intake", {"vendors", "accounts", "invoice_numbers_on_file"}),
    ("ap_exception_scan", {"open_bills", "vendors", "bank_cooldown_hours", "approval_bands"}),
    ("ap_aging_digest", {"open_bills", "vendors"}),
    ("ap_run_prep", {"proposed_run", "lookahead_days"}),
    ("ap_1099_check", {"vendors", "threshold", "paid_ytd"}),
])
async def test_the_slice_carries_what_that_skill_reasons_from(skill, expected):
    tid, _actor, _biz = await _ctx()
    async with SessionLocal() as s:
        ctx = await build_ap_context(s, tid, skill)
    assert expected <= set(ctx), f"{skill} is missing {expected - set(ctx)}"
    assert ctx["today"]                       # every slice is dated, or "overdue" means nothing


async def test_no_slice_carries_the_social_context():
    """The marketing slice is not merely irrelevant to a clerk — it is spent out of the same
    12000 characters the bills have to fit into."""
    tid, _actor, _biz = await _ctx()
    async with SessionLocal() as s:
        for skill in sorted(AP_SKILLS):
            ctx = await build_ap_context(s, tid, skill)
            assert not ({"roster", "media", "recent_audits", "pacing"} & set(ctx)), skill


# ── it fits, and it says when it did not ──────────────────────────────────────────────────

async def test_every_slice_fits_the_prompt_budget():
    """_fill_prompt truncates at 12000 characters. Past that the model is handed JSON that stops
    mid-object, and it will answer anyway."""
    tid, _actor, _biz = await _ctx()
    async with SessionLocal() as s:
        for skill in sorted(AP_SKILLS):
            ctx = await build_ap_context(s, tid, skill)
            size = len(json.dumps(ctx, default=str, ensure_ascii=False))
            assert size < 12000, f"{skill} serialises to {size} chars and would be cut mid-object"


async def test_a_long_list_is_capped_and_says_so():
    """Silence is the failure mode: a clerk told "50 of 312, oldest first" can caveat its
    summary, one handed a severed brace cannot."""
    tid, actor, biz = await _ctx()
    over = 80          # comfortably past what the vendor budget can hold
    async with SessionLocal() as s:
        have = len(await pv.list_vendors(s, tid))
        for i in range(max(0, over - have)):
            await pv.create_vendor(s, tid, actor, {"legal_name": f"Cap Test {i:03d} LLC"})
    async with SessionLocal() as s:
        ctx = await build_ap_context(s, tid, "ap_intake")
    assert len(ctx["vendors"]) < 80, "nothing was dropped, so nothing was budgeted"
    assert "truncated" in ctx, "the slice was cut and did not say so"
    assert any("vendors" in n for n in ctx["truncated"])
    # and it still fits, which is the point of capping in the first place
    assert len(json.dumps(ctx, default=str)) < 12000
