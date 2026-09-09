"""Section labels, durations, and the dates a member is actually shown.

Two frontends read every value in here. A label the console generates one way and the portal
generates another is a bug an author cannot see and cannot fix, which is why the arithmetic lives
in one module and why this file is mostly about arithmetic.
"""
import datetime as dt
from types import SimpleNamespace

import pytest

from app.services import course_sections as cs
from app.services import lesson_media

TODAY = dt.date(2026, 9, 9)          # a Wednesday


def section(**kw):
    base = dict(id=kw.pop("id", "s1"), name="Getting started", summary=None,
                release_rule="immediate", release_day=None, release_date=None,
                due_rule="none", due_day=None, sort=0)
    base.update(kw)
    return SimpleNamespace(**base)


# ── labels ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("scheme,index,expected", [
    ("day", 0, "Day 1"),
    ("day", 4, "Day 5"),
    ("module", 2, "Module 3"),
    ("week", 0, "Week 1"),
    ("phase", 1, "Phase 2"),
    ("part", 0, "Part I"),
    ("part", 3, "Part IV"),
    ("part", 8, "Part IX"),
])
def test_the_label_is_generated_from_the_scheme_and_the_position(scheme, index, expected):
    assert cs.label_for(scheme, index) == expected


@pytest.mark.parametrize("scheme", ["custom", "none", None, ""])
def test_custom_and_none_generate_nothing_so_the_name_carries_it(scheme):
    """An empty chip beside a title is two titles. "" is how the card knows to render one."""
    assert cs.label_for(scheme, 0) == ""


def test_switching_scheme_renames_every_section_and_moves_nothing():
    """The reason the scheme is on the COURSE and the label is not stored. This is one write."""
    sections = [section(id=f"s{i}", sort=i) for i in range(3)]
    days = [s["label"] for s in cs.resolve(sections, scheme="day")]
    modules = [s["label"] for s in cs.resolve(sections, scheme="module")]
    assert days == ["Day 1", "Day 2", "Day 3"]
    assert modules == ["Module 1", "Module 2", "Module 3"]
    assert [s["id"] for s in cs.resolve(sections, scheme="module")] == ["s0", "s1", "s2"]


# ── durations ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind,kwargs,short,long", [
    ("video", {"duration_minutes": 22}, "22m", "22 min"),
    ("reading", {"read_minutes": 6}, "6m read", "6 min read"),
    ("document", {"page_count": 9}, "9 pp", "9 pages"),
])
def test_each_kind_states_its_length_in_its_own_unit(kind, kwargs, short, long):
    out = cs.duration_of(kind, **kwargs)
    assert out["short"] == short
    assert out["long"] == long


def test_a_length_nobody_has_set_is_absent_rather_than_zero():
    """"0 min" is a claim about a lesson. None is the absence of one."""
    assert cs.duration_of("video", duration_minutes=None) is None
    assert cs.duration_of("reading", read_minutes=0) is None


def test_a_reading_lesson_reads_its_minutes_from_the_right_column():
    """A lesson that used to be a video still has `duration_minutes` sitting on it. Reading from
    the wrong column would report the old video's length on the article that replaced it."""
    assert cs.lesson_minutes("reading", 22, 6) == 6
    assert cs.lesson_minutes("video", 22, 6) == 22


def test_a_course_of_articles_no_longer_totals_zero():
    """The bug this replaces: the total summed `duration_minutes` only, so a reading-heavy course
    advertised itself as taking no time at all."""
    lessons = [SimpleNamespace(kind="reading", duration_minutes=None, read_minutes=6),
               SimpleNamespace(kind="reading", duration_minutes=None, read_minutes=4),
               SimpleNamespace(kind="video", duration_minutes=22, read_minutes=None)]
    assert cs.total_minutes(lessons) == 32


def test_pages_are_not_counted_as_minutes():
    """There is no honest conversion, and inventing one would put a made-up number on the card."""
    lessons = [SimpleNamespace(kind="document", duration_minutes=None, read_minutes=None)]
    assert cs.total_minutes(lessons) == 0


# ── release ───────────────────────────────────────────────────────────────────────────────

def test_day_one_is_the_day_the_member_enrolled():
    """Off by one here opens every section a day late for everybody in the workspace."""
    started = dt.date(2026, 9, 9)
    assert cs.opens_on(section(release_rule="day_n", release_day=1), started) == started
    assert cs.opens_on(section(release_rule="day_n", release_day=3), started) \
        == dt.date(2026, 9, 11)


def test_a_day_rule_is_a_different_date_for_every_member():
    """Which is the whole reason release is stored as a rule rather than as a date."""
    rule = section(release_rule="day_n", release_day=3)
    assert cs.opens_on(rule, dt.date(2026, 9, 1)) == dt.date(2026, 9, 3)
    assert cs.opens_on(rule, dt.date(2026, 10, 20)) == dt.date(2026, 10, 22)


def test_after_previous_is_not_a_date_at_all():
    """It is a question about progress. Answering it with a date would be answering it wrongly."""
    assert cs.opens_on(section(release_rule="after_previous"), TODAY) is None


def test_a_section_gated_on_the_previous_one_opens_when_that_one_is_done():
    sections = [section(id="a", sort=0), section(id="b", sort=1, release_rule="after_previous")]
    unfinished = cs.resolve(sections, scheme="day", today=TODAY,
                            done_counts={"a": (1, 3), "b": (0, 2)})
    assert unfinished[1]["state"] == "locked"
    finished = cs.resolve(sections, scheme="day", today=TODAY,
                          done_counts={"a": (3, 3), "b": (0, 2)})
    assert finished[1]["state"] == "current"


def test_a_day_rule_with_no_enrolment_opens_rather_than_locks():
    """A member with no enrolment row has never opened the course. Locking Day 1 for them is a
    course nobody can start."""
    sections = [section(release_rule="day_n", release_day=1)]
    assert cs.resolve(sections, scheme="day", started_on=None,
                      today=TODAY)[0]["released"] is True


def test_lock_sections_gates_on_progress_even_when_the_calendar_has_arrived():
    """Two rules in sequence: the date decides whether the course has reached the section, the
    lock decides whether the member has. Both must pass."""
    sections = [section(id="a", sort=0), section(id="b", sort=1)]
    out = cs.resolve(sections, scheme="day", lock_sections=True, today=TODAY,
                     done_counts={"a": (1, 2), "b": (0, 2)})
    assert out[0]["state"] == "current"
    assert out[1]["state"] == "locked"


def test_an_empty_section_is_never_complete():
    """A section the author has not filled in yet would otherwise show a green tick and unlock
    everything behind it."""
    out = cs.resolve([section(id="a")], scheme="day", today=TODAY, done_counts={"a": (0, 0)})
    assert out[0]["state"] == "current"


# ── due dates ─────────────────────────────────────────────────────────────────────────────

def test_end_of_day_n_lands_on_that_day():
    started = dt.date(2026, 9, 9)
    assert cs.due_on(section(due_rule="end_of_day_n", due_day=1), started) == started
    assert cs.due_on(section(due_rule="end_of_day_n", due_day=5), started) \
        == dt.date(2026, 9, 13)


def test_a_one_week_block_is_seven_days_not_eight():
    """`+ 7` would give every member an extra day, every week, for the life of the course."""
    started = dt.date(2026, 9, 9)
    assert cs.due_on(section(due_rule="end_of_week_n", due_day=1), started) \
        == dt.date(2026, 9, 15)
    assert cs.due_on(section(due_rule="end_of_week_n", due_day=2), started) \
        == dt.date(2026, 9, 22)


@pytest.mark.parametrize("started,due_day,expected", [
    (dt.date(2026, 9, 1), 5, "Overdue"),          # due Sep 5, four days ago
    (dt.date(2026, 9, 9), 1, "Due today"),
    (dt.date(2026, 9, 9), 2, "Due tomorrow"),
    (dt.date(2026, 9, 9), 4, "Due Sat"),
])
def test_the_note_says_where_today_sits_against_the_due_date(started, due_day, expected):
    out = cs.resolve([section(due_rule="end_of_day_n", due_day=due_day)], scheme="day",
                     started_on=started, today=TODAY, done_counts={"s1": (0, 2)})
    assert out[0]["note"] == expected


def test_day_zero_is_not_a_day():
    """1-based everywhere. A 0 would silently mean "the day before enrolment"."""
    assert cs.due_on(section(due_rule="end_of_day_n", due_day=0), TODAY) is None
    assert cs.opens_on(section(release_rule="day_n", release_day=0), TODAY) is None


def test_a_finished_section_stops_nagging():
    """"Overdue" on work somebody has already done is the product being wrong at them."""
    out = cs.resolve([section(due_rule="end_of_day_n", due_day=1)], scheme="day",
                     started_on=dt.date(2026, 9, 1), today=TODAY, done_counts={"s1": (2, 2)})
    assert out[0]["state"] == "complete"
    assert out[0]["note"] == ""


def test_a_locked_section_says_when_it_opens_instead_of_when_it_is_due():
    out = cs.resolve([section(release_rule="day_n", release_day=3)], scheme="day",
                     started_on=TODAY, today=TODAY, done_counts={"s1": (0, 2)})
    assert out[0]["state"] == "locked"
    assert "Opens" in out[0]["note"]


def test_the_day_counter_starts_at_one_and_never_goes_below_it():
    """"Day 0 of 5" is not a day, and a clock skew that put today before the enrolment would
    otherwise produce a negative one."""
    assert cs.day_number(dt.date(2026, 9, 9), TODAY) == 1
    assert cs.day_number(dt.date(2026, 9, 5), TODAY) == 5
    assert cs.day_number(dt.date(2026, 9, 20), TODAY) == 1
    assert cs.day_number(None, TODAY) is None


def test_today_is_the_workspaces_day_not_utcs():
    """At 6pm Mountain the UTC date has already rolled over. A member told they are overdue
    during the afternoon they were given is the product losing an argument it started."""
    assert cs.workspace_today("America/Denver") in (
        dt.datetime.now(dt.timezone.utc).date(),
        dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1))
    assert cs.workspace_today("Not/AZone") == dt.datetime.now(dt.timezone.utc).date()


# ── the player guard ──────────────────────────────────────────────────────────────────────

def test_a_reading_lesson_has_no_player_at_all():
    """The spec calls this the single most likely bug in the change: `resolve` with an empty
    source_ref returns a "no source attached yet" card, which would render on a finished article
    as an apology for a video nobody was expecting."""
    assert lesson_media.resolve("HERE", None, kind="reading") is None
    assert lesson_media.resolve("LOOM", "https://loom.com/share/abc", kind="reading") is None


def test_every_other_kind_still_resolves():
    assert lesson_media.resolve("HERE", None)["mode"] == "none"
    assert lesson_media.resolve("LOOM", "https://loom.com/share/abc")["mode"] == "iframe"
    assert lesson_media.resolve("LOOM", "https://loom.com/share/abc", kind="video")["mode"] \
        == "iframe"


def test_a_course_of_articles_is_not_badged_video():
    """`source_type` on a reading lesson is whatever the dropdown defaulted to."""
    assert lesson_media.course_media(["HERE", "HERE"], ["reading", "reading"]) == "Reading"
    assert lesson_media.course_media(["HERE", "LOOM"], ["reading", "video"]) == "Video + Reading"
    assert lesson_media.course_media(["HERE", "LOOM"]) == "Video"      # unchanged without kinds
