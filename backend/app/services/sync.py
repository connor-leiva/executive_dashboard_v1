"""Per-provider sync into snapshots. Idempotent upserts keyed on
(tenant, source, external_id). QBO refresh-token rotation is persisted on every
call. Upserts target Postgres (prod); the worker does not run against SQLite.
"""
import datetime as dt
import uuid
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select, delete, insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Integration, Transaction, Agent, Lead, PLSnapshot, SyncRun, MetricRecord, Business
from ..security import enc, dec
from ..integrations import fub, sisu, qbo, ghl, arive, stripe_legacy


def _tz(name: str) -> dt.tzinfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:  # noqa: BLE001 — unknown tz name → UTC
        return dt.timezone.utc


def _biz_tz() -> dt.tzinfo:
    """GHL location timezone (its createdAt/fulfilledAt display in this tz)."""
    return _tz(settings.BILLING_TIMEZONE or "America/Denver")


def _local_date(value, tz: dt.tzinfo) -> dt.date | None:
    """A timestamp (ISO string, epoch-ms, or epoch-seconds) → calendar date in `tz`.
    GHL stores UTC; taking the UTC date put the dashboard a day ahead of GHL for
    early-morning charges — converting to the business tz fixes the day AND the dedupe."""
    if value in (None, "", []):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and str(value).isdigit()):
            v = int(value)
            secs = v / 1000 if v > 10_000_000_000 else v          # GHL ms vs Stripe seconds
            d = dt.datetime.fromtimestamp(secs, dt.timezone.utc)
        else:
            d = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(tz).date()
    except (ValueError, OverflowError, OSError, TypeError):
        return None


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


def _parse_any_date(v) -> dt.date | None:
    """A GHL custom-field date value → date. Handles ISO / epoch-ms (via _parse_ghl_dt)
    plus the human formats GHL date pickers emit (MM/DD/YYYY, 'Jan 5, 2026', …)."""
    if v in (None, "", []):
        return None
    d = _parse_ghl_dt(v)
    if d:
        return d
    sv = str(v).strip()
    for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d", "%d/%m/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return dt.datetime.strptime(sv, fmt).date()
        except ValueError:
            continue
    return None


def _parse_money(v) -> float | None:
    if v in (None, "", []):
        return None
    try:
        return round(float(str(v).replace("$", "").replace(",", "").strip()), 2)
    except (ValueError, TypeError):
        return None


def _membership_field_ids(defs: list[dict], cfg: dict) -> dict:
    """Map our semantic membership keys → GHL custom-field ids by matching the field
    NAME (case-insensitive keyword), with config override cfg['membership_fields']
    (semantic_key → field name or id). Degrades to {} when nothing matches, so the
    projection simply falls back to subscriptions."""
    override = {k: str(v).strip().lower() for k, v in (cfg.get("membership_fields") or {}).items()}
    by_name = {}
    ids = set()
    for d in defs:
        nm = (d.get("name") or "").strip().lower()
        if nm:
            by_name.setdefault(nm, d.get("id"))
        ids.add(d.get("id"))

    def pick(key, kws, prefer=None):
        ov = override.get(key)
        if ov:
            return by_name.get(ov) or (ov if ov in ids else None)
        for require in ([True, False] if prefer else [False]):
            for d in defs:
                nm = (d.get("name") or "").lower()
                if any(kw in nm for kw in kws) and (not require or prefer in nm):
                    return d.get("id")
        return None

    out = {}
    for key, kws, prefer in [
        ("renewal_date", ["renewal"], "date"),
        ("enrollment_date", ["enroll"], "date"),
        ("total_cost", ["total", "cost", "amount"], "member"),
        ("payment_plan", ["payment plan", "pay plan", "plan type", "payment type", "membership plan"], None),
        # Richer CRM "Membership Details" fields for the roster view.
        ("member_type", ["member type", "membership type"], None),
        ("member_tier", ["member tier", "tier", "membership level"], None),
        ("status", ["status"], "member"),           # prefer a "member/membership status" field
        ("brokerage", ["brokerage", "affiliation"], None),
        ("stripe_account", ["stripe account", "stripe acct"], None),
    ]:
        fid = pick(key, kws, prefer)
        if fid:
            out[key] = fid
    return out


def _clean_str(v) -> str | None:
    """A GHL custom-field value → a trimmed string (first element of a multi-select),
    or None when blank."""
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
    s = str(v if v is not None else "").strip()
    return s or None


def _member_type(v: str | None) -> str | None:
    """Normalize the CRM 'Member Type' RADIO label → 'primary' | 'add_on' | 'admin'
    (raw kept too). Live options are Primary Member / Add-On Member / Admin."""
    d = (v or "").lower()
    if not d:
        return None
    if "admin" in d:                                      # staff seat, not a paying member
        return "admin"
    if "add" in d or "secondary" in d or "spouse" in d:   # Add-On / Secondary / Spouse
        return "add_on"
    if "primary" in d or "main" in d:
        return "primary"
    return None


# A membership whose CRM "Status" field reads one of these is NOT a current member,
# even if the Member Type field is still populated (a member can go inactive without
# anyone clearing their type). Conservative on purpose — payment states (past_due /
# failed) and transitional ones (pending / resigning) are handled elsewhere and are
# NOT dropped here. Overridable per tenant via cfg['inactive_statuses'].
_INACTIVE_MEMBER_STATUS = ("inactive", "not active", "non-active", "cancel",
                           "lapsed", "expired", "former", "terminated", "churn")


def _is_inactive_status(v: str | None, vocab=_INACTIVE_MEMBER_STATUS) -> bool:
    d = (v or "").strip().lower()
    return bool(d) and any(k in d for k in vocab)


def _read_membership(values: dict, field_ids: dict) -> dict:
    """A contact's custom-field values (id→value) → the semantic membership record."""
    from .billing import normalize_payment_plan
    out = {}
    rd = _parse_any_date(values.get(field_ids.get("renewal_date")))
    ed = _parse_any_date(values.get(field_ids.get("enrollment_date")))
    tc = _parse_money(values.get(field_ids.get("total_cost")))
    pp = normalize_payment_plan(values.get(field_ids.get("payment_plan")))
    if rd:
        out["renewal_date"] = rd.isoformat()
    if ed:
        out["enrollment_date"] = ed.isoformat()
    if tc is not None:
        out["total_cost"] = tc
    if pp:
        out["payment"] = pp
    # Richer roster fields — kept as trimmed labels, blanks dropped.
    mt = _clean_str(values.get(field_ids.get("member_type")))
    if mt:
        out["member_type"] = mt
        norm = _member_type(mt)
        if norm:
            out["member_kind"] = norm            # primary | add_on
    for key in ("status", "brokerage", "stripe_account", "member_tier"):
        val = _clean_str(values.get(field_ids.get(key)))
        if val:
            out[key] = val
    return out


def _member_decision(detail: dict, tset: set, member_tags: set, typed: bool, inactive_vocab) -> tuple:
    """Field-driven membership decision shared by the Forum + beCollective syncs. When the
    Member Type field is mapped (`typed`), membership is defined by that field and an
    inactive Status field drops a lapsed member; otherwise it falls back to the member-tag
    union. Returns (is_member, member_kind, inactive)."""
    kind = detail.get("member_kind")                     # primary | add_on | admin | None
    typed_member = kind in ("primary", "add_on", "admin")
    inactive = typed and typed_member and _is_inactive_status(detail.get("status"), inactive_vocab)
    is_member = (typed_member and not inactive) if typed else bool(tset & member_tags)
    return is_member, kind, inactive


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

    # 1) Contacts → members + event registrations (tag).
    #    A registration whose contact is NOT a member is a guest (prospect seat).
    contacts = await ghl.get_contacts(token, location_id)
    # Map the location's custom fields once, then read each contact's membership detail
    # (member type / renewal / enrollment / cost / plan / brokerage / Stripe account).
    field_ids = {}
    try:
        field_ids = _membership_field_ids(await ghl.get_custom_fields(token, location_id), cfg)
    except Exception as e:  # noqa: BLE001 — custom fields optional; never fail the sync
        print(f"[ghl] custom fields skipped: {e}", flush=True)
    if field_ids:
        print(f"[ghl] membership fields mapped: {sorted(field_ids)}", flush=True)

    # GHL is the definitive source of truth for the roster: a member is a contact typed
    # in the CRM "Member Type" field (Primary / Add-On / Admin). Admins are staff — kept
    # as records (status='admin') so they surface in the roster drawer, but excluded from
    # every active-member count. Tags no longer *define* membership (they were noisy —
    # cohort/guest tags leaked in and tag-less members were missed); they still segment
    # Forum vs Inner Circle. Falls back to the tag union only if the field isn't mapped.
    # A member whose "Status" field reads inactive is dropped from the count even if the
    # Member Type field is still set (that field alone no longer keeps a lapsed member on
    # the roster — the Status field is the authoritative active/inactive signal).
    typed = bool(field_ids.get("member_type"))
    inactive_vocab = tuple(str(x).lower() for x in
                           (cfg.get("inactive_statuses") or _INACTIVE_MEMBER_STATUS))
    members, regs = [], []
    n_admin = n_inactive = 0
    for c in contacts:
        tset = set(ghl.contact_tags(c))
        cid = str(c.get("id"))
        base = dict(tenant_id=tenant_id, business_id=biz, source="ghl",
                    external_id=cid, name=ghl.contact_name(c)[:200],
                    email=(c.get("email") or None),
                    source_url=ghl.contact_url(location_id, c.get("id")))
        detail = _read_membership(ghl.contact_custom_values(c), field_ids) if field_ids else {}
        is_member, kind, inactive = _member_decision(detail, tset, member_tags, typed, inactive_vocab)
        if inactive:
            n_inactive += 1
        if is_member:
            if kind == "admin":
                n_admin += 1
            members.append({**base, "kind": "member",
                            "status": "admin" if kind == "admin" else "active",
                            "segment": ghl.member_segment(tset, forum_tags, ic_tags),
                            "meta": {"membership": detail}})
        if event_tag and event_tag in tset:
            regs.append({**base, "kind": "registration", "status": "registered",
                         "meta": {"event_tag": event_tag, "guest": not is_member, "contact_id": cid}})
    await _ghl_snapshot(s, tenant_id, biz, "member", members)
    # contact_id → payment plan from the field, to drive the membership payment mix below.
    plan_by_contact = {m["external_id"]: (m["meta"]["membership"].get("payment"))
                       for m in members if (m["meta"]["membership"] or {}).get("payment")}
    await _ghl_snapshot(s, tenant_id, biz, "registration", regs)
    n_records = len(members) + len(regs)
    print(f"[ghl] {len(members) - n_admin} members (+{n_admin} admin) via "
          f"{'Member Type field' if typed else 'membership tags'}"
          f"{f', {n_inactive} inactive excluded' if n_inactive else ''}, {len(regs)} registered "
          f"for '{event_tag}' (from {len(contacts)} contacts)", flush=True)

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
    tz = _biz_tz()
    try:
        txns = await ghl.ghl_transactions(token, location_id)
        pay_rows, sub_succeeded = [], {}
        for t in txns:
            status = ghl.txn_status(t)
            sub_id = t.get("subscriptionId")
            name = t.get("entitySourceName")
            # CSV-imported transactions (entitySourceSubType='imported_csv') carry the
            # IMPORT DAY in createdAt; their REAL payment date is `fulfilledAt`. Native
            # Stripe rows use createdAt. Resolve in the business tz so the day matches GHL.
            imported = (t.get("entitySourceSubType") == "imported_csv") or (t.get("entityType") == "external")
            raw = t.get("fulfilledAt") if imported else t.get("createdAt")
            occurred = _local_date(raw, tz) or _local_date(t.get("createdAt"), tz)
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
                      "imported": imported,
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
        matched, from_field = 0, 0
        for m in mrs:
            meta = dict(m.meta or {})
            # Prefer the ClickUp-sourced payment-plan field; else infer from a live sub.
            field_plan = plan_by_contact.get(meta.get("contact_id"))
            is_monthly = meta.get("contact_id") in payers
            meta["payment"] = field_plan or ("monthly" if is_monthly else "pif")
            m.meta = meta
            from_field += 1 if field_plan else 0
            matched += 1 if meta["payment"] in ("monthly", "financed") else 0
        await s.commit()
        print(f"[ghl] payment type: {matched}/{len(mrs)} recurring "
              f"({from_field} from the plan field, {len(payers)} live sub payers)", flush=True)
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

    # 1) Contacts → members + registrations — FIELD-DRIVEN, exactly like the Forum. The
    #    GHL "Membership Details" fields (Member Type / Status / Tier / Enrollment / Renewal
    #    / Payment Plan) are the source of truth; tags only fall back when the Member Type
    #    field isn't mapped on this location, and a financed tag is a payment-plan fallback.
    #    Segment stays 'becollective' (no Forum/Inner-Circle split here).
    contacts = await ghl.get_contacts(token, location_id)
    field_ids = {}
    try:
        field_ids = _membership_field_ids(await ghl.get_custom_fields(token, location_id), cfg)
    except Exception as e:  # noqa: BLE001 — custom fields optional; never fail the sync
        print(f"[ghl_bc] custom fields skipped: {e}", flush=True)
    if field_ids:
        print(f"[ghl_bc] membership fields mapped: {sorted(field_ids)}", flush=True)
    typed = bool(field_ids.get("member_type"))
    inactive_vocab = tuple(str(x).lower() for x in
                           (cfg.get("inactive_statuses") or _INACTIVE_MEMBER_STATUS))
    members, regs, financed_contacts = [], [], set()
    n_admin = n_inactive = 0
    for c in contacts:
        tset = set(ghl.contact_tags(c))
        cid = str(c.get("id"))
        if tset & financed_tags:
            financed_contacts.add(cid)
        base = dict(tenant_id=tenant_id, business_id=biz, source="ghl",
                    external_id=cid, name=ghl.contact_name(c)[:200],
                    email=(c.get("email") or None),
                    source_url=ghl.contact_url(location_id, c.get("id")))
        detail = _read_membership(ghl.contact_custom_values(c), field_ids) if field_ids else {}
        is_member, kind, inactive = _member_decision(detail, tset, member_tags, typed, inactive_vocab)
        if inactive:
            n_inactive += 1
        if is_member:
            if kind == "admin":
                n_admin += 1
            members.append({**base, "kind": "bc_member",
                            "status": "admin" if kind == "admin" else "active",
                            "segment": "becollective", "meta": {"membership": detail}})
        if event_tag and event_tag in tset:
            regs.append({**base, "kind": "bc_registration", "status": "registered",
                         "meta": {"event_tag": event_tag, "guest": not is_member, "contact_id": cid}})
    await _ghl_snapshot(s, tenant_id, biz, "bc_member", members)
    # contact_id → payment plan from the field, so opps below prefer it over the tag.
    plan_by_contact = {m["external_id"]: (m["meta"]["membership"].get("payment"))
                       for m in members if (m["meta"]["membership"] or {}).get("payment")}
    await _ghl_snapshot(s, tenant_id, biz, "bc_registration", regs)
    n_records = len(members) + len(regs)
    print(f"[ghl_bc] {len(members) - n_admin} members (+{n_admin} admin) via "
          f"{'Member Type field' if typed else 'membership tags'}"
          f"{f', {n_inactive} inactive excluded' if n_inactive else ''}, {len(regs)} registered "
          f"for '{event_tag}' (from {len(contacts)} contacts)", flush=True)

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
                # Prefer the member's Payment Plan field; fall back to the financed tag.
                payment = plan_by_contact.get(cid) or ("monthly" if cid in financed_contacts else "pif")
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
    from .billing import classify_stream, forum_offering, classify_installment
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
    tz = _tz(settings.STRIPE_TIMEZONE or "UTC")   # Stripe account tz (Connor's = UTC),
    #   matching Stripe's own display AND the CSV-import copy so the two collapse in dedupe
    # Rich labels from the old Spring B GHL (what each charge is FOR): by GHL invoice id
    # (the charge's metadata.invoiceId — deterministic) and by Stripe charge id (txn hop).
    async def _ghl_map(kind):
        return {ext: nm for ext, nm in (await s.execute(select(
            MetricRecord.external_id, MetricRecord.name).where(
            MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == biz,
            MetricRecord.source == "ghl_legacy", MetricRecord.kind == kind))).all()}
    inv_label = await _ghl_map("invoice")
    pi_label = await _ghl_map("label")

    # ── charges → payment records (roster member AND a Forum/IC offering) ──
    charges = await stripe_legacy.list_charges(key, created_gt=since)
    rows, off_roster, off_forum = [], 0, 0
    for ch in charges:
        email = stripe_legacy.charge_email(ch)
        # The old-GHL label (what the charge is actually FOR) is the primary signal:
        # invoice id (charge metadata) is the deterministic join, the pi_ txn hop the fallback.
        inv_id, pi = stripe_legacy.invoice_id(ch), stripe_legacy.payment_intent(ch)
        label = (inv_id and inv_label.get(inv_id)) or (pi and pi_label.get(pi))
        # A labeled charge classifies by its label, ROSTER-INDEPENDENT — Stripe customer
        # emails don't always match the roster (e.g. an invoice paid from a different
        # address). Only UNLABELED charges keep the roster as a "is this a Forum person" gate.
        if (email in exclude) or (not label and (not email or email not in roster)):
            off_roster += 1
            continue
        desc = label or stripe_legacy.charge_description(ch)
        include, segment = forum_offering(desc, cfg, amount=stripe_legacy.charge_amount(ch),
                                          recurring=bool(ch.get("invoice")))
        if not include:
            off_forum += 1
            continue
        cdt = stripe_legacy.charge_datetime(ch)
        rows.append(dict(
            tenant_id=tenant_id, business_id=biz, source="stripe_legacy", kind="payment",
            external_id=str(ch.get("id")),
            name=(stripe_legacy.charge_name(ch) or email)[:200], email=email,
            amount=stripe_legacy.charge_amount(ch), status=stripe_legacy.charge_status(ch),
            occurred_on=_local_date(ch.get("created"), tz), source_url=stripe_legacy.dashboard_url(ch),
            segment=segment,
            meta={"stream": classify_stream(desc, stream_overrides),
                  "entity_source_name": desc or None, "segment": segment,
                  "amount_refunded": stripe_legacy.charge_refunded(ch),
                  "charge_id": ch.get("id"), "payment_intent": stripe_legacy.payment_intent(ch),
                  "currency": ch.get("currency"), "legacy": True,
                  "charged_at": cdt.isoformat() if cdt else None,
                  "contact": stripe_legacy.charge_contact(ch)}))
    await _metric_snapshot(s, tenant_id, biz, "stripe_legacy", "payment", rows)
    print(f"[stripe_legacy] {len(rows)} Forum charges of {len(charges)} "
          f"({off_roster} off-roster · {off_forum} non-Forum offerings skipped · roster={len(roster)})",
          flush=True)

    # ── subscriptions → the legacy recurring book, so MRR + forward projection stop
    #    understating the legacy dues the new sub-account never received. Read-only. ──
    sub_rows, sub_skipped = [], 0
    try:
        subs = await stripe_legacy.list_subscriptions(key)
    except Exception as e:  # noqa: BLE001 — needs Subscriptions:read; degrade, don't fail the charge sync
        subs = []
        print(f"[stripe_legacy] subscriptions skipped ({e}) — grant Subscriptions:read on the key", flush=True)
    for sub in subs:
        email = stripe_legacy.sub_email(sub)
        if not email or email in exclude or email not in roster:
            sub_skipped += 1
            continue
        plan = stripe_legacy.sub_plan_name(sub) or ""
        include, segment = forum_offering(plan, cfg, is_subscription=True)
        if not include:
            sub_skipped += 1
            continue
        start, end = stripe_legacy.sub_start_date(sub), stripe_legacy.sub_end_date(sub)
        status = stripe_legacy.sub_status(sub)
        amount = stripe_legacy.sub_amount(sub)
        sub_type, inst_total = classify_installment(
            plan, start.isoformat() if start else None, end.isoformat() if end else None, {})
        npd = stripe_legacy.sub_next_charge(sub) if status == "active" else None
        sub_rows.append(dict(
            tenant_id=tenant_id, business_id=biz, source="stripe_legacy", kind="subscription",
            external_id=str(sub.get("id")),
            name=(stripe_legacy.sub_customer_name(sub) or plan or email)[:200], email=email,
            amount=amount, status=status, segment=segment,
            source_url=stripe_legacy.sub_dashboard_url(sub),
            meta={"plan_name": plan or None, "interval": stripe_legacy.sub_interval(sub),
                  "start_date": start.isoformat() if start else None,
                  "end_date": end.isoformat() if end else None,
                  "sub_type": sub_type, "installments_total": inst_total,
                  "installments_collected": None,
                  "next_payment_date": npd.isoformat() if npd else None,
                  "next_payment_amount": amount, "legacy": True, "segment": segment}))
    await _metric_snapshot(s, tenant_id, biz, "stripe_legacy", "subscription", sub_rows)
    active_n = sum(1 for r in sub_rows if r["status"] == "active")
    print(f"[stripe_legacy] {active_n}/{len(sub_rows)} active legacy subscriptions "
          f"({sub_skipped} skipped)", flush=True)

    return len(rows) + len(sub_rows)


async def _bc_roster_emails(s: AsyncSession, tenant_id, business_id) -> set[str]:
    """Emails on the beCollective roster (from the GHL sync) — the deterministic filter for
    beCollective's Stripe account: report a charge only if it belongs to a member. beCollective
    has no GHL payments/subs, so the roster comes from bc_member / bc_membership records."""
    rows = (await s.execute(select(MetricRecord.email).where(
        MetricRecord.tenant_id == tenant_id, MetricRecord.business_id == business_id,
        MetricRecord.source == "ghl",
        MetricRecord.kind.in_(("bc_member", "bc_membership"))))).all()
    return {em.strip().lower() for (em,) in rows if em}


async def sync_becollective_stripe(s: AsyncSession, tenant_id: uuid.UUID, integ: Integration) -> int:
    """Snapshot beCollective MEMBERSHIP payments from beCollective's own dedicated Stripe
    account, read-only. The account is dedicated to beCollective but multiple product types run
    through it (event tickets, courses), so we keep membership + financed-plan charges and drop
    the rest (bc_offering), filtered to the beCollective roster. Written as source='stripe_bc'
    payment/subscription records — the SAME shape as the Forum's legacy-Stripe feed — so
    build_becollective's Cash & Billing lights up exactly like the Forum's. Reuses the generic
    stripe_legacy reader (it just reads a Stripe account).

    Config: sync_since_epoch, extra_member_emails / exclude_emails, stream_overrides, and the
    bc_offering tuning (non_bc_keywords / bc_keywords / membership_min_amount)."""
    from .billing import classify_stream, bc_offering, classify_installment
    key = dec(integ.access_token_enc) if integ.access_token_enc else None
    if not key:
        raise ValueError("beCollective Stripe needs a read-only API key.")
    biz = integ.business_id
    cfg = integ.config or {}

    roster = await _bc_roster_emails(s, tenant_id, biz)
    roster |= {str(e).strip().lower() for e in (cfg.get("extra_member_emails") or [])}
    exclude = {str(e).strip().lower() for e in (cfg.get("exclude_emails") or [])}
    stream_overrides = cfg.get("stream_overrides") or {}
    since = cfg.get("sync_since_epoch")
    tz = _tz(settings.STRIPE_TIMEZONE or "UTC")       # Stripe account tz (Connor's = UTC)

    # ── charges → payment records (roster member AND a membership offering) ──
    charges = await stripe_legacy.list_charges(key, created_gt=since)
    rows, off_roster, off_member = [], 0, 0
    for ch in charges:
        email = stripe_legacy.charge_email(ch)
        # Dedicated account but no old-GHL label source, so the roster email is the gate
        # (a member paying from a different address needs an extra_member_emails override).
        if (email in exclude) or (not email) or (email not in roster):
            off_roster += 1
            continue
        desc = stripe_legacy.charge_description(ch)
        include, segment = bc_offering(desc, cfg, amount=stripe_legacy.charge_amount(ch),
                                       recurring=bool(ch.get("invoice")))
        if not include:
            off_member += 1
            continue
        cdt = stripe_legacy.charge_datetime(ch)
        rows.append(dict(
            tenant_id=tenant_id, business_id=biz, source="stripe_bc", kind="payment",
            external_id=str(ch.get("id")),
            name=(stripe_legacy.charge_name(ch) or email)[:200], email=email,
            amount=stripe_legacy.charge_amount(ch), status=stripe_legacy.charge_status(ch),
            occurred_on=_local_date(ch.get("created"), tz), source_url=stripe_legacy.dashboard_url(ch),
            segment=segment,
            meta={"stream": classify_stream(desc, stream_overrides),
                  "entity_source_name": desc or None, "segment": segment,
                  "amount_refunded": stripe_legacy.charge_refunded(ch),
                  "charge_id": ch.get("id"), "payment_intent": stripe_legacy.payment_intent(ch),
                  "currency": ch.get("currency"), "stripe_bc": True,
                  "charged_at": cdt.isoformat() if cdt else None,
                  "contact": stripe_legacy.charge_contact(ch)}))
    await _metric_snapshot(s, tenant_id, biz, "stripe_bc", "payment", rows)
    print(f"[stripe_bc] {len(rows)} membership charges of {len(charges)} "
          f"({off_roster} off-roster · {off_member} non-membership skipped · roster={len(roster)})",
          flush=True)

    # ── subscriptions → the beCollective financed-plan recurring book. Read-only. ──
    sub_rows, sub_skipped = [], 0
    try:
        subs = await stripe_legacy.list_subscriptions(key)
    except Exception as e:  # noqa: BLE001 — needs Subscriptions:read; degrade, don't fail the charge sync
        subs = []
        print(f"[stripe_bc] subscriptions skipped ({e}) — grant Subscriptions:read on the key", flush=True)
    for sub in subs:
        email = stripe_legacy.sub_email(sub)
        if not email or email in exclude or email not in roster:
            sub_skipped += 1
            continue
        plan = stripe_legacy.sub_plan_name(sub) or ""
        include, segment = bc_offering(plan, cfg, is_subscription=True)
        if not include:
            sub_skipped += 1
            continue
        start, end = stripe_legacy.sub_start_date(sub), stripe_legacy.sub_end_date(sub)
        status = stripe_legacy.sub_status(sub)
        amount = stripe_legacy.sub_amount(sub)
        sub_type, inst_total = classify_installment(
            plan, start.isoformat() if start else None, end.isoformat() if end else None, {})
        npd = stripe_legacy.sub_next_charge(sub) if status == "active" else None
        sub_rows.append(dict(
            tenant_id=tenant_id, business_id=biz, source="stripe_bc", kind="subscription",
            external_id=str(sub.get("id")),
            name=(stripe_legacy.sub_customer_name(sub) or plan or email)[:200], email=email,
            amount=amount, status=status, segment=segment,
            source_url=stripe_legacy.sub_dashboard_url(sub),
            meta={"plan_name": plan or None, "interval": stripe_legacy.sub_interval(sub),
                  "start_date": start.isoformat() if start else None,
                  "end_date": end.isoformat() if end else None,
                  "sub_type": sub_type, "installments_total": inst_total,
                  "installments_collected": None,
                  "next_payment_date": npd.isoformat() if npd else None,
                  "next_payment_amount": amount, "stripe_bc": True, "segment": segment}))
    await _metric_snapshot(s, tenant_id, biz, "stripe_bc", "subscription", sub_rows)
    active_n = sum(1 for r in sub_rows if r["status"] == "active")
    print(f"[stripe_bc] {active_n}/{len(sub_rows)} active beCollective subscriptions "
          f"({sub_skipped} skipped)", flush=True)

    return len(rows) + len(sub_rows)


async def sync_ghl_legacy(s: AsyncSession, tenant_id: uuid.UUID, integ: Integration) -> int:
    """Old Spring B GHL (where the legacy Stripe is wired) — read-only. Builds a
    `pi_ charge id -> real label` map so the legacy-Stripe sync can name + classify each
    charge by what it's actually FOR. The label is the transaction's entitySourceName,
    resolved to the invoice's line item for invoice-type charges (e.g. 'Spring Break
    Special Event Sponsorship' instead of 'Other'). Stored as source='ghl_legacy',
    kind='label', external_id = the pi_ id."""
    cfg = integ.config or {}
    location_id = cfg.get("location_id")
    token = dec(integ.access_token_enc) if integ.access_token_enc else None
    if not (location_id and token):
        raise ValueError("Old GHL needs a token and location_id in config.")
    biz = integ.business_id

    invoices = await ghl.get_invoices(token, location_id)
    inv_label, inv_by_id = {}, {}
    for iv in invoices:
        _id = str(iv.get("_id") or "")
        label = "; ".join(ghl.invoice_items(iv)) or (iv.get("name") or iv.get("title") or "")
        inv_label[_id] = label
        if _id and label:
            inv_by_id[_id] = dict(
                tenant_id=tenant_id, business_id=biz, source="ghl_legacy", kind="invoice",
                external_id=_id, name=label[:200],
                meta={"invoice_number": str(iv.get("invoiceNumberPrefix") or "") + str(iv.get("invoiceNumber") or "")})

    txns = await ghl.ghl_transactions(token, location_id)
    by_pi = {}
    for t in txns:
        pi = t.get("chargeId")
        if not pi or not str(pi).startswith(("pi_", "ch_")):
            continue
        est, name = t.get("entitySourceType"), (t.get("entitySourceName") or "")
        label = (inv_label.get(str(t.get("entitySourceId"))) or name) if est == "invoice" else name
        if not label:
            continue
        by_pi[str(pi)] = dict(
            tenant_id=tenant_id, business_id=biz, source="ghl_legacy", kind="label",
            external_id=str(pi), name=label[:200],
            meta={"entity_source_type": est, "entity_source_name": name})
    rows = list(by_pi.values())
    await _metric_snapshot(s, tenant_id, biz, "ghl_legacy", "label", rows)
    await _metric_snapshot(s, tenant_id, biz, "ghl_legacy", "invoice", list(inv_by_id.values()))
    print(f"[ghl_legacy] {len(rows)} pi_->label + {len(inv_by_id)} invoice labels "
          f"(from {len(txns)} txns, {len(invoices)} invoices)", flush=True)
    return len(rows) + len(inv_by_id)


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
        elif integ.provider == "stripe_bc":
            records = await sync_becollective_stripe(s, tenant_id, integ)
        elif integ.provider == "ghl_legacy":
            records = await sync_ghl_legacy(s, tenant_id, integ)
        elif integ.provider == "qbo":
            prev_synced = integ.last_synced_at                 # txn CDC cursor, pre-bump
            records = await sync_qbo_pl(s, tenant_id, integ)   # syncs all periods itself
            biz = await s.get(Business, integ.business_id)     # Books ingestion is per-entity opt-out
            bcfg = (biz.config or {}) if biz else {}
            if bcfg.get("books_enabled", True):
                # the entity's chosen backfill-start lives on the business; surface it onto
                # the integration the txn sync reads (_backfill_start), bridging create→sync.
                bf = bcfg.get("books_backfill_start")
                if bf and (integ.config or {}).get("books_backfill_start") != bf:
                    integ.config = {**(integ.config or {}), "books_backfill_start": bf}
                from .books_sync import run_books_syncs        # local import avoids a cycle
                await run_books_syncs(s, tenant_id, integ, since=prev_synced)
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
    if any(i.provider == "qbo" for i in integs):       # Books deterministic scan after txn sync
        from .books_scan import run_scan
        await run_scan(s, tenant_id)
    # Binder: extract newly-uploaded documents into obligation PROPOSALS (never obligations —
    # a human confirms each). Independent of integrations; key-gated and a no-op when there are
    # no pending docs. Proven on real data via the go/no-go before wiring here.
    from .binder_extract import run_binder_extraction
    await run_binder_extraction(s, tenant_id)
    # Binder reminders: stage obligations + deliver the digest on its cadence (no key needed).
    from .binder_reminders import run_reminders
    await run_reminders(s, tenant_id)


async def run_one(s: AsyncSession, tenant_id: uuid.UUID, integ_id, period_start: str, period_end: str):
    integ = (await s.execute(select(Integration).where(
        Integration.id == integ_id, Integration.tenant_id == tenant_id))).scalar_one_or_none()
    if integ:
        await _sync_integration(s, tenant_id, integ, period_start, period_end)
        if integ.provider == "qbo":
            from .books_scan import run_scan
            await run_scan(s, tenant_id)
