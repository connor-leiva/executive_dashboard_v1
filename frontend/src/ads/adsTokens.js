/* The ONLY file under frontend/src/ads/ permitted a hex value. SPEC-ads-module.md Part 13.2.
 *
 * The approved mockup is dark neon - indigo, pink and lime on near-black. Acumyn is parchment
 * and evergreen. This is that translation, done once, so no component under ads/ ever reaches
 * for a colour of its own. The acceptance check is a grep, and it is a real rule rather than a
 * tidiness preference: a colour inlined in a component is a colour that survives a rebrand.
 *
 *   grep -rn "#[0-9a-fA-F]\{3,6\}" frontend/src/ads --include=*.jsx | grep -v adsTokens.js
 *
 * Everything here is derived from theme.js rather than re-picked, so the ads tab moves when the
 * product's palette moves. When per-workspace theming lands these become var() references and
 * this file is the only thing that changes.
 */
import { T, alpha } from "../theme.js";

export const C = {
  // Surfaces
  bg: T.parchment,
  surface: T.white,
  surface2: T.parchment,
  line: T.line,
  hair: alpha(T.ink, 0.06),

  // Type
  ink: T.ink,
  slate: T.slate,
  muted: T.muted,
  onDark: T.onDark,
  onDarkMute: T.onDarkMute,

  // Accent. The mockup's indigo becomes teal; its pink is retired rather than remapped, because
  // two accents competing for attention is what made the original hard to scan.
  accent: T.teal,
  accentBg: T.mist,

  // Bands. Weight and hue together - the funnel has to stay readable when a screenshot of it
  // ends up in a slide deck in greyscale.
  goodInk: T.meadowInk,
  goodBg: T.meadowBg,
  goodBar: T.meadow,
  warnInk: T.daffodilText,
  warnBg: T.daffodilBg,
  warnBar: T.daffodil,
  badInk: T.poppyText,
  badBg: alpha(T.poppy, 0.10),
  badBar: T.poppy,

  // The Meta / Acumyn crossing in the funnel. Deliberately NOT a brand colour: the point of the
  // divide is that the rungs above it are somebody else's measurement.
  metaZone: alpha(T.slate, 0.05),
  acumynZone: alpha(T.meadow, 0.06),

  // ── The approved mockup's vocabulary (ads-meta-performance, Appendix B) ──────────────
  // Added under the mockup's OWN names rather than remapped onto the ones above, so a rule
  // ported from it reads the same in both files and nobody has to hold a translation table in
  // their head while checking whether the port is faithful.
  page: T.page,
  parchment: T.parchment,
  // The mockup calls the accent `teal`. Both names point at the same token rather than one
  // being rewritten to the other, because the CSS was ported verbatim and reads `C.teal`, while
  // every component written here reads `C.accent`. A missing alias is not a subtle failure: it
  // renders `undefined` into the stylesheet and throws out of the icon tinting.
  teal: T.teal,
  hairDeep: alpha(T.ink, 0.14),
  body: T.body,
  meadow: T.meadow,
  sprout: T.sprout,
  mist: T.mist,
  mistDeep: alpha(T.teal, 0.55),
  evergreen: T.evergreen,
  poppyDeep: T.poppyActive,
  flagBg: T.flagBg,
  flagDot: T.daffodil,
  flagText: T.daffodilText,
};

/* The brand assets the mockup inlines as base64. They already ship in public/brand, so the port
   references the files rather than carrying eighty kilobytes of duplicated image data in a
   source file - and a rebrand then reaches this tab by replacing an asset, not by editing JSX. */
export const ASSET = {
  ever: "/brand/RibbedGradient.jpg",   // the dark band behind the hero
  sig: "/brand/logo/spring_logomark.png",
  chev: "/brand/icons/chevron_down.png",
  open: "/brand/icons/open.png",
  warn: "/brand/icons/info.png",
  meg: "/brand/icons/notification.png",
  img: "/brand/icons/puzzle.png",
};

/* Every band decision is made by the SERVER (spec Part 10.3); the client only colours what it
 * is handed. `none` is its own state and must never render as `bad` - a missing number and a
 * catastrophic number are different facts, and painting them the same red teaches people to
 * stop reading the colours. */
export const BAND = {
  good: { ink: C.goodInk, bg: C.goodBg, bar: C.goodBar, label: "good" },
  warn: { ink: C.warnInk, bg: C.warnBg, bar: C.warnBar, label: "watch" },
  bad: { ink: C.badInk, bg: C.badBg, bar: C.badBar, label: "high" },
  none: { ink: C.muted, bg: C.surface2, bar: C.line, label: "—" },
};

export const band = (s) => BAND[s] || BAND.none;

/* Formatters. `null` is rendered as an em dash everywhere, never as 0 - the API returns null
 * from rate() on a zero denominator precisely so this distinction survives to the pixel. */
export const usd = (n, d = 0) =>
  n === null || n === undefined
    ? "—"
    : "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

export const num = (n) =>
  n === null || n === undefined ? "—" : Number(n).toLocaleString("en-US");

export const compact = (n) => {
  if (n === null || n === undefined) return "—";
  const v = Number(n);
  if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(2) + "M";
  if (Math.abs(v) >= 1e3) return (v / 1e3).toFixed(1) + "K";
  return String(Math.round(v));
};

export const pct = (n, d = 2) => (n === null || n === undefined ? "—" : Number(n).toFixed(d) + "%");
export const mult = (n) => (n === null || n === undefined ? "—" : Number(n).toFixed(1) + "x");

export const FONT = "Inter,sans-serif";
export const HEAD = "Poppins,sans-serif";
/* Archivo has tabular figures, which is the whole reason it is here. A column of mixed
 * $47,382 / 9.4x / 1.02% that does not line up is unreadable, and the Books ledger already
 * made this call for the same reason. */
export const FIG = "Archivo,Inter,sans-serif";
