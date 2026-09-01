"""The attribution spine. SPEC-ads-module.md Parts 4.3, 4.4 and 9.2.

These are the tests that protect the business rather than the code. Getting attribution wrong
does not raise, does not blank a panel, and does not look wrong: it silently moves closed revenue
between campaigns, weeks after anyone made a decision on the old number.
"""
import datetime as dt

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (Ad, AdAccount, AdAttribution, AdCampaign, Business, Integration,
                        MetricRecord, Tenant)
from app.seed import seed
from app.services import ads_funnel as F

D1 = dt.date(2026, 8, 1)
D2 = dt.date(2026, 8, 20)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _setup():
    """One connected account with one campaign and one ad, plus the ids to build registrations.

    GET-OR-CREATE, deliberately. Creating a fresh account on every call left several campaigns
    sharing a name in the same workspace, and the name->campaign map then kept whichever the
    database returned last - so a test asserting on ITS campaign id compared against a different
    one, and every test passed alone while the file failed as a whole.
    """
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == t.id, Business.key == "springb"))).scalar_one()

        acct = (await s.execute(select(AdAccount).where(
            AdAccount.tenant_id == t.id, AdAccount.external_id == "act_attr"))).scalar_one_or_none()
        if acct is None:
            integ = Integration(tenant_id=t.id, provider="meta_ads", status="connected")
            s.add(integ)
            await s.flush()
            acct = AdAccount(tenant_id=t.id, integration_id=integ.id, business_id=biz.id,
                             platform="meta", external_id="act_attr", name="Attr Acct")
            s.add(acct)
            await s.flush()

        camp = (await s.execute(select(AdCampaign).where(
            AdCampaign.tenant_id == t.id, AdCampaign.ad_account_id == acct.id,
            AdCampaign.external_id == "c1"))).scalar_one_or_none()
        if camp is None:
            camp = AdCampaign(tenant_id=t.id, ad_account_id=acct.id, external_id="c1",
                              name="KB-Webinar-Retarget-Q3")
            s.add(camp)
            await s.flush()

        other = (await s.execute(select(AdCampaign).where(
            AdCampaign.tenant_id == t.id, AdCampaign.ad_account_id == acct.id,
            AdCampaign.external_id == "c2"))).scalar_one_or_none()
        if other is None:
            # A real second campaign. Without somewhere for attribution to move TO, the
            # write-once assertion is satisfied by a system that simply never updates anything.
            s.add(AdCampaign(tenant_id=t.id, ad_account_id=acct.id, external_id="c2",
                             name="Some Other Campaign"))
            await s.flush()

        ad = (await s.execute(select(Ad).where(
            Ad.tenant_id == t.id, Ad.ad_account_id == acct.id,
            Ad.external_id == "120210394"))).scalar_one_or_none()
        if ad is None:
            ad = Ad(tenant_id=t.id, ad_account_id=acct.id, campaign_id=camp.id,
                    external_id="120210394", name="KB-Webinar-1080")
            s.add(ad)
        await s.commit()
        return t.id, biz.id, acct.id, camp.id, ad.id


async def _reg(tid, bid, cid, day, **utm):
    async with SessionLocal() as s:
        s.add(MetricRecord(tenant_id=tid, business_id=bid, source="ghl", kind="bc_shift_reg",
                           external_id=cid, name=f"Person {cid}", status="registered",
                           occurred_on=day,
                           meta={"contact_id": cid, "channel": "Meta", **utm}))
        await s.commit()


async def _clear_regs(tid):
    """Only the registrations.

    _ghl_snapshot REPLACES the whole bc_shift_reg set on every sync, so this is exactly what the
    real sync does to the source rows - and the attribution row surviving that replacement is
    the entire behaviour under test.
    """
    async with SessionLocal() as s:
        for r in (await s.execute(select(MetricRecord).where(
                MetricRecord.tenant_id == tid,
                MetricRecord.kind == "bc_shift_reg"))).scalars():
            await s.delete(r)
        await s.commit()


async def _clear(tid):
    """Registrations AND attribution - a clean slate between unrelated tests.

    NOT for use inside the write-once test: clearing the attribution there would rebuild the very
    row the test exists to prove is never rebuilt, and the test would pass while proving nothing.
    """
    async with SessionLocal() as s:
        for a in (await s.execute(select(AdAttribution).where(
                AdAttribution.tenant_id == tid))).scalars():
            await s.delete(a)
        await s.commit()
    await _clear_regs(tid)


# ── the resolution order, pure ────────────────────────────────────────────────────────
def test_the_grades_resolve_strictest_first():
    class _O:
        def __init__(self, i, c=None, a=None):
            self.id, self.campaign_id, self.ad_account_id = i, c, a

    ads = {"120210394": _O("AD", c="CAMP", a="ACCT")}
    camps = {"kb-webinar-retarget-q3": _O("CAMP", a="ACCT")}
    camps["kb-webinar-retarget-q3"].campaign_id = None

    # utm_content wins over utm_campaign when both are present.
    m = F.resolve_match({"utm_content": "120210394", "utm_campaign": "KB-Webinar-Retarget-Q3",
                         "utm_source": "meta"}, ads, camps)
    assert m["match_method"] == "ad" and m["ad_id"] == "AD" and m["campaign_id"] == "CAMP"

    # A campaign name still matches when the content is not a known ad.
    m = F.resolve_match({"utm_content": "not-an-ad", "utm_campaign": "kb-webinar-retarget-q3",
                         "utm_source": "meta"}, ads, camps)
    assert m["match_method"] == "campaign" and m["ad_id"] is None

    # Source alone is channel grade and grants neither FK.
    m = F.resolve_match({"utm_source": "Instagram"}, ads, camps)
    assert m["match_method"] == "channel" and m["ad_id"] is None and m["campaign_id"] is None

    # Nothing ad-bearing gets NO ROW at all.
    assert F.resolve_match({"utm_source": "newsletter"}, ads, camps) is None
    assert F.resolve_match({}, ads, camps) is None


def test_campaign_names_match_case_and_whitespace_insensitively():
    class _O:
        def __init__(self):
            self.id, self.campaign_id, self.ad_account_id = "CAMP", None, "ACCT"
    camps = {"kb webinar q3": _O()}
    for written in ("KB Webinar Q3", "kb  webinar   q3", "  KB WEBINAR Q3  "):
        assert F.resolve_match({"utm_campaign": written}, {}, camps)["match_method"] == "campaign"


# ── the rules that protect revenue ────────────────────────────────────────────────────
async def test_attribution_is_write_once():
    """THE TEST THAT STOPS REVENUE SILENTLY RELOCATING.

    GHL replaces its record set every sync, so a contact's UTM is current state. If the contact
    is later re-tagged - a second campaign, a retargeting touch, a rep editing a field - a
    re-derived attribution would move that person's eventual enrollment to a different campaign,
    and last month's report would change with nothing to point at.
    """
    tid, bid, acct, camp, ad = await _setup()
    await _clear(tid)
    await _reg(tid, bid, "c-writeonce", D1, utm_campaign="KB-Webinar-Retarget-Q3", utm_source="meta")

    async with SessionLocal() as s:
        await F.sync_ad_attribution(s, tid)
    async with SessionLocal() as s:
        row = (await s.execute(select(AdAttribution).where(
            AdAttribution.tenant_id == tid,
            AdAttribution.identity_key == "c-writeonce"))).scalar_one()
        first_campaign, first_day, created = row.campaign_id, row.first_seen_on, row.created_at

    # The source changes underneath: same contact, different campaign, later date. Only the
    # REGISTRATIONS are cleared - clearing the attribution would rebuild the row under test.
    await _clear_regs(tid)
    await _reg(tid, bid, "c-writeonce", D2, utm_campaign="Some Other Campaign", utm_source="meta")
    async with SessionLocal() as s:
        await F.sync_ad_attribution(s, tid)

    async with SessionLocal() as s:
        row = (await s.execute(select(AdAttribution).where(
            AdAttribution.tenant_id == tid,
            AdAttribution.identity_key == "c-writeonce"))).scalar_one()
    assert row.campaign_id == first_campaign, "the campaign moved - this is the whole bug"
    assert row.first_seen_on == first_day, "the cohort day moved"
    assert row.created_at == created
    assert row.last_seen_on == D2, "last_seen_on is the ONE field allowed to move"


async def test_a_campaign_match_never_grants_an_ad_id():
    """Part 4.4. A campaign-grade match may not appear in ad-level revenue, and the schema-level
    guarantee is that ad_id stays NULL - the read service is not trusted to remember."""
    tid, bid, *_ = await _setup()
    await _clear(tid)
    await _reg(tid, bid, "c-camp", D1, utm_campaign="KB-Webinar-Retarget-Q3", utm_source="meta")
    async with SessionLocal() as s:
        await F.sync_ad_attribution(s, tid)
        row = (await s.execute(select(AdAttribution).where(
            AdAttribution.identity_key == "c-camp"))).scalar_one()
    assert row.match_method == "campaign"
    assert row.ad_id is None
    assert row.campaign_id is not None


async def test_a_channel_match_grants_no_campaign():
    """utm_source alone may be counted in channel totals and must never appear in a campaign's
    revenue. Both foreign keys stay NULL."""
    tid, bid, *_ = await _setup()
    await _clear(tid)
    await _reg(tid, bid, "c-chan", D1, utm_source="facebook")
    async with SessionLocal() as s:
        await F.sync_ad_attribution(s, tid)
        row = (await s.execute(select(AdAttribution).where(
            AdAttribution.identity_key == "c-chan"))).scalar_one()
    assert row.match_method == "channel"
    assert row.campaign_id is None and row.ad_id is None


async def test_an_ad_match_sets_both_keys_and_is_the_only_grade_that_can():
    tid, bid, acct, camp, ad = await _setup()
    await _clear(tid)
    await _reg(tid, bid, "c-ad", D1, utm_content="120210394", utm_source="meta")
    async with SessionLocal() as s:
        await F.sync_ad_attribution(s, tid)
        row = (await s.execute(select(AdAttribution).where(
            AdAttribution.identity_key == "c-ad"))).scalar_one()
    assert row.match_method == "ad" and row.confidence == "exact"
    assert row.ad_id == ad and row.campaign_id == camp


async def test_an_unattributed_identity_gets_no_row_at_all():
    """Not a placeholder row with match_method='none'. The read service counts unattributed
    closes from the source population, so a placeholder would be a second thing to keep in sync
    and a second thing to explain."""
    tid, bid, *_ = await _setup()
    await _clear(tid)
    await _reg(tid, bid, "c-organic", D1, utm_source="newsletter")
    async with SessionLocal() as s:
        stats = await F.sync_ad_attribution(s, tid)
        rows = list((await s.execute(select(AdAttribution).where(
            AdAttribution.identity_key == "c-organic"))).scalars())
    assert rows == []
    assert stats["unattributed"] == 1


async def test_an_undated_registration_is_counted_and_skipped_not_dated_today():
    """A registration with no date has no cohort day, and first_seen_on IS the cohort day.
    Dating it 'today' would file the identity in whatever cohort the worker happened to tick in,
    which moves attributed revenue every time the scheduler runs."""
    tid, bid, *_ = await _setup()
    await _clear(tid)
    await _reg(tid, bid, "c-undated", None, utm_campaign="KB-Webinar-Retarget-Q3", utm_source="meta")
    async with SessionLocal() as s:
        stats = await F.sync_ad_attribution(s, tid)
        rows = list((await s.execute(select(AdAttribution).where(
            AdAttribution.identity_key == "c-undated"))).scalars())
    assert rows == []
    assert stats["undated"] == 1


async def test_a_contact_can_only_have_one_registration_row():
    """A fact about the data model that shapes the whole spine, worth pinning because the first
    version of this file assumed otherwise.

    metric_record is UNIQUE on (tenant_id, source, kind, external_id) and the shift sync writes
    external_id = the contact id. So a contact has at most ONE bc_shift_reg row, ever - "first
    touch" per contact is not a choice between competing rows, it is the single row that exists,
    and what the sync must survive is that row being REPLACED with different UTM.
    """
    from sqlalchemy.exc import IntegrityError

    tid, bid, *_ = await _setup()
    await _clear(tid)
    await _reg(tid, bid, "c-one", D1, utm_source="meta")
    with pytest.raises(IntegrityError):
        await _reg(tid, bid, "c-one", D2, utm_source="meta")


async def test_processing_order_does_not_decide_the_cohort_day():
    """Registrations are processed oldest first, so the cohort day cannot depend on what order
    the database happened to return rows in. Asserted across CONTACTS, since one contact can
    only have one row."""
    tid, bid, *_ = await _setup()
    await _clear(tid)
    await _reg(tid, bid, "c-late", D2, utm_campaign="KB-Webinar-Retarget-Q3", utm_source="meta")
    await _reg(tid, bid, "c-early", D1, utm_campaign="KB-Webinar-Retarget-Q3", utm_source="meta")
    async with SessionLocal() as s:
        await F.sync_ad_attribution(s, tid)
        rows = {r.identity_key: r for r in (await s.execute(select(AdAttribution).where(
            AdAttribution.tenant_id == tid))).scalars()}
    assert rows["c-early"].first_seen_on == D1
    assert rows["c-late"].first_seen_on == D2


async def test_coverage_reports_the_grain_each_number_is_entitled_to():
    """A rising channel-only share is the leading indicator that tagging has degraded - the
    moment before the whole number stops being trustworthy."""
    tid, bid, *_ = await _setup()
    await _clear(tid)
    await _reg(tid, bid, "cov-ad", D1, utm_content="120210394", utm_source="meta")
    await _reg(tid, bid, "cov-camp", D1, utm_campaign="KB-Webinar-Retarget-Q3", utm_source="meta")
    await _reg(tid, bid, "cov-chan", D1, utm_source="instagram")
    await _reg(tid, bid, "cov-none", D1, utm_source="newsletter")
    async with SessionLocal() as s:
        await F.sync_ad_attribution(s, tid)
        cov = await F.attribution_coverage(s, tid, D1, D2)

    assert cov["registrations_total"] == 4
    assert cov["registrations_matched"] == 3
    assert cov["registrations_unattributed"] == 1
    assert cov["by_grade"] == {"ad": 1, "campaign": 1, "channel": 1}
    assert cov["campaign_grade_or_better"] == 2      # channel grade does NOT count toward this
    assert cov["ad_grade"] == 1


async def test_attribution_is_scoped_to_one_workspace():
    """The uniqueness key includes tenant_id, so two workspaces holding the same GHL contact id
    are two independent rows and neither can see the other's."""
    tid, bid, *_ = await _setup()
    await _clear(tid)
    await _reg(tid, bid, "shared-contact", D1, utm_campaign="KB-Webinar-Retarget-Q3", utm_source="meta")
    async with SessionLocal() as s:
        await F.sync_ad_attribution(s, tid)
        rows = list((await s.execute(select(AdAttribution).where(
            AdAttribution.identity_key == "shared-contact"))).scalars())
    assert len(rows) == 1 and rows[0].tenant_id == tid


async def test_a_workspace_with_no_ad_account_is_skipped_rather_than_erroring():
    """Most workspaces will never connect Meta. The daily worker tick runs for all of them and
    must be a no-op rather than an exception in the logs every night."""
    from app.services.provisioning import provision_tenant

    async with SessionLocal() as s:
        r = await provision_tenant(s, slug="noads2", name="No Ads",
                                   owner_email="o@noads2.test", hostname="noads2.localhost")
        other = r.tenant_id
    try:
        async with SessionLocal() as s:
            assert (await F.sync_ad_attribution(s, other)).get("skipped")
    finally:
        from tests.test_ads_api import _remove_tenant
        await _remove_tenant(other)


# ── organic traffic is not paid traffic ───────────────────────────────────────────────
def test_an_organic_instagram_click_is_not_credited_to_paid_spend():
    """FOUND LIVE, by a customer asking why somebody labelled Organic in one drawer was sitting
    in the Meta funnel in another.

    utm_source=ig, utm_medium=social, no campaign is a link in an Instagram bio. Granting channel
    grade on the SOURCE alone counted it against ad spend - 48 of 51 channel-grade rows on the
    live account were this, inflating the denominator of every cost-per figure on the tab.

    The launch classifier had already placed her outside Meta, on the same row: channel read
    "Organic / Existing" while match_method read "channel". The row disagreed with itself.
    """
    from app.services.ads_funnel import resolve_match

    utm = {"utm_source": "ig", "utm_medium": "social", "utm_campaign": None}
    assert resolve_match(utm, {}, {}, channel="Organic / Existing") is None
    # ...and a genuine Meta click through the same rung still resolves.
    assert resolve_match(utm, {}, {}, channel="Meta")["match_method"] == "channel"


def test_the_channel_is_a_veto_never_a_promotion():
    """A launch channel of Meta must not manufacture attribution for somebody whose UTM says
    nothing. The classifier can only take the channel rung away, never grant it."""
    from app.services.ads_funnel import resolve_match

    assert resolve_match({"utm_source": "newsletter"}, {}, {}, channel="Meta") is None
    assert resolve_match({}, {}, {}, channel="Meta") is None


def test_a_campaign_id_in_the_utm_still_resolves_to_its_campaign():
    """Some ad sets template the campaign ID rather than the name. That is a real click wearing
    an unreadable label, and the id is one already held - matching only on name dropped it to
    channel grade, and after the veto above it would have dropped out entirely."""
    from app.services.ads_funnel import resolve_match

    class _C:
        id, ad_account_id, external_id = "camp-uuid", "acct-uuid", "120248784204810082"

    m = resolve_match({"utm_source": "ig", "utm_campaign": "120248784204810082"}, {}, {},
                      channel="Organic / Existing", campaigns_by_ext={"120248784204810082": _C})
    assert m["match_method"] == "campaign" and m["campaign_id"] == "camp-uuid"


def test_name_matching_still_wins_and_is_unaffected():
    from app.services.ads_funnel import resolve_match

    class _C:
        id, ad_account_id = "by-name", "acct"

    m = resolve_match({"utm_campaign": "KB - The Shift - August2026"}, {},
                      {"kb - the shift - august2026": _C}, channel="Organic / Existing")
    assert m["match_method"] == "campaign" and m["campaign_id"] == "by-name"
