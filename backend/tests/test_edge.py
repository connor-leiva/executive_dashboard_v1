"""The Edge — a Forum-replica program off springb in the edge_ namespace, a segment of the
existing Forum GHL + Stripe (classified by product name / tags)."""
import pytest
from sqlalchemy import select

from app.seed import seed
from app.db import SessionLocal
from app.models import Tenant
from app.services.billing import edge_offering
from app.services.tabs import tab_for_metric
from app.services.edge import build_edge


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


def test_edge_offering_classifies_by_name_only():
    # The Edge products carry "The Edge" in the description → classified IN
    assert edge_offering("The Edge Monthly") == (True, "edge")
    assert edge_offering("The Edge Intensive")[0] is True
    assert edge_offering("The Edge Course")[0] is True
    # sibling programs on the same legacy Stripe are NOT Edge revenue
    assert edge_offering("The Forum membership dues")[0] is False
    assert edge_offering("beCollective payment")[0] is False
    assert edge_offering("Inner Circle add-on")[0] is False
    # deliberately NO amount/recurring fallback (that would steal generic Forum dues)
    assert edge_offering("Subscription update", amount=1200, recurring=True)[0] is False
    # event tickets aren't membership revenue
    assert edge_offering("The Edge VIP ticket")[0] is True   # named Edge → kept
    assert edge_offering("General VIP ticket")[0] is False


def test_tab_for_metric_routes_edge_without_touching_siblings():
    assert tab_for_metric("edge_roster") == "edge"
    assert tab_for_metric("edge_members") == "edge"
    assert tab_for_metric("edge_payments") == "edge"
    # siblings unaffected by the new branch
    assert tab_for_metric("forum_roster") == "forum"
    assert tab_for_metric("bc_members") == "becollective"


async def test_build_edge_returns_the_forum_shape():
    async with SessionLocal() as s:
        tid = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id
        d = await build_edge(s, tid, "mtd")
        # same envelope as the Forum / beCollective payload
        assert set(d) >= {"status", "members_total", "roster", "kpis", "deck", "funnel",
                          "renewals", "event", "billing", "pulse", "mg"}
        # every KPI lives in the edge_ namespace (no Forum/bc key leaks)
        assert d["kpis"] and all(k["key"].startswith("edge_") for k in d["kpis"])
        assert all((k.get("drill") or "edge_").startswith("edge_") for k in d["kpis"])


async def test_edge_roster_drill_reads_edge_members():
    """The Active-Members drill (edge_roster) returns the rich roster from edge_member records —
    what the RosterDrawer opens in live mode."""
    from app.models import Business, MetricRecord
    from app.services.lineage import metric_detail
    async with SessionLocal() as s:
        tid = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == tid, Business.key == "springb"))).scalar_one()
        s.add(MetricRecord(tenant_id=tid, business_id=biz.id, source="ghl", kind="edge_member",
                           external_id="edrill1", name="Edge Drill Member", status="active", segment="edge",
                           meta={"membership": {"member_kind": "primary", "total_cost": 6500, "payment": "monthly"}}))
        await s.commit()
        d = await metric_detail(s, tid, "edge_roster", "mtd")
        assert d["view"] == "roster" and d["label"] == "The Edge · Roster"
        assert any(r["name"] == "Edge Drill Member" and r["seg"] == "EDGE" for r in d["rows"])
        assert d["summary"]["primary"] >= 1
        # cleanup so the shared springb DB isn't polluted for sibling tests
        rec = (await s.execute(select(MetricRecord).where(MetricRecord.external_id == "edrill1"))).scalar_one()
        await s.delete(rec)
        await s.commit()


async def test_edge_tab_registered_for_springb():
    from app.services.tabs import tenant_tabs
    async with SessionLocal() as s:
        tid = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id
        tabs = await tenant_tabs(s, tid)
        assert "edge" in tabs and "forum" in tabs and "becollective" in tabs


# ── the location, the value, and the money ────────────────────────────────────────────────
#
# The Edge sync reported zero members from the day it shipped until 2026-10-07. It was reading
# the Forum's GHL location, where the field has never existed, on a stored belief that The Edge
# was "a segment of the same GHL". Two rounds of ever-cleverer name matching were spent on a
# location that could not have matched. These hold the three things that were actually wrong.

def test_the_forums_plain_status_field_is_not_the_edges():
    """The Forum's location has its own field called 'Status' with an IDENTICAL picklist, sitting
    beside Member Type and 'Stripe Account: The Forum Account'. 52 contacts read Active on it and
    NOT ONE of them pays for The Edge. Matching "status" alone would have produced 52 confidently
    wrong members -- worse than the zero it replaced, because zero looks broken and 52 looks fine.
    """
    from app.services.sync import _find_edge_status_field

    forum = {"id": "f1", "name": "Status", "fieldKey": "contact.status"}
    edge = {"id": "e1", "name": "The Edge - Status", "fieldKey": "contact.the_edge__status"}
    assert _find_edge_status_field([forum]) is None
    assert (_find_edge_status_field([edge]) or {}).get("id") == "e1"
    # Both present, either order: the Edge field wins and the Forum's is never the answer.
    assert (_find_edge_status_field([forum, edge]) or {}).get("id") == "e1"
    assert (_find_edge_status_field([edge, forum]) or {}).get("id") == "e1"
    # An explicit override still wins, by key.
    assert (_find_edge_status_field([forum], "contact.status") or {}).get("id") == "f1"


def test_inactive_does_not_read_as_active():
    """The picklist is New / Prospective / Committed / Onboarding / Active / Inactive / Nurture /
    Lost. "Inactive" CONTAINS "active", and the sync matched with `in`, so the first member ever
    marked Inactive would have gone on counting as current. Nothing is Inactive today, which is
    the only reason that was not already wrong -- so it is asserted rather than observed.
    """
    import inspect

    from app.services import sync

    src = inspect.getsource(sync.sync_edge_ghl)
    assert "status == active_val" in src, "the status comparison is no longer exact"
    assert "active_val in status" not in src, "a contains-match is back; Inactive counts as Active"

    for value, expected in [("Active", True), ("active", True), ("Inactive", False),
                            ("Nurture", False), ("Lost", False), ("", False)]:
        assert (bool(value) and value.strip().lower() == "active") is expected, value


def test_stripe_never_asks_for_five_levels_of_expand():
    """`list_subscriptions` expanded data.items.data.price.product -- five levels. Stripe refuses
    the whole request with 400 "You cannot expand more than 4 levels of a property", the caller
    swallowed it and carried on with an empty list, so NO legacy subscription ever reached the
    database: no Edge next-payment dates, and no legacy MRR for the Forum either. It returned 142
    subscriptions the moment the expand was shortened.

    Counted on the real string, because the failure was silent at every other layer.
    """
    import inspect

    from app.integrations import stripe_legacy

    src = inspect.getsource(stripe_legacy.list_subscriptions)
    checked = 0
    for line in [ln for ln in src.splitlines() if "expand[]" in ln]:
        for path in [p for p in line.split('"') if p.startswith("data.")]:
            checked += 1
            assert path.count(".") + 1 <= 4, f"{path} is {path.count('.') + 1} levels; Stripe allows 4"
    assert checked, "no expand paths found to check -- this guard has stopped guarding"


def test_the_product_name_survives_the_shorter_expand():
    """The name is what classifies a subscription as Edge, and it lived in the level that had to
    go. It is fetched separately and grafted back on, so sub_plan_name is unchanged."""
    from app.integrations.stripe_legacy import _graft_product_names, sub_plan_name

    subs = [{"items": {"data": [{"price": {"product": "prod_1", "nickname": None}}]}}]
    _graft_product_names(subs, {"prod_1": "The Edge Monthly"})
    assert sub_plan_name(subs[0]) == "The Edge Monthly"
    # A product we could not name falls back to the nickname rather than losing the row.
    subs2 = [{"items": {"data": [{"price": {"product": "prod_x", "nickname": "The Edge Split Pay"}}]}}]
    _graft_product_names(subs2, {})
    assert sub_plan_name(subs2[0]) == "The Edge Split Pay"


async def test_a_comped_member_is_not_a_missed_payment():
    """Most Edge members are comped (Connor, 2026-10-07): 88 read Active and 19 have ever paid. A
    member with no subscription and no charge is the NORMAL case, so the roster says `comped`
    rather than leaving two empty columns for somebody to read as a billing failure -- and the
    book counts only what is actually billed, because summing the membership price across every
    member would state a number this programme has never invoiced.
    """
    import datetime as dt

    from app.models import Business, MetricRecord
    from app.services.lineage import metric_detail

    async with SessionLocal() as s:
        tid = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == tid, Business.key == "springb"))).scalar_one()
        mem = dict(tenant_id=tid, business_id=biz.id, source="ghl", kind="edge_member",
                   status="active", segment="edge")
        s.add_all([
            MetricRecord(**mem, external_id="ecomp1", name="Comped Member",
                         email="comped@example.com",
                         meta={"membership": {"member_kind": "primary", "total_cost": 997}}),
            MetricRecord(**mem, external_id="epay1", name="Paying Member",
                         email="payer@example.com",
                         meta={"membership": {"member_kind": "primary", "total_cost": 997}}),
            MetricRecord(tenant_id=tid, business_id=biz.id, source="stripe_legacy",
                         kind="edge_payment", external_id="ech1", name="Paying Member",
                         email="payer@example.com", amount=997, status="succeeded",
                         occurred_on=dt.date(2026, 10, 6), segment="edge"),
            MetricRecord(tenant_id=tid, business_id=biz.id, source="stripe_legacy",
                         kind="edge_subscription", external_id="esub1", name="Paying Member",
                         email="payer@example.com", amount=997, status="active", segment="edge",
                         meta={"next_payment_date": "2026-11-06", "plan_name": "The Edge Monthly"}),
        ])
        await s.commit()
        try:
            d = await metric_detail(s, tid, "edge_roster", "mtd")
            by_name = {r["name"]: r for r in d["rows"]}
            payer, comped = by_name["Paying Member"], by_name["Comped Member"]

            assert payer["comped"] is False
            # {date, amount, url}, not a bare string: RosterDrawer's PayCell reads p.date and
            # p.amount off the value and renders an empty cell for anything else.
            assert payer["last_payment"]["date"] == "2026-10-06"
            assert float(payer["last_payment"]["amount"]) == 997.0
            assert payer["next_payment"]["date"] == "2026-11-06"
            assert float(payer["next_payment"]["amount"]) == 997.0
            assert float(payer["amount"]) == 997.0

            assert comped["comped"] is True
            assert comped["last_payment"] is None and comped["next_payment"] is None

            assert d["summary"]["paying"] >= 1 and d["summary"]["comped"] >= 1
            # The comped member's 997 is NOT in the book.
            assert d["summary"]["book"] == round(
                sum(float(r["amount"] or 0) for r in d["rows"]
                    if r["kind"] != "admin" and not r["comped"]), 2)
        finally:
            for ext in ("ecomp1", "epay1", "ech1", "esub1"):
                rec = (await s.execute(select(MetricRecord).where(
                    MetricRecord.external_id == ext))).scalar_one_or_none()
                if rec is not None:
                    await s.delete(rec)
            await s.commit()
