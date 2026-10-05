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
