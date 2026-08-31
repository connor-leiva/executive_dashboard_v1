/* Who is behind a number. SPEC-ads-module.md Part 15.
 *
 * Renders the same `{title, subtitle, count, columns, rows, note}` envelope the launch and
 * sales-desk drills return. The envelope is shared on purpose and the STYLING is not: the launch
 * drill's table lives under a `.bcl` scope, and borrowing another module's stylesheet to get a
 * table would make this tab depend on where that one keeps its CSS.
 *
 * The panel never recounts. It shows what the API returned, so the list and the figure it was
 * opened from cannot disagree - there is a test asserting exactly that, because a drill that
 * quietly answers a different question than the number above it is worse than no drill.
 */
import { useEffect, useRef } from "react";

import { C, FIG, FONT, HEAD } from "./adsTokens.js";

const HEADINGS = {
  name: "Name", email: "Email", campaign: "Campaign", match: "Matched by",
  first_seen: "First seen", reached: "Reached", value: "Contracted", url: "CRM",
};

function Cell({ col, row }) {
  const v = row[col];
  if (col === "url") {
    return v ? <a href={v} target="_blank" rel="noreferrer"
                  style={{ color: C.accent }}>GHL ↗</a> : "—";
  }
  if (col === "value") {
    return <span style={{ fontFamily: FIG, fontVariantNumeric: "tabular-nums" }}>
      {v && v !== "—" ? `$${v}` : "—"}
    </span>;
  }
  if (col === "first_seen" || col === "reached") {
    return <span style={{ fontFamily: FIG, fontVariantNumeric: "tabular-nums",
                          color: v === "undated" ? C.muted : C.ink }}>{v || "—"}</span>;
  }
  return v === null || v === undefined || v === "" ? "—" : String(v);
}

export default function DrillPanel({ drill, loading, error, label, onClose }) {
  const ref = useRef(null);

  /* Escape closes, and focus moves in on open. Without the focus move a keyboard user tabs from
     wherever they were on the page rather than into the thing that just appeared. */
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    ref.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const cols = drill?.columns || [];

  return (
    <div ref={ref} tabIndex={-1} role="region"
         aria-label={`Who is behind ${drill?.title || label || "this number"}`}
         style={{ background: C.surface, border: `1px solid ${C.accent}`, borderRadius: 14,
                  padding: 16, marginTop: 12, outline: "none" }}>
      <style>{`
        .adr-wrap { overflow-x: auto; }
        .adr-tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
        .adr-tbl th { text-align: left; font-family: ${FONT}; font-size: 10.5px;
                      font-weight: 600; letter-spacing: .06em; text-transform: uppercase;
                      color: ${C.muted}; padding: 6px 10px 6px 0; border-bottom: 1px solid ${C.line}; }
        .adr-tbl td { font-family: ${FONT}; color: ${C.ink}; padding: 7px 10px 7px 0;
                      border-bottom: 1px solid ${C.line}; vertical-align: top; }
        .adr-tbl tr:last-child td { border-bottom: none; }
      `}</style>

      <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <span style={{ fontFamily: HEAD, fontSize: 14, fontWeight: 600, color: C.ink }}>
          {drill?.title || label}
        </span>
        <span style={{ flex: 1, fontFamily: FONT, fontSize: 11.5, color: C.slate }}>
          {drill?.subtitle}
        </span>
        <button type="button" onClick={onClose}
                style={{ fontFamily: FONT, fontSize: 12, color: C.slate, background: "none",
                         border: `1px solid ${C.line}`, borderRadius: 8, padding: "4px 10px",
                         cursor: "pointer" }}>
          Close
        </button>
      </div>

      {loading && (
        <div style={{ fontFamily: FONT, fontSize: 12.5, color: C.muted, marginTop: 12 }}>
          Loading…
        </div>
      )}
      {error && (
        <div style={{ fontFamily: FONT, fontSize: 12.5, color: C.badInk, marginTop: 12 }}>
          Couldn&rsquo;t open this one. {String(error.message || error)}
        </div>
      )}

      {/* The note distinguishes "nobody reached this" from "we do not record this". They look
          identical as a zero and mean completely different things. */}
      {!loading && !error && drill?.note && (
        <div style={{ fontFamily: FONT, fontSize: 12.5, color: C.warnInk, background: C.warnBg,
                      border: `1px solid ${C.warnBar}`, borderRadius: 10, padding: "10px 12px",
                      marginTop: 12, lineHeight: 1.5 }}>
          {drill.note}
        </div>
      )}

      {!loading && !error && !!drill?.rows?.length && (
        <div className="adr-wrap" style={{ marginTop: 12, maxHeight: 380, overflowY: "auto" }}>
          <table className="adr-tbl">
            <thead>
              <tr>{cols.map((c) => <th key={c}>{HEADINGS[c] || c}</th>)}</tr>
            </thead>
            <tbody>
              {drill.rows.map((r, i) => (
                <tr key={`${r.email || r.name}-${i}`}>
                  {cols.map((c) => <td key={c}><Cell col={c} row={r} /></td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
