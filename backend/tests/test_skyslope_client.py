"""The SkySlope client: its signature, its session, its rate limit, and that it only reads.

Nothing here has met the real API. The fake is shaped from SkySlope's OpenAPI file, and the things
only a live call can settle are listed at the top of app/integrations/skyslope.py.
"""
import ast
import datetime as dt
import re
from pathlib import Path

import httpx
import pytest

from app.integrations import skyslope
from tests.skyslope_fake import CLIENT_ID, CLIENT_SECRET, PII, fake, listing, sale, user  # noqa: F401

SOURCE = Path(skyslope.__file__).read_text(encoding="utf-8")


def _creds(key="key-1", secret="secret-1"):
    return skyslope.make_creds(key, secret)


# ── the signature ─────────────────────────────────────────────────────────────────────────────
def test_the_signature_matches_one_computed_outside_this_code():
    """Computed with openssl, so a change to the message or the encoding cannot agree with itself:
    printf 'client-id:client-secret:2026-11-04T12:00:00Z' | openssl dgst -sha256 -hmac 'access-secret' -binary | base64
    """
    assert skyslope.sign("client-id", "client-secret", "access-secret", "2026-11-04T12:00:00Z") \
        == "RxFF1nTSBLWGy/cDSKBY4xFre0ICeLpBzcS9zsDb3a4="


def test_the_login_request_signs_the_same_timestamp_it_sends():
    creds = skyslope.Creds("client-id", "client-secret", "the-key", "access-secret", "")
    now = dt.datetime(2026, 11, 4, 12, 0, 0, tzinfo=dt.timezone.utc)
    headers, body = skyslope.login_request(creds, now)
    assert headers == {"Authorization": "SS the-key:RxFF1nTSBLWGy/cDSKBY4xFre0ICeLpBzcS9zsDb3a4=",
                       "Timestamp": "2026-11-04T12:00:00Z"}
    assert body == {"clientID": "client-id", "clientSecret": "client-secret"}


# ── it only reads ─────────────────────────────────────────────────────────────────────────────
def _code_only(source: str) -> str:
    """The source with its strings and docstrings gone, so a comment cannot trip this or satisfy it."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            node.value = ""
    return ast.unparse(tree)


def test_the_client_has_one_post_and_it_is_the_login():
    code = _code_only(SOURCE)
    for verb in (".put(", ".patch(", ".delete(", ".request(", ".stream("):
        assert verb not in code, f"{verb} has no place in a read-only client"
    assert code.count(".post(") == 1, "the login is the only POST this client may make"
    assert "self._http.post(LOGIN_PATH" in code
    # Every other request is built in exactly one place, and that place says GET.
    assert code.count("build_request(") == 1
    assert re.search(r'self\._http\.build_request\(\s*"GET",\s*path', SOURCE)


def test_it_never_mentions_an_endpoint_outside_its_scope():
    """Documents are a paid feature that returns 402, and the terms bar uptime tracking of the API."""
    lowered = SOURCE.lower()
    paths = {line.split('"')[1] for line in SOURCE.splitlines() if '"/api/' in line or '"/auth/' in line}
    assert paths == {"/auth/login", "/api/files", "/api/files/replica-timestamp", "/api/users",
                     "/api/offices", "/api/stages"}, paths
    assert "/document" not in lowered and "/api/healthcheck" not in lowered


async def test_a_whole_run_of_calls_is_gets_and_one_login(fake):
    fake.files = [sale(), listing()]
    fake.users = [user()]
    async with skyslope.client(_creds()) as api:
        await api.replica_timestamp()
        await api.users()
        await api.offices()
        await api.stages()
        [i async for i in api.files(type="summary,listing,sale", createdAfter="2000-01-01T00:00:00Z")]
    writes = [(m, p) for m, p, _ in fake.calls if m != "GET"]
    assert writes == [("POST", "/auth/login")]


# ── the session ───────────────────────────────────────────────────────────────────────────────
async def test_a_session_is_reused_rather_than_logging_in_for_every_call(fake):
    async with skyslope.client(_creds()) as api:
        await api.replica_timestamp()
        await api.replica_timestamp()
    async with skyslope.client(_creds()) as api:     # a later run in the same process
        await api.replica_timestamp()
    assert fake.logins == 1


async def test_a_session_about_to_expire_is_replaced_before_it_is_used(fake):
    async with skyslope.client(_creds()) as api:
        await api.replica_timestamp()
        key = skyslope._session_key(_creds())
        token, _ = skyslope._SESSIONS[key]
        skyslope._SESSIONS[key] = (token, dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1))
        await api.replica_timestamp()
    assert fake.logins == 2


async def test_a_session_is_held_until_five_minutes_before_it_expires(fake):
    async with skyslope.client(_creds()) as api:
        usable_until = await api.login()
    remaining = usable_until - dt.datetime.now(dt.timezone.utc)
    assert dt.timedelta(minutes=114) < remaining <= dt.timedelta(minutes=115)


async def test_a_401_logs_in_once_and_retries(fake):
    async with skyslope.client(_creds()) as api:
        await api.replica_timestamp()
        fake.refuse_session_once = True
        out = await api.replica_timestamp()
    assert out["lagSeconds"] == 1.5
    assert fake.logins == 2


async def test_a_second_401_is_an_error_rather_than_a_loop(fake):
    fake.always_refuse_session = True
    async with skyslope.client(_creds()) as api:
        with pytest.raises(skyslope.SkySlopeError) as caught:
            await api.replica_timestamp()
    assert caught.value.status == 401
    assert fake.logins == 2


async def test_a_refused_login_says_so_as_its_own_error(fake):
    with pytest.raises(skyslope.SkySlopeAuthError) as caught:
        await skyslope.check_credentials(_creds(secret="wrong"))
    assert caught.value.status == 401 and caught.value.code == 1001


async def test_a_cached_session_is_not_shared_with_a_wrong_secret(fake):
    """Same access key, wrong secret: this has to be refused, not handed the good connection's session."""
    async with skyslope.client(_creds()) as api:
        await api.replica_timestamp()
    async with skyslope.client(_creds(secret="wrong")) as api:
        with pytest.raises(skyslope.SkySlopeAuthError):
            await api.replica_timestamp()


# ── what an error may say ─────────────────────────────────────────────────────────────────────
async def test_no_error_or_repr_carries_a_credential_or_a_response_body(fake):
    creds = skyslope.Creds(CLIENT_ID, "SECRET-A", "KEY-A", "SECRET-B", "https://skyslope.test")
    for text in (repr(creds), str(creds)):
        for secret in ("SECRET-A", "KEY-A", "SECRET-B", CLIENT_ID):
            assert secret not in text
    with pytest.raises(skyslope.SkySlopeError) as caught:
        await skyslope.check_credentials(creds)
    message = str(caught.value)
    assert "bad signature" not in message        # the body's prose can echo what was sent
    assert "SECRET" not in message and "KEY-A" not in message
    assert message == "SkySlope answered 401 (code 1001) on /auth/login"


# ── the rate limit ────────────────────────────────────────────────────────────────────────────
async def test_the_servers_count_wins_over_the_local_one():
    bucket = skyslope._Bucket()
    bucket.observe({"x-ratelimit-remaining": "42", "x-ratelimit-reset": "30"})
    await bucket.take()
    assert bucket.remaining == 41


async def test_it_waits_for_the_reset_once_the_floor_is_reached(monkeypatch):
    waits = []
    bucket = skyslope._Bucket()
    bucket.observe({"x-ratelimit-remaining": str(skyslope.RATE_FLOOR), "x-ratelimit-reset": "17"})

    async def record(seconds):
        waits.append(seconds)
        bucket.reset_at = 0.0       # the reset has now passed

    monkeypatch.setattr(skyslope.asyncio, "sleep", record)
    await bucket.take()
    assert len(waits) == 1 and 16 < waits[0] <= 17
    assert bucket.remaining is None, "after the reset the server's old number no longer applies"


async def test_the_local_count_is_the_fallback_before_any_header_arrives(monkeypatch):
    waits = []
    bucket = skyslope._Bucket()

    async def record(seconds):
        waits.append(seconds)
        bucket.stamps.clear()

    monkeypatch.setattr(skyslope.asyncio, "sleep", record)
    for _ in range(skyslope.RATE_LIMIT - skyslope.RATE_FLOOR):
        await bucket.take()
    assert waits == []
    await bucket.take()
    assert len(waits) == 1


def test_the_buckets_are_shared_per_client_id():
    assert skyslope._bucket("one") is skyslope._bucket("one")
    assert skyslope._bucket("one") is not skyslope._bucket("two")


@pytest.mark.parametrize("raw,low,high", [
    (None, 60, 60), ("17", 17, 17), ("garbage", 60, 60), ("9999", 60, 60), ("-4", 0, 0)])
def test_the_reset_header_is_read_whichever_way_it_is_written(raw, low, high):
    assert low <= skyslope._seconds_until_reset(raw) <= high


def test_the_reset_header_can_be_an_epoch_or_a_date():
    import time
    assert 28 < skyslope._seconds_until_reset(str(time.time() + 30)) <= 30
    soon = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=30)
    assert 25 < skyslope._seconds_until_reset(soon.strftime("%Y-%m-%dT%H:%M:%SZ")) <= 30


async def test_being_over_the_limit_waits_and_retries_rather_than_failing(fake):
    async with skyslope.client(_creds()) as api:
        await api.replica_timestamp()
        fake.throttle_next = 2
        out = await api.replica_timestamp()
    assert out["lagSeconds"] == 1.5


# ── the export ────────────────────────────────────────────────────────────────────────────────
async def test_the_export_arrives_item_by_item_with_the_summary_first(fake):
    fake.files = [sale(), listing(), sale()]
    async with skyslope.client(_creds()) as api:
        items = [i async for i in api.files(type="summary,listing,sale", createdAfter="2000-01-01T00:00:00Z")]
    assert [i["objectType"] for i in items] == ["summary", "sale", "listing", "sale"]
    assert items[0]["numberOfSales"] == 2 and items[0]["numberOfListings"] == 1
    assert isinstance(items[1]["salePrice"], float), "the row goes into JSON, so no Decimals"


async def test_the_spec_s_own_type_filter_would_have_lost_the_summary(fake):
    """`summary` is one of the values `type` takes, and the short-read check needs it."""
    fake.files = [sale()]
    async with skyslope.client(_creds()) as api:
        items = [i async for i in api.files(type="listing,sale", createdAfter="2000-01-01T00:00:00Z")]
    assert [i["objectType"] for i in items] == ["sale"]


async def test_a_stream_cut_off_mid_json_is_a_stream_error(fake):
    fake.files = [sale(), sale(), sale()]
    fake.truncate_next_exports = 1
    async with skyslope.client(_creds()) as api:
        with pytest.raises(skyslope.SkySlopeStreamError) as caught:
            [i async for i in api.files(type="summary,listing,sale")]
    assert "IncompleteJSONError" in str(caught.value) or "ended early" in str(caught.value)


async def test_a_connection_that_dies_mid_export_is_a_stream_error(fake):
    fake.files = [sale(), sale(), sale()]
    fake.drop_next_exports = 1
    async with skyslope.client(_creds()) as api:
        with pytest.raises(skyslope.SkySlopeStreamError):
            [i async for i in api.files(type="summary,listing,sale")]


# ── reading their dates ───────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("value", ["0001-01-01T00:00:00", "0001-01-01T00:00:00Z", "", None, "not a date", 7])
def test_values_that_are_not_a_moment_are_none(value):
    assert skyslope.parse_ts(value) is None


@pytest.mark.parametrize("value", ["2026-11-04T12:00:00", "2026-11-04T12:00:00Z", "2026-11-04T05:00:00-07:00",
                                   "2026-11-04T12:00:00.0000000"])
def test_a_moment_is_utc_however_it_was_written(value):
    assert skyslope.parse_ts(value) == dt.datetime(2026, 11, 4, 12, 0, 0, tzinfo=dt.timezone.utc)


def test_filters_are_sent_as_rfc3339_utc():
    moment = dt.datetime(2026, 11, 4, 5, 0, 0, tzinfo=dt.timezone(dt.timedelta(hours=-7)))
    assert skyslope.fmt_ts(moment) == "2026-11-04T12:00:00Z"


def test_the_marker_is_not_in_this_module():
    assert PII not in SOURCE


def test_httpx_is_the_only_network_library_here():
    assert "import requests" not in SOURCE and "urllib" not in SOURCE and isinstance(httpx.Timeout(1), httpx.Timeout)
