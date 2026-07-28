/* Spring · Command Center — shared brand tokens + formatting helpers.
   Palette + type from Spring's Visual Identity System:
   Evergreen / Parchment / Poppy, Poppins + Inter, ribbed gradient.
   Copied verbatim from the canonical mockup. */

/* Ramp-native brand tokens (Spring Command Center brand spec, Section 3). */
export const T = {
  evergreen: "#002E2C",
  ink: "#002E2C",          // text 1
  secondary: "#334733",    // text 2
  tertiary: "#4D6A4D",     // text 3
  slate: "#334733",        // secondary text
  muted: "#89A989",        // text 4 / muted
  meadow: "#61835E",
  meadowInk: "#4D6A4D",
  meadowBg: "#E9EFE7",
  sprout: "#B8CCB8",
  parchment: "#F6F0E9",    // page surface
  page: "#F6F0E9",
  line: "#EAE1D6",         // hairline
  white: "#FFFFFF",
  petal: "#FFBA9F",
  petalDeep: "#E08863",    // deeper petal — pre-window pill, plan accents, focus rings
  poppy: "#FA8069",
  poppyActive: "#F74926",
  poppyText: "#D92B08",    // gap / danger text
  gapText: "#D92B08",
  mist: "#DCE7E9",
  teal: "#227175",
  daffodil: "#FFDD1F",     // flag dot / attention accent
  daffodilBg: "#FFF9D6",
  daffodilText: "#6D5336",
  amber: "#6D5336",        // alias → daffodilText (Forum/drawer "watch" text)
  amberBg: "#FFF9D6",      // alias → daffodilBg
  onDark: "#F3EEE7",
  onDarkMute: "#9CB0AB",
};

export const STATUS = {
  healthy: { dot: T.meadow, text: T.tertiary, label: "Healthy" },
  watch: { dot: T.poppy, text: T.poppyText, label: "Watch" },
  opportunity: { dot: T.teal, text: T.teal, label: "Opportunity" },
};

// A brand token (or any #rrggbb) as an rgba() string — lets translucent overlays derive
// from the palette instead of hardcoding rgb triples.
export const alpha = (hex, a) => {
  const h = String(hex).replace("#", "");
  const n = parseInt(h.length === 3 ? h.replace(/./g, "$&$&") : h, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
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
