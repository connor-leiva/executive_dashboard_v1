"""Refuse to start on a configuration that is silently insecure.

Settings carry working defaults so the app runs locally with no setup. That is a real
convenience and worth keeping — but it means a missing environment variable in production does
not fail, it *succeeds quietly* with a value that is published in the repository. `/health`
returns ok, the dashboard loads, nothing looks wrong, and:

  - APP_SECRET signs every session token. On the committed default, anyone who can read the
    source can mint a valid session for any user in any workspace. There is no attack to
    mount; you just issue yourself a token.
  - FERNET_KEY encrypts every stored integration credential — Sisu, FUB, QuickBooks, Arive,
    Recall. On the committed default, "encrypted at rest" means encrypted with a key that is
    in the git history.

A missing secret is not a smaller version of a set secret. It is the absence of the control,
and the only safe response is to not start.

WHEN THIS ENFORCES. Not gated on ENV alone: ENV defaults to "development", so an ENV-gated
check is skipped by exactly the mistake it exists to catch — a deployment where the variables
were never set. It enforces whenever the app is talking to a real database, or ENV names a
deployed environment. Local SQLite development keeps its defaults and gets warnings instead.

ALLOW_INSECURE_SECRETS=true opts out, for a developer running Postgres locally. It is
deliberately ugly to type and easy to grep for.
"""
from __future__ import annotations

import os

from .config import Settings, settings

DEPLOYED_ENVS = {"production", "prod", "staging"}
MIN_SECRET_LEN = 32

_GENERATE = {
    "APP_SECRET": 'python -c "import secrets; print(secrets.token_urlsafe(48))"',
    "FERNET_KEY": 'python -c "from cryptography.fernet import Fernet; '
                  'print(Fernet.generate_key().decode())"',
}


def _committed_default(name: str):
    """The default declared on Settings, read from the model rather than copied.

    Duplicating the literal here would let the two drift, and the copy that goes stale is the
    check — which then passes on the very value it exists to reject.
    """
    return Settings.model_fields[name].default


def _opted_out() -> bool:
    return os.environ.get("ALLOW_INSECURE_SECRETS", "").strip().lower() in {"1", "true", "yes"}


def is_deployed() -> bool:
    """Whether this process is running somewhere that a real customer can reach.

    A Postgres database is the more trustworthy signal, because it is not something anyone
    forgets to configure — the app cannot run without it. ENV is checked too, for a staging
    deployment that happens to sit on SQLite.
    """
    return (not settings.is_sqlite) or settings.ENV.strip().lower() in DEPLOYED_ENVS


def validate_config() -> tuple[list[str], list[str]]:
    """Inspect the running configuration. Returns (fatal, warnings); never raises.

    Split so the CLI can report everything at once. A check that stops at the first problem
    turns one misconfigured deploy into four rounds of fix-and-retry.
    """
    fatal: list[str] = []
    warn: list[str] = []

    for name in ("APP_SECRET", "FERNET_KEY"):
        value = (getattr(settings, name, "") or "").strip()
        if not value:
            fatal.append(f"{name} is not set. Generate one with:\n      {_GENERATE[name]}")
        elif value == _committed_default(name):
            fatal.append(
                f"{name} is still the value committed to the repository, so it is not a secret "
                f"at all. Generate one with:\n      {_GENERATE[name]}")

    # A malformed key fails at first use — the first time somebody connects an integration,
    # far from the cause. Cheap to catch here, and it cannot false-positive on a valid key.
    fernet = (settings.FERNET_KEY or "").strip()
    if fernet and fernet != _committed_default("FERNET_KEY"):
        try:
            from cryptography.fernet import Fernet
            Fernet(fernet.encode())
        except Exception as exc:
            fatal.append(f"FERNET_KEY is set but is not a usable Fernet key ({exc}). It must be "
                         f"32 url-safe base64 bytes. Generate one with:\n      "
                         f"{_GENERATE['FERNET_KEY']}")

    secret = (settings.APP_SECRET or "").strip()
    if secret and secret != _committed_default("APP_SECRET") and len(secret) < MIN_SECRET_LEN:
        # A warning, not fatal: a short secret is weak, but refusing to boot over length would
        # take a running deployment down over a judgement call rather than a definite fault.
        warn.append(f"APP_SECRET is only {len(secret)} characters. {MIN_SECRET_LEN}+ recommended;"
                    f" rotating it signs out every user, so do it deliberately.")

    # OBJECT STORAGE. Without the four R2 settings, binder_storage falls back to a directory
    # under the system temp dir -- correct on a laptop, and on a deployment it means every SOP
    # document, workspace logo and marketing attachment is written to a container filesystem that
    # is thrown away on the next deploy. Silently: uploads succeed, the rows point at keys, and
    # the bytes are simply gone afterwards. That is worse than refusing to boot, which is why
    # this is fatal rather than a warning.
    r2 = {name: (getattr(settings, name, "") or "").strip()
          for name in ("R2_ACCOUNT_ID", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")}
    missing = sorted(name for name, value in r2.items() if not value)
    if missing and len(missing) < len(r2):
        # Half-configured is a definite fault rather than a choice: whoever set two of these
        # meant to use R2, and the fallback will not tell them they are not.
        fatal.append(
            f"Object storage is half configured: {', '.join(missing)} not set. Uploaded "
            f"documents, logos and attachments would go to a container temp directory and be "
            f"lost on the next deploy.")
    elif missing:
        fatal.append(
            "Object storage is not configured (R2_ACCOUNT_ID, R2_BUCKET, R2_ACCESS_KEY_ID, "
            "R2_SECRET_ACCESS_KEY). Uploaded SOP documents, workspace logos and marketing "
            "attachments would be written to a container temp directory and lost on the next "
            "deploy, silently — the upload succeeds and the bytes disappear.")

    if not settings.is_sqlite and settings.ENV.strip().lower() not in DEPLOYED_ENVS:
        warn.append(f"ENV is {settings.ENV!r} while running on Postgres. Anything keyed to ENV "
                    f"- the single-tenant fallback exemption among them - will behave as if "
                    f"this were a developer's laptop.")

    return fatal, warn


def enforce_config(log=print) -> None:
    """Called at startup. Raises RuntimeError rather than letting an insecure process serve."""
    fatal, warn = validate_config()

    for w in warn:
        log(f"[config warning] {w}")

    if not fatal:
        return
    if not is_deployed() or _opted_out():
        for f in fatal:
            log(f"[config warning: development] {f.splitlines()[0]}")
        return

    lines = "\n".join(f"  - {f}" for f in fatal)
    raise RuntimeError(
        "Refusing to start: this deployment is reachable but its secrets are not set.\n"
        f"{lines}\n"
        "  Set these on the api service and redeploy. To check a deployment before shipping "
        "this guard, run:  python -m scripts.check_config\n"
        "  (ALLOW_INSECURE_SECRETS=true bypasses this. Only ever for local development.)"
    )
