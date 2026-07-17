"""Acumyn Binder — assistant integration (SPEC Part 9.5): the binder_matrix / binder_review
drill keys route to the binder tab and return records, and the assistant context summary is
shaped correctly. (The Claude call itself is covered by the assistant's own tests.)"""
import datetime as dt

import pytest
from sqlalchemy import select

from app.seed import seed
from app.db import SessionLocal
from app.models import Tenant, LegalEntity, BinderDocument, Obligation, ProposedObligation
from app.services.tabs import tab_for_metric
from app.services.lineage import metric_detail
from app.services.binder import build_assistant_summary


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _tid():
    async with SessionLocal() as s:
        return (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id


async def _fixture(tid):
    async with SessionLocal() as s:
        e = LegalEntity(tenant_id=tid, legal_name="Assistant Co, LLC", entity_type="llc", jurisdiction="UT")
        s.add(e)
        await s.flush()
        s.add(Obligation(tenant_id=tid, entity_id=e.id, kind="insurance",
                         due_date=dt.date.today() - dt.timedelta(days=3), cadence="annual",
                         lead_days=45, confirmed_by=None))                       # overdue -> flagged
        d = BinderDocument(tenant_id=tid, entity_id=e.id, filename="asst.pdf",
                           content_hash="asst-hash-1", category="insurance", uploaded_via="upload",
                           extracted={"parsed": {}})
        s.add(d)
        await s.flush()
        s.add(ProposedObligation(tenant_id=tid, document_id=d.id, entity_id=e.id, kind="insurance",
                                 method="read", confidence=0.9, flavor="normal", state="pending",
                                 entity_confidence=0.97, entity_candidates=[],
                                 proposed={"due_date": None, "ambiguous": False, "fields": []}))
        await s.commit()


def test_tab_for_metric_routes_binder():
    assert tab_for_metric("binder_matrix") == "binder"
    assert tab_for_metric("binder_review") == "binder"


async def test_binder_matrix_drill():
    tid = await _tid()
    await _fixture(tid)
    async with SessionLocal() as s:
        d = await metric_detail(s, tid, "binder_matrix", "mtd")
    assert d["source"] == "Binder"
    assert any(r["obligation"] == "insurance" and r["status"] == "overdue" for r in d["rows"])


async def test_binder_review_drill():
    tid = await _tid()
    async with SessionLocal() as s:
        d = await metric_detail(s, tid, "binder_review", "mtd")
    assert d["source"] == "Binder" and d["count"] >= 1
    assert all({"document", "entity", "kind", "ambiguous"} <= set(r) for r in d["rows"])


async def test_assistant_summary_shape():
    tid = await _tid()
    async with SessionLocal() as s:
        summ = await build_assistant_summary(s, tid)
    assert {"flags", "review", "flagged_obligations", "drill"} <= set(summ)
    assert summ["drill"] == {"matrix": "binder_matrix", "review": "binder_review"}
    assert any(f["status"] in ("overdue", "due_soon") for f in summ["flagged_obligations"])
