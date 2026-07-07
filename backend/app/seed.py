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
                          "payment": pay, "stage": "member", "contact_id": f"mem-{i+1:03d}"})

            # Monthly subscriptions (drives MRR) — 16 active, 2 past due. Linked to
            # the first members by contact so the roster's payment column joins.
            for i in range(18):
                _mr(kind="subscription", external_id=f"sub-{i+1:03d}",
                    name=f"Member {i+1:02d}", amount=Decimal(250),
                    status="past_due" if i >= 16 else "active",
                    segment="forum", meta={"contact_id": f"mem-{i+1:03d}"})

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
