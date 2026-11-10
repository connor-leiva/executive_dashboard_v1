"""What's Working (spec phase 5): pooled medians, the live rule, journeys -- plain dicts, no database."""
import datetime as dt
from zoneinfo import ZoneInfo

from app.services import lead_source_behavior as lb

C = dt.datetime(2026, 3, 2, 15, 0, tzinfo=dt.timezone.utc)


def touch(minutes, kind="try", channel="call", duration=None):
    return {"at": C + dt.timedelta(minutes=minutes), "kind": kind, "channel": channel, "duration_sec": duration}


def person(i, touches, agent="a", **kw):
    ts = sorted(touches, key=lambda t: t["at"])
    return {"person_id": str(i), "created": C, "touches": ts, "first_outbound_at": ts[0]["at"] if ts else None,
            "last_activity_at": ts[-1]["at"] if ts else None, "appointments": kw.get("appts", []),
            "deals": [], "stage": "Lead", "sisu_state": kw.get("sisu"), "agent_id": agent,
            "assigned_to": agent, "price": kw.get("price"), "full_name": f"P{i}"}


def test_the_team_median_is_pooled_not_the_average_of_agent_medians():
    a = [person(i, [touch(m)], agent="a") for i, m in enumerate([1, 2, 3])]          # median 2
    b = [person(10 + i, [touch(m)], agent="b") for i, m in enumerate([100])]          # median 100
    pooled = lb.compute_metric_set(a + b)["speed_to_first_min"]
    avg_of_medians = (lb.compute_metric_set(a)["speed_to_first_min"] + lb.compute_metric_set(b)["speed_to_first_min"]) / 2
    assert pooled == 2.5 and avg_of_medians == 51.0


def test_a_note_only_call_is_live_and_moves_attempts_to_live():
    # The universe marks a duration-0 call with a note as `live` (crm_activity.is_live). Two texts
    # come before it, so attempts before first live is 2 -- and its duration stays out of the median.
    p = person(1, [touch(1, channel="text"), touch(2, channel="text"), touch(3, kind="live", duration=0.0)])
    m = lb.compute_metric_set([p])
    assert m["attempts_to_live"] == 2.0 and m["n_with_attempts_log"] == 1
    assert m["median_call_dur_sec"] is None and m["n_live_calls"] == 0
    never = person(2, [touch(1), touch(2)])
    assert lb.attempts_before_first_live(never) is None                 # excluded, not zero


def test_journeys_clamp_past_72_hours_and_flag_that_it_continues():
    p = person(1, [touch(30), touch(80 * 60, kind="live")])
    j = lb.build_journey(p, ZoneInfo("America/Denver"))
    assert [e["axis_pct"] for e in j["events"]] == [round(0.5 / 72 * 100, 2), 100.0]
    assert j["events"][1]["beyond_window"] and j["continues"] and j["touch_count_24h"] == 1


def test_every_median_carries_its_sample_size():
    p = person(1, [touch(5, kind="live", duration=120.0)], price=450000)
    m = lb.compute_metric_set([p])
    for metric in lb.METRICS:
        assert metric[5] in m, metric[0]
    assert m["n_with_speed"] == 1 and m["n_live_calls"] == 1 and m["n_with_price"] == 1


def test_logging_hygiene_flags_thin_logging_and_outcomes_without_touches():
    thin = [person(i, [], agent="x") for i in range(9)]
    assert lb.logging_diagnostics(thin)["is_hygiene_suspect"]
    busy = [person(1, [touch(1)], sisu="closed"), person(2, [touch(1)], sisu="under_contract")]
    d = lb.logging_diagnostics(busy)
    assert d["is_hygiene_suspect"] and "outside Follow Up Boss" in d["hygiene_reason"]
