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


async def test_edge_tab_registered_for_springb():
    from app.services.tabs import tenant_tabs
    async with SessionLocal() as s:
        tid = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one().id
        tabs = await tenant_tabs(s, tid)
        assert "edge" in tabs and "forum" in tabs and "becollective" in tabs
