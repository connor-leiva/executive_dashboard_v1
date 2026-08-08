"""TCPA consent probe — Spring B GHL (READ-ONLY, one-off forensic tool).

WHY THIS EXISTS
    Spring wants to SMS ~15k contacts for the Shift webinar. There is a prior
    TCPA suit, and no workflow was ever built to route opt-in from a form/survey
    into a contact custom field — so consent, if it exists, lives ONLY inside the
    Forms/Surveys *submission* records. This script answers two questions:

      1. DISCOVERY  — "Is there any field at the SUBMISSION level that tracks
                       consent?"  It dumps the full field landscape of every
                       form + survey submission and flags anything that reads
                       like consent (terms / conditions / agree / opt-in / sms /
                       'Spring B Terms', ...).

      2. EXTRACTION — For every submission that carries a consent-signal field,
                       who submitted it, when, from which form, and the exact
                       language they agreed to — aggregated per contact into a
                       clean evidence file your TCPA counsel can rule on.

    IMPORTANT: this tool *finds and organizes* consent evidence. It does NOT and
    cannot decide whether that evidence is legally sufficient TCPA prior express
    written consent for SMS marketing. That is a lawyer's call. Ship the CSVs to
    counsel; do not treat "has evidence" as "cleared to text."

CREDENTIALS
    Reads GHL_TOKEN + GHL_LOCATION_ID from backend/.probe.env (git-ignored;
    Claude never opens it). Same main Spring B location the Forum/Edge use.

READ-ONLY / SAFE
    - Only GET requests to the GHL API. Nothing is written back to GHL.
    - The token stays in .probe.env and is never printed.
    - All PII output goes to a gitignored dir. stdout shows aggregates only.

RUN — two sources
    API (fast recon, PARTIAL):  python consent_probe.py --days 800
        The GHL v2 /forms/submissions API under-returns vs the UI (this location:
        214 via API vs ~thousands in the UI Submissions center). Use only for recon:
        it reliably reveals the consent FIELD NAME + exact language, not the full list.
    CSV (authoritative, COMPLETE):  python consent_probe.py --csv forms_export.csv --csv surveys_export.csv
        Export from GHL UI (Sites → Forms → Submissions → Export; Surveys likewise),
        then feed the CSV(s) here. Same detection + outputs, full population.

OUTPUT  (./consent_audit_out/, gitignored)
    consent_audit_fields.json    — every distinct submission field + consent flag  (the DISCOVERY answer)
    consent_audit_raw_sample.json— full raw JSON of a few submissions, to eyeball
    consent_audit_detail.csv     — one row per consent-bearing submission (audit trail)
    consent_audit_contacts.csv   — one row per unique contact WITH consent evidence
    consent_audit_sendready.csv  — THE TEXTABLE LIST: consented AND not opted-out AND has a phone
    consent_audit_suppressed.csv — consented but removed (col suppress_reason: dnd_sms / dnd_all /
                                   no_phone / contact_not_found / dnd_same_phone_other_account)
    consent_audit_summary.json   — aggregate counts (also printed to stdout)

    Re-run just the DND pass on an existing consent list (no re-pull of submissions):
        python consent_probe.py --dnd-only
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
from collections import defaultdict

import httpx

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE, VERSION = "https://services.leadconnectorhq.com", "2021-07-28"
HERE = os.path.dirname(os.path.abspath(__file__))

# A field is a consent signal if its NAME/LABEL contains any of these (broad — field
# labels are curated, so false positives here are rare and all human-reviewable).
CONSENT_KEY_TERMS = [
    "consent", "terms", "condition", "agree", "opt-in", "opt in", "optin",
    "tcpa", "sms", "text message", "text msg", "texting", "authorize",
    "authorization", "permission to contact", "subscribe", "disclaimer",
]
# ...or if its VALUE contains one of these MULTI-WORD phrases (narrow on purpose —
# matching bare "consent" against free-text values falsely trips names like "NoConsent").
CONSENT_VALUE_PHRASES = [
    "spring b terms", "terms and conditions", "terms & conditions",
    "i agree to", "i consent to", "consent to receive", "agree to receive",
    "opt-in to", "opt in to", "permission to text", "permission to contact",
]
# Identity/meta keys are never "consent answers" — exclude from field scanning to cut noise.
SKIP_KEYS = {
    "id", "_id", "contactid", "contact_id", "locationid", "formid", "surveyid",
    "name", "firstname", "lastname", "email", "phone", "createdat", "createdon",
    "dateadded", "updatedat", "source", "page", "eventtype", "sessionid", "fingerprint",
    "__account", "__source_file",  # internal tags this tool adds; never "answers"
}
# A consent value is NOT affirmative when it stringifies to one of these.
NEGATIVE_VALUES = {
    "", "no", "false", "0", "off", "unchecked", "none", "n", "null",
    "declined", "no consent", "do not", "unsubscribe", "opt-out", "opt out",
}
_FIELD_NAME_KEYS = ("key", "id", "slug", "name", "label", "fieldKey")
_FIELD_VAL_KEYS = ("value", "answer", "fieldValue", "val")


# Three GHL accounts live in backend/.probe.env — pick per --accounts.
_ACCOUNTS = {
    "forum":   ("GHL_TOKEN", "GHL_LOCATION_ID"),        # set 1 — The Forum
    "springb": ("GHL_OLD_TOKEN", "GHL_OLD_LOCATION_ID"),  # set 2 — Spring B (the main DB)
    "bc":      ("GHL_BC_TOKEN", "GHL_BC_LOCATION_ID"),   # set 3 — beCollective
}
_ACCOUNT_ALIASES = {"old": "springb", "spring": "springb", "spring_b": "springb",
                    "becollective": "bc"}


def _read_probe_env() -> dict:
    conf, path = {}, os.path.join(HERE, ".probe.env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                conf[k.strip()] = v.strip().strip('"').strip("'")
    return conf


def load_conf(account: str = "forum") -> tuple[str | None, str | None]:
    """(token, location_id) for a named account from backend/.probe.env. Never printed."""
    account = _ACCOUNT_ALIASES.get(account.lower(), account.lower())
    tk, lk = _ACCOUNTS.get(account, _ACCOUNTS["forum"])
    conf = _read_probe_env()
    g = lambda k: conf.get(k) or os.environ.get(k)
    return g(tk), g(lk)


def _is_affirmative(value) -> bool:
    """Best-effort: a consent checkbox in GHL usually stores the agreed LABEL text as
    its value when checked, so any non-negative, non-empty value reads as affirmative.
    The raw value is preserved in the CSV so a human always makes the final call."""
    s = str(value).strip().lower()
    return bool(s) and s not in NEGATIVE_VALUES


def _semantic_fields(sub: dict) -> list[tuple[str, object]]:
    """Return (field_name, field_value) answer pairs from a submission, tolerating both
    GHL shapes: a flat dict of label→answer, AND a list of {key,value} objects (where a
    naive flatten would mislabel the real field name as the generic leaf 'key')."""
    out: list[tuple[str, object]] = []

    def walk(obj, name_hint: str = ""):
        if isinstance(obj, dict):
            # {key/name/..: X, value/answer/..: Y} → one semantic field X=Y (GHL customFields idiom)
            nk = next((k for k in _FIELD_NAME_KEYS if k in obj), None)
            vk = next((k for k in _FIELD_VAL_KEYS if k in obj), None)
            if nk and vk and not isinstance(obj[vk], (dict, list)):
                out.append((str(obj[nk]), obj[vk]))
                return
            for k, v in obj.items():
                walk(v, str(k))
        elif isinstance(obj, list):
            for v in obj:
                walk(v, name_hint)
        else:
            if name_hint and name_hint.lower() not in SKIP_KEYS:
                out.append((name_hint, obj))

    walk(sub)
    return out


def _field_is_consent(name: str, value) -> bool:
    n = str(name).lower()
    if n in SKIP_KEYS:
        return False
    if any(term in n for term in CONSENT_KEY_TERMS):
        return True
    v = str(value).lower()
    return any(phrase in v for phrase in CONSENT_VALUE_PHRASES)


def _first(d: dict, *keys) -> str:
    """First non-empty value among keys, matched case-insensitively (API dict OR CSV row)."""
    low = {str(k).strip().lower(): v for k, v in d.items()}
    for k in keys:
        v = low.get(k.lower())
        if v not in (None, ""):
            return str(v)
    return ""


def _contact_bits(sub: dict) -> dict:
    """Pull identifying fields off a submission — tolerant of API shape AND CSV-export headers."""
    name = _first(sub, "name", "full name", "full_name", "contact name")
    if not name:
        name = (f"{_first(sub, 'firstname', 'first name', 'first_name')} "
                f"{_first(sub, 'lastname', 'last name', 'last_name')}").strip()
    return {
        "contact_id": _first(sub, "contactId", "contact_id", "contact id"),
        "name": name,
        "email": _first(sub, "email", "email address", "email_address"),
        "phone": _first(sub, "phone", "phone number", "phone_number"),
        "created": _first(sub, "createdAt", "dateAdded", "createdOn", "created", "date",
                          "submitted on", "submission date", "date added", "date created"),
    }


def load_csv_submissions(paths: list[str]) -> list[dict]:
    """Read GHL UI Submissions exports (Forms → Submissions → Export, Surveys likewise).
    Each row becomes a submission dict {column header: cell value}. utf-8-sig strips Excel BOM."""
    rows: list[dict] = []
    for p in paths:
        with open(p, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                rows.append({(k or "").strip(): v for k, v in row.items() if k})
    return rows


def _get(client: httpx.Client, path: str, params: dict) -> dict | None:
    try:
        r = client.get(f"{BASE}/{path}", params=params)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        print(f"  ! {path} {params} -> HTTP {e.response.status_code}", flush=True)
        return None
    except Exception as e:  # noqa: BLE001
        print(f"  ! {path} {params} -> {type(e).__name__}: {e}", flush=True)
        return None


def fetch_field_labels(client: httpx.Client, location_id: str) -> dict[str, str]:
    """{fieldId | fieldKey | slug -> human label} so hash-keyed submission fields (e.g.
    'lWJruIyqLSNszL57szBF') resolve to their real names before consent detection runs."""
    labels: dict[str, str] = {}
    data = _get(client, f"locations/{location_id}/customFields", {})
    for fld in ((data or {}).get("customFields") or []):
        name = fld.get("name") or ""
        if not name:
            continue
        fk = fld.get("fieldKey") or ""
        for k in (fld.get("id"), fk, fk.split(".")[-1]):
            if k:
                labels[k] = name
    return labels


def list_definitions(client: httpx.Client, location_id: str, kind: str) -> dict[str, str]:
    """kind = 'forms' | 'surveys'  ->  {id: name}."""
    out: dict[str, str] = {}
    skip = 0
    while True:
        # surveys/ rejects `skip` with a 422 on some locations → retry without it.
        data = _get(client, f"{kind}/", {"locationId": location_id, "limit": 100, "skip": skip})
        if not data and skip == 0:
            data = _get(client, f"{kind}/", {"locationId": location_id, "limit": 100})
        if not data:
            break
        rows = data.get(kind) or data.get("data") or []
        if not rows:
            break
        for row in rows:
            fid = row.get("id") or row.get("_id")
            if fid:
                out[fid] = row.get("name") or row.get("formName") or "(unnamed)"
        total = (data.get("meta") or {}).get("total") or data.get("total")
        skip += len(rows)
        if not total or skip >= int(total) or len(rows) < 100:
            break
        time.sleep(0.15)
    return out


def _page_window(client: httpx.Client, location_id: str, kind: str,
                 start_at: str | None, end_at: str) -> list[dict]:
    """All submissions in one [start,end] window, following the page cursor."""
    out: list[dict] = []
    page = 1
    while True:
        params: dict = {"locationId": location_id, "limit": 100, "page": page, "endAt": end_at}
        if start_at:
            params["startAt"] = start_at
        data = _get(client, f"{kind}/submissions", params)
        if not data:
            break
        rows = data.get("submissions") or data.get("data") or []
        out.extend(rows)
        nxt = (data.get("meta") or {}).get("nextPage")
        if not rows or not nxt:
            break
        page = int(nxt)
        time.sleep(0.1)
    return out


def pull_submissions(client: httpx.Client, location_id: str, kind: str,
                     start_at: str | None, end_at: str, slice_days: int = 0) -> list[dict]:
    """kind = 'forms' | 'surveys'. When slice_days > 0, walk the window in small date
    slices and UNION (dedup by submission id) — this beats a per-query `total` cap: the
    API may return only N rows for a wide window but the full set for each narrow slice."""
    raw: list[dict] = []
    if slice_days and start_at:
        s = dt.date.fromisoformat(start_at)
        e = dt.date.fromisoformat(end_at)
        cur = s
        while cur <= e:
            nxt = min(cur + dt.timedelta(days=slice_days), e)
            got = _page_window(client, location_id, kind, cur.isoformat(), nxt.isoformat())
            raw.extend(got)
            print(f"  {kind} {cur}..{nxt}: +{len(got)} (raw {len(raw)})", flush=True)
            if nxt >= e:
                break
            cur = nxt + dt.timedelta(days=1)
    else:
        raw = _page_window(client, location_id, kind, start_at, end_at)

    seen, uniq = set(), []
    for r in raw:
        rid = r.get("id") or r.get("_id") or r.get("submissionId")
        key = rid if rid is not None else len(uniq)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    print(f"  {kind}: {len(uniq)} unique submissions (from {len(raw)} rows)", flush=True)
    return uniq


# ------------------------- DND / opt-out exclusion -------------------------
# Consent can be revoked. Before texting, subtract anyone SMS-blocked: global DND,
# SMS-channel DND (GHL auto-sets this when a contact replies STOP), or an opt-out tag.
OPT_OUT_TAG_HINTS = ("opt out", "opted out", "opt-out", "optout", "unsubscrib", "do not text",
                     "do not sms", "do not contact", "do not disturb", "dnc", "stop texting", "sms stop")


def _norm_phone(s: str) -> str:
    """Digits-only, last 10 — so numbers match across accounts despite formatting/+1."""
    d = "".join(ch for ch in str(s) if ch.isdigit())
    return d[-10:] if len(d) >= 10 else d


def sms_block_reason(contact: dict) -> str | None:
    """Why this contact must NOT be texted, or None if textable. SMS-specific."""
    if contact.get("dnd") is True:
        return "dnd_all"
    ds = contact.get("dndSettings") or {}
    sms = ds.get("SMS") or ds.get("sms") or {}
    if str(sms.get("status", "")).lower() == "active":  # GHL: active == DND ON
        return "dnd_sms"
    for t in (str(x).lower() for x in (contact.get("tags") or [])):
        if any(h in t for h in OPT_OUT_TAG_HINTS):
            return f"tag:{t[:40]}"
    return None


def pull_contacts(client: httpx.Client, location_id: str) -> list[dict]:
    """All contacts for the location, following the startAfter/startAfterId cursor."""
    out: list[dict] = []
    params: dict = {"locationId": location_id, "limit": 100}
    while True:
        data = _get(client, "contacts/", params)
        if not data:
            break
        rows = data.get("contacts") or []
        out.extend(rows)
        meta = data.get("meta") or {}
        said, sa = meta.get("startAfterId"), meta.get("startAfter")
        if not rows or not said:
            break
        params["startAfterId"] = said
        if sa:
            params["startAfter"] = sa
        if len(out) % 1000 < 100:
            print(f"    contacts… {len(out)}", flush=True)
        time.sleep(0.1)
    return out


def build_contact_index(accounts: list[str]) -> tuple[dict, set, dict]:
    """Pull every contact per account → index {contact_id: {phone_raw, phone, block, account}},
    the set of SMS-blocked phones (for cross-account matching), and per-account stats. The index
    is also how we ENRICH the consent list's phone — submissions often lack it, contacts have it."""
    index: dict = {}
    blocked_phones: set = set()
    stats: dict = {}
    for acct in accounts:
        token, loc = load_conf(acct)
        if not token or not loc:
            print(f"  ! DND account '{acct}': keys missing — skipping", flush=True)
            continue
        client = httpx.Client(timeout=60, headers={
            "Authorization": f"Bearer {token}", "Version": VERSION, "Accept": "application/json"})
        contacts = pull_contacts(client, loc)
        nb = 0
        for c in contacts:
            cid = c.get("id")
            reason = sms_block_reason(c)
            praw = c.get("phone") or ""
            ph = _norm_phone(praw)
            if cid:
                index[cid] = {"phone_raw": praw, "phone": ph, "block": reason, "account": acct}
            if reason:
                nb += 1
                if ph:
                    blocked_phones.add(ph)
        stats[acct] = {"contacts": len(contacts), "sms_blocked": nb}
        print(f"  DND '{acct}': {len(contacts)} contacts · {nb} SMS-blocked", flush=True)
    return index, blocked_phones, stats


def apply_suppression(consent_rows: list[dict], index: dict,
                      blocked_phones: set) -> tuple[list[dict], list[dict]]:
    """Split the consented list into send-ready vs suppressed (with reason), enriching the
    phone from the live contact record (submissions frequently omit it)."""
    send, supp = [], []
    for r in consent_rows:
        cid = r.get("contact_id") or ""
        info = index.get(cid)
        praw = (info["phone_raw"] if info and info["phone_raw"] else "") or r.get("phone") or ""
        ph = _norm_phone(praw)
        r2 = {**r, "phone": praw}
        if info is None:
            supp.append({**r2, "suppress_reason": "contact_not_found"})  # can't verify DND → exclude
        elif info["block"]:
            supp.append({**r2, "suppress_reason": info["block"]})
        elif not ph:
            supp.append({**r2, "suppress_reason": "no_phone"})
        elif ph in blocked_phones:
            supp.append({**r2, "suppress_reason": "dnd_same_phone_other_account"})
        else:
            send.append(r2)
    return send, supp


def _open_w(path: str):
    """Open for write; if the file is locked (e.g. open in Excel), fall back to <name>.new.<ext>."""
    try:
        return open(path, "w", newline="", encoding="utf-8")
    except PermissionError:
        stem, ext = os.path.splitext(path)
        alt = f"{stem}.new{ext}"
        print(f"  ! {os.path.basename(path)} is locked (open elsewhere?) → wrote {os.path.basename(alt)}",
              flush=True)
        return open(alt, "w", newline="", encoding="utf-8")


def _write_csv(path: str, rows: list[dict]) -> None:
    if not rows:
        return
    with _open_w(path) as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


# Lead / opt-in forms whose consent is typically a page FOOTER ("I agree to Spring B Terms")
# that GHL does NOT always store as a field → their submitters need a separate Tier-2 review.
LEAD_FORM_PATTERNS = ("opt-in", "optin", "opt in", "popup", "pop up", "free access", "guest",
    "giveaway", "application", "apply", "register", "registration", "rsvp", "lead", "waitlist",
    "download", "masterclass", "webinar", "lunch", "template", "diaries", "behind the scenes",
    "partnership", "free ", "vip")
# ...but never these internal/operational forms, even if a pattern loosely matches.
LEAD_FORM_EXCLUDE = ("commit", "committ", "hot seat", "end of day", "eod", "waiver", "contract",
    "feedback", "onboarding", "customer service", "intake", "reimburse", "expense",
    "logo submission", "sponsor", "profitability matrix", "dual membership", "member survey",
    "complete contact", "interaction")


def is_lead_form(name: str) -> bool:
    n = (name or "").lower()
    if any(x in n for x in LEAD_FORM_EXCLUDE):
        return False
    return any(p in n for p in LEAD_FORM_PATTERNS)


def run_dnd_pass(accounts: list[str], consent_rows: list[dict], out_dir: str,
                 index: dict | None = None, blocked_phones: set | None = None) -> tuple[dict, dict, set]:
    """Cross-reference the consent list against live DND/opt-out, write send-ready + suppressed.
    Returns (summary, index, blocked_phones) so a Tier-2 pass can reuse the same contact index."""
    print("\n--- DND / opt-out exclusion ---", flush=True)
    if index is None:
        index, blocked_phones, _ = build_contact_index(accounts)
    send, supp = apply_suppression(consent_rows, index, blocked_phones)
    _write_csv(os.path.join(out_dir, "consent_audit_sendready.csv"), send)
    _write_csv(os.path.join(out_dir, "consent_audit_suppressed.csv"), supp)
    reasons: dict = defaultdict(int)
    for r in supp:
        reasons[r["suppress_reason"].split(":")[0]] += 1
    summary = {"consented": len(consent_rows), "send_ready": len(send), "suppressed": len(supp),
               "suppressed_by_reason": dict(reasons)}
    return summary, index, blocked_phones


def write_tier2(lead_submitters: dict, consented_cids: set, index: dict, blocked_phones: set,
                out_dir: str) -> dict:
    """Tier-2 = lead/opt-in-form submitters with NO stored consent field (footer consent). Same
    phone-enrich + DND suppression; plus a per-form breakdown so counsel can drop any form."""
    rows = []
    forms_by_key: dict = {}   # (account, cid) -> set of form names (kept as a set, never re-split)
    for ls in lead_submitters.values():
        if ls["contact_id"] and ls["contact_id"] not in consented_cids:
            forms_by_key[(ls["account"], ls["contact_id"])] = ls["forms"]
            rows.append({"account": ls["account"], "contact_id": ls["contact_id"], "name": ls["name"],
                         "email": ls["email"], "phone": ls["phone"],
                         "forms": " | ".join(sorted(ls["forms"])),
                         "earliest": ls["earliest"], "latest": ls["latest"]})
    send, supp = apply_suppression(rows, index, blocked_phones)
    _write_csv(os.path.join(out_dir, "consent_audit_sendready_tier2.csv"), send)
    _write_csv(os.path.join(out_dir, "consent_audit_suppressed_tier2.csv"), supp)
    # per-form breakdown from the form SETS (not by re-splitting a joined string — form names
    # can themselves contain ' | '). A contact on multiple lead forms counts under each.
    send_keys = {(r["account"], r["contact_id"]) for r in send}
    by_form: dict = defaultdict(lambda: {"tier2_contacts": 0, "sendready_after_dnd": 0})
    for key, forms in forms_by_key.items():
        ready = key in send_keys
        for fm in forms:
            by_form[fm]["tier2_contacts"] += 1
            if ready:
                by_form[fm]["sendready_after_dnd"] += 1
    form_rows = [{"form": k, **v} for k, v in
                 sorted(by_form.items(), key=lambda x: -x[1]["tier2_contacts"])]
    _write_csv(os.path.join(out_dir, "consent_audit_tier2_by_form.csv"), form_rows)
    return {"tier2_candidates": len(rows), "tier2_send_ready": len(send),
            "tier2_suppressed": len(supp), "forms": len(form_rows)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=800,
                    help="look-back window in days (0 = all time). Default 800 ≈ this year + last.")
    ap.add_argument("--out", default=os.path.join(HERE, "consent_audit_out"))
    ap.add_argument("--sample", type=int, default=25, help="raw submissions to dump for eyeballing")
    ap.add_argument("--csv", action="append", metavar="PATH", help=(
        "process a GHL UI Submissions EXPORT instead of the API (repeatable). Use this for the "
        "COMPLETE picture — the /forms/submissions API under-returns vs the UI Submissions center."))
    ap.add_argument("--accounts", default="forum,springb",
                    help="comma list of GHL accounts to audit: forum | springb | bc. Default both main ones.")
    ap.add_argument("--slice-days", type=int, default=30, dest="slice_days",
                    help="date-slice width to beat the API's per-query cap (0 = one wide query).")
    ap.add_argument("--check-dnd", action=argparse.BooleanOptionalAction, default=True,
                    help="after building the consent list, subtract SMS-blocked/opted-out contacts.")
    ap.add_argument("--dnd-only", action="store_true",
                    help="skip submissions; just re-run DND suppression on the existing contacts.csv.")
    ap.add_argument("--all-field-samples", action="store_true",
                    help="store sample values for EVERY field (not just consent) — audits hidden consent boxes.")
    ap.add_argument("--tier2", action="store_true",
                    help="also emit a Tier-2 list: lead/opt-in-form submitters w/o a stored consent field.")
    args = ap.parse_args()

    # --- DND-only: refresh send-ready/suppressed from the existing consent list, then exit ---
    if args.dnd_only:
        path = os.path.join(args.out, "consent_audit_contacts.csv")
        if not os.path.exists(path):
            sys.exit(f"No consent list at {path} — run a normal audit first.")
        with open(path, newline="", encoding="utf-8-sig") as f:
            consent_rows = list(csv.DictReader(f))
        accounts = [a.strip() for a in args.accounts.split(",") if a.strip()]
        res, _, _ = run_dnd_pass(accounts, consent_rows, args.out)
        print("=" * 64)
        print(f"consented ......... {res['consented']}")
        print(f"SEND-READY ........ {res['send_ready']}")
        print(f"suppressed ........ {res['suppressed']}  {res['suppressed_by_reason']}")
        print(f"\noutputs → {os.path.abspath(args.out)}/  (sendready.csv · suppressed.csv)")
        print("=" * 64)
        return

    os.makedirs(args.out, exist_ok=True)
    today = dt.date.today()
    end_at = today.isoformat()
    start_at = None  # API mode narrows this; CSV mode spans whatever is in the file
    per_account: dict = {}

    if args.csv:
        # --- authoritative source: the GHL UI Submissions export(s) ---
        rows = load_csv_submissions(args.csv)
        all_subs = [("csv", {}, r) for r in rows]
        labels, form_names, survey_names = {}, {}, {}
        form_subs, survey_subs = rows, []
        print(f"CSV mode: {len(rows)} rows from {len(args.csv)} export file(s)\n", flush=True)
    else:
        # --- live API across one or more accounts, date-sliced to beat the per-query cap ---
        start_at = None if args.days <= 0 else (today - dt.timedelta(days=args.days)).isoformat()
        accounts = [a.strip() for a in args.accounts.split(",") if a.strip()]
        all_subs, labels, form_names, survey_names = [], {}, {}, {}
        form_subs, survey_subs, per_account = [], [], {}
        for acct in accounts:
            token, location_id = load_conf(acct)
            if not token or not location_id:
                print(f"  ! account '{acct}': keys missing in .probe.env — skipping", flush=True)
                continue
            print(f"\n=== account '{acct}' @ {location_id} | window {start_at or 'ALL'} → {end_at} "
                  f"| slice {args.slice_days}d ===", flush=True)
            client = httpx.Client(timeout=60, headers={
                "Authorization": f"Bearer {token}", "Version": VERSION, "Accept": "application/json"})
            labels.update(fetch_field_labels(client, location_id))
            fn = list_definitions(client, location_id, "forms")
            sn = list_definitions(client, location_id, "surveys")
            print(f"  forms: {len(fn)} | surveys: {len(sn)}", flush=True)
            fs = pull_submissions(client, location_id, "forms", start_at, end_at, args.slice_days)
            ss = pull_submissions(client, location_id, "surveys", start_at, end_at, args.slice_days)
            for x in fs + ss:
                x["__account"] = acct
            form_names.update(fn)
            survey_names.update(sn)
            form_subs += fs
            survey_subs += ss
            all_subs += [("form", fn, x) for x in fs] + [("survey", sn, x) for x in ss]
            per_account[acct] = {"location": location_id, "forms": len(fn), "surveys": len(sn),
                                 "form_subs": len(fs), "survey_subs": len(ss)}

    print(f"\nscanned {len(all_subs)} submissions "
          f"({len(form_subs)} form / {len(survey_subs)} survey)\n", flush=True)

    # --- DISCOVERY: the full field landscape, consent flag on each distinct key ---
    field_stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "consent_signal": False, "samples": []})
    # --- EXTRACTION: consent-bearing submissions + per-contact rollup ---
    detail_rows: list[dict] = []
    by_contact: dict[str, dict] = {}
    consented_cids: set = set()        # any contact with a STORED affirmative consent field
    lead_submitters: dict = {}         # Tier-2: submitters of lead/opt-in forms (footer, unstored)

    for src, names, sub in all_subs:
        who = _contact_bits(sub)
        acct = sub.get("__account", "csv")
        obj_name = (names.get(sub.get("formId") or sub.get("surveyId") or "", "")
                    or sub.get("formName") or _first(sub, "form name", "form", "survey name")
                    or "(unknown)")
        hit_here = []
        for name, value in _semantic_fields(sub):
            # resolve hash-id / fieldKey → human label so consent detection sees real names
            disp = labels.get(name) or labels.get(f"contact.{name}") or name
            st = field_stats[disp]
            st["count"] += 1
            is_consent = _field_is_consent(disp, value) or _field_is_consent(name, value)
            if is_consent:
                st["consent_signal"] = True
                hit_here.append((disp, value))
            # sample values: always for consent fields; for ALL fields under --all-field-samples
            # (so an opaque hash-keyed consent checkbox storing a bare "true" is auditable).
            if str(value).strip() and len(st["samples"]) < 3 and (is_consent or args.all_field_samples):
                st["samples"].append(str(value)[:200])
        for name, value in hit_here:
            aff = _is_affirmative(value)
            detail_rows.append({
                "account": acct, "source": src, "object": obj_name, "field": name,
                "value": str(value)[:500], "affirmative": aff,
                "contact_id": who["contact_id"], "name": who["name"],
                "email": who["email"], "phone": who["phone"],
                "submitted_at": who["created"], "submission_id": sub.get("id", ""),
            })
            if aff and who["contact_id"]:
                agg = by_contact.setdefault(f"{acct}:{who['contact_id']}", {
                    "account": acct, "contact_id": who["contact_id"], "name": who["name"],
                    "email": who["email"], "phone": who["phone"], "n_consents": 0, "objects": set(),
                    "earliest": who["created"], "latest": who["created"], "best_language": "",
                })
                agg["n_consents"] += 1
                agg["objects"].add(obj_name)
                agg["name"] = agg["name"] or who["name"]
                agg["email"] = agg["email"] or who["email"]
                agg["phone"] = agg["phone"] or who["phone"]
                if who["created"]:
                    agg["earliest"] = min(agg["earliest"] or who["created"], who["created"])
                    agg["latest"] = max(agg["latest"] or who["created"], who["created"])
                if len(str(value)) > len(agg["best_language"]):
                    agg["best_language"] = str(value)[:500]

        # --- Tier-2 tracking: stored-consent contacts, and lead/opt-in-form submitters ---
        cid = who["contact_id"]
        if cid and any(_is_affirmative(v) for _, v in hit_here):
            consented_cids.add(cid)
        if cid and is_lead_form(obj_name):
            ls = lead_submitters.setdefault(f"{acct}:{cid}", {
                "account": acct, "contact_id": cid, "name": who["name"], "email": who["email"],
                "phone": who["phone"], "forms": set(),
                "earliest": who["created"], "latest": who["created"],
            })
            ls["forms"].add(obj_name)
            ls["name"] = ls["name"] or who["name"]
            ls["email"] = ls["email"] or who["email"]
            ls["phone"] = ls["phone"] or who["phone"]
            if who["created"]:
                ls["earliest"] = min(ls["earliest"] or who["created"], who["created"])
                ls["latest"] = max(ls["latest"] or who["created"], who["created"])

    # --- write outputs (PII → gitignored dir; token/stdout stay clean) ---
    fields_out = sorted(
        ({"field": k, **v} for k, v in field_stats.items()),
        key=lambda d: (not d["consent_signal"], -d["count"]),
    )
    with _open_w(os.path.join(args.out, "consent_audit_fields.json")) as f:
        json.dump(fields_out, f, indent=2, default=str)
    with _open_w(os.path.join(args.out, "consent_audit_raw_sample.json")) as f:
        json.dump([x[2] for x in all_subs[: args.sample]], f, indent=2, default=str)

    _write_csv(os.path.join(args.out, "consent_audit_detail.csv"), detail_rows)
    contact_rows = [
        {**{k: v for k, v in a.items() if k != "objects"}, "objects": " | ".join(sorted(a["objects"]))}
        for a in sorted(by_contact.values(), key=lambda a: -a["n_consents"])
    ]
    _write_csv(os.path.join(args.out, "consent_audit_contacts.csv"), contact_rows)

    consent_fields = [d for d in fields_out if d["consent_signal"]]
    aff_by_account: dict = defaultdict(int)
    for r in contact_rows:
        aff_by_account[r.get("account", "csv")] += 1

    # --- DND / opt-out exclusion → send-ready list (Tier-1: explicit stored consent) ---
    dnd = None
    tier2 = None
    if args.check_dnd and not args.csv and contact_rows:
        accounts = [a.strip() for a in args.accounts.split(",") if a.strip()]
        dnd, index, blocked_phones = run_dnd_pass(accounts, contact_rows, args.out)
        # Tier-2: lead/opt-in-form submitters with NO stored consent field (footer consent)
        if args.tier2:
            tier2 = write_tier2(lead_submitters, consented_cids, index, blocked_phones, args.out)
            print(f"  Tier-2: {tier2['tier2_candidates']} lead-form submitters w/o stored consent "
                  f"→ {tier2['tier2_send_ready']} send-ready (DND-clean) across {tier2['forms']} forms",
                  flush=True)

    summary = {
        "window": {"start": start_at or "ALL", "end": end_at},
        "accounts": per_account,
        "forms": len(form_names), "surveys": len(survey_names),
        "submissions_scanned": len(all_subs),
        "form_submissions": len(form_subs), "survey_submissions": len(survey_subs),
        "consent_signal_fields": [{"field": d["field"], "count": d["count"], "samples": d["samples"]}
                                  for d in consent_fields],
        "consent_bearing_submissions": len(detail_rows),
        "unique_contacts_with_affirmative_consent": len(contact_rows),
        "affirmative_by_account": dict(aff_by_account),
        "dnd_exclusion": dnd,
        "tier2_lead_form": tier2,
    }
    with open(os.path.join(args.out, "consent_audit_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    # --- stdout: aggregates only, no PII ---
    print("=" * 64)
    print("DISCOVERY — submission-level consent fields found:")
    if consent_fields:
        for d in consent_fields:
            samp = (d["samples"][0][:70] + "…") if d["samples"] else ""
            print(f"   • {d['field']!r}  (seen {d['count']}x)   e.g. {samp!r}")
    else:
        print("   (none — NO field in any form/survey submission looks like consent)")
    print("-" * 64)
    for acct, pa in per_account.items():
        print(f"account {acct:<8} @ {pa['location']}: "
              f"{pa['form_subs']}+{pa['survey_subs']} subs · {aff_by_account.get(acct, 0)} affirmative contacts")
    print(f"submissions scanned .............. {len(all_subs)} "
          f"({len(form_subs)} form / {len(survey_subs)} survey)")
    print(f"consent-bearing submissions ...... {len(detail_rows)}")
    print(f"UNIQUE contacts w/ affirmative ... {len(contact_rows)}")
    if dnd:
        print("-" * 64)
        print(f"TIER-1 (explicit stored consent):  {dnd['consented']} "
              f"→ SEND-READY {dnd['send_ready']} | suppressed {dnd['suppressed']} {dnd['suppressed_by_reason']}")
    if tier2:
        print(f"TIER-2 (lead-form footer, unstored): {tier2['tier2_candidates']} "
              f"→ SEND-READY {tier2['tier2_send_ready']} across {tier2['forms']} forms (review by_form.csv)")
    print(f"\noutputs → {os.path.abspath(args.out)}/")
    print("   fields.json · detail.csv · contacts.csv (all consented)")
    if dnd:
        print("   sendready.csv (TIER-1 TEXTABLE) · suppressed.csv (removed + reason)")
    if tier2:
        print("   sendready_tier2.csv (TIER-2 textable) · tier2_by_form.csv (per-form — counsel drops forms)")
    print("\nNOTE: 'has evidence' ≠ 'legally cleared to text'. Ship the CSVs to TCPA counsel.")
    print("=" * 64)


if __name__ == "__main__":
    main()
