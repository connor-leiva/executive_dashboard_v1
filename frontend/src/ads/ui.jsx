/* Ads screen primitives. One accent, one type scale, scoped to this module.
   Colours come from adsTokens so a rebrand still has a single place to move. The look lives in
   screenStyles.js as classes, because hover, press, focus and reduced motion cannot be written
   as inline styles. */
import { useEffect, useRef, useState } from "react";

import { C } from "./adsTokens.js";

export const SCREENS = [
  { id: "today", label: "Today" },
  { id: "performance", label: "Performance" },
  { id: "budget", label: "Budget" },
  { id: "activity", label: "Activity" },
  { id: "settings", label: "Settings" },
];

/* The pace arithmetic lives in pace.js, where it is tested without a browser. */
export const PACE_WORD = { ahead: "Ahead", behind: "Behind", on: "On pace", none: "No plan" };

/* The open week stays readable. Mix the status color toward paper instead of
   fading the whole bar, which would turn it grey. */
export function tone(status, open) {
  const solid = status === "behind" ? C.badBar : status === "ahead" ? C.goodBar : C.accent;
  const ink = status === "behind" ? C.badInk : status === "ahead" ? C.goodInk : C.accent;
  if (!open) return { bar: solid, ink };
  return {
    bar: `color-mix(in srgb, ${solid} 58%, ${C.parchment})`,
    ink: `color-mix(in srgb, ${ink} 62%, ${C.slate})`,
  };
}

/* Interface glyphs, drawn the way brand/productIcons.jsx draws a module mark: a 32 artboard,
   2.6 stroke, butt caps. One set for the whole tab, so a chevron, a sort arrow and a close mark
   read as one family with the Ads mark beside the title. They take the text colour. */
const GLYPHS = {
  chevron: "M9 13 L16 20 L23 13",
  arrow: "M7 16 H24 M17 9 L24 16 L17 23",
  close: "M9 9 L23 23 M23 9 L9 23",
  up: "M16 25 V8 M9 15 L16 8 L23 15",
  down: "M16 7 V24 M9 17 L16 24 L23 17",
};

export function Glyph({ name, size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" stroke="currentColor"
         strokeWidth={2.6} strokeLinecap="butt" strokeLinejoin="miter" aria-hidden="true"
         focusable="false" style={{ flex: "none" }}>
      <path d={GLYPHS[name] || GLYPHS.arrow} />
    </svg>
  );
}

/* Every screen names itself for a screen reader. The tab bar right above already shows the
   name, so printing it again as a heading only pushed the screen's content down. */
export function PageTitle({ title }) {
  return <h2 className="ax-sr">{title}</h2>;
}

export function Card({ title, meta, foot, flush, id, children, style }) {
  return (
    <section id={id} className={`ax-card${flush ? " ax-card--flush" : ""}`} style={style}>
      {(title || meta) && (
        <div className="ax-card__head">
          {title && <h3 className="ax-card__title">{title}</h3>}
          {meta && <span className="ax-meta">{meta}</span>}
        </div>
      )}
      {children}
      {foot && <div className="ax-card__foot">{foot}</div>}
    </section>
  );
}

/* A headline figure. With onClick it is a button that lifts and shows where it goes; without,
   it is a card that stays still. */
export function Stat({ label, value, note, color, onClick, hint }) {
  const body = (
    <>
      <span className="ax-label">{label}</span>
      <span className="ax-fig" style={color ? { color } : undefined}>{value}</span>
      {note && <span className="ax-stat__note">{note}</span>}
    </>
  );
  if (!onClick) return <div className="ax-card ax-stat">{body}</div>;
  return (
    <button type="button" className="ax-card ax-stat ax-lift" onClick={onClick} title={hint}>
      {body}
      <span className="ax-stat__go"><Glyph name="arrow" /></span>
    </button>
  );
}

export function Btn({ children, onClick, disabled, primary, title, label, icon }) {
  const cls = `ax-btn${primary ? " ax-btn--primary" : ""}${icon ? " ax-btn--icon" : ""}`;
  return (
    <button type="button" className={cls} onClick={onClick} disabled={disabled} title={title}
            aria-label={label}>
      {children}
    </button>
  );
}

/* Two to six choices, one of them on. The shell's period switch, so a filter here looks like a
   filter there and never like the primary action. */
export function Segmented({ label, options, value, onChange }) {
  return (
    <div className="ax-seg" role="radiogroup" aria-label={label}>
      {options.map(([key, text]) => (
        <button key={key} type="button" role="radio" aria-checked={value === key}
                onClick={() => onChange(key)}>{text}</button>
      ))}
    </div>
  );
}

export function Banner({ title, children, tone: kind }) {
  return (
    <div className="ax-note" data-tone={kind === "bad" ? "bad" : "warn"} role="status">
      <div className="ax-note__title">{title}</div>
      {children && <div className="ax-note__body">{children}</div>}
    </div>
  );
}

export function Empty({ children }) {
  return <p className="ax-text" style={{ color: C.quiet }}>{children}</p>;
}

export function ErrorNote({ children, onRetry }) {
  return (
    <p className="ax-text" style={{ color: C.badInk }}>
      {children}{" "}
      {onRetry && <button type="button" className="ax-link" onClick={onRetry}>Try again</button>}
    </p>
  );
}

/* The screen while its numbers load. The header and the screen tabs stay put above it, so a
   change of period or basis no longer blanks the whole tab to one word. */
export function Skeleton({ label = "Loading" }) {
  return (
    <div className="ax-stack" role="status" aria-busy="true">
      <span className="ax-sr">{label}</span>
      <div className="ax-stats" aria-hidden="true">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="ax-card ax-stat">
            <span className="ax-sk" style={{ width: "45%" }} />
            <span className="ax-sk" style={{ width: "70%", height: 22 }} />
          </div>
        ))}
      </div>
      <div className="ax-card ax-stack" aria-hidden="true">
        <span className="ax-sk" style={{ width: "25%" }} />
        {[0, 1, 2, 3].map((i) => (
          <div key={i} style={{ display: "grid", gap: "var(--ax-2)" }}>
            <span className="ax-sk" style={{ width: "35%" }} />
            <span className="ax-sk" style={{ height: 8 }} />
          </div>
        ))}
      </div>
    </div>
  );
}

function useMedia(query) {
  const get = () => typeof window !== "undefined" && !!window.matchMedia?.(query).matches;
  const [on, setOn] = useState(get);
  useEffect(() => {
    const m = window.matchMedia?.(query);
    if (!m) return undefined;
    const update = () => setOn(m.matches);
    update();
    m.addEventListener?.("change", update);
    return () => m.removeEventListener?.("change", update);
  }, [query]);
  return on;
}

const FOCUSABLE = "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), "
  + "textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

/* The side panel. It takes the focus when it opens and hands it back when it closes, so a
   keyboard user lands in it and returns to the row they came from.
   Beside the page it is a non-modal panel: the rows behind it stay usable, and clicking another
   one swaps what it shows. At phone width it covers the page, so there it is modal: Tab cycles
   inside it, because the controls behind it can no longer be seen. */
export function Drawer({ title, onClose, children }) {
  const panelRef = useRef(null);
  const closeRef = useRef(null);
  const covers = useMedia("(max-width: 640px)");
  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape") { onClose?.(); return; }
      if (e.key !== "Tab" || !covers || !panelRef.current) return;
      const items = [...panelRef.current.querySelectorAll(FOCUSABLE)]
        .filter((el) => el.getClientRects().length > 0);
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      const at = document.activeElement;
      if (!panelRef.current.contains(at)) { e.preventDefault(); first.focus(); return; }
      if (e.shiftKey && at === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && at === last) { e.preventDefault(); first.focus(); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, covers]);
  useEffect(() => {
    const from = document.activeElement;
    closeRef.current?.focus({ preventScroll: true });
    return () => { if (from && from.isConnected) from.focus?.({ preventScroll: true }); };
  }, []);
  return (
    <aside ref={panelRef} className="ax-drawer" role="dialog" aria-labelledby="ax-drawer-title"
           aria-modal={covers ? "true" : undefined}>
      <div className="ax-drawer__head">
        <h2 id="ax-drawer-title" className="ax-drawer__title">{title}</h2>
        <button ref={closeRef} type="button" className="ax-btn ax-btn--icon" onClick={onClose}
                aria-label="Close" title="Close">
          <Glyph name="close" />
        </button>
      </div>
      {children}
    </aside>
  );
}

export function Field({ label, children }) {
  return (
    <label style={{ display: "grid", gap: 6 }}>
      <span className="ax-label">{label}</span>
      {children}
    </label>
  );
}

/* A dollar amount typed by hand. The $ is drawn beside the field, never typed into it, so the
   value saved is still the bare number it always was. */
export function MoneyInput({ value, onChange, label }) {
  return (
    <span className="ax-money">
      <span aria-hidden="true">$</span>
      <input className="ax-input" inputMode="decimal" aria-label={label} value={value}
             onChange={onChange} />
    </span>
  );
}

/* Keyboard access for a clickable table row: Enter or Space does what a click does. */
export function rowKeys(open) {
  return (e) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      open();
    }
  };
}

export function sentenceStatus(status) {
  if (!status) return null;
  const s = String(status).toLowerCase().replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/* Yoga2 and ResultsH3 are headline variants of one video, not a new ad. Size
   tokens like 1080 stay, so a static crop is not folded into a different file. */
export function familyKey(name) {
  let s = String(name || "");
  const variant = /\bresults\s*h\d+\b/i.test(s) || /^\s*video\b/i.test(s);
  s = s.replace(/\bresults\s*h\d+\b/gi, "");
  s = s.replace(/\bv\d+\b/gi, "");
  if (variant) s = s.replace(/(\D)\d+\b/g, "$1");
  return s.replace(/[-–—_]+/g, " ").replace(/\s+/g, " ").trim().toLowerCase();
}

export function foldRows(rows) {
  const groups = [];
  const index = new Map();
  for (const row of rows) {
    const key = `${row.verdict}:${familyKey(row.name) || row.external_id}`;
    if (!index.has(key)) {
      const bucket = { key, rows: [] };
      index.set(key, bucket);
      groups.push(bucket);
    }
    index.get(key).rows.push(row);
  }
  return groups;
}
