"""The Meta client's parsers and guards. SPEC-ads-module.md Parts 5.4 and 7.

Everything here is pure or fake-response driven - no network, no token, no fixture. The functions
that actually matter for correctness in this client are the ones that read Meta's headers and
envelopes, and those are exactly the ones that can be tested without touching Meta.
"""
import httpx
import pytest

from app.config import settings
from app.integrations import meta_ads as meta


def _resp(headers: dict | None = None) -> httpx.Response:
    return httpx.Response(200, headers=headers or {}, request=httpx.Request("GET", "https://x"))


# ── the BUC header, which is the documented trap ──────────────────────────────────────
def test_buc_usage_parses_the_map_of_arrays_shape():
    """X-Business-Use-Case-Usage is a map of business-object-id to an ARRAY of up to 32 objects.
    It is not a flat object and not an integer, and a parser written for X-App-Usage - which IS
    flat - returns nonsense against it without erroring."""
    hdr = ('{"1234567890": [{"type": "ads_insights", "call_count": 12, '
           '"total_cputime": 97, "total_time": 40}]}')
    assert meta.usage_pct(_resp({"X-Business-Use-Case-Usage": hdr})) == 97.0


def test_it_steers_on_the_max_of_all_three_not_on_call_count():
    """Throttling starts when ANY of the three reaches 100. Steering on call_count alone is the
    named trap: a few expensive insights queries put total_cputime at 100 while call_count sits
    in the teens, and the app is throttled with no warning."""
    hdr = '{"a": [{"call_count": 8, "total_cputime": 100, "total_time": 3}]}'
    assert meta.usage_pct(_resp({"X-Business-Use-Case-Usage": hdr})) == 100.0


def test_it_takes_the_worst_across_every_object_in_the_map():
    hdr = ('{"acct_a": [{"call_count": 10, "total_cputime": 10, "total_time": 10}],'
           ' "acct_b": [{"call_count": 5, "total_cputime": 5, "total_time": 88}]}')
    assert meta.usage_pct(_resp({"X-Business-Use-Case-Usage": hdr})) == 88.0


@pytest.mark.parametrize("hdr", ["", "not json", "[1,2,3]", '{"a": "flat"}', '{"a": [null]}',
                                 '{"a": [{"call_count": "x"}]}'])
def test_a_malformed_header_is_zero_and_never_raises(hdr):
    """This header is not worth failing a sync over. Returning 0.0 means "no evidence of
    pressure", which is the safe reading - the request already succeeded."""
    assert meta.usage_pct(_resp({"X-Business-Use-Case-Usage": hdr})) == 0.0
    assert meta.usage_pct(_resp({})) == 0.0


def test_retry_after_is_converted_from_minutes_to_seconds():
    """Meta reports estimated_time_to_regain_access in MINUTES. Sleeping that many SECONDS
    retries while still throttled and burns the remaining budget."""
    hdr = '{"a": [{"call_count": 100, "estimated_time_to_regain_access": 4}]}'
    assert meta.retry_after(_resp({"X-Business-Use-Case-Usage": hdr})) == 240
    assert meta.retry_after(_resp({})) is None


# ── errors stay distinguishable from emptiness ────────────────────────────────────────
def test_a_meta_error_raises_rather_than_returning_empty():
    """The single most important behaviour in this client. Collapsing an error into [] makes a
    rate-limited pull read as a paused account - the numbers just quietly go to zero and the
    dashboard looks calm."""
    with pytest.raises(meta.MetaError) as ei:
        meta._raise_for_meta({"error": {"message": "Rate limit", "code": 17}})
    assert ei.value.is_throttle
    assert "Rate limit" in str(ei.value)


def test_a_non_throttle_error_is_still_an_error():
    with pytest.raises(meta.MetaError) as ei:
        meta._raise_for_meta({"error": {"message": "Bad token", "code": 190}})
    assert not ei.value.is_throttle


def test_a_clean_payload_does_not_raise():
    meta._raise_for_meta({"data": []})
    meta._raise_for_meta({})


# ── version pinning ───────────────────────────────────────────────────────────────────
def test_the_version_appears_in_exactly_one_place():
    """An expired Marketing API version does not error - Meta runs the call as a later version
    and returns a 200 - so a stray hardcoded version is a number that changes silently."""
    import ast
    import re
    from pathlib import Path

    assert settings.META_GRAPH_VERSION in meta._base()

    # Parse rather than grep. The module docstring legitimately DISCUSSES version numbers - the
    # separate Graph and Marketing clocks for the same number is exactly the fact a reader needs -
    # and a line filter cannot tell prose from code. What must not exist is a version inside a
    # string the module actually evaluates.
    tree = ast.parse(Path(meta.__file__).read_text(encoding="utf-8"))
    # clean=False: get_docstring() dedents and strips by default, so the cleaned text no longer
    # equals the raw Constant it came from and every docstring reads as inlined code.
    docstrings = {ast.get_docstring(n, clean=False) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
    hardcoded = [n.value for n in ast.walk(tree)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)
                 and n.value not in docstrings and re.search(r"v2[0-9][.]0", n.value)]
    assert not hardcoded, f"a version string is inlined outside _base(): {hardcoded}"


def test_the_client_never_writes():
    """ads_read only, never ads_management. The only non-GET this module may ever make is the
    async report run, and there is none today."""
    from pathlib import Path

    src = Path(meta.__file__).read_text(encoding="utf-8")
    for verb in (".post(", ".put(", ".patch(", ".delete("):
        assert verb not in src, f"{verb} appears in a read-only client"


# ── pure helpers ──────────────────────────────────────────────────────────────────────
def test_budgets_convert_from_minor_units_and_respect_zero_decimal_currencies():
    """Dividing a yen budget by 100 understates it by two orders of magnitude, and it renders as
    a plausible small number rather than an obvious error."""
    assert meta.minor_to_decimal("5000", "USD") == pytest.approx(50)
    assert meta.minor_to_decimal("5000", "JPY") == pytest.approx(5000)
    assert meta.minor_to_decimal(None, "USD") is None
    assert meta.minor_to_decimal("junk", "USD") is None


def test_action_count_sums_only_the_named_types():
    actions = [{"action_type": "lead", "value": "3"},
               {"action_type": "link_click", "value": "99"}]
    assert meta.action_count(actions, {"lead"}) == 3
    assert meta.action_count(actions, {"lead", "link_click"}) == 102
    assert meta.action_count(None, {"lead"}) == 0


def test_the_headline_is_read_in_the_order_meta_populates_it():
    assert meta.creative_headline(
        {"object_story_spec": {"link_data": {"name": "Link headline"}}}) == "Link headline"
    assert meta.creative_headline(
        {"object_story_spec": {"video_data": {"title": "Video title"}}}) == "Video title"
    assert meta.creative_headline({"title": "Fallback"}) == "Fallback"
    assert meta.creative_headline({}) is None
    assert meta.creative_headline(None) is None


def test_url_tags_parse_into_the_utm_map_the_readiness_check_reads():
    tags = "utm_source=meta&utm_campaign=KB-Webinar&utm_content=120210"
    got = meta.parse_url_tags(tags)
    assert got["utm_source"] == "meta" and got["utm_content"] == "120210"
    assert meta.parse_url_tags(None) == {}
    assert meta.parse_url_tags("") == {}


def test_ad_level_readiness_accepts_the_macro_and_the_resolved_id():
    """A template carries the literal macro; a live ad carries the resolved number. Both mean
    ad-level attribution is available, and a check that only accepted one would tell a correctly
    configured tenant their setup was broken."""
    assert meta.has_ad_level_tagging("utm_content={{ad.id}}")
    assert meta.has_ad_level_tagging("utm_source=meta&utm_content=120210394")
    assert not meta.has_ad_level_tagging("utm_source=meta&utm_campaign=KB")
    assert not meta.has_ad_level_tagging(None)
    assert not meta.has_ad_level_tagging("utm_content=")
