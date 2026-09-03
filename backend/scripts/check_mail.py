"""Ask Resend whether this service's email configuration actually works.

    railway ssh --service executive_dashboard_v1 "python -m scripts.check_mail"
    railway ssh --service worker "python -m scripts.check_mail you@example.com"

Run it in the SERVICE you are asking about. Invites send from the api; Binder digests send from
whichever process runs the scheduler, which is the api itself when RUN_WORKER_IN_API is on and a
separate `worker` service otherwise. On a split deployment those are two environments and "it
works" on one says nothing about the other, so this reports which case it is looking at.

With no argument it inspects the configuration and stops. Give it a recipient and it sends one
real email and prints Resend's exact answer — which is the only way to tell a wrong key from a
wrong FROM domain from a network the service cannot reach.

Never prints the key. It reports length, prefix and shape, because those are what distinguish
"not set" from "truncated on paste" from "genuinely wrong", and none of them require the value.

Exit codes: 0 = configured (and sent, if asked), 1 = something is wrong.
"""
from __future__ import annotations

import asyncio
import sys

from app.config import settings
from app.services import mailer

# What Resend's own errors mean, in terms of what to go and change. The API returns these as
# prose, and prose is exactly what gets skimmed past at the end of a deploy.
_HINTS = {
    "API key is invalid":
        "The key is not one Resend recognises. Resend shows a key ONCE at creation, so the\n"
        "     usual cause is a partial paste. Reissue at resend.com/api-keys and set it again.\n"
        "     Check it belongs to the same Resend team as the verified domain.",
    "not allowed to send from":
        "The key is fine; the FROM domain is not verified for it. Verify the domain in\n"
        "     Resend and make MAIL_FROM an address on that exact domain.",
    "Invalid `from`":
        "MAIL_FROM is malformed. It wants either mail@acumyn.io or Acumyn <mail@acumyn.io>.",
}


def _shape(name: str, value: str) -> list[str]:
    """Describe a secret well enough to debug it, without disclosing it."""
    raw = value or ""
    stripped = raw.strip()
    out = []
    if not stripped:
        out.append(f"  {name:<16} NOT SET")
        return out
    out.append(f"  {name:<16} set, {len(stripped)} chars, starts {stripped[:3]!r}")
    if raw != stripped:
        # The failure this script exists to make visible: the value LOOKS right in the Railway
        # UI, which trims for display, and the header built from it is malformed.
        out.append(f"  {'':<16} ^ has surrounding whitespace — the app strips it, but the "
                   f"variable should not have it")
    if stripped[0] in "'\"" and stripped[-1] == stripped[0]:
        out.append(f"  {'':<16} ^ WRAPPED IN QUOTES. The quotes are part of the value and will "
                   f"be sent as part of the key.")
    return out


def _explain(body: str) -> str | None:
    for needle, hint in _HINTS.items():
        if needle.lower() in body.lower():
            return hint
    return None


async def _send(to: str) -> int:
    subject = "Acumyn mail check"
    ok = await mailer.send(
        to, subject,
        "<p>If you are reading this, transactional email works from this service.</p>",
        "If you are reading this, transactional email works from this service.")
    if ok:
        print(f"\n  [ok] Resend accepted a message to {to}.")
        print("       ACCEPTED IS NOT DELIVERED. Check resend.com/emails for the delivery event,")
        print("       and check the inbox itself — the first sends from a new domain are the")
        print("       ones most likely to be filtered.\n")
        return 0

    # send() swallows the detail by design (it must never raise into a request), so repeat the
    # call here through the seam to show what Resend actually said.
    status, body = await mailer._post(
        {"from": settings.MAIL_FROM.strip(), "to": [to], "subject": subject,
         "html": "<p>check</p>", "text": "check"},
        {"Authorization": f"Bearer {mailer.api_key()}"})
    print(f"\n  [FAIL] Resend answered {status}:")
    print(f"         {body[:500]}")
    hint = _explain(body)
    if hint:
        print(f"\n  ->   {hint}")
    print()
    return 1


def _digest_home() -> list[str]:
    """Where Binder digests send from, which is not always where invites send from.

    The scheduler lives either inside this api process (RUN_WORKER_IN_API) or in a separate
    `worker` service. Only the first case is covered by the variables printed above, and there
    is no way to tell from the api's own environment whether a worker exists — so this states
    what is true HERE and names what to go and check when it isn't.
    """
    if settings.RUN_WORKER_IN_API:
        return ["  scheduler        in this process (RUN_WORKER_IN_API=true), so Binder digests",
                "                   send from here too and these variables cover every send"]
    return ["  scheduler        NOT in this process (RUN_WORKER_IN_API is off)",
            "                   Binder digests send from wherever the scheduler runs. If that is",
            "                   a separate `worker` service, run this there as well. If there is",
            "                   no worker either, NOTHING scheduled runs — syncs included."]


def main() -> int:
    key = mailer.api_key()
    print()
    for line in _shape("RESEND_API_KEY", settings.RESEND_API_KEY):
        print(line)
    print(f"  {'MAIL_FROM':<16} {settings.MAIL_FROM.strip()!r}")
    print(f"  {'MAIL_REPLY_TO':<16} {settings.MAIL_REPLY_TO.strip() or '(none)'}")
    for line in _digest_home():
        print(line)
    print()

    if not key:
        print("  [FAIL] No key in THIS service's environment, so nothing here sends mail.\n")
        return 1
    if not key.startswith("re_"):
        print("  [FAIL] Resend keys begin with 're_'. This value is something else.\n")
        return 1

    to = sys.argv[1] if len(sys.argv) > 1 else None
    if not to:
        print("  [ok] A key is present and looks like a Resend key. That is as far as")
        print("       inspection goes — only a send proves it. Pass an address:")
        print("         python -m scripts.check_mail you@example.com\n")
        return 0
    return asyncio.run(_send(to))


if __name__ == "__main__":
    sys.exit(main())
