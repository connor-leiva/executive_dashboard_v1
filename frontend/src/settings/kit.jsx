/* Settings' shared pieces: the card every page is built from, the two button styles, and the
   small neutral chip. Lifted out of Settings.jsx unchanged so pages that live in their own file
   (Lead sources) are built from the same components rather than copies of them. */
import { T } from "../theme.js";
import { Icon } from "../Brand.jsx";

export function Card({ title, hint, children }) {
  return (
    <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 14, padding: 22, marginBottom: 18 }}>
      {title && <div style={{ fontFamily: "var(--font-display)", fontSize: 15, fontWeight: 600, color: T.ink }}>{title}</div>}
      {hint && <div style={{ fontFamily: "var(--font-text)", fontSize: 13, color: T.muted, marginTop: 3, marginBottom: 14 }}>{hint}</div>}
      {children}
    </div>
  );
}

export function btn(kind) {
  const base = {
    fontFamily: "var(--font-text)", fontSize: 13, fontWeight: 600, borderRadius: 8,
    padding: "6px 12px", cursor: "pointer", border: "none",
  };
  if (kind === "primary") return { ...base, color: T.onDark, background: T.evergreen };
  if (kind === "danger") return { ...base, color: T.poppyText, background: T.white, border: `1px solid ${T.line}` };
  if (kind === "disabled") return { ...base, color: T.muted, background: T.parchment, border: `1px solid ${T.line}`, cursor: "not-allowed" };
  return { ...base, color: T.slate, background: T.parchment, border: `1px solid ${T.line}` };
}

export const Chip = ({ children }) => (
  <span style={{ fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate,
    background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 5, padding: "2px 8px" }}>{children}</span>
);

export function SBtn({ kind = "ghost", small, icon, children, onClick, disabled, title }) {
  const s = { ghost: { bg: T.white, color: T.slate, border: T.line }, primary: { bg: T.poppy, color: "#fff", border: T.poppy } }[kind];
  return (
    <button className={`si-btn ${kind}`} onClick={onClick} disabled={disabled} title={title} style={{
      display: "inline-flex", alignItems: "center", gap: 6, background: disabled ? T.parchment : s.bg,
      color: disabled ? T.muted : s.color, border: `1px solid ${disabled ? T.line : s.border}`, borderRadius: 8,
      padding: small ? "5px 11px" : "7px 14px", fontFamily: "var(--font-display)", fontSize: small ? 11.5 : 12,
      fontWeight: 600, cursor: disabled ? "not-allowed" : "pointer" }}>
      {icon && <Icon name={icon} size={12} color="currentColor" />}{children}
    </button>
  );
}
