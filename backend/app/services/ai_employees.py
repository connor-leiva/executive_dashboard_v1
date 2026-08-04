"""AI Employees — the engine (SPEC-ai-employees-tab §4 worker + §5 execution).

Two per-tenant entry points the worker jobs call (worker.py registers ai_dispatch/1min +
ai_execute/15s):
  dispatch_tenant  — cron-due skills + the pace_check condition → queue AIRun rows
  execute_one      — take the oldest queued run → context pack → Anthropic (validate vs
                     the skill's output_contract, one retry) → draft artifacts → awaiting_approval

Governance (§2): nothing auto-commits — runs land awaiting_approval, artifacts land draft.
The Anthropic seam mirrors binder_extract (lazy client, monkeypatchable _claude_call,
thinking disabled, defensive JSON parse). Writeback (measure→GHL) is a later step and
stays behind AI_EMPLOYEES_WRITEBACK_ENABLED; v1 ships approve+export.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from zoneinfo import ZoneInfo

from croniter import croniter
from jsonschema import Draft7Validator
from sqlalchemy import select, func

from ..config import settings
from ..models import (Tenant, Launch, AISkill, AIEmployee, AIEmployeeSkill, AIRun, AIArtifact,
                      AIRosterAccount, AIIntelEntry)
from .audit import audit
from .ai_skills import SKILLS, KIND_META
from .launch import compute_shift

log = logging.getLogger("app")
SKILL_BY_KEY = {sk["key"]: sk for sk in SKILLS}
_client_obj = None

_SYSTEM = (
    "You are an AI employee producing draft work for a human to approve. Return STRICT JSON "
    "ONLY — no prose, no markdown fences — exactly matching the shape described in the user "
    "message. Never invent metrics or names; use only the provided context. Everything you "
    "produce is a draft; a human approves before anything ships."
)

# ── the coordinated response (the AI employee "in action") ───────────────────
# One trigger → Summer diagnoses the gap, then runs her skills in a dependency chain
# (audit + trend feed strategy; strategy feeds the creative; measure tags it all) and
# bundles every draft into ONE run for a single batch approval. This is the orchestration
# the mockup depicts — the conductor over the six skills.
PACE_RESPONSE = "pace_response"
PACE_RESPONSE_ORDER = ["audit", "trend_brief", "strategy", "design_carousel", "reel_script", "measure"]
_DIAGNOSE_CONTRACT = {"type": "object", "required": ["reads"],
                      "properties": {"reads": {"type": "array", "items": {"type": "string"}}}}
_DIAGNOSE_PROMPT = (
    "You are {org}'s social media strategist reacting to a launch pacing signal. From the pacing "
    "facts and any audit material / roster / intel in the context, write 2-4 short diagnosis lines "
    "— what the numbers say and what is (and isn't) working right now. Concrete, not generic. "
    "Return STRICT JSON only: {\"reads\": [\"line\", \"line\", ...]}.\n\nContext:\n{context}")


def enabled() -> bool:
    return bool(settings.AI_EMPLOYEES_ENABLED and settings.ANTHROPIC_API_KEY)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _tz(employee: AIEmployee) -> ZoneInfo:
    name = (employee.config or {}).get("timezone") or settings.BILLING_TIMEZONE or "America/Denver"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


def _model() -> str:
    return settings.AI_EMPLOYEES_MODEL or settings.ASSISTANT_MODEL


def _client():
    global _client_obj
    if _client_obj is None:
        from anthropic import AsyncAnthropic
        _client_obj = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client_obj


def _text_of(resp) -> str:
    return "".join(b.text for b in (getattr(resp, "content", None) or []) if getattr(b, "type", None) == "text")


async def _claude_call(client, model, system, user_text):
    """The single network seam (tests monkeypatch this). Returns (text, tokens_in, tokens_out)."""
    resp = await client.messages.create(
        model=model, max_tokens=settings.AI_EMPLOYEES_MAX_TOKENS, system=system,
        thinking={"type": "disabled"},
        messages=[{"role": "user", "content": [{"type": "text", "text": user_text}]}])
    u = getattr(resp, "usage", None)
    return _text_of(resp), int(getattr(u, "input_tokens", 0) or 0), int(getattr(u, "output_tokens", 0) or 0)


def _parse_json(text: str) -> dict | None:
    """Defensive parse — strip ```json fences, slice the object. Malformed → None."""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t[:4].lower() == "json":
            t = t[4:]
    try:
        obj = json.loads(t[t.index("{"):t.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def _validate(obj: dict, contract: dict) -> str | None:
    """None when valid; else the first schema error message."""
    try:
        errs = sorted(Draft7Validator(contract).iter_errors(obj), key=lambda e: list(e.path))
        return errs[0].message if errs else None
    except Exception as e:                                   # a broken contract shouldn't 500 a run
        return f"schema error: {e}"


async def call_skill(skill_def: dict, prompt: str, *, client=None, model=None):
    """Call the model, parse, validate against the skill's output_contract, one retry on
    invalid JSON. Returns (result|None, tokens_in, tokens_out, error)."""
    client = client or _client()
    model = model or _model()
    contract = skill_def["output_contract"]
    tin = tout = 0
    last_err = "no response"
    for attempt in range(2):
        user = prompt if attempt == 0 else (
            prompt + "\n\nYour previous reply was not valid JSON matching the required shape. "
            "Return STRICT JSON only, no fences.")
        text, a_in, a_out = await _claude_call(client, model, _SYSTEM, user)
        tin += a_in
        tout += a_out
        obj = _parse_json(text)
        if obj is None:
            last_err = "invalid JSON"
            continue
        err = _validate(obj, contract)
        if err is None:
            return obj, tin, tout, None
        last_err = err
    return None, tin, tout, last_err


# ── context pack (§4.1) ──────────────────────────────────────────────────────
COWORK_DEST = "Instagram · Cowork"          # the label the Cowork ingest stamps on real audits


async def recent_cowork_audits(s, employee_id, days: int = 21) -> list[dict]:
    """The real per-account teardowns gathered via the Cowork bridge, latest one per handle
    within the window. This is what wires an autonomous audit into the cascade — the audit /
    trend / diagnose steps build on this instead of running blind."""
    since = _now() - dt.timedelta(days=days)
    rows = (await s.execute(
        select(AIArtifact).join(AIRun, AIArtifact.run_id == AIRun.id).where(
            AIRun.employee_id == employee_id, AIArtifact.kind == "audit",
            AIArtifact.dest_label == COWORK_DEST, AIArtifact.created_at >= since)
        .order_by(AIArtifact.created_at.desc()))).scalars().all()
    latest: dict = {}
    for a in rows:
        handle = (a.payload or {}).get("handle") or a.title
        latest.setdefault(handle, a.payload)     # first seen = most recent (desc order)
    return list(latest.values())


def _fill_prompt(template: str, scalars: dict, context: dict) -> str:
    out = template
    for k, v in scalars.items():
        out = out.replace("{" + k + "}", str(v))
    return out.replace("{context}", json.dumps(context, default=str, ensure_ascii=False)[:12000])


async def build_context(s, tenant_id, employee: AIEmployee, es: AIEmployeeSkill | None, run: AIRun,
                        *, skill_key: str | None = None, prior: dict | None = None):
    """(scalars, context) for a skill prompt. Scalars fill {org}/{brand_voice}/… ; context is the
    JSON slice placed at {context}. `skill_key` overrides which slice is built (for the cascade,
    where the run's key is 'pace_response'); `prior` carries earlier skills' outputs so a later
    step (strategy, design) builds on the audit/trend/strategy that came before it."""
    cfg = employee.config or {}
    sk = skill_key or (es.skill_key if es else None)
    org = (await s.execute(select(Tenant.name).where(Tenant.id == tenant_id))).scalar() or "the business"
    tc = run.trigger_context or {}
    rc = run.context or {}
    scalars = {
        "org": org,
        "brand_voice": cfg.get("brand_voice") or "the brand's established voice",
        "brand_colors": cfg.get("brand_colors") or "#002E2C, #F6F0E9, #61835E, #FFDD1F",
        "utm_pattern": cfg.get("utm_pattern") or "{campaign} / ig-{skill}-{date}",
        "launch": tc.get("title") or cfg.get("launch_name") or "the current launch",
        "handle": rc.get("handle") or "the target account",
    }
    context: dict = {}
    if sk in ("audit", "trend_brief", "_diagnose"):
        roster = (await s.execute(select(AIRosterAccount).where(
            AIRosterAccount.employee_id == employee.id, AIRosterAccount.status != "archived")
            .limit(30))).scalars().all()
        context["roster"] = [{"handle": r.handle, "why": r.why, "in_launch": r.in_launch,
                              "platform": r.platform} for r in roster]
        audits = await recent_cowork_audits(s, employee.id)     # real teardowns from the Cowork bridge
        if audits:
            context["recent_audits"] = audits
    since = _now() - dt.timedelta(days=90)
    intel = (await s.execute(select(AIIntelEntry).where(
        AIIntelEntry.employee_id == employee.id, AIIntelEntry.created_at >= since)
        .order_by(AIIntelEntry.created_at.desc()).limit(40))).scalars().all()
    if intel:
        context["intel"] = [{"finding": i.finding, "tags": i.tags} for i in intel]
    if tc:
        context["pacing"] = tc
    if rc.get("material"):
        context["source_material"] = str(rc["material"])[:8000]
    if prior:                                    # earlier steps' outputs, so the chain builds on itself
        context["prior_work"] = prior
    return scalars, context


# ── budget (§2 hard stop) ────────────────────────────────────────────────────
async def tokens_used_this_month(s, tenant_id) -> int:
    start = _now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    r = (await s.execute(select(func.coalesce(func.sum(AIRun.tokens_in + AIRun.tokens_out), 0))
         .where(AIRun.tenant_id == tenant_id, AIRun.created_at >= start))).scalar()
    return int(r or 0)


async def _over_budget(s, tenant_id) -> bool:
    budget = settings.AI_EMPLOYEES_TOKEN_BUDGET
    return bool(budget) and (await tokens_used_this_month(s, tenant_id)) >= budget


# ── condition: pace_check bound to compute_shift ─────────────────────────────
async def _tenant_shift_launch(s, tenant_id, today) -> Launch | None:
    """The active launch (any business) that has a Shift configured — the pace source."""
    rows = (await s.execute(select(Launch).where(
        Launch.tenant_id == tenant_id, Launch.is_active.is_(True),
        Launch.shift_goal.isnot(None)))).scalars().all()
    live = [l for l in rows if l.window_start <= today <= l.window_end]
    if live:
        return min(live, key=lambda l: l.window_end)
    upcoming = [l for l in rows if today < l.window_start <= today + dt.timedelta(days=30)]
    if upcoming:
        return min(upcoming, key=lambda l: l.window_start)
    return min(rows, key=lambda l: abs((l.window_end - today).days)) if rows else None


async def pace_facts(s, tenant_id, today: dt.date) -> dict | None:
    """The current launch-pacing signal as a trigger_context (source/label/title/facts), with
    no threshold gate — used by the manual 'run full response' and by eval_pace_check."""
    launch = await _tenant_shift_launch(s, tenant_id, today)
    if not launch:
        return None
    shift = await compute_shift(s, tenant_id, launch, int(launch.seat_goal or 0), today)
    if not shift or not shift.get("expected"):
        return None
    reg, exp, goal = shift["registrants"], shift["expected"], shift["goal"]
    shortfall = round((1 - reg / exp) * 100, 1) if exp else 0.0
    dte = shift.get("days_to_event")
    facts = [f"{reg:,} of {goal:,} registered", f"{shortfall:.0f}% under curve"]
    if dte is not None:
        facts.insert(0, f"{dte} days to event")
    return {"source": "GoHighLevel", "label": "Daily pace check",
            "title": f"{shift['name']} is trailing the registration curve", "facts": facts,
            "shortfall_pct": shortfall, "registrants": reg, "expected": exp}


async def eval_pace_check(s, tenant_id, cond: dict, today: dt.date) -> dict | None:
    """The condition gate: the current pace signal IF registrations trail the curve by more
    than threshold_pct_under_curve, else None."""
    tc = await pace_facts(s, tenant_id, today)
    if not tc:
        return None
    if tc["shortfall_pct"] < float(cond.get("threshold_pct_under_curve", 10)):
        return None
    if cond.get("label"):
        tc["label"] = cond["label"]
    return tc


# ── dispatch (§4 ai_dispatch, per tenant) ────────────────────────────────────
def _in_quiet_hours(employee: AIEmployee, now_local: dt.datetime) -> bool:
    q = (employee.config or {}).get("quiet_hours") or {}
    start, end = q.get("start"), q.get("end")
    if not start or not end:
        return False
    hm = now_local.strftime("%H:%M")
    return (start <= hm < end) if start <= end else (hm >= start or hm < end)   # wraps midnight


async def _cron_due(s, es: AIEmployeeSkill, cron: str, now_local: dt.datetime) -> bool:
    """Due when the cron's most recent fire ≤ now hasn't already produced a scheduled run."""
    try:
        prev_fire = croniter(cron, now_local).get_prev(dt.datetime)
    except Exception:
        return False
    last = (await s.execute(select(func.max(AIRun.created_at)).where(
        AIRun.employee_id == es.employee_id, AIRun.skill_key == es.skill_key,
        AIRun.trigger == "scheduled"))).scalar()
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=dt.timezone.utc)
    return last < prev_fire


async def _condition_fired_today(s, employee_id, max_per_day: int) -> bool:
    """Debounce a condition trigger — count today's condition-fired runs for the employee (the
    pace check queues a 'pace_response' run, so debounce by employee, not by the carrier skill)."""
    day_start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    n = (await s.execute(select(func.count(AIRun.id)).where(
        AIRun.employee_id == employee_id,
        AIRun.trigger == "condition", AIRun.created_at >= day_start))).scalar_one()
    return n >= max_per_day


async def _queue_run(s, tenant_id, employee, skill_key, trigger, trigger_context, over_budget):
    status = "skipped_budget" if over_budget else "queued"
    run = AIRun(tenant_id=tenant_id, employee_id=employee.id, skill_key=skill_key,
                trigger=trigger, status=status, trigger_context=trigger_context)
    s.add(run)
    await s.flush()
    audit(s, tenant_id, None, f"ai.run_{status}", "ai_run", str(run.id),
          {"skill": skill_key, "trigger": trigger, "employee": str(employee.id)})
    return run


async def dispatch_tenant(s, tenant_id, now: dt.datetime) -> None:
    if not enabled():
        return
    over_budget = await _over_budget(s, tenant_id)
    emps = (await s.execute(select(AIEmployee).where(
        AIEmployee.tenant_id == tenant_id, AIEmployee.status == "active"))).scalars().all()
    queued = False
    for emp in emps:
        tz = _tz(emp)
        now_local = now.astimezone(tz)
        skills = (await s.execute(select(AIEmployeeSkill).where(
            AIEmployeeSkill.employee_id == emp.id, AIEmployeeSkill.enabled.is_(True)))).scalars().all()
        for es in skills:
            skill_def = SKILL_BY_KEY.get(es.skill_key)
            if not skill_def:
                continue
            cron = es.schedule_override or skill_def.get("default_schedule")
            if cron == "manual":                 # explicit "no schedule" override (§7.2)
                cron = None
            if cron and not _in_quiet_hours(emp, now_local) and await _cron_due(s, es, cron, now_local):
                await _queue_run(s, tenant_id, emp, es.skill_key, "scheduled", None, over_budget)
                queued = True
            cond = (es.config or {}).get("condition") or {}
            if cond.get("type") == "pace_check":
                if await _condition_fired_today(s, emp.id, int(cond.get("max_per_day", 1))):
                    continue
                tc = await eval_pace_check(s, tenant_id, cond, now_local.date())
                if tc:                            # the gap fires Summer's FULL coordinated response
                    await _queue_run(s, tenant_id, emp, PACE_RESPONSE, "condition", tc, over_budget)
                    queued = True
    if queued:
        await s.commit()


# ── execute (§4 ai_execute, per tenant) ──────────────────────────────────────
async def execute_one(s, tenant_id) -> bool:
    """Take the oldest queued run, execute it to draft artifacts. Returns True if a run was
    picked (worker keeps calling while True), False if the queue is empty."""
    if not enabled():
        return False
    q = (select(AIRun).where(AIRun.tenant_id == tenant_id, AIRun.status == "queued")
         .order_by(AIRun.created_at).limit(1))
    if not settings.is_sqlite:                       # backstop if the API runs >1 process
        q = q.with_for_update(skip_locked=True)
    run = (await s.execute(q)).scalar_one_or_none()
    if not run:
        return False
    run.status, run.started_at = "running", _now()
    await s.commit()

    if run.skill_key == PACE_RESPONSE:                # the coordinated multi-skill cascade
        await execute_pace_response(s, tenant_id, run)
        return True

    emp = await s.get(AIEmployee, run.employee_id)
    es = (await s.execute(select(AIEmployeeSkill).where(
        AIEmployeeSkill.employee_id == run.employee_id,
        AIEmployeeSkill.skill_key == run.skill_key))).scalar_one_or_none()
    skill_def = SKILL_BY_KEY.get(run.skill_key)
    run.finished_at = _now()
    if not emp or not skill_def:
        run.status, run.error = "failed", f"unknown skill '{run.skill_key}'"
        audit(s, tenant_id, None, "ai.run_failed", "ai_run", str(run.id), {"error": run.error})
        await s.commit()
        return True
    try:
        scalars, context = await build_context(s, tenant_id, emp, es, run)
        template = (es.prompt_override if es and es.prompt_override else skill_def["default_prompt"])
        prompt = _fill_prompt(template, scalars, context)
        result, tin, tout, err = await call_skill(skill_def, prompt)
    except Exception as e:                                   # a live-API failure is a failed run, not a crash
        log.warning("ai_execute run %s errored: %s", run.id, e)
        result, tin, tout, err = None, run.tokens_in, run.tokens_out, f"{type(e).__name__}"
    run.tokens_in, run.tokens_out, run.finished_at = tin, tout, _now()
    if result is None:
        run.status, run.error = "failed", err or "invalid output"
        audit(s, tenant_id, None, "ai.run_failed", "ai_run", str(run.id), {"error": run.error})
    else:
        run.reads = result.get("reads") or []
        run.summary = result.get("summary")
        for a in result.get("artifacts", []):
            payload = a.get("payload") or {}
            kind = payload.get("kind") or (skill_def["artifact_kinds"] or ["audit"])[0]
            meta = KIND_META.get(kind, {"lane": "Intel", "dest_label": None})
            s.add(AIArtifact(tenant_id=tenant_id, run_id=run.id, kind=kind, lane=meta["lane"],
                             title=a.get("title") or skill_def["name"], dest_label=meta["dest_label"],
                             payload=payload, state="draft"))
        run.status = "awaiting_approval"
        audit(s, tenant_id, None, "ai.run_drafted", "ai_run", str(run.id),
              {"skill": run.skill_key, "artifacts": len(result.get("artifacts", []))})
    await s.commit()
    return True


# ── the coordinated response (Summer working end to end) ─────────────────────
async def _diagnose(client, model, scalars: dict, context: dict):
    """One call → the run-level diagnosis lines (the mockup's 'she diagnosed the gap')."""
    prompt = _fill_prompt(_DIAGNOSE_PROMPT, scalars, context)
    text, tin, tout = await _claude_call(client, model, _SYSTEM, prompt)
    obj = _parse_json(text) or {}
    reads = obj.get("reads") if isinstance(obj.get("reads"), list) else []
    return [str(r) for r in reads][:6], tin, tout


async def execute_pace_response(s, tenant_id, run: AIRun) -> None:
    """The orchestration: diagnose the gap, then run each enabled skill in dependency order,
    chaining outputs, and land EVERY draft on this one run for a single batch approval. Writes
    incrementally (status stays 'running') so the run surface shows the response build up live,
    then flips to awaiting_approval. A skill that fails is skipped, not fatal."""
    emp = await s.get(AIEmployee, run.employee_id)
    if not emp:
        run.status, run.error, run.finished_at = "failed", "unknown employee", _now()
        audit(s, tenant_id, None, "ai.run_failed", "ai_run", str(run.id), {"skill": PACE_RESPONSE})
        await s.commit()
        return
    client, model = _client(), _model()
    enabled_skills = {es.skill_key: es for es in (await s.execute(select(AIEmployeeSkill).where(
        AIEmployeeSkill.employee_id == emp.id, AIEmployeeSkill.enabled.is_(True)))).scalars().all()}
    # The audit step may already be done — Cowork browsed the roster and filed real audits on
    # this run (the one-button flow). If so, skip generating a blind server audit and build the
    # rest of the cascade on those real teardowns.
    existing_audits = (await s.execute(select(AIArtifact).where(
        AIArtifact.run_id == run.id, AIArtifact.kind == "audit"))).scalars().all()
    order = [k for k in PACE_RESPONSE_ORDER if k in enabled_skills and k in SKILL_BY_KEY
             and not (k == "audit" and existing_audits)]
    tin_total = tout_total = 0

    # 1) diagnose the gap
    try:
        scalars, base_ctx = await build_context(s, tenant_id, emp, None, run, skill_key="_diagnose")
        reads, di, do = await _diagnose(client, model, scalars, base_ctx)
        tin_total += di
        tout_total += do
        run.reads = reads
        run.tokens_in, run.tokens_out = tin_total, tout_total
        await s.commit()                             # reads appear on the surface
    except Exception as e:
        log.warning("pace_response diagnose failed for run %s: %s", run.id, e)

    # 2) run each skill in order, chaining prior outputs, committing as each lands.
    #    Seed from the Cowork audits already on the run so downstream steps build on them.
    prior, kinds = {}, []
    if existing_audits:
        prior["audit"] = existing_audits[0].payload
        kinds = ["audit"] * len(existing_audits)
    for key in order:
        es, skill_def = enabled_skills[key], SKILL_BY_KEY[key]
        try:
            scalars, ctx = await build_context(s, tenant_id, emp, es, run, skill_key=key, prior=prior)
            template = es.prompt_override or skill_def["default_prompt"]
            result, tin, tout, err = await call_skill(
                skill_def, _fill_prompt(template, scalars, ctx), client=client, model=model)
        except Exception as e:
            log.warning("pace_response skill %s errored for run %s: %s", key, run.id, e)
            result, tin, tout = None, 0, 0
        tin_total += tin
        tout_total += tout
        for a in (result or {}).get("artifacts", []):
            payload = a.get("payload") or {}
            kind = payload.get("kind") or (skill_def["artifact_kinds"] or ["audit"])[0]
            meta = KIND_META.get(kind, {"lane": "Intel", "dest_label": None})
            s.add(AIArtifact(tenant_id=tenant_id, run_id=run.id, kind=kind, lane=meta["lane"],
                             title=a.get("title") or skill_def["name"], dest_label=meta["dest_label"],
                             payload=payload, state="draft"))
            prior[kind] = payload
            kinds.append(kind)
        run.tokens_in, run.tokens_out = tin_total, tout_total
        await s.commit()                             # each artifact appears as it's produced

    run.finished_at = _now()
    if not kinds:
        run.status, run.error = "failed", "the response produced no artifacts"
        audit(s, tenant_id, None, "ai.run_failed", "ai_run", str(run.id), {"skill": PACE_RESPONSE})
    else:
        lanes = sorted({KIND_META.get(k, {}).get("lane", "Intel") for k in kinds})
        run.summary = f"Full response — {len(kinds)} drafts across {', '.join(lanes)}."
        run.status = "awaiting_approval"
        audit(s, tenant_id, None, "ai.run_drafted", "ai_run", str(run.id),
              {"skill": PACE_RESPONSE, "artifacts": len(kinds)})
    await s.commit()


# ── helpers shared with the router (§5, §6) ──────────────────────────────────
DEFAULT_ROSTER_WEIGHTS = {"overlap": 1.0, "offer": 1.0, "perf": 1.0, "launch_boost": 1.5}


def next_run_at(cron: str | None, after: dt.datetime) -> dt.datetime | None:
    """The next fire of a cron after `after` (tz preserved). None for manual/invalid."""
    if not cron or cron == "manual":            # "manual" sentinel = no schedule (§7.2)
        return None
    try:
        return croniter(cron, after).get_next(dt.datetime)
    except Exception:
        return None


def writeback_open(employee: AIEmployee) -> bool:
    """Both gates — the env flag AND the per-employee toggle — like the Books pattern."""
    return bool(settings.AI_EMPLOYEES_WRITEBACK_ENABLED and employee.writeback_enabled)


def roster_priority(acc: AIRosterAccount, weights: dict | None = None) -> float:
    """Computed, never stored: weighted score sum with an in-launch boost (§3 note)."""
    w = {**DEFAULT_ROSTER_WEIGHTS, **(weights or {})}
    base = (acc.score_overlap * w["overlap"] + acc.score_offer * w["offer"]
            + acc.score_perf * w["perf"])
    return round(base * (w["launch_boost"] if acc.in_launch else 1.0), 2)


async def seed_employee_skills(s, employee: AIEmployee) -> None:
    """Give a new employee the six product skills (enabled), pinning each seed_version so a
    later seed upgrade can flag a stale override. Caller commits."""
    vers = {k: v for k, v in (await s.execute(select(AISkill.key, AISkill.version))).all()}
    for d in SKILLS:
        s.add(AIEmployeeSkill(tenant_id=employee.tenant_id, employee_id=employee.id,
                              skill_key=d["key"], enabled=True, seed_version=vers.get(d["key"], 1)))
