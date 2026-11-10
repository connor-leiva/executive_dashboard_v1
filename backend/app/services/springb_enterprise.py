"""Spring B enterprise scorecard — the L10 / department / monthly / per-event board.

Additive and idempotent. Matching an existing measurable (by slug, or by the group key + name it
has on the current board) keeps its id, so weekly history stays. A goal already set to something
other than the legacy Q4 number is left alone. Appointments Set and Met are forced back to hand
entry: a HighLevel pass on Nov 10 found zero bookings on every Forum calendar from Oct 1 to Nov 10,
so an auto-count of those calendars only writes zeros over whatever Connor types.

Run from a migration, or:

    python -m app.services.springb_enterprise --backfill 8
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Business, ScorecardGoal, ScorecardGroup, ScorecardMetric

PEOPLE = [
    {"initials": "SB", "name": "Spring", "seat": "Visionary, holding Growth & Marketing until it is hired", "reviewer": "Connor"},
    {"initials": "CL", "name": "Connor", "seat": "Integrator and Finance", "reviewer": "Spring"},
    {"initials": "J", "name": "Justin", "seat": "Director of Enrollment", "reviewer": "Connor"},
    {"initials": "SO", "name": "Sarah", "seat": "Director of Operations", "reviewer": "Connor"},
    {"initials": "MG", "name": "Matt", "seat": "SDR", "reviewer": "Justin"},
    {"initials": "N", "name": "Nicole", "seat": None, "reviewer": "Sarah"},
    {"initials": "B", "name": "Bryna", "seat": None, "reviewer": "Sarah"},
    {"initials": "TBD", "name": "No owner yet", "seat": None, "reviewer": None, "tbd": True},
]

# Weekly goals written by scripts/seed_springb_goals.py (Oct 13). Replaced only when the stored
# number is still one of these, or still the track-only 0.
_LEGACY_WEEKLY = {
    "vip": (2,), "apptset": (2,), "apptmet": (1.5,), "referrals": (1,), "open": (35,), "assets": (80,),
}
_LEGACY_CUM = {
    "vip": (24,), "members": (8,), "apptset": (24,), "apptmet": (20,), "referrals": (13,),
}
# Resolvers the old board already set, which this catalog replaces on purpose.
_LEGACY_RESOLVER = {"vip": {"forum_vip_guests_added"}}
# These two stay hand-entered even on a re-run. Auto-counting the calendars writes a real zero.
_FORCE_HAND = {"apptset", "apptmet"}

RETIRE = [
    ("forum", "Members at Risk", "Replaced by Forum members at red on the L10."),
    ("forum", "Email Open Rate", "Folded into Email open rate floor, by brand, in Marketing."),
    ("becollective", "Mighty Network Growth", "Cut in the quarterly."),
    ("becollective", "Engagement", "Split into call attendance and Mighty engagement on the beCollective board."),
    ("becollective", "Email Open Rate", "Folded into Email open rate floor."),
    ("activated", "Email Open Rate", "Folded into Email open rate floor."),
]


def M(slug, name, **kw):
    row = {
        "slug": slug, "name": name, "type": "flow", "direction": "gte", "goal": None,
        "unit": "count", "cadence": "weekly", "tier": "dept", "owner": ("TBD", "No owner yet"),
        "provenance": "hand", "provenance_from": None, "source": "manual", "core": True,
        "resolver": None, "note": "", "why": None, "detail": "", "check": None,
        "target_by": None, "scores_zero": False, "cadence_label": None, "anchors": None,
        "move_from": None,
    }
    row.update(kw)
    return row


def _g(key, name, lens, sort, metrics, **kw):
    return {"key": key, "name": name, "lens": lens, "sort": sort, "metrics": metrics, **kw}


_SB, _CL, _J, _SO, _MG, _N, _B, _TD = (
    ("SB", "Spring"), ("CL", "Connor"), ("J", "Justin"), ("SO", "Sarah"),
    ("MG", "Matt"), ("N", "Nicole"), ("B", "Bryna"), ("TBD", "No owner yet"),
)
_DEPT = dict(tier="dept")
_L10 = dict(tier="l10")

GROUPS = [
    _g("sb_l10_growth", "Growth & Marketing", "l10", 10, [
        M("leads", "Qualified guest leads", **_L10, owner=_SB, goal=14, resolver="forum_vip_guests_spring",
          provenance="live", provenance_from="GHL", source="ghl",
          note="VIP Guest entries whose opportunity source is the VIP page or the guest application.",
          why="Top of the Forum funnel, and Spring’s rock number.",
          detail="Counts a guest once, on entering the VIP Guest stage group, when opportunity.source is VIP Page, forummastermind.com/vip, or forummastermind.com/guest-application. A blank source falls through to the attribution URL (/vip or /guest-application). Sisu sources are Justin’s row. A guest with no source, no Sisu tag and no URL is in neither count.",
          check="Four of the last 19 VIP opportunities had an empty source. Those stay out of both rows until a source, the Sisu tag, or an attribution URL is on the opportunity."),
        M("cpl", "Cost per qualified lead", **_L10, owner=_SB, direction="lte", type="snapshot", unit="usd",
          provenance="hand",
          note="Paid spend (ads, direct mail, agency fees) ÷ qualified leads.",
          why="Shows whether the leads are working or just bought.",
          detail="Meta spend is in the Ads module, but mail and agency fees are not, and a lead still needs the qualified rule. Enter the week’s cost by hand."),
    ], owner_name="Spring"),
    _g("sb_l10_enroll", "Enrollment", "l10", 20, [
        M("vip", "VIP guests added", **_L10, owner=_J, goal=7, target_by="75 by 1/18",
          resolver="forum_vip_guests_sisu", provenance="live", provenance_from="GHL", source="ghl",
          move_from=("forum", "VIP Guests Added"),
          note="Sisu-sourced guests entering the VIP Guest stage group.",
          why="The next step in the funnel. A miss here shows up at Dallas too late to fix.",
          detail="Counts a guest once, on entering the VIP Guest group, when opportunity.source is Sisu or Sisu Private Mastermind. A blank source falls back to the tag “forum vip guest sisu nov 2026” (or any tag with both vip and sisu), then to an attribution URL containing /sisu. Spring’s page sources are Qualified guest leads, not this row.",
          check="Opt-In and Confirmed are the same stage group, so a guest is not counted twice. Assigned-to does not separate Spring from Justin — source does. Empty-source guests are in neither row."),
        M("members", "Members added", **_L10, owner=_J, target_by="20 by 12/31",
          resolver="forum_members_added", provenance="live", provenance_from="GHL", source="ghl",
          move_from=("forum", "Members Added"),
          note="New signed, paid Forum contracts, by GHL enrollment date.",
          why="The output enrollment exists to produce.",
          detail="Active members whose GHL enrollment date falls in the week. Same reading as the Forum tab’s growth chart."),
        M("edge", "Edge new paid members", **_L10, owner=_J, resolver="sb_edge_paid_added",
          provenance="live", provenance_from="Stripe", source="stripe",
          note="New paid Edge subscriptions starting this week. Comped plans excluded.",
          why="The Edge’s one weekly number.",
          detail="Legacy Stripe subscriptions classified as Edge, start date in the week, amount above zero, plan name without “comp”. Blank until that feed has synced."),
    ], owner_name="Justin"),
    _g("sb_l10_experience", "Member Experience", "l10", 30, [
        M("red", "Forum members at red", **_L10, owner=_SO, direction="lte", type="snapshot",
          provenance="sync",
          note="Members flagged red on attendance: yellow after one missed session, red after three.",
          why="The earliest churn warning, and Sarah’s team can act on it the same week.",
          detail="No Zoom attendance feed. Enter the red count by hand from the Zoom report until one exists."),
        M("renewals", "60-day renewals confirmed", **_L10, owner=_SO, type="rate", unit="pct",
          provenance="sync",
          note="Members renewing in the next 60 days whose renewal is confirmed ÷ all members due.",
          why="Protects contracted revenue. A save is worth as much as a sale.",
          detail="The renewal book is in GHL. “Confirmed” is not stored, so this stays hand-entered."),
    ], owner_name="Sarah"),
    _g("sb_l10_finance", "Integrator & Finance", "l10", 40, [
        M("active", "Active paying Forum members", **_L10, owner=_CL, type="snapshot",
          target_by="125, date open", resolver="sb_active_members",
          provenance="live", provenance_from="GHL", source="ghl",
          note="Active non-admin Forum members on the GHL roster. Written for the current week only.",
          why="The true north: it nets new members against churn.",
          detail="As-of the current week, so look-back does not rewrite history. Comped seats are not a separate flag on the Forum roster, so this is every active member and can sit above the Oct 29 paying count of 43.",
          check="Comped members are included until a comp rule is named. The Oct 29 packet’s 43 excluded them."),
        M("cash", "Cash collected vs. due", **_L10, owner=_CL, goal=100, type="rate", unit="pct",
          resolver="sb_cash_collected_rate", provenance="live", provenance_from="GHL", source="ghl",
          note="Succeeded GHL payments ÷ attempts (succeeded + failed) this week.",
          why="The collections lever. Failed payments roll up into it.",
          detail="A percent. A week with no attempts stays blank rather than 0% or 100%."),
        M("netcash", "Net cash", **_L10, owner=_CL, unit="usd", resolver="sb_net_cash",
          provenance="live", provenance_from="Books", source="qbo",
          note="Cash in minus cash out for the week, from QuickBooks via Books.",
          why="The weekly profit signal while the year sits at a loss.",
          detail="Deposits, sales receipts and payments in. Purchases, expenses and bill payments out. Transfers and journal entries are skipped."),
    ], owner_name="Connor"),
    _g("sb_l10_break", "Spring Break", "l10", 50, [
        M("tickets", "Spring Break tickets sold", **_L10, owner=_TD, provenance="sync",
          note="Cumulative tickets by type. On for the Black Friday push.",
          why="Spring asked for it on Nov 3. On for Black Friday, off in December, back on in January.",
          detail="Payments sync, but Spring Break is filtered out of Forum revenue and there is no product-name filter that is safe to count. Enter by hand while it is in season."),
    ], owner_name="No owner yet", season="Off until Nov 27"),
    _g("sb_d_mkt", "Growth & Marketing", "dept", 110, [
        M("optins", "Net new opted-in contacts", owner=_SB, move_from=("spring_b", "Net New Opted-In Contacts"),
          note="New opt-ins minus unsubscribes, tagged by brand.",
          detail="On the board today as hand-entered. The sync does not store opt-ins."),
        M("seatconv", "Guest seat page conversion", owner=_SB, type="rate", unit="pct", provenance="sync",
          note="Seat requests ÷ unique page visitors.",
          detail="Page visits are not synced."),
        M("adspend", "Ad spend vs. budget", owner=_SB, type="rate", unit="pct", goal=100, direction="lte",
          note="Paid-media spend this week ÷ the planned weekly budget.",
          detail="Meta spend is in the Ads module. No weekly budget is stored, so the ratio is hand-entered."),
        M("mail", "Direct mail response rate", owner=_SB, type="rate", unit="pct", cadence="event",
          cadence_label="Per drop",
          note="Tagged responses ÷ pieces delivered, plus qualified leads per drop.",
          detail="Entered per drop. The next one goes out around Nov 14."),
        M("regs", "Registrations per campaign", owner=_SB, cadence="event", cadence_label="Per campaign",
          note="Registrations for each webinar, lead magnet or campaign, logged when it closes.",
          detail="Registrations sync by event tag for Forum gatherings only."),
        M("assets", "Content assets shipped vs. planned", owner=_SB,
          move_from=("spring_b", "Content Assets Shipped vs. Planned"),
          note="Assets and campaigns live on their calendar date.",
          detail="Hand-entered."),
        M("open", "Email open rate floor", owner=_SB, type="rate", unit="pct",
          move_from=("spring_b", "Email Open Rate Floor"),
          note="Open rate per send, by brand. Any send under the floor gets flagged.",
          detail="Hand-entered. The floor itself is not set yet."),
    ], owner_name="Spring", dept_key="marketing", dept_name="Marketing", lead_name="Spring",
       reviewed_in="Connor’s 1:1 with Spring"),
    _g("sb_d_pipeline", "Guest pipeline", "dept", 120, [
        M("speed", "Calls booked within 24 hours", owner=_MG, type="rate", unit="pct", goal=100,
          note="New seat requests with a call booked within 24 hours ÷ new seat requests.",
          detail="Opt-in times and calendar bookings are not joined, and no Forum calendar has had a booking since July. Enter by hand."),
        M("convos", "Guest conversations completed", owner=_MG,
          note="Live conversations with prospective guests.",
          detail="Matt’s spreadsheet until GHL is set up for him."),
        M("apptset", "Appointments set", owner=_MG, move_from=("forum", "Appointments Set"),
          note="Appointments booked on the Forum calendars.",
          detail="Hand-entered. A read of every Forum calendar from Oct 1 to Nov 10 returned zero appointments. Forum VIP Guest Application (6q9ga2aGN28ryW990aR9) has been inactive since July, and Matt Griner is on no calendar. Adding that calendar id will not fill these weeks.",
          check="Zero appointments on every Forum calendar, Oct 1–Nov 10. Enter the number from Matt’s record. Reactivate 6q9ga2aGN28ryW990aR9 only when he actually books there."),
        M("apptmet", "Appointments met", owner=_MG, move_from=("forum", "Appointments Met"),
          note="Booked appointments that actually happened.",
          detail="Hand-entered, for the same reason as Appointments set: the calendars are empty, so an auto-count would keep writing zero.",
          check="Same calendar finding as Appointments set. Do not treat a zero from GHL as the week’s number."),
        M("nextsteps", "Next steps documented", owner=_MG, type="rate", unit="pct", goal=100,
          note="Open guests with a next step and a follow-up date ÷ open guests.",
          detail="Follow-up dates live on GHL opportunities, which the sync does not store."),
        M("open21", "Decisions open past 21 days", owner=_MG, type="snapshot", direction="lte", goal=0,
          scores_zero=True, resolver="sb_decisions_open", provenance="live", provenance_from="GHL", source="ghl",
          note="VIP guests still open whose stage has not changed in 21 days.",
          detail="Current open sales-funnel opportunities in a VIP Guest stage, last stage change 21 or more days ago. Current week only."),
    ], owner_name="Matt", dept_key="enrollment", dept_name="Enrollment & Edge", lead_name="Justin",
       reviewed_in="Justin’s 1:1 with Connor"),
    _g("sb_d_retention", "Retention", "dept", 130, [
        M("referrals", "Member referrals generated", owner=_J, move_from=("forum", "Member Referrals Generated"),
          note="Qualified guest leads referred by current members.", detail="Hand-entered."),
        M("retconv", "Retention conversations on time", owner=_J, type="rate", unit="pct", goal=100,
          note="Departing members whose retention conversation happened 30+ days before their last day.",
          detail="Offboarding runs in ClickUp, which Axcion does not read."),
        M("save", "Retention save rate", owner=_J, type="rate", unit="pct", cadence="monthly",
          note="Departing members who renewed or moved to another offer ÷ departing.",
          detail="Hand-entered, monthly."),
        M("forecast", "Forecast accuracy", owner=_J, type="rate", unit="pct", cadence="monthly",
          note="Enrollments forecast at the start of the month vs. actual.", detail="Hand-entered."),
    ], owner_name="Justin", dept_key="enrollment", dept_name="Enrollment & Edge", lead_name="Justin",
       reviewed_in="Justin’s 1:1 with Connor"),
    _g("sb_d_edge", "The Edge", "dept", 140, [
        M("edgeatt", "Live-session attendance", owner=_J, type="rate", unit="pct", provenance="sync",
          note="Paid members at one or more live sessions in the trailing 30 days.",
          detail="Same missing Zoom feed as Forum members at red."),
        M("webinars", "Edge webinars held", owner=_J, cadence="monthly",
          note="Edge webinars delivered.", detail="Hand-entered."),
        M("edgeleads", "Edge leads", owner=_J, provenance="sync",
          note="New opt-ins and webinar registrations tagged Edge.",
          detail="Opt-ins tagged Edge are not stored."),
        M("edge2forum", "Edge members to Forum guests", owner=_J, cadence="monthly",
          note="Edge members who registered as Forum VIP guests.",
          detail="Both sides sync, but matching them by email is not reliable enough to auto-count. Enter monthly."),
    ], owner_name="Justin", dept_key="enrollment", dept_name="Enrollment & Edge", lead_name="Justin",
       reviewed_in="Justin’s 1:1 with Connor"),
    _g("sb_d_ops", "Operations", "dept", 150, [
        M("notices", "Cancellation notices received", owner=_SO, direction="lte",
          note="Notices this week, with last paid date and tenure.",
          detail="Notices arrive by email. Enter by hand."),
        M("onboard", "Onboarding past 14 days", owner=_SO, type="snapshot", direction="lte", goal=0,
          scores_zero=True, note="New members not fully onboarded 14 or more days after purchase.",
          detail="Onboarding runs in ClickUp."),
        M("stuck", "Stuck items over 1 business day", owner=_SO, type="snapshot", direction="lte", goal=0,
          scores_zero=True, note="Member-experience tasks stuck for more than one business day.",
          detail="Tracked in ClickUp."),
        M("failed", "Failed payments open past 7 days", owner=_SO, type="snapshot", direction="lte", goal=0,
          scores_zero=True, resolver="sb_failed_payments_open", provenance="live", provenance_from="GHL",
          source="ghl",
          note="Failed GHL payments older than 7 days with no later success.",
          detail="Matched to a later success on the same subscription or the same email. Current week only."),
    ], owner_name="Sarah", dept_key="experience", dept_name="Member experience", lead_name="Sarah",
       reviewed_in="Sarah’s 1:1 with Connor"),
    _g("sb_d_support", "Replays and records", "dept", 160, [
        M("replays", "Replays and handouts within 24 hours", owner=_N, type="rate", unit="pct", goal=100,
          note="Sessions whose replay and handouts posted within 24 hours ÷ sessions held.",
          detail="Hand-entered."),
        M("renewdates", "Renewal dates current", owner=_N, type="rate", unit="pct", goal=100,
          resolver="sb_renewal_dates_current", provenance="live", provenance_from="GHL", source="ghl",
          note="Active members with a renewal date on the membership record ÷ active members.",
          detail="The date field GHL already syncs. There is no separate “verified” flag. Current week only."),
    ], owner_name="Nicole", dept_key="experience", dept_name="Member experience", lead_name="Sarah",
       reviewed_in="Sarah’s 1:1 with Connor"),
    _g("sb_d_welcome", "Member welcome", "dept", 170, [
        M("welcomepost", "Welcome post within 14 days", owner=_B, type="rate", unit="pct", goal=100,
          note="Welcome posts live within 14 days of intake complete ÷ intakes completed.",
          detail="Hand-entered."),
    ], owner_name="Bryna", dept_key="experience", dept_name="Member experience", lead_name="Sarah",
       reviewed_in="Sarah’s 1:1 with Connor"),
    _g("sb_d_fin", "Finance", "dept", 180, [
        M("spend", "Spend vs. budget", owner=_CL, type="rate", unit="pct", goal=100, direction="lte",
          note="Week and month-to-date spend by category ÷ budget.",
          detail="Books has actuals from QuickBooks. No budget is stored, so the ratio is hand-entered."),
        M("close", "Monthly close on time", owner=_CL, cadence="monthly",
          note="Business day the books close each month.", detail="Hand-entered."),
    ], owner_name="Connor", dept_key="finance", dept_name="Finance", lead_name="Connor",
       reviewed_in="Connor’s 1:1 with Spring"),
    _g("sb_monthly", "Monthly", "monthly", 200, [
        M("revenue", "Revenue vs. annual target", tier="monthly", cadence="monthly", owner=_CL, unit="usd",
          type="snapshot", note="Revenue YTD from the close, consolidated.",
          detail="Enter the latest YTD figure. $3.5M is the 2026 target; $1.8M was through Oct 11.",
          target_by="$3.5M annual"),
        M("netprofit", "Net profit vs. annual target", tier="monthly", cadence="monthly", owner=_CL, unit="usd",
          type="snapshot", note="Net profit YTD from the close.",
          detail="Enter the latest figure. Target $1.7M; –$270K as of Oct 29.",
          target_by="$1.7M annual"),
        M("byproduct", "Profit by product", tier="monthly", cadence="monthly", owner=_CL, unit="usd",
          type="snapshot", note="One-page P&L per product. Enter the figure you want on the L10.",
          detail="Forum and beCollective close on their own books. Edge and Spring Break need class tracking inside SB Coaching."),
        M("backlog", "Contracted revenue backlog", tier="monthly", cadence="monthly", owner=_CL, unit="usd",
          type="snapshot", note="Remaining scheduled payments on signed, active contracts.",
          detail="$590K was the rocks-hit projection, not an actual."),
        M("edgemrr", "Edge MRR and cancellations", tier="monthly", cadence="monthly", owner=_J,
          note="Enter monthly payers. Note cancellations in the week you review it.",
          detail="13 monthly payers and 2 canceled as of Oct 29. Stored as the payer count."),
        M("sponsor", "Sponsorship revenue", tier="monthly", cadence="monthly", owner=_SB, unit="usd",
          type="snapshot", note="Sponsorship dollars signed YTD.",
          detail="$129K of $250K as of Oct 29.", target_by="$250K for 2026"),
        M("cac", "CAC per new Forum member", tier="monthly", cadence="monthly", owner=_SB, unit="usd",
          type="snapshot", direction="lte",
          note="Marketing spend plus sales comp ÷ new paid Forum members.",
          detail="The Ads module computes attributed and blended CAC for ad-traced enrollments. Mail and comp are not in that figure, so the L10 number stays hand-entered."),
        M("bcmembers", "beCollective active members", tier="monthly", cadence="monthly", owner=_TD,
          note="Paid beCollective members on a current term.",
          detail="87 of 130 as of Oct 29.", target_by="130"),
        M("upgrade", "Upgrade RevShare", tier="monthly", cadence="monthly", owner=_TD, unit="usd",
          type="snapshot", note="Revenue share received.",
          detail="$561 of $900 as of Oct 29. Only if Upgrade stays in Spring B.", target_by="$900"),
    ], owner_name="Connor"),
    _g("sb_event", "Per event", "event", 210, [
        M("show", "Guest show rate", tier="event", cadence="event", owner=_J, type="rate", unit="pct",
          note="Registered guests who attended ÷ registered.",
          detail="Entered at the first L10 after each gathering.",
          anchors=[{"label": "Scottsdale", "date": "2026-11-11"}, {"label": "Dallas", "date": "2027-01-18"}]),
        M("closerate", "Guest close rate", tier="event", cadence="event", owner=_J, type="rate", unit="pct",
          goal=40, note="Attended guests who enrolled within 21 days ÷ attended.",
          detail="Goal is at least 40%. The path-to-90 math applies 40% to every registered Dallas guest, which assumes everyone shows.",
          anchors=[{"label": "Scottsdale", "date": "2026-11-11"}, {"label": "Dallas", "date": "2027-01-18"}]),
    ], owner_name="Justin"),
    _g("sb_tasks", "On-time task completion", "person", 300, [
        M(f"task-{ini.lower()}", "On-time task completion", tier="person", type="rate", unit="pct", goal=90,
          owner=(ini, name), provenance="sync", core=False,
          note="ClickUp tasks completed by their due date ÷ tasks due.",
          detail="ClickUp is not connected. Enter the percent by hand until it is.")
        for ini, name in (_SB, _CL, _J, _SO, _MG, _N, _B)
    ], owner_name="Connor"),
    _g("sb_bc", "beCollective", "elsewhere", 400, [
        M("bc-active", "Active members", tier="elsewhere", cadence="monthly", owner=_TD, core=False,
          target_by="130 (at 87)", note="Paid members on a current term."),
        M("bc-calls", "Call attendance", tier="elsewhere", owner=_SO, core=False, provenance="sync",
          note="Attendance flags applied to beCollective calls."),
        M("bc-mighty", "Mighty engagement", tier="elsewhere", owner=_SO, core=False, provenance="sync",
          note="Weekly active members in Mighty."),
        M("bc-renew", "Renewals", tier="elsewhere", cadence="monthly", owner=_SO, core=False, type="rate", unit="pct",
          note="Members renewing ÷ members reaching term end."),
        M("bc-nurture", "Nurture emails sent", tier="elsewhere", owner=_TD, core=False,
          note="Planned nurture emails sent on schedule."),
        M("bc-list", "Qualified list growth", tier="elsewhere", owner=_TD, core=False,
          note="New contacts tagged beCollective who fit the target profile."),
        M("bc-feb", "February event sign-ups", tier="elsewhere", owner=_TD, core=False,
          note="Women registered for the February event."),
        M("bc-funnel", "Launch funnel", tier="elsewhere", owner=_TD, core=False,
          target_by="2,000 registrants", note="Launch only. Last launch was 1,400."),
        M("bc-match", "Registrant profile match", tier="elsewhere", owner=_TD, core=False, type="rate", unit="pct",
          note="Launch only. Share of registrants who fit the target profile."),
    ], owner_name="beCollective", reviewed_in="Reviewed with Spring, Julie and Sarah"),
    _g("sb_break_project", "Spring Break project", "elsewhere", 410, [
        M("sbp-rpa", "Revenue per attendee", tier="elsewhere", owner=_TD, core=False, unit="usd", type="snapshot",
          note="Ticket revenue ÷ tickets sold. Needs the price."),
        M("sbp-cpt", "Cost per ticket sold", tier="elsewhere", owner=_TD, core=False, unit="usd",
          direction="lte", type="snapshot", note="Spring Break marketing spend ÷ tickets sold."),
        M("sbp-conv", "Attendees converted", tier="elsewhere", cadence="event", owner=_TD, core=False,
          note="Attendees who became Forum VIP guests or Edge members after the event."),
        M("sbp-sponsor", "Spring Break sponsor revenue", tier="elsewhere", owner=_SB, core=False, unit="usd",
          note="Part of the sponsorship total, not on top of it."),
    ], owner_name="No owner yet"),
]

CORE_SLUGS = tuple(m["slug"] for g in GROUPS for m in g["metrics"] if m["core"])
assert len(CORE_SLUGS) == len(set(CORE_SLUGS)) == 52, len(CORE_SLUGS)


def _dec(v):
    return None if v is None else Decimal(str(v))


def _near(value, options) -> bool:
    if value is None:
        return False
    return any(abs(float(value) - float(o)) < 0.001 for o in options)


def _blank_goal(value) -> bool:
    return value is None or float(value) == 0


def apply_enterprise(session: Session) -> dict:
    """Upsert the enterprise board onto every membership business. Does not delete values."""
    created = adopted = retired = 0
    businesses = session.scalars(select(Business).where(Business.kind == "membership")).all()
    for biz in businesses:
        groups = {g.key: g for g in session.scalars(select(ScorecardGroup).where(
            ScorecardGroup.tenant_id == biz.tenant_id, ScorecardGroup.business_id == biz.id)).all()}
        metrics = session.scalars(select(ScorecardMetric).where(
            ScorecardMetric.tenant_id == biz.tenant_id)).all()
        # only this business's metrics
        metrics = [m for m in metrics if m.group_id in {g.id for g in groups.values()}]
        by_slug = {m.slug: m for m in metrics if m.slug}
        by_name = {}
        for m in metrics:
            g = next((gg for gg in groups.values() if gg.id == m.group_id), None)
            if g:
                by_name[(g.key, m.name)] = m

        wanted = {}
        for spec in GROUPS:
            group = groups.get(spec["key"])
            if group is None:
                group = ScorecardGroup(
                    tenant_id=biz.tenant_id, business_id=biz.id, key=spec["key"], name=spec["name"],
                    is_team_room=False, sort_order=spec["sort"], owner_name=spec.get("owner_name"),
                    lens=spec["lens"], season=spec.get("season"), reviewed_in=spec.get("reviewed_in"),
                    dept_key=spec.get("dept_key"), dept_name=spec.get("dept_name"),
                    lead_name=spec.get("lead_name"), active=True)
                session.add(group)
                session.flush()
                groups[spec["key"]] = group
            else:
                group.name = spec["name"]
                group.lens = spec["lens"]
                group.season = spec.get("season")
                group.reviewed_in = spec.get("reviewed_in")
                group.dept_key = spec.get("dept_key")
                group.dept_name = spec.get("dept_name")
                group.lead_name = spec.get("lead_name")
                group.sort_order = spec["sort"]
                group.active = True
                if spec.get("owner_name") and not group.owner_name:
                    group.owner_name = spec["owner_name"]
            for i, row in enumerate(spec["metrics"]):
                wanted[row["slug"]] = (group, i, row)

        for gkey, name, _why in RETIRE:
            m = by_name.get((gkey, name))
            if m is None or not m.active:
                continue
            m.active = False
            if not m.slug:
                m.slug = ("retired-" + name.lower().replace(" ", "-"))[:48]
            retired += 1

        for slug, (group, i, row) in wanted.items():
            m = by_slug.get(slug)
            adopting = False
            if m is None and row.get("move_from"):
                m = by_name.get(tuple(row["move_from"]))
                adopting = m is not None and not m.slug
            if m is None:
                ini, oname = row["owner"]
                m = ScorecardMetric(
                    tenant_id=biz.tenant_id, group_id=group.id, name=row["name"][:160],
                    slug=slug, sort_order=i, active=True)
                session.add(m)
                adopting = True
                created += 1
            elif adopting or (row.get("move_from") and not m.slug):
                adopted += 1
                adopting = True
            _apply_row(session, m, group, i, row, adopting=adopting or m.slug != slug)
            if m.slug != slug:
                # first time we stamp the slug counts as an adoption of an existing line
                if m in session and created == 0:
                    pass
            m.slug = slug
            by_slug[slug] = m

        # Activated Agent stays, but it is no longer an L10 group.
        activated = groups.get("activated")
        if activated is not None:
            activated.lens = "elsewhere"
            activated.reviewed_in = activated.reviewed_in or "With its partners, once its own workspace is live"
            for m in metrics:
                if m.group_id == activated.id and m.active and not m.tier:
                    m.tier = "elsewhere"
                    m.provenance = m.provenance or "hand"
                    m.provenance_detail = m.provenance_detail or (
                        "Activated Agent is leaving this board for its own workspace. "
                        "Enter here until that board is live.")
                    if not m.owner_initials:
                        m.owner_initials, m.owner_name = "CL", "Connor"

        for key in ("spring_b", "forum", "becollective"):
            g = groups.get(key)
            if g is None:
                continue
            left = session.scalar(select(func.count()).select_from(ScorecardMetric).where(
                ScorecardMetric.group_id == g.id, ScorecardMetric.active.is_(True)))
            if not left:
                g.active = False

    session.flush()
    return {"created": created, "adopted": adopted, "retired": retired, "businesses": len(businesses)}


def _apply_row(session: Session, m: ScorecardMetric, group, sort: int, row: dict, adopting: bool) -> None:
    ini, oname = row["owner"]
    m.group_id = group.id
    m.sort_order = sort
    m.active = True
    m.tier = row["tier"]
    m.cadence = row["cadence"]
    m.unit = row["unit"]
    m.type = row["type"]
    m.direction = row["direction"]
    m.note = row.get("note") or None
    m.why = row.get("why")
    m.target_by = row.get("target_by")
    m.cadence_label = row.get("cadence_label")
    m.provenance = row["provenance"]
    m.provenance_from = row.get("provenance_from")
    m.provenance_detail = row.get("detail") or None
    m.check_note = row.get("check")
    m.scores_zero = bool(row.get("scores_zero"))
    m.anchors = row.get("anchors")
    m.source = row.get("source") or "manual"
    if adopting or not m.name:
        m.name = row["name"][:160]
    elif m.name == (row.get("move_from") or (None, None))[1]:
        m.name = row["name"][:160]
    if adopting or not m.owner_initials:
        m.owner_initials, m.owner_name = ini, oname

    if adopting:
        raw = None if m.goal is None else float(m.goal)
        if _blank_goal(m.goal) or _near(raw, _LEGACY_WEEKLY.get(row["slug"], ())):
            m.goal = _dec(row["goal"])
        elif row["goal"] is not None and m.goal is None:
            m.goal = _dec(row["goal"])
        _retarget_period_goals(session, m, row)
    elif row["slug"] not in _FORCE_HAND and m.goal is None and row["goal"] is not None and row.get("scores_zero"):
        m.goal = _dec(row["goal"])

    want = row.get("resolver")
    if row["slug"] in _FORCE_HAND:
        m.resolver_key = None
        m.source_spec = None
        m.provenance = "hand"
        m.source = "manual"
    elif adopting and want:
        legacy = _LEGACY_RESOLVER.get(row["slug"], set())
        if m.resolver_key in (None, want) or m.resolver_key in legacy:
            m.resolver_key = want
            m.source_spec = None
    elif adopting and not want:
        m.resolver_key = None
        m.source_spec = None


def _retarget_period_goals(session: Session, m: ScorecardMetric, row: dict) -> None:
    """Move a still-legacy per-period goal onto the catalog number. A custom goal stays."""
    if m.id is None:
        return
    goals = session.scalars(select(ScorecardGoal).where(ScorecardGoal.metric_id == m.id)).all()
    for g in goals:
        weekly = float(g.goal)
        if _blank_goal(weekly) or _near(weekly, _LEGACY_WEEKLY.get(row["slug"], ())):
            if row["goal"] is None:
                session.delete(g)
                continue
            g.goal = _dec(row["goal"])
        if g.cumulative_goal is not None and _near(g.cumulative_goal, _LEGACY_CUM.get(row["slug"], ())):
            g.cumulative_goal = None


BACKFILL_KEYS = (
    "forum_members_added", "forum_vip_guests_sisu", "forum_vip_guests_spring",
    "sb_edge_paid_added", "sb_active_members", "sb_cash_collected_rate", "sb_net_cash",
    "sb_decisions_open", "sb_failed_payments_open", "sb_renewal_dates_current",
)


async def backfill(tenant_slug: str = "springb", weeks: int = 8) -> int:
    """Resolve the wired Spring B rows for the recent weeks. Does not touch other boards' metrics."""
    from ..db import SessionLocal
    from ..models import Tenant
    from .scorecard_resolvers import RESOLVERS, _recent_weeks, _write

    async with SessionLocal() as s:
        tenant = (await s.execute(select(Tenant).where(Tenant.slug == tenant_slug))).scalar_one_or_none()
        if tenant is None:
            raise SystemExit(f"no tenant {tenant_slug}")
        tid = tenant.id
        rows = (await s.execute(
            select(ScorecardMetric.id, ScorecardMetric.resolver_key, ScorecardGroup.business_id)
            .join(ScorecardGroup, ScorecardMetric.group_id == ScorecardGroup.id)
            .where(ScorecardMetric.tenant_id == tid, ScorecardMetric.active.is_(True),
                   ScorecardMetric.resolver_key.in_(BACKFILL_KEYS)))).all()
    today = dt.date.today()
    windows = _recent_weeks(today, weeks)
    written = 0
    for mid, key, bid in rows:
        fn = RESOLVERS.get(key)
        if fn is None:
            continue
        for ws, we in windows:
            async with SessionLocal() as s:
                val = await fn(s, tid, bid, ws, we)
                await _write(s, tid, mid, ws, val, key)
                await s.commit()
                written += 1
    return written


def _main():
    import argparse
    import asyncio

    from sqlalchemy.orm import Session as SyncSession

    from ..db import engine

    ap = argparse.ArgumentParser(description="Seed the Spring B enterprise scorecard.")
    ap.add_argument("--backfill", type=int, default=0, help="also resolve this many recent weeks")
    ap.add_argument("--tenant", default="springb")
    args = ap.parse_args()

    async def go():
        async with engine.begin() as conn:
            def _run(sync_conn):
                sess = SyncSession(bind=sync_conn)
                stats = apply_enterprise(sess)
                sess.flush()
                return stats
            stats = await conn.run_sync(_run)
        print(stats)
        if args.backfill:
            n = await backfill(args.tenant, args.backfill)
            print(f"backfill writes: {n}")

    asyncio.run(go())


if __name__ == "__main__":
    _main()
