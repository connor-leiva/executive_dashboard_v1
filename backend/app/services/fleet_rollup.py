"""Cross-workspace views for the operator console: sources by provider, and errors by cause.

Both answer the same question from a different side: is one broken thing breaking many
workspaces? One broken integration usually means one broken credential, not many broken tenants,
and nine failed runs from one expired token are one incident rather than nine.

Operational metadata only: provider names, statuses, timestamps and the error text a provider
returned. Never a record a sync pulled.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Business, Integration, SyncRun, Tenant
from .fleet_health import _aware, provider_name, stale_after

INCIDENT_WINDOW_DAYS = 30

# Known causes, matched against the provider's own error text. A message nothing here recognises
# is still an incident; it just says plainly that its cause is not known yet.
KNOWN_CAUSES = [
    ("qbo", re.compile(r"invalid_grant|refresh.?token|authenticationfailed|token.*(expired|revoked)", re.I),
     "QuickBooks authorisation expired",
     "Intuit expires a refresh token after 100 days without use, and revokes it when a password or "
     "app connection changes on the Intuit side.",
     "The workspace must re-authorise QuickBooks. Operators cannot re-authorise on a workspace's "
     "behalf: send the reconnect link."),
    (None, re.compile(r"\b401\b|unauthori[sz]ed|invalid.*(api.?key|token|credential)|forbidden|\b403\b", re.I),
     "Stored credentials rejected",
     "The provider refused the key or token saved for this connection, usually because it was "
     "rotated, revoked or lost a permission.",
     "Ask the workspace to paste a fresh credential in Settings, Integrations."),
    (None, re.compile(r"\b429\b|rate.?limit|too many requests", re.I),
     "Rate limited by the provider",
     "The provider is throttling this account. It usually clears on its own within the hour.",
     "Nothing, unless it persists across several syncs."),
    (None, re.compile(r"\b50[0234]\b|timed? ?out|timeout|connection (reset|refused|error)|temporarily", re.I),
     "Provider unavailable",
     "The provider did not answer, or answered with a server error.",
     "Nothing on our side. It clears when the provider recovers; retry after it does."),
]


def normalise(message: str | None) -> str:
    """The shape of an error message, with the parts that differ between occurrences removed, so
    the same failure from two runs groups together."""
    text = (message or "").lower()
    text = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<id>", text)
    text = re.sub(r"\b[0-9a-f]{16,}\b", "<id>", text)
    text = re.sub(r"\d+", "#", text)
    return re.sub(r"\s+", " ", text).strip()[:160]


def known_cause(provider: str, message: str | None) -> dict | None:
    for only, pattern, title, cause, fix in KNOWN_CAUSES:
        if (only is None or only == provider) and pattern.search(message or ""):
            return {"title": title, "cause": cause, "fix": fix}
    return None


async def providers(s: AsyncSession, now: dt.datetime) -> list[dict]:
    """Every provider in use across the fleet, with how many connections are fine, stale or
    broken, how many workspaces use it, and its oldest successful sync."""
    rows = (await s.execute(
        select(Integration, Tenant.status, Tenant.config).join(Tenant, Tenant.id == Integration.tenant_id)
        .where(Integration.status.in_(("connected", "error"))))).all()
    out: dict[str, dict] = {}
    for integ, tenant_status, cfg in rows:
        p = out.setdefault(integ.provider, {
            "key": integ.provider, "name": provider_name(integ.provider), "connections": 0,
            "ok": 0, "stale": 0, "error": 0, "paused": 0, "tenants": set(), "oldest_sync": None,
            "never_synced": 0})
        p["connections"] += 1
        p["tenants"].add(integ.tenant_id)
        last = _aware(integ.last_synced_at)
        paused = tenant_status == "suspended" or bool((cfg or {}).get("syncs_frozen"))
        if integ.status == "error":
            p["error"] += 1
        elif paused:
            p["paused"] += 1
        elif last is None or now - last > stale_after(integ.provider):
            p["stale"] += 1
        else:
            p["ok"] += 1
        if last is None:
            p["never_synced"] += 1
        elif p["oldest_sync"] is None or last < p["oldest_sync"]:
            p["oldest_sync"] = last
    result = []
    for p in out.values():
        p["tenants"] = len(p["tenants"])
        p["oldest_sync"] = p["oldest_sync"].isoformat() if p["oldest_sync"] else None
        p["state"] = "broken" if p["error"] else "watch" if p["stale"] else "healthy"
        result.append(p)
    return sorted(result, key=lambda p: (-p["error"], -p["stale"], p["name"]))


async def incidents(s: AsyncSession, now: dt.datetime) -> list[dict]:
    """Open errors grouped by cause.

    An incident is open while at least one connection still reports the error. Its occurrences
    are the failed sync runs in the last 30 days whose message has the same shape, so clearing
    the credential clears the incident, however many runs it failed.
    """
    since = now - dt.timedelta(days=INCIDENT_WINDOW_DAYS)
    broken = (await s.execute(
        select(Integration, Tenant.slug, Tenant.name, Business.name)
        .join(Tenant, Tenant.id == Integration.tenant_id)
        .outerjoin(Business, Business.id == Integration.business_id)
        .where(Integration.status == "error"))).all()
    groups: dict[tuple, dict] = {}
    for integ, slug, tname, biz in broken:
        key = (integ.provider, normalise(integ.last_error))
        g = groups.setdefault(key, {
            "provider": integ.provider, "provider_name": provider_name(integ.provider),
            "error": (integ.last_error or "The sync failed without an error message.")[:500],
            "workspaces": {}, "connections": [], "hits": 0, "first_seen": None, "last_seen": None})
        g["workspaces"][slug] = tname
        g["connections"].append({"tenant_slug": slug, "source_id": str(integ.id), "business": biz})
    if not groups:
        return []

    runs = (await s.execute(
        select(SyncRun.provider, SyncRun.detail, SyncRun.started_at, Tenant.slug)
        .join(Tenant, Tenant.id == SyncRun.tenant_id)
        .where(SyncRun.status == "error", SyncRun.started_at >= since))).all()
    for provider, detail, started, slug in runs:
        g = groups.get((provider, normalise(detail)))
        if g is None or slug not in g["workspaces"]:
            continue
        started = _aware(started)
        g["hits"] += 1
        if started is not None:
            g["first_seen"] = started if g["first_seen"] is None else min(g["first_seen"], started)
            g["last_seen"] = started if g["last_seen"] is None else max(g["last_seen"], started)

    out = []
    for (provider, shape), g in groups.items():
        cause = known_cause(provider, g["error"])
        out.append({
            # Stable across processes (hash() is salted per interpreter), so a key can be linked.
            "key": f"{provider}:{hashlib.sha1(shape.encode()).hexdigest()[:12]}",
            "severity": "broken",
            "provider": provider, "provider_name": g["provider_name"],
            "title": cause["title"] if cause else f"{g['provider_name']} is failing",
            "error": g["error"],
            "cause": cause["cause"] if cause else None,
            "fix": cause["fix"] if cause else None,
            "hits": g["hits"],
            "first_seen": g["first_seen"].isoformat() if g["first_seen"] else None,
            "last_seen": g["last_seen"].isoformat() if g["last_seen"] else None,
            "workspaces": [{"slug": k, "name": v} for k, v in sorted(g["workspaces"].items())],
            "connections": g["connections"],
        })
    return sorted(out, key=lambda i: (-len(i["workspaces"]), -i["hits"], i["title"]))


def group_runs(rows) -> dict[str, list[dict]]:
    """The latest runs per provider, newest first, for a workspace's Sources pane."""
    out: dict[str, list[dict]] = defaultdict(list)
    for run in rows:
        if len(out[run.provider]) >= 5:
            continue
        stats = run.stats or {}
        out[run.provider].append({
            "status": run.status,
            "started_at": _aware(run.started_at).isoformat() if run.started_at else None,
            "seconds": stats.get("seconds"), "records": stats.get("records"),
            "detail": (run.detail or "")[:300] or None,
        })
    return dict(out)
