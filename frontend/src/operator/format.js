/* Formatting for the operator console. Every figure the console prints goes through here, so a
   unit is written the same way on every screen. */

/** Minutes since an ISO timestamp, or null when there is none. */
export function minutesSince(iso, now = Date.now()) {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : Math.max(0, (now - t) / 60000);
}

/** "just now" · "14m ago" · "3h ago" · "12d ago" · "4mo ago" · "never". */
export function ago(isoOrMinutes) {
  const min = typeof isoOrMinutes === "number" ? isoOrMinutes : minutesSince(isoOrMinutes);
  if (min == null) return "never";
  if (min < 1) return "just now";
  if (min < 60) return `${Math.round(min)}m ago`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return d < 30 ? `${d}d ago` : `${Math.floor(d / 30)}mo ago`;
}

/** Whole days since an ISO timestamp (0 for today, null for none). */
export function daysSince(iso) {
  const min = minutesSince(iso);
  return min == null ? null : Math.floor(min / 1440);
}

export const pct = (a, b) => (b ? Math.round((a / b) * 100) : 0);

/** Money arrives in cents from anything Stripe-shaped, and is formatted only at the edge. */
export const dollars = (cents) =>
  cents == null ? "—" : `$${(cents / 100).toLocaleString("en-US", { maximumFractionDigits: cents % 100 ? 2 : 0 })}`;

/** Whole-dollar list prices from plans.py. */
export const listPrice = (whole) => (whole == null ? "—" : `$${Number(whole).toLocaleString("en-US")}`);

export function compact(n) {
  if (n == null) return "—";
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `${Math.round(n / 1e3)}K`;
  return String(n);
}

export const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

export function titleCase(s) {
  return String(s || "")
    .replace(/[._-]+/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((x) => x[0].toUpperCase() + x.slice(1))
    .join(" ");
}
