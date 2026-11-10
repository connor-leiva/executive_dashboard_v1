/** A Sympli-shaped board plus two long names, so the grid can be checked without a login. */

const weeks = [
  { n: 45, start: "2026-11-02", end: "2026-11-08", label: "11/02", complete: true },
  { n: 46, start: "2026-11-09", end: "2026-11-15", label: "11/09", complete: false },
];

function row(partial) {
  return {
    id: partial.measurable,
    direction: "gte",
    type: "flow",
    unit: "count",
    cadence: "weekly",
    scored: true,
    auto: true,
    values: [0, 2],
    week_goals: [partial.goal, partial.goal],
    streak: 0,
    owner: { initials: "JA", name: "Jared" },
    cumulative: {
      "sprint:2026Q4": {
        n: 1, actual: 2, target: 9, attain: 24, gap: -7, required: 9, verdict: "reset", best: 2,
      },
    },
    ...partial,
  };
}

export const fixture = {
  enterprise: false,
  ids_after: 3,
  current_week: 46,
  default_window: 13,
  windows: [4, 13, "qtd"],
  quarter: { key: "2026Q4", start: "2026-11-02", end: "2027-01-18", weeks_total: 12, weeks_closed: 1, weeks_left: 11 },
  weeks,
  sprints: [
    { key: "2026Q3", start: "2026-07-27", end: "2026-10-30", current: false, weeks_total: 14, weeks_closed: 14, weeks_left: 0, mondays: ["2026-07-27", "2026-08-03"] },
    { key: "2026Q4", start: "2026-11-02", end: "2027-01-18", current: true, weeks_total: 12, weeks_closed: 1, weeks_left: 11, mondays: ["2026-11-02", "2026-11-09", "2026-11-16"] },
  ],
  sources: [{ provider: "ghl", synced_at: new Date(Date.now() - 4 * 60000).toISOString() }],
  escalations: [],
  checks: [],
  gates: [],
  groups: [
    {
      id: "g1", key: "north", name: "North Star", is_team_room: false, lens: null,
      owner: { name: "Connor Leiva" },
      rows: [
        row({
          measurable: "Funded (40/mo)", goal: 9.23, owner: { initials: "JA" },
          cumulative_goal: 102, streak: 13,
          cumulative: { "sprint:2026Q4": { n: 1, actual: 2, target: 102, attain: 24, gap: -7, required: 9, verdict: "reset" } },
        }),
        row({
          measurable: "Funded (26/mo)", goal: 6, owner: { initials: "JA" },
          cumulative_goal: 66, streak: 4,
          cumulative: { "sprint:2026Q4": { n: 1, actual: 2, target: 66, attain: 36, gap: -4, required: 6, verdict: "reset" } },
        }),
        row({
          measurable: "Sympli Attach Rate", goal: 32, type: "rate", unit: "pct", owner: { initials: "CO" },
          values: [16.7, 0], streak: 4,
          cumulative: { "sprint:2026Q4": { n: 1, actual: 16.7, target: 32, attain: 52, gap: -15.3, required: 33, verdict: "reset" } },
        }),
        row({
          measurable: "Margin, New Locks", goal: 2.3, type: "rate", unit: "pct", direction: "gte", owner: { initials: "CO" },
          values: [2.0, null], streak: 13,
          cumulative: { "sprint:2026Q4": { n: 1, actual: 2.03, target: 2.3, attain: 90, gap: -0.22, required: 2.27, verdict: "reset" } },
        }),
        row({
          measurable: "Margin, Funded Loans", goal: 2, type: "rate", unit: "pct", owner: { initials: "CO" },
          values: [2, null], streak: 0,
          cumulative: { "sprint:2026Q4": { n: 1, actual: 2.0, target: 2, attain: 100, gap: 0, required: null, verdict: "ahead" } },
        }),
        row({
          measurable: "Net Operating Income ($K)", goal: 60, unit: "usd", owner: { initials: "CO" },
          values: [null, null], streak: 0, cumulative: { "sprint:2026Q4": null },
        }),
        row({
          measurable: "Active Paying Forum Members", goal: 125, type: "snapshot", owner: { initials: "CL", name: "Connor" },
          provenance: "live", provenance_from: "GHL", provenance_detail: "Active members on the roster.",
          target_by: "125, date open", streak: 2, values: [57, null],
          cumulative: { "sprint:2026Q4": null },
        }),
      ],
    },
    {
      id: "g2", key: "pipe", name: "Pipeline", is_team_room: false,
      owner: { name: "Jared Browning" },
      rows: [
        row({
          measurable: "Pre-Application", goal: null, scored: false, owner: { initials: "JB" },
          values: [152, 144], streak: 0, cumulative: { "sprint:2026Q4": null },
        }),
      ],
    },
  ],
};
