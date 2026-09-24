"""ULRG Recruiting — the bounded configuration behind Settings › Recruiting (§4.9).

`Integration(provider="ghl_recruiting").config` is the whole configuration; there is no settings
table. Everything that reaches it comes through `clean_settings`, on the bounded pattern
follow_ups.py established: a stored value is CLAMPED because it has to render whatever is in the
database, and a value arriving from the UI is REFUSED, because silently clamping somebody's input
teaches them the field does nothing.

STAGE GROUPS ARE MATCHED ON STAGE ID. The design shape is `[label, owner_role, [stage_ids]]`. D2
allows name substrings as a first-run SUGGESTION in the settings screen and nowhere else: a
brokerage that renames "Offer out" to "ICA sent" must not silently empty half the tab.
"""
from __future__ import annotations

import re

# Groups the product reasons about by name. A workspace may label the rest however it likes, but
# these five carry behaviour -- Signed is what "signed this month" counts, Met and Offer out are
# what the path is drawn from, Appointment set is the SDR's hand-off, Nurture is held out of the
# active pipeline -- so they are offered as the defaults and validated if present.
KNOWN_GROUPS = ("Sourced", "Appointment set", "Met", "Offer out", "Signed", "Nurture")
OWNER_ROLES = ("sdr", "team_leader")
SEAT_ROLES = ("team_leader", "sdr")

MAX_GROUPS = 12
MAX_STAGES_PER_GROUP = 25
MAX_SEATS = 25
LABEL_MAX = 48                      # recruiting_candidate.stage_group is String(48)
ID_MAX = 64
# E.164: a leading + and 8-15 digits. Anything else is refused rather than stored and discovered
# by a failed send three phases from now.
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")

# The keys Settings owns. Anything else on the config -- location_id, the token's own bookkeeping,
# the cached ghl_users list the sync writes -- is left exactly as it was found.
SETTING_KEYS = ("pipeline_id", "recruiting_stage_groups", "gci_field_id", "brokerage_field_id",
                "quiet_hours", "writeback_enabled", "dry_run", "auto_clear")

DEFAULTS = {
    "pipeline_id": None,
    "recruiting_stage_groups": [],
    "gci_field_id": None,
    "brokerage_field_id": None,
    # TCPA-motivated, and deliberately not configurable outside 08:00-21:00 (§5.5).
    "quiet_hours": {"start": 8, "end": 21},
    "writeback_enabled": False,
    "dry_run": True,
    "auto_clear": True,
}
QUIET_FLOOR, QUIET_CEILING = 8, 21


def _text(value, limit: int) -> str | None:
    if value is None:
        return None
    out = str(value).strip()
    return out[:limit] or None


def clean_settings(raw: dict | None, *, strict: bool = False) -> dict:
    """The settings half of the config, bounded.

    Raises ValueError(field) under `strict`, which the PUT uses; clamps otherwise, which reading
    a stored config uses.
    """
    def bad(field: str, why: str = ""):
        if strict:
            raise ValueError(f"{field}{': ' + why if why else ''}")
        return None

    raw = raw if isinstance(raw, dict) else {}
    out = {k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in DEFAULTS.items()}

    if "pipeline_id" in raw:
        out["pipeline_id"] = _text(raw.get("pipeline_id"), ID_MAX)
    for key in ("gci_field_id", "brokerage_field_id"):
        if key in raw:
            out[key] = _text(raw.get(key), ID_MAX)

    if "recruiting_stage_groups" in raw:
        rows = raw.get("recruiting_stage_groups")
        if not isinstance(rows, list):
            bad("recruiting_stage_groups", "must be a list")
            rows = []
        groups, seen_stage, seen_label = [], set(), set()
        for row in rows[:MAX_GROUPS]:
            try:
                label, owner_role, stage_ids = row[0], row[1], list(row[2] or [])
            except (TypeError, IndexError, KeyError):
                bad("recruiting_stage_groups", "each row is [label, owner_role, [stage_ids]]")
                continue
            label = _text(label, LABEL_MAX)
            if not label:
                bad("recruiting_stage_groups", "a group needs a label")
                continue
            if label.lower() in seen_label:
                bad("recruiting_stage_groups", f"duplicate group {label!r}")
                continue
            if owner_role not in OWNER_ROLES:
                bad("recruiting_stage_groups", f"owner_role must be one of {OWNER_ROLES}")
                owner_role = "team_leader"
            ids = []
            for sid in stage_ids[:MAX_STAGES_PER_GROUP]:
                sid = _text(sid, ID_MAX)
                if not sid:
                    continue
                # ONE group per stage. Two groups claiming a stage would make every count that
                # filters on group depend on dict ordering, which is a bug you find in a quarter.
                if sid in seen_stage:
                    bad("recruiting_stage_groups", f"stage {sid} is in two groups")
                    continue
                seen_stage.add(sid)
                ids.append(sid)
            seen_label.add(label.lower())
            groups.append([label, owner_role, ids])
        out["recruiting_stage_groups"] = groups

    if "quiet_hours" in raw:
        qh = raw.get("quiet_hours") or {}
        try:
            start, end = int(qh.get("start", QUIET_FLOOR)), int(qh.get("end", QUIET_CEILING))
        except (TypeError, ValueError):
            bad("quiet_hours", "start and end are hours, 0-23")
            start, end = QUIET_FLOOR, QUIET_CEILING
        # Clamped INWARD in both modes, deliberately. This is not a preference: a workspace does
        # not get to text recruits at 6am because it typed 6, and refusing the save outright
        # would leave a screen that cannot be submitted.
        out["quiet_hours"] = {"start": max(QUIET_FLOOR, min(start, QUIET_CEILING)),
                              "end": max(QUIET_FLOOR, min(end, QUIET_CEILING))}
        if out["quiet_hours"]["start"] >= out["quiet_hours"]["end"]:
            out["quiet_hours"] = dict(DEFAULTS["quiet_hours"])

    for key in ("writeback_enabled", "dry_run", "auto_clear"):
        if key in raw:
            out[key] = bool(raw.get(key))
    return out


def merge_settings(existing: dict | None, incoming: dict | None, *, strict: bool = True) -> dict:
    """The stored config with the Settings-owned keys replaced. Everything else survives.

    A PUT that replaced the whole config would drop `location_id` -- and the connection would
    still look connected while syncing nothing.
    """
    base = dict(existing or {})
    cleaned = clean_settings({**{k: base.get(k) for k in SETTING_KEYS if k in base},
                              **(incoming or {})}, strict=strict)
    base.update(cleaned)
    return base


def clean_seat(raw: dict | None, *, strict: bool = True) -> dict:
    """One roster row from the UI."""
    raw = raw if isinstance(raw, dict) else {}
    role = (raw.get("role") or "").strip().lower()
    if role not in SEAT_ROLES:
        raise ValueError(f"role must be one of {SEAT_ROLES}")
    name = _text(raw.get("display_name"), 160)
    if not name:
        raise ValueError("display_name is required")
    number = _text(raw.get("from_number"), 20)
    if number and not _E164.match(number):
        # Refused, not stored. A malformed sender number becomes a failed send in Phase 4, at
        # which point nobody remembers typing it.
        raise ValueError("from_number must be E.164, e.g. +18015550142")
    return {
        "role": role,
        "display_name": name,
        "title": _text(raw.get("title"), 160),
        "ghl_user_id": _text(raw.get("ghl_user_id"), ID_MAX),
        "calendar_id": _text(raw.get("calendar_id"), ID_MAX),
        "from_number": number,
        "writeback_enabled": bool(raw.get("writeback_enabled")),
        "active": bool(raw.get("active", True)),
    }


def suggest_groups(stages: list[dict]) -> list[list]:
    """A FIRST-RUN suggestion only, matched on stage NAME, offered for a person to correct.

    This is the one place name matching is allowed, and it never reaches stored config without
    somebody pressing Save -- which is the difference between a helpful default and the silent
    breakage D2 rules out.
    """
    hints = {
        "Sourced": ("source", "new", "lead", "prospect"),
        "Appointment set": ("appoint", "booked", "scheduled", "set"),
        "Met": ("met", "meeting", "interview", "consult"),
        "Offer out": ("offer", "ica", "agreement", "contract"),
        "Signed": ("signed", "won", "joined", "hired"),
        "Nurture": ("nurture", "later", "long", "cold"),
    }
    out = []
    claimed = set()
    for label in KNOWN_GROUPS:
        ids = []
        for st in stages or []:
            sid, sname = st.get("id"), (st.get("name") or "").lower()
            if not sid or sid in claimed:
                continue
            if any(h in sname for h in hints[label]):
                ids.append(sid)
                claimed.add(sid)
        owner = "sdr" if label in ("Sourced", "Appointment set") else "team_leader"
        out.append([label, owner, ids])
    return out


async def load_location_options(integ) -> dict:
    """What the settings screen offers to choose FROM, read live from the location.

    Live rather than cached, because of an order problem: the sync stops early until a pipeline
    is chosen, so nothing has ever cached the pipelines a person needs in order to choose one.
    This is an interactive screen being opened by an admin, not a background tick -- one read is
    the right cost, and a stale list here would have somebody picking a stage that no longer
    exists.

    Every block degrades on its own. A token missing `calendars.readonly` should cost the
    calendar picker and nothing else; returning an error for the whole screen would hide the
    pipeline list that does work.
    """
    from ..integrations import ghl
    from ..security import dec

    cfg = integ.config or {} if integ else {}
    location_id = cfg.get("location_id")
    token = dec(integ.access_token_enc) if integ and integ.access_token_enc else None
    out = {"pipelines": [], "calendars": [], "users": [], "custom_fields": [], "errors": {}}
    if not (token and location_id):
        out["errors"]["all"] = "No recruiting location is connected."
        return out

    try:
        pipes = await ghl.get_pipelines(token, location_id)
        out["pipelines"] = [{"id": p.get("id"), "name": p.get("name"),
                             "stages": [{"id": st.get("id"), "name": st.get("name")}
                                        for st in (p.get("stages") or [])]}
                            for p in pipes if p.get("id")]
    except Exception as exc:  # noqa: BLE001
        out["errors"]["pipelines"] = _scope_hint(exc, "opportunities.readonly")

    try:
        body = await ghl.ghl_json("GET", "/calendars/", token=token, location_id=location_id,
                                  params={"locationId": location_id})
        out["calendars"] = [{"id": c.get("id"), "name": c.get("name"),
                             "slot_minutes": c.get("slotDuration")}
                            for c in (body.get("calendars") or []) if c.get("id")]
        if not out["calendars"]:
            out["errors"]["calendars"] = "No calendars came back. Check calendars.readonly."
    except Exception as exc:  # noqa: BLE001
        out["errors"]["calendars"] = _scope_hint(exc, "calendars.readonly")

    try:
        out["users"] = [{"id": u.get("id"), "name": u.get("name"),
                         "email": (u.get("email") or "").strip().lower()}
                        for u in await ghl.get_users(token, location_id) if u.get("id")]
    except Exception as exc:  # noqa: BLE001
        out["errors"]["users"] = _scope_hint(exc, "users.readonly")

    try:
        fields = await ghl.get_custom_fields(token, location_id, model="opportunity")
        fields += await ghl.get_custom_fields(token, location_id)
        out["custom_fields"] = [{"id": f.get("id"), "name": f.get("name"),
                                 "data_type": f.get("dataType"), "model": f.get("model")}
                                for f in fields if f.get("id")]
    except Exception as exc:  # noqa: BLE001
        out["errors"]["custom_fields"] = _scope_hint(exc, "locations/customFields.readonly")
    return out


def _scope_hint(exc: Exception, scope: str) -> str:
    """A 403 means a scope, and saying which one is the difference between a fix and a ticket."""
    status = getattr(exc, "status", None)
    if status == 403:
        return (f"This connection is missing `{scope}`. An admin can add it to the GHL private "
                f"integration.")
    if status == 401:
        return "The GHL connection needs to be reconnected."
    return "GHL did not answer. Try again in a moment."
