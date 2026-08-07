/* Small shared scorecard UI, ported from the mockup (Card / Chip / Avatar / Bar / Dir /
   Eyebrow). All colors come from scorecardMath's palette so band hexes stay in one place. */
import { C, FD, FB, FM, band, sgn } from "./scorecardMath.js";

export function Eyebrow({ children, color = C.muted }) {
  return <div style={{ fontFamily: FM, fontSize: 9.5, letterSpacing: ".15em", textTransform: "uppercase", color }}>{children}</div>;
}

export function Card({ children, style, pad = 20 }) {
  return <div style={{ background: C.surface, border: `1px solid ${C.hair}`, borderRadius: 14, padding: pad,
    boxShadow: "0 1px 2px rgba(0,46,44,.04), 0 8px 24px -18px rgba(0,46,44,.35)", ...style }}>{children}</div>;
}

export function Chip({ children, ink, bg, dash }) {
  return <span style={{ fontFamily: FM, fontSize: 8.5, letterSpacing: ".08em", padding: "3px 6px", borderRadius: 4,
    background: bg || C.hairSoft, color: ink || C.muted, border: dash ? `1px dashed ${C.hair}` : "none", whiteSpace: "nowrap" }}>{children}</span>;
}

export function Avatar({ t }) {
  const unset = !t || t === "??";
  return <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 22, height: 22,
    borderRadius: 99, fontFamily: FM, fontSize: 9, background: unset ? C.daffodilBg : C.mist,
    color: unset ? C.amberInk : C.teal, border: unset ? `1px dashed ${C.daffodil}` : "none" }}>{t || "??"}</span>;
}

export function Bar({ pct, w }) {
  const b = band(pct), scale = Math.min(125, Math.max(0, pct ?? 0));
  return (
    <div style={{ position: "relative", width: w || "100%", height: 9, background: C.hairSoft, borderRadius: 99, overflow: "hidden", flex: w ? "0 0 auto" : "1 1 auto", minWidth: 44 }}>
      <div style={{ width: `${(scale / 125) * 100}%`, height: "100%", background: (b || {}).bar || C.hairSoft, borderRadius: 99 }} />
      <div style={{ position: "absolute", left: "80%", top: 0, width: 1, height: "100%", background: C.slate, opacity: .4 }} />
    </div>
  );
}

// Table cell/header styles shared by the grid header (Scorecard) and rows (ScorecardRow).
const cellB = `1px solid ${C.hairSoft}`;
export const tdL = { padding: "9px 8px", borderBottom: cellB, verticalAlign: "middle" };
export const tdC = { padding: "9px 5px", borderBottom: cellB, textAlign: "center", verticalAlign: "middle" };
export const cumCell = { padding: "9px 7px", borderBottom: cellB, verticalAlign: "middle" };
export const thL = { padding: "9px 8px", textAlign: "left" };
export const thC = { padding: "8px 4px", textAlign: "center", fontFamily: FM, fontSize: 9, fontWeight: 400 };
export const thCum = { padding: "8px 7px", textAlign: "left", fontFamily: FM, fontSize: 9, letterSpacing: ".07em",
  textTransform: "uppercase", color: C.slate, fontWeight: 400 };
export const groupTh = { padding: "8px", fontFamily: FM, fontSize: 9, letterSpacing: ".1em", textTransform: "uppercase",
  color: C.muted, fontWeight: 400, background: C.hairSoft, borderBottom: `1px solid ${C.hair}`, textAlign: "left" };

export function Dir({ d, showLabel }) {
  if (d === undefined || d === null) return <span style={{ fontFamily: FM, fontSize: 11, color: C.muted }}>–</span>;
  const flat = Math.abs(d) < 3, col = flat ? C.muted : d > 0 ? C.meadow : C.poppyInk;
  return (
    <span title={`Last 4 weeks vs the 4 before, ${sgn(d, 1)} points`}
      style={{ display: "inline-flex", alignItems: "center", gap: 3, fontFamily: FM, fontSize: 11, color: col, whiteSpace: "nowrap" }}>
      <span style={{ fontSize: 12, lineHeight: 1 }}>{flat ? "→" : d > 0 ? "↑" : "↓"}</span>
      {!flat && <span>{Math.abs(d).toFixed(0)}{showLabel ? "pt" : ""}</span>}
    </span>
  );
}
