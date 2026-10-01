"""The batch "approve all" must not consume a proposal it cannot fulfil.

THE BUG THIS PINS SHUT. "Shipping" means two different things depending on the kind, and only
one of them is a state change. For a strategy memo or a reel script, `shipped` IS the whole act
— somebody sent it, and the row records that. For an `ap_bill`, shipping RUNS something:
`accept_proposal` creates a payable with a person's name against it.

`approve_run` implements the first meaning, by assignment:

    a.state, a.shipped_at = "shipped", eng._now()

which was correct for every kind that existed when it was written, and silently wrong for the
one added later. An `ap_bill` stamped here was marked done while creating nothing — and since
`ship_artifact` returns early on an artifact that is already `shipped`, that proposal could
never become a bill afterwards. One click, no error, and an invoice ceased to exist.

The fix is not to make the batch do the work either: "approve all" on a run carrying fifteen
proposals must not be fifteen bills. So these kinds are approved in the batch and shipped one at
a time, deliberately, through the route that actually runs the code.
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import (AIArtifact, AIEmployee, AIEmployeeSkill, AIRun, Payable, Tenant, User,
                        Vendor)
from app.seed import seed
from app.services import payables_vendor as pv
from app.services.ai_skills import KIND_META, SIDE_EFFECT_KINDS

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    prev = settings.AI_EMPLOYEES_ENABLED
    settings.AI_EMPLOYEES_ENABLED = True
    await seed()
    yield
    settings.AI_EMPLOYEES_ENABLED = prev


@pytest.fixture(autouse=True)
async def _clean_roster(monkeypatch):
    monkeypatch.setattr(settings, "AI_EMPLOYEES_WRITEBACK_ENABLED", True)
    async with SessionLocal() as s:
        for model in (AIArtifact, AIRun, AIEmployeeSkill, AIEmployee):
            await s.execute(delete(model))
        await s.commit()
    yield


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(t):
    return {"Authorization": f"Bearer {t}"}


async def _ctx():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        u = (await s.execute(select(User).where(User.tenant_id == t.id))).scalars().first()
        return t.id, u


async def _token():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


async def _clerk(tid, *, writeback=True):
    async with SessionLocal() as s:
        e = AIEmployee(tenant_id=tid, name="AP Clerk", role_title="Accounts Payable",
                       status="active", config={"family": "ap"}, writeback_enabled=writeback)
        s.add(e)
        await s.commit()
        return e.id


async def _vendor(tid, actor, name):
    async with SessionLocal() as s:
        v = await pv.create_vendor(s, tid, actor, {"legal_name": name, "terms_days": 30})
        return v["id"]


async def _run_with(tid, emp_id, *, kinds, state="draft", vendor_id=None, invoice=None):
    """A run carrying one artifact per kind, so a batch approve has something to be wrong about."""
    async with SessionLocal() as s:
        run = AIRun(tenant_id=tid, employee_id=emp_id, skill_key="ap_intake", trigger="document",
                    status="awaiting_approval", context={})
        s.add(run)
        await s.flush()
        ids = {}
        for kind in kinds:
            payload = ({"kind": "ap_bill", "vendor": "Acme", "vendor_match": str(vendor_id),
                        "vendor_match_reason": "exact", "invoice_number": invoice or
                        f"BATCH-{uuid.uuid4().hex[:6]}", "invoice_date": "2026-09-15",
                        "due_date": None, "amount": 900.0, "currency": "USD",
                        "description": "Grounds", "suggested_account": None,
                        "confidence": {"vendor": 0.9}, "unreadable": [], "note": ""}
                       if kind == "ap_bill" else {"kind": kind, "items": [], "note": "n"})
            a = AIArtifact(tenant_id=tid, run_id=run.id, kind=kind,
                           lane=KIND_META[kind]["lane"], title=kind, payload=payload, state=state)
            s.add(a)
            await s.flush()
            ids[kind] = str(a.id)
        await s.commit()
        return str(run.id), ids


# ══ the bug ═══════════════════════════════════════════════════════════════════════════════

async def test_approve_all_does_not_ship_a_proposal_into_nothing():
    """The whole finding in one test. Before the fix this left the artifact `shipped` with no
    payable anywhere, and the ship route then refused to touch it because it was already
    shipped — the proposal was gone."""
    tid, actor = await _ctx()
    emp = await _clerk(tid)
    vid = await _vendor(tid, actor, f"Acme Batch {uuid.uuid4().hex[:4]} LLC")
    run_id, ids = await _run_with(tid, emp, kinds=["ap_bill"], vendor_id=vid)
    tok = await _token()

    async with _client() as c:
        r = await c.post(f"/api/v1/ai/runs/{run_id}/approve", headers=_H(tok))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["approved"] == 1
    assert out["shipped"] == 0, "the batch shipped a kind whose shipping creates a row"
    assert len(out["held"]) == 1, "the screen is not told there is anything left to do"

    async with SessionLocal() as s:
        a = await s.get(AIArtifact, uuid.UUID(ids["ap_bill"]))
        assert a.state == "approved", a.state
        assert a.shipped_at is None
        # and nothing was created behind its back
        made = (await s.execute(select(Payable).where(Payable.tenant_id == tid))).scalars().all()
    assert not [p for p in made if (p.extraction or {}).get("proposed_by") == "ap_clerk"]


async def test_the_proposal_survives_the_batch_and_can_still_become_a_bill():
    """The point of not shipping it: the door is still open afterwards. A test that only
    asserted `state == "approved"` would pass on an artifact that was approved and then stuck."""
    tid, actor = await _ctx()
    emp = await _clerk(tid)
    vid = await _vendor(tid, actor, f"Survives {uuid.uuid4().hex[:4]} LLC")
    run_id, ids = await _run_with(tid, emp, kinds=["ap_bill"], vendor_id=vid)
    tok = await _token()

    async with _client() as c:
        await c.post(f"/api/v1/ai/runs/{run_id}/approve", headers=_H(tok))
        ship = await c.post(f"/api/v1/ai/artifacts/{ids['ap_bill']}/ship", headers=_H(tok))
    assert ship.status_code == 200, ship.text
    body = ship.json()
    assert body["payable"]["status"] == "extracted", body["payable"]["status"]
    assert body["payable"]["amount"] == 900.0
    async with SessionLocal() as s:
        a = await s.get(AIArtifact, uuid.UUID(ids["ap_bill"]))
        assert a.state == "shipped" and a.shipped_at is not None


async def test_the_other_kinds_still_ship_in_the_batch():
    """The fix must not turn "approve all" into "approve all and do nothing". Everything whose
    shipping IS the state change still ships in one click, which is what the button is for."""
    tid, actor = await _ctx()
    emp = await _clerk(tid)
    vid = await _vendor(tid, actor, f"Mixed {uuid.uuid4().hex[:4]} LLC")
    run_id, ids = await _run_with(tid, emp, kinds=["ap_exceptions", "ap_aging", "ap_bill"],
                                  vendor_id=vid)
    tok = await _token()
    async with _client() as c:
        r = await c.post(f"/api/v1/ai/runs/{run_id}/approve", headers=_H(tok))
    out = r.json()
    assert out["approved"] == 3
    assert out["shipped"] == 2, out
    assert [h["kind"] for h in out["held"]] == ["ap_bill"]
    # and a run with something still to do is not reported finished
    assert out["run_status"] == "approved", out["run_status"]

    async with SessionLocal() as s:
        states = {}
        for kind, aid in ids.items():
            states[kind] = (await s.get(AIArtifact, uuid.UUID(aid))).state
    assert states == {"ap_exceptions": "shipped", "ap_aging": "shipped", "ap_bill": "approved"}


async def test_a_run_of_only_shippable_kinds_still_reports_finished():
    tid, _actor = await _ctx()
    emp = await _clerk(tid)
    run_id, _ids = await _run_with(tid, emp, kinds=["ap_exceptions", "ap_aging"])
    tok = await _token()
    async with _client() as c:
        r = await c.post(f"/api/v1/ai/runs/{run_id}/approve", headers=_H(tok))
    assert r.json()["run_status"] == "shipped", r.json()


async def test_with_the_gate_shut_nothing_ships_and_the_proposal_is_untouched(monkeypatch):
    monkeypatch.setattr(settings, "AI_EMPLOYEES_WRITEBACK_ENABLED", False)
    tid, actor = await _ctx()
    emp = await _clerk(tid)
    vid = await _vendor(tid, actor, f"Shut {uuid.uuid4().hex[:4]} LLC")
    run_id, ids = await _run_with(tid, emp, kinds=["ap_exceptions", "ap_bill"], vendor_id=vid)
    tok = await _token()
    async with _client() as c:
        r = await c.post(f"/api/v1/ai/runs/{run_id}/approve", headers=_H(tok))
        out = r.json()
        assert out["shipped"] == 0 and out["writeback_open"] is False
        # the proposal is approved-and-waiting either way, and says so
        assert [h["kind"] for h in out["held"]] == ["ap_bill"]
        ship = await c.post(f"/api/v1/ai/artifacts/{ids['ap_bill']}/ship", headers=_H(tok))
    assert ship.status_code == 409 and "Writeback is disabled" in ship.json()["detail"]


# ══ the shape of the rule, not the instance ═══════════════════════════════════════════════

async def test_every_side_effect_kind_is_a_real_kind_and_is_held_back():
    """Derived from the set rather than from a list of one. A second kind added to
    SIDE_EFFECT_KINDS is covered here the day it is added, instead of being discovered when a
    batch approve eats one."""
    assert SIDE_EFFECT_KINDS, "the set is empty — ap_bill should be in it"
    assert SIDE_EFFECT_KINDS <= set(KIND_META), sorted(SIDE_EFFECT_KINDS - set(KIND_META))

    tid, actor = await _ctx()
    emp = await _clerk(tid)
    vid = await _vendor(tid, actor, f"Every {uuid.uuid4().hex[:4]} LLC")
    tok = await _token()
    for kind in sorted(SIDE_EFFECT_KINDS):
        run_id, ids = await _run_with(tid, emp, kinds=[kind], vendor_id=vid)
        async with _client() as c:
            out = (await c.post(f"/api/v1/ai/runs/{run_id}/approve", headers=_H(tok))).json()
        assert out["shipped"] == 0, f"{kind} was batch-shipped"
        async with SessionLocal() as s:
            assert (await s.get(AIArtifact, uuid.UUID(ids[kind]))).state == "approved", kind


async def test_the_only_code_that_creates_a_bill_is_the_single_ship_route():
    """Structural. The batch route must not grow its own call to accept_proposal later — that
    would be fifteen bills in one click, which is the other way to get this wrong."""
    import ast
    import inspect
    import textwrap

    from app.routers import ai_employees as router_mod

    def _calls(fn) -> set:
        """What this function can CALL. Parsed, not grepped — the batch route's docstring
        explains the bug by name, and a substring check reads that as the bug itself."""
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        return {getattr(n.func, "attr", None) or getattr(n.func, "id", None)
                for n in ast.walk(tree) if isinstance(n, ast.Call)}

    assert "accept_proposal" not in _calls(router_mod.approve_run), "the batch route makes bills"
    assert "accept_proposal" in _calls(router_mod.ship_artifact)
    # and it still consults the set rather than hard-coding a kind
    batch_src = textwrap.dedent(inspect.getsource(router_mod.approve_run))
    body = ast.get_source_segment(batch_src, ast.parse(batch_src).body[0]) or batch_src
    names = {n.id for n in ast.walk(ast.parse(batch_src)) if isinstance(n, ast.Name)}
    assert "SIDE_EFFECT_KINDS" in names, "the batch route no longer consults the set"
