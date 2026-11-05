"""Sample SkySlope data, for showing a workspace what the integration will look like before a real
key exists. Invented from end to end: no person, address or deal here came from SkySlope or from
anybody's real business.

IT GOES THROUGH THE REAL CODE. `SampleApi` stands in for the HTTP client and nothing else, so the
sample is read by the same sync, allow-list, status map and projection that a live key will use. What
the demo shows is the real path, not a mock-up of it.

IT IS ALWAYS MARKED. A sample connection is an ordinary `integration` row whose config says
`"sample": true`, and every surface that shows SkySlope-derived numbers reads that flag
(`is_sample_tenant`) and says so. Connecting a real key purges the sample first
(routers/integrations.py), so the two never mix, and `scripts/skyslope_sample.py --remove` takes it out
by hand.

IT IS STABLE. The data is a pure function of the anchor date stored on the connection, so a second
sync of the same sample changes nothing, exactly as a second sync of a real key must not.
"""
from __future__ import annotations

import datetime as dt
import random
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Integration

PROVIDER = "skyslope"
LABEL = "Sample data"
_NS = uuid.UUID("5a3c1e2b-9d47-4f6a-8b1e-0c2d3e4f5a6b")
OFFICE = "b1a2c3d4-0000-4000-8000-00000000a001"   # "ID - Activated Agents", invented

# Invented people. The addresses are example.com, which exists so that nobody receives mail meant
# for a demo.
_AGENTS = [
    ("Avery", "Collins"), ("Jordan", "Reyes"), ("Taylor", "Brooks"), ("Morgan", "Patel"),
    ("Casey", "Nguyen"), ("Riley", "Thompson"), ("Quinn", "Harper"), ("Drew", "Martinez"),
    ("Skyler", "Bennett"), ("Jamie", "Foster"), ("Parker", "Lee"), ("Reese", "Coleman"),
    ("Emerson", "Hayes"), ("Rowan", "Price"),
]
# How busy each is, roughly: deals a year. The last two do nothing recent, so "Not producing" has
# somebody in it, and the one marked deactivated left the team.
_PACE = [14, 11, 10, 9, 8, 7, 6, 6, 5, 4, 3, 2, 0, 0]
_DEACTIVATED = {13}
_PLACES = [("Boise", "83702"), ("Boise", "83706"), ("Meridian", "83642"), ("Meridian", "83646"),
           ("Nampa", "83651"), ("Eagle", "83616"), ("Caldwell", "83605"), ("Kuna", "83634"),
           ("Star", "83669")]


def _guid(*parts) -> str:
    return str(uuid.uuid5(_NS, "|".join(str(p) for p in parts)))


def _stamp(at: dt.datetime) -> str:
    """As the API sends them: no offset, seven fractional digits."""
    return at.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.0000000")


def _day(at: dt.date) -> str:
    return f"{at.isoformat()}T00:00:00"


def _price(rng: random.Random) -> float:
    return float(round(rng.triangular(310_000, 1_150_000, 520_000), -3))


def _commission(price: float, rng: random.Random) -> dict:
    pct = rng.choice((2.5, 2.75, 3.0))
    side = round(price * pct / 100, 2)
    return {"listingCommissionAmount": side, "listingCommissionPercent": pct, "officeGrossCommission": side,
            "referralAmount": 0.0, "referralAmountPercent": 0.0, "saleCommissionPercent": pct,
            "salesCommissionAmount": side, "transCoordinatorFee": 395.0, "adminBrokerageComp": 150.0}


def build(anchor: dt.date) -> tuple[list[dict], list[dict]]:
    """(files, users) for one anchor date. Deterministic: the same anchor gives the same output."""
    rng = random.Random(f"skyslope-sample-{anchor.isoformat()}")
    agents = [_guid("agent", i) for i in range(len(_AGENTS))]
    users = [{"userGuid": agents[i], "firstName": first, "lastName": last,
              "email": f"{first.lower()}.{last.lower()}@example.com" + ("_deactivated" if i in _DEACTIVATED else ""),
              "userType": "Agent", "officeGuids": [OFFICE]}
             for i, (first, last) in enumerate(_AGENTS)]
    users.append({"userGuid": _guid("broker"), "firstName": "Sample", "lastName": "Broker",
                  "email": "broker@example.com", "userType": "Broker", "officeGuids": [OFFICE]})

    files: list[dict] = []
    created = dt.datetime.combine(anchor, dt.time(9), dt.timezone.utc)

    def place():
        city, zipcode = rng.choice(_PLACES)
        return {"city": city, "state": "ID", "zip": zipcode}

    def base(kind: str, n: int, agent: str, *, days_ago: int) -> dict:
        made = created - dt.timedelta(days=days_ago + 20)
        return {"objectType": kind, "agentGuid": agent, "officeGuid": OFFICE, "mlsNumber": f"SMP-{kind[0].upper()}{n:04d}",
                "createdOn": _stamp(made), "modifiedOn": _stamp(created - dt.timedelta(days=max(days_ago - 1, 0))),
                "property": place(), "coAgentGuids": [], "commissionSplits": []}

    n = 0
    for i, agent in enumerate(agents):
        for k in range(_PACE[i]):
            n += 1
            # Spread over the past year, a little heavier in spring and summer. The busiest five each
            # close one inside the current month, so month-to-date, the dashboard's default view,
            # is never empty on the day somebody looks at it.
            ago = int(rng.triangular(3, 365, 120))
            if k == 0 and i < 5:
                ago = rng.randint(0, max(anchor.day - 1, 0))
            price = _price(rng)
            accepted = anchor - dt.timedelta(days=ago + rng.randint(25, 45))
            closed = anchor - dt.timedelta(days=ago)
            side = rng.choice(("Buyer", "Buyer", "Seller", "Seller", "Dual"))
            sale = {**base("sale", n, agent, days_ago=ago), "saleGuid": _guid("sale", i, k),
                    "status": "Closed", "statusId": 30, "dealType": side, "salePrice": price,
                    "listingPrice": round(price * rng.uniform(1.0, 1.04), -3),
                    "contractAcceptedDate": _day(accepted), "escrowClosingDate": _day(closed),
                    "actualClosingDate": _day(closed), "deadDate": None,
                    "stage": {"name": "Closed"}, "commission": _commission(price, rng)}
            if side == "Seller" and rng.random() < 0.7:
                listing_guid = _guid("converted", i, k)
                sale["listingGuid"] = listing_guid
                files.append({**base("listing", n, agent, days_ago=ago + 40), "listingGuid": listing_guid,
                              "status": "Transaction", "listingDate": _day(accepted - dt.timedelta(days=rng.randint(10, 60))),
                              "expirationDate": _day(accepted + dt.timedelta(days=150)), "deadDate": None,
                              "listingPrice": sale["listingPrice"], "stage": {"name": "Under Contract"},
                              "commission": sale["commission"]})
            if rng.random() < 0.12:
                partner = rng.choice([a for a in agents if a != agent])
                sale["coAgentGuids"] = [partner]
                sale["commissionSplits"] = [{"agentGuid": agent, "amount": None, "percentage": 50.0},
                                            {"agentGuid": partner, "amount": None, "percentage": 50.0}]
            files.append(sale)

    # The open pipeline: a handful under contract (two past their closing date), some pre-contract,
    # a few that fell through, and one SkySlope already marked Expired.
    active = [a for i, a in enumerate(agents) if _PACE[i] >= 3]
    pipeline = ([("Pending", 20, d) for d in (6, 11, 15, 19, 24, 31, 38, 45)] + [("Under Contract", 20, d) for d in (9, 27)]
                + [("Pending", 20, -4), ("Pending", 20, -9)] + [("Pre-contract", 10, d) for d in (40, 55)]
                + [("Expired", 40, -21)] + [("Canceled", 50, d) for d in (-12, -48, -95)])
    for j, (status, status_id, close_in) in enumerate(pipeline):
        n += 1
        agent = active[j % len(active)]
        price = _price(rng)
        accepted = anchor - dt.timedelta(days=rng.randint(4, 40) if close_in > 0 else 40 - close_in)
        if j < 3:
            # A few accepted this month, so New contracts has something in month-to-date.
            accepted = anchor - dt.timedelta(days=rng.randint(0, max(anchor.day - 1, 0)))
        sale = {**base("sale", n, agent, days_ago=max(-close_in, 1)), "saleGuid": _guid("open", j), "status": status,
                "statusId": status_id, "dealType": rng.choice(("Buyer", "Seller")), "salePrice": price,
                "listingPrice": round(price * 1.02, -3), "contractAcceptedDate": _day(accepted),
                "escrowClosingDate": _day(anchor + dt.timedelta(days=close_in)), "actualClosingDate": "0001-01-01T00:00:00",
                "deadDate": _day(anchor + dt.timedelta(days=close_in)) if status == "Canceled" else None,
                "stage": {"name": "In Escrow" if status in ("Pending", "Under Contract") else status},
                "commission": _commission(price, rng)}
        files.append(sale)

    listings = ([("Active", d) for d in (2, 5, 9, 14, 18, 23, 30, 37, 44, 52, 61, 75)] + [("Incomplete", 1), ("Incomplete", 3)]
                + [("Expired", 130), ("Canceled/Pend", 70), ("Canceled/App", 88)])
    for j, (status, listed_ago) in enumerate(listings):
        n += 1
        agent = active[(j * 3) % len(active)]
        price = _price(rng)
        listed = anchor - dt.timedelta(days=listed_ago)
        item = {**base("listing", n, agent, days_ago=listed_ago), "listingGuid": _guid("listing", j), "status": status,
                "listingDate": _day(listed), "expirationDate": _day(listed + dt.timedelta(days=180)),
                "deadDate": _day(listed + dt.timedelta(days=45)) if status.startswith("Canceled") else None,
                "listingPrice": price, "stage": {"name": "Active" if status == "Active" else status},
                "commission": _commission(price, rng)}
        if j % 5 == 0:
            item["coListingAgentGuid"] = rng.choice([a for a in agents if a != agent])
        files.append(item)
    return files, users


class SampleApi:
    """The parts of SkySlopeClient the sync calls. No network, ever."""

    def __init__(self, anchor: dt.date) -> None:
        self.anchor = anchor
        self.files_, self.users_ = build(anchor)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def replica_timestamp(self) -> dict:
        at = dt.datetime.combine(self.anchor, dt.time(12), dt.timezone.utc)
        return {"replicaLastCommitTimeUtc": _stamp(at), "lagSeconds": 0}

    async def files(self, **_filters):
        """Every file on every pull. Upserts make that correct, and unchanged content is what lets a
        second sync report zero changes."""
        yield {"objectType": "summary", "numberOfListings": sum(f["objectType"] == "listing" for f in self.files_),
               "numberOfSales": sum(f["objectType"] == "sale" for f in self.files_)}
        for item in self.files_:
            yield item

    async def users(self) -> list[dict]:
        return list(self.users_)


def is_sample(integ: Integration) -> bool:
    return bool((integ.config or {}).get("sample"))


def anchor_of(integ: Integration) -> dt.date:
    raw = (integ.config or {}).get("sample_anchor")
    try:
        return dt.date.fromisoformat(raw)
    except (TypeError, ValueError):
        return dt.datetime.now(dt.timezone.utc).date()


async def is_sample_tenant(s: AsyncSession, tenant_id) -> bool:
    """Whether this workspace's SkySlope figures are invented. Every surface showing them asks."""
    rows = (await s.execute(select(Integration.config).where(
        Integration.tenant_id == tenant_id, Integration.provider == PROVIDER,
        Integration.status.in_(("connected", "error"))))).scalars().all()
    return any((c or {}).get("sample") for c in rows)
