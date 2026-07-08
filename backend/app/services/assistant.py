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
from ..services.metrics import build_dashboard
from ..services.tabs import effective_tabs, tenant_tabs

log = logging.getLogger("app")

TAB_LEGEND = {
    "portfolio": "Portfolio — the roll-up across all businesses (combined revenue, NOI, margin, cash).",
    "ulrg": "ULRG — the real-estate team (units closed, GCI, volume, pipeline, listings, agents).",
    "forum": "The Forum — mastermind membership: members, recruiting funnel, renewals, events, and Cash & Billing (net cash, MRR, ARR, streams, failed payments).",
    "becollective": "beCollective — cohort community program: members, recruiting funnel, events.",
    "sympli": "Sympli Mortgage — the loan business: funded loans, volume, commission, and financials (Live vs Booked).",
    "flywheel": "Referral Flywheel — ULRG → Sympli referral attach-rate and captured revenue.",
}

_client: AsyncAnthropic | None = None


def enabled() -> bool:
    return bool(settings.ANTHROPIC_API_KEY)


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


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
        f"TAB LEGEND:\n{ctx['legend']}\n\n"
        f"DASHBOARD DATA (JSON):\n{data_json}\n"
    )


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

    resp = await _get_client().messages.create(
        model=settings.ASSISTANT_MODEL,
        max_tokens=settings.ASSISTANT_MAX_TOKENS,
        system=system,
        messages=messages,
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    return {"answer": text, "tabs_used": tabs, "model": settings.ASSISTANT_MODEL}
