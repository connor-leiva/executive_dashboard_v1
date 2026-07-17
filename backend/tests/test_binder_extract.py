"""Acumyn Binder — Step 4 extraction tests (SPEC Part 3 + 4).

Three layers, from most deterministic to least:
  1. Rule lookup + derivation math (pure) — the schedule engine.
  2. Entity matching + the Spring name-collision cases (pure) — the highest-risk piece.
  3. extract_document with the Claude network seam mocked — proposals are written, the raw
     result is cached, gaps/renewals are flavored, and NO Obligation is ever created.
"""
import datetime as dt
import json
import types
import uuid

import pytest
from sqlalchemy import select, func

from app.seed import seed
from app.db import SessionLocal
from app.config import settings
from app.models import Tenant, User, LegalEntity, Obligation, ProposedObligation
from app.services import binder_extract, binder_ingest
from app.services.binder_extract import normalize_name, match_entity
from app.services.binder_rules import lookup_rule, derive


@pytest.fixture(scope="module", autouse=True)
async def _seeded(tmp_path_factory):
    settings.BINDER_STORAGE_BUCKET = str(tmp_path_factory.mktemp("binder_extract_store"))
    await seed()


async def _tid():
    async with SessionLocal() as s:
        return (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id


async def _owner():
    async with SessionLocal() as s:
        return (await s.execute(select(User).where(User.email == "spring@springb.com"))).scalar_one()


# ══ 1. Rule lookup + derivation ═══════════════════════════════════════════════
async def test_lookup_rule_specificity():
    tid = await _tid()
    async with SessionLocal() as s:
        # AZ LLC annual report -> the specific not_applicable rule, not a wildcard.
        az_llc = await lookup_rule(s, tid, jurisdiction="AZ", entity_type="llc", kind="annual_report")
        assert az_llc is not None and az_llc.derivation == "not_applicable"
        # AZ corp (entity_type not llc) -> the AZ wildcard (manual) rule.
        az_corp = await lookup_rule(s, tid, jurisdiction="AZ", entity_type="c_corp", kind="annual_report")
        assert az_corp is not None and az_corp.derivation == "manual"
        # UT LLC -> anniversary-month-end.
        ut_llc = await lookup_rule(s, tid, jurisdiction="UT", entity_type="llc", kind="annual_report")
        assert ut_llc.derivation == "anniversary_month_end"
        # A state with no annual_report rule -> None (no wildcard exists for that kind).
        tx = await lookup_rule(s, tid, jurisdiction="TX", entity_type="llc", kind="annual_report")
        assert tx is None


async def test_derive_anniversary_month_end():
    tid = await _tid()
    async with SessionLocal() as s:
        rule = await lookup_rule(s, tid, jurisdiction="UT", entity_type="llc", kind="annual_report")
    # Formation in August -> due the last day of August in the cycle on/after today.
    r = derive(rule, today=dt.date(2026, 1, 1), anchor=dt.date(2019, 8, 5))
    assert r["due_date"] == dt.date(2026, 8, 31) and r["applicable"] is True
    # If this year's window has passed, roll to next year.
    r2 = derive(rule, today=dt.date(2026, 9, 1), anchor=dt.date(2019, 8, 5))
    assert r2["due_date"] == dt.date(2027, 8, 31)


async def test_derive_not_applicable_and_manual():
    tid = await _tid()
    async with SessionLocal() as s:
        az = await lookup_rule(s, tid, jurisdiction="AZ", entity_type="llc", kind="annual_report")
        boi = await lookup_rule(s, tid, jurisdiction=None, entity_type=None, kind="boi")
    na = derive(az, today=dt.date(2026, 1, 1), anchor=dt.date(2020, 3, 1))
    assert na["applicable"] is False and na["due_date"] is None
    man = derive(boi, today=dt.date(2026, 1, 1))
    assert man["applicable"] is True and man["due_date"] is None      # human sets it


async def test_derive_entity_type_calendar():
    tid = await _tid()
    async with SessionLocal() as s:
        fed = await lookup_rule(s, tid, jurisdiction=None, entity_type=None, kind="federal_tax")
    scorp = derive(fed, today=dt.date(2026, 1, 1), classification="s_corp")
    ccorp = derive(fed, today=dt.date(2026, 1, 1), classification="c_corp")
    assert scorp["due_date"] == dt.date(2026, 3, 15)     # S-corp / partnership -> Mar 15
    assert ccorp["due_date"] == dt.date(2026, 4, 15)     # C-corp / individual -> Apr 15


async def test_derive_estimated_quarters():
    tid = await _tid()
    async with SessionLocal() as s:
        est = await lookup_rule(s, tid, jurisdiction=None, entity_type=None, kind="estimated_payments")
    assert derive(est, today=dt.date(2026, 1, 1))["due_date"] == dt.date(2026, 1, 15)
    assert derive(est, today=dt.date(2026, 5, 1))["due_date"] == dt.date(2026, 6, 15)


# ══ 2. Entity matching (the collision cases) ══════════════════════════════════
def _e(name, nickname=None):
    return types.SimpleNamespace(id=uuid.uuid4(), legal_name=name, nickname=nickname)


def test_normalize_name_strips_suffixes_and_punct():
    assert normalize_name("Spring B - The Forum, LLC") == "spring b the forum"
    assert normalize_name("SNB, Inc.") == "snb"
    assert normalize_name("Meraki Title Partners, LLC") == "meraki title partners"


def test_match_entity_confident_hit():
    ents = [_e("Utah Life Real Estate Group, LLC", "The Team"),
            _e("Spring B - The Forum, LLC"), _e("SNB, Inc")]
    m = match_entity("Utah Life Real Estate Group", ents)
    assert m["entity_id"] == str(ents[0].id) and m["ambiguous"] is False
    assert m["confidence"] > 0.9


def test_match_entity_flags_spring_collisions():
    # The real near-colliding portfolio names -> a bare "Spring B" must be ambiguous.
    ents = [_e("Spring B - The Forum, LLC"), _e("Spring B - beCollective, LLC")]
    m = match_entity("Spring B", ents)
    assert m["ambiguous"] is True and len(m["candidates"]) == 2


def test_match_entity_weak_is_ambiguous():
    m = match_entity("Totally Unrelated Holdings XYZ", [_e("Utah Life Real Estate Group, LLC")])
    assert m["ambiguous"] is True                     # weak top match still needs a human pick


def test_match_entity_no_guess():
    m = match_entity(None, [_e("Utah Life Real Estate Group, LLC")])
    assert m["entity_id"] is None


def test_match_entity_incidental_substring_is_flagged():
    # "Spring B" vs "REALSpringB": ~0.74 raw char similarity but ZERO shared word-tokens -> an
    # incidental substring collision, must be flagged for a human pick (the go/no-go finding).
    m = match_entity("Spring B", [_e("REALSpringB"), _e("SB Coaching, LLC")])
    assert m["ambiguous"] is True


def test_match_entity_genuine_partial_not_flagged():
    # "Meraki Title" vs "Meraki Title Partners, LLC": similar score, but all guess tokens are
    # present -> a real partial, stays confident (must NOT be over-flagged by the fix above).
    m = match_entity("Meraki Title", [_e("Meraki Title Partners, LLC"), _e("SB Coaching, LLC")])
    assert m["ambiguous"] is False and m["entity_id"]


# ══ 3. extract_document (Claude seam mocked) ══════════════════════════════════
def _mock_claude(monkeypatch, payload):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key")
    text = payload if isinstance(payload, str) else json.dumps(payload)

    async def fake(client, model, blocks):
        return text
    monkeypatch.setattr(binder_extract, "_claude_call", fake)
    monkeypatch.setattr(binder_extract, "_client", lambda: object())


async def _entity(tid, **kw):
    async with SessionLocal() as s:
        e = LegalEntity(tenant_id=tid, **kw)
        s.add(e)
        await s.commit()
        return e.id


async def _doc(tid, owner, filename, data, entity_id=None):
    async with SessionLocal() as s:
        doc, _ = await binder_ingest.ingest_document(
            s, tid, owner, filename=filename, data=data, entity_id=entity_id)
        return doc.id


async def _obligations(tid):
    async with SessionLocal() as s:
        return (await s.execute(select(func.count(Obligation.id)).where(
            Obligation.tenant_id == tid))).scalar_one()


async def test_extract_insurance_read_writes_proposal_not_obligation(monkeypatch):
    tid, owner = await _tid(), await _owner()
    await _entity(tid, legal_name="Utah Life Real Estate Group, LLC", nickname="The Team",
                  entity_type="llc", jurisdiction="UT", formation_date=dt.date(2019, 8, 5))
    did = await _doc(tid, owner, "EO_policy.pdf", b"%PDF insurance decl")
    _mock_claude(monkeypatch, {
        "category": "insurance", "entity_name_guess": "Utah Life Real Estate Group",
        "entity_type_signal": None,
        "anchors": {"expiration_date": "2026-06-14"}, "printed_dates": ["2026-06-14"]})

    before = await _obligations(tid)
    async with SessionLocal() as s:
        doc = (await s.execute(select(binder_ingest.BinderDocument).where(
            binder_ingest.BinderDocument.id == did))).scalar_one()
        summ = await binder_extract.extract_document(s, tid, doc, today=dt.date(2026, 1, 1))
        await s.commit()
    assert summ["category"] == "insurance"
    async with SessionLocal() as s:
        props = (await s.execute(select(ProposedObligation).where(
            ProposedObligation.document_id == did))).scalars().all()
        doc = (await s.execute(select(binder_ingest.BinderDocument).where(
            binder_ingest.BinderDocument.id == did))).scalar_one()
    ins = [p for p in props if p.kind == "insurance"]
    assert ins and ins[0].method == "read"
    assert ins[0].proposed["due_date"] == "2026-06-14"
    assert doc.extracted and doc.extracted["match"]["entity_id"]   # matched + cached
    assert await _obligations(tid) == before                       # invariant 1: no Obligation


async def test_extract_formation_derives_annual_report_rule(monkeypatch):
    tid, owner = await _tid(), await _owner()
    await _entity(tid, legal_name="Zenworth Holdings, LLC", entity_type="llc",
                  jurisdiction="UT", formation_date=dt.date(2019, 8, 5))
    did = await _doc(tid, owner, "articles.pdf", b"%PDF articles of org zenworth")
    _mock_claude(monkeypatch, {
        "category": "formation", "entity_name_guess": "Zenworth Holdings",
        "entity_type_signal": "llc",
        "anchors": {"formation_date": "2019-08-05"}, "printed_dates": ["2019-08-05"]})
    async with SessionLocal() as s:
        doc = (await s.execute(select(binder_ingest.BinderDocument).where(
            binder_ingest.BinderDocument.id == did))).scalar_one()
        await binder_extract.extract_document(s, tid, doc, today=dt.date(2026, 1, 1))
        await s.commit()
    async with SessionLocal() as s:
        props = (await s.execute(select(ProposedObligation).where(
            ProposedObligation.document_id == did))).scalars().all()
    ar = [p for p in props if p.kind == "annual_report"]
    assert ar and ar[0].method == "rule"
    assert ar[0].proposed["due_date"] == "2026-08-31"
    assert "Utah" in ar[0].basis or "UT" in ar[0].basis


async def test_extract_gap_boi_for_active_entity(monkeypatch):
    tid, owner = await _tid(), await _owner()
    await _entity(tid, legal_name="SNB Gap, Inc", entity_type="s_corp",
                  jurisdiction="UT", formation_date=dt.date(2016, 1, 4))
    did = await _doc(tid, owner, "snb_something.pdf", b"%PDF snb doc")
    _mock_claude(monkeypatch, {
        "category": "other", "entity_name_guess": "SNB Gap", "entity_type_signal": "s_corp",
        "anchors": {}, "printed_dates": []})
    async with SessionLocal() as s:
        doc = (await s.execute(select(binder_ingest.BinderDocument).where(
            binder_ingest.BinderDocument.id == did))).scalar_one()
        await binder_extract.extract_document(s, tid, doc, today=dt.date(2026, 1, 1))
        await s.commit()
    async with SessionLocal() as s:
        props = (await s.execute(select(ProposedObligation).where(
            ProposedObligation.document_id == did))).scalars().all()
    boi = [p for p in props if p.kind == "boi"]
    assert boi and boi[0].flavor == "gap"


async def test_extract_ambiguous_match_skips_rule_and_gap(monkeypatch):
    """When the entity match is ambiguous, only read proposals (the document's own printed
    dates) are emitted — no rule/gap obligation pinned to a guessed entity (the go/no-go fix)."""
    tid, owner = await _tid(), await _owner()
    await _entity(tid, legal_name="REALSpringB", entity_type="llc", jurisdiction="UT")
    did = await _doc(tid, owner, "springb_mixed.pdf", b"%PDF spring b doc")
    _mock_claude(monkeypatch, {
        "category": "insurance", "entity_name_guess": "Spring B", "entity_type_signal": "llc",
        "anchors": {"expiration_date": "2026-06-14", "return_type": "1120-S"}, "printed_dates": []})
    async with SessionLocal() as s:
        doc = (await s.execute(select(binder_ingest.BinderDocument).where(
            binder_ingest.BinderDocument.id == did))).scalar_one()
        summ = await binder_extract.extract_document(s, tid, doc, today=dt.date(2026, 1, 1))
        await s.commit()
    assert summ["ambiguous"] is True
    async with SessionLocal() as s:
        props = (await s.execute(select(ProposedObligation).where(
            ProposedObligation.document_id == did))).scalars().all()
    kinds = {p.kind for p in props}
    assert "insurance" in kinds                                   # read proposal kept
    assert "federal_tax" not in kinds and "boi" not in kinds      # rule + gap skipped
    assert all((p.proposed or {}).get("ambiguous") for p in props)


async def test_extract_renewal_matches_existing_obligation(monkeypatch):
    tid, owner = await _tid(), await _owner()
    eid = await _entity(tid, legal_name="Renewal Co, LLC", entity_type="llc",
                        jurisdiction="UT", formation_date=dt.date(2020, 2, 2))
    # A confirmed insurance obligation already exists for this entity.
    async with SessionLocal() as s:
        ob = Obligation(tenant_id=tid, entity_id=eid, kind="insurance",
                        due_date=dt.date(2025, 6, 14), cadence="annual")
        s.add(ob)
        await s.commit()
        ob_id = ob.id
    did = await _doc(tid, owner, "renewal_policy.pdf", b"%PDF new policy")
    _mock_claude(monkeypatch, {
        "category": "insurance", "entity_name_guess": "Renewal Co",
        "anchors": {"expiration_date": "2026-06-14"}, "printed_dates": ["2026-06-14"]})
    async with SessionLocal() as s:
        doc = (await s.execute(select(binder_ingest.BinderDocument).where(
            binder_ingest.BinderDocument.id == did))).scalar_one()
        await binder_extract.extract_document(s, tid, doc, today=dt.date(2026, 1, 1))
        await s.commit()
    async with SessionLocal() as s:
        ins = (await s.execute(select(ProposedObligation).where(
            ProposedObligation.document_id == did, ProposedObligation.kind == "insurance"))).scalar_one()
    # renewal_of_id references the existing Obligation (roll it forward, not duplicate).
    assert ins.flavor == "renewal" and ins.renewal_of_id == ob_id


async def test_extract_malformed_files_other_no_proposals(monkeypatch):
    tid, owner = await _tid(), await _owner()
    did = await _doc(tid, owner, "garbled.pdf", b"%PDF garbled")
    _mock_claude(monkeypatch, "this is not json at all {oops")
    async with SessionLocal() as s:
        doc = (await s.execute(select(binder_ingest.BinderDocument).where(
            binder_ingest.BinderDocument.id == did))).scalar_one()
        summ = await binder_extract.extract_document(s, tid, doc, today=dt.date(2026, 1, 1))
        await s.commit()
    assert summ["category"] == "other" and summ["proposals"] == 0
    async with SessionLocal() as s:
        doc = (await s.execute(select(binder_ingest.BinderDocument).where(
            binder_ingest.BinderDocument.id == did))).scalar_one()
    assert doc.extracted is not None       # cached (empty parse) so it is not re-queued forever


async def test_run_extraction_skipped_without_key(monkeypatch):
    tid = await _tid()
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    async with SessionLocal() as s:
        res = await binder_extract.run_binder_extraction(s, tid)
    assert res["skipped"] is True
