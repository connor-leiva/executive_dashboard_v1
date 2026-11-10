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
_LEGACY_RESOLVER = {"vip": {"forum_vip_guests_added", "forum_vip_guests_sisu"}}
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
        M("leads", "Qualified Guest Leads", **_L10, owner=_SB, goal=14, resolver="forum_vip_guests_spring",
          provenance="live", provenance_from="GHL", source="ghl",
          note="VIP Guest entries whose opportunity source is the VIP page or the guest application.",
          why="Top of the Forum funnel, and Spring’s rock number.",
          detail="Counts a guest once, on entering the VIP Guest stage group, when opportunity.source is VIP Page, forummastermind.com/vip, or forummastermind.com/guest-application. A blank source falls through to the attribution URL (/vip or /guest-application). Sisu sources are Justin’s row. A guest with no source, no Sisu tag and no URL is in neither count.",
          check="Four of the last 19 VIP opportunities had an empty source. Those stay out of both rows until a source, the Sisu tag, or an attribution URL is on the opportunity."),
        M("cpl", "Cost Per Qualified Lead", **_L10, owner=_SB, direction="lte", type="snapshot", unit="usd",
          resolver="sb_cost_per_qualified_lead", provenance="live", provenance_from="Meta", source="meta",
          note="Meta campaign spend ÷ qualified guest leads that week.",
          why="Shows whether the leads are working or just bought.",
          detail="All marketing is digital. Spend is campaign-level Meta insights (account act_587749862890426). Leads are the qualified-guest-lead row. Blank when there are no leads."),
    ], owner_name="Spring"),
    _g("sb_l10_enroll", "Enrollment", "l10", 20, [
        M("vip", "VIP Guests Added", **_L10, owner=_J, goal=7, target_by="75 by 1/18",
          resolver="forum_vip_guests_seats", provenance="live", provenance_from="GHL", source="ghl",
          move_from=("forum", "VIP Guests Added"),
          note="Leads who took a guest seat, every source.",
          why="The next step in the funnel. A miss here shows up at Dallas too late to fix.",
          detail="A seat is VIP Guest: Confirmed, Dallas VIP guest confirmed, or Attended only if they never sat in Confirmed. Opt-In stays on Qualified guest leads. The same person can be both. History before this deploy uses lastStageChangeAt for people still in Confirmed, so anyone who already left that stage is missing.",
          check="GHL search has no stage history. The backfill undercounts guests who already moved on from Confirmed."),
        M("members", "Members Added", **_L10, owner=_J, target_by="20 by 12/31",
          resolver="forum_members_added", provenance="live", provenance_from="GHL", source="ghl",
          move_from=("forum", "Members Added"),
          note="New signed, paid Forum contracts, by GHL enrollment date.",
          why="The output enrollment exists to produce.",
          detail="Active members whose GHL enrollment date falls in the week. Same reading as the Forum tab’s growth chart."),
        M("edge", "Edge New Paid Members", **_L10, owner=_J, resolver="sb_edge_paid_added",
          provenance="live", provenance_from="Stripe", source="stripe",
          note="New paid Edge subscriptions starting this week. Comped plans excluded.",
          why="The Edge’s one weekly number.",
          detail="Legacy Stripe subscriptions classified as Edge, start date in the week, amount above zero, plan name without “comp”. Blank until that feed has synced."),
    ], owner_name="Justin"),
    _g("sb_l10_experience", "Member Experience", "l10", 30, [
        M("red", "Forum Members at Red", **_L10, owner=_SO, direction="lte", type="snapshot",
          provenance="sync",
          note="Members flagged red on attendance: yellow after one missed session, red after three.",
          why="The earliest churn warning, and Sarah’s team can act on it the same week.",
          detail="No Zoom attendance feed. Enter the red count by hand from the Zoom report until one exists."),
        M("renewals", "60-Day Renewals Confirmed", **_L10, owner=_SO, type="rate", unit="pct",
          resolver="sb_renewals_confirmed", provenance="live", provenance_from="GHL", source="ghl",
          note="Members due in the next 60 days whose Renewal Status is Confirmed ÷ all due.",
          why="Protects contracted revenue. A save is worth as much as a sale.",
          detail="Renewal Status on the GHL contact (Confirmed / Not yet / Not renewing) over the renewal date already synced. Not yet, Not renewing, and blank stay in the denominator. Current week only."),
    ], owner_name="Sarah"),
    _g("sb_l10_finance", "Integrator & Finance", "l10", 40, [
        M("active", "Active Paying Forum Members", **_L10, owner=_CL, type="snapshot",
          target_by="125, Date Open", resolver="sb_active_members",
          provenance="live", provenance_from="GHL", source="ghl",
          note="Active non-admin Forum members on the GHL roster. Written for the current week only.",
          why="The true north: it nets new members against churn.",
          detail="As-of the current week, so look-back does not rewrite history. Comped seats are not a separate flag on the Forum roster, so this is every active member and can sit above the Oct 29 paying count of 43.",
          check="Comped members are included until a comp rule is named. The Oct 29 packet’s 43 excluded them."),
        M("cash", "Cash Collected vs. Due", **_L10, owner=_CL, goal=100, type="rate", unit="pct",
          resolver="sb_cash_collected_rate", provenance="live", provenance_from="GHL", source="ghl",
          note="Succeeded Forum dollars ÷ succeeded plus failed Forum dollars this week.",
          why="The collections lever. Failed payments roll up into it.",
          detail="The Forum tab’s stitched payments: imported GHL copies dropped, legacy Stripe merged in. Dollar-weighted. A week with no attempts stays blank."),
        M("netcash", "Net Cash", **_L10, owner=_CL, unit="usd", resolver="sb_net_cash",
          provenance="live", provenance_from="Books", source="qbo",
          note="Cash in minus cash out for the week across The Forum, beCollective, and SB Coaching.",
          why="The weekly profit signal while the year sits at a loss.",
          detail="Deposits, sales receipts and payments in. Purchases, expenses and bill payments out. Transfers and journal entries are skipped. The brokerage and the JV are not included."),
    ], owner_name="Connor"),
    _g("sb_l10_break", "Spring Break", "l10", 50, [
        M("tickets", "Spring Break Tickets Sold", **_L10, owner=_TD, type="snapshot",
          resolver="sb_spring_break_tickets", provenance="live", provenance_from="Stripe", source="stripe",
          note="Cumulative succeeded Spring Break tickets. Off until Nov 27.",
          why="Spring asked for it on Nov 3. On for Black Friday, off in December, back on in January.",
          detail="Succeeded charges on the legacy Stripe account whose product name says Spring Break. VIP and GA are tagged when the name says so. Weeks before Nov 27 stay blank."),
    ], owner_name="No owner yet", season="Off Until Nov 27"),
    _g("sb_d_mkt", "Growth & Marketing", "dept", 110, [
        M("optins", "Net New Opted-In Contacts", owner=_SB, move_from=("spring_b", "Net New Opted-In Contacts"),
          note="New opt-ins minus unsubscribes, tagged by brand.",
          detail="On the board today as hand-entered. The sync does not store opt-ins."),
        M("seatconv", "Guest Seat Page Conversion", owner=_SB, type="rate", unit="pct", provenance="sync",
          note="Seat requests ÷ unique page visitors.",
          detail="Page visits are not synced."),
        M("adspend", "Ad Spend vs. Budget", owner=_SB, type="rate", unit="pct", goal=100, direction="lte",
          resolver="sb_ad_spend_vs_budget", provenance="live", provenance_from="Meta", source="meta",
          note="Paid-media spend this week ÷ the planned weekly budget.",
          detail="The plan is the sum of the weekly budgets on the Ads tab. The cell stays blank until one is entered."),
        M("mail", "Direct Mail Response Rate", owner=_SB, type="rate", unit="pct", cadence="event",
          cadence_label="Per Drop",
          note="Tagged responses ÷ pieces delivered, plus qualified leads per drop.",
          detail="Entered per drop. The next one goes out around Nov 14."),
        M("regs", "Registrations Per Campaign", owner=_SB, cadence="event", cadence_label="Per Campaign",
          note="Registrations for each webinar, lead magnet or campaign, logged when it closes.",
          detail="Registrations sync by event tag for Forum gatherings only."),
        M("assets", "Content Assets Shipped vs. Planned", owner=_SB,
          move_from=("spring_b", "Content Assets Shipped vs. Planned"),
          note="Assets and campaigns live on their calendar date.",
          detail="Hand-entered."),
        M("open", "Email Open Rate Floor", owner=_SB, type="rate", unit="pct",
          move_from=("spring_b", "Email Open Rate Floor"),
          note="Open rate per send, by brand. Any send under the floor gets flagged.",
          detail="Hand-entered. The floor itself is not set yet."),
    ], owner_name="Spring", dept_key="marketing", dept_name="Marketing", lead_name="Spring",
       reviewed_in="Connor’s 1:1 with Spring"),
    _g("sb_d_pipeline", "Guest Pipeline", "dept", 120, [
        M("speed", "Calls Booked Within 24 Hours", owner=_MG, type="rate", unit="pct", goal=100,
          note="New seat requests with a call booked within 24 hours ÷ new seat requests.",
          detail="Opt-in times and calendar bookings are not joined, and no Forum calendar has had a booking since July. Enter by hand."),
        M("convos", "Guest Conversations Completed", owner=_MG,
          note="Live conversations with prospective guests.",
          detail="Matt’s spreadsheet until GHL is set up for him."),
        M("apptset", "Appointments Set", owner=_MG, move_from=("forum", "Appointments Set"),
          note="Appointments booked on the Forum calendars.",
          detail="Hand-entered. A read of every Forum calendar from Oct 1 to Nov 10 returned zero appointments. Forum VIP Guest Application (6q9ga2aGN28ryW990aR9) has been inactive since July, and Matt Griner is on no calendar. Adding that calendar id will not fill these weeks.",
          check="Zero appointments on every Forum calendar, Oct 1–Nov 10. Enter the number from Matt’s record. Reactivate 6q9ga2aGN28ryW990aR9 only when he actually books there."),
        M("apptmet", "Appointments Met", owner=_MG, move_from=("forum", "Appointments Met"),
          note="Booked appointments that actually happened.",
          detail="Hand-entered, for the same reason as Appointments set: the calendars are empty, so an auto-count would keep writing zero.",
          check="Same calendar finding as Appointments set. Do not treat a zero from GHL as the week’s number."),
        M("nextsteps", "Next Steps Documented", owner=_MG, type="rate", unit="pct", goal=100,
          note="Open guests with a next step and a follow-up date ÷ open guests.",
          detail="Follow-up dates live on GHL opportunities, which the sync does not store."),
        M("open21", "Decisions Open Past 21 Days", owner=_MG, type="snapshot", direction="lte", goal=0,
          scores_zero=True, resolver="sb_decisions_open", provenance="live", provenance_from="GHL", source="ghl",
          note="VIP guests still open whose stage has not changed in 21 days.",
          detail="Current open sales-funnel opportunities in a VIP Guest stage, last stage change 21 or more days ago. Current week only."),
    ], owner_name="Matt", dept_key="enrollment", dept_name="Enrollment & Edge", lead_name="Justin",
       reviewed_in="Justin’s 1:1 with Connor"),
    _g("sb_d_retention", "Retention", "dept", 130, [
        M("referrals", "Member Referrals Generated", owner=_J, move_from=("forum", "Member Referrals Generated"),
          note="Qualified guest leads referred by current members.", detail="Hand-entered."),
        M("retconv", "Retention Conversations On Time", owner=_J, type="rate", unit="pct", goal=100,
          note="Departing members whose retention conversation happened 30+ days before their last day.",
          detail="Offboarding runs in ClickUp, which Axcion does not read."),
        M("save", "Retention Save Rate", owner=_J, type="rate", unit="pct", cadence="monthly",
          note="Departing members who renewed or moved to another offer ÷ departing.",
          detail="Hand-entered, monthly."),
        M("forecast", "Forecast Accuracy", owner=_J, type="rate", unit="pct", cadence="monthly",
          note="Enrollments forecast at the start of the month vs. actual.", detail="Hand-entered."),
    ], owner_name="Justin", dept_key="enrollment", dept_name="Enrollment & Edge", lead_name="Justin",
       reviewed_in="Justin’s 1:1 with Connor"),
    _g("sb_d_edge", "The Edge", "dept", 140, [
        M("edgeatt", "Live-Session Attendance", owner=_J, type="rate", unit="pct", provenance="sync",
          note="Paid members at one or more live sessions in the trailing 30 days.",
          detail="Same missing Zoom feed as Forum members at red."),
        M("webinars", "Edge Webinars Held", owner=_J, cadence="monthly",
          note="Edge webinars delivered.", detail="Hand-entered."),
        M("edgeleads", "Edge Leads", owner=_J, provenance="sync",
          note="New opt-ins and webinar registrations tagged Edge.",
          detail="Opt-ins tagged Edge are not stored."),
        M("edge2forum", "Edge Members to Forum Guests", owner=_J, cadence="monthly",
          note="Edge members who registered as Forum VIP guests.",
          detail="Both sides sync, but matching them by email is not reliable enough to auto-count. Enter monthly."),
    ], owner_name="Justin", dept_key="enrollment", dept_name="Enrollment & Edge", lead_name="Justin",
       reviewed_in="Justin’s 1:1 with Connor"),
    _g("sb_d_ops", "Operations", "dept", 150, [
        M("notices", "Cancellation Notices Received", owner=_SO, direction="lte",
          note="Notices this week, with last paid date and tenure.",
          detail="Notices arrive by email. Enter by hand."),
        M("onboard", "Onboarding Past 14 Days", owner=_SO, type="snapshot", direction="lte", goal=0,
          scores_zero=True, note="New members not fully onboarded 14 or more days after purchase.",
          detail="Onboarding runs in ClickUp."),
        M("stuck", "Stuck Items Over 1 Business Day", owner=_SO, type="snapshot", direction="lte", goal=0,
          scores_zero=True, note="Member-experience tasks stuck for more than one business day.",
          detail="Tracked in ClickUp."),
        M("failed", "Failed Payments Open Past 7 Days", owner=_SO, type="snapshot", direction="lte", goal=0,
          scores_zero=True, resolver="sb_failed_payments_open", provenance="live", provenance_from="GHL",
          source="ghl",
          note="Failed GHL payments older than 7 days with no later success.",
          detail="Matched to a later success on the same subscription or the same email. Current week only."),
    ], owner_name="Sarah", dept_key="experience", dept_name="Member Experience", lead_name="Sarah",
       reviewed_in="Sarah’s 1:1 with Connor"),
    _g("sb_d_support", "Replays and Records", "dept", 160, [
        M("replays", "Replays and Handouts Within 24 Hours", owner=_N, type="rate", unit="pct", goal=100,
          note="Sessions whose replay and handouts posted within 24 hours ÷ sessions held.",
          detail="Hand-entered."),
        M("renewdates", "Renewal Dates Current", owner=_N, type="rate", unit="pct", goal=100,
          resolver="sb_renewal_dates_current", provenance="live", provenance_from="GHL", source="ghl",
          note="Active members with a renewal date on the membership record ÷ active members.",
          detail="The date field GHL already syncs. There is no separate “verified” flag. Current week only."),
    ], owner_name="Nicole", dept_key="experience", dept_name="Member Experience", lead_name="Sarah",
       reviewed_in="Sarah’s 1:1 with Connor"),
    _g("sb_d_welcome", "Member Welcome", "dept", 170, [
        M("welcomepost", "Welcome Post Within 14 Days", owner=_B, type="rate", unit="pct", goal=100,
          note="Welcome posts live within 14 days of intake complete ÷ intakes completed.",
          detail="Hand-entered."),
    ], owner_name="Bryna", dept_key="experience", dept_name="Member Experience", lead_name="Sarah",
       reviewed_in="Sarah’s 1:1 with Connor"),
    _g("sb_d_fin", "Finance", "dept", 180, [
        M("spend", "Spend vs. Budget", owner=_CL, type="rate", unit="pct", goal=100, direction="lte",
          note="Week and month-to-date spend by category ÷ budget.",
          detail="Books has actuals from QuickBooks. No budget is stored, so the ratio is hand-entered."),
        M("close", "Monthly Close On Time", owner=_CL, cadence="monthly",
          note="Business day the books close each month.", detail="Hand-entered."),
    ], owner_name="Connor", dept_key="finance", dept_name="Finance", lead_name="Connor",
       reviewed_in="Connor’s 1:1 with Spring"),
    _g("sb_monthly", "Monthly", "monthly", 200, [
        M("revenue", "Revenue vs. Annual Target", tier="monthly", cadence="monthly", owner=_CL, unit="usd",
          type="snapshot", note="Revenue YTD from the close, consolidated.",
          detail="Enter the latest YTD figure. $3.5M is the 2026 target; $1.8M was through Oct 11.",
          target_by="$3.5M Annual"),
        M("netprofit", "Net Profit vs. Annual Target", tier="monthly", cadence="monthly", owner=_CL, unit="usd",
          type="snapshot", note="Net profit YTD from the close.",
          detail="Enter the latest figure. Target $1.7M; –$270K as of Oct 29.",
          target_by="$1.7M Annual"),
        M("byproduct", "Profit by Product", tier="monthly", cadence="monthly", owner=_CL, unit="usd",
          type="snapshot", note="One-page P&L per product. Enter the figure you want on the L10.",
          detail="Forum and beCollective close on their own books. Edge and Spring Break need class tracking inside SB Coaching."),
        M("backlog", "Contracted Revenue Backlog", tier="monthly", cadence="monthly", owner=_CL, unit="usd",
          type="snapshot", note="Remaining scheduled payments on signed, active contracts.",
          detail="$590K was the rocks-hit projection, not an actual."),
        M("edgemrr", "Edge MRR and Cancellations", tier="monthly", cadence="monthly", owner=_J,
          note="Enter monthly payers. Note cancellations in the week you review it.",
          detail="13 monthly payers and 2 canceled as of Oct 29. Stored as the payer count."),
        M("sponsor", "Sponsorship Revenue", tier="monthly", cadence="monthly", owner=_SB, unit="usd",
          type="snapshot", note="Sponsorship dollars signed YTD.",
          detail="$129K of $250K as of Oct 29.", target_by="$250K for 2026"),
        M("cac", "CAC Per New Forum Member", tier="monthly", cadence="monthly", owner=_SB, unit="usd",
          type="snapshot", direction="lte",
          note="Marketing spend plus sales comp ÷ new paid Forum members.",
          detail="The Ads module computes attributed and blended CAC for ad-traced enrollments. Mail and comp are not in that figure, so the L10 number stays hand-entered."),
        M("bcmembers", "beCollective Active Members", tier="monthly", cadence="monthly", owner=_TD,
          note="Paid beCollective members on a current term.",
          detail="87 of 130 as of Oct 29.", target_by="130"),
        M("upgrade", "Upgrade RevShare", tier="monthly", cadence="monthly", owner=_TD, unit="usd",
          type="snapshot", note="Revenue share received.",
          detail="$561 of $900 as of Oct 29. Only if Upgrade stays in Spring B.", target_by="$900"),
    ], owner_name="Connor"),
    _g("sb_event", "Per Event", "event", 210, [
        M("show", "Guest Show Rate", tier="event", cadence="event", owner=_J, type="rate", unit="pct",
          note="Registered guests who attended ÷ registered.",
          detail="Entered at the first L10 after each gathering.",
          anchors=[{"label": "Scottsdale", "date": "2026-11-11"}, {"label": "Dallas", "date": "2027-01-18"}]),
        M("closerate", "Guest Close Rate", tier="event", cadence="event", owner=_J, type="rate", unit="pct",
          goal=40, note="Attended guests who enrolled within 21 days ÷ attended.",
          detail="Goal is at least 40%. The path-to-90 math applies 40% to every registered Dallas guest, which assumes everyone shows.",
          anchors=[{"label": "Scottsdale", "date": "2026-11-11"}, {"label": "Dallas", "date": "2027-01-18"}]),
    ], owner_name="Justin"),
    _g("sb_tasks", "On-Time Task Completion", "person", 300, [
        M(f"task-{ini.lower()}", "On-Time Task Completion", tier="person", type="rate", unit="pct", goal=90,
          owner=(ini, name), provenance="sync", core=False,
          note="ClickUp tasks completed by their due date ÷ tasks due.",
          detail="ClickUp is not connected. Enter the percent by hand until it is.")
        for ini, name in (_SB, _CL, _J, _SO, _MG, _N, _B)
    ], owner_name="Connor"),
    _g("sb_bc", "beCollective", "elsewhere", 400, [
        M("bc-active", "Active Members", tier="elsewhere", cadence="monthly", owner=_TD, core=False,
          target_by="130 (at 87)", note="Paid members on a current term."),
        M("bc-calls", "Call Attendance", tier="elsewhere", owner=_SO, core=False, provenance="sync",
          note="Attendance flags applied to beCollective calls."),
        M("bc-mighty", "Mighty Engagement", tier="elsewhere", owner=_SO, core=False, provenance="sync",
          note="Weekly active members in Mighty."),
        M("bc-renew", "Renewals", tier="elsewhere", cadence="monthly", owner=_SO, core=False, type="rate", unit="pct",
          note="Members renewing ÷ members reaching term end."),
        M("bc-nurture", "Nurture Emails Sent", tier="elsewhere", owner=_TD, core=False,
          note="Planned nurture emails sent on schedule."),
        M("bc-list", "Qualified List Growth", tier="elsewhere", owner=_TD, core=False,
          note="New contacts tagged beCollective who fit the target profile."),
        M("bc-feb", "February Event Sign-Ups", tier="elsewhere", owner=_TD, core=False,
          note="Women registered for the February event."),
        M("bc-funnel", "Launch Funnel", tier="elsewhere", owner=_TD, core=False,
          target_by="2,000 Registrants", note="Launch only. Last launch was 1,400."),
        M("bc-match", "Registrant Profile Match", tier="elsewhere", owner=_TD, core=False, type="rate", unit="pct",
          note="Launch only. Share of registrants who fit the target profile."),
    ], owner_name="beCollective", reviewed_in="Reviewed with Spring, Julie and Sarah"),
    _g("sb_break_project", "Spring Break Project", "elsewhere", 410, [
        M("sbp-rpa", "Revenue Per Attendee", tier="elsewhere", owner=_TD, core=False, unit="usd", type="snapshot",
          note="Ticket revenue ÷ tickets sold. Needs the price."),
        M("sbp-cpt", "Cost Per Ticket Sold", tier="elsewhere", owner=_TD, core=False, unit="usd",
          direction="lte", type="snapshot", note="Spring Break marketing spend ÷ tickets sold."),
        M("sbp-conv", "Attendees Converted", tier="elsewhere", cadence="event", owner=_TD, core=False,
          note="Attendees who became Forum VIP guests or Edge members after the event."),
        M("sbp-sponsor", "Spring Break Sponsor Revenue", tier="elsewhere", owner=_SB, core=False, unit="usd",
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


def apply_enterprise(session: Session, tenant_id=None) -> dict:
    """Upsert the enterprise board onto membership businesses. Does not delete values.

    `tenant_id` limits the pass to one tenant. The migration leaves it unset so every
    membership business is seeded; tests pass their own tenant so a shared database's
    other companies stay as they are.
    """
    created = adopted = retired = 0
    q = select(Business).where(Business.kind == "membership")
    if tenant_id is not None:
        q = q.where(Business.tenant_id == tenant_id)
    businesses = session.scalars(q).all()
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
    # The catalog owns the label. A refresh renames the row and leaves its values alone.
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
    replaceable = _LEGACY_RESOLVER.get(row["slug"], set())
    if row["slug"] in _FORCE_HAND:
        m.resolver_key = None
        m.source_spec = None
        m.provenance = "hand"
        m.source = "manual"
    elif want and (m.resolver_key is None or m.resolver_key in replaceable or (adopting and m.resolver_key == want)):
        if m.resolver_key != want:
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
ROUND2_KEYS = (
    "sb_cost_per_qualified_lead", "forum_vip_guests_seats", "sb_edge_paid_added",
    "sb_cash_collected_rate", "sb_net_cash", "sb_spring_break_tickets", "sb_renewals_confirmed",
)
ROUND2_SLUGS = ("cpl", "vip", "edge", "cash", "netcash", "tickets", "renewals")


# Startup runs this once. A row in scorecard_backfill_mark is the lock.
BACKFILL_MARK = "springb_enterprise_13w"


async def _fill_cell(s, tenant_id, metric_id, week_start: dt.date, val, key: str) -> str:
    """Write one auto value without ever clearing a cell or touching a hand-entered one.

    Returns filled | updated | unchanged | skipped_manual | blank | unavailable.
    A resolver result of None (looked, nothing to score) leaves the stored cell alone.
    """
    from decimal import Decimal

    from ..models import ScorecardValue
    from .scorecard_resolvers import UNAVAILABLE

    row = (await s.execute(select(ScorecardValue).where(
        ScorecardValue.metric_id == metric_id,
        ScorecardValue.week_start == week_start))).scalar_one_or_none()
    if val is UNAVAILABLE:
        return "unavailable"
    if val is None:
        return "blank"
    dec = Decimal(str(val))
    if row is None or row.value is None:
        if row is None:
            s.add(ScorecardValue(tenant_id=tenant_id, metric_id=metric_id, week_start=week_start,
                                 value=dec, source="resolver"))
        else:
            row.value, row.source, row.entered_by = dec, "resolver", None
        return "filled"
    if row.source == "manual":
        return "skipped_manual"
    if row.value == dec:
        return "unchanged"
    row.value, row.source, row.entered_by = dec, "resolver", None
    return "updated"


async def backfill_tenant(tenant_id, weeks: int = 13, keys=None) -> dict:
    """Fill the wired Spring B rows for `weeks` recent weeks. Other boards are not queried."""
    from ..db import SessionLocal
    from .scorecard_resolvers import RESOLVERS, UNAVAILABLE, _recent_weeks

    async with SessionLocal() as s:
        rows = (await s.execute(
            select(ScorecardMetric.id, ScorecardMetric.slug, ScorecardMetric.resolver_key,
                   ScorecardGroup.business_id)
            .join(ScorecardGroup, ScorecardMetric.group_id == ScorecardGroup.id)
            .where(ScorecardMetric.tenant_id == tenant_id, ScorecardMetric.active.is_(True),
                   ScorecardMetric.resolver_key.in_(tuple(keys or BACKFILL_KEYS))))).all()
    today = dt.date.today()
    windows = _recent_weeks(today, weeks)
    stats = {"weeks": weeks, "metrics": len(rows), "filled": 0, "updated": 0,
             "unchanged": 0, "skipped_manual": 0, "blank": 0, "unavailable": 0, "errors": 0,
             "sample": {}}
    current_monday = windows[0][0] if windows else None
    for mid, slug, key, bid in rows:
        fn = RESOLVERS.get(key)
        if fn is None:
            continue
        for ws, we in windows:
            async with SessionLocal() as s:
                try:
                    val = await fn(s, tenant_id, bid, ws, we)
                    outcome = await _fill_cell(s, tenant_id, mid, ws, val, key)
                    await s.commit()
                except Exception as exc:  # one bad week must not stop the rest
                    await s.rollback()
                    stats["errors"] += 1
                    print(f"[springb_backfill] {slug or key} {ws} error {type(exc).__name__}", flush=True)
                    continue
            stats[outcome] = stats.get(outcome, 0) + 1
            if ws == current_monday and slug:
                stats["sample"][slug] = "unavailable" if val is UNAVAILABLE else val
    return stats


async def run_backfill_once(weeks: int = 13) -> dict | None:
    """Run the 13-week fill for every tenant that has these rows, unless the marker is already there.

    Returns the stats of the run, or None when every such tenant was already marked.
    """
    from sqlalchemy.exc import IntegrityError

    from ..db import SessionLocal
    from ..models import ScorecardBackfillMark

    async with SessionLocal() as s:
        tids = (await s.execute(
            select(ScorecardMetric.tenant_id)
            .where(ScorecardMetric.active.is_(True),
                   ScorecardMetric.resolver_key.in_(BACKFILL_KEYS))
            .distinct())).scalars().all()
    if not tids:
        print("[springb_backfill] no wired metrics", flush=True)
        return None
    async with SessionLocal() as s:
        marked = set((await s.execute(
            select(ScorecardBackfillMark.tenant_id).where(
                ScorecardBackfillMark.key == BACKFILL_MARK,
                ScorecardBackfillMark.tenant_id.in_(tids)))).scalars().all())
    pending = [t for t in tids if t not in marked]
    if not pending:
        print("[springb_backfill] already done", flush=True)
        return None
    all_stats = []
    for tid in pending:
        print(f"[springb_backfill] start tenant={tid} weeks={weeks}", flush=True)
        stats = await backfill_tenant(tid, weeks)
        async with SessionLocal() as s:
            s.add(ScorecardBackfillMark(tenant_id=tid, key=BACKFILL_MARK, stats=stats))
            try:
                await s.commit()
            except IntegrityError:
                await s.rollback()  # another process finished the same mark first
        sample = " ".join(f"{k}={v}" for k, v in sorted(stats["sample"].items()))
        print(
            f"[springb_backfill] done tenant={tid} weeks={stats['weeks']} metrics={stats['metrics']} "
            f"filled={stats['filled']} updated={stats['updated']} unchanged={stats['unchanged']} "
            f"skipped_manual={stats['skipped_manual']} blank={stats['blank']} "
            f"unavailable={stats['unavailable']} errors={stats['errors']}",
            flush=True)
        print(f"[springb_backfill] sample {sample}", flush=True)
        all_stats.append(stats)
    return {"tenants": len(all_stats), "runs": all_stats}


ROUND2_MARK = "springb_round2_13w"


async def _primary_week_sample(tenant_id, weeks: int = 3) -> str:
    """Latest weeks of the changed L10 rows on the membership business the app actually shows."""
    from ..db import SessionLocal
    from ..models import Business, ScorecardValue
    from .scorecard_resolvers import _recent_weeks
    mondays = [ws for ws, _we in _recent_weeks(dt.date.today(), weeks)]
    async with SessionLocal() as s:
        biz = (await s.execute(select(Business).where(
            Business.tenant_id == tenant_id, Business.kind == "membership")
            .order_by(Business.sort_order).limit(1))).scalar_one_or_none()
        if biz is None:
            return "no membership business"
        rows = (await s.execute(
            select(ScorecardMetric.slug, ScorecardMetric.id)
            .join(ScorecardGroup, ScorecardMetric.group_id == ScorecardGroup.id)
            .where(ScorecardGroup.business_id == biz.id, ScorecardMetric.slug.in_(ROUND2_SLUGS),
                   ScorecardMetric.active.is_(True)))).all()
        vals = (await s.execute(select(ScorecardValue.metric_id, ScorecardValue.week_start, ScorecardValue.value)
            .where(ScorecardValue.metric_id.in_([i for _s, i in rows] or [None]),
                   ScorecardValue.week_start.in_(mondays)))).all() if rows else []
    by = {(mid, ws): (None if v is None else float(v)) for mid, ws, v in vals}
    parts = []
    for slug, mid in sorted(rows):
        cells = " ".join(f"{ws.isoformat()}={by.get((mid, ws), '—')}" for ws in mondays)
        parts.append(f"{slug}[{cells}]")
    return f"business={biz.key} " + " ".join(parts)


async def run_round2_backfill(weeks: int = 13) -> dict | None:
    """Refresh the round-2 rows once. Hand-entered cells are not overwritten."""
    from sqlalchemy.exc import IntegrityError

    from ..db import SessionLocal
    from ..models import Business, ScorecardBackfillMark
    from .sync import materialize_vip_seat_proxies

    async with SessionLocal() as s:
        tids = (await s.execute(
            select(ScorecardMetric.tenant_id).where(
                ScorecardMetric.active.is_(True),
                ScorecardMetric.resolver_key.in_(ROUND2_KEYS)).distinct())).scalars().all()
    if not tids:
        print("[springb_backfill] round2 no wired metrics", flush=True)
        return None
    async with SessionLocal() as s:
        marked = set((await s.execute(
            select(ScorecardBackfillMark.tenant_id).where(
                ScorecardBackfillMark.key == ROUND2_MARK,
                ScorecardBackfillMark.tenant_id.in_(tids)))).scalars().all())
    pending = [t for t in tids if t not in marked]
    if not pending:
        print("[springb_backfill] round2 already done", flush=True)
        return None
    out = []
    for tid in pending:
        async with SessionLocal() as s:
            bids = (await s.execute(select(Business.id).where(Business.tenant_id == tid))).scalars().all()
            proxies = 0
            for bid in bids:
                proxies += await materialize_vip_seat_proxies(s, tid, bid)
            await s.commit()
        print(f"[springb_backfill] round2 start tenant={tid} weeks={weeks} seat_proxies={proxies}", flush=True)
        stats = await backfill_tenant(tid, weeks, keys=ROUND2_KEYS)
        sample = await _primary_week_sample(tid, 3)
        stats["primary_sample"] = sample
        async with SessionLocal() as s:
            s.add(ScorecardBackfillMark(tenant_id=tid, key=ROUND2_MARK, stats={
                k: v for k, v in stats.items() if k != "sample"}))
            try:
                await s.commit()
            except IntegrityError:
                await s.rollback()
        print(
            f"[springb_backfill] round2 done tenant={tid} weeks={stats['weeks']} metrics={stats['metrics']} "
            f"filled={stats['filled']} updated={stats['updated']} unchanged={stats['unchanged']} "
            f"skipped_manual={stats['skipped_manual']} blank={stats['blank']} "
            f"unavailable={stats['unavailable']} errors={stats['errors']}",
            flush=True)
        print(f"[springb_backfill] round2 values {sample}", flush=True)
        out.append(stats)
    return {"tenants": len(out), "runs": out}


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
            from ..models import Tenant
            from ..db import SessionLocal
            async with SessionLocal() as s:
                tenant = (await s.execute(select(Tenant).where(Tenant.slug == args.tenant))).scalar_one_or_none()
            if tenant is None:
                raise SystemExit(f"no tenant {args.tenant}")
            stats = await backfill_tenant(tenant.id, args.backfill)
            print(f"backfill {stats}")

    asyncio.run(go())


if __name__ == "__main__":
    _main()
