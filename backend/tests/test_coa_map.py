"""Chart of accounts mapping — Phase 2 (SPEC-coa-mapping-provenance section 5).

What these are actually defending:

- Identity is the QBO account id. ULRG carries the number 69000 twice, in the NAME. A
  name-keyed map merges those two accounts and nobody notices until the totals are wrong.
- A mapping decision survives a QuickBooks rename, a re-sync, and a rule edit. Somebody will
  spend hours on that map.
- The rule engine is the only reason the unmapped guard is livable. People are accounts;
  a new VA arrives every month.
"""
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.seed import seed
from app.db import SessionLocal
from app.models import (AuditLog, Business, CoaMap, Integration, StandardAccount,
                        Tenant, User)
from app.security import hash_pw, make_token
from app.services import coa_map
from app.services.coa import seed_standard_chart

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()
    # The demo seed does not lay down the standard chart; migration 0038 does that in prod.
    # Everything here maps INTO that chart, so seed it the same way 0038 does.
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        await seed_standard_chart(s, t.id)


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token):
    return {"Authorization": f"Bearer {token}"}


async def _ids():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        biz = {b.key: b.id for b in (await s.execute(
            select(Business).where(Business.tenant_id == t.id))).scalars()}
        return t.id, biz


async def _owner_token():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


async def _std(s, tenant_id, code) -> StandardAccount:
    return (await s.execute(select(StandardAccount).where(
        StandardAccount.tenant_id == tenant_id, StandardAccount.code == code))).scalar_one()


# QBO Account objects as the API actually returns them, taken from the shapes Phase 0 saw on
# ULRG: the account number lives in the NAME, nesting runs deep, and 69000 appears twice.
ACCOUNTS = [
    {"Id": "289", "Name": "41200 Sales Income", "AccountType": "Income",
     "FullyQualifiedName": "41000 Gross Commission Income:41200 Sales Income", "Active": True},
    {"Id": "301", "Name": "Ana Ruiz", "AccountType": "Expense", "Active": True,
     "FullyQualifiedName": "61300 Contract Labor:Virtual Assistants:Ana Ruiz"},
    {"Id": "302", "Name": "Diego Marin", "AccountType": "Expense", "Active": True,
     "FullyQualifiedName": "61300 Contract Labor:Virtual Assistants:Diego Marin"},
    {"Id": "310", "Name": "Kofi Mensah", "AccountType": "Expense", "Active": True,
     "FullyQualifiedName": "61300 Contract Labor:Mentor Bonuses:Kofi Mensah"},
    {"Id": "400", "Name": "69000 Other Expense", "AccountType": "Other Expense",
     "FullyQualifiedName": "69000 Other Expense", "Active": True},
    {"Id": "401", "Name": "69000 Insurance", "AccountType": "Expense",
     "FullyQualifiedName": "69000 Insurance", "Active": True},
    {"Id": "500", "Name": "Rent", "AccountType": "Expense",
     "FullyQualifiedName": "63000 Occupancy:Rent", "Active": True},
    {"Id": "900", "Name": "Old Payroll Clearing", "AccountType": "Other Current Liability",
     "FullyQualifiedName": "Old Payroll Clearing", "Active": False},
]


async def _fake_sync(monkeypatch, tenant_id, business_id, accounts=ACCOUNTS):
    """Run sync_coa_accounts against a canned QBO chart. QuickBooks is never written to and,
    here, never even read from — the seam is qbo.accounts."""
    monkeypatch.setattr(coa_map, "_valid_access_token",
                        lambda s, integ: _immediate("token"))
    # Signature mirrors the real one, include_inactive and all: a stub that quietly accepts
    # fewer arguments than production passes is a test that stops testing.
    monkeypatch.setattr(coa_map.qbo, "accounts",
                        lambda realm, token, include_inactive=False: _immediate(accounts))
    async with SessionLocal() as s:
        integ = (await s.execute(select(Integration).where(
            Integration.tenant_id == tenant_id, Integration.provider == "qbo",
            Integration.business_id == business_id))).scalars().first()
        if integ is None:
            integ = Integration(tenant_id=tenant_id, provider="qbo", business_id=business_id,
                                status="connected", realm_id="9130347534128706")
            s.add(integ)
            await s.commit()
        return await coa_map.sync_coa_accounts(s, tenant_id, integ)


async def _immediate(value):
    return value


async def _rows(tenant_id, business_id):
    async with SessionLocal() as s:
        return {r.qbo_account_id: r for r in (await s.execute(select(CoaMap).where(
            CoaMap.tenant_id == tenant_id, CoaMap.business_id == business_id))).scalars()}


# ── matching ──────────────────────────────────────────────────────────────────────────────

class _R:
    """A rule stand-in — match_rule is pure and does not need the database."""
    def __init__(self, pattern, sid, active=True, match_type="prefix"):
        self.pattern, self.standard_account_id = pattern, sid
        self.is_active, self.match_type = active, match_type


def test_longest_prefix_wins():
    """The whole reason there is no priority column."""
    rules = [_R("61300 Contract Labor:", "generic"),
             _R("61300 Contract Labor:Virtual Assistants:", "va")]
    match = coa_map.match_rule("61300 Contract Labor:Virtual Assistants:Ana Ruiz", rules)
    assert match is not None and match.standard_account_id == "va"
    assert coa_map.match_rule(
        "61300 Contract Labor:Kofi Mensah", rules).standard_account_id == "generic"


def test_match_is_case_and_whitespace_insensitive():
    rules = [_R("contract labor:", "cl")]
    assert coa_map.match_rule("Contract  Labor:Ana Ruiz", rules).standard_account_id == "cl"
    assert coa_map.match_rule("CONTRACT LABOR:Ana", rules).standard_account_id == "cl"


def test_inactive_rule_never_matches():
    assert coa_map.match_rule("Contract Labor:Ana", [_R("contract labor:", "cl", active=False)]) is None


def test_no_rules_no_match():
    assert coa_map.match_rule("Anything", []) is None
    assert coa_map.match_rule(None, [_R("a", "x")]) is None


# ── sync ──────────────────────────────────────────────────────────────────────────────────

async def test_sync_creates_one_row_per_account(monkeypatch):
    tenant_id, biz = await _ids()
    n = await _fake_sync(monkeypatch, tenant_id, biz["ulrg"])
    assert n == len(ACCOUNTS)
    rows = await _rows(tenant_id, biz["ulrg"])
    assert set(rows) == {a["Id"] for a in ACCOUNTS}
    assert all(r.standard_account_id is None for r in rows.values()), "arrives unmapped"
    assert rows["900"].qbo_active is False, "QBO's own Active flag is carried"
    assert rows["301"].qbo_account_fqn.endswith("Ana Ruiz")


async def test_duplicate_account_number_stays_two_accounts(monkeypatch):
    """ULRG holds 69000 twice, in the name. A name-keyed map silently merges them."""
    tenant_id, biz = await _ids()
    await _fake_sync(monkeypatch, tenant_id, biz["ulrg"])
    rows = await _rows(tenant_id, biz["ulrg"])
    assert rows["400"].qbo_account_name.startswith("69000")
    assert rows["401"].qbo_account_name.startswith("69000")
    assert rows["400"].id != rows["401"].id


async def test_resync_is_idempotent_and_preserves_a_hand_mapping(monkeypatch):
    tenant_id, biz = await _ids()
    await _fake_sync(monkeypatch, tenant_id, biz["ulrg"])
    async with SessionLocal() as s:
        rent = await _std(s, tenant_id, "7010")
        await coa_map.set_mapping(s, tenant_id, None, biz["ulrg"], ["500"], rent.id)

    # QuickBooks renames the account; the second sync must not undo the decision.
    renamed = [dict(a) for a in ACCOUNTS]
    renamed[6] = {**renamed[6], "Name": "Office Rent",
                  "FullyQualifiedName": "63000 Occupancy:Office Rent"}
    n = await _fake_sync(monkeypatch, tenant_id, biz["ulrg"], renamed)
    assert n == len(ACCOUNTS), "no duplicate rows on re-sync"
    rows = await _rows(tenant_id, biz["ulrg"])
    assert rows["500"].qbo_account_name == "Office Rent", "source columns refresh"
    assert rows["500"].mapped_via == "manual"
    async with SessionLocal() as s:
        assert rows["500"].standard_account_id == (await _std(s, tenant_id, "7010")).id


async def test_sync_writes_an_audit_row(monkeypatch):
    tenant_id, biz = await _ids()
    await _fake_sync(monkeypatch, tenant_id, biz["ulrg"])
    async with SessionLocal() as s:
        actions = (await s.execute(select(AuditLog.action).where(
            AuditLog.tenant_id == tenant_id))).scalars().all()
    assert "coa.sync_accounts" in actions


# ── rules ─────────────────────────────────────────────────────────────────────────────────

async def test_rule_maps_a_whole_subtree_and_a_later_hire(monkeypatch):
    """The reason coa_map_rule exists: without it every new VA trips the unmapped guard."""
    tenant_id, biz = await _ids()
    await _fake_sync(monkeypatch, tenant_id, biz["springb"])
    async with SessionLocal() as s:
        va = await _std(s, tenant_id, "8060")
        await coa_map.create_rule(s, tenant_id, None,
                                  "61300 Contract Labor:Virtual Assistants:", va.id,
                                  business_id=biz["springb"], note="Phase 0: VAs live here")
    rows = await _rows(tenant_id, biz["springb"])
    assert rows["301"].mapped_via == "rule" and rows["302"].mapped_via == "rule"
    assert rows["310"].standard_account_id is None, "Mentor Bonuses is a different subtree"

    # A new VA hired next month arrives already mapped.
    hire = ACCOUNTS + [{"Id": "303", "Name": "Priya Nair", "AccountType": "Expense",
                        "Active": True,
                        "FullyQualifiedName": "61300 Contract Labor:Virtual Assistants:Priya Nair"}]
    await _fake_sync(monkeypatch, tenant_id, biz["springb"], hire)
    rows = await _rows(tenant_id, biz["springb"])
    async with SessionLocal() as s:
        assert rows["303"].standard_account_id == (await _std(s, tenant_id, "8060")).id
    assert rows["303"].mapped_via == "rule"


async def test_manual_mapping_outranks_a_rule(monkeypatch):
    tenant_id, biz = await _ids()
    await _fake_sync(monkeypatch, tenant_id, biz["sympli"])
    async with SessionLocal() as s:
        named = await _std(s, tenant_id, "8050")
        va = await _std(s, tenant_id, "8060")
        await coa_map.set_mapping(s, tenant_id, None, biz["sympli"], ["301"], named.id)
        await coa_map.create_rule(s, tenant_id, None,
                                  "61300 Contract Labor:Virtual Assistants:", va.id,
                                  business_id=biz["sympli"])
    rows = await _rows(tenant_id, biz["sympli"])
    async with SessionLocal() as s:
        assert rows["301"].standard_account_id == (await _std(s, tenant_id, "8050")).id
        assert rows["302"].standard_account_id == (await _std(s, tenant_id, "8060")).id
    assert rows["301"].mapped_via == "manual"


async def test_deactivating_a_rule_returns_its_accounts_to_unmapped(monkeypatch):
    """A stale answer is worse than an empty one — the guard has to be able to see them."""
    tenant_id, biz = await _ids()
    await _fake_sync(monkeypatch, tenant_id, biz["ulrg"])
    async with SessionLocal() as s:
        va = await _std(s, tenant_id, "8060")
        rule = await coa_map.create_rule(s, tenant_id, None,
                                         "61300 Contract Labor:Virtual Assistants:", va.id,
                                         business_id=biz["ulrg"])
        rid = rule.id
    assert (await _rows(tenant_id, biz["ulrg"]))["302"].standard_account_id is not None
    async with SessionLocal() as s:
        await coa_map.update_rule(s, tenant_id, None, rid, is_active=False)
    row = (await _rows(tenant_id, biz["ulrg"]))["302"]
    assert row.standard_account_id is None and row.mapped_via is None and row.rule_id is None


async def test_deleting_a_rule_clears_it_too(monkeypatch):
    tenant_id, biz = await _ids()
    await _fake_sync(monkeypatch, tenant_id, biz["ulrg"])
    async with SessionLocal() as s:
        # 500 carries a hand mapping from an earlier test; release it so this is a clean
        # test of the rule and not of the manual override (which has its own test).
        await coa_map.set_mapping(s, tenant_id, None, biz["ulrg"], ["500"], None)
        va = await _std(s, tenant_id, "8060")
        rule = await coa_map.create_rule(s, tenant_id, None, "63000 Occupancy:", va.id,
                                         business_id=biz["ulrg"])
        rid = rule.id
    assert (await _rows(tenant_id, biz["ulrg"]))["500"].mapped_via == "rule"
    async with SessionLocal() as s:
        await coa_map.delete_rule(s, tenant_id, None, rid)
    assert (await _rows(tenant_id, biz["ulrg"]))["500"].standard_account_id is None


async def test_tenant_wide_rule_reaches_every_entity(monkeypatch):
    """"Contract Labor:" is a real subtree on Spring B AND The Forum."""
    tenant_id, biz = await _ids()
    await _fake_sync(monkeypatch, tenant_id, biz["ulrg"])
    await _fake_sync(monkeypatch, tenant_id, biz["springb"])
    async with SessionLocal() as s:
        other = await _std(s, tenant_id, "8070")
        await coa_map.create_rule(s, tenant_id, None, "61300 Contract Labor:Mentor Bonuses:",
                                  other.id)      # business_id omitted -> tenant-wide
    for key in ("ulrg", "springb"):
        assert (await _rows(tenant_id, biz[key]))["310"].mapped_via == "rule"


async def test_duplicate_rule_pattern_is_refused():
    tenant_id, biz = await _ids()
    async with SessionLocal() as s:
        acct = await _std(s, tenant_id, "8510")
        await coa_map.create_rule(s, tenant_id, None, "Dues And Subs:", acct.id,
                                  business_id=biz["ulrg"])
        with pytest.raises(ValueError):
            await coa_map.create_rule(s, tenant_id, None, "dues and subs:", acct.id,
                                      business_id=biz["ulrg"])


async def test_rule_pattern_must_be_meaningful():
    tenant_id, biz = await _ids()
    async with SessionLocal() as s:
        acct = await _std(s, tenant_id, "8510")
        with pytest.raises(ValueError):
            await coa_map.create_rule(s, tenant_id, None, "a", acct.id, business_id=biz["ulrg"])


# ── ignore ────────────────────────────────────────────────────────────────────────────────

async def test_ignoring_requires_a_reason(monkeypatch):
    """The one action that makes a number disappear is the one that has to say why."""
    tenant_id, biz = await _ids()
    await _fake_sync(monkeypatch, tenant_id, biz["ulrg"])
    async with SessionLocal() as s:
        with pytest.raises(ValueError):
            await coa_map.set_ignored(s, tenant_id, None, biz["ulrg"], ["900"], "   ")
        await coa_map.set_ignored(s, tenant_id, None, biz["ulrg"], ["900"],
                                  "Dead clearing account, zero balance since 2024")
    row = (await _rows(tenant_id, biz["ulrg"]))["900"]
    assert row.is_ignored and row.ignore_reason.startswith("Dead clearing")


async def test_ignored_accounts_are_left_alone_by_rules(monkeypatch):
    tenant_id, biz = await _ids()
    await _fake_sync(monkeypatch, tenant_id, biz["ulrg"])
    async with SessionLocal() as s:
        await coa_map.set_ignored(s, tenant_id, None, biz["ulrg"], ["302"], "duplicate account")
        va = await _std(s, tenant_id, "8060")
        await coa_map.create_rule(s, tenant_id, None,
                                  "61300 Contract Labor:Virtual Assistants:", va.id,
                                  business_id=biz["ulrg"])
    row = (await _rows(tenant_id, biz["ulrg"]))["302"]
    assert row.is_ignored and row.standard_account_id is None


# ── suggestions ───────────────────────────────────────────────────────────────────────────

# What a QBO AccountType is allowed to be suggested as. A suggestion that crosses this is not
# a near miss, it is a category error, and the reviewer trusting the column would never catch it.
_ALLOWED = {"Expense": {"opex", "cogs"}, "Other Expense": {"other_expense", "opex"},
            "Income": {"revenue"}, "Other Current Liability": {"liability"}}


async def test_suggestion_never_crosses_a_statement_section(monkeypatch):
    """An Expense account is never offered a revenue code, whatever its words say."""
    tenant_id, biz = await _ids()
    await _fake_sync(monkeypatch, tenant_id, biz["springb"])
    async with SessionLocal() as s:
        chart = await coa_map.standard_chart(s, tenant_id)
    by_id = {str(a.id): a for a in chart}
    checked = 0
    for row in (await _rows(tenant_id, biz["springb"])).values():
        sug = coa_map.suggest_for(row, chart)
        allowed = _ALLOWED.get(row.qbo_account_type or "")
        if sug is None or allowed is None:
            continue
        got = by_id[sug["standard_account_id"]]
        assert got.section in allowed, \
            f"{row.qbo_account_name} ({row.qbo_account_type}) -> {got.code} ({got.section})"
        checked += 1
    assert checked >= 3, "the suggester returned almost nothing — the hints regressed"


async def test_exact_name_beats_everything(monkeypatch):
    """QBO's "Rent" and standard 7010 "Rent" is the easy half of 254 accounts."""
    tenant_id, biz = await _ids()
    await _fake_sync(monkeypatch, tenant_id, biz["sympli"])
    async with SessionLocal() as s:
        chart = await coa_map.standard_chart(s, tenant_id)
        await coa_map.set_mapping(s, tenant_id, None, biz["sympli"], ["500"], None)
    sug = coa_map.suggest_for((await _rows(tenant_id, biz["sympli"]))["500"], chart)
    assert sug["code"] == "7010" and sug["confidence"] == "exact"


async def test_suggestion_is_none_once_mapped(monkeypatch):
    tenant_id, biz = await _ids()
    await _fake_sync(monkeypatch, tenant_id, biz["ulrg"])
    async with SessionLocal() as s:
        rent = await _std(s, tenant_id, "7010")
        await coa_map.set_mapping(s, tenant_id, None, biz["ulrg"], ["500"], rent.id)
        chart = await coa_map.standard_chart(s, tenant_id)
    rows = await _rows(tenant_id, biz["ulrg"])
    assert coa_map.suggest_for(rows["500"], chart) is None


# ── the screen + the API ──────────────────────────────────────────────────────────────────

async def test_overview_counts_add_up(monkeypatch):
    tenant_id, biz = await _ids()
    await _fake_sync(monkeypatch, tenant_id, biz["ulrg"])
    async with SessionLocal() as s:
        rent = await _std(s, tenant_id, "7010")
        await coa_map.set_mapping(s, tenant_id, None, biz["ulrg"], ["500"], rent.id)
        await coa_map.set_ignored(s, tenant_id, None, biz["ulrg"], ["900"], "dead account")
        view = await coa_map.mapping_overview(s, tenant_id, biz["ulrg"])
    c, by_qid = view["counts"], {a["qbo_account_id"]: a for a in view["accounts"]}
    assert c["total"] == len(ACCOUNTS)
    # The header's four numbers are what the bookkeeper works against; they have to partition
    # the chart exactly, with no account counted twice or falling between them.
    assert c["mapped"] + c["ignored"] + c["unmapped"] == c["total"]
    assert by_qid["500"]["code"] == "7010" and by_qid["500"]["mapped_via"] == "manual"
    assert by_qid["900"]["is_ignored"] and by_qid["900"]["ignore_reason"] == "dead account"
    assert not by_qid["900"]["code"], "an ignored account carries no standard account"
    assert len(view["standard"]) >= 130, "the whole standard chart is offered"


_UNKNOWN = "00000000-0000-0000-0000-0000000000ff"


async def test_overview_refuses_an_unknown_business():
    """Tenant scoping is checked on the way in, not assumed from the caller's session."""
    tenant_id, _ = await _ids()
    async with SessionLocal() as s:
        with pytest.raises(ValueError):
            await coa_map.mapping_overview(s, tenant_id, _UNKNOWN)


async def test_api_map_and_rules_permissions(monkeypatch):
    tenant_id, biz = await _ids()
    await _fake_sync(monkeypatch, tenant_id, biz["ulrg"])
    owner = await _owner_token()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        u = User(tenant_id=t.id, email="coa-member@x.com", name="coa", role="member",
                 password_hash=hash_pw("x"), status="active", tab_access=["books"],
                 token_version=0)
        s.add(u)
        await s.commit()
        member = make_token(u.id, t.id, 0)
        rent_id = str((await _std(s, tenant_id, "7010")).id)

    async with _client() as c:
        r = await c.get(f"/api/v1/books/coa/map?business_id={biz['ulrg']}", headers=_H(member))
        assert r.status_code == 200 and r.json()["counts"]["total"] == len(ACCOUNTS)

        # a bookkeeper maps accounts...
        r = await c.post("/api/v1/books/coa/map", headers=_H(member), json={
            "business_id": str(biz["ulrg"]), "qbo_account_ids": ["500"],
            "standard_account_id": rent_id})
        assert r.status_code == 200 and r.json()["updated"] == 1

        # ...but a structural rule is a CFO decision
        r = await c.post("/api/v1/books/coa/rules", headers=_H(member), json={
            "pattern": "63000 Occupancy:", "standard_account_id": rent_id})
        assert r.status_code == 403
        r = await c.post("/api/v1/books/coa/rules", headers=_H(owner), json={
            "pattern": "63000 Occupancy:", "standard_account_id": rent_id,
            "business_id": str(biz["ulrg"])})
        assert r.status_code == 200

        # unmapping hands the account back to the rules
        r = await c.post("/api/v1/books/coa/map", headers=_H(member), json={
            "business_id": str(biz["ulrg"]), "qbo_account_ids": ["500"],
            "standard_account_id": None})
        assert r.status_code == 200

        r = await c.get("/api/v1/books/coa", headers=_H(member))
        assert r.status_code == 200
        keys = {e["key"] for e in r.json()["entities"]}
        assert {"ulrg", "springb", "sympli"} <= keys

        # ignoring without a reason is a 400, not a silent success
        r = await c.post("/api/v1/books/coa/ignore", headers=_H(member), json={
            "business_id": str(biz["ulrg"]), "qbo_account_ids": ["900"], "reason": ""})
        assert r.status_code == 400


async def test_api_unknown_business_is_404():
    owner = await _owner_token()
    async with _client() as c:
        r = await c.get(f"/api/v1/books/coa/map?business_id={_UNKNOWN}", headers=_H(owner))
    assert r.status_code == 404


# ── how the suggester reads a path ────────────────────────────────────────────────────────
# These three are the failures the first real run against ULRG's chart produced. They are
# cheap to reintroduce by adding one hint, and expensive to notice: a wrong suggestion that
# looks plausible is accepted, and then it is a wrong number in a statement.

def _suggest(name, fqn, qtype, chart):
    class Row:
        standard_account_id = None
        is_ignored = False
        qbo_account_name, qbo_account_fqn, qbo_account_type = name, fqn, qtype
    return coa_map.suggest_for(Row(), chart)


async def test_a_hint_matches_whole_words_not_fragments():
    """"66000 Automobile" was reading as Mobile Phone."""
    async with SessionLocal() as s:
        chart = await coa_map.standard_chart(s, (await _ids())[0])
    assert _suggest("66000 Automobile", "66000 Automobile", "Expense", chart)["code"] == "8530"
    # ...while the prefix hints that carry real signal still work
    assert _suggest("Utilities Expense", "Utilities Expense", "Expense", chart)["code"] == "7020"
    assert _suggest("Benefits/Provisions", "61000 Compensation:61200 Benefits/Provisions",
                    "Expense", chart)["code"] == "8030"


async def test_the_deepest_part_of_the_path_wins():
    """A VA read as generic contract labour because the parent matched first. Depth is
    specificity — the same reason the longest rule prefix wins."""
    async with SessionLocal() as s:
        chart = await coa_map.standard_chart(s, (await _ids())[0])
    va = _suggest("Ana Ruiz", "61300 Contract Labor:Virtual Assistants:Ana Ruiz",
                  "Expense", chart)
    assert va["code"] == "8060", f"got {va['code']} — the parent drowned out the leaf"
    # a sibling branch with no deeper signal still lands on the generic account
    assert _suggest("Kofi Mensah", "61300 Contract Labor:Kofi Mensah",
                    "Expense", chart)["code"] == "8050"


async def test_the_accounts_own_name_beats_its_ancestors():
    async with SessionLocal() as s:
        chart = await coa_map.standard_chart(s, (await _ids())[0])
    got = _suggest("61400 Professional Services",
                   "61000 Compensation:61400 Professional Services", "Expense", chart)
    assert got["code"] == "8560", f"got {got['code']} — 'Compensation' won over the leaf"
