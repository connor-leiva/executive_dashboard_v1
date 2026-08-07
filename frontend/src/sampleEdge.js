/* Sample /api/v1/edge payload — dev fallback when VITE_API_BASE is unset. The Edge mirrors the
   Forum: field-driven roster + the operational payload (pulse / mg / recruiting), in its own
   edge_ drill namespace. Steady membership; payments come off Spring's shared legacy Stripe. */
const U = "https://app.gohighlevel.com/";
export default {
  status: "watch",
  watch: { count: 1, items: ["payments"] },
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
      { id: "e1", name: "Ava Sinclair", seg: "EDGE", kind: "primary", member_type: "Primary Member", tier: "Gold", status: "Active", payment: "pif", amount: 6500, last_payment: null, next_payment: null, enrolled: "2025-01-15", renews: "2026-01-15", brokerage: null, stripe_account: null, event: true, source_url: U },
      { id: "e2", name: "Leah Ndiaye", seg: "EDGE", kind: "primary", member_type: "Primary Member", tier: "Gold", status: "Active", payment: "monthly", amount: 6500, last_payment: null, next_payment: null, enrolled: "2025-03-04", renews: "2026-03-04", brokerage: null, stripe_account: null, event: true, source_url: U },
      { id: "e3", name: "Priya Raman", seg: "EDGE", kind: "primary", member_type: "Primary Member", tier: "Silver", status: "Active", payment: "quarterly", amount: 6000, last_payment: null, next_payment: null, enrolled: "2025-05-20", renews: "2026-05-20", brokerage: null, stripe_account: null, event: false, source_url: U },
      { id: "e4", name: "Naomi Feldman", seg: "EDGE", kind: "primary", member_type: "Primary Member", tier: "Gold", status: "Active", payment: "pif", amount: 6500, last_payment: null, next_payment: null, enrolled: "2024-11-02", renews: "2026-11-02", brokerage: null, stripe_account: null, event: true, source_url: U },
      { id: "e5", name: "Grace Okonkwo", seg: "EDGE", kind: "add_on", member_type: "Add-On Member", tier: "Silver", status: "Active", payment: "pif", amount: 0, last_payment: null, next_payment: null, enrolled: "2024-11-02", renews: "2026-11-02", brokerage: null, stripe_account: null, event: false, source_url: U },
      { id: "e6", name: "Sofia Marchetti", seg: "EDGE", kind: "primary", member_type: "Primary Member", tier: "Silver", status: "Active", payment: "installments", amount: 6000, last_payment: null, next_payment: null, enrolled: "2025-02-27", renews: "2026-02-27", brokerage: null, stripe_account: null, event: true, source_url: U },
      { id: "e7", name: "Hana Kim", seg: "EDGE", kind: "primary", member_type: "Primary Member", tier: "Gold", status: "Active", payment: "monthly", amount: 6500, last_payment: null, next_payment: null, enrolled: "2024-08-19", renews: "2026-08-19", brokerage: null, stripe_account: null, event: true, source_url: U },
      { id: "e8", name: "Bella Osei", seg: "EDGE", kind: "primary", member_type: "Primary Member", tier: "Silver", status: "Active", payment: "pif", amount: 6000, last_payment: null, next_payment: null, enrolled: "2025-03-03", renews: "2026-03-03", brokerage: null, stripe_account: null, event: false, source_url: U },
      { id: "e9", name: "Program Ops", seg: "EDGE", kind: "admin", member_type: "Admin", tier: null, status: "Active", payment: null, amount: 0, last_payment: null, next_payment: null, enrolled: null, renews: null, brokerage: null, stripe_account: null, event: false, source_url: U },
    ],
  },
  pl: null,
  pulse: {
    members: { value: 30, delta: 2, spark: [24, 25, 26, 28, 29, 30] },
    pipeline: { value: 37, stages: [20, 5, 4, 2] },
    renewals: { book: 84000, count: 5, auto: 84000, needsYou: 0 },
    event: { days: 92, pct: 67, reg: 20, of: 30 },
  },
  action: { failed: 1, recover: 542 },
  recover: [{ name: "Sofia Marchetti", amt: 542, when: "Jul 2", source_url: U }],
  recruiting: {
    total: 37, vipGuests: 8, committed: 4, expected: 3, close_rate_estimate: true,
    stages: [
      { label: "Applied", n: 20 }, { label: "Appointment", n: 5 },
      { label: "Committed", n: 4 }, { label: "Onboarding", n: 2 },
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
        { name: "Hana Kim", seg: "The Edge", plan: "monthly", v: 6500, date: "Aug 19", state: "auto", first: false, source_url: U },
        { name: "Naomi Feldman", seg: "The Edge", plan: "pif", v: 6500, date: "Nov 2", state: "auto", first: false, source_url: U },
        { name: "Ava Sinclair", seg: "The Edge", plan: "pif", v: 6500, date: "Jan 15", state: "auto", first: false, source_url: U },
        { name: "Priya Raman", seg: "The Edge", plan: "quarterly", v: 6000, date: "May 20", state: "auto", first: false, source_url: U },
        { name: "Bella Osei", seg: "The Edge", plan: "pif", v: 6000, date: "Mar 3", state: "auto", first: false, source_url: U },
      ],
    },
    calendar: [
      { m: "Jul", v: 0 }, { m: "Aug", v: 13000 }, { m: "Sep", v: 0 },
      { m: "Oct", v: 0 }, { m: "Nov", v: 12500 }, { m: "Dec", v: 0 },
    ],
  },
  kpis: [
    { key: "edge_members", label: "Active Members", value: "30", sub: "24 primary · 6 add-on", drill: "edge_roster" },
    { key: "edge_arr", label: "Membership Value", value: "$156K", sub: "25 memberships", drill: "edge_arr" },
    { key: "edge_new_members", label: "New Members", value: "2", sub: "month to date" },
    { key: "edge_pipeline", label: "In Pipeline", value: "37", sub: "recruiting" },
    { key: "edge_registered", label: "Registered", value: "20", sub: "The Edge Intensive", drill: "edge_registered" },
    { key: "edge_financed", label: "Financed", value: "14", sub: "payment plans", drill: "edge_financed" },
  ],
  deck: [
    { k: "pipeline", label: "Recruiting pipeline", hero: "37", hero_sub: "in the pipeline", salient: "2 in onboarding", tone: "good" },
    { k: "event", label: "Next event · The Edge Intensive", hero: "92", hero_sub: "days out", salient: "10 unregistered · behind pace", tone: "watch" },
  ],
  funnel: {
    stages: [
      { label: "Applied", v: 20 },
      { label: "Appointment", v: 5 },
      { label: "Committed", v: 4 },
      { label: "Onboarding", v: 2 },
    ],
    footer: "6 more in nurture / unresponsive stages (not active deals)",
  },
  renewals: null,
  event: {
    title: "The Edge Intensive", where: "The Edge Intensive", when: "Oct 2026",
    days_out: 92, registered: 20, members: 30, guests: 6, unregistered: 10,
    behind_pace: true, pace_note: "40 were registered at this point before the prior event",
  },
  billing: {
    available: true, basis: "cash",
    span: { start: "2026-04-01", end: "2026-07-08" },
    net_cash: 71500, full_year: 156000, gross: 78000, refunded: 6500, txn_count: 22,
    failed_amount: 542, failed_count: 1, past_due: 1, past_due_amount: 542,
    monthly: [
      { month: "Jan", ym: "2026-01", actual: 0, projected: 0, net: 0, mtd: false, is_projected: false },
      { month: "Feb", ym: "2026-02", actual: 0, projected: 0, net: 0, mtd: false, is_projected: false },
      { month: "Mar", ym: "2026-03", actual: 0, projected: 0, net: 0, mtd: false, is_projected: false },
      { month: "Apr", ym: "2026-04", actual: 13000, projected: 0, net: 13000, mtd: false, is_projected: false },
      { month: "May", ym: "2026-05", actual: 26000, projected: 0, net: 26000, mtd: false, is_projected: false },
      { month: "Jun", ym: "2026-06", actual: 19500, projected: 0, net: 19500, mtd: false, is_projected: false },
      { month: "Jul", ym: "2026-07", actual: 13000, projected: 1626, net: 14626, mtd: true, is_projected: false },
      { month: "Aug", ym: "2026-08", actual: 0, projected: 4878, net: 4878, mtd: false, is_projected: true },
      { month: "Sep", ym: "2026-09", actual: 0, projected: 4878, net: 4878, mtd: false, is_projected: true },
      { month: "Oct", ym: "2026-10", actual: 0, projected: 4878, net: 4878, mtd: false, is_projected: true },
      { month: "Nov", ym: "2026-11", actual: 0, projected: 4878, net: 4878, mtd: false, is_projected: true },
      { month: "Dec", ym: "2026-12", actual: 0, projected: 4878, net: 4878, mtd: false, is_projected: true },
    ],
    forecast: { next_30: 4878, next_90: 14634, rest_of_year: 24390 },
    mrr: 1626, perpetual_count: 3,
    installments: [{ name: "The Edge · 3-pay", amount: 542, total: 3, collected: 1, final_date: "2026-09-12" }],
    next30: {
      amount: 1626, charges: 3,
      schedule: [
        { date: "2026-07-12", amount: 542, who: "Sofia Marchetti" },
        { date: "2026-07-18", amount: 542, who: "Nathan (3-pay)" },
        { date: "2026-07-25", amount: 542, who: "Grace Okonkwo" },
      ],
    },
    arr_book: 156000, run_rate: 19512,
    streams: [
      { key: "memberships", label: "Memberships", amount: 71500, pct: 100 },
    ],
  },
};
