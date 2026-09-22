/* Axcion's own identity — the platform's brand, and the default every workspace starts from.
 *
 * THE MARK IS THE DESIGNED ARTWORK, LOADED. It used to be DRAWN: before there was a mark there
 * was an identity guide specifying derivable geometry, so this module built a stand-in from those
 * numbers — three arcs around a pupil, exact at every size and recolourable for free. The
 * designed mark arrived on 2026-09-22 and it is a different shape: two crossed tapered blades
 * with a detached leaf below the left arm, taller than it is wide. Nothing about it is derivable,
 * and deriving something adjacent would be redrawing somebody's logo by eye.
 *
 * So the artwork ships as files. `frontend/brand-src/axcion/` holds the delivery untouched and
 * `frontend/scripts/brand_assets.py` resizes it into `./axcion/`, which is what this module
 * imports. Imported rather than served from /public, because four of the five Vite entries set
 * publicDir:false and a runtime path into the dashboard's public directory 404s on the marketing
 * site and the operator console.
 *
 * What that costs, and it is worth stating plainly: a raster cannot be recoloured at runtime, so
 * `AxcionMark` no longer takes a `color` prop and every treatment is its own file. The delivery
 * is PNG — the brief asked for AI/EPS/SVG masters (§23) and they did not come with it. When they
 * do, this module and brand_assets.py are what change; nothing that calls them has to.
 */
import lockupInk from "./axcion/lockup-ink.png";
import lockupPrimary from "./axcion/lockup-primary.png";
import lockupReversed from "./axcion/lockup-reversed.png";
import lockupWhite from "./axcion/lockup-white.png";
import markCadet from "./axcion/mark-cadet.png";
import markInk from "./axcion/mark-ink.png";
import markPrimary from "./axcion/mark-primary.png";
import markReversed from "./axcion/mark-reversed.png";
import markWhite from "./axcion/mark-white.png";

/* Axcion's own site, for any link from inside a workspace to Axcion itself: "Powered by", the
   Privacy Policy and Terms. A constant rather than derived from the page's host, because a
   workspace may be served from its own domain, which says nothing about Axcion's. www rather than
   the apex: www serves the site, and the apex forwards to it at the registrar.


   THIS IS A LIVE URL, NOT A NAME. It is where the Privacy Policy and Terms links on every
   sign-in page actually go, and where "Powered by" points. The marketing site is served at
   whatever MARKETING_HOST names on the web service, so this must always name that same host.
   It was renamed ahead of that host once, during the rebrand, and pointed every workspace's
   legal links at the dashboard catch-all -- which answers 200, so nothing looked broken.
   MARKETING_HOST moved to www.axcion.io at the Phase 9 cutover and this moved with it.
   A test in test_tenancy.py ties this, PLATFORM_DOMAIN and the Caddy host defaults together
   so none of them can drift alone again. */
export const AXCION_SITE = "https://www.axcion.io";

/* The five core colours (§06). Cadet is the brand and the single action colour; Ink carries
   text and dark surfaces; Sage and Haze are support and never carry body copy.

   These are the artwork's colours, not an approximation of them: the delivered PNGs sample to
   #3F6B66, #16201F and #8FB3AE exactly, which is the brief's "inherits the established palette"
   honoured. If a future delivery moves them, the files are the authority and these follow. */
export const CORE = {
  cadet: "#3F6B66",
  ink: "#16201F",
  sage: "#8FB3AE",
  haze: "#BDD5D0",
  paper: "#EFF0EC",
};

/* Cadet ramp — 500 is the brand step. Below 500 is fills, rules and charts, never text. */
export const CADET = {
  50: "#EFF5F3", 100: "#DDEAE7", 200: "#BDD5D0", 300: "#8FB3AE", 400: "#618E88",
  500: "#3F6B66", 600: "#335954", 700: "#284543", 800: "#1D3331", 900: "#16201F",
};

/* Neutrals, olive-tuned to sit with the accent rather than fighting it. */
export const NEUTRAL = {
  50: "#F5F6F3", 100: "#E9EBE5", 200: "#D5D8D0", 300: "#B0B4AB", 400: "#868B82",
  500: "#5F645C", 600: "#474B45", 700: "#333730", 800: "#222521", 900: "#141614",
};

/* Type (§07): three families, one job each. Archivo is already what this app sets numbers in,
   which is the one part of the guide the product was following before it had the guide. */
export const TYPE = {
  display: '"Space Grotesk", "Helvetica Neue", Arial, sans-serif',
  text: '"Instrument Sans", "Helvetica Neue", Arial, sans-serif',
  data: 'Archivo, "Helvetica Neue", Arial, sans-serif',
};

/* THE MARK IS NOT SQUARE. 831x1024 in the delivery: the leaf hangs below and left of the X, and
   cropping to a square would either clip it or centre the X off-axis. Every caller sizes by
   HEIGHT and the width follows, which is why `size` means height throughout this module. */
export const MARK_ASPECT = 831 / 1024;

/* The horizontal lockup, measured from the delivered file rather than assumed: 2255x605 overall,
   with the mark spanning 541 of those 605 pixels. `size` keeps meaning the MARK's height at
   every call site, so the image is drawn slightly taller than `size` to put its mark at `size` —
   otherwise swapping a bare mark for a lockup would silently shrink the mark by 11%. */
const LOCKUP = { ratio: 2255 / 605, markFraction: 541 / 605 };

/* The approved knockouts (§04), each one its own artwork now rather than a pair of colours.
   `mono` is the one-ink cut for print, engraving and fax; `cadet` is the solid brand-colour cut
   for a light ground that already carries ink. */
export const TREATMENT = {
  primary: { mark: markPrimary, lockup: lockupPrimary },     // on white
  reversed: { mark: markReversed, lockup: lockupReversed },  // on Ink
  knockout: { mark: markWhite, lockup: lockupWhite },        // on Cadet, one value
  mono: { mark: markInk, lockup: lockupInk },                // print, engrave, fax
  cadet: { mark: markCadet, lockup: lockupPrimary },         // solid brand colour
};

/**
 * The Axcion mark. `size` is its HEIGHT in pixels; the width follows from the artwork.
 *
 * There is no `color` prop. The treatments are the approved cuts and each is a separate file —
 * tinting a raster would mean a CSS filter, which is how a two-tone mark becomes one muddy one.
 */
export function AxcionMark({ size = 32, treatment = "primary", title, style }) {
  const t = TREATMENT[treatment] || TREATMENT.primary;
  return (
    <img src={t.mark} alt={title || ""} aria-hidden={title ? undefined : true}
         style={{ display: "block", height: size, width: "auto",
                  // aspectRatio rather than a computed width: rounding a width AND a height to
                  // whole pixels independently stretches the artwork by up to a percent, which
                  // is invisible on inspection and wrong. It reserves the space before the file
                  // loads, too, so nothing shifts when it arrives.
                  aspectRatio: MARK_ASPECT, ...style }} />
  );
}

/**
 * The horizontal lockup — the guide's default "in every context that has the width for it", and
 * the designer's own icon-to-wordmark scale and clear space rather than this module's guess at
 * them. `size` is the height of the MARK inside it, so it is interchangeable with AxcionMark.
 */
export function AxcionLockup({ size = 24, treatment = "primary", style }) {
  const t = TREATMENT[treatment] || TREATMENT.primary;
  return (
    <img src={t.lockup} alt="Axcion"
         style={{ display: "block", height: size / LOCKUP.markFraction, width: "auto",
                  aspectRatio: LOCKUP.ratio, ...style }} />
  );
}
