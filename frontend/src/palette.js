/* The palette, as runtime data rather than compiled-in constants.
 *
 * Every colour in this app is an inline style reading `T` from theme.js — 2,482 references
 * across 28 files, with no stylesheet anywhere. That made the palette a build-time fact: one
 * customer's brand, baked into the bundle everybody downloads.
 *
 * The fix is not to touch those 2,482 references. It is to change what `T`'s VALUES ARE. Each
 * token becomes `var(--t-name)`, the variables are declared here with Acumyn's identity as the
 * default, and a workspace overrides whichever of them it cares about at sign-in. Every existing
 * `T.ink` keeps working, unchanged, and the colour moves from build time to runtime.
 *
 * WHY EACH TOKEN IS ALSO EMITTED AS AN -rgb TRIPLE: 73 call sites do `alpha(T.poppy, 0.4)`, and
 * alpha() parses hex. `var(--t-poppy)` cannot be parsed by JavaScript — the browser resolves it,
 * not us. So each token ships twice, and alpha() rewrites the variable name rather than reading
 * a colour it cannot see.
 *
 * The defaults below are Acumyn's brand identity guide v1.0: five core colours, the Cadet and
 * neutral ramps, and the four semantic states. Where the guide names a UI token directly (§10 —
 * primary action Cadet 500, body text Ink 700, canvas Ink 50, borders Ink 100/200) that mapping
 * is used verbatim rather than reinterpreted.
 */

// ── Acumyn core (§06) ────────────────────────────────────────────────────────────────────
export const CORE = { cadet: "#3F6B66", ink: "#16201F", sage: "#8FB3AE",
                      haze: "#BDD5D0", paper: "#EFF0EC" };

export const CADET = { 50: "#EFF5F3", 100: "#DDEAE7", 200: "#BDD5D0", 300: "#8FB3AE",
                       400: "#618E88", 500: "#3F6B66", 600: "#335954", 700: "#284543",
                       800: "#1D3331", 900: "#16201F" };

export const NEUTRAL = { 50: "#F5F6F3", 100: "#E9EBE5", 200: "#D5D8D0", 300: "#B0B4AB",
                         400: "#868B82", 500: "#5F645C", 600: "#474B45", 700: "#333730",
                         800: "#222521", 900: "#141614" };

/* The four states, every one desaturated to Cadet's own level so they read as one family —
   success pushed deep and blue-green so it never twins the accent. All four clear 6:1 against
   white, which is what makes them usable as TEXT and not just as fills; the palette they
   replaced had a warning colour (#FFDD1F) at roughly 1.4:1, legible only as a dot. */
export const SEMANTIC = {
  success: "#1E5C48",   // 7.84:1
  warning: "#7E5A1C",   // 6.24:1
  error:   "#93413A",   // 6.87:1
  info:    "#3D6178",   // 6.60:1
};

/** Mix a hex toward white. The state chips need a pale ground and the guide does not specify
 *  one, so it is derived from the state itself — that way a workspace overriding `error` gets a
 *  matching wash for free instead of a stale tint from somebody else's palette. */
export function tint(hex, amount = 0.9) {
  const n = parseInt(String(hex).replace("#", ""), 16);
  const mix = (c) => Math.round(c + (255 - c) * amount);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  return "#" + [mix(r), mix(g), mix(b)].map((v) => v.toString(16).padStart(2, "0")).join("");
}

/* The 30 slots the app actually uses, mapped onto Acumyn. The slot NAMES are the ones the
   components already say (`evergreen`, `poppy`, `daffodil`) — renaming 2,482 references to suit
   a new palette would be a far bigger and riskier change than repointing them, and the names
   are only strings. What they MEAN is in the comments. */
export const ACUMYN = {
  evergreen: CORE.ink,          // darkest structural surface + primary button ground
  ink: CORE.ink,                // text 1 — headings
  secondary: NEUTRAL[700],      // text 2 — body (guide §10: "body text Ink 700")
  tertiary: NEUTRAL[600],       // text 3
  slate: NEUTRAL[700],          // secondary text
  muted: NEUTRAL[400],          // text 4 — uppercase labels, captions
  meadow: SEMANTIC.success,     // positive accent
  meadowInk: SEMANTIC.success,  // positive text
  meadowBg: tint(SEMANTIC.success),
  sprout: NEUTRAL[300],         // inert / disabled
  parchment: NEUTRAL[50],       // page surface (guide §10: "app canvas Ink 50")
  page: NEUTRAL[50],
  line: NEUTRAL[200],           // hairline (guide §10: "borders Ink 100 / 200")
  white: "#FFFFFF",
  petal: CADET[200],            // soft accent fill
  petalDeep: CADET[400],
  poppy: CADET[500],            // THE action colour — one primary action per screen (§06)
  poppyActive: CADET[600],      // its hover
  poppyText: SEMANTIC.error,    // danger text
  gapText: SEMANTIC.error,      // a negative number that needs recovering
  mist: CADET[100],             // soft surface
  teal: SEMANTIC.info,          // links, focus rings
  edge: CADET[400],             // programme identity — a workspace overrides this per programme
  daffodil: SEMANTIC.warning,   // attention
  daffodilBg: tint(SEMANTIC.warning),
  daffodilText: SEMANTIC.warning,
  amber: SEMANTIC.warning,      // alias of daffodilText, kept because components say both
  amberBg: tint(SEMANTIC.warning),
  onDark: CORE.paper,           // text on a dark surface
  onDarkMute: CORE.sage,        // §06: sage is the on-dark support value
};

/* Typography (§07). Three families, one job each. Variables for the same reason the colours
   are: the app names a font 868 times, and a workspace's type is part of its identity. */
export const ACUMYN_TYPE = {
  display: '"Space Grotesk","Helvetica Neue",Arial,sans-serif',
  text: '"Instrument Sans","Helvetica Neue",Arial,sans-serif',
  data: 'Archivo,"Helvetica Neue",Arial,sans-serif',
};

const VAR = (name) => `--t-${name.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase())}`;

/** `T` reads this: the token's value as a CSS variable reference, not a colour. */
export function ref(name) {
  return `var(${VAR(name)})`;
}

function rgbTriple(hex) {
  const h = String(hex).replace("#", "");
  const full = h.length === 3 ? h.replace(/./g, "$&$&") : h;
  const n = parseInt(full, 16);
  return `${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}`;
}

/** Write tokens onto the document root. Each lands twice — the colour, and its rgb triple for
 *  alpha(). Called once with the defaults at load, then again with a workspace's overrides. */
export function applyPalette(tokens, el) {
  const root = el || (typeof document !== "undefined" && document.documentElement);
  if (!root) return;
  for (const [name, value] of Object.entries(tokens || {})) {
    if (typeof value !== "string" || !value.startsWith("#")) continue;
    root.style.setProperty(VAR(name), value);
    root.style.setProperty(`${VAR(name)}-rgb`, rgbTriple(value));
  }
}

export function applyType(fonts, el) {
  const root = el || (typeof document !== "undefined" && document.documentElement);
  if (!root || !fonts) return;
  for (const slot of ["display", "text", "data"]) {
    if (fonts[slot]) root.style.setProperty(`--font-${slot}`, fonts[slot]);
  }
}

/* Applied at module load, which is before React's first paint — so the defaults are present
   from the first frame and a workspace override later only ever CHANGES a value, never
   introduces one. A missing variable would resolve to nothing and paint the page unstyled. */
applyPalette(ACUMYN);
applyType(ACUMYN_TYPE);
