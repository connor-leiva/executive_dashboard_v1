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

# Sympli's Booked P&L mirrors the reverse-engineered May QBO structure (scaled to
# the demo's Arive commission ~$153K): Commission revenue → LO comp (~55%, the cost
# of sale) → net commission → operating costs (~29%) → NOI → Spring's 50% share. It
# reconciles to the calculated Live lens within ~1% (locked = books_closed).
PL = {
    "ulrg": dict(revenue=420000, cogs=252000, gross_profit=168000, opex=96000, noi=72000, net_income=72000),
    "springb": dict(revenue=68000, cogs=31000, gross_profit=37000, opex=22000, noi=15000, net_income=15000),
    "sympli": dict(revenue=152000, cogs=83600, gross_profit=68400, opex=44080, noi=24320, net_income=24320),
}
# Businesses whose current-period books are already closed (locked reference).
BOOKS_CLOSED = {"sympli"}


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
                          config=SYMPLI_CONFIG,
                          per_loan_share=Decimal(2100), capture_target=Decimal(60),
                          lo_comp_rate=Decimal("0.55"), opex_rate=Decimal("0.29"))
        s.add_all([ulrg, springb, sympli])
        await s.flush()

        biz = {"ulrg": ulrg, "springb": springb, "sympli": sympli}

        # ── Integrations. Sisu is the real Phase-1 source (always connected so the
        #    sync runs it). QBO/FUB are only "connected" for the local demo's green
        #    source pills; in production they stay disconnected until truly wired
        #    (so the sync doesn't attempt them with placeholder creds).
        demo = settings.SEED_SAMPLE_OPS
        s.add(Integration(tenant_id=tenant.id, provider="sisu", business_id=ulrg.id,
                          status="connected", last_synced_at=dt.datetime.utcnow(),
                          config={
                              # Resolved from Sisu's vendor directory at sync time; seeded
                              # here so the attachment flywheel renders locally. Sympli's
                              # three mortgage-LO vendor ids + cash/no-lender + lender names.
                              "sympli_mortgage_vids": [155743, 247597, 249290],
                              "cash_vids": [12, 204025],
                              "lender_names": {"155743": "Sympli Mortgage of Utah",
                                               "125147": "UMortgage-Adam", "158205": "Intercap Lending",
                                               "162203": "First Colony Mortgage", "38423": "City Creek-Jenna Kinard",
                                               "12": "Cash-No Lender", "204025": "Seller Finance"},
                              "referral_domains": ["liveutah.com"],
                          }))
        s.add(Integration(tenant_id=tenant.id, provider="fub", business_id=ulrg.id,
                          status="connected" if demo else "disconnected"))
        for key, b in biz.items():
            s.add(Integration(tenant_id=tenant.id, provider="qbo", business_id=b.id,
                              status="connected" if demo else "disconnected",
                              realm_id=f"realm-{key}"))
        # Arive is Sympli's full multi-state LOS; Spring's dashboard is Utah (ULRG's
        # market). `states` scopes the Sympli card + flywheel — widen it if the JV
        # spans states.
        s.add(Integration(tenant_id=tenant.id, provider="arive", business_id=sympli.id,
                          status="disconnected", config={"states": ["UT"]}))
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
        # beCollective — its OWN Go High Level location (separate account + token),
        # a cohort program (one-time membership, PIF/Financed). Same config schema as
        # the Forum's ghl row (plain keys), so one Settings form edits both; the sync
        # (provider "ghl_bc") writes bc_* records. Connect fills token + location_id.
        s.add(Integration(tenant_id=tenant.id, provider="ghl_bc", business_id=springb.id,
                          status="disconnected",
                          config={
                              "location_id": "",
                              "member_tags": ["be collective financed", "be collective payment complete",
                                              "be collective won onboarded group 1"],
                              "financed_tags": ["be collective financed"],
                              "sales_pipeline_match": "be collective main sales funnel",
                              "onboarded_stage_match": "won: onboarded",
                              "product_match": ["be collective membership"],
                              "event_tag": "the shift ticket purchased",
                              "event_name": "The Shift",
                              "event_title": "beCollective · The Shift",
                              "event_dates": "Oct 2026",
                              "event_date": "2026-10-15",
                              "prior_event_pace": 40,
                          }))

        # ── P&L snapshots (current period) + a prior month (for a real MoM) + cash.
        prior_start, prior_end = _period_range("last_month")
        cur_start, cur_end = _pl_period("mtd")   # calendar key, matches the dashboard read
        for key, b in biz.items():
            s.add(PLSnapshot(tenant_id=tenant.id, business_id=b.id, period_start=cur_start,
                             period_end=cur_end, source="qbo", realm_id=f"realm-{key}",
                             books_closed=(key in BOOKS_CLOSED),
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
            # Buy-side deals (19) carry a mortgage_vid so the attachment flywheel has a
            # realistic mix: Sympli picks (A), competitors, and cash (excluded). Buyer
            # emails/phones on the Sympli/no-vid ones line up with seeded Arive loans.
            gci_parts = _spread(420000, 38)
            price_parts = _spread(14_200_000, 38)
            _COMP_VIDS = [125147, 158205, 162203, 38423]   # UMortgage/Intercap/First Colony/City Creek
            jb = 0
            for i in range(38):
                is_buy = bool(i % 2)
                vid = phone = None
                if is_buy:
                    if jb < 4:            vid = 155743          # Sympli → captured (A)
                    elif jb < 7:          vid = None            # blank → captured via email (C)
                    elif jb < 15:         vid = _COMP_VIDS[(jb - 7) % 4]    # competitor → lost
                    elif jb < 18:         vid = 12              # cash → excluded from denominator
                    else:                 vid = None            # blank + no loan → lost (unknown)
                    phone = f"801200{jb:04d}"
                    jb += 1
                s.add(Transaction(
                    tenant_id=tenant.id, business_id=ulrg.id, source="sisu",
                    external_id=f"txn-closed-{i+1:03d}", side="buy" if is_buy else "sell",
                    status="closed", gci=Decimal(gci_parts[i]), sale_price=Decimal(price_parts[i]),
                    address=f"{100+i} Main St", buyer_name=f"Buyer {i+1}",
                    buyer_email=f"buyer{i+1}@example.com",
                    mortgage_vid=vid, buyer_phone=phone,
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
            # 70 active members: 48 Forum, 22 Inner Circle — each carrying the CRM
            # "Membership Details" (member type, plan, contract value, enrollment +
            # renewal, brokerage, Stripe account) that drives the rich roster view.
            _brokers = ["eXp Realty", "Keller Williams", "Compass", "Real Broker",
                        "The Agency", None, "RE/MAX", None]
            _stripe_acct = ["Legacy SB Account", "Forum Sub-Account"]
            for i in range(70):
                ic = i >= 48
                addon = (i % 9 == 8)                               # ~1 in 9 is an add-on seat
                pay = ("monthly" if i % 3 == 0 else "installments" if i % 5 == 0
                       else "quarterly" if i % 7 == 0 else "pif")
                cost = 0 if addon else (6000 if ic else 24000)
                enroll = dt.date(2025, (i % 12) + 1, min((i % 27) + 1, 28))
                renew = dt.date(2026, (i % 12) + 1, min((i % 27) + 1, 28))
                mem = {"member_type": "Add-On Member" if addon else "Primary Member",
                       "member_kind": "add_on" if addon else "primary",
                       "status": "Active", "payment": pay, "total_cost": cost,
                       "enrollment_date": enroll.isoformat(), "renewal_date": renew.isoformat(),
                       "stripe_account": _stripe_acct[i % 2]}
                brk = _brokers[i % len(_brokers)]
                if brk:
                    mem["brokerage"] = brk
                _mr(kind="member", external_id=f"mem-{i+1:03d}",
                    name=f"Member {i+1:02d}", status="active",
                    segment="inner_circle" if ic else "forum",
                    source_url="https://app.gohighlevel.com/", meta={"membership": mem})

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
                          "payment": pay, "stage": "member", "contact_id": f"mem-{i+1:03d}"})

            # ── GHL Payments (Stripe-fed) — the Cash & Billing ledger. Live shape:
            #    13 active subs (12 perpetual = $21,800 MRR + 1 "3 pay" installment
            #    $8,800), 35 succeeded charges = $141,993 with $10,250 refunded →
            #    net $131,743, 2 failed = $20,000, classified into streams. ──────
            from .services.billing import classify_stream

            _perp = [1700, 1700, 2000, 2000, 2000, 2000, 1600, 2450, 1000, 1000, 1850, 2500]  # Σ 21,800
            _sub_next = mid + dt.timedelta(days=7)     # inside the next-30-day window
            for i, amt in enumerate(_perp):
                _mr(kind="subscription", external_id=f"sub-{i+1:03d}",
                    name=f"Member {i+1:02d}", amount=Decimal(amt), status="active", segment="forum",
                    source_url="https://app.gohighlevel.com/",
                    meta={"contact_id": f"mem-{i+1:03d}", "plan_name": f"Subscription for Member {i+1:02d}",
                          "interval": "month", "sub_type": "perpetual",
                          "start_date": "2026-05-04", "end_date": None,
                          "installments_total": None, "installments_collected": None,
                          "next_payment_date": (_sub_next + dt.timedelta(days=i)).isoformat(),
                          "next_payment_amount": amt})
            _mr(kind="subscription", external_id="sub-013", name="Forum Membership 3 pay",
                amount=Decimal(8800), status="active", segment="forum",
                source_url="https://app.gohighlevel.com/",
                meta={"contact_id": "mem-013", "plan_name": "Forum Membership 3 pay - 27k",
                      "interval": "month", "sub_type": "installment",
                      "start_date": "2026-06-22", "end_date": "2026-08-22",
                      "installments_total": 3, "installments_collected": 2,
                      "next_payment_date": (mid + dt.timedelta(days=5)).isoformat(),
                      "next_payment_amount": 8800})
            # One subscription whose card is failing → past due (excluded from MRR;
            # drives the separate past-due recovery flag).
            _mr(kind="subscription", external_id="sub-014", name="Lapsed Member",
                amount=Decimal(1800), status="past_due", segment="forum",
                source_url="https://app.gohighlevel.com/",
                meta={"contact_id": "mem-014", "plan_name": "Subscription for Lapsed Member",
                      "interval": "month", "sub_type": "perpetual",
                      "start_date": "2026-03-01", "end_date": None,
                      "installments_total": None, "installments_collected": None,
                      "next_payment_date": None, "next_payment_amount": 1800})

            # Payments: (year, month, day, amount, plan_name). Streams classify from
            # the plan name. A balancing row lands the succeeded total on $141,993.
            _psucc = [
                (4, 16, 5000, "Forum Sponsorship"), (4, 17, 5000, "Forum Sponsorship"),
                (4, 23, 5000, "Forum Sponsorship"), (4, 15, 2499, "Forum VIP Guest Ticket"),
                (4, 20, 1994, "The Forum Mastermind RSVP - Q2 Scottsdale 2026"),
                (5, 3, 25000, "New Invoice"), (5, 4, 10000, "Subscription for Mark Dutton"),
                (5, 3, 10000, "Subscription for Jennifer Fetterplace"),
                (5, 4, 8500, "Subscription for Thomas Davis"), (5, 4, 8500, "Subscription for Jonathan Alfonso"),
                (5, 4, 8000, "Manual Payment"), (5, 4, 2000, "Subscription for Cortni Sweeney"),
                (5, 18, 2500, "Subscription for Kellie Revoir"), (5, 19, 2450, "Subscription for Jennifer Stickler"),
                (5, 5, 2000, "Membership for 2 - Financed"), (5, 4, 1700, "Subscription for Katie Merrill"),
                (5, 18, 1600, "Subscription for Will Tompkins"), (5, 21, 1850, "Subscription for Jillian Von ohlen"),
                (6, 22, 8800, "Forum Membership 3 pay - 27k"), (6, 22, 8800, "Forum Membership 3 pay - 27k"),
                (6, 2, 1000, "Subscription for Tyson Williams"), (6, 5, 2000, "Subscription for Renewal A"),
                (6, 10, 2000, "Subscription for Renewal B"), (6, 14, 1700, "Subscription for Renewal C"),
                (6, 20, 2500, "Subscription for Renewal D"), (6, 23, 2500, "Subscription for Renewal E"),
                (7, 4, 1700, "Subscription for Thomas Davis"), (7, 5, 2000, "Subscription for Cortni Sweeney"),
                (7, 4, 2000, "Subscription for Mark Dutton"), (7, 4, 1700, "Subscription for Jonathan Alfonso"),
                (7, 6, 2000, "Subscription for Jennifer Fetterplace"),
            ]
            _ptotal = sum(a for _, _, a, _ in _psucc)
            _psucc.append((7, 6, 141993 - _ptotal, "Subscription for Balance"))    # → exactly $141,993
            _pn = 0
            for mo, dy, amt, plan in _psucc:
                _pn += 1
                _mr(kind="payment", external_id=f"pay-{_pn:03d}", name=plan[:80], status="succeeded",
                    amount=Decimal(amt), occurred_on=dt.date(2026, mo, dy),
                    source_url="https://app.gohighlevel.com/",
                    meta={"stream": classify_stream(plan), "entity_source_name": plan,
                          "entity_source_type": "manual", "amount_refunded": 0})
            # 2 refunded (full) in May + 2 failed THIS month (the MTD recovery list —
            # dated to the current month so the month-to-date failed-charge flag stays
            # populated whenever the seed runs).
            for amt, plan in [(6000, "Forum Sponsorship"), (4250, "The Forum VIP Guest Ticket")]:
                _pn += 1
                _mr(kind="payment", external_id=f"pay-{_pn:03d}", name=plan[:80], status="refunded",
                    amount=Decimal(amt), occurred_on=dt.date(2026, 5, 6),
                    source_url="https://app.gohighlevel.com/",
                    meta={"stream": classify_stream(plan), "entity_source_name": plan,
                          "entity_source_type": "manual", "amount_refunded": amt})
            _failed_on = dt.date.today().replace(day=1)
            for amt, plan in [(10000, "Subscription for Lapsed Member"), (10000, "Membership for 2 - Financed")]:
                _pn += 1
                _mr(kind="payment", external_id=f"pay-{_pn:03d}", name=plan[:80], status="failed",
                    amount=Decimal(amt), occurred_on=_failed_on,
                    source_url="https://app.gohighlevel.com/",
                    meta={"stream": classify_stream(plan), "entity_source_name": plan,
                          "entity_source_type": "manual", "amount_refunded": 0})

            # Recruiting funnel (open Forum Main Sales Funnel opps by real stage —
            # collapsed into Applied/Appointment/VIP Guest/Contract sent/Onboarding;
            # dead/nurture stages like Unresponsive land in the footer count).
            _funnel = [("Opt In - No Application", 0, 14, 0),
                       ("Scheduled Appointment", 4, 9, 0),
                       ("VIP Guest- application submitted", 8, 6, 0),
                       ("Sent Contract: Single - PIF", 16, 5, 0),
                       ("Payment Received: Fulfillment Started", 20, 3, 0),
                       ("Unresponsive", 2, 7, 0)]
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
                    meta={"guest": guest, "event_tag": "the forum q3 2026",
                          "contact_id": (f"guest-{i}" if guest else f"mem-{i+1:03d}")})

            # ── beCollective (separate GHL instance; cohort model, bc_* kinds) ──
            for i in range(30):
                _mr(kind="bc_member", external_id=f"bcm-{i+1:03d}", name=f"BC Member {i+1:02d}",
                    status="active", segment="becollective", source_url="https://app.gohighlevel.com/")
            for i in range(25):
                pay = "monthly" if i % 2 else "pif"          # financed vs paid-in-full
                _mr(kind="bc_membership", external_id=f"bcms-{i+1:03d}", name=f"BC Member {i+1:02d}",
                    status="active", segment="becollective", amount=Decimal(6000 if pay == "pif" else 6500),
                    meta={"payment": pay, "contact_id": f"bcm-{i+1:03d}"})
            _bc_funnel = [("Opt In - No Call Booked", 0, 12), ("Scheduled Appointment - App Submitted", 2, 8),
                          ("Appointment Complete - Needs Decision", 4, 5),
                          ("Payment Sent: Financed", 6, 4), ("Payment Received - Fulfillment Started", 8, 2),
                          ("Appointment No Show / Cancel", 3, 6)]        # last → nurture footer
            _bi = 0
            for label, pos, n in _bc_funnel:
                for _ in range(n):
                    _bi += 1
                    _mr(kind="bc_recruiting", external_id=f"bcopp-{_bi:03d}", name=f"BC Prospect {_bi:02d}",
                        status="open", amount=Decimal(6000),
                        meta={"stage": label, "stage_position": pos})
            for i in range(4):
                _mr(kind="bc_onboarded", external_id=f"bconb-{i+1:03d}", name=f"BC New {i+1}",
                    occurred_on=(mid if i >= 2 else dt.date(2026, (i % 5) + 1, 10)),
                    amount=Decimal(6000), segment="becollective")
            for i in range(26):        # 20 members + 6 guests for The Shift
                guest = i >= 20
                _mr(kind="bc_registration", external_id=f"bcreg-{i+1:03d}",
                    name=(f"Guest {i-19}" if guest else f"BC Member {i+1:02d}"),
                    status="registered", segment="becollective",
                    meta={"guest": guest, "event_tag": "the shift",
                          "contact_id": (f"bcguest-{i}" if guest else f"bcm-{i+1:03d}")})

            # ── Sympli (ARIVE) loan pipeline — representative loans so the Sympli
            #    card + 3-signal flywheel render locally. source="arive", kind="loan".
            #    Funded loans carry the referral source (Utah Life = @liveutah.com) and
            #    borrower email/phone that line up with the ULRG buy-side closings. ──
            _LOS = [("jared@symplimortgage.com", "Jared Browning"),
                    ("nick@symplimortgage.com", "Nick Thompson"),
                    ("jan@symplimortgage.com", "Jan Coon")]

            def _ar(i, status, seg, amount, occurred, borrower_email, phone=None, referral=None, state="UT", lo=None):
                lo = lo or _LOS[i % 3]
                meta = {"status": status, "segment": seg, "purpose": "Purchase",
                        "mortgage_type": "Conventional" if i % 3 else "FHA",
                        "lo_email": lo[0], "lo_name": lo[1],
                        "borrower_email": borrower_email, "borrower_phone": phone or f"801555{i:04d}",
                        "property_state": state}
                if seg == "funded":     # loan-level commission economics (for the financials)
                    gr = round(amount * 0.023)          # ~2.3% lender-paid comp
                    meta.update({"gross_revenue": gr, "net_revenue": round(gr * 0.98),
                                 "compensation": gr, "comp_type": "Lender"})
                if referral:
                    meta.update(referral)
                s.add(MetricRecord(
                    tenant_id=tenant.id, business_id=sympli.id, source="arive", kind="loan",
                    external_id=f"arive-{i:04d}", name=f"Borrower {i:03d}",
                    email=borrower_email, amount=Decimal(amount), status=status, segment=seg,
                    occurred_on=occurred, source_url="https://app.myarive.com/", meta=meta))

            _ULRG_REF = {"referral_email": "grace.laubenthal@liveutah.com",
                         "referral_name": "Grace Laubenthal",
                         "buyer_agent_email": "grace.laubenthal@liveutah.com"}
            # Funded (14): 6 tie back to a ULRG buyer (3 Sympli-vid + 3 matched-by-email;
            # buyer 8 is Sympli-vid with NO loan → the vendor_no_loan gap), 4 are
            # Utah-Life-referred with no matching ULRG deal (reconciliation gap), 4 came
            # from other brokerages. Phones on the vid ones match the ULRG closings.
            funded_specs = (
                [(f"buyer{b}@example.com", _ULRG_REF, f"801200{(b // 2 - 1):04d}")
                 for b in [2, 4, 6, 10, 12, 14]]
                + [(f"extra{k+1}@example.com", _ULRG_REF, None) for k in range(4)]
                + [(f"other{k+1}@example.com", None, None) for k in range(4)])
            fund_amts = _spread(6_800_000, len(funded_specs))
            _n = 0
            for idx, (email, ref, ph) in enumerate(funded_specs):
                _n += 1
                st = "BROKER_CHECK_RECEIVED" if idx % 5 == 0 else "LOAN_FUNDED"   # both "funded"
                # Concentration mirrors prod: Jared writes most; Nick/Jan a handful.
                lo = _LOS[0] if idx < 10 else (_LOS[1] if idx < 12 else _LOS[2])
                _ar(_n, st, "funded", fund_amts[idx], mid, email, phone=ph, referral=ref, lo=lo)
            # A third of the live pipeline is Utah-Life-referred (ULRG-sourced) so the
            # pipeline "by source" view shows a real mix, not all "other".
            for i in range(41):                        # active pre-approvals
                _n += 1
                _ar(_n, "PREAPPROVED", "pipeline", 380000, mid, f"borrower{_n}@myarive.com",
                    referral=(_ULRG_REF if i % 3 == 0 else None))
            for k in range(23):                        # in underwriting / later pipeline
                _n += 1
                st = ["APPLICATION_INTAKE", "UNDERWRITING_SUBMITTED",
                      "APPROVED_WITH_CONDITION", "CLEAR_TO_CLOSE"][k % 4]
                _ar(_n, st, "pipeline", 395000, mid, f"borrower{_n}@myarive.com",
                    referral=(_ULRG_REF if k % 3 == 0 else None))
            for _ in range(5):                         # adverse / withdrawn (dead)
                _n += 1
                _ar(_n, "ADVERSE", "dead", 360000, mid, f"borrower{_n}@myarive.com")
            # Out-of-state Sympli loans (the LOS is multi-state) — excluded by the Utah
            # filter, so they never touch the Sympli card or flywheel.
            for _ in range(3):
                _n += 1
                _ar(_n, "LOAN_FUNDED", "funded", 450000, mid, f"tx{_n}@myarive.com", state="TX")

        await s.commit()

    print("[ok] Seeded tenant 'springb' for period",
          f"{start.isoformat()} -> {end.isoformat()}")
    print(f"  Owner login: {OWNER_EMAIL} / {OWNER_PASSWORD}")
    print("  Businesses: ULRG + Team, Spring B, Sympli Mortgage")


if __name__ == "__main__":
    asyncio.run(seed())
