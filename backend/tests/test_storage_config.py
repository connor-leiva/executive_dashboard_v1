"""A deployment without object storage must refuse to boot, not lose files quietly.

Without the four R2_* settings, binder_storage falls back to a directory under the system temp
dir. On a laptop that is right. On a deployment it means every SOP document, workspace logo and
marketing attachment is written to a container filesystem that is discarded on the next deploy --
and silently: the upload succeeds, the row points at a key, and the bytes are simply gone
afterwards. Nobody finds out until somebody opens a document that is not there.

The keys were also documented nowhere, so a second environment would have hit this by default.

Fatal rather than a warning, for the same reason APP_SECRET is: a process that cannot keep the
files it accepts should not accept them.
"""
import pytest

from app.config import settings
from app.startup_checks import enforce_config, validate_config

R2 = ("R2_ACCOUNT_ID", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")


def _storage_faults(fatal: list[str]) -> list[str]:
    return [f for f in fatal if "storage" in f.lower()]


def _set_r2(monkeypatch, **values):
    for name in R2:
        monkeypatch.setattr(settings, name, values.get(name, ""), raising=False)


def test_no_storage_configured_is_fatal(monkeypatch):
    _set_r2(monkeypatch)
    faults = _storage_faults(validate_config()[0])
    assert faults, "a deployment with no object storage was allowed"
    # The message has to say what is actually at stake, or it reads as pedantry and gets bypassed.
    assert "lost on the next deploy" in faults[0]


def test_half_configured_storage_is_fatal_and_names_what_is_missing(monkeypatch):
    """Whoever set two of these meant to use R2, and the fallback will not tell them otherwise."""
    _set_r2(monkeypatch, R2_ACCOUNT_ID="acct", R2_BUCKET="bucket")
    faults = _storage_faults(validate_config()[0])
    assert faults
    assert "R2_ACCESS_KEY_ID" in faults[0] and "R2_SECRET_ACCESS_KEY" in faults[0]
    assert "R2_ACCOUNT_ID" not in faults[0], "it named a setting that was present"


def test_fully_configured_storage_raises_nothing(monkeypatch):
    _set_r2(monkeypatch, R2_ACCOUNT_ID="acct", R2_BUCKET="bucket",
            R2_ACCESS_KEY_ID="key", R2_SECRET_ACCESS_KEY="secret")
    assert not _storage_faults(validate_config()[0])


def test_a_laptop_is_warned_and_still_starts(monkeypatch):
    """The fallback is correct in development, so this must not stop anyone working. enforce_config
    downgrades every fatal to a warning when the process is not a real deployment -- the property
    that keeps this guard from being the thing everybody disables."""
    _set_r2(monkeypatch)
    monkeypatch.setattr("app.startup_checks.is_deployed", lambda: False)
    logged: list[str] = []
    enforce_config(log=logged.append)          # must not raise
    assert any("storage" in line.lower() for line in logged)


def test_a_deployment_without_storage_actually_refuses(monkeypatch):
    _set_r2(monkeypatch)
    monkeypatch.setattr("app.startup_checks.is_deployed", lambda: True)
    monkeypatch.setattr("app.startup_checks._opted_out", lambda: False)
    with pytest.raises(RuntimeError, match="Refusing to start"):
        enforce_config(log=lambda _: None)


def test_the_keys_are_documented(monkeypatch):
    """They appeared in no .env.example, which is how a second environment inherits the bug."""
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / ".env.example"
    assert example.exists()
    text = example.read_text(encoding="utf-8")
    for name in R2:
        assert name in text, f"{name} is not documented in .env.example"
