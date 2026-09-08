"""The link into Sunburst.

Sunburst ships with Sisu and Sisu ships with this product's customers, so it is part of the
platform rather than a per-tenant integration -- there is nothing for an admin to configure, and
the first version of this asked them to paste a URL that nobody should ever have to find.

THE CODE IS OURS TO MINT. Sisu's link is `app.sisu.co/app/sb/<32 characters>` and the code
identifies the conversation, not the customer, so any code opens a Sunburst session ready to type.

DERIVED, NOT RANDOM, and not stored either:

  Random per click would start a fresh conversation every time somebody opened the page, which is
  the opposite of what a weekly check-in is for -- last week's thread is the context this week's
  builds on.

  Stored would be a column, a migration and a backfill for something that is a pure function of
  two ids we already have.

  HMAC rather than a plain hash because the code is the only thing standing between a URL and
  somebody's coaching conversation. `sha256(tenant:member)` would be reproducible by anyone who
  learned the scheme and could read an id; keyed with APP_SECRET it is not.

One consequence worth naming: rotating APP_SECRET changes everybody's code, and they would each
start a new Sunburst conversation. That is a nuisance rather than a loss -- the old thread still
exists at the old URL -- and it is the right trade against storing a column we would then have to
keep in step with the roster.
"""
from __future__ import annotations

import hashlib
import hmac
from urllib.parse import quote

from ..config import settings

BASE = "https://app.sisu.co/app/sb/"
CODE_LENGTH = 32

# Sisu's link opens an empty box today. When they ship one that carries a question -- Connor has
# asked; they already have three link types and this would be a fourth -- setting this to the
# parameter they use turns every prompt card into one click. It is a constant rather than a tenant
# setting because it is a fact about Sisu's product, the same for every workspace on the platform.
PROMPT_PARAM = ""


def code_for(tenant_id, member_id) -> str:
    """A stable, unguessable 32-character code for one person's Sunburst conversation."""
    message = f"{tenant_id}:{member_id}".encode()
    key = (settings.APP_SECRET or "").encode() or b"acumyn-sunburst"
    return hmac.new(key, message, hashlib.sha256).hexdigest()[:CODE_LENGTH]


def link_for(tenant_id, member_id, prompt: str = "") -> str:
    """The URL to open. `prompt` is carried only once Sisu supports it -- until then it is dropped
    here rather than appended and ignored, because a URL with a parameter the far end throws away
    looks like it worked."""
    url = BASE + code_for(tenant_id, member_id)
    if prompt and PROMPT_PARAM:
        return f"{url}?{PROMPT_PARAM}={quote(prompt)}"
    return url


def carries_prompt() -> bool:
    """Whether a link can bring the question with it. The portal copies to the clipboard when it
    cannot, so this is what decides between one click and two."""
    return bool(PROMPT_PARAM)
