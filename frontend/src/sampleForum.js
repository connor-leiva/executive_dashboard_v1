/* Sample /api/v1/forum payload — dev fallback when VITE_API_BASE is unset.
   Mirrors the mockup's representative numbers so the view renders standalone. */
export default {
  status: "watch",
  watch: { count: 3, items: ["pastdue", "at_risk", "behind_pace"] },
  members_total: 70,
  pl: null,
  kpis: [
    { key: "active_members", label: "Active Members", value: "70", sub: "Forum 26 · Inner Circle 44", drill: "active_members" },
    { key: "forum_arr", label: "Forum ARR", value: "$1.2M", sub: "47 memberships", drill: "forum_arr" },
    { key: "new_members", label: "New Members", value: "0", sub: "month to date" },
    { key: "renewals_due", label: "Renewals Due", value: "0", sub: "July", drill: "renewal_book" },
    { key: "registered", label: "Registered", value: "28", sub: "Park City, UT", drill: "registered" },
    { key: "mrr", label: "MRR", value: "$31K", sub: "monthly subscriptions", drill: "monthly" },
  ],
  deck: [
    { k: "pipeline", label: "Recruiting pipeline", hero: "14", hero_sub: "in the pipeline", salient: "3 invited · $84K near-term", tone: "good" },
    { k: "renewals", label: "Renewals · next 90 days", hero: "$300K", hero_sub: "12 renewals", salient: "3 at risk · $66K", tone: "watch" },
    { k: "event", label: "Next event · Park City", hero: "75", hero_sub: "days out", salient: "42 unregistered · behind pace", tone: "watch" },
    { k: "revq", label: "Revenue quality", hero: "69%", hero_sub: "paid in full", salient: "2 past due · $4.4K", tone: "watch" },
  ],
  funnel: {
    stages: [
      { label: "Applied", v: 14, value: "$392K" },
      { label: "Discovery call booked", v: 9, value: "$252K" },
      { label: "Call held", v: 6, value: "$168K" },
      { label: "Invited · agreement out", v: 3, value: "$84K" },
    ],
    footer: "Q2 cohort: 31 applications → 7 onboarded · 23% application-to-member · avg 21 days to close",
  },
  renewals: {
    rows: [
      { name: "Marcus Tran", seg: "F", month: "Aug", value: "$30K", status: "committed" },
      { name: "Jordan Pierce", seg: "F", month: "Aug", value: "$30K", status: "risk" },
      { name: "Dana Whitfield", seg: "IC", month: "Aug", value: "$18K", status: "talking" },
      { name: "Alicia Romero", seg: "F", month: "Sep", value: "$30K", status: "committed" },
      { name: "Chris Boone", seg: "IC", month: "Sep", value: "$18K", status: "risk" },
      { name: "Sam Kessler", seg: "F", month: "Oct", value: "$30K", status: "talking" },
    ],
    summary: {
      count: 12, value: "$300K",
      mix: { committed: 6, talking: 3, risk: 3 },
      risk_value: "$66K",
      retention: "Trailing 12 mo · 86% logo · 91% dollar retention",
    },
  },
  event: {
    title: "The Forum · Q3 Immersion", where: "Park City, UT", when: "Sep 15–17",
    days_out: 75, registered: 28, members: 70, guests: 4, unregistered: 42,
    behind_pace: true, pace_note: "34 were registered at this point before Scottsdale Q2",
  },
  revq: {
    pif: { value: 828000, count: 33 },
    monthly: { value: 372000, count: 14, sub: "$31K MRR annualized" },
    past_due: { count: 2, value: "$4.4K" },
    bridge: [
      { label: "Jan 1", value: "$1.13M" },
      { label: "New", value: "+$158K" },
      { label: "Churned", value: "−$88K", soft: true },
      { label: "Today", value: "$1.2M", tot: true },
    ],
  },
};
