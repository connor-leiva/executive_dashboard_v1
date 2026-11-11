"""Team and agent pipeline (spec phase 4): the ported rules, on plain person dicts -- no database."""
import datetime as dt

from app.services import lead_source_pipeline as lp

NOW = dt.datetime(2026, 11, 10, 18, 0, tzinfo=dt.timezone.utc)


def person(i, *, agent="a1", stage="Lead", touched=True, sisu=None, appts=(), contacted=False, days_old=30):
    created = NOW - dt.timedelta(days=days_old)
    first = created + dt.timedelta(minutes=4) if touched else None
    return {"person_id": str(i), "agent_id": agent, "assigned_to": f"Agent {agent}", "stage": stage,
            "created": created, "created_date": created.date(), "first_outbound_at": first,
            "last_activity_at": first, "touches": [], "appointments": list(appts), "deals": [],
            "sisu_state": sisu, "contacted_flag": contacted, "full_name": f"P{i}"}


def test_one_close_in_twenty_is_five_percent_not_the_forecast():
    people = [person(i) for i in range(19)] + [person(19, sisu="closed")]
    a = lp.aggregate_by_agent(people, NOW)["a1"]
    assert a.predicted == 5.0


def test_no_closes_and_no_met_with_falls_back_to_the_product():
    people = [person(i) for i in range(20)]
    a = lp.aggregate_by_agent(people, NOW)["a1"]
    expected = round(0.55 * 0.65 * 0.40 * 0.50 * 100.0, 1)
    assert a.predicted == expected == 7.2            # the actual result of the ported rounding


def test_pickup_under_thirty_is_critical_even_when_later_stages_are_strong():
    # 10 leads, 2 contacted -- and both of those went all the way to Sisu closed.
    people = [person(i, touched=False) for i in range(8)]
    people += [person(8, sisu="closed"), person(9, sisu="closed")]
    a = lp.aggregate_by_agent(people, NOW)["a1"]
    assert a.pickup == 20.0
    assert sum(1 for k, v in a.conv.items() if v >= lp.INDUSTRY_BENCHMARKS[k]["q3"]) >= 3
    assert a.tier == "critical"


def test_a_sisu_close_forces_closed_whatever_the_fub_stage_says():
    assert lp.classify_stage(person(1, stage="New", sisu="closed")) == "closed"
    assert lp.classify_stage(person(1, stage="Closed", sisu=None)) != "closed"   # FUB alone does not close
    assert lp.classify_stage(person(1, stage="Lead", sisu="under_contract")) == "offer_written"
    assert lp.classify_stage(person(1, stage="Lead", appts=[{"start": NOW, "outcome": "kept"}])) == "met_with"
    assert lp.classify_stage(person(1, stage="Lead", touched=False, contacted=True)) == "contacted"


def test_counts_are_cumulative_and_stuck_uses_age_since_created():
    p = person(1, stage="Met With", days_old=40)
    counts = lp.stage_counts([p])
    assert [counts[s] for s in lp.STAGE_ORDER] == [1, 1, 1, 1, 1, 0, 0, 0]
    assert lp.is_stuck(p, "met_with", NOW)                       # 40 days, no touch in 7
    fresh = person(2, stage="Met With", days_old=10)
    assert not lp.is_stuck(fresh, "met_with", NOW)               # under the 21-day threshold


def test_the_team_table_has_one_row_per_agent_and_tiers_order_it():
    people = ([person(i, agent="a1", touched=False) for i in range(10)]
              + [person(10 + i, agent="a2", sisu="closed" if i < 3 else None) for i in range(10)])
    agents = lp.aggregate_by_agent(people, NOW)
    funnel = lp.team_funnel(agents, people, NOW)
    assert funnel["stages"][0]["count"] == 20 and funnel["stages"][-1]["count"] == 3
    assert len(agents) == 2 and agents["a1"].tier == "critical"


def test_windows_are_the_legacy_presets_in_the_workspace_calendar():
    today = dt.date(2026, 2, 14)
    assert lp.window_bounds("last_4mo", today) == (dt.date(2025, 10, 1), today)
    assert lp.window_bounds("rolling_30", today) == (dt.date(2026, 1, 15), today)
    assert lp.window_bounds("ytd", today) == (dt.date(2026, 1, 1), today)
    assert lp.window_bounds("all_time", today) == (None, None)


def test_the_date_bar_period_reads_like_every_other_page():
    """The report follows the app's date bar: its periods resolve through metrics._period_range,
    and a custom range is its own two days. Anything unrecognised is the bar's default, month to date."""
    today = dt.date(2026, 11, 11)
    assert lp.window_bounds("mtd", today) == (dt.date(2026, 11, 1), today)
    assert lp.window_bounds("last_month", today) == (dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    assert lp.window_bounds("last_7", today) == (dt.date(2026, 11, 5), today)
    assert lp.window_bounds("c:2026-02-01:2026-11-11", today) == (dt.date(2026, 2, 1), today)
    assert lp.window_bounds("bogus", today) == (dt.date(2026, 11, 1), today)
    meta = lp._window_meta("c:2026-02-01:2026-11-11", today)
    assert meta["key"] == "c:2026-02-01:2026-11-11" and meta["label"] == "Feb 1 – Nov 11, 2026"
    assert meta["start"] == "2026-02-01" and meta["end"] == "2026-11-11"
    assert lp._window_meta("bogus", today)["key"] == "mtd"
    assert lp._window_meta("rolling_30", today)["label"] == "Last 30 days"     # the digest's keys stay


def test_the_program_counts_every_lead_whatever_the_range():
    class G:
        program_start = None
    people = [person(1, days_old=200), person(2, days_old=3)]
    today = dt.date(2026, 11, 10)
    assert lp.program_meta(people, G, today) == {"since": (NOW - dt.timedelta(days=200)).date().isoformat(),
                                                 "until": "2026-11-10", "leads": 2}
    G.program_start = dt.date(2026, 2, 1)
    assert lp.program_meta([], G, today) == {"since": "2026-02-01", "until": "2026-11-10", "leads": 0}
