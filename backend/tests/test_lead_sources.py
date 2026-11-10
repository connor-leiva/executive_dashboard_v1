"""Lead-source groups, their QuickBooks spend mapping and cost shares (spec phase 1).

The acceptance list in docs/specs/ulrg-reporting-dashboard-port.md §10 phase 1, one test per
sentence where that is possible: matching, the Utah Life seed, a second business that cannot touch
Utah Life's rows, every spend-rule case (account, vendor, both, neither, the card account, a refund),
and the two refusals (an empty rule, shares past 100).
"""
import datetime as dt
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import (BookTxn, BookTxnLine, Business, Integration, LeadSourceCostShare,
                        LeadSourceGroup, LeadSourceSpendRule, Tenant, User)
from app.security import hash_pw, make_token
from app.seed import seed
from app.services import lead_source_spend as spend
from app.services import lead_sources as ls
from app.services.books_sync import _txn_to_row

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


async def _tenant(s):
    return (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()


async def _biz(s, tid, key):
    return (await s.execute(select(Business).where(Business.tenant_id == tid,
                                                   Business.key == key))).scalar_one()


async def _owner():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login", json={"email": "spring@springb.com", "password": "springtime"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def _member(email, tabs):
    async with SessionLocal() as s:
        t = await _tenant(s)
        u = User(tenant_id=t.id, email=email, name=email.split("@")[0], password_hash=hash_pw("x" * 12),
                 role="member", status="active", tab_access=tabs, token_version=0)
        s.add(u)
        await s.commit()
        return {"Authorization": f"Bearer {make_token(u.id, t.id, 0)}"}


async def _realtor(s, tid):
    ulrg = await _biz(s, tid, "ulrg")
    return (await s.execute(select(LeadSourceGroup).where(
        LeadSourceGroup.tenant_id == tid, LeadSourceGroup.business_id == ulrg.id,
        LeadSourceGroup.key == "realtor_com"))).scalar_one()


# ── matching ─────────────────────────────────────────────────────────────────────────────────

def test_contains_matches_variants_and_does_not_confuse_realty_with_realtor():
    m = ls.build_matcher(ls.REALTOR_VARIANTS, "contains")
    assert m("Realtor.com")
    assert m("Realtor.com MVIP")
    assert m("Market VIP via Opcity BLA")           # Opcity is in the Realtor.com group on purpose
    assert not m("Realty.com")                      # a different brand
    assert not m("Realtor")                         # the bare string is not a seeded variant
    assert not m("Zillow")
    assert not m(None)


def test_exact_and_regex_modes():
    exact = ls.build_matcher(["Realtor.com"], "exact")
    assert exact("  realtor.com ")
    assert not exact("Realtor.com MVIP")
    rx = ls.build_matcher([r"^zillow( |$)"], "regex")
    assert rx("Zillow Preferred") and rx("zillow")
    assert not rx("Zillow.com")


def test_empty_variants_match_nothing():
    assert not ls.build_matcher([], "contains")("anything")
    assert not ls.build_matcher(["  "], "exact")("")


def test_bad_regex_and_bad_key_are_refused():
    with pytest.raises(ls.InvalidGroup):
        ls.clean_variants(["(unclosed"], "regex")
    for bad in ("Realtor", "1abc", "a", "deleted_realtor", "has space"):
        with pytest.raises(ls.InvalidGroup):
            ls.clean_key(bad)


def test_group_for_prefers_the_lead_then_lead_type_then_unassigned():
    a = SimpleNamespace(key="realtor_com", display_name="Realtor.com", variants=["Realtor.com"],
                        match_mode="contains", is_active=True, display_order=0)
    b = SimpleNamespace(key="realtor_wide", display_name="Wide", variants=["realtor"],
                        match_mode="contains", is_active=True, display_order=5)
    assert ls.group_for("Realtor.com MVIP", None, [b, a])["key"] == "realtor_com"   # lower order wins
    assert ls.group_for(None, "realtor.com", [a])["key"] == "realtor_com"
    assert ls.group_for("Zillow", "realtor.com", [a])["key"] == "unassigned"         # lead wins over Sisu
    assert ls.group_for(None, None, [a]) == {"key": "unassigned", "label": "Unassigned"}


# ── the seed ─────────────────────────────────────────────────────────────────────────────────

async def test_utah_life_seed_matches_the_spec():
    async with SessionLocal() as s:
        t = await _tenant(s)
        g = await _realtor(s, t.id)
        assert g.variants == ls.REALTOR_VARIANTS and g.match_mode == "contains" and g.is_active
        assert g.program_start == dt.date(2026, 2, 1)
        assert g.exp_risk_team_default == Decimal(69) and g.exp_risk_agent_default == Decimal(49)
        rules = (await s.execute(select(LeadSourceSpendRule).where(
            LeadSourceSpendRule.lead_source_group_id == g.id))).scalars().all()
        assert {(r.account_qbo_id, r.vendor_qbo_id) for r in rules} == {
            ("365", None), (None, "962"), (None, "1568")}
        assert len(rules) == 3
        assert all(r.class_label is None for r in rules)
        sympli = await _biz(s, t.id, "sympli")
        shares = (await s.execute(select(LeadSourceCostShare).where(
            LeadSourceCostShare.lead_source_group_id == g.id))).scalars().all()
        assert [(sh.bearer_business_id, sh.share_pct) for sh in shares] == [(sympli.id, Decimal("50.00"))]
        realty = (await s.execute(select(LeadSourceGroup).where(
            LeadSourceGroup.tenant_id == t.id, LeadSourceGroup.key == "realty_com"))).scalar_one()
        assert realty.is_active is False and realty.variants == []
        # Idempotent: a second pass writes nothing and leaves an admin's edit alone.
        g.display_name = "Realtor.com (edited)"
        await s.commit()
        assert await s.run_sync(lambda ss: ls.seed_ulrg(ss, t.id)) == 0
        await s.refresh(g)
        assert g.display_name == "Realtor.com (edited)"
        g.display_name = "Realtor.com"
        await s.commit()


async def test_seed_skips_a_tenant_without_a_ulrg_business():
    async with SessionLocal() as s:
        t = Tenant(slug=f"other-{uuid.uuid4().hex[:6]}", name="Other Realty")
        s.add(t)
        await s.flush()
        s.add(Business(tenant_id=t.id, key="brokerage", name="Other Realty", tag="Real estate",
                       kind="real_estate"))
        await s.commit()
        assert await s.run_sync(lambda ss: ls.seed_ulrg(ss, t.id)) == 0
        assert not (await s.execute(select(LeadSourceGroup).where(
            LeadSourceGroup.tenant_id == t.id))).first()


# ── the API ──────────────────────────────────────────────────────────────────────────────────

async def test_a_second_business_defines_its_own_group_without_touching_utah_life():
    H = await _owner()
    async with SessionLocal() as s:
        t = await _tenant(s)
        before = ls.group_dict(await _realtor(s, t.id))
        if not (await s.execute(select(Business).where(Business.tenant_id == t.id,
                                                       Business.key == "zbrok"))).first():
            s.add(Business(tenant_id=t.id, key="zbrok", name="Second Brokerage", tag="Real estate",
                           kind="real_estate", sort_order=9))
            await s.commit()
    async with _client() as c:
        r = await c.post("/api/v1/businesses/zbrok/lead-sources", headers=H, json={
            "key": "zillow", "display_name": "Zillow", "variants": ["Zillow", "Zillow Preferred"],
            "match_mode": "contains", "program_start": "2026-05-01"})
        assert r.status_code == 201, r.text
        assert r.json()["exp_risk_team_default"] == 0          # $69 is Utah Life's seed, not a default
        dup = await c.post("/api/v1/businesses/zbrok/lead-sources", headers=H,
                           json={"key": "zillow", "display_name": "Zillow again"})
        assert dup.status_code == 400
        lst = (await c.get("/api/v1/businesses/zbrok/lead-sources", headers=H)).json()
        assert [g["key"] for g in lst["groups"]] == ["zillow"]
    async with SessionLocal() as s:
        t = await _tenant(s)
        assert ls.group_dict(await _realtor(s, t.id)) == before


async def test_listing_reads_active_groups_and_members_need_the_tab():
    H = await _owner()
    async with _client() as c:
        active = (await c.get("/api/v1/businesses/ulrg/lead-sources", headers=H)).json()
        every = (await c.get("/api/v1/businesses/ulrg/lead-sources?include_inactive=true", headers=H)).json()
        assert [g["key"] for g in active["groups"]] == ["realtor_com"]
        assert {g["key"] for g in every["groups"]} == {"realtor_com", "realty_com"}
        realtor = active["groups"][0]
        assert realtor["spend_mapped"] is True and realtor["cost_shares"][0]["bearer_name"] == "Sympli Mortgage"

        outsider = await _member(f"forum-{uuid.uuid4().hex[:6]}@x.com", ["forum"])
        assert (await c.get("/api/v1/businesses/ulrg/lead-sources", headers=outsider)).status_code == 403
        reader = await _member(f"ulrg-{uuid.uuid4().hex[:6]}@x.com", ["ulrg"])
        seen = await c.get("/api/v1/businesses/ulrg/lead-sources?include_inactive=true", headers=reader)
        assert seen.status_code == 200
        assert [g["key"] for g in seen.json()["groups"]] == ["realtor_com"]    # members: active only
        assert seen.json()["can_edit"] is False
        assert (await c.patch("/api/v1/businesses/ulrg/lead-sources/realtor_com", headers=reader,
                              json={"display_name": "x"})).status_code == 403


async def test_a_rule_with_nothing_set_and_shares_past_100_are_refused():
    H = await _owner()
    async with _client() as c:
        r = await c.put("/api/v1/businesses/ulrg/lead-sources/realtor_com/spend", headers=H,
                        json={"rules": [{"account_qbo_id": "", "vendor_qbo_id": None}], "cost_shares": []})
        assert r.status_code == 400 and "every expense" in r.json()["detail"]
        biz = (await c.get("/api/v1/businesses", headers=H)).json()
        others = [b["id"] for b in biz if b["key"] in ("sympli", "springb")]
        r = await c.put("/api/v1/businesses/ulrg/lead-sources/realtor_com/spend", headers=H, json={
            "rules": [{"account_qbo_id": "365"}],
            "cost_shares": [{"bearer_business_id": others[0], "share_pct": 60},
                            {"bearer_business_id": others[1], "share_pct": 60}]})
        assert r.status_code == 400 and "100" in r.json()["detail"]
        # Nothing changed: the refused write did not half-apply.
        view = (await c.get("/api/v1/businesses/ulrg/lead-sources/realtor_com/spend", headers=H)).json()
        assert len(view["rules"]) == 3 and view["share_pct"] == 50.0


async def test_soft_delete_frees_the_key():
    H = await _owner()
    async with _client() as c:
        await c.post("/api/v1/businesses/ulrg/lead-sources", headers=H,
                     json={"key": "temp_src", "display_name": "Temp", "variants": ["Temp"]})
        d = await c.delete("/api/v1/businesses/ulrg/lead-sources/temp_src", headers=H)
        assert d.status_code == 200 and d.json()["key"] == "deleted_temp_src"
        again = await c.post("/api/v1/businesses/ulrg/lead-sources", headers=H,
                             json={"key": "temp_src", "display_name": "Temp", "variants": ["Temp"]})
        assert again.status_code == 201
        every = (await c.get("/api/v1/businesses/ulrg/lead-sources?include_inactive=true", headers=H)).json()
        assert not any(g["key"].startswith("deleted_") for g in every["groups"])
        await c.delete("/api/v1/businesses/ulrg/lead-sources/temp_src", headers=H)


# ── QuickBooks spend ─────────────────────────────────────────────────────────────────────────

async def _purchase(s, tid, bid, qbo_id, day, lines, *, vendor=None, credit=False, qbo_type="Purchase",
                    card="Amex 1150040000"):
    txn = BookTxn(tenant_id=tid, business_id=bid, realm_id="realm-ulrg", qbo_type=qbo_type, qbo_id=qbo_id,
                  txn_date=day, amount=Decimal(str(sum(a for a, *_ in lines))), payee="Vendor",
                  vendor_qbo_id=vendor, purchase_credit=credit if qbo_type == "Purchase" else None,
                  bank_account_label=card, account_qbo_id="1150040000")
    s.add(txn)
    await s.flush()
    for i, (amt, acct, *rest) in enumerate(lines):
        s.add(BookTxnLine(tenant_id=tid, book_txn_id=txn.id, line_num=i, amount=Decimal(str(amt)),
                          account_qbo_id=acct, posting_type=(rest[0] if rest else None),
                          detail_type="AccountBasedExpenseLineDetail"))


async def test_spend_rules_count_each_line_once_and_net_the_share():
    """The spec's worked example plus every edge it names, on one month of Utah Life's books."""
    async with SessionLocal() as s:
        t = await _tenant(s)
        ulrg = await _biz(s, t.id, "ulrg")
        s.add(Integration(tenant_id=t.id, provider="qbo", business_id=ulrg.id, status="connected",
                          realm_id="realm-ulrg", last_synced_at=dt.datetime.now(dt.timezone.utc)))
        feb = dt.date(2026, 2, 10)
        await _purchase(s, t.id, ulrg.id, "P-opcity", feb, [(100, "999")], vendor="1568")       # vendor rule
        await _purchase(s, t.id, ulrg.id, "P-rdc", feb, [(100, "777")], vendor="962")           # vendor rule
        await _purchase(s, t.id, ulrg.id, "P-both", feb, [(50, "365")], vendor="1568")          # two rules, once
        await _purchase(s, t.id, ulrg.id, "P-noven", dt.date(2026, 3, 3), [(4000, "365")])      # account, no vendor
        await _purchase(s, t.id, ulrg.id, "P-fb", feb, [(900, "386")], vendor="55")             # Facebook on the Amex: no
        await _purchase(s, t.id, ulrg.id, "P-refund", dt.date(2026, 3, 9), [(200, "365")], credit=True)
        await _purchase(s, t.id, ulrg.id, "JE-1", dt.date(2026, 3, 20), [(30, "365", "credit")],
                        qbo_type="JournalEntry")
        await _purchase(s, t.id, ulrg.id, "BP-1", feb, [(100, "2000")], vendor="962", qbo_type="BillPayment")
        await s.commit()

        g = await _realtor(s, t.id)
        m = await spend.monthly_spend(s, t.id, g)
        assert m["mapped"] and m["synced"]
        assert m["gross_by_month"]["2026-02"] == Decimal("250.00")
        assert spend.net_of(m["gross_by_month"]["2026-02"], m["share_pct"]) == Decimal("125.00")
        # March: $4,000 on account 365 with no vendor, less a $200 refund and a $30 credit posting.
        assert m["gross_by_month"]["2026-03"] == Decimal("3770.00")
        assert m["lines_matching_several_rules"] == 1
        assert not any(ln["qbo_id"] in ("P-fb", "BP-1") for ln in m["lines"])


async def test_a_line_matching_two_groups_counts_in_the_first_only_and_settings_says_so():
    H = await _owner()
    async with _client() as c:
        await c.post("/api/v1/businesses/ulrg/lead-sources", headers=H, json={
            "key": "opcity_extra", "display_name": "Opcity extra", "variants": ["Opcity Direct"],
            "display_order": 20})
        await c.put("/api/v1/businesses/ulrg/lead-sources/opcity_extra/spend", headers=H,
                    json={"rules": [{"vendor_qbo_id": "1568"}], "cost_shares": []})
        view = (await c.get("/api/v1/businesses/ulrg/lead-sources/opcity_extra/spend", headers=H)).json()
        assert view["preview"]["gross"] == 0                       # Realtor.com (order 0) took them
        assert view["overlap_with_other_groups"] == {"realtor_com": 2}
        await c.delete("/api/v1/businesses/ulrg/lead-sources/opcity_extra", headers=H)
    async with SessionLocal() as s:
        t = await _tenant(s)
        m = await spend.monthly_spend(s, t.id, await _realtor(s, t.id))
        assert m["gross_by_month"]["2026-02"] == Decimal("250.00")


async def test_saving_a_rule_changes_the_numbers_on_the_next_read():
    H = await _owner()
    async with _client() as c:
        before = (await c.get("/api/v1/businesses/ulrg/lead-sources/realtor_com/spend", headers=H)).json()
        rules = before["rules"] + [{"account_qbo_id": "386"}]
        biz = {b["key"]: b["id"] for b in (await c.get("/api/v1/businesses", headers=H)).json()}
        r = await c.put("/api/v1/businesses/ulrg/lead-sources/realtor_com/spend", headers=H, json={
            "rules": rules, "cost_shares": [{"bearer_business_id": biz["sympli"], "share_pct": 50}]})
        assert r.status_code == 200, r.text
        assert r.json()["preview"]["gross"] == before["preview"]["gross"] + 900
        # Put it back: Facebook is not Realtor.com.
        await c.put("/api/v1/businesses/ulrg/lead-sources/realtor_com/spend", headers=H, json={
            "rules": before["rules"], "cost_shares": [{"bearer_business_id": biz["sympli"], "share_pct": 50}]})


async def test_no_rule_means_not_mapped_rather_than_zero():
    async with SessionLocal() as s:
        t = await _tenant(s)
        realty = (await s.execute(select(LeadSourceGroup).where(
            LeadSourceGroup.tenant_id == t.id, LeadSourceGroup.key == "realty_com"))).scalar_one()
        m = await spend.monthly_spend(s, t.id, realty)
        assert m["mapped"] is False and m["gross_by_month"] == {}


# ── books_sync keeps what the rules match on ────────────────────────────────────────────────

INTEG = SimpleNamespace(business_id=uuid.uuid4(), realm_id="r", config=None)


def test_books_sync_keeps_the_vendor_id_and_the_credit_flag():
    p = _txn_to_row(uuid.uuid4(), INTEG, "Purchase", {
        "Id": "1", "TxnDate": "2026-02-02", "TotalAmt": 10, "Credit": True,
        "EntityRef": {"value": "1568", "name": "Opcity, Inc", "type": "Vendor"},
        "AccountRef": {"value": "1150040000", "name": "Amex"},
        "Line": [{"Amount": 10, "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "365"}}}]})
    assert p["vendor_qbo_id"] == "1568" and p["purchase_credit"] is True
    bill = _txn_to_row(uuid.uuid4(), INTEG, "Bill", {
        "Id": "2", "TxnDate": "2026-02-02", "TotalAmt": 5, "VendorRef": {"value": "962", "name": "Realtor.com"},
        "Line": []})
    assert bill["vendor_qbo_id"] == "962" and bill["purchase_credit"] is None
    to_customer = _txn_to_row(uuid.uuid4(), INTEG, "Purchase", {
        "Id": "3", "TxnDate": "2026-02-02", "TotalAmt": 5,
        "EntityRef": {"value": "44", "name": "A client", "type": "Customer"}, "Line": []})
    assert to_customer["vendor_qbo_id"] is None and to_customer["purchase_credit"] is False
