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
  { external_id: "23851", id: null, name: "KB-Webinar-Retarget-Q3", group: "KB · Webinar",
    spend: 18240, impressions: 1_284_000, clicks: 21_400, link_clicks: 14_820, leads: 214 },
  { external_id: "23852", id: null, name: "KB-Shift-Broad", group: "KB · Shift",
    spend: 12980, impressions: 962_000, clicks: 13_100, link_clicks: 8_940, leads: 121 },
  { external_id: "23853", id: null, name: "2026 Event Name Research v2", group: "Event Name Research",
    spend: 7420, impressions: 511_000, clicks: 6_800, link_clicks: 4_120, leads: 38 },
  { external_id: "23854", id: null, name: "Spring Webinar Broad", group: "Webinar",
    spend: 6110, impressions: 402_000, clicks: 5_900, link_clicks: 3_640, leads: 44 },
  { external_id: "23855", id: null, name: "Retargeting - warm list", group: "Other",
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
  range: { start: "2026-08-01", end: "2026-08-30", label: "Last 30 days", days: 30 },
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
  groups: groupsOf(withRates),
  unmatched_count: 1,
  alerts: [
    { tone: "bad", metric: "cpl",
      text: "Retargeting - warm list spent $3,180 and reported no leads" },
    { tone: "warn", metric: "grouping",
      text: "1 campaign fell to Other - the grouping rules may have drifted from how the account is being named" },
  ],
  // The ladder. Counts are what the server would return; conversions are computed from them, so
  // a wrong number here shows up on screen rather than hiding.
  funnel: [
    { key: "impression", label: "Impressions", zone: "meta", n: IMPR, prev: null,
      conversion: null, cost_per: SPEND / IMPR, dated: IMPR, undated: 0 },
    { key: "click", label: "Link clicks", zone: "meta", n: LINKS, prev: IMPR,
      conversion: (LINKS / IMPR) * 100, cost_per: SPEND / LINKS, dated: LINKS, undated: 0 },
    { key: "lead", label: "Leads · Meta", zone: "meta", diagnostic: true, n: LEADS, prev: LINKS,
      conversion: (LEADS / LINKS) * 100, cost_per: SPEND / LEADS, dated: LEADS, undated: 0 },
    { key: "registered", label: "Registered", zone: "acumyn", n: 312, prev: LEADS,
      conversion: (312 / LEADS) * 100, cost_per: SPEND / 312, dated: 312, undated: 0 },
    { key: "booked", label: "Call booked", zone: "acumyn", n: 96, prev: 312,
      conversion: (96 / 312) * 100, cost_per: SPEND / 96, dated: 96, undated: 0 },
    { key: "applied", label: "Applied", zone: "acumyn", n: 71, prev: 96,
      conversion: (71 / 96) * 100, cost_per: SPEND / 71, dated: 71, undated: 0 },
    { key: "held", label: "Call held", zone: "acumyn", n: 58, prev: 71,
      conversion: (58 / 71) * 100, cost_per: SPEND / 58, dated: 46, undated: 12 },
    { key: "committed", label: "Cash received", zone: "acumyn", n: 19, prev: 58,
      conversion: (19 / 58) * 100, cost_per: SPEND / 19, dated: 19, undated: 0 },
    { key: "closed", label: "Enrolled", zone: "acumyn", closes: true, n: 14, prev: 19,
      conversion: (14 / 19) * 100, cost_per: SPEND / 14, dated: 14, undated: 0 },
  ],
  revenue: {
    contracted: 174000, collected: 41200, projected: null,
    roas_contracted: 174000 / SPEND, roas_collected: 41200 / SPEND, roas_projected: null,
    annualized_closes: 2, collected_join: "email",
  },
  cac: {
    attributed: SPEND / 14, blended: SPEND / 34,
    blended_label: "Blended - every enrollment in the window, not only those traced to an ad. " +
      "Always lower, and never the ads number.",
    attributed_closes: 14, all_closes: 34,
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
      "Meta counts leads it attributes to itself; Acumyn counts registrations it can match to a " +
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

export const sampleAdsCreatives = {
  connected: true,
  total: 4,
  ads: [
    { id: "a1", name: "KB-Webinar-1080x1080-v3", headline: "The room where it changes",
      thumbnail_url: null, spend: 6420, impressions: 448_000, link_clicks: 5_210, leads: 88,
      ctr: rate(5210, 448000, 100), cpl: rate(6420, 88), url_tags: "utm_source=meta&utm_campaign=KB-Webinar" },
    { id: "a2", name: "KB-Shift-30s-motion", headline: "Ninety days from now",
      thumbnail_url: null, spend: 5180, impressions: 372_000, link_clicks: 3_640, leads: 51,
      ctr: rate(3640, 372000, 100), cpl: rate(5180, 51), url_tags: "utm_source=meta&utm_campaign=KB-Shift" },
    { id: "a3", name: "Event-Research-static-4x5", headline: "What would you call it?",
      thumbnail_url: null, spend: 3110, impressions: 214_000, link_clicks: 1_820, leads: 19,
      ctr: rate(1820, 214000, 100), cpl: rate(3110, 19), url_tags: "utm_source=meta" },
    { id: "a4", name: "Warm-list-carousel", headline: "You already know",
      thumbnail_url: null, spend: 2040, impressions: 96_000, link_clicks: 860, leads: 0,
      ctr: rate(860, 96000, 100), cpl: null, url_tags: "utm_source=meta" },
  ],
  revenue_available: false,
  revenue_reason:
    "Ad-level revenue needs utm_content={{ad.id}} on the ad URLs and a matching utm_content field " +
    "mapped in the CRM. 0 of 34 ads carry it today.",
};
