"""Map 693 QuickBooks accounts onto the standard chart in one sitting.

    Generate:  ./.venv/Scripts/python.exe coa_worksheet.py
    Apply:     ./.venv/Scripts/python.exe coa_worksheet.py --apply coa-mapping-worksheet.xlsx \
                                          --base-url https://... --email you@example.com

Two ideas do almost all the work here.

**Only accounts with activity matter.** 693 accounts exist across the five entities; 254 had
any activity in the sampled month. Phase 3's guard blocks a statement on an unmapped account
*with activity* — a dead account with a zero balance is harmless. So the worksheet sorts by
money, not alphabetically, and the biggest numbers are the first thing on the screen.

**A branch beats a list.** 56 branches of three or more accounts cover 311 of the 693, because
the people are accounts and they sit in named subtrees. One line on the Rules sheet answers
for a whole branch AND for everyone hired into it later. Answering those 56 first is the
difference between an afternoon and a week.

The worksheet generates OFFLINE from the Phase 0 discovery output, so it works before anything
is deployed. Applying goes over the API with your own login, so it needs no database
credentials, respects the same permissions as the screen, and writes the same audit trail.

QuickBooks is never written to.
"""
from __future__ import annotations

import argparse
import collections
import csv
import getpass
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

HERE = pathlib.Path(__file__).resolve().parent
DISCOVERY = HERE / "coa_discovery_out"
DEFAULT_OUT = HERE / "coa-mapping-worksheet.xlsx"

# Discovery CSV -> the `business.key` the API knows the entity by. The CSV filenames come from
# Phase 0; the keys come from the seeded businesses.
ENTITIES = [
    ("ulrg", "ULRG + Team", "coa-ulrg-team.csv"),
    ("springb", "Spring B", "coa-spring-b.csv"),
    ("sympli", "Sympli Mortgage", "coa-sympli-mortgage.csv"),
    ("becollective", "beCollective", "coa-becollective.csv"),
    ("the_forum", "The Forum", "coa-the-forum.csv"),      # note the underscore, that is the key

]

IGNORE = "IGNORE"
_MIN_BRANCH = 3           # below this, a rule is more machinery than the accounts are worth


# ── the account list ──────────────────────────────────────────────────────────────────────

class Account:
    """One QuickBooks account, in the shape `coa_map.suggest_for` expects. It duck-types the
    CoaMap model on purpose so the worksheet and the screen give the SAME suggestion — two
    suggesters that disagree would be worse than none."""
    standard_account_id = None
    is_ignored = False

    def __init__(self, entity_key, entity_name, qbo_id, name, fqn, qtype, activity, active):
        self.entity_key, self.entity_name = entity_key, entity_name
        self.qbo_account_id = qbo_id
        self.qbo_account_name = name
        self.qbo_account_fqn = fqn or name
        self.qbo_account_type = qtype
        self.activity = activity
        self.qbo_active = active

    @property
    def branch(self) -> str:
        i = self.qbo_account_fqn.rfind(":")
        return self.qbo_account_fqn[:i + 1] if i > 0 else ""


def load_discovery() -> list[Account]:
    out: list[Account] = []
    missing = []
    for key, label, fname in ENTITIES:
        path = DISCOVERY / fname
        if not path.exists():
            missing.append(fname)
            continue
        for r in csv.DictReader(path.open(encoding="utf-8")):
            try:
                activity = float(r.get("period_activity") or 0)
            except ValueError:
                activity = 0.0
            out.append(Account(key, label, r["qbo_account_id"], r["name"],
                               r.get("fully_qualified") or r["name"], r.get("type") or "",
                               activity, (r.get("active") or "True") == "True"))
    if missing:
        print(f"  ! no discovery CSV for: {', '.join(missing)} — run coa_discovery.py first")
    return out


def load_api(client, base_url) -> list[Account]:
    """The same list, live from the dashboard. Use this once coa_sync has run and the charts
    have moved on from the Phase 0 snapshot. Activity is absent until Phase 3 stores balances,
    so everything sorts as zero and the money-first ordering is lost — that is the trade."""
    ents = client.get(f"{base_url}/books/coa").json()["entities"]
    out = []
    for e in ents:
        if not e["counts"]["total"]:
            continue
        view = client.get(f"{base_url}/books/coa/map", params={"business_id": e["id"]}).json()
        for a in view["accounts"]:
            if a["standard_account_id"] or a["is_ignored"]:
                continue                                  # already decided
            out.append(Account(e["key"], e["name"], a["qbo_account_id"], a["name"],
                               a["fqn"], a["type"] or "", 0.0, a["qbo_active"]))
    return out


# ── proposals ─────────────────────────────────────────────────────────────────────────────

def _synthetic(branch: str, members: list[Account]) -> Account:
    """A branch, shaped as an account, so the ordinary suggester can read it. The leaf name of
    the branch is what carries the meaning — "…:Virtual Assistants:" is the whole signal."""
    leaf = branch.rstrip(":").split(":")[-1]
    qtype = collections.Counter(m.qbo_account_type for m in members).most_common(1)[0][0]
    return Account(members[0].entity_key, members[0].entity_name, "", leaf,
                   branch.rstrip(":"), qtype, sum(m.activity for m in members), True)


def propose_branches(accounts: list[Account], chart, suggest) -> list[dict]:
    """Branch rules worth writing, ranked so the ones that remove the most work come first."""
    by_branch: dict = collections.defaultdict(list)
    for a in accounts:
        if a.branch:
            by_branch[(a.entity_key, a.branch)].append(a)

    rows = []
    for (entity_key, branch), members in by_branch.items():
        if len(members) < _MIN_BRANCH:
            continue
        s = suggest(_synthetic(branch, members), chart)
        live = [m for m in members if m.activity]
        rows.append({
            "entity_key": entity_key, "entity": members[0].entity_name, "branch": branch,
            "accounts": len(members), "active": len(live),
            "activity": sum(abs(m.activity) for m in members),
            "examples": ", ".join(m.qbo_account_name for m in members[:3]),
            "code": s["code"] if s else "", "why": s["why"] if s else "no suggestion",
        })
    # Most accounts freed per decision first, then by money — that is the order to work in.
    rows.sort(key=lambda r: (-r["active"], -r["accounts"], -r["activity"]))
    return rows


def covering_branch(a: Account, branches: set) -> str:
    """The longest proposed branch that governs this account — the same longest-prefix-wins
    rule the server applies, so the worksheet cannot promise something the server won't do."""
    best = ""
    for b in branches:
        if a.qbo_account_fqn.lower().startswith(b.lower()) and len(b) > len(best):
            best = b
    return best


# ── the workbook ──────────────────────────────────────────────────────────────────────────

def build_workbook(accounts, branch_rows, chart, suggest, out_path):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.workbook.defined_name import DefinedName
    from openpyxl.worksheet.datavalidation import DataValidation

    HEAD = Font(bold=True, color="FFFFFF")
    HEAD_BG = PatternFill("solid", fgColor="002E2C")          # evergreen, same as the app
    ASK = PatternFill("solid", fgColor="FFF9D6")              # the cells you fill in
    MUTE = Font(color="89A989")
    MONEY = "#,##0.00"

    wb = Workbook()

    def sheet(title, headers, widths):
        ws = wb.create_sheet(title)
        ws.append(headers)
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
        for c in ws[1]:
            c.font, c.fill = HEAD, HEAD_BG
            c.alignment = Alignment(vertical="center")
        ws.freeze_panes = "A2"
        return ws

    # ── Chart: reference, and the source of every dropdown ──
    ch = sheet("Chart", ["Pick this", "Code", "Name", "Section", "What belongs here"],
               [46, 8, 40, 16, 90])
    ch.append([f"{IGNORE} · exclude this account from the statement", IGNORE, "", "",
               "Requires a reason. Use it for dead accounts, not for ones you are unsure about."])
    for a in chart:
        if not a.is_active:
            continue
        ch.append([f"{a.code} · {a.name}", a.code, a.name, a.section, a.definition or ""])
    last = ch.max_row
    wb.defined_names.add(DefinedName("StdAccounts", attr_text=f"Chart!$A$2:$A${last}"))

    def dropdown(ws, col, first, lastrow):
        dv = DataValidation(type="list", formula1="=StdAccounts", allow_blank=True,
                            showDropDown=False)
        dv.error = "Pick a value from the list, or leave it blank to skip."
        ws.add_data_validation(dv)
        dv.add(f"{col}{first}:{col}{lastrow}")

    display = {a.code: f"{a.code} · {a.name}" for a in chart}

    # ── 1 Rules ──
    rl = sheet("1 Rules",
               ["Entity", "Branch", "Accounts", "With activity", "Activity $",
                "For example", "MAP THIS BRANCH TO", "Scope", "Why (optional)",
                "Suggested because", "key"],
               [15, 62, 10, 13, 15, 46, 40, 18, 30, 30, 12])
    for r in branch_rows:
        rl.append([r["entity"], r["branch"], r["accounts"], r["active"], r["activity"],
                   r["examples"], display.get(r["code"], ""), "this entity", "", r["why"],
                   r["entity_key"]])
    for row in rl.iter_rows(min_row=2, max_row=rl.max_row):
        row[4].number_format = MONEY
        row[6].fill = ASK
        row[9].font = MUTE
    if rl.max_row > 1:
        dropdown(rl, "G", 2, rl.max_row)
        dv2 = DataValidation(type="list", formula1='"this entity,every entity"',
                             allow_blank=True, showDropDown=False)
        rl.add_data_validation(dv2)
        dv2.add(f"H2:H{rl.max_row}")
    rl.auto_filter.ref = f"A1:K{max(rl.max_row, 1)}"

    # ── 2 Accounts ──
    branches = {r["branch"] for r in branch_rows if r["code"]}
    ac = sheet("2 Accounts",
               ["Entity", "Account", "Type", "Activity $", "Live?", "Handled by branch",
                "MAP THIS ACCOUNT TO", "Reason (only if IGNORE)", "Suggested because",
                "QBO id", "key"],
               [15, 68, 20, 15, 8, 52, 40, 34, 30, 10, 12])
    # Money first. The $2.2M line and the $12 line are not the same decision, and an
    # alphabetical list hides that completely.
    for a in sorted(accounts, key=lambda x: (x.entity_name, -abs(x.activity),
                                             x.qbo_account_fqn.lower())):
        cover = covering_branch(a, branches)
        s = None if cover else suggest(a, chart)
        ac.append([a.entity_name, a.qbo_account_fqn, a.qbo_account_type, a.activity,
                   "yes" if a.activity else "", cover,
                   "" if cover else display.get(s["code"], "") if s else "",
                   "", "" if cover else (s["why"] if s else "no suggestion"),
                   a.qbo_account_id, a.entity_key])
    for row in ac.iter_rows(min_row=2, max_row=ac.max_row):
        row[3].number_format = MONEY
        row[8].font = MUTE
        if not row[5].value:                       # only ask where a branch is not answering
            row[6].fill = ASK
        else:
            row[5].font = MUTE
    if ac.max_row > 1:
        dropdown(ac, "G", 2, ac.max_row)
    ac.auto_filter.ref = f"A1:K{max(ac.max_row, 1)}"

    # ── Start here ──
    live = sum(1 for a in accounts if a.activity)
    covered = sum(1 for a in accounts if covering_branch(a, branches))
    st = wb["Sheet"]
    st.title = "Start here"
    st.column_dimensions["A"].width = 112
    lines = [
        ("Mapping the chart of accounts", True),
        ("", False),
        (f"{len(accounts)} QuickBooks accounts across {len({a.entity_key for a in accounts})} "
         f"entities. {live} of them had activity in the sampled month.", False),
        ("", False),
        ("Only accounts WITH ACTIVITY can block a statement. A dead account with a zero "
         "balance is harmless, so it can wait.", False),
        ("", False),
        ("1. Work sheet '1 Rules' first.", True),
        (f"   {len(branch_rows)} branches, covering {covered} accounts. One line here answers "
         f"for the whole branch AND for everyone hired into it later.", False),
        ("   The 'MAP THIS BRANCH TO' column is pre-filled with a guess. Change it, or clear "
         "it to skip that branch.", False),
        ("   Set Scope to 'every entity' when the same branch name means the same thing "
         "everywhere.", False),
        ("", False),
        ("2. Then sheet '2 Accounts'.", True),
        ("   Sorted by money, biggest first. Filter Live? = yes to see only what matters.", False),
        ("   Rows already handled by a branch are greyed and left blank — leave them alone. "
         "Filling one in overrides the branch for that one account, permanently.", False),
        ("   Choose IGNORE for an account that should not appear in the statement, and give a "
         "reason. The reason is the answer to 'why is this not in the P&L' six months from now.",
         False),
        ("", False),
        ("3. Save the file and send it back / run the apply command.", True),
        ("   coa_worksheet.py --apply <this file> --base-url <api> --email <you>", False),
        ("   It shows you what it would do first. Nothing is written until you add --write.", False),
        ("", False),
        ("Nothing here ever writes to QuickBooks. This only changes how Acumyn READS the books.",
         True),
    ]
    for text, bold in lines:
        st.append([text])
        if bold:
            st.cell(row=st.max_row, column=1).font = Font(bold=True)
        st.cell(row=st.max_row, column=1).alignment = Alignment(wrap_text=True, vertical="top")

    wb._sheets = [wb[t] for t in ("Start here", "1 Rules", "2 Accounts", "Chart")]
    wb.save(out_path)
    return {"accounts": len(accounts), "live": live, "branches": len(branch_rows),
            "covered": covered}


# ── applying ──────────────────────────────────────────────────────────────────────────────

_CODE = re.compile(r"^\s*([A-Za-z0-9]+)\s*(?:·|-|—|\|)?")


def parse_code(cell) -> str:
    """'8050 · Contract Labor, Named Contractors' -> '8050'. Tolerates a bare code too, since
    somebody will type one."""
    if cell is None:
        return ""
    m = _CODE.match(str(cell))
    return m.group(1).upper() if m else ""


def apply_workbook(path, client, base_url, write: bool):
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)

    ents = {e["key"]: e for e in client.get(f"{base_url}/books/coa").json()["entities"]}
    by_name = {e["name"]: e for e in ents.values()}

    def resolve(key, name):
        """`business.key` is stable; the display name is not. Match on the key and keep the
        name only as a fallback for a worksheet someone has retyped by hand."""
        return ents.get(str(key or "").strip()) or by_name.get(name)
    # One entity's chart is enough to read the standard accounts; they are tenant-wide.
    any_id = next((e["id"] for e in ents.values()), None)
    if any_id is None:
        raise SystemExit("No entities returned — is this the right API?")
    chart = client.get(f"{base_url}/books/coa/map",
                       params={"business_id": any_id}).json()["standard"]
    std_id = {a["code"]: a["id"] for a in chart}

    plan = {"rules": [], "maps": collections.defaultdict(list), "ignores": [], "problems": []}

    if "1 Rules" in wb.sheetnames:
        ws = wb["1 Rules"]
        for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            entity, branch, _, _, _, _, code_cell, scope = row[:8]
            note = row[8] if len(row) > 8 else None
            key = row[10] if len(row) > 10 else None
            code = parse_code(code_cell)
            if not code or not branch:
                continue
            if code == IGNORE:
                plan["problems"].append(f"1 Rules row {i}: IGNORE is not valid for a branch")
                continue
            if code not in std_id:
                plan["problems"].append(f"1 Rules row {i}: unknown standard code {code!r}")
                continue
            biz = resolve(key, entity)
            if biz is None:
                plan["problems"].append(f"1 Rules row {i}: unknown entity {entity!r}")
                continue
            everywhere = str(scope or "").strip().lower().startswith("every")
            plan["rules"].append({"pattern": str(branch), "standard_account_id": std_id[code],
                                  "business_id": None if everywhere else biz["id"],
                                  "note": (str(note) if note else None),
                                  "_label": f"{entity} · {branch} -> {code}"
                                            + (" (every entity)" if everywhere else "")})

    if "2 Accounts" in wb.sheetnames:
        ws = wb["2 Accounts"]
        for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
            entity, path_, _, _, _, covered, code_cell, reason = row[:8]
            qbo_id = row[9] if len(row) > 9 else None
            key = row[10] if len(row) > 10 else None
            code = parse_code(code_cell)
            if not code or not qbo_id:
                continue
            biz = resolve(key, entity)
            if biz is None:
                plan["problems"].append(f"2 Accounts row {i}: unknown entity {entity!r}")
                continue
            if code == IGNORE:
                if not (reason and str(reason).strip()):
                    plan["problems"].append(
                        f"2 Accounts row {i}: IGNORE needs a reason ({path_})")
                    continue
                plan["ignores"].append((biz["id"], str(qbo_id), str(reason).strip(), path_))
                continue
            if code not in std_id:
                plan["problems"].append(f"2 Accounts row {i}: unknown standard code {code!r}")
                continue
            if covered:
                # Allowed, but say so: this pins one account away from its branch forever.
                print(f"  · overriding branch for {path_} -> {code}")
            plan["maps"][(biz["id"], code)].append(str(qbo_id))

    print(f"\n{len(plan['rules'])} branch rules")
    for r in plan["rules"]:
        print(f"   {r['_label']}")
    total_mapped = sum(len(v) for v in plan["maps"].values())
    print(f"{total_mapped} accounts mapped by hand across {len(plan['maps'])} targets")
    print(f"{len(plan['ignores'])} accounts ignored")
    for p in plan["problems"][:12]:
        print(f"  ! {p}")
    if len(plan["problems"]) > 12:
        print(f"  ! ...and {len(plan['problems']) - 12} more")
    if plan["problems"]:
        print("  (rows with a problem are SKIPPED — nothing half-applies)")

    if not write:
        print("\nDry run. Nothing written. Add --write to apply.")
        return

    # Rules first: an account row is a deliberate override of its branch, so it has to land
    # after the branch it overrides.
    made = skipped = 0
    for r in plan["rules"]:
        body = {k: v for k, v in r.items() if not k.startswith("_")}
        resp = client.post(f"{base_url}/books/coa/rules", json=body)
        if resp.status_code == 200:
            made += 1
        elif resp.status_code == 400 and "already exists" in resp.text:
            skipped += 1
        else:
            print(f"  ! rule failed ({resp.status_code}): {r['_label']} — {resp.text[:120]}")
    print(f"rules: {made} created, {skipped} already there")

    n = 0
    for (business_id, code), ids in plan["maps"].items():
        for chunk in (ids[i:i + 200] for i in range(0, len(ids), 200)):
            resp = client.post(f"{base_url}/books/coa/map", json={
                "business_id": business_id, "qbo_account_ids": chunk,
                "standard_account_id": std_id[code]})
            if resp.status_code == 200:
                n += resp.json()["updated"]
            else:
                print(f"  ! map failed ({resp.status_code}) for {code}: {resp.text[:120]}")
    print(f"mapped: {n} accounts")

    m = 0
    by_reason: dict = collections.defaultdict(list)
    for business_id, qbo_id, reason, _ in plan["ignores"]:
        by_reason[(business_id, reason)].append(qbo_id)
    for (business_id, reason), ids in by_reason.items():
        resp = client.post(f"{base_url}/books/coa/ignore", json={
            "business_id": business_id, "qbo_account_ids": ids, "reason": reason})
        if resp.status_code == 200:
            m += resp.json()["updated"]
        else:
            print(f"  ! ignore failed ({resp.status_code}): {resp.text[:120]}")
    print(f"ignored: {m} accounts")
    print("\nOpen Books > Mapping to see it. Anything left unmapped is still listed there.")


# ── entry point ───────────────────────────────────────────────────────────────────────────

def _client(base_url, email, token):
    import httpx
    c = httpx.Client(timeout=60)
    if not token:
        if not email:
            raise SystemExit("--email or --token is required to reach the API")
        # Prompted locally and sent straight to the login endpoint; never stored or echoed.
        pw = getpass.getpass(f"Password for {email}: ")
        r = c.post(f"{base_url}/auth/login", json={"email": email, "password": pw})
        if r.status_code != 200:
            raise SystemExit(f"Login failed ({r.status_code}): {r.text[:200]}")
        token = r.json()["token"]
    c.headers["Authorization"] = f"Bearer {token}"
    return c


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", metavar="XLSX", help="read a filled-in worksheet and apply it")
    ap.add_argument("--write", action="store_true",
                    help="with --apply: actually write. Without it, nothing is changed.")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="where to write the worksheet")
    ap.add_argument("--source", choices=("discovery", "api"), default="discovery",
                    help="discovery (default, offline, carries activity) or api (live charts)")
    ap.add_argument("--base-url", default="https://api.acumyn.io/api/v1")
    ap.add_argument("--email")
    ap.add_argument("--token", help="a bearer token, instead of logging in")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                            # noqa: BLE001
        pass

    from app.services.coa import chart_rows
    from app.services.coa_map import suggest_for

    class _Std:                       # chart_rows gives dicts; suggest_for wants attributes
        def __init__(self, d, i):
            self.__dict__.update(d)
            self.id, self.is_active = d["code"], True

    if args.apply:
        client = _client(args.base_url, args.email, args.token)
        print(f"API: {args.base_url}")
        apply_workbook(args.apply, client, args.base_url, args.write)
        return

    chart = [_Std(d, i) for i, d in enumerate(chart_rows())]
    if args.source == "api":
        client = _client(args.base_url, args.email, args.token)
        accounts = load_api(client, args.base_url)
    else:
        accounts = load_discovery()
    if not accounts:
        raise SystemExit("No accounts found. Run coa_discovery.py, or use --source api.")

    branch_rows = propose_branches(accounts, chart, suggest_for)
    stat = build_workbook(accounts, branch_rows, chart, suggest_for, args.out)

    print(f"\nWrote {args.out}")
    print(f"  {stat['accounts']} accounts · {stat['live']} with activity")
    print(f"  {stat['branches']} branch rules proposed, covering {stat['covered']} accounts")
    print(f"  {stat['accounts'] - stat['covered']} accounts left to answer one at a time "
          f"({sum(1 for a in accounts if a.activity and not covering_branch(a, {r['branch'] for r in branch_rows if r['code']}))} of them live)")
    print("\nFill in the yellow columns, then:")
    print(f"  coa_worksheet.py --apply {args.out} --base-url <api> --email <you>")


if __name__ == "__main__":
    main()
