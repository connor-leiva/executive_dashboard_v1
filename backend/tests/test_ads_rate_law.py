"""The ratio law and the grouping rules. SPEC-ads-module.md Parts 10.1 and 10.3.

The spec is explicit that this exists and passes before anything reads from it, and the reason is
that the failure mode is invisible. A wrongly-averaged CTR is not an exception or a blank cell -
it is a plausible number, sitting beside correct numbers, wrong by an amount nobody can predict
from looking at it. Nobody audits a dashboard that looks fine.

No database, no fixtures, no Meta token: every rule here is a pure function, which is most of why
it can be pinned this precisely.
"""
import datetime as dt
from decimal import Decimal

import pytest

from app.services import ads, ads_rules


# ── the law itself ────────────────────────────────────────────────────────────────────
def test_ratios_are_computed_from_sums_not_averaged():
    """THE MOST IMPORTANT TEST IN THE MODULE.

    Day 1: 100 impressions, 10 link clicks -> 10% on the day.
    Day 2: 900 impressions,  0 link clicks ->  0% on the day.

    The period CTR is 10/1000 = 1.0%. The mean of the two daily rates is 5.0%. Averaging is off
    by a factor of five here, and the direction and size of the error depend entirely on how the
    volume happened to be distributed - which is what makes it impossible to eyeball.
    """
    days = [(100, 10), (900, 0)]
    period = ads.ctr(sum(c for _, c in days), sum(i for i, _ in days))
    assert period == pytest.approx(1.0)

    naive_mean = sum(ads.ctr(c, i) for i, c in days) / len(days)
    assert naive_mean == pytest.approx(5.0)
    assert period != pytest.approx(naive_mean), "the whole rule exists because these differ"


def test_a_zero_denominator_is_none_and_never_zero():
    """"No closes yet" and "a CAC of zero" are different facts. Returning 0 would make an account
    that has enrolled nobody display the best CAC on the page, in green."""
    assert ads.rate(5, 0) is None
    assert ads.rate(5, None) is None
    assert ads.cac(10_000, 0) is None
    assert ads.roas(0, 0) is None
    assert ads.rate(0, 5) == 0.0, "a real zero over a real denominator is still zero"


def test_none_bands_as_none_rather_than_bad():
    """The banding half of the same fact. A missing number rendered in the same red as a
    catastrophic one teaches people to stop reading the colours."""
    assert ads_rules.band("cac", None) == "none"
    assert ads_rules.band("roas", None) == "none"
    assert ads_rules.band("cac", 900.0) == "good"
    assert ads_rules.band("cac", 5000.0) == "bad"


def test_direction_is_honoured_so_lower_is_better_where_it_should_be():
    """CTR and ROAS are gte; CPM, CPL and CAC are lte. Getting one backwards paints a disaster
    green, which is worse than showing nothing."""
    assert ads_rules.band("ctr", 2.0) == "good"        # higher is better
    assert ads_rules.band("ctr", 0.1) == "bad"
    assert ads_rules.band("cpm", 5.0) == "good"        # lower is better
    assert ads_rules.band("cpm", 40.0) == "bad"


def test_a_tenant_threshold_overrides_the_default_without_touching_it():
    """Thresholds are per account. One workspace deciding a $3000 CAC is fine must not move
    anybody else's bands."""
    strict = {"cac": {"good": 100.0, "warn": 200.0, "direction": "lte"}}
    assert ads_rules.band("cac", 900.0, strict) == "bad"
    assert ads_rules.band("cac", 900.0) == "good"      # the default is unchanged
    assert ads_rules.DEFAULT_THRESHOLDS["cac"]["good"] == 1200.0


def test_the_rates_accept_decimals_because_money_arrives_as_one():
    """spend is Numeric(14,2), so it arrives as Decimal. Mixing Decimal and float raises in
    Python rather than coercing, and a TypeError inside a payload builder is a 500."""
    assert ads.cpm(Decimal("100.00"), 10_000) == pytest.approx(10.0)
    assert ads.cac(Decimal("48000.00"), 4) == pytest.approx(12000.0)
    assert ads.roas(Decimal("144000"), Decimal("48000")) == pytest.approx(3.0)


# ── leads resolve from stored actions ─────────────────────────────────────────────────
ACTIONS = [
    {"action_type": "lead", "value": "12"},
    {"action_type": "offsite_conversion.fb_pixel_lead", "value": "31"},
    {"action_type": "onsite_conversion.lead_grouped", "value": "7"},
    {"action_type": "link_click", "value": "980"},
]


def test_leads_resolve_from_the_configured_action_types():
    """Meta emits several lead-shaped types and which ones count is a per-account decision. The
    same stored row yields different, equally correct numbers under different configurations."""
    assert ads.resolve_leads(ACTIONS, ["lead"]) == 12
    assert ads.resolve_leads(ACTIONS, ["lead", "offsite_conversion.fb_pixel_lead"]) == 43
    assert ads.resolve_leads(ACTIONS, ["onsite_conversion.lead_grouped"]) == 7
    assert ads.resolve_leads(ACTIONS, []) == 0
    assert ads.resolve_leads(None, ["lead"]) == 0


def test_changing_the_lead_config_re_answers_history_without_a_resync():
    """The reason `actions` is stored WHOLE. Getting the lead definition wrong is normal and
    should cost a settings change, not a 13-month re-pull from Meta."""
    before = ads.resolve_leads(ACTIONS, ["lead"])
    after = ads.resolve_leads(ACTIONS, ["lead", "offsite_conversion.fb_pixel_lead"])
    assert before == 12 and after == 43       # same stored row, no network call


def test_a_malformed_actions_blob_counts_zero_rather_than_raising():
    """Meta has shipped more than one envelope shape. A sync that 500s on an unexpected one is
    worse than a count that reads zero and can be investigated."""
    assert ads.resolve_leads([{"nope": 1}], ["lead"]) == 0
    assert ads.resolve_leads([{"action_type": "lead", "value": "abc"}], ["lead"]) == 0
    assert ads.resolve_leads({"data": ACTIONS}, ["lead"]) == 12   # the wrapped shape


# ── grouping ──────────────────────────────────────────────────────────────────────────
def test_default_group_rules_reproduce_the_mockup():
    """Lifted from the approved mockup, with ONE deliberate divergence recorded below.

    A `split` prefix rule used to take the first WORD after the prefix; it now takes the first
    SEGMENT, up to the next dash. The mockup's names made those identical because they were
    dash-delimited throughout (`KB-Webinar-Retarget`). The live account's are not: it names
    campaigns `KB - The Shift - August2026`, where the dash is the structural delimiter and
    spaces live INSIDE a segment. Word-wise splitting labels that group "KB · The".

    The trade is real and worth stating. Segment-wise gives `KB · The Shift` and `KB · Utah Life`
    on the live account, which is right; on a space-delimited name like `kb-shift lookalike` it
    gives `KB · shift lookalike` where word-wise would have merged that with `kb-shift broad`.
    Real naming here is dash-delimited, and any account that disagrees can now say so in the
    grouping editor rather than filing a bug.
    """
    assert ads_rules.classify_campaign("KB-Webinar-Retarget") == "KB · Webinar"
    assert ads_rules.classify_campaign("kb-shift lookalike") == "KB · shift lookalike"
    assert ads_rules.classify_campaign("2026 Event Name Research v2") == "Event Name Research"
    assert ads_rules.classify_campaign("Spring Webinar Broad") == "Webinar"
    assert ads_rules.classify_campaign("Retargeting - warm") == ads_rules.FALLBACK_GROUP


def test_an_unmatched_campaign_is_counted_not_hidden():
    """A rising Other count is the leading indicator that the naming convention drifted away
    from the rules - the moment before every group total quietly stops meaning what it did."""
    rows = [{"name": "KB-Webinar-A"}, {"name": "mystery one"}, {"name": "mystery two"}]
    groups, unmatched = ads_rules.group_campaigns(rows)
    assert unmatched == 2
    assert len(groups[ads_rules.FALLBACK_GROUP]) == 2


def test_a_tenant_can_supply_its_own_grouping_without_affecting_anyone():
    """Grouping is DATA, per account. A customer who names campaigns nothing like Spring's gets
    their own rules, and the shipped default is untouched by that."""
    mine = [{"match": "prefix", "value": "acme_", "label": "Acme", "split": False}]
    assert ads_rules.classify_campaign("ACME_spring_promo", mine) == "Acme"
    assert ads_rules.classify_campaign("KB-Webinar-A", mine) == ads_rules.FALLBACK_GROUP
    assert ads_rules.classify_campaign("KB-Webinar-A") == "KB · Webinar"


def test_classification_never_raises_on_junk():
    for bad in ("", None, "   "):
        assert ads_rules.classify_campaign(bad) == ads_rules.FALLBACK_GROUP
    assert ads_rules.classify_campaign("kb-", ) == "KB"        # prefix with no tail to split


# ── period resolution ─────────────────────────────────────────────────────────────────
def test_trailing_windows_are_inclusive_of_today():
    """A "last 7 days" that silently means six is the kind of off-by-one that only shows up when
    somebody reconciles against Ads Manager and finds a day of spend missing."""
    s, e, label = ads.ads_period("7d", None, None, "America/Denver")
    assert (e - s).days == 6 and label == "Last 7 days"
    assert e == ads.account_today("America/Denver")


def test_explicit_dates_beat_every_preset():
    s, e, label = ads.ads_period("30d", dt.date(2026, 1, 1), dt.date(2026, 1, 31), None)
    assert (s, e) == (dt.date(2026, 1, 1), dt.date(2026, 1, 31))
    assert "2026-01-01" in label


def test_an_unknown_timezone_falls_back_rather_than_raising():
    """A bad timezone_name arrives from Meta's account metadata, not from us. Rendering the
    dashboard a day out is recoverable; a 500 on every page load is not."""
    assert ads.account_today("Not/AZone") == dt.date.today()
    assert ads.account_today(None) == dt.date.today()
    assert ads.account_today("") == dt.date.today()
