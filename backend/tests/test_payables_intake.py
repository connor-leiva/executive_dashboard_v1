"""Where an AI proposal becomes a row — and every way it must not.

The design point: the clerk never creates anything. It writes an artifact, a person accepts it,
and THAT person is the actor. So `require_human` holds by construction rather than by an
exemption carved out for this path, and the tests below are about what the person's act is
allowed to turn into.
"""
import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (AIArtifact, AIEmployee, AIRun, AuditLog, Business, Payable, Tenant,
                        User, Vendor)
from app.seed import seed
from app.security import hash_pw
from app.services import payables as pay
from app.services import payables_intake as intake
from app.services import payables_vendor as pv
from app.services.payables_actor import NotAHumanError


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _ctx():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        u = (await s.execute(select(User).where(User.tenant_id == t.id))).scalars().first()
        return t.id, u


async def _vendor(tenant_id, actor, name):
    async with SessionLocal() as s:
        v = await pv.create_vendor(s, tenant_id, actor, {"legal_name": name, "terms_days": 30})
        return v["id"], v["display_name"]


async def _artifact(tenant_id, payload, kind="ap_bill", doc_id=None):
    """An approved ap_bill artifact on a real run, which is the only shape that gets this far."""
    async with SessionLocal() as s:
        emp = AIEmployee(tenant_id=tenant_id, name="AP Clerk", role_title="Accounts Payable",
                         config={"family": "ap"}, writeback_enabled=True)
        s.add(emp)
        await s.flush()
        run = AIRun(tenant_id=tenant_id, employee_id=emp.id, skill_key="ap_intake",
                    trigger="manual", status="awaiting_approval",
                    context={"document_id": doc_id} if doc_id else {})
        s.add(run)
        await s.flush()
        a = AIArtifact(tenant_id=tenant_id, run_id=run.id, kind=kind, lane="Payables",
                       title="Proposed bill", payload=payload, state="approved")
        s.add(a)
        await s.commit()
        return a.id


def _proposal(vendor_id, **over):
    p = {"kind": "ap_bill", "vendor": "Acme Landscaping LLC", "vendor_match": str(vendor_id),
         "vendor_match_reason": "exact legal name", "invoice_number": f"AI-{uuid.uuid4().hex[:6]}",
         "invoice_date": "2026-09-15", "due_date": None, "amount": 1250.0, "currency": "USD",
         "description": "Grounds maintenance", "suggested_account": "Repairs & Maintenance",
         "confidence": {"vendor": 0.96, "amount": 0.99, "invoice_number": 0.94},
         "unreadable": [], "note": "clean scan"}
    p.update(over)
    return p


# ── the happy path, and exactly how far it goes ───────────────────────────────────────────

async def test_an_accepted_proposal_lands_as_an_extracted_bill_and_goes_no_further():
    """One step: from a PDF nobody had opened to a row with a name on it. Not coded, not
    submitted, not approved — the next move is a person's, through the same screens they would
    use for a bill somebody typed."""
    tid, actor = await _ctx()
    vid, vname = await _vendor(tid, actor, "Acme Landscaping LLC")
    payload = _proposal(vid)
    aid = await _artifact(tid, payload)

    async with SessionLocal() as s:
        a = await s.get(AIArtifact, aid)
        bill = await intake.accept_proposal(s, tid, actor, a)
        await s.commit()

    assert bill["status"] == "extracted", bill["status"]
    assert bill["vendor"] == vname
    assert bill["amount"] == 1250.0
    assert bill["can_submit"] is False, "an extracted bill has no account yet and must not submit"
    # The due date came from the vendor's TERMS, because the document printed none.
    assert bill["due_date"] == (dt.date(2026, 9, 15) + dt.timedelta(days=30)).isoformat()
    # The model's own output is kept, including what it was unsure of.
    assert bill["extraction"]["proposed_by"] == "ap_clerk"
    assert bill["extraction"]["confidence"]["amount"] == 0.99
    assert bill["extraction"]["artifact_id"] == str(aid)

    async with SessionLocal() as s:
        rows = (await s.execute(select(AuditLog).where(
            AuditLog.tenant_id == tid, AuditLog.target_id == bill["id"],
            AuditLog.action == "payables.proposal_accepted"))).scalars().all()
    assert len(rows) == 1 and rows[0].category == "Payments"
    assert rows[0].actor_user_id == actor.id, "the accepting PERSON is the actor, not the clerk"


async def test_a_printed_due_date_is_kept_and_terms_are_not_applied_over_it():
    tid, actor = await _ctx()
    vid, _ = await _vendor(tid, actor, "Printed Due LLC")
    aid = await _artifact(tid, _proposal(vid, due_date="2026-10-01"))
    async with SessionLocal() as s:
        bill = await intake.accept_proposal(s, tid, actor, await s.get(AIArtifact, aid))
        await s.commit()
    assert bill["due_date"] == "2026-10-01"


# ── and every way it is refused ───────────────────────────────────────────────────────────

async def test_no_vendor_match_is_the_end_of_the_road():
    """The clerk does not create vendors. A vendor invented from the letterhead of whatever
    arrived in the inbox is the first half of every invoice-fraud story."""
    tid, actor = await _ctx()
    aid = await _artifact(tid, _proposal(uuid.uuid4(), vendor_match=None))

    # The DELTA, not the state. Asserting "no vendor named Acme exists" would be answered by a
    # vendor an earlier test created for its own reasons, which is how a test starts reporting
    # on its neighbours instead of on itself.
    async with SessionLocal() as s:
        before = len((await s.execute(select(Vendor).where(
            Vendor.tenant_id == tid))).scalars().all())
        with pytest.raises(intake.ProposalIncomplete) as e:
            await intake.accept_proposal(s, tid, actor, await s.get(AIArtifact, aid))
    assert "matched vendor" in str(e.value)
    async with SessionLocal() as s:
        after = len((await s.execute(select(Vendor).where(
            Vendor.tenant_id == tid))).scalars().all())
    assert after == before, "a vendor was created from a proposal"


async def test_a_vendor_from_another_workspace_is_refused():
    """A model can emit a plausible-looking id. It must not be able to attach a bill to somebody
    else's vendor by guessing one."""
    tid, actor = await _ctx()
    async with SessionLocal() as s:
        other = Tenant(slug=f"intake-other-{uuid.uuid4().hex[:6]}", name="Other")
        s.add(other)
        await s.flush()
        stranger = Vendor(tenant_id=other.id, legal_name="Stranger LLC",
                          display_name="Stranger LLC", name_norm="stranger llc")
        s.add(stranger)
        await s.commit()
        stranger_id = stranger.id

    aid = await _artifact(tid, _proposal(stranger_id))
    async with SessionLocal() as s:
        with pytest.raises(intake.ProposalIncomplete) as e:
            await intake.accept_proposal(s, tid, actor, await s.get(AIArtifact, aid))
    assert "not a vendor in this workspace" in str(e.value)


@pytest.mark.parametrize("over,wanted", [
    ({"invoice_number": ""}, "invoice number"),
    ({"invoice_number": "   "}, "invoice number"),
    ({"amount": None}, "an amount"),
])
async def test_an_incomplete_extraction_names_what_is_missing(over, wanted):
    tid, actor = await _ctx()
    vid, _ = await _vendor(tid, actor, f"Incomplete {uuid.uuid4().hex[:4]} LLC")
    aid = await _artifact(tid, _proposal(vid, **over))
    async with SessionLocal() as s:
        with pytest.raises(intake.ProposalIncomplete) as e:
            await intake.accept_proposal(s, tid, actor, await s.get(AIArtifact, aid))
    assert wanted in str(e.value)


async def test_another_kind_of_artifact_is_not_a_bill():
    tid, actor = await _ctx()
    aid = await _artifact(tid, {"kind": "ap_exceptions", "items": [], "note": "n"},
                          kind="ap_exceptions")
    async with SessionLocal() as s:
        with pytest.raises(intake.NotAProposal):
            await intake.accept_proposal(s, tid, actor, await s.get(AIArtifact, aid))


async def test_the_clerk_itself_cannot_accept_its_own_proposal():
    """The whole design in one assertion. There is no actor the AI can present that turns its
    own output into a bill — the acceptance is a person's act or it does not happen."""
    tid, actor = await _ctx()
    vid, _ = await _vendor(tid, actor, "Self Accept LLC")
    aid = await _artifact(tid, _proposal(vid))

    class _Clerk:
        id = None

    class _Machine:
        id = uuid.uuid4()
        actor_type = "system"

    for nobody in (None, _Clerk(), _Machine()):
        async with SessionLocal() as s:
            with pytest.raises(NotAHumanError):
                await intake.accept_proposal(s, tid, nobody, await s.get(AIArtifact, aid))

    async with SessionLocal() as s:
        made = (await s.execute(select(Payable).where(
            Payable.tenant_id == tid,
            Payable.invoice_number == _proposal(vid)["invoice_number"]))).scalars().all()
    assert not made
async def test_the_person_check_comes_before_anything_else():
    """Two locks hold this door — accept_proposal's own guard and create_payable's — and the
    test above passes on either, so it cannot say which. This one can.

    A non-human actor carrying a proposal that is ALSO incomplete must be refused for not being
    a person, not for the missing fields. That ordering is the difference between "we refuse
    machines" and "we happened to refuse this machine because its paperwork was wrong", and only
    the first survives a proposal that is complete.
    """
    tid, _actor = await _ctx()
    aid = await _artifact(tid, _proposal(uuid.uuid4(), vendor_match=None, amount=None,
                                         invoice_number=""))

    class _Machine:
        id = uuid.uuid4()
        actor_type = "system"

    for nobody in (None, _Machine()):
        async with SessionLocal() as s:
            with pytest.raises(NotAHumanError):      # NOT ProposalIncomplete
                await intake.accept_proposal(s, tid, nobody, await s.get(AIArtifact, aid))
