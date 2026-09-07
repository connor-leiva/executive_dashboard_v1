"""The portal's assistant: answers about a workspace's OWN documents.

Not the dashboard assistant in services/assistant.py. That one reads Connor's numbers across the
tabs he can see; this one reads a tenant's intranet -- their SOPs, their training, their people,
their tools -- for the member asking. Different corpus, different audience, different tenant.

THE CORPUS IS THE MEMBER'S OWN PAYLOAD, for the same reason portal search is: `_published_content`
has already applied the capability matrix and every role audience, so an assistant built on its
output cannot cite a document the asker could not open. Re-querying the tables here would be a
second implementation of those rules and a second chance to get them wrong -- and getting them
wrong in an assistant is worse than in a list, because the answer quotes the thing.

WHAT IT HONESTLY CANNOT DO. The payload carries titles, owners, versions, categories, descriptions
and the body text of authored pages. It does NOT carry the inside of an uploaded SOP document --
those are bytes in object storage that nothing has extracted. So this answers "where is the
under-contract checklist, whose is it, which version is current, what training covers it" and
refuses "what does step four say". That is most of what a portal is opened for, and pretending
otherwise would produce confident answers from nothing, which is the one outcome worse than "I
don't know". The system prompt says so explicitly, and refuse_without_source enforces it.

THE API KEY IS THE PLATFORM'S, in env, unlike every tenant integration in this product. Those are
the customer's own accounts -- their Sisu, their GHL, their Google -- and they configure them
themselves. Anthropic is Acumyn's cost, billed on through the plan tier that carries
`ai_assistant`, exactly like Resend. A tenant never sees or supplies it.
"""
from __future__ import annotations

import json
import logging

from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import IntranetAiSetting

log = logging.getLogger("app")

MAX_QUESTION = 500
# Enough for a real answer with citations, small enough that a runaway reply cannot cost a fortune.
MAX_TOKENS = 900

_client: AsyncAnthropic | None = None


def available() -> bool:
    """Whether the platform can answer at all. Separate from whether a PLAN allows it: one is our
    configuration and the other is the customer's, and a member told "not on your plan" when the
    truth is a missing key would send them to upgrade for a thing that still would not work."""
    return bool((settings.ANTHROPIC_API_KEY or "").strip())


def _anthropic() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY.strip())
    return _client


# ---- the corpus ---------------------------------------------------------------------------

def corpus(content: dict) -> list[dict]:
    """The member's filtered content, flattened into citable entries.

    Every entry carries a `ref` the answer can cite and the portal can turn into a link. Nothing
    reaches this list that was not in the payload the same member's screens render.
    """
    out: list[dict] = []
    c = content or {}

    for course in c.get("courses") or []:
        lessons = [le.get("title") for le in (course.get("lessons") or []) if le.get("title")]
        out.append({
            "kind": "Training",
            "title": course.get("title"),
            "ref": f"/training/{course.get('id')}",
            "facts": {
                "category": course.get("category"),
                "description": course.get("description"),
                "lessons": lessons,
                "sequential": course.get("sequential"),
            },
        })

    for sop in c.get("sops") or []:
        out.append({
            "kind": "SOP",
            "title": sop.get("title"),
            "ref": "/sops",
            "facts": {
                "category": sop.get("category"),
                "version": sop.get("version"),
                "owner": sop.get("owner"),
                # Said explicitly so the model does not infer that a downloadable file means it
                # has read the file.
                "document": ("an attached file, whose contents are NOT available to you"
                             if sop.get("file_url") else "no file attached"),
            },
        })

    for group in c.get("tool_groups") or []:
        for tool in group.get("tools") or []:
            out.append({
                "kind": "Tool", "title": tool.get("name"), "ref": "/tools",
                "facts": {"group": group.get("label"), "url": tool.get("url")},
            })

    for person in c.get("directory") or []:
        out.append({
            "kind": "Person", "title": person.get("name"), "ref": "/directory",
            "facts": {
                "title": person.get("title"),
                "role": person.get("role"),
                "market": person.get("market"),
                # The field that answers "who handles X", which is most of what a directory is
                # opened for.
                "owns": person.get("owns"),
                "email": person.get("email"),
                "phone": person.get("phone"),
                "leadership": person.get("is_leadership"),
            },
        })

    for page in c.get("pages") or []:
        out.append({
            "kind": "Page", "title": page.get("title"), "ref": f"/p/{page.get('key')}",
            "facts": {
                "subtitle": page.get("subtitle"),
                # Authored pages are the one place the payload carries real prose, so this is
                # the only content the assistant can actually quote from.
                "sections": [{"heading": sec.get("heading"), "body": sec.get("body")}
                             for sec in (page.get("sections") or [])],
            },
        })

    for wtd in c.get("wtd_lists") or []:
        out.append({
            "kind": "Win the Day", "title": wtd.get("name"), "ref": "/wtd",
            "facts": {"script": wtd.get("script_name")},
        })

    return [e for e in out if e.get("title")]


SYSTEM = """You are the assistant inside {workspace}'s staff portal. You answer questions from \
{workspace}'s own team about {workspace}'s own documents, people and procedures.

You are given a CORPUS: everything this particular person is allowed to see. It has been filtered \
for them already, so anything in it is fair to cite and anything absent from it is either not in \
this workspace or not theirs to read. Either way you do not have it.

WHAT THE CORPUS ACTUALLY CONTAINS. For most entries it holds titles, owners, versions, categories \
and descriptions -- not the body of the document. Where an SOP says its contents are not available \
to you, you have NOT read that document and must not summarise, paraphrase or quote it. Say where \
it is, who owns it and which version is current, and point them at it. Only authored Pages carry \
real prose you can answer from directly.

{grounding}

Be brief and concrete. Name people by name, SOPs by title and version, and tools by what they are \
for. Do not invent a policy, a procedure, a phone number or a person. If somebody asks something \
this workspace has not written down, say plainly that it is not in the portal{escalation}.

Answer in plain prose. Do not use markdown headings or bullet syntax."""

GROUNDING_STRICT = """EVERY claim must come from the corpus. If the corpus does not answer the \
question, say so and stop -- do not fall back on what is generally true of real-estate teams, or \
of businesses in general. A confident answer assembled from nothing is worse for them than "that \
is not written down here", because they will act on it."""

GROUNDING_LOOSE = """Prefer the corpus for anything specific to this workspace. General knowledge \
is acceptable for general questions, but never present it as this workspace's policy."""


def _system(workspace: str, cfg: IntranetAiSetting | None) -> str:
    # cfg is None for every workspace that has never opened the AI settings screen, which is all
    # of them on day one -- so the no-config path is the COMMON path here, not the edge case. It
    # defaults to the strict reading of every guardrail: an assistant that refuses too often is a
    # disappointment, and one that invents a policy for a team who will act on it is not.
    strict = cfg is None or cfg.refuse_without_source
    escalation = ""
    channel = (cfg.escalation_channel or "").strip() if cfg is not None else ""
    if channel and cfg.offer_escalation:
        escalation = f", and suggest they ask in {channel}"
    return SYSTEM.format(
        workspace=workspace,
        grounding=GROUNDING_STRICT if strict else GROUNDING_LOOSE,
        escalation=escalation)


# The model reports what it used, rather than us guessing from the text. A citation list parsed
# out of prose is a citation list that drifts from the prose.
ANSWER_TOOL = {
    "name": "answer",
    "description": "Give the answer, naming exactly which corpus entries it came from.",
    "input_schema": {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "description": "The answer, in plain prose."},
            "refs": {
                "type": "array",
                "items": {"type": "string"},
                "description": ("The `ref` of every corpus entry the answer relies on. Empty if "
                                "the corpus did not answer the question."),
            },
            "answered": {
                "type": "boolean",
                "description": ("True only if the corpus actually answered the question. False "
                                "when you had to say it is not written down here."),
            },
        },
        "required": ["answer", "refs", "answered"],
    },
}


async def ask(s: AsyncSession, tenant_id, workspace: str, content: dict,
              question: str) -> dict:
    """Answer one question. Returns {answer, citations, answered, failure}. Never raises."""
    entries = corpus(content)
    cfg = await s.get(IntranetAiSetting, tenant_id)
    by_ref = {e["ref"]: e for e in entries}

    if not entries:
        # Nothing to ground an answer in, so nothing is asked of the model. Charging for a call
        # whose only possible honest answer is "this workspace has no content yet" is waste.
        return {"answer": "There is nothing in this portal yet for me to answer from.",
                "citations": [], "answered": False, "failure": None}

    try:
        response = await _anthropic().messages.create(
            model=settings.ASSISTANT_MODEL,
            max_tokens=MAX_TOKENS,
            system=_system(workspace, cfg),
            tools=[ANSWER_TOOL],
            tool_choice={"type": "tool", "name": ANSWER_TOOL["name"]},
            messages=[{
                "role": "user",
                "content": (f"CORPUS:\n{json.dumps(entries, default=str)}\n\n"
                            f"QUESTION: {question}"),
            }],
        )
    except Exception as e:                     # noqa: BLE001 - an outage is not a 500 for them
        log.warning("intranet assistant failed tenant=%s: %s", tenant_id, e)
        return {"answer": "", "citations": [], "answered": False,
                "failure": f"{type(e).__name__}: {e}"[:300]}

    block = next((b for b in response.content if getattr(b, "type", None) == "tool_use"), None)
    if block is None:
        # It answered in prose instead of calling the tool. Usable, but with no citations to show.
        text = "".join(getattr(b, "text", "") for b in response.content).strip()
        return {"answer": text, "citations": [], "answered": bool(text), "failure": None}

    data = block.input or {}
    # CITATIONS ARE FILTERED AGAINST THE CORPUS, not trusted. A ref the model invented would
    # otherwise become a link in the portal to a document that does not exist -- or, worse, one
    # this member cannot open.
    citations = [{"title": by_ref[r]["title"], "kind": by_ref[r]["kind"], "ref": r}
                 for r in (data.get("refs") or []) if r in by_ref]
    answered = bool(data.get("answered")) and bool((data.get("answer") or "").strip())
    if cfg is not None and cfg.always_cite and answered and not citations:
        # The workspace asked for citations on every answer and there are none, which means the
        # answer did not come from their content. Reported as unanswered so it opens a gap.
        answered = False
    return {"answer": (data.get("answer") or "").strip(), "citations": citations,
            "answered": answered, "failure": None}
