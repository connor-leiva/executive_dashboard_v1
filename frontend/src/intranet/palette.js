/* The workspace's own colours, applied to the portal at runtime.
 *
 * The console has authored `IntranetWorkspace.palette` since it shipped and nothing read it, so
 * every workspace's portal wore the same five colours no matter what their admin picked. This is
 * the other half of the brand item: the logos now come through, and so does the palette.
 *
 * ONLY FOUR OF THE FIVE SWATCHES LAND. `gold` is offered by the console and appears nowhere in
 * the portal's stylesheet — there is no gold anything in this design. It is left unapplied rather
 * than assigned somewhere it does not belong; giving a swatch a home it was never drawn for is
 * how a workspace ends up with a gold border it never asked for.
 */
import { darken, lighten, luminance } from "../palette.js";

/** Swatch -> the one variable it maps to directly. */
const DIRECT = {
  ink: "--ink",
  brand: "--primary",
  accent: "--accent",
  canvas: "--page",
};

/* The rail is ink, and everything on it is a shade of ink. Setting --ink alone would leave the
   rail's hover, active and divider colours at the default's shades, so a workspace that changed
   its ink got a rail that disagreed with itself. The offsets reproduce the shipped values when
   applied to the default ink. */
const RAIL_SHADES = [
  ["--rail", 0],
  ["--rail-hover", 0.035],
  ["--rail-active", 0.055],
  ["--rail-line", 0.075],
  ["--dark-line", 0.10],
];

/* Text ON the rail, as a distance from the rail rather than a fixed grey. A workspace that picks
   a LIGHT ink gets a light rail, and light-grey nav labels on it would be unreadable — so the
   direction is chosen from the rail's own luminance instead of assumed. */
const RAIL_TEXT = [
  ["--rail-text", 0.63],
  ["--rail-dim", 0.45],
  ["--rail-label", 0.32],
];

const MANAGED = [
  ...Object.values(DIRECT),
  ...RAIL_SHADES.map(([name]) => name),
  ...RAIL_TEXT.map(([name]) => name),
];

export function applyPortalPalette(palette) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;

  // Cleared first, every time. Without this, un-setting a swatch in the console would leave the
  // previous value painted on until a hard reload — the variable would still be set inline, and
  // an inline custom property beats the stylesheet that holds the default.
  MANAGED.forEach((name) => root.style.removeProperty(name));

  const p = palette || {};
  Object.entries(DIRECT).forEach(([swatch, name]) => {
    if (p[swatch]) root.style.setProperty(name, p[swatch]);
  });

  if (!p.ink) return;
  const dark = luminance(p.ink) < 0.4;
  RAIL_SHADES.forEach(([name, t]) => {
    root.style.setProperty(name, t === 0 ? p.ink : (dark ? lighten(p.ink, t) : darken(p.ink, t)));
  });
  RAIL_TEXT.forEach(([name, t]) => {
    root.style.setProperty(name, dark ? lighten(p.ink, t) : darken(p.ink, t));
  });
}
