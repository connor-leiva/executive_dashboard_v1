/* Books Cleanup -- a list of transactions, the way the owners read them (Oct 19 review of the mockup):

   * five columns up front -- Date · Payee and the account it was paid from · For (the company) and the
     P&L line it's filed to · What it was · Amount -- under one set of headings;
   * month by month, oldest first, each month with its count and total;
   * a quiet ⋯ on each row (or a click anywhere on it) opens a card with what the columns don't say: what
     the cleanup changed, how QuickBooks had it, the review's note, and a possible second copy.

   Used by the Statement's line drawer (a line's transactions, plus its shares of shared costs grouped
   into one row per month) and by the Transactions page (every transaction in the year).

   NO ARITHMETIC beyond adding up what's on screen: every amount is the server's. The words are the
   uploaded file's (the audit's export tidies payees and descriptions); an older file without them still
   reads, on its QuickBooks payee. */
import { useMemo, useState } from "react";
import { EYEBROW, F, L, NUM, Pill, cents, dayLabel, money, monthName } from "./kit.jsx";
import { ChangeArea, changeLabel, useNames } from "./CleanupTxChange.jsx";
import { AskAbout } from "./CleanupAsk.jsx";

/* ── turning the server's rows into list rows ─────────────────────────────────────────────── */

const paidLine = (r) => [r.money_in ? `Into ${r.paid_from || r.company || ""}` : (r.paid_from || r.company || ""),
                         r.card_holder ? `${r.card_holder}'s card` : ""].filter(Boolean).join(" · ");

const listed = (names) => (names.length <= 3 ? names.join(", ")
  : `${names.slice(0, 2).join(", ")} and ${names.length - 2} more`);
const pctText = (f) => `${(Number(f || 0) * 100).toFixed(1).replace(/\.0$/, "")}%`;
const bare = (s) => String(s || "").replace(/,?\s+(LLC|Inc\.?)$/i, "");

/* A line's detail (GET /cleanup/statement/line) as list rows: each transaction, and the shares, timing,
   dues and partner rows grouped into one row per month per kind, with what they're made of. */
export function rowsFromLine(data, { viewShort, hubShort, short, income, year }) {
  const out = [];
  const groups = new Map();
  for (const r of data.rows || []) {
    if (r.kind === "tx") {
      const full = r.tx_amount != null && Math.abs(Math.abs(r.tx_amount) - Math.abs(r.amount)) > 0.5
        ? Math.abs(r.tx_amount) : null;
      out.push({ id: `${r.key}|${r.month}`, kind: "tx", key: r.key, month: r.month, date: r.date, payee: r.payee,
                 paidLine: paidLine({ ...r, company: short(r.paid_by) }), forWho: (r.brands || [r.brand]).map(short),
                 brands: r.brands || [r.brand], company: r.paid_by, line: data.line, what: r.what || r.label,
                 amount: r.amount, full: r.gone ? null : full, status: r.status, changed: r.changed, note: r.note,
                 qbo: r.qbo, booked: r.booked_as, twins: r.twins || [], change: r.change, was: r.was, gone: r.gone,
                 cleared: r.cleared, moneyIn: r.money_in ?? income, pooled: r.pooled });
    } else if (r.kind === "charge") {
      out.push({ id: `charge|${r.charge_ref}`, kind: "tx", month: r.month, date: r.date, payee: r.who,
                 paidLine: `${short(r.paid_by)} · the owner's charge`, forWho: [short(r.brand)], line: data.line,
                 what: r.label, amount: r.amount, status: "changed",
                 changed: "One of the owner's charges, counted as a business cost (Decisions → the owner's charges).",
                 qbo: { category: r.booked_as }, twins: [] });
    } else {
      const g = r.kind === "share" ? r.src : r.kind;
      const id = `${g}|${r.month}`;
      if (!groups.has(id)) groups.set(id, { g, month: r.month, rows: [] });
      groups.get(id).rows.push(r);
    }
  }
  for (const { g, month, rows } of groups.values()) {
    const amount = rows.reduce((n, r) => n + r.amount, 0);
    if (Math.abs(amount) < 0.005 && rows.every((r) => Math.abs(r.amount) < 0.005)) continue;
    const M = monthName(month);
    const byWho = new Map();
    for (const r of rows) {
      const k = r.who || r.brand || r.label;
      const p = byWho.get(k) || { who: k, amount: 0, pct: 0, pool: r.pool_total };
      p.amount += r.amount;
      p.pct += r.pct || 0;
      byWho.set(k, p);
    }
    const parts = [...byWho.values()].sort((a, b) => Math.abs(b.amount) - Math.abs(a.amount));
    const names = parts.map((p) => bare(p.who));
    let payee = "", what = "", made = [];
    if (g === "ads" || g === "fees") {
      const pct = rows.reduce((n, r) => n + (r.pct || 0), 0);
      payee = g === "ads" ? "Shared ads" : "Stripe fees";
      what = g === "ads"
        ? `${hubShort} paid ${money(rows[0].pool_total)} for ads in ${M}; ${viewShort}'s share is ${pctText(pct)}.`
        : `Stripe's fees in ${M} (${money(rows[0].pool_total)}), split by whose sales they were: ${viewShort}'s is ${pctText(pct)}.`;
      if (parts.length > 1) made = parts.map((p) => ({ label: `${short(p.who)} · ${pctText(p.pct)}`, amount: p.amount }));
    } else if (g === "staff") {
      payee = "Shared staff";
      what = `${listed(names)}: ${hubShort} paid them in ${M}; your splits give ${viewShort} its share of each.`;
      made = parts.map((p) => ({ label: `${p.who} · ${pctText(p.pct)} of ${money(p.pool)}`, amount: p.amount }));
    } else if (g === "item") {
      if (parts.length === 1) {
        payee = names[0];
        what = `${hubShort} paid ${names[0]} in ${M}; ${viewShort}'s share is ${pctText(parts[0].pct)}.`;
      } else {
        payee = "Shared costs";
        what = `${listed(names)}: ${hubShort} paid them in ${M}; ${viewShort}'s share of each.`;
        made = parts.map((p) => ({ label: `${bare(p.who)} · ${pctText(p.pct)}`, amount: p.amount }));
      }
    } else if (g === "timing") {
      payee = income ? (amount < 0 ? "Held for later months" : "Earned from earlier payments")
        : (amount < 0 ? "Moved to another month" : "Moved in from another month");
      what = rows.length === 1 ? rows[0].label
        : income ? "Membership money counted in the months members get the program, not the day it was paid."
          : "Counted in the month it belongs to.";
      if (rows.length > 1) made = rows.map((r) => ({ label: r.label, amount: r.amount }));
    } else if (g === "dues") {
      payee = "Dues earned";
      what = rows[0].label;
    } else if (g === "partner") {
      payee = `${rows[0].who}'s share`;
      what = rows[0].label;
    } else {
      payee = rows[0].label;
    }
    out.push({ id: `adj|${g}|${month}`, kind: "adj", src: g, month, date: null, payee,
               paidLine: ["ads", "fees", "staff", "item"].includes(g) ? `${hubShort} paid it` : "",
               forWho: [viewShort], line: data.line, what, amount, made, status: "", twins: [] });
  }
  return out;
}

/* The year's transactions (GET /cleanup/transactions) as list rows. */
export function rowsFromList(rows, short) {
  return rows.map((r) => {
    const place = r.placement || [];
    const brands = [...new Set(place.filter((p) => p.brand).map((p) => short(p.brand)))];
    const lines = [...new Map(place.map((p) => [p.line, Math.abs(p.amount)])).entries()]
      .sort((a, b) => b[1] - a[1]).map(([l]) => l);
    const was = r.was || null;
    const wasLines = was ? [...new Set(was.map((p) => p.line))] : [];
    return { id: r.key, kind: "tx", key: r.key, month: r.month, date: r.date, payee: r.payee,
             paidLine: paidLine({ ...r, company: short(r.company) }), forWho: brands, company: r.company,
             brands: [...new Set(place.filter((p) => p.brand).map((p) => p.brand))],
             shared: place.some((p) => !p.brand), transfer: place.length === 0,
             line: lines.length > 1 ? `${lines[0]} + ${lines.length - 1} more` : lines[0] || wasLines[0] || "",
             what: r.what, amount: Math.abs(r.amount), moneyIn: r.money_in, status: r.status, changed: r.changed,
             note: r.note, qbo: r.qbo, twins: r.twins || [], change: r.change, cleared: r.cleared,
             wasFor: was ? [...new Set(was.map((p) => (p.brand ? short(p.brand) : "Shared")))] : null };
  });
}

/* ── the pieces ──────────────────────────────────────────────────────────────────────────── */

export function Legend() {
  return (
    <span style={{ display: "inline-flex", gap: 10, flexWrap: "wrap", alignItems: "center", fontFamily: F.body,
                   fontSize: 11.5, fontWeight: 400, color: L.muted }}>
      <span><span className="cx-dot" />changed by the cleanup</span>
      <Pill tone="red">Needs you</Pill>
      <Pill tone="gold">Duplicate?</Pill>
      <span>Click a row for more</span>
    </span>
  );
}

function ForCell({ r }) {
  const { ov } = useNames();
  const struck = { fontSize: 11.5, color: L.ghost, textDecoration: "line-through" };
  if (r.change && (r.gone || r.transfer)) {           // moved off this line, or out of every company's results
    const said = changeLabel(r.change, ov).replace(/^→ /, "Moved to ");
    return (
      <span className="cx-for" style={{ fontFamily: F.body, fontSize: 12.5, color: L.body, lineHeight: 1.45 }}>
        <strong style={{ display: "block", color: L.accentInk, fontWeight: 600 }}>{said}</strong>
        <span style={struck}>For {(r.wasFor || r.forWho).join(", ")}{r.line ? ` · ${r.line}` : ""}</span>
      </span>
    );
  }
  const name = (n) => <strong style={{ color: r.change ? L.accentInk : L.deep, fontWeight: 600 }}>{n}</strong>;
  const who = r.transfer ? name("Transfer")
    : r.shared && !r.forWho.length ? <>For {name("Shared")} <span style={{ fontSize: 11.5, color: L.muted }}>(your splits)</span></>
    : <>For {name(r.forWho.join(", ") || "—")}{r.shared ? <span style={{ fontSize: 11.5, color: L.muted }}> and shared</span> : null}</>;
  return (
    <span className="cx-for" style={{ fontFamily: F.body, fontSize: 12.5, color: L.body, lineHeight: 1.45 }}>
      <span style={{ display: "block" }}>{who}
        {r.change && r.wasFor && r.wasFor.join() !== r.forWho.join() ? (
          <span style={{ ...struck, marginLeft: 6 }}>{r.wasFor.join(", ")}</span>) : null}</span>
      <span style={{ fontSize: 11.5, color: L.muted }}>{r.transfer ? "Not income or a cost" : r.line}</span>
    </span>
  );
}

function TxLine({ r, signed, open, onToggle }) {
  const { ov } = useNames();
  const mark = r.status === "yours" ? <Pill tone="green">{changeLabel(r.change, ov)}</Pill>
    : r.status === "decision" ? <Pill tone="red">Needs you</Pill>
    : r.status === "duplicate" ? <Pill tone="gold">Duplicate?</Pill> : null;
  const inFlow = signed && r.moneyIn;
  return (
    <div className="cx-row" onClick={onToggle} style={open ? { background: L.totalBand } : null}>
      <span className="cx-date" style={{ fontFamily: F.num, fontSize: 12, color: L.faint, paddingTop: 2,
                                        fontVariantNumeric: "tabular-nums" }}>{dayLabel(r.date)}</span>
      <span className="cx-payee" style={{ fontFamily: F.body, fontSize: 13.5, lineHeight: 1.4 }}>
        {r.status === "changed" ? <span className="cx-dot" title="The cleanup changed how this is booked" /> : null}
        <span style={{ fontWeight: 600, color: r.payee ? L.deep : L.muted }}>{r.payee || "Payee not recorded"}</span>
        {r.paidLine ? <span style={{ display: "block", fontSize: 11.5, color: L.muted }}>{r.paidLine}</span> : null}
        {mark ? <span style={{ display: "block", marginTop: 4 }}>{mark}</span> : null}
      </span>
      <ForCell r={r} />
      <span className="cx-what" style={{ fontFamily: F.body, fontSize: 13, color: L.body, lineHeight: 1.45,
                                        overflowWrap: "anywhere" }}>{r.what}</span>
      <span className="cx-amt">
        {r.gone ? (
          <span style={{ ...NUM, display: "block", fontSize: 13.5, fontWeight: 600, color: L.ghost,
                         textDecoration: "line-through" }}>{cents(r.was)}</span>
        ) : (
          <span style={{ ...NUM, display: "block", fontSize: 13.5, fontWeight: 600, color: inFlow ? L.accentInk : L.deep }}>
            {inFlow ? "+" : ""}{cents(r.amount)}</span>
        )}
        {r.full ? <span style={{ ...NUM, display: "block", fontSize: 11, color: L.muted, marginTop: 1 }}>
          of {cents(r.full)}</span> : null}
      </span>
      <button className="cx-dots" aria-label={`More about ${r.payee || "this transaction"}`} aria-expanded={open}
        onClick={(e) => { e.stopPropagation(); onToggle(); }}>⋯</button>
    </div>
  );
}

function Detail({ label, children }) {
  return (
    <>
      <dt style={{ ...EYEBROW, paddingTop: 3 }}>{label}</dt>
      <dd style={{ margin: 0, fontFamily: F.body, fontSize: 13, color: L.body, lineHeight: 1.5 }}>{children}</dd>
    </>
  );
}

/* The card under a row: what the columns don't say. */
function TxCard({ r }) {
  const q = r.qbo || {};
  const plainName = (s) => String(s || "").toLowerCase().replace(/[,.]/g, "").replace(/\s+(llc|inc)$/, "").trim();
  const had = q.category || r.booked
    ? [`Filed to “${q.category || r.booked}”`,
       q.payee && plainName(q.payee) !== plainName(r.payee) ? `payee “${q.payee}”` : ""].filter(Boolean).join(", ")
    : "";
  const facts = [];
  if (r.onPair) facts.push(["Why it's on this pair", r.onPair]);
  if (r.made && r.made.length) {
    facts.push(["Made of", r.made.map((p) => (
      <div key={p.label} style={{ display: "flex", gap: 10, maxWidth: 560 }}>
        <span style={{ flex: 1, minWidth: 0 }}>{p.label}</span>
        <span style={{ ...NUM, color: L.deep }}>{cents(p.amount)}</span></div>))]);
  }
  if (r.changed) facts.push(["The cleanup changed", r.changed]);
  if (had) facts.push(["QuickBooks had it", had]);
  if (r.note && r.note !== r.changed) facts.push(["Note", r.note]);
  if (r.twins && r.twins.length) {
    facts.push([r.status === "checked" ? "Checked" : "Possible duplicate",
      (r.status === "checked" ? "Not a double charge. Same amount: " : "Same amount, a few days apart: ")
      + r.twins.map((w) => `${dayLabel(w.date)} · ${cents(Math.abs(w.amount))}${w.paid_from ? ` · ${w.paid_from}` : ""}`).join("; ")]);
  }
  return (
    <div className="cx-card" onClick={(e) => e.stopPropagation()}>
      {facts.length ? (
        <dl className="cx-kv">{facts.map(([k, v]) => <Detail key={k} label={k}>{v}</Detail>)}</dl>
      ) : (
        <div style={{ marginTop: 10, fontFamily: F.body, fontSize: 12.5, color: L.muted }}>
          Nothing more on file: the cleanup didn&rsquo;t change this one.</div>
      )}
      <ChangeArea r={r} />
      {r.kind === "tx" && r.key ? <AskAbout about={{ kind: "tx", key: r.key }} placeholder="Why is this on this company?" /> : null}
    </div>
  );
}

/* ── the list ────────────────────────────────────────────────────────────────────────────── */

/* `signed`: money in and out in one list (the Transactions page), "+" on money in. Otherwise the amounts
   are one line's (a drawer), as the line counts them, or one pair's (Intercompany), in its reading.
   `noun`: what the month headings count, [one, many]. */
export function TxList({ rows, signed, income, year, noun: nounOf }) {
  const [openId, setOpenId] = useState(null);
  const groups = useMemo(() => {
    const sorted = [...rows].sort((a, b) => (a.month - b.month) || ((a.kind === "adj") - (b.kind === "adj"))
      || String(a.date || "").localeCompare(String(b.date || "")) || String(a.payee).localeCompare(String(b.payee)));
    const out = [];
    for (const r of sorted) {
      const g = out[out.length - 1];
      if (g && g.month === r.month) g.rows.push(r);
      else out.push({ month: r.month, rows: [r] });
    }
    return out;
  }, [rows]);
  const noun = nounOf || (signed ? ["transaction", "transactions"] : income ? ["payment", "payments"] : ["charge", "charges"]);
  return (
    <div className="cx-list">
      <div className="cx-head">
        <span className="cx-date" style={EYEBROW}>Date</span>
        <span className="cx-payee" style={EYEBROW}>Payee · paid from</span>
        <span className="cx-for" style={EYEBROW}>For · filed to</span>
        <span className="cx-what" style={EYEBROW}>What it was</span>
        <span className="cx-amt" style={{ ...EYEBROW, textAlign: "right" }}>Amount</span>
        <span className="cx-dots-h" />
      </div>
      {groups.map((g) => {
        const tx = g.rows.filter((r) => r.kind === "tx").length;
        const adj = g.rows.length - tx;
        const sum = (f) => g.rows.filter(f).reduce((n, r) => n + Math.abs(r.amount), 0);
        const out = signed ? sum((r) => !r.moneyIn) : 0, inn = signed ? sum((r) => r.moneyIn) : 0;
        const count = [tx ? `${tx} ${noun[tx === 1 ? 0 : 1]}` : "", adj ? `${adj} adjustment${adj === 1 ? "" : "s"}` : "",
                       out >= 0.5 ? `${money(out)} out` : "", inn >= 0.5 ? `${money(inn)} in` : ""].filter(Boolean).join(" · ");
        return (
          <div key={g.month ?? "none"}>
            <div className="cx-month">
              <span style={{ minWidth: 0 }}>
                <span style={{ fontFamily: F.num, fontWeight: 700, color: L.deep, fontSize: 13 }}>
                  {g.month ? monthName(g.month, year) : "No date"}</span>
                <span style={{ fontFamily: F.body, fontSize: 12, color: L.muted, marginLeft: 10 }}>{count}</span>
              </span>
              {signed ? <span /> : <span style={{ ...NUM, fontSize: 13, fontWeight: 700, color: L.deep }}>
                {cents(g.rows.reduce((n, r) => n + r.amount, 0))}</span>}
              <span />
            </div>
            {g.rows.map((r) => (
              <div key={r.id}>
                <TxLine r={r} signed={signed} open={openId === r.id} onToggle={() => setOpenId(openId === r.id ? null : r.id)} />
                {openId === r.id ? <TxCard r={r} /> : null}
              </div>
            ))}
          </div>
        );
      })}
      <style>{TX_CSS}</style>
    </div>
  );
}

const TX_CSS = `
  .cx-row, .cx-head { display:grid; grid-template-columns: 64px minmax(0,1.1fr) minmax(0,1fr) minmax(0,1.6fr) 118px 30px;
    grid-template-areas: "date payee for what amt dots"; column-gap: 12px; align-items: start; padding: 10px 10px 10px 16px; }
  .cx-row { border-top: 1px solid ${L.ruleLine}; cursor: pointer; background: ${L.paper}; }
  .cx-row:hover { background: ${L.totalBand}; }
  .cx-head { padding-top: 8px; padding-bottom: 8px; background: ${L.colHead}; border-bottom: 1px solid ${L.ruleHeader}; }
  .cx-date { grid-area: date; } .cx-payee { grid-area: payee; min-width: 0; } .cx-for { grid-area: for; min-width: 0; }
  .cx-what { grid-area: what; min-width: 0; } .cx-amt { grid-area: amt; text-align: right; }
  .cx-dots, .cx-dots-h { grid-area: dots; }
  .cx-dots { width: 28px; height: 24px; border-radius: 7px; border: 0; background: transparent; color: ${L.ghost};
    cursor: pointer; font-size: 17px; line-height: 1; padding: 0 0 4px; font-weight: 700; margin-top: -2px; }
  .cx-row:hover .cx-dots, .cx-dots[aria-expanded="true"] { color: ${L.deep}; background: ${L.track}; }
  .cx-dots:focus-visible { outline: 2px solid ${L.accent}; outline-offset: 1px; }
  .cx-dot { display:inline-block; width: 6px; height: 6px; border-radius: 50%; background: ${L.flagRing}; margin-right: 7px;
    vertical-align: 2px; }
  .cx-month { display:grid; grid-template-columns: minmax(0,1fr) 118px 30px; column-gap: 12px; align-items: baseline;
    padding: 9px 10px 8px 16px; background: ${L.sectionBand}; border-top: 1px solid ${L.ruleSection}; }
  .cx-card { padding: 2px 16px 14px 92px; background: ${L.totalBand}; animation: cxOpen 130ms ease-out; }
  .cx-kv { display:grid; grid-template-columns: 150px minmax(0,1fr); gap: 7px 14px; margin: 10px 0 0; max-width: 880px; }
  .cx-acts { display:flex; gap: 6px; flex-wrap: wrap; margin-top: 12px; }
  .cx-act { height: 28px; padding: 0 11px; border-radius: 8px; border: 1px solid ${L.pillEdge}; background: ${L.paper};
    color: ${L.bodyDim}; font-family: ${F.body}; font-size: 12.5px; font-weight: 600; cursor: pointer; white-space: nowrap; }
  .cx-act:hover { border-color: ${L.accentMuted}; color: ${L.deep}; }
  .cx-act.on { background: ${L.deep}; border-color: ${L.deep}; color: ${L.onDeep}; }
  .cx-act.ghost { border-style: dashed; background: transparent; }
  .cx-act:focus-visible { outline: 2px solid ${L.accent}; outline-offset: 1px; }
  .cx-form { margin-top: 10px; border: 1px solid ${L.ruleInner}; border-radius: 10px; background: ${L.paper};
    padding: 12px 14px; display: grid; gap: 10px; max-width: 880px; }
  @keyframes cxOpen { from { opacity:0; transform: translateY(-3px); } }
  @media (prefers-reduced-motion: reduce) { .cx-card { animation: none; } }
  /* Sized by the list's own width, not the screen's: inside a Statement drawer on a phone the list has a
     third of the screen's width to work with. */
  .cx-list { container-type: inline-size; }
  @container (max-width: 860px) {
    .cx-row, .cx-head { grid-template-columns: 56px minmax(0,1.1fr) minmax(0,1fr) 110px 28px;
      grid-template-areas: "date payee for amt dots" ". what what . ."; row-gap: 4px; column-gap: 10px; padding-left: 12px; }
    .cx-head .cx-what { display: none; }
    .cx-month { grid-template-columns: minmax(0,1fr) 110px 28px; column-gap: 10px; padding-left: 12px; }
    .cx-card { padding: 2px 12px 14px 78px; }
  }
  @container (max-width: 520px) {
    .cx-row { grid-template-columns: minmax(0,1fr) auto 26px; grid-template-areas: "date amt dots"
      "payee payee payee" "for for for" "what what what"; row-gap: 3px; column-gap: 8px; padding-left: 12px; }
    .cx-head { display: none; }
    .cx-month { grid-template-columns: minmax(0,1fr) auto 26px; column-gap: 8px; padding-left: 12px; }
    .cx-card { padding: 2px 12px 14px; }
    .cx-kv { grid-template-columns: minmax(0,1fr); gap: 2px; } .cx-kv dt { margin-top: 8px; }
  }
`;
