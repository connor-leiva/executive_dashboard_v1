"""Acumyn Binder — Part 1 schema + rule-seed smoke test (build step 1).

Confirms the five new tables build via create_all, the two uniqueness constraints the
later steps rely on (one obligation per entity/kind; one document per content hash), the
Business↔LegalEntity tie, and — the load-bearing invariants of the whole module — that
the jurisdiction-rule seed populates the expected system rules idempotently while NEVER
seeding a LegalEntity row. Ingestion / extraction / confirmation / API tests arrive with
later steps.
"""
import datetime as dt

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.seed import seed
from app.db import SessionLocal
from app.models import (
    Tenant, Business, LegalEntity, BinderDocument, Obligation,
    ProposedObligation, JurisdictionRule,
)
from app.services.binder_rules import SYSTEM_RULES, seed_jurisdiction_rules


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _springb():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        b = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        return t.id, b.id


# ── Schema builds ────────────────────────────────────────────────────────────

async def test_all_binder_tables_exist():
    """create_all (run in seed) must build every Binder table; a full LegalEntity →
    BinderDocument → Obligation → ProposedObligation chain round-trips."""
    tid, _ = await _springb()
    async with SessionLocal() as s:
        ent = LegalEntity(tenant_id=tid, legal_name="Test Holdings, LLC",
                          entity_type="llc", jurisdiction="UT",
                          formation_date=dt.date(2020, 6, 14), entity_group="holding")
        s.add(ent)
        await s.flush()

        doc = BinderDocument(tenant_id=tid, entity_id=ent.id, filename="articles.pdf",
                             category="formation", content_hash="deadbeef01")
        s.add(doc)
        await s.flush()

        ob = Obligation(tenant_id=tid, entity_id=ent.id, kind="annual_report",
                        jurisdiction="UT", cadence="annual",
                        source_document_id=doc.id, due_date=dt.date(2026, 6, 30))
        s.add(ob)
        await s.flush()

        s.add(ProposedObligation(tenant_id=tid, document_id=doc.id, entity_id=ent.id,
                                 kind="annual_report", method="rule", confidence=0.92,
                                 proposed={"due_date": "2026-06-30", "cadence": "annual"},
                                 basis="Formation date + Utah anniversary-month-end rule."))
        await s.commit()

        got = (await s.execute(select(Obligation).where(Obligation.entity_id == ent.id))).scalars().all()
        assert len(got) == 1 and got[0].kind == "annual_report"


async def test_business_legal_entity_tie():
    """Business.legal_entity_id links an operating business to its legal entity (tax
    lifecycle). Nullable — untied businesses are unaffected."""
    tid, bid = await _springb()
    async with SessionLocal() as s:
        ent = LegalEntity(tenant_id=tid, legal_name="Spring B Operating, LLC",
                          entity_type="llc", jurisdiction="UT", entity_group="operating")
        s.add(ent)
        await s.flush()
        biz = (await s.execute(select(Business).where(Business.id == bid))).scalar_one()
        biz.legal_entity_id = ent.id
        await s.commit()
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.id == bid))).scalar_one()
        assert biz.legal_entity_id == ent.id
        # Other businesses stay untied.
        others = (await s.execute(select(Business).where(Business.key == "ulrg"))).scalar_one()
        assert others.legal_entity_id is None


async def test_obligation_unique_per_entity_kind():
    """One obligation per (tenant, entity, kind) — the matrix cell identity."""
    tid, _ = await _springb()
    async with SessionLocal() as s:
        ent = LegalEntity(tenant_id=tid, legal_name="Dup Test, LLC", entity_type="llc")
        s.add(ent)
        await s.flush()
        s.add(Obligation(tenant_id=tid, entity_id=ent.id, kind="boi"))
        await s.commit()
        eid = ent.id
    async with SessionLocal() as s:
        s.add(Obligation(tenant_id=tid, entity_id=eid, kind="boi"))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_document_content_hash_unique():
    """Dedup key: one stored document per (tenant, content_hash)."""
    tid, _ = await _springb()
    async with SessionLocal() as s:
        s.add(BinderDocument(tenant_id=tid, filename="a.pdf", content_hash="samehash999"))
        await s.commit()
    async with SessionLocal() as s:
        s.add(BinderDocument(tenant_id=tid, filename="b.pdf", content_hash="samehash999"))
        with pytest.raises(IntegrityError):
            await s.commit()


# ── Rule seed (Part 4) ───────────────────────────────────────────────────────

async def test_rule_seed_populates_expected_system_rules():
    """Seed created shared system rules (tenant_id NULL), each stamped last_verified=today
    with a source_note. Spot-check the load-bearing ones."""
    async with SessionLocal() as s:
        rules = (await s.execute(
            select(JurisdictionRule).where(JurisdictionRule.tenant_id.is_(None))
        )).scalars().all()

    by_key = {(r.jurisdiction, r.entity_type, r.kind): r for r in rules}
    assert len(rules) == len(SYSTEM_RULES)

    # Every rule is verified-today and cites a source (the freshness contract).
    today = dt.date.today()
    for r in rules:
        assert r.last_verified == today
        assert r.source_note and len(r.source_note) > 10
        assert r.active is True

    # Utah LLC annual report → anniversary-month-end.
    ut = by_key[("UT", "llc", "annual_report")]
    assert ut.derivation == "anniversary_month_end" and ut.cadence == "annual"

    # Arizona LLC annual report → not applicable (the jurisdiction-nuance case).
    az = by_key[("AZ", "llc", "annual_report")]
    assert az.derivation == "not_applicable"

    # Federal tax → entity-type calendar with the S/P (Mar 15) vs C/individual (Apr 15) split.
    fed = by_key[(None, None, "federal_tax")]
    assert fed.derivation == "entity_type_calendar"
    cal = fed.params["calendar"]
    assert cal["s_corp"]["month"] == 3 and cal["partnership"]["day"] == 15
    assert cal["c_corp"]["month"] == 4 and cal["individual"]["day"] == 15
    assert fed.params["extension_months"] == 6

    # Estimated payments → quarterly, four standard dates.
    est = by_key[(None, None, "estimated_payments")]
    assert est.cadence == "quarterly" and len(est.params["quarters"]) == 4

    # BOI → one-time, manual, and its source_note carries the 2024-2025 turbulence warning.
    boi = by_key[(None, None, "boi")]
    assert boi.cadence == "one_time" and boi.derivation == "manual"
    assert "2024-2025" in boi.source_note


async def test_no_legal_entity_is_ever_seeded():
    """The module's hard invariant: legal entities are user-created data. A freshly seeded
    tenant starts with ZERO of them (only the ones the tests above added)."""
    async with SessionLocal() as s:
        # Nuke test-added entities, then assert the seed itself created none.
        from sqlalchemy import delete
        await s.execute(delete(LegalEntity))
        await s.commit()
    # Re-run the seed's entity path by re-seeding rules (the only seed hook) and confirm
    # it still adds no entities.
    async with SessionLocal() as s:
        await seed_jurisdiction_rules(s)
        await s.commit()
        n = (await s.execute(select(LegalEntity))).scalars().all()
        assert n == []


async def test_rule_seed_is_idempotent():
    """Re-running the seed inserts nothing new (matches on jurisdiction/entity_type/kind)."""
    async with SessionLocal() as s:
        before = len((await s.execute(
            select(JurisdictionRule).where(JurisdictionRule.tenant_id.is_(None))
        )).scalars().all())
        inserted = await seed_jurisdiction_rules(s)
        await s.commit()
        after = len((await s.execute(
            select(JurisdictionRule).where(JurisdictionRule.tenant_id.is_(None))
        )).scalars().all())
    assert inserted == 0
    assert before == after == len(SYSTEM_RULES)


async def test_string_columns_fit_defaults_and_enums():
    """Every varchar must hold its own default + allowed values. Postgres ENFORCES the
    length (SQLite silently ignores it), so this pure-metadata check guards against a class
    of prod-only 500s — like entity_group varchar(8) vs its default 'operating' (9 chars),
    which passed every SQLite test but truncated on Railway's Postgres."""
    from app.services.binder import ENTITY_GROUPS, ENTITY_TYPES
    for model in (LegalEntity, BinderDocument, Obligation, ProposedObligation, JurisdictionRule):
        for col in model.__table__.columns:
            length = getattr(col.type, "length", None)
            d = col.default
            if length is not None and d is not None and isinstance(getattr(d, "arg", None), str):
                assert len(d.arg) <= length, \
                    f"{model.__name__}.{col.name} default {d.arg!r} exceeds varchar({length})"
    # A non-default allowed value can still overflow — check the enum sets too.
    assert max(len(v) for v in ENTITY_GROUPS) <= LegalEntity.__table__.c.entity_group.type.length
    assert max(len(v) for v in ENTITY_TYPES) <= LegalEntity.__table__.c.entity_type.type.length
