"""Drill-down permissions route by what a business IS, not by what it is called.

THE BUG THIS CLOSES. tab_for_metric returned the literal tabs "ulrg" and "sympli" for two whole
metric families. Those are one customer's names for her own companies. A normally-provisioned
tenant's businesses are keyed differently, so the drill asked for a tab that tenant's nav does
not contain, and deps.py correctly refused it — 6 of the 7 tiles on the portfolio strip, every
operational tile on a business page, and every P&L row came back "no access". For the OWNER
too, since owners only get the tabs their tenant actually has.

It presented as a permissions bug, which is the worst possible disguise: nothing is broken, the
numbers are right, and the product simply says no.

THE RISK IN FIXING IT is the mirror image — changing where Spring's drills route would take
away access she has today. So the first test here pins the new path to the old literals.
"""
import pytest

from app.services import roles, tabs


class _B:
    """A Business as tabs.py reads one — no database needed to test a pure mapping."""
    def __init__(self, key, kind, sort_order=0, config=None, display_tab=None, name=None):
        self.key, self.kind, self.sort_order = key, kind, sort_order
        self.config, self.display_tab = config, display_tab
        self.name, self.accent = name or key, "#000000"


SPRING = [
    _B("ulrg", roles.REAL_ESTATE, 0, name="ULRG + Team"),
    _B("springb", roles.MEMBERSHIP, 1, name="Spring B"),
    _B("sympli", roles.COMMISSION_JV, 2, name="Sympli Mortgage"),
]


def test_springs_routing_is_unchanged_to_the_letter():
    """The three literals this replaces were {ulrg, sympli, forum}. If this test ever fails,
    somebody has quietly moved a customer's drill-downs to a different permission."""
    assert tabs.kind_tabs(SPRING) == {
        roles.REAL_ESTATE: "ulrg",
        roles.MEMBERSHIP: "forum",        # her membership entity runs three programs; first wins
        roles.COMMISSION_JV: "sympli",
    }


def test_every_metric_routes_where_it_used_to_for_spring():
    """Not just the map — the function. Walk every key in both migrated families and assert the
    kind-aware path agrees with the hardcoded one it replaced."""
    kt = tabs.kind_tabs(SPRING)
    for key in tabs._ULRG:
        assert tabs.tab_for_metric(key, kind_tab=kt) == "ulrg", key
    for key in tabs._SYMPLI:
        assert tabs.tab_for_metric(key, kind_tab=kt) == "sympli", key
    for key in tabs._FORUM:
        assert tabs.tab_for_metric(key, kind_tab=kt) == "forum", key
    assert tabs.tab_for_metric("forum_anything_at_all", kind_tab=kt) == "forum"


def test_the_sibling_programme_keys_are_not_swallowed_by_the_membership_family():
    """Spring's membership entity runs three programmes off one GHL location, so its kind maps
    to "forum". bc_ and edge_ keys must NOT inherit that — they belong to their own tabs, and a
    member granted only beCollective must not have their drill resolve to The Forum.

    This is the specific way the membership migration could go wrong while every other test
    stayed green, so it is asserted WITH a kind map rather than without one."""
    kt = tabs.kind_tabs(SPRING)
    assert tabs.tab_for_metric("bc_members", kind_tab=kt) == "becollective"
    assert tabs.tab_for_metric("bc_anything", kind_tab=kt) == "becollective"
    assert tabs.tab_for_metric("edge_members", kind_tab=kt) == "edge"
    assert tabs.tab_for_metric("edge_anything", kind_tab=kt) == "edge"


def test_membership_metrics_reach_a_tab_a_normal_tenant_actually_has():
    """metrics.py emits the whole _FORUM family for ANY business of kind membership - it gates
    on kind, not on a name. So a tenant whose membership business is "club" gets six tiles whose
    drills used to demand a tab called "forum" that their nav has never contained."""
    club = [_B("club", roles.MEMBERSHIP, 0, name="The Club")]
    kt = tabs.kind_tabs(club)
    assert kt == {roles.MEMBERSHIP: "club"}
    for key in tabs._FORUM:
        assert tabs.tab_for_metric(key, kind_tab=kt) == "club", key
    assert "club" in tabs._business_tabs(club[0])


def test_a_normally_provisioned_tenant_can_reach_its_own_drills():
    """The actual defect. provisioning.DEFAULT_BUSINESSES gives one business keyed `main` with
    kind real_estate, so every brokerage metric must route to `main` — a tab that tenant has —
    rather than to `ulrg`, which it does not."""
    acme = [_B("main", roles.REAL_ESTATE, 0, name="Main")]
    kt = tabs.kind_tabs(acme)
    assert kt == {roles.REAL_ESTATE: "main"}
    for key in tabs._ULRG:
        assert tabs.tab_for_metric(key, kind_tab=kt) == "main", key
    # ...and the tab it names is one the tenant's nav actually contains.
    assert "main" in tabs._business_tabs(acme[0])


def test_a_tenant_with_no_business_of_that_kind_does_not_get_someone_elses_tab():
    """A membership-only customer has no brokerage. Brokerage metrics should not appear at all,
    but if one is ever asked for, the answer must not be another tenant's vocabulary."""
    memb = [_B("club", roles.MEMBERSHIP, 0, name="The Club")]
    kt = tabs.kind_tabs(memb)
    assert roles.REAL_ESTATE not in kt
    # Falls back to the old literal rather than crashing. Documented, not ideal: a tenant that
    # genuinely has no brokerage has no brokerage tiles to drill from either.
    assert tabs.tab_for_metric("units_closed", kind_tab=kt) == "ulrg"


def test_omitting_the_map_preserves_the_old_behaviour_exactly():
    """The argument is optional so call sites can migrate one at a time. Until one does, it must
    behave precisely as before — a half-migrated file should be boring, not subtly wrong."""
    for key in tabs._ULRG:
        assert tabs.tab_for_metric(key) == "ulrg", key
    for key in tabs._SYMPLI:
        assert tabs.tab_for_metric(key) == "sympli", key
    for key in tabs._FORUM:
        assert tabs.tab_for_metric(key) == "forum", key


def test_the_lowest_sort_order_wins_when_a_tenant_has_two_of_a_kind():
    """Nothing stops a tenant running two brokerages. Whatever the answer is it must be stable,
    and it must be the same business the rail lists first."""
    two = [_B("second", roles.REAL_ESTATE, 5, name="Second"),
           _B("first", roles.REAL_ESTATE, 1, name="First")]
    assert tabs.kind_tabs(two) == {roles.REAL_ESTATE: "first"}


def test_financial_drills_already_fall_back_to_the_business_key():
    """This branch was already correct and is left alone — worth pinning so a later tidy-up of
    _BIZ_TAB does not break it. A business not in Spring's three routes to its own key."""
    assert tabs.tab_for_metric("revenue", business="main") == "main"
    assert tabs.tab_for_metric("revenue", business="ulrg") == "ulrg"
    assert tabs.tab_for_metric("revenue", business="springb") == "forum"


@pytest.mark.parametrize("key,expected", [
    ("flywheel_referrals", "flywheel"),
    ("books_queue", "books"),
    ("binder_matrix", "binder"),
    ("forum_roster", "forum"),
    ("bc_members", "becollective"),
    ("edge_members", "edge"),
    ("combined_profit", "portfolio"),
    ("something_unknown", "portfolio"),
])
def test_the_untouched_branches_still_answer_the_same(key, expected):
    assert tabs.tab_for_metric(key) == expected
