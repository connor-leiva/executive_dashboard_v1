/* Offline payload for the ads tab, used only when VITE_API_BASE is unset.
 *
 * Shaped exactly like the real response so the view cannot develop a dependency on a field the
 * server does not send. Internally consistent to the dollar: every derived figure below is what
 * the server's rate() would produce from these components, so a wrong number here would show up
 * as a wrong number on screen rather than hiding.
 *
 * funnel/revenue/maturity are null, matching what the API returns today. That is Phase 1: the
 * click layer is real and the chain to closed revenue is not built yet, and the view says so
 * rather than rendering an empty ladder.
 */
const CAMPAIGNS = [
  { external_id: "23851", id: null, name: "Forum Dallas - VIP Guest", group: "Forum Dallas",
    daily_budget: 150, lifetime_budget: 4500,
    spend: 18240, impressions: 1_284_000, clicks: 21_400, link_clicks: 14_820, leads: 214 },
  { external_id: "23852", id: null, name: "KB - The Shift - August2026", group: "beCollective / The Shift",
    daily_budget: 200, lifetime_budget: null,
    spend: 12980, impressions: 962_000, clicks: 13_100, link_clicks: 8_940, leads: 121 },
  { external_id: "23853", id: null, name: "Forum Scottsdale - January", group: "Forum Scottsdale",
    daily_budget: 80, lifetime_budget: 2400,
    spend: 7420, impressions: 511_000, clicks: 6_800, link_clicks: 4_120, leads: 38 },
  { external_id: "23854", id: null, name: "The Edge Webinar", group: "Edge Webinar", status: "ACTIVE",
    daily_budget: 40, lifetime_budget: null,
    spend: 6110, impressions: 402_000, clicks: 5_900, link_clicks: 3_640, leads: 44 },
  { external_id: "23855", id: null, name: "Retargeting - warm list", group: "Other", status: "PAUSED",
    daily_budget: null, lifetime_budget: null,
    spend: 3180, impressions: 148_000, clicks: 2_240, link_clicks: 1_510, leads: 0 },
];

const rate = (n, d, scale = 1) => (d ? (n / d) * scale : null);

const withRates = CAMPAIGNS.map((c) => ({
  ...c,
  ctr: rate(c.link_clicks, c.impressions, 100),
  cpm: rate(c.spend, c.impressions, 1000),
  cpc: rate(c.spend, c.link_clicks),
  cpl: rate(c.spend, c.leads),
}));

const sum = (f) => withRates.reduce((a, c) => a + c[f], 0);
const SPEND = sum("spend");
const IMPR = sum("impressions");
const LINKS = sum("link_clicks");
const LEADS = sum("leads");

const groupsOf = (rows) => {
  const m = {};
  rows.forEach((r) => { (m[r.group] = m[r.group] || []).push(r); });
  return Object.entries(m)
    .map(([name, campaigns]) => ({
      name, campaigns,
      spend: campaigns.reduce((a, c) => a + c.spend, 0),
      link_clicks: campaigns.reduce((a, c) => a + c.link_clicks, 0),
    }))
    .sort((a, b) => b.spend - a.spend);
};

export const sampleAdsOverview = {
  connected: true,
  account: { id: "sample", name: "Spring B · Meta", external_id: "act_587749862890426",
             currency: "USD", timezone_name: "America/Denver" },
  range: { start: "2026-11-01", end: "2026-11-11", label: "Month to date", days: 11 },
  scope: { kind: "account", id: null, name: "Spring B · Meta" },
  campaigns_available: [
    { id: "g1", name: "KB - The Shift - August2026", spend: 99_200 },
    { id: "g2", name: "KB - ForumVIP - SubmitApplication", spend: 9_900 },
    { id: "g3", name: "KB - Upgrade", spend: 5_400 },
  ],
  basis: "cohort",
  totals: {
    spend: SPEND, impressions: IMPR, clicks: sum("clicks"), link_clicks: LINKS, leads: LEADS,
    // The period rates, computed from SUMS - not the mean of the per-campaign rates. The two
    // differ, and this file existing to be internally consistent is part of the point.
    ctr: rate(LINKS, IMPR, 100),
    cpm: rate(SPEND, IMPR, 1000),
    cpc: rate(SPEND, LINKS),
    cpl: rate(SPEND, LEADS),
  },
  bands: { ctr: "good", cpm: "good", cpl: "good" },
  campaigns: withRates,
  digest: {
    day: "2026-11-09",
    account: "Spring B · Meta",
    brands: [
      { name: "Forum Dallas", spent: 612, daily_plan: 357, pace: 171 },
      { name: "beCollective / The Shift", spent: 480, daily_plan: 571, pace: 84 },
      { name: "Edge Webinar", spent: 90, daily_plan: 114, pace: 79 },
    ],
    recommendations: [
      { name: "Forum-Dallas-1080x1080-v3", verdict: "kill", group: "Forum Dallas",
        reason: "spent $6,420, no seats / no enrollments" },
    ],
    kept: 4,
    autopilot: [],
    fatigue: ["Forum-Dallas-1080x1080-v3"],
    attendance: { attended: 18, missing: false },
    seats: [
      { name: "Forum VIP", spend: 25660, seats: 1, cost_per_seat: 25660 },
      { name: "Edge webinar", spend: 6110, seats: 4, cost_per_seat: 1528 },
    ],
  },
  writes: {
    can_write: false,
    can_manage: true,
    can_undo: false,
    scope: "ads_read",
    killed: false,
    reconnect: "Reconnect Meta to enable changes",
    message: "Reconnect Meta to enable changes",
    detail: "The saved token can read this account. It does not include ads_management, so Apply previews the change and does not send it.",
    autopilot: { "Forum Dallas": { on: false, pause_only: true, max_daily_spend: 400 } },
  },
  week: {
    has_plan: true,
    days: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((label, i) => ({
      date: `2026-11-${String(9 + i).padStart(2, "0")}`, label,
      spend: [900, 1100, 640, 0, 0, 0, 0][i],
      plan: 1214, open: i === 2, future: i > 2,
    })),
    brands: [
      { name: "Forum Dallas", spend: 1400, plan: 2500 },
      { name: "beCollective / The Shift", spend: 800, plan: 4000 },
      { name: "Forum Scottsdale", spend: 220, plan: 1200 },
      { name: "Edge Webinar", spend: 220, plan: 800 },
    ],
  },
  recommendations: [
    { level: "ad", external_id: "yoga-h1", name: "Video - Yoga - ResultsH1", group: "Forum Dallas",
      campaign: "Forum Dallas - VIP Guest", adset: "Yoga", spend: 420, leads: 0, seats: 0, status: "ACTIVE",
      verdict: "kill", action: "pause", metric: "seats", proposed: { status: "PAUSED" },
      reason: "Spent $420, no seats / no enrollments" },
    { level: "ad", external_id: "yoga-h2", name: "Video - Yoga - ResultsH2", group: "Forum Dallas",
      campaign: "Forum Dallas - VIP Guest", adset: "Yoga", spend: 180, leads: 0, seats: 0, status: "ACTIVE",
      verdict: "kill", action: "pause", metric: "seats", proposed: { status: "PAUSED" },
      reason: "Spent $180, no seats / no enrollments" },
    { level: "ad", external_id: "yoga2-h1", name: "Video - Yoga2 - ResultsH1", group: "Forum Dallas",
      campaign: "Forum Dallas - VIP Guest", adset: "Yoga", spend: 205, leads: 0, seats: 0, status: "ACTIVE",
      verdict: "kill", action: "pause", metric: "seats", proposed: { status: "PAUSED" },
      reason: "Spent $205, no seats / no enrollments" },
    { level: "ad", external_id: "yoga2-low", name: "Video - Yoga2 - ResultsH1", group: "Forum Dallas",
      campaign: "Forum Dallas - VIP Guest", adset: "Yoga", spend: 12, leads: 0, seats: null, status: "ACTIVE",
      verdict: "keep", action: null, metric: "delivery", proposed: null,
      reason: "Below the minimum delivery ($25 and 1,000 impressions) before a change." },
    { level: "campaign", external_id: "23854", name: "The Edge Webinar", group: "Edge Webinar",
      campaign: "The Edge Webinar", adset: null, spend: 6110, leads: 44, seats: 4, status: "ACTIVE",
      verdict: "scale", action: "set_daily_budget", metric: "cpl",
      daily_budget: 40, budget_external_id: "23854", proposed: { daily_budget: 48 },
      reason: "CTR and cost are in the good band. Raise the daily budget from $40 to $48." },
    { level: "ad", external_id: "a2", name: "KB-Shift-30s-motion", group: "beCollective / The Shift",
      campaign: "KB - The Shift - August2026", adset: "Motion", spend: 5180, leads: 51, seats: 0, status: "ACTIVE",
      verdict: "keep", action: null, metric: "ctr", proposed: null,
      reason: "The numbers are inside the bands." },
    { level: "ad", external_id: "a3", name: "Event-Research-static-4x5", group: "Forum Dallas",
      campaign: "Forum Dallas - VIP Guest", adset: "Static", spend: 18, leads: 0, seats: null, status: "PAUSED",
      verdict: "keep", action: null, metric: "delivery", proposed: null,
      reason: "Below the minimum delivery ($25 and 1,000 impressions) before a change." },
  ],
  weekly_budgets: {
    "Forum Dallas": 2500, "Forum Scottsdale": 1200,
    "beCollective / The Shift": 4000, "Edge Webinar": 800,
  },
  groups: groupsOf(withRates).map((g) => {
    const weekly = { "Forum Dallas": 2500, "Forum Scottsdale": 1200,
                     "beCollective / The Shift": 4000, "Edge Webinar": 800 }[g.name] || null;
    // The server's rule: weekly x the window's days / 7, and the window above is 11 days.
    const plan = weekly ? weekly * 11 / 7 : null;
    return { ...g, weekly_budget: weekly, plan_spend: plan,
             pace: plan ? Math.round(g.spend / plan * 1000) / 10 : null };
  }),
  unmatched_count: 1,
  alerts: [
    { tone: "bad", metric: "cpl",
      text: "Retargeting - warm list spent $3,180 and reported no leads" },
    { tone: "warn", metric: "grouping",
      text: "1 campaign fell to Other - the grouping rules may have drifted from how the account is being named" },
    { tone: "warn", metric: "fatigue",
      text: "1 ad with rising frequency and falling CTR: Forum-Dallas-1080x1080-v3" },
  ],
  // The ladder. Counts are what the server would return; conversions are computed from them, so
  // a wrong number here shows up on screen rather than hiding.
  brand_funnels: {
    forum: {
      title: "Forum VIP",
      spend: 25660,
      note: "Qualified guests are Spring's VIP page and guest application. A seat is Confirmed or Dallas VIP guest confirmed. Cost per seat and cost per member use Forum spend only.",
      rungs: [
        { key: "click", label: "Link clicks", zone: "meta", n: 18940, prev: null, conversion: null, cost_per: 1.35, dated: 18940, undated: 0, people: [] },
        { key: "lead", label: "Leads · Meta", zone: "meta", diagnostic: true, n: 252, prev: 18940, conversion: 1.33, cost_per: 101.8, dated: 252, undated: 0, people: [] },
        { key: "qualified", label: "Qualified guest lead", zone: "axcion", n: 2, prev: 252, conversion: 0.8, cost_per: 12830, dated: 2, undated: 0, people: [
          { name: "Ada Lovelace", email: "ada@example.com", reached: "2026-08-12", detail: "VIP Page" },
          { name: "Grace Hopper", email: "grace@example.com", reached: "2026-08-18", detail: "guest-application" },
        ] },
        { key: "seat", label: "VIP guest seat", zone: "axcion", n: 3, prev: 2, conversion: 150, cost_per: 8553, dated: 3, undated: 0, people: [
          { name: "Ada Lovelace", email: "ada@example.com", reached: "2026-08-20", detail: "VIP Guest: Confirmed" },
        ] },
        { key: "member", label: "Forum member", zone: "axcion", n: 1, prev: 1, conversion: 100, cost_per: 25660, dated: 1, undated: 0, people: [
          { name: "Ada Lovelace", email: "ada@example.com", reached: "2026-08-28", detail: "Onboarded" },
        ] },
      ],
    },
    edge: {
      title: "Edge webinar",
      spend: 6110,
      note: "Registration is the Edge event list. Attendance is shown only when a registration is marked attended. A purchase is a paid Edge subscription that started in this window.",
      rungs: [
        { key: "click", label: "Link clicks", zone: "meta", n: 3640, prev: null, conversion: null, cost_per: 1.68, dated: 3640, undated: 0, people: [] },
        { key: "lead", label: "Leads · Meta", zone: "meta", diagnostic: true, n: 44, prev: 3640, conversion: 1.2, cost_per: 138.9, dated: 44, undated: 0, people: [] },
        { key: "registered", label: "Webinar registration", zone: "axcion", n: 18, prev: 44, conversion: 40.9, cost_per: 339.4, dated: 18, undated: 0, people: [
          { name: "Katherine Johnson", email: "kj@example.com", reached: "2026-08-04", detail: "Webinar registration" },
        ] },
        { key: "attended", label: "Attended", zone: "axcion", missing: true, n: null, prev: 18, conversion: null, cost_per: null, dated: 0, undated: 0, people: [],
          note: "Attendance is not on the Edge registration, so this rung is blank." },
        { key: "purchase", label: "Edge purchase", zone: "axcion", n: 4, prev: 18, conversion: 22.2, cost_per: 1527.5, dated: 4, undated: 0, people: [
          { name: "Katherine Johnson", email: "kj@example.com", reached: "2026-08-11", detail: "Edge" },
        ] },
      ],
    },
  },
  funnel: [
    { key: "impression", label: "Impressions", zone: "meta", n: IMPR, prev: null,
      conversion: null, cost_per: SPEND / IMPR, dated: IMPR, undated: 0 },
    { key: "click", label: "Link clicks", zone: "meta", n: LINKS, prev: IMPR,
      conversion: (LINKS / IMPR) * 100, cost_per: SPEND / LINKS, dated: LINKS, undated: 0 },
    { key: "lead", label: "Leads · Meta", zone: "meta", diagnostic: true, n: LEADS, prev: LINKS,
      conversion: (LEADS / LINKS) * 100, cost_per: SPEND / LEADS, dated: LEADS, undated: 0 },
    { key: "registered", label: "Registered", zone: "axcion", n: 312, prev: LEADS,
      conversion: (312 / LEADS) * 100, cost_per: SPEND / 312, dated: 312, undated: 0 },
    { key: "booked", label: "Call booked", zone: "axcion", n: 96, prev: 312,
      conversion: (96 / 312) * 100, cost_per: SPEND / 96, dated: 96, undated: 0 },
    { key: "applied", label: "Applied", zone: "axcion", n: 71, prev: 96,
      conversion: (71 / 96) * 100, cost_per: SPEND / 71, dated: 71, undated: 0 },
    { key: "held", label: "Call held", zone: "axcion", n: 58, prev: 71,
      conversion: (58 / 71) * 100, cost_per: SPEND / 58, dated: 46, undated: 12 },
    // Cash received counts everyone who HAS paid, not only those still sitting at the stage -
    // the 14 enrolled members paid to get where they are. 19 + 14 = 33, which is why Enrolled
    // now converts at 42% instead of the impossible 600% the exclusive count produced.
    { key: "committed", label: "Cash received", zone: "axcion", n: 33, prev: 58,
      conversion: (33 / 58) * 100, cost_per: SPEND / 33, dated: 33, undated: 0, value: 118500,
      still_here: 19 },
    { key: "closed", label: "Enrolled", zone: "axcion", closes: true, n: 14, prev: 33,
      conversion: (14 / 33) * 100, cost_per: SPEND / 14, dated: 14, undated: 0, value: 174000 },
  ],
  revenue: {
    // TWO TOTALS. Contracted is signed value over the enrolled; cash is due-at-signing over
    // everyone who has paid, which is the committed rung as well - 19 + 14 = 33 people.
    contracted: 174000, collected: 118500, projected: null,
    roas_contracted: 174000 / SPEND, roas_collected: 118500 / SPEND, roas_projected: null,
    annualized_closes: 2, collected_join: "email",
    collected_source: "upfront", collected_modelled: 118500, collected_measured: 41200,
    cash_people: 33, committed_people: 19,
    unpriced_closes: 1, ghl_priced_closes: 2,
  },
  cac: {
    attributed: SPEND / 14, blended: SPEND / 34,
    blended_label: "Blended - every enrollment in the window, not only those traced to an ad. " +
      "Always lower, and never the ads number.",
    attributed_closes: 14, all_closes: 34, blended_available: true,
  },
  unattributed: {
    closes: 20,
    note: "Enrollments with no attribution row. Counted here and assigned to no campaign.",
  },
  maturity: { fitted: false, pct: null, median_lag_days: null, expected_additional: null,
              note: "No cohort curve has been fitted yet, so no projection is shown." },
  // The two denominators, side by side and never added. The gap is mostly people who DID
  // register and could not be matched - stripped UTM, cross-device, view-through.
  coverage: {
    registrations_total: 1416, registrations_matched: 879, registrations_unattributed: 537,
    by_grade: { campaign: 879 }, campaign_grade_or_better: 879, ad_grade: 0,
    meta_leads: LEADS, gap: LEADS - 879,
    gap_note:
      "Meta counts leads it attributes to itself; Axcion counts registrations it can match to a " +
      "campaign. They measure overlapping populations, so the difference is not a drop-off - much " +
      "of it is people who did register and could not be matched.",
  },
  funnel_available: true,
  archetype: "program",
  freshness: { last_synced_at: "2026-08-30T14:20:00Z", last_error: null },
  accounts_list: [{ id: "sample", name: "Spring B · Meta", external_id: "act_587749862890426",
                    currency: "USD", timezone_name: "America/Denver", business_key: "springb",
                    archetype: "program", funnel_available: false, status: "active",
                    last_synced_at: "2026-08-30T14:20:00Z", last_error: null,
                    campaigns: 5, ads: 34, first_day: "2026-05-01", last_day: "2026-08-30" }],
};

export const sampleAdsWrites = {
  writes: [
    { id: "w1", action: "pause", level: "ad", name: "Forum-Dallas-1080x1080-v3",
      reason: "Spent $6,420, no seats / no enrollments", status: "preview", dry_run: true,
      before: { status: "ACTIVE" }, after: { status: "PAUSED" },
      actor: "Connor", source: "user", at: "2026-11-11T14:10:00Z", meta_error: null },
    { id: "w2", action: "set_daily_budget", level: "campaign", name: "The Edge Webinar",
      reason: "CTR and cost are in the good band", status: "applied", dry_run: false,
      before: { daily_budget: 40 }, after: { daily_budget: 48 },
      actor: "Autopilot", source: "autopilot", at: "2026-11-10T13:00:00Z", meta_error: null },
  ],
};

export const sampleAdsCreatives = {
  connected: true,
  total: 4,
  ads: [
    { id: "a1", name: "Forum-Dallas-1080x1080-v3", headline: "The room where it changes",
      thumbnail_url: null, spend: 6420, impressions: 448_000, link_clicks: 5_210, leads: 88,
      ctr: rate(5210, 448000, 100), ctr_prior: rate(6400, 448000, 100),
      frequency: 2.4, fatigue: true, seats: 1, cost_per_seat: 6420,
      cpl: rate(6420, 88), url_tags: "utm_source=meta&utm_content={{ad.id}}" },
    { id: "a2", name: "KB-Shift-30s-motion", headline: "Ninety days from now",
      thumbnail_url: null, spend: 5180, impressions: 372_000, link_clicks: 3_640, leads: 51,
      ctr: rate(3640, 372000, 100), cpl: rate(5180, 51), seats: 0, cost_per_seat: null,
      url_tags: "utm_source=meta&utm_campaign=KB-Shift" },
    // thumbnail_url stays null across the sample. A deliberately-broken URL was tried here to
    // exercise the expired-image fallback and made the preview worse: the request HANGS rather
    // than erroring, so the tile showed an empty box forever instead of the placeholder. The
    // fallback renders through the same branch as the null case, which every tile here covers.
    { id: "a3", name: "Event-Research-static-4x5", headline: "What would you call it?",
      thumbnail_url: null, spend: 3110, impressions: 214_000, link_clicks: 1_820, leads: 19,
      ctr: rate(1820, 214000, 100), cpl: rate(3110, 19), url_tags: "utm_source=meta" },
    { id: "a4", name: "Warm-list-carousel", headline: "You already know",
      thumbnail_url: null, spend: 2040, impressions: 96_000, link_clicks: 860, leads: 0,
      ctr: rate(860, 96000, 100), cpl: null, url_tags: "utm_source=meta" },
  ],
  seats_available: true,
  revenue_available: false,
  revenue_reason:
    "Ad-level revenue needs utm_content={{ad.id}} on the ad URLs and a matching utm_content field " +
    "mapped in the CRM. 0 of 34 ads carry it today.",
};

/* The grouping editor's offline payload. Uses the live account's real campaign names so the
   preview shows where those names land under the brand defaults. */
export const sampleAdsGrouping = {
  connected: true,
  account: "sample",
  rules: null,
  using_defaults: true,
  fallback: "Other",
  match_kinds: ["prefix", "contains"],
  defaults: [
    { match: "contains", value: "forum dallas", label: "Forum Dallas" },
    { match: "contains", value: "scottsdale", label: "Forum Scottsdale" },
    { match: "contains", value: "the shift", label: "beCollective / The Shift" },
    { match: "contains", value: "edge", label: "Edge Webinar" },
    { match: "contains", value: "forum", label: "Forum" },
  ],
  campaigns: [
    { id: "g1", name: "KB - The Shift - August2026", spend: 99_200, group: "beCollective / The Shift" },
    { id: "g2", name: "KB - ForumVIP - SubmitApplication", spend: 9_900, group: "Forum" },
    { id: "g3", name: "KB - Upgrade", spend: 5_400, group: "Other" },
    { id: "g4", name: "KB - BeCollective", spend: 3_100, group: "beCollective / The Shift" },
    { id: "g5", name: "KB - Utah Life Mastermind - Lead Form", spend: 1_900, group: "Other" },
    { id: "g6", name: "Generate Inner Circle Interest [Messages]", spend: 640, group: "Other" },
  ],
  unmatched: 3,
  period: ["2026-06-02", "2026-08-30"],
};

/* Offline drill rows, so the panel can be opened and looked at in preview. Deliberately shows
   BOTH shapes: a stage with people, and the "we do not record this" note that distinguishes a
   real zero from an unrecorded one. */
export function sampleAdsDrill(stage, label) {
  if (stage === "applied") {
    return {
      metric: `funnel.${stage}`, type: "records", title: label, count: 0,
      subtitle: "0 at this stage · counted where the person was FIRST SEEN in this window",
      columns: ["name", "email", "campaign", "match", "first_seen", "reached", "url"], rows: [],
      note: `No ${String(label).toLowerCase()} records exist anywhere in this workspace, for any `
            + "window or campaign - so this rung reads zero because the stage is not being "
            + "recorded, not because nobody reached it.",
    };
  }
  const cols = stage === "closed"
    ? ["name", "email", "campaign", "match", "payment", "value", "upfront", "cash", "priced",
       "first_seen", "reached", "url"]
    : ["name", "email", "campaign", "match", "first_seen", "reached", "url"];
  // The financed row is the whole point: she CONTRACTED 14,000 and put 5,000 down. Before this,
  // the deposit was what the Contracted column showed - or nothing at all.
  const rows = [
    { name: "Dana Whitfield", email: "dana@example.com", campaign: "KB - The Shift - August2026",
      match: "campaign", payment: "Financed", value: "14,000", upfront: "5,000", cash: "5,000",
      priced: "price sheet", first_seen: "2026-08-04", reached: "2026-08-19",
      url: "https://example.test/contact/1" },
    { name: "Marcus Alvey", email: "marcus@example.com", campaign: "KB - The Shift - August2026",
      match: "campaign", payment: "PIF", value: "12,000", upfront: "12,000", cash: "12,000",
      priced: "price sheet", first_seen: "2026-08-02", reached: "2026-08-15",
      url: "https://example.test/contact/2" },
    { name: "Robin Nkemdi", email: "robin@example.com", campaign: "KB - The Shift - August2026",
      match: "campaign", payment: "Custom", value: "9,500", upfront: "—", cash: "—",
      priced: "GHL amount", first_seen: "2026-08-06", reached: "2026-08-21",
      url: "https://example.test/contact/3" },
  ];
  return {
    metric: `funnel.${stage}`, type: "records", title: label, count: rows.length,
    subtitle: `${rows.length} at this stage · counted where the person was FIRST SEEN in `
              + "this window, wherever the stage landed later",
    columns: cols, rows, note: null,
  };
}
