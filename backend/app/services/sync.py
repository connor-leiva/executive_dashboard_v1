"""Per-provider sync into snapshots. Idempotent upserts keyed on
(tenant, source, external_id). QBO refresh-token rotation is persisted on every
call. Upserts target Postgres (prod); the worker does not run against SQLite.
"""
import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import select, delete, insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Integration, Transaction, Agent, Lead, PLSnapshot, SyncRun, MetricRecord
from ..security import enc, dec
from ..integrations import fub, sisu, qbo, ghl, arive, stripe_legacy


def _parse_ghl_dt(v) -> dt.date | None:
    """GHL timestamps arrive as ISO strings or epoch-ms; return the calendar date."""
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
            return dt.datetime.utcfromtimestamp(int(v) / 1000).date()
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
    except (ValueError, OverflowError, OSError):
        return None


async def _valid_access_token(s: AsyncSession, integ: Integration) -> str:
    now = dt.datetime.now(dt.timezone.utc)                 # tz-aware
    exp = integ.token_expires_at
    if exp is not None and exp.tzinfo is None:             # Postgres returns aware, SQLite naive
        exp = exp.replace(tzinfo=dt.timezone.utc)
    if exp and exp - now > dt.timedelta(minutes=2):
        return dec(integ.access_token_enc)
    tok = await qbo.refresh(dec(integ.refresh_token_enc))
    integ.access_token_enc = enc(tok["access_token"])
    integ.refresh_token_enc = enc(tok["refresh_token"])   # rotation: persist the new one
    integ.token_expires_at = now + dt.timedelta(seconds=int(tok["expires_in"]))
    await s.commit()
    return tok["access_token"]


async def _upsert_many(s: AsyncSession, model, rows: list[dict], index_elements, update_keys, chunk=500):
    """Batched INSERT ... ON CONFLICT DO UPDATE (uses `excluded` for the SET)."""
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        stmt = pg_insert(model).values(part)
        stmt = stmt.on_conflict_do_update(
            index_elements=index_elements,
            set_={k: stmt.excluded[k] for k in update_keys},
        )
        await s.execute(stmt)
    await s.commit()


_TXN_UPDATE_KEYS = [
    "side", "status", "gci", "agent_commission", "sale_price", "address", "buyer_name",
    "buyer_email", "mortgage_vid", "buyer_email2", "buyer_phone",
    "agent_id", "contract_date", "close_date", "expected_close_date",
    "appt_set_date", "lead_date", "listing_date", "sisu_status_code",
]


async def sync_sisu(s: AsyncSession, tenant_id: uuid.UUID, integ: Integration):
    """Fetch the whole team's clients from Sisu (concurrently) and batch-upsert.
    Also refreshes the vendor directory → the attach-flywheel config (which mortgage
    vendor ids are Sympli / cash, and the vid→lender-name map)."""
    business_id = integ.business_id

    def _prog(done, total, n):
        print(f"[sisu] page {done}/{total} · {n} rows", flush=True)

    # Vendor directory → resolve Sympli / cash vids + lender names (best-effort; the
    # flywheel reads these off the Sisu integration config). Seeds referral_domains.
    try:
        vendors = await sisu.get_team_vendors()
        if vendors:
            vc = sisu.resolve_vendor_config(vendors)
            cfg = dict(integ.config or {})
            cfg.update(vc)
            cfg.setdefault("referral_domains", ["liveutah.com"])
            integ.config = cfg
            await s.commit()
            print(f"[sisu] vendors: {len(vc['sympli_mortgage_vids'])} sympli, "
                  f"{len(vc['cash_vids'])} cash, {len(vc['lender_names'])} lenders", flush=True)
    except Exception as e:  # noqa: BLE001 — never fail the sync on the vendor pull
        print(f"[sisu] vendor sync skipped: {e}", flush=True)

    mapped, agents = await sisu.fetch_all_clients(progress=_prog)
    print(f"[sisu] fetched {len(mapped)} transactions, {len(agents)} agents", flush=True)

    # Enrich agent_commission (= GCI − company dollar) for the financials-relevant
    # subset via the per-deal commission-info endpoint. Best-effort.
    try:
        def _cprog(done, total):
            print(f"[sisu] commissions {done}/{total}", flush=True)
        n = await sisu.enrich_commissions(mapped, progress=_cprog)
        print(f"[sisu] enriched {n} commissions", flush=True)
    except Exception as e:  # noqa: BLE001 — never fail the sync on commission enrichment
        print(f"[sisu] commission enrichment skipped: {e}", flush=True)

    # 1) Batch-upsert the agent roster.
    agent_rows = [
        dict(tenant_id=tenant_id, business_id=business_id, source="sisu",
             external_id=a["external_id"], name=a["name"], email=a.get("email"),
             is_active=a.get("is_active", True))
        for a in agents.values()
    ]
    await _upsert_many(s, Agent, agent_rows, ["tenant_id", "source", "external_id"],
                       ["name", "email", "is_active"])
    print(f"[sisu] upserted {len(agent_rows)} agents", flush=True)

    agent_map = {
        a.external_id: a.id
        for a in (await s.execute(
            select(Agent).where(Agent.tenant_id == tenant_id, Agent.source == "sisu"))
        ).scalars().all()
    }

    # 2) Batch-upsert transactions.
    txn_rows = [
        dict(tenant_id=tenant_id, business_id=business_id, source="sisu",
             external_id=t["external_id"], side=t.get("side"), status=t["status"],
             gci=t.get("gci"), agent_commission=t.get("agent_commission"),
             sale_price=t.get("sale_price"), address=t.get("address"),
             buyer_name=t.get("buyer_name"), buyer_email=t.get("buyer_email"),
             mortgage_vid=t.get("mortgage_vid"), buyer_email2=t.get("buyer_email2"),
             buyer_phone=t.get("buyer_phone"),
             agent_id=agent_map.get(t.get("agent_external_id")),
             contract_date=t.get("contract_date"), close_date=t.get("close_date"),
             expected_close_date=t.get("expected_close_date"),
             appt_set_date=t.get("appt_set_date"), lead_date=t.get("lead_date"),
             listing_date=t.get("listing_date"),
             sisu_status_code=t.get("sisu_status_code"))
        for t in mapped
    ]
    await _upsert_many(s, Transaction, txn_rows, ["tenant_id", "source", "external_id"],
                       _TXN_UPDATE_KEYS)
    print(f"[sisu] upserted {len(txn_rows)} transactions", flush=True)
    return len(agent_rows) + len(txn_rows)


async def sync_fub(s: AsyncSession, tenant_id: uuid.UUID, business_id: uuid.UUID) -> int:
    n = 0
    # Agents (FUB users) first.
    for raw in await fub.fub_users():
        n += 1
        u = fub.map_user(raw)
        await s.execute(pg_insert(Agent).values(
            tenant_id=tenant_id, business_id=business_id, source="fub",
            external_id=u["external_id"], name=u["name"], email=u.get("email"),
            is_active=u.get("is_active", True),
        ).on_conflict_do_update(
            index_elements=["tenant_id", "source", "external_id"],
            set_={"name": u["name"], "email": u.get("email"), "is_active": u.get("is_active", True)},
        ))
    await s.commit()

    agent_map = {
        a.external_id: a.id
        for a in (await s.execute(
            select(Agent).where(Agent.tenant_id == tenant_id, Agent.source == "fub"))
        ).scalars().all()
    }
    # Leads (FUB people).
    for raw in await fub.fub_people():
        n += 1
        p = fub.map_person(raw)
        await s.execute(pg_insert(Lead).values(
            tenant_id=tenant_id, business_id=business_id, source="fub",
            external_id=p["external_id"], stage=p.get("stage"),
            agent_id=agent_map.get(p.get("agent_external_id")),
            created_at_src=p.get("created_at_src"),
        ).on_conflict_do_update(
            index_elements=["tenant_id", "source", "external_id"],
            set_={"stage": p.get("stage"), "agent_id": agent_map.get(p.get("agent_external_id"))},
        ))
    await s.commit()
    return n


async def _metric_snapshot(s: AsyncSession, tenant_id, business_id, source: str, kind: str, rows: list[dict]):
    """Replace the prior record set of (source, kind) for this business (so drops
    fall out). Dialect-agnostic INSERT (no ON CONFLICT — delete-then-insert), so the
    metric syncs are exercisable against SQLite in tests, not just Postgres."""
    await s.execute(delete(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == source, MetricRecord.kind == kind))
    for i in range(0, len(rows), 500):
        await s.execute(insert(MetricRecord).values(rows[i:i + 500]))
    await s.commit()


async def _ghl_snapshot(s: AsyncSession, tenant_id, business_id, kind: str, rows: list[dict]):
    await _metric_snapshot(s, tenant_id, business_id, "ghl", kind, rows)


async def sync_ghl(s: AsyncSession, tenant_id: uuid.UUID, integ: Integration):
    """Snapshot The Forum from Go High Level into metric_record, across the three
    surfaces the team actually uses (see the reference audit):
      • members      — union of member_tags (the official 70), segmented Forum/IC
      • registration — contacts tagged for the next event (event_tag)
      • membership   — open opps in the renewals pipeline → ARR + renewal month
      • onboarded    — sales-funnel opps in the "Won: Onboarded" stage → new members
      • subscription — active GHL subscriptions → MRR (monthly-payer subset)
    Members/registration/opps are the contract; subscriptions degrade to a skip if
    the payments scope is missing, so that never fails the whole sync."""
    cfg = integ.config or {}
    location_id = cfg.get("location_id")
    member_tags = {t.lower() for t in cfg.get("member_tags", [])}
    # Segmentation defaults so an already-connected integration (whose stored config
    # predates these keys) still splits Forum vs Inner Circle without a reconfig.
    forum_tags = {t.lower() for t in (cfg.get("forum_tags") or
                  ["the forum active", "member: secondary", "forumadmin"])}
    ic_tags = {t.lower() for t in (cfg.get("innercircle_tags") or
               ["inner circle active", "inner circle active add on"])}
    event_tag = (cfg.get("event_tag") or "").lower().strip()
    renewals_match = (cfg.get("renewals_pipeline_match") or "renewals").lower()
    onboarded_match = (cfg.get("onboarded_stage_match") or "won: onboarded").lower()
    sales_match = (cfg.get("sales_pipeline_match") or "sales").lower()
    token = dec(integ.access_token_enc) if integ.access_token_enc else None
    if not (location_id and member_tags and token):
        raise ValueError("Go High Level needs a token, location_id, and member_tags in config.")

    biz = integ.business_id

    # 1) Contacts → members (tag union, segmented) + event registrations (tag).
    #    A registration whose contact is NOT a member is a guest (prospect seat).
    contacts = await ghl.get_contacts(token, location_id)
    members, regs = [], []
    for c in contacts:
        tset = set(ghl.contact_tags(c))
        is_member = bool(tset & member_tags)
        cid = str(c.get("id"))
        base = dict(tenant_id=tenant_id, business_id=biz, source="ghl",
                    external_id=cid, name=ghl.contact_name(c)[:200],
                    email=(c.get("email") or None),
                    source_url=ghl.contact_url(location_id, c.get("id")))
        if is_member:
            members.append({**base, "kind": "member", "status": "active",
                            "segment": ghl.member_segment(tset, forum_tags, ic_tags)})
        if event_tag and event_tag in tset:
            regs.append({**base, "kind": "registration", "status": "registered",
                         "meta": {"event_tag": event_tag, "guest": not is_member, "contact_id": cid}})
    await _ghl_snapshot(s, tenant_id, biz, "member", members)
    await _ghl_snapshot(s, tenant_id, biz, "registration", regs)
    n_records = len(members) + len(regs)
    print(f"[ghl] {len(members)} members, {len(regs)} registered for '{event_tag}' "
          f"(from {len(contacts)} contacts)", flush=True)

    # 2) Opportunities → memberships (renewals pipeline) + onboarded (sales funnel).
    try:
        pipelines = await ghl.get_pipelines(token, location_id)
        stage_name = {st.get("id"): st.get("name") for p in pipelines for st in (p.get("stages") or [])}
        ren_ids = {p.get("id") for p in pipelines if renewals_match in (p.get("name") or "").lower()}
        sales_ids = {p.get("id") for p in pipelines if sales_match in (p.get("name") or "").lower()}
        # Stage order within the sales pipeline → funnel position (top = 0).
        stage_pos = {st.get("id"): i for p in pipelines if p.get("id") in sales_ids
                     for i, st in enumerate(p.get("stages") or [])}
        opps = await ghl.get_opportunities(token, location_id)
        memberships, onboarded, recruiting, lost = [], [], [], []
        for o in opps:
            stage = (stage_name.get(o.get("pipelineStageId")) or "").strip()
            cid = str(o.get("contactId") or "")
            base = dict(tenant_id=tenant_id, business_id=biz, source="ghl",
                        external_id=str(o.get("id")), name=ghl.opp_name(o)[:200],
                        source_url=ghl.contact_url(location_id, o.get("contactId")))
            if o.get("pipelineId") in ren_ids and o.get("status") == "open":
                memberships.append({**base, "kind": "membership", "status": "active",
                                    "amount": float(o.get("monetaryValue") or 0),
                                    "meta": {"renewal_month": stage, "contact_id": cid}})
            elif o.get("pipelineId") in ren_ids and o.get("status") == "lost":
                # A member who didn't renew → ARR-bridge churn input.
                lost.append({**base, "kind": "membership_lost", "status": "lost",
                             "amount": float(o.get("monetaryValue") or 0),
                             "occurred_on": _parse_ghl_dt(o.get("lastStatusChangeAt")),
                             "meta": {"stage": stage, "contact_id": cid}})
            elif onboarded_match in stage.lower():
                onboarded.append({**base, "kind": "onboarded", "status": o.get("status") or "won",
                                  "occurred_on": _parse_ghl_dt(o.get("lastStatusChangeAt")),
                                  "amount": float(o.get("monetaryValue") or 0),
                                  "meta": {"stage": stage, "contact_id": cid}})
            elif o.get("pipelineId") in sales_ids and o.get("status") == "open":
                # Open recruiting opps in the sales funnel → the pipeline card.
                recruiting.append({**base, "kind": "recruiting", "status": "open",
                                   "amount": float(o.get("monetaryValue") or 0) or None,
                                   "meta": {"stage": stage,
                                            "stage_position": stage_pos.get(o.get("pipelineStageId"), 99)}})
        await _ghl_snapshot(s, tenant_id, biz, "membership", memberships)
        await _ghl_snapshot(s, tenant_id, biz, "onboarded", onboarded)
        await _ghl_snapshot(s, tenant_id, biz, "recruiting", recruiting)
        await _ghl_snapshot(s, tenant_id, biz, "membership_lost", lost)
        n_records += len(memberships) + len(onboarded) + len(recruiting) + len(lost)
        arr = sum(m["amount"] for m in memberships)
        print(f"[ghl] {len(memberships)} renewals (ARR ${arr:,.0f}), {len(onboarded)} onboarded, "
              f"{len(recruiting)} recruiting, {len(lost)} lost", flush=True)
    except Exception as e:  # noqa: BLE001 — opportunities scope optional
        print(f"[ghl] opportunities skipped: {e}", flush=True)

    # 3) Payments (Stripe via GHL) → cash / failures / MRR. Best effort; needs the
    #    payments scope. Transactions first (they feed installment counts), then the
    #    enriched subscriptions. See spring-command-center-SPEC-forum-billing.md.
    from .billing import classify_stream, classify_installment, next_charge_date
    stream_overrides = cfg.get("stream_overrides") or {}
    installment_cfg = {"installment_plan_names": cfg.get("installment_plan_names") or []}
    try:
        txns = await ghl.ghl_transactions(token, location_id)
        pay_rows, sub_succeeded = [], {}
        for t in txns:
            status = ghl.txn_status(t)
            sub_id = t.get("subscriptionId")
            name = t.get("entitySourceName")
            occurred = _parse_ghl_dt(t.get("createdAt"))
            if status == "succeeded" and sub_id:
                sub_succeeded[sub_id] = sub_succeeded.get(sub_id, 0) + 1
            pay_rows.append(dict(
                tenant_id=tenant_id, business_id=biz, source="ghl", kind="payment",
                external_id=str(t.get("_id") or t.get("chargeId") or t.get("id")),
                name=(t.get("contactName") or name or "Payment")[:200],
                email=(t.get("contactEmail") or None),
                amount=float(t.get("amount") or 0), status=status, occurred_on=occurred,
                source_url=ghl.contact_url(location_id, t.get("contactId")),
                meta={"stream": classify_stream(name, stream_overrides),
                      "entity_source_name": name,
                      "entity_source_type": t.get("entitySourceType"),
                      "subscription_id": sub_id, "charge_id": t.get("chargeId"),
                      "amount_refunded": float(t.get("amountRefunded") or 0),
                      "contact_id": str(t.get("contactId") or "")}))
        await _ghl_snapshot(s, tenant_id, biz, "payment", pay_rows)
        n_records += len(pay_rows)
        print(f"[ghl] {len(pay_rows)} payments "
              f"({sum(1 for r in pay_rows if r['status'] == 'failed')} failed)", flush=True)

        subs = await ghl.get_subscriptions(token, location_id)
        sub_rows = []
        for sub in subs:
            sid_stripe = sub.get("subscriptionId")
            plan = sub.get("entitySourceName")
            start_d, end_d = sub.get("subscriptionStartDate"), sub.get("subscriptionEndDate")
            detail = {}
            if sid_stripe or sub.get("_id"):
                try:
                    detail = await ghl.ghl_subscription_detail(token, location_id,
                                                               sub.get("_id") or sid_stripe)
                except Exception:  # noqa: BLE001 — detail optional
                    detail = {}
            interval = ghl.sub_interval(detail)
            sub_type, inst_total = classify_installment(plan, start_d, end_d, installment_cfg)
            # GHL's list omits the upcoming-payment date; derive it from the billing
            # cadence (start date + interval) so forward-billing / next-30 isn't $0.
            npd = next_charge_date(start_d, interval, dt.date.today()) if ghl.sub_is_active(sub) else None
            sub_rows.append(dict(
                tenant_id=tenant_id, business_id=biz, source="ghl", kind="subscription",
                external_id=str(sub.get("_id") or sid_stripe or sub.get("id")),
                name=(sub.get("contactName") or plan or "Subscription")[:200],
                email=(sub.get("contactEmail") or None),
                amount=ghl.sub_monthly_amount(sub),
                status="active" if ghl.sub_is_active(sub) else (sub.get("status") or "inactive").lower(),
                source_url=ghl.contact_url(location_id, sub.get("contactId")),
                meta={"raw_status": sub.get("status"), "contact_id": str(sub.get("contactId") or ""),
                      "plan_name": plan, "interval": interval,
                      "start_date": (str(start_d)[:10] if start_d else None),
                      "end_date": (str(end_d)[:10] if end_d else None),
                      "sub_type": sub_type, "installments_total": inst_total,
                      "installments_collected": sub_succeeded.get(sid_stripe, 0),
                      "next_payment_date": (npd.isoformat() if npd else None),
                      "next_payment_amount": ghl.sub_monthly_amount(sub)}))
        await _ghl_snapshot(s, tenant_id, biz, "subscription", sub_rows)
        n_records += len(sub_rows)
        active_n = sum(1 for r in sub_rows if r["status"] == "active")
        print(f"[ghl] {active_n}/{len(sub_rows)} active subscriptions "
              f"({sum(1 for r in sub_rows if r['meta']['sub_type'] == 'installment')} installment)", flush=True)

        # 4) Payment type on memberships: monthly if the member has a live
        #    (active/past_due) subscription (match on contact), else PIF.
        payers = {r["meta"]["contact_id"] for r in sub_rows
                  if r["status"] in ("active", "past_due") and r["meta"].get("contact_id")}
        mrs = (await s.execute(select(MetricRecord).where(
            MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == biz,
            MetricRecord.source == "ghl", MetricRecord.kind == "membership"))).scalars().all()
        matched = 0
        for m in mrs:
            meta = dict(m.meta or {})
            is_monthly = meta.get("contact_id") in payers
            meta["payment"] = "monthly" if is_monthly else "pif"
            m.meta = meta
            matched += 1 if is_monthly else 0
        await s.commit()
        print(f"[ghl] payment type: {matched}/{len(mrs)} memberships monthly "
              f"({len(payers)} live sub payers)", flush=True)
    except Exception as e:  # noqa: BLE001 — scope/endpoint optional
        print(f"[ghl] subscriptions skipped: {e}", flush=True)

    # 5) Registration pace snapshot (append-only: one row per event per day).
    if event_tag:
        try:
            today = dt.date.today()
            days_out = None
            if cfg.get("event_date"):
                try:
                    days_out = (dt.date.fromisoformat(str(cfg["event_date"])) - today).days
                except (ValueError, TypeError):
                    days_out = None
            member_regs = sum(1 for r in regs if not (r["meta"] or {}).get("guest"))
            ext = f"{event_tag}:{today.isoformat()}"
            row = (await s.execute(select(MetricRecord).where(
                MetricRecord.tenant_id == tenant_id, MetricRecord.source == "ghl",
                MetricRecord.kind == "reg_count", MetricRecord.external_id == ext))).scalar_one_or_none()
            if row:
                row.amount = member_regs
                row.meta = {"event_tag": event_tag, "days_out": days_out}
            else:
                s.add(MetricRecord(tenant_id=tenant_id, business_id=biz, source="ghl",
                                   kind="reg_count", external_id=ext, amount=member_regs,
                                   occurred_on=today, meta={"event_tag": event_tag, "days_out": days_out}))
            await s.commit()
        except Exception as e:  # noqa: BLE001
            print(f"[ghl] reg_count skipped: {e}", flush=True)

    return n_records


async def sync_becollective_ghl(s: AsyncSession, tenant_id: uuid.UUID, integ: Integration) -> int:
    """Snapshot beCollective from its OWN Go High Level location into bc_* metric
    records. beCollective is a separate GHL account (own location + token), and a
    cohort program (one-time membership, PIF/Financed) — so, unlike The Forum, there
    are no renewals pipeline and no subscriptions. The surfaces:
      • bc_member       — contacts carrying an active-member tag
      • bc_registration — contacts tagged for the next event (guest = non-member)
      • bc_membership   — won-onboarded opps → contract value + PIF/Financed split
      • bc_onboarded    — the same won-onboarded opps, dated → new members in-period
      • bc_recruiting   — open opps in the sales funnel → the pipeline funnel
    Records are written under source='ghl' with bc_-prefixed kinds, so they never
    collide with the Forum's records on the shared Spring B business."""
    cfg = integ.config or {}
    location_id = cfg.get("location_id")
    member_tags = {t.lower() for t in (cfg.get("member_tags") or [])}
    # Which member tag(s) mean a payment plan (Financed) vs paid-in-full.
    financed_tags = {t.lower() for t in (cfg.get("financed_tags") or ["be collective financed"])}
    event_tag = (cfg.get("event_tag") or "").lower().strip()
    onboarded_match = (cfg.get("onboarded_stage_match") or "won: onboarded").lower()
    sales_match = (cfg.get("sales_pipeline_match") or "be collective main sales funnel").lower()
    token = dec(integ.access_token_enc) if integ.access_token_enc else None
    if not (location_id and member_tags and token):
        raise ValueError("beCollective GHL needs a token, location_id, and member_tags in config.")

    biz = integ.business_id

    # 1) Contacts → members (tag union) + registrations (event tag) + financed set.
    contacts = await ghl.get_contacts(token, location_id)
    members, regs, financed_contacts = [], [], set()
    for c in contacts:
        tset = set(ghl.contact_tags(c))
        is_member = bool(tset & member_tags)
        cid = str(c.get("id"))
        if tset & financed_tags:
            financed_contacts.add(cid)
        base = dict(tenant_id=tenant_id, business_id=biz, source="ghl",
                    external_id=cid, name=ghl.contact_name(c)[:200],
                    email=(c.get("email") or None),
                    source_url=ghl.contact_url(location_id, c.get("id")))
        if is_member:
            members.append({**base, "kind": "bc_member", "status": "active", "segment": "becollective"})
        if event_tag and event_tag in tset:
            regs.append({**base, "kind": "bc_registration", "status": "registered",
                         "meta": {"event_tag": event_tag, "guest": not is_member, "contact_id": cid}})
    await _ghl_snapshot(s, tenant_id, biz, "bc_member", members)
    await _ghl_snapshot(s, tenant_id, biz, "bc_registration", regs)
    n_records = len(members) + len(regs)
    print(f"[ghl_bc] {len(members)} members, {len(regs)} registered for '{event_tag}' "
          f"(from {len(contacts)} contacts)", flush=True)

    # 2) Opportunities → memberships + onboarded (won-onboarded) + recruiting (funnel).
    try:
        pipelines = await ghl.get_pipelines(token, location_id)
        stage_name = {st.get("id"): st.get("name") for p in pipelines for st in (p.get("stages") or [])}
        sales_ids = {p.get("id") for p in pipelines if sales_match in (p.get("name") or "").lower()}
        stage_pos = {st.get("id"): i for p in pipelines if p.get("id") in sales_ids
                     for i, st in enumerate(p.get("stages") or [])}
        opps = await ghl.get_opportunities(token, location_id)
        memberships, onboarded, recruiting = [], [], []
        for o in opps:
            stage = (stage_name.get(o.get("pipelineStageId")) or "").strip()
            cid = str(o.get("contactId") or "")
            base = dict(tenant_id=tenant_id, business_id=biz, source="ghl",
                        external_id=str(o.get("id")), name=ghl.opp_name(o)[:200],
                        source_url=ghl.contact_url(location_id, o.get("contactId")))
            if onboarded_match in stage.lower():
                amount = float(o.get("monetaryValue") or 0)
                payment = "monthly" if cid in financed_contacts else "pif"
                memberships.append({**base, "kind": "bc_membership", "status": "active",
                                    "amount": amount,
                                    "meta": {"payment": payment, "stage": stage, "contact_id": cid}})
                onboarded.append({**base, "kind": "bc_onboarded", "status": o.get("status") or "won",
                                  "occurred_on": _parse_ghl_dt(o.get("lastStatusChangeAt")),
                                  "amount": amount, "meta": {"stage": stage, "contact_id": cid}})
            elif o.get("pipelineId") in sales_ids and o.get("status") == "open":
                recruiting.append({**base, "kind": "bc_recruiting", "status": "open",
                                   "amount": float(o.get("monetaryValue") or 0) or None,
                                   "meta": {"stage": stage,
                                            "stage_position": stage_pos.get(o.get("pipelineStageId"), 99)}})
        await _ghl_snapshot(s, tenant_id, biz, "bc_membership", memberships)
        await _ghl_snapshot(s, tenant_id, biz, "bc_onboarded", onboarded)
        await _ghl_snapshot(s, tenant_id, biz, "bc_recruiting", recruiting)
        n_records += len(memberships) + len(onboarded) + len(recruiting)
        value = sum(m["amount"] for m in memberships)
        fin = sum(1 for m in memberships if m["meta"]["payment"] == "monthly")
        print(f"[ghl_bc] {len(memberships)} memberships (value ${value:,.0f}, {fin} financed), "
              f"{len(onboarded)} onboarded, {len(recruiting)} recruiting", flush=True)
    except Exception as e:  # noqa: BLE001 — opportunities scope optional
        print(f"[ghl_bc] opportunities skipped: {e}", flush=True)

    return n_records


def _arive_creds(integ: Integration) -> tuple[str, str, str]:
    """Arive needs three secrets; we store them as one encrypted JSON blob in
    access_token_enc (the client_id/api_key are semi-public, the secret is private —
    keeping all three encrypted together is simplest)."""
    raw = dec(integ.access_token_enc) if integ.access_token_enc else None
    if not raw:
        raise ValueError("Arive needs credentials (Client ID, Secret Key, API Key).")
    import json
    d = json.loads(raw)
    cid, secret, api_key = d.get("client_id"), d.get("secret"), d.get("api_key")
    if not (cid and secret and api_key):
        raise ValueError("Arive credentials incomplete (need client_id, secret, api_key).")
    return cid, secret, api_key


async def sync_arive(s: AsyncSession, tenant_id: uuid.UUID, integ: Integration) -> int:
    """Snapshot Sympli's ARIVE pipeline into metric_record (kind='loan', source=
    'arive'). One row per loan carrying its current status, loan amount, and the
    borrower's email/phone (for the ULRG→Sympli flywheel join). Segment marks the
    loan funded / pipeline / dead so the dashboard counts funded loans once —
    post-funding statuses (broker check, commission) are the SAME funded loan."""
    cid, secret, api_key = _arive_creds(integ)
    biz = integ.business_id
    loans = await arive.get_loans(cid, secret, api_key)

    rows = []
    for ln in loans:
        status = arive.loan_status(ln)
        seg = "funded" if arive.is_funded(status) else ("dead" if arive.is_dead(status) else "pipeline")
        b = arive.loan_borrower(ln)
        prop = ln.get("subjectProperty") if isinstance(ln.get("subjectProperty"), dict) else {}
        rows.append(dict(
            tenant_id=tenant_id, business_id=biz, source="arive", kind="loan",
            external_id=arive.loan_display_id(ln) or str(ln.get("sysGUID") or ""),
            name=(b["name"] or f"Loan {arive.loan_display_id(ln)}")[:200],
            email=b["email"],
            amount=arive.loan_amount(ln),
            status=status or "UNKNOWN",
            segment=seg,
            occurred_on=_parse_ghl_dt(arive.loan_status_date(ln)),
            source_url=arive.loan_deep_link(ln),
            meta={
                "status": status, "segment": seg,
                "purpose": arive.pick(ln, "loanPurpose", "loan_purpose"),
                "mortgage_type": arive.pick(ln, "mortgageType", "mortgage_type"),
                "lo_email": (arive.pick(ln, "loanOriginatorEmail", "loanOfficerEmail") or "").lower() or None,
                "borrower_email": b["email"], "borrower_phone": b["phone"],
                "property_state": arive.pick(prop, "state", "propertyState") or arive.pick(ln, "subjectPropertyState"),
            },
        ))
    # Enrich funded loans with their referral source (only in full detail, not list
    # rows) — how we tell a loan came from Utah Life. This drives the flywheel's
    # Sympli-side capture + per-agent attribution. Best-effort; cap the fan-out.
    funded_rows = [r for r in rows if r["segment"] == "funded"]
    try:
        ids = [r["external_id"] for r in funded_rows if r["external_id"]][:800]
        details = await arive.get_loans_detail(ids, cid, secret, api_key)
        enriched = 0
        for r in funded_rows:
            d = details.get(str(r["external_id"]))
            if d:
                ref = arive.loan_referral(d)
                econ = arive.loan_economics(d)      # gross/net revenue + comp for the financials
                lo_name = arive.pick(d, "loanOriginatorName")   # LO display name (per-LO section)
                r["meta"].update({k: v for k, v in {**ref, **econ, "lo_name": lo_name}.items() if v is not None})
                enriched += 1
        print(f"[arive] enriched {enriched}/{len(funded_rows)} funded loans (referral + economics)", flush=True)
    except Exception as e:  # noqa: BLE001 — referral enrichment optional
        print(f"[arive] referral enrichment skipped: {e}", flush=True)

    await _metric_snapshot(s, tenant_id, biz, "arive", "loan", rows)
    pipe = sum(1 for r in rows if r["segment"] == "pipeline")
    vol = sum(r["amount"] for r in funded_rows)
    print(f"[arive] {len(rows)} loans — {len(funded_rows)} funded (${vol:,.0f}), {pipe} in pipeline", flush=True)
    return len(rows)


async def _forum_roster_emails(s: AsyncSession, tenant_id, business_id) -> set[str]:
    """The set of emails we already know are Forum, from the GHL sync — members and
    everyone the GHL feed has a payment/subscription/renewal for (incl. the CSV
    backfill's contacts). This is the deterministic filter for legacy Stripe: import
    a legacy charge only if it belongs to someone on the Forum roster, mirroring the
    validated backfill ('only members from the export')."""
    rows = (await s.execute(select(MetricRecord.email).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl",
        MetricRecord.kind.in_(("member", "payment", "subscription", "membership"))))).all()
    return {em.strip().lower() for (em,) in rows if em}


async def sync_stripe_legacy(s: AsyncSession, tenant_id: uuid.UUID, integ: Integration) -> int:
    """Snapshot Forum charges from Spring's ORIGINAL Stripe account (the one still
    wired to the old Spring B GHL location). Those legacy recurring dues never reach
    the new Forum sub-account, so we read them read-only and write them as
    source='stripe_legacy' payment records — the SAME shape as the GHL payment feed,
    filtered to the Forum roster. build_forum merges + dedupes them against the GHL
    feed (the CSV-backfill copies) so each charge is counted exactly once.

    Config (integ.config): sync_since_epoch (cap the pull; default = full history),
    extra_forum_emails / exclude_emails (roster overrides), stream_overrides."""
    from .billing import classify_stream
    key = dec(integ.access_token_enc) if integ.access_token_enc else None
    if not key:
        raise ValueError("Legacy Stripe needs a read-only API key.")
    biz = integ.business_id
    cfg = integ.config or {}

    roster = await _forum_roster_emails(s, tenant_id, biz)
    roster |= {str(e).strip().lower() for e in (cfg.get("extra_forum_emails") or [])}
    exclude = {str(e).strip().lower() for e in (cfg.get("exclude_emails") or [])}
    stream_overrides = cfg.get("stream_overrides") or {}
    since = cfg.get("sync_since_epoch")

    charges = await stripe_legacy.list_charges(key, created_gt=since)
    rows, skipped = [], 0
    for ch in charges:
        email = stripe_legacy.charge_email(ch)
        if not email or email in exclude or email not in roster:
            skipped += 1
            continue
        desc = stripe_legacy.charge_description(ch)
        cdt = stripe_legacy.charge_datetime(ch)
        rows.append(dict(
            tenant_id=tenant_id, business_id=biz, source="stripe_legacy", kind="payment",
            external_id=str(ch.get("id")),
            name=(stripe_legacy.charge_name(ch) or email)[:200], email=email,
            amount=stripe_legacy.charge_amount(ch), status=stripe_legacy.charge_status(ch),
            occurred_on=stripe_legacy.charge_date(ch), source_url=stripe_legacy.dashboard_url(ch),
            meta={"stream": classify_stream(desc, stream_overrides),
                  "entity_source_name": desc or None,
                  "amount_refunded": stripe_legacy.charge_refunded(ch),
                  "charge_id": ch.get("id"), "payment_intent": stripe_legacy.payment_intent(ch),
                  "currency": ch.get("currency"), "legacy": True,
                  "charged_at": cdt.isoformat() if cdt else None,
                  "contact": stripe_legacy.charge_contact(ch)}))
    await _metric_snapshot(s, tenant_id, biz, "stripe_legacy", "payment", rows)
    matched = len(rows)
    print(f"[stripe_legacy] {matched} Forum charges of {len(charges)} pulled "
          f"({skipped} non-roster skipped · roster={len(roster)})", flush=True)
    return matched


# Every period the dashboard can toggle to needs its own snapshot.
_QBO_PERIODS = ("mtd", "qtd", "ytd", "last_month")


async def sync_qbo_pl(s: AsyncSession, tenant_id, integ: Integration):
    """Pull the QBO P&L for EVERY dashboard period and upsert a snapshot for each,
    so Month / Quarter / Year / Last month all have data — not just whichever one
    happened to be synced. Periods come from the same _period_range the dashboard
    reads with, so the ranges line up exactly."""
    from .metrics import _period_range, _pl_period          # local import avoids a cycle
    token = await _valid_access_token(s, integ)
    now = dt.datetime.now(dt.timezone.utc)
    for period in _QBO_PERIODS:
        fetch_start, fetch_end = _period_range(period)     # actuals through today
        ps, pe = _pl_period(period)                         # store under the fixed calendar key
        report = await qbo.profit_and_loss(integ.realm_id, token, fetch_start.isoformat(), fetch_end.isoformat())
        nums = {k: Decimal(str(v)) for k, v in qbo.parse_pl(report).items()}   # NUMERIC wants Decimal
        stmt = pg_insert(PLSnapshot).values(
            tenant_id=tenant_id, business_id=integ.business_id, period_start=ps, period_end=pe,
            source="qbo", realm_id=integ.realm_id, **nums,
        ).on_conflict_do_update(
            index_elements=["tenant_id", "business_id", "period_start", "period_end"],
            set_={**nums, "pulled_at": now},
        )
        await s.execute(stmt)
    integ.last_synced_at = now
    await s.commit()
    return len(_QBO_PERIODS)


async def _sync_integration(s: AsyncSession, tenant_id, integ: Integration, period_start, period_end):
    """Sync one integration, recording a SyncRun (with record/timing stats) and
    updating its status."""
    run = SyncRun(tenant_id=tenant_id, provider=integ.provider)
    s.add(run)
    await s.commit()
    started = dt.datetime.utcnow()
    try:
        records = None
        if integ.provider == "sisu":
            records = await sync_sisu(s, tenant_id, integ)
        elif integ.provider == "fub":
            records = await sync_fub(s, tenant_id, integ.business_id)
        elif integ.provider == "ghl":
            records = await sync_ghl(s, tenant_id, integ)
        elif integ.provider == "ghl_bc":
            records = await sync_becollective_ghl(s, tenant_id, integ)
        elif integ.provider == "arive":
            records = await sync_arive(s, tenant_id, integ)
        elif integ.provider == "stripe_legacy":
            records = await sync_stripe_legacy(s, tenant_id, integ)
        elif integ.provider == "qbo":
            records = await sync_qbo_pl(s, tenant_id, integ)   # syncs all periods itself
        run.status, run.finished_at = "ok", dt.datetime.utcnow()
        run.stats = {"records": records,
                     "seconds": round((run.finished_at - started).total_seconds(), 1)}
        # Clear any prior error and mark the source healthy again.
        integ.status, integ.last_error = "connected", None
        integ.last_synced_at = dt.datetime.utcnow()
    except Exception as e:  # noqa: BLE001 — surface error on the integration
        run.status, run.detail, run.finished_at = "error", str(e), dt.datetime.utcnow()
        run.stats = {"records": None, "seconds": round((run.finished_at - started).total_seconds(), 1)}
        integ.status, integ.last_error = "error", str(e)
    await s.commit()


async def run_all(s: AsyncSession, tenant_id: uuid.UUID, period_start: str, period_end: str):
    # Include "error" so a previously-failed sync is retried (a stuck error would
    # otherwise silently skip the source). Disconnected sources are left alone.
    integs = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id,
        Integration.status.in_(("connected", "error"))))).scalars().all()
    for integ in integs:
        await _sync_integration(s, tenant_id, integ, period_start, period_end)


async def run_one(s: AsyncSession, tenant_id: uuid.UUID, integ_id, period_start: str, period_end: str):
    integ = (await s.execute(select(Integration).where(
        Integration.id == integ_id, Integration.tenant_id == tenant_id))).scalar_one_or_none()
    if integ:
        await _sync_integration(s, tenant_id, integ, period_start, period_end)
