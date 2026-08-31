"""What one sync SPENDS, and what it does when Meta says stop. SPEC-ads-module.md Part 5.4.

Written after ten consecutive scheduled syncs reported "ok" while every one of them failed on
`User request limit reached`. Three separate defects held that pattern in place:

  * a spent quota was RETRIED, four times a run, behind a clamp that ignored Meta's own recovery
    estimate - so each attempt to recover cost a call the account did not have;
  * nothing remembered the refusal between runs, so the scheduler rediscovered it every tick;
  * creative enrichment re-read forty ads' headlines EVERY sync, which was the volume that spent
    the quota in the first place.

All three are pure-function testable, which is why they are pinned here rather than left to the
next live failure to find.
"""
import datetime as dt

import pytest

from app.seed import seed


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()




# ── parking a rate-limited integration ────────────────────────────────────────────────
class _FakeInteg:
    """Only .config is touched by the parking helpers."""
    def __init__(self, config=None):
        self.config = config


def test_an_unparked_integration_has_no_cooldown():
    from app.services import ads_sync as A
    assert A._cooldown_remaining(_FakeInteg(None)) == 0
    assert A._cooldown_remaining(_FakeInteg({})) == 0


def test_parking_uses_metas_own_estimate_and_reassigns_config():
    """Reassignment, not mutation: an in-place edit of a JSON column does not mark the row dirty,
    so the park would be silently dropped at commit and the next tick would sync straight into
    the wall again - the bug this whole mechanism exists to stop."""
    from app.services import ads_sync as A
    integ = _FakeInteg({"existing": "kept"})
    before = integ.config
    A._park(integ, 1800, integ.config)
    assert integ.config is not before, "config was mutated in place; SQLAlchemy will not save it"
    assert integ.config["existing"] == "kept"
    remaining = A._cooldown_remaining(integ)
    assert 1700 < remaining <= 1800


def test_parking_without_an_estimate_falls_back_to_an_hour():
    from app.services import ads_sync as A
    integ = _FakeInteg()
    A._park(integ, None, integ.config)
    assert 3500 < A._cooldown_remaining(integ) <= 3600


def test_a_park_is_capped_so_an_absurd_estimate_cannot_strand_the_account():
    from app.services import ads_sync as A
    integ = _FakeInteg()
    A._park(integ, 999_999, integ.config)
    assert A._cooldown_remaining(integ) <= 6 * 3600


def test_a_corrupt_cooldown_reads_as_not_parked():
    """Fail OPEN. A malformed timestamp stranding an integration forever, with no route back that
    does not involve editing the database by hand, is worse than one extra call to Meta."""
    from app.services import ads_sync as A
    for bad in ("", "not a date", None, 12345):
        assert A._cooldown_remaining(_FakeInteg({"meta_cooldown_until": bad})) == 0


def test_the_throttle_message_names_the_tier_meta_actually_reported():
    """"User request limit reached" names no cause and suggests no remedy, and the cause was
    legible the whole time: Meta stamps ads_api_access_tier on every ads response. Reading it beat
    two rounds of reasoning about what the error meant."""
    from app.services import ads_sync as A
    from app.integrations.meta_ads import MetaError
    known = A._explain(MetaError("User request limit reached", code=17, tier="development_access"))
    assert "DEVELOPMENT access tier" in known
    assert "Advanced Access" in known

    # Unknown tier must not assert one. Claiming development_access when the header did not say
    # so sends somebody to change a setting that may already be correct.
    unknown = A._explain(MetaError("User request limit reached", code=17))
    assert "DEVELOPMENT access tier" not in unknown
    assert A._explain(ValueError("something else")) == "ValueError: something else"


# ── creative refresh is enrichment and must not dominate the quota ────────────────────
class _FakeAd:
    def __init__(self, fetched=None):
        self.creative_fetched_at = fetched


def test_a_recently_fetched_creative_is_not_refetched():
    """The volume bug. One request per ad, forty ads, every half hour - roughly 1,900 calls a day
    to re-read headlines that had not changed. It is the largest single consumer in this sync and
    the least valuable, and it is what spent the quota that blanked the spend figures."""
    from app.services import ads_sync as A
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=7)
    assert A._creative_is_fresh(_FakeAd(now - dt.timedelta(days=1)), cutoff) is True
    assert A._creative_is_fresh(_FakeAd(now - dt.timedelta(days=30)), cutoff) is False


def test_a_never_fetched_creative_is_always_stale():
    from app.services import ads_sync as A
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)
    assert A._creative_is_fresh(_FakeAd(None), cutoff) is False


def test_a_naive_timestamp_does_not_raise():
    """SQLite returns naive datetimes and Postgres returns aware ones. Comparing the two raises
    TypeError - and this runs inside the swallowed creative block, so the symptom would have been
    creatives silently never refreshing on exactly one of the two databases."""
    from app.services import ads_sync as A
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)
    assert A._creative_is_fresh(_FakeAd(dt.datetime.utcnow()), cutoff) is True
    assert A._creative_is_fresh(_FakeAd(dt.datetime.utcnow() - dt.timedelta(days=30)), cutoff) is False


# ── the park must actually reach the database ─────────────────────────────────────────
async def test_a_throttle_parks_the_integration_through_a_real_session():
    """The rollback trap, tested against a live session rather than reasoned about.

    `_park` reads integ.config inside the exception handler, which runs AFTER `s.rollback()`.
    Rollback expires the objects in the session, and reading an expired attribute in async
    SQLAlchemy raises MissingGreenlet rather than lazily loading. The parking helpers are pure
    and their unit tests pass on a stub either way, so nothing above this catches it.

    Asserts through a SEPARATE session, because the point is that the park was COMMITTED - an
    in-memory value that never lands leaves the scheduler syncing into the wall exactly as before.
    """
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.integrations import meta_ads as meta
    from app.models import AdAccount, Integration, Tenant
    from app.security import enc
    from app.services import ads_sync as A

    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        integ = Integration(tenant_id=t.id, provider="meta_ads", status="connected",
                            access_token_enc=enc("fake-token"), config={"keep": "me"})
        s.add(integ)
        await s.flush()
        s.add(AdAccount(tenant_id=t.id, integration_id=integ.id, platform="meta",
                        external_id="act_park", name="Park test", timezone_name="UTC"))
        await s.commit()
        integ_id = integ.id

    async def _boom(*a, **kw):
        raise meta.MetaError("User request limit reached", code=17, retry_after_s=1800)

    real = meta.campaigns
    meta.campaigns = _boom
    try:
        async with SessionLocal() as s:
            integ = await s.get(Integration, integ_id)
            out = await A.sync_meta_ads(s, integ.tenant_id, integ)
    finally:
        meta.campaigns = real

    assert out["results"][0]["error"], "the throttle was not recorded on the account"

    async with SessionLocal() as s:                       # fresh session: did it COMMIT?
        again = await s.get(Integration, integ_id)
        assert again.config.get("keep") == "me", "parking clobbered the rest of config"
        assert A._cooldown_remaining(again) > 1700, "the park never reached the database"

        # And the next tick leaves it alone.
        out2 = await A.sync_meta_ads(s, again.tenant_id, again)
        assert "rate limited" in out2.get("skipped", ""), \
            "a parked integration synced anyway; the scheduler will keep spending the quota"

    async with SessionLocal() as s:                       # cleanup: shared module-scoped db
        from sqlalchemy import delete as sa_delete
        await s.execute(sa_delete(AdAccount).where(AdAccount.integration_id == integ_id))
        await s.execute(sa_delete(Integration).where(Integration.id == integ_id))
        await s.commit()


# ── the ads cadence, sized to a measured account ──────────────────────────────────────
class _FakeAcct:
    def __init__(self, last=None):
        self.last_synced_at = last


def test_a_never_synced_account_is_always_due():
    """The interval throttles REPEAT pulls. An account that has never completed a sync is the one
    case where waiting accomplishes nothing at all."""
    from app.services import ads_sync as A
    assert A._is_due(_FakeAcct(None)) is True


def test_a_recently_synced_account_is_not_due():
    from app.services import ads_sync as A
    from app.config import settings
    just_now = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
    long_ago = dt.datetime.now(dt.timezone.utc) - dt.timedelta(
        minutes=settings.ADS_SYNC_INTERVAL_MINUTES + 5)
    assert A._is_due(_FakeAcct(just_now)) is False
    assert A._is_due(_FakeAcct(long_ago)) is True


def test_the_due_check_survives_a_naive_timestamp():
    from app.services import ads_sync as A
    assert A._is_due(_FakeAcct(dt.datetime.utcnow() - dt.timedelta(days=1))) is True


def test_the_interval_is_slower_than_the_general_sync_tick():
    """Ad-level insights cost roughly one request per hundred rows, and rows are ads x days: at
    the 471 ads measured on the live account that is ~33 requests a run before anything else.
    Against a development-tier ceiling near 100 calls an hour, the general half-hourly tick
    cannot fit one complete sync, let alone two."""
    from app.config import settings
    assert settings.ADS_SYNC_INTERVAL_MINUTES > settings.SYNC_INTERVAL_MINUTES


async def test_a_remembered_tier_is_used_when_the_failing_call_cannot_report_one():
    """Meta sends ads_api_access_tier on /campaigns and NOT on /ads. The step that fails is
    therefore the step that cannot name the tier, which is why the first live error message
    hedged - "if it reads development_access" - about a fact already sitting in an earlier
    response from the same sync."""
    from app.integrations import meta_ads as meta
    from app.services import ads_sync as A

    token = meta._TIER.set("development_access")
    try:
        msg = A._explain(meta.MetaError("User request limit reached", code=17))   # no tier on e
        assert "DEVELOPMENT access tier" in msg
    finally:
        meta._TIER.reset(token)


def test_nothing_remembered_means_nothing_asserted():
    """Silence beats a confident wrong answer: claiming development_access when no response ever
    said so sends somebody to change a setting that may already be right."""
    from app.integrations import meta_ads as meta
    from app.services import ads_sync as A

    token = meta._TIER.set(None)
    try:
        msg = A._explain(meta.MetaError("User request limit reached", code=17))
        assert "DEVELOPMENT access tier" not in msg
    finally:
        meta._TIER.reset(token)
