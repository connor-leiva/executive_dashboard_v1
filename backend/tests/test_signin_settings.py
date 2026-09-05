"""The sign-in screen's own settings, and the session lifetime behind "Remember me".

Two things are being asserted here.

FIRST, that "Remember me" is a promise rather than a label. Unticked, the session must actually
be short — the browser puts that token in sessionStorage so it dies with the tab, and a server
that handed out a month regardless would make the checkbox a decoration on a page that says
otherwise. The pair only means anything together.

SECOND, that the sign-in screen's settings survive the trip to the browser. Every one of them
reaches the page through /public/brand, which is filtered through BRAND_DEFAULTS — and an
undeclared key there is dropped silently. That has now cost four settings (`typeface`, `seeds`,
the marks, `hero_plates`), so the declaration is asserted rather than trusted.
"""
import datetime as dt

import pytest
from httpx import AsyncClient, ASGITransport
from jose import jwt
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Tenant
from app.seed import seed
from app.security import ALGO, SESSION_HOURS, SESSION_REMEMBERED_DAYS, make_token

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


async def _owner():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


def _hours_left(token: str) -> float:
    exp = jwt.decode(token, settings.APP_SECRET, algorithms=[ALGO])["exp"]
    return (dt.datetime.fromtimestamp(exp, dt.timezone.utc)
            - dt.datetime.now(dt.timezone.utc)).total_seconds() / 3600


# ── the promise ─────────────────────────────────────────────────────
async def test_an_unremembered_session_is_short():
    """The browser stores this one in sessionStorage. A month-long token there would outlive the
    tab only in the sense that anyone reopening the browser gets it back."""
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    left = _hours_left(r.json()["token"])
    assert SESSION_HOURS - 1 < left <= SESSION_HOURS


async def test_remembering_lasts_a_month():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime",
                               "remember": True})
    assert _hours_left(r.json()["token"]) > SESSION_REMEMBERED_DAYS * 24 - 1


async def test_an_older_client_that_sends_no_flag_gets_the_short_session():
    """Absent means unticked, which is the safer of the two readings — the field is new and a
    caller that has never heard of it should not be handed the longer session."""
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    assert _hours_left(r.json()["token"]) <= SESSION_HOURS


def test_make_token_defaults_to_the_short_session():
    import uuid
    assert _hours_left(make_token(uuid.uuid4(), uuid.uuid4())) <= SESSION_HOURS


# ── the settings ────────────────────────────────────────────────────
async def test_the_sign_in_settings_reach_the_browser():
    """/public/brand is what the sign-in page reads, and BRAND_DEFAULTS filters it. A key not
    declared there is dropped with no error anywhere — the failure mode that has already cost
    four separate settings."""
    from app.services.roles import BRAND_DEFAULTS, brand

    for key in ("tagline", "plate_side", "button_shape", "remember_me"):
        assert key in BRAND_DEFAULTS, f"{key} would be dropped on the way to the sign-in page"

    class _T:
        name = "Acme"
        config = {"brand": {"tagline": "Track production", "plate_side": "right",
                            "button_shape": "square", "remember_me": False}}

    out = brand(_T())
    assert out["tagline"] == "Track production"
    assert out["plate_side"] == "right"
    assert out["button_shape"] == "square"
    assert out["remember_me"] is False, "a false boolean must survive, not fall back to the default"


async def test_defaults_are_the_design_defaults():
    from app.services.roles import brand

    class _T:
        name = "Acme"
        config = {}

    out = brand(_T())
    assert out["plate_side"] == "left" and out["button_shape"] == "pill"
    assert out["remember_me"] is True
    assert out["tagline"] is None, "no tagline is a blank plate, never an invented claim"


async def test_saving_them_round_trips():
    token = await _owner()
    h = {"Authorization": f"Bearer {token}"}
    async with _client() as c:
        r = await c.patch("/api/v1/settings/appearance", headers=h, json={
            "tagline": "  Track   production,  not spreadsheets ",
            "plate_side": "right", "button_shape": "square", "remember_me": False})
        assert r.status_code == 200
        # Whitespace collapsed: a headline is one line, whatever was pasted in.
        assert r.json()["tagline"] == "Track production, not spreadsheets"
        back = (await c.get("/api/v1/settings/appearance", headers=h)).json()
    assert back["plate_side"] == "right" and back["button_shape"] == "square"
    assert back["remember_me"] is False


async def test_a_blank_tagline_clears_it():
    token = await _owner()
    h = {"Authorization": f"Bearer {token}"}
    async with _client() as c:
        await c.patch("/api/v1/settings/appearance", headers=h, json={"tagline": "Something"})
        await c.patch("/api/v1/settings/appearance", headers=h, json={"tagline": ""})
        back = (await c.get("/api/v1/settings/appearance", headers=h)).json()
    assert back["tagline"] == ""


async def test_only_the_known_values_are_accepted():
    """These become CSS on a page that renders before anyone has authenticated. Free text there
    is a workspace styling its own sign-in screen with whatever it likes."""
    token = await _owner()
    h = {"Authorization": f"Bearer {token}"}
    async with _client() as c:
        assert (await c.patch("/api/v1/settings/appearance", headers=h,
                              json={"plate_side": "middle"})).status_code == 400
        assert (await c.patch("/api/v1/settings/appearance", headers=h,
                              json={"button_shape": "blob"})).status_code == 400
        assert (await c.patch("/api/v1/settings/appearance", headers=h,
                              json={"remember_me": "yes"})).status_code == 400
        assert (await c.patch("/api/v1/settings/appearance", headers=h,
                              json={"tagline": "x" * 200})).status_code == 400


async def test_saving_a_sign_in_setting_does_not_discard_the_palette():
    """The third way that palette could have been destroyed. Saving a TYPEFACE already did it
    once — the endpoint popped `palette` on every save without asking what had moved — and every
    new field on this page is another chance to repeat it."""
    token = await _owner()
    h = {"Authorization": f"Bearer {token}"}
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        cfg = dict(t.config or {})
        cfg["brand"] = {**(cfg.get("brand") or {}), "palette": {"ink": "#111111"}}
        t.config = cfg
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(t, "config")
        await s.commit()

    async with _client() as c:
        await c.patch("/api/v1/settings/appearance", headers=h, json={"plate_side": "right"})

    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
    assert (t.config["brand"].get("palette") or {}).get("ink") == "#111111", (
        "a sign-in setting threw away the workspace's colours")
