import { loadTypeface, pairingFromStacks, stacks } from "./typefaces.js";

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

/* ── deriving thirty tokens from five ──────────────────────────────────────────────────────
 *
 * A workspace picks FIVE colours. The other twenty-five are computed, and that is a product
 * decision rather than a shortcut: thirty pickers is not more control, it is more ways to build
 * something illegible, and every one of them becomes a support conversation about why the text
 * disappeared. Five seeds also keep the palette internally consistent — a hover state that is
 * always the brand darkened by the same amount cannot drift away from the thing it is a hover of.
 *
 * WARNING IS NOT A SEED. The four semantic states were specified together at 6:1 or better so
 * they read as one family, and warning in particular is the one people reach for a bright yellow
 * for — the palette this replaced used #FFDD1F at roughly 1.4:1, which is a dot and not a word.
 * Positive and negative are seeds because a workspace has real opinions about them; warning stays
 * fixed because the opinion people have about it is usually wrong.
 */
const WHITE = "#FFFFFF";
const BLACK = "#000000";

function _rgb(hex) {
  const h = String(hex).replace("#", "");
  const full = h.length === 3 ? h.replace(/./g, "$&$&") : h;
  const n = parseInt(full, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function _hex(rgb) {
  return "#" + rgb.map((v) => Math.max(0, Math.min(255, Math.round(v)))
    .toString(16).padStart(2, "0")).join("").toUpperCase();
}

/** Mix `a` toward `b` by `t` (0 = a, 1 = b). */
export function mix(a, b, t) {
  const [r1, g1, b1] = _rgb(a);
  const [r2, g2, b2] = _rgb(b);
  return _hex([r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t]);
}

export const lighten = (hex, t) => mix(hex, WHITE, t);
export const darken = (hex, t) => mix(hex, BLACK, t);

/** Relative luminance, WCAG 2.1. */
export function luminance(hex) {
  const [r, g, b] = _rgb(hex).map((c) => {
    const v = c / 255;
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a, b) {
  const [l1, l2] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (l1 + 0.05) / (l2 + 0.05);
}

/** The five a workspace actually chooses. */
export const SEEDS = ["brand", "surface", "ink", "positive", "negative"];

export const SEED_META = {
  brand: { label: "Brand", hint: "Buttons, links and the one thing on a screen you want clicked." },
  surface: { label: "Surface", hint: "The page behind everything. Keep it light." },
  ink: { label: "Ink", hint: "Text and dark panels. Needs to be dark enough to read." },
  positive: { label: "Positive", hint: "On pace, healthy, money in." },
  negative: { label: "Negative", hint: "Behind, at risk, money out." },
};

/**
 * The five seeds that best describe an EXISTING thirty-token palette.
 *
 * A workspace that predates the picker has thirty hand-chosen colours, not five. Opening the
 * settings panel on Acumyn's defaults rather than theirs is how a save turns into a redesign
 * nobody asked for — which is exactly what happened to the first workspace: the panel offered
 * Acumyn's colours, Save wrote them, and thirty hand-built tokens were replaced by Cadet.
 *
 * These five are the slots the derivation reads BACK from, so re-deriving reproduces the palette
 * closely — not identically, because a hand-built palette can say things five seeds cannot, which
 * is why the explicit palette is kept until somebody deliberately saves over it.
 */
export function seedsFromPalette(palette) {
  const p = palette || {};
  const base = seedsFromAcumyn();
  return {
    brand: p.poppy || base.brand,
    surface: p.page || p.parchment || base.surface,
    ink: p.ink || p.evergreen || base.ink,
    positive: p.meadow || base.positive,
    negative: p.poppyText || p.gapText || base.negative,
  };
}

export function seedsFromAcumyn() {
  return { brand: CADET[500], surface: NEUTRAL[50], ink: CORE.ink,
           positive: SEMANTIC.success, negative: SEMANTIC.error };
}

/**
 * Thirty tokens from five. Every relationship here is a ratio rather than a fixed colour, so a
 * workspace's palette holds together the same way Acumyn's does.
 */
export function derive(seeds) {
  const s = { ...seedsFromAcumyn(), ...(seeds || {}) };
  const { brand, surface, ink, positive, negative } = s;
  return {
    // structure and text, all off ink
    evergreen: ink,
    ink,
    secondary: lighten(ink, 0.14),
    tertiary: lighten(ink, 0.26),
    slate: lighten(ink, 0.14),
    // 0.36 rather than something lighter, and this is a deliberate half-step away from the
    // identity guide. `muted` carries 11px uppercase labels and captions — NORMAL text by WCAG,
    // which wants 4.5:1. The guide's own label neutral scores 3.21:1, fine for large text and
    // short for the size it is actually set at. Deriving to 5.0:1 costs a little hierarchy
    // against `secondary` and buys labels that people can read.
    muted: mix(ink, surface, 0.36),
    onDark: lighten(surface, 0.55),
    onDarkMute: mix(lighten(surface, 0.55), ink, 0.42),

    // ground
    parchment: surface,
    page: surface,
    line: darken(surface, 0.09),
    white: WHITE,

    // the action colour and its family
    poppy: brand,
    poppyActive: darken(brand, 0.14),
    petal: lighten(brand, 0.55),
    petalDeep: darken(brand, 0.16),
    mist: lighten(brand, 0.84),
    edge: darken(brand, 0.24),
    teal: darken(brand, 0.20),          // links and focus rings: needs to hold as text

    // positive
    meadow: positive,
    meadowInk: darken(positive, 0.08),
    meadowBg: lighten(positive, 0.88),
    sprout: lighten(positive, 0.62),

    // negative
    poppyText: negative,
    gapText: negative,

    // warning stays Acumyn's, deliberately — see the note above
    daffodil: SEMANTIC.warning,
    daffodilText: SEMANTIC.warning,
    amber: SEMANTIC.warning,
    daffodilBg: tint(SEMANTIC.warning),
    amberBg: tint(SEMANTIC.warning),
  };
}

/* The pairings that decide whether the result is READABLE. Each is something the product actually
   renders, not a theoretical combination — checking pairs nobody draws produces warnings nobody
   can act on. AA is 4.5:1 for body text and 3:1 for large text and UI edges. */
const CHECKS = [
  { a: "ink", on: "page", min: 4.5, what: "Body text on the page" },
  { a: "secondary", on: "page", min: 4.5, what: "Secondary text on the page" },
  { a: "muted", on: "page", min: 4.5, what: "Labels and captions on the page" },
  { a: "white", on: "poppy", min: 4.5, what: "Button text on a brand button" },
  { a: "teal", on: "page", min: 4.5, what: "Links on the page" },
  { a: "meadowInk", on: "page", min: 4.5, what: "Positive figures on the page" },
  { a: "poppyText", on: "page", min: 4.5, what: "Negative figures on the page" },
  { a: "onDark", on: "evergreen", min: 4.5, what: "Text on a dark panel" },
  { a: "onDarkMute", on: "evergreen", min: 3.0, what: "Muted text on a dark panel" },
];

/**
 * What is unreadable about a proposed palette. Empty means it is fine.
 *
 * Returned rather than thrown so the UI can show every problem at once: a picker that reports one
 * failure, gets fixed, then reports the next is a picker people give up on.
 */
export function contrastProblems(seeds) {
  const t = derive(seeds);
  return CHECKS
    .map((c) => ({ ...c, ratio: contrast(t[c.a], t[c.on]) }))
    .filter((c) => c.ratio < c.min)
    .map((c) => ({ what: c.what, ratio: Math.round(c.ratio * 100) / 100, min: c.min }));
}

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

/**
 * Apply a workspace's colours, whichever shape they are stored in.
 *
 * TWO SHAPES, and the order matters. An explicit `palette` is thirty tokens somebody chose one at
 * a time — the first workspace has exactly that, pinned by migration 0049 so its dashboard did
 * not move when the palette stopped being compiled in. `seeds` is the five-colour choice everyone
 * makes now, derived at render time.
 *
 * Explicit wins, because it can express things the derivation cannot, and demoting a hand-built
 * palette to an approximation of itself would be a visible change nobody asked for.
 */
export function applyBrand(brand, el) {
  const b = brand || {};
  // THE SINGLE SOURCE FOR TYPE. Callers used to follow this with applyType(brand.type), and the
  // two disagreed: this resolved a pairing and fetched its fonts, then that overwrote the
  // variables with a legacy stack whose fonts nobody had requested. The result named Poppins and
  // rendered in a generic sans.
  const face = b.typeface || pairingFromStacks(b.type) || undefined;
  applyType(stacks(face), el);
  loadTypeface(face);
  if (b.palette && Object.keys(b.palette).length) {
    applyPalette(b.palette, el);
    return "explicit";
  }
  if (b.seeds && Object.keys(b.seeds).length) {
    // Derived rather than stored, so a workspace that chose five colours a year ago still gets
    // today's ramps instead of a frozen copy of the ones that existed when they saved.
    applyPalette(derive(b.seeds), el);
    return "derived";
  }
  applyPalette(ACUMYN, el);              // the platform's own identity
  return "platform";
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
applyType(stacks(undefined));
loadTypeface(undefined);          // the platform pairing, requested at module load
