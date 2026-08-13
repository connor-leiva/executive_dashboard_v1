/* Bundled sample for the beCollective Launch section (SPEC-becollective-launch + the Shift
   layer). Rendered when no API base is configured (dev). A pre-window snapshot: the Shift
   (lead-up webinar) is live and sitting behind its pace curve, feeding a 100-member goal;
   the sales cart hasn't opened yet. Shape mirrors LaunchResponse exactly. */
const CURVE = { 14: 0.19, 13: 0.22, 12: 0.247, 11: 0.275, 10: 0.309, 9: 0.348, 8: 0.39,
  7: 0.432, 6: 0.481, 5: 0.584, 4: 0.67, 3: 0.734, 2: 0.801, 1: 0.864, 0: 0.94 };
const curve = Object.entries(CURVE)
  .map(([d, pct]) => ({ d: +d, pct, count: Math.round(pct * 2000) }))
  .sort((a, b) => b.d - a.d);

const sampleLaunch = {
  id: "sample-launch",
  launch: {
    name: "August 2026 Cohort", program: "beCollective",
    event_start: "2026-08-11", event_end: "2026-08-13",
    window_start: "2026-08-11", window_end: "2026-09-12",
    goal_arr: 1000000, ticket_pif: 12000, ticket_plan: 14000, plan_installments: 12,
    mix_pif: 0.5, pipeline_match: "Be Collective August 2026 Sales Funnel",
    cohort_value: "Aug 2026", pace_model: "curve", pace_tolerance: 0.1,
    goal_basis: "seats", seat_goal: 100,
    shift_name: "The Shift", shift_event_date: "2026-08-11", shift_goal: 2000,
    shift_reg_tag: "the shift", shift_actual: 175, shift_pace_curve: CURVE, shift_pace_tolerance: 0.08,
    stage_map: {                                   // mirrors the server default (Committed = paid)
      leads: ["opt in"], booked: ["scheduled appointment", "appointment"],
      booked_app: ["application", "app submitted", "app in"],
      deciding: ["needs decision", "decision", "payment sent"],
      committed: ["payment received", "custom payment"], enrolled: ["won: onboarded", "onboarded"],
      noshow: ["no show", "cancel"], nurture: ["future cohort", "nurture"],
      lost: ["lost", "dq", "abandon"],
    },
  },
  goal_basis: "seats",
  shift: {
    name: "The Shift", event_date: "2026-08-11", goal: 2000, registrants: 240,
    pct_to_goal: 0.12, days_to_event: 14, expected: 380, expected_pct: 0.19,
    gap: -140, state: "behind", source: "synced", reg_to_member: 0.05,
    projected_members: 12, members_at_goal: 100, curve,
    sources: {
      total: 240, paid: 143, organic: 59, comped: 36,
      channels: [
        { key: "meta", label: "Meta", count: 143, pct: 60, paid: true },
        { key: "email", label: "Email", count: 2, pct: 1, paid: false },
        { key: "comped", label: "Comped", count: 36, pct: 15, paid: false },
        { key: "organic", label: "Organic / Existing", count: 59, pct: 25, paid: false },
      ],
    },
  },
  status: "pre", as_of: "2026-07-28",
  window_days: 32, days_elapsed: 0, days_remaining: 32, days_to_open: 14,
  blended_seat: 13000, seat_target: 100,
  enrolled: { pif: 0, plan: 0, seats: 0, arr: 0 },
  committed: { pif: 0, plan: 0, seats: 0, arr: 0 },
  deciding: { count: 11, arr: 143000 },
  funnel: [
    { key: "leads", label: "Leads", owner: "marketing", count: 19, tag: null },
    { key: "booked", label: "Booked", owner: "setters", count: 8, tag: "5 of 8 apps in" },
    { key: "deciding", label: "Deciding", owner: "closers", count: 11, tag: "$143K on the table" },
    { key: "committed", label: "Committed", owner: "payment ops", count: 0, tag: null },
  ],
  side: { no_show: 3, nurture: 40 },
  pace: { expected_arr: 0, gap_arr: 0, state: "pending" },
  pct_to_goal: 0, pct_to_goal_seats: 0, seats_remaining: 100, arr_remaining: 1000000,
  cash: { collected: 0, source: "estimate" },
  momentum: { optins: [40, 55, 70, 88], calls: [6, 9, 12, 14], closes: [0, 0, 0, 0], calls_source: "proxy" },
  warnings: [],
};

export default sampleLaunch;
