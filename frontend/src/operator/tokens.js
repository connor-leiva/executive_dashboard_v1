/* The operator console's design tokens.
 *
 * Every brand value comes from ../brand/axcion.jsx, which is Axcion's identity guide as code. The
 * design file carried a hand-copied duplicate of those values; a duplicate is a second brand the
 * day either one is corrected, so none is reproduced here and a backend test fails on any hex
 * literal in this module outside the functional alert set below.
 *
 * Three deliberate departures from the guide, each a UI decision rather than a brand value:
 *   - Cards are white, not CORE.paper. White cards on a CADET[50] canvas give a dense data table
 *     the separation it needs; Paper is a ground for brand pages.
 *   - Amber and red are off-palette. The guide defines no alert colours, and an operator console
 *     needs exactly two. Healthy stays quiet (a neutral chip, no green): colour goes only where
 *     eyes are needed.
 *   - Amber text is #855C00, not the spec's #9A6B00, which measures 4.09:1 on its own chip and
 *     4.25:1 on the canvas. Both fail AA for the 10-12px text chips and notes are set in.
 */
import { CADET, CORE, NEUTRAL, TYPE } from "../brand/axcion.jsx";

export { TYPE };

export const WHITE = "#FFFFFF";

export const ALERT = {
  warn: "#855C00", warnBg: "#F7EFDA", warnLine: "#E8D6AC",
  stop: "#B3261E", stopBg: "#F8E7E4", stopLine: "#F0CFCB",
};

export const A = {
  ink: CORE.ink,              // headings, the dark bar
  ink80: CADET[800],          // ghost button text, the dark bar's rule
  body: CADET[700],           // body copy
  mute: NEUTRAL[500],         // secondary text, captions: the quietest thing anybody reads
  // NEUTRAL[400] measures 3.48:1 on white. Disabled controls, placeholders and glyphs that are
  // not text (the unsourced em-dash, a chevron) only. Never prose, never a figure.
  faint: NEUTRAL[400],
  line: CADET[100],           // hairlines
  lineMid: CADET[200],        // emphasised rules, never text
  lineSoft: NEUTRAL[100],     // inner rules
  paper: WHITE,
  ground: CADET[50],          // the canvas
  chip: NEUTRAL[100],
  hover: NEUTRAL[50],
  onInk: CADET[50],
  onInkMute: NEUTRAL[300],
  sage: CORE.sage,            // the mark on the dark bar, "ok" fills in charts
  cadet: CORE.cadet,          // THE action colour: at most one Cadet element per view
  cadetDeep: CADET[600],
  cadetInk: CADET[700],
  cadetBg: CADET[50],
  cadetLine: CADET[100],
  ...ALERT,
  lift: "0 1px 2px rgba(22,32,31,.045)",
};

/* One severity vocabulary for the whole console. `rank` is the only sort key, so the fleet list
   and the triage queue cannot disagree; the backend's fleet_health.RANK is held to these by a
   test. */
export const STATE = {
  broken:    { label: "Broken",    c: A.stop,     bg: A.stopBg,  rank: 0 },
  stalled:   { label: "Stalled",   c: A.warn,     bg: A.warnBg,  rank: 1 },
  watch:     { label: "Watch",     c: A.warn,     bg: A.warnBg,  rank: 2 },
  trial:     { label: "Trial",     c: A.cadetInk, bg: A.cadetBg, rank: 3 },
  healthy:   { label: "Healthy",   c: A.mute,     bg: A.chip,    rank: 4 },
  suspended: { label: "Suspended", c: A.mute,     bg: A.chip,    rank: 5 },
};

export const PROVIDER_NAMES = {
  qbo: "QuickBooks",
  sisu: "Sisu",
  fub: "Follow Up Boss",
  ghl: "Go High Level",
  ghl_bc: "Go High Level (second account)",
  ghl_legacy: "Go High Level (legacy)",
  stripe_legacy: "Stripe",
  stripe_bc: "Stripe (second account)",
  arive: "Arive",
  meta_ads: "Meta Marketing",
};

export const providerName = (key) => PROVIDER_NAMES[key] || key;
