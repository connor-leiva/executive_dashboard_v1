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


# ── phase 3: write actions ────────────────────────────────────────────────────────────────
PHASE3_ROUTES = (
    "/tenants/{slug}/sync", "/tenants/{slug}/sources/{id}/sync",
    "/tenants/{slug}/sources/{id}/reconnect-link", "/tenants/{slug}/sources/setup-link",
    "/tenants/{slug}/freeze-syncs", "/tenants/{slug}/unfreeze-syncs",
    "/tenants/{slug}/people/resend-idle", "/tenants/{slug}/people/{id}/resend",
    "/tenants/{slug}/people/{id}/unlock", "/tenants/{slug}/people/{id}/reset-link",
    "/tenants/{slug}/share-links/revoke-all", "/tenants/{slug}/support-access/view-as",
)


def _capture_mail(monkeypatch):
    from app.config import settings
    from app.services import mailer

    sent = []

    async def fake_post(payload, headers):
        sent.append(payload)
        return 200, "{}"
    monkeypatch.setattr(mailer, "_post", fake_post)
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(settings, "MAIL_FROM", "Acumyn <hello@mail.acumyn.io>")
    monkeypatch.setattr(settings, "MAIL_REPLY_TO", "")
    return sent


async def _account(tid, email, *, role="member", status="active", **kw):
    from app.models import User

    async with SessionLocal() as s:
        u = User(tenant_id=tid, email=email, name=email.split("@")[0], role=role, status=status,
                 tab_access=[], token_version=0, **kw)
        s.add(u)
        await s.commit()
        return u.id


async def _trail(tid, action):
    from app.models import AuditLog

    async with SessionLocal() as s:
        return (await s.execute(select(AuditLog).where(
            AuditLog.tenant_id == tid, AuditLog.action == action)
            .order_by(AuditLog.created_at))).scalars().all()


def _by_acumyn(row):
    """Written into the workspace's own log, naming the operator, with no tenant user as actor."""
    return (row.actor_user_id is None and (row.detail or {}).get("by") == OP_EMAIL
            and row.actor_label.endswith("(Acumyn)"))


async def test_a_tenant_session_cannot_reach_any_write_action():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
        owner = r.json()["token"]
        for route in PHASE3_ROUTES:
            path = "/api/v1/platform" + route.format(slug="springb", id=uuid.uuid4())
            r = await c.post(path, headers=_H(owner), json={"reason": "x"})
            assert r.status_code == 401, f"{path} -> {r.status_code}"


async def test_sync_now_runs_the_workspaces_own_job_and_not_twice_at_once(monkeypatch):
    from app.models import Integration
    from app.services import sync_jobs

    tid = await _tenant_with("syncnowco")
    async with SessionLocal() as s:
        integ = Integration(tenant_id=tid, provider="sisu", status="connected")
        s.add(integ)
        await s.commit()
        integ_id = integ.id
    ran = []

    async def fake_all(tenant_id, run_id, period="mtd"):
        ran.append(("all", tenant_id))

    async def fake_one(tenant_id, integ_id, period="mtd"):
        ran.append(("one", integ_id))

    monkeypatch.setattr(sync_jobs, "run_all_job", fake_all)
    monkeypatch.setattr(sync_jobs, "run_one_job", fake_one)
    tok = await _op_token()
    async with _client() as c:
        r = await c.post("/api/v1/platform/tenants/syncnowco/sync", headers=_H(tok))
        assert r.status_code == 202 and r.json()["sources"] == 1, r.text
        # the fake job never finishes its run row, which is exactly a sync still in progress
        r = await c.post("/api/v1/platform/tenants/syncnowco/sync", headers=_H(tok))
        assert r.status_code == 409 and "already running" in r.json()["detail"]
        r = await c.post(f"/api/v1/platform/tenants/syncnowco/sources/{integ_id}/sync", headers=_H(tok))
        assert r.status_code == 202, r.text
        r = await c.post(f"/api/v1/platform/tenants/springb/sources/{integ_id}/sync", headers=_H(tok))
        assert r.status_code == 404, "a source is only reachable through its own workspace"
    assert ran == [("all", tid), ("one", integ_id)]
    rows = await _trail(tid, "tenant.sync_requested")
    assert len(rows) == 2 and all(_by_acumyn(r) for r in rows)


async def test_a_frozen_workspace_pulls_nothing_and_says_who_froze_it_and_why(monkeypatch):
    from app.models import Integration
    from app.services import sync_jobs

    tid = await _tenant_with("freezeco")
    async with SessionLocal() as s:
        integ = Integration(tenant_id=tid, provider="fub", status="connected")
        s.add(integ)
        await s.commit()
        integ_id = integ.id

    async def never(*a, **k):
        raise AssertionError("a frozen workspace was synced")

    monkeypatch.setattr(sync_jobs, "run_all_job", never)
    monkeypatch.setattr(sync_jobs, "run_one_job", never)
    tok = await _op_token()
    async with _client() as c:
        r = await c.post("/api/v1/platform/tenants/freezeco/freeze-syncs", headers=_H(tok),
                         json={"reason": " "})
        assert r.status_code == 400
        r = await c.post("/api/v1/platform/tenants/freezeco/freeze-syncs", headers=_H(tok),
                         json={"reason": "FUB key may have leaked"})
        assert r.status_code == 200, r.text
        assert (await c.post("/api/v1/platform/tenants/freezeco/freeze-syncs", headers=_H(tok),
                             json={"reason": "again"})).status_code == 409
        for path in ("/sync", f"/sources/{integ_id}/sync"):
            r = await c.post(f"/api/v1/platform/tenants/freezeco{path}", headers=_H(tok))
            assert r.status_code == 409 and "frozen" in r.json()["detail"], path

        row = (await c.get("/api/v1/platform/tenants/freezeco", headers=_H(tok))).json()
        assert row["syncs_frozen"] is True
        assert any("Syncs frozen" in line and OP_EMAIL in line for line in row["health"]["why"])
        assert "Freeze reason: FUB key may have leaked" in row["health"]["why"]
        assert not any(sig["key"].split(":")[1] == "stale" for sig in row["signals"]), \
            "a frozen source is paused on purpose, not stale"

        assert (await c.post("/api/v1/platform/tenants/freezeco/unfreeze-syncs",
                             headers=_H(tok))).status_code == 200
        assert (await c.post("/api/v1/platform/tenants/freezeco/unfreeze-syncs",
                             headers=_H(tok))).status_code == 409
    assert [_by_acumyn(r) for r in await _trail(tid, "tenant.syncs_frozen")] == [True]
    assert [_by_acumyn(r) for r in await _trail(tid, "tenant.syncs_unfrozen")] == [True]


async def test_the_workspaces_own_sync_buttons_refuse_while_frozen(monkeypatch):
    """Freezing is for a credential that may be compromised. A Sync button inside the workspace
    that still used it would leave the freeze a label."""
    from app.models import Integration, Tenant
    from app.services import sync_jobs

    async def never(*a, **k):
        raise AssertionError("a frozen workspace was synced")

    monkeypatch.setattr(sync_jobs, "run_all_job", never)
    monkeypatch.setattr(sync_jobs, "run_one_job", never)
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        integ = (await s.execute(select(Integration).where(Integration.tenant_id == t.id))).scalars().first()
    tok = await _op_token()
    async with _client() as c:
        owner = (await c.post("/api/v1/auth/login",
                              json={"email": "spring@springb.com", "password": "springtime"})).json()["token"]
        assert (await c.post("/api/v1/platform/tenants/springb/freeze-syncs", headers=_H(tok),
                             json={"reason": "test freeze"})).status_code == 200
        try:
            r = await c.post("/api/v1/sync/all", headers=_H(owner))
            assert r.status_code == 409 and "Acumyn support" in r.json()["detail"], r.text
            if integ is not None:
                r = await c.post(f"/api/v1/integrations/{integ.id}/sync", headers=_H(owner))
                assert r.status_code == 409, r.text
        finally:
            await c.post("/api/v1/platform/tenants/springb/unfreeze-syncs", headers=_H(tok))


async def test_the_scheduled_provider_jobs_skip_a_paused_workspace(monkeypatch):
    from app import worker
    from app.services import ads_funnel, sync as sync_mod

    tid = await _tenant_with("pausedjobsco")
    seen = []

    async def record(s, tenant_id, *a, **k):
        seen.append(tenant_id)
        return 0

    monkeypatch.setattr(sync_mod, "sync_agent_offices", record)
    monkeypatch.setattr(ads_funnel, "sync_ad_attribution", record)
    monkeypatch.setattr(ads_funnel, "sync_ad_conversions", record)
    tok = await _op_token()
    async with _client() as c:
        assert (await c.post("/api/v1/platform/tenants/pausedjobsco/freeze-syncs", headers=_H(tok),
                             json={"reason": "test"})).status_code == 200
    try:
        await worker.roster_tick()
        await worker.ads_funnel_tick()
        assert tid not in seen and seen, "a frozen workspace was pulled for"
    finally:
        async with _client() as c:
            await c.post("/api/v1/platform/tenants/pausedjobsco/unfreeze-syncs", headers=_H(tok))


async def test_a_reconnect_link_goes_to_whoever_can_reconnect_and_carries_no_token(monkeypatch):
    from app.models import Integration

    sent = _capture_mail(monkeypatch)
    tid = await _tenant_with("reconnectco", name="Reconnect & Co")
    await _account(tid, "owner@reconnect.test", role="owner")
    await _account(tid, "admin@reconnect.test", role="admin")
    await _account(tid, "member@reconnect.test", role="member")
    await _account(tid, "gone@reconnect.test", role="admin", status="disabled")
    async with SessionLocal() as s:
        integ = Integration(tenant_id=tid, provider="qbo", status="error", last_error="invalid_grant")
        s.add(integ)
        await s.commit()
        integ_id = integ.id

    tok = await _op_token()
    async with _client() as c:
        r = await c.post(f"/api/v1/platform/tenants/reconnectco/sources/{integ_id}/reconnect-link",
                         headers=_H(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sent_to"] == ["owner@reconnect.test", "admin@reconnect.test"]
    assert body["url"].endswith("reconnectco.brokerage.test/settings/integrations")
    assert sorted(p["to"][0] for p in sent) == ["admin@reconnect.test", "owner@reconnect.test"]
    for p in sent:
        assert "token" not in p["html"].lower() and body["url"] in p["html"]
        assert "Reconnect &amp; Co" in p["html"], "a workspace name is escaped into the HTML"
    row, = await _trail(tid, "tenant.reconnect_link_sent")
    assert _by_acumyn(row) and row.detail["sent_to"] == body["sent_to"]

    nobody = await _tenant_with("nobodyco")
    async with SessionLocal() as s:
        lone = Integration(tenant_id=nobody, provider="qbo", status="error")
        s.add(lone)
        await s.commit()
        lone_id = lone.id
    async with _client() as c:
        r = await c.post(f"/api/v1/platform/tenants/nobodyco/sources/{lone_id}/reconnect-link",
                         headers=_H(tok))
        assert r.status_code == 409 and "no active owner or admin" in r.json()["detail"]
        r = await c.post("/api/v1/platform/tenants/reconnectco/sources/setup-link", headers=_H(tok))
        assert r.status_code == 200 and r.json()["url"] == body["url"]


async def test_people_actions_email_the_person_and_never_hand_the_operator_a_way_in(monkeypatch):
    import datetime as dt
    from app.models import User

    sent = _capture_mail(monkeypatch)
    now = dt.datetime.now(dt.timezone.utc)
    tid = await _tenant_with("peopleco")
    await _account(tid, "owner@people.test", role="owner")
    invited = await _account(tid, "invited@people.test", status="invited",
                             action_token_purpose="invite", action_token_hash="x",
                             action_token_expires=now - dt.timedelta(days=1))
    locked = await _account(tid, "locked@people.test", failed_logins=10,
                            locked_until=now + dt.timedelta(minutes=10))
    elsewhere = await _account(await _tenant_with("otherpeopleco"), "other@people.test")

    tok = await _op_token()
    base = "/api/v1/platform/tenants/peopleco/people"
    async with _client() as c:
        r = await c.post(f"{base}/{invited}/resend", headers=_H(tok))
        assert r.status_code == 200, r.text
        assert set(r.json()) == {"email", "invite_expires"}, "no link comes back to the operator"
        assert (await c.post(f"{base}/{locked}/resend", headers=_H(tok))).status_code == 409

        r = await c.post(f"{base}/{locked}/unlock", headers=_H(tok))
        assert r.status_code == 200, r.text
        assert (await c.post(f"{base}/{locked}/unlock", headers=_H(tok))).status_code == 409

        r = await c.post(f"{base}/{locked}/reset-link", headers=_H(tok))
        assert r.status_code == 200 and set(r.json()) == {"email", "expires_at"}, r.text
        assert (await c.post(f"{base}/{invited}/reset-link", headers=_H(tok))).status_code == 409

        assert (await c.post(f"{base}/{elsewhere}/unlock", headers=_H(tok))).status_code == 404
        assert (await c.post(f"{base}/not-a-uuid/unlock", headers=_H(tok))).status_code == 404

    to = {p["to"][0]: p["html"] for p in sent}
    assert "accept-invite?token=" in to["invited@people.test"]
    assert "reset-password?token=" in to["locked@people.test"]
    async with SessionLocal() as s:
        inv = await s.get(User, invited)
        lck = await s.get(User, locked)
    assert inv.action_token_purpose == "invite" and fleet_aware(inv.action_token_expires) > now
    assert lck.locked_until is None and lck.failed_logins == 0 and lck.action_token_purpose == "reset"
    for action in ("user.reinvited", "user.unlocked", "user.reset_link"):
        rows = await _trail(tid, action)
        assert len(rows) == 1 and _by_acumyn(rows[0]), action


def fleet_aware(d):
    from app.services.fleet_health import _aware
    return _aware(d)


async def test_resending_idle_invites_clears_the_row_that_asked_for_it(monkeypatch):
    import datetime as dt

    _capture_mail(monkeypatch)
    now = dt.datetime.now(dt.timezone.utc)
    tid = await _tenant_with("idleco")
    await _account(tid, "owner@idle.test", role="owner", last_login_at=now)
    await _account(tid, "idle@idle.test", status="invited", action_token_purpose="invite",
                   action_token_hash="x", action_token_expires=now - dt.timedelta(days=13))
    await _account(tid, "fresh@idle.test", status="invited", action_token_purpose="invite",
                   action_token_hash="y", action_token_expires=now + dt.timedelta(days=6))
    tok = await _op_token()

    def idle_rows(row):
        return [sig for sig in row["signals"] if sig["key"].endswith(":people:idle_invites")]

    async with _client() as c:
        assert idle_rows((await c.get("/api/v1/platform/tenants/idleco", headers=_H(tok))).json())
        r = await c.post("/api/v1/platform/tenants/idleco/people/resend-idle", headers=_H(tok))
        assert r.status_code == 200 and r.json()["sent_to"] == ["idle@idle.test"], r.text
        assert not idle_rows((await c.get("/api/v1/platform/tenants/idleco", headers=_H(tok))).json())
        r = await c.post("/api/v1/platform/tenants/idleco/people/resend-idle", headers=_H(tok))
        assert r.status_code == 409


async def test_revoking_share_links_revokes_the_live_ones_and_clears_the_suspended_row():
    import datetime as dt
    from app.models import ShareLink, Tenant

    now = dt.datetime.now(dt.timezone.utc)
    tid = await _tenant_with("revokeco")
    async with SessionLocal() as s:
        for i in range(2):
            s.add(ShareLink(tenant_id=tid, scope="sd_rep", token=f"live-{uuid.uuid4().hex}"))
        s.add(ShareLink(tenant_id=tid, scope="ulrg_scorecard", token=f"old-{uuid.uuid4().hex}",
                        expires_at=now - dt.timedelta(days=1)))
        (await s.get(Tenant, tid)).status = "suspended"
        await s.commit()
    tok = await _op_token()
    async with _client() as c:
        row = (await c.get("/api/v1/platform/tenants/revokeco", headers=_H(tok))).json()
        assert any(sig["key"].endswith(":share:live_while_suspended") for sig in row["signals"])
        r = await c.post("/api/v1/platform/tenants/revokeco/share-links/revoke-all", headers=_H(tok))
        assert r.status_code == 200 and r.json() == {"revoked": 2}, r.text
        assert (await c.post("/api/v1/platform/tenants/revokeco/share-links/revoke-all",
                             headers=_H(tok))).status_code == 409
        row = (await c.get("/api/v1/platform/tenants/revokeco", headers=_H(tok))).json()
        assert not any(sig["key"].endswith(":share:live_while_suspended") for sig in row["signals"])
    async with SessionLocal() as s:
        links = (await s.execute(select(ShareLink).where(ShareLink.tenant_id == tid))).scalars().all()
    assert sorted(link.revoked_at is not None for link in links) == [False, True, True], \
        "an already-expired link is left as it was"
    row, = await _trail(tid, "tenant.share_links_revoked")
    assert _by_acumyn(row) and row.detail["count"] == 2


@pytest.mark.skipif(not OPERATOR_SRC.exists(), reason="frontend not present")
def test_every_action_the_server_attaches_to_a_signal_is_one_the_console_can_perform():
    """A triage button that navigates instead of doing what it says is a button that lies. Every
    action key fleet_health can emit either has a call in actions.js or is `open`."""
    src = (Path(__file__).resolve().parents[1] / "app" / "services" / "fleet_health.py").read_text(encoding="utf-8")
    emitted = set(re.findall(r'_action\("[^"]+", "(\w+)"', src))
    actions = _code(OPERATOR_SRC / "actions.js")
    handled = set(re.findall(r"^\s{2}(\w+): (?:\{|async)", actions, re.M))
    missing = emitted - handled - {"open"}
    assert not missing, f"signal actions the console cannot perform: {sorted(missing)}"


# ── phase 4: the operator trail and the platform's health ─────────────────────────────────
async def _platform_rows(slug):
    from app.models import PlatformAudit

    async with SessionLocal() as s:
        return (await s.execute(select(PlatformAudit).where(PlatformAudit.tenant_slug == slug)
                                .order_by(PlatformAudit.created_at))).scalars().all()


async def test_every_operator_change_is_written_to_both_trails():
    """§9: the workspace's own log, so the customer sees it, and platform_audit, so the operator
    trail does not have to scan every workspace."""
    import datetime as dt
    from app.models import ShareLink

    now = dt.datetime.now(dt.timezone.utc)
    tid = await _tenant_with("trailco")
    locked = await _account(tid, "locked@trail.test", failed_logins=10,
                            locked_until=now + dt.timedelta(minutes=5))
    async with SessionLocal() as s:
        s.add(ShareLink(tenant_id=tid, scope="sd_rep", token=f"trail-{uuid.uuid4().hex}"))
        await s.commit()
    tok = await _op_token()
    base = "/api/v1/platform/tenants/trailco"
    async with _client() as c:
        for path, body in ((f"{base}/suspend", {"reason": "trail test"}), (f"{base}/resume", {}),
                           (f"{base}/freeze-syncs", {"reason": "trail freeze"}),
                           (f"{base}/unfreeze-syncs", {}), (f"{base}/people/{locked}/unlock", {}),
                           (f"{base}/share-links/revoke-all", {})):
            r = await c.post(path, headers={**_H(tok), "x-forwarded-for": "203.0.113.7"}, json=body)
            assert r.status_code == 200, (path, r.text)

    actions = ["tenant.suspended", "tenant.resumed", "tenant.syncs_frozen", "tenant.syncs_unfrozen",
               "user.unlocked", "tenant.share_links_revoked"]
    rows = await _platform_rows("trailco")
    assert [r.action for r in rows] == actions, "one entry per change, in the order they were made"
    for r in rows:
        assert r.operator_email == OP_EMAIL and r.tenant_id == tid and r.ip == "203.0.113.7"
        assert "by" not in r.detail, "the operator is a column here, not a detail"
        tenant_row, = await _trail(tid, r.action)
        assert _by_acumyn(tenant_row)
    assert {r.action: r.reason for r in rows}["tenant.suspended"] == "trail test"
    assert {r.action: r.reason for r in rows}["tenant.syncs_frozen"] == "trail freeze"


async def test_the_operator_trail_outlives_the_workspace_it_describes():
    """tenant_id is not a foreign key, on purpose: deleting a workspace must not delete the record
    that it was deleted."""
    from sqlalchemy import delete
    from app.models import AuditLog, Base, Domain, Tenant

    assert not Base.metadata.tables["platform_audit"].c.tenant_id.foreign_keys
    tid = await _tenant_with("outliveco")
    tok = await _op_token()
    async with _client() as c:
        assert (await c.post("/api/v1/platform/tenants/outliveco/freeze-syncs", headers=_H(tok),
                             json={"reason": "before deletion"})).status_code == 200
    async with SessionLocal() as s:
        await s.execute(delete(AuditLog).where(AuditLog.tenant_id == tid))
        await s.execute(delete(Domain).where(Domain.tenant_id == tid))
        await s.execute(delete(Tenant).where(Tenant.id == tid))
        await s.commit()
    rows = await _platform_rows("outliveco")
    assert [r.action for r in rows] == ["tenant.syncs_frozen"] and rows[0].tenant_id == tid


async def test_the_audit_view_lists_each_change_once_and_only_changes():
    import datetime as dt
    from app.models import AuditLog

    hour_ago = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
    tid = await _tenant_with("scopeco")
    member = await _account(tid, "member@scope.test", role="admin")
    async with SessionLocal() as s:
        s.add(AuditLog(tenant_id=tid, actor_user_id=member, action="user.invited",
                       target_type="user", detail={"role": "member"}, created_at=hour_ago))
        s.add(AuditLog(tenant_id=tid, actor_user_id=member, action="auth.login", created_at=hour_ago))
        await s.commit()
    tok = await _op_token()
    async with _client() as c:
        assert (await c.post("/api/v1/platform/tenants/scopeco/freeze-syncs", headers=_H(tok),
                             json={"reason": "scope test"})).status_code == 200
        everything = (await c.get("/api/v1/platform/audit?scope=all&tenant=scopeco", headers=_H(tok))).json()
        mine = (await c.get("/api/v1/platform/audit?scope=acumyn&operator=me", headers=_H(tok))).json()
        teams = (await c.get("/api/v1/platform/audit?scope=tenants&tenant=scopeco", headers=_H(tok))).json()
        paged = (await c.get("/api/v1/platform/audit?scope=all&tenant=scopeco&limit=1", headers=_H(tok))).json()
        older = (await c.get("/api/v1/platform/audit", headers=_H(tok), params={
            "scope": "all", "tenant": "scopeco", "limit": 1, "before": paged["next_before"]})).json()
        assert (await c.get("/api/v1/platform/audit?scope=nonsense", headers=_H(tok))).status_code == 400
        owner = (await c.post("/api/v1/auth/login",
                              json={"email": "spring@springb.com", "password": "springtime"})).json()["token"]
        assert (await c.get("/api/v1/platform/audit", headers=_H(owner))).status_code == 401

    actions = [e["action"] for e in everything["events"]]
    assert sorted(actions) == ["tenant.syncs_frozen", "user.invited"], \
        "the operator's change once, the team's change once, and no sign-in"
    assert {e["scope"] for e in mine["events"]} == {"acumyn"}
    assert all(e["who"] == OP_EMAIL for e in mine["events"])
    assert [e["action"] for e in teams["events"]] == ["user.invited"]
    assert teams["events"][0]["who"] == "member@scope.test" and teams["events"][0]["ip"] is None
    assert len(paged["events"]) == 1 and paged["next_before"]
    assert len(older["events"]) == 1 and older["events"][0]["id"] != paged["events"][0]["id"]
    assert everything["retention_days"] == 400


async def test_operator_entries_past_retention_are_pruned_and_nothing_younger():
    import datetime as dt
    from app.models import PlatformAudit
    from app.services.operator_audit import prune

    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        s.add(PlatformAudit(operator_email=OP_EMAIL, action="tenant.old", tenant_slug="pruneco",
                            created_at=now - dt.timedelta(days=401)))
        s.add(PlatformAudit(operator_email=OP_EMAIL, action="tenant.kept", tenant_slug="pruneco",
                            created_at=now - dt.timedelta(days=399)))
        await s.commit()
    async with SessionLocal() as s:
        assert await prune(s, now) >= 1
    assert [r.action for r in await _platform_rows("pruneco")] == ["tenant.kept"]


async def test_system_reports_the_migration_heads_and_whether_the_worker_reports():
    import datetime as dt
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import delete
    from app.models import JobHeartbeat

    backend = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, f"migrations have forked: {heads}"

    tok = await _op_token()
    async with SessionLocal() as s:
        await s.execute(delete(JobHeartbeat))
        await s.commit()
    async with _client() as c:
        before = (await c.get("/api/v1/platform/system", headers=_H(tok))).json()
    assert before["migrations"]["head_count"] == 1 and before["migrations"]["heads"] == list(heads)
    assert before["database"]["state"] == "ok"
    assert before["worker"]["state"] == "never", "no heartbeat means nothing is known to be running"

    async with SessionLocal() as s:
        s.add(JobHeartbeat(job="tick", last_started_at=dt.datetime.now(dt.timezone.utc),
                           last_ok_at=dt.datetime.now(dt.timezone.utc), last_seconds=1.5))
        await s.commit()
    async with _client() as c:
        after = (await c.get("/api/v1/platform/system", headers=_H(tok))).json()
        flags = (await c.get("/api/v1/platform/system/flags", headers=_H(tok))).json()["flags"]
    assert after["worker"]["state"] == "ok"
    keys = {f["key"] for f in flags}
    assert {"ENV", "SINGLE_TENANT_FALLBACK", "SYNC_INTERVAL_MINUTES"} <= keys
    assert not [k for k in keys if re.search(r"SECRET|KEY|TOKEN|PASSWORD|DSN|DATABASE", k)], \
        "the flags list never carries a secret setting"


def test_the_scheduler_registers_every_job_through_a_heartbeat():
    """services/jobs.catalog() is what the System view shows; build_scheduler is what runs. They
    must name the same jobs, and every registered job must write its heartbeat."""
    from app.services import jobs
    from app.worker import build_scheduler

    sched = build_scheduler()
    registered = {j.func.__name__ for j in sched.get_jobs()}
    assert all(hasattr(j.func, "__wrapped__") for j in sched.get_jobs()), "a job without a heartbeat"
    assert registered == {e["job"] for e in jobs.catalog() if e["enabled"]}


async def test_a_heartbeat_records_starts_clean_runs_and_failures():
    from app.models import JobHeartbeat
    from app.services.jobs import heartbeat

    async def fine():
        return 3

    async def broken():
        raise RuntimeError("the provider is down")

    assert await heartbeat(fine, name="test_fine")() == 3
    with pytest.raises(RuntimeError):
        await heartbeat(broken, name="test_broken")()
    async with SessionLocal() as s:
        ok = await s.get(JobHeartbeat, "test_fine")
        bad = await s.get(JobHeartbeat, "test_broken")
    assert ok.last_started_at and ok.last_ok_at and ok.last_failed_at is None
    assert bad.last_failed_at and bad.last_ok_at is None and "the provider is down" in bad.last_error


# ── phase 5: platform billing ─────────────────────────────────────────────────────────────
WEBHOOK_SECRET = "whsec_test_operator_console"


async def _connect_billing(enabled=True):
    """Platform billing as a connected account would leave it, written directly: the connect route
    verifies a key against Stripe, which a test must never reach."""
    from app.models import PlatformBillingConfig
    from app.security import enc

    async with SessionLocal() as s:
        cfg = await s.get(PlatformBillingConfig, 1) or PlatformBillingConfig(id=1)
        cfg.secret_key_enc = enc("sk_test_not_a_real_key")
        cfg.webhook_secret_enc = enc(WEBHOOK_SECRET)
        cfg.enabled = enabled
        cfg.livemode = False
        s.add(cfg)
        await s.commit()


def _signed(event: dict, secret=WEBHOOK_SECRET, at=None):
    import hashlib
    import hmac
    import json
    import time

    body = json.dumps(event).encode()
    t = int(at if at is not None else time.time())
    sig = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return body, {"stripe-signature": f"t={t},v1={sig}", "content-type": "application/json"}


def _no_stripe(monkeypatch):
    from app.services import platform_billing

    async def refuse(*a, **k):
        raise AssertionError("Stripe was called")
    monkeypatch.setattr(platform_billing, "stripe", refuse)


async def _billed(slug, customer, **kw):
    from app.models import PlatformSubscription

    tid = await _tenant_with(slug, plan=kw.pop("plan", "business"))
    async with SessionLocal() as s:
        if await s.get(PlatformSubscription, tid) is None:
            s.add(PlatformSubscription(tenant_id=tid, stripe_customer_id=customer,
                                       status=kw.pop("status", "active"), **kw))
            await s.commit()
    return tid


def _sub_event(event_id, tid, customer, status, created, amount=79900):
    return {"id": event_id, "type": "customer.subscription.updated", "created": created,
            "data": {"object": {"id": f"sub_{customer}", "customer": customer, "status": status,
                                "metadata": {"acumyn_tenant_id": str(tid)},
                                "items": {"data": [{"price": {"id": "price_x", "unit_amount": amount,
                                                              "currency": "usd",
                                                              "recurring": {"interval": "month"}},
                                                    "current_period_end": created + 86400 * 30}]}}}}


async def test_a_webhook_without_a_valid_signature_is_refused_before_anything_is_applied(monkeypatch):
    from app.models import PlatformStripeEvent

    _no_stripe(monkeypatch)
    await _connect_billing()
    tid = await _billed("sigco", "cus_sigco")
    event = _sub_event("evt_sig_1", tid, "cus_sigco", "past_due", 1_800_000_000)
    body, headers = _signed(event)
    async with _client() as c:
        r = await c.post("/api/v1/platform/webhooks/stripe", content=body,
                         headers={"content-type": "application/json"})
        assert r.status_code == 400, "unsigned"
        _, wrong = _signed(event, secret="whsec_somebody_else")
        assert (await c.post("/api/v1/platform/webhooks/stripe", content=body, headers=wrong)).status_code == 400
        _, stale = _signed(event, at=1_000_000_000)
        assert (await c.post("/api/v1/platform/webhooks/stripe", content=body, headers=stale)).status_code == 400, \
            "a captured delivery replayed later"
        async with SessionLocal() as s:
            assert await s.get(PlatformStripeEvent, "evt_sig_1") is None
        r = await c.post("/api/v1/platform/webhooks/stripe", content=body, headers=headers)
        assert r.status_code == 200 and r.json()["outcome"] == "applied", r.text


async def test_a_replayed_invoice_paid_does_not_count_the_money_twice(monkeypatch):
    from app.models import PlatformSubscription

    _no_stripe(monkeypatch)
    await _connect_billing()
    tid = await _billed("replayco", "cus_replayco")
    invoice = {"id": "in_replay_1", "customer": "cus_replayco", "status": "paid", "number": "RPL-0001",
               "amount_due": 79900, "amount_paid": 79900, "created": 1_800_000_000,
               "status_transitions": {"paid_at": 1_800_000_100}, "metadata": {}}
    async with _client() as c:
        for event_id in ("evt_paid_1", "evt_paid_1", "evt_paid_2"):
            body, headers = _signed({"id": event_id, "type": "invoice.paid", "created": 1_800_000_100,
                                     "data": {"object": invoice}})
            r = await c.post("/api/v1/platform/webhooks/stripe", content=body, headers=headers)
            assert r.status_code == 200, r.text
    async with SessionLocal() as s:
        sub = await s.get(PlatformSubscription, tid)
    assert sub.collected_cents == 79900, \
        "the same delivery twice, and a second event for the same invoice, are still one payment"


async def test_an_out_of_order_subscription_update_leaves_the_latest_state(monkeypatch):
    from app.models import PlatformAudit, PlatformSubscription

    _no_stripe(monkeypatch)
    await _connect_billing()
    tid = await _billed("orderco", "cus_orderco", status="active")
    newer = _sub_event("evt_order_new", tid, "cus_orderco", "past_due", 1_800_000_500)
    older = _sub_event("evt_order_old", tid, "cus_orderco", "active", 1_800_000_100)
    async with _client() as c:
        for event in (newer, older):
            body, headers = _signed(event)
            assert (await c.post("/api/v1/platform/webhooks/stripe", content=body, headers=headers)).status_code == 200
    async with SessionLocal() as s:
        sub = await s.get(PlatformSubscription, tid)
        audits = (await s.execute(select(PlatformAudit).where(
            PlatformAudit.tenant_slug == "orderco"))).scalars().all()
    assert sub.status == "past_due", "an older event arriving late must not overwrite newer state"
    assert [a.action for a in audits] == ["billing.past_due"] and audits[0].operator_id is None

    async with _client() as c:
        row = {t["slug"]: t for t in (await c.get("/api/v1/platform/tenants",
                                                   headers=_H(await _op_token()))).json()["tenants"]}["orderco"]
    assert row["health"]["state"] == "broken", "past due is broken on the fleet list, from the mirror"


async def test_changing_the_plan_never_calls_stripe(monkeypatch):
    _no_stripe(monkeypatch)
    await _connect_billing()
    tid = await _billed("planco", "cus_planco", plan="team", amount_cents=39900, interval="month")
    tok = await _op_token()
    async with _client() as c:
        r = await c.patch("/api/v1/platform/tenants/planco/billing", headers=_H(tok),
                          json={"plan": "business", "token_budget": 0})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enforced"]["plan"] == "business"
    assert body["enforced"]["price_differs"] is True, "Stripe still charges Team's price, and the pane says so"
    assert body["enforced"]["token_budget"] is None, "zero is unlimited"
    assert (await _trail(tid, "billing.plan_changed"))
    assert [r.action for r in await _platform_rows("planco")] == ["billing.plan_changed", "billing.budget_changed"]


async def test_billing_routes_that_call_stripe_refuse_until_it_is_connected_and_never_return_a_key(monkeypatch):
    from app.models import PlatformBillingConfig

    _no_stripe(monkeypatch)
    async with SessionLocal() as s:
        cfg = await s.get(PlatformBillingConfig, 1)
        if cfg is not None:
            await s.delete(cfg)
            await s.commit()
    await _tenant_with("unbilledco")
    tok = await _op_token()
    async with _client() as c:
        for path in ("/billing/customer", "/billing/sync", "/billing/retry", "/billing/payment-link"):
            r = await c.post(f"/api/v1/platform/tenants/unbilledco{path}", headers=_H(tok), json={})
            assert r.status_code == 409 and "not connected" in r.json()["detail"], (path, r.text)
        r = await c.put("/api/v1/platform/billing/config", headers=_H(tok), json={"secret_key": "pk_live_nope"})
        assert r.status_code == 400, "a publishable key is not a secret key"
    await _connect_billing()
    async with _client() as c:
        config = await c.get("/api/v1/platform/billing/config", headers=_H(tok))
        billing = await c.get("/api/v1/platform/tenants/unbilledco/billing", headers=_H(tok))
    assert config.json()["connected"] and config.json()["webhook_configured"]
    for r in (config, billing):
        assert "sk_test_not_a_real_key" not in r.text and WEBHOOK_SECRET not in r.text
    assert billing.json()["billed"] is False and billing.json()["subscription"] is None


async def test_mrr_counts_active_subscriptions_by_the_month(monkeypatch):
    from app.models import PlatformSubscription

    _no_stripe(monkeypatch)
    await _connect_billing()
    await _billed("mrrmonthco", "cus_mrrmonth", status="active", amount_cents=119900, interval="month")
    await _billed("mrryearco", "cus_mrryear", status="active", amount_cents=1200000, interval="year")
    await _billed("mrrpastco", "cus_mrrpast", status="past_due", amount_cents=39900, interval="month")
    async with SessionLocal() as s:
        rows = (await s.execute(select(PlatformSubscription))).scalars().all()
    from app.services.platform_billing import monthly_cents
    expected = sum(monthly_cents(r) for r in rows)
    async with _client() as c:
        rollup = (await c.get("/api/v1/platform/fleet", headers=_H(await _op_token()))).json()["rollup"]
    assert rollup["mrr_cents"] == expected
    assert monthly_cents(next(r for r in rows if r.stripe_customer_id == "cus_mrryear")) == 100000
    assert monthly_cents(next(r for r in rows if r.stripe_customer_id == "cus_mrrpast")) == 0


# ── phase 6: support access, export, transfer, delete ─────────────────────────────────────
def _session_from(url: str) -> str:
    return url.split("#session=", 1)[1]


async def test_support_access_is_a_real_read_only_account_that_ends_on_time(monkeypatch):
    import datetime as dt
    from app.models import User

    sent = _capture_mail(monkeypatch)
    tid = await _tenant_with("supportco")
    await _account(tid, "owner@support.test", role="owner")
    tok = await _op_token()
    async with _client() as c:
        r = await c.post("/api/v1/platform/tenants/supportco/support-access", headers=_H(tok),
                         json={"reason": "   ", "minutes": 30})
        assert r.status_code == 400
        r = await c.post("/api/v1/platform/tenants/supportco/support-access", headers=_H(tok),
                         json={"reason": "the owner says the Forum tab is blank", "minutes": 45})
        assert r.status_code == 400, "only the lengths the console offers"
        r = await c.post("/api/v1/platform/tenants/supportco/support-access", headers=_H(tok),
                         json={"reason": "the owner says the Forum tab is blank", "minutes": 15})
        assert r.status_code == 201, r.text
        opened = r.json()
        session = _session_from(opened["url"])
        host = {"x-tenant-host": "supportco.brokerage.test"}

        me = await c.get("/api/v1/me", headers={**_H(session), **host})
        assert me.status_code == 200, "a support session is an ordinary session for reads"
        write = await c.post("/api/v1/users/invite", headers={**_H(session), **host},
                             json={"email": "new@support.test", "role": "member"})
        assert write.status_code == 403 and "read-only" in write.text, write.text

        # the account is visible to the workspace, named, and marked as support
        people = (await c.get("/api/v1/platform/tenants/supportco/people", headers=_H(tok))).json()["people"]
        support = [p for p in people if p["expires_at"]]
        assert len(support) == 1 and "(Acumyn support)" in support[0]["name"]

    assert [p["to"][0] for p in sent] == ["owner@support.test"]
    assert "the owner says the Forum tab is blank" in sent[0]["html"]
    rows = await _trail(tid, "support.access_opened")
    assert len(rows) == 1 and _by_acumyn(rows[0]) and rows[0].detail["reason"]
    assert [r.reason for r in await _platform_rows("supportco")] == ["the owner says the Forum tab is blank"]

    # expiry is enforced on the request itself, not only by the job
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.tenant_id == tid, User.expires_at.is_not(None)))).scalar_one()
        u.expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)
        await s.commit()
    async with _client() as c:
        r = await c.get("/api/v1/me", headers={**_H(session), **host})
    assert r.status_code == 401 and "ended" in r.text

    # and the job disables what expired, killing the session for good
    from app.worker import expire_support_access
    await expire_support_access()
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.tenant_id == tid, User.expires_at.is_not(None)))).scalar_one()
    assert u.status == "disabled"
    assert [r.action for r in await _platform_rows("supportco")] == ["support.access_opened", "support.access_expired"]


async def test_support_access_can_be_ended_early_and_reopened_without_a_second_account(monkeypatch):
    from app.models import User

    _capture_mail(monkeypatch)
    tid = await _tenant_with("reopenco")
    tok = await _op_token()
    body = {"reason": "checking a sync", "minutes": 15}
    async with _client() as c:
        first = (await c.post("/api/v1/platform/tenants/reopenco/support-access", headers=_H(tok), json=body)).json()
        assert (await c.delete("/api/v1/platform/tenants/reopenco/support-access", headers=_H(tok))).status_code == 200
        host = {"x-tenant-host": "reopenco.brokerage.test"}
        assert (await c.get("/api/v1/me", headers={**_H(_session_from(first["url"])), **host})).status_code == 401
        assert (await c.delete("/api/v1/platform/tenants/reopenco/support-access", headers=_H(tok))).status_code == 409
        second = (await c.post("/api/v1/platform/tenants/reopenco/support-access", headers=_H(tok), json=body)).json()
        assert (await c.get("/api/v1/me", headers={**_H(_session_from(second["url"])), **host})).status_code == 200
        assert (await c.get("/api/v1/me", headers={**_H(_session_from(first["url"])), **host})).status_code == 401, \
            "reopening does not revive the earlier session"
    async with SessionLocal() as s:
        accounts = (await s.execute(select(User).where(User.tenant_id == tid))).scalars().all()
    assert len(accounts) == 1


async def test_the_export_is_metadata_and_carries_no_credential():
    import json
    from app.models import Integration
    from app.security import enc

    tid = await _tenant_with("exportco")
    await _account(tid, "owner@export.test", role="owner", password_hash="$argon2id$not-a-real-hash",
                   action_token_hash="deadbeef")
    async with SessionLocal() as s:
        s.add(Integration(tenant_id=tid, provider="fub", status="connected",
                          access_token_enc=enc("fub-secret-token"), config={"api_key_enc": enc("fub-key")}))
        await s.commit()
    async with _client() as c:
        r = await c.post("/api/v1/platform/tenants/exportco/export", headers=_H(await _op_token()))
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    archive = json.loads(r.content)
    assert archive["workspace"]["slug"] == "exportco" and archive["people"][0]["email"] == "owner@export.test"
    for secret in ("argon2id", "deadbeef", "access_token", "api_key", "password", "fub-secret-token", "token_hash"):
        assert secret not in r.text, secret
    assert [row.action for row in await _platform_rows("exportco")] == ["tenant.exported"]


async def test_transferring_ownership_leaves_exactly_one_owner_and_nobody_without_access():
    from app.models import User

    tid = await _tenant_with("transferco")
    old = await _account(tid, "old@transfer.test", role="owner")
    new = await _account(tid, "new@transfer.test", role="member")
    pending = await _account(tid, "pending@transfer.test", role="member", status="invited")
    tok = await _op_token()
    async with _client() as c:
        assert (await c.post("/api/v1/platform/tenants/transferco/transfer-ownership", headers=_H(tok),
                             json={"user_id": str(pending)})).status_code == 409
        r = await c.post("/api/v1/platform/tenants/transferco/transfer-ownership", headers=_H(tok),
                         json={"user_id": str(new)})
    assert r.status_code == 200, r.text
    async with SessionLocal() as s:
        roles = {u.email: u.role for u in (await s.execute(select(User).where(User.tenant_id == tid))).scalars().all()}
    assert roles["new@transfer.test"] == "owner" and roles["old@transfer.test"] == "admin"
    assert list(roles.values()).count("owner") == 1
    assert old and [row.action for row in await _platform_rows("transferco")] == ["tenant.ownership_transferred"]


async def test_deleting_a_workspace_needs_its_slug_and_leaves_the_record_of_it():
    from app.models import Tenant

    await _tenant_with("deleteco")
    tok = await _op_token()
    async with _client() as c:
        assert (await c.delete("/api/v1/platform/tenants/deleteco", headers=_H(tok))).status_code == 400
        assert (await c.delete("/api/v1/platform/tenants/deleteco?confirm=deletec", headers=_H(tok))).status_code == 400
        radius = (await c.get("/api/v1/platform/tenants/deleteco/blast-radius", headers=_H(tok))).json()
        assert radius["hosts"] == ["deleteco.brokerage.test"]
        r = await c.delete("/api/v1/platform/tenants/deleteco?confirm=deleteco", headers=_H(tok))
        assert r.status_code == 200 and r.json()["released_hosts"] == ["deleteco.brokerage.test"], r.text
        assert (await c.get("/api/v1/platform/tenants/deleteco", headers=_H(tok))).status_code == 404
    from app.models import Domain

    async with SessionLocal() as s:
        assert (await s.execute(select(Tenant).where(Tenant.slug == "deleteco"))).scalar_one_or_none() is None
        assert (await s.execute(select(Domain).where(Domain.hostname == "deleteco.brokerage.test"))
                ).scalar_one_or_none() is None, "the hostname is released, not left pointing at nothing"
    rows = await _platform_rows("deleteco")
    assert [row.action for row in rows] == ["tenant.deleted"]
    assert rows[0].detail["hosts"] == ["deleteco.brokerage.test"] and rows[0].operator_email == OP_EMAIL
