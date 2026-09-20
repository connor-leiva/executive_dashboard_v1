"""DRAFT A PROCEDURE FROM A DOCUMENT (SOP-LIBRARY-SPEC.md, phase 6).

A workspace's procedures already exist -- in a Drive folder, as PDFs and Word files, dozens of
them. Typing each one out again as steps is the only reason a library would stay a pile of
documents, so this reads one and hands its owner a first draft to correct.

A DRAFT, NEVER A SAVE. This returns the steps; somebody reads them, fixes them and presses save.
The model is reading a procedure that people follow, and "close enough" is not a standard a
procedure can be held to.
"""
from __future__ import annotations

import json
import logging
import re
import zipfile
from io import BytesIO

from ..config import settings
from . import sop_library
from .intranet_assistant import _anthropic, available  # noqa: F401 -- one client, one key

log = logging.getLogger("acumyn.sop_drafting")

# Enough of a procedure to draft from; a 90-page handbook is not one procedure anyway.
MAX_CHARS = 24000
MAX_PDF_PAGES = 40
MAX_TOKENS = 2000

DRAFT_TOOL = {
    "name": "procedure",
    "description": "The procedure as the library holds it: an opening, numbered steps, and the "
                   "one thing not to skip.",
    "input_schema": {
        "type": "object",
        "properties": {
            "intro": {"type": "string",
                      "description": "One short paragraph saying what this procedure covers."},
            "steps": {
                "type": "array",
                "description": "The steps, in the order they are done.",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string",
                                  "description": "The step as an instruction, under 80 characters."},
                        "text": {"type": "string",
                                 "description": "What it means in practice. One or two sentences."},
                    },
                    "required": ["title", "text"],
                },
            },
            "callout": {
                "type": "object",
                "description": "The one thing that must not be skipped, if the document says so.",
                "properties": {"label": {"type": "string"}, "text": {"type": "string"}},
            },
        },
        "required": ["intro", "steps"],
    },
}

SYSTEM = """You turn an existing written procedure into the shape this staff portal holds: an \
opening paragraph, numbered steps, and at most one "do not skip" warning.

Use ONLY what the document says. Do not invent a step, a deadline, a tool or a name; if the \
document is vague, keep the step vague rather than inventing the detail. Keep the team's own \
words and their names for things. Drop headers, footers, page numbers and signature blocks. If \
the document is not a procedure at all, return no steps."""


def text_from_pdf(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(BytesIO(data))
    out = []
    for page in reader.pages[:MAX_PDF_PAGES]:
        out.append(page.extract_text() or "")
        if sum(len(p) for p in out) > MAX_CHARS:
            break
    return "\n".join(out)


def text_from_docx(data: bytes) -> str:
    """A .docx is a zip whose word/document.xml holds the words. Paragraph tags become line
    breaks and every other tag is dropped -- enough to draft from, and no new dependency."""
    with zipfile.ZipFile(BytesIO(data)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", "ignore")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    return re.sub(r"\n{3,}", "\n\n", xml)


def extract(data: bytes, content_type: str | None) -> str:
    """The words in an uploaded procedure, or "" when they cannot be read from it (a scan, for
    instance, which is a picture of a procedure and not a procedure)."""
    try:
        if content_type == "application/pdf":
            text = text_from_pdf(data)
        elif (content_type or "").endswith("wordprocessingml.document"):
            text = text_from_docx(data)
        else:
            return ""
    except Exception as e:                      # noqa: BLE001 -- a broken file is not a 500
        log.warning("could not read a procedure document: %s", e)
        return ""
    return re.sub(r"[ \t]+", " ", text).strip()[:MAX_CHARS]


async def draft(title: str, text: str) -> dict:
    """A first draft of the procedure, validated exactly as a typed one is. Raises SopError when
    the model gives something the library would refuse."""
    response = await _anthropic().messages.create(
        model=settings.ASSISTANT_MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        tools=[DRAFT_TOOL],
        tool_choice={"type": "tool", "name": DRAFT_TOOL["name"]},
        messages=[{"role": "user",
                   "content": f"PROCEDURE TITLE: {title}\n\nTHE DOCUMENT:\n{text}"}],
    )
    block = next((b for b in response.content if getattr(b, "type", None) == "tool_use"), None)
    if block is None:
        raise sop_library.SopError("body", "The draft came back in the wrong shape. Try again.")
    data = block.input or {}
    steps = [{"title": (s or {}).get("title") or "", "text": (s or {}).get("text") or ""}
             for s in (data.get("steps") or [])][:sop_library.MAX_STEPS]
    callout = data.get("callout") or {}
    body = {
        "intro": (data.get("intro") or "")[:sop_library.TEXT_LIMITS["intro"]],
        "steps": [{"title": s["title"][:sop_library.TEXT_LIMITS["step_title"]],
                   "text": s["text"][:sop_library.TEXT_LIMITS["step_text"]]}
                  for s in steps if s["title"].strip()],
        "callout": ({"label": (callout.get("label") or sop_library.DEFAULT_CALLOUT_LABEL)[
                         :sop_library.TEXT_LIMITS["callout_label"]],
                     "text": (callout.get("text") or "")[:sop_library.TEXT_LIMITS["callout_text"]]}
                    if (callout.get("text") or "").strip() else None),
    }
    # Through the same gate a typed procedure goes through, so a draft cannot land in a shape
    # the editor and the portal disagree about.
    return sop_library.clean_body(body) or {"intro": None, "steps": [], "callout": None}


def summary_of(text: str) -> str:
    """A fallback one-liner for the card, taken from the document's own first sentence."""
    first = re.split(r"(?<=[.!?])\s", (text or "").strip(), maxsplit=1)[0]
    return first[:sop_library.TEXT_LIMITS["summary"]].strip()


__all__ = ["DRAFT_TOOL", "draft", "extract", "summary_of", "text_from_docx", "text_from_pdf",
           "available", "MAX_CHARS"]


def dumps(body: dict) -> str:
    """For the audit note: what the draft came back as, compactly."""
    return json.dumps(body, default=str)[:400]
