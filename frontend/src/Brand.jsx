import { heroCss, heroStyle } from "./palette.js";
import { T } from "./theme.js";
import { fileUrl } from "./api.js";

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
  const next = { logo: null, logomark: null, display_name: "", ...(b || {}) };
  // An uploaded mark is stored server-relative; the API lives on another origin, so a bare path
  // would resolve against the app host and 404. A failed CSS mask shows NOTHING — no broken-image
  // icon, no console error — so this went unnoticed until somebody asked where their logo was.
  for (const k of ["logo", "logomark"]) {
    if (typeof next[k] === "string" && next[k].startsWith("/public/")) next[k] = fileUrl(next[k]);
  }
  _brand = next;
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
        fontFamily: "var(--font-display)", fontWeight: 700,
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
        width: size, height: size, color: c, fontFamily: "var(--font-display)",
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

/* THE HERO WATERMARK — one component, because the treatment being copy-pasted is exactly why it
 * ended up on two views out of fifteen.
 *
 * It was written inline twice (portfolio and financials) with the numbers spelled out each time,
 * so every view built afterwards either reproduced them from memory or skipped it. The Ads hero
 * did neither: it shipped an <img> pointing at one customer's LOGOMARK — the bare circle rather
 * than the wordmark — hardcoded, at a different opacity, tinted with a CSS invert filter.
 *
 * 12% is deliberately faint. This sits on top of a hero band carrying the largest number on the
 * screen, and a mark that competes with that number is worse than no mark. It is aria-hidden and
 * pointer-events:none for the same reason — it is texture, not content, and a screen reader
 * announcing the company name on every panel is noise.
 */
export function HeroMark({ tone = "light", height = 46, opacity = 0.12, top = 14, right = 26 }) {
  return (
    <BrandSignature tone={tone} height={height} aria-hidden style={{
      position: "absolute", top, right, opacity, pointerEvents: "none",
    }} />
  );
}

// Old names kept so the eight call sites need no churn beyond the import.
export const SpringSignature = BrandSignature;
export const SpringLogomark = BrandLogomark;

/* The hero band's surface -- one export, so eleven panels across six files cannot drift.
 *
 * The decision itself (a workspace's own plates vs Acumyn's bokeh) and the reasoning behind it
 * now live in palette.js, next to the colours, because it is resolved the same way they are:
 * written onto the document root when /me answers, then read by name wherever it is needed. This
 * is a pass-through so the call sites that already say `ribbedHero` need no churn.
 *
 * An earlier version collapsed one customer's eight ribbed files into a desaturated master and
 * made it the platform default. It was technically tidy -- the eight are one artwork, measurably
 * -- and it was the wrong thing to do: desaturating somebody's brand asset does not make it
 * yours. The eight are addressed as that workspace's configuration now, never as everybody's
 * default. */
export const ribbedHero = heroStyle;
export { heroCss };

/* Any brand icon, tinted via CSS mask. color follows the text context. */
export function Icon({ name, size = 18, color = "currentColor", style }) {
  return (
    <span aria-hidden style={{
      display: "inline-block", width: size, height: size, flexShrink: 0,
      backgroundColor: color, ...mask(`url(/brand/icons/${name}.png)`), ...style,
    }} />
  );
}
