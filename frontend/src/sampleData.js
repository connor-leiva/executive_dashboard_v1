/* A COMPLETE, valid DashboardResponse (spec Section 7) reproducing the
   mockup's numbers EXACTLY so the rendered dashboard is visually identical
   to the original mockup. Used as the dev / no-backend fallback. */

// Brand accents (mirrors theme.js T, kept literal here so this stays a pure
// data module with no rendering dependency).
const MEADOW = "#61835E";
const POPPY = "#FA8069";
const POPPY_TEXT = "#D92B08";
const TEAL = "#227175";

const PORTFOLIO_REVENUE = 420000 + 68000 + 82000; // 570000
const PORTFOLIO_NOI = 72000 + 15000 + 22000; // 109000

const round1 = (n) => Math.round(n * 10) / 10;

const sampleData = {
  period: {
    label: "Month to date",
    as_of: "2026-03-31",
    start: "2026-03-01",
    end: "2026-03-31",
  },

  portfolio: {
    revenue: PORTFOLIO_REVENUE,
    noi: PORTFOLIO_NOI,
    margin: Math.round((PORTFOLIO_NOI / PORTFOLIO_REVENUE) * 100), // 19
    mom: 8,
    cash: 340000,
    composition: [
      {
        key: "ulrg",
        name: "ULRG + Team",
        revenue: 420000,
        pct: round1((420000 / PORTFOLIO_REVENUE) * 100), // 73.7
        accent: MEADOW,
      },
      {
        key: "sympli",
        name: "Sympli Mortgage",
        revenue: 82000,
        pct: round1((82000 / PORTFOLIO_REVENUE) * 100), // 14.4
        accent: TEAL,
      },
      {
        key: "forum",
        name: "Spring B",
        revenue: 68000,
        pct: round1((68000 / PORTFOLIO_REVENUE) * 100), // 11.9
        accent: "#FFDD1F",
      },
    ],
  },

  scorecards: [
    { label: "Combined Profit", value: "$109K", sub: "19% margin", business_key: "portfolio", key: "combined_profit" },
    { label: "Total GCI", value: "$420K", sub: "month to date", business_key: "ulrg", key: "gci" },
    { label: "Closed Units", value: "38", sub: "this month", business_key: "ulrg", key: "units_closed" },
    { label: "Under Contract", value: "22", sub: "$8.1M pipeline", business_key: "ulrg", key: "pending" },
    { label: "Agents Producing", value: "24", sub: "of 31", business_key: "ulrg", key: "agents_producing" },
    { label: "Loans Funded", value: "19", sub: "$7.3M volume", business_key: "sympli", key: "funded_loans" },
    { label: "Attach Rate", value: "31%", sub: "ULRG → Sympli", business_key: "sympli" },
    { label: "Active Members", value: "70", sub: "The Forum", business_key: "forum", key: "active_members" },
  ],

  areas: {
    ulrg: {
      key: "ulrg",
      name: "ULRG + Team",
      tag: "Real estate",
      status: "healthy",
      accent: MEADOW,
      ink: "#4D6A4D",
      sources: ["QuickBooks", "Sisu", "Follow Up Boss"],
      revenue: 420000,
      noi: 72000,
      margin: 17,
      trend: [49, 44, 52, 58, 55, 64, 72],
      pl: [
        { label: "Revenue (GCI)", value: 420000, kind: "rev" },
        { label: "Cost of sale — agent commissions", value: -252000, kind: "ded" },
        { label: "Gross profit", value: 168000, kind: "sub", note: "40%" },
        { label: "Operating expenses", value: -96000, kind: "ded" },
        { label: "Net operating income", value: 72000, kind: "tot", note: "17% margin" },
      ],
      ops: [
        { label: "Units Closed", value: "38", sub: "month to date", key: "units_closed" },
        { label: "Volume", value: "$14.2M", key: "volume" },
        { label: "GCI", value: "$420K", sub: "month to date", key: "gci" },
        { label: "Avg Sale Price", value: "$374K", key: "avg_price" },
        { label: "Pending Pipeline", value: "22", sub: "$8.1M", key: "pending" },
        { label: "Active Listings", value: "17", key: "active_listings" },
        { label: "Agents Producing", value: "24", sub: "of 31", key: "agents_producing" },
      ],
      funnel: [
        { label: "Leads", v: 680 },
        { label: "Appointments", v: 142 },
        { label: "Under contract", v: 46 },
        { label: "Closed", v: 38 },
      ],
    },

    forum: {
      key: "forum",
      name: "The Forum",
      tag: "Mastermind · 70 members · $1.2M ARR",
      status: "watch",
      accent: "#FFDD1F",
      ink: "#FFDD1F",
      sources: ["QuickBooks"],
      revenue: 68000,
      noi: 15000,
      margin: 22,
      trend: [21, 26, 16, 11, 27, 13, 15],
      pl: [
        { label: "Revenue — membership + events", value: 68000, kind: "rev" },
        { label: "Cost of sale — production, venue, speakers", value: -31000, kind: "ded" },
        { label: "Gross profit", value: 37000, kind: "sub", note: "54%" },
        { label: "Operating expenses", value: -22000, kind: "ded" },
        { label: "Net operating income", value: 15000, kind: "tot", note: "22% margin" },
      ],
      ops: [
        { label: "Active Members", value: "70", sub: "Forum 26 · Inner Circle 44", key: "active_members" },
        { label: "Forum ARR", value: "$1.2M", sub: "47 memberships", key: "forum_arr" },
        { label: "New Members", value: "7", sub: "month to date", key: "new_members" },
        { label: "Renewals Due", value: "5", sub: "July", key: "renewals_due" },
        { label: "Registered", value: "28", sub: "Park City, UT", key: "registered" },
        { label: "MRR", value: "$27K", sub: "monthly subscriptions", key: "mrr" },
      ],
      funnel: null,
    },

    becollective: {
      key: "becollective",
      name: "beCollective",
      tag: "Community · GHL segment",
      status: "opportunity",
      accent: "#FFBA9F",
      ink: "#6D5336",
      sources: ["Go High Level"],
      revenue: null,
      noi: null,
      margin: null,
      trend: [12, 14, 13, 15, 16, 15, 17],
      pl: [],
      ops: [],
      funnel: null,
    },

    sympli: {
      key: "sympli",
      name: "Sympli Mortgage",
      tag: "Joint venture · 50% owned",
      status: "opportunity",
      accent: TEAL,
      ink: TEAL,
      sources: ["QuickBooks", "Arive"],
      revenue: 82000,
      noi: 22000,
      margin: 27,
      trend: [13, 16, 12, 19, 17, 22, 19],
      pl: [
        { label: "Revenue — loan production", value: 82000, kind: "rev" },
        { label: "Cost of sale — loan officer comp", value: -41000, kind: "ded" },
        { label: "Gross profit", value: 41000, kind: "sub", note: "50%" },
        { label: "Operating expenses", value: -19000, kind: "ded" },
        { label: "Net operating income", value: 22000, kind: "tot", note: "27% margin" },
        { label: "Spring's JV share (50%)", value: 11000, kind: "share" },
      ],
      ops: [
        { label: "Funded Loans", value: "19", sub: "month to date" },
        { label: "Loan Volume", value: "$7.3M" },
        { label: "Avg Loan Amount", value: "$384K" },
        { label: "Pre-Approvals", value: "41", sub: "active" },
        { label: "In Underwriting", value: "23", sub: "locked" },
        { label: "Pull-Through Rate", value: "68%" },
      ],
      funnel: [
        { label: "Pre-approvals", v: 41 },
        { label: "Applications", v: 28 },
        { label: "Locked", v: 23 },
        { label: "Funded", v: 19 },
      ],
    },
  },

  flywheel: {
    available: true,
    buyer_closings: 48,
    captured: 15,
    capture_pct: Math.round((15 / 48) * 100), // 31
    per_loan_share: 2100,
    monthly_gap: (48 - 15) * 2100, // 69300
    annual_gap: (48 - 15) * 2100 * 12, // 831600
    agents: [
      { name: "Sarah K.", refs: 9 },
      { name: "Mike R.", refs: 6 },
      { name: "Jen T.", refs: 4 },
      { name: "Dana P.", refs: 3 },
      { name: "Luis M.", refs: 2 },
      { name: "8 other agents", refs: 0, gap: true },
    ],
  },

  sources: [
    { name: "QuickBooks", status: "connected", last_synced: null },
    { name: "Sisu", status: "connected", last_synced: null },
    { name: "Follow Up Boss", status: "connected", last_synced: null },
    { name: "Arive", status: "disconnected", last_synced: null },
  ],
};

export default sampleData;
