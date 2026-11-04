/* Every control on the Allocations page, built once (UI spec 9.4, D10).
 *
 * The page had sub-nav buttons, an entity select, Change, Create a rule, the driver selects
 * and Commit all at different heights and paddings, each set by a literal in whichever file
 * needed it (U21). Five heights is not a style, it is five people guessing. So: the recipes
 * from 9.2 live here, nothing else in the folder renders a bare `<button>`, `<select>` or
 * `<input>` (check B10), and the look has one place to change.
 *
 * Focus and hover are HERE too, not left to each caller. The page had a pointer cursor and
 * nothing else: no hover, no visible keyboard focus, no pressed state (U24). A control that
 * cannot be seen to have focus cannot be used without a mouse.
 *
 * Reuses `cleanup/kit.jsx` where it already has the piece, as the parent spec's standing rule
 * 17 says to. What is here is what that kit does not carry.
 */
import { useState } from "react";
import { LEDGER as L, LEDGER_FONT as F, alpha } from "../../theme.js";

/* The outline every focusable control shares. `:focus-visible` rather than `:focus`, so a
   mouse click does not draw a ring a keyboard user needs and a mouse user reads as an error.
   Declared in the page's style block as `.al-ctl`; this is the shape of it. */
export const CTL = { fontFamily: F.body, cursor: "pointer", outline: "none" };

const SIZES = {
  toolbar: { height: 34, radius: 9, padding: "0 13px", fontSize: 13 },
  card: { height: 31, radius: 8, padding: "0 12px", fontSize: 12.5 },
};

/** Primary (filled), secondary (outlined) or quiet (text). `size` is toolbar or card. */
export function Button({ kind = "secondary", size = "toolbar", children, onClick,
                         disabled, title, type = "button", full = false }) {
  const s = SIZES[size] || SIZES.toolbar;
  const base = { ...CTL, ...s, display: "inline-flex", alignItems: "center",
                 justifyContent: "center", gap: 6, whiteSpace: "nowrap",
                 width: full ? "100%" : undefined,
                 opacity: disabled ? 0.55 : 1,
                 cursor: disabled ? "not-allowed" : "pointer" };
  const look = {
    primary: { background: L.deep, border: `1px solid ${L.deep}`, color: L.onDeep,
               fontWeight: 600 },
    secondary: { background: L.paper, border: `1px solid ${L.pillEdge}`, color: L.bodyDim,
                 fontWeight: 500 },
    quiet: { background: "transparent", border: "1px solid transparent", color: L.accentInk,
             fontWeight: 600, fontSize: 12, padding: "0 4px" },
  }[kind] || {};
  return (
    <button type={type} onClick={onClick} disabled={disabled} title={title}
            className={`al-ctl al-btn-${kind}`} style={{ ...base, ...look }}>
      {children}
    </button>
  );
}

/** A filter chip: tier picks, drawer picks. `on` is the selected state. */
export function Chip({ on, children, onClick, title }) {
  return (
    <button type="button" onClick={onClick} title={title} className="al-ctl al-chip"
            aria-pressed={!!on}
            style={{ ...CTL, height: 30, borderRadius: 8, padding: "0 11px", fontSize: 12.5,
                     fontWeight: on ? 600 : 500,
                     background: on ? L.deep : L.paper,
                     border: `1px solid ${on ? L.deep : L.pillEdge}`,
                     color: on ? L.onDeep : L.bodyDim }}>
      {children}
    </button>
  );
}

/** A select. `options` is `[value, label]` pairs. */
export function Select({ value, onChange, options, ariaLabel, size = "toolbar",
                         tone = "light", width }) {
  const s = SIZES[size] || SIZES.toolbar;
  const dark = tone === "dark";
  return (
    <select value={value} aria-label={ariaLabel} className="al-ctl al-select"
            onChange={(e) => onChange(e.target.value)}
            style={{ ...CTL, ...s, width,
                     fontWeight: dark ? 600 : 500,
                     background: dark ? L.deep : L.controls,
                     border: `1px solid ${dark ? L.onDeepPillEdge : L.pillEdge}`,
                     borderRadius: dark ? 999 : s.radius,
                     height: dark ? 28 : s.height,
                     color: dark ? L.onDeep : L.body }}>
      {options.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
    </select>
  );
}

/** A text or number input. `numeric` right-aligns it and uses the figure face. */
export function Input({ value, onChange, placeholder, ariaLabel, numeric = false,
                        disabled = false, width, flag = false, size = "toolbar",
                        inputMode, onKeyDown }) {
  const s = SIZES[size] || SIZES.toolbar;
  return (
    <input value={value} placeholder={placeholder} aria-label={ariaLabel} disabled={disabled}
           inputMode={inputMode} onKeyDown={onKeyDown} className="al-ctl al-input"
           onChange={(e) => onChange(e.target.value)}
           style={{ ...CTL, ...s, width, cursor: disabled ? "not-allowed" : "text",
                    fontFamily: numeric ? F.num : F.body,
                    textAlign: numeric ? "right" : "left",
                    background: disabled ? L.track : L.paper,
                    border: `1px solid ${flag ? L.flagEdge : L.pillEdge}`,
                    color: L.body }} />
  );
}

/** The underlined tab row that replaces the pill `SegNav` (9.2). */
export function Tabs({ value, onChange, items }) {
  return (
    <div role="tablist" className="al-tabs"
         style={{ display: "flex", gap: 2, flexWrap: "wrap", background: L.controls,
                  borderBottom: `1px solid ${L.ruleHeader}`, padding: "0 clamp(14px,3vw,26px)" }}>
      {items.map(({ key, label, count }) => {
        const on = value === key;
        return (
          <button key={key} role="tab" aria-selected={on} onClick={() => onChange(key)}
                  className="al-ctl al-tab"
                  style={{ ...CTL, fontSize: 13, padding: "11px 14px 12px",
                           marginBottom: -1, background: "transparent",
                           border: "none",
                           // 2px, not 9.2's 2.5: the mockup draws 2 and principle 8
                           // gives appearance to the mockup. Measured side by side.
                           borderBottom: `2px solid ${on ? L.accent : "transparent"}`,
                           color: on ? L.deep : L.faint, fontWeight: on ? 600 : 500 }}>
            {label}{count ? ` · ${count}` : ""}
          </button>
        );
      })}
    </div>
  );
}

/** Three bars where a row will be. Replaces "Reading this month." as loading (U25, 9.5). */
export function Skeleton({ rows = 3 }) {
  return (
    <div aria-busy="true" aria-label="Loading"
         style={{ background: L.paper, border: `1px solid ${L.ruleInner}`, borderRadius: 13,
                  overflow: "hidden" }}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} style={{ display: "flex", gap: 14, alignItems: "center",
                              padding: "14px 18px",
                              borderBottom: i === rows - 1 ? "none"
                                                           : `1px solid ${L.ruleLine}` }}>
          <span className="al-skel" style={{ height: 9, borderRadius: 5, flex: "1 1 auto",
                                             background: L.track }} />
          <span className="al-skel" style={{ height: 9, width: 86, borderRadius: 5,
                                             background: L.track }} />
        </div>
      ))}
    </div>
  );
}

/** The band that confirms a save, with its dot. Replaces a button label changing (U25). */
export function SavedBand({ children, onUndo }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "10px 16px",
                  background: L.accentBg, fontFamily: F.body, fontSize: 12.5,
                  color: L.accentInk }}>
      <span aria-hidden style={{ width: 7, height: 7, borderRadius: "50%",
                                 background: L.accent, flex: "0 0 auto" }} />
      <span style={{ flex: "1 1 auto" }}>{children}</span>
      {onUndo && <Button kind="quiet" size="card" onClick={onUndo}>Undo</Button>}
    </div>
  );
}

/** The page frame: one surface, so the page stops being boxes inside boxes (U19, 9.2). */
export function PageFrame({ children }) {
  return (
    <div style={{ background: L.card, border: `1px solid ${L.cardEdge}`, borderRadius: 16,
                  maxWidth: 1180, overflow: "hidden",
                  boxShadow: `0 16px 38px -26px ${alpha(L.deep, 0.5)}` }}>
      {children}
    </div>
  );
}

/** Hover state without a stylesheet rule, for the few places that need it inline. */
export function useHover() {
  const [on, setOn] = useState(false);
  return [on, { onMouseEnter: () => setOn(true), onMouseLeave: () => setOn(false) }];
}
