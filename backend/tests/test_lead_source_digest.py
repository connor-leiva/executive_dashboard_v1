"""The weekly lead-source digest (spec phase 6): schedule, idempotency, retries, ClickUp gate, model, unsubscribe.

No network: the mailer's send and ClickUp are replaced, and the model rewrite is a stub that records
which model it was asked for.
"""
import datetime as dt
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (Business, IntranetWorkspace, Lead, LeadSourceDigest, LeadSourceDigestDelivery,
                        LeadSourceGroup, Tenant)
from app.services import lead_source_digest as ld

# Monday 9 November 2026, 06:05 in Denver = 13:05 UTC.
MONDAY_0605 = dt.datetime(2026, 11, 9, 13, 5, tzinfo=dt.timezone.utc)


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


async def _digest(**kw) -> uuid.UUID:
    async with SessionLocal() as s:
        t = Tenant(slug=f"dig-{uuid.uuid4().hex[:6]}", name="Digest Realty", plan="portfolio")
        s.add(t)
        await s.flush()
        b = Business(tenant_id=t.id, key="brokerage", name="Digest Realty", tag="Real estate", kind="real_estate")
        s.add(b)
        await s.flush()
        s.add(IntranetWorkspace(tenant_id=t.id, portal_name="d", subdomain=t.slug, timezone="America/Denver"))
        g = LeadSourceGroup(tenant_id=t.id, business_id=b.id, key="realtor_com", display_name="Realtor.com",
                            variants=["Realtor.com"], is_active=True, display_order=0,
                            exp_risk_team_default=Decimal(0), exp_risk_agent_default=Decimal(0))
        s.add(g)
        await s.flush()
        s.add(Lead(tenant_id=t.id, business_id=b.id, external_id="1", origin="Realtor.com", stage="Lead",
                   created_at_src=dt.date(2026, 11, 2),
                   src_created_at=dt.datetime(2026, 11, 2, 17, tzinfo=dt.timezone.utc)))
        d = LeadSourceDigest(tenant_id=t.id, lead_source_group_id=g.id, is_active=True, send_weekday=0, send_hour=6,
                             send_minute=0, recipients=["coach@example.com"], unsubscribed=[], model=ld.DEFAULT_MODEL,
                             **kw)
        s.add(d)
        await s.commit()
        return d.id


async def _deliveries(did):
    async with SessionLocal() as s:
        return (await s.execute(select(LeadSourceDigestDelivery).where(
            LeadSourceDigestDelivery.digest_id == did).order_by(LeadSourceDigestDelivery.created_at))).scalars().all()


@pytest.fixture
def mail(monkeypatch):
    sent = []

    async def ok(to, subject, html, text, reply_to=None, idempotency_key=None):
        sent.append({"to": to, "subject": subject, "html": html})
        return True
    monkeypatch.setattr(ld.mailer, "send", ok)
    return sent


@pytest.fixture
def clickup(monkeypatch):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"id": "task1"})
    monkeypatch.setattr(ld, "CLICKUP_TRANSPORT", httpx.MockTransport(handler))
    return calls


async def test_monday_six_am_queues_once_a_week(mail):
    did = await _digest()
    async with SessionLocal() as s:
        before = await ld.enqueue_due(s, now=MONDAY_0605 - dt.timedelta(minutes=10))   # 05:55: not yet
    assert not [r for r in before if r.digest_id == did]
    for minutes in (0, 5, 60, 60 * 24 * 3):                       # four ticks, same week
        async with SessionLocal() as s:
            await ld.enqueue_due(s, now=MONDAY_0605 + dt.timedelta(minutes=minutes))
    rows = await _deliveries(did)
    assert [r.run_key for r in rows] == ["week:2026-11-08"]       # the Sunday the week closed on
    async with SessionLocal() as s:
        await ld.enqueue_due(s, now=MONDAY_0605 + dt.timedelta(days=7))
    assert [r.run_key for r in await _deliveries(did)] == ["week:2026-11-08", "week:2026-11-15"]


async def test_a_failing_send_is_tried_three_times_then_failed(monkeypatch):
    async def refuse(*a, **k):
        return False
    monkeypatch.setattr(ld.mailer, "send", refuse)
    did = await _digest()
    async with SessionLocal() as s:
        await ld.enqueue_due(s, now=MONDAY_0605)
    for _ in range(5):
        async with SessionLocal() as s:
            await ld.drain(s, now=MONDAY_0605)
    row = [r for r in await _deliveries(did)][0]
    assert (row.status, row.attempts) == ("failed", 3)


async def test_no_clickup_call_without_a_list_and_one_task_per_coaching_block_with_one(mail, clickup):
    did = await _digest()
    async with SessionLocal() as s:
        await ld.enqueue_due(s, now=MONDAY_0605)
        await ld.drain(s, now=MONDAY_0605)
    assert [r.status for r in await _deliveries(did)] == ["sent"]
    assert clickup == []

    async with SessionLocal() as s:
        d = await s.get(LeadSourceDigest, did)
        blocks = [{"agent": "A B", "tier": "coach", "headline": "h", "body": "b"},
                  {"agent": "C D", "tier": "reinforce", "headline": "h", "body": "b"}]
        assert await ld.clickup_tasks(s, d, blocks, dt.date(2026, 11, 8)) == []
        d.clickup_list_id = "901"
        out = await ld.clickup_tasks(s, d, blocks, dt.date(2026, 11, 8))
    assert out == [{"error": "No ClickUp token is saved for this workspace."}] and clickup == []


async def test_the_next_send_uses_the_model_saved_on_the_config(mail, monkeypatch):
    asked = []

    async def fake_rewrite(model, blocks):
        asked.append(model)
        return blocks
    monkeypatch.setattr(ld, "_rewrite", fake_rewrite)
    did = await _digest()
    async with SessionLocal() as s:
        d = await s.get(LeadSourceDigest, did)
        d.model = "claude-opus-5-5"
        await s.commit()
        await ld.enqueue_due(s, now=MONDAY_0605)
        await ld.drain(s, now=MONDAY_0605)
    assert asked == ["claude-opus-5-5"]
    assert (await _deliveries(did))[0].model_used == "claude-opus-5-5"


async def test_without_a_key_the_template_is_sent(mail):
    did = await _digest()
    async with SessionLocal() as s:
        await ld.enqueue_due(s, now=MONDAY_0605)
        await ld.drain(s, now=MONDAY_0605)
    row = (await _deliveries(did))[0]
    assert row.model_used == "template" and mail and "Realtor.com coaching" in mail[-1]["subject"]
    assert "unsubscribe?token=" in mail[-1]["html"]


async def test_unsubscribe_is_honoured(mail):
    did = await _digest()
    async with SessionLocal() as s:
        d = await s.get(LeadSourceDigest, did)
        token = ld.unsubscribe_token(d, "Coach@Example.com")
        assert await ld.unsubscribe(s, token) == "coach@example.com"
        assert await ld.unsubscribe(s, "not-a-token") is None
        await ld.enqueue_due(s, now=MONDAY_0605)
        await ld.drain(s, now=MONDAY_0605)
    row = (await _deliveries(did))[0]
    assert row.status == "skipped" and not [m for m in mail if m["to"] == "coach@example.com"]


async def test_the_seeded_digest_is_off_with_one_recipient():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        rows = (await s.execute(select(LeadSourceDigest).where(LeadSourceDigest.tenant_id == t.id))).scalars().all()
    assert len(rows) == 1
    assert rows[0].is_active is False and rows[0].recipients == ["connor.leiva@gmail.com"]
    assert rows[0].clickup_list_id is None and rows[0].model == "claude-sonnet-4-6"


def test_the_default_bar_compounds_to_about_eight_and_ten_percent():
    floors = [v["floor"] for v in ld.DEFAULT_BENCHMARKS["transitions"].values()]
    targets = [v["target"] for v in ld.DEFAULT_BENCHMARKS["transitions"].values()]
    # The legacy comment says "10.2%"; the product is 10.15%. About 8% and about 10%, as §4.5 puts it.
    assert round(ld.product(floors) * 100, 1) == 7.8 and round(ld.product(targets) * 100, 1) == 10.1
