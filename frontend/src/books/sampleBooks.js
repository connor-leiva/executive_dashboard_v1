/* Sample Books payloads — used when VITE_API_BASE is unset (dev/offline), so the pages
   render standalone. Shapes mirror the /books, /books/pl, /books/queue, /books/ic
   endpoints exactly. Representative numbers (ULRG-flavored), clearly not live. */

export const sampleHome = {
  period: "mtd",
  synced_at: new Date(Date.now() - 4 * 60 * 1000).toISOString(),
  entities: { active: 5, holdings: 12 },
  rail: { captured: 1284, auto_categorized: 1181, cleared: 86, needs_approval: 14, escalated: 3 },
  tiles: {
    pl: { noi: 166523, revenue: 1094888, margin: 15, note: "Lead Generation up 18% MoM" },
    queue: { count: 14, oldest_days: 6, oldest_label: "J. Alvarez, $2,500" },
    ic: { open: 2, blocking: true, note: "$15,000 ULRG -> Sympli transfer" },
    close: [
      { business: "ulrg", steps: { bank_rec: true, card_rec: true, intercompany: true, accruals: false, statements: false }, status: "open", note: null },
      { business: "sympli", steps: { bank_rec: true, card_rec: true, intercompany: true, accruals: true, statements: true }, status: "closed", note: "Closed Jul 6" },
      { business: "springb", steps: { bank_rec: true, card_rec: false, intercompany: false, accruals: false, statements: false }, status: "open", note: null },
    ],
  },
  review: {
    status: "draft",
    period: "2026-06-01",
    body: "June closed with $1.09M in revenue and $166.5K in net operating income, a 15% margin. Lead Generation was the largest expense mover, up 18% month over month, driven mainly by Realtor.com and Zillow spend. Intercompany activity was clean except one $15,000 ULRG to Sympli transfer awaiting characterization. Contract labor and compensation held flat.",
  },
  invariants_ok: true,
};

const plAll = {
  business: "all", period_label: "June 2026",
  totals: { revenue: 1094888, gross_profit: 371178, opex: 204655, noi: 166523,
            prior: { revenue: 1010400, gross_profit: 352900, opex: 190200, noi: 162700 } },
  revenue: [
    { label: "41100 Listing Income", v: 348782, pv: 331200 },
    { label: "41200 Sales Income", v: 701731, pv: 640500 },
    { label: "Transaction Fee", v: 44375, pv: 38700 },
  ],
  cos: [
    { label: "51300 Listing COS", v: 213664, pv: 205100 },
    { label: "51400 Buyer COS", v: 333111, pv: 300200 },
    { label: "51600 Referral COS", v: 172769, pv: 158900 },
  ],
  opex: [
    { cat: "61000 Compensation", v: 20766, pv: 20100, lines: [
      { label: "61120 Administration", v: 18183, pv: 17600 },
      { label: "61200 Benefits/Processing", v: 2583, pv: 2500 } ] },
    { cat: "62000 Lead Generation", v: 55201, pv: 46800, lines: [
      { label: "62100 General Prospecting", v: 41205, pv: 35200 },
      { label: "62200 Listing Management", v: 13996, pv: 11600 } ] },
    { cat: "63000 Occupancy", v: 51251, pv: 50900, lines: [
      { label: "63100 Rent/Desk Fees", v: 31345, pv: 31300 },
      { label: "63200 Utilities", v: 3932, pv: 3700 } ] },
  ],
  eliminations: { applied: true, amount: 15000 },
};

export const samplePL = {
  all: plAll,
  ulrg: { ...plAll, business: "ulrg", eliminations: undefined },
  springb: { ...plAll, business: "springb", eliminations: undefined,
             totals: { revenue: 96000, gross_profit: 88000, opex: 42000, noi: 46000,
                       prior: { revenue: 92000, gross_profit: 85000, opex: 40000, noi: 45000 } } },
  sympli: { ...plAll, business: "sympli", eliminations: undefined, jv_share: 0.5,
            totals: { revenue: 210000, gross_profit: 96000, opex: 61000, noi: 35000,
                      prior: { revenue: 198000, gross_profit: 90000, opex: 58000, noi: 32000 } } },
};

export const sampleQueue = {
  stats: { awaiting: 14, escalated: 2, approved_7d: 61 },
  approvals: [
    { id: "s1", entity: "ulrg", date: "Jul 11", vendor: "Canva Teams", amount: -389,
      suggest: "Marketing - Software", conf: "92%", reason: "Matches 4 prior charges categorized here.",
      source: "AMEX", flags: {} },
    { id: "s2", entity: "ulrg", date: "Jul 10", vendor: "Realty.com", amount: -7300,
      suggest: "62130 Internet Lead Generation", conf: "88%", reason: "Recurring lead-gen vendor.",
      source: "AMEX", flags: { over_band: true } },
    { id: "s3", entity: "sympli", date: "Jul 9", vendor: "New Vendor LLC", amount: -1240,
      suggest: "68200 Office Supplies", conf: "61%", reason: "First time seeing this vendor.",
      source: "Bank feed", flags: { first_vendor: true } },
  ],
  escalations: [
    { id: "e1", kind: "ic", date: "Jul 7", amount: 15000,
      label: "ULRG -> Sympli transfer", reason: "No covering policy rule, or over the monthly cap.",
      options: ["Loan (due-to / due-from)", "Distribution", "Capital contribution", "Shared expense", "Rent", "Payroll allocation"],
      tax_note: "Characterization affects basis and taxes; the CFO decides." },
  ],
};

export const sampleIC = {
  pairs: [
    { id: "p1", from: "ulrg", to: "sympli", amount: 15000, date: "2026-07-07", status: "escalated", characterization: null },
    { id: "p2", from: "sympli", to: "springb", amount: 3050, date: "2026-07-03", status: "auto_tied", characterization: "shared_expense" },
    { id: "p3", from: "ulrg", to: "springb", amount: 6000, date: "2026-06-30", status: "tied", characterization: "rent" },
  ],
  rules: [
    { id: "r1", label: "Office rent to holding LLC", characterization: "rent", from: "ulrg", to: null, monthly_cap: null, active: false },
    { id: "r2", label: "ULRG-Sympli co-op marketing (cap $5,000/mo PLACEHOLDER)", characterization: "shared_expense", from: "sympli", to: "springb", monthly_cap: 5000, active: false },
    { id: "r3", label: "Payroll allocation (split PLACEHOLDER)", characterization: "payroll_alloc", from: null, to: null, monthly_cap: null, active: false },
  ],
  blocking: { open: 1, amount: 15000 },
};
