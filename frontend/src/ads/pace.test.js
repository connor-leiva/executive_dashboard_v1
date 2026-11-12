/* The pace arithmetic behind Today's "This week's pace" and Budget's plan figures.
 * Run: node --test src/ads/pace.test.js   (from frontend/)
 */
import assert from "node:assert/strict";
import test from "node:test";

import {
  accountToday, brandPace, daysBetween, elapsedDays, paceStatus, weekPace, windowPace, windowPhrase,
} from "./pace.js";

// The sample week: Monday and Tuesday done, Wednesday open, Thursday to Sunday still to come.
const WEEK = {
  has_plan: true,
  days: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((label, i) => ({
    date: `2026-11-${String(9 + i).padStart(2, "0")}`, label,
    spend: [900, 1100, 640, 0, 0, 0, 0][i], plan: 1214, open: i === 2, future: i > 2,
  })),
  brands: [
    { name: "Forum Dallas", spend: 1400, plan: 2500 },
    { name: "beCollective / The Shift", spend: 800, plan: 4000 },
    { name: "Unplanned", spend: 300, plan: null },
  ],
};

test("the band edges: 90 and 110 are on pace, either side is not", () => {
  assert.equal(paceStatus(89.9), "behind");
  assert.equal(paceStatus(90), "on");
  assert.equal(paceStatus(110), "on");
  assert.equal(paceStatus(110.1), "ahead");
  assert.equal(paceStatus(null), "none");
  assert.equal(paceStatus(Number.NaN), "none");
});

test("on a Wednesday a brand is measured against three days of plan, not seven", () => {
  const wk = weekPace(WEEK);
  assert.equal(wk.elapsed, 3);
  assert.equal(wk.day, 3);
  assert.equal(wk.dayName, "Wednesday");
  const dallas = wk.brands.find((b) => b.name === "Forum Dallas");
  // $1,400 against $2,500 x 3/7 = $1,071.43 through today.
  assert.equal(Math.round(dallas.pace), 131);
  assert.equal(dallas.status, "ahead");
  // The bar still shows the whole week: $1,400 of $2,500.
  assert.equal(Math.round(dallas.fill), 56);
});

test("regression: the old full-week division called an ahead brand behind", () => {
  const dallas = weekPace(WEEK).brands.find((b) => b.name === "Forum Dallas");
  const oldMath = (dallas.spend / dallas.plan) * 100;
  assert.equal(paceStatus(oldMath), "behind");
  assert.notEqual(dallas.status, paceStatus(oldMath));
});

test("a brand with no weekly plan has no bar", () => {
  assert.deepEqual(weekPace(WEEK).brands.map((b) => b.name), ["Forum Dallas", "beCollective / The Shift"]);
});

test("on Sunday the plan through today is the whole week", () => {
  const sunday = { ...WEEK, days: WEEK.days.map((d, i) => ({ ...d, open: i === 6, future: false })) };
  const wk = weekPace(sunday);
  assert.equal(wk.share, 1);
  const dallas = wk.brands.find((b) => b.name === "Forum Dallas");
  assert.equal(Math.round(dallas.pace), Math.round(dallas.fill));
});

test("a week with no days gives no pace rather than a wrong one", () => {
  const wk = weekPace({ days: [], brands: [{ name: "A", spend: 10, plan: 70 }] });
  assert.equal(wk.share, null);
  assert.equal(wk.brands[0].status, "none");
  assert.equal(wk.day, null);
});

test("days are counted inclusively and a DST change loses none", () => {
  assert.equal(daysBetween("2026-11-01", "2026-11-11"), 11);
  assert.equal(daysBetween("2026-03-01", "2026-03-31"), 31); // US clocks change on March 8
  assert.equal(daysBetween("2026-11-01", "2026-11-01"), 1);
});

test("elapsed days: closed, open, and not yet started windows", () => {
  const mtd = { start: "2026-11-01", end: "2026-11-11", days: 11 };
  assert.equal(elapsedDays(mtd, "2026-11-11"), 11);
  const year = { start: "2026-01-01", end: "2026-12-31", days: 365 };
  assert.equal(elapsedDays(year, "2026-11-11"), 315);
  const lastMonth = { start: "2026-10-01", end: "2026-10-31", days: 31 };
  assert.equal(elapsedDays(lastMonth, "2026-11-11"), 31);
  const nextMonth = { start: "2026-12-01", end: "2026-12-31", days: 31 };
  assert.equal(elapsedDays(nextMonth, "2026-11-11"), 0);
});

const GROUPS = [
  { name: "Forum Dallas", spend: 18_240, weekly_budget: 2500 },
  { name: "Edge Webinar", spend: 2_000, weekly_budget: 800 },
  { name: "Other", spend: 1_000, weekly_budget: null },
];

test("month to date reads exactly as the server's plan does", () => {
  const data = { groups: GROUPS, range: { start: "2026-11-01", end: "2026-11-11", days: 11 } };
  const p = windowPace(data, "2026-11-11");
  // The server's plan_spend: weekly x window days / 7, summed over the planned brands.
  assert.equal(p.expected, (3300 * 11) / 7);
  assert.equal(p.spend, 21_240);
  assert.equal(p.partial, false);
  assert.equal(p.open, true);
});

test("a window that runs past today is measured to today, not to its end", () => {
  const data = { groups: GROUPS, range: { start: "2026-01-01", end: "2026-12-31", days: 365 } };
  const p = windowPace(data, "2026-11-11");
  assert.equal(p.elapsed, 315);
  assert.equal(p.expected, (3300 * 315) / 7);
  assert.equal(p.partial, true);
});

test("a window that has not started has no pace", () => {
  const data = { groups: GROUPS, range: { start: "2026-12-01", end: "2026-12-31", days: 31 } };
  const p = windowPace(data, "2026-11-11");
  assert.equal(p.pace, null);
  assert.equal(p.status, "none");
});

test("no weekly plans anywhere is 'no plan', never zero percent", () => {
  const data = { groups: [{ name: "A", spend: 100 }], range: { start: "2026-11-01", end: "2026-11-11", days: 11 } };
  const p = windowPace(data, "2026-11-11");
  assert.equal(p.plan, 0);
  assert.equal(p.pace, null);
});

test("a brand row is on the same footing as the headline", () => {
  const row = brandPace({ spend: 18_240, weekly_budget: 2500 }, 11);
  assert.equal(row.plan, (2500 * 11) / 7);
  assert.ok(row.left < 0);
  assert.equal(row.status, "ahead");
  assert.equal(brandPace({ spend: 5, weekly_budget: null }, 11).status, "none");
  assert.equal(brandPace({ spend: 5, weekly_budget: "" }, 11).plan, null);
});

test("today comes from the account's week, then the browser's local date", () => {
  assert.equal(accountToday({ week: WEEK }), "2026-11-11");
  // 11pm on Nov 11 in a UTC-7 zone is Nov 12 in UTC; the local date must win.
  const evening = new Date(2026, 10, 11, 23, 30);
  assert.equal(accountToday({}, evening), "2026-11-11");
});

test("window labels read inside a sentence", () => {
  assert.equal(windowPhrase("Month to date"), "month to date");
  assert.equal(windowPhrase("Last 7 days"), "last 7 days");
  assert.equal(windowPhrase("This year"), "this year");
  assert.equal(windowPhrase("2026-11-01 to 2026-11-30"), "2026-11-01 to 2026-11-30");
  assert.equal(windowPhrase(undefined), "this window");
});
