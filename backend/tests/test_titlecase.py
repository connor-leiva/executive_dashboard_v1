"""Scorecard labels use one title-case rule, and the catalog already follows it."""
from app.services.springb_enterprise import GROUPS
from app.services.titlecase import title_case


def test_title_case_matches_the_board_examples():
    assert title_case("Qualified guest leads") == "Qualified Guest Leads"
    assert title_case("Cost per qualified lead") == "Cost Per Qualified Lead"
    assert title_case("VIP guests added") == "VIP Guests Added"
    assert title_case("Forum members at red") == "Forum Members at Red"
    assert title_case("60-day renewals confirmed") == "60-Day Renewals Confirmed"
    assert title_case("Cash collected vs. due") == "Cash Collected vs. Due"
    assert title_case("Profit by product") == "Profit by Product"
    assert title_case("beCollective active members") == "beCollective Active Members"
    assert title_case("Upgrade RevShare") == "Upgrade RevShare"
    assert title_case("Excluded from the total") == "Excluded From the Total"
    assert title_case("2026Q4 to date") == "2026Q4 to Date"
    assert title_case("$250K for 2026") == "$250K for 2026"
    assert title_case("130 (at 87)") == "130 (at 87)"
    assert title_case(title_case("Net cash")) == "Net Cash"


def test_catalog_labels_are_title_case_and_notes_stay_sentences():
    for group in GROUPS:
        assert group["name"] == title_case(group["name"])
        if group.get("dept_name"):
            assert group["dept_name"] == title_case(group["dept_name"])
        if group.get("season"):
            assert group["season"] == title_case(group["season"])
        for row in group["metrics"]:
            assert row["name"] == title_case(row["name"]), row["slug"]
            if row.get("cadence_label"):
                assert row["cadence_label"] == title_case(row["cadence_label"])
            if row.get("target_by"):
                assert row["target_by"] == title_case(row["target_by"])
    leads = next(m for g in GROUPS if g["key"] == "sb_l10_growth" for m in g["metrics"] if m["slug"] == "leads")
    assert leads["note"].startswith("VIP Guest entries whose")
    assert "Qualified Guest Leads" not in leads["note"]
