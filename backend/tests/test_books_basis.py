"""Why a suggestion exists, as a stable key.

The prose in `suggestion.reason` interpolates a count, so the live books carry ten spellings
of "matched from history" inside the top twelve reasons and a tail of ~715 more. Grouping or
filtering on that text is impossible. These tests pin the tag that replaces it, and the
backfill that reads the old prose.
"""
import importlib.util
import pathlib

import pytest

from app.services.books_scan import (
    BASIS_HISTORY, BASIS_OVER_BAND, BASIS_SPLIT, BASIS_CLAUDE, BASIS_NONE, _pass1,
)

_MIG = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / \
    "0059_books_suggestion_basis.py"


def _classify():
    spec = importlib.util.spec_from_file_location("mig0059", _MIG)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m._classify


# ── the vocabulary ────────────────────────────────────────────────────────────────────────

def test_basis_values_are_distinct():
    vals = [BASIS_HISTORY, BASIS_OVER_BAND, BASIS_SPLIT, BASIS_CLAUDE, BASIS_NONE]
    assert len(set(vals)) == len(vals)
    assert all(v == v.lower() and " " not in v for v in vals)


# ── the backfill, against the real strings from the live books ────────────────────────────

@pytest.mark.parametrize("reason,basis,priors", [
    # every one of these is a real reason string counted in production
    ("Matches 195 prior charges categorized here.", BASIS_HISTORY, 195),
    ("Matches 12 prior charges categorized here.", BASIS_HISTORY, 12),
    ("Matches 6 prior charges categorized here.", BASIS_HISTORY, 6),
    ("Matches 3 prior charges categorized here.", BASIS_HISTORY, 3),
    ("Matches 102 prior charges categorized here.", BASIS_HISTORY, 102),
    ("Split across multiple accounts; needs review.", BASIS_SPLIT, None),
    ("Known vendor, amount above the usual range (8 priors).", BASIS_OVER_BAND, 8),
    ("Known vendor, amount above the usual range (1 prior).", BASIS_OVER_BAND, 1),
])
def test_backfill_reads_the_prose(reason, basis, priors):
    got_basis, got_priors = _classify()(reason)
    assert got_basis == basis
    assert got_priors == priors


def test_ten_spellings_collapse_to_one_tag():
    """The whole point: the top of the live reason list is one reason wearing ten numbers."""
    c = _classify()
    reasons = [f"Matches {n} prior charges categorized here." for n in
               (195, 12, 6, 5, 7, 3, 102, 13, 38, 56)]
    assert {c(r)[0] for r in reasons} == {BASIS_HISTORY}
    assert sorted(c(r)[1] for r in reasons) == [3, 5, 6, 7, 12, 13, 38, 56, 102, 195]


def test_unrecognized_prose_falls_to_claude():
    """Pass 1 writes two known sentences and splits write a third, so anything else — including
    an empty reason, which only Pass 3 produces — came from Claude."""
    c = _classify()
    assert c("Recurring SaaS charge consistent with prior months.")[0] == BASIS_CLAUDE
    assert c("")[0] == BASIS_CLAUDE
    assert c(None)[0] == BASIS_CLAUDE


# ── going forward: the scanner writes the tag itself ──────────────────────────────────────

class _Txn:
    def __init__(self, amount, label="Office Supplies", qbo_id="42"):
        self.amount = amount
        self.account_label = label
        self.account_qbo_id = qbo_id
        self.id = "t1"


class _Prior:
    def __init__(self, amount, label="Office Supplies"):
        self.amount = amount
        self.account_label = label
        self.id = object()


def test_history_clear_carries_its_basis_and_prior_count():
    priors = [_Prior(100) for _ in range(6)]
    kind, sug = _pass1(_Txn(90), priors)
    assert kind == "clear"
    assert sug["basis"] == BASIS_HISTORY
    assert sug["priors"] == 6
    assert "6 prior charges" in sug["reason"]          # prose and tag agree


def test_over_band_is_tagged_differently_from_a_clean_match():
    priors = [_Prior(100) for _ in range(6)]
    kind, sug = _pass1(_Txn(10_000), priors)           # far above the trailing max
    assert kind == "over_band"
    assert sug["basis"] == BASIS_OVER_BAND
    assert sug["confidence"] < 0.99
