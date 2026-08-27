"""Defaults must not encode one customer's geography, domain, or tag names.

Each of these was a fallback in the compute layer that silently applied one workspace's answer
to every other workspace. They share a failure mode worth naming: the sync succeeds, the UI
shows no error, and the number is simply wrong or missing. That is strictly worse than a crash,
because nobody investigates a dashboard that looks fine.

The rule they now follow: an unset option means DO LESS — do not filter, do not match, do not
segment — never "assume the first customer's answer".
"""
from app.integrations import ghl
from app.services.metrics import _in_states


class _Loan:
    def __init__(self, state):
        self.meta = {"property_state": state}


def test_an_unset_state_filter_counts_every_loan():
    """The default was {"UT"}. A lender operating in Texas connected Arive, the sync reported
    success, and every loan was dropped by a filter nothing in the UI mentions."""
    for state in ("TX", "UT", "CA", "", None):
        assert _in_states(_Loan(state), None) is True, state


def test_a_set_filter_still_filters():
    assert _in_states(_Loan("UT"), {"UT"}) is True
    assert _in_states(_Loan("TX"), {"UT"}) is False
    assert _in_states(_Loan("tx"), {"TX"}) is True          # case is normalised
    assert _in_states(_Loan(None), {"UT"}) is False


def test_no_segmentation_tags_means_everyone_is_simply_a_member():
    """forum_tags/innercircle_tags defaulted to one workspace's literal tag names, so every
    other workspace's member split rendered empty with no field to correct it. Empty must mean
    'this workspace runs one programme', which is the common case."""
    assert ghl.member_segment({"anything"}, set(), set()) == "member"
    assert ghl.member_segment(set(), set(), set()) == "member"


def test_segmentation_still_works_when_configured():
    forum, ic = {"my forum tag"}, {"my ic tag"}
    assert ghl.member_segment({"my forum tag"}, forum, ic) == "forum"
    assert ghl.member_segment({"my ic tag"}, forum, ic) == "inner_circle"
    assert ghl.member_segment({"my forum tag", "my ic tag"}, forum, ic) == "forum"   # forum wins
    assert ghl.member_segment({"unrelated"}, forum, ic) == "member"


def test_the_sync_no_longer_seeds_a_referral_domain():
    """It used to setdefault the first customer's domain into whoever synced next, as the value
    identifying THEIR referrals — so their flywheel reconciled to zero forever."""
    src = (__import__("pathlib").Path(__file__).resolve().parents[1]
           / "app" / "services" / "sync.py").read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "liveutah.com" not in code, "a customer's domain is back in the sync defaults"


def test_no_state_or_tag_literals_remain_in_the_compute_defaults():
    """A broad guard rather than a precise one: these are the shapes that keep reappearing."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app" / "services"
    offenders = []
    for f in (root / "metrics.py", root / "sync.py"):
        code = "\n".join(l for l in f.read_text(encoding="utf-8").splitlines()
                         if not l.strip().startswith("#"))
        # CODE patterns, not any mention — the docstrings deliberately record what the old
        # defaults were and why they went, and that history is worth keeping readable.
        for needle in ('or ["UT"]', 'or {"UT"}', 'or ["liveutah.com"]',
                       'or ["the forum active"'):
            if needle in code:
                offenders.append(f"{f.name}: {needle}")
    assert not offenders, offenders
