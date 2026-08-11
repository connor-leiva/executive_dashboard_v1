/* ============================================================================
   L10 SCORECARD GRID — v2 design tokens + primitives (the approved mockup).

   Scoped to the L10 grid CARD only. The Move cards, Settings panel and the rest
   of the dashboard keep the shared theme (scorecardMath.js `band()` / theme.js).
   Band-color hexes normally live only in scorecardMath.js; this grid ships its
   own approved palette from the mockup, so its 3-band colors (good/warn/bad)
   live HERE and nowhere else — a documented, grid-scoped exception.
   ========================================================================== */

export const T = {
  forest: "#12291F",
  ink: "#15231C",
  inkSoft: "#3E4A43",
  muted: "#8B8578",
  faint: "#ABA396",
  line: "#E8E2D7",
  lineSoft: "#F1ECE3",
  paper: "#FFFDF9",
  rail: "#FCFAF5",
  shell: "#F4F0E8",
  good: "#4C6B4F",
  goodBg: "#E9F0E4",
  warn: "#B08420",
  warnBg: "#FAF1DA",
  bad: "#BC5138",
  badBg: "#FAE6E0",
};

export const FONT = `'DM Sans', ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`;
export const NUM = { fontVariantNumeric: "tabular-nums", fontFeatureSettings: '"tnum" 1' };
export const EASE = "cubic-bezier(.32,.72,0,1)";
export const DUR = 360;

/* geometry — every width is a border-box width; the .l10 reset absorbs borders */
export const W_IDX = 28;
export const W_NAME = 206;
export const W_OWN = 34;
export const W_GOAL = 56;
export const W_PIP = 14;
export const RAIL = W_IDX + W_NAME + W_OWN + W_GOAL + W_PIP; // 338

export const C_ACTUAL = 88;
export const C_PACE = 130;
export const C_TREND = 48;
export const C_GAP = 56;
export const C_REC = 122;
export const CUM = C_ACTUAL + C_PACE + C_TREND + C_GAP + C_REC; // 444

export const WK = 58;
export const ROW_H = 52;

export const LBL = { fontSize: 9.5, fontWeight: 600, letterSpacing: "0.085em", textTransform: "uppercase", color: T.faint };

export const TONE = {
  good: { fg: T.good, bg: T.goodBg },
  warn: { fg: T.warn, bg: T.warnBg },
  bad: { fg: T.bad, bg: T.badBg },
  none: { fg: T.faint, bg: "transparent" },
};

export const paceState = (p) => (p >= 100 ? "good" : p >= 85 ? "warn" : "bad");

/* three-band cell color from a value vs its (per-period) weekly goal. null ≠ zero; a goal of 0 is
   a track-only row, so it stays uncolored. Direction-aware (mirrors scorecard.py's attainment law):
   for lte (lower is better) a value at/under the cap reads good. Band law: >=100 good, >=80 warn. */
export function tone(v, goal, direction) {
  if (v === null || v === undefined || !goal) return TONE.none;
  const r = direction === "lte" ? (v ? goal / v : 2) : v / goal;
  return r >= 1 ? TONE.good : r >= 0.8 ? TONE.warn : TONE.bad;
}

/* verdict string (server-computed) → label + band kind for the status tag */
const VERDICT = {
  ahead: { label: "Ahead", kind: "good" },
  catchable: { label: "Catchable", kind: "good" },
  stretch: { label: "Stretch", kind: "warn" },
  reset: { label: "Reset", kind: "bad" },
};

/* ------------------------------- primitives ------------------------------- */

export function Chevron({ open, size = 11, color = T.muted }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 12 12"
      fill="none"
      aria-hidden="true"
      style={{ transform: `rotate(${open ? 90 : 0}deg)`, transition: `transform 260ms ${EASE}`, flexShrink: 0 }}
    >
      <path d="M4.5 2.5L8 6l-3.5 3.5" stroke={color} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Pip({ pct }) {
  if (pct === null || pct === undefined) return <div style={{ width: W_PIP, flexShrink: 0 }} />;
  const h = Math.max(6, Math.min(26, (pct / 100) * 22));
  return (
    <div style={{ width: W_PIP, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center" }} title={`${pct}% of pace`}>
      <div style={{ width: 3, height: h, borderRadius: 2, background: TONE[paceState(pct)].fg, opacity: 0.9, transition: `height 320ms ${EASE}` }} />
    </div>
  );
}

export function PaceBar({ pct }) {
  const kind = paceState(pct);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, width: C_PACE, flexShrink: 0, paddingRight: 14 }}>
      <div style={{ position: "relative", flex: 1, height: 6, borderRadius: 3, background: T.lineSoft, overflow: "hidden" }}>
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            bottom: 0,
            width: `${(Math.min(pct, 120) / 120) * 100}%`,
            borderRadius: 3,
            background: TONE[kind].fg,
            opacity: 0.85,
            transition: `width 420ms ${EASE}`,
          }}
        />
        <div style={{ position: "absolute", left: "83.333%", top: -2, bottom: -2, width: 1, background: T.faint, opacity: 0.7 }} />
      </div>
      <span style={{ ...NUM, fontSize: 13, fontWeight: 600, color: TONE[kind].fg, width: 38, flexShrink: 0, textAlign: "right", letterSpacing: "-0.01em" }}>
        {pct}%
      </span>
    </div>
  );
}

export function StatusTag({ verdict }) {
  const s = VERDICT[verdict];
  if (!s) return null;
  const t = TONE[s.kind];
  return (
    <span
      style={{
        fontSize: 8,
        fontWeight: 600,
        letterSpacing: "0.02em",
        textTransform: "uppercase",
        color: t.fg,
        background: s.kind === "good" ? t.bg : "transparent",
        border: `1px solid ${s.kind === "good" ? "transparent" : t.fg}`,
        borderRadius: 3,
        padding: "1px 4px",
        whiteSpace: "nowrap",
        opacity: s.kind === "good" ? 1 : 0.72,
      }}
    >
      {s.label}
    </span>
  );
}
