"""Go High Level tag-driven membership classification (config-driven)."""
from app.integrations import ghl


def test_classify_member_segments():
    member = {"inner circle active", "the forum active", "member: secondary"}
    forum = {"the forum active", "forumadmin"}
    becoll = {"inner circle active"}

    is_m, seg = ghl.classify_member(["the forum active", "vip"], member, forum, becoll)
    assert is_m and seg == "forum"

    is_m, seg = ghl.classify_member(["inner circle active"], member, forum, becoll)
    assert is_m and seg == "becollective"

    is_m, seg = ghl.classify_member(["member: secondary"], member, forum, becoll)
    assert is_m and seg is None            # member but not segmented

    is_m, seg = ghl.classify_member(["lead", "cold"], member, forum, becoll)
    assert not is_m and seg is None


def test_contact_helpers():
    c = {"id": "abc", "firstName": "Jane", "lastName": "Doe",
         "email": "j@x.com", "tags": ["The Forum Active", "VIP"]}
    assert ghl.contact_tags(c) == ["the forum active", "vip"]   # lowercased
    assert ghl.contact_name(c) == "Jane Doe"
    assert "loc123" in ghl.contact_url("loc123", "abc") and "abc" in ghl.contact_url("loc123", "abc")

    # name falls back through contactName / email / id
    assert ghl.contact_name({"id": "z", "email": "e@x.com"}) == "e@x.com"


def test_subscription_helpers():
    # dollars, monthly → as-is
    assert ghl.sub_monthly_amount({"amount": 49, "interval": "month"}) == 49.0
    # cents heuristic (>= 1000) → divided by 100
    assert ghl.sub_monthly_amount({"amount": 4900, "interval": "month"}) == 49.0
    # yearly → normalised to monthly (1200/yr = 100/mo)
    assert ghl.sub_monthly_amount({"amount": 120000, "interval": "year"}) == 100.0
    # missing/garbage amount → 0, never raises
    assert ghl.sub_monthly_amount({"status": "active"}) == 0.0

    assert ghl.sub_is_active({"status": "active"}) is True
    assert ghl.sub_is_active({"status": "trialing"}) is True
    assert ghl.sub_is_active({"status": "cancelled"}) is False
    assert ghl.sub_is_active({}) is False
