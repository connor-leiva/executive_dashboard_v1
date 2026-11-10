"""Phase A: UTM actually stored, Forum-only CPL, brand groups, fatigue."""
from app.services.ads_funnel import registration_utm_coverage
from app.services.ads_rules import classify_campaign, fatigue_signal, is_forum_group
from app.services.sync import _utm_meta, forum_opp_signals


def test_utm_content_and_term_are_kept():
    meta = _utm_meta(
        {"c": "120210", "t": "dallas"},
        {"content": "c", "term": "t", "campaign": "missing"})
    assert meta["utm_content"] == "120210"
    assert meta["utm_term"] == "dallas"
    assert meta["utm_campaign"] is None


def test_forum_stage_meta_carries_the_contact_utm():
    sig = forum_opp_signals({
        "source": "VIP Page",
        "meta": {"utm_content": "120210", "utm_term": "dallas", "utm_campaign": "Forum Dallas"},
    })
    assert sig["utm_content"] == "120210"
    assert sig["utm_term"] == "dallas"
    assert sig["page_url"] is None


def test_registration_coverage_counts_content_that_matches_an_ad():
    metas = [
        {"utm_content": "120210", "utm_campaign": "Forum Dallas", "utm_term": "dallas"},
        {"utm_campaign": "Forum Dallas"},
        {},
    ]
    cov = registration_utm_coverage(metas, {"120210"})
    assert cov["registrations"] == 3
    assert cov["with_utm_content"] == 1
    assert cov["with_utm_term"] == 1
    assert cov["content_matches_ad"] == 1
    assert cov["content_unmatched"] == 0


def test_brand_groups_and_forum_spend():
    assert is_forum_group(classify_campaign("Forum Dallas - VIP"))
    assert is_forum_group(classify_campaign("Scottsdale guest application"))
    assert not is_forum_group(classify_campaign("KB - The Shift - August2026"))
    assert not is_forum_group(classify_campaign("The Edge Webinar"))


def test_fatigue_needs_frequency_up_and_ctr_down():
    assert fatigue_signal(0.8, 1.2, 3.1, 2.4) is True
    assert fatigue_signal(1.4, 1.2, 3.1, 2.4) is False
    assert fatigue_signal(0.8, 1.2, 2.0, 2.4) is False
    assert fatigue_signal(None, 1.2, 3.1, 2.4) is False
