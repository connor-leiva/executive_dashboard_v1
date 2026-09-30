"""Onboarding — API. ONBOARDING-SPEC.md §6.

Every route requires the `onboarding` tab (owners and admins pass implicitly). Beyond that:

  READS are scoped by `services.onboarding.plan_summaries` / `readable_plan`, which limit a
  member to their OWN plan. The tab grant decides whether Onboarding is in somebody's rail; it
  does not decide whether they may read a colleague's day-by-day account of a month that went
  badly. Owners and admins see every plan in the workspace, which is the point of the module.

  WRITES additionally require `may_write` -- the subject, or an owner/admin. Every write is
  audited with the actor, because "who ticked this off" is a question a thirty-day review can
  reasonably ask and the row itself does not record it.

The write surface is deliberately small: tick a block, judge its outcome, write the day's
debrief, move a scoreboard number, add or remove a script, log or update a conversation. There
is no endpoint that edits the PLAN -- the days, the blocks and the targets are authored
elsewhere and seeded. An editor is a later phase, and shipping one now would mean the first
person using the module could rewrite the programme they are being measured against.
"""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..deps import assert_tab, current_user
from ..models import (OnboardingBlock, OnboardingConversation, OnboardingDay, OnboardingPlan,
                      OnboardingScript, OnboardingTarget, User)
from ..services import onboarding as eng
from ..services.audit import audit

TAB = "onboarding"

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


# ── request bodies ────────────────────────────────────────────────────────────────────────
class BlockPatch(BaseModel):
    done: bool | None = None
    # "hit" | "miss" | "" — the empty string CLEARS it, which None cannot mean here because
    # None is already "this field was not sent".
    outcome_state: str | None = None


class DebriefPatch(BaseModel):
    debrief: str = Field(default="", max_length=8000)


class TargetPatch(BaseModel):
    actual: int | None = Field(default=None, ge=0)
    done: bool | None = None


class ScriptCreate(BaseModel):
    motion: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=8000)


class ConversationCreate(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    team: str | None = Field(default=None, max_length=160)
    context: str | None = Field(default=None, max_length=4000)
    next_step: str | None = Field(default=None, max_length=4000)


class ConversationPatch(BaseModel):
    heat: str | None = None                      # hot | warm | no | "" to clear
    appointment_set: bool | None = None


# ── helpers ───────────────────────────────────────────────────────────────────────────────
async def _writable_plan(s: AsyncSession, user: User, plan_id) -> OnboardingPlan:
    """The plan this user may write to, or 403/404. Used by every mutation."""
    await assert_tab(user, s, TAB)
    plan = await eng.readable_plan(s, user.tenant_id, user, plan_id)
    if plan is None:
        raise HTTPException(404, "No such plan")
    if not eng.may_write(user, plan):
        raise HTTPException(403, "This plan is not yours to change")
    return plan


async def _block(s: AsyncSession, user: User, block_id: uuid.UUID
                 ) -> tuple[OnboardingBlock, OnboardingDay, OnboardingPlan]:
    """A block, its day and its plan — resolved TOGETHER and tenant-scoped.

    The tenant filter is on the block itself rather than inferred from the plan: an id from
    another workspace must 404 here, not walk up to a plan check that would also have refused
    it. One of the two is enough; both is what makes it not matter which is read first.
    """
    b = (await s.execute(select(OnboardingBlock).where(
        OnboardingBlock.id == block_id,
        OnboardingBlock.tenant_id == user.tenant_id))).scalar_one_or_none()
    if b is None:
        raise HTTPException(404, "No such block")
    d = await s.get(OnboardingDay, b.day_id)
    plan = await _writable_plan(s, user, d.plan_id)
    return b, d, plan


# ── read ──────────────────────────────────────────────────────────────────────────────────
@router.get("")
async def get_onboarding(plan_id: uuid.UUID | None = Query(default=None),
                         user: User = Depends(current_user),
                         s: AsyncSession = Depends(get_session)):
    """The tab, in one request: which plans this user may open, and the full detail of one.

    Five views over one plan means one payload -- five endpoints would be five round trips for
    one screen, and four chances for them to disagree about how far along somebody is.
    """
    await assert_tab(user, s, TAB)
    plans = await eng.plan_summaries(s, user.tenant_id, user)
    plan = await eng.readable_plan(s, user.tenant_id, user, plan_id)
    if plan_id is not None and plan is None:
        raise HTTPException(404, "No such plan")
    return {"plans": plans,
            "plan": await eng.plan_payload(s, plan, user) if plan is not None else None}


# ── progress ──────────────────────────────────────────────────────────────────────────────
@router.patch("/blocks/{block_id}")
async def patch_block(block_id: uuid.UUID, body: BlockPatch,
                      user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session)):
    b, day, plan = await _block(s, user, block_id)
    if body.done is not None:
        b.done = body.done
        b.done_at = dt.datetime.now(dt.timezone.utc) if body.done else None
    if body.outcome_state is not None:
        if body.outcome_state not in ("hit", "miss", ""):
            raise HTTPException(400, "outcome_state must be hit, miss or empty")
        b.outcome_state = body.outcome_state or None
    audit(s, user.tenant_id, user.id, "onboarding.block", "onboarding_block", b.id,
          {"done": b.done, "outcome": b.outcome_state})
    await s.commit()
    return {"id": str(b.id), "done": b.done, "outcome_state": b.outcome_state}


@router.patch("/days/{day_id}/debrief")
async def patch_debrief(day_id: uuid.UUID, body: DebriefPatch,
                        user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session)):
    d = (await s.execute(select(OnboardingDay).where(
        OnboardingDay.id == day_id,
        OnboardingDay.tenant_id == user.tenant_id))).scalar_one_or_none()
    if d is None:
        raise HTTPException(404, "No such day")
    await _writable_plan(s, user, d.plan_id)
    d.debrief = body.debrief or None
    d.debrief_at = dt.datetime.now(dt.timezone.utc) if d.debrief else None
    # The text itself is NOT in the audit payload. It is somebody's account of their own day,
    # and an audit log is read by more people than the plan is.
    audit(s, user.tenant_id, user.id, "onboarding.debrief", "onboarding_day", d.id,
          {"chars": len(d.debrief or "")})
    await s.commit()
    return {"id": str(d.id), "debrief": d.debrief or ""}


@router.patch("/targets/{target_id}")
async def patch_target(target_id: uuid.UUID, body: TargetPatch,
                       user: User = Depends(current_user),
                       s: AsyncSession = Depends(get_session)):
    t = (await s.execute(select(OnboardingTarget).where(
        OnboardingTarget.id == target_id,
        OnboardingTarget.tenant_id == user.tenant_id))).scalar_one_or_none()
    if t is None:
        raise HTTPException(404, "No such target")
    await _writable_plan(s, user, t.plan_id)
    if body.actual is not None:
        t.actual = body.actual
    if body.done is not None:
        t.done = body.done
    audit(s, user.tenant_id, user.id, "onboarding.target", "onboarding_target", t.id,
          {"actual": t.actual, "done": t.done})
    await s.commit()
    return {"id": str(t.id), "actual": t.actual, "done": t.done}


# ── the script library ────────────────────────────────────────────────────────────────────
@router.post("/plans/{plan_id}/scripts", status_code=201)
async def add_script(plan_id: uuid.UUID, body: ScriptCreate,
                     user: User = Depends(current_user),
                     s: AsyncSession = Depends(get_session)):
    plan = await _writable_plan(s, user, plan_id)
    # The motion must be one the plan declares. Free text would let a typo create a fourth
    # column that looks like a fifth motion and holds one line nobody finds again.
    if body.motion not in (plan.motions or []):
        raise HTTPException(400, "Unknown motion for this plan")
    row = OnboardingScript(tenant_id=user.tenant_id, plan_id=plan.id,
                           motion=body.motion, text=body.text)
    s.add(row)
    audit(s, user.tenant_id, user.id, "onboarding.script_add", "onboarding_plan", plan.id,
          {"motion": body.motion})
    await s.commit()
    return {"id": str(row.id), "motion": row.motion, "text": row.text,
            "created_at": row.created_at.isoformat() if row.created_at else None}


@router.delete("/scripts/{script_id}")
async def delete_script(script_id: uuid.UUID, user: User = Depends(current_user),
                        s: AsyncSession = Depends(get_session)):
    row = (await s.execute(select(OnboardingScript).where(
        OnboardingScript.id == script_id,
        OnboardingScript.tenant_id == user.tenant_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "No such script")
    await _writable_plan(s, user, row.plan_id)
    await s.delete(row)
    audit(s, user.tenant_id, user.id, "onboarding.script_delete", "onboarding_script", script_id)
    await s.commit()
    # A body, not a 204: the SPA's delJSON parses the response, and an empty one throws in the
    # client rather than anywhere a server log would show it.
    return {"ok": True}


# ── the conversation log ──────────────────────────────────────────────────────────────────
@router.post("/plans/{plan_id}/conversations", status_code=201)
async def add_conversation(plan_id: uuid.UUID, body: ConversationCreate,
                           user: User = Depends(current_user),
                           s: AsyncSession = Depends(get_session)):
    plan = await _writable_plan(s, user, plan_id)
    row = OnboardingConversation(tenant_id=user.tenant_id, plan_id=plan.id, name=body.name,
                                 team=body.team, context=body.context, next_step=body.next_step)
    s.add(row)
    audit(s, user.tenant_id, user.id, "onboarding.convo_add", "onboarding_plan", plan.id)
    await s.commit()
    return {"id": str(row.id), "name": row.name, "team": row.team, "context": row.context,
            "next_step": row.next_step, "heat": row.heat,
            "appointment_set": row.appointment_set,
            "created_at": row.created_at.isoformat() if row.created_at else None}


@router.patch("/conversations/{convo_id}")
async def patch_conversation(convo_id: uuid.UUID, body: ConversationPatch,
                             user: User = Depends(current_user),
                             s: AsyncSession = Depends(get_session)):
    row = (await s.execute(select(OnboardingConversation).where(
        OnboardingConversation.id == convo_id,
        OnboardingConversation.tenant_id == user.tenant_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "No such conversation")
    await _writable_plan(s, user, row.plan_id)
    if body.heat is not None:
        if body.heat not in ("hot", "warm", "no", ""):
            raise HTTPException(400, "heat must be hot, warm, no or empty")
        row.heat = body.heat or None
    if body.appointment_set is not None:
        row.appointment_set = body.appointment_set
    audit(s, user.tenant_id, user.id, "onboarding.convo", "onboarding_conversation", row.id,
          {"heat": row.heat, "appointment_set": row.appointment_set})
    await s.commit()
    return {"id": str(row.id), "heat": row.heat, "appointment_set": row.appointment_set}


@router.delete("/conversations/{convo_id}")
async def delete_conversation(convo_id: uuid.UUID, user: User = Depends(current_user),
                              s: AsyncSession = Depends(get_session)):
    row = (await s.execute(select(OnboardingConversation).where(
        OnboardingConversation.id == convo_id,
        OnboardingConversation.tenant_id == user.tenant_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "No such conversation")
    await _writable_plan(s, user, row.plan_id)
    await s.delete(row)
    audit(s, user.tenant_id, user.id, "onboarding.convo_delete", "onboarding_conversation",
          convo_id)
    await s.commit()
    return {"ok": True}
