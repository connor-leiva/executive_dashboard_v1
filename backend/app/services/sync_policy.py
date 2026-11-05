"""Which sources may be pulled without a person asking.

Every other source syncs on the half-hourly tick. SkySlope's API terms (§3(n)) bar processes that run
while nobody is logged in, so until SkySlope approves polling in writing it syncs only when a signed-in
admin of that workspace asks for it.

ONE ANSWER, because the question has many askers. A flag on the job alone would not have held: `tick`
calls run_all, and run_all syncs every connected source. And a closed gate has to be known to whatever
watches freshness as well, or a source doing exactly what it should reads as "Auto-sync has missed its
last 40 runs" and moves its workspace to `watch` in the operator console. SKYSLOPE-SPEC.md §5.
"""
from __future__ import annotations

from ..config import settings

# Never swept up by run_all, whatever the flag says. When polling is allowed these run on their own
# job with their own timeout, because a first backfill can outlast a tenant's share of the tick and
# their rate limit is shared with every other workspace on this application.
OWN_SCHEDULE = frozenset({"skyslope"})


def is_manual(provider: str) -> bool:
    """True when this source runs only because a signed-in admin of the workspace asked."""
    return provider == "skyslope" and not settings.SKYSLOPE_SCHEDULED_SYNC
