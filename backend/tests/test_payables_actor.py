"""Nothing without a name moves money.

Every mutating function in Payables takes an `actor` and every one of them assumed it was a
person; nothing checked. It was never reachable, because every caller happened to pass a User —
the control was a habit of the call sites rather than a property of the code. Phase 4 puts a
non-human caller in this codebase, which is what turns "never reachable" into "reachable the
moment somebody wires it up".

The coverage test below is DERIVED from the modules rather than listing the writers by hand,
because a hand-kept list never contains the next one. A new mutating function added to Payables
without the guard fails here, not in production.
"""
import ast
import datetime as dt
import inspect
import pathlib
import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Payable, PayableApproval, Tenant, User
from app.seed import seed
from app.services import payables as pay
from app.services import payables_run as run
from app.services import payables_vendor as pv
from app.services.payables_actor import NotAHumanError, require_human


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


# ── the rule itself ───────────────────────────────────────────────────────────────────────

class _Nobody:
    """What an un-wired machine caller looks like: an object with no id."""
    id = None


class _Machine:
    """And what an honest one looks like — it says so, and is refused on its own word."""
    id = uuid.uuid4()
    actor_type = "system"


class _Person:
    id = uuid.uuid4()


@pytest.mark.parametrize("actor", [None, _Nobody(), _Machine()], ids=["none", "nameless", "system"])
def test_a_nameless_actor_is_refused(actor):
    with pytest.raises(NotAHumanError) as e:
        require_human(actor, "releasing a payment run")
    assert "releasing a payment run" in str(e.value)     # says which act, not just "denied"


def test_a_person_passes():
    require_human(_Person(), "releasing a payment run")   # does not raise


# ── coverage, derived rather than listed ──────────────────────────────────────────────────

_MUTATORS = {
    "payables": {"create_payable", "update_coding", "submit_for_approval", "decide",
                 "replace_policies"},
    "payables_run": {"create_run", "hold_line", "override_line", "release_run", "reconcile_run"},
    "payables_vendor": {"create_vendor", "update_vendor", "add_bank_account"},
}


def _guarded(module) -> set:
    """Functions whose FIRST executable statement is the guard. Parsed rather than grepped: a
    require_human buried further down is a window in which the work has already begun."""
    tree = ast.parse(pathlib.Path(inspect.getsourcefile(module)).read_text(encoding="utf-8"))
    out = set()
    for node in tree.body:
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        body = [n for n in node.body
                if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
                and getattr(first.value.func, "id", None) == "require_human"):
            out.add(node.name)
    return out


@pytest.mark.parametrize("modname,module", [("payables", pay), ("payables_run", run),
                                            ("payables_vendor", pv)])
def test_every_mutating_writer_checks_for_a_person_first(modname, module):
    missing = _MUTATORS[modname] - _guarded(module)
    assert not missing, (
        f"{modname}: {sorted(missing)} move money or change who may be paid, and do not check "
        f"that a person asked. An AI employee reaching one of these is the failure this guards.")


def test_the_expected_writers_still_exist():
    """The list above is only worth what its names are worth. A writer renamed away would make
    the coverage test pass by asking about nothing."""
    for modname, names in _MUTATORS.items():
        module = {"payables": pay, "payables_run": run, "payables_vendor": pv}[modname]
        for n in names:
            assert callable(getattr(module, n, None)), f"{modname}.{n} has gone"


# ── and the specific inversion that made this worth doing ─────────────────────────────────

async def test_a_nameless_actor_cannot_claim_an_approval_slot():
    """The one that was genuinely alarming. With actor=None, `decide` took actor_id None and the
    very first lookup — approver_user_id == actor_id and decision is None — matched an UNASSIGNED
    slot. It claimed it, left approver_user_id NULL, and moved the bill to approved: a payment
    approved with nobody's name against it, and an audit row naming no one.
    """
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        actor = (await s.execute(select(User).where(User.tenant_id == t.id))).scalars().first()
        tid = t.id

    async with SessionLocal() as s:
        if not await pay.list_policies(s, tid):
            await pay.replace_policies(s, tid, actor, [
                {"label": "Any amount", "min_amount": 0, "max_amount": None}])
        v = await pv.create_vendor(s, tid, actor, {"legal_name": "Nameless Test LLC"})
        v = await pv.update_vendor(s, tid, actor, v["id"], {"w9_document_id": uuid.uuid4()})
        v = await pv.add_bank_account(s, tid, actor, v["id"], {
            "routing_last4": "1111", "account_last4": "2222", "verified": True})
        p = await pay.create_payable(s, tid, actor, {
            "vendor_id": uuid.UUID(v["id"]), "invoice_number": f"NA-{uuid.uuid4().hex[:6]}",
            "amount": 500, "due_date": dt.date.today() + dt.timedelta(days=5),
            "standard_account_id": uuid.uuid4()})
        pid = uuid.UUID(p["id"])
        await pay.submit_for_approval(s, tid, actor, pid)

    for nobody in (None, _Nobody(), _Machine()):
        async with SessionLocal() as s:
            with pytest.raises(NotAHumanError):
                await pay.decide(s, tid, nobody, pid, "approve")

    async with SessionLocal() as s:
        row = await s.get(Payable, pid)
        slots = (await s.execute(select(PayableApproval).where(
            PayableApproval.payable_id == pid))).scalars().all()
    assert row.status == "awaiting_approval", "a nameless actor approved a bill"
    assert all(a.decision is None for a in slots)
    assert all(a.approver_user_id is None for a in slots)   # and claimed nothing on the way out
