/* Sample three-lens financials (dev / no-API fallback) — matches the SPEC-financials
   Section 5 example so the view renders identically to the mockup offline. */
const sampleFinancials = {
  period: { label: "MTD", start: "2026-07-01", end: "2026-07-31", is_current: true },
  expense_run_rate: 96000,
  expense_run_rate_source: "trailing_3mo",
  lenses: {
    live: {
      profit: 72000, units: 38,
      rows: [
        { l: "Gross GCI", v: 420000, kind: "rev" },
        { l: "Agent commissions", v: -252000, kind: "ded" },
        { l: "Net GCI", v: 168000, kind: "sub" },
        { l: "Est. expenses", v: -96000, kind: "ded", est: true },
        { l: "Net profit, MTD", v: 72000, kind: "tot" },
      ],
    },
    projection: {
      profit: 138000, units: 60, closed_units: 38, pending_units: 22,
      closed_gci: 420000, pending_gci: 165000, gci: 585000,
      rows: [
        { l: "Projected GCI", v: 585000, kind: "rev" },
        { l: "Commissions", v: -351000, kind: "ded" },
        { l: "Net GCI", v: 234000, kind: "sub" },
        { l: "Est. expenses", v: -96000, kind: "ded", est: true },
        { l: "Projected profit", v: 138000, kind: "tot" },
      ],
    },
    booked: {
      profit: 48000, units: null, flag: "close_in_progress",
      rows: [
        { l: "Revenue", v: 340000, kind: "rev" },
        { l: "Cost of sale", v: -204000, kind: "ded" },
        { l: "Gross profit", v: 136000, kind: "sub" },
        { l: "Operating expenses", v: -88000, kind: "ded" },
        { l: "Net operating income", v: 48000, kind: "tot" },
      ],
    },
  },
  reconciliation: { sisu_closed: 420000, qbo_booked: 340000, gap_gci: 80000, gap_profit: 24000 },
};

export default sampleFinancials;
