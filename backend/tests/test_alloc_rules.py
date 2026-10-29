"""Applied rules and their versions (ALLOCATION-RULES-SPEC Phase 3).

The thing being defended is that a closed month stays reconstructable. `update_rule` assigns
whatever the caller sent and `delete_rule` removes the row, which is why a March restatement was
impossible before this: there was nothing left that said what March had been allocated on.

So the tests that matter are the resolution ones -- v1 from January and v2 from April resolving
February and May, and a retroactive v3 resolving February both ways depending on when you ask
-- and the one at the bottom that watches the SQL and fails if anything but `is_current` and
`superseded_by` is ever UPDATEd on a version row.
"""
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.db import SessionLocal, engine
from app.main import app
from app.models import (AllocationContribution, AllocationRule, AllocationRuleVersion,
                        Business, Tenant, User)
from app.seed import seed
from app.services import alloc_rules as AR

TRANSPORT = ASGITransport(app=app)
JAN, APR, MAY, FEB = (dt.date(2026, 1, 1), dt.date(2026, 4, 1),
                      dt.date(2026, 5, 1), dt.date(2026, 2, 1))


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    """The seed carries three businesses; the Advertising scenario needs the two membership
    entities as well. F14: the keys differ between seed, tests and frontend, and they are minted
    at runtime from typed names, so a fixture that needs one creates it."""
    await seed()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        have = {b.key for b in (await s.execute(
            select(Business).where(Business.tenant_id == t.id))).scalars()}
        for key, name, order in (("the_forum", "The Forum", 40),
                                 ("becollective", "beCollective", 50)):
            if key not in have:
                s.add(Business(tenant_id=t.id, key=key, name=name, tag="Mastermind",
                               archetype="program", sort_order=order))
        await s.commit()


async def _ctx():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        biz = {b.key: b.id for b in (await s.execute(
            select(Business).where(Business.tenant_id == t.id))).scalars()}
        actor = (await s.execute(select(User).where(User.tenant_id == t.id))).scalars().first()
        return t.id, biz, actor


@pytest.fixture(autouse=True)
async def _clean():
    tenant_id, _b, _a = await _ctx()
    async with SessionLocal() as s:
        await s.execute(delete(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id))
        await s.execute(delete(AllocationRuleVersion).where(
            AllocationRuleVersion.tenant_id == tenant_id))
        await s.execute(delete(AllocationRule).where(AllocationRule.tenant_id == tenant_id))
        await s.commit()
    yield


def _v(method="split", **kw):
    """A version's terms, with the fixture's Advertising split as the default (Appendix B1)."""
    base = {"method": method, "effective_from": JAN, "reach": "forward",
            "rationale": "Advertising is run centrally and billed to one card."}
    base.update(kw)
    return base


def _targets(biz, *pairs):
    return [{"business_id": str(biz[k]), "share": str(v)} for k, v in pairs]


async def _advertising(tenant_id, biz, actor):
    """Appendix B1. v1 from January: Forum 0.7, beCollective 0.3, pool entity 0.
    v2 from April: 0.5, 0.3, 0.2. Shares are fractions (amendment 12)."""
    async with SessionLocal() as s:
        rule, v1 = await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern="Advertising",
            pool_name="Advertising", source_business_id=biz["springb"],
            **_v(targets=_targets(biz, ("the_forum", "0.7"), ("becollective", "0.3"),
                                  ("springb", "0"))))
    async with SessionLocal() as s:
        v2 = await AR.supersede(
            s, tenant_id, actor, rule.id,
            **_v(effective_from=APR,
                 rationale="Spring B started taking a share in April.",
                 targets=_targets(biz, ("the_forum", "0.5"), ("becollective", "0.3"),
                                  ("springb", "0.2"))))
    return rule, v1, v2


# ── validate_version, pure ────────────────────────────────────────────────────────────────

KNOWN = {"a", "b", "c"}


def test_an_empty_rationale_is_refused_with_a_sentence():
    """The rationale IS the feature: it is what the Statement shows when somebody asks why a
    cost reached their entity. Defaulting it would ship the column and lose the point."""
    for bad in ("", "   ", None):
        with pytest.raises(ValueError) as e:
            AR.validate_version(method="retained", effective_from=JAN, reach="forward",
                                rationale=bad, known_business_ids=KNOWN)
        assert "in your own words" in str(e.value)
        assert str(e.value).endswith(".") or str(e.value).endswith("entity.")


def test_a_split_that_does_not_add_up_is_refused_not_pended():
    """Amendment 16. A stored split that cannot resolve is a bug, not a state -- the engine
    would have to invent something to do with it."""
    with pytest.raises(ValueError) as e:
        AR.validate_version(**_v(targets=[{"business_id": "a", "share": "0.7"},
                                          {"business_id": "b", "share": "0.2"}]),
                            known_business_ids=KNOWN)
    assert "add up to 1" in str(e.value)
    assert "0.9" in str(e.value), "it says what they do add up to"


def test_shares_are_fractions_not_percentages():
    """Amendment 12, and the house convention `books_cleanup.pct` already set. 70 is not a
    share, and accepting it would silently allocate seventy times the pool."""
    with pytest.raises(ValueError) as e:
        AR.validate_version(**_v(targets=[{"business_id": "a", "share": "70"},
                                          {"business_id": "b", "share": "30"}]),
                            known_business_ids=KNOWN)
    assert "between 0 and 1" in str(e.value)
    ok = AR.validate_version(**_v(targets=[{"business_id": "a", "share": "0.7"},
                                           {"business_id": "b", "share": "0.3"}]),
                             known_business_ids=KNOWN)
    assert [t["share"] for t in ok["targets"]] == ["0.7000", "0.3000"]


def test_the_tier_comes_from_the_method_and_a_contradicting_one_is_refused():
    assert AR.validate_version(**_v("retained", targets=None),
                               known_business_ids=KNOWN)["tier"] == 4
    assert AR.validate_version(**_v("direct", targets=[{"business_id": "a", "share": "1"}]),
                               known_business_ids=KNOWN)["tier"] == 1
    assert AR.validate_version(**_v("ended", targets=None),
                               known_business_ids=KNOWN)["tier"] is None
    with pytest.raises(ValueError) as e:
        AR.validate_version(**_v("retained", targets=None, tier=1), known_business_ids=KNOWN)
    assert "tier 4, not tier 1" in str(e.value)


def test_each_method_takes_only_its_own_payload():
    with pytest.raises(ValueError, match="does not take a list of entities"):
        AR.validate_version(**_v("retained", targets=[{"business_id": "a", "share": "1"}]),
                            known_business_ids=KNOWN)
    with pytest.raises(ValueError, match="does not read a driver"):
        AR.validate_version(**_v("retained", driver_id="d"), known_business_ids=KNOWN)
    with pytest.raises(ValueError, match="does not read an outside source"):
        AR.validate_version(**_v("retained", source_spec={"source": "meta_campaigns"}),
                            known_business_ids=KNOWN)
    with pytest.raises(ValueError, match="needs a driver"):
        AR.validate_version(**_v("driven"), known_business_ids=KNOWN)
    with pytest.raises(ValueError, match="needs a map"):
        AR.validate_version(**_v("traced", source_spec={"source": "meta_campaigns"}),
                            known_business_ids=KNOWN)


def test_a_direct_rule_has_one_entity_holding_the_whole_cost():
    with pytest.raises(ValueError, match="exactly one entity"):
        AR.validate_version(**_v("direct", targets=[{"business_id": "a", "share": "0.5"},
                                                    {"business_id": "b", "share": "0.5"}]),
                            known_business_ids=KNOWN)
    with pytest.raises(ValueError, match="its share is 1"):
        AR.validate_version(**_v("direct", targets=[{"business_id": "a", "share": "0.5"}]),
                            known_business_ids=KNOWN)


def test_an_entity_outside_the_workspace_and_a_repeated_one_are_both_refused():
    with pytest.raises(ValueError, match="not in this workspace"):
        AR.validate_version(**_v(targets=[{"business_id": "zz", "share": "1"}]),
                            known_business_ids=KNOWN)
    with pytest.raises(ValueError, match="listed twice"):
        AR.validate_version(**_v(targets=[{"business_id": "a", "share": "0.5"},
                                          {"business_id": "a", "share": "0.5"}]),
                            known_business_ids=KNOWN)


def test_a_version_is_effective_from_a_month_never_a_day():
    out = AR.validate_version(**_v("retained", effective_from=dt.date(2026, 4, 14)),
                              known_business_ids=KNOWN)
    assert out["effective_from"] == APR, "a version starting mid-month makes one month two rules"


def test_an_unknown_method_or_reach_says_what_is_allowed():
    with pytest.raises(ValueError) as e:
        AR.validate_version(**_v("sideways", targets=None), known_business_ids=KNOWN)
    assert "direct" in str(e.value) and "retained" in str(e.value)
    with pytest.raises(ValueError) as e:
        AR.validate_version(**_v("retained", reach="whenever"), known_business_ids=KNOWN)
    assert "forward" in str(e.value)


# ── effective_version, pure ───────────────────────────────────────────────────────────────

def _fake(version, effective_from, created_at):
    return SimpleNamespace(version=version, effective_from=effective_from,
                           created_at=created_at)


def test_february_resolves_to_v1_and_may_to_v2():
    """The Phase 3 gate, on the Advertising scenario. No version ever needs an end date."""
    t0 = dt.datetime(2026, 1, 5, tzinfo=dt.timezone.utc)
    v1, v2 = _fake(1, JAN, t0), _fake(2, APR, t0 + dt.timedelta(days=90))
    assert AR.effective_version([v1, v2], FEB).version == 1
    assert AR.effective_version([v1, v2], MAY).version == 2
    assert AR.effective_version([v1, v2], APR).version == 2, "effective_from is inclusive"
    assert AR.effective_version([v1, v2], dt.date(2025, 12, 1)) is None, "before it started"


def test_a_retro_version_moves_every_month_and_as_of_still_sees_the_old_one():
    """The restatement, and its reconstruction. This is why versions are appended."""
    t0 = dt.datetime(2026, 1, 5, tzinfo=dt.timezone.utc)
    t1 = dt.datetime(2026, 4, 2, tzinfo=dt.timezone.utc)
    t2 = dt.datetime(2026, 9, 9, tzinfo=dt.timezone.utc)
    v1, v2, v3 = _fake(1, JAN, t0), _fake(2, APR, t1), _fake(3, JAN, t2)
    vs = [v1, v2, v3]

    assert AR.effective_version(vs, FEB).version == 3, "retro v3 governs February now"
    assert AR.effective_version(vs, MAY).version == 3, "and every other month"
    before = t2 - dt.timedelta(seconds=1)
    assert AR.effective_version(vs, FEB, as_of=before).version == 1, "as it stood at the close"
    assert AR.effective_version(vs, MAY, as_of=before).version == 2
    assert AR.effective_version(vs, FEB, as_of=t0).version == 1
    # At t0 only v1 exists, and v1 governs May too: a version has no end date, so it runs
    # until something supersedes it. Asserting None here was wrong, and wrong in the direction
    # that matters -- it would have meant a month with a live rule resolving to nothing.
    assert AR.effective_version(vs, MAY, as_of=t0).version == 1


def test_an_ended_version_resolves_and_says_the_rule_claims_nothing():
    t0 = dt.datetime(2026, 1, 5, tzinfo=dt.timezone.utc)
    v1 = _fake(1, JAN, t0)
    v2 = SimpleNamespace(version=2, effective_from=MAY, method="ended",
                         created_at=t0 + dt.timedelta(days=120))
    assert AR.effective_version([v1, v2], APR).version == 1
    got = AR.effective_version([v1, v2], MAY)
    assert got.version == 2 and got.method == "ended"


def test_a_naive_created_at_is_read_as_utc():
    """SQLite hands back naive datetimes. Comparing one against an aware `as_of` raises, and
    the symptom would be a restatement that cannot be reconstructed at all."""
    v1 = _fake(1, JAN, dt.datetime(2026, 1, 5))
    assert AR.effective_version([v1], FEB, as_of=dt.datetime(
        2026, 6, 1, tzinfo=dt.timezone.utc)).version == 1
    assert AR.effective_version([v1], FEB, as_of=dt.datetime(2026, 6, 1)).version == 1


# ── the service ───────────────────────────────────────────────────────────────────────────

async def test_a_rule_and_its_first_version_arrive_together():
    tenant_id, biz, actor = await _ctx()
    rule, v1, v2 = await _advertising(tenant_id, biz, actor)
    async with SessionLocal() as s:
        fresh = await s.get(AllocationRule, rule.id)
        assert fresh.mode == "applied" and fresh.scope_kind == "account"
        assert fresh.source_business_id == biz["springb"], "the pool entity funds it"
        assert fresh.target_business_id is None, "targets live on the version"
        history = await AR.versions_for(s, tenant_id, rule.id)
    assert [v.version for v in history] == [1, 2]
    assert [v.is_current for v in history] == [False, True]
    assert history[0].superseded_by == history[1].id
    assert history[1].superseded_by is None


async def test_the_superseded_versions_terms_are_untouched():
    """The whole reason a closed month stays answerable."""
    tenant_id, biz, actor = await _ctx()
    rule, _v1, _v2 = await _advertising(tenant_id, biz, actor)
    async with SessionLocal() as s:
        history = await AR.versions_for(s, tenant_id, rule.id)
    assert [t["share"] for t in history[0].targets] == ["0.7000", "0.3000", "0.0000"]
    assert [t["share"] for t in history[1].targets] == ["0.5000", "0.3000", "0.2000"]
    assert history[0].effective_from == JAN and history[1].effective_from == APR
    assert "billed to one card" in history[0].rationale
    assert "April" in history[1].rationale
    assert AR.effective_version(history, FEB).version == 1
    assert AR.effective_version(history, MAY).version == 2


async def test_ending_a_rule_appends_rather_than_deactivating():
    tenant_id, biz, actor = await _ctx()
    rule, _v1, _v2 = await _advertising(tenant_id, biz, actor)
    async with SessionLocal() as s:
        v3 = await AR.end_rule(s, tenant_id, actor, rule.id, effective_from=dt.date(2026, 7, 9),
                               rationale="The card moved to each entity's own name.")
    assert v3.method == "ended" and v3.tier is None and v3.targets is None
    assert v3.effective_from == dt.date(2026, 7, 1), "normalized to the month"
    async with SessionLocal() as s:
        history = await AR.versions_for(s, tenant_id, rule.id)
        rule_now = await s.get(AllocationRule, rule.id)
    assert AR.effective_version(history, dt.date(2026, 6, 1)).version == 2
    assert AR.effective_version(history, dt.date(2026, 7, 1)).method == "ended"
    assert rule_now.is_active is True, "the rule is not deactivated; a version ended it"


async def test_the_same_scope_on_the_same_entity_is_refused_once_it_exists():
    tenant_id, biz, actor = await _ctx()
    await _advertising(tenant_id, biz, actor)
    async with SessionLocal() as s:
        with pytest.raises(ValueError, match="already covers that"):
            await AR.create_applied_rule(
                s, tenant_id, actor, scope_kind="account", pattern="  advertising  ",
                pool_name="Ads again", source_business_id=biz["springb"],
                **_v(targets=_targets(biz, ("the_forum", "1"))))
    # A different entity, or a different scope kind, is a different rule.
    async with SessionLocal() as s:
        await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="vendor", pattern="Advertising",
            pool_name="Ads by payee", source_business_id=biz["springb"],
            **_v("direct", targets=_targets(biz, ("the_forum", "1"))))


async def test_an_observed_rule_is_not_versionable():
    tenant_id, biz, actor = await _ctx()
    async with SessionLocal() as s:
        observed = AllocationRule(
            tenant_id=tenant_id, source_business_id=biz["springb"],
            target_business_id=biz["the_forum"], pattern="Shared Service Expenses:",
            pool_name="Shared Services")
        s.add(observed)
        await s.commit()
        rid = observed.id
    async with SessionLocal() as s:
        with pytest.raises(ValueError, match="observed rule"):
            await AR.supersede(s, tenant_id, actor, rid, **_v("retained"))


# ── the routes ────────────────────────────────────────────────────────────────────────────

async def _token(client) -> str:
    r = await client.post("/api/v1/auth/login",
                          json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


async def test_patch_and_delete_refuse_an_applied_rule_with_409():
    """The two routes that would erase a restatement. 409 because the body is fine and the
    state of the thing is what makes it impossible."""
    tenant_id, biz, actor = await _ctx()
    rule, _v1, _v2 = await _advertising(tenant_id, biz, actor)
    async with AsyncClient(transport=TRANSPORT, base_url="http://t") as c:
        h = {"Authorization": f"Bearer {await _token(c)}"}
        r = await c.patch(f"/api/v1/books/allocations/rules/{rule.id}",
                          json={"pool_name": "Renamed"}, headers=h)
        assert r.status_code == 409, r.text
        assert "versioned" in r.json()["detail"]
        d = await c.delete(f"/api/v1/books/allocations/rules/{rule.id}", headers=h)
        assert d.status_code == 409, d.text
    async with SessionLocal() as s:
        still = await s.get(AllocationRule, rule.id)
        assert still is not None and still.pool_name == "Advertising"


async def test_an_observed_rule_can_still_be_patched_and_deleted():
    """The 409 is about applied rules only. Breaking the observed path would break the module
    that is actually in production."""
    tenant_id, biz, _actor = await _ctx()
    async with SessionLocal() as s:
        observed = AllocationRule(
            tenant_id=tenant_id, source_business_id=biz["springb"],
            target_business_id=biz["the_forum"], pattern="Shared Service Expenses:",
            pool_name="Shared Services")
        s.add(observed)
        await s.commit()
        rid = observed.id
    async with AsyncClient(transport=TRANSPORT, base_url="http://t") as c:
        h = {"Authorization": f"Bearer {await _token(c)}"}
        r = await c.patch(f"/api/v1/books/allocations/rules/{rid}",
                          json={"pool_name": "Renamed"}, headers=h)
        assert r.status_code == 200, r.text
        d = await c.delete(f"/api/v1/books/allocations/rules/{rid}", headers=h)
        assert d.status_code == 200, d.text


async def test_the_routes_create_version_and_end_a_rule():
    tenant_id, biz, _actor = await _ctx()
    async with AsyncClient(transport=TRANSPORT, base_url="http://t") as c:
        h = {"Authorization": f"Bearer {await _token(c)}"}
        body = {"scope_kind": "account", "pattern": "Advertising", "pool_name": "Advertising",
                "source_business_id": str(biz["springb"]), "method": "split",
                "effective_from": "2026-01-01", "reach": "forward",
                "rationale": "Run centrally, billed to one card.",
                "targets": _targets(biz, ("the_forum", "0.7"), ("becollective", "0.3"))}
        r = await c.post("/api/v1/books/allocations/applied", json=body, headers=h)
        assert r.status_code == 200, r.text
        rid = r.json()["id"]
        assert r.json()["version"]["version"] == 1
        assert r.json()["version"]["tier_label"] == "Driven"

        bad = {**body, "rationale": "   "}
        del bad["scope_kind"], bad["pattern"], bad["pool_name"], bad["source_business_id"]
        r = await c.post(f"/api/v1/books/allocations/applied/{rid}/versions", json=bad, headers=h)
        assert r.status_code == 400 and "in your own words" in r.json()["detail"]

        v2 = {"method": "split", "effective_from": "2026-04-01", "reach": "from_date",
              "rationale": "Spring B took a share.",
              "targets": _targets(biz, ("the_forum", "0.5"), ("becollective", "0.3"),
                                  ("springb", "0.2"))}
        r = await c.post(f"/api/v1/books/allocations/applied/{rid}/versions", json=v2, headers=h)
        assert r.status_code == 200 and r.json()["version"]["version"] == 2

        r = await c.post(f"/api/v1/books/allocations/applied/{rid}/end",
                         json={"effective_from": "2026-07-01",
                               "rationale": "Cards moved."}, headers=h)
        assert r.status_code == 200 and r.json()["version"]["method"] == "ended"

        r = await c.get(f"/api/v1/books/allocations/applied/{rid}/versions", headers=h)
        got = r.json()["versions"]
        assert [v["version"] for v in got] == [1, 2, 3]
        assert [v["is_current"] for v in got] == [False, False, True]


# ── the append-only gate ──────────────────────────────────────────────────────────────────

async def test_no_update_reaches_a_version_except_is_current_and_superseded_by():
    """The Phase 3 gate, watched at the SQL rather than asserted about the code.

    A reviewer can miss an assignment; the wire cannot. Every statement the engine executes
    while a rule is created, superseded and ended is captured, and any UPDATE on
    `allocation_rule_version` that SETs anything but those two columns fails this.
    """
    tenant_id, biz, actor = await _ctx()
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()))

    sa.event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        rule, _v1, _v2 = await _advertising(tenant_id, biz, actor)
        async with SessionLocal() as s:
            await AR.end_rule(s, tenant_id, actor, rule.id, effective_from=MAY,
                              rationale="Cards moved to each entity.")
    finally:
        sa.event.remove(engine.sync_engine, "before_cursor_execute", record)

    updates = [q for q in seen
               if q.upper().startswith("UPDATE") and "allocation_rule_version" in q]
    assert updates, "no UPDATE at all means this gate watched nothing"
    allowed = {"is_current", "superseded_by"}
    for q in updates:
        body = q[q.upper().index(" SET ") + 5:]
        body = body[:body.upper().index(" WHERE ")] if " WHERE " in body.upper() else body
        cols = {part.split("=")[0].strip().strip('"') for part in body.split(",")}
        assert cols <= allowed, f"an UPDATE set {sorted(cols - allowed)}: {q}"

    # And nothing was deleted either: an appended history with a hole is not a history.
    deletes = [q for q in seen
               if q.upper().startswith("DELETE") and "allocation_rule_version" in q]
    assert not deletes, deletes
    async with SessionLocal() as s:
        assert [v.version for v in await AR.versions_for(s, tenant_id, rule.id)] == [1, 2, 3]
