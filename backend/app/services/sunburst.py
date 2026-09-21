"""The links into Sunburst.

Sunburst ships with Sisu and Sisu ships with this product's customers, so it is part of the
platform rather than a per-tenant integration -- there is nothing for an admin to configure, and
the first version of this asked them to paste a URL that nobody should ever have to find.

TWO LINKS, because Sisu has two and they do different jobs.

  THE CONVERSATION, `/app/sb/<32 characters>`, reopens one person's ongoing check-in. The code is
  ours to mint: Sisu opens a conversation for a code it has never seen and the same code reopens
  it on the next visit (tested on next.sisu.co, 2026-09-16), so last week's thread is still there
  as this week's context.

  THE QUESTION, `/app/sb/ask?input=...&autosend=...&view=...`, starts a NEW chat with the question
  already in it -- and with autosend, already asked. The portal adds the question itself rather
  than this module, because the Ask box sends whatever somebody typed and Sisu decodes with the
  counterpart of the browser's encodeURIComponent.

THE CODE IS DERIVED, NOT RANDOM, and not stored either:

  Random per click would start a fresh conversation every time somebody opened the page, which is
  the opposite of what a weekly check-in is for.

  Stored would be a column, a migration and a backfill for something that is a pure function of
  two ids we already have.

  HMAC rather than a plain hash because the code is the only thing standing between a URL and
  somebody's coaching conversation. `sha256(tenant:member)` would be reproducible by anyone who
  learned the scheme and could read an id; keyed with APP_SECRET it is not.

One consequence worth naming: rotating APP_SECRET changes everybody's code, and they would each
start a new Sunburst conversation. That is a nuisance rather than a loss -- the old thread still
exists at the old URL -- and it is the right trade against storing a column we would then have to
keep in step with the roster.

THE HOST IS A SETTING. Sisu ships to next.sisu.co first, on a different database, then to
app.sisu.co. SUNBURST_HOST points at next to try something and back at app for everyone.
SUNBURST_ASK_LINKS says whether that host has the question link: both have it now, and it exists so
a host that does not is never sent a prompt it cannot read.
"""
from __future__ import annotations

import hashlib
import hmac

from ..config import settings

CODE_LENGTH = 32


def _host() -> str:
    return (settings.SUNBURST_HOST or "https://app.sisu.co").strip().rstrip("/")


def code_for(tenant_id, member_id) -> str:
    """A stable, unguessable 32-character code for one person's Sunburst conversation."""
    message = f"{tenant_id}:{member_id}".encode()
    key = (settings.APP_SECRET or "").encode() or b"axcion-sunburst"
    return hmac.new(key, message, hashlib.sha256).hexdigest()[:CODE_LENGTH]


def link_for(tenant_id, member_id) -> str:
    """One person's ongoing Sunburst conversation."""
    return f"{_host()}/app/sb/{code_for(tenant_id, member_id)}"


def ask_url() -> str:
    """Where a question goes, or "" while the host does not have Sisu's question link.

    The portal appends `?input=...&autosend=true&view=fullscreen`. Empty rather than a URL that
    would not work: without it the page copies the question and opens the conversation instead,
    which works on every host.
    """
    return f"{_host()}/app/sb/ask" if settings.SUNBURST_ASK_LINKS else ""


def carries_prompt() -> bool:
    """Whether a click can bring the question with it: one click, rather than copy then paste."""
    return bool(ask_url())
