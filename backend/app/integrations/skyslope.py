"""SkySlope API client (READ ONLY). SKYSLOPE-SPEC.md, docs/skyslope-integration-spec.md.

READ ONLY IS A TEST, NOT A PROMISE. The one POST here is the login, and every other request goes
through one GET in `_open`. tests/test_skyslope_client.py fails if that stops being true.

AUTH. A login signs `ClientID:ClientSecret:Timestamp` with HMAC-SHA256, keyed with the user's Access
Secret, and returns a session that lasts two hours and cannot be refreshed. So the client logs in
again a little before expiry and once more on any 401. The security scheme in SkySlope's OpenAPI file
points at a placeholder URL: never generate auth from it. Sessions are held in memory with their
expiry, and neither they nor any credential appear in an error, a log line or a repr.

RATE LIMITS. 100 requests a minute per Client ID, shared by every key and therefore by every
workspace on this application. A local count cannot see the API process and the worker at once, so
the server's `x-ratelimit-remaining` wins whenever it has sent one; the local count is the fallback
for the first request. The over-limit status code is undocumented, so "over limit" means a 429, or
any failure that arrives with nothing remaining.

THE EXPORT. `GET /api/files` streams `{"value": [...]}`, with items flushed one at a time. It is
parsed as a stream and never held whole, because a team-wide key can return tens of thousands of
files. `type` has to name `summary` as well as the file kinds: it is one of that parameter's values,
and the first item's counts are how a short read is noticed. Asking for only `listing,sale`, as the
handed-over spec has it, risks losing them.

STILL TO CONFIRM ON THE FIRST LIVE CALL (nothing here has met the real API yet, and
scripts/skyslope_probe.py exists to check these):
  - the login body's key casing and the `SS` prefix's case;
  - whether the summary's counts respect the filters, which the sync relies on;
  - what `x-ratelimit-reset` holds, so it is read as seconds, an epoch or a date;
  - whether timestamps are really UTC. They carry no offset and are treated as UTC.

MUST NOT: add a healthcheck or any uptime or latency tracking. There is no health path in the OpenAPI
file, and §3(p) of the terms bars benchmarking. `replica-timestamp` is the first call of every run
and proves the same thing. Must not call a document endpoint either: they are paid and return 402.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import dataclasses
import datetime as dt
import hashlib
import hmac
import json
import random
import time
from typing import AsyncIterator

import httpx
import ijson

from ..config import settings

LOGIN_PATH = "/auth/login"
FILES_PATH = "/api/files"
# Sessions last two hours. Renewing early means a long run never starts a call on a dying session.
SESSION_LIFETIME = dt.timedelta(hours=2)
SESSION_MARGIN = dt.timedelta(minutes=5)

RATE_LIMIT = 100
RATE_WINDOW_S = 60.0
# Left unspent, so a manual sync in the API process does not collide with a job in the worker.
RATE_FLOOR = 5

RETRY_STATUS = {500, 502, 503, 504}
MAX_ATTEMPTS = 5
# `read` is the longest gap between bytes, not the length of the export: items are flushed one by
# one, so a stall of two minutes is a dead stream however large the file set is.
TIMEOUT = httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=30.0)

_UTC = dt.timezone.utc


@dataclasses.dataclass(frozen=True)
class Creds:
    client_id: str = dataclasses.field(repr=False)
    client_secret: str = dataclasses.field(repr=False)
    access_key: str = dataclasses.field(repr=False)
    access_secret: str = dataclasses.field(repr=False)
    base_url: str = ""


def configured() -> bool:
    """Whether this deployment holds the application's credentials. Without them the source is not
    offered and cannot be connected."""
    return bool(settings.SKYSLOPE_CLIENT_ID and settings.SKYSLOPE_CLIENT_SECRET)


def make_creds(access_key: str, access_secret: str) -> Creds:
    return Creds(client_id=settings.SKYSLOPE_CLIENT_ID, client_secret=settings.SKYSLOPE_CLIENT_SECRET,
                 access_key=access_key, access_secret=access_secret,
                 base_url=settings.SKYSLOPE_BASE_URL.rstrip("/"))


class SkySlopeError(RuntimeError):
    """A call that failed. Its text lands in `integration.last_error` and on the Settings page, so it
    is built from the status, SkySlope's `code` and the path: never from a request's headers or a
    response's body, which could echo a credential or a client's name."""

    def __init__(self, status: int | None, code=None, path: str = "", message: str = "") -> None:
        self.status, self.code, self.path = status, code, path
        detail = message or (f"SkySlope answered {status}" if status else "SkySlope could not be reached")
        if code not in (None, ""):
            detail += f" (code {code})"
        if path:
            detail += f" on {path}"
        super().__init__(detail)


class SkySlopeAuthError(SkySlopeError):
    """The login itself was refused: the key is wrong, revoked, or not this application's."""


class SkySlopeStreamError(RuntimeError):
    """An export that did not arrive whole. Rerunning the same call is always safe, because the rows
    are upserted and the watermark has not moved."""


def sign(client_id: str, client_secret: str, access_secret: str, timestamp: str) -> str:
    """The login signature: HMAC-SHA256 of `ClientID:ClientSecret:Timestamp`, keyed with the Access
    Secret, then Base64."""
    message = f"{client_id}:{client_secret}:{timestamp}".encode()
    digest = hmac.new(access_secret.encode(), message, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def fmt_ts(ts: dt.datetime) -> str:
    """RFC3339 in UTC, the only shape the login's timestamp and the date filters are sent in."""
    return ts.astimezone(_UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def login_request(creds: Creds, now: dt.datetime | None = None) -> tuple[dict, dict]:
    """(headers, body) for a login. The timestamp has to be within five minutes of real time."""
    stamp = fmt_ts(now or dt.datetime.now(_UTC))
    signature = sign(creds.client_id, creds.client_secret, creds.access_secret, stamp)
    return ({"Authorization": f"SS {creds.access_key}:{signature}", "Timestamp": stamp},
            {"clientID": creds.client_id, "clientSecret": creds.client_secret})


def parse_ts(value) -> dt.datetime | None:
    """An aware UTC datetime, or None for anything that does not mean a moment.

    `0001-01-01T00:00:00` is .NET's empty date and SkySlope's way of saying "not set". Timestamps
    with no offset are treated as UTC, which SkySlope has not confirmed.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.year <= 1:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_UTC)
    try:
        return parsed.astimezone(_UTC)
    except OverflowError:
        return None


# ── rate limit, per Client ID ─────────────────────────────────────────────────────────────────
class _Bucket:
    """Local count until the server says otherwise, then the server's numbers."""

    def __init__(self) -> None:
        self.stamps: list[float] = []
        self.remaining: int | None = None
        self.reset_at: float | None = None
        self.lock = asyncio.Lock()

    async def take(self) -> None:
        """Wait until a request can go without spending the floor."""
        while True:
            async with self.lock:
                now = time.monotonic()
                if self.reset_at is not None and now >= self.reset_at:
                    self.remaining, self.reset_at = None, None
                self.stamps = [t for t in self.stamps if now - t < RATE_WINDOW_S]
                if self.remaining is not None:
                    if self.remaining > RATE_FLOOR:
                        self.remaining -= 1
                        self.stamps.append(now)
                        return
                    wait = (self.reset_at or now + RATE_WINDOW_S) - now
                elif len(self.stamps) < RATE_LIMIT - RATE_FLOOR:
                    self.stamps.append(now)
                    return
                else:
                    wait = RATE_WINDOW_S - (now - min(self.stamps))
            await asyncio.sleep(min(max(wait, 0.05), RATE_WINDOW_S + 5))

    def observe(self, headers) -> None:
        raw = headers.get("x-ratelimit-remaining")
        if raw is None:
            return
        try:
            self.remaining = int(raw)
        except (TypeError, ValueError):
            return
        self.reset_at = time.monotonic() + _seconds_until_reset(headers.get("x-ratelimit-reset"))

    def limited(self) -> None:
        """Spent, whether the server said so in a header or only in a status."""
        self.remaining = 0
        if self.reset_at is None:
            self.reset_at = time.monotonic() + RATE_WINDOW_S


def _seconds_until_reset(raw) -> float:
    """`x-ratelimit-reset` could be seconds from now, an epoch or a date, and SkySlope does not say
    which. All three are read, and the answer is held inside the limit's own window so a surprising
    value can only cost a minute."""
    if raw is None:
        return RATE_WINDOW_S
    try:
        number = float(raw)
    except (TypeError, ValueError):
        at = parse_ts(raw)
        seconds = (at - dt.datetime.now(_UTC)).total_seconds() if at else RATE_WINDOW_S
    else:
        seconds = number - time.time() if number > 1e9 else number
    return min(max(seconds, 0.0), RATE_WINDOW_S)


_BUCKETS: dict[str, _Bucket] = {}
# Keyed by a hash so the map holds no credential even as a key. Value: (session, usable until).
_SESSIONS: dict[str, tuple[str, dt.datetime]] = {}


def _bucket(client_id: str) -> _Bucket:
    b = _BUCKETS.get(client_id)
    if b is None:
        b = _BUCKETS[client_id] = _Bucket()
    return b


def _session_key(creds: Creds) -> str:
    # The secret is part of it: a session belongs to exact credentials. Without it, a connection holding
    # the right key and the wrong secret rides on whichever connection logged in properly, even in
    # another workspace, and never finds out its secret is wrong.
    identity = f"{creds.base_url}|{creds.client_id}|{creds.access_key}|{creds.access_secret}"
    return hashlib.sha256(identity.encode()).hexdigest()


def _backoff(attempt: int) -> float:
    return min(2 ** attempt, 30) + random.uniform(0, 0.5)


def _field(body, name: str):
    """A response field whatever its casing, at the top level or inside `value`. The login's casing
    is on the list of things to confirm against the real API."""
    if not isinstance(body, dict):
        return None
    lowered = {str(k).lower(): v for k, v in body.items()}
    if name in lowered:
        return lowered[name]
    return _field(lowered.get("value"), name)


def _value(body):
    """Most responses wrap their payload as `{"value": ..., "warnings": ..., "links": ...}`."""
    return body["value"] if isinstance(body, dict) and "value" in body else body


def _error_from(status: int, content: bytes, path: str, cls=SkySlopeError) -> SkySlopeError:
    """SkySlope errors are `{"errors": [...], "code": ...}`. Switch on `code`; `errors[]` is prose
    that can echo what was sent, so it is not repeated."""
    code = None
    with contextlib.suppress(ValueError, TypeError, AttributeError):
        code = json.loads(content).get("code")
    return cls(status, code=code, path=path)


class _AsyncBytes:
    """The small file-like object ijson reads, over httpx's byte iterator."""

    def __init__(self, chunks) -> None:
        self._chunks = chunks.__aiter__()
        self._buf = b""

    async def read(self, n: int = -1) -> bytes:
        while not self._buf:
            try:
                self._buf = await self._chunks.__anext__()
            except StopAsyncIteration:
                return b""
        if n is None or n < 0 or n >= len(self._buf):
            out, self._buf = self._buf, b""
        else:
            out, self._buf = self._buf[:n], self._buf[n:]
        return out


class SkySlopeClient:
    def __init__(self, creds: Creds, http: httpx.AsyncClient) -> None:
        self._creds = creds
        self._http = http
        self._limit = _bucket(creds.client_id)

    # ── session ───────────────────────────────────────────────────────────────────────────────
    async def login(self) -> dt.datetime:
        """Log in now and return when the session stops being usable. Refused means refused: a 401 or
        403 here is about the key, and raises SkySlopeAuthError."""
        for attempt in range(MAX_ATTEMPTS):
            await self._limit.take()
            headers, body = login_request(self._creds)
            response = await self._http.post(LOGIN_PATH, headers=headers, json=body)
            self._limit.observe(response.headers)
            status = response.status_code
            if status in (401, 403) and self._limit.remaining != 0:
                raise _error_from(status, response.content, LOGIN_PATH, SkySlopeAuthError)
            if status == 429 or (status >= 400 and self._limit.remaining == 0):
                self._limit.limited()
                continue
            if status in RETRY_STATUS and attempt < MAX_ATTEMPTS - 1:
                await asyncio.sleep(_backoff(attempt))
                continue
            if status >= 400:
                raise _error_from(status, response.content, LOGIN_PATH)
            try:
                data = response.json()
            except ValueError:
                raise SkySlopeError(status, path=LOGIN_PATH, message="SkySlope's login answer was not JSON")
            session = _field(data, "session")
            if not session:
                raise SkySlopeError(status, path=LOGIN_PATH, message="SkySlope's login answer had no session")
            expires = parse_ts(_field(data, "expiration")) or dt.datetime.now(_UTC) + SESSION_LIFETIME
            usable_until = expires - SESSION_MARGIN
            _SESSIONS[_session_key(self._creds)] = (session, usable_until)
            return usable_until
        raise SkySlopeError(429, path=LOGIN_PATH, message="SkySlope kept refusing the login as over its limit")

    async def _session(self) -> str:
        held = _SESSIONS.get(_session_key(self._creds))
        if held is None or dt.datetime.now(_UTC) >= held[1]:
            await self.login()
            held = _SESSIONS[_session_key(self._creds)]
        return held[0]

    def _forget_session(self) -> None:
        _SESSIONS.pop(_session_key(self._creds), None)

    # ── reading ───────────────────────────────────────────────────────────────────────────────
    async def _open(self, path: str, params: dict | None = None) -> httpx.Response:
        """THE ONE DATA REQUEST. Every read in this client comes through this GET, returned open so an
        export can be streamed. The caller closes it.

        Logs in again once on a 401, waits out the limit, and retries a server error. An export that
        drops AFTER this returns is not retried here: see SkySlopeStreamError.
        """
        relogged = False
        status = None
        for attempt in range(MAX_ATTEMPTS):
            session = await self._session()
            await self._limit.take()
            request = self._http.build_request(
                "GET", path, params=params, headers={"Session": session, "Accept": "application/json"})
            try:
                response = await self._http.send(request, stream=True)
            except httpx.TransportError as e:
                if attempt == MAX_ATTEMPTS - 1:
                    raise SkySlopeError(None, path=path, message=f"SkySlope could not be reached ({type(e).__name__})") from e
                await asyncio.sleep(_backoff(attempt))
                continue
            self._limit.observe(response.headers)
            status = response.status_code
            if status < 400:
                return response
            content = await response.aread()
            await response.aclose()
            if status == 401 and not relogged:
                relogged = True
                self._forget_session()
                continue
            if status == 429 or self._limit.remaining == 0:
                self._limit.limited()
                continue
            if status in RETRY_STATUS and attempt < MAX_ATTEMPTS - 1:
                await asyncio.sleep(_backoff(attempt))
                continue
            raise _error_from(status, content, path)
        raise SkySlopeError(status, path=path, message=f"SkySlope kept answering {status}")

    async def _get_json(self, path: str, params: dict | None = None):
        response = await self._open(path, params)
        try:
            content = await response.aread()
        except httpx.TransportError as e:
            raise SkySlopeError(None, path=path, message=f"SkySlope's answer was cut off ({type(e).__name__})") from e
        finally:
            await response.aclose()
        try:
            return json.loads(content)
        except ValueError as e:
            raise SkySlopeError(response.status_code, path=path, message="SkySlope's answer was not JSON") from e

    async def replica_timestamp(self) -> dict:
        """`replicaLastCommitTimeUtc`, `utcNow` and `lagSeconds`. Read BEFORE an export: the watermark
        has to be a moment the export is known to include."""
        return _value(await self._get_json("/api/files/replica-timestamp"))

    async def users(self, *, include_deactivated: bool = True) -> list[dict]:
        """Everybody this key can see. There is no change filter, and a deactivated person's email
        carries `_deleted` or `_deactivated`."""
        body = _value(await self._get_json(
            "/api/users", {"includeDeactivated": "true" if include_deactivated else "false"}))
        return (body or {}).get("users") or []

    async def offices(self) -> list[dict]:
        return (_value(await self._get_json("/api/offices")) or {}).get("offices") or []

    async def stages(self) -> list[dict]:
        return (_value(await self._get_json("/api/stages")) or {}).get("stages") or []

    async def files(self, **params) -> AsyncIterator[dict]:
        """The Bulk Export, one item at a time, the summary first. Raises SkySlopeStreamError if the
        stream breaks or the JSON stops short; nothing yielded before that is wrong."""
        response = await self._open(FILES_PATH, params)
        try:
            items = ijson.items_async(_AsyncBytes(response.aiter_bytes()), "value.item", use_float=True)
            async for item in items:
                yield item
        except (httpx.TransportError, ijson.JSONError) as e:
            raise SkySlopeStreamError(f"The export stream ended early ({type(e).__name__})") from e
        finally:
            await response.aclose()


def _transport():
    """Where requests go. None is the real network; the tests replace this with a fake SkySlope."""
    return None


@contextlib.asynccontextmanager
async def client(creds: Creds) -> AsyncIterator[SkySlopeClient]:
    async with httpx.AsyncClient(base_url=creds.base_url, timeout=TIMEOUT,
                                 transport=_transport()) as http:
        yield SkySlopeClient(creds, http)


async def check_credentials(creds: Creds) -> None:
    """Log in once, for a person who just pasted a key. Raises SkySlopeAuthError if it is refused."""
    async with client(creds) as api:
        await api.login()
