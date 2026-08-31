"""Who is behind an ads number. SPEC-ads-module.md Part 15 (lineage).

Every rung on the chain is a count of PEOPLE, and a count nobody can open is a count nobody can
check. This returns the same `{metric, type, title, subtitle, count, columns, rows}` envelope the
launch and sales-desk drills already return, so the contract is one contract even though the ads
tab renders it with its own tokens.

Two things this deliberately does NOT do:

  * It does not recount. The rows come from the same AdConversion rows the funnel counted, so the
    drill cannot disagree with the number it opened. A drill that runs its own query is a second
    definition of the metric, and the two drift.
  * It does not invent identity. Names and emails come from the MetricRecord the conversion was
    written from; where there is none, the row shows the contact id rather than a blank, because
    a blank reads as missing data when it is actually an unsynced name.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AdAttribution, AdCampaign, AdConversion, MetricRecord
from .ads_funnel import ACUMYN_STAGES, FUNNEL_DEFS

# Stages whose source field exists but is never populated on this tenant's data are NOT errors,
# and the drill is the natural place to say so - somebody clicking a zero wants to know whether
# it means "nobody did this" or "we do not record this".
STAGE_LABELS = {d["key"]: d["label"] for d in FUNNEL_DEFS["program"]}


def _identity_rows(recs: list[MetricRecord]) -> dict[str, MetricRecord]:
    """contact_id -> the record carrying the best identity for it."""
    out: dict[str, MetricRecord] = {}
    for r in recs:
        cid = str((r.meta or {}).get("contact_id") or "").strip()
        if not cid:
            continue
        # Prefer a record that actually has a name; the first one seen otherwise.
        if cid not in out or (r.name and not out[cid].name):
            out[cid] = r
    return out


async def drill_ads(s: AsyncSession, tenant_id, acct, metric: str, start, end,
                    basis: str = "cohort", campaign=None) -> dict | None:
    """One funnel rung, opened. `metric` is `funnel.<stage>`; None for anything unrecognised."""
    if not metric.startswith("funnel."):
        return None
    stage = metric.split(".", 1)[1]
    if stage not in ACUMYN_STAGES:
        return None

    label = STAGE_LABELS.get(stage, stage.title())

    attrs = list((await s.execute(select(AdAttribution).where(
        AdAttribution.tenant_id == tenant_id))).scalars())
    by_attr = {a.id: a for a in attrs}
    camps = {c.id: c.name for c in (await s.execute(select(AdCampaign).where(
        AdCampaign.tenant_id == tenant_id))).scalars()}

    convs = list((await s.execute(select(AdConversion).where(
        AdConversion.tenant_id == tenant_id,
        AdConversion.stage_key == stage))).scalars())

    # The SAME window rule the funnel used, or the drill would open a different population than
    # the number it was opened from - the one failure that makes a drill worse than none.
    def in_window(c: AdConversion) -> bool:
        a = by_attr.get(c.attribution_id)
        if a is None:
            return False
        if campaign is not None and a.campaign_id != campaign:
            return False
        if basis == "period":
            return c.occurred_on is not None and start <= c.occurred_on <= end
        return a.first_seen_on is not None and start <= a.first_seen_on <= end

    convs = [c for c in convs if in_window(c)]

    keys = {by_attr[c.attribution_id].identity_key for c in convs if c.attribution_id in by_attr}
    recs = _identity_rows(list((await s.execute(select(MetricRecord).where(
        MetricRecord.tenant_id == tenant_id,
        MetricRecord.kind.in_(("bc_shift_reg", "bc_onboarded", "bc_launch_opp")))))
        .scalars())) if keys else {}

    rows = []
    for c in convs:
        a = by_attr.get(c.attribution_id)
        if a is None:
            continue
        rec = recs.get(a.identity_key)
        row = {
            "name": (rec.name if rec and rec.name else a.identity_key),
            "email": (rec.email if rec and rec.email else a.email_norm) or "—",
            "campaign": camps.get(a.campaign_id) or "— channel only",
            "match": a.match_method,
            "first_seen": a.first_seen_on.isoformat() if a.first_seen_on else "—",
            "reached": c.occurred_on.isoformat() if c.occurred_on else "undated",
            "url": rec.source_url if rec else None,
        }
        if stage == "closed":
            row["value"] = (f"{float(c.value_contracted):,.0f}"
                            if c.value_contracted is not None else "—")
        rows.append(row)

    # Newest first: somebody opening Enrolled wants the most recent two, not the oldest.
    rows.sort(key=lambda r: (r["reached"] == "undated", r["reached"]), reverse=True)

    columns = ["name", "email", "campaign", "match", "first_seen", "reached", "url"]
    if stage == "closed":
        columns.insert(4, "value")

    basis_note = ("counted where the stage was REACHED in this window"
                  if basis == "period" else
                  "counted where the person was FIRST SEEN in this window, wherever the stage "
                  "landed later")
    subtitle = f"{len(rows)} at this stage · {basis_note}"

    # An empty rung is either "nobody got here" or "we do not record this", and those are
    # completely different facts. The funnel cannot tell them apart; the drill can, because it
    # knows whether the source ever produces this stage at all.
    note = None
    if not rows:
        total_at_stage = (await s.execute(select(AdConversion).where(
            AdConversion.tenant_id == tenant_id,
            AdConversion.stage_key == stage))).scalars().first()
        note = (f"No {label.lower()} records exist anywhere in this workspace, for any window or "
                f"campaign - so this rung reads zero because the stage is not being recorded, "
                f"not because nobody reached it."
                if total_at_stage is None else
                f"Nobody reached {label.lower()} in this window. The stage IS recorded "
                f"elsewhere, so this is a real zero.")

    return {"metric": metric, "type": "records", "title": label, "subtitle": subtitle,
            "count": len(rows), "columns": columns, "rows": rows, "note": note}
