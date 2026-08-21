/* Books · Statement — the P&L read as a ledger (SPEC-coa-mapping-provenance 7, refreshed to
   Connor's design canvas "Books Statement.dc.html", 2026-08-21).

   NO ARITHMETIC LIVES HERE. Every figure is the server's: `line.amount` for the active mode,
   `bucket.total`, `section.total`, `totals.*`. The only derived value on this screen is
   percent-of-revenue, which is a ratio of two numbers the server already sent. The refresh was
   visual; the maths is untouched, and it needs to stay that way.

   A flagged row is one whose number was composed rather than observed — part of it was funded
   by another entity. The treatment is a gold wash, a solid left edge, a ringed marker before
   the account name, and the intercompany share in its own column.

   THE MARKER IS NOT OPTIONAL. Roughly one man in twelve has some colour vision deficiency, and
   a faint tint on a cream ground is low contrast for everyone. Meaning never lives in colour
   alone — the ring, the note and the share column each carry it independently. */
import { useMemo, useState } from "react";
import { useCoaEntities, useStatement, useLineDetail } from "./useBooks.js";
import { StatePanel } from "./ui.jsx";
import { LEDGER as L, LEDGER_FONT as F, alpha } from "../theme.js";

/* Round the MAGNITUDE, not the signed value: Math.round(-0.5) is -0 while Math.round(0.5) is
   1, so rounding first would print two mirrored amounts a dollar apart. A rounded-to-nothing
   figure prints "$0", never "($0)". */
const money = (n) => {
  const v = Math.round(Math.abs(Number(n) || 0));
  const neg = Number(n) < 0 && v !== 0;
  return (neg ? "(" : "") + "$" + v.toLocaleString("en-US") + (neg ? ")" : "");
};

/* The tie-out is the one figure on this screen that cannot be read in whole dollars: the
   tolerance is a CENT, so every break the check actually cares about would print as "$0". */
const cents = (n) =>
  "$" + (Number(n) || 0).toLocaleString("en-US",
    { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/* Percent of net revenue. An em dash rather than a number whenever there is nothing sound to
   divide by — including NEGATIVE revenue, which is reachable (contra-revenue sits inside the
   revenue section, so a period whose refunds beat its billings nets below zero) and which
   would otherwise flip the sign of every percentage on the statement. */
const pctOf = (n, revenue) =>
  revenue > 0 ? (n / revenue * 100).toFixed(1) + "%" : "—";
const ratioOf = (n, revenue) => (revenue > 0 ? n / revenue : 0);

const NUM = { fontFamily: F.num, fontVariantNumeric: "tabular-nums", textAlign: "right",
              whiteSpace: "nowrap" };
const EYEBROW = { fontFamily: F.num, fontSize: 10, letterSpacing: ".14em",
                  textTransform: "uppercase", color: L.faint };

const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function periodLabel(p) {
  if (!p?.start || !p?.end) return "";
  const [ys, ms, ds] = p.start.split("-").map(Number);
  const [ye, me, de] = p.end.split("-").map(Number);
  const left = `${MONTH[ms - 1]} ${ds}`;
  const right = `${MONTH[me - 1]} ${de}, ${ye}`;
  return ys === ye ? `${left} – ${right}` : `${left}, ${ys} – ${right}`;
}

/* The ledger's three columns. A class rather than an inline style so one media query can drop
   the percent column and tighten the money column on a phone — the alternative is measuring
   the viewport in JS, which is a worse way to answer the same question. */
const ROW = "st-row";

function Chip({ on, children, onClick, dark }) {
  return (
    <button onClick={onClick} style={{
      height: 28, padding: "0 13px", borderRadius: 999, cursor: "pointer",
      fontFamily: F.body, fontSize: 13, fontWeight: on ? 600 : 500, whiteSpace: "nowrap",
      border: dark
        ? `1px solid ${on ? L.paper : L.onDeepPillEdge}`
        : `1px solid ${on ? L.deep : L.pillEdge}`,
      background: dark ? (on ? L.paper : "transparent") : (on ? L.deep : L.paper),
      color: dark ? (on ? L.deep : L.onDeepPill) : (on ? L.onDeep : L.bodyDim),
    }}>{children}</button>
  );
}

/* ── the composed-line panel (SPEC 6.5, 7.4) ──────────────────────────────────────────── */

function Composition({ line, mode }) {
  // Own activity + what was allocated in = the allocated figure, ALWAYS. Which of the two the
  // row above is showing depends on the basis, so both totals are labelled and the one on
  // screen is marked. Showing only "as it appears above" made the panel fail to add up in
  // booked mode: own + inbound is the allocated number, never the booked one.
  const shownIsBooked = mode === "booked";
  const here = (
    <span style={{ fontWeight: 600, color: L.flagInk }}> · shown above</span>
  );
  return (
    <div style={{ marginBottom: 16 }}>
      <div style={EYEBROW}>How this number is made</div>
      <div style={{ marginTop: 8, display: "grid", gap: 6 }}>
        <div style={{ display: "flex", gap: 12, fontFamily: F.body, fontSize: 12.5 }}>
          <span style={{ flex: 1, color: L.bodyDim }}>
            This entity&rsquo;s own activity{shownIsBooked ? here : null}</span>
          <span style={{ ...NUM, color: L.deep, minWidth: 110 }}>{money(line.as_booked)}</span>
        </div>
        {(line.contributions || []).map((c, i) => (
          <div key={i} style={{ display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
            <span style={{ flex: 1, minWidth: 200, fontFamily: F.body, fontSize: 12.5,
                           color: L.bodyDim }}>
              {/* The word, not only the sign: direction must never rest on a minus alone. */}
              <strong style={{ color: L.deep }}>
                {c.direction === "in" ? "Allocated in" : "Allocated out"}
              </strong>{" "}
              {c.direction === "in" ? "from" : "to"} {c.counterparty_business}
              <span style={{ color: L.muted }}>
                {" · "}{c.pool_name}
                {c.basis ? ` · ${c.basis}` : " · no stated basis"}
                {c.driver_source ? ` · ${c.driver_source}` : ""}
                {c.approved_by ? ` · approved by ${c.approved_by}` : ""}
                {c.je_ref ? ` · ${c.je_ref}` : ""}
              </span>
            </span>
            <span style={{ ...NUM, fontSize: 12.5, color: L.flagInk, minWidth: 110 }}>
              {money(c.amount)}</span>
          </div>
        ))}
        <div style={{ display: "flex", gap: 12, borderTop: `1px solid ${L.ruleInner}`,
                      paddingTop: 6 }}>
          <span style={{ flex: 1, fontFamily: F.body, fontSize: 12.5, fontWeight: 600,
                         color: L.deep }}>
            As allocated{shownIsBooked ? null : here}</span>
          <span style={{ ...NUM, fontSize: 12.5, fontWeight: 700, color: L.deep, minWidth: 110 }}>
            {money(line.as_allocated)}</span>
        </div>
      </div>
    </div>
  );
}

/* ── the audit trail (accounts, then transactions) ────────────────────────────────────── */

function LineDetail({ line, businessId, period, mode }) {
  const { data, error, loading, retry } = useLineDetail(
    businessId, line.standard_account_id, period);

  if (loading) {
    return <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.muted }}>
      Loading the transactions behind this line…</div>;
  }
  if (error) {
    return (
      <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.flagInk }}>
        Couldn&rsquo;t load the detail.{" "}
        <button onClick={retry} style={{ height: 24, padding: "0 10px", borderRadius: 8,
          cursor: "pointer", fontFamily: F.body, fontSize: 12,
          border: `1px solid ${L.pillEdge}`, background: L.paper, color: L.bodyDim }}>Retry</button>
      </div>
    );
  }
  if (!data) return null;
  const rec = data.reconciliation || {};
  // The early-return path on the server sends a reconciliation carrying only a note, so the
  // figures have to be proven present rather than assumed — money(undefined) is "$NaN".
  const hasFigures = typeof rec.line_total === "number"
                  && typeof rec.transaction_total === "number";

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div>
        <div style={EYEBROW}>QuickBooks accounts in this line</div>
        <div style={{ marginTop: 6 }}>
          {(data.accounts || []).map((a) => (
            <div key={a.qbo_account_id} style={{ display: "flex", gap: 12, alignItems: "baseline",
              padding: "4px 0", flexWrap: "wrap" }}>
              <span style={{ flex: 1, minWidth: 200, fontFamily: F.body, fontSize: 12.5,
                             color: L.body, overflowWrap: "anywhere" }}>
                {a.fqn}
                <span style={{ color: L.muted }}>
                  {" · "}{a.type}{a.mapped_via ? ` · mapped by ${a.mapped_via}` : ""}
                  {" · "}{a.transactions} {a.transactions === 1 ? "txn" : "txns"}
                </span>
              </span>
              <span style={{ ...NUM, fontSize: 12.5, color: L.deep, minWidth: 110 }}>
                {money(a.amount)}</span>
            </div>
          ))}
        </div>
      </div>

      <div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline",
                      gap: 12, flexWrap: "wrap" }}>
          <span style={EYEBROW}>Transactions</span>
          <span style={{ fontFamily: F.body, fontSize: 11.5, color: L.ghost }}>
            biggest first{data.truncated ? ` · ${data.truncated} more not shown` : ""}
          </span>
        </div>
        <div style={{ marginTop: 6, maxHeight: 320, overflowY: "auto" }}>
          {(data.transactions || []).length === 0 && (
            <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.muted, padding: "6px 0" }}>
              No transactions in this window.
            </div>
          )}
          {(data.transactions || []).map((t) => (
            <div key={t.id} style={{ display: "flex", gap: 10, alignItems: "baseline",
              padding: "5px 0", borderTop: `1px solid ${L.ruleLine}`, flexWrap: "wrap" }}>
              <span style={{ fontFamily: F.num, fontSize: 11.5, color: L.ghost,
                             fontVariantNumeric: "tabular-nums", width: 76, flexShrink: 0 }}>
                {t.date}</span>
              <span style={{ flex: 1, minWidth: 180, fontFamily: F.body, fontSize: 12.5,
                             color: L.body, overflowWrap: "anywhere" }}>
                {t.payee || t.qbo_type}
                {t.memo ? <span style={{ color: L.muted }}> · {t.memo}</span> : null}
                <span style={{ color: L.muted }}> · {t.account}</span>
                {/* Named because it is the main reason this list may not add up. */}
                {t.multi_line && (
                  <span style={{ color: L.flagInk }}> · multi-line, shown at full amount</span>
                )}
              </span>
              <span style={{ ...NUM, fontSize: 12.5, color: L.deep, minWidth: 96 }}>
                {money(t.amount)}</span>
              {t.qbo_url && (
                <a href={t.qbo_url} target="_blank" rel="noreferrer"
                   onClick={(e) => e.stopPropagation()}
                   style={{ fontFamily: F.body, fontSize: 11.5, fontWeight: 600,
                            color: L.accentInk, textDecoration: "none", flexShrink: 0 }}>Open ↗</a>
              )}
            </div>
          ))}
        </div>
      </div>

      <div style={{ borderTop: `1px solid ${L.ruleInner}`, paddingTop: 8, display: "flex",
                    gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
        {hasFigures && (
        <span style={{ display: "flex", alignItems: "center", gap: 7, padding: "4px 11px",
          borderRadius: 999, whiteSpace: "nowrap", fontFamily: F.body, fontSize: 12,
          fontWeight: 500,
          background: rec.explained ? L.accentBg : L.flagBg,
          color: rec.explained ? L.accentInk : L.flagInk }}>
          <span style={{ width: 6, height: 6, borderRadius: "50%",
            background: rec.explained ? L.accent : L.flagRing }} />
          {/* Direction matters and a sign will not carry it: positive means the transactions
              do not reach the line, negative means they overshoot — which is what a multi-line
              entry counted at its full header amount looks like. */}
          {rec.explained ? "Transactions account for the line"
            : rec.delta > 0 ? `Transactions short by ${money(Math.abs(rec.delta))}`
                            : `Transactions over by ${money(Math.abs(rec.delta))}`}
        </span>
        )}
        <span style={{ flex: 1, minWidth: 240, fontFamily: F.body, fontSize: 12,
                       color: L.ghost, lineHeight: 1.5 }}>
          {rec.note}
          {/* The transactions are QuickBooks' — they explain the account's whole activity,
              which is the ALLOCATED figure. In booked mode the row above shows less than
              that, so the two are deliberately different numbers and the panel says which. */}
          {mode === "booked" && hasFigures
            ? " Reconciled against the allocated figure, since that is the full activity on"
              + " these accounts; the row above shows this entity's own share of it."
            : ""}
        </span>
        {hasFigures && (
          <span style={{ ...NUM, fontSize: 12, color: L.ghost }}>
            {money(rec.transaction_total)} of {money(rec.line_total)}
          </span>
        )}
      </div>
    </div>
  );
}

/* ── ledger rows ───────────────────────────────────────────────────────────────────────── */

function SectionRow({ label, count, open, total, pct, onToggle }) {
  return (
    <button className={ROW} onClick={onToggle} aria-expanded={open} style={{
      width: "100%", border: 0, borderTop: `1px solid ${L.ruleSection}`,
      borderBottom: `1px solid ${L.ruleInner}`, background: L.sectionBand,
      padding: "12px 20px 12px 18px", cursor: "pointer", textAlign: "left",
      fontFamily: F.body, alignItems: "center",
    }}>
      <span style={{ display: "flex", alignItems: "center", gap: 9, minWidth: 0 }}>
        <span aria-hidden="true" style={{ fontSize: 10, color: L.faint, width: 10, flex: "none" }}>
          {open ? "▾" : "▸"}</span>
        <span style={{ fontFamily: F.num, fontSize: 15.5, fontWeight: 700, letterSpacing: ".02em",
                       color: L.deep }}>{label}</span>
        <span style={{ fontFamily: F.body, fontSize: 11.5, color: L.ghost,
                       whiteSpace: "nowrap" }}>{count}</span>
      </span>
      {/* A collapsed section still has to show what it is worth, or collapsing hides the
          number instead of the detail. */}
      <span style={{ ...NUM, fontSize: 16, fontWeight: 700, color: L.deep,
                     letterSpacing: "-.02em" }}>{open ? "" : money(total)}</span>
      <span className="st-pct" style={{ ...NUM, fontSize: 12, fontWeight: 500, color: L.muted }}>
        {open ? "" : pct}</span>
    </button>
  );
}

function GroupRow({ label, total, pct }) {
  return (
    <div className={ROW} style={{ alignItems: "center", padding: "10px 20px 10px 32px",
      borderBottom: `1px solid ${L.ruleGroup}`, background: L.paper }}>
      <span style={{ fontFamily: F.body, fontSize: 14, fontWeight: 600, color: L.deep,
                     letterSpacing: "-.01em", minWidth: 0 }}>{label}</span>
      <span style={{ ...NUM, fontSize: 14, fontWeight: 600, color: L.deep }}>{money(total)}</span>
      <span className="st-pct" style={{ ...NUM, fontSize: 12, fontWeight: 500, color: L.muted }}>
        {pct}</span>
    </div>
  );
}

/* Every line expands, not only the composed ones. A number you cannot open is a number you
   have to take on trust, and the point of this statement is that you do not have to. */
function LineRow({ line, revenue, open, onToggle, businessId, period, mode }) {
  const on = line.flagged;
  return (
    <>
      <div className={`${ROW} st-line${on ? " st-flagged" : ""}`}
        role="button" tabIndex={0} aria-expanded={open}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggle(); }
        }}
        title={line.definition || undefined}
        style={{
          alignItems: "center", padding: "8px 20px 8px 32px",
          borderBottom: `1px solid ${L.ruleLine}`,
          boxShadow: on ? `inset 3px 0 0 ${L.flagEdge}` : "none",
          cursor: "pointer",
        }}>
        <span style={{ display: "flex", alignItems: "center", gap: 9, minWidth: 0 }}>
          {/* The non-colour carrier of the same meaning. */}
          <span aria-hidden="true" style={on
            ? { width: 7, height: 7, borderRadius: "50%", border: `2px solid ${L.flagRing}`,
                flex: "none", boxSizing: "border-box" }
            : { width: 7, height: 7, flex: "none" }} />
          <span style={{ fontFamily: F.num, fontSize: 12, color: L.code, flex: "none",
                         width: 38, fontVariantNumeric: "tabular-nums" }}>{line.code}</span>
          {/* Name and note are ONE run so the ellipsis can eat the note before the name, and
              so neither can shove the money column off a narrow screen. The share reads only
              on the allocated basis, where it is the share of the number actually shown. */}
          <span style={{ fontFamily: F.body, fontSize: 13.5, color: L.body, whiteSpace: "nowrap",
                         overflow: "hidden", textOverflow: "ellipsis", minWidth: 0 }}>
            {line.name}
            {on && (
              <span style={{ color: L.flagInk }}>
                {" · "}part funded by another entity
                {mode !== "booked" && line.ic_share_pct !== null
                  && line.ic_share_pct !== undefined
                  ? ` · ${line.ic_share_pct.toFixed(1)}% funded` : ""}
              </span>
            )}
          </span>
          <span aria-hidden="true" style={{ fontSize: 9, color: L.code, flex: "none" }}>
            {open ? "▾" : "▸"}</span>
        </span>
        <span style={{ ...NUM, fontSize: 13.5, fontWeight: on ? 600 : 400,
                       color: on ? L.deep : L.body }}>{money(line.amount)}</span>
        <span className="st-pct" style={{ ...NUM, fontSize: 12,
                                          color: on ? L.flagInk : L.ghost }}>
          {pctOf(line.amount, revenue)}</span>
      </div>
      {open && (
        <div className="st-drawer" style={{ background: L.totalBand,
          borderBottom: `1px solid ${L.ruleInner}`,
          boxShadow: `inset 3px 0 0 ${on ? L.flagEdge : L.ruleSection}`,
          padding: "14px 20px 16px 32px" }}>
          {on && <Composition line={line} mode={mode} />}
          <LineDetail line={line} businessId={businessId} period={period} mode={mode} />
        </div>
      )}
    </>
  );
}

function TotalRow({ label, value, pct, tone }) {
  const big = tone === "big", strong = tone === "strong";
  return (
    <div className={ROW} style={{
      alignItems: "center",
      padding: big ? "16px 20px 16px 18px" : "12px 20px 12px 18px",
      background: big ? L.deep : L.totalBand,
      borderTop: big ? "none" : (strong ? `1.5px solid ${L.deep}` : `1px solid ${L.ruleInner}`),
      borderBottom: big ? "none" : `1px solid ${L.ruleInner}`,
    }}>
      <span style={{ fontFamily: F.body, fontSize: big ? 16 : strong ? 15 : 14,
        fontWeight: big || strong ? 700 : 600, letterSpacing: "-.01em",
        color: big ? L.onDeep : strong ? L.deep : L.bodyDim }}>{label}</span>
      <span style={{ ...NUM, fontSize: big ? 22 : strong ? 17 : 15, fontWeight: 700,
        letterSpacing: "-.02em",
        color: big ? L.paper : strong ? L.deep : L.body }}>{money(value)}</span>
      <span className="st-pct" style={{ ...NUM, fontSize: 12, fontWeight: 500,
        color: big ? L.onDeepPct : L.muted }}>{pct}</span>
    </div>
  );
}

/* What the statement says when it refuses to render (SPEC 5.3, 5.4). Both refusals are
   deliberate and both are fixable by a person, so both name what to do. */
function Blocked({ detail }) {
  const unmapped = detail.error === "unmapped_accounts";
  return (
    <div style={{ padding: "22px 26px 26px" }}>
      <div style={{ border: `1px solid ${L.flagEdge}`, borderRadius: 13, background: L.flagBg,
                    padding: "16px 18px" }}>
        <div style={{ ...EYEBROW, color: L.flagInk }}>
          {unmapped ? "Not rendered — accounts have nowhere to go"
                    : "Not rendered — the mapped total does not tie"}</div>
        <div style={{ fontFamily: F.body, fontSize: 13.5, color: L.deep, marginTop: 8,
                      lineHeight: 1.5 }}>{detail.message}</div>
        {unmapped && (detail.accounts || []).length > 0 && (
          <div style={{ marginTop: 12 }}>
            {detail.accounts.slice(0, 12).map((a) => (
              <div key={a.qbo_account_id} style={{ display: "flex", gap: 12, padding: "4px 0",
                borderTop: `1px solid ${alpha(L.flagRing, 0.35)}`, flexWrap: "wrap" }}>
                <span style={{ flex: 1, minWidth: 200, fontFamily: F.body, fontSize: 12.5,
                               color: L.body, overflowWrap: "anywhere" }}>
                  {a.fqn} <span style={{ color: L.flagInk }}>· {a.reason}</span></span>
                <span style={{ ...NUM, fontSize: 12.5, color: L.deep }}>{money(a.amount)}</span>
              </div>
            ))}
            {detail.accounts.length > 12 && (
              <div style={{ fontFamily: F.body, fontSize: 12, color: L.flagInk, marginTop: 6 }}>
                …and {detail.accounts.length - 12} more.</div>
            )}
            <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.bodyDim, marginTop: 10 }}>
              Map them on the Mapping tab and this statement renders.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── the screen ────────────────────────────────────────────────────────────────────────── */

export default function BooksStatement({ period = "mtd" }) {
  const entities = useCoaEntities();
  const [bizId, setBizId] = useState(null);
  const [mode, setMode] = useState("allocated");
  const [threshold, setThreshold] = useState(null);      // null = the tenant's own setting
  const [expanded, setExpanded] = useState(null);
  const [closed, setClosed] = useState({});              // section key -> collapsed

  const list = entities.data?.entities || [];
  const active = bizId || list[0]?.id || null;
  const view = useStatement(active, { period, mode, threshold });
  const d = view.data;

  const flaggedCount = useMemo(() => {
    if (!d) return 0;
    return d.sections.reduce((n, sec) => n + sec.buckets.reduce(
      (m, b) => m + b.lines.filter((l) => l.flagged).length, 0), 0);
  }, [d]);

  /* The guard and the tie-out answer 409 with a structured, actionable payload. Letting that
     fall through to the generic "Couldn't load this view" panel throws away the entire point
     of refusing to render — and offers a Retry that can never succeed. */
  const blocked = view.error?.status === 409 && view.error?.detail?.error
    ? view.error.detail : null;

  const revenue = d?.totals?.net_revenue ?? 0;
  const t = d?.totals || {};
  const gpLabel = d?.business?.gross_profit_label || "Gross Profit";

  /* Where each calculated line closes. Emitted whether or not its section is present, so an
     entity with no cost of sale still shows a gross-profit line rather than skipping it. */
  const CLOSERS = {
    revenue: [["Net Revenue", t.net_revenue, "strong"]],
    cogs: [["Total Cost of Sale", t.cost_of_sale, null], [gpLabel, t.gross_profit, "strong"]],
    opex: [["Total Operating Expenses", t.operating_expenses, null],
           ["Net Operating Income", t.net_operating_income, "strong"]],
    other_expense: [["Below the Line", t.below_the_line, null],
                    ["Net Income", t.net_income, "big"]],
  };
  const ORDER = ["revenue", "cogs", "opex", "other_income", "other_expense"];

  const summary = d ? [
    // Derived, not asserted: "100%" beside $0 of revenue is a claim about nothing.
    { label: "Net Revenue", value: t.net_revenue, pct: pctOf(t.net_revenue, revenue),
      ratio: revenue > 0 ? 1 : 0, tone: L.deep },
    { label: gpLabel, value: t.gross_profit, pct: pctOf(t.gross_profit, revenue),
      ratio: ratioOf(t.gross_profit, revenue), tone: L.accentInk },
    { label: "Operating Expenses", value: t.operating_expenses,
      pct: pctOf(t.operating_expenses, revenue),
      ratio: ratioOf(t.operating_expenses, revenue), tone: L.accentMuted },
    { label: "Net Income", value: t.net_income, pct: pctOf(t.net_income, revenue),
      ratio: Math.max(ratioOf(t.net_income, revenue), 0), tone: L.accent },
  ] : [];

  const rows = [];
  if (d) {
    const byKey = Object.fromEntries(d.sections.map((s) => [s.key, s]));
    ORDER.forEach((key) => {
      const sec = byKey[key];
      if (sec) {
        const open = !closed[key];
        const lineCount = sec.buckets.reduce((n, b) => n + b.lines.length, 0);
        rows.push(
          <SectionRow key={`s-${key}`} label={sec.label} open={open} total={sec.total}
            pct={pctOf(sec.total, revenue)}
            count={`${lineCount} ${lineCount === 1 ? "account" : "accounts"}`}
            onToggle={() => setClosed((p) => ({ ...p, [key]: open }))} />
        );
        if (open) {
          sec.buckets.forEach((b) => {
            rows.push(<GroupRow key={`g-${key}-${b.key}`} label={b.label} total={b.total}
                                pct={pctOf(b.total, revenue)} />);
            b.lines.forEach((l) => {
              const id = l.standard_account_id;
              rows.push(
                <LineRow key={`l-${id}`} line={l} revenue={revenue} businessId={active}
                  period={period} mode={mode} open={expanded === id}
                  onToggle={() => setExpanded(expanded === id ? null : id)} />
              );
            });
          });
        }
      }
      (CLOSERS[key] || []).forEach(([label, value, tone]) => {
        if (value === undefined || value === null) return;
        rows.push(<TotalRow key={`t-${label}`} label={label} value={value} tone={tone}
                            pct={pctOf(value, revenue)} />);
      });
    });
  }

  return (
    <StatePanel loading={entities.loading} error={entities.error} retry={entities.retry}
      empty={!entities.loading && list.length === 0}
      emptyTitle="No entities yet"
      emptyMsg="Connect a QuickBooks company in Settings and the statement builds on the next sync.">

      <div style={{ border: `1px solid ${L.cardEdge}`, borderRadius: 16, overflow: "hidden",
        background: L.card, boxShadow: `0 16px 38px -26px ${alpha(L.deep, 0.5)}` }}>

        {/* entity bar */}
        <div style={{ background: L.deep, padding: "11px 26px", display: "flex",
                      alignItems: "center", gap: 14, flexWrap: "wrap" }}>
          <span style={{ ...EYEBROW, color: L.onDeepEyebrow, flex: "none" }}>Entity</span>
          <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            {list.map((e) => (
              <Chip key={e.id} dark on={e.id === active}
                onClick={() => { setBizId(e.id); setExpanded(null); }}>{e.name}</Chip>
            ))}
          </div>
          <div style={{ flex: 1 }} />
          <span style={{ fontFamily: F.num, fontSize: 11, color: L.onDeepEyebrow,
                         whiteSpace: "nowrap" }}>{d ? periodLabel(d.period) : ""}</span>
        </div>

        {blocked ? <Blocked detail={blocked} /> : null}
        <StatePanel loading={view.loading} error={blocked ? null : view.error} retry={view.retry}>
          {d && (
            <>
              {/* controls */}
              <div style={{ background: L.controls, borderBottom: `1px solid ${L.ruleHeader}`,
                padding: "13px 26px", display: "flex", alignItems: "center", gap: 16,
                flexWrap: "wrap" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 3,
                  background: L.sectionBand, borderRadius: 10, padding: 3 }}>
                  {[["As allocated", "allocated"], ["As booked", "booked"]].map(([label, v]) => (
                    <button key={v} onClick={() => setMode(v)} style={{
                      height: 32, padding: "0 15px", border: 0, borderRadius: 8, cursor: "pointer",
                      fontFamily: F.body, fontSize: 13.5, fontWeight: mode === v ? 600 : 500,
                      whiteSpace: "nowrap",
                      background: mode === v ? L.deep : "transparent",
                      color: mode === v ? L.onDeep : L.bodyDim,
                      boxShadow: mode === v ? `0 2px 6px -3px ${alpha(L.deep, 0.65)}` : "none",
                    }}>{label}</button>
                  ))}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontFamily: F.body, fontSize: 13, color: L.muted }}>Flag at</span>
                  <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    {/* Squarer and smaller than the entity chips on purpose: these are a
                        setting, not a subject. Routing them through Chip made them read as
                        peers of the entity selector. */}
                    {[["Any", 0], ["5%", 5], ["10%", 10]].map(([label, v]) => {
                      const on = threshold === v
                        || (threshold === null && v === d.threshold_pct);
                      return (
                        <button key={label} onClick={() => setThreshold(v)} style={{
                          height: 28, padding: "0 11px", borderRadius: 8, cursor: "pointer",
                          fontFamily: F.body, fontSize: 12.5, fontWeight: on ? 600 : 500,
                          border: `1px solid ${on ? L.deep : L.pillEdge}`,
                          background: on ? L.deep : L.paper,
                          color: on ? L.onDeep : L.bodyDim,
                        }}>{label}</button>
                      );
                    })}
                  </div>
                </div>
                <div style={{ flex: 1 }} />
                <span style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 11px",
                  borderRadius: 999, whiteSpace: "nowrap", fontFamily: F.body, fontSize: 12.5,
                  fontWeight: 500,
                  background: d.tie_out.delta === 0 ? L.accentBg : L.flagBg,
                  color: d.tie_out.delta === 0 ? L.accentInk : L.flagInk }}>
                  <span style={{ width: 6, height: 6, borderRadius: "50%",
                    background: d.tie_out.delta === 0 ? L.accent : L.flagRing }} />
                  Ties to QuickBooks · delta {cents(d.tie_out.delta)} of{" "}
                  {money(d.tie_out.gross)}
                </span>
                <span style={{ fontFamily: F.num, fontSize: 11, color: L.ghost,
                               whiteSpace: "nowrap" }}>
                  {flaggedCount} composed {flaggedCount === 1 ? "line" : "lines"}
                  {d.period.books_closed ? " · books closed" : ""}
                  {d.period.synced_at ? ` · synced ${d.period.synced_at.slice(0, 10)}` : ""}
                </span>
              </div>

              <div style={{ padding: "20px 26px 26px", display: "flex", flexDirection: "column",
                            gap: 18 }}>

                {/* summary band */}
                <div className="st-summary" style={{ display: "grid", gap: 12 }}>
                  {summary.map((s) => (
                    <div key={s.label} style={{ border: `1px solid ${L.ruleInner}`,
                      borderRadius: 12, background: L.paper, padding: "13px 15px 14px",
                      display: "flex", flexDirection: "column", gap: 6 }}>
                      <div style={{ ...EYEBROW, color: L.ghost }}>{s.label}</div>
                      <div style={{ fontFamily: F.num, fontVariantNumeric: "tabular-nums",
                        fontSize: 23, fontWeight: 500, color: L.deep, letterSpacing: "-.02em" }}>
                        {money(s.value)}</div>
                      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                        <div style={{ flex: 1, height: 5, borderRadius: 999, background: L.track,
                                      overflow: "hidden" }}>
                          <div style={{ width: `${Math.min(Math.max(s.ratio, 0), 1) * 100}%`,
                            height: "100%", borderRadius: 999, background: s.tone }} />
                        </div>
                        <span style={{ fontFamily: F.num, fontSize: 11, color: L.faint,
                                       flex: "none" }}>{s.pct}</span>
                      </div>
                    </div>
                  ))}
                </div>

                {/* the ledger */}
                <div style={{ border: `1px solid ${L.ruleInner}`, borderRadius: 13,
                              background: L.paper, overflow: "hidden" }}>
                  <div className={ROW} style={{ alignItems: "center",
                    padding: "9px 20px 9px 18px", background: L.colHead,
                    borderBottom: `1px solid ${L.ruleHeader}` }}>
                    <span style={EYEBROW}>Account</span>
                    <span style={{ ...EYEBROW, textAlign: "right" }}>
                      {d.mode === "allocated" ? "Allocated" : "Booked"}</span>
                    <span className="st-pct" style={{ ...EYEBROW, textAlign: "right" }}>% rev</span>
                  </div>
                  {rows}
                </div>

                <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.ghost,
                              lineHeight: 1.55, textWrap: "pretty" }}>
                  {d.mode === "allocated"
                    ? `Shown as allocated. As booked, net income is ${money(d.net_income_booked)}`
                    : `Shown as booked. As allocated, net income is ${money(d.net_income_allocated)}`}
                  {flaggedCount ? ` — ${flaggedCount} composed ${flaggedCount === 1 ? "line is" : "lines are"} part funded by another entity.` : "."}
                  {" "}{d.caveat}
                </div>

                {d.exclusions?.length > 0 && (
                  <div style={{ border: `1px solid ${L.ruleInner}`, borderRadius: 12,
                                background: L.paper, padding: "13px 16px 14px" }}>
                    <div style={EYEBROW}>Left out of this statement</div>
                    <div style={{ marginTop: 8 }}>
                      {d.exclusions.map((x) => (
                        <div key={x.qbo_account_id} style={{ display: "flex", gap: 12,
                          fontFamily: F.body, fontSize: 12.5, padding: "5px 0",
                          flexWrap: "wrap" }}>
                          <span style={{ flex: 1, minWidth: 180, color: L.body }}>
                            {x.fqn} <span style={{ color: L.muted }}>· {x.reason}</span></span>
                          <span style={{ ...NUM, color: L.deep }}>{money(x.amount)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </StatePanel>
      </div>

      {/* One grid definition, three breakpoints. Row expansion is the only motion, it is under
          150ms, and the wash itself never transitions — a fading highlight on financial data
          reads as instability. */}
      <style>{`
        .${ROW} { display:grid; grid-template-columns: 1fr 160px 82px; gap:14px; }
        .st-line { background:${L.paper}; }
        .st-flagged { background:${L.flagBg}; }
        .st-summary { grid-template-columns: repeat(4, minmax(0,1fr)); }
        .st-line:hover { background:${L.totalBand}; }
        .st-flagged:hover { background:${L.flagBgHover}; }
        .st-drawer { animation: stOpen 130ms ease-out; }
        @keyframes stOpen { from { opacity:0; transform:translateY(-3px); } }
        @media (max-width: 1080px) { .st-summary { grid-template-columns: repeat(2, minmax(0,1fr)); } }
        @media (max-width: 820px) {
          .${ROW} { grid-template-columns: 1fr 116px; gap:10px; }
          .st-pct { display:none; }
        }
        @media (max-width: 520px) { .st-summary { grid-template-columns: minmax(0,1fr); } }
        @media (prefers-reduced-motion: reduce) { .st-drawer { animation: none; } }
      `}</style>
    </StatePanel>
  );
}
