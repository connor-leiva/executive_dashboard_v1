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


# ── plans ────────────────────────────────────────────────────────────────────────────────
def test_a_plan_gates_the_platform_modules_but_never_a_workspaces_own_entities():
    """A plan limits how many businesses a workspace may HAVE. It does not limit whether it may
    look at the ones it has — those are its own entities and its own numbers. Only the platform
    modules are gated."""
    from app import plans

    class _T:
        def __init__(self, plan): self.plan = plan

    nav = ["portfolio", "ulrg", "forum", "flywheel", "books", "binder", "ai_employees"]
    assert plans.plan_tabs(_T("team"), nav) == ["portfolio", "ulrg", "forum"]
    assert plans.plan_tabs(_T("business"), nav) == [
        "portfolio", "ulrg", "forum", "flywheel", "books", "ai_employees"]
    assert plans.plan_tabs(_T("portfolio"), nav) == nav
    # Its own business and programme tabs survive on every plan.
    for plan in plans.ORDER:
        got = plans.plan_tabs(_T(plan), nav)
        assert "ulrg" in got and "forum" in got and "portfolio" in got, plan


def test_an_unknown_or_missing_plan_defaults_generously():
    """Guessing low takes a tab away from somebody who is paying; guessing high costs revenue that
    was not being collected and shows up in the operator console rather than a support ticket."""
    from app import plans

    class _T:
        plan = None

    assert plans.plan_of(_T()) == plans.PORTFOLIO
    assert plans.plan_of(object()) == plans.PORTFOLIO

    class _Bogus:
        plan = "enterprise-plus"

    assert plans.plan_of(_Bogus()) == plans.PORTFOLIO


def test_the_plan_is_applied_before_a_members_grants():
    """A member carrying a Binder grant from when their workspace was on Portfolio must not keep
    reaching Binder after it moves down. A stale grant is not an entitlement, and a downgrade that
    leaves a door open is not a downgrade."""
    from app.services.tabs import effective_tabs

    class _T:
        plan = "team"

    class _U:
        role = "member"
        tab_access = ["portfolio", "binder", "books"]

    assert effective_tabs(_U(), ["portfolio", "binder", "books"], tenant=_T()) == ["portfolio"]

    class _O(_U):
        role = "owner"

    # ...and an OWNER does not escape it either. The plan is the workspace's, not the user's.
    assert effective_tabs(_O(), ["portfolio", "binder", "books"], tenant=_T()) == ["portfolio"]


def test_sources_are_listed_not_counted():
    """A count would invite a workspace to disconnect QuickBooks to stay under a limit, and a
    pricing rule that encourages somebody to make their own numbers wrong is a bug in the pricing."""
    from app import plans

    class _T:
        def __init__(self, plan): self.plan = plan

    assert plans.allows_source(_T("team"), "sisu")
    assert not plans.allows_source(_T("team"), "qbo")
    assert plans.allows_source(_T("business"), "qbo")
    assert plans.allows_source(_T("portfolio"), "arive")


def test_every_plan_is_complete_so_a_gate_cannot_read_a_missing_key():
    """A limit that is absent reads as None, and None means unlimited everywhere in plans.py. A
    typo in a key name would therefore hand out an unlimited allowance silently."""
    from app import plans

    required = {"name", "price_monthly", "max_businesses", "max_users", "sources", "extra_tabs",
                "max_share_links", "history_months", "custom_branding", "max_ai_employees"}
    for key, plan in plans.PLANS.items():
        assert set(plan) == required, f"{key} is missing or has extra: {set(plan) ^ required}"
    # Prices ascend with the tier, which is the one relationship a reader will assume.
    prices = [plans.PLANS[p]["price_monthly"] for p in plans.ORDER]
    assert prices == sorted(prices) and len(set(prices)) == len(prices), prices


async def test_the_gates_count_the_thing_the_limit_actually_means():
    """Each cap counts a deliberately chosen population, and the choice matters more than the
    number:

      * users counts INVITED as well as active — an invitation is a seat somebody is expected to
        take, and a cap that only counts accepted users is a cap you get around by never
        accepting one.
      * share links counts only LIVE ones. A revoked link occupies nothing, and making somebody
        delete their history to mint a new share would be a limit that punishes tidiness.
    """
    from app import plans

    class _T:
        plan = "team"

    t = _T()
    # Team includes 5 users: the fifth is allowed, the sixth is not.
    assert not plans.over_limit(t, "max_users", 4)
    assert plans.over_limit(t, "max_users", 5)
    # 3 share links, same boundary.
    assert not plans.over_limit(t, "max_share_links", 2)
    assert plans.over_limit(t, "max_share_links", 3)
    # Team has no AI employees at all, so the first one is already over.
    assert plans.over_limit(t, "max_ai_employees", 0)

    class _P:
        plan = "portfolio"

    # Unlimited means unlimited, not a large number.
    for key in ("max_users", "max_share_links", "max_ai_employees", "max_businesses"):
        assert not plans.over_limit(_P(), key, 10_000), key


def test_the_history_window_is_a_reach_limit_not_a_deletion():
    """History bounds how far the DASHBOARD looks back. Nothing is deleted and nothing is hidden
    from an export — a plan limit that destroys a customer's data is not a plan limit."""
    import datetime as dt

    from app import plans

    class _T:
        def __init__(self, plan): self.plan = plan

    today = dt.date(2026, 9, 2)
    assert plans.history_start(_T("team"), today) == dt.date(2025, 9, 1)      # 12 months
    assert plans.history_start(_T("business"), today) == dt.date(2023, 9, 1)  # 36 months
    assert plans.history_start(_T("portfolio"), today) is None               # unlimited

    assert plans.within_history(_T("team"), dt.date(2026, 1, 1), today)
    assert not plans.within_history(_T("team"), dt.date(2024, 1, 1), today)
    assert plans.within_history(_T("portfolio"), dt.date(2001, 1, 1), today)


# ── appearance ───────────────────────────────────────────────────────────────────────────
def test_five_seeds_derive_thirty_tokens_that_all_read():
    """The five-knob decision only holds if the derivation reaches everything and stays legible.
    Acumyn's own seeds are the case that must never fail — they are what every workspace starts
    from, so a contrast failure there ships to everybody who has not chosen colours."""
    import json
    import re
    import subprocess
    from pathlib import Path

    pal = Path(__file__).resolve().parents[2] / "frontend" / "src" / "palette.js"
    script = rf"""
      const fs = require('fs');
      let src = fs.readFileSync({json.dumps(str(pal))}, 'utf8')
        .replace(/^import .*$/gm, '')
        .replace(/^export /gm, '')
        // the module applies itself at load and calls into typefaces.js; neither is what this
        // test is about, so both are stubbed out rather than pulled in.
        .replace(/^applyPalette\(.*$|^applyType\(.*$|^loadTypeface\(.*$/gm, '');
      const loadTypeface = () => {{}};
      const stacks = () => ({{}});
      eval(src);
      const t = derive(seedsFromAcumyn());
      console.log(JSON.stringify({{
        count: Object.keys(t).length,
        defaults: contrastProblems(seedsFromAcumyn()),
        garish: contrastProblems({{brand:'#FFEB3B',surface:'#FFFFFF',ink:'#CCCCCC',
                                   positive:'#AAFFAA',negative:'#FFDDDD'}}).length,
        warningFixed: t.daffodil,
      }}));
    """
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)

    assert got["count"] == 30, "the derivation must cover every token the app reads"
    assert got["defaults"] == [], f"Acumyn's own seeds fail contrast: {got['defaults']}"
    assert got["garish"] >= 5, "an unreadable palette must be caught, not merely disliked"
    # Warning is not a seed: it stays the specified value whatever else a workspace picks.
    assert got["warningFixed"].upper() == "#7E5A1C"


def test_the_typeface_list_agrees_across_the_two_languages():
    """The pairings are defined in the browser and validated on the server, so the two lists can
    drift — and the failure is asymmetric. A name the frontend offers and the backend rejects is a
    save button that does nothing; a name the backend accepts and the frontend does not know
    resolves to the default with no error anywhere.

    Only a NAME crosses the wire, never a font stack: a workspace supplying its own font-family
    string would be injecting CSS into every page it renders.
    """
    import re
    from pathlib import Path

    from app.routers.users import TYPEFACES

    js = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "typefaces.js").read_text(
        encoding="utf-8")
    block = js[js.index("export const PAIRINGS"):]
    block = block[: block.index("\n};")]
    names = set(re.findall(r"^  (\w+): \{", block, re.M))

    assert names == set(TYPEFACES), (
        f"frontend offers {sorted(names)}, backend accepts {sorted(TYPEFACES)}")

    # Every pairing sets figures in the same face; that is the one thing a workspace cannot change.
    assert "DATA_STACK" in js and "Archivo" in js
    assert not re.search(r"^  \w+: \{[^}]*\bdata:", block, re.M | re.S), (
        "a pairing declared its own data face — tabular figures are not a choice")


def test_an_uploaded_mark_names_the_workspace_but_never_the_object():
    """The property worth pinning is narrower than it first looks.

    The route USED to resolve the workspace from X-Tenant-Host, which sounded stricter and was
    simply broken: a CSS mask or <img> sends no custom headers, so the API saw only the platform
    host and returned 404 to every mark. Verifying it with a curl that did send the header made
    it look correct — the header made the test pass and made the feature impossible.

    So the caller names the WORKSPACE, which it already did by typing the subdomain. What it must
    never name is the OBJECT: the storage ref is read out of that workspace's own config, so
    there is no key to guess and no path to traverse.
    """
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app" / "routers" / "auth.py").read_text(
        encoding="utf-8")
    head = src.index('@router.get("/public/brand/{slug}/{kind}")')
    route = src[head: src.index("@router.", head + 20)]

    assert "Tenant.slug == slug" in route, "the workspace must come from the path"
    assert "current_tenant_id" not in route, (
        "back to a header the browser cannot send for an image request")
    assert "_ref" in route and "config" in route, "the ref must come from that workspace's config"
    assert 'if kind not in ("logo", "logomark")' in route, "kind must be a closed set"
