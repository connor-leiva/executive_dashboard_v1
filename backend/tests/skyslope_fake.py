"""Just enough of SkySlope to run the real client and sync against, over httpx.

SHAPED FROM THE LIVE OPENAPI FILE (version 2.2.1-preview.0.27): every property of `BulkExportSale`,
`BulkExportListing`, `BulkExportSummary` and `UserDTO2` is present, in the shape that file gives it.
Nothing here has met the real API, and the unknowns are listed in app/integrations/skyslope.py.

THE MARKER. Every field the sync must never store holds `PII`, including ones nested in objects it
partly keeps. tests/test_skyslope_sync.py searches every stored row for it, so a new field that
leaks fails the suite by name instead of landing in somebody's database.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid

import httpx
import pytest

from app.config import settings
from app.integrations import skyslope

PII = "NEVER-STORE-THIS"
UTC = dt.timezone.utc
CLIENT_ID, CLIENT_SECRET = "client-id", "client-secret"


def guid() -> str:
    return str(uuid.uuid4())


def _contact() -> dict:
    return {"firstName": PII, "lastName": PII, "email": PII, "phone": PII, "company": PII}


def _people() -> list[dict]:
    return [{"firstName": PII, "lastName": PII, "email": PII, "phone": PII,
             "streetAddress": PII, "city": PII, "state": PII, "zip": PII}]


def _property(**over) -> dict:
    return {"areasqft": PII, "city": "Boise", "county": PII, "direction": PII, "realPropertySubtypeId": 3,
            "realPropertyTypeId": 2, "state": "ID", "streetAddress": PII, "streetNumber": PII, "unit": PII,
            "yearBuilt": 1987, "zip": "83702", **over}


def _commission() -> dict:
    return {"commissionBreakdown": PII, "listingCommissionAmount": 9000.0, "listingCommissionPercent": 3.0,
            "officeGrossCommission": 18000.0, "referralAmount": 0.0, "referralAmountPercent": 0.0,
            "saleCommissionPercent": 3.0, "salesCommissionAmount": 9000.0, "transCoordinatorFee": 395.0,
            "adminBrokerageComp": 150.0}


def _stamp(at: dt.datetime) -> str:
    """No offset, as the API sends them. Seven fractional digits, as .NET writes them."""
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.1234567")


def sale(*, sale_guid=None, agent=None, status="Pending", status_id=20, modified=None, created=None, **over) -> dict:
    now = dt.datetime.now(UTC)
    return {
        "objectType": "sale", "saleGuid": sale_guid or guid(), "listingGuid": None, "agentGuid": agent or guid(),
        "createdByGuid": PII, "mlsNumber": "MLS-1", "escrowNumber": PII, "statusId": status_id, "status": status,
        "officeId": 7, "officeGuid": "AAAAAAAA-0000-0000-0000-00000000000A", "checklistType": PII,
        "escrowClosingDate": "2026-12-15T00:00:00", "actualClosingDate": "0001-01-01T00:00:00",
        "contractAcceptedDate": "2026-11-01T00:00:00", "modifiedOn": _stamp(modified or now), "source": PII,
        "otherSource": PII, "deadDate": None, "dealType": "Buyer", "listingPrice": 500000.0, "salePrice": 485000.5,
        "isOfficeLead": False, "coBrokerCompany": PII, "email": PII, "property": _property(),
        "commission": _commission(), "sellers": _people(), "buyers": _people(), "titleContact": _contact(),
        "escrowContact": _contact(), "attorneyContact": [_contact()], "lenderContact": _contact(),
        "otherSideAgentContact": _contact(), "homeWarrantyContact": _contact(), "miscContact": _contact(),
        "commercialLease": {"tenant": PII}, "commissionBreakdowns": [{"name": "Agent commission", "details": PII, "amount": 9000.0}],
        "coAgentGuids": [], "commissionSplits": [], "stage": {"id": 1, "name": "In Escrow"},
        "commissionReferral": {"contact": _contact(), "amount": 0.0}, "agent": [_contact()], "coAgents": [_contact()],
        "transactionCoordinators": [_contact()], "earnestMoneyDeposit": {"holder": PII}, "customFields": {"note": PII},
        "createdOn": _stamp(created or now - dt.timedelta(days=30)), "checklist": {"item": PII},
        **over}


def listing(*, listing_guid=None, agent=None, status="Active", modified=None, created=None, **over) -> dict:
    now = dt.datetime.now(UTC)
    return {
        "objectType": "listing", "listingGuid": listing_guid or guid(), "agentGuid": agent or guid(),
        "createdByGuid": PII, "coListingAgentGuid": None, "referringAgentGuid": PII, "mlsNumber": "MLS-2",
        "status": status, "modifiedOn": _stamp(modified or now), "officeId": 7,
        "officeGuid": "AAAAAAAA-0000-0000-0000-00000000000A", "checklistType": PII, "type": PII,
        "listingDate": "2026-10-01T00:00:00", "source": PII, "otherSource": PII, "fileId": PII,
        "commission": _commission(), "expirationDate": "2027-04-01T00:00:00", "deadDate": None, "company": PII,
        "listingPrice": 750000.0, "email": PII, "property": _property(city="Eagle"), "commercialLease": {"tenant": PII},
        "sellers": _people(), "homeWarrantyContact": _contact(), "miscContact": _contact(),
        "stage": {"id": 2, "name": "Coming Soon"}, "coAgentGuids": [], "agent": [_contact()], "coAgents": [_contact()],
        "transactionCoordinators": [_contact()], "customFields": {"note": PII},
        "createdOn": _stamp(created or now - dt.timedelta(days=30)), "checklist": {"item": PII},
        **over}


def user(*, user_guid=None, first="Ann", last="Agent", email="ann@example.test", user_type="Agent", offices=None) -> dict:
    return {"userGuid": user_guid or guid(), "contactId": 42, "oktaUserID": PII, "firstName": first, "lastName": last,
            "email": email, "userType": user_type, "publicId": PII,
            "officeGuids": offices if offices is not None else ["AAAAAAAA-0000-0000-0000-00000000000A"],
            "streetNumber": PII, "streetName": PII, "zipCode": PII, "city": PII, "state": PII,
            "phone": PII, "alternatePhone": PII}


class _Broken(httpx.AsyncByteStream):
    """Sends part of a body, then the connection dies."""

    def __init__(self, body: bytes) -> None:
        self.body = body

    async def __aiter__(self):
        yield self.body
        raise httpx.ReadError("connection reset")

    async def aclose(self) -> None:
        pass


class FakeSkySlope:
    def __init__(self, files=(), users=()) -> None:
        self.files = list(files)
        self.users = list(users)
        self.calls: list[tuple[str, str, dict]] = []
        self.logins = 0
        self.valid = {"key-1": "secret-1", "key-2": "secret-2"}
        self.refuse_session_once = False
        self.always_refuse_session = False
        self.drop_next_exports = 0       # transport dies partway through
        self.truncate_next_exports = 0   # the body stops mid-JSON
        self.short_next_exports = 0      # a clean close with fewer files than the summary promised
        self.omit_summary = False
        self.users_status = 200
        self.throttle_next = 0
        self.replica = dt.datetime(2026, 11, 4, 12, 0, 0, tzinfo=UTC)
        self.tick_replica_on_export = dt.timedelta(0)
        self.only_for_key: dict[str, set] = {}    # key -> guids that key may see
        self.session_owner: dict[str, str] = {}

    # ── routing ──────────────────────────────────────────────────────────────────────────────
    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, dict(request.url.params)
        self.calls.append((request.method, path, params))
        if request.method == "POST" and path == skyslope.LOGIN_PATH:
            return self._login(request)
        if request.method != "GET":
            return httpx.Response(405, json={"errors": ["read only"], "code": 405})
        if self.throttle_next:
            self.throttle_next -= 1
            return httpx.Response(429, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "0"},
                                  json={"errors": ["slow down"], "code": 429})
        session = request.headers.get("Session") or ""
        if self.always_refuse_session or self.refuse_session_once or session not in self.session_owner:
            self.refuse_session_once = False
            return httpx.Response(401, json={"errors": ["expired"], "code": 401})
        if path == "/api/files/replica-timestamp":
            return httpx.Response(200, json={"replicaLastCommitTimeUtc": _iso(self.replica), "utcNow": _iso(self.replica),
                                             "lagSeconds": 1.5})
        if path == skyslope.FILES_PATH:
            return self._export(params, self.session_owner[session])
        if path == "/api/users":
            if self.users_status != 200:
                return httpx.Response(self.users_status, json={"errors": ["no"], "code": 77})
            return httpx.Response(200, json={"value": {"users": self.users}, "warnings": [], "links": []})
        if path == "/api/offices":
            return httpx.Response(200, json={"value": {"offices": [{"officeId": 7, "officeGuid": "o", "officeName": "ID"}]}})
        if path == "/api/stages":
            return httpx.Response(200, json={"value": {"stages": [{"id": 1, "name": "In Escrow", "isDefault": True}]}})
        return httpx.Response(404, json={"errors": ["no such path"], "code": 404})

    def _login(self, request: httpx.Request) -> httpx.Response:
        scheme, _, rest = (request.headers.get("Authorization") or "").partition(" ")
        access_key, _, signature = rest.partition(":")
        stamp = request.headers.get("Timestamp") or ""
        secret = self.valid.get(access_key)
        body = json.loads(request.content)
        good = (scheme == "SS" and secret is not None
                and body == {"clientID": CLIENT_ID, "clientSecret": CLIENT_SECRET}
                and signature == skyslope.sign(CLIENT_ID, CLIENT_SECRET, secret, stamp))
        if not good:
            return httpx.Response(401, json={"errors": ["bad signature"], "code": 1001})
        self.logins += 1
        token = f"session-{self.logins}"
        self.session_owner[token] = access_key
        return httpx.Response(200, json={"Session": token,
                                         "Expiration": _iso(dt.datetime.now(UTC) + dt.timedelta(hours=2))})

    # ── the export ───────────────────────────────────────────────────────────────────────────
    def _export(self, params: dict, key: str) -> httpx.Response:
        self.replica += self.tick_replica_on_export
        chosen = [f for f in self.files if self._matches(f, params, key)]
        summary = {"objectType": "summary",
                   "numberOfSales": sum(f["objectType"] == "sale" for f in chosen),
                   "numberOfListings": sum(f["objectType"] == "listing" for f in chosen),
                   "numberOfUsers": 0, "createdOn": _iso(self.replica)}
        wanted = (params.get("type") or "all").split(",")
        items = ([summary] if ("summary" in wanted or "all" in wanted) and not self.omit_summary else [])
        if self.short_next_exports:
            self.short_next_exports -= 1
            items += chosen[:-1]
        else:
            items += chosen
        body = '{"value":[' + ",\n".join(json.dumps(i) for i in items) + "]}"
        if self.truncate_next_exports:
            self.truncate_next_exports -= 1
            return httpx.Response(200, content=body[: max(40, len(body) // 2)].encode())
        if self.drop_next_exports:
            self.drop_next_exports -= 1
            return httpx.Response(200, stream=_Broken(body[: max(40, len(body) // 2)].encode()))
        return httpx.Response(200, content=body.encode())

    def _matches(self, f: dict, params: dict, key: str) -> bool:
        file_guid = f.get("saleGuid") or f.get("listingGuid")
        allowed = self.only_for_key.get(key)
        if allowed is not None and file_guid not in allowed:
            return False
        if params.get("modifiedAfter"):
            if skyslope.parse_ts(f["modifiedOn"]) <= skyslope.parse_ts(params["modifiedAfter"]):
                return False
        if params.get("createdAfter"):
            if skyslope.parse_ts(f["createdOn"]) <= skyslope.parse_ts(params["createdAfter"]):
                return False
        if params.get("status"):
            words = {w.lower() for w in params["status"].split(",")}
            if str(f.get("status")).lower() not in words:
                return False
        return True

    # ── asking what happened ─────────────────────────────────────────────────────────────────
    def exports(self) -> list[dict]:
        return [p for m, path, p in self.calls if path == skyslope.FILES_PATH]


def _iso(at: dt.datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def fake(monkeypatch):
    """A fake SkySlope behind the real client, with this deployment's application credentials set."""
    server = FakeSkySlope()
    monkeypatch.setattr(settings, "SKYSLOPE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(settings, "SKYSLOPE_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setattr(settings, "SKYSLOPE_BASE_URL", "https://skyslope.test")
    monkeypatch.setattr(skyslope, "_transport", lambda: httpx.MockTransport(server))
    # Sessions and limits are process-wide by design, which is exactly what a test must not inherit.
    skyslope._SESSIONS.clear()
    skyslope._BUCKETS.clear()

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(skyslope.asyncio, "sleep", no_wait)
    yield server
    skyslope._SESSIONS.clear()
    skyslope._BUCKETS.clear()
