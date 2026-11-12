/* Pace, in one place, so Today and Budget cannot disagree about what "behind" means.
 *
 * Pace compares what was spent with what the plan says should have been spent BY NOW. The bug
 * this file exists for: Today's weekly bars divided week-to-date spend by the whole week's plan,
 * so on a Wednesday every brand read "behind" (Forum Dallas showed 56% when it was at 131% of
 * where it should have been), and they only became right on Sunday. The same mistake waits for
 * any window that runs past today, such as "This year" or a custom range ending next month.
 *
 * Today counts as a whole day, as it does in the server's month-to-date plan
 * (`_group_payload`: weekly x window days / 7), so the default window reads exactly as before.
 *
 * Pure on purpose: no React and no tokens, so `node --test src/ads/pace.test.js` checks the
 * arithmetic directly.
 */

export const BAND_LOW = 90;
export const BAND_HIGH = 110;

export function paceStatus(pct) {
  if (pct == null || !Number.isFinite(pct)) return "none";
  return pct < BAND_LOW ? "behind" : pct > BAND_HIGH ? "ahead" : "on";
}

const WEEKDAYS = {
  Mon: "Monday", Tue: "Tuesday", Wed: "Wednesday", Thu: "Thursday",
  Fri: "Friday", Sat: "Saturday", Sun: "Sunday",
};

export const weekdayName = (label) => WEEKDAYS[label] || label || "";

function localIso(now) {
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${m}-${d}`;
}

/* The ad account's today. The server works out this week in the account's timezone and marks
   today's column `open`, so that is the date to trust. The browser's own date is the fallback;
   never `toISOString()`, which is UTC and turns into tomorrow every evening in Denver. */
export function accountToday(data, now = new Date()) {
  const open = (data?.week?.days || []).find((d) => d.open);
  return open?.date || localIso(now);
}

function dayNumber(iso) {
  const [y, m, d] = String(iso).split("-").map(Number);
  return Date.UTC(y, m - 1, d) / 86400000;
}

/* Inclusive count of calendar days from `start` to `end`, both ISO dates. UTC arithmetic, so a
   daylight-saving change inside the range cannot lose or gain a day. */
export function daysBetween(start, end) {
  return Math.round(dayNumber(end) - dayNumber(start)) + 1;
}

/* How many of a window's days have happened: all of them once it has closed, through today
   while it is open, none before it starts. */
export function elapsedDays(range, today) {
  const total = Number(range?.days) || 0;
  if (!range?.start || !range?.end || !today) return total;
  if (today < range.start) return 0;
  const last = range.end < today ? range.end : today;
  return Math.max(0, Math.min(total || Infinity, daysBetween(range.start, last)));
}

/* The window on screen (month to date unless the period bar says otherwise): all spend against
   the weekly plans, prorated to the days that have happened. */
export function windowPace(data, today) {
  const groups = data?.groups || [];
  const plan = groups.reduce((sum, g) => sum + (Number(g.weekly_budget) || 0), 0);
  const spend = groups.reduce((sum, g) => sum + (Number(g.spend) || 0), 0);
  const range = data?.range || {};
  const days = Number(range.days) || 7;
  const elapsed = range.start ? elapsedDays(range, today) : days;
  const expected = plan ? (plan * elapsed) / 7 : 0;
  const pace = expected ? (spend / expected) * 100 : null;
  return {
    plan, spend, expected, pace, status: paceStatus(pace), days, elapsed,
    // Part of the window is still to come, so "the plan for these days" is only the plan so far.
    partial: elapsed < days,
    open: !!(range.end && today && range.end >= today),
  };
}

/* One brand's row on Budget, on the same footing as the headline above it. */
export function brandPace(group, elapsed) {
  const weekly = group?.weekly_budget;
  if (weekly === null || weekly === undefined || weekly === "") {
    return { plan: null, left: null, pace: null, status: "none" };
  }
  const plan = (Number(weekly) * elapsed) / 7;
  const spend = Number(group.spend) || 0;
  const pace = plan ? (spend / plan) * 100 : null;
  return { plan, left: plan - spend, pace, status: paceStatus(pace) };
}

/* This week, Monday to Sunday in the account's timezone. Each brand is measured against its plan
   through the end of today; the bar still shows the whole week, with a mark where today ends. */
export function weekPace(week) {
  const days = week?.days || [];
  const elapsed = days.filter((d) => !d.future).length;
  const share = days.length ? elapsed / 7 : null;
  const openIndex = days.findIndex((d) => d.open);
  const brands = (week?.brands || []).filter((b) => Number(b.plan) > 0).map((b) => {
    const plan = Number(b.plan);
    const spend = Number(b.spend) || 0;
    const expected = share == null ? null : plan * share;
    const pace = expected ? (spend / expected) * 100 : null;
    return {
      name: b.name, spend, plan, expected, pace, status: paceStatus(pace),
      fill: Math.min(100, (spend / plan) * 100),
    };
  });
  return {
    elapsed, share, brands,
    day: openIndex >= 0 ? openIndex + 1 : null,
    dayName: openIndex >= 0 ? weekdayName(days[openIndex].label) : null,
  };
}

/* The window's own label, made to read inside a sentence: "Pace, month to date". Only the
   server's named windows are lowercased; a date range or a code is left exactly as it came. */
export function windowPhrase(label) {
  if (!label) return "this window";
  return /^(Month|Quarter|Year|Last|This|Next)\b/.test(label)
    ? label.charAt(0).toLowerCase() + label.slice(1)
    : label;
}
