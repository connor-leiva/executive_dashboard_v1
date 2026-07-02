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
    PLSnapshot, CashSnapshot, MetricRecord,
)
from .security import hash_pw
from .services.metrics import _period_range, _pl_period

OWNER_EMAIL = "spring@springb.com"
OWNER_PASSWORD = "springtime"   # dev only — change after first login


def _spread(total: int, n: int) -> list[int]:
    """Split `total` into n ints that sum exactly to total."""
    base = total // n
    out = [base] * n
    out[0] += total - base * n
    return out


# Placeholder tiles shown only until Go High Level syncs; once connected, the
# Forum panel is rebuilt from live data (members / ARR / renewals / onboarded).
SPRINGB_CONFIG = {
    "trend": [21, 26, 16, 11, 27, 13, 15],
    "ops": [
        {"label": "Active Members", "value": "—", "sub": "connect Go High Level"},
        {"label": "Forum ARR", "value": "—"},
        {"label": "New Members", "value": "—"},
        {"label": "Renewals Due", "value": "—"},
        {"label": "Registered", "value": "—"},
        {"label": "MRR", "value": "—"},
    ],
    "scorecard": {"members": None},
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

        ulrg = Business(tenant_id=tenant.id, key="ulrg", name="ULRG + Team", tag="Real estate",
                        status="healthy", accent="#61835E", ink="#4F6A4D", is_jv=False,
                        jv_share=Decimal("1.0"), sort_order=0, config=ULRG_CONFIG,
                        expense_run_rate_mode="manual", expense_run_rate_manual=Decimal(96000),
                        default_agent_split=Decimal("0.60"))
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
        # Go High Level (The Forum) — mapping from the live API audit (see the
        # GHL reference memory). member_tags = the official 70; forum/innercircle
        # tags split the segments; the renewals pipeline drives ARR + renewals due;
        # the sales funnel's "Won: Onboarded" stage drives new members; the next
        # event + Registered are tag-driven. Connect fills token + location_id.
        s.add(Integration(tenant_id=tenant.id, provider="ghl", business_id=springb.id,
                          status="disconnected",
                          config={
                              "location_id": "",
                              "member_tags": ["inner circle active", "the forum active",
                                              "forumadmin", "member: secondary",
                                              "inner circle active add on"],
                              "forum_tags": ["the forum active", "member: secondary", "forumadmin"],
                              "innercircle_tags": ["inner circle active", "inner circle active add on"],
                              "renewals_pipeline_match": "renewals",
                              "onboarded_stage_match": "won: onboarded",
                              # The Forum focused view (Part 2 config):
                              "sales_pipeline_match": "sales funnel",
                              "renewal_stage_status": {"committed to renew": "committed",
                                                       "in conversation": "talking",
                                                       "at risk": "risk"},
                              "default_contract_value": 12000,
                              "event_tag": "the forum q3 2026",
                              "event_name": "Park City, UT",
                              "event_title": "The Forum · Q3 2026",
                              "event_dates": "Sep 18–20, 2026",
                              "event_date": "2026-09-18",
                              "prior_event_pace": 34,
                          }))

        # ── P&L snapshots (current period) + a prior month (for a real MoM) + cash.
        prior_start, prior_end = _period_range("last_month")
        cur_start, cur_end = _pl_period("mtd")   # calendar key, matches the dashboard read
        for key, b in biz.items():
            s.add(PLSnapshot(tenant_id=tenant.id, business_id=b.id, period_start=cur_start,
                             period_end=cur_end, source="qbo", realm_id=f"realm-{key}",
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

            # Pending (22): pipeline sums 8.1M, GCI sums 165k, all expected to
            # close this month (feeds the Projection lens). 8 went UC this period.
            pend_parts = _spread(8_100_000, 22)
            pend_gci = _spread(165_000, 22)
            for i in range(22):
                # Expected closes spread across the month (many AFTER today) so the
                # projection spans the whole period, not just up to today.
                exp_close = start + dt.timedelta(days=(i % 27) + 1)
                s.add(Transaction(
                    tenant_id=tenant.id, business_id=ulrg.id, source="sisu",
                    external_id=f"txn-pending-{i+1:03d}", side="buy" if i % 2 else "sell",
                    status="pending", gci=Decimal(pend_gci[i]), sale_price=Decimal(pend_parts[i]),
                    address=f"{500+i} Oak Ave", buyer_name=f"Pending Buyer {i+1}",
                    agent_id=agents[i % 24].id,
                    contract_date=mid if i < 8 else prev_month, expected_close_date=exp_close))

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

            # ── The Forum (GHL) — representative records so the focused view
            #    renders end-to-end locally. Mirrors the live shape: 70 members
            #    across two segments, a renewal book, a recruiting funnel, an
            #    upcoming event, and a revenue-quality mix. ────────────────────
            def _mr(**kw):
                s.add(MetricRecord(tenant_id=tenant.id, business_id=springb.id,
                                   source="ghl", **kw))

            _MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            # 70 active members: 48 Forum, 22 Inner Circle.
            for i in range(70):
                ic = i >= 48
                _mr(kind="member", external_id=f"mem-{i+1:03d}",
                    name=f"Member {i+1:02d}", status="active",
                    segment="inner_circle" if ic else "forum",
                    source_url="https://app.gohighlevel.com/")

            # Memberships (billable) — renewal month, status, payment, amount.
            # 47 memberships; renewal months spread; a handful at-risk/talking.
            _renew_status = (["committed"] * 30 + ["talking"] * 12 + ["risk"] * 5)
            for i in range(47):
                ic = i >= 32
                mon = _MON[(6 + (i % 6))]          # Jul..Dec renewals
                pay = "monthly" if i % 3 == 0 else "pif"
                amt = 6000 if ic else (250 if pay == "monthly" else 3000)
                _mr(kind="membership", external_id=f"ms-{i+1:03d}",
                    name=f"Member {i+1:02d}", status="active",
                    segment="inner_circle" if ic else "forum",
                    amount=Decimal(amt),
                    meta={"renewal_month": mon, "renewal_status": _renew_status[i],
                          "payment": pay, "stage": "member"})

            # Monthly subscriptions (drives MRR) — 16 active, 2 past due.
            for i in range(18):
                _mr(kind="subscription", external_id=f"sub-{i+1:03d}",
                    name=f"Member {i+1:02d}", amount=Decimal(250),
                    status="past_due" if i >= 16 else "active",
                    segment="forum")

            # Recruiting funnel (open sales-funnel opps by stage).
            _funnel = [("New Lead", 0, 14, 0), ("Discovery", 1, 9, 12000),
                       ("Proposal", 2, 5, 12000), ("Invited", 3, 3, 12000)]
            fi = 0
            for label, pos, n, val in _funnel:
                for _ in range(n):
                    fi += 1
                    _mr(kind="recruiting", external_id=f"opp-{fi:03d}",
                        name=f"Prospect {fi:02d}", status="open",
                        amount=Decimal(val) if val else None,
                        meta={"stage": label, "stage_position": pos})

            # Onboarded this year (new members → ARR add) + a couple this month.
            for i in range(9):
                on_date = dt.date(2026, (i % 6) + 1, 12)
                if i >= 7:
                    on_date = mid
                _mr(kind="onboarded", external_id=f"onb-{i+1:03d}",
                    name=f"New Member {i+1}", occurred_on=on_date,
                    amount=Decimal(3000), segment="forum")

            # Members lost this year (ARR bridge, churned).
            for i in range(3):
                _mr(kind="membership_lost", external_id=f"lost-{i+1:03d}",
                    name=f"Former Member {i+1}", occurred_on=dt.date(2026, (i * 2) + 2, 8),
                    amount=Decimal(3000), segment="forum")

            # Event registrations for the next event: 38 members + 6 guests.
            for i in range(44):
                guest = i >= 38
                _mr(kind="registration", external_id=f"reg-{i+1:03d}",
                    name=(f"Guest {i-37}" if guest else f"Member {i+1:02d}"),
                    status="registered", segment="forum",
                    meta={"guest": guest, "event_tag": "the forum q3 2026"})

        await s.commit()

    print("[ok] Seeded tenant 'springb' for period",
          f"{start.isoformat()} -> {end.isoformat()}")
    print(f"  Owner login: {OWNER_EMAIL} / {OWNER_PASSWORD}")
    print("  Businesses: ULRG + Team, Spring B, Sympli Mortgage")


if __name__ == "__main__":
    asyncio.run(seed())
