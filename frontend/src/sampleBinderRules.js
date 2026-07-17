/* Representative Binder rules payload for preview/sample mode. Mirrors GET /binder/rules. */
const r = (kind, label, jur, et, cadence, derivation, last_verified, note, stale = false) => ({
  id: `${kind}-${jur || "x"}-${et || "x"}`, kind, kind_label: label, jurisdiction: jur,
  entity_type: et, cadence, derivation, last_verified, stale, source_note: note,
  active: true, scope: "system",
});

const rules = [
  r("annual_report", "Annual report", "UT", "llc", "annual", "anniversary_month_end", "2026-02-01",
    "Utah LLC annual report renews by the last day of the formation-anniversary month."),
  r("annual_report", "Annual report", "AZ", "llc", "none", "not_applicable", "2026-02-01",
    "Arizona does NOT require LLC annual reports (A.R.S. Title 29)."),
  r("annual_report", "Annual report", "AZ", null, "annual", "manual", "2026-02-01",
    "Arizona corporations file on the ACC-assigned anniversary; set per entity."),
  r("federal_tax", "Federal tax", null, null, "annual", "entity_type_calendar", "2026-02-01",
    "IRS filing-deadline calendar (1120 / 1120-S / 1065 / 1040)."),
  r("state_tax", "State tax", "UT", null, "annual", "entity_type_calendar", "2026-02-01",
    "Utah income tax mirrors the federal cadence for v1; confirm with CPA."),
  r("estimated_payments", "Estimated payments", null, null, "quarterly", "fixed_date", "2026-02-01",
    "Federal quarterly estimated-tax dates (Apr 15 / Jun 15 / Sep 15 / Jan 15)."),
  r("boi", "BOI / FinCEN", null, null, "one_time", "manual", "2024-06-01",
    "BOI filing requirement was legally turbulent 2024-2025; RE-VERIFY before relying on this.", true),
];

export default { rules, stale_after_months: 12, stale_count: rules.filter((x) => x.stale).length };
