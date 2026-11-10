"""The weekly coaching digest for a lead-source group (docs/specs/ulrg-reporting-dashboard-port.md §4.6,
phase 6).

Ported from the legacy `digest_select.py` and `benchmarks.py`: the same scoring components (speed,
persistence, channels, funnel leak), the same tiers (logging, coach, address, reinforce), the same
top-performer gate. What moved is everything that was code or environment there:

  * the coaching bar -- the floor and target rate for each funnel transition, and the speed,
    attempts and channel bars -- is data on `lead_source_digest.benchmarks`, editable in Settings;
  * the schedule, the recipients, the ClickUp list and assignee, and the model are columns;
  * the ClickUp token is a tenant secret on an `integration` row (provider `clickup`);
  * mail goes through Axcion's own mailer (services/mailer.py, Resend), not a second SMTP stack.

ONCE A WEEK, NEVER TWICE. A scheduled send is a `lead_source_digest_delivery` row keyed
`week:<week ending>`, unique per digest, so a second tick in the same week finds it and stops. A
failed send is retried on later ticks and gives up after three attempts.

THE PROSE. Each block is written from a template first, which is what is sent when there is no
ANTHROPIC_API_KEY. With a key, the configured model may rewrite a block's sentences; its answer is
kept only if every number in it is a number the template already said. Anything else falls back.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import uuid
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from ..config import settings
from ..models import (Business, Integration, LeadSourceDigest, LeadSourceDigestDelivery, LeadSourceGroup,
                      User)
from ..security import dec, make_capability, read_capability
from . import mailer
from .lead_source_behavior import FUNNEL_TRANSITIONS, compute_metric_set, funnel_diagnostics, is_dead
from .lead_source_pipeline import in_window
from .lead_source_report import load_universe

DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_ATTEMPTS = 3
RECENT_WINDOW = "rolling_30"
UNSUB_PURPOSE = "lead_source_digest_unsubscribe"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Installed by tests (an httpx.MockTransport) for ClickUp. None = the network.
CLICKUP_TRANSPORT: httpx.AsyncBaseTransport | None = None
CLICKUP_API = "https://api.clickup.com/api/v2"

# The legacy coaching bar (benchmarks.py): the per-stage rates that compound to an 8% close rate at
# the floor and 10% at the target. DEFAULTS for a new digest, not constants: a tenant edits them.
DEFAULT_BENCHMARKS = {
    "transitions": {
        "leads_received__contacted": {"floor": 0.85, "target": 0.90},
        "contacted__appt_set": {"floor": 0.40, "target": 0.42},
        "appt_set__met_with": {"floor": 0.80, "target": 0.83},
        "met_with__showing": {"floor": 0.65, "target": 0.68},
        "showing__offer_written": {"floor": 0.55, "target": 0.58},
        "offer_written__closed": {"floor": 0.80, "target": 0.82},
    },
    "speed_first_touch_min": {"floor": 15, "target": 5},
    "pct_5plus_attempts": {"floor": 70, "target": 85},
    "multichannel_24h_pct": {"floor": 60, "target": 80},
    "top_performer": {"close_rate": 0.10, "min_closes": 2},
}
COACH_THRESHOLD, ADDRESS_THRESHOLD, SECONDARY_GAP_RATIO = 2.0, 0.8, 0.6


def benchmarks_of(d: LeadSourceDigest) -> dict:
    """The digest's bar, with any key it does not set taken from the defaults."""
    out = json.loads(json.dumps(DEFAULT_BENCHMARKS))
    for k, v in (d.benchmarks or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            for k2, v2 in v.items():
                if isinstance(v2, dict) and isinstance(out[k].get(k2), dict):
                    out[k][k2].update(v2)
                else:
                    out[k][k2] = v2
    return out


def product(rates: list[float]) -> float:
    p = 1.0
    for r in rates:
        p *= r
    return p


# ── schedule ─────────────────────────────────────────────────────────────────────────────────

async def tz_of(s, d: LeadSourceDigest) -> dt.tzinfo:
    if d.timezone:
        try:
            return ZoneInfo(d.timezone)
        except Exception:  # noqa: BLE001 -- a bad name falls back to the workspace's zone
            pass
    from .sync import _workspace_tz
    return await _workspace_tz(s, d.tenant_id)


def scheduled_at(d: LeadSourceDigest, now_local: dt.datetime) -> dt.datetime:
    """This week's send time in the digest's zone (weeks start Monday)."""
    monday = now_local.date() - dt.timedelta(days=now_local.weekday())
    day = monday + dt.timedelta(days=int(d.send_weekday or 0))
    return dt.datetime.combine(day, dt.time(int(d.send_hour or 0), int(d.send_minute or 0)), tzinfo=now_local.tzinfo)


def week_ending(send_local: dt.datetime) -> dt.date:
    """The Sunday the digest's week closed on: the day before a Monday send, or earlier."""
    return send_local.date() - dt.timedelta(days=send_local.weekday() + 1)


async def enqueue_due(s, now: dt.datetime | None = None) -> list[LeadSourceDigestDelivery]:
    """Queue this week's delivery for every active digest whose send time has passed. Idempotent:
    the week's row exists after the first tick, and the unique key refuses a second."""
    now = now or dt.datetime.now(dt.timezone.utc)
    made = []
    for d in (await s.execute(select(LeadSourceDigest).where(LeadSourceDigest.is_active.is_(True)))).scalars():
        tz = await tz_of(s, d)
        when = scheduled_at(d, now.astimezone(tz))
        if now.astimezone(tz) < when:
            continue
        key = f"week:{week_ending(when).isoformat()}"
        have = (await s.execute(select(LeadSourceDigestDelivery.id).where(
            LeadSourceDigestDelivery.digest_id == d.id, LeadSourceDigestDelivery.run_key == key))).first()
        if have:
            continue
        row = LeadSourceDigestDelivery(id=uuid.uuid4(), tenant_id=d.tenant_id, digest_id=d.id, run_key=key,
                                       week_ending=week_ending(when), status="queued")
        s.add(row)
        await s.commit()
        made.append(row)
    return made


# ── scoring (legacy digest_select.py, against the digest's own bar) ─────────────────────────

def score_agent(m: dict, bar: dict) -> dict:
    log = m.get("logging_diagnostics") or {}
    if log.get("is_hygiene_suspect"):
        return {"total": 999.0, "components": {}, "primary": "hygiene", "secondary": None,
                "hygiene_flag": True, "hygiene_reason": log.get("hygiene_reason")}
    comps = {}
    sp = m.get("speed_to_first_min")
    floor = bar["speed_first_touch_min"]["floor"]
    comps["speed"] = None if sp is None else (0.0 if sp <= floor else min(3.0, sp / floor - 1.0))
    p5 = m.get("pct_5plus_attempts")
    comps["persistence"] = None if p5 is None else min(2.0, max(0.0, bar["pct_5plus_attempts"]["floor"] - p5) / 25.0)
    mc = m.get("multichannel_24h_pct")
    comps["channel"] = None if mc is None else min(1.5, max(0.0, bar["multichannel_24h_pct"]["floor"] - mc) / 30.0)
    worst = (m.get("funnel") or {}).get("worst_transition")
    lost = (worst or {}).get("leads_lost")
    if not worst or not lost or lost <= 0:
        comps["funnel"] = None
    elif lost <= 1:
        comps["funnel"] = float(lost)
    elif lost <= 3:
        comps["funnel"] = 1.0 + (lost - 1) / 2.0
    else:
        comps["funnel"] = min(3.0, 2.0 + (lost - 3) / 3.0)
    ranked = sorted(((k, v) for k, v in comps.items() if v is not None), key=lambda kv: kv[1], reverse=True)
    primary = ranked[0][0] if ranked else None
    secondary = ranked[1][0] if len(ranked) > 1 and ranked[0][1] > 0 and ranked[1][1] >= SECONDARY_GAP_RATIO * ranked[0][1] else None
    return {"total": sum(v for _k, v in ranked), "components": {k: v for k, v in comps.items() if v is not None},
            "primary": primary, "secondary": secondary, "hygiene_flag": False, "funnel_detail": worst}


def is_top_performer(m: dict, bar: dict) -> bool:
    n, c = m.get("n_leads") or 0, m.get("n_closed") or 0
    return n > 0 and c >= bar["top_performer"]["min_closes"] and c / n >= bar["top_performer"]["close_rate"]


def _mins(v) -> str:
    if v is None:
        return "no first touch"
    return f"{round(v)} minutes" if v < 90 else f"{v / 60:.1f} hours"


def block_for(agent: str, tier: str, m: dict, sc: dict, bar: dict, window_label: str) -> dict:
    """One agent's block: a headline and two short sentences, from the legacy templates."""
    first = agent.split()[0]
    if tier == "hygiene":
        return {"agent": agent, "tier": tier, "headline": f"{first}'s logging is too thin to coach from",
                "body": f"{sc.get('hygiene_reason') or 'Too few logged touches to read.'} Log calls and texts in "
                        f"Follow Up Boss, even the ones that don't connect, so next week's numbers are real."}
    if tier == "reinforce":
        return {"agent": agent, "tier": tier, "headline": f"{first} is doing it right",
                "body": f"{m.get('n_closed', 0)} closed from {m.get('n_leads', 0)} leads, first touch in a median "
                        f"{_mins(m.get('speed_to_first_min'))}, and {m.get('pct_5plus_attempts') or 0:.0f}% of leads worked five "
                        f"times or more. Worth saying out loud in the team meeting."}
    primary = sc.get("primary")
    if primary == "speed":
        body = (f"Median first touch was {_mins(m.get('speed_to_first_min'))} {window_label}, against a "
                f"{bar['speed_first_touch_min']['floor']}-minute floor. Paid leads go to whoever calls first: the bar is "
                f"{bar['speed_first_touch_min']['target']} minutes.")
        head = f"{first} is reaching new leads too slowly"
    elif primary == "persistence":
        body = (f"{m.get('pct_5plus_attempts') or 0:.0f}% of leads got five or more attempts {window_label}, against "
                f"a {bar['pct_5plus_attempts']['floor']}% floor. Most of these leads answer on the fourth to sixth try.")
        head = f"{first} is giving up on leads early"
    elif primary == "channel":
        body = (f"{m.get('multichannel_24h_pct') or 0:.0f}% of leads heard from two channels in the first day {window_label}, "
                f"against a {bar['multichannel_24h_pct']['floor']}% floor. A text after an unanswered call roughly doubles the connect rate.")
        head = f"{first} is calling without texting"
    elif primary == "funnel" and sc.get("funnel_detail"):
        w = sc["funnel_detail"]
        rate = round((w.get("agent_rate") or 0) * 100)
        floor = round((w.get("benchmark_rate") or 0) * 100)
        body = (f"{w['label']} converts at {rate}% against a {floor}% floor, about {w['leads_lost']:.0f} "
                f"lead{'s' if round(w['leads_lost']) != 1 else ''} short {window_label}. Walk the stuck list together and pick the next step for each.")
        head = f"{first}'s pipeline leaks at {w['label'].lower()}"
    else:
        body = f"Below the bar on more than one measure {window_label}. Review the stuck list together."
        head = f"{first} needs a pipeline review"
    return {"agent": agent, "tier": tier, "headline": head, "body": body}


async def compose(s, d: LeadSourceDigest, group: LeadSourceGroup, business: Business, *, now=None) -> dict:
    """Pick the agents and write their blocks from the stored universe -- no CRM calls."""
    bar = benchmarks_of(d)
    floors = {k: v["floor"] for k, v in bar["transitions"].items()}
    uni = await load_universe(s, d.tenant_id, business.id, group, now=now)
    now, today = uni["now"], uni["today"]
    lifetime, recent = uni["people"], in_window(uni["people"], RECENT_WINDOW, today)
    for p in lifetime:
        p["created_local_hour"] = p["created"].astimezone(uni["tz"]).hour
    by_recent, by_life, names = {}, {}, {}
    for p in recent:
        by_recent.setdefault(p.get("agent_id") or "unassigned", []).append(p)
    for p in lifetime:
        k = p.get("agent_id") or "unassigned"
        by_life.setdefault(k, []).append(p)
        names[k] = p.get("assigned_to") or "Unassigned"
    team = compute_metric_set(recent)
    scored = []
    for k, people in by_recent.items():
        if k == "unassigned" or len(people) < max(1, int(d.min_leads or 1)):
            continue
        m = compute_metric_set(people)
        m["funnel"] = funnel_diagnostics(people, recent, floors)
        scored.append({"key": k, "agent": names[k], "m": m, "life": compute_metric_set(by_life.get(k, [])),
                       "score": score_agent(m, bar)})
    window_label = "over the last 30 days"
    hygiene = sorted((x for x in scored if x["score"]["hygiene_flag"]),
                     key=lambda x: -(x["m"]["logging_diagnostics"].get("outcome_count") or 0))[:2]
    normal = sorted((x for x in scored if not x["score"]["hygiene_flag"]), key=lambda x: -x["score"]["total"])
    eligible = [x for x in normal if not is_top_performer(x["life"], bar)]
    coach = [x for x in eligible if x["score"]["total"] >= COACH_THRESHOLD][:3]
    taken = {x["key"] for x in coach}
    address = [x for x in eligible if x["key"] not in taken and x["score"]["total"] >= ADDRESS_THRESHOLD][:2]
    taken |= {x["key"] for x in address} | {x["key"] for x in hygiene}
    reinforce = []
    if d.include_reinforce:
        pool = [x for x in normal if x["key"] not in taken]
        pool.sort(key=lambda x: (-(x["life"].get("n_closed") or 0), x["score"]["total"]))
        reinforce = pool[:1]
    blocks = ([block_for(x["agent"], "hygiene", x["m"], x["score"], bar, window_label) for x in hygiene]
              + [block_for(x["agent"], "coach", x["m"], x["score"], bar, window_label) for x in coach]
              + [block_for(x["agent"], "address", x["m"], x["score"], bar, window_label) for x in address]
              + [block_for(x["agent"], "reinforce", x["life"], x["score"], bar, window_label) for x in reinforce])
    cap = max(1, int(d.max_agents or 6))
    while len(blocks) > cap and any(b["tier"] == "address" for b in blocks):
        blocks.remove(next(b for b in reversed(blocks) if b["tier"] == "address"))
    while len(blocks) > cap and any(b["tier"] == "reinforce" for b in blocks):
        blocks.remove(next(b for b in reversed(blocks) if b["tier"] == "reinforce"))
    trends = []
    sp = team.get("speed_to_first_min")
    if sp is not None and sp > bar["speed_first_touch_min"]["floor"]:
        trends.append(f"The team's median first touch is {_mins(sp)}, over the {bar['speed_first_touch_min']['floor']}-minute floor.")
    mc = team.get("multichannel_24h_pct")
    if mc is not None and mc < bar["multichannel_24h_pct"]["floor"]:
        trends.append(f"{mc:.0f}% of new leads heard from two channels in their first day, under the {bar['multichannel_24h_pct']['floor']}% floor.")
    dead = sum(1 for p in recent if is_dead(p, now))
    return {"blocks": blocks, "trends": trends, "team": team, "leads": len(recent), "dead": dead,
            "floor_close": round(product([v["floor"] for v in bar["transitions"].values()]) * 100, 1),
            "target_close": round(product([v["target"] for v in bar["transitions"].values()]) * 100, 1)}


# ── the optional model rewrite ───────────────────────────────────────────────────────────────

_NUM = re.compile(r"\d+(?:\.\d+)?")


async def _rewrite(model: str, blocks: list[dict]) -> list[dict] | None:
    """Ask the configured model for plainer sentences. Kept only when it invents no number."""
    if not settings.ANTHROPIC_API_KEY or not blocks:
        return None
    try:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        prompt = ("Rewrite each block's body for a real estate team leader's weekly coaching email: two short, "
                  "direct sentences, warm but specific, no greeting. Keep every number exactly as given and add "
                  "no number. Return only JSON: a list of {\"agent\", \"body\"} in the same order.\n\n"
                  + json.dumps([{"agent": b["agent"], "tier": b["tier"], "body": b["body"]} for b in blocks]))
        resp = await client.messages.create(model=model, max_tokens=1200,
                                            messages=[{"role": "user", "content": prompt}])
        text = "".join(getattr(part, "text", "") for part in resp.content).strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
        out = json.loads(text)
        if not isinstance(out, list) or len(out) != len(blocks):
            return None
        new = []
        for b, o in zip(blocks, out):
            body = str((o or {}).get("body") or "").strip()
            if not body or not set(_NUM.findall(body)) <= set(_NUM.findall(b["body"])):
                return None
            new.append({**b, "body": body})
        return new
    except Exception:  # noqa: BLE001 -- any failure is the template, never a failed send
        return None


# ── rendering ────────────────────────────────────────────────────────────────────────────────

TIER_TITLES = {"hygiene": "Fix the logging first", "coach": "Sit down with", "address": "Mention in passing",
               "reinforce": "Recognize"}


def render(group: LeadSourceGroup, body: dict, week: dt.date, unsub_url: str | None) -> tuple[str, str, str]:
    subject = f"{group.display_name} coaching · week ending {week.strftime('%b')} {week.day}"
    order = ["hygiene", "coach", "address", "reinforce"]
    parts_h, parts_t = [], []
    esc = html.escape
    parts_h.append(f'<div style="font-family:Arial,sans-serif;color:#16201F;max-width:620px">'
                   f'<h2 style="font-size:20px;font-weight:600;margin:0 0 4px">{esc(group.display_name)} · weekly coaching</h2>'
                   f'<p style="font-size:13px;color:#5F645C;margin:0 0 20px">{body["leads"]} new leads in the last 30 days. '
                   f'The bar is {body["floor_close"]}% to {body["target_close"]}% close.</p>')
    parts_t.append(f"{group.display_name} · weekly coaching\n{body['leads']} new leads in the last 30 days.\n")
    for line in body["trends"]:
        parts_h.append(f'<p style="font-size:14px;margin:0 0 8px">{esc(line)}</p>')
        parts_t.append(f"- {line}")
    for tier in order:
        tier_blocks = [b for b in body["blocks"] if b["tier"] == tier]
        if not tier_blocks:
            continue
        parts_h.append(f'<h3 style="font-size:14px;font-weight:600;margin:20px 0 8px;color:#3F6B66">{TIER_TITLES[tier]}</h3>')
        parts_t.append(f"\n{TIER_TITLES[tier].upper()}")
        for b in tier_blocks:
            parts_h.append(f'<div style="border-top:1px solid #E3E5DF;padding:10px 0"><div style="font-size:14px;font-weight:600">'
                           f'{esc(b["agent"])} · {esc(b["headline"])}</div><div style="font-size:14px;line-height:1.5;margin-top:4px">'
                           f'{esc(b["body"])}</div></div>')
            parts_t.append(f"{b['agent']}: {b['headline']}\n{b['body']}\n")
    if not body["blocks"]:
        parts_h.append('<p style="font-size:14px">Nobody cleared the bar for a coaching note this week.</p>')
        parts_t.append("Nobody cleared the bar for a coaching note this week.")
    if unsub_url:
        parts_h.append(f'<p style="font-size:12px;color:#5F645C;margin-top:24px"><a href="{esc(unsub_url)}" style="color:#5F645C">'
                       f'Stop sending me this digest</a></p>')
        parts_t.append(f"\nStop sending me this digest: {unsub_url}")
    parts_h.append("</div>")
    return subject, "".join(parts_h), "\n".join(parts_t)


def unsubscribe_token(d: LeadSourceDigest, email: str) -> str:
    return make_capability(UNSUB_PURPOSE, minutes=60 * 24 * 120, tid=str(d.tenant_id), did=str(d.id),
                           email=email.strip().lower())


async def unsubscribe(s, token: str) -> str | None:
    """Honour an unsubscribe link. Returns the address removed, or None for a bad or stale link."""
    try:
        data = read_capability(token, UNSUB_PURPOSE)
        did, email = uuid.UUID(data["did"]), str(data["email"]).lower()
    except Exception:  # noqa: BLE001
        return None
    d = await s.get(LeadSourceDigest, did)
    if d is None or str(d.tenant_id) != data.get("tid"):
        return None
    if email not in (d.unsubscribed or []):
        d.unsubscribed = [*(d.unsubscribed or []), email]
    await s.commit()
    return email


# ── ClickUp ──────────────────────────────────────────────────────────────────────────────────

async def clickup_token(s, tenant_id) -> str | None:
    integ = (await s.execute(select(Integration).where(Integration.tenant_id == tenant_id,
                                                       Integration.provider == "clickup"))).scalars().first()
    if integ is None or not integ.access_token_enc:
        return None
    try:
        return dec(integ.access_token_enc) or None
    except Exception:  # noqa: BLE001
        return None


async def clickup_tasks(s, d: LeadSourceDigest, blocks: list[dict], week: dt.date) -> list[dict]:
    """One task per coaching block, when -- and only when -- the digest names a ClickUp list."""
    if not (d.clickup_list_id or "").strip():
        return []
    token = await clickup_token(s, d.tenant_id)
    if not token:
        return [{"error": "No ClickUp token is saved for this workspace."}]
    made = []
    due = int(dt.datetime.combine(week + dt.timedelta(days=7), dt.time(17)).timestamp() * 1000)
    async with httpx.AsyncClient(base_url=CLICKUP_API, timeout=20, transport=CLICKUP_TRANSPORT,
                                 headers={"Authorization": token}) as c:
        for b in blocks:
            if b["tier"] == "reinforce":
                continue
            assignee = (d.agent_map or {}).get(b["agent"]) or d.clickup_assignee
            body = {"name": f"{b['agent']}: {b['headline']}", "description": b["body"], "due_date": due}
            if assignee and str(assignee).isdigit():
                body["assignees"] = [int(assignee)]
            r = await c.post(f"/list/{d.clickup_list_id.strip()}/task", json=body)
            made.append({"agent": b["agent"], "status": r.status_code,
                         "id": (r.json() or {}).get("id") if r.status_code < 300 else None})
    return made


# ── sending ──────────────────────────────────────────────────────────────────────────────────

async def send(s, row: LeadSourceDigestDelivery, *, now=None) -> LeadSourceDigestDelivery:
    """One attempt at one delivery. Records the outcome on the row; never raises."""
    d = await s.get(LeadSourceDigest, row.digest_id)
    row.attempts = (row.attempts or 0) + 1
    try:
        group = await s.get(LeadSourceGroup, d.lead_source_group_id)
        business = await s.get(Business, group.business_id)
        body = await compose(s, d, group, business, now=now)
        rewritten = await _rewrite(d.model or DEFAULT_MODEL, body["blocks"])
        if rewritten is not None:
            body["blocks"] = rewritten
        recipients = [r for r in (d.recipients or []) if r and r.lower() not in (d.unsubscribed or [])]
        if not recipients:
            row.status, row.last_error = "skipped", "No recipients (everyone unsubscribed or none set)."
            await s.commit()
            return row
        from ..tenancy import tenant_app_url
        base = await tenant_app_url(s, d.tenant_id)
        ok = True
        subject = None
        for to in recipients:
            unsub = f"{base}/digest/unsubscribe?token={unsubscribe_token(d, to)}"
            subject, page, text = render(group, body, row.week_ending, unsub)
            ok = await mailer.send(to, ("[Test] " if row.is_test else "") + subject, page, text,
                                   idempotency_key=f"lsd-{row.id}-{to}") and ok
        row.recipients, row.subject = recipients, subject
        row.agents = [b["agent"] for b in body["blocks"]]
        row.model_used = (d.model or DEFAULT_MODEL) if rewritten is not None else "template"
        if not ok:
            raise RuntimeError("The mail service refused the message.")
        row.status, row.sent_at, row.last_error = "sent", dt.datetime.now(dt.timezone.utc), None
        # ClickUp after the email has gone, so a ClickUp outage can never cost the email.
        try:
            row.clickup_tasks = await clickup_tasks(s, d, body["blocks"], row.week_ending) or None
        except Exception as e:  # noqa: BLE001
            row.clickup_tasks = [{"error": f"{type(e).__name__}: {e}"[:300]}]
    except Exception as e:  # noqa: BLE001
        row.last_error = f"{type(e).__name__}: {e}"[:500]
        if row.status != "sent":
            row.status = "failed" if row.attempts >= MAX_ATTEMPTS else "queued"
    await s.commit()
    return row


async def drain(s, *, now=None) -> int:
    """Send every queued delivery that has attempts left. One row at a time."""
    rows = (await s.execute(select(LeadSourceDigestDelivery).where(
        LeadSourceDigestDelivery.status == "queued",
        LeadSourceDigestDelivery.attempts < MAX_ATTEMPTS).order_by(LeadSourceDigestDelivery.created_at))).scalars().all()
    for row in rows:
        await send(s, row, now=now)
    return len(rows)


# ── config ───────────────────────────────────────────────────────────────────────────────────

def digest_dict(d: LeadSourceDigest | None, *, token_set: bool, default_tz: str) -> dict:
    bar = benchmarks_of(d) if d else json.loads(json.dumps(DEFAULT_BENCHMARKS))
    return {
        "exists": d is not None, "is_active": bool(d and d.is_active),
        "send_weekday": d.send_weekday if d else 0, "send_hour": d.send_hour if d else 6,
        "send_minute": d.send_minute if d else 0, "timezone": d.timezone if d else None,
        "workspace_timezone": default_tz, "recipients": list(d.recipients or []) if d else [],
        "unsubscribed": list(d.unsubscribed or []) if d else [],
        "max_agents": d.max_agents if d else 6, "min_leads": d.min_leads if d else 1,
        "include_reinforce": bool(d.include_reinforce) if d else True, "benchmarks": bar,
        "floor_close_pct": round(product([v["floor"] for v in bar["transitions"].values()]) * 100, 1),
        "target_close_pct": round(product([v["target"] for v in bar["transitions"].values()]) * 100, 1),
        "transition_labels": {f"{a}__{b}": lbl for a, b, lbl in FUNNEL_TRANSITIONS},
        "clickup_list_id": d.clickup_list_id if d else None, "clickup_assignee": d.clickup_assignee if d else None,
        "agent_map": d.agent_map if d else None, "clickup_token_set": token_set,
        "model": (d.model if d else None) or DEFAULT_MODEL, "model_key_set": bool(settings.ANTHROPIC_API_KEY),
        "weekdays": WEEKDAYS,
    }


async def seed_recipient(s, tenant_id) -> str:
    """The spec's one seeded recipient: the workspace user named Connor Leiva, else his address."""
    u = (await s.execute(select(User).where(User.tenant_id == tenant_id, User.name == "Connor Leiva"))).scalars().first()
    return (u.email if u else "connor.leiva@gmail.com").lower()


async def seed_ulrg_digest(s, tenant_id) -> bool:
    """The dev-fixture twin of migration 0106: Utah Life's Realtor.com digest, inactive, one recipient."""
    g = (await s.execute(select(LeadSourceGroup).join(Business, Business.id == LeadSourceGroup.business_id).where(
        LeadSourceGroup.tenant_id == tenant_id, Business.key == "ulrg",
        LeadSourceGroup.key == "realtor_com"))).scalars().first()
    if g is None or (await s.execute(select(LeadSourceDigest.id).where(
            LeadSourceDigest.lead_source_group_id == g.id))).first():
        return False
    s.add(LeadSourceDigest(tenant_id=tenant_id, lead_source_group_id=g.id, is_active=False,
                           recipients=[await seed_recipient(s, tenant_id)], unsubscribed=[], model=DEFAULT_MODEL))
    return True
