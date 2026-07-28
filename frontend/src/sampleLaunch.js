/* Bundled sample for the beCollective Launch section — the spec's in-window snapshot
   (SPEC-becollective-launch §8). Rendered when no API base is configured (dev) so the
   section looks live without a backend. Shape mirrors LaunchResponse exactly. */
const sampleLaunch = {
  id: "sample-launch",
  launch: {
    name: "August 2026 Cohort", program: "beCollective",
    event_start: "2026-08-11", event_end: "2026-08-13",
    window_start: "2026-08-11", window_end: "2026-09-12",
    goal_arr: 1000000, ticket_pif: 12000, ticket_plan: 14000, plan_installments: 12,
    mix_pif: 0.5, pipeline_match: "Be Collective August 2026 Sales Funnel",
    cohort_value: "Aug 2026", pace_model: "linear", pace_tolerance: 0.1,
  },
  status: "open", as_of: "2026-08-23",
  window_days: 32, days_elapsed: 12, days_remaining: 20, days_to_open: 0,
  blended_seat: 13000, seat_target: 77,
  enrolled: { pif: 13, plan: 11, seats: 24, arr: 310000 },
  committed: { pif: 2, plan: 3, seats: 5, arr: 66000 },
  deciding: { count: 11, arr: 143000 },
  funnel: [
    { key: "leads", label: "Leads", owner: "marketing", count: 19, tag: null },
    { key: "booked", label: "Booked", owner: "setters", count: 8, tag: "5 of 8 apps in" },
    { key: "deciding", label: "Deciding", owner: "closers", count: 11, tag: "$143K on the table" },
    { key: "committed", label: "Committed", owner: "payment ops", count: 5, tag: "2 PIF · 3 plan" },
  ],
  side: { no_show: 9, nurture: 32 },
  pace: { expected_arr: 375000, gap_arr: -65000, state: "onpace" },
  pct_to_goal: 0.31, seats_remaining: 53, arr_remaining: 690000,
  cash: { collected: 168833, source: "estimate" },
  momentum: { optins: [5, 8, 24, 14, 13, 16], calls: [2, 4, 15, 9, 8, 10],
              closes: [0, 1, 9, 5, 4, 5], calls_source: "proxy" },
  warnings: [],
};

export default sampleLaunch;
