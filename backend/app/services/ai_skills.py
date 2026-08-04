"""AI Employees — the product skill catalog (SPEC-ai-employees-tab §1, §8).

PRODUCT data: the six Social-Media-Manager skills, each a prompt template + an
output_contract (JSON schema the run must satisfy). Prompts are TENANT-NEUTRAL — every
brand specific (voice, offer, launch, roster) arrives via the run's context pack, never
hardcoded here. `seed_ai_skills(s)` upserts by key and is called from seed(); it bumps
`version` only when the seed definition changes, so a tenant's prompt_override can be
flagged stale after a seed upgrade (the seed row itself is never mutated by an override).

The `payload` shapes below are exactly the mockup's `OUTPUTS[n].preview` objects
(SPEC Appendix A) so the mockup renderers and the product stay one contract.
"""
from __future__ import annotations

from sqlalchemy import select

from ..models import AISkill

# kind → the lane chip + destination label the run surface shows (product constants,
# not tenant config). The artifact carries these so the frontend needs no lookup table.
KIND_META: dict[str, dict] = {
    "audit":    {"lane": "Intel",    "dest_label": "Claude in Chrome"},
    "trend":    {"lane": "Intel",    "dest_label": "Weekly trend brief"},
    "strategy": {"lane": "Strategy", "dest_label": "Strategy memo"},
    "design":   {"lane": "Creative", "dest_label": "Claude Design"},
    "script":   {"lane": "Creative", "dest_label": "Reel script"},
    "measure":  {"lane": "Tracking", "dest_label": "GoHighLevel"},
}

# Every run returns this envelope: diagnosis `reads`, a one-line `summary`, and one or
# more `artifacts` (each a {title, payload}). Per-skill contracts below set the payload
# schema for their artifact kind(s).
_STR_ARR = {"type": "array", "items": {"type": "string"}}


def _run_contract(payload_schema: dict) -> dict:
    return {
        "type": "object",
        "required": ["reads", "summary", "artifacts"],
        "properties": {
            "reads": _STR_ARR,
            "summary": {"type": "string"},
            "artifacts": {
                "type": "array", "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["title", "payload"],
                    "properties": {
                        "title": {"type": "string"},
                        "payload": payload_schema,
                    },
                },
            },
        },
    }


# ── payload schemas (mockup preview shapes) ──────────────────────────────────
_AUDIT = {"type": "object", "required": ["kind", "handle", "top", "mechanics"], "properties": {
    "kind": {"const": "audit"}, "handle": {"type": "string"},
    "top": {"type": "array", "items": {"type": "object", "required": ["name", "val", "w"],
            "properties": {"name": {"type": "string"}, "val": {"type": "string"}, "w": {"type": "string"}}}},
    "mechanics": _STR_ARR, "note": {"type": "string"}}}

_TREND = {"type": "object", "required": ["kind", "patterns", "change"], "properties": {
    "kind": {"const": "trend"}, "scanned": {"type": "string"},
    "patterns": {"type": "array", "items": {"type": "object", "required": ["p", "d"],
                 "properties": {"p": {"type": "string"}, "d": {"type": "string"}}}},
    "timely": _STR_ARR, "change": {"type": "string"}, "note": {"type": "string"}}}

_STRATEGY = {"type": "object", "required": ["kind", "shift", "plan"], "properties": {
    "kind": {"const": "strategy"}, "title": {"type": "string"}, "shift": {"type": "string"},
    "plan": {"type": "array", "items": {"type": "object", "required": ["day", "what"],
             "properties": {"day": {"type": "string"}, "what": {"type": "string"}, "cta": {"type": "string"}}}},
    "why": {"type": "string"}}}

_DESIGN = {"type": "object", "required": ["kind", "slides"], "properties": {
    "kind": {"const": "design"},
    "slides": {"type": "array", "minItems": 1, "items": {"type": "object", "required": ["bg", "fg", "h"],
               "properties": {"bg": {"type": "string"}, "fg": {"type": "string"},
                              "h": {"type": "string"}, "sub": {"type": "string"}}}},
    "caption": {"type": "string"}, "note": {"type": "string"}}}

_SCRIPT = {"type": "object", "required": ["kind", "hook", "shots"], "properties": {
    "kind": {"const": "script"}, "hookType": {"type": "string"}, "hook": {"type": "string"},
    "shots": {"type": "array", "items": {"type": "object", "required": ["vis", "vo"],
              "properties": {"vis": {"type": "string"}, "vo": {"type": "string"}}}}}}

_MEASURE = {"type": "object", "required": ["kind", "tags"], "properties": {
    "kind": {"const": "measure"},
    "tags": {"type": "array", "items": {"type": "object", "required": ["asset", "utm"],
             "properties": {"asset": {"type": "string"}, "utm": {"type": "string"}}}},
    "note": {"type": "string"}}}

# ── skill definitions ────────────────────────────────────────────────────────
# {placeholders} are filled from the context pack by the executor (Section 4).
_COMMON = ("Return STRICT JSON only — no prose, no markdown fences — in EXACTLY this shape:\n"
           "{\"reads\": [\"2-4 short diagnosis lines\"], \"summary\": \"one line for the run list\", "
           "\"artifacts\": [{\"title\": \"a short headline for this artifact\", \"payload\": {…the shape below…}}]}\n"
           "Every artifact object needs BOTH a \"title\" and a \"payload\". Never invent metrics; work only "
           "from the material provided. Draft in {brand_voice}; nothing is published — a human approves "
           "every item.\n\nContext:\n{context}")

SKILLS: list[dict] = [
    {
        "key": "audit", "name": "Account Audit",
        "description": "Teardown of a competitor/peer account's recent winners and the mechanics behind them, from provided audit material.",
        "default_prompt": ("You are a social media strategist auditing {handle} for {org}. From the "
                           "provided posts/screenshots, identify the top-performing recent posts and the "
                           "reusable mechanics (hook style, format, cadence). Nothing is copied — the "
                           "mechanics inform {org}'s own posts.\n" + _COMMON +
                           "\n\nartifacts[0].payload = {\"kind\":\"audit\",\"handle\":str,\"top\":[{\"name\":str,"
                           "\"val\":\"4.2x\",\"w\":\"100%\"}],\"mechanics\":[str],\"note\":str}"),
        "default_schedule": None, "artifact_kinds": ["audit"], "output_contract": _run_contract(_AUDIT),
    },
    {
        "key": "trend_brief", "name": "Weekly Trend Brief",
        "description": "Scans the watched roster + provided material for patterns winning across the niche, ending with the one change to make.",
        "default_prompt": ("You are a social media analyst filing {org}'s weekly trend brief. From the "
                           "roster and provided material, extract the patterns winning across the niche and "
                           "what is timely this week. End with the single change to the plan — intel that "
                           "doesn't change the plan is trivia.\n" + _COMMON +
                           "\n\nartifacts[0].payload = {\"kind\":\"trend\",\"scanned\":\"12 accounts\","
                           "\"patterns\":[{\"p\":str,\"d\":\"3.1x\"}],\"timely\":[str],\"change\":str,\"note\":str}"),
        "default_schedule": "0 7 * * 1", "artifact_kinds": ["trend"], "output_contract": _run_contract(_TREND),
    },
    {
        "key": "strategy", "name": "Strategy Pivot",
        "description": "Re-architects the next few days of content around what's working and the pacing gap.",
        "default_prompt": ("You are {org}'s content strategist. Given the pacing facts and the audit/trend "
                           "findings, re-architect the remaining days of the {launch} into a daily plan that "
                           "closes the gap. State the pivot plainly and why.\n" + _COMMON +
                           "\n\nartifacts[0].payload = {\"kind\":\"strategy\",\"title\":str,\"shift\":str,"
                           "\"plan\":[{\"day\":\"Day 4\",\"what\":str,\"cta\":str}],\"why\":str}"),
        "default_schedule": None, "artifact_kinds": ["strategy"], "output_contract": _run_contract(_STRATEGY),
    },
    {
        "key": "design_carousel", "name": "Carousel Design",
        "description": "Drafts a multi-slide carousel in the brand — copy + slide backgrounds/foregrounds — from the strategy.",
        "default_prompt": ("You are {org}'s designer-copywriter. Draft a 5-slide carousel in {brand_voice} using "
                           "the brand colors {brand_colors}. Each slide has a background/foreground hex and a "
                           "headline; the hook is adapted (not copied) from the audit. Nothing posts.\n" + _COMMON +
                           "\n\nartifacts[0].payload = {\"kind\":\"design\",\"slides\":[{\"bg\":\"#002E2C\","
                           "\"fg\":\"#F6F0E9\",\"h\":str,\"sub\":str}],\"caption\":str,\"note\":str}"),
        "default_schedule": None, "artifact_kinds": ["design"], "output_contract": _run_contract(_DESIGN),
    },
    {
        "key": "reel_script", "name": "Reel Script",
        "description": "Writes a reflection-hook Reel script with a shotlist (visual + voiceover per shot).",
        "default_prompt": ("You are {org}'s short-form scriptwriter. Write a Reel with a reflection hook and a "
                           "4-shot shotlist (visual + voiceover per shot), in {brand_voice}.\n" + _COMMON +
                           "\n\nartifacts[0].payload = {\"kind\":\"script\",\"hookType\":str,\"hook\":str,"
                           "\"shots\":[{\"vis\":str,\"vo\":str}]}"),
        "default_schedule": None, "artifact_kinds": ["script"], "output_contract": _run_contract(_SCRIPT),
    },
    {
        "key": "measure", "name": "Attribution Tagging",
        "description": "UTM-tags each drafted asset so registrations trace back to the post that drove them.",
        "default_prompt": ("You are {org}'s attribution assistant. For each drafted asset, produce a UTM tag "
                           "using the tenant's UTM pattern {utm_pattern} so every registration traces to its "
                           "post. Drafts only — no writeback until approved.\n" + _COMMON +
                           "\n\nartifacts[0].payload = {\"kind\":\"measure\",\"tags\":[{\"asset\":str,"
                           "\"utm\":\"theshift / ig-carousel-reflection-d4\"}],\"note\":str}"),
        "default_schedule": None, "artifact_kinds": ["measure"], "output_contract": _run_contract(_MEASURE),
    },
]

SKILL_KEYS = [sk["key"] for sk in SKILLS]


async def seed_ai_skills(s) -> int:
    """Upsert the six product skills by key (idempotent). Adds to the session; the caller
    (seed()) commits. Bumps version + refreshes fields when the seed definition changed."""
    existing = {sk.key: sk for sk in (await s.execute(select(AISkill))).scalars().all()}
    n = 0
    for d in SKILLS:
        row = existing.get(d["key"])
        fields = dict(name=d["name"], description=d["description"], default_prompt=d["default_prompt"],
                      default_schedule=d["default_schedule"], artifact_kinds=d["artifact_kinds"],
                      output_contract=d["output_contract"])
        if row is None:
            s.add(AISkill(key=d["key"], version=1, **fields))
            n += 1
        elif (row.default_prompt != d["default_prompt"] or row.output_contract != d["output_contract"]
              or row.default_schedule != d["default_schedule"] or row.artifact_kinds != d["artifact_kinds"]):
            for k, v in fields.items():
                setattr(row, k, v)
            row.version = (row.version or 1) + 1
            n += 1
    return n
