"""The front door of the AP pipeline: an invoice arriving, and everything it must not become.

Two things are being tested and they pull in opposite directions.

The channel is PUBLIC. Anyone who can email a workspace's AP address can put a PDF in front of
its clerk, and that is the point — you cannot ask suppliers for a credential. So most of what
follows is about what an arriving message is NOT allowed to decide: which workspace it lands in,
whether a vendor exists, whether a bill exists, and what the Binder shows.

And the documents are Payables', not the Binder's. The Binder is the legal record of the
entities, read behind a second factor; a month of supplier invoices filed among somebody's
formation papers buries them, and a clerk that can be handed a Binder document by id is a way
around the code somebody typed to read a lease.
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import (AIEmployee, AIEmployeeSkill, AIRun, AuditLog, BinderDocument, Tenant,
                        User)
from app.seed import seed
from app.security import hash_pw
from app.services import binder_ingest, payables_intake as intake
from app.services.ai_skills import AP
from .conftest import binder_headers

TRANSPORT = ASGITransport(app=app)
SECRET = "payables-ingest-secret"


@pytest.fixture(scope="module", autouse=True)
async def _seeded(tmp_path_factory):
    settings.BINDER_STORAGE_BUCKET = str(tmp_path_factory.mktemp("pay_store"))
    await seed()


@pytest.fixture(autouse=True)
def _channel(monkeypatch):
    """The platform half of the channel, open. Every test that wants it closed says so."""
    monkeypatch.setattr(settings, "PAYABLES_INGEST_SECRET", SECRET)
    monkeypatch.setattr(settings, "PAYABLES_INGEST_DOMAIN", "")      # -> PLATFORM_DOMAIN


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


async def _tid():
    async with SessionLocal() as s:
        return (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id


async def _token(email="spring@springb.com", password="springtime"):
    async with _client() as c:
        r = await c.post("/api/v1/auth/login", json={"email": email, "password": password})
    return r.json()["token"]


async def _open_channel(local="ap", enabled=True):
    async with SessionLocal() as s:
        t = await s.get(Tenant, await _tid())
        intake.set_email_settings(t, enabled=enabled, local_part=local)
        await s.commit()
        return intake.email_settings(t)


async def _close_channel():
    async with SessionLocal() as s:
        t = await s.get(Tenant, await _tid())
        cfg = dict(t.config or {})
        cfg.pop(intake.CFG_ENABLED, None)
        t.config = cfg
        await s.commit()


def _pdf(name="invoice.pdf", body=None):
    return ("files", (name, body or uuid.uuid4().bytes + b"%PDF-1.4 invoice", "application/pdf"))


async def _post(files=None, data=None, secret=SECRET):
    async with _client() as c:
        return await c.post("/api/v1/payables/ingest/email", data=data or {},
                            files=files or [_pdf()],
                            headers={"X-Ingest-Secret": secret})


# ══ who is allowed to reach the channel at all ═════════════════════════════════════════════

async def test_no_platform_secret_means_the_channel_does_not_exist(monkeypatch):
    monkeypatch.setattr(settings, "PAYABLES_INGEST_SECRET", "")
    await _open_channel()
    r = await _post(data={"recipient": "ap@springb.axcion.io"}, secret="anything")
    assert r.status_code == 404, r.text


async def test_a_wrong_secret_is_404_not_403():
    """403 would confirm the route exists and the workspace does. 404 is the same answer as a
    mailbox that was never there."""
    await _open_channel()
    r = await _post(data={"recipient": "ap@springb.axcion.io"}, secret="wrong")
    assert r.status_code == 404


async def test_a_workspace_that_never_asked_for_an_address_cannot_be_posted_to():
    """The secret authenticates the PROVIDER, not the sender. One platform-wide secret plus a
    caller-named workspace would let anyone who can reach the channel push documents into any
    workspace at all, so the channel is opt-in and fails closed."""
    await _close_channel()
    r = await _post(data={"recipient": "ap@springb.axcion.io"})
    assert r.status_code == 404, r.text


async def test_the_local_part_has_to_be_the_one_the_workspace_configured():
    """This is what "configurable" buys. The host is public — it is the workspace's own slug — so
    the local part is the only part of the address that can be made hard to guess, and a
    workspace that wants that can have it."""
    await _open_channel(local="ap-7f3k")
    assert (await _post(data={"recipient": "ap@springb.axcion.io"})).status_code == 404
    ok = await _post(data={"recipient": "AP-7F3K@springb.axcion.io"})    # case-insensitive
    assert ok.status_code == 200, ok.text
    tagged = await _post(data={"recipient": "ap-7f3k+bills@springb.axcion.io"})
    assert tagged.status_code == 200, "a plus tag is delivered to the same mailbox"


async def test_a_workspaces_own_group_address_does_not_resolve_as_ours():
    """ap@springb.com is the address a workspace FORWARDS FROM, and it is in the To: header of
    every message it forwards. Matching the first label of the host would have accepted it as if
    it were ours, which is how the sender ends up choosing the workspace."""
    await _open_channel()
    for addr in ("ap@springb.com", "ap@springb.evil.example", "ap@springb.axcion.io.evil.example"):
        r = await _post(data={"to": addr})
        assert r.status_code == 404, f"{addr} resolved"


# ══ which workspace an arriving message belongs to ════════════════════════════════════════

async def test_the_envelope_beats_the_to_header():
    """The To: header is written by the SENDER, and this route resolves a workspace from the
    recipient. So when the provider tells us where it actually delivered the message, that is the
    only field read — otherwise anyone able to reach the channel could name the workspace their
    attachment lands in by typing a different To:.

    Also the ordinary case: a forwarded message still carries the original To:, so the header
    would name the workspace's own group address rather than where it arrived.
    """
    await _open_channel()
    body = b"%PDF-1.4 envelope wins " + uuid.uuid4().bytes
    r = await _post(files=[_pdf("env.pdf", body)],
                    data={"envelope": '{"to":["ap@springb.axcion.io"],"from":"x@y.com"}',
                          "to": "ap@someone-else.axcion.io"})
    assert r.status_code == 200, r.text
    # and it landed in springb
    async with SessionLocal() as s:
        doc = (await s.execute(select(BinderDocument).where(
            BinderDocument.content_hash == binder_ingest.binder_storage.content_hash(body)
        ))).scalar_one()
    assert doc.tenant_id == await _tid()


async def test_the_header_is_the_last_resort_not_a_peer():
    """Structural: with an envelope present the header is not consulted, whatever it says. A
    behavioural test could pass because the header happened to name nothing that resolves."""
    got = intake.candidate_recipients(envelope='{"to":["a@x.axcion.io"]}', to="b@y.axcion.io")
    assert got == ["a@x.axcion.io"]
    got = intake.candidate_recipients(recipient="a@x.axcion.io", to="b@y.axcion.io")
    assert got == ["a@x.axcion.io"]
    assert intake.candidate_recipients(to="b@y.axcion.io") == ["b@y.axcion.io"]


async def test_a_non_ascii_local_part_is_refused_and_does_not_crash():
    """compare_digest raises TypeError on a non-ASCII str, and the local part arrives off the
    wire — so this is the difference between a 404 and a 500 with a traceback in the logs."""
    await _open_channel()
    r = await _post(data={"recipient": "áp@springb.axcion.io"})
    assert r.status_code == 404, r.text


# ══ what arrives, and what it is allowed to be ════════════════════════════════════════════

async def test_a_forwarded_invoice_is_filed_as_a_payables_document_with_its_sender_recorded():
    await _open_channel()
    body = b"%PDF-1.4 real invoice " + uuid.uuid4().bytes
    r = await _post(files=[_pdf("acme-sept.pdf", body)],
                    data={"recipient": "ap@springb.axcion.io", "sender": "billing@acme.example"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["documents"]) == 1 and out["skipped"] == 0
    doc_id = out["documents"][0]["document_id"]

    async with SessionLocal() as s:
        doc = await s.get(BinderDocument, uuid.UUID(doc_id))
        assert doc.category == "payable", "filed where the Binder would show it"
        assert doc.uploaded_via == "email"
        assert doc.uploaded_by is None, "no user put this here"
        rows = (await s.execute(select(AuditLog).where(
            AuditLog.action == "payables.email_ingest",
            AuditLog.tenant_id == doc.tenant_id))).scalars().all()
    assert rows, "an invoice with no provenance is one nobody can ask a question about"
    last = rows[-1]
    assert last.detail["from"] == "billing@acme.example"
    assert last.category == "Payments"
    # A machine, not a person. audit_log carries a CHECK enumerating user|system|integration, and
    # filing this as a user puts an inbound email in the trail under somebody's name.
    assert last.actor_type == "integration"


async def test_the_same_bytes_twice_produce_one_document_and_one_reading():
    """A provider retry, or two people forwarding the same invoice. Two readings become two
    proposals and then two bills for one invoice."""
    await _open_channel()
    body = b"%PDF-1.4 duplicate " + uuid.uuid4().bytes
    first = await _post(files=[_pdf("dup.pdf", body)], data={"recipient": "ap@springb.axcion.io"})
    second = await _post(files=[_pdf("dup.pdf", body)], data={"recipient": "ap@springb.axcion.io"})
    assert first.status_code == second.status_code == 200
    a, b = first.json()["documents"][0], second.json()["documents"][0]
    assert a["document_id"] == b["document_id"]
    assert b["deduped"] is True and b["intake"]["queued"] is False
    assert b["intake"]["reason"] == "duplicate"


async def test_a_message_with_too_many_attachments_is_refused():
    """Reachable by anyone who knows the address, and every attachment is read into memory."""
    await _open_channel()
    files = [_pdf(f"x{i}.pdf") for i in range(25)]
    r = await _post(files=files, data={"recipient": "ap@springb.axcion.io"})
    assert r.status_code == 413, r.text


async def test_the_blob_store_is_not_an_open_door(monkeypatch):
    """The run cap bounds what gets READ. On its own it left storage open: a duplicate queues no
    run but is still received, and 25 MB an attachment from anyone who knows the address adds up
    faster than anybody notices. So the forwarded door counts DOCUMENTS too.

    429, not 404: whoever is on the other end already produced a working address, and a provider
    told 404 may decide the mailbox is dead and stop retrying for good.
    """
    await _open_channel()
    tid = await _tid()
    async with SessionLocal() as s:
        already = await intake._emailed_today(s, tid)
    monkeypatch.setattr(intake, "MAX_INTAKE_PER_DAY", already + 1)
    assert (await _post(data={"recipient": "ap@springb.axcion.io"})).status_code == 200
    full = await _post(data={"recipient": "ap@springb.axcion.io"})
    assert full.status_code == 429, full.text


async def test_what_lands_before_the_day_fills_is_still_attributable(monkeypatch):
    """Each attachment commits as it lands, so a message that runs into the cap half way through
    leaves some documents filed and some refused. Those that landed must still carry who sent
    them — provenance is the one thing about an inbound document that cannot be recovered later.
    """
    await _open_channel()
    tid = await _tid()
    async with SessionLocal() as s:
        already = await intake._emailed_today(s, tid)
    monkeypatch.setattr(intake, "MAX_INTAKE_PER_DAY", already + 2)
    r = await _post(files=[_pdf(f"part{i}.pdf") for i in range(5)],
                    data={"recipient": "ap@springb.axcion.io", "sender": "half@way.example"})
    assert r.status_code == 429, r.text
    async with SessionLocal() as s:
        rows = (await s.execute(select(AuditLog).where(
            AuditLog.tenant_id == tid, AuditLog.action == "payables.email_ingest"))).scalars().all()
    last = rows[-1]
    assert last.detail["from"] == "half@way.example"
    assert last.detail["day_full"] is True
    assert last.detail["files"] == 2, last.detail


async def test_an_upload_is_not_bounded_by_the_forwarded_door(monkeypatch):
    """An upload came from somebody who signed in. Letting a flood of forwarded mail lock the
    screen's own upload button would turn a nuisance into an outage."""
    tid = await _tid()
    monkeypatch.setattr(intake, "MAX_INTAKE_PER_DAY", 0)
    async with SessionLocal() as s:
        out = await intake.receive_invoice(s, tid, None, filename="by-hand.pdf",
                                           data=b"%PDF " + uuid.uuid4().bytes, via="upload")
    assert out["document_id"]


async def test_a_hostile_recipient_field_cannot_cost_a_lookup_each(monkeypatch):
    """A thousand addresses in one header is a thousand tenant lookups on an unauthenticated
    route. Both the field and the candidate list are bounded."""
    huge = ", ".join(f"ap@t{i}.axcion.io" for i in range(500))
    got = intake.candidate_recipients(to=huge)
    assert len(got) <= intake.MAX_CANDIDATES
    assert len(intake.candidate_recipients(recipient="x" * 50000 + "@springb.axcion.io")) == 0


async def test_an_empty_attachment_is_skipped_not_filed():
    await _open_channel()
    r = await _post(files=[("files", ("empty.pdf", b"", "application/pdf"))],
                    data={"recipient": "ap@springb.axcion.io"})
    assert r.status_code == 400, r.text


# ══ the documents stay out of the Binder ══════════════════════════════════════════════════

async def test_an_invoice_is_invisible_to_the_binder_and_readable_from_payables():
    """Both halves. Binder's list and Binder's raw route must not serve it — and Payables' own
    route must, or the separation just loses the document."""
    await _open_channel()
    body = b"%PDF-1.4 hidden from binder " + uuid.uuid4().bytes
    r = await _post(files=[_pdf("hidden.pdf", body)], data={"recipient": "ap@springb.axcion.io"})
    doc_id = r.json()["documents"][0]["document_id"]
    tok = await _token()

    async with _client() as c:
        lst = await c.get("/api/v1/binder/documents", headers=binder_headers(tok))
        assert lst.status_code == 200
        assert doc_id not in [d["id"] for d in lst.json()["documents"]]
        raw = await c.get(f"/api/v1/binder/documents/{doc_id}/raw", headers=binder_headers(tok))
        assert raw.status_code == 404, "the Binder served a supplier invoice"
        mine = await c.get(f"/api/v1/payables/documents/{doc_id}/raw",
                           headers={"Authorization": f"Bearer {tok}"})
    assert mine.status_code == 200, mine.text
    assert mine.content == body


async def test_the_payables_route_will_not_serve_a_binder_document():
    """The converse, and the one that matters: Payables is NOT behind the second factor, so if
    its raw route served any document by id it would be a way to read a lease without the code."""
    tid = await _tid()
    # With a real blob behind it. Without one the route 404s at the "file is not available"
    # check, so the test passes whether the category filter is there or not -- it asserts the
    # right status for the wrong reason, which is how a control ends up untested. (Found by
    # deleting the filter: the test stayed green.)
    async with SessionLocal() as s:
        doc = BinderDocument(tenant_id=tid, filename="lease.pdf", category="lease",
                             content_hash=uuid.uuid4().hex, uploaded_via="upload")
        s.add(doc)
        await s.flush()
        doc.storage_ref = binder_ingest.binder_storage.store(
            tid, doc.id, doc.filename, b"%PDF-1.4 the lease itself")
        await s.commit()
        did = doc.id
    tok = await _token()
    async with _client() as c:
        r = await c.get(f"/api/v1/payables/documents/{did}/raw",
                        headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 404, "Payables served a Binder document"


async def test_the_binder_extractor_does_not_read_payables_documents():
    """It would spend a Claude call per invoice to conclude an invoice is not an insurance
    policy, file it as an 'other' Binder document, and mark it extracted — which then hides it
    from the clerk whose job it actually was."""
    from app.services import binder_extract
    tid = await _tid()
    async with SessionLocal() as s:
        doc = BinderDocument(tenant_id=tid, filename="inv.pdf", category="payable",
                             content_hash=uuid.uuid4().hex, uploaded_via="email")
        s.add(doc)
        await s.commit()
        did = doc.id
    # Derived from the queue itself rather than by running the extractor, which needs a key: the
    # pass takes exactly what this query returns.
    async with SessionLocal() as s:
        picked = (await s.execute(select(BinderDocument.id).where(
            BinderDocument.tenant_id == tid, BinderDocument.extracted.is_(None),
            binder_ingest.binder_scope()))).scalars().all()
    assert did not in picked
    assert binder_extract.run_binder_extraction, "the pass still exists to be filtered"


async def test_the_clerk_cannot_be_handed_a_binder_document():
    """document_id arrives in a RUN's context, and a manager can queue a run by hand. Without the
    category check, naming a Binder document there would have the clerk read a lease back out in
    its summary — past the step-up, with no code asked for."""
    from app.services.ai_context_ap import document_blocks
    tid = await _tid()
    async with SessionLocal() as s:
        lease = BinderDocument(tenant_id=tid, filename="lease2.pdf", category="lease",
                              content_hash=uuid.uuid4().hex, uploaded_via="upload")
        s.add(lease)
        await s.flush()
        lease.storage_ref = binder_ingest.binder_storage.store(
            tid, lease.id, lease.filename, b"%PDF-1.4 the lease")
        await s.commit()
        lid = lease.id

    class _Run:
        context = {"document_id": str(lid)}

    async with SessionLocal() as s:
        assert await document_blocks(s, tid, _Run()) is None


# ══ the settings surface ══════════════════════════════════════════════════════════════════

async def test_only_an_owner_or_admin_can_move_the_door():
    """A books grant is bookkeeping. Changing what the workspace accepts from the outside is
    administration."""
    tid = await _tid()
    async with SessionLocal() as s:
        u = User(tenant_id=tid, email=f"clerk-{uuid.uuid4().hex[:6]}@springb.com", name="Clerk",
                 password_hash=hash_pw("x"), role="member", status="active",
                 tab_access=["books"], token_version=0)
        s.add(u)
        await s.commit()
        email = u.email
    tok = await _token(email, "x")
    async with _client() as c:
        got = await c.get("/api/v1/payables/email", headers={"Authorization": f"Bearer {tok}"})
        put = await c.put("/api/v1/payables/email", json={"enabled": True},
                          headers={"Authorization": f"Bearer {tok}"})
    assert got.status_code == 200 and got.json()["can_manage"] is False
    assert put.status_code == 403, put.text


@pytest.mark.parametrize("bad", ["", "  ", "Not Valid!", "-leading", "a" * 32, "binder"])
async def test_an_address_that_cannot_work_is_refused_with_a_reason(bad):
    tok = await _token()
    async with _client() as c:
        r = await c.put("/api/v1/payables/email", json={"local_part": bad},
                        headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 400, f"{bad!r} was accepted"
    assert r.json()["detail"], "refused with nothing to act on"


async def test_changing_the_address_is_audited_with_what_it_was():
    """An address that quietly changed is an address whose invoices quietly stopped arriving."""
    await _open_channel(local="ap")
    tok = await _token()
    async with _client() as c:
        r = await c.put("/api/v1/payables/email", json={"local_part": "bills"},
                        headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["address"].startswith("bills@")
    async with SessionLocal() as s:
        rows = (await s.execute(select(AuditLog).where(
            AuditLog.tenant_id == await _tid(),
            AuditLog.action == "payables.email_channel"))).scalars().all()
    assert rows and rows[-1].detail["from"]["local_part"] == "ap"
    assert rows[-1].detail["to"]["local_part"] == "bills"
    assert rows[-1].category == "Payments"


async def test_the_screen_says_when_the_platform_half_is_missing(monkeypatch):
    """A workspace can open its address and forward its mail, and with no platform secret every
    message is answered 404 and the sender is not told. The screen has to say so."""
    monkeypatch.setattr(settings, "PAYABLES_INGEST_SECRET", "")
    tok = await _token()
    async with _client() as c:
        r = await c.get("/api/v1/payables/email", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["channel_open"] is False


# ══ the trigger: a filed document becomes a queued reading ════════════════════════════════

async def _clerk(tid, *, skill_enabled=True):
    async with SessionLocal() as s:
        emp = AIEmployee(tenant_id=tid, name="AP Clerk", role_title="Accounts Payable",
                         status="active", config={"family": AP}, writeback_enabled=False)
        s.add(emp)
        await s.flush()
        s.add(AIEmployeeSkill(tenant_id=tid, employee_id=emp.id, skill_key="ap_intake",
                              enabled=skill_enabled))
        await s.commit()
        return emp.id


async def _retire(emp_id):
    async with SessionLocal() as s:
        e = await s.get(AIEmployee, emp_id)
        e.status = "archived"
        await s.commit()


async def _file(tid, monkeypatch, *, body=None, via="upload"):
    """Through the UPLOAD door by default, deliberately.

    There are two caps and they share one number: readings per day, and forwarded documents per
    day. A test that lowers the number to exercise one of them trips the other, and the failure
    reads like the control under test firing when it is the other one. The upload door is exempt
    from the document cap -- somebody signed in to use it -- so filing this way isolates the
    reading cap, and the document cap has its own test on the email door.
    """
    async with SessionLocal() as s:
        return await intake.receive_invoice(
            s, tid, None, filename="t.pdf", data=body or (b"%PDF " + uuid.uuid4().bytes),
            via=via, sender="x@y.example")


async def test_a_filed_invoice_queues_a_reading_naming_that_document(monkeypatch):
    """The trigger, and the whole reason the channel exists. Before this, ap_intake could only be
    fired by hand by somebody who already knew a document's id."""
    monkeypatch.setattr(settings, "AI_EMPLOYEES_ENABLED", True)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-test")
    tid = await _tid()
    emp_id = await _clerk(tid)
    try:
        out = await _file(tid, monkeypatch)
        assert out["intake"]["queued"] is True, out
        async with SessionLocal() as s:
            run = await s.get(AIRun, uuid.UUID(out["intake"]["run_id"]))
            assert run.skill_key == "ap_intake"
            assert run.trigger == "document"
            assert run.status == "queued"
            # This is the field the seam reads to attach the file. Without it the clerk is handed
            # vendors and a chart of accounts and asked to read an invoice it never received.
            assert run.context["document_id"] == out["document_id"]
            assert run.trigger_context["title"] == "t.pdf"
    finally:
        await _retire(emp_id)


async def test_with_nobody_doing_ap_the_document_is_still_filed(monkeypatch):
    """Every refusal to queue keeps the document. The reason is something a person can act on —
    not a PDF in a table with nothing watching it."""
    monkeypatch.setattr(settings, "AI_EMPLOYEES_ENABLED", True)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-test")
    tid = await _tid()
    out = await _file(tid, monkeypatch)           # no AP-family employee exists
    assert out["intake"] == {"queued": False, "reason": "no_clerk"}
    async with SessionLocal() as s:
        assert await s.get(BinderDocument, uuid.UUID(out["document_id"])) is not None


async def test_a_switched_off_skill_files_without_reading(monkeypatch):
    monkeypatch.setattr(settings, "AI_EMPLOYEES_ENABLED", True)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-test")
    tid = await _tid()
    emp_id = await _clerk(tid, skill_enabled=False)
    try:
        out = await _file(tid, monkeypatch)
        assert out["intake"]["reason"] == "skill_off", out
    finally:
        await _retire(emp_id)


async def test_with_ai_off_entirely_nothing_is_queued(monkeypatch):
    monkeypatch.setattr(settings, "AI_EMPLOYEES_ENABLED", False)
    tid = await _tid()
    emp_id = await _clerk(tid)
    try:
        out = await _file(tid, monkeypatch)
        assert out["intake"]["reason"] == "ai_disabled", out
    finally:
        await _retire(emp_id)


async def test_the_day_is_bounded(monkeypatch):
    """A stranger pulls this trigger. The monthly token budget is the money backstop, but it is
    spent by the time it bites; this bounds the day. Past the cap the document is still filed."""
    monkeypatch.setattr(settings, "AI_EMPLOYEES_ENABLED", True)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-test")
    tid = await _tid()
    # The cap counts the DAY, and earlier tests in this module have already spent some of it
    # against the same workspace. A literal 2 here would be a test that passes or fails depending
    # on which of its neighbours ran first -- so the headroom is measured, and what is asserted is
    # that exactly that much gets through and the next one does not.
    async with SessionLocal() as s:
        already = await intake._intake_runs_today(s, tid)
    monkeypatch.setattr(intake, "MAX_INTAKE_PER_DAY", already + 2)
    emp_id = await _clerk(tid)
    try:
        reasons = []
        for _ in range(4):
            out = await _file(tid, monkeypatch)
            reasons.append(out["intake"]["reason"])
        assert reasons[:2] == [None, None], reasons
        # From queue_intake, not from the document cap: these went in through the upload door.
        assert reasons[2:] == ["daily_cap", "daily_cap"], reasons
    finally:
        await _retire(emp_id)


async def test_the_cap_is_read_at_call_time_not_bound_at_import():
    """The test above monkeypatches the module attribute. If queue_intake had captured the
    constant into a default argument or a local at import, that patch would be a no-op and the
    test would be measuring nothing."""
    import inspect
    src = inspect.getsource(intake.queue_intake)
    assert "MAX_INTAKE_PER_DAY" in src, "the cap moved out of the function that enforces it"
    assert intake.MAX_INTAKE_PER_DAY >= 1


async def test_an_upload_goes_through_the_same_door(monkeypatch):
    """One door, two entrances. A PDF dropped on the screen is filed as a payable and read by the
    same clerk — the upload route no longer files invoices as Binder 'other' documents."""
    monkeypatch.setattr(settings, "AI_EMPLOYEES_ENABLED", True)
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-test")
    tid = await _tid()
    emp_id = await _clerk(tid)
    tok = await _token()
    try:
        async with _client() as c:
            r = await c.post("/api/v1/payables/upload",
                             headers={"Authorization": f"Bearer {tok}"},
                             files=[("file", ("dropped.pdf",
                                              b"%PDF " + uuid.uuid4().bytes, "application/pdf"))])
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["intake"]["queued"] is True, out
        async with SessionLocal() as s:
            doc = await s.get(BinderDocument, uuid.UUID(out["document_id"]))
            assert doc.category == "payable"
            assert doc.uploaded_via == "upload"
            assert doc.uploaded_by is not None, "a person dropped this one"
    finally:
        await _retire(emp_id)


async def test_a_w9_is_filed_with_payables_not_among_the_entitys_tax_papers():
    """A W-9 IS a tax document — the supplier's. Filed as plain `tax` it sat in the middle of the
    workspace's own filings, in the section people unlock with a code."""
    tok = await _token()
    async with _client() as c:
        v = await c.post("/api/v1/payables/vendors",
                         headers={"Authorization": f"Bearer {tok}"},
                         json={"legal_name": f"W9 Co {uuid.uuid4().hex[:5]}", "terms_days": 30})
        assert v.status_code in (200, 201), v.text
        vid = v.json()["id"]
        r = await c.post(f"/api/v1/payables/vendors/{vid}/documents",
                         headers={"Authorization": f"Bearer {tok}"},
                         files=[("file", ("w9.pdf", b"%PDF w9 " + uuid.uuid4().bytes,
                                          "application/pdf"))])
    assert r.status_code == 200, r.text
    assert r.json()["w9"] is True
    async with SessionLocal() as s:
        doc = await s.get(BinderDocument, uuid.UUID(r.json()["w9_document_id"]))
    assert doc.category == "vendor_tax"
    assert doc.category in binder_ingest.PAYABLES_CATEGORIES


async def test_every_payables_category_is_hidden_from_the_binder():
    """Derived from the set, not listed by hand: a third Payables category added later is covered
    here rather than discovered in the Binder's document list."""
    assert binder_ingest.PAYABLES_CATEGORIES <= binder_ingest.CATEGORIES
    assert not (binder_ingest.PAYABLES_CATEGORIES & binder_ingest.BINDER_CATEGORIES)
    tid = await _tid()
    made = []
    async with SessionLocal() as s:
        for cat in sorted(binder_ingest.PAYABLES_CATEGORIES):
            d = BinderDocument(tenant_id=tid, filename=f"{cat}.pdf", category=cat,
                               content_hash=uuid.uuid4().hex, uploaded_via="upload")
            s.add(d)
            made.append(d)
        await s.commit()
        ids = {str(d.id) for d in made}
    tok = await _token()
    async with _client() as c:
        lst = await c.get("/api/v1/binder/documents", headers=binder_headers(tok))
    shown = {d["id"] for d in lst.json()["documents"]}
    assert not (ids & shown), sorted(ids & shown)
