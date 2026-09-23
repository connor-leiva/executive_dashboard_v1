"""My Settings: what a person may change about their own roster entry.

Until this, a member could change nothing about themselves. A headshot, a pronoun, a headline from
a job they no longer do -- each one was a message to whoever holds console access, and in a
workspace of eighty-six that is a queue nobody wants to own.

The line these hold is the one that matters: a profile field DESCRIBES a colleague, so its subject
is the best source for it; where somebody appears in the directory is the workspace presenting
itself, so it stays with an admin. And every route is scoped to the member the SESSION resolves
to, never to an id in the request -- there is no other entry to reach, by construction.
"""
import datetime as dt
import io
import uuid

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Business, Domain, IntranetMember, IntranetRole, Tenant, User
from app.security import hash_pw, make_token, make_view_token
from app.services import whos_who
from app.services.intranet_bootstrap import bootstrap_intranet

ASGI = httpx.ASGITransport(app=app)
ME = "/api/v1/intranet/me/profile"


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


def _client():
    return httpx.AsyncClient(transport=ASGI, base_url="http://testserver")


def _H(token, host):
    return {"Authorization": f"Bearer {token}", "x-tenant-host": host}


async def _workspace(slug: str) -> dict:
    async with SessionLocal() as s:
        t = Tenant(slug=slug, name=slug.title(), status="active", plan="portfolio")
        s.add(t)
        await s.flush()
        s.add(Domain(tenant_id=t.id, hostname=f"{slug}.localhost", is_primary=True))
        s.add(Business(tenant_id=t.id, key="main", name="Main", tag="Business", sort_order=0))
        owner = User(tenant_id=t.id, email=f"owner@{slug}.test", name="Owner",
                     password_hash=hash_pw("pw"), role="owner", status="active", tab_access=[],
                     token_version=0)
        s.add(owner)
        await s.flush()
        await bootstrap_intranet(s, t.id, workspace_name=slug.title(), subdomain=slug,
                                 owner_email=owner.email)
        await s.commit()
        roles = {r.key: r.id for r in (await s.execute(select(IntranetRole).where(
            IntranetRole.tenant_id == t.id))).scalars()}
        return {"tid": t.id, "host": f"{slug}.localhost", "roles": roles, "slug": slug}


async def _person(ws, name, *, on_roster=True, **fields) -> dict:
    async with SessionLocal() as s:
        email = f"{name.split()[0].lower()}@{ws['host']}"
        u = User(tenant_id=ws["tid"], email=email, name=name, password_hash=hash_pw("pw"),
                 role="member", status="active", tab_access=[], token_version=0)
        s.add(u)
        await s.flush()
        mid = None
        if on_roster:
            m = IntranetMember(tenant_id=ws["tid"], full_name=name, email=email,
                               role_id=ws["roles"]["member"], status="Active",
                               auth_source="Manual", user_id=u.id, **fields)
            s.add(m)
            await s.flush()
            mid = m.id
        await s.commit()
        return {"id": mid, "user_id": u.id, "email": email,
                "token": make_token(u.id, ws["tid"], 0)}


def _png(size=(80, 80), colour=(10, 120, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


# ── the split itself ──────────────────────────────────────────────────────────────────────

def test_every_profile_field_is_either_theirs_or_an_admins():
    """The decision is forced, not defaulted. A field added to PROFILE_FIELDS and to neither set
    fails here, so whoever adds it has to say which it is -- the alternative is a new field
    quietly becoming self-service because the allowlist was written as a subtraction."""
    undecided = whos_who.PROFILE_FIELDS - whos_who.SELF_SERVICE_FIELDS - whos_who.ADMIN_ONLY_FIELDS
    assert not undecided, (
        f"these profile fields belong to nobody yet: {sorted(undecided)}. Add each to "
        "whos_who.SELF_SERVICE_FIELDS or whos_who.ADMIN_ONLY_FIELDS.")
    both = whos_who.SELF_SERVICE_FIELDS & whos_who.ADMIN_ONLY_FIELDS
    assert not both, f"claimed by both: {sorted(both)}"
    assert whos_who.SELF_SERVICE_FIELDS <= whos_who.PROFILE_FIELDS
    assert set(whos_who.COLUMN_LIMITS) == whos_who.SELF_SERVICE_COLUMNS, \
        "every self-service column needs a length, or an over-long value reaches Postgres"


def test_the_things_that_decide_where_you_appear_are_not_yours():
    assert "directory_placement" in whos_who.ADMIN_ONLY_FIELDS
    assert "directory_order" in whos_who.ADMIN_ONLY_FIELDS
    assert "email" not in whos_who.SELF_SERVICE_COLUMNS, "email is how they sign in"


# ── reading and writing your own ──────────────────────────────────────────────────────────

async def test_a_member_reads_and_edits_their_own_profile():
    ws = await _workspace(f"mine{uuid.uuid4().hex[:6]}")
    me = await _person(ws, "Ada Agent", headline="Buyer agent")

    async with _client() as c:
        r = await c.get(ME, headers=_H(me["token"], ws["host"]))
        assert r.status_code == 200, r.text
        assert r.json()["headline"] == "Buyer agent"
        assert r.json()["email"] == me["email"]
        assert r.json()["photo_url"] is None
        assert r.json()["limits"]["headline"] == whos_who.TEXT_LIMITS["headline"]

        r = await c.patch(ME, headers=_H(me["token"], ws["host"]), json={
            "headline": "Listing agent", "pronoun": "she", "phone": "801-555-0100",
            "bio": "Ten years in Salt Lake.", "full_name": "Ada A. Agent"})
        assert r.status_code == 200, r.text

    async with SessionLocal() as s:
        row = await s.get(IntranetMember, me["id"])
        assert (row.headline, row.pronoun, row.phone) == ("Listing agent", "she", "801-555-0100")
        assert row.full_name == "Ada A. Agent"


async def test_an_admin_only_field_is_refused_by_name_rather_than_dropped():
    """Silently ignoring what somebody typed teaches them the product is broken. The refusal says
    which field and where it is set."""
    ws = await _workspace(f"deny{uuid.uuid4().hex[:6]}")
    me = await _person(ws, "Bo Agent", directory_placement="agents")

    async with _client() as c:
        r = await c.patch(ME, headers=_H(me["token"], ws["host"]),
                          json={"headline": "Fine", "directory_placement": "leadership"})
    assert r.status_code == 422
    assert "directory_placement" in r.json()["detail"]

    async with SessionLocal() as s:
        row = await s.get(IntranetMember, me["id"])
        assert row.directory_placement == "agents", "the refused write must change nothing"
        assert row.headline is None, "and must not half-apply the fields it did allow"


async def test_a_field_is_validated_the_same_way_an_admins_edit_is():
    ws = await _workspace(f"long{uuid.uuid4().hex[:6]}")
    me = await _person(ws, "Cy Agent")
    async with _client() as c:
        r = await c.patch(ME, headers=_H(me["token"], ws["host"]),
                          json={"headline": "x" * (whos_who.TEXT_LIMITS["headline"] + 1)})
        assert r.status_code == 422 and "headline" in r.json()["detail"]

        # SQLite would take an over-long name and Postgres would not: the length is checked here.
        r = await c.patch(ME, headers=_H(me["token"], ws["host"]), json={"full_name": "y" * 201})
        assert r.status_code == 422 and "full_name" in r.json()["detail"]

        r = await c.patch(ME, headers=_H(me["token"], ws["host"]), json={"full_name": "   "})
        assert r.status_code == 422, "a person must have a name; the directory reads it"


async def test_you_can_only_ever_edit_yourself():
    """No id is taken from the request, so there is nothing to point at somebody else. This holds
    the property rather than the absence of a parameter."""
    ws = await _workspace(f"other{uuid.uuid4().hex[:6]}")
    me = await _person(ws, "Dee Agent")
    them = await _person(ws, "Eli Agent", headline="Untouched")

    async with _client() as c:
        r = await c.patch(ME, headers=_H(me["token"], ws["host"]), json={"headline": "Mine"})
    assert r.status_code == 200

    async with SessionLocal() as s:
        assert (await s.get(IntranetMember, them["id"])).headline == "Untouched"
        assert (await s.get(IntranetMember, me["id"])).headline == "Mine"


async def test_somebody_not_on_the_roster_is_told_why_rather_than_404d():
    ws = await _workspace(f"noros{uuid.uuid4().hex[:6]}")
    nobody = await _person(ws, "Fay Nobody", on_roster=False)
    async with _client() as c:
        r = await c.get(ME, headers=_H(nobody["token"], ws["host"]))
    assert r.status_code == 409
    assert "roster" in r.json()["detail"].lower()


# ── the photo ─────────────────────────────────────────────────────────────────────────────

async def test_a_photo_is_re_encoded_stored_and_can_be_taken_down_again():
    ws = await _workspace(f"photo{uuid.uuid4().hex[:6]}")
    me = await _person(ws, "Gus Agent")

    async with _client() as c:
        r = await c.post("/api/v1/intranet/me/photo", headers=_H(me["token"], ws["host"]),
                         files={"file": ("me.png", _png(), "image/png")})
        assert r.status_code == 200, r.text
        assert r.json()["photo_url"] and "?v=" in r.json()["photo_url"]

        async with SessionLocal() as s:
            key = (await s.get(IntranetMember, me["id"])).photo_key
        assert key and key.endswith(".jpg"), "stored re-encoded as JPEG, never as sent"

        r = await c.delete("/api/v1/intranet/me/photo", headers=_H(me["token"], ws["host"]))
        assert r.status_code == 200 and r.json()["photo_url"] is None

    async with SessionLocal() as s:
        assert (await s.get(IntranetMember, me["id"])).photo_key is None


async def test_a_file_that_is_not_an_image_is_refused():
    ws = await _workspace(f"notimg{uuid.uuid4().hex[:6]}")
    me = await _person(ws, "Hal Agent")
    async with _client() as c:
        r = await c.post("/api/v1/intranet/me/photo", headers=_H(me["token"], ws["host"]),
                         files={"file": ("me.png", b"MZ\x00\x00 not an image", "image/png")})
    assert r.status_code == 422, "checked by its bytes, not by what it calls itself"


# ── the guard that matters most ───────────────────────────────────────────────────────────

async def test_an_axcion_support_session_cannot_edit_the_person_it_is_looking_as():
    """Support access opens the portal AS a member, read-only, and the member is never told. If a
    view-as session could write a profile, an operator could change someone's identity invisibly.
    deps.current_user refuses every non-GET for these tokens; this pins that it covers the new
    routes too, which is the whole reason they are safe without a check of their own.
    """
    ws = await _workspace(f"support{uuid.uuid4().hex[:6]}")
    me = await _person(ws, "Ivy Agent", headline="Hers")

    async with SessionLocal() as s:
        sup = User(tenant_id=ws["tid"], email=f"support@{ws['slug']}.test", name="Axcion Support",
                   role="member", status="active", tab_access=[], token_version=0,
                   expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=30))
        s.add(sup)
        await s.commit()
        token = make_view_token(sup.id, ws["tid"], 0, me["id"], sup.expires_at)

    async with _client() as c:
        seen = await c.get(ME, headers=_H(token, ws["host"]))
        wrote = await c.patch(ME, headers=_H(token, ws["host"]), json={"headline": "Changed"})
        photo = await c.post("/api/v1/intranet/me/photo", headers=_H(token, ws["host"]),
                             files={"file": ("x.png", _png(), "image/png")})

    assert seen.status_code == 200 and seen.json()["headline"] == "Hers", "reading is the point"
    assert wrote.status_code == 403 and photo.status_code == 403

    async with SessionLocal() as s:
        row = await s.get(IntranetMember, me["id"])
        assert row.headline == "Hers" and row.photo_key is None
