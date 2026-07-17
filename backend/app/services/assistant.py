"""Dashboard assistant — answers questions about the numbers on the tabs a user is
allowed to see, using Claude.

Context-pack approach: the SAME payloads the tabs render are serialized and handed to
the model as context, scoped to the user's tab_access so the assistant can never
surface data a member's tiles wouldn't show. The API key stays server-side (config);
the browser only ever talks to our own endpoint.
"""
from __future__ import annotations

import json
import logging

from anthropic import AsyncAnthropic
from sqlalchemy import select

from ..config import settings
from ..models import Tenant, User
from ..services.becollective import build_becollective
from ..services.forum import build_forum
from ..services.lineage import metric_detail
from ..services.metrics import build_dashboard
from ..services.tabs import effective_tabs, tab_for_metric, tenant_tabs, biz_tab_map

log = logging.getLogger("app")

TAB_LEGEND = {
    "portfolio": "Portfolio — the roll-up across all businesses (combined revenue, NOI, margin, cash).",
    "ulrg": "ULRG — the real-estate team (units closed, GCI, volume, pipeline, listings, agents).",
    "forum": "The Forum — mastermind membership: members, recruiting funnel, renewals, events, and Cash & Billing (net cash, MRR, ARR, streams, failed payments).",
    "becollective": "beCollective — cohort community program: members, recruiting funnel, events.",
    "sympli": "Sympli Mortgage — the loan business: funded loans, volume, commission, and financials (Live vs Booked).",
    "flywheel": "Referral Flywheel — ULRG → Sympli referral attach-rate and captured revenue.",
    "binder": "Binder — legal-entity compliance: entities and their obligations (annual reports, BOI, taxes, insurance) with due-date status.",
}

_client: AsyncAnthropic | None = None

# The one tool: fetch the records behind a tile (what a user sees on click). Lets the
# assistant name members, list transactions, and reconcile counts the summary can't.
DRILL_TOOL = {
    "name": "get_dashboard_detail",
    "description": (
        "Fetch the underlying records behind a dashboard tile — the same drill-down a user "
        "gets by clicking a number (e.g. the member roster behind 'active_members', the "
        "renewal book behind 'renewal_book', the transactions behind 'forum_payments' or "
        "'units_closed'). Use this whenever the summary numbers can't answer the question — "
        "to name specific members, list transactions, or RECONCILE two counts (e.g. which of "
        "the 70 active members lack a membership: drill 'active_members' and 'forum_arr' or "
        "'renewal_book' and compare the rosters). Pass metric_key exactly as it appears in a "
        "'drill' field of the dashboard JSON."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "metric_key": {"type": "string", "description": "The tile/drill key, e.g. active_members, forum_arr, renewal_book, registered, monthly, unregistered, forum_payments, units_closed."},
            "business": {"type": "string", "description": "Business key when relevant: springb (Forum/beCollective), ulrg, or sympli."},
            "stream": {"type": "string", "description": "Optional filter for revenue-stream drills (memberships, event_tickets, sponsorships, invoices)."},
            "stage": {"type": "string", "description": "Optional pipeline-stage filter."},
            "source": {"type": "string", "description": "Optional lead-source filter."},
        },
        "required": ["metric_key"],
    },
}
MAX_TOOL_STEPS = 5


def enabled() -> bool:
    return bool(settings.ANTHROPIC_API_KEY)


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


def _text_of(resp) -> str:
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()


def _scope_dashboard(d, tabs):
    """Same filtering the /dashboard endpoint applies for members — keep only granted
    areas; null portfolio/scorecards/flywheel unless granted."""
    tabset = set(tabs)
    d.areas = {k: v for k, v in d.areas.items() if k in tabset}
    if "portfolio" not in tabset:
        d.portfolio = d.portfolio.model_copy(update={
            "revenue": None, "noi": None, "margin": None, "mom": None, "cash": None, "composition": []})
        d.scorecards = []
    if "flywheel" not in tabset:
        from ..schemas import Flywheel
        d.flywheel = Flywheel(available=False)
    return d


async def _build_context(s, user: User, period: str):
    """Return (context_dict, tabs) — the data this user is allowed to see."""
    all_tabs = await tenant_tabs(s, user.tenant_id)
    tabs = list(all_tabs) if user.role in ("owner", "admin") else effective_tabs(user, all_tabs)

    d = await build_dashboard(s, user.tenant_id, period)
    if user.role not in ("owner", "admin"):
        d = _scope_dashboard(d, tabs)

    data = {"period": period, "dashboard": d.model_dump(mode="json")}
    if "forum" in tabs:
        data["forum_detail"] = await build_forum(s, user.tenant_id, period)
    if "becollective" in tabs:
        data["becollective_detail"] = await build_becollective(s, user.tenant_id, period)
    if "binder" in tabs:
        from .binder import build_assistant_summary
        data["binder_detail"] = await build_assistant_summary(s, user.tenant_id)

    tenant = (await s.execute(select(Tenant).where(Tenant.id == user.tenant_id))).scalar_one_or_none()
    legend = "\n".join(f"- {TAB_LEGEND[t]}" for t in tabs if t in TAB_LEGEND)
    return {"org": (tenant.name if tenant else "the"), "tabs": tabs, "legend": legend, "data": data}, tabs


def _system_prompt(ctx: dict, period: str) -> str:
    data_json = json.dumps(ctx["data"], separators=(",", ":"), default=str)
    return (
        f"You are the analyst for {ctx['org']}'s executive command center — a live dashboard of its "
        f"businesses. Answer the user's question using ONLY the JSON data below; it is exactly what the "
        f"user is looking at.\n\n"
        f"Rules:\n"
        f"- Lead with the number/answer, then a one-line 'why' only if it helps. Be concise — an exec is reading.\n"
        f"- Use the exact figures from the data and format money like the dashboard ($1.2M, $20.2K). "
        f"Never invent, estimate, or extrapolate numbers that aren't present.\n"
        f"- If the answer isn't in the data, say so plainly and name what's missing "
        f"(e.g. 'QuickBooks isn't connected, so booked P&L isn't available — the cash figure is $X').\n"
        f"- State the period when relevant. The data covers period='{period}'.\n"
        f"- The user can only see these tabs: {', '.join(ctx['tabs'])}. Never reference anything outside them.\n"
        f"- Short markdown is fine (a bold number, a tight bullet list). No preamble like 'Based on the data'.\n\n"
        f"TOOL — get_dashboard_detail: the JSON below is the summary only (tile numbers). To answer "
        f"anything that needs the records BEHIND a tile — naming specific members, listing transactions, "
        f"or reconciling two counts (e.g. 'who is in the 70 active members but has no membership' → drill "
        f"'active_members' and 'forum_arr'/'renewal_book' and diff the rosters) — call the tool with the "
        f"metric_key from the relevant 'drill' field. Prefer the summary for overview questions; drill only "
        f"when the summary genuinely can't answer. You may call it more than once to compare rosters.\n\n"
        f"TAB LEGEND:\n{ctx['legend']}\n\n"
        f"DASHBOARD DATA (JSON):\n{data_json}\n"
    )


async def _run_tool(s, user: User, allowed_tabs: list[str], period: str, inp: dict) -> dict:
    """Execute a drill on the user's behalf — same permission gate as the tile itself,
    so the assistant can't pull records for a tab the user can't see."""
    key = (inp.get("metric_key") or "").strip()
    business = inp.get("business") or None
    if not key:
        return {"error": "metric_key is required."}
    tab = tab_for_metric(key, business, await biz_tab_map(s, user.tenant_id))
    if tab not in allowed_tabs:
        return {"error": f"No access to '{key}' — it belongs to the {tab} tab, which this user can't see."}
    try:
        detail = await metric_detail(
            s, user.tenant_id, key, period, business,
            None, None, inp.get("stage"), inp.get("source"), inp.get("stream"))
    except Exception as e:  # unknown key / builder error — tell the model, don't 500
        return {"error": f"Couldn't fetch '{key}' ({type(e).__name__})."}
    rows = detail.get("rows")
    if isinstance(rows, list) and len(rows) > 300:   # bound the token cost
        detail = {**detail, "rows": rows[:300], "rows_truncated_from": len(rows)}
    return detail


async def ask(s, user: User, question: str, history: list[dict] | None = None, period: str = "mtd") -> dict:
    ctx, tabs = await _build_context(s, user, period)
    system = _system_prompt(ctx, period)

    messages: list[dict] = []
    for turn in (history or [])[-6:]:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content[:4000]})
    messages.append({"role": "user", "content": question.strip()[:2000]})

    client = _get_client()
    tools_used: list[str] = []
    for step in range(MAX_TOOL_STEPS):
        resp = await client.messages.create(
            model=settings.ASSISTANT_MODEL,
            max_tokens=settings.ASSISTANT_MAX_TOKENS,
            system=system,
            tools=[DRILL_TOOL],
            # Extended thinking is on by default for this model and its thinking stream
            # eats the output budget (it can burn the whole cap before emitting an answer).
            # We don't need a visible reasoning trace for data Q&A — turn it off so the
            # budget goes to tool calls + the answer.
            thinking={"type": "disabled"},
            messages=messages,
        )
        tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        log.info("assistant step=%d stop=%s out=%s blocks=%s", step, resp.stop_reason,
                 getattr(resp.usage, "output_tokens", "?"),
                 [getattr(b, "type", "?") for b in resp.content])

        # A drill turn is defined by the PRESENCE of tool_use blocks, not stop_reason
        # (a cut-off turn can carry tool_use with stop_reason=max_tokens).
        if not tool_uses:
            text = _text_of(resp)
            if not text:
                log.warning("assistant empty final stop=%s out=%s — forcing text",
                            resp.stop_reason, getattr(resp.usage, "output_tokens", "?"))
                retry = await client.messages.create(
                    model=settings.ASSISTANT_MODEL, max_tokens=settings.ASSISTANT_MAX_TOKENS,
                    system=system, tools=[DRILL_TOOL], tool_choice={"type": "none"},
                    thinking={"type": "disabled"}, messages=messages)
                text = _text_of(retry)
            return {"answer": text or "I pulled the data but couldn't compose an answer — try rephrasing.",
                    "tabs_used": tabs, "tools_used": tools_used, "model": settings.ASSISTANT_MODEL}

        # replay the assistant turn (as plain dicts) + run each requested drill
        assistant_content, results = [], []
        for b in resp.content:
            if b.type == "text":
                assistant_content.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                assistant_content.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                tools_used.append(b.input.get("metric_key", "?"))
                out = await _run_tool(s, user, tabs, period, b.input)
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": json.dumps(out, separators=(",", ":"), default=str)[:24000]})
        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": results})

    # Ran out of tool steps — force a text answer (tool_choice=none) with what it has.
    log.warning("assistant hit MAX_TOOL_STEPS — forcing a final answer")
    final = await client.messages.create(
        model=settings.ASSISTANT_MODEL, max_tokens=settings.ASSISTANT_MAX_TOKENS,
        system=system, tools=[DRILL_TOOL], tool_choice={"type": "none"},
        thinking={"type": "disabled"}, messages=messages)
    return {"answer": _text_of(final) or "I couldn't finish that lookup — try narrowing the question.",
            "tabs_used": tabs, "tools_used": tools_used, "model": settings.ASSISTANT_MODEL}
