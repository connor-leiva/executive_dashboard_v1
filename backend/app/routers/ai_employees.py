"""AI Employees — API (SPEC-ai-employees-tab §5).

Every route is gated by AI_EMPLOYEES_ENABLED (404 when off). Reads require the
`ai_employees` tab grant (owners/admins pass implicitly); mutations require owner/admin
(a member-with-grant gets 403). Governance: approving marks artifacts `approved`; they
only reach `shipped` when BOTH writeback gates are open (env flag + per-employee toggle),
and shipping an unapproved artifact is a 409. Nothing here calls the model — the worker
(ai_execute) does; these endpoints queue runs and manage the human-approval lifecycle.
"""
from __future__ import annotations

import datetime as dt
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..deps import current_user, require_role, assert_tab
from ..models import (User, Tenant, AISkill, AIEmployee, AIEmployeeSkill, AIRun, AIArtifact,
                      AIRosterAccount, AIIntelEntry)
from ..security import make_capability, read_capability
from ..services.audit import audit
from ..services import ai_employees as eng
from ..services.ai_skills import SKILLS, SKILL_KEYS, KIND_META

TAB = "ai_employees"


def ai_enabled() -> None:
    """Router-wide gate — the whole surface 404s when the feature flag is off."""
    if not settings.AI_EMPLOYEES_ENABLED:
        raise HTTPException(404, "AI Employees is not enabled")


router = APIRouter(prefix="/ai", tags=["ai"], dependencies=[Depends(ai_enabled)])

manager = require_role("owner", "admin")     # mutations; member-with-grant → 403


# ── request bodies ────────────────────────────────────────────────────────────
class EmployeeCreate(BaseModel):
    name: str
    role_title: str = "Social Media Manager"
    avatar_color: str = "#227175"
    config: dict | None = None


class EmployeePatch(BaseModel):
    name: str | None = None
    role_title: str | None = None
    avatar_color: str | None = None
    status: str | None = None                # active | paused
    writeback_enabled: bool | None = None
    config: dict | None = None


class SkillPatch(BaseModel):
    enabled: bool | None = None
    prompt_override: str | None = None
    schedule_override: str | None = None
    config: dict | None = None


class RunCreate(BaseModel):
    material: str | None = None              # pasted post text / audit source
    handle: str | None = None                # the account being audited
    context: dict | None = None              # any extra run context


class CoworkIngest(BaseModel):
    token: str                               # the signed capability token from the deep link
    artifact: dict | None = None             # one audit { title, payload:{kind:"audit", handle, top, …} }
    artifacts: list[dict] | None = None      # or several (the roster audit for a full response)


class VoiceProfile(BaseModel):
    voice_profile: str = ""                  # the full, uncompressed voice spec (may be many pages)


class RosterUpsert(BaseModel):
    handle: str | None = None
    platform: str | None = None
    why: str | None = None
    status: str | None = None                # watch | active | archived
    score_overlap: int | None = None
    score_offer: int | None = None
    score_perf: int | None = None
    in_launch: bool | None = None


# ── lookups ───────────────────────────────────────────────────────────────────
async def _emp(s, tenant_id, emp_id) -> AIEmployee:
    e = (await s.execute(select(AIEmployee).where(
        AIEmployee.tenant_id == tenant_id, AIEmployee.id == emp_id))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Unknown employee")
    return e


async def _run(s, tenant_id, run_id) -> AIRun:
    r = (await s.execute(select(AIRun).where(
        AIRun.tenant_id == tenant_id, AIRun.id == run_id))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Unknown run")
    return r


async def _artifact(s, tenant_id, art_id) -> AIArtifact:
    a = (await s.execute(select(AIArtifact).where(
        AIArtifact.tenant_id == tenant_id, AIArtifact.id == art_id))).scalar_one_or_none()
    if not a:
        raise HTTPException(404, "Unknown artifact")
    return a


def _iso(x) -> str | None:
    return x.isoformat() if x else None


async def _awaiting_by_employee(s, tenant_id) -> dict:
    """employee_id → count of draft artifacts awaiting approval (the badge source)."""
    rows = (await s.execute(
        select(AIRun.employee_id, func.count(AIArtifact.id))
        .join(AIArtifact, AIArtifact.run_id == AIRun.id)
        .where(AIRun.tenant_id == tenant_id, AIArtifact.state == "draft")
        .group_by(AIRun.employee_id))).all()
    return {eid: n for eid, n in rows}


async def _next_run_for(s, employee) -> str | None:
    """Soonest next scheduled fire across the employee's enabled scheduled skills."""
    skills = (await s.execute(select(AIEmployeeSkill).where(
        AIEmployeeSkill.employee_id == employee.id,
        AIEmployeeSkill.enabled.is_(True)))).scalars().all()
    now = dt.datetime.now(dt.timezone.utc).astimezone(eng._tz(employee))
    defaults = {d["key"]: d.get("default_schedule") for d in SKILLS}
    fires = []
    for es in skills:
        cron = es.schedule_override or defaults.get(es.skill_key)
        nxt = eng.next_run_at(cron, now)
        if nxt:
            fires.append(nxt)
    return _iso(min(fires)) if fires else None


def _run_out(r: AIRun) -> dict:
    return {"id": str(r.id), "skill_key": r.skill_key, "trigger": r.trigger, "status": r.status,
            "summary": r.summary, "reads": r.reads or [], "error": r.error,
            "trigger_context": r.trigger_context, "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
            "created_at": _iso(r.created_at), "started_at": _iso(r.started_at),
            "finished_at": _iso(r.finished_at)}


def _artifact_out(a: AIArtifact) -> dict:
    return {"id": str(a.id), "run_id": str(a.run_id), "kind": a.kind, "lane": a.lane,
            "title": a.title, "dest_label": a.dest_label, "payload": a.payload, "state": a.state,
            "approved_at": _iso(a.approved_at), "shipped_at": _iso(a.shipped_at)}


# ── employees ─────────────────────────────────────────────────────────────────
@router.get("/employees")
async def list_employees(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    await assert_tab(user, s, TAB)
    emps = (await s.execute(select(AIEmployee).where(
        AIEmployee.tenant_id == user.tenant_id, AIEmployee.status != "archived")
        .order_by(AIEmployee.created_at))).scalars().all()
    awaiting = await _awaiting_by_employee(s, user.tenant_id)
    writeback_env = settings.AI_EMPLOYEES_WRITEBACK_ENABLED
    out = []
    for e in emps:
        last = (await s.execute(select(AIRun).where(AIRun.employee_id == e.id)
                .order_by(AIRun.created_at.desc()).limit(1))).scalar_one_or_none()
        out.append({
            "id": str(e.id), "name": e.name, "role_title": e.role_title,
            "avatar_color": e.avatar_color, "status": e.status,
            "writeback_enabled": e.writeback_enabled,
            # the voice profile can be many pages — don't ship it in the list; it has its own route
            "config": {k: v for k, v in (e.config or {}).items() if k != "voice_profile"},
            "has_voice_profile": bool((e.config or {}).get("voice_profile")),
            "awaiting_approval": int(awaiting.get(e.id, 0)),
            "next_run_at": await _next_run_for(s, e),
            "last_run": _run_out(last) if last else None,
        })
    return {"employees": out, "awaiting_total": int(sum(awaiting.values())),
            "writeback_env_open": writeback_env,
            "can_manage": user.role in ("owner", "admin")}


@router.post("/employees", status_code=201)
async def create_employee(body: EmployeeCreate, user: User = Depends(manager),
                          s: AsyncSession = Depends(get_session)):
    e = AIEmployee(tenant_id=user.tenant_id, name=body.name.strip(), role_title=body.role_title,
                   avatar_color=body.avatar_color, config=body.config or {})
    s.add(e)
    await s.flush()
    await eng.seed_employee_skills(s, e)
    audit(s, user.tenant_id, user.id, "ai.employee_create", "ai_employee", e.id, {"name": e.name})
    await s.commit()
    return {"id": str(e.id), "name": e.name}


@router.get("/settings")
async def ai_settings(user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    """Tenant-level env state + the live monthly token meter for the Settings group (§7.5).
    Model + budget are environment-controlled in v1, so they're returned read-only."""
    await assert_tab(user, s, TAB)
    return {
        "writeback_env_open": settings.AI_EMPLOYEES_WRITEBACK_ENABLED,
        "model": eng._model(), "max_tokens": settings.AI_EMPLOYEES_MAX_TOKENS,
        "token_budget": settings.AI_EMPLOYEES_TOKEN_BUDGET,
        "tokens_used": await eng.tokens_used_this_month(s, user.tenant_id),
        "can_manage": user.role in ("owner", "admin"),
    }


@router.patch("/employees/{emp_id}")
async def patch_employee(emp_id: str, body: EmployeePatch, user: User = Depends(manager),
                         s: AsyncSession = Depends(get_session)):
    e = await _emp(s, user.tenant_id, emp_id)
    fields = body.model_dump(exclude_unset=True)
    if "status" in fields and fields["status"] not in ("active", "paused"):
        raise HTTPException(400, "status must be active or paused")
    for k, v in fields.items():
        setattr(e, k, v)
    audit(s, user.tenant_id, user.id, "ai.employee_update", "ai_employee", e.id,
          {"fields": sorted(fields)})
    await s.commit()
    return {"ok": True}


@router.delete("/employees/{emp_id}")
async def archive_employee(emp_id: str, user: User = Depends(manager),
                           s: AsyncSession = Depends(get_session)):
    """Soft delete (§7.1): archive keeps the runs/artifacts for the record; dispatch already
    skips non-active employees, and the list hides archived ones."""
    e = await _emp(s, user.tenant_id, emp_id)
    e.status = "archived"
    audit(s, user.tenant_id, user.id, "ai.employee_archive", "ai_employee", e.id, {})
    await s.commit()
    return {"ok": True}


@router.get("/employees/{emp_id}/voice")
async def get_voice(emp_id: str, user: User = Depends(manager),
                    s: AsyncSession = Depends(get_session)):
    """The employee's full voice profile — the uncompressed spec the voice pass writes against."""
    e = await _emp(s, user.tenant_id, emp_id)
    text = (e.config or {}).get("voice_profile", "") or ""
    return {"voice_profile": text, "chars": len(text)}


@router.put("/employees/{emp_id}/voice")
async def set_voice(emp_id: str, body: VoiceProfile, user: User = Depends(manager),
                    s: AsyncSession = Depends(get_session)):
    """Store (or clear) the full voice profile. Content skills stay lean; the cascade's final
    voice pass rewrites the published copy against this, verbatim (never compressed)."""
    e = await _emp(s, user.tenant_id, emp_id)
    cfg = dict(e.config or {})
    text = (body.voice_profile or "").strip()
    if text:
        cfg["voice_profile"] = body.voice_profile
    else:
        cfg.pop("voice_profile", None)
    e.config = cfg
    audit(s, user.tenant_id, user.id, "ai.voice_update", "ai_employee", e.id, {"chars": len(text)})
    await s.commit()
    return {"ok": True, "chars": len(text)}


@router.get("/employees/{emp_id}/export")
async def export_employee(emp_id: str, user: User = Depends(current_user),
                          s: AsyncSession = Depends(get_session)):
    """Everything for one employee as JSON (§7.7): runs, artifacts, intel, roster."""
    await assert_tab(user, s, TAB)
    e = await _emp(s, user.tenant_id, emp_id)
    runs = (await s.execute(select(AIRun).where(AIRun.employee_id == e.id)
            .order_by(AIRun.created_at))).scalars().all()
    arts = (await s.execute(select(AIArtifact).join(AIRun, AIArtifact.run_id == AIRun.id)
            .where(AIRun.employee_id == e.id))).scalars().all()
    intel = (await s.execute(select(AIIntelEntry).where(AIIntelEntry.employee_id == e.id))).scalars().all()
    roster = (await s.execute(select(AIRosterAccount).where(AIRosterAccount.employee_id == e.id))).scalars().all()
    weights = (e.config or {}).get("roster_weights") or {}
    return {
        "employee": {"id": str(e.id), "name": e.name, "role_title": e.role_title,
                     "status": e.status, "config": e.config or {}},
        "runs": [_run_out(r) for r in runs],
        "artifacts": [_artifact_out(a) for a in arts],
        "intel": [{"finding": i.finding, "tags": i.tags or [], "created_at": _iso(i.created_at)} for i in intel],
        "roster": [_roster_out(a, weights) for a in roster],
    }


# ── skills (merged seed + override) ───────────────────────────────────────────
@router.get("/employees/{emp_id}/skills")
async def list_skills(emp_id: str, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session)):
    await assert_tab(user, s, TAB)
    e = await _emp(s, user.tenant_id, emp_id)
    overrides = {es.skill_key: es for es in (await s.execute(select(AIEmployeeSkill).where(
        AIEmployeeSkill.employee_id == e.id))).scalars().all()}
    catalog = {sk.key: sk for sk in (await s.execute(select(AISkill))).scalars().all()}
    out = []
    for d in SKILLS:
        es = overrides.get(d["key"])
        cat = catalog.get(d["key"])
        cur_ver = cat.version if cat else 1
        out.append({
            "key": d["key"], "name": d["name"], "description": d["description"],
            "default_prompt": d["default_prompt"], "default_schedule": d["default_schedule"],
            "artifact_kinds": d["artifact_kinds"],
            "enabled": es.enabled if es else True,
            "prompt_override": es.prompt_override if es else None,
            "schedule_override": es.schedule_override if es else None,
            "effective_schedule": (es.schedule_override if es and es.schedule_override
                                   else d["default_schedule"]),
            "config": (es.config if es else {}) or {},
            "seed_version": es.seed_version if es else cur_ver, "current_version": cur_ver,
            "stale_override": bool(es and es.prompt_override and es.seed_version < cur_ver),
        })
    return {"skills": out}


@router.patch("/employees/{emp_id}/skills/{key}")
async def patch_skill(emp_id: str, key: str, body: SkillPatch, user: User = Depends(manager),
                      s: AsyncSession = Depends(get_session)):
    if key not in SKILL_KEYS:
        raise HTTPException(404, "Unknown skill")
    e = await _emp(s, user.tenant_id, emp_id)
    es = (await s.execute(select(AIEmployeeSkill).where(
        AIEmployeeSkill.employee_id == e.id, AIEmployeeSkill.skill_key == key))).scalar_one_or_none()
    if not es:
        es = AIEmployeeSkill(tenant_id=user.tenant_id, employee_id=e.id, skill_key=key)
        s.add(es)
    fields = body.model_dump(exclude_unset=True)
    for k, v in fields.items():
        setattr(es, k, v)
    audit(s, user.tenant_id, user.id, "ai.skill_update", "ai_employee_skill", e.id,
          {"skill": key, "fields": sorted(fields)})
    await s.commit()
    return {"ok": True}


@router.post("/employees/{emp_id}/skills/{key}/run", status_code=201)
async def run_skill(emp_id: str, key: str, body: RunCreate, user: User = Depends(manager),
                    s: AsyncSession = Depends(get_session)):
    """Manually queue a run. The worker (ai_execute) drafts the artifacts asynchronously."""
    if key not in SKILL_KEYS:
        raise HTTPException(404, "Unknown skill")
    e = await _emp(s, user.tenant_id, emp_id)
    ctx = dict(body.context or {})
    if body.material:
        ctx["material"] = body.material
    if body.handle:
        ctx["handle"] = body.handle
    over = await eng._over_budget(s, user.tenant_id)
    run = AIRun(tenant_id=user.tenant_id, employee_id=e.id, skill_key=key, trigger="manual",
                status="skipped_budget" if over else "queued", context=ctx or None)
    s.add(run)
    await s.flush()
    audit(s, user.tenant_id, user.id, "ai.run_manual", "ai_run", run.id,
          {"skill": key, "status": run.status})
    await s.commit()
    return {"id": str(run.id), "status": run.status}


@router.post("/employees/{emp_id}/respond", status_code=201)
async def run_full_response(emp_id: str, request: Request, user: User = Depends(manager),
                            s: AsyncSession = Depends(get_session)):
    """One button → the whole coordinated response on ONE run. If the employee watches a roster,
    Cowork audits those accounts (real IG data) and the cascade continues on the same run,
    building on the audits, for a single approval. With no roster it runs straight through
    server-side. Returns a claude:// deep link when the Cowork audit is the first step."""
    e = await _emp(s, user.tenant_id, emp_id)
    org = (await s.execute(select(Tenant.name).where(Tenant.id == user.tenant_id))).scalar() or "the brand"
    tc = await eng.pace_facts(s, user.tenant_id, dt.date.today()) or {
        "source": "Manual", "label": "Full response", "title": "Full campaign response", "facts": []}
    if await eng._over_budget(s, user.tenant_id):
        run = AIRun(tenant_id=user.tenant_id, employee_id=e.id, skill_key=eng.PACE_RESPONSE,
                    trigger="manual", status="skipped_budget", trigger_context=tc)
        s.add(run)
        await s.flush()
        await s.commit()
        return {"run_id": str(run.id), "deep_link": None, "status": "skipped_budget"}
    roster = list((await s.execute(select(AIRosterAccount.handle).where(
        AIRosterAccount.employee_id == e.id, AIRosterAccount.status != "archived")
        .order_by(AIRosterAccount.created_at).limit(5))).scalars().all())
    if roster:
        # Cowork audits the roster first; the ingest flips the run back to 'queued' so the
        # worker runs the rest of the cascade on those real teardowns — all one run.
        run = AIRun(tenant_id=user.tenant_id, employee_id=e.id, skill_key=eng.PACE_RESPONSE,
                    trigger="manual", status="awaiting_audit", trigger_context=tc,
                    context={"external": "cowork", "handles": roster})
        s.add(run)
        await s.flush()
        token = make_capability("cowork_audit", minutes=90, run_id=str(run.id), tid=str(user.tenant_id))
        ingest_url = str(request.base_url).rstrip("/") + "/api/v1/ai/cowork/ingest"
        deep_link = "claude://cowork/new?q=" + quote(
            _cowork_cascade_instruction(org, roster, ingest_url, token), safe="")
        audit(s, user.tenant_id, user.id, "ai.run_response", "ai_run", run.id,
              {"status": "awaiting_audit", "roster": len(roster)})
        await s.commit()
        return {"run_id": str(run.id), "deep_link": deep_link, "status": "awaiting_audit"}
    run = AIRun(tenant_id=user.tenant_id, employee_id=e.id, skill_key=eng.PACE_RESPONSE,
                trigger="manual", status="queued", trigger_context=tc)
    s.add(run)
    await s.flush()
    audit(s, user.tenant_id, user.id, "ai.run_response", "ai_run", run.id, {"status": "queued"})
    await s.commit()
    return {"run_id": str(run.id), "deep_link": None, "status": "queued"}


# ── Cowork audit bridge (autonomous IG audit via Claude Desktop + Chrome) ─────
# A web page can't reach the Chrome extension, but it CAN fire a claude:// deep link that
# opens a Cowork task; Cowork browses Instagram through its Chrome connector and POSTs the
# teardown back here. `start_cowork_audit` mints the deep link + a scoped, single-use
# capability token; `cowork_ingest` (token-gated, no login) lands the result as a draft.
def _cowork_instruction(org: str, handle: str, ingest_url: str, token: str) -> str:
    schema = ('{"token":"%s","artifact":{"title":"<one line: what is converting>","payload":{'
              '"kind":"audit","handle":"%s",'
              '"top":[{"name":"<post + format>","val":"<e.g. 4.2x>","w":"<bar width, top=100%%>"}],'
              '"mechanics":["<reusable mechanic>"],"note":"<how it informs %s, nothing copied>"}}}'
              % (token, handle, org))
    return (
        f"You are running the Account Audit skill for {org}. Open Instagram and audit {handle}: "
        f"read its ~12 most recent posts, noting each post's format (Reel / carousel / static), its "
        f"hook, and visible engagement (likes, comments). Identify the top 3 performers and the "
        f"reusable mechanics behind them (hook style, format, cadence). Nothing is copied — the "
        f"mechanics inform {org}'s own posts.\n\n"
        f"When done, send ONE HTTP POST to {ingest_url} with this exact JSON body and nothing else:\n"
        f"{schema}\n"
        f"Use the real numbers you observed; 'w' is a bar width like '100%%' for the top performer, "
        f"scaled down for the rest. Do not post anything to Instagram.")


def _cowork_cascade_instruction(org: str, handles: list[str], ingest_url: str, token: str) -> str:
    """The audit step of a full response: audit every roster account, post one audit per account.
    The ingest continues the cascade (strategy / creative / tags) on those real teardowns."""
    schema = ('{"token":"%s","artifacts":[{"title":"<one line: what is converting>","payload":{'
              '"kind":"audit","handle":"<@account>",'
              '"top":[{"name":"<post + format>","val":"<e.g. 4.2x>","w":"<bar width, top=100%%>"}],'
              '"mechanics":["<reusable mechanic>"],"note":"<how it informs %s>"}}]}' % (token, org))
    return (
        f"You are running {org}'s competitor audits ahead of a launch push. Open Instagram and audit "
        f"EACH of these accounts: {', '.join(handles)}. For each, read ~12 recent posts — noting format "
        f"(Reel / carousel / static), hook, and visible engagement — and identify its top 3 performers "
        f"and the reusable mechanics (hook style, format, cadence). Nothing is copied.\n\n"
        f"When ALL accounts are done, send ONE HTTP POST to {ingest_url} with one audit object per "
        f"account in the artifacts array, exactly this shape and nothing else:\n{schema}\n"
        f"Use the real numbers you observed; 'w' is a bar width like '100%%' for the top performer. "
        f"Do not post anything to Instagram.")


@router.post("/employees/{emp_id}/cowork-audit", status_code=201)
async def start_cowork_audit(emp_id: str, body: RunCreate, request: Request,
                             user: User = Depends(manager), s: AsyncSession = Depends(get_session)):
    """Begin an autonomous audit: create a pending run and return a claude:// deep link that
    launches a Cowork task to browse the account and post the teardown back (no paste)."""
    handle = (body.handle or "").strip()
    if not handle:
        raise HTTPException(400, "A handle is required")
    e = await _emp(s, user.tenant_id, emp_id)
    org = (await s.execute(select(Tenant.name).where(Tenant.id == user.tenant_id))).scalar() or "the brand"
    tc = {"source": "Instagram · Cowork", "label": "Live audit",
          "title": f"Auditing {handle}", "facts": [handle, "browsing via Claude in Chrome (Cowork)"]}
    run = AIRun(tenant_id=user.tenant_id, employee_id=e.id, skill_key="audit", trigger="manual",
                status="running", trigger_context=tc, context={"external": "cowork", "handle": handle})
    s.add(run)
    await s.flush()
    token = make_capability("cowork_audit", minutes=90, run_id=str(run.id),
                            tid=str(user.tenant_id), handle=handle)
    ingest_url = str(request.base_url).rstrip("/") + "/api/v1/ai/cowork/ingest"
    deep_link = "claude://cowork/new?q=" + quote(_cowork_instruction(org, handle, ingest_url, token), safe="")
    audit(s, user.tenant_id, user.id, "ai.cowork_audit_start", "ai_run", run.id, {"handle": handle})
    await s.commit()
    return {"run_id": str(run.id), "deep_link": deep_link, "ingest_url": ingest_url}


@router.post("/cowork/ingest")
async def cowork_ingest(body: CoworkIngest, s: AsyncSession = Depends(get_session)):
    """The return door: a Cowork task posts the audit result here with its capability token.
    No login — the signed, single-use token authorizes creating ONE draft audit artifact on
    its run (which still needs human approval, so a leaked token has zero blast radius)."""
    try:
        data = read_capability(body.token, "cowork_audit")
    except Exception:
        raise HTTPException(401, "Invalid or expired token")
    run = (await s.execute(select(AIRun).where(
        AIRun.id == data["run_id"], AIRun.tenant_id == data["tid"]))).scalar_one_or_none()
    if not run:
        raise HTTPException(404, "Unknown run")
    if run.status not in ("running", "awaiting_audit"):   # single-use: already fulfilled/cancelled
        raise HTTPException(409, "This audit is no longer awaiting a result")
    items = body.artifacts if body.artifacts else ([body.artifact] if body.artifact else [])
    if not items:
        raise HTTPException(400, "No audit provided")
    meta = KIND_META.get("audit", {"lane": "Intel", "dest_label": None})
    handles = []
    for art in items:
        payload = dict(art.get("payload") or {})
        payload.setdefault("kind", "audit")
        h = payload.get("handle") or data.get("handle") or "the account"
        payload.setdefault("handle", h)
        handles.append(h)
        s.add(AIArtifact(tenant_id=run.tenant_id, run_id=run.id, kind="audit", lane=meta["lane"],
                         title=(art.get("title") or f"Audit of {h}")[:160],
                         dest_label="Instagram · Cowork", payload=payload, state="draft"))
    audit(s, run.tenant_id, None, "ai.cowork_audit_ingest", "ai_run", run.id, {"handles": handles})
    if run.skill_key == eng.PACE_RESPONSE:
        run.status = "queued"                    # continue the cascade on those real teardowns
    else:
        run.status = "awaiting_approval"
        run.summary = f"Live audit of {handles[0]} via Cowork."
        run.finished_at = dt.datetime.now(dt.timezone.utc)
    await s.commit()
    return {"ok": True, "run_id": str(run.id), "status": run.status}


# ── runs + artifacts ──────────────────────────────────────────────────────────
@router.get("/employees/{emp_id}/runs")
async def list_runs(emp_id: str, status: str | None = None,
                    page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
                    user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    await assert_tab(user, s, TAB)
    e = await _emp(s, user.tenant_id, emp_id)
    q = select(AIRun).where(AIRun.employee_id == e.id)
    if status:
        q = q.where(AIRun.status == status)
    rows = (await s.execute(q.order_by(AIRun.created_at.desc())
            .offset((page - 1) * size).limit(size))).scalars().all()
    return {"runs": [_run_out(r) for r in rows], "page": page}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, user: User = Depends(current_user),
                  s: AsyncSession = Depends(get_session)):
    await assert_tab(user, s, TAB)
    r = await _run(s, user.tenant_id, run_id)
    arts = (await s.execute(select(AIArtifact).where(AIArtifact.run_id == r.id)
            .order_by(AIArtifact.created_at))).scalars().all()
    return {"run": _run_out(r), "artifacts": [_artifact_out(a) for a in arts]}


async def _approve_artifact(a: AIArtifact, user: User) -> None:
    if a.state == "draft":
        a.state = "approved"
        a.approved_by = user.id
        a.approved_at = eng._now()


@router.post("/runs/{run_id}/approve")
async def approve_run(run_id: str, user: User = Depends(manager),
                      s: AsyncSession = Depends(get_session)):
    """Batch: approve every draft artifact on the run. Ships those whose gates are open;
    the rest stay `approved` with writeback disabled (§2)."""
    r = await _run(s, user.tenant_id, run_id)
    e = await _emp(s, user.tenant_id, r.employee_id)
    arts = (await s.execute(select(AIArtifact).where(AIArtifact.run_id == r.id))).scalars().all()
    gate = eng.writeback_open(e)
    approved = shipped = 0
    for a in arts:
        if a.state == "draft":
            await _approve_artifact(a, user)
            approved += 1
        if gate and a.state == "approved":
            a.state, a.shipped_at = "shipped", eng._now()
            shipped += 1
    r.status = "shipped" if (arts and all(a.state == "shipped" for a in arts)) else "approved"
    audit(s, user.tenant_id, user.id, "ai.run_approve", "ai_run", r.id,
          {"approved": approved, "shipped": shipped, "writeback": gate})
    await s.commit()
    return {"approved": approved, "shipped": shipped, "writeback_open": gate,
            "run_status": r.status}


@router.post("/artifacts/{art_id}/approve")
async def approve_artifact(art_id: str, user: User = Depends(manager),
                           s: AsyncSession = Depends(get_session)):
    a = await _artifact(s, user.tenant_id, art_id)
    await _approve_artifact(a, user)          # already-approved is a no-op
    audit(s, user.tenant_id, user.id, "ai.artifact_approve", "ai_artifact", a.id, {"kind": a.kind})
    await s.commit()
    return _artifact_out(a)


@router.post("/artifacts/{art_id}/ship")
async def ship_artifact(art_id: str, user: User = Depends(manager),
                        s: AsyncSession = Depends(get_session)):
    """Explicit ship (export/writeback) of an approved artifact once gates are open. 409 if the
    artifact isn't approved, or if writeback is disabled (§2 governance)."""
    a = await _artifact(s, user.tenant_id, art_id)
    if a.state == "shipped":
        return _artifact_out(a)               # idempotent
    if a.state != "approved":
        raise HTTPException(409, "Artifact must be approved before shipping")
    r = await _run(s, user.tenant_id, a.run_id)
    e = await _emp(s, user.tenant_id, r.employee_id)
    if not eng.writeback_open(e):
        raise HTTPException(409, "Writeback is disabled (env flag or employee toggle is off)")
    a.state, a.shipped_at = "shipped", eng._now()
    audit(s, user.tenant_id, user.id, "ai.artifact_ship", "ai_artifact", a.id, {"kind": a.kind})
    await s.commit()
    return _artifact_out(a)


@router.post("/artifacts/{art_id}/dismiss")
async def dismiss_artifact(art_id: str, user: User = Depends(manager),
                           s: AsyncSession = Depends(get_session)):
    a = await _artifact(s, user.tenant_id, art_id)
    a.state = "dismissed"
    audit(s, user.tenant_id, user.id, "ai.artifact_dismiss", "ai_artifact", a.id, {"kind": a.kind})
    await s.commit()
    return _artifact_out(a)


# ── roster (computed priority) ────────────────────────────────────────────────
def _roster_out(acc: AIRosterAccount, weights: dict) -> dict:
    return {"id": str(acc.id), "platform": acc.platform, "handle": acc.handle, "why": acc.why,
            "status": acc.status, "added_by": acc.added_by, "in_launch": acc.in_launch,
            "score_overlap": acc.score_overlap, "score_offer": acc.score_offer,
            "score_perf": acc.score_perf, "priority": eng.roster_priority(acc, weights),
            "last_audited_at": _iso(acc.last_audited_at)}


@router.get("/employees/{emp_id}/roster")
async def list_roster(emp_id: str, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session)):
    await assert_tab(user, s, TAB)
    e = await _emp(s, user.tenant_id, emp_id)
    weights = (e.config or {}).get("roster_weights") or {}
    rows = (await s.execute(select(AIRosterAccount).where(
        AIRosterAccount.employee_id == e.id))).scalars().all()
    out = sorted((_roster_out(a, weights) for a in rows), key=lambda r: -r["priority"])
    return {"roster": out}


@router.post("/employees/{emp_id}/roster", status_code=201)
async def add_roster(emp_id: str, body: RosterUpsert, user: User = Depends(manager),
                     s: AsyncSession = Depends(get_session)):
    e = await _emp(s, user.tenant_id, emp_id)
    if not body.handle:
        raise HTTPException(400, "handle is required")
    acc = AIRosterAccount(tenant_id=user.tenant_id, employee_id=e.id, handle=body.handle.strip(),
                          platform=body.platform or "instagram", why=body.why,
                          status=body.status or "watch", added_by="human")
    for f in ("score_overlap", "score_offer", "score_perf", "in_launch"):
        v = getattr(body, f)
        if v is not None:
            setattr(acc, f, v)
    s.add(acc)
    await s.flush()
    audit(s, user.tenant_id, user.id, "ai.roster_add", "ai_roster_account", acc.id,
          {"handle": acc.handle})
    await s.commit()
    return {"id": str(acc.id)}


@router.patch("/roster/{acc_id}")
async def patch_roster(acc_id: str, body: RosterUpsert, user: User = Depends(manager),
                       s: AsyncSession = Depends(get_session)):
    acc = (await s.execute(select(AIRosterAccount).where(
        AIRosterAccount.tenant_id == user.tenant_id,
        AIRosterAccount.id == acc_id))).scalar_one_or_none()
    if not acc:
        raise HTTPException(404, "Unknown roster account")
    fields = body.model_dump(exclude_unset=True)
    for k, v in fields.items():
        setattr(acc, k, v)
    audit(s, user.tenant_id, user.id, "ai.roster_update", "ai_roster_account", acc.id,
          {"fields": sorted(fields)})
    await s.commit()
    return {"ok": True}


@router.delete("/roster/{acc_id}")
async def delete_roster(acc_id: str, user: User = Depends(manager),
                        s: AsyncSession = Depends(get_session)):
    acc = (await s.execute(select(AIRosterAccount).where(
        AIRosterAccount.tenant_id == user.tenant_id,
        AIRosterAccount.id == acc_id))).scalar_one_or_none()
    if not acc:
        raise HTTPException(404, "Unknown roster account")
    await s.delete(acc)
    audit(s, user.tenant_id, user.id, "ai.roster_delete", "ai_roster_account", acc_id, {})
    await s.commit()
    return {"ok": True}


# ── intel + briefs ────────────────────────────────────────────────────────────
@router.get("/employees/{emp_id}/intel")
async def list_intel(emp_id: str, tag: str | None = None,
                     page: int = Query(1, ge=1), size: int = Query(30, ge=1, le=100),
                     user: User = Depends(current_user), s: AsyncSession = Depends(get_session)):
    await assert_tab(user, s, TAB)
    e = await _emp(s, user.tenant_id, emp_id)
    rows = (await s.execute(select(AIIntelEntry).where(AIIntelEntry.employee_id == e.id)
            .order_by(AIIntelEntry.created_at.desc())
            .offset((page - 1) * size).limit(size))).scalars().all()
    items = [{"id": str(i.id), "finding": i.finding, "tags": i.tags or [],
              "created_at": _iso(i.created_at)} for i in rows]
    if tag:
        items = [i for i in items if tag in i["tags"]]
    return {"intel": items, "page": page}


@router.get("/employees/{emp_id}/briefs")
async def list_briefs(emp_id: str, user: User = Depends(current_user),
                      s: AsyncSession = Depends(get_session)):
    """The trend-brief archive — `trend` artifacts newest first."""
    await assert_tab(user, s, TAB)
    e = await _emp(s, user.tenant_id, emp_id)
    rows = (await s.execute(
        select(AIArtifact).join(AIRun, AIArtifact.run_id == AIRun.id)
        .where(AIRun.employee_id == e.id, AIArtifact.kind == "trend")
        .order_by(AIArtifact.created_at.desc()).limit(50))).scalars().all()
    return {"briefs": [_artifact_out(a) for a in rows]}
