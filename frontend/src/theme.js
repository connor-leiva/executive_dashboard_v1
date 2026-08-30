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
  edge: "#B26248",         // The Edge nav/identity — terracotta, distinct from the other programs
  daffodil: "#FFDD1F",     // flag dot / attention accent
  daffodilBg: "#FFF9D6",
  daffodilText: "#6D5336",
  amber: "#6D5336",        // alias → daffodilText (Forum/drawer "watch" text)
  amberBg: "#FFF9D6",      // alias → daffodilBg
  onDark: "#F3EEE7",
  onDarkMute: "#9CB0AB",
};

/* ── Books Statement ledger palette + type ────────────────────────────────────────────────
   From Connor's design canvas ("Books Statement.dc.html", 2026-08-21), which supersedes the
   brand tokens above FOR THIS SURFACE ONLY — a financial statement wants a warmer, lower
   contrast ground than the dashboard's, and gold rather than daffodil for the composed rows.

   The hexes live here rather than in the component for the usual reason: a palette in a view
   file is a palette nobody can find, and `frontend/src/books/` is grepped for raw hex.

   Type: Archivo on every number and label (it has tabular figures, which is the whole reason
   a ledger column lines up), Outfit on prose. */
export const LEDGER = {
  // surfaces, lightest to darkest
  card: "#F7F2EA",
  cardEdge: "#D8CDBC",
  paper: "#FFFFFF",
  controls: "#FDFBF7",
  colHead: "#FBF7F1",
  totalBand: "#FBF8F2",
  sectionBand: "#EFE8DC",
  track: "#F0E9DD",

  // rules, faintest to strongest
  ruleLine: "#F7F1E7",       // between account lines
  ruleGroup: "#F4EDE2",      // under a bucket heading
  ruleInner: "#E2D9CA",      // around blocks
  ruleHeader: "#E8E0D3",
  ruleSection: "#DCD2C2",
  pillEdge: "#E0D7C9",

  // ink, darkest to faintest
  deep: "#0E2B22",           // dark rows, active chips, the strongest rule
  onDeep: "#F3EFE6",
  body: "#40564B",           // an account name
  bodyDim: "#5C7367",
  tab: "#6D8579",
  muted: "#7C8F85",
  faint: "#8B9C92",          // column headers
  code: "#9AA9A0",           // the account-code column
  ghost: "#A4B1A8",          // footnotes, zero-signal figures

  // on the dark entity bar
  onDeepEyebrow: "#7D9A8D",
  onDeepPill: "#A7C0B4",
  onDeepPillEdge: "#2F5044",
  onDeepPct: "#9DBDAE",

  // accents
  accent: "#5F9C82",         // title tick, summary fill
  accentMuted: "#8FAE9F",    // the operating-expenses fill — a quieter green, not a tint
  accentInk: "#3F7A63",      // "ties to QuickBooks" text
  accentBg: "#EEF4F0",

  // composed (allocated) rows — gold, softer than the dashboard's daffodil
  flagBg: "#FEFAEE",
  flagBgHover: "#FBF3DE",    // hover on a composed row: deeper gold, never neutral
  flagEdge: "#E0C476",
  flagRing: "#D4B45F",
  flagInk: "#93854F",
};

/* Archivo everywhere a figure or a label appears; Outfit for prose. Both loaded in
   index.html. The fallbacks matter — a ledger that reflows when a webfont lands is worse
   than one that never had it. */
export const LEDGER_FONT = {
  num: "Archivo,system-ui,-apple-system,Segoe UI,sans-serif",
  body: "Outfit,system-ui,-apple-system,Segoe UI,sans-serif",
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
  meta_ads: "Meta Ads",
};

/* ── OPS: the operator console's palette ─────────────────────────────────────────
 *
 * Greyscale, deliberately, and it is not a styling preference — it is the only thing on screen
 * that tells an operator which building they are standing in. The console and a customer's
 * dashboard show similar-looking tables of similar-looking numbers, and the operator surface is
 * the one where "suspend" switches off a paying customer. Sharing Spring's evergreen and
 * parchment would make the two read as the same product with different data in it.
 *
 * So: no hue anywhere. If a future brand refresh adds colour here, that is a regression, not a
 * polish pass — the absence IS the signal.
 *
 * The keys mirror T's so the console reads the same as the rest of the app; only the values
 * differ. Status cannot lean on hue, so it leans on WEIGHT: the more urgent a state, the darker
 * and heavier it renders. Healthy is quiet and pale, attention sits mid-grey, and anything
 * destructive is near-black and filled. That ordering survives greyscale printing, most colour
 * blindness, and a bad monitor, which hue does not.
 */
export const OPS = {
  evergreen: "#1F1F1F",    // primary surface / primary button — the darkest structural tone
  ink: "#1A1A1A",          // text 1
  slate: "#3D3D3D",        // text 2
  tertiary: "#5C5C5C",     // text 3
  muted: "#8A8A8A",        // text 4 / uppercase labels
  line: "#E2E2E2",         // hairline
  parchment: "#F4F4F4",    // page surface
  white: "#FFFFFF",
  onDark: "#F5F5F5",       // text on the dark surface
  onDarkMute: "#A6A6A6",

  // Status, ordered by weight rather than hue — pale = fine, dark = deal with it.
  meadowBg: "#F0F0F0",     // healthy chip fill
  meadowInk: "#5C5C5C",    // healthy chip text
  meadow: "#7A7A7A",       // healthy accent
  sprout: "#D4D4D4",       // inert / disabled
  daffodil: "#4A4A4A",     // attention dot — mid grey, reads as "look at this"
  poppy: "#1F1F1F",        // destructive border
  poppyText: "#1A1A1A",    // destructive text — carried by weight and wording, not colour
};
