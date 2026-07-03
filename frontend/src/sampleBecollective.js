/* Sample /api/v1/becollective payload — dev fallback when VITE_API_BASE is unset.
   Cohort program: recruiting funnel + event (no renewals/revq). */
export default {
  status: "watch",
  watch: { count: 1, items: ["behind_pace"] },
  members_total: 30,
  pl: null,
  kpis: [
    { key: "bc_members", label: "Active Members", value: "30", sub: "beCollective", drill: "bc_members" },
    { key: "bc_arr", label: "Membership Value", value: "$156K", sub: "25 memberships", drill: "bc_arr" },
    { key: "bc_new_members", label: "New Members", value: "2", sub: "month to date" },
    { key: "bc_pipeline", label: "In Pipeline", value: "37", sub: "recruiting" },
    { key: "bc_registered", label: "Registered", value: "20", sub: "The Shift", drill: "bc_registered" },
    { key: "bc_financed", label: "Financed", value: "12", sub: "payment plans", drill: "bc_financed" },
  ],
  deck: [
    { k: "pipeline", label: "Recruiting pipeline", hero: "37", hero_sub: "in the pipeline", salient: "2 in onboarding", tone: "good" },
    { k: "event", label: "Next event · The Shift", hero: "—", hero_sub: "days out", salient: "10 unregistered · behind pace", tone: "watch" },
  ],
  funnel: {
    stages: [
      { label: "Applied", v: 20 },
      { label: "Appointment", v: 5 },
      { label: "Payment sent", v: 4 },
      { label: "Onboarding", v: 2 },
    ],
    footer: "6 more in nurture / unresponsive stages (not active deals)",
  },
  renewals: null,
  event: {
    title: "beCollective · The Shift", where: "The Shift", when: "Oct 2026",
    days_out: null, registered: 20, members: 30, guests: 6, unregistered: 10,
    behind_pace: true, pace_note: "40 were registered at this point before the prior event",
  },
  revq: null,
};
