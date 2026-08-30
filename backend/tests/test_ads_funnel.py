"""The funnel to closed revenue. SPEC-ads-module.md Parts 4.5, 4.7 and 9.3/9.5.

The phase the module exists for, and the tests that keep its numbers honest. Every failure mode
here produces a plausible figure rather than an error - a blended CAC quoted as the ads number, a
contracted and a collected figure averaged into one, a cohort read as a period.
"""
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (AdAttribution, AdConversion, Business, MetricRecord, SalesCall, Tenant)
from app.seed import seed
from app.services import ads_funnel as F

W1, W2 = dt.date(2026, 6, 1), dt.date(2026, 6, 30)      # the cohort window
LATE = dt.date(2026, 8, 15)                              # a close landing two months later


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _ids():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        b = (await s.execute(select(Business).where(
            Business.tenant_id == t.id, Business.key == "springb"))).scalar_one()
        return t.id, b.id


async def _attr(tid, bid, key, day, email=None):
    async with SessionLocal() as s:
        a = AdAttribution(tenant_id=tid, identity_kind="ghl_contact", identity_key=key,
                          email_norm=email, business_id=bid, channel="Meta",
                          match_method="campaign", confidence="probable",
                          first_seen_on=day, last_seen_on=day)
        s.add(a)
        await s.commit()
        return a.id


async def _close(tid, bid, cid, day, amount, payment=None):
    async with SessionLocal() as s:
        s.add(MetricRecord(tenant_id=tid, business_id=bid, source="ghl", kind="bc_onboarded",
                           external_id=f"onb-{cid}", name=f"P {cid}", status="won",
                           amount=amount, occurred_on=day,
                           meta={"contact_id": cid, "payment": payment}))
        await s.commit()


async def _payment(tid, bid, email, amount, day):
    async with SessionLocal() as s:
        s.add(MetricRecord(tenant_id=tid, business_id=bid, source="stripe_legacy", kind="payment",
                           external_id=f"pay-{email}-{amount}", name="Pmt", email=email,
                           amount=amount, status="succeeded", occurred_on=day, meta={}))
        await s.commit()


async def _wipe(tid):
    async with SessionLocal() as s:
        for m in (AdConversion, AdAttribution):
            for r in (await s.execute(select(m).where(m.tenant_id == tid))).scalars():
                await s.delete(r)
        for r in (await s.execute(select(MetricRecord).where(
                MetricRecord.tenant_id == tid,
                MetricRecord.kind.in_(("bc_onboarded", "payment", "bc_shift_reg",
                                       "bc_launch_opp"))))).scalars():
            await s.delete(r)
        for r in (await s.execute(select(SalesCall).where(SalesCall.tenant_id == tid))).scalars():
            await s.delete(r)
        await s.commit()


async def test_contracted_and_collected_never_merge():
    """A financed enrollment CONTRACTS 14000 and COLLECTS 1167 in month one. Reporting either
    alone is a lie in one direction or the other, and averaging them is a lie in both. No field
    anywhere may hold 15167, and none may hold the mean."""
    tid, bid = await _ids()
    await _wipe(tid)
    await _attr(tid, bid, "c-fin", W1, email="fin@x.com")
    await _close(tid, bid, "c-fin", W1, Decimal("14000"), payment="financed")
    await _payment(tid, bid, "fin@x.com", Decimal("1167"), W1)

    async with SessionLocal() as s:
        await F.sync_ad_conversions(s, tid)
        row = (await s.execute(select(AdConversion).where(
            AdConversion.tenant_id == tid, AdConversion.stage_key == "closed"))).scalar_one()
    assert float(row.value_contracted) == 14000.0
    assert float(row.value_collected) == 1167.0
    for bad in (15167.0, (14000 + 1167) / 2):
        assert float(row.value_contracted) != bad and float(row.value_collected) != bad


async def test_a_monthly_membership_is_flagged_annualized():
    """Twelve months is a MODELLING CHOICE, not a signed number, so the row carries the flag and
    the UI can say so. An unflagged annualization is a projection wearing a fact's clothes."""
    tid, bid = await _ids()
    await _wipe(tid)
    await _attr(tid, bid, "c-mon", W1)
    await _close(tid, bid, "c-mon", W1, Decimal("1200"), payment="monthly")
    async with SessionLocal() as s:
        await F.sync_ad_conversions(s, tid)
        row = (await s.execute(select(AdConversion).where(
            AdConversion.stage_key == "closed"))).scalar_one()
    assert float(row.value_contracted) == 14400.0     # 1200 x 12
    assert row.value_annualized is True

    await _wipe(tid)
    await _attr(tid, bid, "c-pif", W1)
    await _close(tid, bid, "c-pif", W1, Decimal("12000"), payment="pif")
    async with SessionLocal() as s:
        await F.sync_ad_conversions(s, tid)
        row = (await s.execute(select(AdConversion).where(
            AdConversion.stage_key == "closed"))).scalar_one()
    assert float(row.value_contracted) == 12000.0
    assert row.value_annualized is False


async def test_blended_cac_is_present_lower_and_labeled():
    """Somebody will compute the blended number anyway. Including it labeled is how it stops
    being quoted as the ads number."""
    tid, bid = await _ids()
    await _wipe(tid)
    await _attr(tid, bid, "c-att", W1)
    await _close(tid, bid, "c-att", W1, Decimal("12000"), payment="pif")
    # Three more enrollments in the window that no ad can claim.
    for i in range(3):
        await _close(tid, bid, f"c-org{i}", W1, Decimal("12000"), payment="pif")

    async with SessionLocal() as s:
        await F.sync_ad_conversions(s, tid)
        f = await F.build_funnel(s, tid, None, W1, W2, "cohort", ads_rungs={"spend": 12000})

    assert f["cac"]["attributed"] == pytest.approx(12000.0)   # 12000 / 1 traced close
    assert f["cac"]["blended"] == pytest.approx(3000.0)       # 12000 / 4 closes in the window
    assert f["cac"]["blended"] < f["cac"]["attributed"]
    assert "never the ads number" in f["cac"]["blended_label"]


async def test_unattributed_closes_are_counted_and_assigned_to_nobody():
    """The structural ceiling from Part 4.7, made visible. Word of mouth, the list, a referral,
    a phone-to-laptop hop - these will never be attributable, and naming the count stops the
    attributed number reading as a failure."""
    tid, bid = await _ids()
    await _wipe(tid)
    await _attr(tid, bid, "c-known", W1)
    await _close(tid, bid, "c-known", W1, Decimal("12000"), payment="pif")
    await _close(tid, bid, "c-stranger", W1, Decimal("12000"), payment="pif")

    async with SessionLocal() as s:
        await F.sync_ad_conversions(s, tid)
        f = await F.build_funnel(s, tid, None, W1, W2, "cohort", ads_rungs={"spend": 6000})

    assert f["unattributed"]["closes"] == 1
    assert f["cac"]["attributed_closes"] == 1
    assert f["cac"]["all_closes"] == 2
    # The stranger's 12000 must not appear in attributed revenue.
    assert f["revenue"]["contracted"] == pytest.approx(12000.0)


async def test_cohort_basis_differs_from_period_basis():
    """Spend in window W, the close landing in W+2. Cohort attributes it to W - what marketing
    needs, because this month's revenue came from last quarter's spend. Period attributes it to
    W+2 - what accounting wants. Both are right; the payload always names which it is."""
    tid, bid = await _ids()
    await _wipe(tid)
    await _attr(tid, bid, "c-lag", W1)              # first touched in June
    await _close(tid, bid, "c-lag", LATE, Decimal("12000"), payment="pif")   # enrolled in August

    async with SessionLocal() as s:
        await F.sync_ad_conversions(s, tid)
        cohort = await F.build_funnel(s, tid, None, W1, W2, "cohort", ads_rungs={"spend": 5000})
        period = await F.build_funnel(s, tid, None, W1, W2, "period", ads_rungs={"spend": 5000})

    assert cohort["basis"] == "cohort" and period["basis"] == "period"
    # Cohort sees the revenue even though it landed outside the window.
    assert cohort["revenue"]["contracted"] == pytest.approx(12000.0)
    # Period does not: nothing was RECOGNISED in June.
    assert period["revenue"]["contracted"] == pytest.approx(0.0)


async def test_no_curve_means_no_projection_anywhere():
    """A guessed curve is worse than an absent one, because it looks like a number. Until one is
    fitted from complete cohorts, every projected figure is null and maturity says it is."""
    tid, bid = await _ids()
    await _wipe(tid)
    await _attr(tid, bid, "c-proj", W1)
    await _close(tid, bid, "c-proj", W1, Decimal("12000"), payment="pif")
    async with SessionLocal() as s:
        await F.sync_ad_conversions(s, tid)
        f = await F.build_funnel(s, tid, None, W1, W2, "cohort", ads_rungs={"spend": 3000})

    assert f["maturity"]["fitted"] is False
    assert f["maturity"]["pct"] is None
    assert f["revenue"]["projected"] is None
    assert f["revenue"]["roas_projected"] is None
    # ...while the figures that ARE measured still render.
    assert f["revenue"]["roas_contracted"] == pytest.approx(4.0)


async def test_held_reads_the_sales_desk_log_not_a_live_ghl_field():
    """One definition of held, in one place. A no-show that survived a rebook must stay a
    no-show, which is exactly what is_current on the Desk's log preserves."""
    tid, bid = await _ids()
    await _wipe(tid)
    aid = await _attr(tid, bid, "c-call", W1)
    async with SessionLocal() as s:
        launch = (await s.execute(select(__import__("app.models", fromlist=["Launch"]).Launch)
                                  .where())).scalars().first()
        s.add(SalesCall(tenant_id=tid, launch_id=launch.id, opportunity_id="o1",
                        contact_id="c-call", booking_id="b1",
                        call_time_utc=dt.datetime(2026, 6, 5, tzinfo=dt.timezone.utc),
                        outcome="No Show", is_current=False))     # superseded by the rebook
        s.add(SalesCall(tenant_id=tid, launch_id=launch.id, opportunity_id="o2",
                        contact_id="c-call", booking_id="b2",
                        call_time_utc=dt.datetime(2026, 6, 9, tzinfo=dt.timezone.utc),
                        outcome="Showed",
                        outcome_at=dt.datetime(2026, 6, 9, tzinfo=dt.timezone.utc),
                        is_current=True))
        await s.commit()

    async with SessionLocal() as s:
        await F.sync_ad_conversions(s, tid)
        stages = {c.stage_key for c in (await s.execute(select(AdConversion).where(
            AdConversion.tenant_id == tid))).scalars()}
    assert "booked" in stages and "held" in stages


async def test_an_undated_stage_is_counted_and_never_timed():
    """Phase 0 measured outcome_at on only 80 percent of calls. A stage with no date is COUNTED
    in the funnel and excluded from every duration - counting it is honest, timing it is not."""
    tid, bid = await _ids()
    await _wipe(tid)
    await _attr(tid, bid, "c-nodate", W1)
    async with SessionLocal() as s:
        launch = (await s.execute(select(__import__("app.models", fromlist=["Launch"]).Launch)
                                  .where())).scalars().first()
        s.add(SalesCall(tenant_id=tid, launch_id=launch.id, opportunity_id="o3",
                        contact_id="c-nodate", booking_id="b3",
                        outcome="Showed", outcome_at=None, is_current=True))
        await s.commit()

    async with SessionLocal() as s:
        await F.sync_ad_conversions(s, tid)
        held = (await s.execute(select(AdConversion).where(
            AdConversion.tenant_id == tid, AdConversion.stage_key == "held"))).scalar_one()
        f = await F.build_funnel(s, tid, None, W1, W2, "cohort", ads_rungs={"spend": 100})

    assert held.dated is False and held.occurred_on is None
    rung = next(r for r in f["rungs"] if r["key"] == "held")
    assert rung["n"] == 1 and rung["undated"] == 1     # counted, and its lack of a date reported


async def test_meta_rungs_are_never_added_to_acumyn_rungs():
    """Part 4.8. Meta's lead count and Acumyn's matched registrations measure overlapping
    populations. The ladder keeps them in separate zones and each rung reports its own count."""
    zones = {r["key"]: r["zone"] for r in F.FUNNEL_DEFS["program"]}
    assert zones["impression"] == zones["click"] == zones["lead"] == "meta"
    assert zones["registered"] == zones["closed"] == "acumyn"
    lead = next(r for r in F.FUNNEL_DEFS["program"] if r["key"] == "lead")
    assert lead.get("diagnostic") is True, "Meta's lead count is never a denominator"
