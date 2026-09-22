"""The portal a plan already includes, given to a workspace provisioned before it existed.

The gap this covers: `plans.allows(tenant, "intranet")` says a workspace may HAVE a portal, and
the app switcher additionally requires an `intranet_workspace` row, which is the portal EXISTING.
Only `provision_tenant` ever created that row, from 2026-09-03 onward, so every workspace older
than that is entitled to something it does not have and nothing in the product says so. `springb`
is the real one.

Worth stating because it is the failure this backfill must not become: the only other thing that
creates these rows is `scripts/seed_intranet.py`, which is one customer's real staff list and
courses. A backfill that reached for it would put Utah Life's people in Spring's workspace.
"""
import uuid

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal, engine
from app.models import (
    Base,
    IntranetCapability,
    IntranetMember,
    IntranetPermission,
    IntranetRole,
    IntranetSetupTask,
    IntranetWorkspace,
    Tenant,
    User,
)
from scripts.backfill_intranet import BackfillError, _run


async def _workspace(plan: str = "portfolio", *, owners: int = 1) -> tuple[uuid.UUID, str]:
    """A tenant as it existed BEFORE the bootstrap was wired in: no intranet rows at all.

    Built by hand rather than through `provision_tenant`, because provisioning now creates the
    very rows this backfill exists to add -- a fixture that used it would test nothing.
    """
    slug = f"pre{uuid.uuid4().hex[:8]}"
    # Other modules get their schema from seed(); these tests never seed, because a seeded
    # workspace is the state this backfill exists to NOT be in.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        tenant = Tenant(slug=slug, name="Before The Portal", plan=plan)
        s.add(tenant)
        await s.flush()
        for i in range(owners):
            s.add(User(tenant_id=tenant.id, email=f"owner{i}@{slug}.test",
                       name=f"Owner {i}", role="owner"))
        await s.commit()
        return tenant.id, slug


async def _count(model, tenant_id) -> int:
    async with SessionLocal() as s:
        return (await s.execute(select(func.count()).select_from(model).where(
            model.tenant_id == tenant_id))).scalar_one()


@pytest.mark.anyio
async def test_it_gives_an_older_workspace_the_structure_and_nothing_else():
    tid, slug = await _workspace()
    assert await _count(IntranetWorkspace, tid) == 0

    out = await _run(slug, None, dry_run=False, force=False)

    assert out["action"] == "created"
    assert await _count(IntranetWorkspace, tid) == 1
    assert await _count(IntranetRole, tid) == 3                 # Owner / Manager / Member
    assert await _count(IntranetCapability, tid) == 8
    assert await _count(IntranetPermission, tid) == 24          # every capability x every role
    assert await _count(IntranetSetupTask, tid) == 8

    # STRUCTURE ONLY. The owner is the single member; no roster, and the checklist starts empty
    # because nothing has been configured yet.
    async with SessionLocal() as s:
        members = (await s.execute(select(IntranetMember).where(
            IntranetMember.tenant_id == tid))).scalars().all()
        assert [m.email for m in members] == [f"owner0@{slug}.test"]
        assert all(t.completed_at is None for t in (await s.execute(select(IntranetSetupTask).where(
            IntranetSetupTask.tenant_id == tid))).scalars().all())


@pytest.mark.anyio
async def test_the_owner_can_open_the_console_afterwards():
    """The whole point of the member row: console_access=Full on the Owner role, and the member
    joined to the existing User so signing in actually lands on it."""
    tid, slug = await _workspace()
    await _run(slug, None, dry_run=False, force=False)

    async with SessionLocal() as s:
        member = (await s.execute(select(IntranetMember).where(
            IntranetMember.tenant_id == tid))).scalars().one()
        user = (await s.execute(select(User).where(User.tenant_id == tid))).scalars().one()
        assert member.user_id == user.id, "the member must be the same person as the account"
        assert member.status == "Active"

        cap = (await s.execute(select(IntranetCapability).where(
            IntranetCapability.tenant_id == tid,
            IntranetCapability.key == "console_access"))).scalars().one()
        level = (await s.execute(select(IntranetPermission.level).where(
            IntranetPermission.tenant_id == tid,
            IntranetPermission.capability_id == cap.id,
            IntranetPermission.role_id == member.role_id))).scalar_one()
        assert level == "Full"


@pytest.mark.anyio
async def test_running_it_twice_changes_nothing():
    """Prod backfills get re-run -- by a retry, or by somebody who is not sure it worked. The
    second run must not duplicate the roles or reset a permission an admin has since changed."""
    tid, slug = await _workspace()
    await _run(slug, None, dry_run=False, force=False)

    async with SessionLocal() as s:                       # an admin's later change
        role = (await s.execute(select(IntranetRole).where(
            IntranetRole.tenant_id == tid, IntranetRole.key == "member"))).scalars().one()
        role.name = "Agent"
        await s.commit()

    out = await _run(slug, None, dry_run=False, force=False)

    assert out["action"] == "no-op"
    assert await _count(IntranetRole, tid) == 3
    async with SessionLocal() as s:
        names = (await s.execute(select(IntranetRole.name).where(
            IntranetRole.tenant_id == tid))).scalars().all()
    assert "Agent" in names, "a re-run must not reset what the workspace renamed"


@pytest.mark.anyio
async def test_a_dry_run_writes_nothing():
    tid, slug = await _workspace()
    out = await _run(slug, None, dry_run=True, force=False)
    assert out["action"] == "would create"
    assert await _count(IntranetWorkspace, tid) == 0


@pytest.mark.anyio
async def test_a_plan_without_the_portal_is_refused_rather_than_half_built():
    """Building it anyway would leave rows the app switcher never offers, which reads as a broken
    backfill instead of the wrong plan."""
    tid, slug = await _workspace(plan="team")
    with pytest.raises(BackfillError, match="does not include the portal"):
        await _run(slug, None, dry_run=False, force=False)
    assert await _count(IntranetWorkspace, tid) == 0

    out = await _run(slug, None, dry_run=False, force=True)      # said on purpose
    assert out["action"] == "created"


@pytest.mark.anyio
async def test_two_owners_is_a_question_for_the_operator():
    _tid, slug = await _workspace(owners=2)
    with pytest.raises(BackfillError, match="--owner-email"):
        await _run(slug, None, dry_run=False, force=False)


@pytest.mark.anyio
async def test_an_unknown_slug_says_so():
    with pytest.raises(BackfillError, match="no workspace with slug"):
        await _run("nosuchworkspace", None, dry_run=False, force=False)
