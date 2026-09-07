"""Getting a marketing request out of the building.

WHY DELIVERY IS QUEUED AND NOT INLINE. Submitting a request could POST to the destination before
answering the agent, and that would be a minute faster. It would also mean any tenant admin can
point the webhook at a URL that accepts a connection and never answers, and from then on every
agent who files a request ties up a server worker until the timeout -- a denial of service on our
own API, configured through a text box, by a customer who need not even be malicious to do it. The
queue removes that: the POST writes a row and returns, and nothing user-facing ever waits on a
third party.

The second reason matters more over time. First attempt and retry are the SAME code path here,
which means the retry path runs on every single request rather than being a rarely-exercised
branch that quietly rots until the day something is actually down.

WHAT A FAILURE IS ALLOWED TO DO: nothing, to anyone. The request is already saved, and the record
is the product -- delivery is how it gets noticed sooner. So this module never raises into a
caller and never leaves a request looking delivered when it is not.
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import logging
import socket
from typing import NamedTuple
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (IntranetIntegration, IntranetMarketingAttachment, IntranetMarketingRequest,
                      IntranetMarketingSetting)
from ..security import dec
from . import mailer

log = logging.getLogger("app")

SLACK_PROVIDER = "slack"
SLACK_API = "https://slack.com/api/chat.postMessage"
TIMEOUT = 10.0

# 1m, 5m, 25m, 2h, 10h after the first failure, then done. Short enough at the front to ride out a
# provider blip, long enough at the back that a destination taken down for a working day still
# catches the request when it comes back -- and finite, because a URL that has been wrong for
# thirteen hours is wrong, not busy.
BACKOFF = (60, 300, 1500, 7200, 36000)
MAX_ATTEMPTS = len(BACKOFF) + 1


class Outcome(NamedTuple):
    """What one attempt did, and whether waiting could change it.

    `retryable` travels WITH the message rather than being re-derived from it. A sender knows the
    difference between a receiver that is down and a URL that is wrong; nothing downstream can
    recover that from the prose, and a version of this that tried got it wrong.
    """
    delivered: bool
    detail: str
    retryable: bool


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---- where a webhook is allowed to point ------------------------------------------------
# This URL is typed by a tenant admin and POSTed to by our server, which is server-side request
# forgery surface by construction: the request leaves from inside the network, so it reaches
# everything a browser cannot -- the cloud metadata endpoint, the private network our database
# sits on, our own API on localhost.
#
# Resolving the name and refusing private answers is the substance of the defence, and it also
# disposes of the encodings people reach for (http://2130706433/, http://[::ffff:127.0.0.1]/, a
# public name deliberately pointed at 127.0.0.1) because all of them are checked AFTER resolution,
# as addresses rather than as text.
#
# The gap that leaves is rebinding: resolution and connection are two separate lookups, and a
# hostile DNS server can answer them differently. What closes that here is the https requirement,
# which the console enforces when the URL is saved and this module enforces again. A rebound
# connection to 169.254.169.254, or to our own Postgres, still has to complete a TLS handshake for
# the attacker's hostname, and an internal service has no certificate for it -- so the handshake
# fails and the POST never happens. https is doing security work here, not hygiene.
#
# Redirects are off for the same reason the addresses are checked: a 302 names a destination
# nobody validated. A webhook receiver that redirects is broken anyway.
def _blocked(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True                      # unparseable is not a thing we connect to
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped          # ::ffff:127.0.0.1 is loopback wearing a costume
    return (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
            or addr.is_multicast or addr.is_unspecified)


def _resolve(host: str, port: int) -> set[str]:
    """Every address this name answers with. A seam, like _post: DNS is a network call, and a
    test that has to reach a real resolver to check a rule about addresses is testing the
    internet."""
    return {i[4][0] for i in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)}


def check_webhook_target(url: str) -> str | None:
    """None if the URL is safe to POST to, else the reason it is not."""
    parts = urlsplit(url or "")
    if parts.scheme != "https":
        return "Webhook URLs must start with https://."
    try:
        host = parts.hostname
        port = parts.port or 443
    except ValueError:
        return "That webhook URL is not valid."
    if not host:
        return "That webhook URL has no host."
    try:
        addresses = _resolve(host, port)
    except socket.gaierror:
        return host + " could not be resolved."
    if not addresses:
        return host + " could not be resolved."
    # EVERY answer must be public, not merely one of them: a name resolving to a public address
    # and a private one is the private one whenever the connection happens to pick it.
    bad = sorted(a for a in addresses if _blocked(a))
    if bad:
        return host + " resolves to a private address (" + bad[0] + "), which cannot be reached."
    return None


# ---- what the destination is told --------------------------------------------------------

def _lines(row: IntranetMarketingRequest, attachments: int, link: str | None) -> list[str]:
    """The request as a person reads it, in the order they need it."""
    out = ["*" + row.title + "*", "From " + row.requester_label]
    for label, value in (("Type", row.request_type), ("Listing", row.listing),
                         ("Client", row.client)):
        if value:
            out.append(label + ": " + value)
    out.append("Priority: " + (row.priority or "Normal"))
    if row.due_date:
        out.append("Due: " + row.due_date.isoformat())
    if row.description:
        out.append("")
        out.append(row.description)
    if attachments:
        # Named rather than sent. The files sit behind the workspace's own auth and a Slack
        # channel does not, so the message says they exist and where to open them; attaching them
        # would republish an unlisted client's photographs to whoever is in that channel.
        plural = "s" if attachments != 1 else ""
        out.append("")
        out.append(str(attachments) + " attachment" + plural
                   + " -- open the request to download.")
    if link:
        out.append("")
        out.append(link)
    return out


def payload(row: IntranetMarketingRequest, attachments: int, link: str | None) -> dict:
    """The webhook body. Fields, not prose: a receiver automating on this should not have to
    parse a human summary out of a string. `text` rides along so a receiver that only forwards
    into a chat still reads properly."""
    return {
        "event": "marketing_request.created",
        "request": {
            "id": str(row.id),
            "title": row.title,
            "requester": row.requester_label,
            "request_type": row.request_type,
            "listing": row.listing,
            "client": row.client,
            "description": row.description,
            "due_date": row.due_date.isoformat() if row.due_date else None,
            "priority": row.priority,
            "status": row.status,
            "attachments": attachments,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "url": link,
        },
        "text": "\n".join(_lines(row, attachments, link)),
    }


# ---- the three ways out ------------------------------------------------------------------

async def _post(url: str, json: dict, headers: dict | None = None) -> tuple[int, str]:
    """The network seam for both Slack and webhooks. Tests monkeypatch THIS, so no test in the
    suite ever reaches the internet."""
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as c:
        r = await c.post(url, json=json, headers=headers or {})
    return r.status_code, r.text[:500]


async def _slack_token(s: AsyncSession, tenant_id) -> str | None:
    row = (await s.execute(select(IntranetIntegration).where(
        IntranetIntegration.tenant_id == tenant_id,
        IntranetIntegration.provider_key == SLACK_PROVIDER))).scalar_one_or_none()
    if row is None or not row.credential_ref:
        return None
    try:
        return dec(row.credential_ref)
    except Exception:                          # noqa: BLE001 - a rotated key, say
        log.warning("slack token for tenant %s could not be decrypted", tenant_id)
        return None


async def _send_slack(s: AsyncSession, tenant_id, channel: str, body: dict) -> Outcome:
    token = await _slack_token(s, tenant_id)
    if not token:
        # Retryable: somebody connecting Slack five minutes from now is the ordinary case, and
        # the request should go out when they do rather than having been abandoned.
        return Outcome(False, "Slack is not connected. Add the bot token on the integrations "
                              "page.", True)
    status, text = await _post(
        SLACK_API, {"channel": channel, "text": body["text"], "mrkdwn": True},
        {"Authorization": "Bearer " + token})
    if status >= 300:
        return Outcome(False, "Slack answered " + str(status) + ".", True)
    # SLACK ANSWERS 200 WHEN IT REFUSES. channel_not_found, not_in_channel and invalid_auth all
    # arrive as a perfectly successful HTTP response carrying ok:false, so a status-code check
    # alone would record every one of them as delivered.
    if '"ok":true' not in text.replace(" ", ""):
        reason = text.split('"error":"', 1)[-1].split('"', 1)[0] if '"error"' in text else text
        # invalid_auth is a revoked or mistyped token and waiting does not fix it. The others --
        # not_in_channel above all -- are fixed by inviting the bot, which is exactly the kind of
        # thing somebody does a few minutes after seeing the failure.
        return Outcome(False, "Slack refused: " + reason, reason != "invalid_auth")
    return Outcome(True, "Posted to " + channel, False)


async def _send_email(to: str, row: IntranetMarketingRequest, body: dict) -> Outcome:
    plain = body["text"].replace("*", "")
    html = "<p>" + plain.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
    ok = await mailer.send(
        to, "Marketing request: " + row.title, html, plain,
        # One request, one email, however many times the queue retries it. A retry after a
        # timeout whose answer we never saw is the exact case that double-sends.
        idempotency_key="marketing-request-" + str(row.id))
    if ok:
        return Outcome(True, "Emailed " + to, False)
    return Outcome(False, "Email to " + to + " was not accepted.", True)


async def _send_webhook(url: str, body: dict) -> Outcome:
    problem = check_webhook_target(url)
    if problem:
        # A name that will not resolve today may resolve in an hour -- DNS fails transiently and
        # a receiver being redeployed is the common case. A name that resolves to a private
        # address, or a URL that is not https, is a configuration mistake: it will be exactly as
        # wrong in ten hours, and retrying it just means ten more resolutions of the same name.
        return Outcome(False, problem, "could not be resolved" in problem)
    status, text = await _post(url, body)
    if status >= 300:
        return Outcome(False, str(urlsplit(url).hostname) + " answered " + str(status) + ".", True)
    return Outcome(True, "Posted to " + str(urlsplit(url).hostname), False)


async def send_one(s: AsyncSession, row: IntranetMarketingRequest,
                   cfg: IntranetMarketingSetting | None, link: str | None) -> Outcome:
    """One delivery attempt. Never raises."""
    attachments = (await s.execute(select(IntranetMarketingAttachment).where(
        IntranetMarketingAttachment.request_id == row.id))).scalars().all()
    body = payload(row, len(attachments), link)
    destination = ((cfg.destination if cfg else None) or "").strip()
    try:
        if cfg is None or not cfg.enabled or cfg.destination_type == "none" or not destination:
            # Switched off after the request was filed. Not worth retrying -- there is nowhere to
            # retry TO -- and if somebody switches it back on, the console can requeue.
            return Outcome(False, "No destination is configured for this workspace.", False)
        if cfg.destination_type == "slack":
            return await _send_slack(s, row.tenant_id, destination, body)
        if cfg.destination_type == "email":
            return await _send_email(destination, row, body)
        if cfg.destination_type == "webhook":
            return await _send_webhook(destination, body)
        return Outcome(False, "Unknown destination type " + repr(cfg.destination_type) + ".",
                       False)
    except Exception as e:                     # noqa: BLE001 - see the module docstring
        # An unexpected exception is a timeout, a reset connection or a TLS failure far more
        # often than it is a bug, so it retries.
        log.warning("marketing delivery error request=%s: %s", row.id, e)
        return Outcome(False, (type(e).__name__ + ": " + str(e))[:300], True)


def record(row: IntranetMarketingRequest, outcome: Outcome) -> None:
    """Write the outcome of one attempt onto the request. The only place these columns move.

    Whether to retry is the SENDER's answer, carried on the outcome, rather than something
    inferred here from the wording of the message. The first version of this matched prefixes of
    the detail string, which meant the retry decision and the text a person reads were two facts
    that had to agree -- and they did not: "resolves to a private address" fell through the
    prefixes and got scheduled for five more attempts at a URL that could never work.
    """
    row.delivery_attempts = (row.delivery_attempts or 0) + 1
    row.delivery_detail = outcome.detail[:1000]
    if outcome.delivered:
        row.delivered_at = _now()
        row.delivery_next_attempt_at = None
        return
    n = row.delivery_attempts
    if not outcome.retryable or n >= MAX_ATTEMPTS:
        row.delivery_next_attempt_at = None       # tried, and there is nothing left to try
    else:
        row.delivery_next_attempt_at = _now() + dt.timedelta(seconds=BACKOFF[n - 1])
