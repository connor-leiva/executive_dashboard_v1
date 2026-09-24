"""Settings › Integrations grouped view (spec v3 Part 1.4)."""
import datetime as dt

import pytest
from sqlalchemy import select, update

from app.seed import seed
from app.db import SessionLocal
from app.models import Business, Integration, SyncRun
from app.services.integrations_view import build_integrations_view


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _tenant(s):
    return (await s.execute(select(Business).where(Business.key == "ulrg"))).scalar_one().tenant_id


async def test_qbo_grouped_and_attention():
    async with SessionLocal() as s:
        t = await _tenant(s)
        biz = {b.key: b.id for b in (await s.execute(
            select(Business).where(Business.tenant_id == t))).scalars().all()}
        await s.execute(update(Integration).where(
            Integration.tenant_id == t, Integration.provider == "qbo",
            Integration.business_id == biz["springb"]).values(status="error", last_error="Token expired Jun 29"))
        await s.commit()
        out = await build_integrations_view(s, t)

    qbo = next(x for x in out.sources if x.provider == "qbo")
    assert qbo.status == "attention"
    assert "1 of 3" in qbo.status_note
    assert len(qbo.entities) == 3                       # grouped, ordered by sort_order
    assert [e.business_key for e in qbo.entities] == ["ulrg", "springb", "sympli"]
    err = [e for e in qbo.entities if e.state == "error"]
    assert len(err) == 1 and "expired" in err[0].detail.lower()


async def test_freshness_stale():
    async with SessionLocal() as s:
        t = await _tenant(s)
        old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=3 * 30)   # 3× the interval
        await s.execute(update(Integration).where(
            Integration.tenant_id == t, Integration.provider == "sisu").values(
            status="connected", last_synced_at=old))
        await s.commit()
        out = await build_integrations_view(s, t)

    sisu = next(x for x in out.sources if x.provider == "sisu")
    assert sisu.status == "stale"
    assert sisu.fresh and "ago" in sisu.fresh


async def test_stats_last_run():
    async with SessionLocal() as s:
        t = await _tenant(s)
        # fresh sisu + a run with stats → last_run renders counts.
        await s.execute(update(Integration).where(
            Integration.tenant_id == t, Integration.provider == "sisu").values(
            status="connected", last_synced_at=dt.datetime.now(dt.timezone.utc)))
        s.add(SyncRun(tenant_id=t, provider="sisu", status="ok",
                      finished_at=dt.datetime.now(dt.timezone.utc), stats={"records": 412, "seconds": 3.1}))
        await s.commit()
        out = await build_integrations_view(s, t)

    sisu = next(x for x in out.sources if x.provider == "sisu")
    assert sisu.last_run and "412 records" in sisu.last_run


async def test_qbo_disconnected_entity_marked_distinctly():
    """A disconnected QBO integration renders with state='disconnected' (not 'ok'), so
    the UI can show it as disconnected + offer Reconnect rather than looking unchanged."""
    async with SessionLocal() as s:
        t = await _tenant(s)
        biz = {b.key: b.id for b in (await s.execute(
            select(Business).where(Business.tenant_id == t))).scalars().all()}
        await s.execute(update(Integration).where(
            Integration.tenant_id == t, Integration.provider == "qbo",
            Integration.business_id == biz["sympli"]).values(status="disconnected", access_token_enc=None))
        await s.commit()
        out = await build_integrations_view(s, t)
    qbo = next(x for x in out.sources if x.provider == "qbo")
    sympli = next(e for e in qbo.entities if e.business_key == "sympli")
    assert sympli.state == "disconnected"      # not "ok" — the UI reflects the disconnect


# ── the vendor-grouped view (Phase 1 of INTEGRATIONS-SPEC.md) ────────────────────────────────
async def test_every_provider_the_api_can_return_has_a_family_and_a_category():
    """Derived from ORDER, not from a list kept by hand: a provider added to the payload without
    its display metadata renders a row with a blank name and no category, and nothing else in the
    system complains. This is the same guard shape that already protects DESC and MONO."""
    from app.services.integrations_view import FAMILY, ORDER
    missing = [p for p in ORDER if p not in FAMILY]
    assert not missing, f"no family/category/meta for: {missing}"
    for prov in ORDER:
        family, vendor, category, meta_line, _secondary = FAMILY[prov]
        assert family and vendor and category and meta_line, prov


async def test_a_vendor_row_stands_for_two_providers_without_merging_them():
    """Go High Level is ONE ROW over two providers. The row is presentation; the providers keep
    separate credentials, separate configs and independent failure, and each appears beneath it
    as its own connection carrying its own configuration. That last part is what makes the row
    honest rather than a claim that one credential serves both locations."""
    async with SessionLocal() as s:
        t = await _tenant(s)
        any_biz = (await s.execute(select(Business).where(
            Business.tenant_id == t))).scalars().first()
        for prov, loc in (("ghl", "LOC-FORUM"), ("ghl_bc", "LOC-BCOLL")):
            row = (await s.execute(select(Integration).where(
                Integration.tenant_id == t, Integration.provider == prov))).scalars().first()
            if row is None:
                row = Integration(tenant_id=t, provider=prov, business_id=any_biz.id)
                s.add(row)
            row.status = "connected"
            row.last_synced_at = dt.datetime.now(dt.timezone.utc)
            row.config = {"location_id": loc, "member_tags": ["member"]}
        await s.commit()
        out = await build_integrations_view(s, t)
        underneath = len((await s.execute(select(Integration).where(
            Integration.tenant_id == t,
            Integration.provider.in_(("ghl", "ghl_bc"))))).scalars().all())

    rows = [x for x in out.sources if x.family == "ghl"]
    assert len(rows) == 1, "Go High Level should be one row"
    row = rows[0]
    assert row.vendor == "Go High Level"
    # Three providers now: the two programme locations and the brokerage's recruiting one.
    # Asserted as a superset rather than a literal list, because the POINT of this test is that
    # a vendor row spans its members without merging them -- not how many there happen to be.
    assert set(row.members) >= {"ghl", "ghl_bc"} and "ghl_recruiting" in row.members
    assert underneath == 2, "the integrations themselves must not have been merged"
    assert {e.provider for e in row.entities} == {"ghl", "ghl_bc"}
    # Each connection carries ITS OWN configuration. A summary on the row would describe one
    # location and imply both.
    locs = {e.provider: dict(e.config_summary).get("Location ID") for e in row.entities}
    assert locs["ghl"] and locs["ghl_bc"] and locs["ghl"] != locs["ghl_bc"]


async def test_a_broken_entity_becomes_an_alert_that_names_it():
    """The banner says "Spring B lost its QuickBooks connection" and offers one button. Built on
    the server because a client re-deriving that sentence from a status enum is how the card's
    collapsed line already drifted from the card it summarised."""
    async with SessionLocal() as s:
        t = await _tenant(s)
        biz = {b.key: b.id for b in (await s.execute(
            select(Business).where(Business.tenant_id == t))).scalars().all()}
        await s.execute(update(Integration).where(
            Integration.tenant_id == t, Integration.provider == "qbo",
            Integration.business_id == biz["springb"]).values(
            status="error", last_error="Token expired"))
        await s.commit()
        out = await build_integrations_view(s, t)
    alert = next(a for a in out.alerts if a.business_key == "springb")
    assert "QuickBooks" in alert.title and alert.provider == "qbo"
    assert alert.action == "reconnect"
    assert alert.detail                                       # never a bare title


async def test_entity_rows_carry_their_colour_and_their_actions():
    """The swatch is the workspace's own Business.accent, and the menu is server-built: a menu
    offering an action the API refuses is the dead-button failure this module shipped three
    times."""
    async with SessionLocal() as s:
        out = await build_integrations_view(s, await _tenant(s))
    qbo = next(x for x in out.sources if x.provider == "qbo")
    assert qbo.entities
    for e in qbo.entities:
        assert e.provider == "qbo"
        assert e.accent and e.accent.startswith("#")
        assert ("sync" in e.actions) == (e.state == "ok")
        assert ("reconnect" in e.actions) == (e.state != "ok")
        # `remove` is conditional -- see the removable test below -- so it is not asserted here.
        assert {"edit", "disconnect"} <= set(e.actions)


async def test_the_counts_describe_the_list_they_sit_above():
    async with SessionLocal() as s:
        out = await build_integrations_view(s, await _tenant(s))
    assert out.healthy == sum(1 for x in out.sources if x.status == "ok")
    assert out.needs_attention == sum(1 for x in out.sources if x.status in ("attention", "stale"))
    assert out.total == len(out.sources)
    assert out.entities_mapped >= 1


async def test_a_row_is_held_back_only_when_every_member_is_secondary():
    """A second GHL location is secondary; the FIRST one is not, so the vendor row belongs in the
    list. Both of our Stripes are one workspace's arrangements, so that row is held back until a
    row exists — offered to everybody it reads as a catalogue of somebody else's programmes."""
    async with SessionLocal() as s:
        out = await build_integrations_view(s, await _tenant(s))
    by = {x.family: x for x in out.sources}
    assert set(by["ghl"].members) >= {"ghl", "ghl_bc", "ghl_recruiting"}
    assert by["ghl"].secondary is False
    assert by["stripe"].members == ["stripe_legacy", "stripe_bc"]
    assert by["stripe"].secondary is True
    assert by["ghl_legacy"].secondary is True
    assert not any(by[f].secondary for f in ("qbo", "sisu", "fub", "arive", "meta"))


async def test_the_row_age_is_a_column_not_a_sentence():
    """`fresh` is a sentence for the drawer ("Synced 26 min ago"); `ago` has to line up down a
    74px column, so it is "26 min"."""
    async with SessionLocal() as s:
        t = await _tenant(s)
        await s.execute(update(Integration).where(
            Integration.tenant_id == t, Integration.provider == "sisu").values(
            status="connected", last_synced_at=dt.datetime.now(dt.timezone.utc)))
        await s.commit()
        out = await build_integrations_view(s, t)
    sisu = next(x for x in out.sources if x.provider == "sisu")
    assert sisu.ago and "ago" not in sisu.ago and "Synced" not in sisu.ago


async def test_remove_is_offered_only_where_the_api_would_accept_it():
    """delete_qbo_entity protects the workspace's last business and any business with a non-QBO
    source attached -- deleting one of those orphans a live sync. The page used to guard this with
    a hardcoded set of one customer's three business keys, which protected her and nobody else.
    The menu now reads the same rule the endpoint enforces."""
    async with SessionLocal() as s:
        t = await _tenant(s)
        out = await build_integrations_view(s, t)
        attached = {i.business_id for i in (await s.execute(select(Integration).where(
            Integration.tenant_id == t, Integration.provider != "qbo"))).scalars().all()}
        keys = {b.id: b.key for b in (await s.execute(
            select(Business).where(Business.tenant_id == t))).scalars().all()}
    busy_keys = {keys[bid] for bid in attached if bid in keys}
    qbo = next(x for x in out.sources if x.provider == "qbo")
    for e in qbo.entities:
        offered = "remove" in e.actions
        assert offered != (e.business_key in busy_keys), (
            f"{e.business_key}: remove offered={offered} while a non-QBO source "
            f"{'is' if e.business_key in busy_keys else 'is not'} attached")


async def test_a_vendor_row_wears_the_worst_state_of_its_connections():
    """The row is the thing somebody scans, so "one of these is broken" has to survive the merge.
    And a Connect on that row creates the member that has no row yet, so connecting a second
    location does not ask anybody to know that it is called ghl_bc."""
    async with SessionLocal() as s:
        t = await _tenant(s)
        rows = {r.provider: r for r in (await s.execute(select(Integration).where(
            Integration.tenant_id == t,
            Integration.provider.in_(("ghl", "ghl_bc"))))).scalars().all()}
        rows["ghl"].status, rows["ghl"].last_error = "error", "Token rejected"
        rows["ghl_bc"].status = "connected"
        rows["ghl_bc"].last_synced_at = dt.datetime.now(dt.timezone.utc)
        await s.commit()
        out = await build_integrations_view(s, t)
    row = next(x for x in out.sources if x.family == "ghl")
    assert row.status == "attention"          # the healthy sibling does not hide the broken one
    assert {e.state for e in row.entities} == {"error", "ok"}
    # Something IS left to add -- ghl_recruiting has no row in this fixture -- so the offer
    # names it. It must never name a member that already has one: that is the whole reason
    # connect_provider exists instead of the row's own provider.
    assert row.connect_provider == "ghl_recruiting"
    assert row.multi_entity is True


async def test_a_vendor_row_offers_its_unconnected_member():
    async with SessionLocal() as s:
        t = await _tenant(s)
        row = (await s.execute(select(Integration).where(
            Integration.tenant_id == t, Integration.provider == "ghl_bc"))).scalars().first()
        await s.delete(row)
        await s.commit()
        out = await build_integrations_view(s, t)
    row = next(x for x in out.sources if x.family == "ghl")
    assert row.connect_provider == "ghl_bc"
    assert row.multi_entity is True
