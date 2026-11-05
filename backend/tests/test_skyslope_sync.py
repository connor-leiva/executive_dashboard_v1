"""The SkySlope sync, run the way production runs it: the real client, the real upsert and the real
`_sync_integration`, against a fake SkySlope served over httpx.

The spec's own done-when is here against that fake: a backfill fills the tables, an immediate second
run changes 0 rows, and an edit made at the source appears on the next incremental. It still has to
be shown on a real test account, which does not exist yet.
"""
import datetime as dt
import json
import uuid

import pytest
from sqlalchemy import func, select, text

from app.db import SessionLocal
from app.integrations import skyslope
from app.models import (Base, Business, Integration, SkyslopeFileAgent, SkyslopeListing, SkyslopeSale,
                        SkyslopeUser, SyncRun, Tenant)
from app.security import dec, enc
from app.services import skyslope_sync
from app.services.sync import _sync_integration
from tests.skyslope_fake import PII, fake, guid, listing, sale, user  # noqa: F401

UTC = dt.timezone.utc


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


async def _workspace(slug=None, keys=(("key-1", "secret-1"),)) -> dict:
    slug = slug or f"sky{uuid.uuid4().hex[:10]}"
    async with SessionLocal() as s:
        t = Tenant(slug=slug, name=slug, status="active", plan="portfolio", config={})
        s.add(t)
        await s.flush()
        b = Business(tenant_id=t.id, key="main", name="Main", tag="Brokerage", sort_order=0, kind="real_estate")
        s.add(b)
        await s.flush()
        ids = []
        for key, secret in keys:
            integ = Integration(tenant_id=t.id, provider="skyslope", business_id=b.id, status="connected",
                                access_token_enc=enc(json.dumps({"access_key": key, "access_secret": secret})),
                                config={"key_hint": key[-4:]})
            s.add(integ)
            await s.flush()
            ids.append(integ.id)
        await s.commit()
        return {"tenant": t.id, "business": b.id, "keys": ids}


async def _sync(w, index=0) -> dict:
    """One cycle for one key through the production entry point. Returns that run's numbers."""
    async with SessionLocal() as s:
        # By id rather than by "newest": SQLite's clock has one-second resolution, and two runs in the
        # same second tie, which handed these tests the previous run's numbers.
        before = set((await s.execute(select(SyncRun.id).where(SyncRun.tenant_id == w["tenant"]))).scalars())
        integ = await s.get(Integration, w["keys"][index])
        await _sync_integration(s, w["tenant"], integ, "2026-11-01", "2026-11-30")
        runs = (await s.execute(select(SyncRun).where(SyncRun.tenant_id == w["tenant"]))).scalars().all()
        run = next(r for r in runs if r.id not in before)
        await s.refresh(integ)
        return {"run": run, "integ": integ, "stats": (run.stats or {}).get("skyslope") or {}}


async def _rows(model, w):
    async with SessionLocal() as s:
        return (await s.execute(select(model).where(model.tenant_id == w["tenant"]))).scalars().all()


async def _set_state(w, index=0, **changes):
    async with SessionLocal() as s:
        integ = await s.get(Integration, w["keys"][index])
        state = skyslope_sync.state_of(integ)
        state.update(changes)
        integ.config = {**(integ.config or {}), skyslope_sync.STATE_KEY: state}
        await s.commit()


def _ago(**kw) -> str:
    return (dt.datetime.now(UTC) - dt.timedelta(**kw)).isoformat()


# ── the spec's done-when, against the fake ────────────────────────────────────────────────────
async def test_a_backfill_fills_the_tables_and_a_second_run_changes_nothing_and_an_edit_appears(fake):
    w = await _workspace()
    sale_guid, listing_guid = guid(), guid()
    fake.files = [sale(sale_guid=sale_guid), listing(listing_guid=listing_guid)]
    fake.users = [user()]

    first = await _sync(w)
    assert first["run"].status == "ok", first["run"].detail
    assert first["stats"]["rows_seen"] == 2 and first["stats"]["rows_changed"] == 2
    assert first["stats"]["passes"][0]["kind"] == "backfill"
    assert len(await _rows(SkyslopeSale, w)) == 1 and len(await _rows(SkyslopeListing, w)) == 1

    before = (await _rows(SkyslopeSale, w))[0]
    second = await _sync(w)
    assert second["run"].status == "ok"
    assert second["stats"]["rows_changed"] == 0, "an immediate second run must change nothing"
    assert second["stats"]["users_changed"] == 0
    assert second["stats"]["passes"][0]["kind"] == "incremental"
    after = (await _rows(SkyslopeSale, w))[0]
    assert after.content_hash == before.content_hash and after.first_seen_at == before.first_seen_at

    # Somebody edits the deal in SkySlope, by hand.
    fake.files[0] = sale(sale_guid=sale_guid, status="Closed", status_id=30, salePrice=490000.0,
                         actualClosingDate="2026-11-03T00:00:00", modified=dt.datetime.now(UTC) + dt.timedelta(minutes=1))
    third = await _sync(w)
    assert third["stats"]["rows_changed"] == 1
    edited = (await _rows(SkyslopeSale, w))[0]
    assert edited.status_raw == "Closed" and float(edited.sale_price) == 490000.0
    assert edited.closed_at.year == 2026
    assert edited.first_seen_at == before.first_seen_at, "a rewrite must not forget when the file appeared"


async def test_the_backfill_asks_for_everything_and_for_the_summary(fake):
    w = await _workspace()
    fake.files = [sale()]
    await _sync(w)
    assert fake.exports()[0] == {"createdAfter": "2000-01-01T00:00:00Z", "type": "summary,listing,sale"}


async def test_an_incremental_reads_back_ten_minutes_before_the_watermark(fake):
    w = await _workspace()
    fake.files = [sale()]
    await _sync(w)
    await _sync(w)
    assert fake.exports()[1] == {"modifiedAfter": "2026-11-04T11:50:00Z", "type": "summary,listing,sale"}


async def test_the_watermark_is_the_reading_taken_before_the_export(fake):
    """A file changed while the export runs may or may not be in it. Reading the clock afterwards
    would step over it, and it would never be asked for again."""
    w = await _workspace()
    fake.files = [sale()]
    fake.tick_replica_on_export = dt.timedelta(minutes=5)
    out = await _sync(w)
    state = skyslope_sync.state_of(out["integ"])
    assert state["watermark"] == "2026-11-04T12:00:00Z"
    assert out["stats"]["watermark"] == "2026-11-04T12:00:00Z"
    assert out["stats"]["lag_seconds"] == 1.5


async def test_an_incremental_also_keeps_the_reading_from_before_its_export(fake):
    """The same rule on the path that runs most often. Found by breaking it: the test above only
    reaches the backfill."""
    w = await _workspace()
    fake.files = [sale()]
    await _sync(w)
    fake.replica = dt.datetime(2026, 11, 4, 13, 0, 0, tzinfo=UTC)
    fake.tick_replica_on_export = dt.timedelta(minutes=5)
    out = await _sync(w)
    assert out["stats"]["passes"][0]["kind"] == "incremental"
    assert skyslope_sync.state_of(out["integ"])["watermark"] == "2026-11-04T13:00:00Z"


async def test_the_same_file_with_its_lists_in_another_order_is_not_a_change(fake):
    """No list here has a meaningful order, and a false change would break the promise that an
    immediate second run changes nothing."""
    w = await _workspace()
    target, first, second = guid(), guid(), guid()
    fake.files = [sale(sale_guid=target, coAgentGuids=[first, second],
                       commissionSplits=[{"agentGuid": first, "percentage": 60.0}, {"agentGuid": second, "percentage": 40.0}])]
    await _sync(w)
    fake.files = [{**fake.files[0], "coAgentGuids": [second, first],
                   "commissionSplits": list(reversed(fake.files[0]["commissionSplits"]))}]
    assert (await _sync(w))["stats"]["rows_changed"] == 0


# ── what is stored ────────────────────────────────────────────────────────────────────────────
async def test_the_mapping_reads_what_the_api_sends(fake):
    w = await _workspace()
    agent = guid().upper()
    fake.files = [sale(agent=agent, status="Under Contract", status_id=11, mlsNumber="MLS-77")]
    await _sync(w)
    row = (await _rows(SkyslopeSale, w))[0]
    assert row.agent_guid == agent.lower(), "GUIDs are stored lowercase so a join cannot miss on case"
    assert row.status_raw == "Under Contract" and row.status_id == 11
    assert row.status is None, "normalizing is the next phase"
    assert row.stage_name == "In Escrow" and row.deal_side == "Buyer" and row.mls_number == "MLS-77"
    assert (row.city, row.state, row.zip) == ("Boise", "ID", "83702")
    assert float(row.sale_price) == 485000.5 and float(row.list_price) == 500000.0
    assert row.closed_at is None, "0001-01-01 is .NET's empty date, and means not closed"
    assert row.contract_accepted_at.date() == dt.date(2026, 11, 1)
    assert row.expected_close_at.date() == dt.date(2026, 12, 15)
    assert row.commission_raw["commission"]["salesCommissionAmount"] == 9000.0
    assert row.commission_raw["breakdowns"] == [{"name": "Agent commission", "amount": 9000.0}]
    assert row.removed_from_source_at is None and row.business_id == w["business"]


async def test_a_listing_maps_its_own_dates_and_a_sale_can_name_the_listing_it_came_from(fake):
    w = await _workspace()
    source = guid()
    fake.files = [listing(listing_guid=source), sale(listingGuid=source)]
    await _sync(w)
    row = (await _rows(SkyslopeListing, w))[0]
    assert row.listed_at.date() == dt.date(2026, 10, 1) and row.expires_at.date() == dt.date(2027, 4, 1)
    assert float(row.list_price) == 750000.0
    assert (await _rows(SkyslopeSale, w))[0].listing_guid == source


async def test_the_contract_date_is_read_under_either_name(fake):
    w = await _workspace()
    other = sale(contractAcceptanceDate="2026-09-09T00:00:00")
    other.pop("contractAcceptedDate")
    fake.files = [other]
    await _sync(w)
    assert (await _rows(SkyslopeSale, w))[0].contract_accepted_at.date() == dt.date(2026, 9, 9)


async def test_nothing_on_the_never_store_list_reaches_any_stored_row(fake):
    """Every such field in the fake holds the marker, including ones nested inside objects the sync
    partly keeps: a street on `property`, a contact on `commissionReferral`, a home address on a user.
    This reads the whole of what was written, so a leak fails here by name."""
    w = await _workspace()
    fake.files = [sale(commissionSplits=[{"agentGuid": guid(), "amount": 100.0, "percentage": 50.0, "note": PII}]),
                  listing(coAgentGuids=[guid()])]
    fake.users = [user()]
    out = await _sync(w)
    assert out["run"].status == "ok", out["run"].detail

    leaks = []
    async with SessionLocal() as s:
        for table in Base.metadata.tables.values():
            if table.name.startswith("skyslope_"):
                rows = (await s.execute(select(table).where(table.c.tenant_id == w["tenant"]))).mappings().all()
                assert rows or table.name == "skyslope_agent_link", f"nothing was written to {table.name}"
                for row in rows:
                    for column, value in row.items():
                        if PII in json.dumps(value, default=str):
                            leaks.append(f"{table.name}.{column}")
    integ = out["integ"]
    if PII in json.dumps(integ.config, default=str):
        leaks.append("integration.config")
    if PII in json.dumps(out["run"].stats, default=str) or PII in (out["run"].detail or ""):
        leaks.append("sync_run")
    assert not leaks, f"stored what the spec says never to store: {sorted(set(leaks))}"


async def test_a_new_field_skysope_adds_is_not_stored_until_somebody_decides_to(fake):
    """An allow-list fails closed, where a list of fields to strip would have written this."""
    w = await _workspace()
    fake.files = [sale(brandNewField=PII, property={"city": "Boise", "brandNewStreet": PII})]
    await _sync(w)
    assert PII not in json.dumps((await _rows(SkyslopeSale, w))[0].raw)


async def test_no_stored_column_could_hold_a_street_or_a_phone():
    names = {c.name for t in Base.metadata.tables.values() if t.name.startswith("skyslope_") for c in t.c}
    for banned in ("street", "address", "phone", "buyer", "seller", "contact"):
        assert not [n for n in names if banned in n], banned


async def test_every_free_string_fits_its_column_on_postgres(fake):
    """SQLite ignores varchar lengths and Postgres raises on them, so this holds the mapping to the
    models' own lengths. Otherwise one long stage name passes here and fails production."""
    long = "x" * 900
    item = sale(status=long, mlsNumber=long, dealType=long, stage={"name": long},
                property={"city": long, "state": long, "zip": long})
    columns, _ = skyslope_sync.map_sale(item)
    for name, value in columns.items():
        if isinstance(value, str):
            limit = SkyslopeSale.__table__.c[name].type.length
            assert limit and len(value) <= limit, name
    mapped = skyslope_sync.map_user(user(first=long, last=long, email=long, user_type=long))
    for name, value in mapped.items():
        if isinstance(value, str):
            limit = SkyslopeUser.__table__.c[name].type.length
            assert limit and len(value) <= limit, name


async def test_a_price_too_large_for_its_column_is_dropped_rather_than_failing_the_write():
    columns, _ = skyslope_sync.map_sale(sale(salePrice=1e15, listingPrice="not a number"))
    assert columns["sale_price"] is None and columns["list_price"] is None


async def test_a_file_with_no_guid_is_skipped_and_counted(fake):
    w = await _workspace()
    fake.files = [sale(saleGuid=None), sale()]
    out = await _sync(w)
    assert out["run"].status == "ok", out["run"].detail
    assert out["stats"]["passes"][0]["skipped"] == 1
    assert len(await _rows(SkyslopeSale, w)) == 1


# ── who is on a file ──────────────────────────────────────────────────────────────────────────
async def test_each_person_on_a_file_gets_a_row_for_each_role(fake):
    w = await _workspace()
    primary, co, split = guid(), guid(), guid()
    target = guid()
    fake.files = [
        sale(sale_guid=target, agent=primary, coAgentGuids=[co],
             commissionSplits=[{"agentGuid": primary, "amount": 6000.0, "percentage": 60.0},
                               {"agentGuid": split, "amount": 4000.0, "percentage": 40.0}]),
        listing(agent=primary, coListingAgentGuid=co, coAgentGuids=[]),
    ]
    await _sync(w)
    rows = await _rows(SkyslopeFileAgent, w)
    on_sale = {(r.user_guid, r.role): r.split_pct for r in rows if r.file_guid == target}
    assert set(on_sale) == {(primary, "primary"), (co, "co_agent"), (primary, "split"), (split, "split")}
    assert float(on_sale[(split, "split")]) == 40.0 and on_sale[(primary, "primary")] is None
    on_listing = {(r.user_guid, r.role) for r in rows if r.file_kind == "listing"}
    assert on_listing == {(primary, "primary"), (co, "co_listing")}


async def test_a_split_naming_someone_twice_is_merged_rather_than_failing_the_key(fake):
    w = await _workspace()
    who = guid()
    fake.files = [sale(commissionSplits=[{"agentGuid": who, "percentage": 30.0}, {"agentGuid": who, "percentage": 20.0}])]
    out = await _sync(w)
    assert out["run"].status == "ok", out["run"].detail
    rows = [r for r in await _rows(SkyslopeFileAgent, w) if r.role == "split"]
    assert len(rows) == 1 and float(rows[0].split_pct) == 50.0


async def test_an_empty_guid_means_nobody(fake):
    w = await _workspace()
    empty = "00000000-0000-0000-0000-000000000000"
    fake.files = [sale(agentGuid=empty, coAgentGuids=[empty], officeGuid=empty)]
    await _sync(w)
    row = (await _rows(SkyslopeSale, w))[0]
    assert row.agent_guid is None and row.office_guid is None
    assert await _rows(SkyslopeFileAgent, w) == []


async def test_changing_who_is_on_a_file_replaces_its_rows(fake):
    w = await _workspace()
    target, first, second = guid(), guid(), guid()
    fake.files = [sale(sale_guid=target, agent=first)]
    await _sync(w)
    fake.files = [sale(sale_guid=target, agent=second, modified=dt.datetime.now(UTC) + dt.timedelta(minutes=1))]
    await _sync(w)
    assert {r.user_guid for r in await _rows(SkyslopeFileAgent, w)} == {second}


# ── an export that does not arrive whole ──────────────────────────────────────────────────────
async def test_a_stream_that_drops_once_is_rerun_and_nothing_is_lost(fake):
    w = await _workspace()
    fake.files = [sale() for _ in range(5)]
    fake.drop_next_exports = 1
    out = await _sync(w)
    assert out["run"].status == "ok", out["run"].detail
    assert len(fake.exports()) == 2 and fake.exports()[0] == fake.exports()[1], "the same call, the same filter"
    assert len(await _rows(SkyslopeSale, w)) == 5
    assert skyslope_sync.state_of(out["integ"])["watermark"]


@pytest.mark.parametrize("failure", ["drop_next_exports", "truncate_next_exports", "short_next_exports"])
async def test_an_export_that_never_arrives_whole_leaves_the_watermark_where_it_was(fake, failure):
    """The next run has to ask for the same window again, or those changes are gone for good."""
    w = await _workspace()
    fake.files = [sale(), sale(), sale()]
    await _sync(w)
    state = skyslope_sync.state_of((await _sync(w))["integ"])
    assert state["watermark"] == "2026-11-04T12:00:00Z"

    fake.replica += dt.timedelta(hours=1)
    fake.files.append(sale(modified=dt.datetime.now(UTC) + dt.timedelta(minutes=1)))
    setattr(fake, failure, 2)       # the first try and its one rerun
    out = await _sync(w)
    assert out["run"].status == "error"
    assert out["integ"].status == "error" and out["integ"].last_error
    assert skyslope_sync.state_of(out["integ"])["watermark"] == "2026-11-04T12:00:00Z"

    healed = await _sync(w)
    assert healed["run"].status == "ok", healed["run"].detail
    assert healed["integ"].status == "connected" and healed["integ"].last_error is None
    assert skyslope_sync.state_of(healed["integ"])["watermark"] == "2026-11-04T13:00:00Z"
    assert len(await _rows(SkyslopeSale, w)) == 4


async def test_a_failed_backfill_does_not_count_as_a_backfill(fake):
    w = await _workspace()
    fake.files = [sale(), sale()]
    fake.short_next_exports = 2
    out = await _sync(w)
    assert out["run"].status == "error"
    assert "short" in out["run"].detail
    assert not skyslope_sync.state_of(out["integ"]).get("backfilled_at")
    assert (await _sync(w))["stats"]["passes"][0]["kind"] == "backfill"


async def test_an_export_with_no_summary_cannot_move_the_watermark(fake):
    w = await _workspace()
    fake.files = [sale()]
    fake.omit_summary = True
    out = await _sync(w)
    assert out["run"].status == "error" and "summary" in out["run"].detail
    assert not skyslope_sync.state_of(out["integ"]).get("watermark")


async def test_a_missing_replica_timestamp_pulls_nothing(fake, monkeypatch):
    w = await _workspace()
    fake.files = [sale()]

    async def nothing(self):
        return {"utcNow": "2026-11-04T12:00:00Z"}

    monkeypatch.setattr(skyslope.SkySlopeClient, "replica_timestamp", nothing)
    out = await _sync(w)
    assert out["run"].status == "error" and "replica" in out["run"].detail
    assert fake.exports() == []


async def test_a_refused_key_is_recorded_on_the_connection_without_its_secret(fake):
    w = await _workspace(keys=(("key-1", "wrong-secret"),))
    fake.files = [sale()]
    out = await _sync(w)
    assert out["run"].status == "error" and out["integ"].status == "error"
    for text_ in (out["run"].detail, out["integ"].last_error):
        assert "wrong-secret" not in text_ and "key-1" not in text_ and "client-secret" not in text_


# ── daily and weekly ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("state,expected", [
    ({}, ["backfill"]),
    ({"backfilled_at": "x"}, ["backfill"]),      # no watermark means nowhere safe to resume from
    ({"backfilled_at": "x", "watermark": "w", "last_full_at": _ago(days=1), "last_daily_at": _ago(hours=1)}, ["incremental"]),
    ({"backfilled_at": "x", "watermark": "w", "last_full_at": _ago(days=1), "last_daily_at": _ago(hours=25)}, ["incremental", "daily"]),
    ({"backfilled_at": "x", "watermark": "w", "last_full_at": _ago(days=8), "last_daily_at": _ago(hours=25)}, ["full"]),
])
def test_what_a_cycle_owes(state, expected):
    assert skyslope_sync.plan(state, dt.datetime.now(UTC)) == expected


async def test_the_daily_reconcile_catches_a_flip_that_did_not_touch_modified_on(fake):
    w = await _workspace()
    target = guid()
    old = dt.datetime(2026, 9, 1, tzinfo=UTC)
    fake.files = [listing(listing_guid=target, status="Active", modified=old)]
    await _sync(w)
    # It expired on a date, and modifiedOn did not move.
    fake.files = [listing(listing_guid=target, status="Expired", modified=old)]
    assert (await _sync(w))["stats"]["rows_changed"] == 0, "an incremental cannot see this"

    await _set_state(w, last_daily_at=_ago(hours=25))
    out = await _sync(w)
    assert [p["kind"] for p in out["stats"]["passes"]] == ["incremental", "daily"]
    assert fake.exports()[-1]["status"] == skyslope_sync.OPEN_STATUSES
    assert (await _rows(SkyslopeListing, w))[0].status_raw == "Expired"
    assert out["stats"]["rows_changed"] == 1


async def test_the_daily_reconcile_does_not_move_the_watermark(fake):
    w = await _workspace()
    fake.files = [sale()]
    await _sync(w)
    await _set_state(w, last_daily_at=_ago(hours=25), watermark="2026-11-04T09:00:00Z")
    fake.short_next_exports = 0
    out = await _sync(w)
    state = skyslope_sync.state_of(out["integ"])
    assert state["watermark"] == "2026-11-04T12:00:00Z", "the incremental moved it, not the reconcile"
    assert skyslope.parse_ts(state["last_daily_at"]) > dt.datetime.now(UTC) - dt.timedelta(minutes=1)


async def test_a_weekly_full_pull_flags_a_file_that_has_gone_and_never_deletes_it(fake):
    w = await _workspace()
    keep, gone = guid(), guid()
    fake.files = [sale(sale_guid=keep), sale(sale_guid=gone)]
    await _sync(w)
    fake.files = [sale(sale_guid=keep)]
    await _set_state(w, last_full_at=_ago(days=8))
    out = await _sync(w)
    assert out["stats"]["passes"][0]["kind"] == "full" and out["stats"]["removed"] == 1
    rows = {r.sale_guid: r for r in await _rows(SkyslopeSale, w)}
    assert set(rows) == {keep, gone}, "a sync never deletes a row"
    assert rows[gone].removed_from_source_at is not None and rows[keep].removed_from_source_at is None


async def test_a_file_that_comes_back_is_no_longer_flagged(fake):
    w = await _workspace()
    target = sale()
    fake.files = [target, sale()]
    await _sync(w)
    fake.files = fake.files[1:]
    await _set_state(w, last_full_at=_ago(days=8))
    await _sync(w)
    fake.files.append(target)
    await _set_state(w, last_full_at=_ago(days=8))
    out = await _sync(w)
    assert out["stats"]["rows_changed"] == 1, "coming back is a change"
    assert all(r.removed_from_source_at is None for r in await _rows(SkyslopeSale, w))


async def test_a_full_pull_that_returns_nothing_flags_nothing(fake):
    """A key that lost its scope overnight must not make the whole team read as gone."""
    w = await _workspace()
    fake.files = [sale(), sale()]
    await _sync(w)
    fake.files = []
    await _set_state(w, last_full_at=_ago(days=8))
    out = await _sync(w)
    assert out["stats"]["removed"] == 0
    assert all(r.removed_from_source_at is None for r in await _rows(SkyslopeSale, w))


# ── several keys ──────────────────────────────────────────────────────────────────────────────
async def test_two_keys_returning_one_file_make_one_row_credited_from_the_file(fake):
    w = await _workspace(keys=(("key-1", "secret-1"), ("key-2", "secret-2")))
    shared, agent = guid(), guid()
    fake.files = [sale(sale_guid=shared, agent=agent)]
    await _sync(w, 0)
    second = await _sync(w, 1)
    assert second["run"].status == "ok", second["run"].detail
    assert second["stats"]["rows_changed"] == 0, "the same file from another key is not a change"
    rows = await _rows(SkyslopeSale, w)
    assert len(rows) == 1 and rows[0].agent_guid == agent, "from the file's own field, not from the key"


async def test_a_file_leaving_one_key_is_not_gone_while_another_key_still_returns_it(fake):
    w = await _workspace(keys=(("key-1", "secret-1"), ("key-2", "secret-2")))
    shared, only_two = guid(), guid()
    fake.files = [sale(sale_guid=shared), sale(sale_guid=only_two)]
    fake.only_for_key = {"key-1": {shared}, "key-2": {shared, only_two}}
    await _sync(w, 0)
    await _sync(w, 1)
    fake.only_for_key = {"key-1": {shared}, "key-2": {shared, only_two}}

    # Key one's weekly pull does not return `only_two`, which it never could see.
    await _set_state(w, 0, last_full_at=_ago(days=8))
    await _set_state(w, 1, last_full_at=_ago(days=1))
    out = await _sync(w, 0)
    assert out["stats"]["passes"][0]["kind"] == "full"
    assert all(r.removed_from_source_at is None for r in await _rows(SkyslopeSale, w))


async def test_a_key_that_has_never_finished_a_full_pull_holds_flagging_back(fake):
    """It might be the only key that can see the file, so nothing can be said until it has looked."""
    w = await _workspace(keys=(("key-1", "secret-1"), ("key-2", "secret-2")))
    keep, gone = guid(), guid()
    fake.files = [sale(sale_guid=keep), sale(sale_guid=gone)]
    await _sync(w, 0)
    fake.files = [sale(sale_guid=keep)]
    await _set_state(w, 0, last_full_at=_ago(days=8))
    out = await _sync(w, 0)
    assert out["stats"]["passes"][0]["kind"] == "full" and out["stats"]["removed"] == 0
    assert all(r.removed_from_source_at is None for r in await _rows(SkyslopeSale, w))


async def test_a_file_is_flagged_only_after_every_key_has_missed_it(fake):
    w = await _workspace(keys=(("key-1", "secret-1"), ("key-2", "secret-2")))
    shared, gone = guid(), guid()
    fake.files = [sale(sale_guid=shared), sale(sale_guid=gone)]
    await _sync(w, 0)
    await _sync(w, 1)
    fake.files = [sale(sale_guid=shared)]

    # Key one has pulled since the file went. Key two's latest full pull still predates that, so it
    # may yet return the file, and nothing can be said.
    await _set_state(w, 0, last_full_at=_ago(days=8))
    await _set_state(w, 1, last_full_at=_ago(days=6))
    await _sync(w, 0)
    assert all(r.removed_from_source_at is None for r in await _rows(SkyslopeSale, w))

    await _set_state(w, 1, last_full_at=_ago(days=8))
    await _sync(w, 1)
    flagged = {r.sale_guid for r in await _rows(SkyslopeSale, w) if r.removed_from_source_at}
    assert flagged == {gone}


# ── users ─────────────────────────────────────────────────────────────────────────────────────
async def test_users_are_refreshed_every_cycle_and_their_emails_normalized(fake):
    w = await _workspace()
    active, gone = guid(), guid()
    fake.users = [user(user_guid=active, email="  Ann.Agent@Example.Test ", offices=["BBBB", "AAAA"]),
                  user(user_guid=gone, first="Bob", email="bob@example.test_deactivated")]
    out = await _sync(w)
    assert out["stats"]["users"] == 2 and out["stats"]["users_changed"] == 2
    rows = {r.user_guid: r for r in await _rows(SkyslopeUser, w)}
    assert rows[active].email_norm == "ann.agent@example.test" and rows[active].deactivated is False
    assert rows[active].name == "Ann Agent" and rows[active].user_type == "Agent"
    assert rows[active].office_guids == ["aaaa", "bbbb"]
    assert rows[gone].email_raw == "bob@example.test_deactivated"
    assert rows[gone].email_norm == "bob@example.test" and rows[gone].deactivated is True

    fake.users[0] = user(user_guid=active, first="Anne", email="ann.agent@example.test", offices=["AAAA", "BBBB"])
    out = await _sync(w)
    assert out["stats"]["users_changed"] == 1
    assert {r.user_guid: r.name for r in await _rows(SkyslopeUser, w)}[active] == "Anne Agent"


@pytest.mark.parametrize("raw,norm", [("A@B.test", "a@b.test"), (" a@b.test ", "a@b.test"), ("a@b.test_deleted", "a@b.test"),
                                      ("a@b.test_deactivated", "a@b.test"), ("", None), (None, None)])
def test_normalizing_an_email(raw, norm):
    assert skyslope_sync.normalize_email(raw) == norm


async def test_a_key_not_allowed_to_list_users_still_syncs_its_files(fake):
    """An agent-level key may be refused the roster. That is a fact about its scope, to be recorded."""
    w = await _workspace()
    fake.files = [sale()]
    fake.users_status = 403
    out = await _sync(w)
    assert out["run"].status == "ok", out["run"].detail
    assert out["stats"]["users"] == "denied"
    assert len(await _rows(SkyslopeSale, w)) == 1


async def test_any_other_failure_on_users_is_a_failure(fake):
    w = await _workspace()
    fake.users_status = 422
    out = await _sync(w)
    assert out["run"].status == "error" and out["integ"].status == "error"


# ── credentials ───────────────────────────────────────────────────────────────────────────────
async def test_the_state_is_not_written_through_the_loaded_dict(fake):
    """Mutating the dict SQLAlchemy loaded makes the UPDATE a silent no-op, which cost another source
    four "successful" syncs. Read from a fresh session, so this sees only what was really committed."""
    w = await _workspace()
    fake.files = [sale()]
    await _sync(w)
    async with SessionLocal() as s:
        integ = await s.get(Integration, w["keys"][0])
        assert skyslope_sync.state_of(integ)["backfilled_at"]
        assert (integ.config or {}).get("key_hint") == "ey-1", "the sync must keep the rest of the config"
        assert json.loads(dec(integ.access_token_enc))["access_key"] == "key-1"


async def test_a_connection_with_no_key_fails_clearly(fake):
    w = await _workspace(keys=())
    async with SessionLocal() as s:
        integ = Integration(tenant_id=w["tenant"], provider="skyslope", business_id=w["business"], status="connected")
        s.add(integ)
        await s.flush()
        w["keys"] = [integ.id]
        await s.commit()
    out = await _sync(w)
    assert out["run"].status == "error" and "no access key" in out["run"].detail


async def test_the_tables_are_really_empty_for_another_workspace(fake):
    a, b = await _workspace(), await _workspace()
    fake.files = [sale()]
    await _sync(a)
    assert await _rows(SkyslopeSale, b) == []
    async with SessionLocal() as s:
        assert (await s.execute(select(func.count()).select_from(SkyslopeSale).where(
            SkyslopeSale.tenant_id == a["tenant"]))).scalar_one() == 1
        assert (await s.execute(text("select 1"))).scalar_one() == 1
