"""Which Follow Up Boss source strings a lead-source group should list, found by asking FUB.

A port of the legacy dashboard's `_discover_variants` (cohorts_live.py). A group only reaches the
people whose `source` it names, and FUB's source strings are typed by whoever set up each lead
feed: "Realtor.com", "Realtor.com MVIP", a bare "Realtor", "Market VIP via Opcity BLA". A string
nobody listed is a cohort that silently comes up short. So, for each group:

  * SOURCE PROBES. `/people?source=<s>&createdAfter=<program start>&limit=1` for every candidate,
    reading FUB's `_metadata.total`: how many people that exact string has brought since the
    program began. The candidates are the group's variants, the first word of each (a "Realtor.com"
    group probes a bare "Realtor"), and any source already synced here that shares one of those
    words.
  * SEARCH PROBES. `/people?q=<hint>&createdAfter=...&limit=1` for the same words in lower case, as
    the legacy RELATED_NON_REALTOR_HINTS probe did. FUB's search reads names and addresses as well
    as sources, so the total is a hint, not a count; the one person it returns names a source
    string that is real, and that string is probed too.

Nothing about a particular brokerage is written here: the words come from the group's own
variants. One request per candidate, `limit=1`, so a group costs a dozen requests.

The result is stored on the FUB integration (`fub_state.lead_source_discovery[group key]`), by the
daily full sync and by Settings' "Check Follow Up Boss" action, and read back by Settings, which
adds what is already synced here and whether the group matches each string today.
"""
from __future__ import annotations

import datetime as dt
import re

import httpx
from sqlalchemy import func, select

from ..integrations import fub
from ..models import Lead, LeadSourceGroup
from . import lead_sources as ls

_WORDS = re.compile(r"[\s.]+")
MIN_STEM = 4                    # "Realtor" yes; "BLA", "via" no
MAX_SYNCED_CANDIDATES = 15      # synced strings probed per group, the most common first


def _variants(group: LeadSourceGroup) -> list[str]:
    return [str(v).strip() for v in (group.variants or []) if str(v).strip()]


def stems(variants: list[str]) -> list[str]:
    """The first word of each variant, when it is a word of its own: "Realtor.com" gives "Realtor".
    Exact-case as first written; none that is already a variant."""
    have = {v.lower() for v in variants}
    out: list[str] = []
    for v in variants:
        head = _WORDS.split(v)[0]
        if len(head) >= MIN_STEM and head.lower() not in have:
            have.add(head.lower())
            out.append(head)
    return out


def hints(variants: list[str]) -> list[str]:
    """Search words: each variant's first word, and its first two words when it has more than two
    ("Market VIP via Opcity BLA" gives "market vip"). Lower case, no repeats."""
    out: list[str] = []
    for v in variants:
        head = _WORDS.split(v.strip().lower())[0]
        spaced = v.lower().split()                       # "realtor.com mvip" is two words, not three
        for h in (head if len(head) >= MIN_STEM else None,
                  " ".join(spaced[:2]) if len(spaced) > 2 else None):
            if h and h not in out:
                out.append(h)
    return out


def related(origin: str, words: list[str]) -> bool:
    low = (origin or "").lower()
    return any(w.lower() in low for w in words)


async def synced_counts(s, tenant_id, business_id, group: LeadSourceGroup) -> dict[str, int]:
    """Leads already stored on this business per FUB source string, from the program start."""
    q = select(Lead.origin, func.count()).where(
        Lead.tenant_id == tenant_id, Lead.business_id == business_id, Lead.origin.is_not(None))
    if group.program_start:
        q = q.where(Lead.created_at_src >= group.program_start)
    return {o: int(n) for o, n in (await s.execute(q.group_by(Lead.origin))).all() if o}


async def discover(c: httpx.AsyncClient, group: LeadSourceGroup, since: dt.datetime | None,
                   synced: dict[str, int], *, now: dt.datetime | None = None) -> dict:
    """Probe FUB for a group's source strings. `since` is the program start as an aware datetime
    (the workspace's midnight), `synced` what synced_counts returned."""
    from .fub_sync import fmt
    now = now or dt.datetime.now(dt.timezone.utc)
    variants = _variants(group)
    # The same people the ingestion reads: trashed ones too, from the program start.
    after = {"includeTrash": "true", **({"createdAfter": fmt(since)} if since else {})}
    words = stems(variants) + hints(variants)

    cands: dict[str, tuple[str, str]] = {}           # lower -> (as written, how it was found)

    def add(src: str, via: str) -> None:
        if src and src.strip() and src.strip().lower() not in cands:
            cands[src.strip().lower()] = (src.strip(), via)

    for v in variants:
        add(v, "variant")
    for st in stems(variants):
        add(st, "first word")
    near = sorted(((o, n) for o, n in synced.items() if related(o, words)), key=lambda x: -x[1])
    for o, _n in near[:MAX_SYNCED_CANDIDATES]:
        add(o, "synced here")

    searches = []
    for h in hints(variants):
        total, row = await fub.count_people(c, {"q": h, **after})
        sample = fub.clip((row or {}).get("source"), 120)
        searches.append({"q": h, "total": total, "sample_source": sample})
        if sample:
            add(sample, "search")

    sources = []
    for src, via in cands.values():
        total, _row = await fub.count_people(c, {"source": src, **after})
        sources.append({"source": src, "fub_count": total, "via": via})
    return {"at": now.isoformat(timespec="seconds"),
            "program_start": group.program_start.isoformat() if group.program_start else None,
            "sources": sources, "searches": searches}


def view(group: LeadSourceGroup, found: dict | None, synced: dict[str, int]) -> dict:
    """What Settings shows for a group: every string FUB reported or this business holds that the
    group matches or is near, with both counts and whether the group matches it today."""
    variants = _variants(group)
    match = ls.build_matcher(group.variants or [], group.match_mode)
    words = stems(variants) + hints(variants)
    rows: dict[str, dict] = {}
    for r in (found or {}).get("sources") or []:
        rows[r["source"].lower()] = {"source": r["source"], "fub_count": r.get("fub_count"),
                                     "via": r.get("via"), "synced_count": 0}
    for origin, n in synced.items():
        key = origin.lower()
        if key in rows:
            rows[key]["synced_count"] += n
        elif match(origin) or related(origin, words):
            rows[key] = {"source": origin, "fub_count": None, "via": "synced here", "synced_count": n}
    out = []
    for r in rows.values():
        r["in_group"] = bool(match(r["source"]))
        r["is_variant"] = r["source"].lower() in {v.lower() for v in variants}
        if not r["is_variant"] and not (r["fub_count"] or 0) and not r["synced_count"]:
            continue                       # a probed word nobody uses is not a suggestion
        out.append(r)
    out.sort(key=lambda r: (not r["in_group"], -(r["fub_count"] or 0), -r["synced_count"], r["source"].lower()))
    shown = {r["source"].lower() for r in out}
    others = sum(1 for o in synced if o.lower() not in shown)
    return {"checked_at": (found or {}).get("at"), "sources": out,
            "searches": (found or {}).get("searches") or [], "other_sources": others,
            "stale": bool(found) and (found.get("program_start") !=
                                      (group.program_start.isoformat() if group.program_start else None))}
