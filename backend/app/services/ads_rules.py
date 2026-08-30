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

# The mockup's heuristic, preserved to the letter. `split: True` means the label carries the
# token after the prefix, so "KB-Webinar-Retarget" groups as "KB · Webinar" rather than
# collapsing every KB campaign into one bucket.
DEFAULT_GROUP_RULES: list[dict] = [
    {"match": "prefix", "value": "kb-", "label": "KB", "split": True},
    {"match": "contains", "value": "event name research", "label": "Event Name Research"},
    {"match": "contains", "value": "webinar", "label": "Webinar"},
]

FALLBACK_GROUP = "Other"


def classify_campaign(name: str, rules: list[dict] | None = None) -> str:
    """Which group a campaign name belongs to. Pure, and deliberately boring.

    Returns FALLBACK_GROUP when nothing matches, and the read service COUNTS those rather than
    hiding them: a rising "Other" count is the leading indicator that the naming convention has
    drifted away from the rules, which is the moment before every group total quietly stops
    meaning what it used to.
    """
    if not name:
        return FALLBACK_GROUP
    n = name.lower()
    for r in rules if rules is not None else DEFAULT_GROUP_RULES:
        value = str(r.get("value") or "").lower()
        if not value:
            continue
        if r.get("match") == "prefix" and n.startswith(value):
            if not r.get("split"):
                return r.get("label") or FALLBACK_GROUP
            tail = name[len(value):].replace("-", " ").split()
            head = r.get("label") or FALLBACK_GROUP
            return f"{head} · {tail[0]}" if tail else head
        if r.get("match") == "contains" and value in n:
            return r.get("label") or FALLBACK_GROUP
    return FALLBACK_GROUP


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
