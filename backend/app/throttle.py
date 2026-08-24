"""Rate limiting for the unauthenticated surface.

Why this exists now and not before. Tenancy resolves from a client-supplied header on ONE
shared API origin (see tenancy.request_tenant_host), so every tenant's login, invite-accept and
reset endpoints are reachable from a single host by anyone. The per-user lockout in auth.py
covers a sustained attack on ONE account; it does nothing about the shape that shared origin
invites — nine guesses each across a thousand known corporate addresses, which never trips a
ten-strike counter on any of them and, until recently, wrote no audit row either.

Deliberately in-process, with eyes open. Two API replicas mean an attacker gets 2x the budget,
and a restart clears the counters. That is a real limitation and it is still worth having: the
alternative is a Redis dependency this project does not otherwise need, and a limiter that
raises the cost of a spray by an order of magnitude is the difference between a feasible attack
and an infeasible one. The correct home for a shared limiter is the reverse proxy; this is the
floor, not the ceiling. If a second replica is ever added for the API, revisit.

Not applied to authenticated routes. Those already carry a session bound to a tenant, and a
per-request budget there is a quota question (Phase 4), not an abuse one.
"""
from __future__ import annotations

import time
from collections import deque

from fastapi import HTTPException, Request

# (max hits, window seconds) per bucket. Login is the tightest: a human types one password at a
# time, so anything above a handful a minute from one address is not a person. The token
# endpoints are looser because a legitimate user may retry a bad link, but bounded because both
# consume a guessable-length secret.
RULES: dict[str, tuple[int, int]] = {
    # Deliberately LOOSER than auth.LOCK_THRESHOLD (10). If the limiter fired first, the
    # per-account lockout would be unreachable from a single address — the two mechanisms guard
    # different things and both need to work: the lockout protects one targeted account, this
    # caps volume across many. It also keeps the clearer 423 "account locked" answer reachable
    # for a real person who mistyped, instead of masking it with a generic 429.
    "login": (20, 60),
    "token": (20, 300),        # accept-invite / reset-password
    "ingest": (60, 60),        # the email provider's webhook — legitimate bursts are possible
}

# path prefix -> rule name. Checked longest-first so a more specific prefix wins.
PATHS: dict[str, str] = {
    "/api/v1/auth/login": "login",
    "/api/v1/auth/accept-invite": "token",
    "/api/v1/auth/reset-password": "token",
    "/api/v1/binder/ingest": "ingest",
}

_hits: dict[tuple[str, str, str], deque] = {}
# A cap on the number of tracked buckets, so the limiter cannot itself become the memory
# exhaustion it is meant to prevent. Evicting the oldest bucket is safe: it can only ever
# forgive, never block a legitimate caller.
MAX_BUCKETS = 20_000


def client_ip(request: Request) -> str:
    """The caller's address, honouring one hop of X-Forwarded-For.

    Railway terminates TLS and proxies, so request.client.host is the proxy. Only the FIRST
    entry is read and only one hop is trusted — reading the whole chain would let a caller
    prepend anything it liked and rotate its own identity at will.
    """
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def rule_for(path: str) -> str | None:
    for prefix in sorted(PATHS, key=len, reverse=True):
        if path.startswith(prefix):
            return PATHS[prefix]
    return None


def check(rule: str, ip: str, tenant_host: str, now: float | None = None) -> int | None:
    """Record a hit. Returns None if allowed, or the seconds to wait if over the limit.

    Keyed on (rule, ip, tenant_host) so pointing at a different tenant realm does not reset the
    budget for that address, and one noisy tenant cannot exhaust another's allowance.
    """
    limit, window = RULES[rule]
    now = now if now is not None else time.monotonic()
    key = (rule, ip, tenant_host)
    q = _hits.get(key)
    if q is None:
        if len(_hits) >= MAX_BUCKETS:
            _hits.pop(next(iter(_hits)), None)
        q = _hits[key] = deque()
    cutoff = now - window
    while q and q[0] <= cutoff:
        q.popleft()
    if len(q) >= limit:
        return max(1, int(q[0] + window - now) + 1)
    q.append(now)
    return None


def reset() -> None:
    """Clear all counters. For tests — the suite makes far more than 10 login calls."""
    _hits.clear()


async def enforce(request: Request) -> None:
    """Raise 429 when the caller is over the limit for this path. No-op for unmatched paths."""
    rule = rule_for(request.url.path)
    if rule is None:
        return
    from .tenancy import request_tenant_host

    retry = check(rule, client_ip(request), request_tenant_host(request))
    if retry is None:
        return
    # 429 with Retry-After, and a message that says what happened without confirming anything
    # about the account or tenant that was targeted.
    raise HTTPException(429, "Too many attempts. Try again shortly.",
                        headers={"Retry-After": str(retry)})
