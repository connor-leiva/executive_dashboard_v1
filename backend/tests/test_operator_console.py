"""The operator console at admin.acumyn.io, built from OPERATOR-CONSOLE-SPEC.md.

test_platform_operators.py holds the original wall between the two realms. This file holds what
the console added on top of it, phase by phase, and the tests the spec calls load-bearing: the
realms stay unusable by each other's tokens, nothing an operator reads is a tenant's numbers, and
every limit the console shows is the one the product enforces.
"""
import re
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app import plans, throttle
from app.db import SessionLocal
from app.main import app
from app.models import PlatformUser
from app.security import hash_pw, make_capability
from app.seed import seed

TRANSPORT = ASGITransport(app=app)
OP_EMAIL = "console-operator@platform.test"
OP_PASSWORD = "console-operator-password-1"
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
OPERATOR_SRC = FRONTEND / "src" / "operator"


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()
    async with SessionLocal() as s:
        op = (await s.execute(select(PlatformUser).where(
            PlatformUser.email == OP_EMAIL))).scalar_one_or_none()
        if op is None:
            s.add(PlatformUser(email=OP_EMAIL, name="Console Operator",
                               password_hash=hash_pw(OP_PASSWORD)))
            await s.commit()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token, host=None):
    h = {"Authorization": f"Bearer {token}"}
    if host:
        h["x-tenant-host"] = host
    return h


async def _op():
    async with SessionLocal() as s:
        return (await s.execute(select(PlatformUser).where(
            PlatformUser.email == OP_EMAIL))).scalar_one()


async def _op_token():
    async with _client() as c:
        r = await c.post("/api/v1/platform/login",
                         json={"email": OP_EMAIL, "password": OP_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


# ── phase 1: the realm, and the plan table the console reads ──────────────────────────────
async def test_a_capability_token_is_not_an_operator_session():
    """make_capability accepts arbitrary claims, so a one-shot token minted with a `pu` claim
    carried everything current_platform_user looked for. current_user refused `cap` tokens after
    the QBO state token authenticated as an owner; the operator realm had not caught up."""
    op = await _op()
    forged = make_capability("anything", minutes=5, pu=str(op.id), ver=0)
    async with _client() as c:
        r = await c.get("/api/v1/platform/me", headers=_H(forged))
    assert r.status_code == 401, r.text

    async with _client() as c:                      # the control: a real session works
        r = await c.get("/api/v1/platform/me", headers=_H(await _op_token()))
    assert r.status_code == 200


async def test_the_operator_login_budget_belongs_to_the_address_not_the_host():
    """The bucket keyed on X-Tenant-Host, which the caller writes. A new invented host per
    request was a fresh budget per request, on the most valuable login on the platform."""
    throttle.reset()
    codes = []
    async with _client() as c:
        for i in range(12):
            r = await c.post("/api/v1/platform/login",
                             headers={"x-tenant-host": f"spray-{i}.example.test"},
                             json={"email": "nobody@platform.test", "password": "wrong"})
            codes.append(r.status_code)
    throttle.reset()
    assert codes[:10] == [401] * 10, codes
    assert 429 in codes[10:], f"a rotating host kept buying new attempts: {codes}"


async def test_the_console_reads_plan_limits_from_the_table_the_product_enforces():
    """The design carried its own copy of every limit, and the copy drifted during its own audit.
    The console now reads this endpoint, so this asserts the endpoint IS plans.PLANS."""
    async with _client() as c:
        r = await c.get("/api/v1/platform/plans", headers=_H(await _op_token()))
    assert r.status_code == 200, r.text
    body = r.json()
    assert [p["key"] for p in body["plans"]] == list(plans.ORDER)
    for served in body["plans"]:
        source = plans.PLANS[served["key"]]
        for field in ("name", "price_monthly", "max_businesses", "max_users", "max_share_links",
                      "history_months", "max_ai_employees", "intranet", "ai_assistant"):
            assert served[field] == source[field], (served["key"], field)
        assert set(served["extra_tabs"]) == source["extra_tabs"], served["key"]
    assert {g["key"] for g in body["gated_tabs"]} == plans._PORTFOLIO_TABS
    for g in body["gated_tabs"]:
        assert g["key"] in plans.PLANS[g["lowest_tier"]]["extra_tabs"]
    assert body["default_plan"] == "portfolio"


def test_plan_of_defaults_every_unusable_value_to_the_most_generous_tier():
    class _T:
        def __init__(self, plan):
            self.plan = plan

    for value in (None, "", "nonsense", "  "):
        assert plans.plan_of(_T(value)) == "portfolio", repr(value)
    assert plans.plan_of(_T(" Team ")) == "team"


async def test_every_workspace_row_says_whether_its_plan_was_actually_set():
    """plan_of() quietly hands an unset plan the most generous tier. The console must be able to
    show that rather than leave an operator to discover it in a ticket."""
    from app.models import Tenant

    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        before = t.plan
        t.plan = "not-a-real-tier"
        await s.commit()
    try:
        async with _client() as c:
            r = await c.get("/api/v1/platform/tenants", headers=_H(await _op_token()))
        row = {t["slug"]: t for t in r.json()["tenants"]}["springb"]
        assert row["plan"] == "portfolio" and row["plan_set"] is False
        assert row["plan_stored"] == "not-a-real-tier"
    finally:
        async with SessionLocal() as s:
            t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
            t.plan = before
            await s.commit()


# ── phase 1: the frontend's structural rules (the frontend has no test runner) ─────────────
def _operator_files():
    return sorted(p for p in OPERATOR_SRC.rglob("*") if p.suffix in (".js", ".jsx"))


def _code(path):
    """Source with comments removed, so the reasons written above a rule cannot trip it."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("//"))


@pytest.mark.skipif(not OPERATOR_SRC.exists(), reason="frontend not present")
def test_the_operator_module_takes_colour_only_from_the_platform_brand():
    """acumyn.jsx IS the brand as code. A hex literal in the operator module is a second copy of
    it, and the spec allows exactly one exception: the functional amber and red, which the brand
    guide does not define."""
    allowed = {"#855C00", "#F7EFDA", "#E8D6AC", "#B3261E", "#F8E7E4", "#F0CFCB", "#FFFFFF"}
    offenders = {}
    for path in _operator_files():
        found = {h.upper() for h in re.findall(r"#[0-9A-Fa-f]{6}\b", _code(path))} - allowed
        if found:
            offenders[path.name] = sorted(found)
    assert not offenders, f"hex colours outside the brand module: {offenders}"


@pytest.mark.skipif(not OPERATOR_SRC.exists(), reason="frontend not present")
def test_the_operator_module_never_imports_a_tenants_theming():
    """palette.js, theme.js and Brand.jsx resolve against a workspace. The operator realm has no
    workspace, so each would render against nothing."""
    offenders = {}
    for path in _operator_files():
        hits = re.findall(r"from\s+[\"']([^\"']*(?:palette|theme|Brand)(?:\.jsx?)?)[\"']",
                          _code(path))
        if hits:
            offenders[path.name] = hits
    assert not offenders, f"tenant theming imported by the operator console: {offenders}"


@pytest.mark.skipif(not OPERATOR_SRC.exists(), reason="frontend not present")
def test_the_operator_client_never_names_a_tenant_realm():
    """X-Tenant-Host selects a tenant. The operator realm has none, and admin.acumyn.io resolves
    to 404 by design, so a client that sent it would be asking for a realm it can never have."""
    for path in _operator_files():
        assert "x-tenant-host" not in _code(path).lower(), path.name


@pytest.mark.skipif(not OPERATOR_SRC.exists(), reason="frontend not present")
def test_the_mark_keeps_its_gap_at_top_dead_centre():
    """acumyn.jsx exports inGap because this is the one property of the mark that arithmetic
    cannot confirm: without the SVG Y-axis flip every measurement still checks out and a blade
    lands at the top. Evaluated here from the file's own numbers."""
    src = (FRONTEND / "src" / "brand" / "acumyn.jsx").read_text(encoding="utf-8")
    sweep = float(re.search(r"^const SWEEP = ([0-9.]+)", src, re.M).group(1))
    axes = [int(x) for x in re.search(r"^const GAP_AXES = \[([0-9, ]+)\]", src, re.M)
            .group(1).split(",")]
    half_gap = (360 - sweep * len(axes)) / len(axes) / 2

    def in_gap(deg):
        d = deg % 360
        return any(abs(((d - axis + 540) % 360) - 180) < half_gap for axis in axes)

    assert in_gap(90), "the gap is not at top dead centre"
    assert not in_gap(90 + half_gap + sweep / 2), "the middle of a blade reads as a gap"
    assert "export function inGap" in src


# ── fleet health: the ladder, rule by rule ────────────────────────────────────────────────
def _facts(**kw):
    import datetime as dt
    from app.services import fleet_health as fh

    base = dict(slug="acme", name="Acme", status="active",
                created_at=dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc))
    base.update(kw)
    return fh.WorkspaceFacts(**base)


NOW = __import__("datetime").datetime(2026, 9, 17, 12, tzinfo=__import__("datetime").timezone.utc)


def _person(role="owner", status="active", **kw):
    from app.services import fleet_health as fh
    return fh.PersonFact(id=kw.pop("id", str(uuid.uuid4())), email=kw.pop("email", f"{role}@acme.test"),
                         name=role, role=role, status=status, **kw)


def _source(status="connected", minutes_ago=5, provider="sisu", **kw):
    import datetime as dt
    from app.services import fleet_health as fh
    last = None if minutes_ago is None else NOW - dt.timedelta(minutes=minutes_ago)
    return fh.SourceFact(id=kw.pop("id", str(uuid.uuid4())), provider=provider, status=status,
                         last_synced_at=last, **kw)


def test_a_workspace_with_nothing_wrong_is_healthy_and_says_why():
    from app.services import fleet_health as fh
    f = _facts(people=[_person(last_login_at=NOW)], sources=[_source()])
    h = fh.evaluate(f, NOW)
    assert h["state"] == "healthy" and h["signals"] == []
    assert h["why"] and h["derivation"], "a state without its reason breaks the console's one law"


def test_one_erroring_source_is_one_broken_row_however_many_runs_failed():
    from app.services import fleet_health as fh
    f = _facts(people=[_person(last_login_at=NOW)],
               sources=[_source(status="error", last_error="token expired", failed_runs_7d=9)])
    h = fh.evaluate(f, NOW)
    assert h["state"] == "broken"
    broken = [s for s in h["signals"] if s["severity"] == "broken"]
    assert len(broken) == 1 and "9 failed runs" in broken[0]["meta"]
    assert broken[0]["derivation"] and broken[0]["primary_action"]


def test_onboarding_that_will_not_finish_by_itself_is_stalled():
    import datetime as dt
    from app.services import fleet_health as fh

    silent = _person(status="invited", invite_expires=NOW + dt.timedelta(days=2),
                     created_at=NOW - dt.timedelta(days=5))
    h = fh.evaluate(_facts(people=[silent], sources=[_source()]), NOW)
    assert h["state"] == "stalled" and "never signed in" in h["why"][0]

    expired = _person(status="invited", invite_expires=NOW - dt.timedelta(days=1))
    h = fh.evaluate(_facts(people=[expired], sources=[_source()]), NOW)
    assert h["state"] == "stalled" and "expired" in h["why"][0]

    fresh = _person(status="invited", invite_expires=NOW + dt.timedelta(days=6, hours=12))
    h = fh.evaluate(_facts(people=[fresh], sources=[_source()],
                           created_at=NOW - dt.timedelta(hours=12)), NOW)
    assert h["state"] == "healthy", "an owner invited this morning is not stuck yet"

    empty = fh.evaluate(_facts(people=[_person(last_login_at=NOW)], sources=[]), NOW)
    assert empty["state"] == "stalled" and "Nothing is connected" in empty["why"]


def test_things_going_wrong_slowly_are_watch():
    import datetime as dt
    from app.services import fleet_health as fh

    stale = fh.evaluate(_facts(people=[_person(last_login_at=NOW)],
                               sources=[_source(minutes_ago=240)]), NOW)
    assert stale["state"] == "watch"

    # Ads sync on a slower cadence, so the same age is not stale for them.
    ads = fh.evaluate(_facts(people=[_person(last_login_at=NOW)],
                             sources=[_source(provider="meta_ads", minutes_ago=240)]), NOW)
    assert ads["state"] == "healthy"

    idle = _person(role="member", status="invited", email="late@acme.test",
                   invite_expires=NOW - dt.timedelta(days=9))
    h = fh.evaluate(_facts(people=[_person(last_login_at=NOW), idle], sources=[_source()]), NOW)
    assert h["state"] == "watch" and "idle" in h["why"][0]

    budget = fh.evaluate(_facts(people=[_person(last_login_at=NOW)], sources=[_source()],
                                tokens_used=1_900_000, token_budget=2_000_000), NOW)
    assert budget["state"] == "watch"


def test_a_suspended_workspace_is_suspended_but_its_live_share_links_still_reach_triage():
    from app.services import fleet_health as fh
    f = _facts(status="suspended", people=[_person(last_login_at=NOW)], sources=[_source()],
               share_links_live=3, suspended_by="ops@acumyn.io", suspended_reason="billing lapsed")
    h = fh.evaluate(f, NOW)
    assert h["state"] == "suspended"
    assert any("Reason: billing lapsed" == line for line in h["why"])
    assert [s["severity"] for s in h["signals"]] == ["watch"]
    # ...and a suspended workspace's sources are not reported stale: its syncs stopped on purpose.
    f.sources = [_source(minutes_ago=10_000)]
    assert all("stale" not in s["key"] for s in fh.evaluate(f, NOW)["signals"])


def test_a_zero_token_budget_means_unlimited_not_no_ai():
    """AI_EMPLOYEES_TOKEN_BUDGET=0 is documented as no cap. Zero is falsy but present, and the
    design once read it in four places as a cap of nothing: the opposite fact."""
    from app.services import fleet_health as fh

    class _T:
        config = {"ai_token_budget": 0}

    assert fh.token_budget_for(_T()) is None
    h = fh.evaluate(_facts(people=[_person(last_login_at=NOW)], sources=[_source()],
                           tokens_used=10_000_000, token_budget=fh.token_budget_for(_T())), NOW)
    assert h["state"] == "healthy"


def test_the_severity_ranks_are_the_ones_the_console_sorts_by():
    from app.services import fleet_health as fh

    src = (OPERATOR_SRC / "tokens.js").read_text(encoding="utf-8")
    console = {m.group(1): int(m.group(2))
               for m in re.finditer(r"^\s+(\w+):\s*\{[^}]*rank:\s*(\d+)", src, re.M)}
    assert console == fh.RANK


async def test_a_suspended_or_frozen_workspace_is_left_out_of_the_scheduled_sync():
    from app.models import Tenant
    from app.worker import syncable_tenant_ids

    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        assert t.id in await syncable_tenant_ids(s)
        t.status = "suspended"
        await s.commit()
        assert t.id not in await syncable_tenant_ids(s)
        t.status = "active"
        t.config = {**(t.config or {}), "syncs_frozen": True}
        await s.commit()
        assert t.id not in await syncable_tenant_ids(s)
        cfg = dict(t.config)
        cfg.pop("syncs_frozen")
        t.config = cfg
        await s.commit()
        assert t.id in await syncable_tenant_ids(s)


# ── provisioning, as the console drives it ────────────────────────────────────────────────
async def test_a_slug_must_be_a_usable_web_address():
    from app.services.provisioning import normalize_slug

    for bad in ("Acme Realty!", "ab", "-acme", "acme-", "ac_me", "a" * 64):
        with pytest.raises(ValueError):
            normalize_slug(bad)
    assert normalize_slug(" Harbor-Lane ") == "harbor-lane"
    for reserved in ("api", "admin", "www", "app", "intranet"):
        with pytest.raises(ValueError):
            normalize_slug(reserved)


async def test_the_console_cannot_create_a_workspace_without_a_plan():
    """provision_tenant's own comment documents the bug: no plan meant the column default, `team`,
    and a customer who bought the portal got a workspace without it."""
    async with _client() as c:
        r = await c.post("/api/v1/platform/tenants", headers=_H(await _op_token()), json={
            "slug": "noplanco", "name": "No Plan", "owner_email": "o@noplan.test"})
    assert r.status_code == 422, r.text


async def test_more_businesses_than_the_plan_allows_is_refused_at_creation():
    two = [{"key": "one", "name": "One", "tag": "Business"},
           {"key": "two", "name": "Two", "tag": "Business"}]
    async with _client() as c:
        r = await c.post("/api/v1/platform/tenants", headers=_H(await _op_token()), json={
            "slug": "capco", "name": "Cap Co", "owner_email": "o@capco.test",
            "hostname": "capco.localhost", "plan": "team", "businesses": two})
        assert r.status_code == 400 and "Team plan allows 1" in r.json()["detail"], r.text
        r = await c.post("/api/v1/platform/tenants", headers=_H(await _op_token()), json={
            "slug": "capco", "name": "Cap Co", "owner_email": "o@capco.test",
            "hostname": "capco.localhost", "plan": "business", "businesses": two})
    assert r.status_code == 201, r.text


async def test_a_suspension_needs_a_reason_and_the_fleet_shows_it():
    tok = await _op_token()
    async with _client() as c:
        r = await c.post("/api/v1/platform/tenants/springb/suspend", headers=_H(tok),
                         json={"reason": "   "})
        assert r.status_code == 400
        r = await c.post("/api/v1/platform/tenants/springb/suspend", headers=_H(tok),
                         json={"reason": "billing lapsed"})
        assert r.status_code == 200
        try:
            rows = (await c.get("/api/v1/platform/tenants", headers=_H(tok))).json()["tenants"]
            row = {t["slug"]: t for t in rows}["springb"]
            assert row["health"]["state"] == "suspended"
            assert "Reason: billing lapsed" in row["health"]["why"]
            assert any(OP_EMAIL in line for line in row["health"]["why"])
        finally:
            await c.post("/api/v1/platform/tenants/springb/resume", headers=_H(tok))


async def test_no_operator_response_carries_a_tenants_numbers():
    """Operational metadata only. Asserted on what the API serialises, not by looking at a screen:
    no key in a workspace row names money, production or a statement line."""
    async with _client() as c:
        r = await c.get("/api/v1/platform/tenants", headers=_H(await _op_token()))
    forbidden = re.compile(r"revenue|gci|noi|profit|income|expense|amount|volume|balance|mrr|cash",
                           re.I)

    def keys(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield f"{path}.{k}"
                yield from keys(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for item in obj:
                yield from keys(item, path)

    offenders = sorted({k for k in keys(r.json()) if forbidden.search(k.rsplit(".", 1)[-1])})
    assert not offenders, f"tenant figures in the operator payload: {offenders}"


@pytest.mark.skipif(not OPERATOR_SRC.exists(), reason="frontend not present")
def test_the_operator_console_ships_in_its_own_bundle_on_its_own_host():
    """A customer's browser never downloads the screen that suspends customers, and the console's
    session never shares an origin with a workspace. Both follow from the console being its own
    Vite entry served on its own host; this holds the pieces that make that true."""
    offenders = []
    for path in (FRONTEND / "src").rglob("*.js*"):
        if OPERATOR_SRC in path.parents:
            continue
        if re.search(r"from\s+[\"'][^\"']*operator/", _code(path)):
            offenders.append(path.name)
    assert not offenders, f"product code imports the operator console: {offenders}"

    import json
    scripts = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))["scripts"]
    assert "build:operator" in scripts["build:all"], "the Dockerfile runs build:all"
    assert "COPY --from=build /app/dist/operator /srv/operator" in (FRONTEND / "Dockerfile").read_text(encoding="utf-8")
    caddy = (FRONTEND / "Caddyfile").read_text(encoding="utf-8")
    assert "@operator host {$OPERATOR_HOST:admin.acumyn.io}" in caddy
    assert caddy.index("@operator host") < caddy.index("handle {"), "the dashboard catch-all would win"
    assert not (FRONTEND / "src" / "platform" / "PlatformConsole.jsx").exists(), "two operator consoles"


# ── phase 2: the panes ────────────────────────────────────────────────────────────────────
async def _tenant_with(slug, plan="team", **kw):
    """A bare workspace built directly, so a test controls exactly what it contains."""
    from app.models import Domain, Tenant

    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
        if t is None:
            t = Tenant(slug=slug, name=kw.get("name", slug.title()), plan=plan, status="active")
            s.add(t)
            await s.flush()
            s.add(Domain(tenant_id=t.id, hostname=f"{slug}.brokerage.test", is_primary=True))
            await s.commit()
        return t.id


async def test_modules_come_in_two_groups_and_the_gated_one_is_the_tiers():
    """For each tier, `gated` is exactly the tier's extra_tabs, and a Team workspace reports Books,
    Binder, the flywheel and AI employees all excluded: the assertion that catches flattening."""
    tok = await _op_token()
    for tier in plans.ORDER:
        slug = f"modules{tier}"
        await _tenant_with(slug, plan=tier)
        async with _client() as c:
            r = await c.get(f"/api/v1/platform/tenants/{slug}/modules", headers=_H(tok))
        assert r.status_code == 200, r.text
        body = r.json()
        included = {m["key"] for m in body["gated"] if m["included"]}
        assert included == plans.PLANS[tier]["extra_tabs"], tier
        assert {m["key"] for m in body["gated"]} == plans._PORTFOLIO_TABS
        for m in body["gated"]:
            assert set(m) == {"key", "name", "included", "lowest_tier", "lowest_tier_name"}, \
                "a gated module must carry no switch"
        assert any(o["key"] == "portfolio" and o["always"] for o in body["own"]), tier
        if tier == "team":
            assert included == set()

        async with _client() as c:
            r = await c.patch(f"/api/v1/platform/tenants/{slug}/modules", headers=_H(tok),
                              json={"books": True})
        assert r.status_code == 403


async def test_the_people_pane_never_carries_a_credential():
    from app.models import Tenant

    async with SessionLocal() as s:
        tid = (await s.execute(select(Tenant.id).where(Tenant.slug == "springb"))).scalar_one()
    async with _client() as c:
        r = await c.get("/api/v1/platform/tenants/springb/people", headers=_H(await _op_token()))
    assert r.status_code == 200 and r.json()["people"], r.text
    keys = {k for p in r.json()["people"] for k in p}
    for secret in ("password_hash", "action_token_hash", "totp_secret_enc", "totp_recovery",
                   "totp_last_used", "token_version"):
        assert secret not in keys, secret
    assert tid  # the workspace exists; the pane read it


async def test_a_share_link_is_listed_but_its_token_never_leaves_the_server():
    import datetime as dt
    from app.models import ShareLink

    tid = await _tenant_with("sharelinks")
    secret = "tok-" + uuid.uuid4().hex
    async with SessionLocal() as s:
        s.add(ShareLink(tenant_id=tid, scope="ulrg_scorecard", token=secret))
        s.add(ShareLink(tenant_id=tid, scope="sd_rep", token="tok-" + uuid.uuid4().hex,
                        revoked_at=dt.datetime.now(dt.timezone.utc)))
        await s.commit()
    async with _client() as c:
        r = await c.get("/api/v1/platform/tenants/sharelinks/share-links", headers=_H(await _op_token()))
    assert r.status_code == 200
    assert secret not in r.text and "token" not in {k for link in r.json()["links"] for k in link}
    assert sorted(link["live"] for link in r.json()["links"]) == [False, True]


async def test_one_expired_credential_is_one_incident_however_many_runs_it_failed():
    import datetime as dt
    from app.models import Integration, SyncRun

    tid = await _tenant_with("incidentco", plan="portfolio")
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        s.add(Integration(tenant_id=tid, provider="qbo", status="error", realm_id="9130",
                          last_error="invalid_grant: refresh token expired (Intuit 3200) ref 88112"))
        for i in range(9):
            s.add(SyncRun(tenant_id=tid, provider="qbo", status="error",
                          started_at=now - dt.timedelta(hours=i + 1),
                          detail=f"invalid_grant: refresh token expired (Intuit 3200) ref {1000 + i}"))
        await s.commit()
    async with _client() as c:
        r = await c.get("/api/v1/platform/fleet/incidents", headers=_H(await _op_token()))
    assert r.status_code == 200, r.text
    mine = [i for i in r.json()["incidents"] if any(w["slug"] == "incidentco" for w in i["workspaces"])]
    assert len(mine) == 1, mine
    assert mine[0]["hits"] == 9 and mine[0]["title"] == "QuickBooks authorisation expired"
    assert mine[0]["fix"], "an incident says what clears it"

    async with _client() as c:
        r = await c.get("/api/v1/platform/fleet/providers", headers=_H(await _op_token()))
    qbo = {p["key"]: p for p in r.json()["providers"]}["qbo"]
    assert qbo["error"] >= 1 and qbo["state"] == "broken"


async def test_the_usage_pane_reads_its_caps_from_the_plan():
    await _tenant_with("usageco", plan="business")
    async with _client() as c:
        r = await c.get("/api/v1/platform/tenants/usageco/usage", headers=_H(await _op_token()))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["seats"]["cap"] == plans.PLANS["business"]["max_users"]
    assert body["businesses"]["cap"] == plans.PLANS["business"]["max_businesses"]
    assert body["share_links"]["cap"] == plans.PLANS["business"]["max_share_links"]
    assert body["documents"]["bytes"] is None, "storage is unsourced, not a guessed figure"


async def test_the_fleet_rollup_and_triage_come_from_one_read_and_agree():
    async with _client() as c:
        r = await c.get("/api/v1/platform/fleet", headers=_H(await _op_token()))
        tenants = (await c.get("/api/v1/platform/tenants", headers=_H(await _op_token()))).json()["tenants"]
    assert r.status_code == 200, r.text
    body = r.json()
    assert sum(body["rollup"]["states"].values()) == body["rollup"]["workspaces"]["total"] == len(tenants)
    assert len(body["triage"]) == sum(len(t["signals"]) for t in tenants)
    ranks = [plans_rank for plans_rank in (s["severity"] for s in body["triage"])]
    from app.services.fleet_health import RANK
    assert [RANK[x] for x in ranks] == sorted(RANK[x] for x in ranks), "triage is ranked by severity"
    assert body["rollup"]["mrr_cents"] is None
