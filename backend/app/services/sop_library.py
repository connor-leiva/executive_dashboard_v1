"""THE SOP LIBRARY: what a procedure is, and what each screen needs of it
(SOP-LIBRARY-SPEC.md, phases 1-4).

An SOP used to be an uploaded file with a title on it. It is now a procedure that can be READ: an
intro, numbered steps, and the one thing not to skip -- with the document still there for the ones
that are a document, and for the ones nobody has written out yet (D1). Every screen in the mockup
is drawn from what is here.

TWO COPIES OF THE PROCEDURE, ON PURPOSE (D2). `body` is what the console edits and
`published_body` is what members read. The rest of the console publishes nothing -- a title saved
is a title live -- but a procedure halfway through a rewrite is not the procedure the team should
be following, so the text waits for Publish.

A VERSION IS A REVISION, not a file. Acknowledgements hang off the version, so "everyone read
v3.1" stays true only until there is a v3.2 -- whether that revision is a new document, a rewritten
body, or both.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

TEXT_LIMITS = {
    "summary": 200,        # the card's one line
    "applies_to": 120,     # "Listing Agents"
    "intro": 1500,
    "step_title": 160,
    "step_text": 1200,
    "callout_label": 40,
    "callout_text": 800,
}
MAX_STEPS = 40
MAX_TOOLS = 12
DEFAULT_CALLOUT_LABEL = "Do Not Skip"
PROFILE_FIELDS = {"summary", "applies_to", "tool_ids", "required"}


class SopError(ValueError):
    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


# ── what a procedure may say ─────────────────────────────────────────────────────────────────

def _text(field: str, value: Any, limit_key: str | None = None) -> str:
    text = str(value or "").strip()
    limit = TEXT_LIMITS[limit_key or field]
    if len(text) > limit:
        raise SopError(field, f"At most {limit} characters.")
    return text


def clean_field(field: str, value: Any):
    """One of the SOP's own fields, checked; SopError names the one that is wrong."""
    if field in ("summary", "applies_to"):
        return _text(field, value) or None
    if field == "required":
        if not isinstance(value, bool):
            raise SopError(field, "Expected true or false.")
        return value
    if field == "tool_ids":
        if value in (None, ""):
            return None
        if not isinstance(value, list):
            raise SopError(field, "Expected a list of tools.")
        if len(value) > MAX_TOOLS:
            raise SopError(field, f"At most {MAX_TOOLS} tools.")
        ids = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in ids:
                ids.append(text)
        return ids or None
    raise SopError(field, "Unknown field.")


def clean_body(value: Any) -> dict | None:
    """The written procedure, or None when nothing is written.

    `{"intro": str, "steps": [{"title": str, "text": str}], "callout": {"label", "text"}}`.
    An empty step is dropped rather than refused -- the editor adds a blank row when somebody
    presses Add a step -- but a step with words and no title is refused, because the step list is
    also the page's contents and a nameless entry cannot appear in it.
    """
    if value in (None, ""):
        return None
    if not isinstance(value, dict):
        raise SopError("body", "Expected a procedure.")
    unknown = set(value) - {"intro", "steps", "callout"}
    if unknown:
        raise SopError("body", f"Unexpected: {', '.join(sorted(unknown))}.")

    intro = _text("intro", value.get("intro"))

    raw_steps = value.get("steps") or []
    if not isinstance(raw_steps, list):
        raise SopError("steps", "Expected a list of steps.")
    if len(raw_steps) > MAX_STEPS:
        raise SopError("steps", f"At most {MAX_STEPS} steps.")
    steps = []
    for i, step in enumerate(raw_steps):
        if not isinstance(step, dict):
            raise SopError(f"steps.{i}", "Expected a step.")
        title = _text(f"steps.{i}.title", step.get("title"), "step_title")
        text = _text(f"steps.{i}.text", step.get("text"), "step_text")
        if not title and not text:
            continue
        if not title:
            raise SopError(f"steps.{i}.title", "A step needs a title.")
        steps.append({"title": title, "text": text or None})

    callout = None
    raw_callout = value.get("callout") or {}
    if raw_callout:
        if not isinstance(raw_callout, dict):
            raise SopError("callout", "Expected a label and the text.")
        label = _text("callout.label", raw_callout.get("label"), "callout_label")
        text = _text("callout.text", raw_callout.get("text"), "callout_text")
        if text:
            callout = {"label": label or DEFAULT_CALLOUT_LABEL, "text": text}

    if not intro and not steps and not callout:
        return None
    return {"intro": intro or None, "steps": steps, "callout": callout}


def has_body(body: Any) -> bool:
    body = body or {}
    return bool(body.get("intro") or body.get("steps") or body.get("callout"))


def step_anchor(index: int) -> str:
    """`#step-03`: the On This Page links, and a place for the assistant to point at."""
    return f"step-{index:02d}"


def body_text(body: Any) -> str | None:
    """The procedure as one block of text, for the assistant (D10).

    It used to be told a procedure was "an attached file, whose contents are NOT available to
    you", because that was true. A written one it can read, so *how do I order photography* can
    be answered from the step that says. Only ever handed the PUBLISHED body, and only to a
    member whose role may open the library -- the corpus is built from that member's own payload.
    """
    body = body or {}
    if not has_body(body):
        return None
    parts = []
    if body.get("intro"):
        parts.append(body["intro"])
    for i, step in enumerate(body.get("steps") or [], start=1):
        line = f"{i}. {step.get('title') or ''}".strip()
        if step.get("text"):
            line = f"{line} {step['text']}"
        parts.append(line)
    callout = body.get("callout") or {}
    if callout.get("text"):
        parts.append(f"{callout.get('label') or DEFAULT_CALLOUT_LABEL}: {callout['text']}")
    return "\n".join(parts)


# ── what the screens need ────────────────────────────────────────────────────────────────────

def updated_on(sop, version) -> dt.date | None:
    """The date a member is shown as "last updated": when the current revision landed, not when
    somebody last touched the row (D6). A typo fixed in place does not make a procedure new."""
    if version is not None and version.uploaded_at is not None:
        return version.uploaded_at.date()
    if sop.last_reviewed_on:
        return sop.last_reviewed_on
    return sop.updated_at.date() if sop.updated_at else None


def card(sop, *, category_name: str | None, owner: dict | None, version,
         acknowledged_at, document: bool) -> dict:
    """One procedure as the library draws it: the chip, the version, the blurb, the owner and the
    date. Everything the mockup's card shows and nothing the reader alone needs."""
    return {
        "id": str(sop.id),
        "title": sop.title,
        "summary": sop.summary,
        "department": category_name,
        "version": version.version_label if version is not None else None,
        "owner": owner,
        "updated_on": (updated_on(sop, version).isoformat()
                       if updated_on(sop, version) else None),
        "required": bool(sop.required),
        "has_body": has_body(sop.published_body),
        "has_document": document,
        "acknowledged_at": acknowledged_at,
    }


def reader(sop, *, category_name: str | None, owner: dict | None, version, acknowledged_at,
           document: dict | None, tools: list[dict], acknowledged_count: int,
           team_size: int) -> dict:
    """One procedure as its own page draws it: the card, the body, the sidebar, and how many
    colleagues have read this revision."""
    body = sop.published_body or {}
    steps = [{**step, "index": i + 1, "anchor": step_anchor(i + 1)}
             for i, step in enumerate(body.get("steps") or [])]
    return {
        **card(sop, category_name=category_name, owner=owner, version=version,
               acknowledged_at=acknowledged_at, document=bool(document)),
        "applies_to": sop.applies_to,
        "intro": body.get("intro"),
        "steps": steps,
        "callout": body.get("callout"),
        "tools": tools,
        "document": document,
        # "64 of 86 agents have acknowledged v3.1": the count is the workspace's, and who has NOT
        # read it stays in the console (D3).
        "acknowledged_count": acknowledged_count,
        "team_size": team_size,
    }


def changed_this_month(cards: list[dict], today: dt.date, limit: int = 3) -> list[dict]:
    """The mockup's *Changed This Month* panel: the most recent revisions of the current calendar
    month, newest first. Empty when nothing changed this month, and the panel is then not drawn --
    a panel headed "changed this month" listing something from April is a lie."""
    start = today.replace(day=1).isoformat()
    recent = [c for c in cards if c.get("updated_on") and c["updated_on"] >= start]
    recent.sort(key=lambda c: c["updated_on"], reverse=True)
    return recent[:limit]


def departments(cards: list[dict], names: list[str]) -> list[dict]:
    """The rail: every department the workspace has, with how many procedures are in it, plus
    All Procedures at the top. Departments with nothing in them are still listed -- the rail is
    the shape of the library, and an empty one is an admin's cue."""
    counts: dict[str, int] = {}
    for c in cards:
        if c.get("department"):
            counts[c["department"]] = counts.get(c["department"], 0) + 1
    return [{"name": "All Procedures", "key": "", "count": len(cards)}] + [
        {"name": name, "key": name, "count": counts.get(name, 0)} for name in names
    ]
