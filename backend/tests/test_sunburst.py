"""The Sunburst links.

Sunburst ships with Sisu and Sisu ships with this product's customers, so it is part of the
platform: every workspace gets it, nothing is configured, and the links are derived per member.

Two things are worth defending. The conversation code has to be stable (or every visit starts a
new thread), unique per person (or two agents share one) and unguessable (or knowing the scheme and
an id is enough). And the question link must not be offered before Sisu's host has it: a card that
opens a link the far end does not understand looks like it worked, which is worse than copying.
"""
import datetime as dt
import re
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Business, Domain, IntranetMember, IntranetRole, Tenant, User
from app.security import hash_pw, make_token
from app.services import sunburst


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


@pytest.fixture(autouse=True)
def _production_defaults(monkeypatch):
    """Every test starts from what production runs today, whatever the environment says."""
    monkeypatch.setattr(settings, "SUNBURST_HOST", "https://app.sisu.co")
    monkeypatch.setattr(settings, "SUNBURST_ASK_LINKS", False)


async def _workspace(slug: str, *, on_roster=True):
    host = f"{slug}.localhost"
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        t = Tenant(slug=slug, name=slug.title(), status="active", plan="portfolio",
                   config={"features": {"intranet": True}})
        s.add(t)
        await s.flush()
        s.add(Domain(tenant_id=t.id, hostname=host, is_primary=True))
        s.add(Business(tenant_id=t.id, key="main", name="Main", tag="Business", sort_order=0))
        u = User(tenant_id=t.id, email=f"ada@{slug}.test", name="Ada",
                 password_hash=hash_pw("pw"), role="owner", status="active", token_version=0)
        s.add(u)
        await s.flush()
        if on_roster:
            role = IntranetRole(tenant_id=t.id, key="agent", name="Agent", sort=1,
                                published_at=now)
            s.add(role)
            await s.flush()
            s.add(IntranetMember(tenant_id=t.id, full_name="Ada", email=u.email,
                                 role_id=role.id, status="Active", auth_source="Manual",
                                 user_id=u.id))
        await s.commit()
        return {"host": host, "tenant_id": t.id, "token": make_token(u.id, t.id, 0)}


async def _config(ws):
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://testserver") as c:
        r = await c.get("/api/v1/intranet/config",
                        headers={"Authorization": f"Bearer {ws['token']}",
                                 "x-tenant-host": ws["host"]})
    assert r.status_code == 200, r.text
    return r.json()["config"]


# ── the derived code ──────────────────────────────────────────────────────────────────────

def test_the_code_is_stable_for_one_person():
    """Random per click would start a fresh conversation every visit, which is the opposite of
    what a weekly check-in is for -- last week's thread is this week's context."""
    t, m = uuid.uuid4(), uuid.uuid4()
    assert sunburst.code_for(t, m) == sunburst.code_for(t, m)


def test_two_people_never_share_a_conversation():
    t = uuid.uuid4()
    assert sunburst.code_for(t, uuid.uuid4()) != sunburst.code_for(t, uuid.uuid4())


def test_the_same_member_id_in_two_workspaces_is_two_conversations():
    m = uuid.uuid4()
    assert sunburst.code_for(uuid.uuid4(), m) != sunburst.code_for(uuid.uuid4(), m)


def test_the_code_is_not_reproducible_from_the_ids_alone():
    """Keyed, not a plain hash. sha256(tenant:member) is reproducible by anyone who learns the
    scheme and can read an id -- and the code is the only thing protecting the conversation."""
    import hashlib
    t, m = uuid.uuid4(), uuid.uuid4()
    naive = hashlib.sha256(f"{t}:{m}".encode()).hexdigest()[:32]
    assert sunburst.code_for(t, m) != naive


def test_the_code_is_the_shape_sisu_expects():
    code = sunburst.code_for(uuid.uuid4(), uuid.uuid4())
    assert len(code) == 32
    assert re.fullmatch(r"[0-9a-f]{32}", code), "not the 32-character code the link takes"


# ── the host, and the question link ───────────────────────────────────────────────────────

def test_the_conversation_link_is_on_sisus_production_host_by_default():
    t, m = uuid.uuid4(), uuid.uuid4()
    assert sunburst.link_for(t, m) == f"https://app.sisu.co/app/sb/{sunburst.code_for(t, m)}"


def test_the_host_is_one_setting_for_trying_sisus_next_release(monkeypatch):
    """Sisu ships to next.sisu.co first, on a different database. Pointing there must move every
    link, or a test would open half of Sunburst on one database and half on the other."""
    monkeypatch.setattr(settings, "SUNBURST_HOST", "https://next.sisu.co/")
    monkeypatch.setattr(settings, "SUNBURST_ASK_LINKS", True)
    t, m = uuid.uuid4(), uuid.uuid4()
    assert sunburst.link_for(t, m).startswith("https://next.sisu.co/app/sb/")
    assert sunburst.ask_url() == "https://next.sisu.co/app/sb/ask", "a trailing slash doubled up"


def test_the_question_link_is_not_offered_until_the_host_has_it():
    """app.sisu.co does not have Sisu's question link yet. Offering it would send every prompt
    card to a link the far end does not understand -- the page copies the question instead."""
    assert sunburst.ask_url() == ""
    assert sunburst.carries_prompt() is False


def test_turning_the_question_link_on_is_one_setting(monkeypatch):
    monkeypatch.setattr(settings, "SUNBURST_ASK_LINKS", True)
    assert sunburst.ask_url() == "https://app.sisu.co/app/sb/ask"
    assert sunburst.carries_prompt() is True


# ── what the portal receives ──────────────────────────────────────────────────────────────

async def test_every_workspace_gets_a_link_with_nothing_configured():
    """The whole point: no console setting, no integration to connect."""
    ws = await _workspace("sbevery")
    config = await _config(ws)
    assert config["sunburst"]["url"].startswith("https://app.sisu.co/app/sb/")
    assert config["sunburst"]["ask_url"] == ""
    assert config["sunburst"]["carries_prompt"] is False


async def test_the_portal_is_handed_the_question_link_once_it_is_on(monkeypatch):
    monkeypatch.setattr(settings, "SUNBURST_ASK_LINKS", True)
    ws = await _workspace("sbask")
    config = await _config(ws)
    assert config["sunburst"]["ask_url"] == "https://app.sisu.co/app/sb/ask"
    assert config["sunburst"]["carries_prompt"] is True


async def test_the_link_a_member_gets_is_their_own():
    a = await _workspace("sbmine")
    b = await _workspace("sbtheirs")
    assert (await _config(a))["sunburst"]["url"] != (await _config(b))["sunburst"]["url"]


async def test_the_same_member_gets_the_same_link_every_time():
    ws = await _workspace("sbagain")
    assert (await _config(ws))["sunburst"]["url"] == (await _config(ws))["sunburst"]["url"]


async def test_somebody_not_on_the_roster_gets_no_link_rather_than_somebody_elses(monkeypatch):
    """An owner who never added themselves. There is no member to derive a code for, and the page
    says so instead of opening a conversation that belongs to nobody."""
    monkeypatch.setattr(settings, "SUNBURST_ASK_LINKS", True)
    ws = await _workspace("sbnoroster", on_roster=False)
    config = await _config(ws)
    assert (config["sunburst"]["url"], config["sunburst"]["ask_url"]) == ("", "")


async def test_sunburst_is_not_a_tenant_setting_any_more():
    """It shipped as one for a few hours. A workspace that PATCHes it should be refused rather
    than quietly storing a URL nothing reads."""
    ws = await _workspace("sbnocfg")
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://testserver") as c:
        await c.patch("/api/v1/intranet/config",
                      json={"sunburst": {"url": "https://evil.test/x"}},
                      headers={"Authorization": f"Bearer {ws['token']}",
                               "x-tenant-host": ws["host"]})
    # Either refused outright or ignored -- what must NOT happen is the portal serving it back.
    assert (await _config(ws))["sunburst"]["url"].startswith("https://app.sisu.co/app/sb/"), \
        "a workspace overrode the platform link"
