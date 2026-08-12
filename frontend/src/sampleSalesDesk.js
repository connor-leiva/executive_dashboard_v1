/* Illustrative Sales Desk payload — the exact shape GET /businesses/{key}/launches/active/
   sales-desk returns. Used only when VITE_API_BASE is unset (no backend). */
const _iso = (d, h, m) => new Date(Date.UTC(2026, 7, d, h, m)).toISOString();

const sampleSalesDesk = {
  history_since: "2026-08-08T00:00:00+00:00",
  as_of: _iso(20, 18, 0),
  default_tz: "America/Denver",
  totals: {
    booked: 59, held: 13, no_show: 6, cancelled: 2, rescheduled: 4, upcoming: 34, pending: 4,
    deciding: 5, won: 20, show_rate: 61.9, close_rate: 66.7, blended: 13000,
    blended_provisional: false, on_the_table: 65000,
  },
  reps: [
    { rep_email: "aimee@purposeledperformance.com", display_name: "Aimee Stephens", booked: 19, held: 13, noshow: 3, cancelled: 1, resched: 2, upcoming: 2, inplay: 5, won: 8, show_rate: 76.5, close_rate: 61.5, unmapped: false, unassigned: false },
    { rep_email: "brianna@springb.com", display_name: "Brianna Wood", booked: 16, held: 10, noshow: 3, cancelled: 1, resched: 1, upcoming: 2, inplay: 4, won: 6, show_rate: 71.4, close_rate: 60.0, unmapped: false, unassigned: false },
    { rep_email: "michele@authenticitysells.ai", display_name: "Michele Torres", booked: 13, held: 8, noshow: 2, cancelled: 1, resched: 0, upcoming: 2, inplay: 4, won: 4, show_rate: 72.7, close_rate: 50.0, unmapped: false, unassigned: false },
    { rep_email: "allison@empirepartners.io", display_name: null, booked: 9, held: 5, noshow: 2, cancelled: 0, resched: 1, upcoming: 1, inplay: 3, won: 2, show_rate: 71.4, close_rate: 40.0, unmapped: true, unassigned: false },
    { rep_email: null, display_name: null, booked: 2, held: 0, noshow: 1, cancelled: 0, resched: 0, upcoming: 1, inplay: 0, won: 0, show_rate: null, close_rate: null, unmapped: false, unassigned: true },
  ],
  calls: [
    { call_time_utc: _iso(20, 16, 0), contact_name: "R. Delgado", rep_email: "aimee@purposeledperformance.com", display_name: "Aimee Stephens", outcome: "Showed", unscheduled: false, unmapped: false },
    { call_time_utc: _iso(20, 17, 30), contact_name: "S. Okamoto", rep_email: "brianna@springb.com", display_name: "Brianna Wood", outcome: "No Show", unscheduled: false, unmapped: false },
    { call_time_utc: _iso(20, 21, 0), contact_name: "T. Marsh", rep_email: "michele@authenticitysells.ai", display_name: "Michele Torres", outcome: null, unscheduled: false, unmapped: false },
    { call_time_utc: _iso(21, 15, 30), contact_name: "L. Piersall", rep_email: "aimee@purposeledperformance.com", display_name: "Aimee Stephens", outcome: null, unscheduled: false, unmapped: false },
    { call_time_utc: _iso(21, 18, 0), contact_name: "B. Nguyen", rep_email: "allison@empirepartners.io", display_name: null, outcome: null, unscheduled: false, unmapped: true },
    { call_time_utc: null, contact_name: "Amy Kane", rep_email: null, display_name: null, outcome: null, unscheduled: true, unmapped: false },
  ],
  no_shows: [
    { contact_name: "P. Sandoval", rep_email: "aimee@purposeledperformance.com", display_name: "Aimee Stephens", days_since: 1, rebooked: false, opportunity_id: "o1", unmapped: false },
    { contact_name: "K. Reyes", rep_email: "brianna@springb.com", display_name: "Brianna Wood", days_since: 2, rebooked: true, opportunity_id: "o2", unmapped: false },
    { contact_name: "A. Fontaine", rep_email: "michele@authenticitysells.ai", display_name: "Michele Torres", days_since: 3, rebooked: false, opportunity_id: "o3", unmapped: false },
  ],
  payment_mix: [
    { type: "PIF", count: 9, acv: 12000, upfront: 12000, provisional: false, note: "$12,000 at signing" },
    { type: "Financed", count: 6, acv: 14000, upfront: 5000, provisional: false, note: "$5,000 down, then $750/mo" },
    { type: "Monthly", count: 4, acv: 14400, upfront: 1200, provisional: true, note: "$1,200/mo" },
    { type: "Custom", count: 1, acv: null, upfront: null, provisional: true, note: "negotiated — not priced" },
  ],
  upfront_total: 143600,
  priced_arr: 250800,
  warnings: [
    { n: 2, label: "bookings with no rep", hint: "host unassigned in the portal — attribution blank" },
    { n: 1, label: "rep not in the roster", hint: "allison@empirepartners.io — add a display name in settings" },
    { n: 1, label: "won with no payment type", hint: "Custom Payment has no 5.x workflow — unpriced in ARR" },
  ],
};

export default sampleSalesDesk;
