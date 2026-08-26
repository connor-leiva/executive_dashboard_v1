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
