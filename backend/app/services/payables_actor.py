"""Who is allowed to move money in Payables.

Its own module rather than a helper inside `payables.py`, because all three payables services
need it and `payables` already imports from `payables_vendor` — putting it in either would have
made the import graph a circle, and the usual way that gets resolved is a local import inside
the function, which is exactly how a guard ends up quietly skipped in one call path.

THE RULE: every mutating function in Payables takes an `actor`, and every one of them assumed it
was a person. Nothing checked. `decide(..., actor=None, ...)` took actor_id None, matched the
first UNASSIGNED approval slot on its very first lookup, claimed it, left approver_user_id NULL
and moved the bill to approved — an approved payment with nobody's name against it. It was never
reachable, because every caller happened to pass a User. The control was a habit of the call
sites, not a property of the code.

Phase 4 introduces a non-human caller for the first time. That is precisely what turns "never
reachable" into "reachable as soon as somebody wires it up", so the assumption is checked where
it is relied on. An AI employee may PROPOSE. It does not code, submit, approve, schedule, hold,
override, release or reconcile — and it cannot come to do any of those by accident, or by a
later refactor that forgets which caller was the careful one.
"""
from __future__ import annotations


class NotAHumanError(PermissionError):
    """Something with no name against it tried to move money."""


def require_human(actor, doing: str) -> None:
    """Refuse anything that is not a signed-in person.

    Checks for a real `id` — a None actor, a bare sentinel, or anything whose id never got set —
    and, where the object declares one, that its `actor_type` is "user". `audit()` already uses
    that vocabulary for machine-written rows ("system", "integration"), so an object honest
    enough to describe itself as a machine is refused on its own word.
    """
    if getattr(actor, "id", None) is None:
        raise NotAHumanError(
            f"{doing} has to be done by a person — this actor has no name against it")
    if getattr(actor, "actor_type", "user") != "user":
        raise NotAHumanError(f"{doing} has to be done by a person — not by {actor.actor_type}")
