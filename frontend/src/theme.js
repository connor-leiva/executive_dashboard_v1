/* Spring · Command Center — shared brand tokens + formatting helpers.
   Palette + type from Spring's Visual Identity System:
   Evergreen / Parchment / Poppy, Poppins + Inter, ribbed gradient.
   Copied verbatim from the canonical mockup. */

export const T = {
  evergreen: "#002E2C",
  meadow: "#61835E",
  sprout: "#B8CCB8",
  parchment: "#F8F5F2",
  line: "#E8E0D4",
  white: "#FFFFFF",
  petal: "#FFBA9F",
  poppy: "#FA8069",
  poppyText: "#CE4E29",
  mist: "#DCE7E9",
  teal: "#227175",
  daffodil: "#FFF3AD",
  ink: "#002E2C",
  slate: "#56655C",
  muted: "#8A968C",
  onDark: "#F3EEE7",
  onDarkMute: "#9CB0AB",
  // The Forum focused view: daffodil = "attention today", amber = watch,
  // meadow tints = positive progress. No poppy in the Forum view.
  meadowInk: "#4F6A4D",
  meadowBg: "#E9EFE7",
  daffodilBg: "#FFF9D6",
  amber: "#9C6A1E",
  amberBg: "#F5EAD3",
};

export const STATUS = {
  healthy: { dot: T.meadow, text: "#4F6A4D", label: "Healthy" },
  watch: { dot: T.poppy, text: T.poppyText, label: "Watch" },
  opportunity: { dot: T.teal, text: T.teal, label: "Opportunity" },
};

export const usd = (n) => "$" + Math.abs(Math.round(n)).toLocaleString("en-US");
export const signed = (n) => (n < 0 ? `(${usd(n)})` : usd(n));

// "4 minutes ago" from an ISO timestamp; null-safe.
export function relativeTime(iso) {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  const s = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} minute${m === 1 ? "" : "s"} ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} hour${h === 1 ? "" : "s"} ago`;
  const d = Math.round(h / 24);
  return `${d} day${d === 1 ? "" : "s"} ago`;
}

// Provider key → display name.
export const PROVIDER_NAME = {
  qbo: "QuickBooks",
  sisu: "Sisu",
  fub: "Follow Up Boss",
  ghl: "Go High Level",
  arive: "Arive",
};
