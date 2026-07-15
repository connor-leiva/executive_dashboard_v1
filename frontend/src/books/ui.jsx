/* Shared Books UI primitives — port of the acumyn-books-v2 mockup's kit, mapped onto the
   live dashboard theme tokens (theme.js). */
import { T, usd } from "../theme.js";

export const signed = (n) => (n < 0 ? `(${usd(n)})` : usd(n));
export const font = { head: "Poppins,sans-serif", body: "Inter,sans-serif" };

export const ENTITY = {
  ulrg: { label: "ULRG + Team", dot: T.meadow },
  sympli: { label: "Sympli Mortgage", dot: T.teal },
  springb: { label: "Spring B", dot: T.daffodil },
  forum: { label: "The Forum", dot: T.daffodil },
  becollective: { label: "beCollective", dot: T.petal },
  all: { label: "Consolidated", dot: T.evergreen },
};
export const ent = (k) => ENTITY[k] || { label: k || "—", dot: T.muted };

export const CHAR_LABEL = {
  loan: "Loan (due-to / due-from)", distribution: "Distribution",
  contribution: "Capital contribution", shared_expense: "Shared expense",
  rent: "Rent", payroll_alloc: "Payroll allocation",
};
export const CHAR_BY_LABEL = Object.fromEntries(
  Object.entries(CHAR_LABEL).map(([k, v]) => [v, k]));

export function Eyebrow({ children, onDark }) {
  return (
    <span style={{ fontFamily: font.head, fontSize: 11, fontWeight: 700, letterSpacing: "0.12em",
                   textTransform: "uppercase", color: onDark ? T.onDarkMute : T.secondary }}>{children}</span>
  );
}

export function Card({ children, style, onClick, className }) {
  return (
    <div onClick={onClick} className={className} style={{ background: T.white, border: `1px solid ${T.line}`,
      borderRadius: 14, padding: 22, ...style }}>{children}</div>
  );
}

export function EntityChip({ k }) {
  const e = ent(k);
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span style={{ width: 8, height: 8, borderRadius: 2, background: e.dot }} />
      <span style={{ fontFamily: font.body, fontSize: 11.5, fontWeight: 600, color: T.secondary }}>{e.label}</span>
    </span>
  );
}

/* delta chip — favorable direction flips for money-out (expense/cost) lines via `inverse`. */
export function Delta({ v, pv, inverse }) {
  if (pv === undefined || pv === null || pv === 0) return null;
  const pct = ((v - pv) / Math.abs(pv)) * 100;
  if (Math.abs(pct) < 0.5) return <span style={{ fontFamily: font.body, fontSize: 10.5, color: T.muted }}>flat</span>;
  const up = pct > 0;
  const good = inverse ? !up : up;
  return (
    <span style={{ fontFamily: font.body, fontSize: 10.5, fontWeight: 700, color: good ? T.meadowInk : T.poppyText }}>
      {up ? "▲" : "▼"} {Math.abs(pct).toFixed(0)}%</span>
  );
}

const TONES = {
  good: { bg: T.meadowBg, fg: T.meadowInk }, warn: { bg: T.daffodilBg, fg: T.daffodilText },
  bad: { bg: "#FDECE7", fg: T.poppyText }, muted: { bg: T.parchment, fg: T.muted },
};
export function Pill({ tone = "muted", children }) {
  const t = TONES[tone] || TONES.muted;
  return (
    <span style={{ fontFamily: font.body, fontSize: 11, fontWeight: 700, borderRadius: 999,
                   padding: "3px 10px", background: t.bg, color: t.fg, whiteSpace: "nowrap" }}>{children}</span>
  );
}

/* A labeled detail line (Type / Memo / Account …) for the expanded rows. */
export function Field({ label, value }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <div style={{ display: "flex", gap: 8, fontFamily: font.body, fontSize: 12, lineHeight: 1.5 }}>
      <span style={{ color: T.muted, minWidth: 92, flexShrink: 0 }}>{label}</span>
      <span style={{ color: T.secondary, fontWeight: 500 }}>{value}</span>
    </div>
  );
}

/* Deep link to the transaction in QuickBooks — names the company so the user knows which. */
export function QboLink({ url, entity }) {
  if (!url) return null;
  return (
    <a href={url} target="_blank" rel="noreferrer" style={{ fontFamily: font.body, fontSize: 11.5,
      fontWeight: 700, color: T.teal, textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 4 }}>
      Open in QuickBooks{entity ? ` · ${ent(entity).label}` : ""} ↗</a>
  );
}

/* The evergreen "on dark" surface the mockup uses for the rail, review, and IC hero. */
export const DARK = {
  background: T.evergreen,
  backgroundImage: "repeating-linear-gradient(90deg, rgba(255,255,255,0.045) 0px, rgba(255,255,255,0.045) 1.5px, rgba(255,255,255,0) 1.5px, rgba(255,255,255,0) 13px)",
};

/* loading skeleton / error+retry / empty — the three data states. */
export function StatePanel({ loading, error, retry, empty, emptyTitle, emptyMsg, children }) {
  if (loading) {
    return (
      <div style={{ display: "grid", gap: 16 }}>
        {[0, 1, 2].map((i) => (
          <div key={i} style={{ height: i === 0 ? 150 : 200, borderRadius: 14,
                                background: `linear-gradient(90deg,${T.white},${T.parchment},${T.white})`,
                                border: `1px solid ${T.line}` }} />
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <Card style={{ textAlign: "center", padding: 40 }}>
        <div style={{ fontFamily: font.body, fontSize: 13, color: T.secondary }}>Couldn't load this view.</div>
        <button onClick={retry} style={{ marginTop: 12, fontFamily: font.body, fontSize: 12.5, fontWeight: 600,
          color: T.slate, background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 8,
          padding: "6px 14px", cursor: "pointer" }}>Retry</button>
      </Card>
    );
  }
  if (empty) {
    return (
      <Card style={{ textAlign: "center", padding: 44 }}>
        <div style={{ fontFamily: font.head, fontSize: 15, fontWeight: 600, color: T.ink }}>{emptyTitle || "Nothing here yet"}</div>
        <div style={{ fontFamily: font.body, fontSize: 13, color: T.muted, marginTop: 6 }}>{emptyMsg}</div>
      </Card>
    );
  }
  return children;
}

export { usd, T };
