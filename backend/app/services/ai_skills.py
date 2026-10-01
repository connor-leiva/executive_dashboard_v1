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
    "audit":    {"lane": "Intel",    "dest_label": "Roster audit"},
    "trend":    {"lane": "Intel",    "dest_label": "Weekly trend brief"},
    "strategy": {"lane": "Strategy", "dest_label": "Strategy memo"},
    "design":   {"lane": "Creative", "dest_label": "Claude Design"},
    "script":   {"lane": "Creative", "dest_label": "Reel script"},
    "measure":  {"lane": "Tracking", "dest_label": "GoHighLevel"},
    # AP Clerk. "Books" is the destination for everything it produces, because every one of
    # these lands as something a person opens in Books and decides on — never as an action.
    "ap_bill":       {"lane": "Payables", "dest_label": "Books · Payables inbox"},
    "ap_exceptions": {"lane": "Payables", "dest_label": "Books · Payables"},
    "ap_run":        {"lane": "Payables", "dest_label": "Books · Payables runs"},
    "ap_aging":      {"lane": "Payables", "dest_label": "Books · Payables"},
    "ap_1099":       {"lane": "Payables", "dest_label": "Books · Vendors"},
}

# Kinds whose "ship" does something in the WORLD rather than flipping a state.
#
# For every other kind, shipping IS the state change -- the artifact is a brief or a script, and
# "shipped" records that somebody sent it. `ap_bill` is the first one where shipping runs code:
# it calls payables_intake.accept_proposal and a bill appears in the Payables inbox with a
# person's name against it.
#
# That difference is why these are excluded from any BATCH action. "Approve all" on a run
# carrying fifteen drafts must never be fifteen bills, and a loop that stamps `shipped` without
# calling the code behind it would mark the proposal done while creating nothing at all.
#
# Read by the batch approve route; the single-artifact ship route does the real work per kind.
SIDE_EFFECT_KINDS = frozenset({"ap_bill"})

# Every run returns this envelope: diagnosis `reads`, a one-line `summary`, and one or
# more `artifacts` (each a {title, payload}). Per-skill contracts below set the payload
# schema for their artifact kind(s).
# Which kind of employee a skill belongs to. The catalog is GLOBAL — one list, read by every
# employee — so without this a finance skill lands on the social-media manager and Summer starts
# filing nightly accounts-payable exception scans. Absent means "social", so the six social skills
# need no annotation and nothing that already exists changes meaning.
SOCIAL, AP = "social", "ap"


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
                              "h": {"type": "string"}, "sub": {"type": "string"},
                              "media_id": {"type": "string"}}}},   # optional: a real photo behind the slide
    "caption": {"type": "string"}, "note": {"type": "string"}}}

_SCRIPT = {"type": "object", "required": ["kind", "hook", "shots"], "properties": {
    "kind": {"const": "script"}, "hookType": {"type": "string"}, "hook": {"type": "string"},
    "shots": {"type": "array", "items": {"type": "object", "required": ["vis", "vo"],
              "properties": {"vis": {"type": "string"}, "vo": {"type": "string"},
                             "media_id": {"type": "string"}}}}}}   # optional: real b-roll for the shot

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

# ── AP Clerk payloads ────────────────────────────────────────────────────────
# Every one of these describes a PROPOSAL. Nothing here is an instruction to the system: a
# person reads it in Books and decides. The shapes are deliberately close to what the payables
# screens already render, so a proposal and a real row look alike to whoever is reading them.

_MONEY = {"type": "number"}
_AP_BILL = {"type": "object", "required": ["kind", "vendor", "amount", "confidence"], "properties": {
    "kind": {"const": "ap_bill"},
    "vendor": {"type": "string", "description": "the payee exactly as printed on the document"},
    "vendor_match": {"type": ["string", "null"], "description": "id of an existing vendor, or null"},
    "vendor_match_reason": {"type": "string"},
    "invoice_number": {"type": ["string", "null"]},
    "invoice_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
    "due_date": {"type": ["string", "null"], "description": "YYYY-MM-DD, only if PRINTED"},
    "amount": _MONEY,
    "currency": {"type": "string"},
    "description": {"type": "string"},
    "suggested_account": {"type": ["string", "null"], "description": "a standard account NAME from context"},
    "confidence": {"type": "object", "required": ["vendor", "amount", "invoice_number"],
                   "properties": {"vendor": {"type": "number"}, "amount": {"type": "number"},
                                  "invoice_number": {"type": "number"}}},
    "unreadable": {"type": "array", "items": {"type": "string"},
                   "description": "fields the document did not state"},
    "note": {"type": "string"}}}

_AP_EXCEPTIONS = {"type": "object", "required": ["kind", "items"], "properties": {
    "kind": {"const": "ap_exceptions"},
    "items": {"type": "array", "items": {"type": "object",
              "required": ["invoice", "vendor", "issue", "why"], "properties": {
                  "invoice": {"type": "string"}, "vendor": {"type": "string"},
                  "issue": {"type": "string",
                            "description": "duplicate | off_band | no_w9 | bank_cooldown | no_entity"},
                  "why": {"type": "string"}, "amount": _MONEY}}},
    "note": {"type": "string"}}}

_AP_RUN = {"type": "object", "required": ["kind", "lines", "total"], "properties": {
    "kind": {"const": "ap_run"}, "run_date": {"type": ["string", "null"]},
    "entity": {"type": "string"},
    "lines": {"type": "array", "items": {"type": "object",
              "required": ["vendor", "invoice", "amount"], "properties": {
                  "vendor": {"type": "string"}, "invoice": {"type": "string"},
                  "amount": _MONEY, "due": {"type": ["string", "null"]},
                  "held": {"type": "boolean"}, "hold_reason": {"type": "string"}}}},
    "total": _MONEY, "held_total": _MONEY, "note": {"type": "string"}}}

_AP_AGING = {"type": "object", "required": ["kind", "buckets"], "properties": {
    "kind": {"const": "ap_aging"},
    "buckets": {"type": "array", "items": {"type": "object",
                "required": ["label", "count", "amount"], "properties": {
                    "label": {"type": "string"}, "count": {"type": "integer"},
                    "amount": _MONEY}}},
    "stuck": {"type": "array", "items": {"type": "object",
              "required": ["invoice", "vendor", "waiting_days"], "properties": {
                  "invoice": {"type": "string"}, "vendor": {"type": "string"},
                  "waiting_days": {"type": "integer"}, "with_whom": {"type": "string"}}}},
    "note": {"type": "string"}}}

_AP_1099 = {"type": "object", "required": ["kind", "vendors"], "properties": {
    "kind": {"const": "ap_1099"}, "threshold": _MONEY,
    "vendors": {"type": "array", "items": {"type": "object",
                "required": ["vendor", "paid_ytd", "issue"], "properties": {
                    "vendor": {"type": "string"}, "paid_ytd": _MONEY,
                    "issue": {"type": "string", "description": "missing_w9 | not_flagged | duplicate_record"},
                    "why": {"type": "string"}}}},
    "note": {"type": "string"}}}


_AP_COMMON = (
    "\nReply with ONE JSON object and nothing else:\n"
    "{\"reads\": [\"2-4 short lines saying what you looked at\"], \"summary\": \"one line for the run list\", "
    "\"artifacts\": [{\"title\": \"a short headline\", \"payload\": {…the shape below…}}]}\n"
    "Every artifact needs BOTH a title and a payload, and there is ALWAYS exactly one artifact — "
    "when there is nothing to report, return it with an empty list and say so in the note. An "
    "empty answer is a finding, not a failure.\n"
    "\nYOU ARE PROPOSING, NOT DECIDING. Nothing you return pays anybody, approves anything or "
    "changes a record. A person reads this in Books and decides. So: never state a figure the "
    "material does not contain, never guess at a number to fill a field, and where the document "
    "or the data does not say, leave the field null and name it in `unreadable` or the note. A "
    "confident wrong amount is worse here than an admitted gap, because the gap gets checked and "
    "the confident number gets paid.\n"
    "\nContext:\n{context}")


SKILLS: list[dict] = [
    {
        "key": "audit", "name": "Account Audit",
        "description": "Teardown of a competitor/peer account's recent winners and the mechanics behind them, from provided audit material.",
        "default_prompt": ("You are a social media strategist producing a competitor teardown for {org}. "
                           "If context.recent_audits is present, those are REAL per-account teardowns already "
                           "gathered from the roster via Claude in Chrome — synthesize the top cross-account "
                           "performers and the reusable mechanics (hook style, format, cadence) from that real "
                           "data, naming the account each came from. Otherwise use any provided material. "
                           "Nothing is copied — the mechanics inform {org}'s own posts. If there is no audit "
                           "data at all, say so plainly in the note rather than inventing posts.\n" + _COMMON +
                           "\n\nartifacts[0].payload = {\"kind\":\"audit\",\"handle\":str,\"top\":[{\"name\":str,"
                           "\"val\":\"4.2x\",\"w\":\"100%\"}],\"mechanics\":[str],\"note\":str}"),
        "default_schedule": None, "artifact_kinds": ["audit"], "output_contract": _run_contract(_AUDIT),
    },
    {
        "key": "trend_brief", "name": "Weekly Trend Brief",
        "description": "Scans the watched roster + provided material for patterns winning across the niche, ending with the one change to make.",
        "default_prompt": ("You are a social media analyst filing {org}'s weekly trend brief. Use "
                           "context.recent_audits (real per-account teardowns from the roster) as your primary "
                           "evidence, plus the roster and any provided material, to extract the patterns "
                           "winning across the niche and what is timely this week. End with the single change "
                           "to the plan — intel that doesn't change the plan is trivia.\n" + _COMMON +
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
                           "headline; the hook is adapted (not copied) from the audit. Nothing posts.\n"
                           "If context.media is provided, it is the brand's real photo/b-roll library. For "
                           "slides where a real photo strengthens the message, set that slide's media_id to the "
                           "best-matching asset id from context.media (match on its description) — keep the fg "
                           "hex for legible text over the photo. Leave media_id out for slides that read better "
                           "as a solid brand color (openers and CTAs usually do). ONLY use ids that appear in "
                           "context.media; never invent one.\n" + _COMMON +
                           "\n\nartifacts[0].payload = {\"kind\":\"design\",\"slides\":[{\"bg\":\"#002E2C\","
                           "\"fg\":\"#F6F0E9\",\"h\":str,\"sub\":str,\"media_id\":\"<id from context.media, optional>\"}],"
                           "\"caption\":str,\"note\":str}"),
        "default_schedule": None, "artifact_kinds": ["design"], "output_contract": _run_contract(_DESIGN),
    },
    {
        "key": "reel_script", "name": "Reel Script",
        "description": "Writes a reflection-hook Reel script with a shotlist (visual + voiceover per shot).",
        "default_prompt": ("You are {org}'s short-form scriptwriter. Write a Reel with a reflection hook and a "
                           "4-shot shotlist (visual + voiceover per shot), in {brand_voice}.\n"
                           "If context.media has b-roll or a photo that fits a shot, set that shot's media_id to "
                           "its asset id (match on its description); leave it out otherwise. ONLY use ids from "
                           "context.media.\n" + _COMMON +
                           "\n\nartifacts[0].payload = {\"kind\":\"script\",\"hookType\":str,\"hook\":str,"
                           "\"shots\":[{\"vis\":str,\"vo\":str,\"media_id\":\"<id from context.media, optional>\"}]}"),
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
    # ── AP Clerk (family "ap") ────────────────────────────────────────────────
    {
        "key": "ap_intake", "name": "Bill Intake", "family": AP,
        "description": "Reads an arriving bill, extracts what it says, and proposes a draft payable and a vendor match. Never creates a vendor and never approves.",
        "default_prompt": ("You are {org}'s accounts-payable clerk, reading ONE arriving document.\n"
                           "Decide first whether it is actually a bill. A statement, a receipt for something "
                           "already paid, a quote or a contract is NOT a bill — say so in the note and return "
                           "an empty artifact rather than inventing an invoice from it.\n"
                           "Extract only what is PRINTED: payee, invoice number, invoice date, amount, and a "
                           "due date ONLY if the document states one. Do not compute a due date from terms — "
                           "the system derives that from the vendor, and a date you calculate would silently "
                           "override the vendor's terms.\n"
                           "Match the payee against context.vendors and put that vendor's id in vendor_match, "
                           "with your reason. If no vendor is a confident match, set vendor_match to null and "
                           "say which of them was closest — a vendor is created by a person who has seen a "
                           "W-9, never by you.\n"
                           "Suggest an account from context.accounts by NAME if one clearly fits; otherwise "
                           "null. Give an honest per-field confidence: a smudged total is a low number, not a "
                           "guess." + _AP_COMMON +
                           "\n\nartifacts[0].payload = {\"kind\":\"ap_bill\",\"vendor\":str,"
                           "\"vendor_match\":id|null,\"vendor_match_reason\":str,\"invoice_number\":str|null,"
                           "\"invoice_date\":\"YYYY-MM-DD\"|null,\"due_date\":\"YYYY-MM-DD\"|null,"
                           "\"amount\":num,\"currency\":\"USD\",\"description\":str,"
                           "\"suggested_account\":str|null,\"confidence\":{\"vendor\":0-1,\"amount\":0-1,"
                           "\"invoice_number\":0-1},\"unreadable\":[str],\"note\":str}"),
        "default_schedule": None, "artifact_kinds": ["ap_bill"], "output_contract": _run_contract(_AP_BILL),
    },
    {
        "key": "ap_exception_scan", "name": "Exception Scan", "family": AP,
        "description": "Nightly pass over open bills for the things that should stop a payment: duplicates, unusual amounts, missing W-9s, fresh banking.",
        "default_prompt": ("You are {org}'s accounts-payable clerk doing the nightly exception pass.\n"
                           "Work ONLY from context.open_bills, context.vendors and context.recent_payments. "
                           "Report, for each: a likely DUPLICATE (same vendor and amount close together, or "
                           "the same invoice number seen before), an amount well outside what this vendor is "
                           "usually paid, a vendor with no W-9, banking added too recently to pay against, and "
                           "a bill with no entity set.\n"
                           "Say why in a sentence a bookkeeper can act on — 'same amount as INV-4471 eight "
                           "days ago' rather than 'possible duplicate'. Do not repeat a hold the system has "
                           "already flagged on the row unless you can add something to it." + _AP_COMMON +
                           "\n\nartifacts[0].payload = {\"kind\":\"ap_exceptions\",\"items\":["
                           "{\"invoice\":str,\"vendor\":str,\"issue\":\"duplicate|off_band|no_w9|"
                           "bank_cooldown|no_entity\",\"why\":str,\"amount\":num}],\"note\":str}"),
        "default_schedule": "0 2 * * *", "artifact_kinds": ["ap_exceptions"],
        "output_contract": _run_contract(_AP_EXCEPTIONS),
    },
    {
        "key": "ap_run_prep", "name": "Run Prep", "family": AP,
        "description": "Monday summary of what is approved and due, what is held and why, so the run is read before it is created.",
        "default_prompt": ("You are {org}'s accounts-payable clerk preparing Monday's payment run.\n"
                           "From context.proposed_run, lay out what is approved and due inside the window, "
                           "what is held and why, and the totals for each. Name anything that will need a "
                           "decision before the run can be released — a held line, a vendor whose banking "
                           "changed, a duplicate — so the decisions are made before somebody is sitting in "
                           "front of the release button.\n"
                           "You are describing a proposal the system computed. Do not add lines to it, do not "
                           "re-total it from memory, and do not suggest releasing anything." + _AP_COMMON +
                           "\n\nartifacts[0].payload = {\"kind\":\"ap_run\",\"run_date\":\"YYYY-MM-DD\"|null,"
                           "\"entity\":str,\"lines\":[{\"vendor\":str,\"invoice\":str,\"amount\":num,"
                           "\"due\":\"YYYY-MM-DD\"|null,\"held\":bool,\"hold_reason\":str}],"
                           "\"total\":num,\"held_total\":num,\"note\":str}"),
        "default_schedule": "0 7 * * 1", "artifact_kinds": ["ap_run"],
        "output_contract": _run_contract(_AP_RUN),
    },
    {
        "key": "ap_aging_digest", "name": "Aging Digest", "family": AP,
        "description": "Weekly view of what is overdue and what has been sitting in approval, and with whom.",
        "default_prompt": ("You are {org}'s accounts-payable clerk filing the weekly aging digest.\n"
                           "From context.open_bills, bucket what is outstanding by how overdue it is, and "
                           "list separately anything that has been waiting on an approval for long enough to "
                           "be stuck — with whom, and how long. Aging that names no one is a number; aging "
                           "that names the person waiting on is a thing that gets unstuck." + _AP_COMMON +
                           "\n\nartifacts[0].payload = {\"kind\":\"ap_aging\",\"buckets\":["
                           "{\"label\":\"Not yet due|1-30 days|31-60 days|60+ days\",\"count\":int,"
                           "\"amount\":num}],\"stuck\":[{\"invoice\":str,\"vendor\":str,"
                           "\"waiting_days\":int,\"with_whom\":str}],\"note\":str}"),
        "default_schedule": "0 7 * * 5", "artifact_kinds": ["ap_aging"],
        "output_contract": _run_contract(_AP_AGING),
    },
    {
        "key": "ap_1099_check", "name": "1099 Check", "family": AP,
        "description": "Quarterly sweep for vendors paid over the threshold without a W-9, not flagged for a 1099, or recorded twice.",
        "default_prompt": ("You are {org}'s accounts-payable clerk running the quarterly 1099 check.\n"
                           "From context.vendors and context.paid_ytd, find vendors paid over "
                           "context.threshold this year that have no W-9 on file, are not flagged as "
                           "1099-eligible, or appear to be the same payee recorded twice under slightly "
                           "different names. For a duplicate, say which two records and what makes you think "
                           "they are one payee.\n"
                           "This is the list somebody works through before January, so an honest short list "
                           "beats a long speculative one." + _AP_COMMON +
                           "\n\nartifacts[0].payload = {\"kind\":\"ap_1099\",\"threshold\":num,"
                           "\"vendors\":[{\"vendor\":str,\"paid_ytd\":num,\"issue\":\"missing_w9|"
                           "not_flagged|duplicate_record\",\"why\":str}],\"note\":str}"),
        "default_schedule": "0 8 1 1,4,7,10 *", "artifact_kinds": ["ap_1099"],
        "output_contract": _run_contract(_AP_1099),
    },
]

def family_of(skill: dict) -> str:
    return skill.get("family", SOCIAL)


def skills_for(family: str) -> list[dict]:
    """The catalog as ONE employee sees it. Every place that answers "which skills?" goes
    through here, so a new family cannot be half-wired: seeding, the settings list and the
    dispatcher all ask the same question and get the same answer."""
    return [sk for sk in SKILLS if family_of(sk) == (family or SOCIAL)]


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
