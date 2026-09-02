import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Business, Domain, Tenant, User
from app.security import hash_pw, make_token

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token, host):
    return {"Authorization": f"Bearer {token}", "x-tenant-host": host}


async def _tenant(slug: str, *, intranet: bool):
    host = f"{slug}.localhost"
    async with SessionLocal() as s:
        existing = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
        if existing:
            users = (await s.execute(select(User).where(User.tenant_id == existing.id))).scalars().all()
            return host, existing, {u.email.split("@")[0]: make_token(u.id, existing.id, 0) for u in users}
        t = Tenant(
            slug=slug,
            name=slug.title(),
            status="active",
            config={"features": {"intranet": True}} if intranet else {},
        )
        s.add(t)
        await s.flush()
        s.add(Domain(tenant_id=t.id, hostname=host, is_primary=True))
        s.add(Business(tenant_id=t.id, key="main", name="Main", tag="Business", sort_order=0))
        users = [
            User(tenant_id=t.id, email=f"owner@{slug}.test", name="Owner",
                 password_hash=hash_pw("password123"), role="owner", status="active", token_version=0),
            User(tenant_id=t.id, email=f"admin@{slug}.test", name="Admin",
                 password_hash=hash_pw("password123"), role="admin", status="active", token_version=0),
            User(tenant_id=t.id, email=f"member@{slug}.test", name="Member",
                 password_hash=hash_pw("password123"), role="member", status="active",
                 tab_access=[], token_version=0),
        ]
        s.add_all(users)
        await s.commit()
        return host, t, {u.email.split("@")[0]: make_token(u.id, t.id, 0) for u in users}


async def test_intranet_is_a_tenant_module_not_a_tab_grant():
    host, _, tokens = await _tenant("intraon", intranet=True)
    off_host, _, off_tokens = await _tenant("intraoff", intranet=False)
    async with _client() as c:
        member = await c.get("/api/v1/intranet/config", headers=_H(tokens["member"], host))
        assert member.status_code == 200, member.text
        me = (await c.get("/api/v1/me", headers=_H(tokens["member"], host))).json()
        assert "intranet" in {a["id"] for a in me["apps"]}
        assert me["tabs"] == []

        disabled = await c.get("/api/v1/intranet/config", headers=_H(off_tokens["owner"], off_host))
        assert disabled.status_code == 403
        me_off = (await c.get("/api/v1/me", headers=_H(off_tokens["owner"], off_host))).json()
        assert "intranet" not in {a["id"] for a in me_off["apps"]}


async def test_intranet_config_is_admin_only_and_tenant_scoped():
    host, _, tokens = await _tenant("intracfg", intranet=True)
    async with _client() as c:
        patch = {
            "calendar": {"google_calendar_url": "https://calendar.google.com/calendar/embed?src=test"},
            "marketing_requests": {"url": "https://forms.example.com/request", "label": "Request form"},
        }
        assert (await c.patch("/api/v1/intranet/config", headers=_H(tokens["member"], host),
                              json=patch)).status_code == 403
        saved = await c.patch("/api/v1/intranet/config", headers=_H(tokens["admin"], host),
                              json=patch)
        assert saved.status_code == 200, saved.text
        cfg = (await c.get("/api/v1/intranet/config", headers=_H(tokens["member"], host))).json()["config"]
        assert cfg["calendar"]["google_calendar_url"].startswith("https://calendar.google.com/")
        assert cfg["marketing_requests"]["url"] == "https://forms.example.com/request"

        bad = await c.patch("/api/v1/intranet/config", headers=_H(tokens["admin"], host),
                            json={"calendar": {"google_calendar_url": "https://example.com/calendar"}})
        assert bad.status_code == 400


async def test_win_the_day_state_resets_by_user_and_local_date():
    host, _, tokens = await _tenant("intrastate", intranet=True)
    other_host, _, other_tokens = await _tenant("intrastateb", intranet=True)
    payload = {
        "state_key": "2026-09-02",
        "timezone": "America/Chicago",
        "value": {"checked": {"power:Review priorities": True}, "tallies": {"calls": 7}},
    }
    async with _client() as c:
        saved = await c.put("/api/v1/intranet/state/wtd", headers=_H(tokens["member"], host),
                            json=payload)
        assert saved.status_code == 200, saved.text
        same = (await c.get("/api/v1/intranet/state/wtd?state_key=2026-09-02",
                            headers=_H(tokens["member"], host))).json()
        assert same["value"]["tallies"]["calls"] == 7

        next_day = (await c.get("/api/v1/intranet/state/wtd?state_key=2026-09-03",
                               headers=_H(tokens["member"], host))).json()
        assert next_day["value"] == {}
        same_tenant_other_user = (await c.get("/api/v1/intranet/state/wtd?state_key=2026-09-02",
                                             headers=_H(tokens["owner"], host))).json()
        assert same_tenant_other_user["value"] == {}
        other_tenant = (await c.get("/api/v1/intranet/state/wtd?state_key=2026-09-02",
                                   headers=_H(other_tokens["member"], other_host))).json()
        assert other_tenant["value"] == {}
