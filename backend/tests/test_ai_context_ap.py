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
# ── the bill itself ───────────────────────────────────────────────────────────────────────

async def test_ap_intake_is_handed_the_document_and_everything_else_is_not():
    """A bill arrives as a PDF, not as text in a context slice. The seam carries it as a content
    block ahead of the prompt — material first, then what to do with it.

    The conditional matters as much as the block: five existing test doubles take
    (client, model, system, user) positionally with no **kwargs, so a `blocks=` passed
    unconditionally would break every one of them. It is forwarded only when there is something
    to send.
    """
    from app.services import ai_employees
    from app.services.ai_skills import skills_for, AP

    seen = {}

    async def old_style(client, model, system, user):        # the shape every double uses today
        seen["called"] = True
        return '{"reads":[],"summary":"s","artifacts":[{"title":"t","payload":{"kind":"ap_exceptions","items":[],"note":"n"}}]}', 1, 1

    scan = next(sk for sk in skills_for(AP) if sk["key"] == "ap_exception_scan")
    ai_employees._claude_call, real = old_style, ai_employees._claude_call
    try:
        out, _i, _o, err = await ai_employees.call_skill(scan, "prompt")
    finally:
        ai_employees._claude_call = real
    assert err is None and seen.get("called"), "a no-document skill broke the old-style double"

    # and with blocks, the seam passes them through in front of the text
    captured = {}

    async def block_aware(client, model, system, user, *, blocks=None):
        captured["blocks"] = blocks
        return '{"reads":[],"summary":"s","artifacts":[{"title":"t","payload":{"kind":"ap_exceptions","items":[],"note":"n"}}]}', 1, 1

    ai_employees._claude_call, real = block_aware, ai_employees._claude_call
    try:
        await ai_employees.call_skill(scan, "prompt",
                                      blocks=[{"type": "document", "source": {"data": "x"}}])
    finally:
        ai_employees._claude_call = real
    assert captured["blocks"], "the document never reached the seam"


async def test_a_missing_document_is_a_run_that_says_so_not_a_crash():
    """None is a legitimate answer. ap_intake then has vendors and a chart and no bill, and its
    prompt tells it to say so — which is a better outcome than a run that dies because an
    attachment went missing."""
    tid, _actor, _biz = await _ctx()

    class _Run:
        context = {"document_id": str(uuid.uuid4())}         # a real-looking id for nothing

    async with SessionLocal() as s:
        assert await ai_context_ap.document_blocks(s, tid, _Run()) is None

    class _NoDoc:
        context = {}

    async with SessionLocal() as s:
        assert await ai_context_ap.document_blocks(s, tid, _NoDoc()) is None
async def test_the_seam_puts_the_document_ahead_of_the_prompt():
    """The test above stops at the seam: it replaces _claude_call wholesale, so it proves the
    blocks REACH it and nothing about what it does with them. Deleting the blocks from the
    message body left that test green, which is exactly the hole this closes.

    So this one drives the REAL _claude_call with a fake client and reads the message it built.
    Order matters: material first, instruction second — a model told what to do before it is
    shown the thing answers from the instruction.
    """
    from app.services import ai_employees

    built = {}

    class _Messages:
        async def create(self, **kw):
            built.update(kw)

            class _R:
                content = [type("B", (), {"type": "text", "text": "{}"})()]
                usage = type("U", (), {"input_tokens": 1, "output_tokens": 1})()
            return _R()

    class _Client:
        messages = _Messages()

    doc = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                          "data": "JVBERi0="}}
    await ai_employees._claude_call(_Client(), "m", "sys", "read this bill", blocks=[doc])
    content = built["messages"][0]["content"]
    assert [c["type"] for c in content] == ["document", "text"], content
    assert content[0] == doc
    assert content[1]["text"] == "read this bill"

    # and with no document the message is exactly what it always was
    built.clear()
    await ai_employees._claude_call(_Client(), "m", "sys", "just text")
    assert [c["type"] for c in built["messages"][0]["content"]] == ["text"]
