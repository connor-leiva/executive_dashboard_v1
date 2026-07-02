"""Sisu mapping tests — built from the real get-team-clients record shapes
(type_id b/s, status_code CLOSD/LOSTT, RFC-2822 dates, archive_ts=lost,
trans_amt=price, gross_commission_amt=GCI, embedded agent)."""
import datetime as dt

from app.integrations import sisu


def _closed_buyer():
    return {
        "client_id": 440612, "transaction_id": None, "contact_id": 522,
        "type_id": "b", "status": "N", "status_code": "CLOSD",
        "gross_commission_amt": 10230.0, "commission_amt": 4737.0, "trans_amt": 341000.0,
        "closed_volume_amt": None, "listing_amt": 0.0,
        "lead_dt": "Mon, 09 Dec 2019 00:00:00 GMT",
        "appt_set_dt": "Mon, 09 Dec 2019 00:00:00 GMT",
        "uc_dt": "Tue, 24 Mar 2020 00:00:00 GMT",
        "closed_dt": "Wed, 29 Apr 2020 00:00:00 GMT",
        "archive_ts": None, "lost_reason_id": None,
        "address_1": "123 Main St", "buyer_names": None, "seller_names": "Doe Family",
        "email": "x@example.com", "agent_id": 3589,
        "agent": {"agent_id": 3589, "first_name": "Jane", "last_name": "Smith",
                  "email": None, "status": "D"},
    }


def test_parse_rfc2822_and_iso():
    assert sisu.parse_dt("Wed, 29 Apr 2020 00:00:00 GMT") == dt.date(2020, 4, 29)
    assert sisu.parse_dt("2026-07-01") == dt.date(2026, 7, 1)
    assert sisu.parse_dt(None) is None
    assert sisu.parse_dt("") is None


def test_classify_closed():
    assert sisu.classify_status(_closed_buyer()) == "closed"


def test_classify_dead_via_archive_ts():
    # Lost record: archive_ts set, lost_reason_id null (the real signal is archive_ts).
    c = {"type_id": "s", "status_code": "LOSTT", "archive_ts": "Fri, 06 Dec 2019 14:33:00 GMT",
         "lost_reason_id": None, "uc_dt": None, "closed_dt": None}
    assert sisu.classify_status(c) == "dead"


def test_classify_pending_and_active():
    assert sisu.classify_status({"uc_dt": "Tue, 24 Mar 2020 00:00:00 GMT"}) == "pending"
    assert sisu.classify_status({"listing_dt": "Tue, 24 Mar 2020 00:00:00 GMT"}) == "active"
    assert sisu.classify_status({}) == "active"


def test_future_closed_is_not_closed():
    c = {"closed_dt": "Thu, 29 Apr 2999 00:00:00 GMT", "uc_dt": "Tue, 24 Mar 2999 00:00:00 GMT"}
    assert sisu.classify_status(c) == "pending"


def test_map_client_fields():
    t = sisu.map_client(_closed_buyer())
    assert t["external_id"] == "440612"
    assert t["side"] == "buy"
    assert t["status"] == "closed"
    assert t["gci"] == 10230.0
    assert t["sale_price"] == 341000.0        # trans_amt (closed_volume_amt was null)
    assert t["close_date"] == dt.date(2020, 4, 29)
    assert t["contract_date"] == dt.date(2020, 3, 24)
    assert t["appt_set_date"] == dt.date(2019, 12, 9)
    assert t["agent_external_id"] == "3589"
    assert t["sisu_status_code"] == "CLOSD"


def test_map_client_expected_close_and_commission_placeholder():
    t = sisu.map_client(_closed_buyer())
    # Sisu keeps the (projected) close in closed_dt → expected_close_date mirrors it.
    assert t["expected_close_date"] == dt.date(2020, 4, 29)
    # agent_commission is filled later by enrich_commissions(), not at map time.
    assert t["agent_commission"] is None


def test_team_income_from_commission_info():
    # Real shape (pending 6614734): 70/30 split — team keeps 6,787.50.
    ci = {"team_income": 6787.5, "summaries": {"final": {
        "agent": {"external_type": 2, "name": "Kaestle Muir", "value": 15837.5},
        "team": {"external_type": 1, "name": "Utah Life Real Estate Group", "value": 6787.5},
    }}}
    assert sisu.team_income(ci) == 6787.5                       # net GCI = the team's take
    # agent_commission (cost of sale) = GCI − team_income
    assert round(22625.0 - sisu.team_income(ci), 2) == 15837.5


def test_team_income_sums_final_when_no_top_level():
    # No top-level team_income → sum external_type==1 in summaries.final.
    ci = {"summaries": {"final": {
        "agent": {"external_type": 2, "value": 10944.0},
        "exp": {"external_type": 2, "value": 2736.0},
        "team": {"external_type": 1, "value": 3420.0},
    }}}
    assert sisu.team_income(ci) == 3420.0
    assert sisu.team_income({}) is None                         # absent → None (caller falls back)


def test_map_client_seller_side():
    c = _closed_buyer() | {"type_id": "s"}
    assert sisu.map_client(c)["side"] == "sell"


def test_map_client_listing_date():
    c = _closed_buyer() | {"type_id": "s", "listing_dt": "Tue, 24 Mar 2020 00:00:00 GMT"}
    t = sisu.map_client(c)
    assert t["side"] == "sell"
    assert t["listing_date"] == dt.date(2020, 3, 24)
    # buyer record has no listing date
    assert sisu.map_client(_closed_buyer())["listing_date"] is None


def test_map_agent_active_flag():
    a = sisu.map_agent(_closed_buyer())
    assert a["external_id"] == "3589"
    assert a["name"] == "Jane Smith"
    assert a["is_active"] is False            # agent.status == "D"
    b = sisu.map_agent({"agent": {"agent_id": 7, "first_name": "A", "last_name": "B",
                                  "status": "N"}})
    assert b["is_active"] is True
