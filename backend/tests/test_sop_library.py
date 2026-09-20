"""The SOP library as a procedure you can read (SOP-LIBRARY-SPEC.md, phases 1-4).

An SOP was an uploaded file with a title on it: members could download it and nothing else, the
console could not write one, and "Publish" did not hold an edit back. These hold the replacement.

The ones that are corrections rather than features are marked with the fault they close: the
procedure waits for Publish (D2/F1), an uploaded document is checked before it is stored (F2),
archiving can be undone (F3), and somebody can see who has read the thing (F4).
"""
import datetime as dt
import io
import struct
import zlib

import httpx
import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import (Business, Domain, IntranetLaunchpadTile, IntranetLaunchpadTileRole,
                        IntranetMember, IntranetRole, IntranetSop, IntranetSopCategory, Tenant,
                        User)
from app.security import hash_pw, make_token
from app.services import intranet_assistant, sop_library
from app.services.intranet_bootstrap import bootstrap_intranet

ASGI = httpx.ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


def _client():
    return httpx.AsyncClient(transport=ASGI, base_url="http://testserver")


def _H(token, host):
    return {"Authorization": f"Bearer {token}", "x-tenant-host": host}


def _pdf(size: int = 400) -> bytes:
    return b"%PDF-1.4" + b"\x00" * size


def _docx() -> bytes:
    """A zip that names Word, which is what a .docx is."""
    def chunk(name: bytes) -> bytes:
        return (struct.pack("<IHHHHHIIIHH", 0x04034b50, 20, 0, 0, 0, 0, 0, 0, 0, len(name), 0)
                + name)
    return chunk(b"[Content_Types].xml") + chunk(b"word/document.xml") + zlib.compress(b"x" * 64)


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


async def _person(ws, name, *, role="member", **fields) -> dict:
    async with SessionLocal() as s:
        email = f"{name.split()[0].lower()}@{ws['host']}"
        u = User(tenant_id=ws["tenant_id"], email=email, name=name, password_hash=hash_pw("pw"),
                 role="member", status="active", tab_access=[], token_version=0)
        s.add(u)
        await s.flush()
        m = IntranetMember(tenant_id=ws["tenant_id"], full_name=name, email=email,
                           role_id=ws["roles"][role], status="Active", auth_source="Manual",
                           user_id=u.id, **fields)
        s.add(m)
        await s.commit()
        return {"id": str(m.id), "token": make_token(u.id, ws["tenant_id"], 0), "email": email}


async def _category(ws, name: str) -> str:
    async with SessionLocal() as s:
        row = IntranetSopCategory(tenant_id=ws["tenant_id"], name=name, sort=0)
        s.add(row)
        await s.commit()
        return str(row.id)


async def _sop(c, ws, **body) -> dict:
    r = await c.post("/api/console/sops", headers=_H(ws["owner"], ws["host"]), json=body)
    assert r.status_code == 200, r.text
    return r.json()["item"]


async def _content(c, ws, token) -> dict:
    r = await c.get("/api/v1/intranet/config", headers=_H(token, ws["host"]))
    assert r.status_code == 200, r.text
    return r.json()["config"]["content"]


BODY = {
    "intro": "From the seller saying yes to the sign coming down.",
    "steps": [{"title": "Log the appointment", "text": "In Sisu, the same day."},
              {"title": "Send the packet", "text": "The current one, from the Drive."}],
    "callout": {"label": "Do Not Skip", "text": "Nothing goes to the MLS unsigned."},
}


# ── what a procedure may say ───────────────────────────────────────────────────────────────

def test_a_procedure_is_checked_before_it_is_stored():
    clean = sop_library.clean_body(BODY)
    assert [s["title"] for s in clean["steps"]] == ["Log the appointment", "Send the packet"]
    assert clean["callout"]["label"] == "Do Not Skip"
    # Nothing written at all is nothing stored, not an empty shell.
    assert sop_library.clean_body({"intro": "", "steps": [], "callout": {}}) is None
    # The editor's blank row is dropped; a step with words and no title is refused, because the
    # step list is also the page's contents and a nameless entry cannot appear in it.
    assert sop_library.clean_body({"steps": [{"title": "", "text": ""}]}) is None
    with pytest.raises(sop_library.SopError) as bad:
        sop_library.clean_body({"steps": [{"title": "", "text": "do the thing"}]})
    assert bad.value.field == "steps.0.title"
    with pytest.raises(sop_library.SopError) as long:
        sop_library.clean_body({"steps": [{"title": "x" * 200, "text": ""}]})
    assert long.value.field == "steps.0.title"


def test_the_assistant_is_handed_the_written_procedure_not_a_file_it_cannot_read():
    """D10. An uploaded document is still off limits; a written one it may answer from."""
    written = intranet_assistant.corpus({"sops": [
        {"id": "1", "title": "Intake", "steps": ["Log the appointment"],
         "body_text": sop_library.body_text(sop_library.clean_body(BODY))}]})[0]
    assert "Log the appointment" in written["facts"]["procedure"]
    assert written["ref"] == "/sops/1", "a procedure should cite its own page"
    filed = intranet_assistant.corpus({"sops": [
        {"id": "2", "title": "Filed", "file_url": "/intranet/sops/2/file"}]})[0]
    assert filed["facts"]["procedure"] is None
    assert "NOT available" in filed["facts"]["document"]


# ── the procedure waits for Publish (D2 / F1) ──────────────────────────────────────────────

async def test_a_rewrite_is_not_in_force_until_it_is_published():
    ws = await _workspace("soppub")
    category = await _category(ws, "Listings")
    viewer = await _person(ws, "Vera Viewer")
    async with _client() as c:
        h = _H(ws["owner"], ws["host"])
        sop = await _sop(c, ws, title="New Listing Intake", category_id=category, state="Live",
                         summary="From yes to the sign coming down.")
        await c.post(f"/api/console/sops/{sop['id']}/versions", headers=h,
                     data={"version_label": "v1"},
                     files={"file": ("intake.pdf", _pdf(), "application/pdf")})
        await c.post("/api/console/publish", headers=h, json={})

        # Written, saved, and not yet published: the team is still reading the document alone.
        saved = await c.put(f"/api/console/sops/{sop['id']}/body", headers=h, json={"body": BODY})
        assert saved.status_code == 200, saved.text
        assert saved.json()["item"]["body_live"] is False
        before = await _content(c, ws, viewer["token"])
        early = await c.get(f"/api/v1/intranet/sops/{sop['id']}",
                            headers=_H(viewer["token"], ws["host"]))

        await c.post("/api/console/publish", headers=h, json={})
        after = await _content(c, ws, viewer["token"])
        reader = await c.get(f"/api/v1/intranet/sops/{sop['id']}",
                             headers=_H(viewer["token"], ws["host"]))

        # Now a rewrite, in progress, and then thrown away.
        await c.put(f"/api/console/sops/{sop['id']}/body", headers=h,
                    json={"body": {**BODY, "intro": "Halfway through a rewrite."}})
        mid = await c.get(f"/api/v1/intranet/sops/{sop['id']}",
                          headers=_H(viewer["token"], ws["host"]))
        discarded = await c.post("/api/console/publish/discard", headers=h)
        assert discarded.status_code == 200, discarded.text
        restored = await c.get(f"/api/console/sops/{sop['id']}", headers=h)

    assert before["sops"][0]["has_body"] is False, "an unpublished procedure was already in force"
    assert early.json()["intro"] is None and early.json()["steps"] == []
    assert after["sops"][0]["has_body"] is True
    assert reader.json()["intro"] == BODY["intro"]
    assert [s["index"] for s in reader.json()["steps"]] == [1, 2]
    assert mid.json()["intro"] == BODY["intro"], "members were shown a rewrite in progress"
    assert restored.json()["body"]["intro"] == BODY["intro"], \
        "discard left the abandoned rewrite in the editor"
    assert restored.json()["body_live"] is True


# ── the document (F2) ──────────────────────────────────────────────────────────────────────

async def test_an_uploaded_document_is_checked_by_its_bytes():
    ws = await _workspace("sopfile")
    category = await _category(ws, "Transactions")
    async with _client() as c:
        h = _H(ws["owner"], ws["host"])
        sop = await _sop(c, ws, title="Under Contract", category_id=category, state="Live")
        url = f"/api/console/sops/{sop['id']}/versions"
        empty = await c.post(url, headers=h, data={"version_label": "v0"},
                             files={"file": ("x.pdf", b"", "application/pdf")})
        wrong = await c.post(url, headers=h, data={"version_label": "v0"},
                             files={"file": ("sneak.pdf", b"<html>hi</html>", "application/pdf")})
        big = await c.post(url, headers=h, data={"version_label": "v0"},
                           files={"file": ("big.pdf", _pdf(26 * 1024 * 1024), "application/pdf")})
        good = await c.post(url, headers=h, data={"version_label": "v1"},
                            files={"file": ("intake.pdf", _pdf(), "application/octet-stream")})
        word = await c.post(url, headers=h, data={"version_label": "v2"},
                            files={"file": ("intake.docx", _docx(), "text/plain")})
    assert empty.status_code == 422 and wrong.status_code == 422 and big.status_code == 422
    assert "PDF" in wrong.json()["detail"]["errors"][0]["message"]
    assert good.status_code == 200, good.text
    # The stored type is what the bytes are, not what the browser said they were.
    assert good.json()["item"]["content_type"] == "application/pdf"
    assert word.status_code == 200, word.text
    assert word.json()["item"]["content_type"].endswith("wordprocessingml.document")


async def test_a_written_procedure_revises_without_a_file():
    """Acknowledgements hang off the version, so a rewrite has to be able to ask again."""
    ws = await _workspace("soprev")
    category = await _category(ws, "Listings")
    reader = await _person(ws, "Rhea Reader")
    async with _client() as c:
        h = _H(ws["owner"], ws["host"])
        sop = await _sop(c, ws, title="Intake", category_id=category, state="Live")
        no_body = await c.post(f"/api/console/sops/{sop['id']}/versions", headers=h,
                               data={"version_label": "v1"})
        await c.put(f"/api/console/sops/{sop['id']}/body", headers=h, json={"body": BODY})
        v1 = await c.post(f"/api/console/sops/{sop['id']}/versions", headers=h,
                          data={"version_label": "v1"})
        await c.post("/api/console/publish", headers=h, json={})
        acked = await c.post(f"/api/v1/intranet/sops/{sop['id']}/acknowledge",
                             headers=_H(reader["token"], ws["host"]))
        # There is no document, so there is nothing to download.
        no_file = await c.get(f"/api/v1/intranet/sops/{sop['id']}/file",
                              headers=_H(reader["token"], ws["host"]))
        v2 = await c.post(f"/api/console/sops/{sop['id']}/versions", headers=h,
                          data={"version_label": "v2"})
        after = await _content(c, ws, reader["token"])
    assert no_body.status_code == 422, "a revision with neither a file nor a procedure"
    assert v1.status_code == 200 and v1.json()["item"]["filename"] is None
    assert acked.status_code == 200 and acked.json()["acknowledged_at"]
    assert no_file.status_code == 404
    assert v2.status_code == 200
    assert after["sops"][0]["version"] == "v2"
    assert after["sops"][0]["acknowledged_at"] is None, "a new revision kept the old assurance"


# ── the corrections (F3, F4) ───────────────────────────────────────────────────────────────

async def test_an_archived_procedure_can_come_back():
    ws = await _workspace("soparch")
    category = await _category(ws, "Listings")
    async with _client() as c:
        h = _H(ws["owner"], ws["host"])
        sop = await _sop(c, ws, title="Expired listings", category_id=category, state="Live")
        await c.delete(f"/api/console/sops/{sop['id']}", headers=h)
        listed = await c.get("/api/console/sops", headers=h)
        with_archived = await c.get("/api/console/sops?include_archived=true", headers=h)
        back = await c.post(f"/api/console/sops/{sop['id']}/restore", headers=h)
        again = await c.post(f"/api/console/sops/{sop['id']}/restore", headers=h)
    assert [i["title"] for i in listed.json()["items"]] == []
    assert [i["title"] for i in with_archived.json()["items"]] == ["Expired listings"]
    assert back.status_code == 200, back.text
    # Back as a Draft: putting it in front of the team again is a deliberate act.
    assert back.json()["item"]["state"] == "Draft" and back.json()["item"]["archived_at"] is None
    assert again.status_code == 422, "restoring a live procedure should say so"


async def test_who_has_read_it_is_countable_and_nameable():
    ws = await _workspace("sopack")
    category = await _category(ws, "Listings")
    read_it = await _person(ws, "Rory Read")
    await _person(ws, "Nora Not")
    async with _client() as c:
        h = _H(ws["owner"], ws["host"])
        sop = await _sop(c, ws, title="Intake", category_id=category, state="Live")
        await c.put(f"/api/console/sops/{sop['id']}/body", headers=h, json={"body": BODY})
        await c.post(f"/api/console/sops/{sop['id']}/versions", headers=h,
                     data={"version_label": "v1"})
        await c.post("/api/console/publish", headers=h, json={})
        await c.post(f"/api/v1/intranet/sops/{sop['id']}/acknowledge",
                     headers=_H(read_it["token"], ws["host"]))
        detail = await c.get(f"/api/console/sops/{sop['id']}", headers=h)
        roster = await c.get(f"/api/console/sops/{sop['id']}/acknowledgements", headers=h)
        seen = await c.get(f"/api/v1/intranet/sops/{sop['id']}",
                           headers=_H(read_it["token"], ws["host"]))
    # Was a hardcoded zero: the console asked and was always told nobody.
    assert detail.json()["acknowledged_count"] == 1
    names = {i["name"]: i["acknowledged_at"] for i in roster.json()["items"]}
    assert names["Rory Read"] and names["Nora Not"] is None
    assert roster.json()["acknowledged"] == 1 and roster.json()["version"] == "v1"
    # The member sees the count, never the roster (D3).
    assert seen.json()["acknowledged_count"] == 1
    assert seen.json()["team_size"] >= 2
    assert "items" not in seen.json()


async def test_a_review_is_a_fact_not_only_a_due_date():
    ws = await _workspace("soprev2")
    category = await _category(ws, "Listings")
    async with _client() as c:
        h = _H(ws["owner"], ws["host"])
        sop = await _sop(c, ws, title="Intake", category_id=category, state="Needs Review")
        done = await c.post(f"/api/console/sops/{sop['id']}/review", headers=h,
                            json={"review_due_on": "2027-06-30"})
    item = done.json()["item"]
    assert item["last_reviewed_on"] == dt.date.today().isoformat()
    assert item["review_due_on"] == "2027-06-30"
    assert item["state"] == "Live", "a procedure reviewed is no longer Needs Review"


async def test_an_earlier_revision_can_be_put_back_in_front_of_the_team():
    ws = await _workspace("sopback")
    category = await _category(ws, "Listings")
    async with _client() as c:
        h = _H(ws["owner"], ws["host"])
        sop = await _sop(c, ws, title="Intake", category_id=category, state="Live")
        first = await c.post(f"/api/console/sops/{sop['id']}/versions", headers=h,
                             data={"version_label": "v1"},
                             files={"file": ("a.pdf", _pdf(), "application/pdf")})
        await c.post(f"/api/console/sops/{sop['id']}/versions", headers=h,
                     data={"version_label": "v2"},
                     files={"file": ("b.pdf", _pdf(500), "application/pdf")})
        back = await c.post(
            f"/api/console/sops/{sop['id']}/versions/{first.json()['item']['id']}/current",
            headers=h)
    assert back.status_code == 200, back.text
    assert back.json()["item"]["current_version"]["version_label"] == "v1"


# ── what the member's two screens are given ────────────────────────────────────────────────

async def test_the_library_gives_the_rail_the_card_and_the_owner():
    ws = await _workspace("soplib")
    listings = await _category(ws, "Listings")
    await _category(ws, "Client Care")
    owner = await _person(ws, "Sasha Lead", role="manager", title="Listing Director")
    viewer = await _person(ws, "Vera Viewer")
    async with _client() as c:
        h = _H(ws["owner"], ws["host"])
        sop = await _sop(c, ws, title="New Listing Intake", category_id=listings, state="Live",
                         owner_member_id=owner["id"], summary="From yes to the sign coming down.",
                         applies_to="Listing Agents")
        await c.put(f"/api/console/sops/{sop['id']}/body", headers=h, json={"body": BODY})
        await c.post(f"/api/console/sops/{sop['id']}/versions", headers=h,
                     data={"version_label": "v3.1"},
                     files={"file": ("intake.pdf", _pdf(), "application/pdf")})
        await c.post("/api/console/publish", headers=h, json={})
        content = await _content(c, ws, viewer["token"])
        reader = await c.get(f"/api/v1/intranet/sops/{sop['id']}",
                             headers=_H(viewer["token"], ws["host"]))
    card = content["sops"][0]
    assert card["summary"] == "From yes to the sign coming down."
    assert card["department"] == "Listings" and card["version"] == "v3.1"
    assert card["owner"]["name"] == "Sasha Lead" and card["owner"]["title"] == "Listing Director"
    assert card["owner"]["profile_url"] == f"/directory/{owner['id']}"
    assert card["has_body"] is True and card["has_document"] is True
    assert card["updated_on"] == dt.date.today().isoformat()
    assert card["steps"] == ["Log the appointment", "Send the packet"]
    # The rail: every department the workspace has, with its count, All Procedures first.
    rail = content["sop_departments"]
    assert rail[0] == {"name": "All Procedures", "key": "", "count": 1}
    assert {d["name"]: d["count"] for d in rail[1:]} == {"Listings": 1, "Client Care": 0}
    page = reader.json()
    assert page["applies_to"] == "Listing Agents"
    assert page["callout"]["text"] == "Nothing goes to the MLS unsigned."
    assert page["steps"][0]["anchor"] == "step-01"
    assert page["document"]["inline"] is True, "a PDF should be readable in the page"


async def test_a_procedure_only_offers_the_tools_the_reader_may_open():
    ws = await _workspace("soptools")
    category = await _category(ws, "Listings")
    viewer = await _person(ws, "Vera Viewer")
    async with SessionLocal() as s:
        shared = IntranetLaunchpadTile(tenant_id=ws["tenant_id"], name="Sisu", tile_group="Daily",
                                       url="https://sisu.example", auth_type="SSO", sort=0,
                                       published_at=dt.datetime.now(dt.timezone.utc),
                                       draft_dirty=False)
        restricted = IntranetLaunchpadTile(tenant_id=ws["tenant_id"], name="Recruiting",
                                           tile_group="Leadership", url="https://rec.example",
                                           auth_type="SSO", sort=1,
                                           published_at=dt.datetime.now(dt.timezone.utc),
                                           draft_dirty=False)
        s.add_all([shared, restricted])
        await s.flush()
        s.add(IntranetLaunchpadTileRole(tenant_id=ws["tenant_id"], tile_id=restricted.id,
                                        role_id=ws["roles"]["manager"]))
        await s.commit()
        tool_ids = [str(shared.id), str(restricted.id)]
    async with _client() as c:
        h = _H(ws["owner"], ws["host"])
        sop = await _sop(c, ws, title="Intake", category_id=category, state="Live",
                         tool_ids=tool_ids)
        await c.put(f"/api/console/sops/{sop['id']}/body", headers=h, json={"body": BODY})
        await c.post(f"/api/console/sops/{sop['id']}/versions", headers=h,
                     data={"version_label": "v1"})
        await c.post("/api/console/publish", headers=h, json={})
        page = await c.get(f"/api/v1/intranet/sops/{sop['id']}",
                           headers=_H(viewer["token"], ws["host"]))
    assert [t["name"] for t in page.json()["tools"]] == ["Sisu"], \
        "a restricted tool became a link through the back of a procedure"


async def test_the_procedure_page_is_shut_to_a_role_that_may_not_read_sops():
    from app.models import IntranetCapability, IntranetPermission

    ws = await _workspace("sopdeny")
    category = await _category(ws, "Listings")
    viewer = await _person(ws, "Vera Viewer")
    async with SessionLocal() as s:
        capability = (await s.execute(select(IntranetCapability).where(
            IntranetCapability.tenant_id == ws["tenant_id"],
            IntranetCapability.key == "sop_library"))).scalars().first()
        row = (await s.execute(select(IntranetPermission).where(
            IntranetPermission.tenant_id == ws["tenant_id"],
            IntranetPermission.capability_id == capability.id,
            IntranetPermission.role_id == ws["roles"]["member"]))).scalars().first()
        row.level = "None"
        await s.commit()
    async with _client() as c:
        h = _H(ws["owner"], ws["host"])
        sop = await _sop(c, ws, title="Intake", category_id=category, state="Live")
        await c.put(f"/api/console/sops/{sop['id']}/body", headers=h, json={"body": BODY})
        await c.post(f"/api/console/sops/{sop['id']}/versions", headers=h,
                     data={"version_label": "v1"})
        await c.post("/api/console/publish", headers=h, json={})
        page = await c.get(f"/api/v1/intranet/sops/{sop['id']}",
                           headers=_H(viewer["token"], ws["host"]))
        content = await _content(c, ws, viewer["token"])
    assert page.status_code == 404, "404, not 403: which procedures exist is not theirs to learn"
    assert content["sops"] == [] and content["sop_departments"] == []


async def test_the_departments_keep_the_order_the_console_gives_them():
    ws = await _workspace("soporder")
    first = await _category(ws, "Listings")
    second = await _category(ws, "Transactions")
    viewer = await _person(ws, "Vera Viewer")
    async with _client() as c:
        h = _H(ws["owner"], ws["host"])
        sop = await _sop(c, ws, title="Intake", category_id=first, state="Live")
        await c.put(f"/api/console/sops/{sop['id']}/body", headers=h, json={"body": BODY})
        await c.post(f"/api/console/sops/{sop['id']}/versions", headers=h,
                     data={"version_label": "v1"})
        await c.post("/api/console/publish", headers=h, json={})
        moved = await c.put("/api/console/sop-categories/order", headers=h,
                            json={"ids": [second, first]})
        await c.post("/api/console/publish", headers=h, json={})
        content = await _content(c, ws, viewer["token"])
    assert moved.status_code == 200, moved.text
    assert [d["name"] for d in content["sop_departments"]] == \
        ["All Procedures", "Transactions", "Listings"]


def test_changed_this_month_is_this_month_or_nothing():
    today = dt.date(2026, 9, 20)
    cards = [{"title": "New", "updated_on": "2026-09-04"},
             {"title": "Newer", "updated_on": "2026-09-18"},
             {"title": "Old", "updated_on": "2026-04-03"}]
    assert [c["title"] for c in sop_library.changed_this_month(cards, today)] == ["Newer", "New"]
    assert sop_library.changed_this_month([cards[2]], today) == [], \
        "a panel headed Changed This Month must not list April"
