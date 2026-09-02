"""The startup guard, and specifically the ways a guard like this fails open.

A check that refuses insecure config is easy. A check that is still running two years later,
in the deployment that actually needs it, is the hard part — so most of these are about the
guard's own failure modes rather than about the secrets.
"""
import pytest

from app import startup_checks as SC
from app.config import Settings, settings

REAL_SECRET = "s" * 64
REAL_FERNET = "aG7bQ2xK9pL4vN8mR1tY6wZ3cD5fH0jS7kU2nP4qX8E="   # valid 32-byte urlsafe b64


@pytest.fixture
def cfg(monkeypatch):
    """Drive the module-level settings singleton without touching the real environment."""
    def _set(**kw):
        for k, v in kw.items():
            monkeypatch.setattr(settings, k, v, raising=False)
        monkeypatch.delenv("ALLOW_INSECURE_SECRETS", raising=False)
    return _set


def _postgres(cfg, **kw):
    base = dict(DATABASE_URL="postgresql+asyncpg://u:p@db.example.com:5432/app",
                APP_SECRET=REAL_SECRET, FERNET_KEY=REAL_FERNET, ENV="production")
    base.update(kw)                      # callers override individual fields
    cfg(**base)


def test_a_deployment_on_committed_secrets_refuses_to_start(cfg):
    _postgres(cfg, APP_SECRET=Settings.model_fields["APP_SECRET"].default)
    with pytest.raises(RuntimeError) as ei:
        SC.enforce_config(log=lambda *_: None)
    assert "APP_SECRET" in str(ei.value)


def test_enforcement_does_not_depend_on_ENV_being_set(cfg):
    """THE POINT OF THE WHOLE FILE.

    ENV defaults to "development". An ENV-gated check is therefore skipped by exactly the
    mistake it exists to catch: a deployment where nobody set the variables. Whoever forgot
    APP_SECRET plausibly forgot ENV too, and then the guard politely stands down.

    So enforcement keys on talking to a real database, which nobody forgets — the app cannot
    run without it.
    """
    _postgres(cfg, ENV="development", FERNET_KEY=Settings.model_fields["FERNET_KEY"].default)
    assert SC.is_deployed() is True
    with pytest.raises(RuntimeError):
        SC.enforce_config(log=lambda *_: None)


def test_local_sqlite_development_keeps_its_convenient_defaults(cfg):
    cfg(DATABASE_URL="sqlite+aiosqlite:///./command_center.db", ENV="development",
        APP_SECRET=Settings.model_fields["APP_SECRET"].default,
        FERNET_KEY=Settings.model_fields["FERNET_KEY"].default)
    said = []
    SC.enforce_config(log=said.append)                 # must not raise
    assert any("APP_SECRET" in m for m in said), "should still say so, just not fatally"


def test_real_secrets_start_cleanly(cfg):
    _postgres(cfg)
    fatal, _ = SC.validate_config()
    assert fatal == []
    SC.enforce_config(log=lambda *_: None)


def test_a_malformed_fernet_key_is_caught_at_boot_not_at_first_use(cfg):
    """Otherwise it surfaces the first time somebody connects an integration, days later and
    nowhere near the cause."""
    _postgres(cfg, FERNET_KEY="not-a-real-fernet-key")
    fatal, _ = SC.validate_config()
    assert any("not a usable Fernet key" in f for f in fatal)


def test_the_opt_out_works_and_is_the_only_thing_that_bypasses(cfg, monkeypatch):
    _postgres(cfg, APP_SECRET=Settings.model_fields["APP_SECRET"].default)
    monkeypatch.setenv("ALLOW_INSECURE_SECRETS", "true")
    SC.enforce_config(log=lambda *_: None)             # must not raise
    monkeypatch.setenv("ALLOW_INSECURE_SECRETS", "no")
    with pytest.raises(RuntimeError):
        SC.enforce_config(log=lambda *_: None)


def test_the_default_is_read_from_Settings_so_it_cannot_go_stale(cfg):
    """If the check held its own copy of the default string, editing config.py would leave the
    check comparing against a value nobody uses — and it would pass on the very secret it
    exists to reject. It reads the model instead, so a changed default is still caught."""
    assert SC._committed_default("APP_SECRET") == Settings.model_fields["APP_SECRET"].default
    _postgres(cfg, APP_SECRET=SC._committed_default("APP_SECRET"))
    fatal, _ = SC.validate_config()
    assert any("still the value committed" in f for f in fatal)


def test_an_empty_secret_is_as_fatal_as_a_default_one(cfg):
    _postgres(cfg, APP_SECRET="   ")
    fatal, _ = SC.validate_config()
    assert any("APP_SECRET is not set" in f for f in fatal)


def test_a_short_secret_warns_but_does_not_take_a_deployment_down(cfg):
    """Weak, but a judgement call rather than a definite fault — and the cost of being wrong
    is a running deployment refusing to boot."""
    _postgres(cfg, APP_SECRET="short-but-real")
    fatal, warn = SC.validate_config()
    assert fatal == []
    assert any("characters" in w for w in warn)
    SC.enforce_config(log=lambda *_: None)


def test_the_dev_fixture_refuses_to_seed_a_real_database(cfg):
    """app.seed is a development fixture and DEPLOY.md named it as the production bootstrap.

    Two consequences, both live: it is idempotent by WIPING, so a second run deletes the
    workspace of that slug and everything in it; and it creates the owner — the highest-privilege
    account in a workspace — with a nine-character dictionary word that is in this repository, in
    the runbook, and in every clone. Nothing forced a rotation and nothing recorded one.

    The runbook is fixed, but a warning in a document is not a control.
    """
    import os

    import pytest as _pytest

    from app import seed as S

    # On a laptop it is unchanged: dozens of tests sign in with this constant.
    cfg(DATABASE_URL="sqlite+aiosqlite:///./command_center.db", ENV="development")
    assert S._seed_credentials() == S.OWNER_PASSWORD

    # Against a real database it refuses outright.
    _postgres(cfg)
    os.environ.pop("SEED_ALLOW_DEPLOYED", None)
    with _pytest.raises(SystemExit) as ei:
        S._seed_credentials()
    assert "refused" in str(ei.value)
    assert "create_tenant" in str(ei.value), "the refusal must name the supported path"


def test_forcing_the_fixture_onto_a_real_database_still_needs_a_password_of_your_own(cfg, monkeypatch):
    """The escape hatch exists, and it does not hand back the published password."""
    import pytest as _pytest

    from app import seed as S

    _postgres(cfg)
    monkeypatch.setenv("SEED_ALLOW_DEPLOYED", "true")
    monkeypatch.delenv("SEED_OWNER_PASSWORD", raising=False)
    with _pytest.raises(SystemExit) as ei:
        S._seed_credentials()
    assert "SEED_OWNER_PASSWORD" in str(ei.value)

    monkeypatch.setenv("SEED_OWNER_PASSWORD", "short")
    with _pytest.raises(SystemExit):
        S._seed_credentials()

    monkeypatch.setenv("SEED_OWNER_PASSWORD", "a-real-password-chosen-here")
    assert S._seed_credentials() == "a-real-password-chosen-here"


def test_the_single_tenant_fallback_exemption_cannot_follow_a_build_into_production(cfg):
    """Its docstring claimed the ENV key meant it "cannot follow a build into prod". ENV defaults
    to "development", so a deployment where nobody set it was indistinguishable from a laptop —
    and the tenant-count gate the docstring calls the real gate was never reached. Whoever forgets
    ENV is precisely who needed it."""
    from app import startup_checks as SC

    _postgres(cfg, ENV="development")
    assert SC.is_deployed() is True, (
        "a Postgres deployment with ENV unset must still count as deployed, or the fallback "
        "serves one customer's dashboard to every unrecognised host")
