"""Phase C: verdicts, guardrails, and that every write is one validate_only preview until the token can manage ads."""
import datetime as dt
import json
import uuid

import httpx
import pytest

from app.db import SessionLocal, engine
from app.integrations import meta_ads_write as mw
from app.models import AdAccount, AdCampaign, AdWrite, Base, Business, Integration, Tenant, User
from app.security import enc
from app.services.ads_recommend import clamp_increase, recommend
from app.services import ads_write as W


def test_kill_names_spend_with_no_seats_and_scale_raises_twenty_percent():
    kill = recommend({
        "level": "ad", "external_id": "1", "name": "Dallas VIP", "group": "Forum Dallas",
        "spend": 80, "impressions": 5000, "leads": 0, "seats": 0, "ctr": 0.4, "cpl": None,
        "daily_budget": 40,
    })
    assert kill["verdict"] == "kill" and kill["action"] == "pause"
    assert "spent $80, no seats / no enrollments" in kill["reason"]

    scale = recommend({
        "level": "campaign", "external_id": "c1", "name": "Edge", "group": "Edge Webinar",
        "spend": 200, "impressions": 8000, "leads": 4, "seats": 2, "ctr": 1.4, "cpl": 50,
        "cost_per_seat": 100, "daily_budget": 40, "budget_external_id": "c1",
    })
    assert scale["verdict"] == "scale"
    assert scale["proposed"]["daily_budget"] == 48

    quiet = recommend({
        "level": "ad", "external_id": "2", "name": "New", "spend": 5, "impressions": 100,
        "leads": 0, "seats": 0, "ctr": 0.2,
    })
    assert quiet["verdict"] == "keep" and quiet["action"] is None


def test_guardrails_cap_the_raise_and_refuse_past_the_weekly_plan():
    allowed, why = clamp_increase(100, 150, weekly_budget=1400, week_spend=100, today_spend=0,
                                  already_pct=0, guard={"max_increase_pct": 20, "max_daily_increase_pct": 20})
    assert allowed == 120 and why is None
    allowed, why = clamp_increase(100, 120, weekly_budget=700, week_spend=700, today_spend=0,
                                  already_pct=0, guard=None)
    assert allowed is None and "planned budget" in why
    allowed, why = clamp_increase(100, 120, weekly_budget=None, week_spend=0, today_spend=0,
                                  already_pct=20, guard=None)
    assert allowed is None and "daily maximum" in why
    allowed, why = clamp_increase(40, 80, weekly_budget=None, week_spend=0, today_spend=10,
                                  already_pct=0, guard={"max_daily_spend": 50, "max_increase_pct": 50,
                                                        "max_daily_increase_pct": 50})
    assert allowed == 50 and why is None


def test_every_write_path_is_a_validate_only_request():
    """The body Meta receives for a dry run. Image upload is the path that cannot be validated."""
    cases = [
        ("pause", "111", None, {}),
        ("activate", "111", None, {}),
        ("set_daily_budget", "111", None, {"daily_budget": 40}),
        ("set_lifetime_budget", "111", None, {"lifetime_budget": 400}),
        ("duplicate", "111", None, {}),
        ("fix_tagging", "111", "cr1", {}),
        ("set_url_tags", "111", None, {}),
        ("create_ad", "222", None, {"name": "Test", "adset_id": "222", "headline": "Hello", "link": "https://springb.com"}),
    ]
    for action, ext, creative, params in cases:
        path, body, kind = mw.plan_request(
            action, ext, creative_id=creative, params=params, account_external_id="act_1")
        assert "execution_options" not in body
        assert kind in mw.PATH_NOTES
        if action == "duplicate":
            assert path.endswith("/copies") and body["status_option"] == "PAUSED"
        if action == "set_daily_budget":
            assert body["daily_budget"] == 4000
        if action == "fix_tagging":
            assert "utm_content={{ad.id}}" in body["url_tags"]
            assert path == "cr1"
        if action == "create_ad":
            assert path == "act_1/ads" and body["status"] == "PAUSED"
            assert "image" not in body
    assert "no validate_only" in mw.PATH_NOTES["create_ad"]


@pytest.mark.asyncio
async def test_graph_post_puts_validate_only_on_the_wire(monkeypatch):
    seen = {}

    class _Resp:
        status_code = 200
        def json(self):
            return {"success": True}

    class _Client:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, headers=None, json=None):
            seen["url"] = url
            seen["body"] = json
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    out = await mw.graph_post("token", "111", {"status": "PAUSED"}, validate_only=True)
    assert out["success"] is True
    assert seen["body"]["execution_options"] == ["validate_only"]
    assert seen["body"]["status"] == "PAUSED"
    assert seen["url"].endswith("/111")


@pytest.fixture
async def env():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        t = Tenant(slug=f"c-{uuid.uuid4().hex[:8]}", name="PhaseC")
        s.add(t)
        await s.flush()
        b = Business(tenant_id=t.id, key=f"f{uuid.uuid4().hex[:4]}", name="Forum",
                     tag="mb", kind="membership", sort_order=0)
        s.add(b)
        await s.flush()
        user = User(tenant_id=t.id, email=f"{uuid.uuid4().hex[:6]}@x.com", name="Ada",
                    role="admin", status="active")
        integ = Integration(tenant_id=t.id, provider="meta_ads", business_id=b.id, status="connected",
                            access_token_enc=enc("test-token"))
        s.add_all([user, integ])
        await s.flush()
        acct = AdAccount(tenant_id=t.id, integration_id=integ.id, business_id=b.id, platform="meta",
                         external_id="act_1", name="Meta", currency="USD", write_scope="ads_read")
        s.add(acct)
        await s.flush()
        camp = AdCampaign(tenant_id=t.id, ad_account_id=acct.id, external_id="c1", name="Forum Dallas VIP",
                          status="ACTIVE", daily_budget=40)
        s.add(camp)
        await s.commit()
        yield {"tid": t.id, "bid": b.id, "uid": user.id, "aid": acct.id}


@pytest.mark.asyncio
async def test_apply_without_the_scope_stays_a_preview(env, monkeypatch):
    calls = []

    async def fake(token, path, body, *, validate_only):
        calls.append((path, body, validate_only))
        return {"success": True}

    monkeypatch.setattr(W, "graph_post", fake)
    async with SessionLocal() as s:
        account = await s.get(AdAccount, env["aid"])
        user = await s.get(User, env["uid"])
        result = await W.execute(
            s, account, actor=user, action="pause", level="campaign", external_id="c1",
            reason="spent $80, no seats", dry_run=False)
        await s.commit()
        camp = (await s.execute(
            __import__("sqlalchemy").select(AdCampaign).where(
                AdCampaign.ad_account_id == env["aid"], AdCampaign.external_id == "c1"))).scalar_one()
        row = (await s.execute(
            __import__("sqlalchemy").select(AdWrite))).scalars().first()
    assert calls[0][2] is True
    assert calls[0][1]["status"] == "PAUSED"
    assert result["dry_run"] is True
    assert result["status"] == "preview"
    assert "Reconnect Meta" in (result["validation_note"] or "")
    assert camp.status == "ACTIVE"
    assert row.dry_run is True and row.before["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_a_live_pause_lands_and_undo_turns_it_back_on(env, monkeypatch):
    async def fake(token, path, body, *, validate_only):
        assert validate_only is False
        return {"success": True}

    monkeypatch.setattr(W, "graph_post", fake)
    async with SessionLocal() as s:
        account = await s.get(AdAccount, env["aid"])
        account.write_scope = "ads_management"
        user = await s.get(User, env["uid"])
        await s.commit()
        first = await W.execute(
            s, account, actor=user, action="pause", level="campaign", external_id="c1",
            reason="kill", dry_run=False)
        await s.commit()
        assert first["status"] == "applied"
        undone = await W.undo_last(s, account, user)
        await s.commit()
        camp = (await s.execute(
            __import__("sqlalchemy").select(AdCampaign).where(
                AdCampaign.ad_account_id == env["aid"], AdCampaign.external_id == "c1"))).scalar_one()
    assert undone["action"] == "activate"
    assert undone["status"] == "applied"
    assert camp.status == "ACTIVE"


@pytest.mark.asyncio
async def test_the_kill_switch_blocks_a_token_that_could_write(env, monkeypatch):
    async def fake(token, path, body, *, validate_only):
        assert validate_only is True
        return {"success": True}

    monkeypatch.setattr(W, "graph_post", fake)
    async with SessionLocal() as s:
        account = await s.get(AdAccount, env["aid"])
        account.write_scope = "ads_management"
        account.writes_killed = True
        user = await s.get(User, env["uid"])
        result = await W.execute(
            s, account, actor=user, action="pause", level="campaign", external_id="c1",
            reason="kill", dry_run=False)
    assert result["dry_run"] is True
    assert "stopped" in (result["validation_note"] or "")


def test_scope_miss_recognises_metas_permission_error():
    from app.integrations.meta_ads import MetaError
    assert mw.is_scope_miss(MetaError("(#200) Requires ads_management permission to manage the object", code=200))
    assert not mw.is_scope_miss(MetaError("Invalid parameter", code=100))
