"""A roster entry becomes Active the first time its person signs in.

A console invite creates two records: an account, which is what signs in, and a roster entry,
which carries the portal role. The account turned active the moment the person accepted -- by the
emailed link, a password reset, or simply signing in with Google -- while the roster entry stayed
"Invited" until an admin went back and edited it by hand. The consequences were nowhere near the
invite: an agent using the portal every day was missing from Who's Who, which lists Active people
only, and an invited co-admin could not open the console, which requires an Active member.

INVITED BECOMES ACTIVE, AND NOTHING ELSE MOVES. A Removed entry is somebody an admin took off the
roster, and signing in -- perhaps with an account that still exists for the dashboard -- must not
put them back. Only an admin restores a removed member.

Called from every route that ends in a new session for a person proving who they are: password
login, Google sign-in, accepting an invite, and a password reset. Changing a password is not among
them, because that person is already signed in -- one of the four has run.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import IntranetMember, User
from .audit import audit


async def activate_on_sign_in(s: AsyncSession, user: User, *, via: str) -> int:
    """Mark this person's Invited roster entry Active, and return how many entries changed.

    Found by the linked account, or -- for an entry added before its account existed -- by the
    same address in the same workspace, which is then linked while we are here. An entry linked
    to a DIFFERENT account is never matched on address alone: that is somebody else's seat, and a
    sign-in must not activate it. The caller commits.
    """
    email = (user.email or "").strip().lower()
    rows = (await s.execute(select(IntranetMember).where(
        IntranetMember.tenant_id == user.tenant_id,
        IntranetMember.status == "Invited",
        or_(IntranetMember.user_id == user.id,
            and_(IntranetMember.user_id.is_(None), IntranetMember.email == email)),
    ))).scalars().all()
    now = dt.datetime.now(dt.timezone.utc)
    for member in rows:
        member.status = "Active"
        member.activated_at = member.activated_at or now
        member.user_id = user.id
        audit(s, user.tenant_id, user.id, "access.member.activated", "member", member.id,
              {"via": via})
    return len(rows)
