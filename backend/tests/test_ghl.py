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
