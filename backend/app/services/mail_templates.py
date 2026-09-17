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
    body = (f"<p>{who} to the <strong>{ws}</strong> workspace on Acumyn.</p>"
            "<p>Setting a password takes about a minute.</p>")
    foot = f"This link expires in {days} days. If you weren't expecting it, ignore this email."
    text = f"{inviter or 'You have been invited'} — {workspace} on Acumyn.\n\n{url}\n\n{foot}"
    subject = f"{inviter} invited you to {workspace}" if inviter else f"You're invited to {workspace}"
    return subject, _WRAP.format(body=body, url=url, cta="Accept invite", footer=foot), text


def reset(url: str, workspace: str, hours: int = 24):
    ws = _esc(workspace)
    body = (f"<p>Someone asked to reset the password on your <strong>{ws}</strong> "
            "Acumyn account.</p>")
    foot = (f"This link expires in {hours} hours. If you didn't ask for it you can ignore "
            "this — your password has not changed.")
    return ("Reset your Acumyn password",
            _WRAP.format(body=body, url=url, cta="Set a new password", footer=foot),
            f"Reset your Acumyn password for {workspace}.\n\n{url}\n\n{foot}")


def owner_invite(url: str, workspace: str, days: int = 7):
    ws = _esc(workspace)
    body = (f"<p>Your Acumyn workspace <strong>{ws}</strong> is ready.</p>"
            "<p>This link makes you the owner — set a password, then connect your first "
            "system.</p>")
    foot = f"This link expires in {days} days."
    return ("Your Acumyn workspace is ready",
            _WRAP.format(body=body, url=url, cta="Set up your workspace", footer=foot),
            f"Your Acumyn workspace {workspace} is ready.\n\n{url}\n\n{foot}")


# The finder's footer. Nothing about the account in it: the reader may not be the person who
# typed the address, and "you have an account" is exactly what the page refused to say.
_FINDER_FOOT = ("Someone entered this address to find its Acumyn workspaces. If that wasn't you, "
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
                "on Acumyn.</p>")
        return ("Your Acumyn workspace",
                _WRAP.format(body=body, url=_esc(url), cta="Go to sign in", footer=_FINDER_FOOT),
                f"This address can sign in to {name} on Acumyn:\n\n{url}\n\n{_FINDER_FOOT}")

    rows = "".join(_LIST_ROW.format(name=_esc(n), url=_esc(u),
                                    label=_esc(u.split("://", 1)[-1]))
                   for n, u in workspaces)
    body = (f"<p>This address can sign in to {len(workspaces)} workspaces on Acumyn. Each one "
            "has its own address:</p>")
    listing = "\n".join(f"{n}\n{u}\n" for n, u in workspaces)
    return ("Your Acumyn workspaces",
            _LIST_WRAP.format(body=body, rows=rows, footer=_FINDER_FOOT),
            f"This address can sign in to {len(workspaces)} workspaces on Acumyn:\n\n"
            f"{listing}\n{_FINDER_FOOT}")


def no_workspace(email: str):
    """Nothing matched. Says so plainly, and says the one thing that fixes it: an invite from
    whoever runs the team. It must NOT hint that the address was close to something — no "did
    you mean", no mention of a domain that does have a workspace."""
    body = (f"<p>Someone asked which Acumyn workspaces <strong>{_esc(email)}</strong> can sign in "
            "to, and it isn't on any.</p>"
            "<p>Workspaces are joined by invitation. If your team uses Acumyn, ask whoever runs "
            "it to invite this address.</p>")
    html = ("<div style=\"font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;"
            "font-size:15px;line-height:1.6;color:#333730;max-width:520px\">"
            f"{body}<p style=\"color:#868B82;font-size:12px;margin-top:24px\">{_FINDER_FOOT}</p>"
            "</div>")
    text = (f"Someone asked which Acumyn workspaces {email} can sign in to, and it isn't on any.\n\n"
            "Workspaces are joined by invitation. If your team uses Acumyn, ask whoever runs it "
            f"to invite this address.\n\n{_FINDER_FOOT}")
    return "No Acumyn workspace for this address", html, text


def binder_digest(subject: str, body_text: str):
    """The Binder digest is already assembled as plain text by binder_reminders. Wrap it
    rather than rebuilding it — that module owns the content."""
    esc = _html.escape(body_text)
    html_body = (f"<div style=\"font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;"
                 f"font-size:14px;line-height:1.6;color:#333730\"><pre style=\"font-family:"
                 f"ui-monospace,Menlo,monospace;font-size:13px;white-space:pre-wrap\">{esc}"
                 f"</pre></div>")
    return subject, html_body, body_text
