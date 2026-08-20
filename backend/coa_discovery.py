"""Phase 0 discovery — SPEC-coa-mapping-provenance §4. READ-ONLY.

For every business with a live QBO connection, pull the full chart of accounts and the
trial balance for the most recent CLOSED month, then report what the mapping effort is
actually up against. Emits one CSV per entity plus a summary markdown.

This is gated on purpose: the seed chart in §3 is a hypothesis. If the real charts diverge
more than expected, the mapping design may need a second nesting level, and building against
the assumption is how this becomes a rewrite.

Run:  cd backend && ./.venv/Scripts/python.exe coa_discovery.py [--out DIR]
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# `railway run` injects the SERVICE's env, whose DATABASE_URL is Railway's PRIVATE hostname
# (postgres.railway.internal). That only resolves inside Railway's own network, so from a
# laptop it fails in DNS before SQLAlchemy ever speaks Postgres. Swap in the public proxy URL
# when one is available. Must happen before app.config is imported — Settings reads env once.
def _resolve_db_url() -> str:
    override = os.environ.get("COA_DATABASE_URL", "").strip()
    if override:
        os.environ["DATABASE_URL"] = override
        return override
    url = os.environ.get("DATABASE_URL", "")
    if ".railway.internal" in url:
        pub = next((os.environ[k].strip() for k in
                    ("DATABASE_PUBLIC_URL", "POSTGRES_PUBLIC_URL", "PGPUBLICURL")
                    if os.environ.get(k, "").strip()), "")
        if pub:
            os.environ["DATABASE_URL"] = pub
            return pub
    return url


_DB_URL = _resolve_db_url()

from sqlalchemy import select                                    # noqa: E402
from app.db import SessionLocal                                  # noqa: E402
from app.models import Business, Integration                     # noqa: E402
from app.integrations import qbo                                 # noqa: E402
from app.services.sync import _valid_access_token                # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

# ── the standard chart, verbatim from SPEC §3.1/3.2 ───────────────────────────────────────
# Phase 0 only uses this to score map SUGGESTIONS. It is NOT seeded here — open questions 1
# and 2 must be answered first (§8 gate).
STANDARD = [
    # (code, name, bucket)
    ("5510", "Salaries, Staff", "salaries_wages"),
    ("5520", "Payroll Taxes, Staff", "salaries_wages"),
    ("5530", "Other Employee Benefits, Staff", "salaries_wages"),
    ("5540", "Payroll Processing Fees", "salaries_wages"),
    ("5550", "Contract Labor, Named Contractors", "salaries_wages"),
    ("5560", "Contract Labor, Virtual Assistants", "salaries_wages"),
    ("5570", "Contract Labor, Other", "salaries_wages"),
    ("6010", "Rent", "occupancy"),
    ("6020", "Utilities", "occupancy"),
    ("6030", "Repairs and Maintenance", "occupancy"),
    ("6040", "Commercial Property Taxes", "occupancy"),
    ("6090", "Other Occupancy", "occupancy"),
    ("7010", "Print and Direct Mail", "advertising"),
    ("7020", "Billboard", "advertising"),
    ("7030", "Radio", "advertising"),
    ("7040", "Online Marketing, SEO Organic and Paid", "advertising"),
    ("7050", "Online Marketing, Social", "advertising"),
    ("7060", "Online Marketing, Lead Buy", "advertising"),
    ("7061", "Internet Lead Sources", "advertising"),
    ("7070", "Sign Purchase", "advertising"),
    ("7080", "Sign Installation", "advertising"),
    ("7090", "Other Advertising", "advertising"),
    ("7510", "Travel", "sales_promotion"),
    ("7520", "Lodging", "sales_promotion"),
    ("7530", "Meals and Entertainment", "sales_promotion"),
    ("7540", "Gifts", "sales_promotion"),
    ("7550", "Coaching, Training and Education", "sales_promotion"),
    ("7560", "Conferences and Conventions", "sales_promotion"),
    ("7570", "Charitable Donations", "sales_promotion"),
    ("7590", "Other Sales Promotion", "sales_promotion"),
    ("8010", "Telephone", "office_expense"),
    ("8020", "Internet", "office_expense"),
    ("8030", "Mobile Phone", "office_expense"),
    ("8040", "Communication Equipment and Services", "office_expense"),
    ("8050", "Office Supplies", "office_expense"),
    ("8060", "Postage", "office_expense"),
    ("8070", "Office Equipment Rental and Repairs", "office_expense"),
    ("8080", "Computer Hardware", "office_expense"),
    ("8090", "Other Office Expense", "office_expense"),
    ("9010", "Dues and Subscriptions", "general_admin"),
    ("9020", "Computer Technology Subscriptions", "general_admin"),
    ("9030", "Vehicle Expense", "general_admin"),
    ("9040", "Professional Services, Accounting and Tax Prep", "general_admin"),
    ("9050", "Professional Services, Legal", "general_admin"),
    ("9060", "Professional Services, Other", "general_admin"),
    ("9070", "Personal Property Taxes", "general_admin"),
    ("9080", "Business Licenses and Taxes", "general_admin"),
    ("9090", "Business Use Tax", "general_admin"),
    ("9100", "E and O Insurance", "general_admin"),
    ("9110", "Other Insurance", "general_admin"),
    ("9120", "Bank Charges", "general_admin"),
    ("9130", "Bad Debt Expense", "general_admin"),
    ("9190", "Other General and Administrative", "general_admin"),
    ("1000", "Operating Cash", "balance_sheet"),
    ("1050", "Undeposited Funds", "balance_sheet"),
    ("1100", "Accounts Receivable", "balance_sheet"),
    ("1200", "Prepaid Expenses", "balance_sheet"),
    ("1300", "Due From Affiliates", "balance_sheet"),
    ("1500", "Fixed Assets at Cost", "balance_sheet"),
    ("1590", "Accumulated Depreciation", "balance_sheet"),
    ("2000", "Accounts Payable", "balance_sheet"),
    ("2100", "Credit Cards Payable", "balance_sheet"),
    ("2200", "Accrued Liabilities", "balance_sheet"),
    ("2300", "Deferred Revenue", "balance_sheet"),
    ("2400", "Due To Affiliates", "balance_sheet"),
    ("2500", "Loans Payable", "balance_sheet"),
    ("3000", "Member Equity", "balance_sheet"),
    ("3100", "Member Draws and Distributions", "balance_sheet"),
    ("3200", "Retained Earnings", "balance_sheet"),
    ("3900", "Opening Balance Equity", "balance_sheet"),
]

# Synonyms carrying real signal that QBO names use but the standard name doesn't spell out.
HINTS = {
    "5510": ("salary", "salaries", "wages", "payroll expense"),
    "5520": ("payroll tax", "employer tax", "fica", "futa", "suta"),
    "5530": ("benefit", "health insurance", "401k", "retirement"),
    "5540": ("gusto", "adp", "payroll fee", "payroll processing"),
    "5550": ("contract labor", "contractor", "1099"),
    "5560": ("virtual assistant", "offshore"),
    "6010": ("rent", "lease"),
    "6020": ("utilit", "electric", "water"),
    "7040": ("seo", "google ads", "adwords", "ppc"),
    "7050": ("facebook", "instagram", "social media", "meta ads"),
    "7060": ("lead buy", "zillow", "realtor com", "leads"),
    "7061": ("lead source", "internet lead"),
    "7530": ("meals", "entertainment", "dining", "restaurant"),
    "7550": ("coaching", "training", "education", "mastermind", "tuition"),
    "7560": ("conference", "convention", "summit", "event registration"),
    "8010": ("telephone", "phone line", "landline"),
    "8020": ("internet", "broadband", "wifi"),
    "8030": ("mobile", "cell phone", "cellular"),
    "8050": ("office supplies", "supplies"),
    "8080": ("computer hardware", "laptop", "equipment purchase"),
    "9010": ("dues", "subscription", "membership"),
    "9020": ("software", "saas", "technology subscription", "app subscription"),
    "9030": ("vehicle", "auto", "mileage", "fuel"),
    "9040": ("accounting", "bookkeeping", "tax prep", "cpa"),
    "9050": ("legal", "attorney", "lawyer"),
    "9100": ("e and o", "errors and omissions"),
    "9110": ("insurance",),
    "9120": ("bank charge", "bank fee", "merchant fee", "processing fee", "stripe fee"),
    "1000": ("checking", "operating cash", "bank", "savings"),
    "1050": ("undeposited",),
    "1100": ("accounts receivable",),
    "1300": ("due from", "receivable from affiliate", "intercompany receivable"),
    "2000": ("accounts payable",),
    "2100": ("credit card", "amex", "visa"),
    "2400": ("due to", "payable to affiliate", "intercompany payable"),
    "3200": ("retained earnings",),
    "3900": ("opening balance equity",),
}

# QBO AccountType -> the standard section it can map into. Narrows suggestions and is the
# basis for the "bulk map by type" first pass in §5.5.
TYPE_SECTION = {
    "Bank": "balance_sheet", "Other Current Asset": "balance_sheet",
    "Fixed Asset": "balance_sheet", "Accounts Receivable": "balance_sheet",
    "Other Asset": "balance_sheet", "Credit Card": "balance_sheet",
    "Accounts Payable": "balance_sheet", "Other Current Liability": "balance_sheet",
    "Long Term Liability": "balance_sheet", "Equity": "balance_sheet",
    "Expense": "opex", "Other Expense": "opex",
    "Cost of Goods Sold": "cogs", "Income": "revenue", "Other Income": "revenue",
}

# Accounting vocabulary. Two capitalized words is a weak signal on its own — "Bank Charges"
# and "Contract Labor" look exactly like "Mike Lee" to a shape test — so any account whose
# name contains a domain term is disqualified from the person heuristic.
_STOP = {
    "llc", "inc", "co", "corp", "corporation", "ltd", "the", "and", "of", "for",
    "services", "service", "group", "team", "real", "estate", "mortgage", "title",
    "expense", "expenses", "income", "revenue", "fee", "fees", "other", "general",
    "admin", "administrative", "office", "sales", "cost", "costs", "bank", "charge",
    "charges", "contract", "labor", "payroll", "tax", "taxes", "insurance", "rent",
    "utilities", "supplies", "travel", "meals", "advertising", "marketing", "software",
    "subscription", "subscriptions", "dues", "equipment", "repairs", "maintenance",
    "professional", "legal", "accounting", "bookkeeping", "interest", "depreciation",
    "amortization", "payable", "receivable", "asset", "assets", "liability", "liabilities",
    "equity", "capital", "draws", "distributions", "earnings", "retained", "opening",
    "balance", "cash", "checking", "savings", "credit", "card", "loan", "loans", "note",
    "notes", "deferred", "prepaid", "accrued", "undeposited", "funds", "commission",
    "commissions", "training", "education", "coaching", "donations", "charitable",
    "gifts", "postage", "telephone", "internet", "mobile", "phone", "vehicle", "auto",
    "conference", "conferences", "lodging", "signs", "sign", "print", "radio", "billboard",
    "misc", "miscellaneous", "reimbursement", "reimbursements", "transfer", "transfers",
    "clearing", "suspense", "escrow", "closing", "processing", "merchant", "bad", "debt",
    "benefits", "benefit", "wages", "salary", "salaries", "staff", "employee", "employees",
    "contractor", "contractors", "consulting", "consultant", "leads", "lead", "rental",
    "utility", "water", "electric", "hardware", "computer", "technology", "licenses",
    "license", "permits", "permit", "membership", "memberships", "entertainment",
}
# A surname may carry an apostrophe or hyphen — O'Brien, Smith-Jones.
_WORD = r"[A-Z][A-Za-z]*(?:['’-][A-Za-z]+)*"
_PERSON = re.compile(r"^%s(?:\s+[A-Z]\.?)?\s+%s$" % (_WORD, _WORD))


# Common US given names. A shape test alone cannot tell "Mike Lee" from "Amex Platinum" or
# "Facebook Ads" — brand names are unbounded, so a stoplist never catches up. Requiring real
# positive evidence (the first token is a given name) inverts that: it needs proof, not the
# absence of proof. Names outside this list fall to "possible" rather than being lost.
GIVEN_NAMES = set("""
james robert john michael david william richard joseph thomas christopher charles daniel
matthew anthony mark donald steven paul andrew joshua kenneth kevin brian george timothy
ronald jason edward jeffrey ryan jacob gary nicholas eric jonathan stephen larry justin
scott brandon benjamin samuel gregory alexander patrick frank raymond jack dennis jerry
tyler aaron jose adam nathan henry zachary douglas peter kyle noah ethan jeremy walter
christian keith roger terry austin sean gerald carl harold dylan arthur lawrence jordan
jesse bryan billy bruce gabriel joe logan alan juan albert willie elijah wayne randy
vincent mason roy ralph bobby russell bradley philip eugene louis caleb ian jonah cody
chad marcus travis shane derek trevor blake colin evan grant hunter jared jeff kurt lance
lucas miles nolan owen preston reid seth spencer tanner wesley wyatt cole drew garrett
mary patricia jennifer linda elizabeth barbara susan jessica sarah karen nancy lisa betty
margaret sandra ashley kimberly emily donna michelle carol amanda dorothy melissa deborah
stephanie rebecca sharon laura cynthia amy kathleen angela shirley anna brenda pamela emma
nicole helen samantha katherine christine debra rachel carolyn janet catherine maria heather
diane ruth julie olivia joyce virginia victoria kelly lauren christina joan evelyn judith
megan andrea cheryl hannah jacqueline martha gloria teresa ann sara madison frances kathryn
janice jean abigail alice julia judy sophia grace denise amber doris marilyn danielle beverly
isabella theresa diana natalie brittany charlotte marie kayla alexis lori tiffany kathy bonnie
crystal erin stacy dawn tracy monica jane holly leslie sherry allison lindsey courtney vanessa
audrey renee tara krista dana carrie erica april jill robin gail cindy anne jodi kristin
becky jenny mandy alyssa chelsea kaitlyn morgan taylor jamie casey riley avery peyton quinn
juana rosa ana luz carmen elena sofia lucia isabel diego carlos miguel luis jorge pedro
raul javier ricardo fernando alejandro sergio pablo hector oscar ruben ramon armando
ahmed ali omar hassan yusuf ibrahim aisha fatima layla zara amir karim nadia samir
wei ming li jun hui yan feng chen ying jing lei ling ping hong mei tao
priya raj amit anil sunil deepak neha pooja rahul vikram arjun ananya kiran
dmitri ivan sergei olga natasha katya boris mikhail anton pavel irina svetlana
kwame ama kofi abena chidi ngozi emeka amara zuri jabari imani malik jamal tyrone
dalila aimee jasmin desiree marisol yolanda alma esperanza rosalinda guadalupe
connor spring justin dori becky heather megan amy jessica
""".split())


def person_signal(name: str) -> str:
    """'likely' | 'possible' | 'no' — is this account a human posted as a sub-account?
    The July audit found ~146 of ~245 ULRG line items were individual people, the single
    biggest driver of chart size and the reason a per-account map is impractical.
    Two tiers because one fuzzy number would overstate certainty: 'likely' has a real given
    name behind it, 'possible' only has the shape."""
    n = re.sub(r"\s+", " ", (name or "").split(":")[-1].strip())
    if not n or any(ch.isdigit() for ch in n):
        return "no"
    words = [w for w in re.split(r"[\s,]+", n) if w]
    if not 2 <= len(words) <= 3:
        return "no"
    if any(w.lower().strip(".") in _STOP for w in words):
        return "no"
    if not _PERSON.match(n):
        return "no"
    return "likely" if words[0].lower() in GIVEN_NAMES else "possible"


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def suggest(acct: dict) -> tuple[str, str, float, str]:
    """(code, name, confidence 0-1, why) — a first-pass suggestion, never applied automatically."""
    leaf = norm((acct.get("Name") or "").split(":")[-1])
    full = norm(acct.get("FullyQualifiedName") or acct.get("Name") or "")
    padded = " " + full + " "
    atype = acct.get("AccountType") or ""
    section = TYPE_SECTION.get(atype)
    best = ("", "", 0.0, "no match")

    for code, sname, bucket in STANDARD:
        if section == "balance_sheet" and bucket != "balance_sheet":
            continue
        if section == "opex" and bucket == "balance_sheet":
            continue
        if section in ("revenue", "cogs"):
            continue                                   # deliberately unstandardized, OQ2
        score, why = 0.0, ""
        sn = norm(sname)
        if leaf == sn:
            score, why = 0.98, "exact name"
        else:
            for hint in HINTS.get(code, ()):
                if hint and hint in padded and 0.82 > score:
                    score, why = 0.82, "keyword '" + hint + "'"
            first = sn.split(",")[0].strip()
            if first and first in full and 0.70 > score:
                score, why = 0.70, "contains '" + first + "'"
            toks = {t for t in sn.replace(",", " ").split() if len(t) > 3}
            if toks:
                overlap = len(toks & set(full.split())) / len(toks)
                if overlap >= 0.5 and 0.45 + 0.25 * overlap > score:
                    score, why = 0.45 + 0.25 * overlap, "token overlap"
        if score > best[2]:
            best = (code, sname, round(score, 2), why)

    if best[2] == 0.0 and section == "opex":
        return ("9190", "Other General and Administrative", 0.15, "fallback: unmatched opex")
    return best


def depth(acct: dict) -> int:
    return len((acct.get("FullyQualifiedName") or acct.get("Name") or "").split(":"))


def _fernet_note() -> list:
    """Explain a Fernet InvalidToken without ever printing key material. Comparing against the
    checked-in default is safe (it is already in the repo) and separates the two real causes:
    the key was never injected, or a different service's key was."""
    from app.config import settings, Settings
    default = Settings.model_fields["FERNET_KEY"].default
    injected = bool(os.environ.get("FERNET_KEY", "").strip())
    out = []
    if not injected:
        out.append("FERNET_KEY was NOT injected, so the checked-in development default was used.")
        out.append("Run against the service that holds the real key (the one serving the QBO")
        out.append("OAuth callback), for example:  railway run --service <api> ...")
    elif settings.FERNET_KEY == default:
        out.append("FERNET_KEY was injected but equals the checked-in development default,")
        out.append("so that service never had a real key set.")
    else:
        out.append("A real FERNET_KEY was injected, but it is not the key these tokens were")
        out.append("encrypted with. Different Railway services can carry different keys —")
        out.append("run against the service that serves the QBO OAuth callback:")
        out.append("  railway run --service <api-service> <python> coa_discovery.py --out DIR")
        out.append("List services with:  railway service")
    return out


async def discover(out_dir: Path, dump: bool = False) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    today = dt.date.today()
    first_this = today.replace(day=1)              # most recent CLOSED month
    p_end = first_this - dt.timedelta(days=1)
    p_start = p_end.replace(day=1)
    print("Discovery period (most recent closed month): %s .. %s\n" % (p_start, p_end))

    summary: list[dict] = []
    async with SessionLocal() as s:
        integs = (await s.execute(select(Integration).where(
            Integration.provider == "qbo", Integration.realm_id.is_not(None)))).scalars().all()
        biz = {b.id: b for b in (await s.execute(select(Business))).scalars()}
        print("%d QBO connection(s)\n" % len(integs))
        fernet_failures = []

        for integ in integs:
            b = biz.get(integ.business_id)
            label = (b.name if b else None) or (b.key if b else None) or str(integ.realm_id)
            slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "entity"
            try:
                token = await _valid_access_token(s, integ)
                accts = await qbo.accounts(integ.realm_id, token)
                tb_raw = await qbo.trial_balance(integ.realm_id, token,
                                                 p_start.isoformat(), p_end.isoformat())
                tb = qbo.parse_trial_balance(tb_raw)
            except Exception as e:  # noqa: BLE001 — one dead connection must not stop discovery
                kind = type(e).__name__
                if kind == "InvalidToken":          # Fernet, not QuickBooks: wrong FERNET_KEY
                    fernet_failures.append(label)
                    detail = "token blob will not decrypt with the FERNET_KEY in this env"
                else:
                    detail = str(e)[:160]
                print("  !! %s: %s: %s" % (label, kind, detail[:120]))
                summary.append({"entity": label, "error": "%s: %s" % (kind, detail)})
                continue

            # trial-balance activity, keyed by account id where QBO gives one, else by name
            tb_by_id = {r["qbo_account_id"]: r for r in tb if r["qbo_account_id"]}
            tb_by_name = {norm(r["account"]): r for r in tb}

            def activity(a) -> float:
                r = tb_by_id.get(str(a.get("Id"))) or tb_by_name.get(
                    norm((a.get("FullyQualifiedName") or a.get("Name") or "").split(":")[-1]))
                return abs(r["amount"]) if r else 0.0

            by_type = Counter(a.get("AccountType") or "?" for a in accts)
            depths = Counter(depth(a) for a in accts)
            sig = [person_signal(a.get("Name") or "") for a in accts]
            people = [x for x in sig if x == "likely"]
            maybe = [x for x in sig if x == "possible"]
            active = [a for a in accts if activity(a) > 0]

            nums = defaultdict(list)
            for a in accts:
                if a.get("AcctNum"):
                    nums[str(a["AcctNum"]).strip()].append(a)
            dupes = {k: v for k, v in nums.items() if len(v) > 1}

            # a parent carrying its own balance while none of its children do
            children = defaultdict(list)
            for a in accts:
                fq = a.get("FullyQualifiedName") or a.get("Name") or ""
                if ":" in fq:
                    children[fq.rsplit(":", 1)[0]].append(a)
            orphan_parents = []
            for a in accts:
                fq = a.get("FullyQualifiedName") or a.get("Name") or ""
                kids = children.get(fq) or []
                if kids and activity(a) > 0 and not any(activity(k) > 0 for k in kids):
                    orphan_parents.append((fq, activity(a), len(kids)))

            rows = []
            for a in accts:
                code, sname, conf, why = suggest(a)
                rows.append({
                    "qbo_account_id": a.get("Id"), "acct_num": a.get("AcctNum") or "",
                    "name": a.get("Name"), "fully_qualified": a.get("FullyQualifiedName"),
                    "type": a.get("AccountType"), "subtype": a.get("AccountSubType"),
                    "active": a.get("Active"), "depth": depth(a),
                    "period_activity": round(activity(a), 2),
                    "person_signal": person_signal(a.get("Name") or ""),
                    "suggested_code": code, "suggested_name": sname,
                    "confidence": conf, "why": why,
                })
            rows.sort(key=lambda r: (-r["period_activity"], r["fully_qualified"] or ""))
            csv_path = out_dir / ("coa-%s.csv" % slug)
            with csv_path.open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["name"])
                w.writeheader()
                w.writerows(rows)

            hi = sum(1 for r in rows if r["confidence"] >= 0.8)
            mid = sum(1 for r in rows if 0.45 <= r["confidence"] < 0.8)
            lo = len(rows) - hi - mid
            tb_total = round(sum(r["amount"] for r in tb), 2)
            summary.append({
                "entity": label, "slug": slug, "realm": integ.realm_id, "accounts": len(accts),
                "active_accounts": len(active), "people": len(people), "maybe": len(maybe), "dupes": len(dupes),
                "dupe_keys": sorted(dupes)[:8],
                "max_depth": max(depths) if depths else 0,
                "deep": sum(n for d, n in depths.items() if d > 3),
                "orphan_parents": orphan_parents[:8], "n_orphan_parents": len(orphan_parents),
                "by_type": dict(by_type.most_common()), "depths": dict(sorted(depths.items())),
                "hi": hi, "mid": mid, "lo": lo, "tb_rows": len(tb), "tb_total": tb_total,
                "csv": csv_path.name,
            })
            print("  %-26s %4d accts | %3d active | %3d person (+%d maybe) | depth<=%d | "
                  "suggest %d/%d/%d hi/mid/lo"
                  % (label, len(accts), len(active), len(people), len(maybe),
                     max(depths) if depths else 0, hi, mid, lo))

    if fernet_failures:
        note = _fernet_note()
        print("")
        print("All %d connection(s) failed to DECRYPT, which is a key problem, not a QuickBooks"
              % len(fernet_failures))
        print("problem. The stored tokens are fine — prod reads them every sync.")
        for line in note:
            print("  " + line)

    # ── summary markdown ──
    md = out_dir / "coa-discovery-summary.md"
    L: list[str] = []
    L.append("# COA discovery — Phase 0\n")
    L.append("_SPEC-coa-mapping-provenance §4. Read-only. Generated %s._\n"
             % dt.date.today().isoformat())
    L.append("Period sampled: **%s .. %s** (most recent closed month)\n" % (p_start, p_end))
    ok = [x for x in summary if "error" not in x]
    err = [x for x in summary if "error" in x]
    tot_a = sum(x["accounts"] for x in ok)
    tot_p = sum(x["people"] for x in ok)
    tot_m = sum(x["maybe"] for x in ok)
    L.append("\n## Portfolio\n")
    L.append("- Entities with a live QBO connection: **%d**%s"
             % (len(ok), (" (%d failed)" % len(err)) if err else ""))
    L.append("- Total accounts across the portfolio: **%d**" % tot_a)
    L.append("- Accounts that are a named person: **%d**%s, plus **%d** more that fit the "
             "shape but whose first name is not in the reference list"
             % (tot_p, (" (%d%% of the chart)" % round(100 * tot_p / tot_a)) if tot_a else "",
                tot_m))
    L.append("- Entities needing nesting deeper than 3: **%d**\n"
             % sum(1 for x in ok if x["max_depth"] > 3))
    L.append("| Entity | Accounts | Active | Person | Maybe | Max depth | Dup nums | "
             "Suggest hi/mid/lo |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---|")
    for x in ok:
        L.append("| %s | %d | %d | %d | %d | %d | %d | %d/%d/%d |"
                 % (x["entity"], x["accounts"], x["active_accounts"], x["people"], x["maybe"],
                    x["max_depth"], x["dupes"], x["hi"], x["mid"], x["lo"]))
    if err:
        L.append("\n### Connections that failed\n")
        for x in err:
            L.append("- **%s** — %s" % (x["entity"], x["error"]))
    L.append("\n## Per entity\n")
    for x in ok:
        L.append("### %s\n" % x["entity"])
        L.append("- realm `%s` · CSV `%s`" % (x["realm"], x["csv"]))
        L.append("- %d accounts, %d with activity in the period"
                 % (x["accounts"], x["active_accounts"]))
        L.append("- Trial balance: %d rows, sums to **%s** (debit-positive; should be ~0)"
                 % (x["tb_rows"], format(x["tb_total"], ",.2f")))
        L.append("- Named-person accounts: **%d** (+%d possible)" % (x["people"], x["maybe"]))
        L.append("- Nesting depth: %s%s"
                 % (x["depths"], (" — **%d deeper than 3**" % x["deep"]) if x["deep"] else ""))
        if x["dupes"]:
            L.append("- **Duplicate account numbers: %d** → %s"
                     % (x["dupes"], ", ".join(x["dupe_keys"])))
        if x["n_orphan_parents"]:
            L.append("- **Parents carrying their own balance with no active child: %d**"
                     % x["n_orphan_parents"])
            for fq, amt, n in x["orphan_parents"]:
                L.append("    - `%s` — %s across %d children" % (fq, format(amt, ",.2f"), n))
        L.append("- Accounts by QBO type: %s" % x["by_type"])
        L.append("")
    L.append("\n## What this means for the build\n")
    L.append("Read this before answering open questions 1 and 2 (§8 gate). The seed chart in §3 "
             "is a hypothesis; these numbers are the test of it.\n")
    md.write_text("\n".join(L), encoding="utf-8")
    print("\nWrote %s" % md)
    for x in ok:
        print("       %s" % (out_dir / x["csv"]))

    if dump:
        # Inside the Railway container — the only place FERNET_KEY exists — the filesystem is
        # ephemeral, so stream the files too, framed so they can be split back apart.
        print("")
        print("===== BEGIN SUMMARY =====")
        print(md.read_text(encoding="utf-8"))
        print("===== END SUMMARY =====")
        for x in ok:
            print("===== BEGIN CSV %s =====" % x["csv"])
            print((out_dir / x["csv"]).read_text(encoding="utf-8"))
            print("===== END CSV %s =====" % x["csv"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="coa_discovery_out")
    ap.add_argument("--stdout", action="store_true",
                    help="also stream the report to stdout, for capturing over railway ssh")
    a = ap.parse_args()
    try:
        asyncio.run(discover(Path(a.out), dump=a.stdout))
    except OSError as e:
        # Inside Railway the private hostname is correct, so this can only be diagnosed by
        # failing, never up front — `railway run` injects the same RAILWAY_* vars locally.
        print("Could not reach the database: %s: %s" % (type(e).__name__, e))
        if ".railway.internal" in _DB_URL:
            print("")
            print("DATABASE_URL points at Railway private networking (%s), which resolves only"
                  % _DB_URL.split("@")[-1].split("/")[0])
            print("inside Railway. To run from this machine, take Postgres -> Variables ->")
            print("DATABASE_PUBLIC_URL and set it so it wins over the injected value:")
            print('  $env:COA_DATABASE_URL = "postgresql://...proxy.rlwy.net:PORT/railway"')
        raise SystemExit(2) from None
