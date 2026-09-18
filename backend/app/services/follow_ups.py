"""Who needs a follow-up: the rules behind the portal's Needs You Today.

This section of the home page was three rows compiled into the portal -- "New lead follow-up ·
Source not connected" -- whatever the workspace had connected. It now answers the question the
title asks, per agent, from what the Follow Up Boss sync holds.

THE RULES are Follow Up Boss's own idea of a follow-up, not ours:
  * NEW LEAD   -- nobody has contacted them (`contacted` false), they arrived in the last N days,
                  and they are not in Trash. Speed to lead is the whole game with a new lead.
  * OVERDUE    -- an open task on them was due before today, within the last M days. Older ones
                  are left out rather than burying yesterday's missed call under two-year-old
                  action-plan tasks; admins see how many the window leaves out.
  * DUE TODAY  -- an open task on them is due today, in the workspace's calendar.
  * GOING COLD -- off until an admin picks the stages it applies to: no activity for K days while
                  in one of them. Stage names are the account's own, so there is no honest default.

ONE ROW PER PERSON. A new lead with a call due today is one person to ring, listed once under the
more urgent reason with the other beside it. Counts are distinct people per rule.

A ROW OPENS THE PERSON IN FOLLOW UP BOSS and stores only a name. The call happens there, where it
is logged and where `contacted` flips -- a tel: link from here would dial around the CRM and leave
the lead looking untouched -- so no phone number or email address is copied into this database.

EMPTIES ARE REASONS. As in member_numbers: no connection, no sync yet, not on the roster, no FUB
user matched, a role without Win the Day, and genuinely caught up are six different facts that need
six different people to act, and only the last is good news.

PERMISSIONS BELONG TO THE ROUTER. This module is told whether the viewer may see their own queue
and the team's, and counts.
"""
from __future__ import annotations

import datetime as dt
import re
import uuid

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Agent, CrmTask, Integration, IntranetMember, Lead
from . import member_identity

DEFAULTS = {"new_lead_days": 7, "overdue_max_days": 30, "cold_enabled": False, "cold_days": 14,
            "cold_stages": []}
BOUNDS = {"new_lead_days": (1, 60), "overdue_max_days": (0, 365), "cold_days": (3, 365)}
KINDS = ("new_lead", "overdue", "due_today", "going_cold")          # most urgent first
ITEM_CAP = 200
MAX_STAGES = 30
TRASH = "Trash"
_DOMAIN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


# ── settings ─────────────────────────────────────────────────────────────────────────────

def clean_settings(raw: dict | None, *, strict: bool = False) -> dict:
    """The workspace's settings, bounded. `strict` raises ValueError(field) instead of clamping,
    for the console's PATCH -- a stored value is clamped because it has to render regardless."""
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULTS, cold_stages=[])
    for key, (lo, hi) in BOUNDS.items():
        if key not in raw:
            continue
        try:
            value = int(raw[key])
        except (TypeError, ValueError):
            if strict:
                raise ValueError(key)
            continue
        if strict and not lo <= value <= hi:
            raise ValueError(key)
        out[key] = min(max(value, lo), hi)
    if "cold_enabled" in raw:
        out["cold_enabled"] = bool(raw["cold_enabled"])
    stages = raw.get("cold_stages")
    if stages is not None:
        if not isinstance(stages, list):
            if strict:
                raise ValueError("cold_stages")
            stages = []
        seen: list[str] = []
        for stage in stages:
            name = str(stage or "").strip()[:80]
            if name and name != TRASH and name not in seen:
                seen.append(name)
        if strict and len(seen) > MAX_STAGES:
            raise ValueError("cold_stages")
        out["cold_stages"] = seen[:MAX_STAGES]
    return out


def settings_for(tenant) -> dict:
    cfg = (getattr(tenant, "config", None) or {}).get("intranet") or {}
    return clean_settings(cfg.get("follow_ups"))


def cold_active(rules: dict) -> bool:
    return bool(rules["cold_enabled"] and rules["cold_stages"])


# ── the connection ───────────────────────────────────────────────────────────────────────

def _aware(ts: dt.datetime | None) -> dt.datetime | None:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)


def account_domain(integ: Integration | None) -> str | None:
    """The FUB account's subdomain, if it is one: it goes into a link, so it is checked."""
    if integ is None:
        return None
    domain = str(((integ.config or {}).get("fub_state") or {}).get("account_domain") or "")
    domain = domain.strip().lower()
    return domain if _DOMAIN.match(domain) else None


def person_url(domain: str | None, person_id) -> str | None:
    if not domain or person_id in (None, ""):
        return None
    return f"https://{domain}.followupboss.com/2/people/view/{person_id}"


async def fub_integration(s: AsyncSession, tenant_id) -> Integration | None:
    """The workspace's Follow Up Boss connection: a live one before a disconnected one, then the
    most recently synced."""
    rows = (await s.execute(select(Integration).where(
        Integration.tenant_id == tenant_id, Integration.provider == "fub"))).scalars().all()
    if not rows:
        return None

    def rank(row):
        live = row.status in ("connected", "error")
        synced = _aware(row.last_synced_at) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
        return (live, synced)
    return max(rows, key=rank)


def connection(integ: Integration | None, *, show_error: bool) -> dict:
    """Whether follow-ups can be shown at all, and how fresh they are.

    `error` still counts as connected -- the credentials are there and the next pass retries, as
    `sync.run_all` treats it -- but the last good data is shown with the failure beside it rather
    than replaced by an empty box.
    """
    if integ is None or integ.status not in ("connected", "error"):
        return {"state": "not_connected", "status": integ.status if integ else None,
                "synced_at": None, "sync_failed": False, "error": None, "account_domain": None}
    fstate = (integ.config or {}).get("fub_state") or {}
    quick = fstate.get("quick") or {}
    stamps = [_aware(integ.last_synced_at)]
    if quick.get("ok") and quick.get("at"):
        try:
            stamps.append(_aware(dt.datetime.fromisoformat(quick["at"])))
        except ValueError:
            pass
    stamps = [t for t in stamps if t is not None]
    synced = max(stamps) if stamps else None
    failed = integ.status == "error" or (quick.get("ok") is False)
    reason = integ.last_error if integ.status == "error" else (quick.get("error") if failed else None)
    return {
        "state": "ready" if synced else "not_synced",
        "status": integ.status,
        "synced_at": synced.isoformat() if synced else None,
        "sync_failed": bool(failed),
        # The raw error is for the people who can fix it; everybody else is told it failed.
        "error": (str(reason)[:300] if reason else None) if show_error else None,
        "account_domain": account_domain(integ),
    }


# ── one queue ────────────────────────────────────────────────────────────────────────────

def _not_trash():
    return func.coalesce(Lead.stage, "") != TRASH


def _whose(column, agent_id):
    """`agent_id` None means nobody's: the unassigned queue."""
    return column.is_(None) if agent_id is None else column == agent_id


def _window(rules: dict, today: dt.date) -> dt.date | None:
    days = rules["overdue_max_days"]
    return today - dt.timedelta(days=days) if days else None


async def queue(s: AsyncSession, tenant_id, agent_id: uuid.UUID | None, rules: dict, *,
                today: dt.date, now: dt.datetime, domain: str | None,
                cap: int = ITEM_CAP) -> dict:
    """One agent's follow-ups -- or the unassigned ones, for agent_id None."""
    new_since = now - dt.timedelta(days=rules["new_lead_days"])
    people: dict[str, dict] = {}

    def person_of(lead: Lead | None, ext: str | None) -> dict:
        key = (lead.external_id if lead is not None else None) or ext or ""
        row = people.get(key)
        if row is None:
            row = people[key] = {
                "key": key, "kinds": [], "task": None, "at": None, "sort": None,
                "person": {"id": key or None,
                           "name": (lead.name if lead is not None else None),
                           "stage": (lead.stage if lead is not None else None),
                           "origin": (lead.origin if lead is not None else None)},
            }
        return row

    def add(row: dict, kind: str, *, at, sort, task=None) -> None:
        """The most urgent reason decides where the person sits and what the row says; within
        one reason, the task that sorts first (the latest miss, the earliest call) is shown."""
        if kind not in row["kinds"]:
            row["kinds"].append(kind)
        elif kind != min(row["kinds"], key=KINDS.index) or sort >= row["sort"]:
            return
        if kind == min(row["kinds"], key=KINDS.index):
            row["at"], row["sort"] = at, sort
            if task is not None:
                row["task"] = task
        elif row["task"] is None and task is not None:
            row["task"] = task

    counts = {kind: 0 for kind in KINDS}

    # New leads.
    for lead in (await s.execute(select(Lead).where(
            Lead.tenant_id == tenant_id, Lead.source == "fub", _whose(Lead.agent_id, agent_id),
            Lead.contacted.is_(False), _not_trash(), Lead.src_created_at >= new_since,
    ).order_by(Lead.src_created_at.desc()))).scalars():
        counts["new_lead"] += 1
        created = _aware(lead.src_created_at)
        add(person_of(lead, None), "new_lead", at=created,
            sort=-(created.timestamp() if created else 0))

    # Tasks due today and overdue (inside the window).
    window = _window(rules, today)
    conds = [CrmTask.tenant_id == tenant_id, CrmTask.source == "fub",
             _whose(CrmTask.agent_id, agent_id), CrmTask.due_on.is_not(None),
             CrmTask.due_on <= today]
    if window is not None:
        conds.append(CrmTask.due_on >= window)
    rows = (await s.execute(
        select(CrmTask, Lead).outerjoin(Lead, and_(
            Lead.tenant_id == CrmTask.tenant_id, Lead.source == "fub",
            Lead.external_id == CrmTask.person_external_id))
        .where(*conds))).all()
    seen_task_people = {"overdue": set(), "due_today": set()}
    for task, lead in rows:
        if lead is not None and lead.stage == TRASH:
            continue
        kind = "due_today" if task.due_on == today else "overdue"
        row = person_of(lead, task.person_external_id or f"task:{task.external_id}")
        if row["key"] not in seen_task_people[kind]:
            seen_task_people[kind].add(row["key"])
            counts[kind] += 1
        due_at = _aware(task.due_at)
        detail = {"name": task.name, "type": task.task_type,
                  "due_on": task.due_on.isoformat(), "due_at": due_at.isoformat() if due_at else None}
        if kind == "overdue":
            # Most recently due first: yesterday's miss above last month's.
            sort = -(task.due_on.toordinal() * 86400 + (due_at.timestamp() % 86400 if due_at else 0))
        else:
            # Earliest first; a task with a time comes before one due "some time today".
            sort = due_at.timestamp() if due_at else float("inf")
        add(row, kind, at=due_at or dt.datetime.combine(task.due_on, dt.time.min,
                                                         tzinfo=dt.timezone.utc),
            sort=sort, task=detail)

    cold_ids: set = set()
    # Going cold. Counted in full, listed up to the cap: an agent can have hundreds of quiet
    # clients, and a count read off a capped list would stop at the cap while the team's did not.
    if cold_active(rules):
        quiet_since = now - dt.timedelta(days=rules["cold_days"])
        cold = (Lead.tenant_id == tenant_id, Lead.source == "fub", _whose(Lead.agent_id, agent_id),
                Lead.stage.in_(rules["cold_stages"]), Lead.last_activity_at.is_not(None),
                Lead.last_activity_at < quiet_since)
        cold_ids = set((await s.execute(select(Lead.external_id).where(*cold))).scalars())
        counts["going_cold"] = len(cold_ids)
        for lead in (await s.execute(select(Lead).where(*cold).order_by(
                Lead.last_activity_at.desc()).limit(cap + 1))).scalars():
            last = _aware(lead.last_activity_at)
            add(person_of(lead, None), "going_cold", at=last, sort=-last.timestamp())

    ordered = sorted(people.values(), key=lambda r: (KINDS.index(min(r["kinds"], key=KINDS.index)),
                                                     r["sort"] if r["sort"] is not None else 0,
                                                     (r["person"]["name"] or "").lower()))
    items = []
    for r in ordered[:cap]:
        primary = min(r["kinds"], key=KINDS.index)
        items.append({
            "kind": primary,
            "also": [k for k in KINDS if k in r["kinds"] and k != primary],
            "person": r["person"],
            "task": r["task"],
            "at": r["at"].isoformat() if r["at"] else None,
            "url": person_url(domain, r["person"]["id"]),
        })
    counts["total"] = len(set(people) | cold_ids)
    return {"counts": counts, "items": items, "truncated": len(ordered) > cap}


# ── the team ─────────────────────────────────────────────────────────────────────────────

async def team(s: AsyncSession, tenant_id, rules: dict, *, today: dt.date,
               now: dt.datetime) -> dict:
    """Per-agent counts for everyone with something waiting, and the unassigned new leads.

    Distinct people per rule and per agent, computed from (agent, person) pairs rather than
    summed, so a person with two overdue tasks is one person to call.
    """
    per: dict = {}

    def mark(agent_id, kind, person):
        per.setdefault(agent_id, {k: set() for k in KINDS})[kind].add(person)

    new_since = now - dt.timedelta(days=rules["new_lead_days"])
    for agent_id, ext in (await s.execute(select(Lead.agent_id, Lead.external_id).where(
            Lead.tenant_id == tenant_id, Lead.source == "fub", Lead.contacted.is_(False),
            _not_trash(), Lead.src_created_at >= new_since))).all():
        mark(agent_id, "new_lead", ext)

    window = _window(rules, today)
    conds = [CrmTask.tenant_id == tenant_id, CrmTask.source == "fub",
             CrmTask.due_on.is_not(None), CrmTask.due_on <= today]
    if window is not None:
        conds.append(CrmTask.due_on >= window)
    for agent_id, person, ext, due_on, stage in (await s.execute(
            select(CrmTask.agent_id, CrmTask.person_external_id, CrmTask.external_id,
                   CrmTask.due_on, Lead.stage)
            .outerjoin(Lead, and_(Lead.tenant_id == CrmTask.tenant_id, Lead.source == "fub",
                                  Lead.external_id == CrmTask.person_external_id))
            .where(*conds))).all():
        if stage == TRASH:
            continue
        mark(agent_id, "due_today" if due_on == today else "overdue", person or f"task:{ext}")

    if cold_active(rules):
        quiet_since = now - dt.timedelta(days=rules["cold_days"])
        for agent_id, ext in (await s.execute(select(Lead.agent_id, Lead.external_id).where(
                Lead.tenant_id == tenant_id, Lead.source == "fub",
                Lead.stage.in_(rules["cold_stages"]), Lead.last_activity_at.is_not(None),
                Lead.last_activity_at < quiet_since))).all():
            mark(agent_id, "going_cold", ext)

    names = {}
    ids = [a for a in per if a is not None]
    if ids:
        names = dict((await s.execute(select(Agent.id, Agent.name).where(
            Agent.tenant_id == tenant_id, Agent.id.in_(ids)))).all())

    totals = {k: set() for k in KINDS}
    rows = []
    for agent_id, sets in per.items():
        for k in KINDS:
            totals[k] |= {(agent_id, p) for p in sets[k]}
        if agent_id is None:
            continue
        everyone = set().union(*sets.values())
        rows.append({"agent_id": str(agent_id), "name": names.get(agent_id) or "Unnamed agent",
                     **{k: len(sets[k]) for k in KINDS}, "total": len(everyone)})
    rows.sort(key=lambda r: (-r["total"], -r["overdue"], r["name"].lower()))
    unassigned = per.get(None)
    counts = {k: len(totals[k]) for k in KINDS}
    counts["total"] = len(set().union(*totals.values()))
    return {"counts": counts,
            "unassigned_new_leads": len(unassigned["new_lead"]) if unassigned else 0,
            "unassigned_total": len(set().union(*unassigned.values())) if unassigned else 0,
            "by_agent": rows}


# ── the whole payload ────────────────────────────────────────────────────────────────────

async def payload(s: AsyncSession, tenant, member: IntranetMember | None, *, today: dt.date,
                  now: dt.datetime, own_allowed: bool, team_allowed: bool, show_error: bool,
                  viewing: Agent | str | None = None) -> dict:
    """GET /intranet/follow-ups. `viewing` is an agent a leader opened, or "unassigned"."""
    rules = settings_for(tenant)
    integ = await fub_integration(s, tenant.id)
    conn = connection(integ, show_error=show_error)
    out = {"connection": conn, "rules": rules, "as_of": today.isoformat(),
           "own": None, "own_reason": None, "team": None, "viewing": None}
    if conn["state"] != "ready":
        return out
    domain = conn["account_domain"]

    if member is None:
        out["own_reason"] = "not_on_roster"
    elif not own_allowed:
        out["own_reason"] = "denied"
    else:
        match = (await member_identity.matches_for(s, tenant.id, member)).get("fub")
        if match is None:
            out["own_reason"] = "unmatched"
        else:
            agent, how = match
            out["own"] = {"agent": {"id": str(agent.id), "name": agent.name, "matched_by": how},
                          **await queue(s, tenant.id, agent.id, rules, today=today, now=now,
                                        domain=domain)}

    if team_allowed:
        out["team"] = await team(s, tenant.id, rules, today=today, now=now)
        if viewing == "unassigned":
            out["viewing"] = {"agent_id": "unassigned", "name": "Unassigned",
                              **await queue(s, tenant.id, None, rules, today=today, now=now,
                                            domain=domain)}
        elif isinstance(viewing, Agent):
            out["viewing"] = {"agent_id": str(viewing.id), "name": viewing.name,
                              **await queue(s, tenant.id, viewing.id, rules, today=today, now=now,
                                            domain=domain)}
    return out


async def stages_in_use(s: AsyncSession, tenant_id) -> list[dict]:
    """The stages the synced leads actually carry, with counts: the Going Cold picker offers
    these rather than asking an admin to spell their own pipeline from memory."""
    rows = (await s.execute(select(Lead.stage, func.count()).where(
        Lead.tenant_id == tenant_id, Lead.source == "fub", Lead.stage.is_not(None),
        Lead.stage != TRASH).group_by(Lead.stage).order_by(func.count().desc()))).all()
    return [{"stage": stage, "people": int(n)} for stage, n in rows]
