"""Migration 0047 actually runs, and the ads schema is tenant-scoped without exception.

WHY THIS FILE EXISTS AT ALL. 0046 reached production broken twice in one afternoon, and both
faults shared a shape: something only wrong on Postgres, in a suite that only runs SQLite, in a
migration that had never executed in any test because migrations here do not replay from scratch
(0001 does create_all). "Additive and idempotent" was true of 0046 as well; it is not the property
that saves you.

So this exercises the DDL rather than reasoning about it, and checks the two things that were
invisible last time: that the body runs, and that the revision id fits the column alembic stamps
it into.
"""
import sqlalchemy as sa

from app.models import (Ad, AdAccount, AdAttribution, AdCampaign, AdCohortCurve, AdConversion,
                        AdInsightDaily)

ADS_TABLES = ("ad_account", "ad_campaign", "ad", "ad_insight_daily",
              "ad_attribution", "ad_conversion", "ad_cohort_curve")
ADS_MODELS = (AdAccount, AdCampaign, Ad, AdInsightDaily, AdAttribution, AdConversion, AdCohortCurve)


def _migration():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0047_ads_module.py"
    spec = importlib.util.spec_from_file_location("mig0047", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_ads_table_is_tenant_scoped_with_no_exceptions():
    """The whole module is being built under a multi-tenant lens, and the cheapest place for that
    to fail is a table somebody adds later without a tenant_id. One customer's ad spend appearing
    under another's CAC is not a rendering bug, it is a disclosure."""
    for model in ADS_MODELS:
        cols = {c.name for c in model.__table__.columns}
        assert "tenant_id" in cols, f"{model.__tablename__} has no tenant_id"
        fk = next(iter(model.__table__.c.tenant_id.foreign_keys))
        assert fk.column.table.name == "tenant", model.__tablename__
        assert fk.ondelete == "CASCADE", (
            f"{model.__tablename__}.tenant_id must CASCADE - removing a workspace must not "
            f"orphan its ad history")


def test_the_revision_id_fits_the_column_alembic_stamps_it_into():
    """alembic_version.version_num is VARCHAR(32). 0046 shipped at 34 and Postgres refused the
    stamp AFTER the body had run - a migration that worked, failing anyway."""
    mod = _migration()
    assert len(mod.revision) <= 32, f"{mod.revision!r} is {len(mod.revision)} chars"
    assert mod.down_revision == "0046_grandfather_defaults", "chained from the wrong head"


def test_the_migration_body_actually_creates_its_tables():
    """0047's upgrade() had never executed anywhere before this test, exactly as 0046's had not.

    Built by create_all so the FK targets (tenant, integration, business) exist, then the ads
    tables are dropped and the migration is asked to put them back - which exercises the create
    path with real foreign keys rather than asserting the file parses.
    """
    from app.models import Base

    mod = _migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        Base.metadata.create_all(conn)
        for t in reversed(ADS_TABLES):
            conn.execute(sa.text(f"DROP TABLE IF EXISTS {t}"))
        before = set(sa.inspect(conn).get_table_names())
        assert not (before & set(ADS_TABLES)), "setup failed to drop the ads tables"

        # Bind alembic's `op` proxy to this connection the way a real migration run does,
        # rather than patching its functions - a hand-rolled create_table cannot resolve
        # foreign keys, and a test that fakes the machinery does not exercise the DDL.
        with _bound_op(conn):
            mod.upgrade()

        after = set(sa.inspect(conn).get_table_names())
    missing = set(ADS_TABLES) - after
    assert not missing, f"the migration did not create: {sorted(missing)}"


def test_running_it_twice_is_a_no_op():
    """A failed deploy gets retried. Every create is guarded, so the retry must find the tables
    already there and do nothing rather than erroring on a duplicate."""
    from app.models import Base

    mod = _migration()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        Base.metadata.create_all(conn)          # tables already present: every guard should skip
        with _bound_op(conn):
            mod.upgrade()
            mod.upgrade()                        # must not raise
        assert set(ADS_TABLES) <= set(sa.inspect(conn).get_table_names())


def test_the_insight_day_is_unique_so_restatement_is_an_update():
    """Meta revises recent days as attribution settles. Without this constraint a re-pull would
    INSERT a second row for the same day and every total would drift upward on every sync - the
    spec's rule is that if you find yourself writing delete-then-insert, the constraint is wrong."""
    uq = next((c for c in AdInsightDaily.__table__.constraints
               if c.name == "uq_ad_insight_day"), None)
    assert uq is not None, "the day constraint is gone - restatement would insert duplicates"
    # tenant_id FIRST: the same Meta day for two workspaces is two rows, never a collision.
    assert [c.name for c in uq.columns] == [
        "tenant_id", "ad_account_id", "level", "object_external_id", "occurred_on"]


def test_no_ratio_is_stored_on_an_insight_row():
    """The most important correctness rule in the module, enforced by ABSENCE. A mean of daily
    CTRs is not the period CTR; the surest way nobody averages one is for there to be no rate in
    the table to average. Meta returns ctr, cpm and cpc and none of them are kept."""
    cols = {c.name for c in AdInsightDaily.__table__.columns}
    for banned in ("ctr", "cpm", "cpc", "cost_per_lead", "cpl"):
        assert banned not in cols, f"{banned} is stored - every rate must be derived at read time"


def test_attribution_can_hold_an_ad_only_when_it_is_entitled_to():
    """Schema-level half of Part 4.4. ad_id is nullable precisely so a campaign-grade match can
    leave it empty; the read service must never infer an ad from a campaign match."""
    assert AdAttribution.__table__.c.ad_id.nullable
    assert AdAttribution.__table__.c.campaign_id.nullable
    assert not AdAttribution.__table__.c.match_method.nullable, (
        "match_method is the grain a row is allowed to grant revenue at and must always be set")


def _bound_op(conn):
    """Bind alembic's module-level `op` proxy to a live connection.

    This is how alembic itself wires a migration, so the body runs against the real Operations
    implementation: foreign keys resolve, server defaults render, and the DDL emitted is the DDL
    a deploy would emit. Patching op's functions instead would test the patch."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    return Operations.context(MigrationContext.configure(conn))
