"""AI Employees — Step 4 router: flag gate, role/tab guards, and the approval lifecycle
(draft → approve → ship), including the 409 on shipping an unapproved artifact.
"""
import uuid

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.seed import seed
from app.config import settings
from app.db import SessionLocal
from app.models import Tenant, User, AIEmployee, AIRun, AIArtifact
from app.security import hash_pw, make_token

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded_enabled():
    prev = settings.AI_EMPLOYEES_ENABLED
    settings.AI_EMPLOYEES_ENABLED = True          # the whole surface is flag-gated
    await seed()
    yield
    settings.AI_EMPLOYEES_ENABLED = prev


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token):
    return {"Authorization": f"Bearer {token}"}


async def _tenant_id():
    async with SessionLocal() as s:
        return (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id


async def _owner_token():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


async def _mk_member(email, tabs):
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        u = User(tenant_id=t.id, email=email.lower(), name=email.split("@")[0],
                 password_hash=hash_pw("password123"), role="member", status="active",
                 tab_access=tabs, token_version=0)
        s.add(u)
        await s.commit()
        return make_token(u.id, t.id, 0)


async def _seed_run_with_draft(emp_id, tenant_id, state="draft"):
    async with SessionLocal() as s:
        run = AIRun(tenant_id=tenant_id, employee_id=uuid.UUID(emp_id), skill_key="strategy",
                    trigger="manual", status="awaiting_approval", summary="pivot", reads=["r1"])
        s.add(run)
        await s.flush()
        art = AIArtifact(tenant_id=tenant_id, run_id=run.id, kind="strategy", lane="Strategy",
                         title="Strategy pivot", dest_label="Strategy memo",
                         payload={"kind": "strategy"}, state=state)
        s.add(art)
        await s.commit()
        return str(run.id), str(art.id)


# ── flag gate ─────────────────────────────────────────────────────────────────
async def test_flag_off_returns_404():
    owner = await _owner_token()
    settings.AI_EMPLOYEES_ENABLED = False
    try:
        async with _client() as c:
            assert (await c.get("/api/v1/ai/employees", headers=_H(owner))).status_code == 404
    finally:
        settings.AI_EMPLOYEES_ENABLED = True


# ── role / tab guards ─────────────────────────────────────────────────────────
async def test_member_without_grant_403_and_with_grant_reads():
    owner = await _owner_token()
    async with _client() as c:
        emp = (await c.post("/api/v1/ai/employees", headers=_H(owner),
                            json={"name": "Nova", "role_title": "Social Media Manager"})).json()
    no_grant = await _mk_member("ainogrant@x.com", tabs=["forum"])
    with_grant = await _mk_member("aigrant@x.com", tabs=["ai_employees"])
    async with _client() as c:
        assert (await c.get("/api/v1/ai/employees", headers=_H(no_grant))).status_code == 403
        # member WITH the grant reads, but cannot mutate
        assert (await c.get("/api/v1/ai/employees", headers=_H(with_grant))).status_code == 200
        assert (await c.post("/api/v1/ai/employees", headers=_H(with_grant),
                             json={"name": "X"})).status_code == 403
        assert (await c.post(f"/api/v1/ai/employees/{emp['id']}/skills/strategy/run",
                             headers=_H(with_grant), json={})).status_code == 403


# ── lifecycle: draft → approve → ship (+ the 409s) ────────────────────────────
async def test_lifecycle_draft_approve_ship():
    owner = await _owner_token()
    tid = await _tenant_id()
    async with _client() as c:
        emp = (await c.post("/api/v1/ai/employees", headers=_H(owner),
                            json={"name": "Aria"})).json()
        eid = emp["id"]
        run_id, art_id = await _seed_run_with_draft(eid, tid)

        # the run shows one draft artifact
        got = (await c.get(f"/api/v1/ai/runs/{run_id}", headers=_H(owner))).json()
        assert len(got["artifacts"]) == 1 and got["artifacts"][0]["state"] == "draft"

        # approve with writeback gates CLOSED → approved, not shipped
        settings.AI_EMPLOYEES_WRITEBACK_ENABLED = False
        appr = (await c.post(f"/api/v1/ai/runs/{run_id}/approve", headers=_H(owner))).json()
        assert appr["approved"] == 1 and appr["shipped"] == 0 and appr["writeback_open"] is False
        assert appr["run_status"] == "approved"

        # shipping while the gate is closed is a 409 (writeback disabled)
        r = await c.post(f"/api/v1/ai/artifacts/{art_id}/ship", headers=_H(owner))
        assert r.status_code == 409 and "disabled" in r.json()["detail"].lower()

        # open BOTH gates, then ship succeeds
        settings.AI_EMPLOYEES_WRITEBACK_ENABLED = True
        await c.patch(f"/api/v1/ai/employees/{eid}", headers=_H(owner),
                      json={"writeback_enabled": True})
        shipped = (await c.post(f"/api/v1/ai/artifacts/{art_id}/ship", headers=_H(owner))).json()
        assert shipped["state"] == "shipped" and shipped["shipped_at"]
    settings.AI_EMPLOYEES_WRITEBACK_ENABLED = False


async def test_ship_unapproved_returns_409():
    owner = await _owner_token()
    tid = await _tenant_id()
    async with _client() as c:
        emp = (await c.post("/api/v1/ai/employees", headers=_H(owner),
                            json={"name": "Iris"})).json()
        _, art_id = await _seed_run_with_draft(emp["id"], tid, state="draft")
        r = await c.post(f"/api/v1/ai/artifacts/{art_id}/ship", headers=_H(owner))
        assert r.status_code == 409 and "approved" in r.json()["detail"].lower()


async def test_settings_summary_and_export():
    owner = await _owner_token()
    tid = await _tenant_id()
    async with _client() as c:
        st = (await c.get("/api/v1/ai/settings", headers=_H(owner))).json()
        assert set(st) >= {"writeback_env_open", "model", "token_budget", "tokens_used", "can_manage"}
        assert st["can_manage"] is True
        emp = (await c.post("/api/v1/ai/employees", headers=_H(owner), json={"name": "Faye"})).json()
        await _seed_run_with_draft(emp["id"], tid)
        exp = (await c.get(f"/api/v1/ai/employees/{emp['id']}/export", headers=_H(owner))).json()
        assert exp["employee"]["name"] == "Faye" and len(exp["runs"]) == 1 and len(exp["artifacts"]) == 1


async def test_archive_hides_employee_from_list():
    owner = await _owner_token()
    async with _client() as c:
        emp = (await c.post("/api/v1/ai/employees", headers=_H(owner), json={"name": "Zed"})).json()
        assert (await c.delete(f"/api/v1/ai/employees/{emp['id']}", headers=_H(owner))).status_code == 200
        lst = (await c.get("/api/v1/ai/employees", headers=_H(owner))).json()
        assert all(e["id"] != emp["id"] for e in lst["employees"])   # archived → hidden


async def test_manual_schedule_override_means_no_next_run():
    """schedule_override='manual' zeroes the next scheduled run even for a skill with a
    default cron (trend_brief)."""
    owner = await _owner_token()
    async with _client() as c:
        emp = (await c.post("/api/v1/ai/employees", headers=_H(owner), json={"name": "Odette"})).json()
        await c.patch(f"/api/v1/ai/employees/{emp['id']}/skills/trend_brief", headers=_H(owner),
                      json={"schedule_override": "manual"})
        # every other skill is manual-by-default → the employee now has no scheduled run
        lst = (await c.get("/api/v1/ai/employees", headers=_H(owner))).json()
        mine = next(e for e in lst["employees"] if e["id"] == emp["id"])
        assert mine["next_run_at"] is None


async def test_cowork_audit_bridge_round_trip():
    """start_cowork_audit → pending run + claude:// deep link; a token-gated post-back (as
    Cowork would send) lands the audit as a draft awaiting approval, and is single-use."""
    import re
    from urllib.parse import urlparse, parse_qs
    owner = await _owner_token()
    async with _client() as c:
        emp = (await c.post("/api/v1/ai/employees", headers=_H(owner), json={"name": "Cass"})).json()
        start = (await c.post(f"/api/v1/ai/employees/{emp['id']}/cowork-audit",
                              headers=_H(owner), json={"handle": "@rival"})).json()
        assert start["deep_link"].startswith("claude://cowork/new?q=")
        rid = start["run_id"]
        # pending run shows "running" (external — the worker leaves it alone)
        r = (await c.get(f"/api/v1/ai/runs/{rid}", headers=_H(owner))).json()
        assert r["run"]["status"] == "running" and not r["artifacts"]
        # pull the capability token out of the deep-link instruction, as Cowork receives it
        q = parse_qs(urlparse(start["deep_link"]).query)["q"][0]
        token = re.search(r'"token":"([^"]+)"', q).group(1)
        # Cowork posts the teardown back — NO login header, just the token
        ing = await c.post("/api/v1/ai/cowork/ingest", json={"token": token, "artifact": {
            "title": "Rival wins on reflection Reels",
            "payload": {"kind": "audit", "handle": "@rival",
                        "top": [{"name": "Reflection Reel", "val": "4.2x", "w": "100%"}],
                        "mechanics": ["Hook names a hidden problem"], "note": "informs our posts"}}})
        assert ing.status_code == 200 and ing.json()["ok"] is True
        r2 = (await c.get(f"/api/v1/ai/runs/{rid}", headers=_H(owner))).json()
        assert r2["run"]["status"] == "awaiting_approval"
        assert len(r2["artifacts"]) == 1
        a = r2["artifacts"][0]
        assert a["kind"] == "audit" and a["state"] == "draft" and a["dest_label"] == "Instagram · Cowork"
        # single-use: a second post-back is refused
        ing2 = await c.post("/api/v1/ai/cowork/ingest", json={"token": token, "artifact": {}})
        assert ing2.status_code == 409
        # a garbage token is unauthorized
        bad = await c.post("/api/v1/ai/cowork/ingest", json={"token": "nope", "artifact": {}})
        assert bad.status_code == 401


async def test_full_response_audits_roster_then_continues():
    """One button: with a watch roster, the full response returns a Cowork deep link and lands in
    'awaiting_audit'; posting the roster audit back flips the run to 'queued' (the cascade
    continues on the worker) with the audit(s) attached."""
    import re
    from urllib.parse import urlparse, parse_qs
    owner = await _owner_token()
    async with _client() as c:
        emp = (await c.post("/api/v1/ai/employees", headers=_H(owner), json={"name": "Nova"})).json()
        await c.post(f"/api/v1/ai/employees/{emp['id']}/roster", headers=_H(owner), json={"handle": "@rival"})
        r = (await c.post(f"/api/v1/ai/employees/{emp['id']}/respond", headers=_H(owner))).json()
        assert r["status"] == "awaiting_audit" and r["deep_link"].startswith("claude://cowork/new?q=")
        rid = r["run_id"]
        token = re.search(r'"token":"([^"]+)"', parse_qs(urlparse(r["deep_link"]).query)["q"][0]).group(1)
        ing = await c.post("/api/v1/ai/cowork/ingest", json={"token": token, "artifacts": [
            {"title": "@rival wins on carousels", "payload": {"kind": "audit", "handle": "@rival",
             "top": [{"name": "carousel", "val": "3.4x", "w": "100%"}], "mechanics": ["teach then sell"]}}]})
        assert ing.status_code == 200 and ing.json()["status"] == "queued"   # cascade continues
        det = (await c.get(f"/api/v1/ai/runs/{rid}", headers=_H(owner))).json()
        assert det["run"]["status"] == "queued"
        assert len(det["artifacts"]) == 1 and det["artifacts"][0]["dest_label"] == "Instagram · Cowork"


async def test_dismiss_run_and_bulk_clear_pending():
    """Clear-without-approving: dismissing a run drops its drafts off the badge; the bulk
    endpoint clears every pending run for the employee at once."""
    owner = await _owner_token()
    tid = await _tenant_id()
    async with _client() as c:
        emp = (await c.post("/api/v1/ai/employees", headers=_H(owner), json={"name": "Dez"})).json()
        r1, a1 = await _seed_run_with_draft(emp["id"], tid)
        r2, _ = await _seed_run_with_draft(emp["id"], tid)
        # badge = 2 draft artifacts across the two awaiting_approval runs
        me = next(e for e in (await c.get("/api/v1/ai/employees", headers=_H(owner))).json()["employees"] if e["id"] == emp["id"])
        assert me["awaiting_approval"] == 2
        # dismiss one run → its draft is dismissed, badge drops to 1
        assert (await c.post(f"/api/v1/ai/runs/{r1}/dismiss", headers=_H(owner))).json()["run_status"] == "dismissed"
        det = (await c.get(f"/api/v1/ai/runs/{r1}", headers=_H(owner))).json()
        assert det["run"]["status"] == "dismissed" and det["artifacts"][0]["state"] == "dismissed"
        me = next(e for e in (await c.get("/api/v1/ai/employees", headers=_H(owner))).json()["employees"] if e["id"] == emp["id"])
        assert me["awaiting_approval"] == 1
        # bulk clear → the rest go, badge to 0
        assert (await c.post(f"/api/v1/ai/employees/{emp['id']}/dismiss-pending", headers=_H(owner))).json()["dismissed"] >= 1
        me = next(e for e in (await c.get("/api/v1/ai/employees", headers=_H(owner))).json()["employees"] if e["id"] == emp["id"])
        assert me["awaiting_approval"] == 0


async def test_media_upload_list_serve_and_delete():
    """Upload → catalog row + blob; list returns it with a token-gated url; the file endpoint
    serves the bytes with that token; delete removes it."""
    owner = await _owner_token()
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)   # minimal PNG-ish bytes
    async with _client() as c:
        emp = (await c.post("/api/v1/ai/employees", headers=_H(owner), json={"name": "Mia"})).json()
        up = await c.post(f"/api/v1/ai/employees/{emp['id']}/media", headers=_H(owner),
                          files={"file": ("stage.png", png, "image/png")},
                          data={"kind": "event", "title": "On stage", "tags": "keynote, warm"})
        assert up.status_code == 201
        a = up.json()
        assert a["kind"] == "event" and a["is_image"] and a["tags"] == ["keynote", "warm"]
        lst = (await c.get(f"/api/v1/ai/employees/{emp['id']}/media", headers=_H(owner))).json()
        assert len(lst["assets"]) == 1
        url = lst["assets"][0]["url"]                 # /ai/media/{id}/file?t=<token>
        got = await c.get("/api/v1" + url)            # token-gated — no auth header needed
        assert got.status_code == 200 and got.content == png and got.headers["content-type"] == "image/png"
        # bad/missing token is rejected
        assert (await c.get(f"/api/v1/ai/media/{a['id']}/file?t=nope")).status_code == 401
        # edit metadata, then delete
        await c.patch(f"/api/v1/ai/media/{a['id']}", headers=_H(owner), json={"description": "Spring mid-talk"})
        assert (await c.delete(f"/api/v1/ai/media/{a['id']}", headers=_H(owner))).status_code == 200
        assert (await c.get(f"/api/v1/ai/employees/{emp['id']}/media", headers=_H(owner))).json()["assets"] == []
        # only images/video allowed
        bad = await c.post(f"/api/v1/ai/employees/{emp['id']}/media", headers=_H(owner),
                           files={"file": ("x.txt", b"hello", "text/plain")}, data={"kind": "stock"})
        assert bad.status_code == 415


async def test_voice_profile_stored_and_excluded_from_list():
    owner = await _owner_token()
    async with _client() as c:
        emp = (await c.post("/api/v1/ai/employees", headers=_H(owner), json={"name": "Vox"})).json()
        prof = "# Forensic Voice Profile\n" + ("line of the spec\n" * 400)   # many pages
        put = await c.put(f"/api/v1/ai/employees/{emp['id']}/voice", headers=_H(owner),
                          json={"voice_profile": prof})
        assert put.status_code == 200 and put.json()["chars"] == len(prof.strip())
        got = (await c.get(f"/api/v1/ai/employees/{emp['id']}/voice", headers=_H(owner))).json()
        assert got["voice_profile"] == prof            # stored verbatim (not stripped)
        # the big profile is NOT shipped in the employee list, but its presence is flagged
        me = next(e for e in (await c.get("/api/v1/ai/employees", headers=_H(owner))).json()["employees"]
                  if e["id"] == emp["id"])
        assert "voice_profile" not in me["config"] and me["has_voice_profile"] is True


async def test_full_response_without_roster_runs_server_side():
    owner = await _owner_token()
    async with _client() as c:
        emp = (await c.post("/api/v1/ai/employees", headers=_H(owner), json={"name": "Ivy"})).json()
        r = (await c.post(f"/api/v1/ai/employees/{emp['id']}/respond", headers=_H(owner))).json()
        assert r["deep_link"] is None and r["status"] == "queued"   # no roster → straight cascade


async def test_create_seeds_six_skills_and_awaiting_badge():
    owner = await _owner_token()
    tid = await _tenant_id()
    async with _client() as c:
        emp = (await c.post("/api/v1/ai/employees", headers=_H(owner),
                            json={"name": "Wren"})).json()
        skills = (await c.get(f"/api/v1/ai/employees/{emp['id']}/skills",
                              headers=_H(owner))).json()["skills"]
        assert {s["key"] for s in skills} == {
            "audit", "trend_brief", "strategy", "design_carousel", "reel_script", "measure"}
        await _seed_run_with_draft(emp["id"], tid)
        lst = (await c.get("/api/v1/ai/employees", headers=_H(owner))).json()
        mine = next(e for e in lst["employees"] if e["id"] == emp["id"])
        assert mine["awaiting_approval"] == 1 and lst["awaiting_total"] >= 1
