"""Parked candidates are not the pipeline, and they are not anybody's work.

Connor's ask, in his words: "As an exec, I want to see a snapshot of what's happening and moving
RIGHT NOW." At ULRG that matters arithmetically, not aesthetically -- 1,347 of 1,625 open
candidates are parked, so a chart drawn over all of them is one enormous bar and five slivers.

Two things had to change for that to be safe. The pipeline split on the literal label "Nurture",
and three rules skipped on the literal pair `(Signed, Nurture)` -- so the moment a brokerage split
nurture into Hot/Warm/Cold, three labels that are none of those would have been drawn as active
stages AND started producing daily tasks for people nobody intended to chase. An unmapped stage
already behaved that way, which is why the Settings footer's promise that an unmapped stage "is
not counted anywhere" was only ever half true.

Both now key on FUNNEL_GROUPS: what the funnel IS, rather than a list of what to leave out.
"""
import datetime as dt
import re
import types
import uuid
from pathlib import Path

from app.services import recruiting_rules as R
from app.services.recruiting_settings import CHASE_GROUPS, FUNNEL_GROUPS, KNOWN_GROUPS

NOW = dt.datetime(2026, 9, 24, 18, 0, tzinfo=dt.timezone.utc)
TZ = R.business_tz()


def _cand(group, **kw):
    base = dict(id=uuid.uuid4(), stage_group=group, status="open", owner_seat_id=None,
                entered_stage_at=NOW - dt.timedelta(days=200),
                created_at_src=NOW - dt.timedelta(days=270),
                first_seen_at=NOW - dt.timedelta(days=270),
                ghl_task_id=None, name="Sunny Kaur")
    base.update(kw)
    return types.SimpleNamespace(**base)


def _facts(cands, watch=None):
    return R.Facts(candidates=list(cands), appointments=[], activity_by_candidate={},
                   seats_by_id={}, sdr_seat_id=None, watch_start=watch,
                   group_owner={g: "team_leader" for g in KNOWN_GROUPS})


def _fire(cand, watch=None):
    """Every rule, against one candidate. Returns the rule keys that claimed it."""
    facts = _facts([cand], watch)
    out = []
    for key, fn in R.RULES:
        if fn(facts, cand, NOW, TZ, dict(R.DEFAULTS[key])):
            out.append(key)
    return out


def test_a_parked_candidate_is_on_nobodys_list():
    """Whatever the band is called. This is the case that did not exist before: "Hot Nurture" is
    not the literal "Nurture", so the old guard would have chased all of them."""
    for band in ("Nurture", "Hot Nurture", "Warm Nurture", "Cold Nurture"):
        assert _fire(_cand(band)) == [], f"{band!r} produced work"


def test_an_unmapped_stage_is_on_nobodys_list():
    """The Settings footer says an unmapped stage "is not counted anywhere". It was true of the
    counts and false of the queue: no group meant no match on the skip list, so the rules treated
    it as an active funnel stage. Unmapping CO OP Agent would have dropped 680 items on the team.
    """
    assert _fire(_cand(None)) == [], "an unmapped candidate produced work"
    assert _fire(_cand("Something A Brokerage Invented")) == []


def test_the_funnel_still_produces_work():
    """The inversion must not have made everything quiet."""
    watch = NOW - dt.timedelta(days=400)          # watching long enough for the floor to clear
    assert "new_lead_untouched" in _fire(_cand("Sourced"), watch)
    assert "stage_14d" in _fire(_cand("Met"), watch)
    assert "met_no_next_step" in _fire(_cand("Met"), watch)
    assert "offer_out_stale" in _fire(_cand("Offer out"), watch)
    assert "appt_set_no_event" in _fire(_cand("Appointment set"), watch)
    # Signed is in the funnel but is an outcome, not a position: no chasing.
    assert _fire(_cand("Signed"), watch) == []


def test_chase_groups_is_the_funnel_minus_the_outcome():
    assert CHASE_GROUPS == set(FUNNEL_GROUPS) - {"Signed"}
    assert "Nurture" not in CHASE_GROUPS and "Signed" not in CHASE_GROUPS


def test_the_settings_screen_offers_exactly_the_groups_the_server_knows():
    """`REC_GROUPS` in Settings.jsx is a second copy of KNOWN_GROUPS. A group the screen offers but
    the server has never heard of would be mapped by somebody and then silently behave as parked;
    one the server knows and the screen hides cannot be chosen at all. Scan the consumer rather
    than trusting two hand-kept lists to stay equal."""
    jsx = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "Settings.jsx"
           ).read_text(encoding="utf-8")
    m = re.search(r"const REC_GROUPS = \[(.*?)\];", jsx, re.S)
    assert m, "REC_GROUPS has moved or been renamed in Settings.jsx"
    offered = tuple(re.findall(r'"([^"]+)"', m.group(1)))
    assert offered == KNOWN_GROUPS, (
        f"Settings.jsx offers {offered}\nthe server knows {KNOWN_GROUPS}")
