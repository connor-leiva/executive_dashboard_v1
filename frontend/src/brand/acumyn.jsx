/* Acumyn's own identity — the platform's brand, and the default every workspace starts from.
 *
 * THE MARK IS DRAWN, NOT LOADED. The identity guide specifies it as fully derivable geometry
 * ("so it can be rebuilt at any size without redrawing by eye"), so it is built from those
 * numbers rather than shipped as PNGs. That buys three things a file cannot: it is exact at
 * every size instead of at the sizes somebody happened to export, the small-size cut is a
 * parameter rather than a second asset, and it recolours to any approved treatment without a
 * per-colourway export. It also means the "Powered by Acumyn" footer costs no network request.
 *
 * Geometry, from the guide (§01), on a 64x64 artboard:
 *     blade radius 22u · blade weight 8u (12.5% of artboard) · blade sweep 92 degrees each
 *     gap axes at 90 / 210 / 330 degrees · pupil radius 6.5u · terminals butt, radial
 * Small-size cut (§02, at or below 24px): blade weight 10u, pupil 8u — drawn to survive
 * rasterisation, because below 24px the standard cut loses its gaps.
 *
 * The three gaps stay equal and the blade weight stays 12.5% — the guide calls both
 * non-negotiable, and deriving them means they cannot drift.
 */

const ART = 64;
const C = ART / 2;
const BLADE_RADIUS = 22;
const PUPIL_RADIUS = 6.5;
const SWEEP = 92;                       // degrees of arc per blade
const GAP_AXES = [90, 210, 330];        // the centre of each gap

const SMALL = { weight: 10, pupil: 8 }; // the small-size cut
const STANDARD = { weight: 8, pupil: PUPIL_RADIUS };

/* The five core colours (§06). Cadet is the brand and the single action colour; Ink carries
   text and dark surfaces; Sage and Haze are support and never carry body copy. */
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

const rad = (deg) => (deg * Math.PI) / 180;

/* The guide's angles are STANDARD MATH CONVENTION — Y up, counterclockwise from east. SVG is
   Y DOWN, so the sine is negated here and the arcs sweep counterclockwise (flag 0).
   Without the flip the whole mark mirrors: the gap axes still land 120 degrees apart and every
   measurement still checks out, but a BLADE ends up at top dead centre instead of a gap — which
   the guide lists under misuse ("Don't rotate. The gap sits at top dead centre."). It is the
   kind of error that survives arithmetic review and only shows up when somebody looks at it. */
const pt = (deg, r) => [C + r * Math.cos(rad(deg)), C - r * Math.sin(rad(deg))];

/** One blade as an SVG arc path. Butt terminals, so the path is the centreline and the
 *  stroke width gives the blade its weight. */
function bladePath(startDeg, sweepDeg) {
  const [x1, y1] = pt(startDeg, BLADE_RADIUS);
  const [x2, y2] = pt(startDeg + sweepDeg, BLADE_RADIUS);
  const large = sweepDeg > 180 ? 1 : 0;
  // sweep-flag 0: counterclockwise on screen, which is the positive direction once Y is flipped.
  return `M ${x1.toFixed(3)} ${y1.toFixed(3)} A ${BLADE_RADIUS} ${BLADE_RADIUS} 0 ${large} 0 ${x2.toFixed(3)} ${y2.toFixed(3)}`;
}

/** The three blades, derived from the gap axes so the gaps are equal by construction. */
export function bladePaths() {
  return GAP_AXES.map((axis) => bladePath(axis + halfGap(), SWEEP));
}

/** Half the angular width of one gap. Derived rather than written down, so the guide's
 *  "the three gaps stay equal" holds by construction: 3 blades of 92 leave 84 degrees, so each
 *  gap is 28 and a blade starts 14 past its gap axis. */
export function halfGap() {
  return (360 - SWEEP * GAP_AXES.length) / GAP_AXES.length / 2;
}

/** True when `deg` falls inside a gap. Exported because "the gap sits at top dead centre" is
 *  the one property of this mark that arithmetic cannot confirm and a person has to see —
 *  so it is worth being able to assert instead. inGap(90) must be true. */
export function inGap(deg) {
  const h = halfGap();
  const d = ((deg % 360) + 360) % 360;
  return GAP_AXES.some((axis) => {
    // shortest angular distance from the gap's centre, 0..180
    const delta = Math.abs(((d - axis + 540) % 360) - 180);
    return delta < h;
  });
}

/* The approved knockouts (§04). The two-tone mark is the default; a one-colour cut is for
   anywhere two values will not reproduce. In one-colour cuts the pupil takes the SAME value as
   the blades — never a hole, or the mark stops reading as an aperture. */
export const TREATMENT = {
  primary: { blades: CORE.cadet, pupil: CORE.ink },     // on white
  reversed: { blades: CORE.sage, pupil: "#FFFFFF" },    // on Ink
  knockout: { blades: "#FFFFFF", pupil: "#FFFFFF" },    // on Cadet, one value
  mono: { blades: CORE.ink, pupil: CORE.ink },          // print, engrave, fax
};

/**
 * The Acumyn mark. `size` picks the cut automatically — the guide switches at 24px, and
 * choosing it by hand is how the wrong one ends up in a favicon.
 */
export function AcumynMark({ size = 32, treatment = "primary", color, title, style }) {
  const cut = size <= 24 ? SMALL : STANDARD;
  const t = TREATMENT[treatment] || TREATMENT.primary;
  const blades = color || t.blades;
  const pupil = color || t.pupil;      // an explicit colour makes it a one-colour cut
  return (
    <svg width={size} height={size} viewBox={`0 0 ${ART} ${ART}`} style={style}
         role={title ? "img" : "presentation"} aria-hidden={title ? undefined : true}
         focusable="false">
      {title ? <title>{title}</title> : null}
      {bladePaths().map((d, i) => (
        <path key={i} d={d} fill="none" stroke={blades} strokeWidth={cut.weight}
              strokeLinecap="butt" />
      ))}
      <circle cx={C} cy={C} r={cut.pupil} fill={pupil} />
    </svg>
  );
}

/**
 * The horizontal lockup — the guide's default "in every context that has the width for it".
 * Gap is 0.30 x mark; the wordmark is Space Grotesk Bold at -0.02em with its cap height
 * optically aligned to the mark's outer diameter.
 */
export function AcumynLockup({ size = 24, treatment = "primary", color, style }) {
  const t = TREATMENT[treatment] || TREATMENT.primary;
  const wordColor = color || (treatment === "reversed" || treatment === "knockout"
    ? "#FFFFFF" : CORE.ink);
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: size * 0.30, ...style }}>
      <AcumynMark size={size} treatment={treatment} color={color} title="Acumyn" />
      <span style={{
        fontFamily: TYPE.display, fontWeight: 700, fontSize: size * 0.86,
        letterSpacing: "-0.02em", lineHeight: 1, color: wordColor,
      }}>Acumyn</span>
    </span>
  );
}
