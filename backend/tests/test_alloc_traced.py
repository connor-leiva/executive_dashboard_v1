"""Traced allocations: Meta's spend is the ratio, QuickBooks' figure is the pool
(ALLOCATION-RULES-SPEC 5.4, D13, Phase 6).

Appendix B7 is the gate: three campaigns, two mapped and one not, where the mapped shares and
the unmapped share together account for 100% of the month's spend. The unmapped fraction's money
stays on the pool entity and is reported with its dollars, because a map with a hole in it is
only actionable if somebody is told how much fell through.

`match_name` is shared with campaign grouping on purpose. A copy of `_norm` in the engine would
be a second normalization and the two would drift the first time one learned about a new kind of
dash, which is exactly how the live account's nineteen campaigns once all fell to "Other".
"""
import datetime as dt
import uuid
from decimal import Decimal as D

import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import (AdAccount, AdCampaign, AdInsightDaily, AllocationContribution,
                        AllocationRule, Business, CoaMap, Integration, StandardAccount, Tenant,
                        User)
from app.seed import seed
from app.services import alloc_engine as E
from app.services import alloc_rules as AR
from app.services.ads_rules import classify_campaign, match_name
from app.services.coa import seed_standard_chart

MONTH = (dt.date(2026, 7, 1), dt.date(2026, 7, 31))
TODAY = dt.date(2026, 9, 30)
JULY = dt.date(2026, 7, 14)

# B7's three campaigns: 6,000 / 3,000 / 1,000, so the shares are 60 / 30 / 10 and the 10 is the
# one that matches nothing. Unequal on purpose, and the pool below does not divide evenly by
# them either.
CAMPAIGNS = [("Forum - VIP Day", "6000.00"),
             ("KB - BeCollective - Launch", "3000.00"),
             ("Brand Awareness", "1000.00")]


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        await seed_standard_chart(s, t.id)
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
        std = {m.code: m.id for m in (await s.execute(select(StandardAccount).where(
            StandardAccount.tenant_id == t.id))).scalars()}
        return t.id, biz, actor, std


@pytest.fixture(autouse=True)
async def _clean():
    tenant_id, biz, _a, _s = await _ctx()
    async with SessionLocal() as s:
        await s.execute(delete(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id))
        await s.execute(delete(AllocationRule).where(AllocationRule.tenant_id == tenant_id))
        await s.execute(delete(AdInsightDaily).where(AdInsightDaily.tenant_id == tenant_id))
        await s.execute(delete(AdCampaign).where(AdCampaign.tenant_id == tenant_id))
        await s.execute(delete(AdAccount).where(AdAccount.tenant_id == tenant_id))
        await s.commit()
    yield


async def _ad_account(tenant_id, business_id, external_id="act_test") -> uuid.UUID:
    async with SessionLocal() as s:
        integ = (await s.execute(select(Integration).where(
            Integration.tenant_id == tenant_id))).scalars().first()
        a = AdAccount(tenant_id=tenant_id, integration_id=integ.id, business_id=business_id,
                      platform="meta", external_id=external_id, name="Test account",
                      currency="USD", timezone_name="America/Denver", status="connected")
        s.add(a)
        await s.commit()
        return a.id


async def _spend(tenant_id, ad_account_id, rows=CAMPAIGNS, on=JULY, level="campaign"):
    async with SessionLocal() as s:
        for i, (name, amount) in enumerate(rows):
            # Unique per call: the same campaign name can legitimately be loaded again
            # at another level or in another month, and `uq` is on (account, external_id).
            c = AdCampaign(tenant_id=tenant_id, ad_account_id=ad_account_id,
                           external_id=f"c{i}-{external(name)}-{level}-{on.isoformat()}",
                           name=name)
            s.add(c)
            await s.flush()
            s.add(AdInsightDaily(
                tenant_id=tenant_id, ad_account_id=ad_account_id, level=level,
                object_external_id=c.external_id, campaign_id=c.id, occurred_on=on,
                spend=D(amount)))
        await s.commit()


def external(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())[:20]


def _spec(biz, account_ids=None):
    """B7's map: the Forum by prefix, beCollective by contains, and nothing for the third."""
    out = {"source": "meta_campaigns",
           "map": [{"match": "prefix", "value": "forum", "business_id": str(biz["the_forum"])},
                   {"match": "contains", "value": "becollective",
                    "business_id": str(biz["becollective"])}]}
    if account_ids:
        out["ad_account_ids"] = [str(a) for a in account_ids]
    return out


# ── match_name is shared with campaign grouping ───────────────────────────────────────────

def test_match_name_follows_the_separator_rules_grouping_already_uses():
    """The live account names campaigns `KB - The Shift - August2026`, with spaces around the
    dash. Losing that handling once made all nineteen fall to Other."""
    assert match_name("KB - The Shift - August2026", "prefix", "kb-") is True
    assert match_name("KB-Webinar-Retarget", "prefix", "kb-") is True
    assert match_name("Forum - VIP Day", "prefix", "forum") is True
    assert match_name("KB - BeCollective - Launch", "contains", "becollective") is True
    assert match_name("Brand Awareness", "prefix", "forum") is False


def test_match_name_refuses_the_patterns_that_would_match_everything():
    assert match_name("anything", "prefix", "") is False
    assert match_name("anything", "prefix", "   ") is False
    assert match_name("", "prefix", "forum") is False
    assert match_name("anything", "regex", "any") is False, "an unknown kind matches nothing"


def test_campaign_grouping_still_behaves_after_sharing_the_matcher():
    """`classify_campaign` calls `match_name` now. Its own behaviour must not have moved: the
    split form slices the case-preserved tail, which is how an operator recognises the thing."""
    assert classify_campaign("KB - The Shift - August2026").startswith("KB")
    assert "The Shift" in classify_campaign("KB - The Shift - August2026")
    assert classify_campaign("random thing") == "Other"
    assert classify_campaign("") == "Other"


# ── traced_shares, pure ───────────────────────────────────────────────────────────────────

def test_b7_the_mapped_shares_and_the_unmapped_one_account_for_every_dollar():
    """The gate. 60 / 30 / 10, where the 10 matched nothing and therefore stays put."""
    biz = {"the_forum": uuid.uuid4(), "becollective": uuid.uuid4()}
    spend = {name: D(amount) for name, amount in CAMPAIGNS}
    shares, unmapped, reason = E.traced_shares(spend, _spec(biz))
    assert reason is None
    assert unmapped == D("1000.00")
    assert shares[biz["the_forum"]] == D("6000.00") / D("10000.00")
    assert shares[biz["becollective"]] == D("3000.00") / D("10000.00")
    # The mapped shares plus the unmapped fraction are the whole thing.
    assert sum(shares.values()) + (unmapped / D("10000.00")) == D(1)


def test_the_unmapped_fraction_stays_on_the_pool_entity_to_the_cent():
    """Meta is the ratio and QuickBooks is the pool, so the pool is a figure of its own that
    need not divide evenly by Meta's percentages."""
    biz = {"the_forum": uuid.uuid4(), "becollective": uuid.uuid4()}
    spend = {name: D(amount) for name, amount in CAMPAIGNS}
    shares, unmapped, _r = E.traced_shares(spend, _spec(biz))
    pool = D("12345.67")
    parts = E.split_amounts(pool, shares)
    left = E.retained(pool, parts)
    assert sum(parts.values()) + left == pool, "every cent of the pool is accounted for"
    assert left == D("1234.57"), "ten percent of the pool, rounded once"
    assert unmapped == D("1000.00"), "and Meta's own unmatched figure is separate from it"


def test_a_month_with_no_spend_at_all_reports_rather_than_resolving():
    biz = {"the_forum": uuid.uuid4(), "becollective": uuid.uuid4()}
    assert E.traced_shares({}, _spec(biz))[2] == "source_not_connected"
    assert E.traced_shares({"x": D(0)}, _spec(biz))[2] == "source_not_connected"


def test_everything_unmapped_leaves_the_whole_pool_where_it_was():
    biz = {"the_forum": uuid.uuid4(), "becollective": uuid.uuid4()}
    spend = {"Brand Awareness": D("500"), "Retargeting": D("500")}
    shares, unmapped, reason = E.traced_shares(spend, _spec(biz))
    assert reason is None and shares == {} and unmapped == D("1000")
    pool = D("999.99")
    assert E.retained(pool, E.split_amounts(pool, shares)) == pool


def test_first_match_wins_in_map_order():
    """A campaign matching two entries is the author's ordering decision, the same precedence
    `classify_campaign` gives its rules."""
    a, b = uuid.uuid4(), uuid.uuid4()
    spec = {"source": "meta_campaigns",
            "map": [{"match": "contains", "value": "launch", "business_id": str(a)},
                    {"match": "contains", "value": "becollective", "business_id": str(b)}]}
    shares, unmapped, _r = E.traced_shares({"KB - BeCollective - Launch": D("1000")}, spec)
    assert list(shares) == [a] and unmapped == ZERO_D()
    spec["map"].reverse()
    shares, _u, _r = E.traced_shares({"KB - BeCollective - Launch": D("1000")}, spec)
    assert list(shares) == [b]


def ZERO_D():
    return D("0.00")


def test_a_map_entry_for_an_entity_with_no_spend_simply_gets_nothing():
    biz = {"the_forum": uuid.uuid4(), "becollective": uuid.uuid4()}
    shares, unmapped, reason = E.traced_shares({"Forum - VIP Day": D("1000")}, _spec(biz))
    assert reason is None and unmapped == ZERO_D()
    assert list(shares) == [biz["the_forum"]]
    assert shares[biz["the_forum"]] == D(1)


# ── meta_spend, against the database ──────────────────────────────────────────────────────

async def test_meta_spend_reads_the_month_at_campaign_level_only():
    """Account-level rows carry the same spend again and ad-level rows a third time. Mixing
    grains would multiply the denominator by how much of the account somebody synced."""
    tenant_id, biz, _a, _s = await _ctx()
    acct = await _ad_account(tenant_id, biz["springb"])
    await _spend(tenant_id, acct)
    await _spend(tenant_id, acct, rows=[("Forum - VIP Day", "99999.00")], level="account")
    async with SessionLocal() as s:
        got = await E.meta_spend(s, tenant_id, MONTH, {})
    assert sum(got.values()) == D("10000.00"), "the account-level duplicate stayed out"
    assert got["Forum - VIP Day"] == D("6000.00")


async def test_meta_spend_is_bounded_by_the_month_and_the_named_accounts():
    tenant_id, biz, _a, _s = await _ctx()
    mine = await _ad_account(tenant_id, biz["springb"], "act_mine")
    other = await _ad_account(tenant_id, biz["the_forum"], "act_other")
    await _spend(tenant_id, mine)
    await _spend(tenant_id, other, rows=[("Forum - VIP Day", "50000.00")])
    await _spend(tenant_id, mine, rows=[("Forum - VIP Day", "777.00")],
                 on=dt.date(2026, 6, 30))
    async with SessionLocal() as s:
        scoped = await E.meta_spend(s, tenant_id, MONTH, {"ad_account_ids": [str(mine)]})
        everything = await E.meta_spend(s, tenant_id, MONTH, {})
    assert sum(scoped.values()) == D("10000.00"), "June is out and the other account is out"
    assert sum(everything.values()) == D("60000.00"), "no account filter means every account"


async def test_two_traced_rules_on_different_accounts_keep_their_own_denominators():
    """The bug this nearly shipped with: one merged spend read would make each rule's ratio a
    fraction of the UNION's total, so connecting a second ad account would silently shrink every
    existing traced rule's shares."""
    tenant_id, biz, _a, _s = await _ctx()
    one = await _ad_account(tenant_id, biz["springb"], "act_one")
    two = await _ad_account(tenant_id, biz["the_forum"], "act_two")
    await _spend(tenant_id, one, rows=[("Forum - VIP Day", "1000.00")])
    await _spend(tenant_id, two, rows=[("Forum - Other", "9000.00")])
    async with SessionLocal() as s:
        a = await E.meta_spend(s, tenant_id, MONTH, {"ad_account_ids": [str(one)]})
        b = await E.meta_spend(s, tenant_id, MONTH, {"ad_account_ids": [str(two)]})
    sa, _u, _r = E.traced_shares(a, _spec(biz, [one]))
    sb, _u2, _r2 = E.traced_shares(b, _spec(biz, [two]))
    assert sa[biz["the_forum"]] == D(1), "the whole of its own account's spend"
    assert sb[biz["the_forum"]] == D(1), "and so is the other, not a tenth of the union"


# ── the whole way through derive_applied ──────────────────────────────────────────────────

async def _traced_rule(tenant_id, biz, actor, account_ids=None):
    async with SessionLocal() as s:
        return await AR.create_applied_rule(
            s, tenant_id, actor, scope_kind="account", pattern="Advertising",
            pool_name="Advertising", source_business_id=biz["springb"], method="traced",
            effective_from=MONTH[0], reach="forward",
            rationale="Meta decides the split; the card decides the amount.",
            source_spec=_spec(biz, account_ids))


async def test_a_traced_rule_writes_the_mapped_shares_and_reports_the_unmapped_dollars():
    tenant_id, biz, actor, std = await _ctx()
    acct = await _ad_account(tenant_id, biz["springb"])
    await _spend(tenant_id, acct)
    rule, v = await _traced_rule(tenant_id, biz, actor, [acct])
    async with SessionLocal() as s:
        spend = await E.meta_spend(s, tenant_id, MONTH, v.source_spec)
        out = await E.derive_applied(
            s, tenant_id, MONTH, rules_and_versions=[(rule, v)],
            pools_by_rule={rule.id: {std["6040"]: D("12345.67")}}, today=TODAY,
            spend_by_rule={rule.id: spend})
        await s.commit()
    assert out["contributions"] == 2, "the two mapped entities, not the third"
    gaps = [p for p in out["pending"] if p["reason"] == "source_unmapped_spend"]
    assert len(gaps) == 1
    assert gaps[0]["meta_spend"] == D("1000.00"), "what Meta spent unmatched"
    assert gaps[0]["amount"] == D("1234.57"), "and what it cost in pool money"
    async with SessionLocal() as s:
        rows = (await s.execute(select(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id))).scalars().all()
    assert sum(r.amount for r in rows) == D("11111.10")
    assert sum(r.amount for r in rows) + gaps[0]["amount"] == D("12345.67")
    assert {r.target_business_id for r in rows} == {biz["the_forum"], biz["becollective"]}


async def test_a_traced_rule_with_no_insight_rows_reports_source_not_connected():
    tenant_id, biz, actor, std = await _ctx()
    rule, v = await _traced_rule(tenant_id, biz, actor)
    async with SessionLocal() as s:
        out = await E.derive_applied(
            s, tenant_id, MONTH, rules_and_versions=[(rule, v)],
            pools_by_rule={rule.id: {std["6040"]: D("5000.00")}}, today=TODAY,
            spend_by_rule={rule.id: {}})
        await s.commit()
    assert out["contributions"] == 0
    assert [p["reason"] for p in out["pending"]] == ["source_not_connected"]
    async with SessionLocal() as s:
        assert (await s.execute(select(AllocationContribution).where(
            AllocationContribution.tenant_id == tenant_id))).scalars().all() == []


# ── gaps the mutation harness found ───────────────────────────────────────────────────────

def test_a_ratio_that_does_not_land_on_four_places_still_adds_back():
    """B7's 6,000/3,000/1,000 gives 0.6 and 0.3, which quantize to four places EXACTLY, so a
    test built on it cannot tell a quantized share from an exact one. Equal spend gives thirds,
    which lose a cent at four places and keep it at full precision. Same lesson as the count
    driver, found again by mutating the quantize back in."""
    biz = {"the_forum": uuid.uuid4(), "becollective": uuid.uuid4()}
    spend = {"Forum - A": D("1000"), "KB - BeCollective - B": D("1000"), "Unmapped": D("1000")}
    shares, unmapped, reason = E.traced_shares(spend, _spec(biz))
    assert reason is None and unmapped == D("1000")
    assert any(v != v.quantize(D("0.0001")) for v in shares.values()), \
        "the fixture has to carry a ratio that four places would round"
    pool = D("900.00")
    parts = E.split_amounts(pool, shares)
    left = E.retained(pool, parts)
    assert sum(parts.values()) + left == pool, "every cent of the pool is placed"
    assert sum(parts.values()) == D("600.00"), "two thirds of it went out"
    assert left == D("300.00")


async def test_spend_after_the_month_ends_stays_out():
    """The earlier bound test put its out-of-range row in JUNE, before the month, so widening
    the UPPER bound admitted nothing and the mutation survived. A row in August is the one that
    tells."""
    tenant_id, biz, _a, _s = await _ctx()
    acct = await _ad_account(tenant_id, biz["springb"])
    await _spend(tenant_id, acct, rows=[("Forum - VIP Day", "1000.00")])
    await _spend(tenant_id, acct, rows=[("Forum - VIP Day", "50000.00")],
                 on=dt.date(2026, 8, 1))
    await _spend(tenant_id, acct, rows=[("Forum - VIP Day", "40000.00")],
                 on=dt.date(2026, 6, 30))
    async with SessionLocal() as s:
        got = await E.meta_spend(s, tenant_id, MONTH, {})
    assert sum(got.values()) == D("1000.00"), "neither August nor June belongs to July"


def test_a_prefix_match_is_anchored_at_the_start():
    """`match_name` treating prefix as contains survived every earlier assertion, because each
    one happened to put the value AT the start. A value in the middle is the discriminating
    case."""
    assert match_name("The Forum VIP Day", "prefix", "forum") is False
    assert match_name("Forum VIP Day", "prefix", "forum") is True
    assert match_name("The Forum VIP Day", "contains", "forum") is True
    assert match_name("Retarget - KB - The Shift", "prefix", "kb-") is False
    assert match_name("KB - The Shift", "prefix", "kb-") is True
