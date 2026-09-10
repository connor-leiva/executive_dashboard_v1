"""The Friday expense review: see everything, know why, act in bulk.

Connor and Spring go line by line through every transaction weekly. The queue previously
answered exactly one question — "what needs approval?" — so the only transactions visible were
the ones the pipeline got stuck on, out of a window nobody chose.
"""
import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.seed import seed
from app.db import SessionLocal
from app.models import Tenant, Business, BookTxn, User
from app.services import books
from app.services.books_scan import BASIS_HISTORY, BASIS_SPLIT, BASIS_NONE


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


async def _ctx():
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        b = (await s.execute(select(Business).where(Business.key == "springb"))).scalar_one()
        u = (await s.execute(select(User).where(User.tenant_id == t.id))).scalars().first()
        return t.id, b.id, u


def _txn(tid, bid, qbo_id, state, day, sug=None, reviewed_at=None):
    return BookTxn(tenant_id=tid, business_id=bid, realm_id="r-rev", qbo_type="Purchase",
                   qbo_id=qbo_id, txn_date=dt.date.today() - dt.timedelta(days=day),
                   amount=Decimal("50.00"), payee=f"vendor-{qbo_id}", scan_state=state,
                   suggestion=sug, reviewed_at=reviewed_at)


async def _reset_and_seed_rows():
    """A window with one transaction in each state, plus one signed off and one long past."""
    tid, bid, u = await _ctx()
    async with SessionLocal() as s:
        for r in (await s.execute(select(BookTxn).where(
                BookTxn.realm_id == "r-rev"))).scalars().all():
            await s.delete(r)
        await s.commit()
    async with SessionLocal() as s:
        s.add_all([
            _txn(tid, bid, "C1", "cleared", 1,
                 {"category": "Office Supplies", "confidence": 0.99, "basis": BASIS_HISTORY,
                  "priors": 12, "reason": "Matches 12 prior charges categorized here."}),
            _txn(tid, bid, "N1", "needs_approval", 2,
                 {"category": "Uncategorized Asset", "confidence": 0.0, "basis": BASIS_SPLIT,
                  "reason": "Split across multiple accounts; needs review."}),
            _txn(tid, bid, "E1", "escalated", 3),
            _txn(tid, bid, "P1", "pending", 4),                       # no suggestion at all
            _txn(tid, bid, "S1", "cleared", 5,
                 {"category": "Rent", "confidence": 0.99, "basis": BASIS_HISTORY, "priors": 3,
                  "reason": "Matches 3 prior charges categorized here."},
                 reviewed_at=dt.datetime.now(dt.timezone.utc)),       # already signed off
            _txn(tid, bid, "OLD", "cleared", 60),                     # outside a 7-day window
        ])
        await s.commit()
    return tid, bid, u


async def test_the_window_comes_from_the_global_period():
    tid, _, _ = await _reset_and_seed_rows()
    today = dt.date.today()
    # A 90-day custom range, so the assertion holds wherever in the calendar the suite runs.
    wide = f"c:{(today - dt.timedelta(days=90)).isoformat()}:{today.isoformat()}"
    async with SessionLocal() as s:
        wk = await books.build_books_queue(s, tid, period="last_7", state="all")
        broad = await books.build_books_queue(s, tid, period=wide, state="all")
    assert wk["period"]["key"] == "last_7"
    assert wk["period"]["label"] == "Last 7 days"
    assert (dt.date.fromisoformat(wk["period"]["end"])
            - dt.date.fromisoformat(wk["period"]["start"])).days == 6
    week = {r["vendor"] for r in wk["rows"]}
    assert "vendor-OLD" not in week                 # 60 days back: outside the review window
    assert "vendor-OLD" in {r["vendor"] for r in broad["rows"]}
    assert "vendor-C1" in week


async def test_every_stage_is_reachable_not_just_needs_approval():
    tid, _, _ = await _reset_and_seed_rows()
    async with SessionLocal() as s:
        for state in ("cleared", "needs_approval", "escalated", "pending"):
            q = await books.build_books_queue(s, tid, period="ytd", state=state)
            assert q["rows"], state
            assert {r["scan_state"] for r in q["rows"]} == {state}
        allq = await books.build_books_queue(s, tid, period="ytd", state="all")
    assert len({r["scan_state"] for r in allq["rows"]}) >= 4


async def test_stage_counts_describe_the_window_not_the_active_filter():
    """Otherwise the tab you're standing on is the only number you can trust."""
    tid, _, _ = await _reset_and_seed_rows()
    async with SessionLocal() as s:
        a = await books.build_books_queue(s, tid, period="ytd", state="cleared")
        b = await books.build_books_queue(s, tid, period="ytd", state="escalated")
    assert a["stages"] == b["stages"]
    assert a["stages"]["all"] == sum(a["stages"][st] for st in books.RAIL_STATES)
    # Every tab the UI can render must carry a count, or that tab silently shows a blank one.
    for key, _label in [("all", ""), ("auto", ""), ("cleared", ""), ("needs_approval", ""),
                        ("escalated", ""), ("pending", ""), ("approved", "")]:
        assert key in a["stages"], key


async def test_the_auto_categorized_lens_cuts_across_the_rail():
    """It is a stage on the home funnel but not a bucket in the partition, so it needs its own
    filter rather than a slot in scan_state."""
    tid, bid, _ = await _reset_and_seed_rows()
    async with SessionLocal() as s:
        t = (await s.execute(select(BookTxn).where(
            BookTxn.realm_id == "r-rev", BookTxn.qbo_id == "C1"))).scalar_one()
        t.came_categorized = True
        await s.commit()
    async with SessionLocal() as s:
        q = await books.build_books_queue(s, tid, period="ytd", state="auto")
    assert q["stages"]["auto"] == 1
    assert [r["vendor"] for r in q["rows"]] == ["vendor-C1"]
    assert all(r["came_categorized"] for r in q["rows"])


async def test_signed_off_rows_do_not_come_back_next_friday():
    tid, _, _ = await _reset_and_seed_rows()
    async with SessionLocal() as s:
        hidden = await books.build_books_queue(s, tid, period="ytd", state="cleared")
        shown = await books.build_books_queue(s, tid, period="ytd", state="cleared",
                                              include_signed_off=True)
    assert not any(r["signed_off"] for r in hidden["rows"])
    assert any(r["signed_off"] for r in shown["rows"])
    assert len(shown["rows"]) > len(hidden["rows"])


async def test_the_approved_chip_is_never_a_count_above_an_empty_list():
    """Approving stamps reviewed_at, so the default "hide what I've signed off" filter would
    empty the Approved stage of its own contents — a chip reading 9 over nothing."""
    tid, _, u = await _reset_and_seed_rows()
    async with SessionLocal() as s:                      # approve one, the way Friday does
        t = (await s.execute(select(BookTxn).where(
            BookTxn.realm_id == "r-rev", BookTxn.qbo_id == "N1"))).scalar_one()
        await books.approve_txn(s, tid, u, t.id)
    async with SessionLocal() as s:
        q = await books.build_books_queue(s, tid, period="ytd", state="approved")
    assert q["stages"]["approved"] > 0
    assert len(q["rows"]) == q["stages"]["approved"]
    assert all(r["signed_off"] for r in q["rows"])


async def test_a_chip_count_predicts_what_clicking_it_returns():
    """The whole point of faceting. A count computed over the window regardless of the other
    active filters reads 30 above a list showing 4, which is how you stop trusting the screen."""
    tid, _, _ = await _reset_and_seed_rows()
    async with SessionLocal() as s:
        base = await books.build_books_queue(s, tid, period="ytd", state="all",
                                             basis=BASIS_HISTORY)
        for stage, promised in base["stages"].items():
            if stage in ("all", "auto", "auto_categorized"):
                continue
            got = await books.build_books_queue(s, tid, period="ytd", state=stage,
                                                basis=BASIS_HISTORY)
            assert len(got["rows"]) == promised, f"{stage}: chip said {promised}"


async def test_the_two_axes_are_independent():
    """Stage is where a transaction sits; basis is what decided it. Something can be cleared BY
    history or cleared BY Claude, and the second question was previously unaskable."""
    tid, _, _ = await _reset_and_seed_rows()
    async with SessionLocal() as s:
        q = await books.build_books_queue(s, tid, period="ytd", state="cleared",
                                          basis=BASIS_HISTORY)
    assert q["rows"]
    assert all(r["scan_state"] == "cleared" for r in q["rows"])
    assert all(r["basis"] == BASIS_HISTORY for r in q["rows"])


async def test_strength_is_decided_once_on_the_server():
    """The filter, the row badge and the drawer must not each decide what "weak" means."""
    tid, _, _ = await _reset_and_seed_rows()
    async with SessionLocal() as s:
        q = await books.build_books_queue(s, tid, period="ytd", state="all", weak_only=True)
    assert all(r["strength"] == "weak" for r in q["rows"])
    assert len(q["rows"]) == q["lenses"]["weak"]
    # 3 priors is below the strong threshold; 195 is far above it.
    assert books.strength_of({"basis": BASIS_HISTORY, "priors": 3}) == "weak"
    assert books.strength_of({"basis": BASIS_HISTORY, "priors": 195}) == "strong"
    # A known vendor behaving unusually is never a strong call, however many priors it has.
    assert books.strength_of({"basis": "over_band", "priors": 400}) == "thin"
    # Splits and unreached rows have nothing to be confident about.
    assert books.strength_of({"basis": BASIS_SPLIT}) == "na"
    assert books.strength_of(None) == "na"


async def test_entities_travel_with_the_payload_not_a_frontend_constant():
    """A hardcoded entity map is per-customer code: wrong labels and no colours for tenant two."""
    tid, _, _ = await _reset_and_seed_rows()
    async with SessionLocal() as s:
        q = await books.build_books_queue(s, tid, period="ytd", state="all")
    assert q["entities"]
    for e in q["entities"]:
        assert e["key"] and e["name"]
        assert e["accent"] and e["accent"].startswith("#")
    assert q["thresholds"]["history_strong"] > q["thresholds"]["history_thin"]


async def test_an_approved_row_names_who_signed_it_off():
    """Acuity reads this stage. "Approved" with nobody's name against it is an assertion
    rather than a record, and an outside reviewer cannot do anything with an assertion."""
    tid, _, u = await _reset_and_seed_rows()
    async with SessionLocal() as s:
        t = (await s.execute(select(BookTxn).where(
            BookTxn.realm_id == "r-rev", BookTxn.qbo_id == "N1"))).scalar_one()
        await books.approve_txn(s, tid, u, t.id)
    async with SessionLocal() as s:
        q = await books.build_books_queue(s, tid, period="ytd", state="approved")
    row = next(r for r in q["rows"] if r["vendor"] == "vendor-N1")
    assert row["decided_by"] == (u.name or u.email)
    assert row["decision"] == "approve"
    assert row["signed_off_at"]


async def test_a_recategorized_row_carries_the_category_a_human_chose():
    """recategorize_txn records the new category but never touches QuickBooks, so the decision
    and what the books actually say are two different values. Both have to be visible."""
    tid, _, u = await _reset_and_seed_rows()
    async with SessionLocal() as s:
        t = (await s.execute(select(BookTxn).where(
            BookTxn.realm_id == "r-rev", BookTxn.qbo_id == "N1"))).scalar_one()
        await books.recategorize_txn(s, tid, u, t.id, "Office Supplies")
    async with SessionLocal() as s:
        q = await books.build_books_queue(s, tid, period="ytd", state="approved")
    row = next(r for r in q["rows"] if r["vendor"] == "vendor-N1")
    assert row["decision"] == "recategorize"
    assert row["decided_category"] == "Office Supplies"
    assert row["decided_category"] != row["current_category"]     # QBO still has the old one


async def test_other_stages_still_hide_signed_off_work():
    """The exemption is scoped to the Approved stage only; everywhere else the whole point is
    that what you signed off last Friday does not come back."""
    tid, _, _ = await _reset_and_seed_rows()
    async with SessionLocal() as s:
        for st in ("cleared", "all"):
            q = await books.build_books_queue(s, tid, period="ytd", state=st)
            assert not any(r["signed_off"] for r in q["rows"]), st


async def test_a_row_carries_why_it_is_where_it_is():
    tid, _, _ = await _reset_and_seed_rows()
    async with SessionLocal() as s:
        q = await books.build_books_queue(s, tid, period="ytd", state="all")
    by = {r["vendor"]: r for r in q["rows"]}
    hist = by["vendor-C1"]
    assert hist["basis"] == BASIS_HISTORY and hist["priors"] == 12
    assert hist["basis_label"] == "Matched from history"
    assert hist["is_proposal"] is True

    split = by["vendor-N1"]
    assert split["basis"] == BASIS_SPLIT
    # a split's category is where it already sits, NOT a 0%-confidence guess
    assert split["is_proposal"] is False

    pend = by["vendor-P1"]
    assert pend["basis"] == BASIS_NONE
    assert pend["basis_label"] == "Not yet reached"
    assert pend["is_proposal"] is False


async def test_acknowledge_keeps_the_state_and_the_rail_balanced():
    """An auto-cleared txn was never proposed for approval; calling it approved would overstate
    what happened, and moving it to a new bucket would alarm the home screen."""
    tid, _, u = await _reset_and_seed_rows()
    async with SessionLocal() as s:
        t = (await s.execute(select(BookTxn).where(
            BookTxn.realm_id == "r-rev", BookTxn.qbo_id == "C1"))).scalar_one()
        before = t.scan_state
        await books.acknowledge_txn(s, tid, u, t.id)
    async with SessionLocal() as s:
        t = (await s.execute(select(BookTxn).where(
            BookTxn.realm_id == "r-rev", BookTxn.qbo_id == "C1"))).scalar_one()
        assert t.scan_state == before == "cleared"
        assert t.reviewed_at is not None and t.reviewed_by == u.id
        assert t.decision["action"] == "acknowledged"
        inv = await books.books_invariants(s, tid)
    assert inv["rail_conservation"] is True


async def test_bulk_approve_stamps_every_row_individually():
    """A bulk action is a hundred decisions made quickly, not one decision about a hundred."""
    tid, _, u = await _reset_and_seed_rows()
    async with SessionLocal() as s:
        rows = (await s.execute(select(BookTxn).where(
            BookTxn.realm_id == "r-rev", BookTxn.scan_state == "needs_approval"))).scalars().all()
        ids = [r.id for r in rows]
        res = await books.bulk_review(s, tid, u, ids, "approve")
    assert res["applied"] == len(ids) and res["missing"] == []
    async with SessionLocal() as s:
        for i in ids:
            t = (await s.execute(select(BookTxn).where(BookTxn.id == i))).scalar_one()
            assert t.scan_state == "approved"
            assert t.reviewed_by == u.id and t.decision["bulk"] is True
        inv = await books.books_invariants(s, tid)
    assert inv["rail_conservation"] is True          # approving must not unbalance the rail
    assert inv["no_unreviewed_approvals"] is True


async def test_bulk_reports_ids_it_could_not_find():
    import uuid as _u
    tid, _, u = await _reset_and_seed_rows()
    async with SessionLocal() as s:
        ghost = _u.uuid4()
        res = await books.bulk_review(s, tid, u, [ghost], "acknowledge")
    assert res["requested"] == 1 and res["applied"] == 0
    assert res["missing"] == [str(ghost)]           # never silently does less than asked


async def test_bulk_rejects_an_unknown_action_and_an_oversized_batch():
    import uuid as _u
    tid, _, u = await _reset_and_seed_rows()
    async with SessionLocal() as s:
        with pytest.raises(ValueError):
            await books.bulk_review(s, tid, u, [], "delete_everything")
        with pytest.raises(ValueError):
            await books.bulk_review(s, tid, u, [_u.uuid4() for _ in range(books.BULK_MAX + 1)],
                                    "approve")
