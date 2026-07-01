"""Seed tenant #1 (Spring) + the three businesses + representative data.

Run:  python -m app.seed
Idempotent: wipes the `springb` tenant's rows and recreates them, anchored to
the CURRENT month-to-date period so the dashboard lights up against live queries.

ULRG operational figures are computed from the seeded transactions/leads (the
real Phase-1 path). Spring B + Sympli operational tiles come from each business's
brand `config` (manual until their sources connect). P&L snapshots are seeded for
all three so the financial panels, combined profit, composition, and cash render.
"""
import asyncio
import datetime as dt
from decimal import Decimal

from sqlalchemy import delete, select

from .config import settings
from .db import SessionLocal, engine
from .models import (
    Base, Tenant, Domain, User, Business, Integration, Agent, Transaction, Lead,
    PLSnapshot, CashSnapshot,
)
from .security import hash_pw
from .services.metrics import _period_range

OWNER_EMAIL = "spring@springb.com"
OWNER_PASSWORD = "springtime"   # dev only — change after first login


def _spread(total: int, n: int) -> list[int]:
    """Split `total` into n ints that sum exactly to total."""
    base = total // n
    out = [base] * n
    out[0] += total - base * n
    return out


SPRINGB_CONFIG = {
    "trend": [21, 26, 16, 11, 27, 13, 15],
    "ops": [
        {"label": "Active members", "value": "142", "sub": "beCollective"},
        {"label": "Recurring revenue", "value": "$28K", "sub": "MRR"},
        {"label": "Next Forum event", "value": "18 days"},
        {"label": "Registered", "value": "86", "sub": "of 120 seats"},
        {"label": "Member churn", "value": "3.1%", "sub": "30-day"},
        {"label": "Event margin", "value": "19%", "sub": "below target"},
    ],
    "scorecard": {"members": "142"},
}

SYMPLI_CONFIG = {
    "trend": [13, 16, 12, 19, 17, 22, 19],
    "ops": [
        {"label": "Funded loans", "value": "19", "sub": "month to date"},
        {"label": "Loan volume", "value": "$7.3M"},
        {"label": "Avg loan amount", "value": "$384K"},
        {"label": "Pre-approvals", "value": "41", "sub": "active"},
        {"label": "In underwriting", "value": "23", "sub": "locked"},
        {"label": "Pull-through rate", "value": "68%"},
    ],
    "funnel": [
        {"label": "Pre-approvals", "v": 41}, {"label": "Applications", "v": 28},
        {"label": "Locked", "v": 23}, {"label": "Funded", "v": 19},
    ],
    "scorecard": {"funded": "19", "volume": "$7.3M"},
}

ULRG_CONFIG = {"trend": [49, 44, 52, 58, 55, 64, 72]}

PL = {
    "ulrg": dict(revenue=420000, cogs=252000, gross_profit=168000, opex=96000, noi=72000, net_income=72000),
    "springb": dict(revenue=68000, cogs=31000, gross_profit=37000, opex=22000, noi=15000, net_income=15000),
    "sympli": dict(revenue=82000, cogs=41000, gross_profit=41000, opex=19000, noi=22000, net_income=22000),
}


async def _wipe(s, tid):
    for model in (Transaction, Lead, Agent, PLSnapshot, CashSnapshot, Integration,
                  Business, User, Domain):
        await s.execute(delete(model).where(model.tenant_id == tid))
    await s.execute(delete(Tenant).where(Tenant.id == tid))
    await s.commit()


async def seed():
    if settings.is_sqlite:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    start, end = _period_range("mtd")
    # A point inside the current period for close/contract dates.
    mid = start + dt.timedelta(days=min(5, (end - start).days))
    prev_month = start - dt.timedelta(days=10)   # outside the current period

    async with SessionLocal() as s:
        existing = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one_or_none()
        if existing:
            await _wipe(s, existing.id)

        tenant = Tenant(slug="springb", name="Spring")
        s.add(tenant)
        await s.flush()

        s.add(Domain(tenant_id=tenant.id, hostname="cmd.springb.com", is_primary=True))
        s.add(User(tenant_id=tenant.id, email=OWNER_EMAIL, password_hash=hash_pw(OWNER_PASSWORD),
                   name="Spring Bengtzen", role="owner"))

        ulrg = Business(tenant_id=tenant.id, key="ulrg", name="ULRG + Team", tag="Brokerage",
                        status="healthy", accent="#61835E", ink="#4F6A4D", is_jv=False,
                        jv_share=Decimal("1.0"), sort_order=0, config=ULRG_CONFIG)
        springb = Business(tenant_id=tenant.id, key="springb", name="Spring B",
                           tag="beCollective + The Forum", status="watch", accent="#FA8069",
                           ink="#CE4E29", is_jv=False, jv_share=Decimal("1.0"), sort_order=1,
                           config=SPRINGB_CONFIG)
        sympli = Business(tenant_id=tenant.id, key="sympli", name="Sympli Mortgage",
                          tag="Joint venture · 50% owned", status="opportunity", accent="#227175",
                          ink="#227175", is_jv=True, jv_share=Decimal("0.5"), sort_order=2,
                          config=SYMPLI_CONFIG)
        s.add_all([ulrg, springb, sympli])
        await s.flush()

        biz = {"ulrg": ulrg, "springb": springb, "sympli": sympli}

        # ── Integrations. Sisu is the real Phase-1 source (always connected so the
        #    sync runs it). QBO/FUB are only "connected" for the local demo's green
        #    source pills; in production they stay disconnected until truly wired
        #    (so the sync doesn't attempt them with placeholder creds).
        demo = settings.SEED_SAMPLE_OPS
        s.add(Integration(tenant_id=tenant.id, provider="sisu", business_id=ulrg.id,
                          status="connected", last_synced_at=dt.datetime.utcnow()))
        s.add(Integration(tenant_id=tenant.id, provider="fub", business_id=ulrg.id,
                          status="connected" if demo else "disconnected"))
        for key, b in biz.items():
            s.add(Integration(tenant_id=tenant.id, provider="qbo", business_id=b.id,
                              status="connected" if demo else "disconnected",
                              realm_id=f"realm-{key}"))
        s.add(Integration(tenant_id=tenant.id, provider="arive", business_id=sympli.id,
                          status="disconnected"))
        # Go High Level (Spring B) — member tags pre-filled from Connor's Forum
        # filter; add beCollective tags when segmented. Connect fills token + location_id.
        s.add(Integration(tenant_id=tenant.id, provider="ghl", business_id=springb.id,
                          status="disconnected",
                          config={"location_id": "",
                                  "member_tags": ["inner circle active", "the forum active",
                                                  "forumadmin", "member: secondary",
                                                  "inner circle active add on"],
                                  "forum_tags": [], "becollective_tags": []}))

        # ── P&L snapshots (current period) + a prior month (for a real MoM) + cash.
        prior_start, prior_end = _period_range("last_month")
        for key, b in biz.items():
            s.add(PLSnapshot(tenant_id=tenant.id, business_id=b.id, period_start=start,
                             period_end=end, source="qbo", realm_id=f"realm-{key}",
                             **{k: Decimal(v) for k, v in PL[key].items()}))
            # Prior month ~7-8% lower so the hero MoM badge reflects real movement.
            s.add(PLSnapshot(tenant_id=tenant.id, business_id=b.id, period_start=prior_start,
                             period_end=prior_end, source="qbo", realm_id=f"realm-{key}",
                             **{k: Decimal(round(v * 0.926)) for k, v in PL[key].items()}))
        s.add(CashSnapshot(tenant_id=tenant.id, business_id=None, as_of=end, amount=Decimal(340000)))

        # ── Representative ULRG operational data (gated; off in prod so the real
        #    Sisu sync is the sole source). ─────────────────────────────────────
        if settings.SEED_SAMPLE_OPS:
            # ULRG agents (31 active; first 24 produce).
            agents = []
            for i in range(31):
                a = Agent(tenant_id=tenant.id, business_id=ulrg.id, source="sisu",
                          external_id=f"sisu-agent-{i+1:02d}", name=f"Agent {i+1:02d}",
                          email=f"agent{i+1:02d}@ulrg.com", is_active=True)
                agents.append(a)
            s.add_all(agents)
            await s.flush()

            # Closed transactions (38): GCI sums 420k, volume sums 14.2M, 24 distinct agents.
            gci_parts = _spread(420000, 38)
            price_parts = _spread(14_200_000, 38)
            for i in range(38):
                s.add(Transaction(
                    tenant_id=tenant.id, business_id=ulrg.id, source="sisu",
                    external_id=f"txn-closed-{i+1:03d}", side="buy" if i % 2 else "sell",
                    status="closed", gci=Decimal(gci_parts[i]), sale_price=Decimal(price_parts[i]),
                    address=f"{100+i} Main St", buyer_name=f"Buyer {i+1}",
                    buyer_email=f"buyer{i+1}@example.com",
                    agent_id=agents[i % 24].id,
                    contract_date=mid, close_date=mid))

            # Pending (22): pipeline sums 8.1M. 8 went under contract this period.
            pend_parts = _spread(8_100_000, 22)
            for i in range(22):
                s.add(Transaction(
                    tenant_id=tenant.id, business_id=ulrg.id, source="sisu",
                    external_id=f"txn-pending-{i+1:03d}", side="buy" if i % 2 else "sell",
                    status="pending", sale_price=Decimal(pend_parts[i]),
                    address=f"{500+i} Oak Ave", buyer_name=f"Pending Buyer {i+1}",
                    agent_id=agents[i % 24].id,
                    contract_date=mid if i < 8 else prev_month))

            # Active listings (17, sell side).
            for i in range(17):
                s.add(Transaction(
                    tenant_id=tenant.id, business_id=ulrg.id, source="sisu",
                    external_id=f"txn-active-{i+1:03d}", side="sell", status="active",
                    sale_price=Decimal(380000), address=f"{900+i} Pine Rd",
                    listing_date=mid, agent_id=agents[i % 24].id))

            # Leads (680; 142 at appointment) for the funnel top.
            for i in range(680):
                s.add(Lead(tenant_id=tenant.id, business_id=ulrg.id, source="fub",
                           external_id=f"lead-{i+1:04d}",
                           stage="Appointment" if i < 142 else "Lead",
                           agent_id=agents[i % 24].id, created_at_src=mid))

        await s.commit()

    print("[ok] Seeded tenant 'springb' for period",
          f"{start.isoformat()} -> {end.isoformat()}")
    print(f"  Owner login: {OWNER_EMAIL} / {OWNER_PASSWORD}")
    print("  Businesses: ULRG + Team, Spring B, Sympli Mortgage")


if __name__ == "__main__":
    asyncio.run(seed())
