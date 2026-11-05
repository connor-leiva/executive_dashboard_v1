"""Ask the real SkySlope the questions only a live call can answer, and print the answers.

Phase 2's done-when, and the list at the top of app/integrations/skyslope.py. A person runs this once
test credentials exist. It reads and never writes, and it prints SHAPES rather than content: key
names, types, counts and headers. No names, emails, addresses, sessions or keys.

Credentials are read from the environment, or from the uncommitted backend/.probe.env, and are never
printed:
  SKYSLOPE_CLIENT_ID, SKYSLOPE_CLIENT_SECRET   the application's, from SkySlope's CSM
  SKYSLOPE_ACCESS_KEY, SKYSLOPE_ACCESS_SECRET  a user's, from My Account, Integrations
  SKYSLOPE_BASE_URL                            optional: the test account may be on staging or integ

Usage:
  cd backend && ./.venv/Scripts/python.exe -m scripts.skyslope_probe
"""
from __future__ import annotations

import asyncio
import collections
import datetime as dt
import os
import sys
import time
from pathlib import Path

from app.config import settings
from app.integrations import skyslope

SINCE = "2000-01-01T00:00:00Z"


def _load_probe_env() -> dict:
    path = Path(__file__).resolve().parents[1] / ".probe.env"
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _shape(value) -> str:
    if isinstance(value, dict):
        return "{" + ", ".join(sorted(value)) + "}"
    if isinstance(value, list):
        return f"list[{_shape(value[0])}]" if value else "list[]"
    return type(value).__name__


async def main() -> int:
    file_env = _load_probe_env()

    def need(name: str) -> str:
        return os.environ.get(name) or file_env.get(name) or ""

    creds = skyslope.Creds(client_id=need("SKYSLOPE_CLIENT_ID"), client_secret=need("SKYSLOPE_CLIENT_SECRET"),
                           access_key=need("SKYSLOPE_ACCESS_KEY"), access_secret=need("SKYSLOPE_ACCESS_SECRET"),
                           base_url=(need("SKYSLOPE_BASE_URL") or settings.SKYSLOPE_BASE_URL).rstrip("/"))
    missing = [n for n, v in (("SKYSLOPE_CLIENT_ID", creds.client_id), ("SKYSLOPE_CLIENT_SECRET", creds.client_secret),
                              ("SKYSLOPE_ACCESS_KEY", creds.access_key), ("SKYSLOPE_ACCESS_SECRET", creds.access_secret)) if not v]
    if missing:
        print(f"Missing: {', '.join(missing)}. Nothing was called.", file=sys.stderr)
        return 2

    print(f"base url: {creds.base_url}\n")
    async with skyslope.client(creds) as api:
        usable_until = await api.login()
        print(f"login ok. Body casing and the SS prefix are right. Session usable until {usable_until.isoformat()}")

        reading = await api.replica_timestamp()
        print(f"replica-timestamp: keys {sorted(reading)}, lagSeconds {reading.get('lagSeconds')}, "
              f"replicaLastCommitTimeUtc {reading.get('replicaLastCommitTimeUtc')}")

        limit = skyslope._bucket(creds.client_id)
        resets_in = round(limit.reset_at - time.monotonic(), 1) if limit.reset_at else None
        print(f"rate limit after two calls: remaining {limit.remaining}, resets in {resets_in}s "
              "(None for both means no x-ratelimit headers came back)")

        for name, call in (("users", api.users), ("offices", api.offices), ("stages", api.stages)):
            try:
                rows = await call()
                print(f"{name}: {len(rows)} rows, each {_shape(rows[0]) if rows else 'n/a'}")
                if name == "users":
                    domains = collections.Counter(str(r.get("email") or "").rpartition("@")[2].lower() for r in rows)
                    suffixed = sum(str(r.get("email") or "").lower().endswith(("_deleted", "_deactivated")) for r in rows)
                    print(f"  email domains (which address does the API return?): {dict(domains.most_common(6))}")
                    print(f"  deactivated by suffix: {suffixed}")
            except skyslope.SkySlopeError as e:
                print(f"{name}: {e}")

        # Do the summary's counts respect the filters? The sync's short-read check depends on it.
        for label, params in (("everything", {"createdAfter": SINCE}),
                              ("modified in the last hour", {"modifiedAfter": skyslope.fmt_ts(
                                  skyslope.parse_ts(reading["replicaLastCommitTimeUtc"])
                                  - dt.timedelta(hours=1))})):
            summary, got, statuses, shapes = None, collections.Counter(), collections.Counter(), {}
            async for item in api.files(type="summary,listing,sale", **params):
                kind = item.get("objectType")
                if kind == "summary":
                    summary = item
                    continue
                got[kind] += 1
                statuses[(kind, item.get("status"), item.get("statusId"))] += 1
                shapes.setdefault(kind, item)
            print(f"\nexport, {label}: summary {summary and {k: v for k, v in summary.items() if k != 'createdOn'}}, "
                  f"received {dict(got)}")
            print("  counts match the summary:",
                  bool(summary) and got["sale"] == summary.get("numberOfSales") and got["listing"] == summary.get("numberOfListings"))
            if label == "everything":
                print("  every status string seen, for the normalization map:")
                for (kind, status, status_id), n in sorted(statuses.items(), key=str):
                    print(f"    {kind:8} {status!r:24} id={status_id}  x{n}")
                for kind, item in shapes.items():
                    print(f"  {kind} keys: {sorted(item)}")
                    for field in ("property", "commission", "stage"):
                        print(f"    {field}: {_shape(item.get(field))}")
                    for field in ("commissionSplits", "commissionBreakdowns", "agent", "coAgents"):
                        if field in item:
                            print(f"    {field}: {_shape(item.get(field))}")
                    dates = {k: item[k] for k in ("contractAcceptedDate", "contractAcceptanceDate", "modifiedOn", "createdOn") if k in item}
                    print(f"    date formats: {dates}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
