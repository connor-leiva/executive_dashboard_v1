"""The Sunburst link.

Sunburst ships with Sisu and Sisu ships with this product's customers, so it is part of the
platform: every workspace gets it, nothing is configured, and the link is derived per member.

What is worth defending is the derivation. The code is the only thing between a URL and somebody's
coaching conversation, so it has to be stable (or every visit starts a new thread), unique per
person (or two agents share one), and unguessable (or knowing the scheme and an id is enough).
"""
import datetime as dt
import re
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Business, Domain, IntranetMember, IntranetRole, Tenant, User
from app.security import hash_pw, make_token
from app.services import sunburst


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


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


def test_the_link_is_sisus_sunburst_url():
    link = sunburst.link_for(uuid.uuid4(), uuid.uuid4())
    assert link.startswith("https://app.sisu.co/app/sb/")
    assert len(link) == len(sunburst.BASE) + 32


def test_a_prompt_is_dropped_rather_than_appended_while_sisu_ignores_it():
    """A URL carrying a parameter the far end throws away looks like it worked. Until Sisu ships
    a link that takes the question, the portal copies it to the clipboard instead -- and
    `carries_prompt` is what tells it which."""
    plain = sunburst.link_for(uuid.uuid4(), uuid.uuid4())
    withq = sunburst.link_for(uuid.uuid4(), uuid.uuid4(), "Walk me through last week")
    assert "?" not in withq
    assert sunburst.carries_prompt() is False
    assert len(plain) == len(withq)


def test_turning_the_prompt_on_is_one_constant(monkeypatch):
    """The forward path Connor asked Sisu for. When they answer, this is the change."""
    monkeypatch.setattr(sunburst, "PROMPT_PARAM", "q")
    link = sunburst.link_for(uuid.uuid4(), uuid.uuid4(), "Where am I leaking deals?")
    assert link.endswith("?q=Where%20am%20I%20leaking%20deals%3F")
    assert sunburst.carries_prompt() is True


# ── what the portal receives ──────────────────────────────────────────────────────────────

async def test_every_workspace_gets_a_link_with_nothing_configured():
    """The whole point of the change: no console setting, no integration to connect."""
    ws = await _workspace("sbevery")
    config = await _config(ws)
    assert config["sunburst"]["url"].startswith("https://app.sisu.co/app/sb/")
    assert config["sunburst"]["carries_prompt"] is False


async def test_the_link_a_member_gets_is_their_own():
    a = await _workspace("sbmine")
    b = await _workspace("sbtheirs")
    assert (await _config(a))["sunburst"]["url"] != (await _config(b))["sunburst"]["url"]


async def test_the_same_member_gets_the_same_link_every_time():
    ws = await _workspace("sbagain")
    assert (await _config(ws))["sunburst"]["url"] == (await _config(ws))["sunburst"]["url"]


async def test_somebody_not_on_the_roster_gets_no_link_rather_than_somebody_elses():
    """An owner who never added themselves. There is no member to derive a code for, and the page
    says so instead of opening a conversation that belongs to nobody."""
    ws = await _workspace("sbnoroster", on_roster=False)
    assert (await _config(ws))["sunburst"]["url"] == ""


async def test_sunburst_is_not_a_tenant_setting_any_more():
    """It shipped as one for a few hours. A workspace that PATCHes it should be refused rather
    than quietly storing a URL nothing reads."""
    ws = await _workspace("sbnocfg")
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://testserver") as c:
        r = await c.patch("/api/v1/intranet/config",
                          json={"sunburst": {"url": "https://evil.test/x"}},
                          headers={"Authorization": f"Bearer {ws['token']}",
                                   "x-tenant-host": ws["host"]})
    # Either refused outright or ignored -- what must NOT happen is the portal serving it back.
    assert (await _config(ws))["sunburst"]["url"].startswith("https://app.sisu.co/app/sb/"), \
        "a workspace overrode the platform link"
