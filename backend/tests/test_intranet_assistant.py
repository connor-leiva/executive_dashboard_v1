"""The portal assistant.

Most of these are about what it must NOT do. An assistant that answers well is pleasant; an
assistant that cites a document the asker cannot open, or invents a policy for a real-estate team
that will act on it, is a liability -- and both failures look like success from the outside.

No test here reaches Anthropic: `intranet_assistant.ask` is patched at the module seam, except
where the corpus builder is exercised directly, which is a pure function.
"""
import datetime as dt
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import (Business, Domain, IntranetAiQuestion, IntranetAiSetting,
                        IntranetCapability, IntranetContentGap, IntranetCourse, IntranetMember,
                        IntranetPage, IntranetPageSection, IntranetPermission, IntranetRole,
                        IntranetSop, IntranetSopCategory, Tenant, User)
from app.security import hash_pw, make_token
from app.services import intranet_assistant as ia

TRANSPORT = ASGITransport(app=app)


@pytest.fixture(scope="module", autouse=True)
async def _schema():
    from app.seed import seed
    await seed()


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    """The platform key, present for every test unless one is about its absence."""
    from app.config import settings
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-test-not-real")


def _client():
    return AsyncClient(transport=TRANSPORT, base_url="http://testserver")


def _H(token, host):
    return {"Authorization": f"Bearer {token}", "x-tenant-host": host}


async def _workspace(slug: str, *, plan="portfolio"):
    """A workspace with one agent, an SOP, a course and an authored page.

    `plan` is a COLUMN on Tenant, not a config key -- plans.plan_of reads tenant.plan and falls
    back to portfolio only when the value is not a known tier, so putting it in config would have
    silently given every one of these the top plan."""
    host = f"{slug}.localhost"
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        t = Tenant(slug=slug, name=slug.title(), status="active", plan=plan,
                   config={"features": {"intranet": True}})
        s.add(t)
        await s.flush()
        s.add(Domain(tenant_id=t.id, hostname=host, is_primary=True))
        s.add(Business(tenant_id=t.id, key="main", name="Main", tag="Business", sort_order=0))
        agent_user = User(tenant_id=t.id, email=f"agent@{slug}.test", name="Agent",
                          password_hash=hash_pw("password123"), role="member", status="active",
                          tab_access=[], token_version=0)
        s.add(agent_user)
        await s.flush()
        role = IntranetRole(tenant_id=t.id, key="agent", name="Agent", sort=1, published_at=now)
        s.add(role)
        await s.flush()
        s.add(IntranetMember(tenant_id=t.id, full_name="Ada Agent", email=f"agent@{slug}.test",
                             role_id=role.id, status="Active", auth_source="Manual",
                             user_id=agent_user.id, owns="Compliance"))
        cat = IntranetSopCategory(tenant_id=t.id, name="Transactions", sort=1, published_at=now)
        s.add(cat)
        await s.flush()
        s.add(IntranetSop(tenant_id=t.id, title="Under contract checklist", category_id=cat.id,
                          state="Live", published_at=now))
        s.add(IntranetCourse(tenant_id=t.id, title="Listing Mastery", category="Sales",
                             state="Live", sort=1, published_at=now))
        page = IntranetPage(tenant_id=t.id, key="jv", title="JV Partners", nav_group="Partners",
                            sort=1, active=True, published_at=now)
        s.add(page)
        await s.flush()
        s.add(IntranetPageSection(tenant_id=t.id, page_id=page.id, heading="Lending",
                                  body="Sympli handles all lending referrals.", links=[], sort=1,
                                  published_at=now))
        await s.commit()
        return host, t.id, role.id, make_token(agent_user.id, t.id, 0)


def _reply(answer="The v3 checklist is Ada's.", refs=("/sops",), answered=True, failure=None):
    async def fake(s, tenant_id, workspace, content, question):
        return {"answer": answer,
                "citations": [{"title": "Under contract checklist", "kind": "SOP", "ref": r}
                              for r in refs],
                "answered": answered, "failure": failure}
    return fake


async def _ask(host, token, question="Who owns the under contract checklist?"):
    async with _client() as c:
        return await c.post("/api/v1/intranet/ask", json={"question": question},
                            headers=_H(token, host))


# ── the corpus is the member's own payload ────────────────────────────────────────────────

async def test_the_assistant_is_given_only_what_the_member_can_already_see():
    """The single most important property. `_published_content` has already applied the capability
    matrix and every role audience, so building the corpus from its output means the assistant
    CANNOT cite a document the asker could not open. Re-querying the tables here would be a second
    implementation of those rules and a second chance to get them wrong."""
    host, tenant_id, role_id, token = await _workspace("aicorpus")

    # Deny this role the SOP library, exactly as the console would.
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        cap = IntranetCapability(tenant_id=tenant_id, key="sop_library", name="SOPs",
                                 description="", sort=0, published_at=now)
        s.add(cap)
        await s.flush()
        s.add(IntranetPermission(tenant_id=tenant_id, capability_id=cap.id, role_id=role_id,
                                 level="None", published_at=now))
        await s.commit()

    seen = {}

    async def capture(s, tid, workspace, content, question):
        seen["content"] = content
        return {"answer": "", "citations": [], "answered": False, "failure": None}

    original, ia.ask = ia.ask, capture
    try:
        await _ask(host, token)
    finally:
        ia.ask = original

    titles = [e["title"] for e in ia.corpus(seen["content"])]
    assert "Under contract checklist" not in titles, \
        "the assistant was handed an SOP this role is denied"
    assert "Listing Mastery" in titles, "it lost content the member CAN see"


def test_an_sop_with_a_file_says_the_file_was_not_read():
    """The payload carries titles and metadata, not the bytes of an uploaded document. Without
    saying so, a model handed `"file_url": "..."` will happily summarise a document it has never
    seen -- confident, plausible and invented."""
    entry = ia.corpus({"sops": [{"title": "Commission policy", "version": "v2",
                                 "file_url": "/intranet/sops/1/file"}]})[0]
    assert "NOT available" in entry["facts"]["document"]

    without = ia.corpus({"sops": [{"title": "Commission policy", "version": "v2"}]})[0]
    assert without["facts"]["document"] == "no file attached"


def test_people_are_searchable_by_what_they_own():
    """"Who handles compliance" is the question a directory is opened for, and a name-only entry
    cannot answer it."""
    entry = ia.corpus({"directory": [{"name": "Ada", "owns": "Compliance", "title": "Ops"}]})[0]
    assert entry["facts"]["owns"] == "Compliance"


def test_authored_pages_carry_their_prose():
    """The one content type the assistant can actually answer FROM rather than point at."""
    entry = ia.corpus({"pages": [{"title": "JV", "key": "jv", "sections": [
        {"heading": "Lending", "body": "Sympli handles all lending referrals."}]}]})[0]
    assert entry["facts"]["sections"][0]["body"].startswith("Sympli handles")


# ── citations ─────────────────────────────────────────────────────────────────────────────

async def test_a_citation_the_model_invented_is_dropped():
    """A ref that is not in the corpus would become a link in the portal to a document that does
    not exist -- or, worse, one this member is not allowed to open."""
    from app.config import settings

    class FakeBlock:
        type = "tool_use"
        input = {"answer": "See the checklist.",
                 "refs": ["/sops", "/secret/hr-file", "/training/999"], "answered": True}

    class FakeResponse:
        content = [FakeBlock()]

    class FakeMessages:
        async def create(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        messages = FakeMessages()

    original, ia._client = ia._client, FakeClient()
    try:
        async with SessionLocal() as s:
            out = await ia.ask(s, uuid.uuid4(), "Acme",
                               {"sops": [{"title": "Checklist", "version": "v1"}]},
                               "where is it")
    finally:
        ia._client = original

    assert [c["ref"] for c in out["citations"]] == ["/sops"], \
        "a fabricated citation survived into the answer"
    assert out["answered"] is True


async def test_always_cite_turns_an_uncited_answer_into_a_gap():
    """The workspace asked for a citation on every answer. An answer with none did not come from
    their content, whatever it claims -- so it is reported unanswered, which is what opens a gap
    for somebody to go and write the thing down."""
    class FakeBlock:
        type = "tool_use"
        input = {"answer": "Generally, teams split commission 70/30.", "refs": [],
                 "answered": True}

    class FakeResponse:
        content = [FakeBlock()]

    class FakeMessages:
        async def create(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        messages = FakeMessages()

    tenant_id = uuid.uuid4()
    async with SessionLocal() as s:
        s.add(Tenant(id=tenant_id, slug="aicite", name="AiCite", status="active", config={}))
        s.add(IntranetAiSetting(tenant_id=tenant_id, always_cite=True))
        await s.commit()

    original, ia._client = ia._client, FakeClient()
    try:
        async with SessionLocal() as s:
            out = await ia.ask(s, tenant_id, "AiCite",
                               {"sops": [{"title": "Checklist"}]}, "what is the split")
    finally:
        ia._client = original

    assert out["answered"] is False, "an uncited answer was passed off as grounded"


async def test_an_empty_workspace_is_not_charged_for_a_model_call():
    """Nothing to ground an answer in means only one honest answer exists. Paying Anthropic to
    produce it is waste."""
    called = []

    class FakeMessages:
        async def create(self, **kwargs):
            called.append(1)
            return None

    class FakeClient:
        messages = FakeMessages()

    original, ia._client = ia._client, FakeClient()
    try:
        async with SessionLocal() as s:
            out = await ia.ask(s, uuid.uuid4(), "Empty", {}, "anything")
    finally:
        ia._client = original
    assert not called
    assert out["answered"] is False


# ── the three different noes ──────────────────────────────────────────────────────────────

async def test_a_plan_without_the_assistant_is_refused_and_says_which_no_it_is():
    """Three reasons a member cannot ask -- no key, not on the plan, role denied -- and collapsing
    them would send somebody to upgrade a plan that still would not answer."""
    host, _, _, token = await _workspace("aiplan", plan="business")   # portal yes, assistant no
    r = await _ask(host, token)
    assert r.status_code == 402, r.text

    async with _client() as c:
        st = await c.get("/api/v1/intranet/assistant", headers=_H(token, host))
    assert st.json() == {"available": False, "on_plan": False, "configured": True,
                         "permitted": True}


async def test_no_platform_key_is_a_503_not_an_upgrade_prompt(monkeypatch):
    """Nothing the customer can do about this one."""
    from app.config import settings
    host, _, _, token = await _workspace("aikeyless")
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    r = await _ask(host, token)
    assert r.status_code == 503, r.text


async def test_a_role_denied_the_assistant_cannot_ask():
    host, tenant_id, role_id, token = await _workspace("aidenied")
    now = dt.datetime.now(dt.timezone.utc)
    async with SessionLocal() as s:
        cap = IntranetCapability(tenant_id=tenant_id, key="ai_assistant", name="Assistant",
                                 description="", sort=0, published_at=now)
        s.add(cap)
        await s.flush()
        s.add(IntranetPermission(tenant_id=tenant_id, capability_id=cap.id, role_id=role_id,
                                 level="None", published_at=now))
        await s.commit()
    r = await _ask(host, token)
    assert r.status_code == 403, r.text


# ── what gets recorded ────────────────────────────────────────────────────────────────────

async def test_every_question_is_stored_with_who_asked_it():
    host, tenant_id, _, token = await _workspace("aistore")
    original, ia.ask = ia.ask, _reply()
    try:
        r = await _ask(host, token, "Who owns the checklist?")
    finally:
        ia.ask = original
    assert r.status_code == 200, r.text

    async with SessionLocal() as s:
        rows = (await s.execute(select(IntranetAiQuestion).where(
            IntranetAiQuestion.tenant_id == tenant_id))).scalars().all()
    assert len(rows) == 1
    assert rows[0].question == "Who owns the checklist?"
    assert rows[0].asker_label == "Ada Agent"
    assert rows[0].answered is True
    assert rows[0].citations[0]["ref"] == "/sops"


async def test_an_unanswered_question_opens_a_content_gap():
    host, tenant_id, _, token = await _workspace("aigap")
    original, ia.ask = ia.ask, _reply(answer="That is not written down here.", refs=(),
                                      answered=False)
    try:
        await _ask(host, token, "What is the parental leave policy?")
        await _ask(host, token, "what is the PARENTAL leave policy?")   # same question, shouted
    finally:
        ia.ask = original

    async with SessionLocal() as s:
        gaps = (await s.execute(select(IntranetContentGap).where(
            IntranetContentGap.tenant_id == tenant_id))).scalars().all()
    assert len(gaps) == 1, "ten people asking the same thing must be one thing to write"
    assert gaps[0].ask_count == 2
    assert gaps[0].status == "Open"


async def test_asking_again_reopens_a_gap_somebody_marked_resolved():
    """The resolution said the content now exists and the assistant just said it cannot find it.
    One of those is wrong, and an admin should be the one to decide which."""
    host, tenant_id, _, token = await _workspace("aireopen")
    original, ia.ask = ia.ask, _reply(refs=(), answered=False)
    try:
        await _ask(host, token, "Where is the referral agreement?")
        async with SessionLocal() as s:
            gap = (await s.execute(select(IntranetContentGap).where(
                IntranetContentGap.tenant_id == tenant_id))).scalar_one()
            gap.status = "Resolved"
            await s.commit()
        await _ask(host, token, "Where is the referral agreement?")
    finally:
        ia.ask = original

    async with SessionLocal() as s:
        gap = (await s.execute(select(IntranetContentGap).where(
            IntranetContentGap.tenant_id == tenant_id))).scalar_one()
    assert gap.status == "Open"


async def test_an_outage_is_not_filed_as_a_hole_in_the_customers_documentation():
    """Our incident, not their missing SOP. Filing it as a content gap fills an admin's to-do list
    with work that does not exist."""
    host, tenant_id, _, token = await _workspace("aioutage")
    original, ia.ask = ia.ask, _reply(answer="", refs=(), answered=False,
                                      failure="APIConnectionError: boom")
    try:
        r = await _ask(host, token, "anything at all")
    finally:
        ia.ask = original

    assert r.status_code == 503, "an outage was reported as an answer"
    async with SessionLocal() as s:
        gaps = (await s.execute(select(IntranetContentGap).where(
            IntranetContentGap.tenant_id == tenant_id))).scalars().all()
        asked = (await s.execute(select(IntranetAiQuestion).where(
            IntranetAiQuestion.tenant_id == tenant_id))).scalars().all()
    assert not gaps, "an outage opened a content gap"
    assert len(asked) == 1 and asked[0].failure, \
        "the question was not recorded, so nobody can see the outage happened"


async def test_a_question_is_recorded_even_when_the_answer_fails_to_reach_the_asker():
    """The commit happens before the 503. A question that vanished because the model was down is
    a question nobody can learn from."""
    host, tenant_id, _, token = await _workspace("aicommit")
    original, ia.ask = ia.ask, _reply(answer="", refs=(), answered=False, failure="boom")
    try:
        await _ask(host, token, "did this survive")
    finally:
        ia.ask = original
    async with SessionLocal() as s:
        rows = (await s.execute(select(IntranetAiQuestion).where(
            IntranetAiQuestion.tenant_id == tenant_id))).scalars().all()
    assert len(rows) == 1 and rows[0].question == "did this survive"


# ── the console's view ────────────────────────────────────────────────────────────────────

async def test_a_workspace_only_ever_sees_its_own_questions():
    host_a, tenant_a, _, token_a = await _workspace("aiqone")
    host_b, tenant_b, _, token_b = await _workspace("aiqtwo")
    original, ia.ask = ia.ask, _reply()
    try:
        await _ask(host_a, token_a, "A's private question")
        await _ask(host_b, token_b, "B's private question")
    finally:
        ia.ask = original

    async with SessionLocal() as s:
        rows = (await s.execute(select(IntranetAiQuestion).where(
            IntranetAiQuestion.tenant_id == tenant_b))).scalars().all()
    assert [r.question for r in rows] == ["B's private question"]
