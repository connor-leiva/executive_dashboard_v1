/* Shared Books UI primitives — reuse the dashboard's theme tokens and card idiom. */
import { T, usd } from "../theme.js";

export const ENTITY = {
  ulrg: { label: "ULRG", dot: T.meadow },
  sympli: { label: "Sympli", dot: T.teal },
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

export const font = { head: "Poppins,sans-serif", body: "Inter,sans-serif" };

export function Card({ children, style }) {
  return (
    <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 14,
                  padding: 22, ...style }}>{children}</div>
  );
}

export function Eyebrow({ children }) {
  return (
    <div style={{ fontFamily: font.body, fontSize: 11, fontWeight: 700, letterSpacing: 0.8,
                  textTransform: "uppercase", color: T.muted, marginBottom: 10 }}>{children}</div>
  );
}

export function EntityChip({ k }) {
  const e = ent(k);
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontFamily: font.body,
                   fontSize: 12, fontWeight: 600, color: T.secondary }}>
      <span style={{ width: 7, height: 7, borderRadius: 9, background: e.dot }} />{e.label}
    </span>
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
                   padding: "3px 9px", background: t.bg, color: t.fg, whiteSpace: "nowrap" }}>{children}</span>
  );
}

/* MoM delta. For expense/cost lines pass invert so a decrease reads as good. */
export function Delta({ v, pv, invert = false }) {
  if (pv === undefined || pv === null || pv === 0) return <span style={{ color: T.muted, fontSize: 11 }}>—</span>;
  const pct = Math.round(((v - pv) / Math.abs(pv)) * 100);
  if (pct === 0) return <span style={{ color: T.muted, fontSize: 11 }}>flat</span>;
  const up = pct > 0;
  const good = invert ? !up : up;
  return (
    <span style={{ fontFamily: font.body, fontSize: 11.5, fontWeight: 700,
                   color: good ? T.meadowInk : T.poppyText }}>
      {up ? "▲" : "▼"} {Math.abs(pct)}%
    </span>
  );
}

/* Loading skeleton / error+retry / empty — the three data states, one place. */
export function StatePanel({ loading, error, retry, empty, emptyTitle, emptyMsg, children }) {
  if (loading) {
    return (
      <div style={{ display: "grid", gap: 16 }}>
        {[0, 1, 2].map((i) => (
          <div key={i} style={{ height: i === 0 ? 120 : 200, borderRadius: 14,
                                background: `linear-gradient(90deg,${T.white},${T.parchment},${T.white})`,
                                border: `1px solid ${T.line}` }} />
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <Card style={{ textAlign: "center", padding: 40 }}>
        <div style={{ fontFamily: font.body, fontSize: 13, color: T.secondary }}>
          Couldn't load this view.</div>
        <button onClick={retry} style={{ marginTop: 12, fontFamily: font.body, fontSize: 12.5,
          fontWeight: 600, color: T.slate, background: T.parchment, border: `1px solid ${T.line}`,
          borderRadius: 8, padding: "6px 14px", cursor: "pointer" }}>Retry</button>
      </Card>
    );
  }
  if (empty) {
    return (
      <Card style={{ textAlign: "center", padding: 44 }}>
        <div style={{ fontFamily: font.head, fontSize: 15, fontWeight: 600, color: T.ink }}>
          {emptyTitle || "Nothing here yet"}</div>
        <div style={{ fontFamily: font.body, fontSize: 13, color: T.muted, marginTop: 6 }}>
          {emptyMsg}</div>
      </Card>
    );
  }
  return children;
}

export { usd };
