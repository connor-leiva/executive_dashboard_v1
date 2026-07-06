"""Does an ARIVE loan record who referred it (real-estate agent / brokerage / lead
source)? Profiles the referral fields + businessContacts/loanTeam arrays, focused on
UTAH loans (the ULRG-relevant ones), and hunts for 'Utah Life' or known ULRG agents.
Read-only. Creds from backend/.probe.env (ARIVE_*).

Run:  cd backend && ./.venv/Scripts/python.exe probe_arive_referral.py
"""
from __future__ import annotations
import os, sys, re, asyncio, json
from collections import Counter
import httpx

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://gwapiconnect.myarive.com/api"
SAMPLE = 60

REF_FIELDS = ["leadSource", "referralContactSourceName", "referralContactSourceEmail",
              "leadProvidedBy", "industryChannel", "orgUnitDisplayName", "loanCreatedFrom"]
ULRG_RE = re.compile(r"utah\s*life|\bulrg\b|utahlife|kaestle|muir|ed\s*fuller|liffick|"
                     r"devine|trish\s*thompson|jeff\s*anderson", re.I)


def load_creds():
    c = {}
    p = os.path.join(HERE, ".probe.env")
    if os.path.exists(p):
        for ln in open(p, encoding="utf-8"):
            s = ln.strip()
            if "=" in s and not s.startswith("#"):
                k, _, v = s.partition("="); c[k.strip()] = v.strip().strip('"').strip("'")
    g = lambda k: os.environ.get(k) or c.get(k)
    return g("ARIVE_CLIENT_ID"), g("ARIVE_SECRET"), g("ARIVE_API_KEY")


def g(obj, *keys):
    for k in keys:
        v = obj.get(k) if isinstance(obj, dict) else None
        if v not in (None, "", [], {}, "null"):
            return v
    return None


async def main():
    cid, secret, api_key = load_creds()
    if not all((cid, secret, api_key)):
        sys.exit("Missing ARIVE_* in backend/.probe.env")
    async with httpx.AsyncClient(timeout=60) as c:
        tr = await c.post(f"{BASE}/auth/access-token",
                          headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                          json={"clientId": cid, "secret": secret, "apiKey": api_key})
        token = tr.json().get("AccessToken")
        H = {"X-API-KEY": api_key, "Authorization": f"Bearer {token}", "Accept": "application/json"}
        lr = await c.get(f"{BASE}/loans", headers=H, params={"limit": 100, "orderBy": "updatedAt", "sort": "DESC"})
        rows = (lr.json().get("rows") or [])
        print(f"Listed {len(rows)} loans; pulling detail on {min(SAMPLE, len(rows))}…\n", flush=True)

        pop = Counter(); pop_ut = Counter()
        n = n_ut = 0
        bc_roles = Counter(); team_roles = Counter()
        ulrg_hits = []
        ut_samples = []
        for r in rows[:SAMPLE]:
            lid = r.get("ariveLoanId") or r.get("sysGUID")
            try:
                d = (await c.get(f"{BASE}/loans/{lid}", headers=H)).json()
            except Exception as e:  # noqa: BLE001
                continue
            if not isinstance(d, dict) or "ariveLoanId" not in d:
                continue
            n += 1
            state = (g(d.get("subjectProperty") or {}, "state") or "").upper()
            is_ut = state == "UT"
            n_ut += 1 if is_ut else 0
            for f in REF_FIELDS:
                if g(d, f) is not None:
                    pop[f] += 1
                    if is_ut:
                        pop_ut[f] += 1
            # businessContacts / loanTeam roles
            for bc in (d.get("businessContacts") or []):
                if isinstance(bc, dict):
                    bc_roles[str(g(bc, "contactType", "type", "role", "businessContactType") or "?")] += 1
            for tm in (d.get("loanTeam") or []):
                if isinstance(tm, dict):
                    team_roles[str(g(tm, "role", "type", "loanTeamMemberType") or "?")] += 1
            # ULRG mentions anywhere in the doc
            blob = json.dumps(d)
            for m in set(ULRG_RE.findall(blob)):
                ulrg_hits.append((lid, state, m))
            if is_ut and len(ut_samples) < 4:
                ut_samples.append({
                    "loan": lid, "leadSource": g(d, "leadSource"),
                    "referralName": g(d, "referralContactSourceName"),
                    "referralEmail": g(d, "referralContactSourceEmail"),
                    "leadProvidedBy": g(d, "leadProvidedBy"),
                    "orgUnit": g(d, "orgUnitDisplayName"),
                    "businessContacts": [{k: bc.get(k) for k in list(bc)[:6]} for bc in (d.get("businessContacts") or [])[:3]],
                    "loanTeam": [{k: tm.get(k) for k in list(tm)[:5]} for tm in (d.get("loanTeam") or [])[:4]],
                })

        print(f"── Detail pulled on {n} loans ({n_ut} in Utah).\n")
        print("── Referral-field population (all / Utah-only):")
        for f in REF_FIELDS:
            print(f"   {f:28} {pop[f]:3}/{n}   ·  UT {pop_ut[f]:3}/{n_ut}", flush=True)
        print(f"\n── businessContacts roles seen: {dict(bc_roles)}")
        print(f"── loanTeam roles seen:         {dict(team_roles)}")
        print(f"\n── 'Utah Life' / ULRG-agent mentions: {ulrg_hits[:20] if ulrg_hits else '(NONE found)'}")
        print("\n── Sample UTAH loans (referral fields + contacts):")
        for s in ut_samples:
            print(json.dumps(s, indent=2, default=str)[:1400], flush=True)


if __name__ == "__main__":
    asyncio.run(main())
