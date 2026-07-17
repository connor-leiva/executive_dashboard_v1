"""Acumyn Binder — Step 6 confirmation-loop tests (SPEC Part 5.1 / 6).

The human-in-the-loop that turns ProposedObligation rows into tracked Obligations: confirm
(the ONLY path that creates an obligation, always with confirmed_by), ambiguous-pick
enforcement, renewal roll-forward, upsert-on-(entity,kind), dismiss, complete recurrence,
applicability, edit, the review payload, permissions, and status math.
"""
import datetime as dt

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, func

from app.main import app
from app.seed import seed
from app.db import SessionLocal
from app.config import settings
from app.models import (Tenant, User, LegalEntity, BinderDocument, Obligation,
                        ProposedObligation, AuditLog)
from app.security import make_token, hash_pw
from app.services import binder
from app.services.binder_status import compute_status, roll_forward

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(t):
    return {"Authorization": f"Bearer {t}"}


async def _tid():
    async with SessionLocal() as s:
        return (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id


async def _owner_token():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


async def _member(email, tabs):
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        u = User(tenant_id=t.id, email=email, name=email.split("@")[0], password_hash=hash_pw("x"),
                 role="member", status="active", tab_access=tabs, token_version=0)
        s.add(u)
        await s.commit()
        return make_token(u.id, t.id, 0)


_N = [0]   # per-call counter so auto-generated names/hashes never collide


async def _fixture(entity_kw=None, doc_kw=None, prop_kw=None, proposed=None):
    """Create one entity + document + pending proposal; return (entity_id, doc_id, prop_id)."""
    _N[0] += 1
    tag = _N[0]
    async with SessionLocal() as s:
        tid = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id
        e = LegalEntity(tenant_id=tid, legal_name=(entity_kw or {}).get("legal_name", f"Prop Co {tag}, LLC"),
                        entity_type="llc", jurisdiction="UT", **{k: v for k, v in (entity_kw or {}).items() if k != "legal_name"})
        s.add(e)
        d = BinderDocument(tenant_id=tid, filename=(doc_kw or {}).get("filename", "policy.pdf"),
                           content_hash=(doc_kw or {}).get("content_hash", f"hash-{tag}"),
                           category=(doc_kw or {}).get("category", "insurance"),
                           uploaded_via="upload", extracted={"parsed": {}})
        s.add(d)
        await s.flush()
        base = dict(tenant_id=tid, document_id=d.id, entity_id=e.id, kind="insurance",
                    method="read", confidence=0.9, flavor="normal", state="pending",
                    entity_confidence=0.97, entity_candidates=[],
                    proposed=(proposed or {"due_date": None, "lead_days": 45, "cadence": "annual",
                                           "applicable": True, "ambiguous": False, "fields": []}))
        base.update(prop_kw or {})
        p = ProposedObligation(**base)
        s.add(p)
        await s.commit()
        return e.id, d.id, p.id


# ── Confirm ───────────────────────────────────────────────────────────────────
async def test_confirm_creates_obligation_with_confirmed_by():
    tid = await _tid()
    due = (dt.date.today() + dt.timedelta(days=400)).isoformat()
    eid, did, pid = await _fixture(proposed={"due_date": due, "lead_days": 45, "cadence": "annual",
                                             "applicable": True, "ambiguous": False, "fields": []})
    tok = await _owner_token()
    async with _client() as c:
        r = await c.post(f"/api/v1/binder/review/{pid}/confirm", headers=_H(tok), json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "insurance" and body["status"] == "current"
    assert body["confirmed_by"] and body["source_document_id"] == str(did)
    async with SessionLocal() as s:
        ob = (await s.execute(select(Obligation).where(Obligation.id == body["id"]))).scalar_one()
        assert ob.confirmed_by is not None                 # invariant 1
        prop = (await s.execute(select(ProposedObligation).where(
            ProposedObligation.id == pid))).scalar_one()
        assert prop.state == "confirmed"
        doc = (await s.execute(select(BinderDocument).where(BinderDocument.id == did))).scalar_one()
        assert doc.entity_id == eid                        # evidence linked to the entity


async def test_confirm_ambiguous_requires_entity_pick():
    eid, did, pid = await _fixture(
        entity_kw={"legal_name": "Ambiguous Co, LLC"},
        doc_kw={"content_hash": "amb1"},
        prop_kw={"entity_id": None},
        proposed={"due_date": None, "lead_days": 45, "cadence": "annual", "applicable": True,
                  "ambiguous": True, "fields": []})
    tok = await _owner_token()
    async with _client() as c:
        deny = await c.post(f"/api/v1/binder/review/{pid}/confirm", headers=_H(tok), json={})
        allow = await c.post(f"/api/v1/binder/review/{pid}/confirm", headers=_H(tok),
                             json={"entity_id": str(eid)})
    assert deny.status_code == 400 and "ambiguous" in deny.json()["detail"]
    assert allow.status_code == 200 and allow.json()["entity_id"] == str(eid)


async def test_confirm_renewal_advances_existing_obligation():
    tid = await _tid()
    async with SessionLocal() as s:
        e = LegalEntity(tenant_id=tid, legal_name="Renew Co, LLC", entity_type="llc", jurisdiction="UT")
        s.add(e); await s.flush()
        ob = Obligation(tenant_id=tid, entity_id=e.id, kind="insurance",
                        due_date=dt.date(2025, 6, 14), cadence="annual",
                        confirmed_by=None, last_reminded_stage="urgent")
        s.add(ob); await s.flush()
        d = BinderDocument(tenant_id=tid, filename="renewal.pdf", content_hash="renew1",
                           category="insurance", uploaded_via="upload", extracted={"parsed": {}})
        s.add(d); await s.flush()
        newdue = (dt.date.today() + dt.timedelta(days=300)).isoformat()
        p = ProposedObligation(tenant_id=tid, document_id=d.id, entity_id=e.id, kind="insurance",
                               method="read", confidence=0.95, flavor="renewal", renewal_of_id=ob.id,
                               state="pending", entity_confidence=0.99, entity_candidates=[],
                               proposed={"due_date": newdue, "lead_days": 45, "cadence": "annual",
                                         "applicable": True, "ambiguous": False, "fields": []})
        s.add(p); await s.commit()
        eid, ob_id, pid = e.id, ob.id, p.id
    tok = await _owner_token()
    async with _client() as c:
        r = await c.post(f"/api/v1/binder/review/{pid}/confirm", headers=_H(tok), json={})
    assert r.status_code == 200 and r.json()["id"] == str(ob_id)     # same obligation, advanced
    async with SessionLocal() as s:
        # No duplicate obligation was created.
        n = (await s.execute(select(func.count(Obligation.id)).where(
            Obligation.tenant_id == tid, Obligation.entity_id == eid,
            Obligation.kind == "insurance"))).scalar_one()
        ob = (await s.execute(select(Obligation).where(Obligation.id == ob_id))).scalar_one()
    assert n == 1
    assert ob.due_date.isoformat() == newdue and ob.last_completed == dt.date.today()
    assert ob.last_reminded_stage is None and ob.confirmed_by is not None


async def test_confirm_edits_override_proposed():
    _, _, pid = await _fixture(doc_kw={"content_hash": "edit1"},
                               proposed={"due_date": (dt.date.today() + dt.timedelta(days=400)).isoformat(),
                                         "lead_days": 45, "cadence": "annual", "applicable": True,
                                         "ambiguous": False, "fields": []})
    tok = await _owner_token()
    override = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    async with _client() as c:
        r = await c.post(f"/api/v1/binder/review/{pid}/confirm", headers=_H(tok),
                         json={"edits": {"due_date": override, "cadence": "quarterly"}})
    assert r.status_code == 200
    body = r.json()
    assert body["due_date"] == override and body["cadence"] == "quarterly"
    assert body["status"] == "overdue"                     # yesterday's date


# ── Dismiss ─────────────────────────────────────────────────────────────────
async def test_dismiss_keeps_document_filed():
    tid = await _tid()
    _, did, pid = await _fixture(doc_kw={"content_hash": "dismiss1"})
    tok = await _owner_token()
    async with _client() as c:
        r = await c.post(f"/api/v1/binder/review/{pid}/dismiss", headers=_H(tok))
    assert r.status_code == 200 and r.json()["state"] == "dismissed"
    async with SessionLocal() as s:
        doc = (await s.execute(select(BinderDocument).where(BinderDocument.id == did))).scalar_one_or_none()
        obs = (await s.execute(select(func.count(Obligation.id)).where(
            Obligation.tenant_id == tid, Obligation.source_document_id == did))).scalar_one()
    assert doc is not None and obs == 0                    # filed, but nothing tracked


# ── Complete (recurrence) ─────────────────────────────────────────────────────
async def test_complete_rolls_due_date_forward():
    tid = await _tid()
    async with SessionLocal() as s:
        e = LegalEntity(tenant_id=tid, legal_name="Complete Co, LLC", entity_type="llc")
        s.add(e); await s.flush()
        ob = Obligation(tenant_id=tid, entity_id=e.id, kind="annual_report",
                        due_date=dt.date(2026, 3, 31), cadence="annual", confirmed_by=None,
                        last_reminded_stage="lead")
        s.add(ob); await s.commit()
        ob_id = ob.id
    tok = await _owner_token()
    async with _client() as c:
        r = await c.post(f"/api/v1/binder/obligations/{ob_id}/complete", headers=_H(tok))
    assert r.status_code == 200
    async with SessionLocal() as s:
        ob = (await s.execute(select(Obligation).where(Obligation.id == ob_id))).scalar_one()
    assert ob.due_date == dt.date(2027, 3, 31) and ob.last_completed == dt.date.today()
    assert ob.last_reminded_stage is None


def test_roll_forward_cadences():
    assert roll_forward(dt.date(2026, 3, 31), "annual") == dt.date(2027, 3, 31)
    assert roll_forward(dt.date(2026, 3, 31), "biennial") == dt.date(2028, 3, 31)
    assert roll_forward(dt.date(2026, 1, 15), "quarterly") == dt.date(2026, 4, 15)
    assert roll_forward(dt.date(2026, 3, 31), "one_time") == dt.date(2026, 3, 31)   # terminal


# ── Applicability + edit ──────────────────────────────────────────────────────
async def test_applicability_flips_status_to_na():
    tid = await _tid()
    async with SessionLocal() as s:
        e = LegalEntity(tenant_id=tid, legal_name="NA Co, LLC", entity_type="llc")
        s.add(e); await s.flush()
        ob = Obligation(tenant_id=tid, entity_id=e.id, kind="annual_report",
                        due_date=dt.date.today(), cadence="annual", confirmed_by=None)
        s.add(ob); await s.commit()
        ob_id = ob.id
    tok = await _owner_token()
    async with _client() as c:
        r = await c.post(f"/api/v1/binder/obligations/{ob_id}/applicability", headers=_H(tok),
                         json={"applicable": False})
    assert r.status_code == 200 and r.json()["status"] == "not_applicable"


async def test_edit_obligation_fields():
    tid = await _tid()
    async with SessionLocal() as s:
        e = LegalEntity(tenant_id=tid, legal_name="Edit Co, LLC", entity_type="llc")
        s.add(e); await s.flush()
        ob = Obligation(tenant_id=tid, entity_id=e.id, kind="insurance",
                        due_date=dt.date(2026, 6, 1), cadence="annual", confirmed_by=None)
        s.add(ob); await s.commit()
        ob_id = ob.id
    tok = await _owner_token()
    async with _client() as c:
        r = await c.patch(f"/api/v1/binder/obligations/{ob_id}", headers=_H(tok),
                          json={"notes": "renewed early", "lead_days": 30})
    assert r.status_code == 200 and r.json()["notes"] == "renewed early" and r.json()["lead_days"] == 30


# ── Review payload + permissions ──────────────────────────────────────────────
async def test_review_payload_shape():
    await _fixture(doc_kw={"content_hash": "rev1"},
                   prop_kw={"flavor": "gap"}, proposed={"due_date": None, "lead_days": 45,
                            "cadence": "annual", "applicable": True, "ambiguous": False, "fields": []})
    tok = await _owner_token()
    async with _client() as c:
        r = await c.get("/api/v1/binder/review", headers=_H(tok))
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"stats", "proposals", "filed_no_obligation"}
    assert set(body["stats"]) == {"awaiting", "confirmed_this_pass", "entity_unclear", "gaps"}
    assert body["stats"]["gaps"] >= 1
    assert all("basis" in p and "flavor" in p for p in body["proposals"])


async def test_review_requires_binder_tab():
    no_tab = await _member("noreview@springb.com", ["forum"])
    async with _client() as c:
        r = await c.get("/api/v1/binder/review", headers=_H(no_tab))
    assert r.status_code == 403


async def test_every_obligation_has_confirmed_by_after_confirm():
    """Invariant 1 across the suite: every obligation created via the confirm path carries a
    confirmed_by (the directly-seeded test obligations above pass confirmed_by=None, so scope
    this to obligations that came through a confirmed proposal)."""
    tid = await _tid()
    async with SessionLocal() as s:
        confirmed_doc_ids = {p.document_id for p in (await s.execute(select(ProposedObligation).where(
            ProposedObligation.tenant_id == tid, ProposedObligation.state == "confirmed"))).scalars().all()}
        obs = (await s.execute(select(Obligation).where(
            Obligation.tenant_id == tid,
            Obligation.source_document_id.in_(confirmed_doc_ids)))).scalars().all()
    assert obs and all(o.confirmed_by is not None for o in obs)


# ── Status math (Part 6) ──────────────────────────────────────────────────────
def test_compute_status():
    t = dt.date(2026, 7, 16)
    base = dict(applicable=True, kind="insurance", lead_days=45, today=t)
    assert compute_status(due_date=t - dt.timedelta(days=1), **base) == "overdue"
    assert compute_status(due_date=t + dt.timedelta(days=10), **base) == "due_soon"
    assert compute_status(due_date=t + dt.timedelta(days=200), **base) == "current"
    assert compute_status(applicable=False, kind="insurance", due_date=t, lead_days=45, today=t) == "not_applicable"
    assert compute_status(applicable=True, kind="federal_tax", due_date=t + dt.timedelta(days=5),
                          lead_days=45, today=t, books_pending=True) == "in_progress"
