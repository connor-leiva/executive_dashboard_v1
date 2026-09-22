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


async def test_a_family_spans_providers_without_merging_them():
    """Go High Level is one VENDOR over two providers, and Stripe is another. They must keep
    separate rows here — separate tokens, configs and sync paths — while sharing a family, which
    is what lets the UI draw them as one row with two sub-rows."""
    async with SessionLocal() as s:
        out = await build_integrations_view(s, await _tenant(s))
    by = {x.provider: x for x in out.sources}
    assert by["ghl"].family == by["ghl_bc"].family == "ghl"
    assert by["ghl"].vendor == by["ghl_bc"].vendor == "Go High Level"
    assert by["stripe_legacy"].family == by["stripe_bc"].family == "stripe"
    assert by["ghl"] is not by["ghl_bc"]                      # still two sources


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


async def test_secondary_sources_are_marked_so_the_list_can_hold_them_back():
    """A second GHL location and a legacy Stripe belong to one workspace's arrangements. Offered
    to every workspace they read as a catalogue of somebody else's programmes."""
    async with SessionLocal() as s:
        out = await build_integrations_view(s, await _tenant(s))
    by = {x.provider: x for x in out.sources}
    assert all(by[p].secondary for p in ("ghl_bc", "ghl_legacy", "stripe_legacy", "stripe_bc"))
    assert not any(by[p].secondary for p in ("qbo", "sisu", "fub", "arive", "meta_ads"))


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
