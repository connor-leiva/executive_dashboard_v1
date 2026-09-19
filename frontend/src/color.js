/* COLOUR ARITHMETIC, AND NOTHING ELSE.
 *
 * These lived in palette.js, which also applies the dashboard's identity AT MODULE LOAD -- its
 * palette, and its Space Grotesk / Instrument Sans type -- onto the page root. The portal needed
 * four of these functions to derive its own shades, imported them from palette.js, and so every
 * portal page was repainted in the DASHBOARD's fonts: the mockup's DM Sans and Playfair were
 * declared in ui.css and overridden by an inline style the portal never asked for. A module the
 * portal imports must not do anything when imported; this one does not. */

export const WHITE = "#FFFFFF";
export const BLACK = "#000000";

export function rgb(hex) {
  const h = String(hex).replace("#", "");
  const full = h.length === 3 ? h.replace(/./g, "$&$&") : h;
  const n = parseInt(full, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function hex(channels) {
  return "#" + channels.map((v) => Math.max(0, Math.min(255, Math.round(v)))
    .toString(16).padStart(2, "0")).join("").toUpperCase();
}

/** Mix `a` toward `b` by `t` (0 = a, 1 = b). */
export function mix(a, b, t) {
  const [r1, g1, b1] = rgb(a);
  const [r2, g2, b2] = rgb(b);
  return hex([r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t]);
}

export const lighten = (value, t) => mix(value, WHITE, t);
export const darken = (value, t) => mix(value, BLACK, t);

/** Relative luminance, WCAG 2.1. */
export function luminance(value) {
  const [r, g, b] = rgb(value).map((c) => {
    const v = c / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a, b) {
  const [l1, l2] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (l1 + 0.05) / (l2 + 0.05);
}
