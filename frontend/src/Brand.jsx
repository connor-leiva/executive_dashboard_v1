import { T } from "./theme.js";

/* Brand assets. The delivered logo/logomark/icon files are black art on a
   transparent ground, so we render them as CSS masks over a colored box — one
   file tints to any brand color on any surface (no per-colorway exports). */

const mask = (url) => ({
  WebkitMaskImage: url, maskImage: url,
  WebkitMaskSize: "contain", maskSize: "contain",
  WebkitMaskRepeat: "no-repeat", maskRepeat: "no-repeat",
  WebkitMaskPosition: "center", maskPosition: "center",
});

/* ── the tenant's mark ─────────────────────────────────────────────────────────────────
   Read from a module-level holder rather than threaded through as props: the wordmark is
   rendered in eight places across five files, most of them deep in a tree that has no
   business knowing about identity. `setBrand` is called once, when /me answers.

   A tenant with NO logo configured does not inherit the previous one — it renders its own
   NAME as a wordmark. There is no per-tenant asset upload yet, and showing one customer's
   logo to another is worse than showing plain text. */
let _brand = { logo: null, logomark: null, display_name: "" };

export function setBrand(b) {
  _brand = { logo: null, logomark: null, display_name: "", ...(b || {}) };
}

export function getBrand() {
  return _brand;
}

/* The drawn signature, or the tenant's name set as one. tone: "dark" = Evergreen ink
   (light surfaces), "light" = Parchment ink (dark surfaces). Or pass an explicit color. */
export function BrandSignature({ tone = "dark", height = 34, color, style }) {
  const c = color || (tone === "light" ? T.onDark : T.evergreen);
  const { logo, display_name: name } = _brand;
  if (!logo) {
    // Wordmark fallback. Sized off `height` so it occupies roughly the same space as the
    // image would, and never wraps — these sit in headers and absolute-positioned corners.
    return (
      <span role="img" aria-label={name || "Home"} style={{
        display: "inline-block", color: c, whiteSpace: "nowrap",
        fontFamily: "Poppins,sans-serif", fontWeight: 700,
        fontSize: Math.round(height * 0.62), letterSpacing: "-.02em",
        lineHeight: 1, ...style,
      }}>{name}</span>
    );
  }
  // native aspect ratio 1200x419 ≈ 2.86
  return (
    <span role="img" aria-label={name || "Home"} style={{
      display: "inline-block", height, width: height * (1200 / 419),
      backgroundColor: c, ...mask(`url(${logo})`), ...style,
    }} />
  );
}

/* The bare logomark (rail collapse, loading, avatar seed). Falls back to the tenant's
   initial, which is what an avatar would show anyway. */
export function BrandLogomark({ tone = "dark", size = 28, color, style }) {
  const c = color || (tone === "light" ? T.onDark : T.evergreen);
  const { logomark, display_name: name } = _brand;
  if (!logomark) {
    return (
      <span aria-hidden style={{
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        width: size, height: size, color: c, fontFamily: "Poppins,sans-serif",
        fontWeight: 800, fontSize: Math.round(size * 0.7), lineHeight: 1, ...style,
      }}>{(name || "?").trim().charAt(0).toUpperCase()}</span>
    );
  }
  return (
    <span aria-hidden style={{
      display: "inline-block", width: size, height: size,
      backgroundColor: c, ...mask(`url(${logomark})`), ...style,
    }} />
  );
}

// Old names kept so the eight call sites need no churn beyond the import.
export const SpringSignature = BrandSignature;
export const SpringLogomark = BrandLogomark;

/* Ribbed-gradient hero surface. A NEUTRAL texture multiplied over a palette colour: the white
   ground leaves the colour untouched and the ribs deepen it. One ribbed surface per view, always
   the hero band.

   This used to be eight files, one per colour — RibbedGradient_Evergreen.jpg and friends. They
   are the same artwork tinted eight ways (measured: after normalising, they differ by at most
   5.3/255, which is JPEG noise), so the tint was doing nothing the multiply could not. Collapsing
   them to one greyscale master removed seven files AND removed the ceiling: a hero band now
   exists for any colour a workspace ever configures, including colours nobody has drawn yet.
   That is the difference between eight supported brands and all of them.

   On the texture's strength: the eight originals did not darken equally — a yellow tint darkens
   barely at all, an evergreen one a great deal — so no single master reproduces all eight
   exactly. The master is cut at 82%, which was the closest match across the range when compared
   against all eight, and which keeps the ribs visible on the dark grounds the hero actually uses
   rather than optimising for the light ones that outnumber them. */
const HERO_BG = {
  evergreen: T.evergreen, meadow: T.meadow, poppy: T.poppy,
  mist: T.mist, parchment: T.parchment, petal: T.petal, daffodil: T.daffodilBg,
};
export function ribbedHero(gradient = "evergreen") {
  return {
    backgroundColor: HERO_BG[String(gradient).toLowerCase()] || T.evergreen,
    backgroundImage: "url(/brand/RibbedGradient.jpg)",
    backgroundSize: "cover", backgroundPosition: "center",
    backgroundBlendMode: "multiply",
  };
}

/* Any brand icon, tinted via CSS mask. color follows the text context. */
export function Icon({ name, size = 18, color = "currentColor", style }) {
  return (
    <span aria-hidden style={{
      display: "inline-block", width: size, height: size, flexShrink: 0,
      backgroundColor: color, ...mask(`url(/brand/icons/${name}.png)`), ...style,
    }} />
  );
}
