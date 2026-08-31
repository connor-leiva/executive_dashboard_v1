"""Campaign grouping as CONFIGURATION. SPEC-ads-module.md Part 10.3.

Grouping decides which business line a campaign's spend belongs to, and it was editable only by
someone with database access. That is the wrong shape for a product: a new campaign lands in
Other the moment it is created, and nobody outside engineering can move it.

The live failure that prompted this is in the first test and is worth reading before the rest.
"""
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import AdAccount, AdCampaign, Integration, Tenant, User
from app.security import hash_pw, make_token
from app.seed import seed
from app.services import ads_rules as R

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _seeded():
    await seed()


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(t):
    return {"Authorization": f"Bearer {t}"}


async def _owner_token():
    async with _client() as c:
        r = await c.post("/api/v1/auth/login",
                         json={"email": "spring@springb.com", "password": "springtime"})
    return r.json()["token"]


# -- the classifier --------------------------------------------------------------------
def test_the_shipped_default_matches_real_meta_naming():
    """THE LIVE FAILURE. The default rule matches the prefix `kb-`, taken from the mockup where
    campaigns read `KB-Webinar-Retarget`. The live account names them `KB - The Shift - August2026`
    - spaces around the dash - so nothing matched, all nineteen campaigns fell to Other, and
    "Where it came from" rendered as one undifferentiated $119,537 bar.

    Two spaces. Nothing in the code was wrong in a way anybody could see."""
    assert R.classify_campaign("KB - The Shift - August2026") == "KB · The Shift"
    assert R.classify_campaign("KB-Webinar-Retarget") == "KB · Webinar"     # still works


def test_the_split_label_keeps_the_accounts_own_capitalisation():
    """Title-casing renders ForumVIP as "Forumvip" and BeCollective as "Becollective". The
    account's own capitalisation is how its operator recognises the thing in a list."""
    assert R.classify_campaign("KB - ForumVIP - SubmitApplication") == "KB · ForumVIP"
    assert R.classify_campaign("KB - BeCollective") == "KB · BeCollective"


def test_a_multi_word_segment_survives_normalisation():
    """Collapsing spaces and dashes into one separator would flatten `KB - The Shift` into four
    equal tokens and label the group "The". Whitespace AROUND a dash is a separator; whitespace
    on its own is part of the name."""
    assert R.classify_campaign(
        "KB - Utah Life Mastermind - Lead Form") == "KB · Utah Life Mastermind"


def test_en_and_em_dashes_count_as_separators():
    """Meta's own campaign names contain them - `Event Name Research - Be Collective` came off
    the live account with an en dash."""
    assert R.classify_campaign(
        "Event Name Research – Be Collective") == "Event Name Research"


def test_an_unmatched_campaign_still_falls_to_other_rather_than_guessing():
    """Other is a real answer and the read service counts it. Inventing a group for an unmatched
    name would hide the drift this whole mechanism exists to surface."""
    assert R.classify_campaign("Generate Inner Circle Interest [Messages]") == R.FALLBACK_GROUP
    assert R.classify_campaign("") == R.FALLBACK_GROUP


def test_a_rule_typed_with_spaces_behaves_like_one_typed_without():
    """Somebody reading their own campaign names will type the prefix the way they see it."""
    spaced = [{"match": "prefix", "value": "KB - ", "label": "KB", "split": True}]
    tight = [{"match": "prefix", "value": "kb-", "label": "KB", "split": True}]
    for rules in (spaced, tight):
        assert R.classify_campaign("KB - The Shift - August2026", rules) == "KB · The Shift"


# -- validation ------------------------------------------------------------------------
def test_null_rules_are_valid_and_mean_the_defaults():
    assert R.validate_group_rules(None) == []


def test_malformed_rules_are_named_rather_than_silently_accepted():
    """classify_campaign does not raise on nonsense, which is worse rather than better: a
    malformed rule set classifies EVERYTHING as Other, and that is indistinguishable from a
    naming drift somebody would then go hunting for in Ads Manager."""
    assert R.validate_group_rules("not a list")
    assert R.validate_group_rules([{"match": "regex", "value": "x"}])       # unsupported kind
    assert R.validate_group_rules([{"match": "prefix", "value": "   "}])    # nothing to match
    assert R.validate_group_rules([{"match": "prefix", "value": "x", "split": "yes"}])
    assert R.validate_group_rules([{"match": "prefix", "value": "x", "label": 7}])
    assert R.validate_group_rules([{"match": "prefix", "value": "x"}] * (R.MAX_RULES + 1))
    assert R.validate_group_rules(["not an object"])


def test_a_usable_rule_set_passes():
    assert R.validate_group_rules([
        {"match": "prefix", "value": "kb - ", "label": "KB", "split": True},
        {"match": "contains", "value": "webinar", "label": "Webinar"},
    ]) == []


# -- the API ---------------------------------------------------------------------------
CAMPAIGNS = ("KB - The Shift - August2026", "Generate Inner Circle Interest [Messages]")


async def _account_fixture():
    """REUSE or create, and reset the rules every time.

    (tenant_id, external_id) is unique on ad_account, so a helper that unconditionally inserts
    works for exactly one test and then fails the rest of the file with an IntegrityError that
    points at the fixture rather than at anything under test. Resetting group_rules matters just
    as much: one test writes rules, and without the reset the next one inherits them and asserts
    against a default that is no longer in force.
    """
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        acct = (await s.execute(select(AdAccount).where(
            AdAccount.tenant_id == t.id,
            AdAccount.external_id == "act_group"))).scalar_one_or_none()
        if acct is None:
            integ = Integration(tenant_id=t.id, provider="meta_ads", status="connected")
            s.add(integ)
            await s.flush()
            acct = AdAccount(tenant_id=t.id, integration_id=integ.id, platform="meta",
                             external_id="act_group", name="Grouping", timezone_name="UTC")
            s.add(acct)
            await s.flush()
            for nm in CAMPAIGNS:
                s.add(AdCampaign(tenant_id=t.id, ad_account_id=acct.id,
                                 external_id=f"c_{nm[:8]}", name=nm))
        acct.group_rules = None
        await s.commit()
        return str(acct.id), str(acct.integration_id)


async def test_the_editor_gets_the_rules_the_defaults_and_every_campaigns_group():
    """One payload, because the editor is useless without all of it at once - what makes rule
    writing tractable is seeing which campaigns land where."""
    acct_id, _ = await _account_fixture()
    tok = await _owner_token()
    async with _client() as c:
        r = await c.get(f"/api/v1/ads/grouping?account={acct_id}", headers=_H(tok))
    assert r.status_code == 200
    b = r.json()
    assert b["using_defaults"] is True and b["rules"] is None
    assert b["defaults"] and b["fallback"] == "Other"
    names = {c["name"]: c["group"] for c in b["campaigns"]}
    assert names["KB - The Shift - August2026"] == "KB · The Shift"
    assert names["Generate Inner Circle Interest [Messages]"] == "Other"
    assert b["unmatched"] == 1, "an unmatched campaign must be counted, not hidden"


async def test_saving_rules_regroups_immediately_and_stores_nothing_derived():
    """Groups are resolved live, never stored - the tab says so. Renaming a campaign or editing a
    rule must regroup on the next read rather than leaving spend where it was."""
    acct_id, _ = await _account_fixture()
    tok = await _owner_token()
    rules = [{"match": "contains", "value": "inner circle", "label": "Inner Circle"}]
    async with _client() as c:
        w = await c.patch(f"/api/v1/ads/accounts/{acct_id}",
                          json={"group_rules": rules}, headers=_H(tok))
        assert w.status_code == 200
        r = await c.get(f"/api/v1/ads/grouping?account={acct_id}", headers=_H(tok))
    b = r.json()
    names = {c["name"]: c["group"] for c in b["campaigns"]}
    assert names["Generate Inner Circle Interest [Messages]"] == "Inner Circle"
    assert names["KB - The Shift - August2026"] == "Other", "the new rule set replaces the defaults"
    assert b["using_defaults"] is False


async def test_the_preview_shows_what_a_rule_set_would_do_without_saving_it():
    """The preview is the feature. The live failure was a default that looked correct and
    classified all nineteen campaigns as Other; nobody could have caught that by reading rules.

    Server-side on purpose: classifying in the browser would put the same logic in two languages,
    and the copy the editor showed would be the untested one - so the preview would drift from
    the answer and quietly stop predicting it."""
    acct_id, _ = await _account_fixture()
    tok = await _owner_token()
    proposed = [{"match": "contains", "value": "shift", "label": "Shift"}]
    async with _client() as c:
        r = await c.post(f"/api/v1/ads/grouping/preview?account={acct_id}",
                         json={"rules": proposed}, headers=_H(tok))
        assert r.status_code == 200
        assert {c["name"]: c["group"] for c in r.json()["campaigns"]}[
            "KB - The Shift - August2026"] == "Shift"

        # ...and nothing was written.
        saved = await c.get(f"/api/v1/ads/grouping?account={acct_id}", headers=_H(tok))
    assert saved.json()["using_defaults"] is True, "the preview saved the rules"


async def test_a_half_typed_rule_previews_as_the_defaults_rather_than_wiping_the_screen():
    """This endpoint is called on every keystroke. A rule mid-typing is not an error yet, and
    flashing every campaign into Other while somebody is still typing reads as "you just broke
    it" - so problems come back beside a still-useful preview instead of as a 400."""
    acct_id, _ = await _account_fixture()
    tok = await _owner_token()
    async with _client() as c:
        r = await c.post(f"/api/v1/ads/grouping/preview?account={acct_id}",
                         json={"rules": [{"match": "prefix", "value": ""}]}, headers=_H(tok))
    assert r.status_code == 200
    b = r.json()
    assert b["problems"], "an unusable rule set must say so"
    assert {c["name"]: c["group"] for c in b["campaigns"]}[
        "KB - The Shift - August2026"] == "KB · The Shift"


async def test_the_preview_is_tab_gated():
    acct_id, _ = await _account_fixture()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        u = User(tenant_id=t.id, email="nopreview@springb.com", name="np",
                 password_hash=hash_pw("x"), role="member", status="active",
                 tab_access=["portfolio"], token_version=0)
        s.add(u)
        await s.commit()
        tok = make_token(u.id, t.id, 0)
    async with _client() as c:
        r = await c.post(f"/api/v1/ads/grouping/preview?account={acct_id}",
                         json={"rules": []}, headers=_H(tok))
    assert r.status_code == 403


async def test_a_malformed_rule_set_is_refused_rather_than_written():
    acct_id, _ = await _account_fixture()
    tok = await _owner_token()
    async with _client() as c:
        r = await c.patch(f"/api/v1/ads/accounts/{acct_id}",
                          json={"group_rules": [{"match": "regex", "value": ".*"}]},
                          headers=_H(tok))
    assert r.status_code == 400
    assert "prefix" in r.text and "contains" in r.text     # says what IS allowed


async def test_a_member_can_read_the_grouping_but_not_rewrite_it():
    """Reading is tab-gated; writing moves every revenue figure between business lines and is
    owner/admin. Authorization is enforced on the ENDPOINT, never only in the nav."""
    acct_id, _ = await _account_fixture()
    async with SessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.slug == "springb"))).scalar_one()
        u = User(tenant_id=t.id, email="grouper@springb.com", name="grouper",
                 password_hash=hash_pw("x"), role="member", status="active",
                 tab_access=["portfolio", "ads"], token_version=0)
        s.add(u)
        await s.commit()
        tok = make_token(u.id, t.id, 0)

    async with _client() as c:
        assert (await c.get(f"/api/v1/ads/grouping?account={acct_id}",
                            headers=_H(tok))).status_code == 200
        w = await c.patch(f"/api/v1/ads/accounts/{acct_id}",
                          json={"group_rules": []}, headers=_H(tok))
    assert w.status_code == 403


async def test_grouping_for_an_account_in_another_workspace_does_not_resolve():
    from app.services.provisioning import provision_tenant

    async with SessionLocal() as s:
        res = await provision_tenant(s, slug="grpco", name="Grp Co",
                                     owner_email="owner@grpco.test", hostname="grpco.localhost")
        other = res.tenant_id
        integ = Integration(tenant_id=other, provider="meta_ads", status="connected")
        s.add(integ)
        await s.flush()
        acct = AdAccount(tenant_id=other, integration_id=integ.id, platform="meta",
                         external_id="act_other", name="Theirs")
        s.add(acct)
        await s.commit()
        foreign = str(acct.id)

    try:
        tok = await _owner_token()
        async with _client() as c:
            r = await c.get(f"/api/v1/ads/grouping?account={foreign}", headers=_H(tok))
        assert r.status_code == 404
        assert "act_other" not in r.text and "Theirs" not in r.text
    finally:
        from sqlalchemy import delete as sa_delete

        from app.models import Base
        async with SessionLocal() as s:
            for tbl in [t for t in reversed(Base.metadata.sorted_tables) if "tenant_id" in t.c]:
                await s.execute(sa_delete(tbl).where(tbl.c.tenant_id == other))
            tt = await s.get(Tenant, other)
            if tt is not None:
                await s.delete(tt)
            await s.commit()
