"""Win the Day as a playbook the workspace writes (WHOS-WHO-WIN-THE-DAY-SPEC.md, phases 2 and 3).

The portal's Win the Day was a compiled-in checklist with nothing behind it. These hold its
replacement to the spec: every section is validated with the field named, a saved section is a
draft until somebody publishes, a playbook moves between workspaces as a file that is checked whole
and never overwrites by accident, the page the portal gets is numbered, grouped, linked and counted,
and each agent's targets come from their own numbers, then the on-ramp, then the team's.
"""
import datetime as dt

import httpx
import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.main import app
from app.models import (Business, Domain, Integration, IntranetMember, IntranetRole,
                        IntranetWorkspace, IntranetWtdList, IntranetWtdPlaybook,
                        IntranetWtdScript, Tenant, User)
from app.security import enc, hash_pw, make_token
from app.services import wtd_playbook as w
from app.services.intranet_bootstrap import bootstrap_intranet
from tests.fub_fake import fake  # noqa: F401 -- `fake` is a fixture

ASGI = httpx.ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


def _client():
    return httpx.AsyncClient(transport=ASGI, base_url="http://testserver")


def _H(token, host):
    return {"Authorization": f"Bearer {token}", "x-tenant-host": host}


async def _workspace(slug: str, *, fub: bool = True) -> dict:
    """A bootstrapped portal and, unless told otherwise, a Follow Up Boss connection to the
    account `acme`."""
    async with SessionLocal() as s:
        t = Tenant(slug=slug, name=slug.title(), status="active", plan="portfolio",
                   config={"features": {"intranet": True}})
        s.add(t)
        await s.flush()
        s.add(Domain(tenant_id=t.id, hostname=f"{slug}.localhost", is_primary=True))
        b = Business(tenant_id=t.id, key="main", name="Main", tag="Business", sort_order=0)
        s.add(b)
        owner = User(tenant_id=t.id, email=f"owner@{slug}.test", name="Owner",
                     password_hash=hash_pw("pw"), role="owner", status="active", tab_access=[],
                     token_version=0)
        s.add(owner)
        await s.flush()
        await bootstrap_intranet(s, t.id, workspace_name=slug.title(), subdomain=slug,
                                 owner_email=owner.email)
        wsrow = (await s.execute(select(IntranetWorkspace).where(
            IntranetWorkspace.tenant_id == t.id))).scalar_one()
        wsrow.timezone = "America/Denver"
        if fub:
            s.add(Integration(tenant_id=t.id, provider="fub", business_id=b.id, status="connected",
                              access_token_enc=enc("fka_test_secret_key"),
                              config={"fub_state": {"account_id": 777, "account_domain": "acme"}}))
        await s.commit()
        roles = {r.key: r.id for r in (await s.execute(select(IntranetRole).where(
            IntranetRole.tenant_id == t.id))).scalars()}
        return {"tenant_id": t.id, "host": f"{slug}.localhost", "roles": roles,
                "owner": make_token(owner.id, t.id, 0)}


async def _agent(ws, email, **member) -> dict:
    async with SessionLocal() as s:
        u = User(tenant_id=ws["tenant_id"], email=email, name=email.split("@")[0].title(),
                 password_hash=hash_pw("pw"), role="member", status="active", tab_access=[],
                 token_version=0)
        s.add(u)
        await s.flush()
        m = IntranetMember(tenant_id=ws["tenant_id"], full_name=u.name, email=email,
                           role_id=ws["roles"]["member"], status="Active", auth_source="Manual",
                           user_id=u.id, **member)
        s.add(m)
        await s.commit()
        return {"token": make_token(u.id, ws["tenant_id"], 0), "member_id": str(m.id)}


def _bundle() -> dict:
    """A small but complete playbook: every tab has something on it."""
    return {
        "format": "axcion.wtd-playbook", "version": 1,
        "content": {
            "header": {"eyebrow": "Agent Playbook", "title": "Win the Day.",
                       "lede": "The daily workflow.",
                       "meta": [{"k": "Home Base", "v": "Follow Up Boss"}],
                       "cta": {"label": "", "url": None}},
            "rule": {"eyebrow": "The One Rule", "text": "Log every call.", "sub": "Before the next."},
            "tabs": {"run": "Today’s Run", "lists": "The {n} Lists", "call": "The Call",
                     "scripts": "Scripts", "numbers": "The Numbers", "tools": "PLACE Tools"},
            "run": {"title": "{N} Blocks. Same Shape Every Day.", "intro": "About two hours.",
                    "blocks": [{"key": "b1", "title": "Time-Sensitive", "minutes": 10, "text": "Clock."},
                               {"key": "b2", "title": "Close Out", "minutes": 15, "text": "Tally."}],
                    "trips": [{"title": "Letting one call end the block.", "text": "Finish it."}],
                    "weekly": {"eyebrow": "Once a week", "items": [{"title": "Meeting Day",
                                                                    "text": "Clean-ups."}]}},
            "lists": {"title": "Lists 1 Through {n}, in Order.", "intro": "Smart lists.",
                      "groups": [{"key": "hot", "label": "Time-sensitive"},
                                 {"key": "long", "label": "The Long Game"}],
                      "trips": [],
                      "ponds": {"eyebrow": "If you have pond access", "text": "Parallel lists.",
                                "links": [{"label": "New Leads", "list_id": "74"}]}},
            "call": {"title": "How to Work a List.", "intro": "Same way.",
                     "steps": [{"title": "Work top down.", "text": "The order is the decision."}],
                     "compliance": {"eyebrow": "Compliance", "text": "Honor do-not-call."},
                     "habits_title": "Keep These Habits", "habits": [{"title": "Log first.",
                                                                     "text": "Always."}]},
            "scripts": {"title": "The Script.", "intro": "Every script is live.",
                        "library": {"label": "Open the Full Script Library",
                                    "url": "https://scripts.example.test/library"},
                        "groups": [{"key": "leads", "label": "Leads", "sub": "Lists 1, 2"}],
                        "fallback": {"eyebrow": "If none fit", "text": "Ask Gabbi."}},
            "numbers": {"title": "The Scoreboard.", "intro": "Conversations matter.",
                        "rows": [{"label": "Dials", "daily": "25 / day", "weekly": "125 / week",
                                  "tally": {"key": "dials", "short": "dials", "goal": 25}},
                                 {"label": "Conversations Logged", "daily": "8 / day",
                                  "weekly": "40 / week", "highlight": True,
                                  "tally": {"key": "convos", "short": "convos", "goal": 8}},
                                 {"label": "Appointments Set", "daily": "—",
                                  "weekly": "1+ / week", "tally": {"key": "appts"}}],
                        "footnote": "Team defaults.",
                        "onramp": {"title": "The New Agent On-ramp", "intro": "Ramps targets.",
                                   "phases": [{"label": "Week One", "through_day": 5,
                                               "goals": {"dials": 15, "convos": 4}, "focus": "Phone."},
                                              {"label": "Week Two", "through_day": 10,
                                               "goals": {"dials": 20, "convos": 6}, "focus": "Market."},
                                              {"label": "Day 11 Onward",
                                               "goals": {"dials": 25, "convos": 8}, "focus": "Rhythm."}]}},
            "tools": {"title": "The Tools.", "intro": "Four tools.",
                      "items": [{"block": "Block 1 · Power Up", "name": "AI Call Coach",
                                 "url": "https://coach.example.test/", "tagline": "Practice.",
                                 "text": "Two reps."}]},
        },
        "scripts": [
            {"ref": "s1", "name": "LPMAMA", "chip": "LPMAMA", "url": "https://scripts.example.test/lpmama",
             "description": "The core framework.", "group_key": "leads"},
            {"ref": "s2", "name": "Sphere Model", "url": "https://scripts.example.test/sphere"},
        ],
        "lists": [
            {"name": "Recently Active Leads", "external_list_id": "35", "cadence": "Daily",
             "kind": "clear", "description": "Re-engaged.", "group_key": "hot", "block_key": "b1",
             "scripts": ["s1", "s2"]},
            {"name": "New Leads", "external_list_id": "36", "cadence": "Daily", "kind": "top_down",
             "group_key": "hot", "block_key": "b1", "scripts": ["s1"]},
            {"name": "Sphere / Past Clients", "external_list_id": "84", "cadence": "Ongoing",
             "kind": "scan", "group_key": "long", "scripts": []},
        ],
    }


async def _import(c, ws, bundle=None, replace=None):
    body = {"bundle": bundle or _bundle()}
    if replace is not None:
        body["replace"] = replace
    return await c.post("/api/console/wtd/import", headers=_H(ws["owner"], ws["host"]), json=body)


async def _publish(c, ws):
    r = await c.post("/api/console/publish", headers=_H(ws["owner"], ws["host"]), json={})
    assert r.status_code == 200, r.text


async def _wtd(c, ws, token=None):
    r = await c.get("/api/v1/intranet/config", headers=_H(token or ws["owner"], ws["host"]))
    assert r.status_code == 200, r.text
    return r.json()["config"]["content"]["wtd"]


# ── the document ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("section,value,field", [
    ("header", {"title": "x" * 81}, "header.title"),
    ("header", {"cta": {"url": "javascript:alert(1)"}}, "header.cta.url"),
    ("header", {"meta": [{"k": "a", "v": "b"}] * 4}, "header.meta"),
    ("run", {"blocks": [{"key": "b", "title": "One"}, {"key": "b", "title": "Two"}]}, "run"),
    ("run", {"blocks": [{"key": "has space", "title": "One"}]}, "run.blocks.0.key"),
    ("lists", {"ponds": {"links": [{"label": "Pond", "list_id": "74a"}]}}, "lists.ponds.links.0"),
    ("numbers", {"rows": [{"label": "x", "tally": {"key": f"t{i}"}} for i in range(5)]}, "numbers"),
    ("numbers", {"onramp": {"phases": [{"label": "p", "goals": {"dials": 1}}]}}, "numbers"),
    ("numbers", {"onramp": {"phases": [{"label": "a"}, {"label": "b", "through_day": 3}]}},
     "numbers"),
    ("tools", {"items": [{"name": "Coach", "surprise": True}]}, "tools.items.0.surprise"),
])
def test_a_section_the_page_cannot_draw_is_refused_with_the_field_named(section, value, field):
    with pytest.raises(w.SectionError) as e:
        w.clean_section(section, value)
    assert e.value.field == field, (e.value.field, e.value.message)


def test_counts_in_a_title_stay_true():
    assert w.fill("{N} Blocks. Same Shape Every Day.", 5) == "Five Blocks. Same Shape Every Day."
    assert w.fill("The {n} Lists", 13) == "The 13 Lists"
    assert w.number_words(86) == "eighty-six"
    assert w.fill("{N} agents", 1200) == "1200 agents"      # past words helping anybody


def test_targets_are_personal_then_the_on_ramp_then_the_teams():
    numbers = w.clean_section("numbers", _bundle()["content"]["numbers"])
    monday = dt.date(2026, 9, 14)
    tuesday = monday + dt.timedelta(days=1)
    assert w.goals_for(numbers, None, None, tuesday) == ({"dials": 25, "convos": 8, "appts": None},
                                                         None)
    assert w.goals_for(numbers, monday, None, tuesday)[0]["dials"] == 15
    # Working days: started the Saturday before last, so today is working day 7 -> Week Two.
    assert w.goals_for(numbers, monday - dt.timedelta(days=9), None, tuesday)[1] == "Week Two"
    assert w.goals_for(numbers, monday, {"dials": 40}, tuesday)[0] == {
        "dials": 40, "convos": 4, "appts": None}
    assert [w.working_day(monday, monday + dt.timedelta(days=d)) for d in (0, 4, 5, 6, 7)] == [
        1, 5, 5, 5, 6]


# ── the console ───────────────────────────────────────────────────────────────────────────

async def test_a_saved_section_is_a_draft_until_somebody_publishes():
    ws = await _workspace("wtddraft")
    async with _client() as c:
        saved = await c.patch("/api/console/wtd/playbook", headers=_H(ws["owner"], ws["host"]),
                              json={"header": {"title": "Win the Day.", "eyebrow": "Agent Playbook"}})
        assert saved.status_code == 200, saved.text
        assert saved.json()["pending_changes"] >= 1
        before = await _wtd(c, ws)
        await _publish(c, ws)
        after = await _wtd(c, ws)
    assert before["has_playbook"] is False and before["header"]["title"] == ""
    assert after["has_playbook"] is True and after["header"]["title"] == "Win the Day."


async def test_a_bad_section_is_refused_and_nothing_is_saved():
    ws = await _workspace("wtdbad")
    async with _client() as c:
        r = await c.patch("/api/console/wtd/playbook", headers=_H(ws["owner"], ws["host"]),
                          json={"tools": {"items": [{"name": "x", "url": "ftp://nope"}]}})
        got = await c.get("/api/console/wtd/playbook", headers=_H(ws["owner"], ws["host"]))
    assert r.status_code == 422
    assert r.json()["detail"]["errors"][0]["field"] == "tools.items.0.url"
    assert got.json()["exists"] is False


async def test_the_new_tables_join_the_publish_cycle():
    from app.routers.console import _publishable_models
    found = set(_publishable_models())
    assert {IntranetWtdPlaybook, IntranetWtdScript, IntranetWtdList} <= found


async def test_import_refuses_to_overwrite_and_a_round_trip_keeps_everything():
    ws = await _workspace("wtdimport")
    async with _client() as c:
        first = await _import(c, ws)
        assert first.status_code == 200, first.text
        assert (first.json()["lists"], first.json()["scripts"]) == (3, 2)
        again = await _import(c, ws)
        exported = (await c.get("/api/console/wtd/export", headers=_H(ws["owner"], ws["host"]))).json()
        replaced = await _import(c, ws, exported, replace=True)
    assert again.status_code == 409, "an import overwrote a playbook without being told to"
    assert replaced.status_code == 200, replaced.text
    bundle = _bundle()
    assert exported["content"] == w.clean_content(bundle["content"])
    assert [row["name"] for row in exported["lists"]] == [row["name"] for row in bundle["lists"]]
    assert [row["scripts"] for row in exported["lists"]] == [["s1", "s2"], ["s1"], []]
    assert [s["name"] for s in exported["scripts"]] == ["LPMAMA", "Sphere Model"]
    async with SessionLocal() as s:
        lists = await s.scalar(select(func.count()).select_from(IntranetWtdList).where(
            IntranetWtdList.tenant_id == ws["tenant_id"]))
        drafts = await s.scalar(select(func.count()).select_from(IntranetWtdList).where(
            IntranetWtdList.tenant_id == ws["tenant_id"], IntranetWtdList.published_at.is_(None)))
    assert lists == 3 and drafts == 3, "an import must arrive as a draft, replacing, not adding"


async def test_a_bad_file_is_refused_whole_and_names_the_field():
    ws = await _workspace("wtdbadfile")
    bundle = _bundle()
    bundle["lists"][1]["group_key"] = "nowhere"
    async with _client() as c:
        r = await _import(c, ws, bundle)
    assert r.status_code == 422
    assert r.json()["detail"]["errors"][0]["field"] == "file.lists.1.group_key"
    async with SessionLocal() as s:
        written = await s.scalar(select(func.count()).select_from(IntranetWtdScript).where(
            IntranetWtdScript.tenant_id == ws["tenant_id"]))
    assert written == 0, "half a file was written"


async def test_a_list_must_point_at_this_playbook_and_this_workspaces_scripts():
    ws = await _workspace("wtdrefs")
    other = await _workspace("wtdrefs2")
    async with _client() as c:
        await _import(c, ws)
        await _import(c, other)
        lists = (await c.get("/api/console/wtd-lists", headers=_H(ws["owner"], ws["host"]))).json()
        theirs = (await c.get("/api/console/wtd/scripts",
                              headers=_H(other["owner"], other["host"]))).json()["items"]
        target = lists["items"][0]["id"]
        bad_group = await c.patch(f"/api/console/wtd-lists/{target}",
                                  headers=_H(ws["owner"], ws["host"]), json={"group_key": "nope"})
        foreign = await c.patch(f"/api/console/wtd-lists/{target}",
                                headers=_H(ws["owner"], ws["host"]),
                                json={"script_ids": [theirs[0]["id"]]})
        ok = await c.patch(f"/api/console/wtd-lists/{target}", headers=_H(ws["owner"], ws["host"]),
                           json={"group_key": "long", "kind": "scan", "cadence": "Weekly"})
    assert bad_group.status_code == 422
    assert foreign.status_code == 422, "a list took another workspace's script"
    assert ok.status_code == 200, ok.text
    assert (ok.json()["item"]["group_key"], ok.json()["item"]["kind"]) == ("long", "scan")


async def test_removing_a_script_takes_it_off_every_list():
    ws = await _workspace("wtdscriptgone")
    async with _client() as c:
        await _import(c, ws)
        scripts = (await c.get("/api/console/wtd/scripts",
                               headers=_H(ws["owner"], ws["host"]))).json()["items"]
        lpmama = next(s["id"] for s in scripts if s["name"] == "LPMAMA")
        r = await c.delete(f"/api/console/wtd/scripts/{lpmama}", headers=_H(ws["owner"], ws["host"]))
        lists = (await c.get("/api/console/wtd-lists", headers=_H(ws["owner"], ws["host"]))).json()
    assert r.status_code == 200, r.text
    assert all(lpmama not in row["script_ids"] for row in lists["items"])
    assert lists["items"][0]["script_ids"] == [next(s["id"] for s in scripts
                                                    if s["name"] == "Sphere Model")]


async def test_deleting_and_reordering_lists_keeps_the_numbers_one_to_n():
    ws = await _workspace("wtdnumbers")
    async with _client() as c:
        await _import(c, ws)
        h = _H(ws["owner"], ws["host"])
        items = (await c.get("/api/console/wtd-lists", headers=h)).json()["items"]
        # Reversed in one request: every position collides with another row on the way.
        flipped = await c.put("/api/console/wtd-lists/order", headers=h,
                              json={"ids": [i["id"] for i in reversed(items)]})
        assert flipped.status_code == 200, flipped.text
        assert [i["name"] for i in flipped.json()["items"]] == [
            "Sphere / Past Clients", "New Leads", "Recently Active Leads"]
        gone = await c.delete(f"/api/console/wtd-lists/{items[1]['id']}", headers=h)
        after = (await c.get("/api/console/wtd-lists", headers=h)).json()["items"]
    assert gone.status_code == 200, gone.text
    assert [(i["position"], i["name"]) for i in after] == [
        (1, "Sphere / Past Clients"), (2, "Recently Active Leads")]


async def test_the_account_check_names_missing_ids_and_never_the_key(fake):
    ws = await _workspace("wtdcheck")
    fake.smart_lists = [{"id": 35, "name": "01. Recently Active Leads"},
                        {"id": 36, "name": "03. New Leads"}]
    async with _client() as c:
        await _import(c, ws)
        r = await c.get("/api/console/wtd/lists/check", headers=_H(ws["owner"], ws["host"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["checked"] == 4
    assert {(m["kind"], m["id"]) for m in body["missing"]} == {("list", "84"), ("pond", "74")}
    assert {f["account_name"] for f in body["found"]} == {"01. Recently Active Leads", "03. New Leads"}
    assert "fka_test_secret_key" not in r.text


# ── the portal ────────────────────────────────────────────────────────────────────────────

async def test_the_portal_gets_the_page_numbered_grouped_linked_and_counted():
    ws = await _workspace("wtdpage")
    async with _client() as c:
        await _import(c, ws)
        await _publish(c, ws)
        wtd = await _wtd(c, ws)
    assert [t["label"] for t in wtd["tabs"]] == ["Today’s Run", "The 3 Lists", "The Call",
                                                 "Scripts", "The Numbers", "PLACE Tools"]
    assert wtd["run"]["title"] == "Two Blocks. Same Shape Every Day."
    assert wtd["lists"]["title"] == "Lists 1 Through 3, in Order."
    items = wtd["lists"]["items"]
    assert [(i["no"], i["name"], i["group_key"]) for i in items] == [
        ("01", "Recently Active Leads", "hot"), ("02", "New Leads", "hot"),
        ("03", "Sphere / Past Clients", "long")]
    assert items[0]["url"] == "https://acme.followupboss.com/2/people/list/35"
    assert items[0]["scripts"] == [{"name": "LPMAMA", "url": "https://scripts.example.test/lpmama"},
                                   {"name": "Sphere Model", "url": "https://scripts.example.test/sphere"}]
    assert [b["lists"] for b in wtd["run"]["blocks"]] == [["01", "02"], []]
    assert wtd["lists"]["ponds"]["links"] == [
        {"label": "New Leads", "url": "https://acme.followupboss.com/2/people/list/74"}]
    assert wtd["header"]["cta"] == {"label": "Open Follow Up Boss →",
                                    "url": "https://acme.followupboss.com/2/people"}
    # Only grouped scripts are cards on the Scripts tab; Sphere Model is a chip only.
    assert [[s["name"] for s in g["items"]] for g in wtd["scripts"]["groups"]] == [["LPMAMA"]]
    assert [t["key"] for t in wtd["sheet"]["tallies"]] == ["dials", "convos", "appts"]
    assert wtd["goals"] == {"dials": 25, "convos": 8, "appts": None}
    assert [k["key"] for k in wtd["kinds"]] == ["clear", "top_down", "scan"]


async def test_a_workspace_with_no_account_has_no_link_to_invent():
    ws = await _workspace("wtdnofub", fub=False)
    async with _client() as c:
        await _import(c, ws)
        await _publish(c, ws)
        wtd = await _wtd(c, ws)
    assert wtd["header"]["cta"] == {"label": "", "url": None}
    assert wtd["lists"]["items"][0]["url"] is None
    assert wtd["lists"]["ponds"]["links"][0]["url"] is None


async def test_each_agent_gets_their_own_targets():
    ws = await _workspace("wtdgoals")
    today = dt.datetime.now(dt.timezone.utc).date()
    new_hire = await _agent(ws, "new@wtdgoals.test", started_on=today)
    veteran = await _agent(ws, "vet@wtdgoals.test")
    async with _client() as c:
        await _import(c, ws)
        await _publish(c, ws)
        h = _H(ws["owner"], ws["host"])
        personal = await c.patch(f"/api/console/members/{veteran['member_id']}", headers=h,
                                 json={"wtd_goals": {"dials": 40}})
        unknown = await c.patch(f"/api/console/members/{veteran['member_id']}", headers=h,
                                json={"wtd_goals": {"doors": 5}})
        mine = await _wtd(c, ws, new_hire["token"])
        theirs = await _wtd(c, ws, veteran["token"])
        people = (await c.get("/api/console/wtd/people", headers=h)).json()
    assert personal.status_code == 200, personal.text
    assert unknown.status_code == 422, "a target for something the scoreboard never counts"
    # The workspace's "today" is Denver's; a new hire is in Week One unless UTC has already
    # turned over into tomorrow there, which still leaves them inside it.
    assert mine["onramp_phase"] == "Week One" and mine["goals"]["dials"] == 15
    assert theirs["onramp_phase"] is None and theirs["goals"] == {"dials": 40, "convos": 8,
                                                                  "appts": None}
    by_name = {p["name"]: p for p in people["items"]}
    assert by_name["Vet"]["goals_today"]["dials"] == 40
    assert [t["key"] for t in people["tallies"]] == ["dials", "convos", "appts"]


async def test_lists_still_show_for_a_workspace_that_never_wrote_a_playbook():
    ws = await _workspace("wtdlistsonly")
    async with SessionLocal() as s:
        s.add(IntranetWtdList(tenant_id=ws["tenant_id"], position=1, name="Chair turns",
                              provider="manual", active=True,
                              published_at=dt.datetime.now(dt.timezone.utc)))
        await s.commit()
    async with _client() as c:
        wtd = await _wtd(c, ws)
    assert wtd["has_playbook"] is False
    assert [t["key"] for t in wtd["tabs"]] == ["lists"]
    assert wtd["lists"]["groups"] == [{"key": "", "label": ""}]
    assert wtd["lists"]["items"][0]["name"] == "Chair turns"


# ── the rebrand: a file exported before the rename still imports ──────────────────────────
def test_a_playbook_exported_before_the_rebrand_still_imports():
    """`format` names the product, and the product was renamed Acumyn -> Axcion in September
    2026. Unlike everything else the rename touched, this string leaves the system: it is
    written into an EXPORT FILE that sits on somebody's disk or a shared drive and comes back
    months later. No migration can reach those files.

    Accepting only the new spelling would reject every playbook exported before the rename,
    permanently, with a validation error naming a field the person never wrote and cannot see.
    That is why `_Bundle.format` takes both. Exports emit the new one.
    """
    old = {**_bundle(), "format": "acumyn.wtd-playbook"}
    cleaned = w.clean_bundle(old)
    assert cleaned["content"] == w.clean_content(_bundle()["content"]), \
        "the old envelope must not change how the document itself is read"


def test_a_bundle_from_some_other_product_is_still_refused():
    """The compatibility arm widens the door by exactly one known value, not to anything. A
    file that is not a playbook at all must still be rejected on its format alone."""
    for bogus in ("notacumyn.wtd-playbook", "axcion.some-other-thing", "", None):
        with pytest.raises(Exception):
            w.clean_bundle({**_bundle(), "format": bogus})


def test_an_export_is_written_with_the_new_name():
    """The compatibility is one-way: old files are read, new files are never written."""
    assert w.FORMAT == "axcion.wtd-playbook"
    out = w.export_bundle(_bundle()["content"], [], [])
    assert out["format"] == "axcion.wtd-playbook"
