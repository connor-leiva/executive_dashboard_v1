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
    # The Forum's GHL returns whole dollars — monthly amounts pass through as-is.
    assert ghl.sub_monthly_amount({"amount": 2500, "interval": "month"}) == 2500.0
    assert ghl.sub_monthly_amount({"amount": 1850}) == 1850.0
    # yearly → normalised to monthly (30k/yr = 2500/mo)
    assert ghl.sub_monthly_amount({"amount": 30000, "interval": "year"}) == 2500.0
    # missing/garbage amount → 0, never raises
    assert ghl.sub_monthly_amount({"status": "active"}) == 0.0

    assert ghl.sub_is_active({"status": "active"}) is True
    assert ghl.sub_is_active({"status": "trialing"}) is True
    assert ghl.sub_is_active({"status": "cancelled"}) is False
    assert ghl.sub_is_active({}) is False


def test_member_segment():
    forum = {"the forum active", "member: secondary", "forumadmin"}
    ic = {"inner circle active", "inner circle active add on"}
    assert ghl.member_segment({"the forum active"}, forum, ic) == "forum"
    assert ghl.member_segment({"inner circle active"}, forum, ic) == "inner_circle"
    # Forum takes precedence when a contact carries both
    assert ghl.member_segment({"the forum active", "inner circle active"}, forum, ic) == "forum"
    assert ghl.member_segment({"random"}, forum, ic) == "member"
