"""THE WIN THE DAY PLAYBOOK: one workspace's daily method, as its console writes it.

The portal's Win the Day was a compiled-in checklist -- five blocks of three generic items, the same
for every customer -- with no model behind it. This is the model: a document in
`intranet_wtd_playbook.content`, plus the rows other things point at (call lists, scripts).

ONE DOCUMENT, VALIDATED SECTION BY SECTION. The header, the blocks of the day, the call steps, the
habits, the scoreboard and the on-ramp are short ordered lists of prose with no identity outside
the page, written and read together. Each section has its own model here, so the console saves
one section at a time and a bad one is refused with the field named -- the document is never
half-checked.

`{n}` AND `{N}` KEEP COUNTS TRUE. "Five Blocks. Same Shape Every Day." and "The 13 Lists" are
the mockup's words, and they stop being true the day somebody adds a block. So a title can say
`{N} Blocks` (the count in words, capitalised) or `The {n} Lists` (in digits), and the number is
filled in when the page is drawn.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Annotated, Any, Literal

from pydantic import (AfterValidator, BaseModel, ConfigDict, Field, StringConstraints,
                      ValidationError, model_validator)

FORMAT = "acumyn.wtd-playbook"
FORMAT_VERSION = 1

# THE THREE KINDS OF LIST ARE THE PRODUCT'S, NOT THE WORKSPACE'S. They change behaviour -- only
# a Top Down list is timed -- so a workspace renaming them would break the "done means" contract
# the whole page is built on. The order is the legend's.
KINDS = (
    {"key": "clear", "label": "Clear It", "text": "Done means empty.", "glyph": "■"},
    {"key": "top_down", "label": "Top Down", "text": "Done means the clock ran out.",
     "glyph": "↓"},
    {"key": "scan", "label": "Scan", "text": "Done means you looked.", "glyph": "◉"},
)
KIND_KEYS = {k["key"] for k in KINDS}

KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
LINK_RE = re.compile(r"^https?://[^\s<>\"']+$", re.I)
LIST_ID_RE = re.compile(r"^[0-9]{1,12}$")


class SectionError(ValueError):
    """A section that does not validate: `field` is a dotted path ("run.blocks.2.title")."""

    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


# ── field types ─────────────────────────────────────────────────────────────────────────────

def _text(n: int):
    return Annotated[str, StringConstraints(strip_whitespace=True, max_length=n)]


def _link(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    if len(value) > 500 or not LINK_RE.match(value):
        raise ValueError("Expected a web address starting with https://")
    return value


def _key(value: str) -> str:
    value = str(value).strip()
    if not KEY_RE.match(value):
        raise ValueError("Use up to 32 letters, numbers, - or _.")
    return value


Link = Annotated[str | None, AfterValidator(_link)]
Key = Annotated[str, AfterValidator(_key)]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _unique(items, attr: str, what: str) -> None:
    seen = set()
    for i, item in enumerate(items):
        value = getattr(item, attr)
        if value in seen:
            raise ValueError(f"Two {what} share the key '{value}' (item {i + 1}).")
        seen.add(value)


# ── the sections ────────────────────────────────────────────────────────────────────────────

class Item(_M):
    title: _text(160) = ""
    text: _text(1200) = ""


class Callout(_M):
    eyebrow: _text(120) = ""
    text: _text(1500) = ""


class Meta(_M):
    k: _text(40)
    v: _text(80)


class Cta(_M):
    label: _text(60) = ""
    url: Link = None      # None -> the connected Follow Up Boss account, when there is one


class Header(_M):
    eyebrow: _text(60) = ""
    title: _text(80) = ""
    lede: _text(400) = ""
    meta: list[Meta] = Field(default_factory=list, max_length=3)
    cta: Cta = Field(default_factory=Cta)


class Rule(_M):
    eyebrow: _text(80) = ""
    text: _text(600) = ""
    sub: _text(240) = ""


class Tabs(_M):
    run: _text(40) = "Today’s Run"
    lists: _text(40) = "The {n} Lists"
    call: _text(40) = "The Call"
    scripts: _text(40) = "Scripts"
    numbers: _text(40) = "The Numbers"
    tools: _text(40) = "Tools"


class Block(_M):
    key: Key
    title: _text(60)
    minutes: int | None = Field(default=None, ge=1, le=480)
    text: _text(800) = ""


class Weekly(_M):
    eyebrow: _text(120) = ""
    items: list[Item] = Field(default_factory=list, max_length=4)


class Run(_M):
    title: _text(120) = ""
    intro: _text(600) = ""
    blocks: list[Block] = Field(default_factory=list, max_length=8)
    trips: list[Item] = Field(default_factory=list, max_length=4)
    weekly: Weekly = Field(default_factory=Weekly)

    @model_validator(mode="after")
    def _keys(self):
        _unique(self.blocks, "key", "blocks")
        return self


class Group(_M):
    key: Key
    label: _text(120)


class PondLink(_M):
    label: _text(80)
    list_id: _text(12)

    @model_validator(mode="after")
    def _digits(self):
        if not LIST_ID_RE.match(self.list_id):
            raise ValueError("A pond's list id is the number at the end of its address.")
        return self


class Ponds(_M):
    eyebrow: _text(160) = ""
    text: _text(800) = ""
    links: list[PondLink] = Field(default_factory=list, max_length=12)


class Lists(_M):
    title: _text(160) = ""
    intro: _text(600) = ""
    groups: list[Group] = Field(default_factory=list, max_length=12)
    trips: list[Item] = Field(default_factory=list, max_length=4)
    ponds: Ponds = Field(default_factory=Ponds)

    @model_validator(mode="after")
    def _keys(self):
        _unique(self.groups, "key", "groups")
        return self


class Call(_M):
    title: _text(120) = ""
    intro: _text(600) = ""
    steps: list[Item] = Field(default_factory=list, max_length=10)
    compliance: Callout = Field(default_factory=Callout)
    habits_title: _text(80) = ""
    habits: list[Item] = Field(default_factory=list, max_length=12)


class ScriptGroup(_M):
    key: Key
    label: _text(120)
    sub: _text(80) = ""


class Library(_M):
    label: _text(80) = ""
    url: Link = None


class Scripts(_M):
    title: _text(120) = ""
    intro: _text(600) = ""
    library: Library = Field(default_factory=Library)
    groups: list[ScriptGroup] = Field(default_factory=list, max_length=8)
    fallback: Callout = Field(default_factory=Callout)

    @model_validator(mode="after")
    def _keys(self):
        _unique(self.groups, "key", "script groups")
        return self


class Tally(_M):
    key: Key
    short: _text(24) = ""
    goal: int | None = Field(default=None, ge=0, le=10000)


class Row(_M):
    label: _text(80)
    daily: _text(40) = ""
    weekly: _text(40) = ""
    highlight: bool = False
    tally: Tally | None = None


class Phase(_M):
    label: _text(40)
    through_day: int | None = Field(default=None, ge=1, le=365)
    goals: dict[str, Annotated[int, Field(ge=0, le=10000)]] = Field(default_factory=dict)
    focus: _text(300) = ""


class Onramp(_M):
    title: _text(80) = ""
    intro: _text(600) = ""
    phases: list[Phase] = Field(default_factory=list, max_length=6)


class Numbers(_M):
    title: _text(120) = ""
    intro: _text(600) = ""
    rows: list[Row] = Field(default_factory=list, max_length=12)
    footnote: _text(600) = ""
    onramp: Onramp = Field(default_factory=Onramp)

    @model_validator(mode="after")
    def _tallies_and_phases(self):
        tallies = [r.tally for r in self.rows if r.tally is not None]
        if len(tallies) > 4:
            raise ValueError("Today's sheet has room for four tallies at most.")
        _unique(tallies, "key", "tallies")
        keys = {t.key for t in tallies}
        last = 0
        for i, phase in enumerate(self.onramp.phases):
            unknown = sorted(set(phase.goals) - keys)
            if unknown:
                raise ValueError(f"On-ramp phase {i + 1} sets a target for '{unknown[0]}', "
                                 "which no scoreboard row counts.")
            if phase.through_day is None:
                if i != len(self.onramp.phases) - 1:
                    raise ValueError("Only the last on-ramp phase can run on with no end day.")
            elif phase.through_day <= last:
                raise ValueError("On-ramp phases must end on later days, in order.")
            else:
                last = phase.through_day
        return self


class Tool(_M):
    block: _text(60) = ""
    name: _text(80)
    url: Link = None
    tagline: _text(160) = ""
    text: _text(600) = ""


class Tools(_M):
    title: _text(120) = ""
    intro: _text(600) = ""
    items: list[Tool] = Field(default_factory=list, max_length=12)


class Playbook(_M):
    version: Literal[1] = 1
    header: Header = Field(default_factory=Header)
    rule: Rule = Field(default_factory=Rule)
    tabs: Tabs = Field(default_factory=Tabs)
    run: Run = Field(default_factory=Run)
    lists: Lists = Field(default_factory=Lists)
    call: Call = Field(default_factory=Call)
    scripts: Scripts = Field(default_factory=Scripts)
    numbers: Numbers = Field(default_factory=Numbers)
    tools: Tools = Field(default_factory=Tools)


SECTIONS: dict[str, type[_M]] = {
    "header": Header, "rule": Rule, "tabs": Tabs, "run": Run, "lists": Lists, "call": Call,
    "scripts": Scripts, "numbers": Numbers, "tools": Tools,
}


# ── validation ─────────────────────────────────────────────────────────────────────────────

def _message(err: dict) -> str:
    kind, ctx = err.get("type", ""), err.get("ctx") or {}
    if kind == "string_too_long":
        return f"At most {ctx.get('max_length')} characters."
    if kind == "too_long":
        return f"At most {ctx.get('max_length')} items."
    if kind == "missing":
        return "Required."
    if kind == "extra_forbidden":
        return "Unknown field."
    if kind in ("int_parsing", "int_type", "int_from_float"):
        return "Expected a whole number."
    if kind == "greater_than_equal":
        return f"Must be at least {ctx.get('ge')}."
    if kind == "less_than_equal":
        return f"Must be at most {ctx.get('le')}."
    if kind == "value_error":
        return str(ctx.get("error") or err.get("msg", "")).removeprefix("Value error, ")
    return str(err.get("msg") or "Not valid.")


def _raise(prefix: str, e: ValidationError) -> None:
    err = e.errors()[0]
    loc = ".".join(str(p) for p in (prefix, *err.get("loc", ())) if p != "")
    raise SectionError(loc or prefix, _message(err))


def clean_section(name: str, value: Any) -> dict:
    model = SECTIONS.get(name)
    if model is None:
        raise SectionError(name, "Unknown section.")
    try:
        return model.model_validate(value).model_dump(mode="json")
    except ValidationError as e:
        _raise(name, e)


def clean_content(value: Any) -> dict:
    try:
        return Playbook.model_validate(value or {}).model_dump(mode="json")
    except ValidationError as e:
        _raise("content", e)


def empty_content() -> dict:
    return Playbook().model_dump(mode="json")


def current(content: dict | None) -> dict:
    """A stored document, read tolerantly: a section written by an older shape falls back to its
    empty form rather than taking the whole page down."""
    base = empty_content()
    for name, model in SECTIONS.items():
        raw = (content or {}).get(name)
        if raw is None:
            continue
        try:
            base[name] = model.model_validate(raw).model_dump(mode="json")
        except ValidationError:
            pass
    return base


# ── words, days and targets ──────────────────────────────────────────────────────────────────

_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
         "fifteen sixteen seventeen eighteen nineteen").split()
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")


def number_words(n: int) -> str:
    """86 -> "eighty-six". Digits past 999, where words stop helping anybody read."""
    if n < 0 or n > 999:
        return str(n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")
    hundreds, rest = divmod(n, 100)
    return f"{_ONES[hundreds]} hundred" + (f" and {number_words(rest)}" if rest else "")


def fill(text: str | None, n: int) -> str:
    """`{n}` -> "13", `{N}` -> "Thirteen"."""
    text = text or ""
    if "{N}" in text:
        words = number_words(n)
        text = text.replace("{N}", words[:1].upper() + words[1:])
    return text.replace("{n}", str(n))


def working_day(start: dt.date | None, today: dt.date) -> int | None:
    """Which working day (Monday to Friday) `today` is, counting `start` as day 1. None with no
    start; 0 before it."""
    if start is None:
        return None
    if today < start:
        return 0
    days = (today - start).days + 1
    weeks, rest = divmod(days, 7)
    count = weeks * 5
    for i in range(rest):
        if (start + dt.timedelta(days=weeks * 7 + i)).weekday() < 5:
            count += 1
    return count


def goals_for(numbers: dict, started_on: dt.date | None, personal: dict | None,
              today: dt.date) -> tuple[dict, str | None]:
    """This person's targets for today, and the on-ramp phase they are in (None if not on it).

    Personal targets beat the on-ramp, which beats the team's. The on-ramp counts WORKING days:
    the mockup's phases are Week One, Week Two and "Day 11 Onward" -- ten working days."""
    goals = {r["tally"]["key"]: r["tally"].get("goal")
             for r in numbers.get("rows") or [] if r.get("tally")}
    phase_label = None
    day = working_day(started_on, today)
    if day is not None:
        for phase in (numbers.get("onramp") or {}).get("phases") or []:
            end = phase.get("through_day")
            if end is None or max(day, 1) <= end:
                for key, value in (phase.get("goals") or {}).items():
                    if key in goals:
                        goals[key] = value
                phase_label = phase.get("label")
                break
    for key, value in (personal or {}).items():
        if key in goals and isinstance(value, int) and not isinstance(value, bool):
            goals[key] = value
    return goals, phase_label


def clean_personal_goals(value: Any, tally_keys: set[str]) -> dict | None:
    """What an admin may store as somebody's own targets: known tally keys, whole numbers."""
    if value in (None, {}):
        return None
    if not isinstance(value, dict):
        raise SectionError("wtd_goals", "Expected an object of targets.")
    out = {}
    for key, goal in value.items():
        if key not in tally_keys:
            raise SectionError(f"wtd_goals.{key}", "No scoreboard row counts this.")
        if goal in (None, ""):
            continue
        if isinstance(goal, bool) or not isinstance(goal, int) or not 0 <= goal <= 10000:
            raise SectionError(f"wtd_goals.{key}", "Expected a whole number from 0 to 10000.")
        out[key] = goal
    return out or None


def tally_keys(content: dict | None) -> set[str]:
    rows = ((content or {}).get("numbers") or {}).get("rows") or []
    return {r["tally"]["key"] for r in rows if isinstance(r, dict) and r.get("tally")}


# ── what the portal is handed ────────────────────────────────────────────────────────────────

def people_url(list_base: str | None) -> str | None:
    """A Follow Up Boss list address -> the account's people page, the mockup's
    "Open Follow Up Boss" (`https://<account>.followupboss.com/2/people`)."""
    if not list_base:
        return None
    base = list_base.rstrip("/")
    if base.endswith("/list"):
        return base[: -len("/list")]
    return None


def _has_text(obj: dict | None, *fields: str) -> bool:
    return bool(obj) and any(str(obj.get(f) or "").strip() for f in fields)


def resolve(content: dict | None, *, lists: list, scripts: list, list_url, list_base: str | None,
            started_on: dt.date | None, personal: dict | None, today: dt.date) -> dict:
    """The page, ready to draw: counts filled in, lists numbered, grouped and linked, scripts
    joined, pond and account links built, and this person's targets worked out.

    `content` is the PUBLISHED document or None (a workspace that only ever authored call lists
    still gets them). `lists` are the active, published IntranetWtdList rows in run order and
    `scripts` the active, published IntranetWtdScript rows; `list_url(row)` is the router's own
    link builder, so a list here and anywhere else resolve the same way."""
    doc = current(content)
    by_script = {str(s.id): s for s in scripts}

    def _chips(row) -> list[dict]:
        out = []
        for sid in row.script_ids or []:
            script = by_script.get(str(sid))
            if script is not None:
                out.append({"name": script.chip or script.name, "url": script.url})
        if not out and row.script_name:
            out.append({"name": row.script_name, "url": None})
        return out

    items = [{
        "id": str(row.id), "no": f"{i:02d}", "name": row.name, "url": list_url(row),
        "cadence": row.cadence, "kind": row.kind if row.kind in KIND_KEYS else "clear",
        "description": row.description, "group_key": row.group_key, "block_key": row.block_key,
        "scripts": _chips(row),
    } for i, row in enumerate(lists, start=1)]
    count = len(items)

    group_keys = {g["key"] for g in doc["lists"]["groups"]}
    groups = [{"key": g["key"], "label": g["label"]} for g in doc["lists"]["groups"]
              if any(it["group_key"] == g["key"] for it in items)]
    if any(it["group_key"] not in group_keys for it in items):
        groups.append({"key": "", "label": ""})
        for it in items:
            if it["group_key"] not in group_keys:
                it["group_key"] = ""

    blocks = [{**b, "lists": [it["no"] for it in items if it["block_key"] == b["key"]]}
              for b in doc["run"]["blocks"]]

    goals, phase = goals_for(doc["numbers"], started_on, personal, today)
    tallies = [{"key": r["tally"]["key"], "label": r["label"],
                "short": r["tally"].get("short") or r["label"], "goal": goals.get(r["tally"]["key"])}
               for r in doc["numbers"]["rows"] if r.get("tally")]

    ponds = doc["lists"]["ponds"]
    pond_links = [{"label": p["label"],
                   "url": f"{list_base.rstrip('/')}/{p['list_id']}" if list_base else None}
                  for p in ponds["links"]]

    script_groups = []
    for g in doc["scripts"]["groups"]:
        members = sorted((s for s in scripts if s.group_key == g["key"]),
                         key=lambda s: (s.position or 0, s.name.lower()))
        if members:
            script_groups.append({**g, "items": [
                {"id": str(s.id), "name": s.name, "url": s.url, "description": s.description}
                for s in members]})

    header = doc["header"]
    cta_url = header["cta"]["url"] or people_url(list_base)
    cta_label = header["cta"]["label"] or ("Open Follow Up Boss →"
                                           if not header["cta"]["url"] and cta_url else "")

    run = {"title": fill(doc["run"]["title"], len(blocks)), "intro": doc["run"]["intro"],
           "blocks": blocks, "trips": doc["run"]["trips"], "weekly": doc["run"]["weekly"]}
    lists_out = {"title": fill(doc["lists"]["title"], count), "intro": doc["lists"]["intro"],
                 "count": count, "groups": groups, "items": items, "trips": doc["lists"]["trips"],
                 "ponds": ({"eyebrow": ponds["eyebrow"], "text": ponds["text"],
                            "links": pond_links}
                           if _has_text(ponds, "eyebrow", "text") or pond_links else None)}

    tabs_text = doc["tabs"]
    present = {
        "run": bool(blocks or tallies or doc["run"]["trips"]),
        "lists": bool(items),
        "call": bool(doc["call"]["steps"] or doc["call"]["habits"]
                     or _has_text(doc["call"]["compliance"], "text")),
        "scripts": bool(script_groups or _has_text(doc["scripts"]["fallback"], "text")),
        "numbers": bool(doc["numbers"]["rows"] or doc["numbers"]["onramp"]["phases"]),
        "tools": bool(doc["tools"]["items"]),
    }
    tabs = [{"key": key, "label": fill(tabs_text[key], count)}
            for key in ("run", "lists", "call", "scripts", "numbers", "tools") if present[key]]

    return {
        "has_playbook": content is not None,
        "header": {**header, "cta": {"label": cta_label, "url": cta_url}},
        "rule": doc["rule"] if _has_text(doc["rule"], "text") else None,
        "tabs": tabs,
        "kinds": list(KINDS),
        "run": run,
        "lists": lists_out,
        "call": doc["call"],
        "scripts": {**doc["scripts"], "groups": script_groups},
        "numbers": doc["numbers"],
        "tools": doc["tools"],
        "sheet": {"tallies": tallies},
        "goals": goals,
        "onramp_phase": phase,
    }


# ── moving a playbook between workspaces ─────────────────────────────────────────────────────

def export_bundle(content: dict | None, lists: list, scripts: list) -> dict:
    """The playbook, its call lists and its scripts as one portable file. Scripts are named by a
    `ref` rather than their row id, which means nothing in another workspace."""
    refs = {str(s.id): f"s{i}" for i, s in enumerate(scripts, start=1)}
    return {
        "format": FORMAT, "version": FORMAT_VERSION,
        "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "content": current(content),
        "scripts": [{"ref": refs[str(s.id)], "name": s.name, "chip": s.chip, "url": s.url,
                     "description": s.description, "group_key": s.group_key,
                     "active": bool(s.active)} for s in scripts],
        "lists": [{"name": row.name, "provider": row.provider,
                   "external_list_id": row.external_list_id, "cadence": row.cadence,
                   "kind": row.kind, "description": row.description,
                   "group_key": row.group_key, "block_key": row.block_key,
                   "scripts": [refs[str(sid)] for sid in (row.script_ids or []) if str(sid) in refs],
                   "active": bool(row.active)} for row in lists],
    }


class _ImportScript(_M):
    ref: Key
    name: _text(120)
    chip: _text(40) | None = None
    url: Link = None
    description: _text(400) | None = None
    group_key: Key | None = None
    active: bool = True


class _ImportList(_M):
    name: _text(120)
    provider: _text(80) = "follow_up_boss"
    external_list_id: _text(64) | None = None
    cadence: _text(32) | None = None
    kind: Literal["clear", "top_down", "scan"] = "clear"
    description: _text(600) | None = None
    group_key: Key | None = None
    block_key: Key | None = None
    scripts: list[Key] = Field(default_factory=list, max_length=6)
    active: bool = True


class _Bundle(_M):
    format: Literal["acumyn.wtd-playbook"]
    version: Literal[1]
    exported_at: str | None = None
    content: dict
    scripts: list[_ImportScript] = Field(default_factory=list, max_length=100)
    lists: list[_ImportList] = Field(default_factory=list, max_length=60)


def clean_bundle(value: Any) -> dict:
    """An import file, checked whole before anything is written: its document, and that every
    list's group, block and scripts exist in it."""
    try:
        bundle = _Bundle.model_validate(value)
    except ValidationError as e:
        _raise("file", e)
    content = clean_content(bundle.content)
    refs = [s.ref for s in bundle.scripts]
    if len(set(refs)) != len(refs):
        raise SectionError("file.scripts", "Two scripts share a ref.")
    groups = {g["key"] for g in content["lists"]["groups"]}
    blocks = {b["key"] for b in content["run"]["blocks"]}
    script_groups = {g["key"] for g in content["scripts"]["groups"]}
    for i, script in enumerate(bundle.scripts):
        if script.group_key and script.group_key not in script_groups:
            raise SectionError(f"file.scripts.{i}.group_key",
                               f"No script group '{script.group_key}' in the playbook.")
    for i, row in enumerate(bundle.lists):
        if row.group_key and row.group_key not in groups:
            raise SectionError(f"file.lists.{i}.group_key", f"No group '{row.group_key}'.")
        if row.block_key and row.block_key not in blocks:
            raise SectionError(f"file.lists.{i}.block_key", f"No block '{row.block_key}'.")
        missing = [r for r in row.scripts if r not in set(refs)]
        if missing:
            raise SectionError(f"file.lists.{i}.scripts", f"No script '{missing[0]}' in the file.")
        if row.external_list_id and not LIST_ID_RE.match(row.external_list_id):
            raise SectionError(f"file.lists.{i}.external_list_id",
                               "A list id is the number at the end of its address.")
    return {"content": content,
            "scripts": [s.model_dump(mode="json") for s in bundle.scripts],
            "lists": [row.model_dump(mode="json") for row in bundle.lists]}
