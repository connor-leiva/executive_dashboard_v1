"""Books Cleanup -- who sees the Forum's members one by one, and what the tab says when their projection doesn't build.

The Forum's projection from its members' records carries a list of them under it (names, what each paid and is set
to pay, renewal dates, flags, links into the CRM). That list is the membership tab's roster, so it goes where the
roster goes: owners, admins, and whoever has the Forum tab. Anyone else with Books Cleanup gets the same projection
and the same counts, and the block says the list is held back. Nothing else on the tab carries the members: the Ask
reads the file and the saved changes only.

On the invented three-company file (test_books_cleanup.py), the club gets three invented members with names no other
part of the file uses, so finding one anywhere in a response is a leak:

  * Quintessa Marlowe pays in full, $1,200 a year, renewing Jul 1, paid last July;
  * Barnaby Thistlewood pays monthly, renewing May 15, with nothing paid or scheduled -- flagged;
  * Ottoline Varga has no renewal date -- left out, and named.

And when the projection doesn't build, a change to the Forum's assumptions or plan is refused as that (503), not as
"Nothing to change", and nothing is saved.
"""
import datetime as dt
import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Business, MetricRecord, Tenant, User
from app.routers.books_cleanup import load_dataset
from app.security import hash_pw, make_token
from app.services import cleanup_ask
from app.services import cleanup_live as live
from tests.test_books_cleanup import CLUB, COHORT, forwarded, keyed

TODAY = dt.date(2026, 4, 20)
NAMES = ["Quintessa Marlowe", "Barnaby Thistlewood", "Ottoline Varga"]
CRM = "https://crm.test/roster/"


def _H(token, slug="rosterco"):
    return {"Authorization": f"Bearer {token}", "x-tenant-host": f"{slug}.localhost"}


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def _records(t, biz):
    def member(cid, name, total, renewal, plan):
        mem = {"total_cost": total, "payment": plan, "member_kind": "primary"}
        if renewal:
            mem["renewal_date"] = renewal
        return MetricRecord(tenant_id=t.id, business_id=biz.id, source="ghl", kind="member", external_id=cid, name=name,
                            email=f"{cid}@club.test", status="active", source_url=CRM + cid, meta={"membership": mem})
    return [member("qm", NAMES[0], 1200, "2026-07-01", "pif"),
            member("bt", NAMES[1], 1200, "2026-05-15", "monthly"),
            member("ov", NAMES[2], 900, None, "pif"),
            MetricRecord(tenant_id=t.id, business_id=biz.id, source="ghl", kind="payment", external_id="p1",
                         name=NAMES[0], email="qm@club.test", amount=1200, status="succeeded",
                         occurred_on=dt.date(2025, 7, 1),
                         meta={"contact_id": "qm", "entity_source_name": "Forum membership", "stream": "memberships"}),
            MetricRecord(tenant_id=t.id, business_id=biz.id, source="ghl", kind="membership_lost", external_id="l1",
                         occurred_on=dt.date(2025, 11, 3), meta={})]


async def _tenant(s, slug, program_tabs, people):
    """A workspace with the invented file loaded and a membership business holding the three members. Its tab is the
    Forum's (`program_tabs`, the way Spring's is), or the business's own key when there's none."""
    t = Tenant(slug=slug, name=slug.title(), plan="portfolio")
    s.add(t)
    await s.flush()
    biz = Business(tenant_id=t.id, key="club", name="Club Holdings", tag="Memberships", kind="membership",
                   config={"program_tabs": program_tabs} if program_tabs else None)
    s.add(biz)
    await s.flush()
    s.add_all(_records(t, biz))
    users = {who: User(tenant_id=t.id, email=f"{who}@{slug}.test", name=who, role=role,
                       password_hash=hash_pw("x" * 12), status="active", tab_access=tabs)
             for who, (role, tabs) in people.items()}
    s.add_all(users.values())
    await s.flush()
    await load_dataset(s, t.id, forwarded(keyed()), user_id=users["owner"].id)
    await s.commit()
    return {who: make_token(u.id, t.id, 0) for who, u in users.items()}


@pytest.fixture(scope="module")
async def ctx():
    from app.seed import seed
    await seed()
    async with SessionLocal() as s:
        tokens = await _tenant(s, "rosterco", ["forum"], {
            "owner": ("owner", None), "admin": ("admin", None),
            "keeper": ("member", ["cleanup"]),                      # the bookkeeper: Books Cleanup only
            "reader": ("member", ["cleanup", "forum"])})            # Books Cleanup and the Forum tab
        # a workspace whose membership business has no Forum tab: its owner still sees the members
        other = await _tenant(s, "plainco", None, {"owner": ("owner", None), "keeper": ("member", ["cleanup", "club"])})
    return {**tokens, "plain": other}


async def _forward(c, token, slug="rosterco"):
    r = await c.get("/api/v1/cleanup/forward", headers=_H(token, slug))
    assert r.status_code == 200, r.text
    return r


def _forum(body):
    return next(f for f in body["forwards"] if f["company"] == CLUB)


# ── who sees the members by name ────────────────────────────────────────────────────────────────

async def test_owners_admins_and_the_forum_tab_see_the_members_by_name(ctx, monkeypatch):
    monkeypatch.setattr(live, "_today", lambda: TODAY)
    async with _client() as c:
        for who in ("owner", "admin", "reader"):
            det = _forum((await _forward(c, ctx[who])).json())["detail"]
            assert sorted(m["name"] for m in det["members"]) == sorted(NAMES[:2]), who
            assert det["left_out"] == [{"name": NAMES[2], "why": "no renewal date", "source_url": CRM + "ov"}], who
            assert next(m for m in det["members"] if m["name"] == NAMES[1])["flag"], who
            assert "restricted" not in det, who
        det = _forum((await _forward(c, ctx["plain"]["owner"], "plainco")).json())["detail"]
        assert sorted(m["name"] for m in det["members"]) == sorted(NAMES[:2])


async def test_without_the_forum_tab_the_projection_comes_without_the_members(ctx, monkeypatch):
    monkeypatch.setattr(live, "_today", lambda: TODAY)
    async with _client() as c:
        full = (await _forward(c, ctx["owner"])).json()
        r = await _forward(c, ctx["keeper"])
        # the business's own tab isn't the Forum's: a member there with it is held back too
        plain = (await _forward(c, ctx["plain"]["keeper"], "plainco")).text
    body = r.json()
    forum, whole = _forum(body), _forum(full)
    assert forum["detail"] == {"counts": whole["detail"]["counts"], "left_out_count": 1, "restricted": True,
                               "roster_tab": "The Forum"}
    assert forum["detail"]["counts"]["nothing"] == 1 and forum["detail"]["counts"]["due"] == 2
    # every figure is the same, and the file's own projection is untouched
    assert {k: v for k, v in forum.items() if k != "detail"} == {k: v for k, v in whole.items() if k != "detail"}
    assert body["forwards"][0] == full["forwards"][0] and body["forwards"][0]["company"] == COHORT
    assert body["forward"] == full["forward"] and body["budgets"] == full["budgets"]
    # and nobody's name, link or flag is anywhere in what was sent
    for text in (r.text, plain):
        for leak in NAMES + [CRM, "qm@club.test", "Nothing paid toward this term"]:
            assert leak not in text, leak
    assert any(n in json.dumps(full) for n in NAMES)                  # the owner's response does carry them


async def test_the_ask_never_carries_the_members(ctx, monkeypatch):
    """The Ask's context is the file and the saved changes: about the Forum's own dues line, its transactions and
    what it owes, still nobody's name, for someone without the Forum tab or with it."""
    monkeypatch.setattr(live, "_today", lambda: TODAY)
    seen = []

    async def fake(system, messages):
        seen.append(system)
        return "The club's dues."
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cleanup_ask, "_claude_call", fake)
    async with _client() as c:
        for who in ("keeper", "reader"):
            for about in ({"kind": "line", "view": CLUB, "line": "Membership dues"},
                          {"kind": "tx", "key": "ev"}, {"kind": "pair", "pair": "Hub and Club"}):
                r = await c.post("/api/v1/cleanup/ask", headers=_H(ctx[who]),
                                 json={"about": about, "question": "Who are the members behind this?"})
                assert r.status_code == 200, r.text
    assert len(seen) == 6 and '"line":"Membership dues"' in seen[0]
    for system in seen:
        for leak in NAMES + [CRM]:
            assert leak not in system


# ── when the projection doesn't build ─────────────────────────────────────────────────────────────

async def test_a_save_for_the_forum_when_its_projection_didnt_build_says_so(ctx, monkeypatch):
    monkeypatch.setattr(live, "_today", lambda: TODAY)
    renew = {"path": ["forward", CLUB, "renew_pct"], "value": 60}
    async with _client() as c:
        # built: the Forum's change is checked against its block, and a field it doesn't have is still a 400
        bad = await c.put("/api/v1/cleanup/inputs", headers=_H(ctx["owner"]),
                          json={"changes": [{"path": ["forward", CLUB, "commission_pct"], "value": 5}]})
        assert bad.status_code == 400 and "Nothing to change" in bad.json()["detail"]

        async def broken(*a, **k):
            raise RuntimeError("the members' records")
        monkeypatch.setattr(live, "blocks", broken)
        version = (await c.get("/api/v1/cleanup", headers=_H(ctx["owner"]))).json()["version"]
        for changes in ([renew], [renew, {"path": ["forward", "commission_pct"], "value": 10}],
                        [{"path": ["budget", CLUB, "Staff and contractors"], "value": 300}]):
            r = await c.put("/api/v1/cleanup/inputs", headers=_H(ctx["owner"]), json={"changes": changes})
            assert r.status_code == 503, r.text
            assert "members' records didn't build" in r.json()["detail"] and "Nothing to change" not in r.text
        # all or nothing: the file's own change that came with it wasn't saved either
        assert (await c.get("/api/v1/cleanup", headers=_H(ctx["owner"]))).json()["version"] == version
        # the bookkeeper is still refused as one, before anything is built
        assert (await c.put("/api/v1/cleanup/inputs", headers=_H(ctx["keeper"]),
                            json={"changes": [renew]})).status_code == 403
        # the file's own projected company doesn't need the records: its changes still save
        ok = await c.put("/api/v1/cleanup/inputs", headers=_H(ctx["owner"]),
                         json={"changes": [{"path": ["forward", "commission_pct"], "value": 10}]})
        assert ok.status_code == 200, ok.text
        body = (await _forward(c, ctx["keeper"])).json()
        assert [f["company"] for f in body["forwards"]] == [COHORT]
        assert body["live_error"] == "The projection from the members' records didn't build. Try again in a moment."
