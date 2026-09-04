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

const QBO = (route, id) => `https://app.qbo.intuit.com/app/${route}?txnId=${id}`;

/* Every row carries the review fields too — basis/priors/scan_state/signed_off — so the
   offline view exercises the same shapes the server sends, including the two that are easy to
   get wrong: a split (which is NOT a proposal) and a pending row (which has no opinion yet). */
const _q = (o) => ({ basis: "history_match", basis_label: "Matched from history",
  priors: 4, scan_state: "needs_approval", came_categorized: true, is_proposal: true,
  signed_off: false, signed_off_at: null, decision: null, ...o });

export const sampleQueue = {
  stats: { awaiting: 14, escalated: 2, approved_7d: 61 },
  period: { key: "last_7", label: "Last 7 days", start: "2026-08-14", end: "2026-08-20" },
  filter: { state: "needs_approval", include_signed_off: false },
  stages: { all: 6, auto: 4, cleared: 2, needs_approval: 3, escalated: 1, pending: 0,
            approved: 0, posted: 0, auto_categorized: 4, signed_off: 1 },
  approvals: [
    _q({ id: "s1", entity: "ulrg", date: "Jul 11", vendor: "Canva Teams", amount: -389,
      qbo_type: "Purchase", memo: "Canva Teams annual - design subscription", current_category: "Marketing - Software",
      bank_account: "Delta SkyMiles (AMEX)", suggest: "Marketing - Software", conf: "92%",
      reason: "Matches 4 prior charges categorized here.", source: "AMEX", flags: {}, qbo_url: QBO("expense", "1041") }),
    _q({ id: "s2", entity: "ulrg", date: "Jul 10", vendor: "Realty.com", amount: -7300,
      qbo_type: "Purchase", memo: "Realty.com - lead package Q3", current_category: "62130 Internet Lead Generation",
      bank_account: "Delta SkyMiles (AMEX)", suggest: "62130 Internet Lead Generation", conf: "88%",
      basis: "over_band", basis_label: "Known vendor, unusual amount", priors: 9,
      reason: "Known vendor, amount above the usual range (9 priors).", source: "AMEX", flags: { over_band: true }, qbo_url: QBO("expense", "1042") }),
    _q({ id: "s3", entity: "sympli", date: "Jul 9", vendor: "New Vendor LLC", amount: -1240,
      qbo_type: "Bill", memo: "Invoice #4471", current_category: "Uncategorized Expense",
      bank_account: null, suggest: "68200 Office Supplies", conf: "61%",
      basis: "claude", basis_label: "Claude read it", priors: null, came_categorized: false,
      reason: "First time seeing this vendor.", source: "Bank feed", flags: { first_vendor: true }, qbo_url: QBO("bill", "1043") }),
  ],
  escalations: [
    { id: "e1", kind: "ic", date: "Jun 2", amount: 42712,
      label: "Zions Operating - transfer", reason: "One-sided — no matching counterpart found in another entity.",
      options: ["Loan (due-to / due-from)", "Distribution", "Capital contribution", "Shared expense", "Rent", "Payroll allocation"],
      tax_note: "Characterization affects basis and taxes; the CFO decides.",
      txns: [{ entity: "springb", qbo_type: "Transfer", date: "Jun 2", amount: 42712, payee: null,
        memo: "Owner draw to holding acct", account: null, bank_account: "Zions Operating *3251",
        source: "Bank feed", qbo_url: QBO("transfer", "2087") }] },
  ],
};

export const sampleIC = {
  pairs: [
    { id: "p1", from: "ulrg", to: "sympli", amount: 15000, date: "2026-07-07", status: "escalated", characterization: null,
      txns: [{ entity: "ulrg", qbo_type: "Transfer", date: "Jul 7", amount: 15000, payee: null,
        memo: "Marketing co-op advance", account: null, bank_account: "Operating Checking", qbo_url: QBO("transfer", "2091") }] },
    { id: "p2", from: "sympli", to: "springb", amount: 3050, date: "2026-07-03", status: "auto_tied", characterization: "shared_expense",
      txns: [{ entity: "sympli", qbo_type: "Transfer", date: "Jul 3", amount: 3050, payee: null,
        memo: "Co-op marketing", account: null, bank_account: "Sympli Operating", qbo_url: QBO("transfer", "2092") }] },
    { id: "p3", from: "ulrg", to: "springb", amount: 6000, date: "2026-06-30", status: "tied", characterization: "rent",
      txns: [{ entity: "ulrg", qbo_type: "JournalEntry", date: "Jun 30", amount: 6000, payee: null,
        memo: "June office rent", account: "63100 Rent/Desk Fees", bank_account: null, qbo_url: QBO("journal", "2093") }] },
  ],
  rules: [
    { id: "r1", label: "Office rent to holding LLC", characterization: "rent", from: "ulrg", to: null, monthly_cap: null, active: false },
    { id: "r2", label: "ULRG-Sympli co-op marketing (cap $5,000/mo PLACEHOLDER)", characterization: "shared_expense", from: "sympli", to: "springb", monthly_cap: 5000, active: false },
    { id: "r3", label: "Payroll allocation (split PLACEHOLDER)", characterization: "payroll_alloc", from: null, to: null, monthly_cap: null, active: false },
  ],
  blocking: { open: 1, amount: 15000 },
};

/* ── Chart of accounts mapping (Phase 2) ────────────────────────────────────────────────
   Offline/demo payload. Shapes match /books/coa and /books/coa/map exactly, and the
   accounts are the ones Phase 0 actually found on ULRG — the number lives in the NAME,
   nesting runs deep, and 69000 appears twice. */
export const sampleCoaEntities = {
  entities: [
    { id: "b-ulrg", key: "ulrg", name: "ULRG + Team", archetype: "transactional", qbo_connected: true,
      counts: { total: 271, mapped: 118, ignored: 9, unmapped: 144 } },
    { id: "b-springb", key: "springb", name: "Spring B", archetype: "program", qbo_connected: true,
      counts: { total: 186, mapped: 62, ignored: 4, unmapped: 120 } },
    { id: "b-sympli", key: "sympli", name: "Sympli Mortgage", archetype: "transactional", qbo_connected: true,
      counts: { total: 155, mapped: 0, ignored: 0, unmapped: 155 } },
  ],
};

const STD = [
  { id: "s-7010", code: "7010", name: "Rent", bucket: "occupancy", statement: "pl", section: "opex", is_active: true, definition: null },
  { id: "s-8050", code: "8050", name: "Contract Labor, Named Contractors", bucket: "salaries_wages", statement: "pl", section: "opex", is_active: true, definition: null },
  { id: "s-8060", code: "8060", name: "Contract Labor, Virtual Assistants", bucket: "salaries_wages", statement: "pl", section: "opex", is_active: true, definition: null },
  { id: "s-8610", code: "8610", name: "Other Insurance", bucket: "general_admin", statement: "pl", section: "opex", is_active: true, definition: null },
  { id: "s-8690", code: "8690", name: "Other General and Administrative", bucket: "general_admin", statement: "pl", section: "opex", is_active: true, definition: null },
  { id: "s-4010", code: "4010", name: "Gross Commission Income, Listing Side", bucket: "revenue_transactional", statement: "pl", section: "revenue", is_active: true, definition: null },
  { id: "s-9910", code: "9910", name: "Other Expense", bucket: "other_expense", statement: "pl", section: "other_expense", is_active: true, definition: null },
  { id: "s-2200", code: "2200", name: "Accrued Liabilities", bucket: "balance_sheet", statement: "bs", section: "liability", is_active: true, definition: null },
];

const acct = (o) => ({ qbo_active: true, is_ignored: false, ignore_reason: null, mapped_via: null,
  rule_id: null, standard_account_id: null, code: null, standard_name: null, suggestion: null, ...o });

export const sampleCoaMap = {
  business: { id: "b-ulrg", key: "ulrg", name: "ULRG + Team", archetype: "transactional" },
  counts: { total: 8, mapped: 3, ignored: 1, unmapped: 4, by_rule: 2 },
  accounts: [
    acct({ qbo_account_id: "289", name: "41200 Sales Income", fqn: "41000 Gross Commission Income:41200 Sales Income",
      type: "Income", depth: 2, standard_account_id: "s-4010", code: "4010",
      standard_name: "Gross Commission Income, Listing Side", mapped_via: "manual" }),
    acct({ qbo_account_id: "301", name: "Ana Ruiz", fqn: "61300 Contract Labor:Virtual Assistants:Ana Ruiz",
      type: "Expense", depth: 3, standard_account_id: "s-8060", code: "8060",
      standard_name: "Contract Labor, Virtual Assistants", mapped_via: "rule", rule_id: "r-va" }),
    acct({ qbo_account_id: "302", name: "Diego Marin", fqn: "61300 Contract Labor:Virtual Assistants:Diego Marin",
      type: "Expense", depth: 3, standard_account_id: "s-8060", code: "8060",
      standard_name: "Contract Labor, Virtual Assistants", mapped_via: "rule", rule_id: "r-va" }),
    acct({ qbo_account_id: "310", name: "Kofi Mensah", fqn: "61300 Contract Labor:Mentor Bonuses:Kofi Mensah",
      type: "Expense", depth: 3,
      suggestion: { code: "8050", name: "Contract Labor, Named Contractors", standard_account_id: "s-8050",
        confidence: "keyword", why: "contains “contract labor”" } }),
    acct({ qbo_account_id: "400", name: "69000 Other Expense", fqn: "69000 Other Expense", type: "Other Expense", depth: 1,
      suggestion: { code: "9910", name: "Other Expense", standard_account_id: "s-9910",
        confidence: "exact", why: "name matches exactly" } }),
    acct({ qbo_account_id: "401", name: "69000 Insurance", fqn: "69000 Insurance", type: "Expense", depth: 1,
      suggestion: { code: "8610", name: "Other Insurance", standard_account_id: "s-8610",
        confidence: "keyword", why: "contains “insurance”" } }),
    acct({ qbo_account_id: "500", name: "Rent", fqn: "63000 Occupancy:Rent", type: "Expense", depth: 2,
      suggestion: { code: "7010", name: "Rent", standard_account_id: "s-7010",
        confidence: "exact", why: "name matches exactly" } }),
    acct({ qbo_account_id: "900", name: "Old Payroll Clearing", fqn: "Old Payroll Clearing",
      type: "Other Current Liability", depth: 1, qbo_active: false, is_ignored: true,
      ignore_reason: "Dead clearing account, zero balance since 2024" }),
  ],
  standard: STD,
  rules: [
    { id: "r-va", pattern: "61300 Contract Labor:Virtual Assistants:", match_type: "prefix",
      business_id: "b-ulrg", business_key: "ulrg", standard_account_id: "s-8060", code: "8060",
      is_active: true, note: "Phase 0: the VAs live in this subtree", covers: 2 },
  ],
};

/* ── The mapped statement with provenance (Phase 5) ─────────────────────────────────────
   Offline payload. Shapes match /books/statement exactly. The Forum's July, where the
   shared-service charge funded by Spring B is most of the operating cost. */
const line = (o) => ({ ic_net: 0, ic_share_pct: null, provenance: "direct", flagged: false,
  is_intercompany_account: false, definition: null, sources: [], contributions: [],
  as_booked: o.amount, as_allocated: o.amount, ...o });

export const sampleStatement = {
  business: { id: "b-forum", key: "the_forum", name: "The Forum", archetype: "program",
              gross_profit_label: "Gross Profit" },
  period: { start: "2026-07-01", end: "2026-07-31", books_closed: false,
            synced_at: "2026-08-20T21:37:31Z" },
  statement: "pl", mode: "allocated", threshold_pct: 5.0,
  sections: [
    { key: "revenue", label: "Revenue", total: 61100,
      buckets: [{ key: "revenue_event", label: "Event Revenue", total: 61100,
        total_booked: 61100, total_allocated: 61100, flagged: false,
        lines: [line({ standard_account_id: "s1", code: "4410", name: "Ticket Revenue, General",
                       sort_order: 300, amount: 61100 })] }] },
    { key: "opex", label: "Operating Expenses", total: 49523,
      buckets: [
        { key: "advertising", label: "Advertising", total: 19946, total_booked: 6455,
          total_allocated: 19946, flagged: true,
          lines: [line({ standard_account_id: "s2", code: "6090", name: "Other Advertising",
            sort_order: 900, amount: 19946, as_booked: 6455, as_allocated: 19946,
            ic_net: 13491, ic_share_pct: 67.6, provenance: "allocated", flagged: true,
            sources: [{ qbo_account_id: "2", name: "Shared Service Expenses:Shared Service Expense - Advertising", amount: 13491 },
                      { qbo_account_id: "4", name: "Advertising & Marketing", amount: 6455 }],
            contributions: [{ direction: "in", counterparty_business: "Spring B", amount: 13491,
              pool_name: "Shared Services", basis: null,
              driver_source: "QuickBooks: Shared Service Expenses sub-accounts",
              approved_by: "Spring", je_ref: null, booking: "observed" }] })] },
        { key: "salaries_wages", label: "Salaries, Wages and Contract Labor", total: 16022,
          total_booked: 0, total_allocated: 16022, flagged: true,
          lines: [line({ standard_account_id: "s3", code: "8010", name: "Salaries, Staff",
            sort_order: 1400, amount: 16022, as_booked: 0, as_allocated: 16022,
            ic_net: 16022, ic_share_pct: 100.0, provenance: "allocated", flagged: true,
            sources: [{ qbo_account_id: "1", name: "Shared Service Expenses:Shared Service Expense - Payroll", amount: 16022 }],
            contributions: [{ direction: "in", counterparty_business: "Spring B", amount: 16022,
              pool_name: "Shared Services", basis: null,
              driver_source: "QuickBooks: Shared Service Expenses sub-accounts",
              approved_by: "Spring", je_ref: null, booking: "observed" }] })] },
        { key: "general_admin", label: "General and Administrative", total: 13555,
          total_booked: 10618, total_allocated: 13555, flagged: false,
          lines: [line({ standard_account_id: "s4", code: "8510", name: "Dues and Subscriptions",
            sort_order: 1500, amount: 13555, as_booked: 10618, as_allocated: 13555,
            ic_net: 2937, ic_share_pct: 21.7, provenance: "allocated", flagged: true,
            contributions: [{ direction: "in", counterparty_business: "Spring B", amount: 2937,
              pool_name: "Shared Services", basis: null, driver_source: null,
              approved_by: "Spring", je_ref: null, booking: "observed" }] })] },
      ] },
  ],
  totals: { net_revenue: 61100, cost_of_sale: 0, gross_profit: 61100,
            operating_expenses: 49523, net_operating_income: 11577, below_the_line: 0,
            net_income: 11577 },
  net_income_booked: 44127, net_income_allocated: 11577,
  tie_out: { status: "tied", delta: 0, mapped: 0, booked: 0, tolerance: 0.01,
             accounts: 23, gross: 250908, excluded: 0 },
  exclusions: [],
  caveat: "Tied to QuickBooks. That proves the map is faithful to the books — it says nothing about whether the books are right.",
};

/* Drill-down behind one statement line. The reconciliation is deliberately IMPERFECT in this
   sample, because that is the honest common case: a multi-line transaction is stored against
   its first category at its full header amount, so the transactions rarely add up exactly. */
export const sampleLineDetail = {
  line: { standard_account_id: "s2", code: "6090", name: "Other Advertising",
          section: "opex", amount: 19946, definition: null },
  business: { id: "b-forum", key: "the_forum", name: "The Forum" },
  period: { start: "2026-07-01", end: "2026-07-31" },
  accounts: [
    { qbo_account_id: "2", fqn: "Shared Service Expenses:Shared Service Expense - Advertising",
      type: "Expense", mapped_via: "manual", amount: 13491, transactions: 1 },
    { qbo_account_id: "4", fqn: "Advertising & Marketing", type: "Expense",
      mapped_via: "rule", amount: 6455, transactions: 3 },
  ],
  transactions: [
    { id: "t1", date: "2026-07-28", qbo_type: "JournalEntry", payee: "SB Coaching LLC",
      memo: "July shared advertising", amount: 13491, account: "Shared Service Expense - Advertising",
      bank_account: null, multi_line: true, scan_state: "cleared", qbo_url: null },
    { id: "t2", date: "2026-07-14", qbo_type: "Purchase", payee: "Meta Platforms",
      memo: "Forum event campaign", amount: 4200, account: "Advertising & Marketing",
      bank_account: "Forum Operating", multi_line: false, scan_state: "cleared", qbo_url: null },
    { id: "t3", date: "2026-07-09", qbo_type: "Purchase", payee: "Canva",
      memo: null, amount: 1455, account: "Advertising & Marketing",
      bank_account: "Forum Operating", multi_line: false, scan_state: "needs_approval", qbo_url: null },
    { id: "t4", date: "2026-07-02", qbo_type: "Purchase", payee: "Printful",
      memo: "Event signage", amount: 800, account: "Advertising & Marketing",
      bank_account: "Forum Operating", multi_line: false, scan_state: "cleared", qbo_url: null },
  ],
  truncated: 0,
  reconciliation: { line_total: 19946, transaction_total: 19946, delta: 0, explained: true,
    transactions: 4, multi_line: 1,
    note: "These transactions account for the whole line." },
};
