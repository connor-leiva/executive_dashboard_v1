"""Development fixture for the lead-source reports: Realtor.com leads, their activity, Sisu deals and
QuickBooks spend, on the `springb` workspace that `python -m app.seed` builds.

    python -m app.seed
    python -m scripts.lead_source_fixture

DEVELOPMENT ONLY, like app.seed: it refuses to run against a deployed database. Everything it writes
is invented -- agent names, people, deals and charges -- so the cohort, pipeline and What's Working
screens have something realistic to lay out on a laptop. None of it is any customer's data, and the
screens themselves carry no sample data: against an empty database they show their empty states.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import random
import uuid
from decimal import Decimal

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import (Agent, BookTxn, BookTxnLine, Business, CoaMap, CrmActivity, Integration, Lead,
                        Tenant, Transaction)
from app.startup_checks import is_deployed

RNG = random.Random(20260201)
TODAY = dt.datetime.now(dt.timezone.utc)
SOURCES = ["Realtor.com"] * 5 + ["Realtor.com MVIP"] * 3 + ["Market VIP via Opcity BLA"] * 3
# (name, minutes-to-first-touch median, chance a lead is ever touched, attempts, appointment rate, close rate)
AGENTS = [
    ("Maya Thornton", 3, 0.98, 7, 0.34, 0.11), ("Derek Halvorsen", 380, 0.25, 2, 0.08, 0.0),
    ("Priya Raman", 9, 0.92, 6, 0.28, 0.07), ("Jordan Ellis", 22, 0.86, 5, 0.30, 0.03),
    ("Camila Ortega", 4, 0.96, 8, 0.36, 0.12), ("Tyler Brandt", 60, 0.70, 1, 0.12, 0.05),
    ("Grace Whitfield", 14, 0.90, 5, 0.24, 0.06), ("Nate Okafor", 35, 0.82, 4, 0.18, 0.0),
]
FIRST = ["Ava", "Liam", "Sofia", "Noah", "Isla", "Ethan", "Mila", "Lucas", "Aria", "Mason", "Zoe", "Owen",
         "Hazel", "Caleb", "Nora", "Wyatt", "Leah", "Eli", "Ruby", "Jonah", "Clara", "Asher", "Ivy", "Levi"]
LAST = ["Nguyen", "Carter", "Patel", "Brooks", "Larsen", "Hughes", "Diaz", "Fischer", "Morales", "Price",
        "Sandoval", "Kimura", "Lindqvist", "Ochoa", "Barrett", "Holm", "Iverson", "Sato", "Yates", "Quinn"]


def _ts(d: dt.date, hour: int, minute: int) -> dt.datetime:
    # `hour` hours after 7am Mountain (13:00 UTC), so most leads arrive in the day and a few late.
    return dt.datetime(d.year, d.month, d.day, 13, minute, tzinfo=dt.timezone.utc) + dt.timedelta(hours=hour - 1)


async def main() -> None:
    if is_deployed():
        raise SystemExit("[refused] this is a development fixture and this is a real database.")
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one_or_none()
        if t is None:
            raise SystemExit("Run `python -m app.seed` first.")
        ulrg = (await s.execute(select(Business).where(Business.tenant_id == t.id, Business.key == "ulrg"))).scalar_one()
        for model in (CrmActivity,):
            await s.execute(delete(model).where(model.tenant_id == t.id))
        await s.execute(delete(Transaction).where(Transaction.tenant_id == t.id, Transaction.external_id.like("ls-%")))
        await s.execute(delete(Lead).where(Lead.tenant_id == t.id, Lead.external_id.like("ls-%")))
        old = [x.id for x in (await s.execute(select(BookTxn).where(BookTxn.tenant_id == t.id,
                                                                     BookTxn.qbo_id.like("ls-%")))).scalars()]
        if old:
            await s.execute(delete(BookTxnLine).where(BookTxnLine.book_txn_id.in_(old)))
            await s.execute(delete(BookTxn).where(BookTxn.id.in_(old)))
        await s.execute(delete(Agent).where(Agent.tenant_id == t.id, Agent.external_id.like("ls-%")))
        await s.execute(delete(CoaMap).where(CoaMap.tenant_id == t.id, CoaMap.business_id == ulrg.id))
        await s.commit()

        agents = []
        for i, row in enumerate(AGENTS):
            a = Agent(tenant_id=t.id, business_id=ulrg.id, source="fub", external_id=f"ls-agent-{i}",
                      name=row[0], email=f"{row[0].split()[0].lower()}@ulrg.test", is_active=True)
            s.add(a)
            agents.append((a, row))
        await s.flush()

        fub = (await s.execute(select(Integration).where(Integration.tenant_id == t.id,
                                                         Integration.provider == "fub"))).scalars().first()
        if fub is not None:
            fub.config = {**(fub.config or {}), "fub_state": {**((fub.config or {}).get("fub_state") or {}),
                                                              "account_domain": "springb"}}
        n_people = 0
        months = []
        d = dt.date(2026, 2, 1)
        while d <= TODAY.date():
            months.append(d)
            d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        for m in months:
            last_day = ((m.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)).day
            count = RNG.randint(15, 23)
            for _ in range(count):
                day = m.replace(day=RNG.randint(1, last_day))
                if day > TODAY.date():
                    continue
                agent, (name, speed, touch_p, attempts, appt_p, close_p) = RNG.choice(agents + agents[:3] + agents[4:5])
                created = _ts(day, RNG.randint(1, 15), RNG.randint(0, 59))
                if created > TODAY:
                    continue
                n_people += 1
                ext = f"ls-{n_people:04d}"
                full = f"{RNG.choice(FIRST)} {RNG.choice(LAST)}"
                age_days = (TODAY - created).days
                touched = RNG.random() < touch_p
                appt = touched and RNG.random() < appt_p
                closes = appt and age_days > 60 and RNG.random() < close_p * 3.2
                uc = appt and not closes and age_days > 25 and RNG.random() < 0.22
                stage = ("Closed" if closes else "Pending" if uc else
                         RNG.choice(["Met With Customer", "Showing Homes", "Appointment Set"]) if appt else
                         RNG.choice(["Attempted Contact", "Spoke With Customer", "Nurture", "Qualified", "Lead"])
                         if touched else RNG.choice(["Lead", "Lead", "Trash", "Unresponsive"]))
                lead = Lead(tenant_id=t.id, business_id=ulrg.id, source="fub", external_id=ext, stage=stage,
                            agent_id=agent.id, created_at_src=(created - dt.timedelta(hours=7)).date(),
                            src_created_at=created, name=full, origin=RNG.choice(SOURCES),
                            email=f"{full.lower().replace(' ', '.')}{n_people}@example.com" if RNG.random() > 0.08 else None,
                            contacted=touched, price=Decimal(RNG.randrange(280, 900) * 1000) if RNG.random() < 0.7 else None,
                            last_activity_at=created)
                s.add(lead)
                await s.flush()
                at = created
                if touched:
                    first = max(0.5, RNG.lognormvariate(0, 0.9) * speed)
                    at = created + dt.timedelta(minutes=first)
                    n_att = max(1, int(RNG.gauss(attempts, 1.5)))
                    for k in range(n_att):
                        if at > TODAY:
                            break
                        channel = "call" if k % 3 != 1 else RNG.choice(["text", "email"])
                        live = channel == "call" and RNG.random() < (0.42 if k > 0 else 0.25)
                        dur = Decimal(RNG.randint(40, 600)) if live and RNG.random() < 0.6 else Decimal(0)
                        s.add(CrmActivity(tenant_id=t.id, business_id=ulrg.id, source="fub",
                                          kind={"call": "call", "text": "text", "email": "email"}[channel],
                                          external_id=f"{ext}-{k}", person_external_id=ext, occurred_at=at,
                                          direction="outbound", channel=channel, is_live=live,
                                          duration_sec=dur if channel == "call" else None,
                                          has_note=live and dur == 0))
                        lead.last_activity_at = at
                        at += dt.timedelta(hours=RNG.choice([1, 3, 6, 20, 30, 70]))
                    # Nurture: an agent who works their pipeline keeps touching a live lead.
                    if stage not in ("Trash", "Unresponsive", "Closed") and RNG.random() < touch_p - 0.15:
                        k = n_att
                        while at < TODAY - dt.timedelta(days=1):
                            at += dt.timedelta(days=RNG.randint(3, 9))
                            if at >= TODAY:
                                break
                            k += 1
                            s.add(CrmActivity(tenant_id=t.id, business_id=ulrg.id, source="fub", kind="text",
                                              external_id=f"{ext}-n{k}", person_external_id=ext, occurred_at=at,
                                              direction="outbound", channel="text", is_live=False))
                            lead.last_activity_at = at
                if appt:
                    start = created + dt.timedelta(days=RNG.randint(2, 12), hours=2)
                    if start < TODAY:
                        outcome = RNG.choice(["met", "met", "kept", "no show", "completed", None])
                        s.add(CrmActivity(tenant_id=t.id, business_id=ulrg.id, source="fub", kind="appointment",
                                          external_id=f"{ext}-appt", person_external_id=ext, occurred_at=start,
                                          direction="unknown", channel="appt", outcome=outcome,
                                          is_live=outcome in ("met", "kept", "completed")))
                if lead.email and (closes or uc or RNG.random() < 0.5):
                    lead.sisu_contact_id = str(800000 + n_people)
                lead.sisu_checked_at = TODAY
                if closes or uc:
                    gci = Decimal(RNG.randrange(7200, 16800, 50))
                    if closes:
                        close = min(TODAY.date() - dt.timedelta(days=3), (created + dt.timedelta(days=RNG.randint(45, 150))).date())
                        api = RNG.random() < 0.4
                        pay = (gci * Decimal(str(RNG.choice([0.62, 0.65, 0.7, 0.72])))).quantize(Decimal("0.01"))
                        team, agent_fee = (Decimal("69.49"), Decimal("47.98")) if api else (Decimal(69), Decimal(49))
                        cd = gci + Decimal(295) - team - agent_fee - pay
                        s.add(Transaction(tenant_id=t.id, business_id=ulrg.id, source="sisu", external_id=f"ls-{ext}",
                                          side=RNG.choice(["buy", "buy", "sell"]), status="closed", gci=gci,
                                          sale_price=gci * 35, closed_volume=gci * 35, address=f"{RNG.randint(100, 9900)} S {RNG.choice(['Highland', 'Redwood', 'Canyon', 'Willow'])} Dr",
                                          buyer_name=full, buyer_email=lead.email, agent_id=None,
                                          contract_date=close - dt.timedelta(days=30), close_date=close,
                                          trans_fee=Decimal(295), exp_risk_team=team, exp_risk_agent=agent_fee,
                                          exp_risk_source="api" if api else "default", agent_payment=pay,
                                          team_income=gci - pay, adjusted_gci=gci, referral_fee_out=Decimal(0),
                                          company_dollar=cd, lost_signal=False, lead_id=lead.id,
                                          lead_type="realtor.com", commission_read_at=TODAY))
                    else:
                        s.add(Transaction(tenant_id=t.id, business_id=ulrg.id, source="sisu", external_id=f"ls-{ext}",
                                          side="buy", status="pending", gci=gci, sale_price=gci * 34,
                                          closed_volume=gci * 34, buyer_name=full, buyer_email=lead.email,
                                          contract_date=TODAY.date() - dt.timedelta(days=RNG.randint(3, 25)),
                                          expected_close_date=TODAY.date() + dt.timedelta(days=RNG.randint(8, 40)),
                                          lost_signal=False, lead_id=lead.id, lead_type="realtor.com"))
            # Other sources the group must NOT pick up.
            for k in range(RNG.randint(8, 12)):
                day = m.replace(day=RNG.randint(1, last_day))
                if day > TODAY.date():
                    continue
                n_people += 1
                s.add(Lead(tenant_id=t.id, business_id=ulrg.id, source="fub", external_id=f"ls-{n_people:04d}",
                           stage="Lead", agent_id=RNG.choice(agents)[0].id, created_at_src=day,
                           src_created_at=_ts(day, 3, 0), name="Other source", origin=RNG.choice(["Zillow", "Zillow Flex", "Realty.com", "Sphere"])))

        # A few of this month's seeded closings came from Realtor.com leads: link them, for the
        # Referral Flywheel drawer's lead-source chips.
        mine = [lead for lead in (await s.execute(select(Lead).where(
            Lead.tenant_id == t.id, Lead.external_id.like("ls-%"), Lead.origin.like("%ealtor%")))).scalars()][:6]
        seeded = (await s.execute(select(Transaction).where(
            Transaction.tenant_id == t.id, Transaction.external_id.like("txn-closed-%"),
            Transaction.side == "buy"))).scalars().all()
        for lead, txn in zip(mine, seeded[:5]):
            txn.lead_id = lead.id
            pay = (txn.gci * Decimal("0.7")).quantize(Decimal("0.01"))
            txn.trans_fee, txn.exp_risk_team, txn.exp_risk_agent = Decimal(295), Decimal(69), Decimal(49)
            txn.exp_risk_source, txn.agent_payment, txn.team_income = "default", pay, txn.gci - pay
            txn.company_dollar = txn.gci + Decimal(295) - Decimal(118) - pay
            txn.closed_volume, txn.lost_signal, txn.commission_read_at = txn.sale_price, False, TODAY
        for txn in seeded[5:7]:
            txn.lead_type = "Realtor.com MVIP"

        # QuickBooks: the brokerage's company, its chart, and a year of charges.
        qbo = (await s.execute(select(Integration).where(Integration.tenant_id == t.id, Integration.provider == "qbo",
                                                         Integration.business_id == ulrg.id))).scalars().first()
        qbo.status, qbo.last_synced_at = "connected", TODAY
        chart = [("365", "Realtor.com", "62000 Lead Generation:62100 General Prospecting & Marketing:62130 Internet Lead Generation:Realtor.com"),
                 ("366", "Zillow", "62000 Lead Generation:62100 General Prospecting & Marketing:62130 Internet Lead Generation:Zillow"),
                 ("386", "Facebook", "62000 Lead Generation:62100 General Prospecting & Marketing:62130 Internet Lead Generation:Facebook"),
                 ("579", "Realty.com", "62000 Lead Generation:62100 General Prospecting & Marketing:62130 Internet Lead Generation:Realty.com"),
                 ("350", "Ylopo", "62000 Lead Generation:62100 General Prospecting & Marketing:62130 Internet Lead Generation:Ylopo"),
                 ("702", "Referral Fees Paid", "51000 Cost of Sales:51200 Referral Fees Paid")]
        for acc, name, fqn in chart:
            s.add(CoaMap(tenant_id=t.id, business_id=ulrg.id, qbo_account_id=acc, qbo_account_name=name,
                         qbo_account_fqn=fqn, qbo_account_type="Expense" if acc != "702" else "Cost of Goods Sold"))
        n = 0

        async def charge(day, amount, account, vendor, payee, credit=False):
            nonlocal n
            n += 1
            tx = BookTxn(tenant_id=t.id, business_id=ulrg.id, realm_id=qbo.realm_id or "realm-ulrg", qbo_type="Purchase",
                         qbo_id=f"ls-{n}", txn_date=day, amount=Decimal(amount), payee=payee, vendor_qbo_id=vendor,
                         purchase_credit=credit, bank_account_label="Amex Business", account_qbo_id="1150040000",
                         came_categorized=True)
            s.add(tx)
            await s.flush()
            s.add(BookTxnLine(tenant_id=t.id, book_txn_id=tx.id, line_num=0, amount=Decimal(amount),
                              account_qbo_id=account, detail_type="AccountBasedExpenseLineDetail"))
        for m in months:
            await charge(m.replace(day=3), RNG.randrange(4200, 5900, 25), "365", "962", "Realtor.com")
            if RNG.random() < 0.6:
                await charge(m.replace(day=18), RNG.randrange(900, 2400, 25), "702", "1568", "Opcity, Inc")
            await charge(m.replace(day=9), RNG.randrange(600, 1500, 25), "386", "55", "Facebook Ads")
            await charge(m.replace(day=11), RNG.randrange(1800, 3100, 25), "366", "68", "Zillow.com")
        await charge(months[2].replace(day=24), 350, "365", "962", "Realtor.com", credit=True)
        await s.commit()
        print(f"[ok] lead-source fixture: {n_people} people, {n} QuickBooks charges, {len(AGENTS)} agents")


if __name__ == "__main__":
    asyncio.run(main())
