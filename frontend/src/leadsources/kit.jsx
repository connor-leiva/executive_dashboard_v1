/* The lead-source reports' own small pieces, composed from the dashboard's primitives (panels.jsx)
   and theme tokens. Formatting follows the product's rules: whole dollars in tables, accounting
   parentheses for a negative (the P&L's `signed`), sentence-case labels, and every rate written with
   what it is a rate of. Nothing here computes a metric -- the server sends them decided. */
import { T, compactUsd, usd, signed } from "../theme.js";
import { Icon } from "../Brand.jsx";
import { Card, Skel, SkeletonCard } from "../panels.jsx";

export const FD = "var(--font-display)";
export const FB = "var(--font-text)";
export const FM = "var(--font-data)";

export const fmt = {
  money: (n) => (n == null ? "—" : signed(n)),
  plainMoney: (n) => (n == null ? "—" : usd(n)),
  compactMoney: (n) => compactUsd(n),
  pct: (n) => (n == null ? "—" : `${Number.isInteger(n) ? n : Number(n).toFixed(1)}%`),
  mult: (n) => (n == null ? "—" : `${Number(n).toFixed(2)}×`),
  count: (n) => (n == null ? "—" : Number(n).toLocaleString("en-US")),
  minutes: (m) => {
    if (m == null) return "—";
    if (m < 60) return `${m < 10 ? Number(m).toFixed(1).replace(/\.0$/, "") : Math.round(m)} min`;
    if (m < 1440) return `${(m / 60).toFixed(1).replace(/\.0$/, "")} hr`;
    return `${(m / 1440).toFixed(1).replace(/\.0$/, "")} days`;
  },
  seconds: (sec) => (sec == null ? "—" : `${Math.floor(sec / 60)}:${String(Math.round(sec % 60)).padStart(2, "0")}`),
  days: (d) => (d == null ? "—" : `${Number(d).toFixed(1).replace(/\.0$/, "")} days`),
  pp: (n) => (n == null ? "" : `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(Math.round(n))} pts`),
  // A gap to benchmark as a phrase: "51 pts below", "4 pts above", "at benchmark".
  gap: (n) => {
    if (n == null) return "";
    const r = Math.abs(Math.round(n));
    return r === 0 ? "at benchmark" : `${r} pts ${n < 0 ? "below" : "above"}`;
  },
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const monthLabel = (ym) => (ym ? `${MONTHS[Number(ym.slice(5, 7)) - 1]} ${ym.slice(0, 4)}` : "");
export const dayLabel = (iso) => (iso ? `${MONTHS[Number(iso.slice(5, 7)) - 1]} ${Number(iso.slice(8, 10))}` : "—");

/* Table cells in the transactions panel's measure (TransactionsPanel.jsx): 12px muted headers,
   13px tabular figures, a hairline under each row. */
export const th = { fontFamily: FB, fontWeight: 600, fontSize: 12, color: T.muted, padding: "6px 8px",
                    borderBottom: `1px solid ${T.line}`, textAlign: "right", whiteSpace: "nowrap", background: T.white };
export const td = { fontFamily: FB, padding: "8px 8px", fontSize: 13, color: T.ink, textAlign: "right",
                    fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap", borderBottom: `1px solid ${T.line}` };
export const small = { fontFamily: FB, fontSize: 12, color: T.muted, lineHeight: 1.5 };

/* RFC 4180-ish: quote every field, double any quote. The rows on screen, nothing more. */
export function downloadCsv(filename, columns, rows) {
  const esc = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const body = [columns.map((c) => esc(c.label)).join(",")]
    .concat(rows.map((r) => columns.map((c) => esc(typeof c.value === "function" ? c.value(r) : r[c.key])).join(",")))
    .join("\r\n");
  const url = URL.createObjectURL(new Blob([body], { type: "text/csv;charset=utf-8" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* The one action in a card's header, in the Flywheel's "View the list" look: white, outlined, the
   display face, with its glyph. */
export function OutlineButton({ onClick, disabled, icon, children, title }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} title={title} style={{
      display: "inline-flex", alignItems: "center", gap: 6, background: T.white, border: `1px solid ${T.line}`,
      borderRadius: 8, padding: "6px 12px", cursor: disabled ? "default" : "pointer", whiteSpace: "nowrap",
      fontFamily: FD, fontSize: 12, fontWeight: 600, color: disabled ? T.muted : T.slate }}>
      {icon && <Icon name={icon} size={12} color={disabled ? T.muted : T.slate} />}{children}
    </button>
  );
}

export function ExportButton({ onClick, disabled }) {
  return <OutlineButton icon="download" onClick={onClick} disabled={disabled}>Export CSV</OutlineButton>;
}

/* A card's header row: the accent-ruled label on the left, chips and actions on the right. */
export function CardHead({ children, right }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 14 }}>
      {children}
      {right && <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>{right}</span>}
    </div>
  );
}

/* A boxed sentence for something the reader should know before trusting a number: gold for a
   gap in the data, neutral for a caveat. The same treatment as the dashboard's sample banner. */
export function Note({ children, tone = "gold" }) {
  const t = tone === "gold" ? { bg: T.daffodilBg, edge: T.daffodil } : { bg: T.parchment, edge: T.line };
  return (
    <div role="note" style={{ boxSizing: "border-box", background: t.bg, border: `1px solid ${t.edge}`, borderRadius: 12,
      padding: "12px 16px", fontFamily: FB, fontSize: 13, color: T.ink, lineHeight: 1.5 }}>{children}</div>
  );
}

/* Loading, error and empty, in the dashboard's own states: the shimmer skeleton, the "couldn't
   reach" card with Retry, and a centred empty card that says what would fill it. */
export function ReportSkeleton() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }} aria-busy="true">
      <div className="cc-score">{[0, 1, 2, 3].map((i) => <SkeletonCard key={i} />)}</div>
      <Skel h={280} r={14} />
      <Skel h={180} r={14} />
    </div>
  );
}

/* What went wrong, in the words that fit it: no answer at all is the network; a 5xx answered and
   failed; a 403 is access; anything else, the server's own reason. */
function errorWords(error) {
  if (!error || !error.status) return "The dashboard couldn't reach its server. Check the connection, then retry.";
  if (error.status === 403) return "Your role can't see this brokerage's lead-source reports. An owner or admin can give access.";
  if (error.status >= 500) return "The server answered with an error while building it. Retrying usually works.";
  return typeof error.detail === "string" && error.detail ? error.detail : `The server refused it (${error.status}).`;
}

export function ReportError({ onRetry, error }) {
  return (
    <Card style={{ textAlign: "center", padding: 40 }}>
      <div style={{ fontFamily: FD, fontSize: 16, fontWeight: 600, color: T.ink }}>Couldn't load this report</div>
      <div style={{ fontFamily: FB, fontSize: 13, color: T.muted, marginTop: 6, maxWidth: 520, marginInline: "auto", lineHeight: 1.5 }}>{errorWords(error)}</div>
      <button onClick={onRetry} className="cc-nav" style={{ marginTop: 16, fontFamily: FB, fontSize: 13, fontWeight: 600,
        color: T.onDark, background: T.evergreen, border: "none", borderRadius: 8, padding: "9px 16px", cursor: "pointer" }}>Retry</button>
    </Card>
  );
}

export function ReportEmpty({ title, children, action }) {
  return (
    <Card style={{ textAlign: "center", padding: "44px 28px" }}>
      <div style={{ fontFamily: FD, fontSize: 16, fontWeight: 600, color: T.ink }}>{title}</div>
      <div style={{ fontFamily: FB, fontSize: 13, color: T.muted, marginTop: 8, lineHeight: 1.55, maxWidth: 520, marginInline: "auto" }}>{children}</div>
      {action && <div style={{ marginTop: 16 }}>{action}</div>}
    </Card>
  );
}

/* Tier and maturity chips in the dashboard's pill tones (books/ui.jsx Pill), keyed by meaning. */
export const TIER_TONE = { critical: "bad", needs_coaching: "warn", on_track: "muted", strong: "good" };
export const TIER_DOT = { critical: T.poppyText, needs_coaching: T.daffodil, on_track: T.mist, strong: T.meadow };
