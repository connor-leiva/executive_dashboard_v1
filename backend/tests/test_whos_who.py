"""Who's Who as the workspace sets it up (WHOS-WHO-WIN-THE-DAY-SPEC.md, phases 4 and 5).

The directory was a flat list of whoever had signed in, with photos that could never display and
profile fields no screen could edit. These hold the replacement to the spec: a profile field the
page cannot draw is refused with the field named; everyone on the roster is listed unless removed
or hidden (D1), a hidden person is absent everywhere; the featured person appears once; leadership
keeps its chosen order; stats and the intro count the team; a photo is re-encoded before it is
stored (D7); and a profile shows what its person owns, SOPs included, to those who can open them.
"""
import datetime as dt
import io

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import (Business, Domain, IntranetCapability, IntranetMember, IntranetPermission,
                        IntranetRole, IntranetSop, IntranetSopCategory, IntranetSopVersion, Tenant,
                        User)
from app.security import hash_pw, make_token
from app.services import binder_storage, whos_who
from app.services.intranet_bootstrap import bootstrap_intranet

ASGI = httpx.ASGITransport(app=app)
NOW = dt.datetime.now(dt.timezone.utc)


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
        t = Tenant(slug=slug, name=slug.title(), status="active", plan="portfolio",
                   config={"features": {"intranet": True}})
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
        return {"tenant_id": t.id, "host": f"{slug}.localhost", "roles": roles,
                "owner": make_token(owner.id, t.id, 0)}


async def _person(ws, name, *, role="member", status="Active", account=True, **fields) -> dict:
    async with SessionLocal() as s:
        email = f"{name.split()[0].lower()}@{ws['host']}"
        user_id = None
        token = None
        if account:
            u = User(tenant_id=ws["tenant_id"], email=email, name=name, password_hash=hash_pw("pw"),
                     role="member", status="active", tab_access=[], token_version=0)
            s.add(u)
            await s.flush()
            user_id = u.id
            token = make_token(u.id, ws["tenant_id"], 0)
        m = IntranetMember(tenant_id=ws["tenant_id"], full_name=name, email=email,
                           role_id=ws["roles"][role], status=status, auth_source="Manual",
                           user_id=user_id, **fields)
        s.add(m)
        await s.commit()
        return {"id": str(m.id), "token": token}


async def _directory(c, ws, token):
    r = await c.get("/api/v1/intranet/config", headers=_H(token, ws["host"]))
    assert r.status_code == 200, r.text
    return r.json()["config"]["content"]["directory"]


def _names(cards):
    return [p["name"] for p in cards]


def _png(size=(3000, 1000), alpha=True) -> bytes:
    img = Image.new("RGBA" if alpha else "RGB", size, (20, 40, 60, 0) if alpha else (20, 40, 60))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def _jpeg_with_gps() -> bytes:
    img = Image.new("RGB", (800, 600), (200, 100, 50))
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"                     # Make
    exif[0x8825] = {1: "N", 2: (40.0, 45.0, 0.0)}   # GPSInfo: somewhere in Utah
    out = io.BytesIO()
    img.save(out, "JPEG", exif=exif)
    return out.getvalue()


def _png_header(width: int, height: int) -> bytes:
    """A PNG that only CLAIMS a size: a real header and a token of image data. Enough for the
    size check, which must refuse it before anything is decoded."""
    import struct
    import zlib

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(64)))
            + chunk(b"IEND", b""))


# ── what a profile may say ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field,value,where", [
    ("message_url", "javascript:alert(1)", "message_url"),
    ("owns_items", [{"label": "Growth planning", "url": "ftp://files"}], "owns_items.0.url"),
    ("photo_focus", "150% 0%", "photo_focus"),
    ("bring", ["a"] * 7, "bring"),
    ("pronoun", "xe", "pronoun"),
    ("tag", "x" * 25, "tag"),
    ("directory_placement", "sidebar", "directory_placement"),
])
def test_a_profile_field_the_page_cannot_draw_is_refused(field, value, where):
    with pytest.raises(whos_who.ProfileError) as e:
        whos_who.clean_field(field, value)
    assert e.value.field == where


def test_the_messages_a_profile_can_send_to():
    for ok in ("https://slack.com/app_redirect?channel=U123", "mailto:spring@example.test",
               "sms:+1 801 555 0100", "tel:+18015550100", "slack://user?team=T1&id=U2"):
        assert whos_who.clean_field("message_url", ok) == ok
    assert whos_who.clean_field("owns_items", [{"label": "All-team Meeting", "url": "/calendar"}])


def test_volume_reads_as_a_figure_on_a_band():
    assert whos_who.money_short(268_000_000) == "$268M"
    assert whos_who.money_short(9_400_000) == "$9.4M"
    assert whos_who.money_short(241_800) == "$242k"


# ── who goes where ─────────────────────────────────────────────────────────────────────────

async def test_everyone_but_the_removed_and_hidden_the_featured_person_once():
    ws = await _workspace("whoplace")
    spring = await _person(ws, "Spring Leader", role="owner", title="Team Leader",
                           headline="Associate Broker")
    jamie = await _person(ws, "Jamie Lead", role="manager", title="Team Leader", market="SLC")
    zoe = await _person(ws, "Zoe Agent", title="Buyer Agent")
    held = await _person(ws, "Held Agent", status="Invited", account=False)
    hidden = await _person(ws, "Hidden Owner", role="owner", directory_placement="hidden")
    await _person(ws, "Gone Agent", status="Removed", account=False)
    async with _client() as c:
        h = _H(ws["owner"], ws["host"])
        r = await c.patch("/api/console/directory", headers=h,
                          json={"featured_member_id": spring["id"], "featured_label": "Team Leader"})
        assert r.status_code == 200, r.text
        refused = await c.patch("/api/console/directory", headers=h,
                                json={"featured_member_id": hidden["id"]})
        d = await _directory(c, ws, zoe["token"])
        hidden_profile = await c.get(f"/api/v1/intranet/directory/{hidden['id']}",
                                     headers=_H(zoe["token"], ws["host"]))
        held_profile = await c.get(f"/api/v1/intranet/directory/{held['id']}",
                                   headers=_H(zoe["token"], ws["host"]))
    assert refused.status_code == 422, "a hidden person was featured"
    assert d["featured"]["name"] == "Spring Leader" and d["featured"]["eyebrow"] == "Team Leader"
    assert d["featured"]["headline"] == "Associate Broker"
    # The bootstrap owner is on the roster too, a leadership role on auto.
    assert "Jamie Lead" in _names(d["leadership"])
    assert "Spring Leader" not in _names(d["leadership"]) + _names(d["agents"]), \
        "the featured person appears twice"
    assert _names(d["agents"]) == ["Held Agent", "Zoe Agent"]
    everyone = _names(d["leadership"]) + _names(d["agents"]) + [d["featured"]["name"]]
    assert "Hidden Owner" not in everyone and "Gone Agent" not in everyone
    assert d["team_size"] == len(everyone)
    assert hidden_profile.status_code == 404, "a hidden person's profile was served"
    assert held_profile.status_code == 200, "an invited colleague's profile was refused"
    assert next(p for p in d["agents"] if p["name"] == "Zoe Agent")["is_you"] is True
    assert jamie  # listed by role, placed by nobody


async def test_leadership_keeps_its_chosen_order():
    ws = await _workspace("whoorder")
    a = await _person(ws, "Aaron Lead", role="manager")
    b = await _person(ws, "Bea Lead", role="manager")
    viewer = await _person(ws, "Cal Agent")
    async with _client() as c:
        h = _H(ws["owner"], ws["host"])
        before = _names((await _directory(c, ws, viewer["token"]))["leadership"])
        r = await c.put("/api/console/directory/leadership/order", headers=h,
                        json={"ids": [b["id"], a["id"]]})
        assert r.status_code == 200, r.text
        after = _names((await _directory(c, ws, viewer["token"]))["leadership"])
    assert before.index("Aaron Lead") < before.index("Bea Lead")
    assert after.index("Bea Lead") < after.index("Aaron Lead")


async def test_the_console_says_where_automatic_would_put_someone():
    ws = await _workspace("whoauto")
    lead = await _person(ws, "Dana Lead", role="manager")
    await _person(ws, "Eli Agent")
    async with _client() as c:
        h = _H(ws["owner"], ws["host"])
        moved = await c.patch(f"/api/console/members/{lead['id']}", headers=h,
                              json={"directory_placement": "agents"})
        assert moved.status_code == 200, moved.text
        people = {p["name"]: p for p in (await c.get("/api/console/directory", headers=h)).json()["people"]}
    # Moved into Agents by hand, while Automatic would still mean Leadership. The console's
    # "Automatic (...)" option names where Automatic puts them, not where they sit now -- it
    # read "Automatic (agents)" for a manager, which is exactly what Automatic would not do.
    assert people["Dana Lead"]["shown_in"] == "agents"
    assert people["Dana Lead"]["auto_shown_in"] == "leadership"
    assert people["Eli Agent"]["auto_shown_in"] == "agents"


async def test_the_intro_and_stats_count_the_listed_team():
    ws = await _workspace("whostats")
    viewer = await _person(ws, "Dee Agent")
    await _person(ws, "Eli Agent")
    await _person(ws, "Hid Agent", directory_placement="hidden")
    async with _client() as c:
        h = _H(ws["owner"], ws["host"])
        r = await c.patch("/api/console/directory", headers=h, json={
            "intro": "{N} agents, one team.",
            "stats": [{"label": "Units, year to date", "source": "manual", "value": "612"},
                      {"label": "Agents on the team", "source": "team_size"}],
            "preview_count": 12})
        assert r.status_code == 200, r.text
        sisu = await c.patch("/api/console/directory", headers=h, json={
            "stats": [{"label": "Volume", "source": "sisu_volume_ytd"}]})
        d = await _directory(c, ws, viewer["token"])
    assert sisu.status_code == 422, "a Sisu total was offered with no Sisu connected"
    size = d["team_size"]
    assert d["intro"] == whos_who.fill("{N} agents, one team.", size)
    assert d["stats"] == [{"label": "Units, year to date", "value": "612"},
                          {"label": "Agents on the team", "value": str(size)}]
    assert d["preview_count"] == 12
    assert "Hid Agent" not in _names(d["agents"])


# ── photos ─────────────────────────────────────────────────────────────────────────────────

def _card(directory: dict, name: str) -> dict:
    everyone = [directory.get("featured"), *directory["leadership"], *directory["agents"]]
    return next(p for p in everyone if p and p["name"] == name)


async def test_a_photo_is_reencoded_before_it_is_stored():
    ws = await _workspace("whophoto")
    zoe = await _person(ws, "Zoe Agent")
    async with _client() as c:
        h = _H(ws["owner"], ws["host"])
        png = await c.post(f"/api/console/members/{zoe['id']}/photo", headers=h,
                           files={"file": ("cutout.png", _png(), "image/png")})
        assert png.status_code == 200, png.text
        assert png.json()["item"]["has_photo"] is True
        served = await c.get(f"/api/v1/intranet/directory/{zoe['id']}/photo",
                             headers=_H(zoe["token"], ws["host"]))
        first_url = _card(await _directory(c, ws, zoe["token"]), "Zoe Agent")["photo_url"]
        gps = await c.post(f"/api/console/members/{zoe['id']}/photo", headers=h,
                           files={"file": ("phone.jpg", _jpeg_with_gps(), "image/jpeg")})
        served_gps = await c.get(f"/api/v1/intranet/directory/{zoe['id']}/photo",
                                 headers=_H(zoe["token"], ws["host"]))
        second_url = _card(await _directory(c, ws, zoe["token"]), "Zoe Agent")["photo_url"]
        preview = await c.get(f"/api/console/members/{zoe['id']}/photo", headers=h)
        text = await c.post(f"/api/console/members/{zoe['id']}/photo", headers=h,
                            files={"file": ("photo.png", b"not an image at all", "image/png")})
        removed = await c.delete(f"/api/console/members/{zoe['id']}/photo", headers=h)
        gone = await c.get(f"/api/v1/intranet/directory/{zoe['id']}/photo",
                           headers=_H(zoe["token"], ws["host"]))
    img = Image.open(io.BytesIO(served.content))
    assert served.headers["content-type"].startswith("image/jpeg")
    assert img.format == "JPEG" and img.mode == "RGB", "transparency was kept"
    assert max(img.size) == 2000, "an oversized photo was stored as it came"
    stripped = Image.open(io.BytesIO(served_gps.content))
    assert not stripped.getexif(), "a phone photo's EXIF (and its GPS) reached the portal"
    # The portal caches a photo for a few minutes, so its address must change with the photo;
    # the console's preview is not cached at all.
    assert first_url != second_url, "a replaced photo kept its address, so the old one is cached"
    assert preview.headers["cache-control"] == "no-store"
    assert text.status_code == 422, "a file that is not an image was stored"
    assert removed.status_code == 200 and removed.json()["item"]["has_photo"] is False
    assert gone.status_code == 404


def test_a_huge_image_is_refused_before_it_is_decoded():
    # 56 megapixels: over the limit, under Pillow's own warning. 400: Pillow's bomb guard.
    for w, h in ((8000, 7000), (20000, 20000)):
        with pytest.raises(whos_who.ProfileError) as refused:
            whos_who.process_photo(_png_header(w, h))
        assert "too large" in refused.value.message, (w, h)


def test_a_big_phone_photo_is_decoded_small():
    big = io.BytesIO()
    Image.new("RGB", (4000, 3000), (90, 120, 150)).save(big, "JPEG", quality=80)
    out = Image.open(io.BytesIO(whos_who.process_photo(big.getvalue())))
    assert out.size == (2000, 1500)


# ── the profile ────────────────────────────────────────────────────────────────────────────

async def test_a_profile_says_what_its_person_owns_to_those_who_can_open_it():
    ws = await _workspace("whoowns")
    lead = await _person(ws, "Spring Leader", role="manager")
    viewer = await _person(ws, "Zoe Agent")
    async with SessionLocal() as s:
        cat = IntranetSopCategory(tenant_id=ws["tenant_id"], name="Referrals", sort=0,
                                  published_at=NOW)
        s.add(cat)
        await s.flush()
        sop = IntranetSop(tenant_id=ws["tenant_id"], title="Referring out of area",
                          category_id=cat.id, owner_member_id=lead["id"], state="Live",
                          published_at=NOW)
        s.add(sop)
        await s.flush()
        version = IntranetSopVersion(tenant_id=ws["tenant_id"], sop_id=sop.id, version_label="v1.0",
                                     filename="r.pdf", storage_key="k", content_type="application/pdf",
                                     byte_size=10, published_at=NOW)
        s.add(version)
        await s.flush()
        sop.current_version_id = version.id
        await s.commit()
        sop_id = str(sop.id)
    async with _client() as c:
        h = _H(ws["owner"], ws["host"])
        r = await c.patch(f"/api/console/members/{lead['id']}", headers=h, json={
            "pronoun": "she", "quote": "Confidence and certainty.", "office": "Head office",
            "bring": ["A goal you want to raise."], "message_url": "",
            "owns_items": [{"label": "All-team Meeting · Thursdays", "url": "/calendar"}]})
        assert r.status_code == 200, r.text
        profile = (await c.get(f"/api/v1/intranet/directory/{lead['id']}",
                               headers=_H(viewer["token"], ws["host"]))).json()
    assert profile["headings"] == {"bring": "Bring Her", "reach": "Reach Her", "owns": "She Owns"}
    assert profile["owns_items"][0] == {"label": "Referring out of area · SOP v1.0",
                                        "url": f"/sops#sop-{sop_id}", "kind": "sop"}
    assert profile["owns_items"][1]["url"] == "/calendar"
    assert profile["message_url"] == f"mailto:{profile['email']}", "Message had nowhere to go"
    assert profile["bring"] == ["A goal you want to raise."] and profile["quote"]

    # A role that cannot open the SOP library is not handed a door into it.
    async with SessionLocal() as s:
        cap = (await s.execute(select(IntranetCapability).where(
            IntranetCapability.tenant_id == ws["tenant_id"],
            IntranetCapability.key == "sop_library"))).scalar_one()
        perm = (await s.execute(select(IntranetPermission).where(
            IntranetPermission.tenant_id == ws["tenant_id"],
            IntranetPermission.capability_id == cap.id,
            IntranetPermission.role_id == ws["roles"]["member"]))).scalar_one()
        perm.level = "None"
        await s.commit()
    async with _client() as c:
        denied = (await c.get(f"/api/v1/intranet/directory/{lead['id']}",
                              headers=_H(viewer["token"], ws["host"]))).json()
    assert [o["kind"] for o in denied["owns_items"]] == ["link"]


async def test_a_bad_profile_field_is_refused_by_the_console():
    ws = await _workspace("whobad")
    zoe = await _person(ws, "Zoe Agent")
    async with _client() as c:
        r = await c.patch(f"/api/console/members/{zoe['id']}", headers=_H(ws["owner"], ws["host"]),
                          json={"message_url": "javascript:alert(1)"})
    assert r.status_code == 422
    assert r.json()["detail"]["errors"][0]["field"] == "message_url"
