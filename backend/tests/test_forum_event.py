"""The Forum's quarterly VIP event (FORUM-EVENT-SPEC.md Phase 2).

The fixture is Spring's real Q4 2026 shape, because the numbers that matter here were only
discovered by looking at live data: 24 guest-tagged contacts of whom 18 sit in VIP Guest:
Confirmed, 5 have no opportunity in the funnel at all and 1 RSVP'd after being marked
unresponsive; 35 members registered under a different tag; and one contact carrying both tags.

Counting stages instead of tags reports 18 and looks correct. These tests exist so that cannot
come back.
"""
import datetime as dt

import pytest
from sqlalchemy import select, delete

from app.db import SessionLocal
from app.models import Business, ForumEvent, ForumEventGuest, ForumEventWeekly
from app.seed import seed
from app.services import forum_event as FE

# The event, and a clock fixed far enough out that the pace curve has something to say.
STARTS = dt.date(2026, 11, 13)          # Connor: 13-15 November 2026
ASOF = dt.date(2026, 10, 5)             # 39 days out
GOAL = 60
CURVE = {"60": 0.10, "39": 0.45, "14": 0.80, "0": 1.00}

PRICES = {
    "Single - PIF": {"acv": 12000, "upfront": 12000},
    "Single - Monthly": {"acv": 14400, "upfront": 1200, "monthly": 1200, "months": 12},
    "Dual - PIF": {"acv": 20000, "upfront": 20000},
    "Dual - Monthly": {"acv": 24000, "upfront": 2000, "monthly": 2000, "months": 12},
}


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _event(**over):
    """Spring's Q4 shape. Returns primitives, never ORM objects."""
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        tid = biz.tenant_id
        await s.execute(delete(ForumEventWeekly).where(ForumEventWeekly.tenant_id == tid))
        await s.execute(delete(ForumEventGuest).where(ForumEventGuest.tenant_id == tid))
        await s.execute(delete(ForumEvent).where(ForumEvent.tenant_id == tid))
        fields = dict(
            name="The Forum Q4 2026", slug="q4-2026", status="selling",
            starts_on=STARTS, ends_on=dt.date(2026, 11, 15),
            window_start=dt.date(2026, 8, 1), window_end=STARTS,
            guest_goal=GOAL, pace_curve=CURVE, pace_tolerance=0.08,
            guest_tags=["the forum q4 2026 guest rsvp"],
            member_tags=["the forum q4 2026 rsvp"],
            stage_map=FE.DEFAULT_STAGE_MAP,
        )
        fields.update(over)
        ev = ForumEvent(tenant_id=tid, business_id=biz.id, **fields)
        s.add(ev)
        await s.flush()
        eid = ev.id

        n = [0]

        def guest(group, kind="guest", **kw):
            n[0] += 1
            s.add(ForumEventGuest(
                tenant_id=tid, event_id=eid, contact_id=f"c{n[0]}", kind=kind, group=group,
                opportunity_id=kw.pop("opp", f"o{n[0]}"), **kw))

        # 18 in VIP Guest: Confirmed, with an opportunity each.
        for _ in range(18):
            guest("registered")
        # 5 tagged RSVPs with NO opportunity in the funnel - the people this tab exists for.
        for _ in range(5):
            guest("uncategorized", opp=None)
        # 1 tagged RSVP whose opportunity says Cold Nurture: tag and stage disagree.
        guest("nurture")
        # 35 members registered under their own tag. Never derived by subtraction.
        for _ in range(35):
            guest("uncategorized", kind="member", opp=None)
        await s.commit()
        return tid, eid


async def _compute(tid, eid, today=ASOF):
    async with SessionLocal() as s:
        ev = (await s.execute(select(ForumEvent).where(ForumEvent.id == eid))).scalar_one()
        return await FE.compute_event(s, tid, ev, today=today)


# ── the vocabulary ────────────────────────────────────────────────────────────────────────
def test_a_won_opportunity_is_converted_even_though_its_text_also_says_nurture():
    # "Previously Attended Event" is in `nurture`; "Won: Onboarded" is in `converted`. Precedence
    # is the whole reason GROUP_ORDER exists - a substring matching two groups is the oldest bug
    # in stage mapping.
    assert FE.classify_stage("Won: Onboarded", None) == "converted"
    assert FE.classify_stage("Hot Nurture: Current VIP Event", None) == "attending"
    assert FE.classify_stage("Warm Nurture: Upcoming VIP Event", None) == "nurture"
    assert FE.classify_stage("VIP Guest: Confirmed", None) == "registered"
    assert FE.classify_stage("Sent Contract: Dual - PIF", None) == "deciding"
    assert FE.classify_stage("Something nobody mapped", None) == "uncategorized"


def test_guests_match_as_substrings_and_members_match_exactly():
    guest_tags = ["the forum q4 2026 guest rsvp"]
    member_tags = ["the forum q4 2026 rsvp"]
    # The `paid` variant is still a guest.
    assert FE.match_guest_tag(["the forum q4 2026 guest rsvp paid"], guest_tags)
    assert FE.match_guest_tag(["The Forum Q4 2026 Guest RSVP"], guest_tags)     # case
    # The member tag must NOT swallow the guest tag, and vice versa.
    assert not FE.match_member_tag(["the forum q4 2026 guest rsvp"], member_tags)
    assert FE.match_member_tag(["the forum q4 2026 rsvp"], member_tags)
    assert not FE.match_guest_tag(["the forum q4 2026 rsvp"], guest_tags)
    # THE CASE THAT DISCRIMINATES. The two lines above pass under a substring implementation
    # too, because "...q4 2026 rsvp" happens not to be a substring of "...q4 2026 guest rsvp" -
    # they assert the right thing and prove nothing. A member tag that is a PREFIX of a longer
    # tag is the real risk, and Spring already has the guest-side version of it
    # ("...guest rsvp" / "...guest rsvp paid"). Someone adding "...rsvp declined" would have
    # every decliner counted as registered.
    assert not FE.match_member_tag(["the forum q4 2026 rsvp declined"], member_tags)
    assert not FE.match_member_tag(["x the forum q4 2026 rsvp"], member_tags)


def test_comped_is_a_configurable_substring():
    assert FE.is_comped(["q3 august 2026 guest vip comped"], "comp")
    # November's sponsor seats carry no "comp" - parked, and this is what parking it means.
    assert not FE.is_comped(["forum vip guest sisu nov 2026"], "comp")
    assert FE.is_comped(["forum vip guest sisu nov 2026"], "sisu")


# ── the headline numbers ──────────────────────────────────────────────────────────────────
async def test_guests_come_from_the_tag_not_the_stage():
    """The hero is 24 and the funnel's `registered` row is 18, deliberately. Five of the
    difference have no opportunity at all and one is parked in Cold Nurture."""
    tid, eid = await _event()
    d = await _compute(tid, eid)
    r = d["registration"]
    assert r["guests"] == 24
    assert r["guests_without_opp"] == 5
    assert r["guests_stage_conflict"] == 1
    funnel = {f["key"]: f["count"] for f in d["funnel"]}
    assert funnel["registered"] == 18, "the funnel counts stages; the hero counts tags"


async def test_a_guest_who_converts_still_counts_as_a_guest():
    """Phase 2's gate. If `guests` were the population of one stage, the number would FALL every
    time a sale succeeded - the tab would punish the thing it exists to encourage."""
    tid, eid = await _event()
    async with SessionLocal() as s:
        rows = (await s.execute(select(ForumEventGuest).where(
            ForumEventGuest.event_id == eid, ForumEventGuest.group == "registered"))).scalars().all()
        for g in rows[:4]:
            g.group = "converted"
        await s.commit()
    d = await _compute(tid, eid)
    assert d["registration"]["guests"] == 24, "converting must not reduce the guest count"
    assert d["revenue"]["members"] == 4
    assert {f["key"]: f["count"] for f in d["funnel"]}["converted"] == 4


async def test_members_are_counted_from_their_own_tag_and_the_room_adds_both():
    tid, eid = await _event()
    d = await _compute(tid, eid)
    r = d["registration"]
    assert r["members_registered"] == 35
    assert r["guests"] == 24
    assert r["room"] == 59, "the only figure allowed to add the two populations"


# ── pace, time-travelled ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("today,dte,expected_pct", [
    (dt.date(2026, 9, 14), 60, 0.10),      # early
    (dt.date(2026, 10, 5), 39, 0.45),      # the real as-of
    (dt.date(2026, 10, 30), 14, 0.80),     # the run-up
])
async def test_the_curve_is_read_at_the_injected_clock(today, dte, expected_pct):
    tid, eid = await _event()
    d = await _compute(tid, eid, today=today)
    r = d["registration"]
    assert r["days_to_event"] == dte
    assert r["expected_pct"] == pytest.approx(expected_pct, abs=1e-4)
    assert r["expected"] == round(expected_pct * GOAL)
    assert r["gap"] == 24 - r["expected"]


async def test_state_moves_from_onpace_to_behind_to_done_as_the_clock_advances():
    tid, eid = await _event()
    # 39 days out: expected 27 against 24 - inside the 8% of 60 = 4.8 tolerance.
    assert (await _compute(tid, eid, today=dt.date(2026, 10, 5)))["registration"]["state"] == "onpace"
    # 14 days out: expected 48 against 24 - far behind.
    assert (await _compute(tid, eid, today=dt.date(2026, 10, 30)))["registration"]["state"] == "behind"
    # after the event, pace is history, not a verdict.
    assert (await _compute(tid, eid, today=dt.date(2026, 11, 20)))["registration"]["state"] == "done"


async def test_an_undated_event_is_pending_not_behind():
    tid, eid = await _event(starts_on=None)
    r = (await _compute(tid, eid))["registration"]
    assert r["days_to_event"] is None and r["state"] == "pending"


# ── money is optional ─────────────────────────────────────────────────────────────────────
async def test_every_count_works_with_no_price_configured_and_money_is_none_not_zero():
    """D8. `vip_price` and `price_map` ship unset, and a revenue figure with no price must be
    None - a dash reads as an absence, a zero reads as a result."""
    tid, eid = await _event()                      # no vip_price, no price_map
    d = await _compute(tid, eid)
    assert d["revenue"]["ticket_booked"] is None
    assert d["revenue"]["member_arr"] is None
    # ...and not one count is affected.
    assert d["registration"]["guests"] == 24 and d["registration"]["room"] == 59
    assert d["registration"]["expected"] == 27


async def test_pricing_it_prices_it():
    tid, eid = await _event(vip_price=2500, price_map=PRICES)
    async with SessionLocal() as s:
        rows = (await s.execute(select(ForumEventGuest).where(
            ForumEventGuest.event_id == eid, ForumEventGuest.group == "registered"))).scalars().all()
        rows[0].group, rows[0].payment_type = "converted", "Single - PIF"
        rows[1].group, rows[1].payment_type = "converted", "Dual - Monthly"
        await s.commit()
    d = await _compute(tid, eid)
    assert d["revenue"]["ticket_booked"] == 24 * 2500          # none are comped in this fixture
    assert d["revenue"]["member_arr"] == 12000 + 24000


async def test_a_converted_member_we_cannot_price_is_charged_the_blended_rate_and_flagged():
    tid, eid = await _event(vip_price=2500, price_map=PRICES)
    async with SessionLocal() as s:
        rows = (await s.execute(select(ForumEventGuest).where(
            ForumEventGuest.event_id == eid, ForumEventGuest.group == "registered"))).scalars().all()
        rows[0].group, rows[0].payment_type = "converted", "Single - PIF"      # 12000
        rows[1].group, rows[1].payment_type = "converted", None                # unpriceable
        await s.commit()
    d = await _compute(tid, eid)
    assert d["revenue"]["unpriced_members"] == 1
    assert d["revenue"]["member_arr"] == 12000 + 12000, "blended of what we could price"
    assert any("blended" in w for w in d["warnings"])


async def test_conversion_rate_is_none_on_an_empty_denominator():
    tid, eid = await _event()
    async with SessionLocal() as s:
        await s.execute(delete(ForumEventGuest).where(ForumEventGuest.event_id == eid))
        await s.commit()
    d = await _compute(tid, eid)
    assert d["revenue"]["conversion"]["rate"] is None, "a dash, never a zero"
    assert d["registration"]["guests"] == 0


# ── warnings, shape, selection ────────────────────────────────────────────────────────────
async def test_the_gaps_are_reported_rather_than_hidden():
    tid, eid = await _event()
    w = " | ".join((await _compute(tid, eid))["warnings"])
    assert "5 guests with no opportunity" in w
    assert "parked or lost" in w


async def test_payload_has_the_documented_shape():
    tid, eid = await _event()
    d = await _compute(tid, eid)
    assert set(d) >= {"event", "registration", "funnel", "revenue", "momentum",
                      "groups", "warnings", "as_of"}
    assert [f["key"] for f in d["funnel"]] == list(FE.FUNNEL_ORDER)
    # The frontend reads the vocabulary from the payload instead of re-declaring it.
    assert d["groups"] == list(FE.GROUPS)
    # config_out must emit everything the editor can set, or a future save blanks it.
    cfg = d["event"]
    assert cfg["guest_goal"] == GOAL and cfg["guest_tags"] and cfg["member_tags"]
    assert cfg["vip_price"] is None and cfg["price_map"] == {}


async def test_active_event_prefers_the_open_window_then_the_nearest_upcoming():
    tid, eid = await _event()
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        s.add(ForumEvent(tenant_id=tid, business_id=biz.id, name="Q1 2027", slug="q1-2027",
                         status="draft", starts_on=dt.date(2027, 2, 10),
                         window_start=dt.date(2026, 12, 1), window_end=dt.date(2027, 2, 10)))
        await s.commit()
        got = await FE.active_event(s, tid, biz.id, today=ASOF)
        assert got.id == eid, "Q4's window contains today; Q1 is upcoming"
        later = await FE.active_event(s, tid, biz.id, today=dt.date(2026, 12, 15))
        assert later.slug == "q1-2027"


async def test_an_event_belongs_to_one_tenant():
    tid, eid = await _event()
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        import uuid
        assert await FE.active_event(s, uuid.uuid4(), biz.id, today=ASOF) is None


# ── the authorization hole (F14) ──────────────────────────────────────────────────────────
def test_an_unknown_event_key_does_not_fall_through_to_portfolio():
    """`tab_for_metric` ends in `return "portfolio"`. Without an explicit branch, every event
    drill-down would be readable by anyone granted the Portfolio tab."""
    from app.services.tabs import tab_for_metric
    assert tab_for_metric("event_guests") == "forum"
    assert tab_for_metric("event_room") == "forum"
    # The point of the test: a key nobody has added to the set yet still lands on the Forum.
    assert tab_for_metric("event_some_metric_added_next_year") == "forum"
    assert tab_for_metric("genuinely_unknown") == "portfolio", "the fallthrough still exists"


# ── the sync (Phase 3) ────────────────────────────────────────────────────────────────────
class _FakeGHL:
    """Stands in for app.integrations.ghl. Read-only in the real thing, so there is nothing to
    assert about writes - what matters is that the sync UPSERTS and never sweeps."""

    def __init__(self, contacts, opps=(), stages=None):
        self._contacts, self._opps = contacts, list(opps)
        self._stages = stages or {"s_conf": "VIP Guest: Confirmed",
                                  "s_cold": "Cold Nurture: Upcoming VIP Event (Unresponsive)",
                                  "s_dual": "Sent Contract: Dual - Monthly",
                                  "s_won": "Won: Onboarded"}

    async def get_contacts(self, *a, **k):
        return self._contacts

    async def get_opportunities(self, *a, **k):
        return self._opps

    async def get_pipelines(self, *a, **k):
        return [{"id": "p1", "name": "01.1 - Forum Main Sales Funnel",
                 "stages": [{"id": k, "name": v} for k, v in self._stages.items()]}]

    async def get_custom_fields(self, *a, **k):
        return [{"id": "f_rep", "name": "Sales Rep"}, {"id": "f_ref", "name": "Referred By"},
                {"id": "f_inv", "name": "Guest Invited By"}]

    @staticmethod
    def contact_tags(c):
        return c.get("tags") or []

    @staticmethod
    def contact_name(c):
        return c.get("name")

    @staticmethod
    def contact_custom_values(c):
        return c.get("cvals") or {}

    @staticmethod
    def opp_custom_values(o):
        return (o or {}).get("cvals") or {}


def _contact(cid, *tags, **kw):
    return {"id": cid, "name": kw.pop("name", f"Person {cid}"), "tags": list(tags), **kw}


def _opp(oid, cid, stage="s_conf", **kw):
    return {"id": oid, "contactId": cid, "pipelineId": "p1", "pipelineStageId": stage,
            "updatedAt": kw.pop("updatedAt", "2026-10-01T00:00:00Z"), **kw}


async def _sync(monkeypatch, eid, tid, contacts, opps=(), today=ASOF):
    import app.integrations.ghl as real
    fake = _FakeGHL(contacts, opps)
    for name in ("get_contacts", "get_opportunities", "get_pipelines", "get_custom_fields",
                 "contact_tags", "contact_name", "contact_custom_values", "opp_custom_values"):
        monkeypatch.setattr(real, name, getattr(fake, name))
    async with SessionLocal() as s:
        ev = (await s.execute(select(ForumEvent).where(ForumEvent.id == eid))).scalar_one()
        return await FE.sync_forum_event(s, tid, ev, "tok", "loc", today=today)


GUEST = "the forum q4 2026 guest rsvp"
MEMBER = "the forum q4 2026 rsvp"


async def test_sync_upserts_and_a_second_run_does_not_duplicate(monkeypatch):
    tid, eid = await _event()
    async with SessionLocal() as s:                       # start from empty
        await s.execute(delete(ForumEventGuest).where(ForumEventGuest.event_id == eid))
        await s.commit()
    contacts = [_contact("c1", GUEST), _contact("c2", GUEST), _contact("c3", MEMBER)]
    opps = [_opp("o1", "c1")]
    first = await _sync(monkeypatch, eid, tid, contacts, opps)
    assert (first["guests"], first["members"], first["new"]) == (2, 1, 3)
    second = await _sync(monkeypatch, eid, tid, contacts, opps)
    assert second["new"] == 0, "the second run must update, not insert"
    async with SessionLocal() as s:
        rows = (await s.execute(select(ForumEventGuest).where(
            ForumEventGuest.event_id == eid))).scalars().all()
    assert len(rows) == 3


async def test_a_contact_carrying_both_tags_is_one_guest_and_is_counted_as_such(monkeypatch):
    """D10. Stored once, as a guest - which is why `room` cannot double-count, and why a
    both-tags figure derived from the stored rows would be structurally zero."""
    tid, eid = await _event()
    async with SessionLocal() as s:
        await s.execute(delete(ForumEventGuest).where(ForumEventGuest.event_id == eid))
        await s.commit()
    stat = await _sync(monkeypatch, eid, tid, [_contact("c1", GUEST, MEMBER)])
    assert (stat["guests"], stat["members"], stat["both_tags"]) == (1, 0, 1)
    async with SessionLocal() as s:
        rows = (await s.execute(select(ForumEventGuest).where(
            ForumEventGuest.event_id == eid))).scalars().all()
    assert len(rows) == 1 and rows[0].kind == "guest"


async def test_losing_the_tag_removes_the_person_from_THIS_event_only(monkeypatch):
    tid, eid = await _event()
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(ForumEventGuest).where(ForumEventGuest.event_id == eid))
        other = ForumEvent(tenant_id=tid, business_id=biz.id, name="Q3", slug="q3",
                           guest_tags=["q3 guest"], stage_map=FE.DEFAULT_STAGE_MAP)
        s.add(other)
        await s.flush()
        oid = other.id
        s.add(ForumEventGuest(tenant_id=tid, event_id=oid, contact_id="c1", kind="guest"))
        await s.commit()
    await _sync(monkeypatch, eid, tid, [_contact("c1", GUEST)])
    stat = await _sync(monkeypatch, eid, tid, [])          # c1 lost the Q4 tag
    assert stat["untagged"] == 1
    async with SessionLocal() as s:
        q4 = (await s.execute(select(ForumEventGuest).where(
            ForumEventGuest.event_id == eid))).scalars().all()
        q3 = (await s.execute(select(ForumEventGuest).where(
            ForumEventGuest.event_id == oid))).scalars().all()
    assert len(q4) == 0, "removed from the event whose tag they lost"
    assert len(q3) == 1, "and NOT from any other event - that is the whole bug being avoided"


async def test_the_payment_type_comes_out_of_the_contract_stage(monkeypatch):
    """The Forum encodes Single/Dual x Monthly/PIF in the stage name rather than in a Payment
    Type field, so price_map is keyed by what follows the colon and nothing is inferred."""
    tid, eid = await _event()
    async with SessionLocal() as s:
        await s.execute(delete(ForumEventGuest).where(ForumEventGuest.event_id == eid))
        await s.commit()
    await _sync(monkeypatch, eid, tid,
                [_contact("c1", GUEST), _contact("c2", GUEST)],
                [_opp("o1", "c1", "s_dual"), _opp("o2", "c2", "s_won")])
    async with SessionLocal() as s:
        rows = {r.contact_id: r for r in (await s.execute(select(ForumEventGuest).where(
            ForumEventGuest.event_id == eid))).scalars()}
    assert rows["c1"].payment_type == "Dual - Monthly" and rows["c1"].group == "deciding"
    # A member whose contract stage we never saw has no type. That is honest, not a gap - they
    # are priced at the blended rate and counted in the warning.
    assert rows["c2"].payment_type is None and rows["c2"].group == "converted"


async def test_a_tagged_rsvp_with_no_opportunity_is_still_a_guest(monkeypatch):
    """Five of Spring's twenty-four are exactly this. Requiring an opportunity would drop the
    people the tab exists to surface."""
    tid, eid = await _event()
    async with SessionLocal() as s:
        await s.execute(delete(ForumEventGuest).where(ForumEventGuest.event_id == eid))
        await s.commit()
    stat = await _sync(monkeypatch, eid, tid, [_contact("c1", GUEST)])      # no opps at all
    assert stat["guests"] == 1
    async with SessionLocal() as s:
        ev = (await s.execute(select(ForumEvent).where(ForumEvent.id == eid))).scalar_one()
        d = await FE.compute_event(s, tid, ev, today=ASOF)
    assert d["registration"]["guests"] == 1
    assert d["registration"]["guests_without_opp"] == 1
    # ...and it must NOT also be reported as an unmapped stage. It has no stage to map.
    assert not any("unmapped" in w for w in d["warnings"])


async def test_declined_members_are_captured_without_joining_the_room(monkeypatch):
    tid, eid = await _event()
    async with SessionLocal() as s:
        await s.execute(delete(ForumEventGuest).where(ForumEventGuest.event_id == eid))
        ev = (await s.execute(select(ForumEvent).where(ForumEvent.id == eid))).scalar_one()
        ev.declined_tags = ["q4 not registered - member"]
        await s.commit()
    stat = await _sync(monkeypatch, eid, tid, [
        _contact("c1", GUEST), _contact("c2", MEMBER),
        _contact("c3", "q4 not registered - member")])
    assert stat["declined"] == 1
    async with SessionLocal() as s:
        ev = (await s.execute(select(ForumEvent).where(ForumEvent.id == eid))).scalar_one()
        d = await FE.compute_event(s, tid, ev, today=ASOF)
    assert d["registration"]["room"] == 2, "a decliner is not in the room"


async def test_the_weekly_row_is_one_per_iso_week_and_is_updated_in_place(monkeypatch):
    tid, eid = await _event()
    async with SessionLocal() as s:
        await s.execute(delete(ForumEventGuest).where(ForumEventGuest.event_id == eid))
        await s.execute(delete(ForumEventWeekly).where(ForumEventWeekly.event_id == eid))
        await s.commit()
    mon = dt.date(2026, 10, 5)                       # a Monday
    await _sync(monkeypatch, eid, tid, [_contact("c1", GUEST)], today=mon)
    await _sync(monkeypatch, eid, tid, [_contact("c1", GUEST), _contact("c2", GUEST)],
                today=mon + dt.timedelta(days=3))    # same ISO week
    await _sync(monkeypatch, eid, tid, [_contact("c1", GUEST), _contact("c2", GUEST),
                                        _contact("c3", GUEST)],
                today=mon + dt.timedelta(days=8))    # the next week
    async with SessionLocal() as s:
        weeks = (await s.execute(select(ForumEventWeekly).where(
            ForumEventWeekly.event_id == eid).order_by(ForumEventWeekly.week_start))).scalars().all()
    assert [w.week_start for w in weeks] == [mon, mon + dt.timedelta(days=7)]
    assert [w.guests for w in weeks] == [2, 3], "updated in place, then a new week"


# ── the API (Phase 4) ─────────────────────────────────────────────────────────────────────
async def _token(email=None, password=None):
    """The login response key is `token`, not `access_token`, and the seeded owner's
    credentials are constants - never literals, because the seed rotates them."""
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    from app.seed import OWNER_EMAIL, OWNER_PASSWORD
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        r = await c.post("/api/v1/auth/login", json={"email": email or OWNER_EMAIL,
                                                     "password": password or OWNER_PASSWORD})
        body = r.json()
        assert "token" in body, f"login failed: {r.status_code} {body}"
        return body["token"]


async def _api(method, path, token=None, **kw):
    from httpx import AsyncClient, ASGITransport
    from app.main import app
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        return await getattr(c, method)(path, headers=headers, **kw)


async def test_current_404s_when_no_event_is_configured_and_that_is_the_tab_being_absent():
    """A 404 is the designed signal that the sub-tab is ABSENT. Returning an empty shell would
    render a tab full of zeroes on a workspace that has never run an event."""
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(ForumEventGuest).where(ForumEventGuest.tenant_id == biz.tenant_id))
        await s.execute(delete(ForumEvent).where(ForumEvent.tenant_id == biz.tenant_id))
        await s.commit()
    r = await _api("get", "/api/v1/businesses/springb/events/current", await _token())
    assert r.status_code == 404


async def test_current_serves_the_payload_once_an_event_exists():
    tid, eid = await _event()
    r = await _api("get", "/api/v1/businesses/springb/events/current", await _token())
    assert r.status_code == 200, r.text
    d = r.json()
    # The FIXTURE has no both-tags overlap, so 24 + 35 = 59. Live Spring data has one,
    # which is why the live gate expects 34 members and 58 in the room.
    assert d["registration"]["guests"] == 24 and d["registration"]["room"] == 59
    assert set(d) >= {"event", "registration", "funnel", "revenue", "momentum", "warnings"}


async def test_reads_need_the_forum_tab_not_becollective():
    """_launch_tab hardcodes "becollective" and gates every launch route behind it. Copying that
    would put Forum events behind the wrong grant for any workspace holding both."""
    from app.models import User
    from app.security import hash_pw
    await _event()
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(User).where(User.email == "evtab@x.com"))
        s.add(User(tenant_id=biz.tenant_id, email="evtab@x.com", name="No Forum",
                   password_hash=hash_pw("pw12345678"), role="member",
                   tab_access=["becollective"]))      # beCollective but NOT forum
        await s.commit()
    tok = await _token("evtab@x.com", "pw12345678")
    r = await _api("get", "/api/v1/businesses/springb/events/current", tok)
    assert r.status_code == 403, "the beCollective grant must not open the Forum's events"

    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.email == "evtab@x.com"))).scalar_one()
        u.tab_access = ["forum"]
        await s.commit()
    r = await _api("get", "/api/v1/businesses/springb/events/current",
                   await _token("evtab@x.com", "pw12345678"))
    assert r.status_code == 200, "the forum grant is the one that opens them"


async def test_an_unknown_business_key_is_404_not_someone_elses_event():
    await _event()
    r = await _api("get", "/api/v1/businesses/not-a-business/events/current", await _token())
    assert r.status_code == 404


async def test_an_event_cannot_be_read_through_another_businesss_path():
    """Checking the tab on one business and then reading a child row by id alone is how a tab
    grant on one business reached another's recordings once already."""
    tid, eid = await _event()
    r = await _api("get", f"/api/v1/businesses/ulrg/events/{eid}", await _token())
    assert r.status_code in (403, 404), "never serve it through a business that does not own it"


async def test_writes_are_owner_admin_only_and_audited():
    from app.models import User, AuditLog
    from app.security import hash_pw
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(ForumEvent).where(ForumEvent.tenant_id == biz.tenant_id))
        await s.execute(delete(User).where(User.email == "evmem@x.com"))
        s.add(User(tenant_id=biz.tenant_id, email="evmem@x.com", name="Member",
                   password_hash=hash_pw("pw12345678"), role="member", tab_access=["forum"]))
        await s.commit()
        tid = biz.tenant_id

    body = {"name": "Q1 2027", "slug": "q1-2027", "guest_goal": 40}
    r = await _api("post", "/api/v1/businesses/springb/events",
                   await _token("evmem@x.com", "pw12345678"), json=body)
    assert r.status_code == 403, "a member with the tab may READ, never create"

    r = await _api("post", "/api/v1/businesses/springb/events", await _token(), json=body)
    assert r.status_code == 201, r.text
    assert r.json()["event"]["slug"] == "q1-2027"

    async with SessionLocal() as s:
        rows = (await s.execute(select(AuditLog).where(
            AuditLog.tenant_id == tid, AuditLog.action == "forum_event.create"))).scalars().all()
    assert rows, "every write leaves an audit row"


async def test_post_requires_the_required_set_and_put_is_a_partial_patch():
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(ForumEvent).where(ForumEvent.tenant_id == biz.tenant_id))
        await s.commit()
    tok = await _token()
    r = await _api("post", "/api/v1/businesses/springb/events", tok, json={"guest_goal": 10})
    assert r.status_code == 400 and "name" in r.text

    r = await _api("post", "/api/v1/businesses/springb/events", tok,
                   json={"name": "Q2 2027", "slug": "q2-2027", "guest_goal": 50,
                         "starts_on": "2027-05-10"})
    assert r.status_code == 201
    eid = r.json()["event"]["id"]

    # A partial patch leaves everything it does not name alone.
    r = await _api("put", f"/api/v1/businesses/springb/events/{eid}", tok,
                   json={"guest_goal": 55})
    assert r.status_code == 200
    cfg = r.json()["event"]
    assert cfg["guest_goal"] == 55 and cfg["slug"] == "q2-2027" and cfg["starts_on"] == "2027-05-10"


async def test_a_bad_date_is_a_400_not_a_string_in_the_column():
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(ForumEvent).where(ForumEvent.tenant_id == biz.tenant_id))
        await s.commit()
    r = await _api("post", "/api/v1/businesses/springb/events", await _token(),
                   json={"name": "X", "slug": "x", "starts_on": "not-a-date"})
    assert r.status_code == 400 and "Bad date" in r.text


async def test_the_upsert_schema_the_config_and_the_model_all_agree():
    """F17. Thirteen launch columns are PUT-able with no UI field, and two are accepted by the
    schema and never returned by config_out - a future editor reading config and saving it back
    silently blanks them. These three lists must not drift."""
    from app.schemas import ForumEventUpsert
    from app.models import ForumEvent as FEModel
    accepted = set(ForumEventUpsert.model_fields)
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        ev = FEModel(tenant_id=biz.tenant_id, business_id=biz.id, name="n", slug="s")
        emitted = set(FE.config_out(ev)) - {"id"}
    columns = {c.name for c in FEModel.__table__.c}
    assert accepted == emitted, f"accepted-but-not-emitted: {accepted - emitted}; " \
                                f"emitted-but-not-accepted: {emitted - accepted}"
    assert accepted <= columns, f"schema accepts fields with no column: {accepted - columns}"


async def test_the_drill_opens_the_people_behind_a_figure():
    tid, eid = await _event()
    tok = await _token()
    r = await _api("get", f"/api/v1/businesses/springb/events/{eid}/drill/event_without_opp", tok)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["type"] == "records" and d["count"] == 5, "the five RSVPs with no sale open"
    assert set(d["rows"][0]) >= {"name", "kind", "stage", "group", "rep"}

    r = await _api("get", f"/api/v1/businesses/springb/events/{eid}/drill/event_guests", tok)
    assert r.json()["count"] == 24


async def test_an_unknown_drill_metric_is_a_404_not_an_empty_drawer():
    """drill_launch falls through to a soft "no drill-down defined for this value yet", which
    turns a typo in the frontend into a figure that opens onto nothing and reads as missing
    data. A 404 says which key was wrong."""
    tid, eid = await _event()
    r = await _api("get", f"/api/v1/businesses/springb/events/{eid}/drill/event_nonsense",
                   await _token())
    assert r.status_code == 404 and "event_nonsense" in r.text


# ── assistant + lineage (Phase 6) ─────────────────────────────────────────────────────────
def test_every_drill_metric_is_in_the_lineage_set_so_none_can_fall_through():
    """Two hand-kept lists that must agree: the metrics the drill serves, and the keys
    tab_for_metric knows. A drill key missing from the set still lands on the Forum via the
    `event_` prefix branch - but the set is what documents the family, and letting them drift
    is how _wipe ended up 116 tables behind. Assert rather than remember."""
    from app.services.tabs import _EVENT, tab_for_metric
    from app.services.forum_event import _DRILL_TITLES, GROUPS
    assert set(_DRILL_TITLES) <= _EVENT, f"drill metrics not in _EVENT: {set(_DRILL_TITLES) - _EVENT}"
    for k in _DRILL_TITLES:
        assert tab_for_metric(k) == "forum"
    # the per-group keys the funnel rows emit
    for g in GROUPS:
        assert tab_for_metric(f"event_group_{g}") == "forum"


def test_the_assistant_legend_tells_it_the_two_rules_that_are_easy_to_get_wrong():
    """The Ask panel will happily add ticket revenue to MRR, or report the funnel's 18 as the
    guest count, unless the legend says otherwise. Both are stated."""
    from app.services.assistant import TAB_LEGEND
    forum = TAB_LEGEND["forum"].lower()
    assert "rsvp tag" in forum and "no opportunity still counts" in forum
    assert "never added to mrr" in forum


async def test_the_assistant_packs_the_event_when_one_exists():
    """Without this the Ask panel can see the Forum payload and not the Event sub-tab sitting
    on top of it - the number is on screen and unanswerable."""
    tid, eid = await _event()
    from app.services import assistant
    from app.models import User
    async with SessionLocal() as s:
        user = (await s.execute(select(User).where(
            User.tenant_id == tid, User.role == "owner"))).scalars().first()
        ctx = await assistant._build_context(s, user, "mtd")
    blob = ctx if isinstance(ctx, str) else str(ctx)
    assert "forum_event_detail" in blob, "the event must reach the assistant's context"
    assert "24" in blob


async def test_the_integration_entrypoint_actually_runs(monkeypatch):
    """The function the WORKER calls, not just the one the other tests call.

    sync_events_for_integration shipped with `from ..crypto import dec` - a module that does not
    exist; it is app.security. Every unit test above calls sync_forum_event directly with a
    token, so none of them ever reached the import, and the caller in sync.py wraps this in a
    try/except that would have printed "[forum_event] skipped" every tick, forever, while the
    tab stayed empty and nothing looked broken.

    Caught by running it against production. This test is the cheaper way.
    """
    import app.integrations.ghl as real
    from app.models import Integration
    from app.security import enc
    tid, eid = await _event()
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(ForumEventGuest).where(ForumEventGuest.event_id == eid))
        integ = (await s.execute(select(Integration).where(
            Integration.tenant_id == tid, Integration.business_id == biz.id,
            Integration.provider == "ghl"))).scalar_one_or_none()
        if integ is None:
            integ = Integration(tenant_id=tid, business_id=biz.id, provider="ghl",
                                status="connected")
            s.add(integ)
        integ.config = {**(integ.config or {}), "location_id": "loc"}
        integ.access_token_enc = enc("tok")
        await s.commit()
        iid = integ.id

    fake = _FakeGHL([_contact("c1", GUEST), _contact("c2", MEMBER)], [_opp("o1", "c1")])
    for name in ("get_contacts", "get_opportunities", "get_pipelines", "get_custom_fields",
                 "contact_tags", "contact_name", "contact_custom_values", "opp_custom_values"):
        monkeypatch.setattr(real, name, getattr(fake, name))

    async with SessionLocal() as s:
        integ = (await s.execute(select(Integration).where(Integration.id == iid))).scalar_one()
        n = await FE.sync_events_for_integration(s, tid, integ)
    assert n == 2, "the entrypoint must actually pull, not swallow an ImportError"
    async with SessionLocal() as s:
        rows = (await s.execute(select(ForumEventGuest).where(
            ForumEventGuest.event_id == eid))).scalars().all()
    assert {r.kind for r in rows} == {"guest", "member"}


async def test_declined_members_are_counted_not_promised(monkeypatch):
    """members_declined was hardcoded to None with a comment saying the sync would set it.
    It never did. The sync stored the rows all along, so a tenant who configured the
    not-attending tag watched the setting take and the figure never appear - which is exactly
    how Connor found it. A comment is not an implementation."""
    tid, eid = await _event()
    async with SessionLocal() as s:
        await s.execute(delete(ForumEventGuest).where(ForumEventGuest.event_id == eid))
        ev = (await s.execute(select(ForumEvent).where(ForumEvent.id == eid))).scalar_one()
        ev.declined_tags = ["q4 not registered - member"]
        for i in range(3):
            s.add(ForumEventGuest(tenant_id=tid, event_id=eid, contact_id=f"d{i}", kind="declined"))
        for i in range(2):
            s.add(ForumEventGuest(tenant_id=tid, event_id=eid, contact_id=f"m{i}", kind="member"))
        await s.commit()
    d = await _compute(tid, eid)
    r = d["registration"]
    assert r["members_declined"] == 3, "stored and counted, not promised"
    assert r["members_registered"] == 2
    assert r["room"] == 2, "a decliner is not in the room"


async def test_the_member_shares_use_the_forums_own_roster_count():
    """One member total per screen. The denominator is counted the same way metrics.py counts
    the Forum's member KPI - kind='member', status='active' - so the Event tab and the Forum
    tab cannot disagree about how many members there are."""
    from app.models import MetricRecord
    tid, eid = await _event()
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(MetricRecord).where(
            MetricRecord.tenant_id == tid, MetricRecord.kind == "member"))
        for i in range(50):
            s.add(MetricRecord(tenant_id=tid, business_id=biz.id, source="ghl", kind="member",
                               external_id=f"mem{i}", status="active"))
        # an inactive member is NOT in the denominator, matching metrics.py
        s.add(MetricRecord(tenant_id=tid, business_id=biz.id, source="ghl", kind="member",
                           external_id="gone", status="inactive"))
        await s.commit()
    r = (await _compute(tid, eid))["registration"]
    assert r["members_total"] == 50, "active only"
    assert r["members_registered"] == 35
    assert r["members_registered_pct"] == 0.7


async def test_the_shares_are_none_not_zero_with_no_roster():
    from app.models import MetricRecord
    tid, eid = await _event()
    async with SessionLocal() as s:
        await s.execute(delete(MetricRecord).where(
            MetricRecord.tenant_id == tid, MetricRecord.kind == "member"))
        await s.commit()
    r = (await _compute(tid, eid))["registration"]
    assert r["members_total"] is None and r["members_registered_pct"] is None, \
        "a dash reads as 'we do not know'; a zero reads as 'none of them'"


async def test_members_who_have_not_answered_are_counted_and_nameable():
    """The chase list: on the roster, no registration, no decline. It was only ever visible by
    subtracting two tiles from a third, which is not visible at all.

    These people have no forum_event_guest row BY DEFINITION - not registering generates no
    event - so the count and the drill both come off the member roster.
    """
    from app.models import MetricRecord
    tid, eid = await _event()
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(ForumEventGuest).where(ForumEventGuest.event_id == eid))
        await s.execute(delete(MetricRecord).where(
            MetricRecord.tenant_id == tid, MetricRecord.kind == "member"))
        ev = (await s.execute(select(ForumEvent).where(ForumEvent.id == eid))).scalar_one()
        ev.declined_tags = ["q4 not registered - member"]
        # 10 on the roster: 3 registered, 2 declined, 5 silent.
        for i in range(10):
            s.add(MetricRecord(tenant_id=tid, business_id=biz.id, source="ghl", kind="member",
                               external_id=f"c{i}", name=f"Member {i}", status="active"))
        for i in range(3):
            s.add(ForumEventGuest(tenant_id=tid, event_id=eid, contact_id=f"c{i}", kind="member"))
        for i in range(3, 5):
            s.add(ForumEventGuest(tenant_id=tid, event_id=eid, contact_id=f"c{i}", kind="declined"))
        await s.commit()

    d = await _compute(tid, eid)
    r = d["registration"]
    assert (r["members_total"], r["members_registered"], r["members_declined"]) == (10, 3, 2)
    assert r["members_unanswered"] == 5
    assert r["members_unanswered_pct"] == 0.5

    async with SessionLocal() as s:
        ev = (await s.execute(select(ForumEvent).where(ForumEvent.id == eid))).scalar_one()
        drill = await FE.drill_event(s, tid, ev, "event_unanswered")
    assert drill["count"] == 5
    names = {row["name"] for row in drill["rows"]}
    assert names == {f"Member {i}" for i in range(5, 10)}, "the silent ones, by name"


async def test_a_guest_is_not_counted_as_an_unanswered_member():
    """A VIP guest who also happens to be on the member roster has answered - by being a guest.
    Counting them as silent would pad the chase list with people already coming."""
    from app.models import MetricRecord
    tid, eid = await _event()
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        await s.execute(delete(ForumEventGuest).where(ForumEventGuest.event_id == eid))
        await s.execute(delete(MetricRecord).where(
            MetricRecord.tenant_id == tid, MetricRecord.kind == "member"))
        for i in range(2):
            s.add(MetricRecord(tenant_id=tid, business_id=biz.id, source="ghl", kind="member",
                               external_id=f"c{i}", name=f"Member {i}", status="active"))
        s.add(ForumEventGuest(tenant_id=tid, event_id=eid, contact_id="c0", kind="member"))
        await s.commit()
    r = (await _compute(tid, eid))["registration"]
    assert r["members_unanswered"] == 1, "c1 is silent; c0 answered"
