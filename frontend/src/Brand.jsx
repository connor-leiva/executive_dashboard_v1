import { T, alpha } from "./theme.js";

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

/* The hero band's surface.
 *
 * TWO ROUTES, and which one runs is a question about whose artwork it is.
 *
 * A workspace that supplies `hero_plates` — a map of ground colour to image — gets its own,
 * multiplied over the ground the way its designer intended. Spring's eight ribbed gradients are
 * exactly that, and they are HERS: delivered as part of her visual identity system.
 *
 * Everything else gets Acumyn's bokeh. That is a real Acumyn asset — soft aperture-shaped blurs
 * echoing the mark's own blades — laid under a wash of the ground colour, which is the technique
 * from Acumyn's own site. The plate carries texture, the wash carries the brand, so it works for
 * any colour a workspace ever configures.
 *
 * An earlier version of this collapsed Spring's eight files into one desaturated master and made
 * it the platform default. It was technically tidy — the eight are one artwork, measurably — and
 * it was the wrong thing to do: desaturating somebody's brand asset does not make it yours, and
 * Acumyn shipping a derivative of a customer's identity as its own look is not a licensing
 * question so much as a taste one. The eight files are back where they belong, addressed as
 * Spring's configuration rather than as everybody's default. */
const HERO_BG = {
  evergreen: T.evergreen, meadow: T.meadow, poppy: T.poppy,
  mist: T.mist, parchment: T.parchment, petal: T.petal, daffodil: T.daffodilBg,
};

// Which plate reads better under the wash. Keyed on the SLOT rather than the colour, because
// the colour is a CSS variable by the time it gets here and JavaScript cannot measure it.
const LIGHT_GROUNDS = new Set(["mist", "parchment", "petal", "daffodil", "sprout"]);

export function ribbedHero(gradient = "evergreen") {
  const g = String(gradient).toLowerCase();
  const ground = HERO_BG[g] || T.evergreen;
  const plates = _brand.hero_plates || null;
  if (plates && plates[g]) {
    return {
      backgroundColor: ground, backgroundImage: `url(${plates[g]})`,
      backgroundSize: "cover", backgroundPosition: "center",
      backgroundBlendMode: "multiply",
    };
  }
  const plate = LIGHT_GROUNDS.has(g) ? "bokeh-light" : "bokeh-ink";
  return {
    backgroundColor: ground,
    // Wash first, plate under it: the ground stays the workspace's colour and the bokeh reads as
    // texture through it. alpha() resolves the CSS variable to its rgb triple.
    backgroundImage: `linear-gradient(${alpha(ground, 0.78)}, ${alpha(ground, 0.88)}), `
                     + `url(/brand/acumyn/${plate}.jpg)`,
    backgroundSize: "cover", backgroundPosition: "center",
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
