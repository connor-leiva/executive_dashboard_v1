"""After a Sisu sync: tie deals to the Follow Up Boss leads they came from, and read their commission lines.

Phase 2 of docs/specs/ulrg-reporting-dashboard-port.md, §5.2 and §7. Only for people inside an
ACTIVE lead-source group (from its program start), and bounded per run, so a brokerage with 30,000
Sisu files and 130,000 Follow Up Boss people pays for the few hundred the reports cover.

THE JOIN, strongest first, the order the legacy report used:

  0. The bulk feed names the FUB person (Sisu external id under service `fub`) -- if it does.
  1. A contact lookup by FUB id (/v1/contact/external). When the id misses and the person has an
     email, the lookup falls back to the email with `save_mapping_on_fallback=true`, which writes
     the FUB id onto that Sisu contact. That write happens only on the fallback, only for an
     in-scope lead, and is logged on the run. The contact's transactions are the lead's.
     A contact with NO transactions is the duplicate-contact case (an old "{account}_{id}" mapping
     left the closes on another contact with the same email): every other contact with that email
     is searched and its transactions merged, deduped by transaction id.
  2. The lead's primary email equals the deal's buyer email or second email.

A contact or bulk-feed link overrides an email link; an email link only fills a deal nobody has
claimed. A link is never removed here.

COMMISSION LINES (§4.3) are read for linked CLOSED deals: commission-info plus edit-client, once,
and again while a close is recent enough for its adjustments to still be settling. The existing
`agent_commission` write is untouched, so the financial cards do not move.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm.attributes import flag_modified

from ..config import settings
from ..integrations import sisu
from ..models import Lead, Transaction
from . import lead_sources as ls

RECHECK_AFTER = dt.timedelta(days=3)        # a contact's deal list is re-read this often
RECENT_CLOSE = dt.timedelta(days=60)        # adjustments on a close can still change in this window
REREAD_AFTER = dt.timedelta(days=1)


def _aware(d):
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


async def after_sisu(s, tenant_id, integ, creds, mapped=None, *, now=None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    business_id = integ.business_id
    groups = await ls.groups_for_business(s, tenant_id, business_id)
    where = await ls.scope_filter(s, tenant_id, business_id, groups)
    stats = {"in_scope": 0, "lookups": 0, "contacts_found": 0, "mappings_saved": 0,
             "mapping_writes": [], "linked_feed": 0, "linked_contact": 0, "linked_email": 0,
             "commission_reads": 0}
    if where is None:
        return stats
    leads = list((await s.execute(select(Lead).where(where))).scalars().all())
    stats["in_scope"] = len(leads)
    by_ext = {lead.external_id: lead for lead in leads}

    strong: dict[str, object] = {}                 # transaction external_id -> lead id
    for t in mapped or []:
        fid = t.get("fub_person_id")
        if fid and fid in by_ext:
            strong[t["external_id"]] = by_ext[fid].id
            stats["linked_feed"] += 1

    service = (integ.config or {}).get("external_service_id") or settings.SISU_EXTERNAL_SERVICE or "fub"
    due = [lead for lead in leads if lead.email and (
        lead.sisu_checked_at is None or now - _aware(lead.sisu_checked_at) >= RECHECK_AFTER)]
    due.sort(key=lambda lead: (lead.sisu_checked_at is not None, _aware(lead.sisu_checked_at) or now))
    batch = due[:settings.SISU_CONTACT_LOOKUPS_PER_RUN]

    async with sisu.contact_client(creds) as c:
        for lead in batch:
            contact, saved = await sisu.contact_by_fub_id(c, lead.external_id, lead.email, service)
            stats["lookups"] += 1
            lead.sisu_checked_at = now
            if contact is None:
                continue
            stats["contacts_found"] += 1
            if saved:
                stats["mappings_saved"] += 1
                if len(stats["mapping_writes"]) < 50:
                    stats["mapping_writes"].append(lead.external_id)
            cid = contact.get("contact_id")
            if cid in (None, ""):
                continue
            lead.sisu_contact_id = str(cid)[:32]
            txs = await sisu.contact_transactions(c, cid)
            if not txs and lead.email:
                seen_contacts, seen_tx = {str(cid)}, set()
                for other in await sisu.contacts_by_email(c, lead.email):
                    ocid = other.get("contact_id")
                    if ocid in (None, "") or str(ocid) in seen_contacts:
                        continue
                    seen_contacts.add(str(ocid))
                    for tx in await sisu.contact_transactions(c, ocid):
                        tid = sisu.txn_id_of(tx)
                        if tid and tid not in seen_tx:
                            seen_tx.add(tid)
                            txs.append(tx)
            for tx in txs:
                tid = sisu.txn_id_of(tx)
                if tid:
                    strong[tid] = lead.id

        # ── apply the links ──
        email_owner: dict[str, object] = {}
        for lead in sorted(leads, key=lambda x: (x.src_created_at or now, x.external_id)):
            if lead.email:
                email_owner.setdefault(lead.email.strip().lower(), lead.id)
        txns = (await s.execute(select(Transaction).where(
            Transaction.tenant_id == tenant_id, Transaction.business_id == business_id,
            or_(Transaction.external_id.in_(list(strong) or [""]),
                Transaction.buyer_email.is_not(None), Transaction.buyer_email2.is_not(None)))
        )).scalars().all()
        for t in txns:
            if t.external_id in strong:
                if t.lead_id != strong[t.external_id]:
                    t.lead_id = strong[t.external_id]
                    stats["linked_contact"] += 1
                continue
            if t.lead_id is not None:
                continue
            for em in (t.buyer_email, t.buyer_email2):
                owner = email_owner.get((em or "").strip().lower())
                if owner is not None:
                    t.lead_id = owner
                    stats["linked_email"] += 1
                    break
        await s.flush()

        # ── commission lines for linked closes ──
        defaults = {}
        lead_by_id = {lead.id: lead for lead in leads}
        for lead in leads:
            g = ls.group_for(lead.origin, None, groups)
            match = next((x for x in groups if x.key == g["key"]), None)
            defaults[lead.id] = ((match.exp_risk_team_default, match.exp_risk_agent_default)
                                 if match else (Decimal(0), Decimal(0)))
        closes = (await s.execute(select(Transaction).where(
            Transaction.tenant_id == tenant_id, Transaction.business_id == business_id,
            Transaction.status == "closed", Transaction.lead_id.in_(list(lead_by_id) or [None]))
        )).scalars().all()
        today = now.date()
        summary_fee = {t["external_id"]: t.get("trans_fee") for t in (mapped or [])}

        def wants(t):
            read = _aware(t.commission_read_at)
            if read is None:
                return True
            recent = t.close_date is not None and today - t.close_date <= RECENT_CLOSE
            return recent and now - read >= REREAD_AFTER

        todo = sorted((t for t in closes if wants(t)),
                      key=lambda t: (t.commission_read_at is not None, t.close_date or today))
        for t in todo[:settings.SISU_COMMISSION_READS_PER_RUN]:
            ci = await sisu.commission_info(c, t.external_id)
            detail = await sisu.client_detail(c, t.external_id)
            team_d, agent_d = defaults.get(t.lead_id, (Decimal(0), Decimal(0)))
            lines = sisu.commission_lines(gci=float(t.gci) if t.gci is not None else None,
                                          summary_trans_fee=summary_fee.get(t.external_id),
                                          ci=ci, detail=detail,
                                          exp_team_default=float(team_d), exp_agent_default=float(agent_d))
            for k, v in lines.items():
                setattr(t, k, Decimal(str(v)) if isinstance(v, float) else v)
            t.commission_read_at = now
            stats["commission_reads"] += 1

    # The run's record of what this pass did -- including every FUB id it wrote back to Sisu --
    # read into SyncRun.stats by sync._sync_integration.
    integ.config = {**(integ.config or {}), "lead_source_last_run": {"at": now.isoformat(), **stats}}
    flag_modified(integ, "config")
    await s.commit()
    return {k: v for k, v in stats.items() if k != "mapping_writes"}
