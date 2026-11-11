"""Header-only ledger rows get their lines (and vendor ids) back, so lead-source spend can match.

The production shape: Utah Life's realm was first backfilled before `book_txn_line` (0096) and
`book_txn.vendor_qbo_id` (0104) existed, so its older Realtor.com Purchases are stored as bare
headers. CDC only re-sends what somebody edits, and an unchanged monthly card charge never comes
back, so the Realtor.com rules (line account 365, header vendor 962 or 1568) had nothing to match
and the cohort report showed $0 spend on every month.

QuickBooks is stubbed; nothing here reaches it. The payloads are Purchases in the live shape --
the Amex on the header, the expense account on the line -- with the live account and vendor ids
and amounts only as large as each assertion needs.
"""
import datetime as dt
import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select

from app.db import SessionLocal
from app.integrations import qbo
from app.main import app
from app.models import Base, BookTxn, BookTxnLine, Business, Integration, LeadSourceGroup, Tenant, User
from app.security import hash_pw, make_token
from app.services import books_sync
from app.services import lead_source_spend as spend
from app.services.lead_sources import seed_ulrg

TRANSPORT = ASGITransport(app=app)
START = "2026-02-01"
AMEX = {"value": "1150040000", "name": "Amex"}


def _line(amount, account, name):
    return {"Amount": amount, "DetailType": "AccountBasedExpenseLineDetail",
            "AccountBasedExpenseLineDetail": {"AccountRef": {"value": account, "name": name}}}


def _purchase(qid, day, amount, account, name, vendor=None, credit=False):
    obj = {"Id": qid, "SyncToken": "0", "TxnDate": day, "TotalAmt": amount, "AccountRef": AMEX,
           "Line": [_line(amount, account, name)]}
    if vendor:
        obj["EntityRef"] = {"value": vendor[0], "name": vendor[1], "type": "Vendor"}
    if credit:
        obj["Credit"] = True
    return obj


# What QuickBooks returns for August when asked again. All five already sit in book_txn as bare
# headers (see _bare): the realm was backfilled before lines and vendor ids were stored.
AUGUST = [
    _purchase("RDC-1", "2026-08-15", 80.00, "365", "Realtor.com", vendor=("962", "Realtor.com")),
    _purchase("RDC-2", "2026-08-16", 100.00, "365", "Realtor.com"),                  # card charge, no payee
    _purchase("OPC-1", "2026-08-17", 50.00, "999", "Referral fees", vendor=("1568", "Opcity")),
    _purchase("FB-1", "2026-08-18", 40.00, "386", "Facebook", vendor=("77", "Meta")),  # sibling lead gen: no
    _purchase("RDC-R", "2026-08-20", 20.00, "365", "Realtor.com", vendor=("962", "Realtor.com"), credit=True),
]
AUGUST_GROSS = Decimal("210.00")             # 80 + 100 + 50 - 20; Facebook and the Amex header excluded


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


_created: list = []


@pytest.fixture(autouse=True)
async def _cleanup():
    """Each test's tenant has a business keyed `ulrg`, as Utah Life's does, and other test files
    look that key up across tenants. Every row a test tenant owns goes when the test ends."""
    yield
    if not _created:
        return
    ids = list(_created)
    _created.clear()
    async with SessionLocal() as s:
        for table in reversed(Base.metadata.sorted_tables):
            if "tenant_id" in table.c:
                await s.execute(delete(table).where(table.c.tenant_id.in_(ids)))
        await s.execute(delete(Tenant).where(Tenant.id.in_(ids)))
        await s.commit()


@pytest.fixture(autouse=True)
def _quickbooks(monkeypatch):
    """Stub the QuickBooks client and record every call. `query_all` answers from QBO_ROWS."""
    calls = {"query_all": [], "cdc": []}
    rows: dict[str, list] = {"Purchase": list(AUGUST)}

    async def query_all(realm_id, token, entity, where=""):
        calls["query_all"].append((entity, where))
        return list(rows.get(entity, []))

    async def cdc(realm_id, token, entities, since):
        calls["cdc"].append(entities)
        return {"CDCResponse": [{"QueryResponse": []}]}

    async def token(s, integ):
        return "token"

    monkeypatch.setattr(qbo, "query_all", query_all)
    monkeypatch.setattr(qbo, "cdc", cdc)
    monkeypatch.setattr(books_sync, "_valid_access_token", token)
    return {"calls": calls, "rows": rows}


async def _workspace():
    """A fresh tenant shaped like Utah Life: ULRG + Team with the seeded Realtor.com group (three
    rules, 50% borne by Sympli) and its QuickBooks synced within the CDC window."""
    async with SessionLocal() as s:
        t = Tenant(slug=f"hyd-{uuid.uuid4().hex[:6]}", name="Hydrate Realty", plan="portfolio")
        s.add(t)
        await s.flush()
        _created.append(t.id)
        ulrg = Business(tenant_id=t.id, key="ulrg", name="ULRG + Team", tag="Real estate", kind="real_estate")
        sympli = Business(tenant_id=t.id, key="sympli", name="Sympli Mortgage", tag="JV",
                          kind="commission_jv", sort_order=1)
        s.add_all([ulrg, sympli])
        await s.flush()
        await s.run_sync(lambda sync_s: seed_ulrg(sync_s, t.id))
        integ = Integration(tenant_id=t.id, provider="qbo", business_id=ulrg.id, status="connected",
                            realm_id=f"realm-{uuid.uuid4().hex[:8]}", config={"books_backfill_start": START},
                            last_synced_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1))
        s.add(integ)
        owner = User(tenant_id=t.id, email=f"o@{t.slug}.test", name="Owner", role="owner", status="active",
                     password_hash=hash_pw("x" * 12))
        s.add(owner)
        await s.commit()
        return {"tenant_id": t.id, "slug": t.slug, "ulrg": ulrg.id, "integ": integ.id,
                "realm": integ.realm_id, "token": make_token(owner.id, t.id, 0)}


async def _bare(ws, objs):
    """Store each object the way the first backfill did: a header, no vendor id, no lines."""
    async with SessionLocal() as s:
        for o in objs:
            s.add(BookTxn(tenant_id=ws["tenant_id"], business_id=ws["ulrg"], realm_id=ws["realm"],
                          qbo_type="Purchase", qbo_id=o["Id"], txn_date=dt.date.fromisoformat(o["TxnDate"]),
                          amount=Decimal(str(o["TotalAmt"])), payee=(o.get("EntityRef") or {}).get("name"),
                          account_label="Realtor.com", bank_account_label="Amex", came_categorized=True))
        await s.commit()


async def _sync(ws):
    async with SessionLocal() as s:
        integ = await s.get(Integration, ws["integ"])
        return await books_sync.sync_qbo_txns(s, ws["tenant_id"], integ)


async def _august(ws):
    async with SessionLocal() as s:
        g = (await s.execute(select(LeadSourceGroup).where(
            LeadSourceGroup.tenant_id == ws["tenant_id"], LeadSourceGroup.key == "realtor_com"))).scalar_one()
        return await spend.monthly_spend(s, ws["tenant_id"], g)


async def test_a_header_with_no_lines_is_invisible_to_spend():
    """Pins the join the fix works around: a bare header, even one carrying vendor 962, matches
    nothing, because every rule reads a LINE."""
    ws = await _workspace()
    async with SessionLocal() as s:
        s.add(BookTxn(tenant_id=ws["tenant_id"], business_id=ws["ulrg"], realm_id=ws["realm"],
                      qbo_type="Purchase", qbo_id="BARE-1", txn_date=dt.date(2026, 8, 15),
                      amount=Decimal("80.00"), vendor_qbo_id="962", account_qbo_id="365"))
        await s.commit()
    m = await _august(ws)
    assert m["mapped"] and m["synced"]
    assert "2026-08" not in m["gross_by_month"] and m["lines"] == []


async def test_a_realm_with_bare_headers_is_hydrated_then_matched(_quickbooks):
    ws = await _workspace()
    await _bare(ws, AUGUST)
    assert "2026-08" not in (await _august(ws))["gross_by_month"]

    await _sync(ws)

    calls = _quickbooks["calls"]
    # CDC still ran for the window, and the three line-bearing types were re-read from the start.
    assert calls["cdc"]
    pulled = {e: w for e, w in calls["query_all"] if e in ("Purchase", "Bill", "JournalEntry")}
    assert set(pulled) == {"Purchase", "Bill", "JournalEntry"}
    assert all(w == f"WHERE TxnDate >= '{START}'" for w in pulled.values())

    async with SessionLocal() as s:
        txns = {t.qbo_id: t for t in (await s.execute(select(BookTxn).where(
            BookTxn.tenant_id == ws["tenant_id"]))).scalars()}
        assert len(txns) == 5                                               # re-upserted, not duplicated
        assert txns["RDC-1"].vendor_qbo_id == "962" and txns["OPC-1"].vendor_qbo_id == "1568"
        assert txns["RDC-2"].vendor_qbo_id is None and txns["RDC-R"].purchase_credit is True
        lines = (await s.execute(select(func.count(BookTxnLine.id)).where(
            BookTxnLine.tenant_id == ws["tenant_id"]))).scalar_one()
        assert lines == 5
        integ = await s.get(Integration, ws["integ"])
        assert integ.config["ledger_hydrate"]["missing_before"] == 5
        assert integ.config["ledger_hydrate"]["left_without_lines"] == 0
        assert integ.config["books_backfill_start"] == START               # the cursor is untouched

    m = await _august(ws)
    assert m["gross_by_month"]["2026-08"] == AUGUST_GROSS
    assert spend.net_of(AUGUST_GROSS, m["share_pct"]) == Decimal("105.00")
    matched = {ln["qbo_id"] for ln in m["lines"]}
    assert matched == {"RDC-1", "RDC-2", "OPC-1", "RDC-R"}                # not Facebook 386
    assert all(ln["account_qbo_id"] != "1150040000" for ln in m["lines"])  # never the Amex


async def test_a_hydrated_realm_goes_back_to_cdc(_quickbooks):
    ws = await _workspace()
    await _bare(ws, AUGUST)
    await _sync(ws)
    calls = _quickbooks["calls"]
    calls["query_all"].clear()
    calls["cdc"].clear()

    await _sync(ws)

    assert calls["cdc"]
    assert not [e for e, _w in calls["query_all"] if e in ("Purchase", "Bill", "JournalEntry")]


async def test_a_header_quickbooks_no_longer_returns_does_not_trigger_a_pull_every_cycle(_quickbooks):
    """A charge deleted in QuickBooks keeps its header here and can never gain a line. The hydrate
    runs once, records it, and the next cycle stays on CDC."""
    ws = await _workspace()
    gone = _purchase("DELETED-1", "2026-08-01", 10.00, "365", "Realtor.com", vendor=("962", "Realtor.com"))
    await _bare(ws, AUGUST + [gone])
    await _sync(ws)
    async with SessionLocal() as s:
        rec = (await s.get(Integration, ws["integ"])).config["ledger_hydrate"]
    assert rec["missing_before"] == 6 and rec["left_without_lines"] == 1

    calls = _quickbooks["calls"]
    calls["query_all"].clear()
    await _sync(ws)
    assert not [e for e, _w in calls["query_all"] if e in ("Purchase", "Bill", "JournalEntry")]

    # A NEW bare header (more than the one recorded) is a new gap, and is pulled.
    await _bare(ws, [_purchase("RDC-9", "2026-09-15", 80.00, "365", "Realtor.com")])
    _quickbooks["rows"]["Purchase"].append(
        _purchase("RDC-9", "2026-09-15", 80.00, "365", "Realtor.com"))
    await _sync(ws)
    assert [e for e, _w in calls["query_all"] if e == "Purchase"]
    assert (await _august(ws))["gross_by_month"]["2026-09"] == Decimal("80.00")


async def test_headers_before_the_backfill_start_are_left_alone(_quickbooks):
    """The gap is counted from the same start the first backfill used, so years-old headers outside
    it never trigger a pull."""
    ws = await _workspace()
    await _bare(ws, [_purchase("OLD-1", "2025-06-01", 80.00, "365", "Realtor.com")])
    await _sync(ws)
    assert not [e for e, _w in _quickbooks["calls"]["query_all"] if e in ("Purchase", "Bill", "JournalEntry")]


async def test_the_date_bar_keeps_august_spend_after_a_hydrate(_quickbooks):
    """PR #29's period handling picks which cohort months show; it never touches the spend lookup."""
    from app.config import settings
    ws = await _workspace()
    await _bare(ws, AUGUST)
    await _sync(ws)
    H = {"Authorization": f"Bearer {ws['token']}", "X-Tenant-Host": f"{ws['slug']}.{settings.PLATFORM_DOMAIN}"}
    base = "/api/v1/businesses/ulrg/lead-sources/realtor_com/cohorts"
    async with AsyncClient(transport=TRANSPORT, base_url="http://testserver") as c:
        for period in ("c:2026-08-01:2026-08-31", "ytd"):
            r = await c.get(f"{base}?period={period}", headers=H)
            assert r.status_code == 200, r.text
            aug = next(x for x in r.json()["cohort_rows"] if x["cohort_key"] == "2026-08")
            assert aug["gross_spend"] == 210.0 and aug["net_spend"] == 105.0, (period, aug)
