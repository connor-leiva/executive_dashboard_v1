"""Acumyn Binder — extraction pipeline (SPEC-binder-module Part 3).

Runs (in the worker, once proven via validate_binder) over BinderDocument rows with no
`extracted` result yet. One Claude call per document classifies + pulls anchors; we then
fuzzy-match the entity, derive obligation proposals (read/rule), and flag gaps and renewals.

HARD INVARIANT (Part 10 #1, grep-checkable): this module writes ONLY ProposedObligation and
BinderDocument.extracted. It NEVER constructs an Obligation — a human confirms each proposal
first (the confirm loop, Step 6). The only model instantiated here is ProposedObligation(.

Follows assistant.py conventions: AsyncAnthropic, server-side key, thinking disabled, model
from BINDER_EXTRACT_MODEL defaulting to ASSISTANT_MODEL. Parse is defensive — a malformed or
unreadable document yields a low-confidence proposal or none and files as `other`; it never
raises and never produces an Obligation.
"""
from __future__ import annotations

import base64
import datetime as dt
import difflib
import json
import logging
import os
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import BinderDocument, LegalEntity, Obligation, ProposedObligation
from . import binder_storage
from .binder_ingest import CATEGORIES
from .binder_rules import lookup_rule, derive, RETURN_TYPE_CLASS

log = logging.getLogger("app")


# ── Entity matching (SPEC 3.2 — the genuinely hard part) ──────────────────────
_SUFFIX = re.compile(
    r"\b(l\.?l\.?c\.?|inc\.?|incorporated|corp\.?|corporation|co\.?|company|"
    r"ltd\.?|l\.?p\.?|l\.?l\.?p\.?|p\.?l\.?l\.?c\.?|p\.?c\.?|trust)\b", re.I)


def normalize_name(s: str | None) -> str:
    """Lowercase, drop legal suffixes + punctuation, collapse whitespace. So 'Spring B - The
    Forum, LLC' and 'spring b the forum' compare on their distinctive core."""
    s = (s or "").lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[.,\-/]", " ", s)
    s = _SUFFIX.sub(" ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _token_overlap(guess_norm: str, ent_norm: str) -> float:
    """Fraction of the guess's word-tokens that appear as tokens in the entity name. This is
    what tells a genuine partial ('meraki title' -> 'meraki title partners', overlap 1.0) apart
    from an incidental substring collision ('spring b' -> 'realspringb', overlap 0.0) — both of
    which score ~0.74 on raw character similarity."""
    gt = set(guess_norm.split())
    return (len(gt & set(ent_norm.split())) / len(gt)) if gt else 0.0


# When the top two candidates are within this, or the best match is weak, or the match is a
# character-incidental substring (weak token overlap), the proposal is ambiguous and confirm
# must require an explicit entity pick (never a silent commit).
_AMBIGUOUS_DELTA = 0.08
_AMBIGUOUS_FLOOR = 0.55
_WEAK_MATCH = 0.50
_STRONG_MATCH = 0.95       # trusted even with low token overlap (e.g. a near-exact string)
_MIN_TOKEN_OVERLAP = 0.5   # below this (and not near-exact) the match is treated as incidental
_AUTO_LINK = 0.80          # confident enough to attach the document to the entity


def match_entity(guess: str | None, entities: list) -> dict:
    """Fuzzy-match `guess` against LegalEntity.legal_name + nickname. Returns
    {entity_id, confidence, candidates, ambiguous}. Keeps the top guess even when ambiguous
    (Part 5 requires the human to pick), but flags it so nothing commits silently."""
    ng = normalize_name(guess)
    if not ng or not entities:
        return {"entity_id": None, "confidence": 0.0, "candidates": [], "ambiguous": bool(guess)}
    scored = []
    for e in entities:
        s = _ratio(ng, normalize_name(e.legal_name))
        if e.nickname:
            s = max(s, _ratio(ng, normalize_name(e.nickname)))
        scored.append((s, e))
    scored.sort(key=lambda x: x[0], reverse=True)
    top_score, top = scored[0]
    candidates = [{"entity_id": str(e.id), "name": e.legal_name, "score": round(sc, 3)}
                  for sc, e in scored[:5]]
    top_overlap = _token_overlap(ng, normalize_name(top.legal_name))
    if top.nickname:
        top_overlap = max(top_overlap, _token_overlap(ng, normalize_name(top.nickname)))
    close = len(scored) > 1 and (scored[0][0] - scored[1][0]) < _AMBIGUOUS_DELTA and scored[1][0] >= _AMBIGUOUS_FLOOR
    weak = top_score < _WEAK_MATCH
    incidental = top_overlap < _MIN_TOKEN_OVERLAP and top_score < _STRONG_MATCH
    return {"entity_id": str(top.id), "confidence": round(top_score, 3),
            "candidates": candidates, "ambiguous": close or weak or incidental}


# ── The Claude call (single network seam; tests monkeypatch _claude_call) ──────
_client_obj = None
_MEDIA = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
          ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}

_SYSTEM = (
    "You extract compliance metadata from a single legal/business document for an entity "
    "binder. Return STRICT JSON only, no prose, matching exactly this shape: "
    '{"category": one of ["formation","insurance","tax","lease","registered_agent","estate","other"], '
    '"entity_name_guess": string|null, "entity_type_signal": one of '
    '["llc","s_corp","c_corp","partnership","trust"]|null, '
    '"anchors": {"formation_date": ISO|null, "expiration_date": ISO|null, "renewal_date": ISO|null, '
    '"return_type": string|null, "expected_returns": [string]}, "printed_dates": [ISO]}. '
    "Dates must be ISO YYYY-MM-DD or null. Infer entity_type_signal from a tax return form "
    "number if present (1120-S -> s_corp, 1065 -> partnership, 1120 -> c_corp, 1040 -> individual). "
    "If the document is unreadable, return category 'other' with null fields. Never invent dates."
)
_USER = "Extract the binder metadata for this document as strict JSON."


def _enabled() -> bool:
    return bool(settings.ANTHROPIC_API_KEY)


def _model() -> str:
    return settings.BINDER_EXTRACT_MODEL or settings.ASSISTANT_MODEL


def _client():
    global _client_obj
    if _client_obj is None:
        from anthropic import AsyncAnthropic
        _client_obj = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client_obj


def _text_of(resp) -> str:
    parts = []
    for block in getattr(resp, "content", None) or []:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts)


def _content_block(filename: str, data: bytes) -> dict:
    """A Claude document/image/text block for the file, chosen by extension."""
    ext = os.path.splitext(filename or "")[1].lower()
    media = _MEDIA.get(ext)
    if ext == ".pdf":
        return {"type": "document", "source": {"type": "base64",
                "media_type": "application/pdf", "data": base64.b64encode(data).decode()}}
    if media and media.startswith("image/"):
        return {"type": "image", "source": {"type": "base64",
                "media_type": media, "data": base64.b64encode(data).decode()}}
    return {"type": "text", "text": data.decode("utf-8", "replace")[:20000]}


async def _claude_call(client, model, content_blocks) -> str:
    """The single network seam. Returns the model's raw text."""
    resp = await client.messages.create(
        model=model, max_tokens=1024, system=_SYSTEM,
        thinking={"type": "disabled"},
        messages=[{"role": "user", "content": content_blocks}])
    return _text_of(resp)


def _parse_extraction_json(text: str) -> dict:
    """Defensive parse of the model's JSON object. Malformed -> {} (files as 'other', no
    proposals). Never raises."""
    if not text:
        return {}
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t[:4].lower() == "json":
            t = t[4:]
    try:
        obj = json.loads(t[t.index("{"):t.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return {}
    return obj if isinstance(obj, dict) else {}


# ── Derivation → ProposedObligation rows (SPEC 3.3) ───────────────────────────
def _pdate(v) -> dt.date | None:
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _entity_attributes(parsed: dict) -> dict:
    """The assisted-onboarding attributes a formation/return document proposes for the entity
    (Part 5.0). Applied only on human confirm; surfaced here for the entity form to pre-fill."""
    anchors = parsed.get("anchors") or {}
    return {"entity_type": parsed.get("entity_type_signal"),
            "formation_date": anchors.get("formation_date")}


def _ent_by_id(entities: list, entity_id: str | None):
    if not entity_id:
        return None
    for e in entities:
        if str(e.id) == str(entity_id):
            return e
    return None


async def _existing_obligation(s: AsyncSession, tenant_id, entity_id, kind):
    if not entity_id:
        return None
    return (await s.execute(select(Obligation).where(
        Obligation.tenant_id == tenant_id, Obligation.entity_id == entity_id,
        Obligation.kind == kind))).scalar_one_or_none()


async def _has_document(s: AsyncSession, tenant_id, entity_id, category, exclude_id) -> bool:
    if not entity_id:
        return False
    row = (await s.execute(select(BinderDocument.id).where(
        BinderDocument.tenant_id == tenant_id, BinderDocument.entity_id == entity_id,
        BinderDocument.category == category, BinderDocument.id != exclude_id))).first()
    return row is not None


async def _derive_proposals(s: AsyncSession, tenant_id, doc, parsed, today, match, entities) -> list:
    """Build (but do not commit) the ProposedObligation rows a document implies. Read
    derivations from printed dates, rule derivations from anchors + JurisdictionRule, plus
    gap (implied-but-missing) and renewal (matches an existing obligation) flavors."""
    anchors = parsed.get("anchors") or {}
    eid = match["entity_id"]
    ent = _ent_by_id(entities, eid)
    juris = ent.jurisdiction if ent else None
    out: list[ProposedObligation] = []

    async def add(kind, method, conf, *, due_date=None, cadence="annual", lead_days=45,
                  basis="", jurisdiction=None, applicable=True, rule_id=None, force_flavor=None):
        # Renewal vs new: a match on an existing (entity, kind) obligation rolls it forward.
        existing = await _existing_obligation(s, tenant_id, eid, kind)
        flavor = force_flavor or ("renewal" if existing is not None else "normal")
        fields = [["Entity", (ent.legal_name if ent else (parsed.get("entity_name_guess") or "unclear"))],
                  ["Kind", kind], ["Method", method]]
        if due_date:
            fields.append(["Due date", due_date.isoformat()])
        if jurisdiction:
            fields.append(["Jurisdiction", jurisdiction])
        out.append(ProposedObligation(
            tenant_id=tenant_id, document_id=doc.id, entity_id=(uuid.UUID(eid) if eid else None),
            entity_confidence=match["confidence"], entity_candidates=match["candidates"],
            kind=kind, method=method, confidence=conf,
            proposed={"due_date": due_date.isoformat() if due_date else None,
                      "lead_days": lead_days, "cadence": cadence, "applicable": applicable,
                      "jurisdiction": jurisdiction, "rule_id": (str(rule_id) if rule_id else None),
                      "ambiguous": match["ambiguous"], "fields": fields},
            basis=basis, flavor=flavor,
            renewal_of_id=(existing.id if (existing is not None and flavor == "renewal") else None),
            state="pending"))

    # ── read: a printed date maps straight to a kind ──
    exp = _pdate(anchors.get("expiration_date"))
    if exp:
        await add("insurance", "read", 0.95, due_date=exp, cadence="annual",
                  basis=f"Policy declarations read expiration {exp.isoformat()}, taken directly.")
    ragent = _pdate(anchors.get("renewal_date"))
    if ragent and (parsed.get("category") == "registered_agent"):
        await add("registered_agent", "read", 0.9, due_date=ragent, cadence="annual",
                  basis=f"Registered-agent renewal date read {ragent.isoformat()}.")

    # Rule- and gap-derived proposals depend on WHICH entity this is (its jurisdiction, its
    # implied filings). When the entity match is ambiguous we don't trust that identity, so we
    # emit only the read proposals above (the document's own printed dates) and leave the entity
    # pick to the human — never a rule/gap obligation, and never a "no BOI on file" alert, pinned
    # to a guessed entity. (Fix from the go/no-go: a Zenworth doc shouldn't flag a BOI gap on
    # whatever it happened to fuzzy-match.)
    if not match["ambiguous"]:
        # ── rule: annual report from formation date + jurisdiction ──
        formation = _pdate(anchors.get("formation_date")) or (ent.formation_date if ent else None)
        etype = (ent.entity_type if ent else None) or parsed.get("entity_type_signal")
        if formation and juris:
            rule = await lookup_rule(s, tenant_id, jurisdiction=juris, entity_type=etype, kind="annual_report")
            if rule is not None:
                r = derive(rule, today=today, anchor=formation)
                verb = "not required" if not r["applicable"] else (
                    f"due {r['due_date'].isoformat()}" if r["due_date"] else "human-set")
                await add("annual_report", "rule", 0.85, due_date=r["due_date"], cadence=r["cadence"],
                          lead_days=r["lead_days"], jurisdiction=juris, applicable=r["applicable"],
                          rule_id=rule.id,
                          basis=(f"Formation {formation.isoformat()} + {juris} rule "
                                 f"({rule.derivation}): annual report {verb}, derived."))

        # ── rule: federal + state tax from the return type ──
        rt = (anchors.get("return_type") or "").lower().replace(" ", "")
        cls = RETURN_TYPE_CLASS.get(rt)
        if cls:
            for kind, jr in (("federal_tax", None), ("state_tax", juris)):
                rule = await lookup_rule(s, tenant_id, jurisdiction=jr, entity_type=None, kind=kind)
                if rule is None:
                    continue
                r = derive(rule, today=today, classification=cls)
                await add(kind, "rule", 0.8, due_date=r["due_date"], cadence=r["cadence"],
                          lead_days=r["lead_days"], jurisdiction=jr, rule_id=rule.id,
                          basis=(f"Return type {anchors.get('return_type')} ({cls}) + "
                                 f"{rule.derivation}: {kind.replace('_', ' ')} schedule, derived."))

        # ── gap: an active entity that implies a BOI filing with none on file ──
        if ent and (ent.entity_type in {"llc", "s_corp", "c_corp", "partnership"}):
            has_ob = await _existing_obligation(s, tenant_id, eid, "boi")
            has_doc = await _has_document(s, tenant_id, eid, "tax", doc.id)  # BOI evidence files under tax
            if has_ob is None and not has_doc:
                rule = await lookup_rule(s, tenant_id, jurisdiction=None, entity_type=None, kind="boi")
                await add("boi", "rule", 0.6, cadence="one_time", lead_days=45,
                          rule_id=(rule.id if rule else None), force_flavor="gap",
                          basis=("Entity is active and its type implies a BOI/FinCEN filing; none is "
                                 "on file. Human-set date (BOI posture was legally turbulent 2024-2025; "
                                 "re-verify current requirement)."))
    return out


# ── Orchestration ─────────────────────────────────────────────────────────────
async def extract_document(s: AsyncSession, tenant_id, doc, *, today=None, entities=None,
                           client=None, model=None) -> dict:
    """Extract one document: classify + match + derive, caching the raw result on the row and
    writing ProposedObligation rows. Never writes an Obligation. Returns a per-doc summary."""
    today = today or dt.date.today()
    if entities is None:
        entities = (await s.execute(select(LegalEntity).where(
            LegalEntity.tenant_id == tenant_id, LegalEntity.active.is_(True)))).scalars().all()

    # Fetch the blob. On Railway the worker and API run in SEPARATE containers with separate
    # disks, so a doc uploaded via the API isn't on the worker's filesystem. If the blob isn't
    # present in THIS process, DEFER instead of poisoning the doc as an empty 'other': leave
    # extracted=None so the API-side post-upload task (which has the file) processes it.
    _defer = {"document_id": str(doc.id), "category": None, "entity_guess": None,
              "entity_id": None, "entity_confidence": 0.0, "ambiguous": False,
              "proposals": 0, "gaps": 0, "deferred": True}
    if doc.storage_ref and not binder_storage.exists(doc.storage_ref):
        log.warning("binder_extract doc=%s: blob %s absent in this process; deferring",
                    doc.id, doc.storage_ref)
        return _defer
    try:
        data = binder_storage.read(doc.storage_ref) if doc.storage_ref else b""
    except Exception as e:
        log.warning("binder_extract doc=%s: blob read failed (%s); deferring", doc.id, e)
        return _defer

    parsed: dict = {}
    if data and _enabled():
        try:
            text = await _claude_call(client or _client(), model or _model(),
                                      [_content_block(doc.filename, data), {"type": "text", "text": _USER}])
            parsed = _parse_extraction_json(text)
        except Exception as e:                       # network/parse failure -> files as 'other'
            log.warning("binder_extract doc=%s call failed: %s", doc.id, e)
            parsed = {}

    category = parsed.get("category")
    if category not in CATEGORIES:
        category = "other"
    doc.category = category

    guess = parsed.get("entity_name_guess")
    match = match_entity(guess, entities)
    doc.extracted = {"parsed": parsed, "entity_attributes": _entity_attributes(parsed),
                     "match": match, "extracted_at": today.isoformat()}
    # Attach the document to the entity only on a confident, unambiguous match.
    if match["entity_id"] and not match["ambiguous"] and match["confidence"] >= _AUTO_LINK \
            and doc.entity_id is None:
        doc.entity_id = uuid.UUID(match["entity_id"])

    proposals = await _derive_proposals(s, tenant_id, doc, parsed, today, match, entities)
    for p in proposals:
        s.add(p)
    await s.flush()

    gaps = sum(1 for p in proposals if p.flavor == "gap")
    log.info("binder_extract doc=%s entity=%s conf=%.2f proposals=%d ambiguous=%s gaps=%d",
             doc.id, (guess or "?"), match["confidence"], len(proposals), match["ambiguous"], gaps)
    return {"document_id": str(doc.id), "category": category, "entity_guess": guess,
            "entity_id": match["entity_id"], "entity_confidence": match["confidence"],
            "ambiguous": match["ambiguous"], "proposals": len(proposals), "gaps": gaps}


async def run_binder_extraction(s: AsyncSession, tenant_id, *, limit: int = 50, today=None) -> dict:
    """Worker pass: extract every not-yet-extracted document for a tenant. Commits once. Gated
    on a Claude key (no key -> skipped, nothing written)."""
    if not _enabled():
        return {"skipped": True, "documents": 0, "proposals": 0}
    today = today or dt.date.today()
    entities = (await s.execute(select(LegalEntity).where(
        LegalEntity.tenant_id == tenant_id, LegalEntity.active.is_(True)))).scalars().all()
    docs = (await s.execute(select(BinderDocument).where(
        BinderDocument.tenant_id == tenant_id, BinderDocument.extracted.is_(None))
        .order_by(BinderDocument.created_at.asc()).limit(limit))).scalars().all()
    summaries = []
    for doc in docs:
        summaries.append(await extract_document(s, tenant_id, doc, today=today, entities=entities))
    await s.commit()
    return {"skipped": False, "documents": len(docs),
            "deferred": sum(1 for x in summaries if x.get("deferred")),
            "proposals": sum(x["proposals"] for x in summaries), "results": summaries}
