/* Sample /api/v1/becollective payload — dev fallback when VITE_API_BASE is unset.
   beCollective now mirrors the Forum: field-driven roster + the operational payload
   (pulse / mg / recruiting). Cohort program, so no GHL subscriptions/payments →
   billing is null, the recover list is empty, and renewals default to auto. */
const U = "https://app.gohighlevel.com/";
export default {
  status: "watch",
  watch: { count: 1, items: ["behind_pace"] },
  members_total: 30,
  roster: {
    total: 30, forum: 0, inner_circle: 0,
    primary: 24, add_on: 6, admin: 2,
    payment_mix: { monthly: 8, quarterly: 3, pif: 16, installments: 3 },
  },
  roster_detail: {
    summary: {
      total: 30, forum: 0, inner_circle: 0, primary: 24, add_on: 6, admin: 2,
      payment_mix: { monthly: 8, quarterly: 3, pif: 16, installments: 3 }, book: 156000,
    },
    count: 32,
    rows: [
      { id: "b1", name: "Ava Sinclair", seg: "BC", kind: "primary", member_type: "Primary Member", tier: "Gold", status: "Active", payment: "pif", amount: 6500, last_payment: null, next_payment: null, enrolled: "2025-01-15", renews: "2026-01-15", brokerage: null, stripe_account: null, event: true, source_url: U },
      { id: "b2", name: "Leah Ndiaye", seg: "BC", kind: "primary", member_type: "Primary Member", tier: "Gold", status: "Active", payment: "monthly", amount: 6500, last_payment: null, next_payment: null, enrolled: "2025-03-04", renews: "2026-03-04", brokerage: null, stripe_account: null, event: true, source_url: U },
      { id: "b3", name: "Priya Raman", seg: "BC", kind: "primary", member_type: "Primary Member", tier: "Silver", status: "Active", payment: "quarterly", amount: 6000, last_payment: null, next_payment: null, enrolled: "2025-05-20", renews: "2026-05-20", brokerage: null, stripe_account: null, event: false, source_url: U },
      { id: "b4", name: "Naomi Feldman", seg: "BC", kind: "primary", member_type: "Primary Member", tier: "Gold", status: "Active", payment: "pif", amount: 6500, last_payment: null, next_payment: null, enrolled: "2024-11-02", renews: "2026-11-02", brokerage: null, stripe_account: null, event: true, source_url: U },
      { id: "b5", name: "Grace Okonkwo", seg: "BC", kind: "add_on", member_type: "Add-On Member", tier: "Silver", status: "Active", payment: "pif", amount: 0, last_payment: null, next_payment: null, enrolled: "2024-11-02", renews: "2026-11-02", brokerage: null, stripe_account: null, event: false, source_url: U },
      { id: "b6", name: "Sofia Marchetti", seg: "BC", kind: "primary", member_type: "Primary Member", tier: "Silver", status: "Active", payment: "installments", amount: 6000, last_payment: null, next_payment: null, enrolled: "2025-02-27", renews: "2026-02-27", brokerage: null, stripe_account: null, event: true, source_url: U },
      { id: "b7", name: "Hana Kim", seg: "BC", kind: "primary", member_type: "Primary Member", tier: "Gold", status: "Active", payment: "monthly", amount: 6500, last_payment: null, next_payment: null, enrolled: "2024-08-19", renews: "2026-08-19", brokerage: null, stripe_account: null, event: true, source_url: U },
      { id: "b8", name: "Bella Osei", seg: "BC", kind: "primary", member_type: "Primary Member", tier: "Silver", status: "Active", payment: "pif", amount: 6000, last_payment: null, next_payment: null, enrolled: "2025-03-03", renews: "2026-03-03", brokerage: null, stripe_account: null, event: false, source_url: U },
      { id: "b9", name: "Program Ops", seg: "BC", kind: "admin", member_type: "Admin", tier: null, status: "Active", payment: null, amount: 0, last_payment: null, next_payment: null, enrolled: null, renews: null, brokerage: null, stripe_account: null, event: false, source_url: U },
    ],
  },
  pl: null,
  // ── operational refinement (v9) — same blocks as the Forum ──
  pulse: {
    members: { value: 30, delta: 2, spark: [24, 25, 26, 28, 29, 30] },
    pipeline: { value: 37, stages: [20, 5, 4, 2] },
    renewals: { book: 84000, count: 5, auto: 84000, needsYou: 0 },
    event: { days: 92, pct: 67, reg: 20, of: 30 },
  },
  action: { failed: 0, recover: 0 },
  recover: [],
  recruiting: {
    total: 37, vipGuests: 8, committed: 4, expected: 3, close_rate_estimate: true,
    stages: [
      { label: "Applied", n: 20 }, { label: "Appointment", n: 5 },
      { label: "Payment sent", n: 4 }, { label: "Onboarding", n: 2 },
    ],
  },
  mg: {
    active: 30, primary: 24, addOn: 6, admin: 2, book: 156000,
    growth: {
      months: ["Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul"],
      total: [20, 21, 22, 24, 25, 26, 27, 28, 29, 29, 30, 30],
      joined: [2, 1, 1, 2, 1, 1, 1, 1, 1, 0, 1, 1],
      lost: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
      netMTD: 0, joinedMTD: 1, lostMTD: 1,
      net12: 10, joined12: 13, lost12: 3, ratePct: 50, retentionPct: 91,
    },
    pay: { pif: 16, monthly: 8, quarterly: 3, installments: 3, lump: 96000, lumpPct: 62 },
    tenure: { avg: 9, first: 3 },
    renewals: {
      book: 84000, count: 5, auto: 84000, needsYou: 0, resigns: 0, failing: 0,
      rows: [
        { name: "Hana Kim", seg: "beCollective", plan: "monthly", v: 6500, date: "Aug 19", state: "auto", first: false, source_url: U },
        { name: "Naomi Feldman", seg: "beCollective", plan: "pif", v: 6500, date: "Nov 2", state: "auto", first: false, source_url: U },
        { name: "Ava Sinclair", seg: "beCollective", plan: "pif", v: 6500, date: "Jan 15", state: "auto", first: false, source_url: U },
        { name: "Priya Raman", seg: "beCollective", plan: "quarterly", v: 6000, date: "May 20", state: "auto", first: false, source_url: U },
        { name: "Bella Osei", seg: "beCollective", plan: "pif", v: 6000, date: "Mar 3", state: "auto", first: false, source_url: U },
      ],
    },
    calendar: [
      { m: "Jul", v: 0 }, { m: "Aug", v: 13000 }, { m: "Sep", v: 0 },
      { m: "Oct", v: 0 }, { m: "Nov", v: 12500 }, { m: "Dec", v: 0 },
    ],
  },
  kpis: [
    { key: "bc_members", label: "Active Members", value: "30", sub: "24 primary · 6 add-on", drill: "bc_roster" },
    { key: "bc_arr", label: "Membership Value", value: "$156K", sub: "25 memberships", drill: "bc_arr" },
    { key: "bc_new_members", label: "New Members", value: "2", sub: "month to date" },
    { key: "bc_pipeline", label: "In Pipeline", value: "37", sub: "recruiting" },
    { key: "bc_registered", label: "Registered", value: "20", sub: "The Shift", drill: "bc_registered" },
    { key: "bc_financed", label: "Financed", value: "14", sub: "payment plans", drill: "bc_financed" },
  ],
  deck: [
    { k: "pipeline", label: "Recruiting pipeline", hero: "37", hero_sub: "in the pipeline", salient: "2 in onboarding", tone: "good" },
    { k: "event", label: "Next event · The Shift", hero: "92", hero_sub: "days out", salient: "10 unregistered · behind pace", tone: "watch" },
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
    days_out: 92, registered: 20, members: 30, guests: 6, unregistered: 10,
    behind_pace: true, pace_note: "40 were registered at this point before the prior event",
  },
  billing: null,
};
