"""Email copy for the transactional sends.

Plain and short on purpose. An invite that reads like marketing is an invite that lands in
Promotions, and this is the email that hands somebody their credentials.

Copy lives here rather than in the routers so that changing a word never touches request
handling. Each builder returns (subject, html, text) — positionally compatible with
``mailer.send(to, *template(...))``.
"""
from __future__ import annotations

import html as _html

_WRAP = """<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;
font-size:15px;line-height:1.6;color:#333730;max-width:520px">
{body}
<p style="margin-top:28px"><a href="{url}" style="background:#16201F;color:#ffffff;
text-decoration:none;padding:12px 22px;border-radius:999px;display:inline-block;
font-weight:600">{cta}</a></p>
<p style="color:#5F645C;font-size:13px;margin-top:24px">
Or paste this into your browser:<br>{url}</p>
<p style="color:#868B82;font-size:12px;margin-top:20px">{footer}</p></div>"""


def _esc(v: str | None) -> str:
    """Names and workspace names are user-supplied and land in HTML. Escape them."""
    return _html.escape(v or "", quote=True)


def invite(url: str, inviter: str | None, workspace: str, days: int = 7):
    who = f"{_esc(inviter)} has invited you" if inviter else "You have been invited"
    ws = _esc(workspace)
    body = (f"<p>{who} to the <strong>{ws}</strong> workspace on Axcion.</p>"
            "<p>Setting a password takes about a minute.</p>")
    foot = f"This link expires in {days} days. If you weren't expecting it, ignore this email."
    text = f"{inviter or 'You have been invited'} — {workspace} on Axcion.\n\n{url}\n\n{foot}"
    subject = f"{inviter} invited you to {workspace}" if inviter else f"You're invited to {workspace}"
    return subject, _WRAP.format(body=body, url=url, cta="Accept invite", footer=foot), text


def reset(url: str, workspace: str, hours: int = 24):
    ws = _esc(workspace)
    body = (f"<p>Someone asked to reset the password on your <strong>{ws}</strong> "
            "Axcion account.</p>")
    foot = (f"This link expires in {hours} hours. If you didn't ask for it you can ignore "
            "this — your password has not changed.")
    return ("Reset your Axcion password",
            _WRAP.format(body=body, url=url, cta="Set a new password", footer=foot),
            f"Reset your Axcion password for {workspace}.\n\n{url}\n\n{foot}")


def owner_invite(url: str, workspace: str, days: int = 7):
    ws = _esc(workspace)
    body = (f"<p>Your Axcion workspace <strong>{ws}</strong> is ready.</p>"
            "<p>This link makes you the owner — set a password, then connect your first "
            "system.</p>")
    foot = f"This link expires in {days} days."
    return ("Your Axcion workspace is ready",
            _WRAP.format(body=body, url=url, cta="Set up your workspace", footer=foot),
            f"Your Axcion workspace {workspace} is ready.\n\n{url}\n\n{foot}")


# Sent by Axcion support from the operator console, to a workspace's owners and admins. Neither
# carries a token: the link is the workspace's own Settings page and the reader signs in as
# themselves, so forwarding the email hands nobody a way in.
_SUPPORT_FOOT = ("Sent by Axcion support. You are receiving this because you are an owner or "
                 "admin of this workspace.")


def reconnect_source(url: str, workspace: str, provider: str, business: str | None = None):
    ws, name = _esc(workspace), _esc(provider)
    which = f"{name} for {_esc(business)}" if business else name
    body = (f"<p>{which} has stopped syncing to the <strong>{ws}</strong> workspace on Axcion, so "
            "the figures it feeds are no longer updating.</p>"
            f"<p>Sign in, open Settings, then Integrations, and connect {name} again. It has to be "
            f"someone who can sign in to {name}: Axcion cannot authorise it for you.</p>")
    plain_which = f"{provider} for {business}" if business else provider
    return (f"Reconnect {provider} to {workspace}",
            _WRAP.format(body=body, url=url, cta="Open integrations", footer=_SUPPORT_FOOT),
            f"{plain_which} has stopped syncing to {workspace} on Axcion. Sign in and connect it "
            f"again from Settings, Integrations:\n\n{url}\n\n{_SUPPORT_FOOT}")


def connect_first_source(url: str, workspace: str):
    ws = _esc(workspace)
    body = (f"<p>Nothing is connected to the <strong>{ws}</strong> workspace on Axcion yet, so "
            "every panel in it is empty.</p>"
            "<p>Sign in, open Settings, then Integrations, and connect the first system your team "
            "works in.</p>")
    return (f"Connect your first system to {workspace}",
            _WRAP.format(body=body, url=url, cta="Open integrations", footer=_SUPPORT_FOOT),
            f"Nothing is connected to {workspace} on Axcion yet. Sign in and connect your first "
            f"system from Settings, Integrations:\n\n{url}\n\n{_SUPPORT_FOOT}")


def payment_link(url: str, workspace: str, amount_cents: int | None, currency: str | None = "usd"):
    """Stripe's own hosted page for an open invoice. The link is Stripe's, so paying never passes a
    card detail through Axcion."""
    ws = _esc(workspace)
    amount = (f"${amount_cents / 100:,.2f}" if (currency or "usd").lower() == "usd" and amount_cents is not None
              else "the amount due")
    body = (f"<p>There is an unpaid invoice for {amount} for the <strong>{ws}</strong> workspace on "
            "Axcion.</p><p>Stripe's page below lets you pay it or update the payment method on file.</p>")
    foot = "Payments are handled by Stripe. Axcion never sees your card details."
    return (f"Your Axcion invoice for {workspace}",
            _WRAP.format(body=body, url=url, cta="Pay the invoice", footer=foot),
            f"There is an unpaid invoice for {amount} for {workspace} on Axcion. Pay it or update "
            f"the payment method on Stripe's page:\n\n{url}\n\n{foot}")


def support_access_opened(workspace: str, operator: str, reason: str, minutes: int, ends: str):
    """To every owner, when an Axcion operator opens support access. No link: there is nothing for
    the owner to do, and a message that invites a click is the shape of a phishing email."""
    ws, who, why = _esc(workspace), _esc(operator), _esc(reason)
    html = ("<div style=\"font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;"
            "font-size:15px;line-height:1.6;color:#333730;max-width:520px\">"
            f"<p>{who} from Axcion support has opened read-only access to the <strong>{ws}</strong> "
            f"workspace for {minutes} minutes, until {_esc(ends)}.</p>"
            f"<p><strong>Reason given:</strong> {why}</p>"
            "<p>The session can read but not change anything, it ends on its own, and it appears in your "
            "workspace's audit log and on your Team page while it is open.</p>"
            "<p style=\"color:#868B82;font-size:12px;margin-top:24px\">If you did not expect this, reply "
            "to this email and we will close it.</p></div>")
    text = (f"{operator} from Axcion support has opened read-only access to {workspace} for {minutes} "
            f"minutes, until {ends}.\n\nReason given: {reason}\n\nThe session can read but not change "
            "anything, ends on its own, and appears in your audit log and Team page while it is open.")
    return f"Axcion support opened access to {workspace}", html, text


# The finder's footer. Nothing about the account in it: the reader may not be the person who
# typed the address, and "you have an account" is exactly what the page refused to say.
_FINDER_FOOT = ("Someone entered this address to find its Axcion workspaces. If that wasn't you, "
                "you can ignore this email — nothing has changed.")

# _WRAP carries exactly one link, which fits one workspace and not several. This is the same
# frame with a list where the button was.
_LIST_WRAP = """<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;
font-size:15px;line-height:1.6;color:#333730;max-width:520px">
{body}
<table role="presentation" cellpadding="0" cellspacing="0" style="border-collapse:collapse;
width:100%;margin-top:18px">{rows}</table>
<p style="color:#868B82;font-size:12px;margin-top:24px">{footer}</p></div>"""

_LIST_ROW = """<tr><td style="padding:12px 0;border-top:1px solid #E9EBE5">
<strong style="color:#16201F">{name}</strong><br>
<a href="{url}" style="color:#3F6B66">{label}</a></td></tr>"""


def workspace_list(workspaces: list[tuple[str, str]]):
    """(name, url) pairs, already sorted. Called from auth.find_workspace."""
    if len(workspaces) == 1:
        name, url = workspaces[0]
        body = (f"<p>This address can sign in to the <strong>{_esc(name)}</strong> workspace "
                "on Axcion.</p>")
        return ("Your Axcion workspace",
                _WRAP.format(body=body, url=_esc(url), cta="Go to sign in", footer=_FINDER_FOOT),
                f"This address can sign in to {name} on Axcion:\n\n{url}\n\n{_FINDER_FOOT}")

    rows = "".join(_LIST_ROW.format(name=_esc(n), url=_esc(u),
                                    label=_esc(u.split("://", 1)[-1]))
                   for n, u in workspaces)
    body = (f"<p>This address can sign in to {len(workspaces)} workspaces on Axcion. Each one "
            "has its own address:</p>")
    listing = "\n".join(f"{n}\n{u}\n" for n, u in workspaces)
    return ("Your Axcion workspaces",
            _LIST_WRAP.format(body=body, rows=rows, footer=_FINDER_FOOT),
            f"This address can sign in to {len(workspaces)} workspaces on Axcion:\n\n"
            f"{listing}\n{_FINDER_FOOT}")


def no_workspace(email: str):
    """Nothing matched. Says so plainly, and says the one thing that fixes it: an invite from
    whoever runs the team. It must NOT hint that the address was close to something — no "did
    you mean", no mention of a domain that does have a workspace."""
    body = (f"<p>Someone asked which Axcion workspaces <strong>{_esc(email)}</strong> can sign in "
            "to, and it isn't on any.</p>"
            "<p>Workspaces are joined by invitation. If your team uses Axcion, ask whoever runs "
            "it to invite this address.</p>")
    html = ("<div style=\"font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;"
            "font-size:15px;line-height:1.6;color:#333730;max-width:520px\">"
            f"{body}<p style=\"color:#868B82;font-size:12px;margin-top:24px\">{_FINDER_FOOT}</p>"
            "</div>")
    text = (f"Someone asked which Axcion workspaces {email} can sign in to, and it isn't on any.\n\n"
            "Workspaces are joined by invitation. If your team uses Axcion, ask whoever runs it "
            f"to invite this address.\n\n{_FINDER_FOOT}")
    return "No Axcion workspace for this address", html, text


def binder_digest(subject: str, body_text: str):
    """The Binder digest is already assembled as plain text by binder_reminders. Wrap it
    rather than rebuilding it — that module owns the content."""
    esc = _html.escape(body_text)
    html_body = (f"<div style=\"font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;"
                 f"font-size:14px;line-height:1.6;color:#333730\"><pre style=\"font-family:"
                 f"ui-monospace,Menlo,monospace;font-size:13px;white-space:pre-wrap\">{esc}"
                 f"</pre></div>")
    return subject, html_body, body_text
