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

import { C } from "./adsTokens.js";
import { Glyph } from "./ui.jsx";

const HEADINGS = {
  name: "Name", email: "Email", campaign: "Campaign", match: "Matched by",
  detail: "Detail",
  first_seen: "First seen", reached: "Reached", url: "CRM",
  // THREE MONEY COLUMNS, DELIBERATELY. What she signed for, what the price sheet says was due at
  // signing, and what we can actually match in the payment records. One column would have to pick
  // one of the three, and picking silently is exactly the bug this drill exposed.
  payment: "Pays", value: "Contracted", upfront: "Due at signing", cash: "Cash matched",
  priced: "Priced from",
};

const MONEY = new Set(["value", "upfront", "cash"]);

function Cell({ col, row }) {
  const v = row[col];
  if (col === "url") {
    return v ? <a href={v} target="_blank" rel="noreferrer" className="ax-link">Open in GHL</a> : "—";
  }
  if (MONEY.has(col)) {
    return <span className="ax-num" style={{ color: col === "value" ? C.ink : C.slate }}>
      {v && v !== "—" ? `$${v}` : "—"}
    </span>;
  }
  if (col === "priced") {
    // A price-sheet figure and a number somebody typed into a GHL opportunity are not the same
    // kind of fact, so the weaker provenances are flagged rather than blending in.
    const weak = v === "GHL amount" || v === "not priced";
    return weak
      ? <span className="ax-tag" style={{ marginLeft: 0 }}>{v}</span>
      : <span className="ax-meta">{v || "—"}</span>;
  }
  if (col === "first_seen" || col === "reached") {
    return <span className="ax-num" style={{ color: v === "undated" ? C.quiet : C.ink }}>{v || "—"}</span>;
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
    <div ref={ref} tabIndex={-1} role="region" className="ax-card ax-stack"
         aria-label={`Who is behind ${drill?.title || label || "this number"}`}
         style={{ outline: "none", gap: 12 }}>
      <div className="ax-drawer__head">
        <div style={{ minWidth: 0 }}>
          <h3 className="ax-card__title">{drill?.title || label}</h3>
          {drill?.subtitle && <p className="ax-meta" style={{ marginTop: 2 }}>{drill.subtitle}</p>}
        </div>
        <button type="button" className="ax-btn ax-btn--icon" onClick={onClose}
                aria-label="Close" title="Close">
          <Glyph name="close" />
        </button>
      </div>

      {loading && <p className="ax-text" style={{ color: C.quiet }}>Loading…</p>}
      {error && (
        <p className="ax-text" style={{ color: C.badInk }}>
          Couldn&rsquo;t open this one. {String(error.message || error)}
        </p>
      )}

      {/* The note distinguishes "nobody reached this" from "we do not record this". They look
          identical as a zero and mean completely different things. */}
      {!loading && !error && drill?.note && (
        <div className="ax-note"><div className="ax-note__body">{drill.note}</div></div>
      )}

      {!loading && !error && !!drill?.rows?.length && (
        <div className="ax-scroll" style={{ maxHeight: 380, overflowY: "auto" }}>
          <table className="ax-table ax-table--plain ax-table--stack">
            <thead>
              <tr>{cols.map((c) => <th key={c} scope="col" className={MONEY.has(c) ? "num" : undefined}>{HEADINGS[c] || c}</th>)}</tr>
            </thead>
            <tbody>
              {drill.rows.map((r, i) => (
                <tr key={`${r.email || r.name}-${i}`}>
                  {cols.map((c) => (
                    // On a phone the name leads each person's block and the rest sit under labels.
                    <td key={c} data-label={c === "name" ? undefined : (HEADINGS[c] || c)}
                        className={[MONEY.has(c) ? "num" : "", c === "name" ? "ax-cell-title ax-strong" : ""].join(" ").trim() || undefined}>
                      <Cell col={c} row={r} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
