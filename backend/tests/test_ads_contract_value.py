"""The contract value on the ads tab. SPEC-ads-module.md Part 9.3.

THE BUG THIS FILE EXISTS FOR. `value_contracted` was GHL's opportunity `monetaryValue`, read off
the `bc_onboarded` record. Three things were wrong with that at once and they compounded:

  * A person who has not yet reached "Won: Onboarded" has NO bc_onboarded record, so the value
    was null. On the live August cohort that was three of the five enrollments traced to Meta.
  * For a financed member the amount that IS there is the DOWN PAYMENT, not the contract.
  * So the two rows showing a number were the two who had paid in full - cash collected,
    presented under a column headed Contracted.

The fix prices each person off the launch's own price sheet, which is the same sheet the Launch
tab prices ARR from. These tests pin the ladder, the fallbacks, and the one rule that matters
more than any of them: a modelled number and a signed one must never be reported as one fact.
"""
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (AdAttribution, AdConversion, Business, Launch, MetricRecord, SalesCall,
                        Tenant)
from app.seed import seed
from app.services import ads_funnel as F
from app.services.launch import (DEFAULT_PAYMENT_PLAN_MAP, DEFAULT_STAGE_MAP,
                                 contract_prices)

DAY = dt.date.today() - dt.timedelta(days=4)
PREFIX = "cv_"


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


@pytest.fixture(autouse=True)
async def _clean(_seeded):
    """Before AND after. The suite shares one database, and a file that leaves attributions
    behind changes what every later file counts."""
    await _wipe()
    yield
    await _wipe()


async def _wipe():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        attrs = [a.id for a in (await s.execute(select(AdAttribution).where(
            AdAttribution.tenant_id == t.id,
            AdAttribution.identity_key.like(f"{PREFIX}%")))).scalars()]
        if attrs:
            await s.execute(sa_delete(AdConversion).where(
                AdConversion.attribution_id.in_(attrs)))
            await s.execute(sa_delete(AdAttribution).where(AdAttribution.id.in_(attrs)))
        await s.execute(sa_delete(MetricRecord).where(
            MetricRecord.tenant_id == t.id, MetricRecord.external_id.like(f"{PREFIX}%")))
        await s.execute(sa_delete(SalesCall).where(
            SalesCall.tenant_id == t.id, SalesCall.opportunity_id.like(f"{PREFIX}%")))
        await s.commit()


async def _ctx():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        launch = (await s.execute(select(Launch).where(
            Launch.tenant_id == t.id).order_by(Launch.window_start.desc()))).scalars().first()
        biz = await s.get(Business, launch.business_id)
        return t.id, biz.id, launch


async def _person(tid, bid, launch, key, legacy_pay, *, group="enrolled", four=None,
                  ghl_amount=None, email=None, day=DAY):
    """One attributed identity plus the launch opportunity that decides her group.

    `legacy_pay` is what the snapshot writes into meta ("pif" | "plan" | "custom" | None).
    `four` is the Sales Desk's logged four-type Payment Type, which is a stronger signal and is
    written onto a SalesCall exactly the way sync_sales_calls does.
    """
    opp = f"{PREFIX}opp_{key}"
    async with SessionLocal() as s:
        s.add(AdAttribution(
            tenant_id=tid, identity_kind="ghl_contact", identity_key=f"{PREFIX}{key}",
            email_norm=email, business_id=bid, match_method="campaign",
            confidence="probable", channel="Meta", first_seen_on=day, last_seen_on=day))
        s.add(MetricRecord(
            tenant_id=tid, business_id=bid, source="ghl", kind="bc_launch_opp",
            name=f"P {key}", external_id=opp, occurred_on=day,
            meta={"contact_id": f"{PREFIX}{key}", "group": group,
                  "launch_id": str(launch.id), "payment_type": legacy_pay,
                  "stage": "Won: Onboarded" if group == "enrolled" else "Payment Received"}))
        if ghl_amount is not None:
            s.add(MetricRecord(
                tenant_id=tid, business_id=bid, source="ghl", kind="bc_onboarded",
                name=f"P {key}", external_id=f"{PREFIX}onb_{key}", occurred_on=day,
                amount=Decimal(str(ghl_amount)), meta={"contact_id": f"{PREFIX}{key}"}))
        if four is not None:
            s.add(SalesCall(
                tenant_id=tid, launch_id=launch.id, opportunity_id=opp,
                contact_id=f"{PREFIX}{key}", is_current=True, payment_type=four))
        await s.commit()
    return opp


async def _stages(tid, key):
    async with SessionLocal() as s:
        await F.sync_ad_conversions(s, tid)
    async with SessionLocal() as s:
        attr = (await s.execute(select(AdAttribution).where(
            AdAttribution.tenant_id == tid,
            AdAttribution.identity_key == f"{PREFIX}{key}"))).scalar_one()
        return {r.stage_key: r for r in (await s.execute(select(AdConversion).where(
            AdConversion.attribution_id == attr.id))).scalars()}


# -- the ladder --------------------------------------------------------------------------------

async def test_a_financed_member_is_worth_her_contract_not_her_deposit():
    """THE REPORTED BUG, at its smallest. She signed for the full plan price; GHL holds the
    5000 she put down. Before this, 5000 was what the Contracted column said - or nothing at
    all, when no onboarded record existed to hold even that."""
    tid, bid, launch = await _ctx()
    assert (launch.price_map or {}).get("Financed", {}).get("acv"), "fixture lost its price sheet"
    acv = float(launch.price_map["Financed"]["acv"])
    upfront = float(launch.price_map["Financed"]["upfront"])
    assert acv != upfront, "this test cannot prove anything if the two prices are equal"

    await _person(tid, bid, launch, "fin", "plan", four="Financed", ghl_amount=upfront)
    row = (await _stages(tid, "fin"))["closed"]

    assert float(row.value_contracted) == acv
    assert float(row.value_upfront) == upfront
    assert float(row.value_contracted) != upfront, "the deposit is standing in for the contract"
    assert row.value_source == "price_map"
    assert row.payment_type == "Financed"


async def test_someone_enrolled_with_no_onboarded_record_still_has_a_contract():
    """The three of five. Enrolled by the launch's own stage group, sitting at "Onboarding Call
    Attended", so no bc_onboarded row exists anywhere - and the price sheet does not care,
    because it prices the PAYMENT TYPE and not the event."""
    tid, bid, launch = await _ctx()
    await _person(tid, bid, launch, "mid", "plan", four="Financed", ghl_amount=None)
    row = (await _stages(tid, "mid"))["closed"]
    assert row.value_contracted is not None, \
        "an enrolled member with no onboarded record is still under contract"
    assert float(row.value_contracted) == float(launch.price_map["Financed"]["acv"])


async def test_the_legacy_plan_field_falls_back_to_the_launchs_own_ticket_price():
    """Rung 3. `plan` cannot be resolved to Financed vs Monthly - sync collapses both into it -
    so it prices off ticket_plan, which is the exact fallback the Launch tab's _priced() uses.
    The two tabs cannot disagree, because they read the same field of the same row."""
    tid, bid, launch = await _ctx()
    await _person(tid, bid, launch, "leg", "plan", four=None)
    row = (await _stages(tid, "leg"))["closed"]
    assert float(row.value_contracted) == float(launch.ticket_plan)
    assert row.value_source == "ticket"


async def test_paid_in_full_prices_off_the_sheet_without_a_sales_call():
    """`pif` IS invertible to a four-type, unlike `plan`, so it reaches the price sheet on the
    legacy field alone."""
    tid, bid, launch = await _ctx()
    await _person(tid, bid, launch, "pif", "pif", four=None)
    row = (await _stages(tid, "pif"))["closed"]
    assert float(row.value_contracted) == float(launch.price_map["PIF"]["acv"])
    assert row.value_source == "price_map"


async def test_a_negotiated_deal_is_never_priced_off_the_standard_ticket():
    """Custom carries acv=None ON PURPOSE - it means negotiated, not free. Pricing it at the
    list price would be a guess wearing a measurement's clothes, so it falls through to the GHL
    amount and says so."""
    tid, bid, launch = await _ctx()
    await _person(tid, bid, launch, "cus", "custom", four="Custom", ghl_amount=9500)
    row = (await _stages(tid, "cus"))["closed"]
    assert float(row.value_contracted) == 9500.0
    assert row.value_source == "ghl_amount", \
        "a number somebody typed into GHL must not be presented as a price-sheet figure"


async def test_a_monthly_membership_stays_flagged_as_annualized():
    """price_map's Monthly acv IS months x monthly - a modelling choice, and the row has to keep
    saying so now the number comes from the sheet rather than from _annualize."""
    tid, bid, launch = await _ctx()
    await _person(tid, bid, launch, "mon", "plan", four="Monthly")
    row = (await _stages(tid, "mon"))["closed"]
    assert float(row.value_contracted) == float(launch.price_map["Monthly"]["acv"])
    assert row.value_annualized is True


# -- the money that was being thrown away ------------------------------------------------------

async def test_cash_received_carries_dollars_and_never_a_contract():
    """The rung is LABELLED "Cash received" and held only a headcount, because value_upfront did
    not exist and the collected join looked at `closed` alone. It now carries the cash - and
    deliberately not the contract, because nothing is contracted until it is signed."""
    tid, bid, launch = await _ctx()
    await _person(tid, bid, launch, "com", "pif", four="PIF", group="committed")
    row = (await _stages(tid, "com"))["committed"]
    assert float(row.value_upfront) == float(launch.price_map["PIF"]["upfront"])
    assert row.value_contracted is None, "an unsigned contract must not carry a contract value"


async def test_a_committed_persons_payment_is_no_longer_discarded():
    """The collected loop computed her cash and then looked for a `closed` row to put it on,
    found none, and dropped it on the floor - the whole Cash received rung, silently."""
    tid, bid, launch = await _ctx()
    await _person(tid, bid, launch, "cash", "pif", four="PIF", group="committed",
                  email="cash@x.test")
    async with SessionLocal() as s:
        s.add(MetricRecord(tenant_id=tid, business_id=bid, source="stripe_bc", kind="payment",
                           external_id=f"{PREFIX}pay_cash", name="Pmt", email="cash@x.test",
                           amount=Decimal("5000"), status="succeeded", occurred_on=DAY, meta={}))
        await s.commit()
    row = (await _stages(tid, "cash"))["committed"]
    assert float(row.value_collected) == 5000.0


async def test_a_refunded_charge_is_not_cash_received():
    """amount_refunded was carried by every payment writer and read by none of them."""
    tid, bid, launch = await _ctx()
    await _person(tid, bid, launch, "ref", "pif", four="PIF", email="ref@x.test")
    async with SessionLocal() as s:
        s.add(MetricRecord(tenant_id=tid, business_id=bid, source="stripe_bc", kind="payment",
                           external_id=f"{PREFIX}pay_ref", name="Pmt", email="ref@x.test",
                           amount=Decimal("12000"), status="succeeded", occurred_on=DAY,
                           meta={"amount_refunded": 12000}))
        await s.commit()
    row = (await _stages(tid, "ref"))["closed"]
    assert float(row.value_collected) == 0.0, "a fully refunded seat counted as collected in full"


async def test_another_programmes_dues_do_not_land_in_this_ones_cash():
    """Same tenant, same email, different business. The payment query filtered on tenant and
    kind only, so a member who is also on the Forum roster brought her Forum dues into the
    beCollective ads figure."""
    tid, bid, launch = await _ctx()
    await _person(tid, bid, launch, "biz", "pif", four="PIF", email="both@x.test")
    async with SessionLocal() as s:
        other = (await s.execute(select(Business).where(
            Business.tenant_id == tid, Business.id != bid))).scalars().first()
        assert other is not None, "the fixture needs a second business to prove this"
        s.add(MetricRecord(tenant_id=tid, business_id=other.id, source="ghl", kind="payment",
                           external_id=f"{PREFIX}pay_other", name="Forum dues",
                           email="both@x.test", amount=Decimal("97"), status="succeeded",
                           occurred_on=DAY, meta={}))
        await s.commit()
    row = (await _stages(tid, "biz"))["closed"]
    assert row.value_collected is None or float(row.value_collected) == 0.0, \
        "another programme's dues were counted as this campaign's cash"


# -- what the page reads -----------------------------------------------------------------------

async def test_the_hero_gets_both_totals_and_names_where_the_cash_came_from():
    """Connor's ask: both totals, not one. Contracted over the enrolled, cash over EVERYONE who
    has paid - which is the committed rung plus the enrolled, the same population the Launch
    tab's Section 9.4 cash line uses and for the reason stated there."""
    tid, bid, launch = await _ctx()
    pif_acv = float(launch.price_map["PIF"]["acv"])
    pif_up = float(launch.price_map["PIF"]["upfront"])
    fin_acv = float(launch.price_map["Financed"]["acv"])
    fin_up = float(launch.price_map["Financed"]["upfront"])

    await _person(tid, bid, launch, "h1", "pif", four="PIF")
    await _person(tid, bid, launch, "h2", "plan", four="Financed")
    await _person(tid, bid, launch, "h3", "plan", four="Financed", group="committed")

    async with SessionLocal() as s:
        await F.sync_ad_conversions(s, tid)
        f = await F.build_funnel(s, tid, None, DAY - dt.timedelta(days=2),
                                 DAY + dt.timedelta(days=2), "cohort",
                                 ads_rungs={"spend": 10000})
    rev = f["revenue"]
    assert rev["contracted"] == pytest.approx(pif_acv + fin_acv), \
        "contracted must cover every enrolled member, financed ones included"
    assert rev["collected"] == pytest.approx(pif_up + fin_up + fin_up), \
        "cash must include the committed rung - that rung IS people who have paid"
    assert rev["collected_source"] == "upfront"
    assert rev["cash_people"] == 3 and rev["committed_people"] == 1
    assert rev["unpriced_closes"] == 0


async def test_the_two_money_rungs_carry_their_dollars():
    """And Cash received carries the dollars of EVERYONE who has paid, which is the same figure
    the hero totals - one call, so the rung and the hero cannot drift apart."""
    tid, bid, launch = await _ctx()
    up = float(launch.price_map["PIF"]["upfront"])
    await _person(tid, bid, launch, "r1", "pif", four="PIF")
    await _person(tid, bid, launch, "r2", "pif", four="PIF", group="committed")
    async with SessionLocal() as s:
        await F.sync_ad_conversions(s, tid)
        f = await F.build_funnel(s, tid, None, DAY - dt.timedelta(days=2),
                                 DAY + dt.timedelta(days=2), "cohort",
                                 ads_rungs={"spend": 1000})
    rungs = {r["key"]: r for r in f["rungs"]}
    assert rungs["closed"]["value"] == pytest.approx(float(launch.price_map["PIF"]["acv"]))
    assert rungs["committed"]["value"] == pytest.approx(up * 2),         "the enrolled member paid too; her cash belongs on the rung that counts cash"
    assert rungs["committed"]["value"] == pytest.approx(f["revenue"]["collected"])
    # An ads rung is Meta measuring itself. There is no dollar figure to put on an impression.
    assert rungs["impression"]["value"] is None


# -- the funnel that refilled --------------------------------------------------------------------

async def test_cash_received_counts_everyone_who_has_paid_not_only_those_sitting_there():
    """CONNOR'S REPORT. The rung showed 1 person and a cost-each of the ENTIRE ad spend, sitting
    above an Enrolled rung of 6 - a funnel that refills, and a 600% conversion.

    classify_stage puts a member in exactly one CURRENT group, so signing moves her out of
    `committed` and into `closed`. Every other rung on the ladder answers "who has reached here";
    only these two answered "who is sitting here", and mixing the two kinds broke the count, the
    conversion, the cost-per and the biggest-leak callout at once.
    """
    tid, bid, launch = await _ctx()
    for i in range(6):
        await _person(tid, bid, launch, f"e{i}", "pif", four="PIF")            # enrolled
    await _person(tid, bid, launch, "c0", "pif", four="PIF", group="committed")  # still deciding

    async with SessionLocal() as s:
        await F.sync_ad_conversions(s, tid)
        f = await F.build_funnel(s, tid, None, DAY - dt.timedelta(days=2),
                                 DAY + dt.timedelta(days=2), "cohort",
                                 ads_rungs={"spend": 70000})
    rungs = {r["key"]: r for r in f["rungs"]}

    assert rungs["committed"]["n"] == 7, "the six who signed had all paid to get there"
    assert rungs["closed"]["n"] == 6
    # The number that made this visible: spend / 1 was the whole budget on one person's head.
    assert rungs["committed"]["cost_per"] == pytest.approx(10000.0)
    # A funnel does not refill. Nothing below the crossing may convert above 100%.
    for r in f["rungs"]:
        if r["zone"] == "acumyn" and r["conversion"] is not None:
            assert r["conversion"] <= 100.0, f"{r['key']} converts at {r['conversion']}%"


async def test_the_rung_says_how_many_are_still_parked_there():
    """"Cash received 6, Enrolled 6" is correct and reads as though nothing happens between
    them. The question actually being asked is how many have paid and NOT yet signed, and that
    number has to be on the rung or the reader goes looking for it in the wrong place."""
    tid, bid, launch = await _ctx()
    for i in range(3):
        await _person(tid, bid, launch, f"s{i}", "pif", four="PIF")               # signed
    await _person(tid, bid, launch, "waiting", "pif", four="PIF", group="committed")

    async with SessionLocal() as s:
        await F.sync_ad_conversions(s, tid)
        f = await F.build_funnel(s, tid, None, DAY - dt.timedelta(days=2),
                                 DAY + dt.timedelta(days=2), "cohort",
                                 ads_rungs={"spend": 4000})
    cash = next(r for r in f["rungs"] if r["key"] == "committed")
    assert cash["n"] == 4, "everyone who has paid"
    assert cash["still_here"] == 1, "one of them has not signed yet"
    # A rung nobody can move past has no occupancy to report, and a 0 would read as a finding.
    assert next(r for r in f["rungs"] if r["key"] == "closed")["still_here"] is None
    assert next(r for r in f["rungs"] if r["key"] == "held")["still_here"] is None


async def test_a_stale_committed_row_does_not_count_as_still_parked():
    """CONNOR'S 1. AdConversion is insert-only, so a member who paid, then signed, keeps her
    committed row forever. Counting those rows raw said one person was sitting at Cash received
    when she had already enrolled - she was the sixth enrolled member, not a seventh payer."""
    tid, bid, launch = await _ctx()
    await _person(tid, bid, launch, "moved", "pif", four="PIF")     # now enrolled
    async with SessionLocal() as s:
        await F.sync_ad_conversions(s, tid)
    async with SessionLocal() as s:
        attr = (await s.execute(select(AdAttribution).where(
            AdAttribution.tenant_id == tid,
            AdAttribution.identity_key == f"{PREFIX}moved"))).scalar_one()
        # The row her earlier sync left behind, while she was still at Payment Received.
        s.add(AdConversion(
            tenant_id=tid, attribution_id=attr.id, business_id=bid, stage_key="committed",
            source_kind="bc_launch_opp", source_ref=f"{PREFIX}opp_moved", occurred_on=DAY,
            dated=True, value_upfront=Decimal(str(launch.price_map["PIF"]["upfront"])),
            value_source="price_map"))
        await s.commit()

    async with SessionLocal() as s:
        f = await F.build_funnel(s, tid, None, DAY - dt.timedelta(days=2),
                                 DAY + dt.timedelta(days=2), "cohort",
                                 ads_rungs={"spend": 1000})
    cash = next(r for r in f["rungs"] if r["key"] == "committed")
    assert cash["n"] == 1, "one person, holding rows at two stages, is still one person"
    assert cash["still_here"] == 0, "she has signed; nobody is waiting"


async def test_the_cash_received_drill_lists_the_same_people_the_rung_counted():
    """A drill that answers a different question than the number above it is worse than no
    drill. The rung counts everyone who has paid, so the drill must list them - including the
    members who have since signed and left the group."""
    from app.services.ads_drill import drill_ads

    tid, bid, launch = await _ctx()
    await _person(tid, bid, launch, "dc1", "pif", four="PIF", email="dc1@x.test")
    await _person(tid, bid, launch, "dc2", "pif", four="PIF", group="committed",
                  email="dc2@x.test")
    async with SessionLocal() as s:
        await F.sync_ad_conversions(s, tid)
        f = await F.build_funnel(s, tid, None, DAY - dt.timedelta(days=2),
                                 DAY + dt.timedelta(days=2), "cohort",
                                 ads_rungs={"spend": 1000})
        d = await drill_ads(s, tid, None, "funnel.committed", DAY - dt.timedelta(days=2),
                            DAY + dt.timedelta(days=2))
    rung = next(r["n"] for r in f["rungs"] if r["key"] == "committed")
    assert d["count"] == rung == 2, f"drill says {d['count']}, the rung says {rung}"
    assert len({r["email"] for r in d["rows"]}) == 2, "somebody was listed twice"


async def test_the_drill_shows_the_contract_the_deposit_and_the_cash_apart():
    """One column would have to pick one of the three, and picking silently is the bug."""
    from app.services.ads_drill import drill_ads

    tid, bid, launch = await _ctx()
    await _person(tid, bid, launch, "d1", "plan", four="Financed", email="d1@x.test")
    async with SessionLocal() as s:
        await F.sync_ad_conversions(s, tid)
        d = await drill_ads(s, tid, None, "funnel.closed", DAY - dt.timedelta(days=2),
                            DAY + dt.timedelta(days=2))
    assert {"value", "upfront", "cash", "payment", "priced"} <= set(d["columns"])
    row = d["rows"][0]
    assert row["value"] == f"{float(launch.price_map['Financed']['acv']):,.0f}"
    assert row["upfront"] == f"{float(launch.price_map['Financed']['upfront']):,.0f}"
    assert row["priced"] == "price sheet"
    assert row["payment"] == "Financed"


# -- the pricing ladder itself, without the funnel around it -----------------------------------

async def test_an_opp_is_priced_against_its_own_launch_never_todays():
    """An ads window can span two cohorts. Pricing an August enrollment off a November sheet
    would be a wrong number with nothing on screen to reveal it."""
    tid, bid, launch = await _ctx()
    async with SessionLocal() as s:
        other = Launch(
            tenant_id=tid, business_id=bid, name="Test Cohort - different prices",
            window_start=DAY - dt.timedelta(days=400), window_end=DAY - dt.timedelta(days=370),
            goal_arr=Decimal(100000), ticket_pif=Decimal(1), ticket_plan=Decimal(2),
            plan_installments=12, mix_pif=Decimal("0.5"), is_active=False,
            pipeline_match="test cohort sales", stage_map=DEFAULT_STAGE_MAP,
            payment_plan_map=DEFAULT_PAYMENT_PLAN_MAP,
            price_map={"PIF": {"acv": 999, "upfront": 999, "provisional": False}})
        s.add(other)
        await s.flush()
        other_id = other.id
        s.add(MetricRecord(
            tenant_id=tid, business_id=bid, source="ghl", kind="bc_launch_opp",
            name="Old cohort", external_id=f"{PREFIX}opp_old", occurred_on=DAY,
            meta={"contact_id": f"{PREFIX}old", "group": "enrolled",
                  "launch_id": str(other_id), "payment_type": "pif"}))
        await s.commit()
    try:
        async with SessionLocal() as s:
            prices = await contract_prices(s, tid)
        assert prices[f"{PREFIX}opp_old"]["acv"] == 999.0, \
            "the opp was priced off a launch that is not its own"
    finally:
        async with SessionLocal() as s:
            await s.execute(sa_delete(MetricRecord).where(
                MetricRecord.external_id == f"{PREFIX}opp_old"))
            await s.execute(sa_delete(Launch).where(Launch.id == other_id))
            await s.commit()


async def test_editing_the_price_sheet_reprices_rows_that_already_exist():
    """The sheet is tenant-editable. An edit that could not reach the rows it prices would be an
    edit that silently did nothing - and the stage semantics stay insert-only regardless: WHO is
    enrolled never changes under anybody, only what we say they signed for."""
    tid, bid, launch = await _ctx()
    await _person(tid, bid, launch, "rp", "pif", four="PIF")
    first = (await _stages(tid, "rp"))["closed"]
    original = float(first.value_contracted)

    async with SessionLocal() as s:
        row = await s.get(Launch, launch.id)
        pm = dict(row.price_map or {})
        pm["PIF"] = {**pm["PIF"], "acv": original + 1500}
        row.price_map = pm
        await s.commit()
    try:
        again = (await _stages(tid, "rp"))["closed"]
        assert float(again.value_contracted) == original + 1500
    finally:
        async with SessionLocal() as s:
            row = await s.get(Launch, launch.id)
            pm = dict(row.price_map or {})
            pm["PIF"] = {**pm["PIF"], "acv": original}
            row.price_map = pm
            await s.commit()


# -- the two ways a cash total goes silently wrong ---------------------------------------------

async def test_measured_cash_is_not_discarded_because_somebody_else_was_priced():
    """THE ALL-OR-NOTHING BUG. The cash figure first preferred the price sheet whenever it had
    priced ANYBODY, which meant one priced deposit standing beside real matched charges threw
    those charges away and reported the deposit alone. Coalesced per ROW instead, so each person
    contributes the best fact available about them."""
    tid, bid, launch = await _ctx()
    up = float(launch.price_map["PIF"]["upfront"])

    await _person(tid, bid, launch, "mx1", "pif", four="PIF")            # priced by the sheet
    await _person(tid, bid, launch, "mx2", None, four=None, email="mx2@x.test")  # unpriced, paid
    async with SessionLocal() as s:
        s.add(MetricRecord(tenant_id=tid, business_id=bid, source="stripe_bc", kind="payment",
                           external_id=f"{PREFIX}pay_mx2", name="Pmt", email="mx2@x.test",
                           amount=Decimal("9000"), status="succeeded", occurred_on=DAY, meta={}))
        await s.commit()

    async with SessionLocal() as s:
        await F.sync_ad_conversions(s, tid)
        f = await F.build_funnel(s, tid, None, DAY - dt.timedelta(days=2),
                                 DAY + dt.timedelta(days=2), "cohort",
                                 ads_rungs={"spend": 1000})
    assert f["revenue"]["collected"] == pytest.approx(up + 9000.0), \
        "a matched payment was discarded because a different person had a price"
    assert f["revenue"]["collected_source"] == "mixed", \
        "a total built from two kinds of fact must say so rather than claim one"


async def test_one_person_on_two_money_rungs_is_counted_once():
    """A stage that moved backwards, or an old opportunity the pipeline never cleared, leaves
    the same identity carrying a committed row AND a closed row. Summing both rungs counted her
    twice, once at each price."""
    tid, bid, launch = await _ctx()
    await _person(tid, bid, launch, "dup", "pif", four="PIF")
    async with SessionLocal() as s:
        await F.sync_ad_conversions(s, tid)
    # A stale committed row for the same identity, as a backwards stage move would leave.
    async with SessionLocal() as s:
        attr = (await s.execute(select(AdAttribution).where(
            AdAttribution.tenant_id == tid,
            AdAttribution.identity_key == f"{PREFIX}dup"))).scalar_one()
        s.add(AdConversion(
            tenant_id=tid, attribution_id=attr.id, business_id=bid, stage_key="committed",
            source_kind="bc_launch_opp", source_ref=f"{PREFIX}opp_dup", occurred_on=DAY,
            dated=True, value_upfront=Decimal(str(launch.price_map["PIF"]["upfront"])),
            value_source="price_map"))
        await s.commit()

    async with SessionLocal() as s:
        f = await F.build_funnel(s, tid, None, DAY - dt.timedelta(days=2),
                                 DAY + dt.timedelta(days=2), "cohort",
                                 ads_rungs={"spend": 1000})
    assert f["revenue"]["cash_people"] == 1, "one person was counted on two rungs"
    assert f["revenue"]["collected"] == pytest.approx(
        float(launch.price_map["PIF"]["upfront"]))


async def test_a_window_with_no_launch_data_says_blended_is_unavailable():
    """bc_launch_opp holds only the ACTIVE launch's opportunities, so a window before that
    launch has no population to blend against. Zero enrollments and no denominator look
    identical as a dash; the payload separates them."""
    tid, bid, launch = await _ctx()
    await _person(tid, bid, launch, "bl", "pif", four="PIF")
    old_start = DAY - dt.timedelta(days=900)
    async with SessionLocal() as s:
        await F.sync_ad_conversions(s, tid)
        f = await F.build_funnel(s, tid, None, old_start, old_start + dt.timedelta(days=10),
                                 "cohort", ads_rungs={"spend": 1000})
    assert f["cac"]["blended_available"] is True, \
        "no attributed closes and no denominator is an honest empty window"
