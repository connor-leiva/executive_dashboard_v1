"""Campaign grouping, thresholds, and banding. SPEC-ads-module.md Part 10.3.

Pure functions, no database, no I/O - so every rule here is testable without a fixture and
without a Meta token.

WHY GROUPING IS DATA. Sarah's dashboard classified campaigns with a naming heuristic compiled
into a regex, which means the moment somebody renames a campaign, spend silently moves between
groups and nobody is told. Same shape as Launch.stage_map, and the same answer: a code default
that reproduces today's behaviour exactly, a per-account override that is data, one pure
classifier, and anything unmatched SURFACED rather than swallowed.

MULTI-TENANT. Nothing here reads global state. Every function takes the rules or thresholds it
should use, and the caller resolves those from `ad_account.group_rules` / `.thresholds`, falling
back to these defaults when the account has configured nothing. A tenant that never opens the
settings still gets correct behaviour; a tenant that does gets theirs and nobody else's.
"""
from __future__ import annotations

import re

# The mockup's heuristic, preserved to the letter. `split: True` means the label carries the
# token after the prefix, so "KB-Webinar-Retarget" groups as "KB · Webinar" rather than
# collapsing every KB campaign into one bucket.
DEFAULT_GROUP_RULES: list[dict] = [
    {"match": "prefix", "value": "kb-", "label": "KB", "split": True},
    {"match": "contains", "value": "event name research", "label": "Event Name Research"},
    {"match": "contains", "value": "webinar", "label": "Webinar"},
]

FALLBACK_GROUP = "Other"


def _norm(s: str) -> str:
    """Separator-normalised, CASE PRESERVED. `KB - The Shift` becomes `KB-The Shift`.

    NOT cosmetic. The shipped default matches the prefix `kb-`, taken from the mockup where
    campaigns were named `KB-Webinar-Retarget`. The live account names them `KB - The Shift -
    August2026`, with spaces around the dash - so nothing matched, all nineteen campaigns fell to
    Other, and "Where it came from" became one undifferentiated bar. Two spaces were the whole
    difference.

    Whitespace AROUND a dash collapses into the dash; whitespace on its own does not. Losing that
    distinction would flatten `KB - The Shift` to four equal tokens and label the group "The"
    rather than "The Shift". En and em dashes count as dashes, because Meta's own campaign names
    contain them.
    """
    t = re.sub(r"[\s]*[-–—_][\s]*", "-", str(s or ""))
    return re.sub(r"\s+", " ", t).strip().strip("-")


def classify_campaign(name: str, rules: list[dict] | None = None) -> str:
    """Which group a campaign name belongs to. Pure, and deliberately boring.

    Returns FALLBACK_GROUP when nothing matches, and the read service COUNTS those rather than
    hiding them: a rising "Other" count is the leading indicator that the naming convention has
    drifted away from the rules, which is the moment before every group total quietly stops
    meaning what it used to.
    """
    if not name:
        return FALLBACK_GROUP
    n = _norm(name)
    low = n.lower()
    for r in rules if rules is not None else DEFAULT_GROUP_RULES:
        value = _norm(r.get("value")).lower()
        if not value:
            continue
        if r.get("match") == "prefix" and low.startswith(value):
            if not r.get("split"):
                return r.get("label") or FALLBACK_GROUP
            # The segment after the prefix, sliced from the CASE-PRESERVED form. Title-casing it
            # would render ForumVIP as "Forumvip" and BeCollective as "Becollective" - the ad
            # account's own capitalisation is how its operator recognises the thing.
            tail = [t for t in n[len(value):].split("-") if t.strip()]
            head = r.get("label") or FALLBACK_GROUP
            return f"{head} · {tail[0].strip()}" if tail else head
        if r.get("match") == "contains" and value in low:
            return r.get("label") or FALLBACK_GROUP
    return FALLBACK_GROUP


MATCH_KINDS = ("prefix", "contains")
MAX_RULES = 40


def validate_group_rules(rules) -> list[str]:
    """Problems with a proposed rule set, as plain sentences. Empty list means it is usable.

    The PATCH endpoint wrote whatever JSON it was handed straight onto the column. classify_campaign
    is defensive enough not to raise on nonsense, which is worse rather than better: a malformed
    rule set silently classifies everything as Other, and the failure looks exactly like a naming
    drift somebody would then go hunting for in Ads Manager.
    """
    if rules is None:
        return []                                   # null means "use the defaults", which is valid
    if not isinstance(rules, list):
        return ["Grouping rules must be a list."]
    if len(rules) > MAX_RULES:
        return [f"Too many rules ({len(rules)}); the limit is {MAX_RULES}."]

    problems: list[str] = []
    for i, r in enumerate(rules, 1):
        if not isinstance(r, dict):
            problems.append(f"Rule {i} is not an object.")
            continue
        if r.get("match") not in MATCH_KINDS:
            problems.append(f"Rule {i}: match must be one of {', '.join(MATCH_KINDS)}.")
        if not str(r.get("value") or "").strip():
            problems.append(f"Rule {i}: needs something to match on.")
        label = r.get("label")
        if label is not None and not isinstance(label, str):
            problems.append(f"Rule {i}: label must be text.")
        if isinstance(label, str) and len(label) > 80:
            problems.append(f"Rule {i}: label is too long (80 characters max).")
        if "split" in r and not isinstance(r["split"], bool):
            problems.append(f"Rule {i}: split must be true or false.")
    return problems


# Thresholds cover the funnel, not just the click layer - the point of the module is that a
# campaign can be excellent on every click metric and produce nothing.
#
# "direction" says which way is good: gte means higher is better (CTR, ROAS), lte means lower is
# better (CPM, CPL, CAC). Getting that backwards would paint a disaster green.
DEFAULT_THRESHOLDS: dict = {
    "ctr":  {"good": 1.0,    "warn": 0.5,    "direction": "gte"},   # percent
    "cpm":  {"good": 10.0,   "warn": 15.0,   "direction": "lte"},   # dollars
    "cpl":  {"good": 100.0,  "warn": 200.0,  "direction": "lte"},
    "cac":  {"good": 1200.0, "warn": 2000.0, "direction": "lte"},   # per enrollment
    "roas": {"good": 6.0,    "warn": 3.0,    "direction": "gte"},   # on CONTRACTED value
    "min_spending_campaigns": 3,
}

BANDS = ("good", "warn", "bad", "none")


def band(metric: str, value: float | None, thresholds: dict | None = None) -> str:
    """Which band a number sits in. The SERVER decides this; the client only colours it.

    None bands as "none", never as "bad". A missing number and a terrible number are different
    facts, and rendering "no closes yet" in the same red as "a catastrophic CAC" is how a
    dashboard teaches people to distrust it. rate() returns None on a zero denominator precisely
    so this distinction survives all the way to the pixel.
    """
    if value is None:
        return "none"
    cfg = (thresholds or DEFAULT_THRESHOLDS).get(metric)
    if not isinstance(cfg, dict):
        return "none"
    good, warn = cfg.get("good"), cfg.get("warn")
    if good is None or warn is None:
        return "none"
    if cfg.get("direction") == "lte":          # lower is better
        if value <= good:
            return "good"
        return "warn" if value <= warn else "bad"
    if value >= good:                          # higher is better
        return "good"
    return "warn" if value >= warn else "bad"


def group_campaigns(rows: list[dict], rules: list[dict] | None = None,
                    name_key: str = "name") -> tuple[dict[str, list[dict]], int]:
    """Group campaign rows and report how many fell through.

    Returns (groups, unmatched_count). The count is returned rather than logged because it
    belongs on the page: "N campaigns fell to Other" is a finding, not a diagnostic.
    """
    out: dict[str, list[dict]] = {}
    unmatched = 0
    for row in rows:
        g = classify_campaign(row.get(name_key) or "", rules)
        if g == FALLBACK_GROUP:
            unmatched += 1
        out.setdefault(g, []).append(row)
    return out, unmatched
