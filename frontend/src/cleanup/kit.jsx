/* Books Cleanup -- the shared pieces, in the Books Statement's ledger language (books/
   BooksStatement.jsx): the cream card, the dark entity bar, the controls strip, Archivo figures.
   The palette is theme.js LEDGER; no colour is spelled here. */
import { createContext } from "react";
import { LEDGER as L, LEDGER_FONT as F, alpha } from "../theme.js";

/* The tab's shell, for anything deep in a page that changes the books: the overview, whether this person
   may change them (owners and admins), the save counter's bump, and a way to another page. */
export const CleanupCtx = createContext(null);

/* Round the MAGNITUDE, not the signed value, exactly as the Statement does: Math.round(-0.5) is -0,
   so rounding first prints mirrored amounts a dollar apart. Nothing rounds to "−$0".
   A negative reads "−$89,844" (a true minus sign), not "($89,844)": the owners asked for it on Oct 19,
   and a loss says so in words where it's a result (see `Loss`). */
export const money = (n) => {
  const v = Math.round(Math.abs(Number(n) || 0));
  const neg = Number(n) < 0 && v !== 0;
  return (neg ? "−$" : "$") + v.toLocaleString("en-US");
};

/* Cents, for anything somebody will type into QuickBooks: an entry, a balance, a transaction. */
export const cents = (n) => {
  const v = Math.abs(Number(n) || 0);
  const s = v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const neg = Number(n) < 0 && s !== "0.00";
  return (neg ? "−$" : "$") + s;
};

/* " loss" after a negative result (net income, operating income), so a loss never rests on a sign alone. */
export function Loss({ v, size = 12, color }) {
  return Math.round(Number(v) || 0) < 0
    ? <span style={{ fontFamily: F.body, fontSize: size, fontWeight: 600, marginLeft: 5, color: color || L.loss }}>loss</span>
    : null;
}

const MONTH_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                    "November", "December"];
/* "March 2026", from the period's year and a month number. */
export const monthName = (m, year) => `${MONTH_LONG[m - 1] || ""}${year ? ` ${year}` : ""}`;
export const dayLabel = (iso) => (iso ? `${MONTH[Number(iso.slice(5, 7)) - 1]} ${Number(iso.slice(8, 10))}` : "");

/* A small rounded label: "Needs you", "Duplicate?", a saved change. */
export function Pill({ children, tone = "gold", title }) {
  const t = { red: [L.lossBg, L.loss, L.lossEdge], gold: [L.flagBg, L.flagInk, L.flagEdge],
              green: [L.accentBg, L.accentInk, L.accentEdge], blue: [L.infoBg, L.infoInk, L.infoEdge] }[tone];
  return (
    <span title={title} style={{ display: "inline-block", fontFamily: F.body, fontSize: 11, fontWeight: 600,
      padding: "1px 8px", borderRadius: 999, border: `1px solid ${t[2]}`, background: t[0], color: t[1],
      whiteSpace: "nowrap", lineHeight: 1.5 }}>{children}</span>
  );
}

/* A boxed sentence: an explanation (blue), a warning (gold), a done thing (green). */
export function Note({ children, tone = "plain", style }) {
  const t = { plain: [L.totalBand, L.bodyDim, L.ruleInner], gold: [L.flagBg, L.flagInk, L.flagEdge],
              blue: [L.infoBg, L.infoInk, L.infoEdge], green: [L.accentBg, L.accentInk, L.accentEdge],
              red: [L.lossBg, L.loss, L.lossEdge] }[tone];
  return <div style={{ background: t[0], color: t[1], border: `1px solid ${t[2]}`, borderRadius: 10,
                       padding: "10px 13px", fontFamily: F.body, fontSize: 13, lineHeight: 1.5, ...style }}>{children}</div>;
}

/* A signed change, said with a word as well as a sign: direction never rests on a minus alone. */
export const change = (n) => {
  const v = Number(n) || 0;
  if (Math.abs(v) < 0.5) return "no change";
  return (v > 0 ? "+" : "−") + money(Math.abs(v));
};

export const pctOf = (n, revenue) => (revenue > 0 ? (n / revenue * 100).toFixed(1) + "%" : "—");
export const share = (f) => `${(Number(f || 0) * 100).toFixed(1).replace(/\.0$/, "")}%`;

export const NUM = { fontFamily: F.num, fontVariantNumeric: "tabular-nums", textAlign: "right",
                     whiteSpace: "nowrap" };
export const EYEBROW = { fontFamily: F.num, fontSize: 10, letterSpacing: ".14em",
                         textTransform: "uppercase", color: L.faint };
export const BODY = { fontFamily: F.body, fontSize: 13, color: L.body, lineHeight: 1.5 };

const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export function periodLabel(p) {
  if (!p?.start || !p?.end) return "";
  const [, ms, ds] = p.start.split("-").map(Number);
  const [ye, me, de] = p.end.split("-").map(Number);
  return `${MONTH[ms - 1]} ${ds} – ${MONTH[me - 1]} ${de}, ${ye}`;
}

/* A company's short name where the file gives one ("Acme" for "Acme Holdings LLC"). */
export const shortOf = (ov, c) => (c === "all" ? "All three" : (ov?.company_short || {})[c] || c);

export function Chip({ on, children, onClick, dark, title }) {
  return (
    <button onClick={onClick} title={title} aria-pressed={on} style={{
      height: 28, padding: "0 13px", borderRadius: 999, cursor: "pointer",
      fontFamily: F.body, fontSize: 13, fontWeight: on ? 600 : 500, whiteSpace: "nowrap",
      border: dark ? `1px solid ${on ? L.paper : L.onDeepPillEdge}` : `1px solid ${on ? L.deep : L.pillEdge}`,
      background: dark ? (on ? L.paper : "transparent") : (on ? L.deep : L.paper),
      color: dark ? (on ? L.deep : L.onDeepPill) : (on ? L.onDeep : L.bodyDim),
    }}>{children}</button>
  );
}

/* The squarer, smaller toggle the Statement uses for a setting (as opposed to a subject). */
export function Toggle({ on, children, onClick, disabled, title }) {
  return (
    <button onClick={onClick} disabled={disabled} title={title} aria-pressed={on} style={{
      height: 28, padding: "0 11px", borderRadius: 8, cursor: disabled ? "default" : "pointer",
      fontFamily: F.body, fontSize: 12.5, fontWeight: on ? 600 : 500, whiteSpace: "nowrap",
      border: `1px solid ${on ? L.deep : L.pillEdge}`, background: on ? L.deep : L.paper,
      color: on ? L.onDeep : L.bodyDim, opacity: disabled && !on ? 0.55 : 1,
    }}>{children}</button>
  );
}

/* The segmented control (the Statement's "As allocated / As booked"). */
export function Seg({ options, value, onChange, disabled }) {
  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 3, background: L.sectionBand,
                  borderRadius: 10, padding: 3, flexWrap: "wrap" }}>
      {options.map(([label, v]) => (
        <button key={String(v)} onClick={() => !disabled && v !== value && onChange(v)} disabled={disabled}
          aria-pressed={v === value} style={{
            height: 32, padding: "0 15px", border: 0, borderRadius: 8,
            cursor: disabled ? "default" : "pointer", fontFamily: F.body, fontSize: 13.5,
            fontWeight: v === value ? 600 : 500, whiteSpace: "nowrap",
            background: v === value ? L.deep : "transparent", color: v === value ? L.onDeep : L.bodyDim,
            boxShadow: v === value ? `0 2px 6px -3px ${alpha(L.deep, 0.65)}` : "none",
          }}>{label}</button>
      ))}
    </div>
  );
}

/* The outer card every page sits in. */
export function Panel({ children, style }) {
  return (
    <div style={{ border: `1px solid ${L.cardEdge}`, borderRadius: 16, overflow: "hidden", background: L.card,
                  boxShadow: `0 16px 38px -26px ${alpha(L.deep, 0.5)}`, ...style }}>{children}</div>
  );
}

/* The dark bar across the top: what the page is about (an entity, a month), and the period. */
export function Bar({ label, children, right }) {
  return (
    <div style={{ background: L.deep, padding: "11px 26px", display: "flex", alignItems: "center", gap: 14,
                  flexWrap: "wrap" }}>
      <span style={{ ...EYEBROW, color: L.onDeepEyebrow, flex: "none" }}>{label}</span>
      <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", minWidth: 0 }}>{children}</div>
      <div style={{ flex: 1 }} />
      {right ? <span style={{ fontFamily: F.num, fontSize: 11, color: L.onDeepEyebrow, whiteSpace: "nowrap" }}>
        {right}</span> : null}
    </div>
  );
}

export function Controls({ children }) {
  return (
    <div style={{ background: L.controls, borderBottom: `1px solid ${L.ruleHeader}`, padding: "13px 26px",
                  display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>{children}</div>
  );
}

/* The white block inside the card: a ledger, a table, a form. */
export function Sheet({ children, style, title, right }) {
  return (
    <div style={{ border: `1px solid ${L.ruleInner}`, borderRadius: 13, background: L.paper, overflow: "hidden",
                  ...style }}>
      {title ? (
        <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap", padding: "12px 18px",
                      background: L.colHead, borderBottom: `1px solid ${L.ruleHeader}` }}>
          <span style={{ fontFamily: F.num, fontSize: 14.5, fontWeight: 700, color: L.deep, letterSpacing: ".01em" }}>
            {title}</span>
          <div style={{ flex: 1 }} />
          {right}
        </div>
      ) : null}
      {children}
    </div>
  );
}

/* The check pill: green with a dot when it holds, gold with a ring when it doesn't. */
export function OkPill({ ok, children, title }) {
  return (
    <span title={title} style={{ display: "inline-flex", alignItems: "center", gap: 8, padding: "5px 11px",
      borderRadius: 999, whiteSpace: "nowrap", fontFamily: F.body, fontSize: 12.5, fontWeight: 500,
      background: ok ? L.accentBg : L.flagBg, color: ok ? L.accentInk : L.flagInk }}>
      <span aria-hidden="true" style={ok
        ? { width: 6, height: 6, borderRadius: "50%", background: L.accent }
        : { width: 7, height: 7, borderRadius: "50%", border: `2px solid ${L.flagRing}`, boxSizing: "border-box" }} />
      {children}
    </span>
  );
}

export function Foot({ children, style }) {
  return <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.ghost, lineHeight: 1.55, textWrap: "pretty",
                       ...style }}>{children}</div>;
}

export function Button({ children, onClick, disabled, primary, title, type = "button" }) {
  return (
    <button type={type} onClick={onClick} disabled={disabled} title={title} style={{
      height: 30, padding: "0 13px", borderRadius: 8, cursor: disabled ? "default" : "pointer",
      fontFamily: F.body, fontSize: 12.5, fontWeight: 600, whiteSpace: "nowrap",
      border: `1px solid ${primary ? L.deep : L.pillEdge}`, background: primary ? L.deep : L.paper,
      color: primary ? L.onDeep : L.bodyDim, opacity: disabled ? 0.55 : 1,
    }}>{children}</button>
  );
}

export const inputStyle = {
  height: 30, padding: "0 9px", borderRadius: 7, border: `1px solid ${L.pillEdge}`, background: L.paper,
  fontFamily: F.num, fontSize: 13, color: L.deep, fontVariantNumeric: "tabular-nums", boxSizing: "border-box",
  minWidth: 0,
};

/* What a save said back, or why it failed, in one line under the thing that was saved. */
export function SaveLine({ state }) {
  if (!state) return null;
  const { busy, error, saved } = state;
  return (
    <span role="status" style={{ fontFamily: F.body, fontSize: 12, color: error ? L.flagInk : L.muted }}>
      {busy ? "Saving…" : error ? error : saved ? saved : ""}
    </span>
  );
}

/* The server's reason when it has one ("A share is a number between 0 and 1."). */
export const reasonOf = (e) => (typeof e?.detail === "string" ? e.detail : e?.message) || "That didn't save.";

export { L, F, alpha };
