import { T } from "./theme.js";

/* Spring brand assets. The delivered logo/logomark/icon files are black art on a
   transparent ground, so we render them as CSS masks over a colored box — one
   file tints to any brand color on any surface (no per-colorway exports). */

const mask = (url) => ({
  WebkitMaskImage: url, maskImage: url,
  WebkitMaskSize: "contain", maskSize: "contain",
  WebkitMaskRepeat: "no-repeat", maskRepeat: "no-repeat",
  WebkitMaskPosition: "center", maskPosition: "center",
});

/* The drawn signature. tone: "dark" = Evergreen ink (light surfaces),
   "light" = Parchment ink (dark surfaces). Or pass an explicit color. */
export function SpringSignature({ tone = "dark", height = 34, color, style }) {
  const c = color || (tone === "light" ? T.onDark : T.evergreen);
  // native aspect ratio 1200x419 ≈ 2.86
  return (
    <span role="img" aria-label="Spring" style={{
      display: "inline-block", height, width: height * (1200 / 419),
      backgroundColor: c, ...mask("url(/brand/logo/spring_logo.png)"), ...style,
    }} />
  );
}

/* The bare-"s" logomark (rail collapse, loading, avatar seed). */
export function SpringLogomark({ tone = "dark", size = 28, color, style }) {
  const c = color || (tone === "light" ? T.onDark : T.evergreen);
  return (
    <span aria-hidden style={{
      display: "inline-block", width: size, height: size,
      backgroundColor: c, ...mask("url(/brand/logo/spring_logomark.png)"), ...style,
    }} />
  );
}

/* Ribbed-gradient hero surface: the delivered gradient (light ground, colored
   ribs) placed over the business token color with multiply — white ground
   becomes the brand color, ribs become deeper texture. One ribbed surface per
   view (always the hero band). tone drives the text stack, not per-element. */
const HERO_BG = {
  evergreen: T.evergreen, meadow: T.meadow, poppy: T.poppy,
  mist: T.mist, parchment: T.parchment, petal: T.petal, daffodil: T.daffodilBg,
};
export function ribbedHero(gradient = "evergreen") {
  const g = gradient.toLowerCase();
  const file = g.charAt(0).toUpperCase() + g.slice(1);
  return {
    backgroundColor: HERO_BG[g] || T.evergreen,
    backgroundImage: `url(/brand/RibbedGradient_${file}.jpg)`,
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
