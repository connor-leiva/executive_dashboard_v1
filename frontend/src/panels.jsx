/* The Command Center's panel primitives, lifted out of CommandCenter.jsx so every dashboard view is
   built from the same ones: the card, its accent-ruled label, the source chip and the mono source
   tag, the small operational tile, the bar list, the shimmer skeleton, and the segmented control the
   loan pipeline and the audit drawer already used. Moved unchanged; the two new exports (StatTile,
   Segmented) are the Portfolio strip's tile and the loan pipeline's toggle as components. */
import { T } from "./theme.js";
import { Icon } from "./Brand.jsx";

/* Sentence case for labels that arrive Title Cased ("Combined Profit" -> "Combined profit"),
   leaving acronyms (GCI, NOI) alone. */
export const sentence = (str) => String(str || "").split(" ")
  .map((w, i) => (i === 0 || /^[A-Z0-9]{2,}$/.test(w) ? w : w.toLowerCase())).join(" ");

export function Source({ name }) {
  return (
    <span style={{
      fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate,
      background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 5, padding: "2px 7px",
    }}>{name}</span>
  );
}

/* Where a figure came from, as one muted tag in the data face. The name is the server's
   (Scorecard.source), in the same words as the source list in the top bar: a client-side guess
   would call a SkySlope brokerage's closings "Sisu", and SkySlope's own name is not ours to show.
   No source, no tag: a seeded placeholder came from no system. */
export function SourceTag({ of, onDark }) {
  if (!of) return null;
  return (
    <span style={{
      fontFamily: "var(--font-data)", fontSize: 11, letterSpacing: "0.01em", lineHeight: "16px",
      color: onDark ? T.onDarkMute : T.tertiary, padding: "0 6px", borderRadius: 4,
      border: `1px solid ${onDark ? "rgba(239,240,236,0.14)" : T.line}`, whiteSpace: "nowrap",
    }}>{of}</span>
  );
}

export function Eyebrow({ children, onDark }) {
  return (
    <div style={{
      fontFamily: "var(--font-text)", fontSize: 13, fontWeight: 500,
      color: onDark ? T.onDarkMute : T.tertiary,
    }}>{children}</div>
  );
}

/* A method note kept off the page: the info glyph, with the note as its tooltip and its accessible
   name. Focusable, so a keyboard reaches it too -- except inside a button, where a second tab stop
   is invalid and the button's own focus already reads the label. What's Working's caveats
   introduced it. */
export function Info({ text, size = 13, focusable = true }) {
  if (!text) return null;
  return (
    <span title={text} aria-label={text} role="img" tabIndex={focusable ? 0 : undefined}
      style={{ display: "inline-flex", cursor: "help", flexShrink: 0, borderRadius: 99 }}>
      <Icon name="info" size={size} color={T.muted} />
    </span>
  );
}

export function PanelLabel({ children, accent, info }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
      <span style={{ width: 3, height: 14, borderRadius: 2, background: accent || T.evergreen }} />
      <span style={{ fontFamily: "var(--font-display)", fontSize: 12, fontWeight: 600, color: T.slate }}>
        {children}
      </span>
      <Info text={info} />
    </div>
  );
}

export function Card({ children, style }) {
  // border-box so the 22px padding sits INSIDE the width — several cards carry a
  // minWidth: 300 that would otherwise render 344 and overflow a phone column.
  return <div style={{ boxSizing: "border-box", background: T.white, border: `1px solid ${T.line}`, borderRadius: 14, padding: 22, ...style }}>{children}</div>;
}

export function OpTile({ d, onDrill }) {
  const clickable = Boolean(d.key) && Boolean(onDrill);
  const Tag = clickable ? "button" : "div";
  return (
    <Tag onClick={clickable ? () => onDrill(d.key) : undefined} className={clickable ? "cc-card cc-optile" : undefined}
      style={{ display: "block", textAlign: "left", width: "100%", minWidth: 0, boxSizing: "border-box", margin: 0, font: "inherit",
        border: "none", background: T.parchment, borderRadius: 10, padding: "12px 13px", cursor: clickable ? "pointer" : "default" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
        <span style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.slate, fontWeight: 500 }}>{d.asIs ? d.label : sentence(d.label)}</span>
        {clickable && <span style={{ marginLeft: "auto", fontSize: 12, color: T.muted }}>↗</span>}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <span style={{ fontFamily: "var(--font-display)", fontSize: 22, fontWeight: 600, color: T.ink, fontVariantNumeric: "tabular-nums" }}>{d.value}</span>
        {d.sub && <span style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.muted }}>{d.sub}</span>}
      </div>
    </Tag>
  );
}

export function Bars({ rows, accent }) {
  const max = Math.max(...rows.map((r) => r.v));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
      {rows.map((r, i) => (
        <div key={i} style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ width: 108, fontFamily: "var(--font-text)", fontSize: 12, color: T.slate, textAlign: "right" }}>{r.label}</span>
          <div style={{ flex: 1, height: 22, background: T.parchment, borderRadius: 5, overflow: "hidden" }}>
            <div style={{ width: `${(r.v / max) * 100}%`, height: "100%", background: accent, opacity: 0.4 + 0.6 * (r.v / max), borderRadius: 5 }} />
          </div>
          <span style={{ width: 42, fontFamily: "var(--font-display)", fontSize: 13, fontWeight: 600, color: T.ink, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{r.v}</span>
        </div>
      ))}
    </div>
  );
}

export function Skel({ w = "100%", h = 12, r = 8, style }) {
  return <span className="cc-skel" style={{ display: "block", width: w, height: h, borderRadius: r, ...style }} />;
}

export function SkeletonCard({ h = 96 }) {
  return (
    <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 12, padding: 16 }}>
      <Skel w="55%" h={10} style={{ marginBottom: 14 }} />
      <Skel w="72%" h={h > 110 ? 30 : 24} />
    </div>
  );
}

/* The Portfolio strip's tile: label and source tag, one figure, one line under it. Clickable when
   it drills. `value` is a node, so a caller can render a muted "Not mapped" in the same slot.
   `asIs` keeps a label the caller already wrote in sentence case -- `sentence` would lower-case
   the proper nouns in "ROI on full QuickBooks spend". */
export function StatTile({ label, source, value, sub, onClick, title, asIs, info }) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag onClick={onClick} title={title} className={onClick ? "cc-card" : undefined}
      style={{
        textAlign: "left", width: "100%", minWidth: 0, boxSizing: "border-box", margin: 0, font: "inherit",
        background: T.white, border: `1px solid ${T.line}`,
        borderRadius: 14, padding: 20, cursor: onClick ? "pointer" : "default",
      }}>
      {/* A fixed-height header row: a tile with a source tag and one without keep their titles level. */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, minHeight: 18 }}>
        <span style={{ fontFamily: "var(--font-text)", fontSize: 13, color: T.tertiary, fontWeight: 500 }}>{asIs ? label : sentence(label)}</span>
        <Info text={info} focusable={!onClick} />
        <span style={{ marginLeft: "auto" }}><SourceTag of={source} /></span>
      </div>
      <div style={{ fontFamily: "var(--font-data)", fontSize: 32, fontWeight: 500, color: T.ink, lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>{value}</div>
      <div style={{ fontFamily: "var(--font-text)", fontSize: 13, color: T.tertiary, marginTop: 8 }}>{sub}</div>
    </Tag>
  );
}

/* The loan pipeline's Total / By loan officer / By source toggle, as a component. `options` is
   [[value, label], ...]. `tone` colours the selected label (teal by default, as it was). */
export function Segmented({ options, value, onChange, tone, label }) {
  const seg = (on) => ({
    fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, cursor: "pointer",
    border: "none", borderRadius: 6, padding: "5px 11px", whiteSpace: "nowrap",
    background: on ? T.white : "transparent", color: on ? (tone || T.teal) : T.muted,
    boxShadow: on ? "0 1px 3px rgba(0,46,44,.12)" : "none",
  });
  return (
    <div role="group" aria-label={label} style={{ display: "inline-flex", gap: 3, background: T.parchment, borderRadius: 8, padding: 3, flexWrap: "wrap" }}>
      {options.map(([v, l]) => (
        <button key={v} type="button" aria-pressed={value === v} onClick={() => onChange(v)} style={seg(value === v)}>{l}</button>
      ))}
    </div>
  );
}
