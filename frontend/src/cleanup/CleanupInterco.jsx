/* Books Cleanup · Intercompany -- what the companies owe each other once the costs sit where they
   belong, each brand's year on a cash basis, and what the partners earned against what they were paid.
   All three move with the decisions; every figure is the server's.

   Each pair opens (Oct 19 review) to why the cleanup changes it, by reason, and then everything behind that,
   month by month in the tab's transaction list: each transaction the review put through the pair (whose card
   can move it), each old journal entry the review undid, each shared cost's share, each change made on the tab. */
import { Fragment, useMemo, useState } from "react";
import { StatePanel } from "../books/ui.jsx";
import { useCleanupInterco, useCleanupPair } from "./useCleanup.js";
import { Bar, EYEBROW, Foot, L, F, NUM, OkPill, Panel, Sheet, cents, money, monthName, periodLabel,
         shortOf } from "./kit.jsx";
import { Legend, TxList, rowsFromList } from "./CleanupTx.jsx";
import { AskAbout } from "./CleanupAsk.jsx";

const SHARE_PAYEE = { staff: "Shared staff", item: "Shared costs", ads: "Shared ads", fees: "Payment processing",
                      billback: "Ads billed back" };

/* A pair's rows (GET /cleanup/intercompany/pair) as list rows, each amount in the pair's reading. No arithmetic
   beyond adding up a month's shares: every amount is the server's. */
export function rowsFromPair(data, ov) {
  const short = (x) => shortOf(ov, x);
  const out = [];
  const shares = new Map();
  for (const r of data.rows) {
    const id = `${r.kind}|${r.key || r.what || ""}|${r.component}|${r.account}|${r.month}`;
    const onPair = r.why ? `${r.label}: ${r.why}` : r.label;
    if (r.kind === "tx" && r.tx) {
      const [base] = rowsFromList([r.tx], short);
      // the part of it on this pair; a payout split three ways also says how big it was
      const full = Math.abs(Math.abs(r.amount) - base.amount) > 0.5 ? base.amount : null;
      out.push({ ...base, id, month: r.month, amount: r.amount, full, onPair });
    } else if (r.kind === "tx") {                 // an old journal entry the review undid: not a transaction the tab lists
      const e = r.entry || {};
      out.push({ id, kind: "tx", month: r.month, date: e.date || null,
                 payee: e.number ? `Journal entry ${e.number}` : "Journal entry",
                 paidLine: e.company ? `${short(e.company)} · the old books` : "the old books",
                 forWho: r.other ? [short(r.other)] : [], line: "Undone by the cleanup", what: e.what || r.why || r.label,
                 amount: r.amount, status: "changed", note: e.why, onPair, twins: [] });
    } else if (r.kind === "share") {
      const g = `${r.src}|${r.month}|${r.other || ""}`;
      if (!shares.has(g)) shares.set(g, { src: r.src, month: r.month, other: r.other, rows: [] });
      shares.get(g).rows.push(r);
    } else {
      out.push({ id, kind: "adj", src: r.component, month: r.month, date: null, payee: r.label, paidLine: "",
                 forWho: r.other ? [short(r.other)] : [], line: "", what: r.what, amount: r.amount, made: [],
                 status: "", twins: [] });
    }
  }
  for (const { src, month, other, rows } of shares.values()) {
    const byWhat = new Map();
    for (const r of rows) byWhat.set(r.what, (byWhat.get(r.what) || 0) + r.amount);
    const made = [...byWhat.entries()].sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
      .map(([label, amount]) => ({ label, amount }));
    const M = monthName(month);
    const hub = short(ov.hub);
    const what = src === "billback"
      ? `${hub} paid for the ads in ${M}; your ad split bills part of them back to ${other}.`
      : src === "ads" ? `${hub} paid for the shared ads in ${M}; your ad split gives ${short(other)} its share.`
      : src === "fees" ? `${hub} paid the payment processing fees in ${M}; ${short(other)}'s share is by whose sales they were.`
      : made.length === 1
        ? `${hub} paid ${made[0].label} in ${M}; your splits give ${short(other)} its share.`
        : `${hub} paid them in ${M}; your splits give ${short(other)} its share of each.`;
    out.push({ id: `share|${src}|${month}|${other || ""}`, kind: "adj", src, month, date: null,
               payee: src === "item" && made.length === 1 ? made[0].label : SHARE_PAYEE[src] || src,
               paidLine: `${hub} paid it`, forWho: other ? [short(other)] : [], line: "", what,
               amount: rows.reduce((n, r) => n + r.amount, 0), made: made.length > 1 ? made : [], status: "", twins: [] });
  }
  return out;
}

/* One pair, opened: the change by reason, then item by item. */
function PairDetail({ ov, pair, rev }) {
  const { data, error, loading, retry } = useCleanupPair(pair.pair, rev);
  const rows = useMemo(() => (data ? rowsFromPair(data, ov) : []), [data]); // eslint-disable-line react-hooks/exhaustive-deps
  const year = (ov.period?.start || "").slice(0, 4);
  if (loading) return <div style={{ padding: "4px 18px 16px", fontFamily: F.body, fontSize: 12.5, color: L.muted }}>
    Loading what&rsquo;s behind it…</div>;
  if (error) {
    return (
      <div style={{ padding: "4px 18px 16px", fontFamily: F.body, fontSize: 12.5, color: L.flagInk }}>
        Couldn&rsquo;t load the detail.{" "}
        <button onClick={retry} style={{ height: 24, padding: "0 10px", borderRadius: 8, cursor: "pointer",
          fontFamily: F.body, fontSize: 12, border: `1px solid ${L.pillEdge}`, background: L.paper,
          color: L.bodyDim }}>Retry</button>
      </div>
    );
  }
  if (!data) return null;
  const total = rows.reduce((n, r) => n + r.amount, 0);
  return (
    <div className="ci-detail" style={{ padding: "4px 18px 18px", display: "grid", gap: 12, background: L.totalBand }}>
      <div style={{ border: `1px solid ${L.ruleInner}`, borderRadius: 10, background: L.paper, padding: "10px 14px",
                    display: "grid", gap: 6 }}>
        <div style={{ ...EYEBROW, color: L.ghost }}>Why the cleanup changes it · {pair.reads}</div>
        {data.parts.map((x) => (
          <div key={x.component} style={{ display: "flex", gap: 12, alignItems: "baseline" }}>
            <span style={{ flex: 1, minWidth: 0, fontFamily: F.body, fontSize: 13, color: L.body }}>{x.label}</span>
            <span style={{ ...NUM, fontSize: 13, color: L.deep }}>{cents(x.amount)}</span>
          </div>))}
        <div style={{ display: "flex", gap: 12, alignItems: "baseline", borderTop: `1px solid ${L.ruleLine}`, paddingTop: 6 }}>
          <span style={{ flex: 1, fontFamily: F.body, fontSize: 13, fontWeight: 700, color: L.deep }}>The cleanup&rsquo;s change</span>
          <span style={{ ...NUM, fontSize: 13.5, fontWeight: 700, color: L.deep }}>{cents(data.corrections)}</span>
        </div>
      </div>
      <Sheet right={<Legend />} title={<>Item by item <span style={{ fontFamily: F.body, fontSize: 12.5, fontWeight: 400,
        color: L.muted }}>· oldest first · a positive amount adds to what&rsquo;s owed</span></>}>
        {rows.length ? <TxList rows={rows} year={year} noun={["item", "items"]} /> : (
          <div style={{ padding: "12px 16px", fontFamily: F.body, fontSize: 12.5, color: L.muted }}>
            The cleanup doesn&rsquo;t change what these two owe each other.</div>)}
        <div style={{ padding: "9px 16px", borderTop: `1px solid ${L.ruleInner}`, background: L.totalBand }}>
          <OkPill ok={data.ties}>{data.ties ? `Together ${money(total)}: the cleanup's change to this pair`
            : `These add to ${money(total)}, not the change of ${money(data.corrections)}`}</OkPill>
        </div>
      </Sheet>
      {!data.itemized ? <Foot>This file doesn&rsquo;t list the review&rsquo;s transactions one by one: its moves show by
        reason. A newer export of the audit lists each one.</Foot> : null}
      <AskAbout about={{ kind: "pair", pair: pair.pair }} label="Ask about this pair"
                placeholder="Why does one owe the other this much?" />
    </div>
  );
}

export default function CleanupInterco({ ov, rev }) {
  const ic = useCleanupInterco(rev);
  const [open, setOpen] = useState(null);
  const d = ic.data;
  const s = d?.summary;
  const through = s ? ov.period.months.find((m) => m.m === s.months_through)?.label : "";
  const pairGrid = { display: "grid", gridTemplateColumns: "14px minmax(0,1.4fr) 120px 120px 132px", gap: 12 };
  const brandCols = [["revenue", "Revenue"], ["direct", "Its own costs"], ["ads", "Ads"], ["staff", "Staff"],
                     ["software", "Software"], ["other", "Other shared"], ["fees", "Fees"],
                     ["total_costs", "Total costs"], ["operating_result", "Result"]];
  return (
    <Panel>
      {/* The brands' year runs to the period's last day; the partners' shares stop at the last full
          month (the file's accrual_months), as the workbook's Summary tab has them. */}
      <Bar label="Between the companies" right={periodLabel(ov.period)} />
      <StatePanel loading={ic.loading} error={ic.error} retry={ic.retry}>
        {d && (
          <div style={{ padding: "20px 26px 26px", display: "grid", gap: 18 }}>
            <Sheet title="What the companies owe each other">
              <div style={{ overflowX: "auto" }}>
                <div style={{ minWidth: 560 }}>
                  <div style={{ ...pairGrid, padding: "9px 18px", background: L.colHead, borderBottom: `1px solid ${L.ruleHeader}` }}>
                    <span />
                    <span style={EYEBROW}>Between</span>
                    <span style={{ ...EYEBROW, textAlign: "right" }}>As booked</span>
                    <span style={{ ...EYEBROW, textAlign: "right" }}>Cleanup</span>
                    <span style={{ ...EYEBROW, textAlign: "right" }}>Corrected</span>
                  </div>
                  {d.pairs.map((p) => {
                    const on = open === p.pair;
                    const toggle = () => setOpen(on ? null : p.pair);
                    return (
                      <Fragment key={p.pair}>
                        <div role="button" tabIndex={0} aria-expanded={on} className="ci-pair" onClick={toggle}
                             onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } }}
                             style={{ ...pairGrid, padding: "10px 18px", alignItems: "baseline",
                                      borderBottom: `1px solid ${L.ruleLine}`, background: on ? L.totalBand : undefined }}>
                          <span aria-hidden="true" style={{ fontFamily: F.num, fontSize: 13, color: L.muted, display: "inline-block",
                            transform: on ? "rotate(90deg)" : "none", transition: "transform 120ms" }}>›</span>
                          <span style={{ fontFamily: F.body, fontSize: 13.5, color: L.deep, fontWeight: 600 }}>{p.pair}
                            <span style={{ display: "block", fontWeight: 400, fontSize: 12, color: L.muted }}>{p.reads}</span></span>
                          <span style={{ ...NUM, fontSize: 13, color: L.muted }}>{money(p.booked)}</span>
                          <span style={{ ...NUM, fontSize: 13, color: Math.abs(p.corrections) >= 1 ? L.flagInk : L.ghost }}>
                            {Math.abs(p.corrections) < 0.5 ? "–" : money(p.corrections)}</span>
                          <span style={{ ...NUM, fontSize: 15, fontWeight: 700, color: L.deep }}>{money(p.corrected)}</span>
                          {!on && p.parts?.length ? (           // why it changed, in a line across the row
                            <span style={{ gridColumn: "2 / -1", fontFamily: F.body, fontSize: 12, color: L.bodyDim,
                                           marginTop: -4 }}>
                              {p.parts.slice(0, 3).map((x) => `${x.label} ${money(x.amount)}`).join(" · ")}
                              {p.parts.length > 3 ? ` · and ${p.parts.length - 3} more` : ""}
                              <span style={{ color: L.accentInk, fontWeight: 600 }}> · see each one</span></span>) : null}
                        </div>
                        {on ? <PairDetail ov={ov} pair={p} rev={rev} /> : null}
                      </Fragment>);
                  })}
                </div>
              </div>
            </Sheet>

            <Sheet title="Each brand's year, cash basis"
                   right={<span style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>{periodLabel(ov.period)}</span>}>
              <div style={{ overflowX: "auto" }}>
                <div style={{ minWidth: 980 }}>
                  <div className="ci-grid" style={{ padding: "9px 18px", background: L.colHead, borderBottom: `1px solid ${L.ruleHeader}` }}>
                    <span style={EYEBROW}>Brand</span>
                    {brandCols.map(([, h]) => <span key={h} style={{ ...EYEBROW, textAlign: "right" }}>{h}</span>)}
                  </div>
                  {s.brands.map((b) => (
                    <div key={b.brand} className="ci-grid" style={{ padding: "9px 18px", alignItems: "baseline",
                                                                    borderBottom: `1px solid ${L.ruleLine}` }}>
                      <span style={{ fontFamily: F.body, fontSize: 13.5, fontWeight: 600, color: L.deep }}>{b.brand}
                        {shortOf(ov, ov.brand_company?.[b.brand]) !== b.brand && (
                          <span style={{ fontWeight: 400, fontSize: 11.5, color: L.muted }}> · inside {shortOf(ov, ov.brand_company?.[b.brand])}</span>
                        )}</span>
                      {brandCols.map(([k]) => (
                        <span key={k} style={{ ...NUM, fontSize: 13, fontWeight: k === "operating_result" ? 700 : 400,
                                               color: k === "operating_result" ? L.deep : L.body }}>{money(b[k])}</span>
                      ))}
                    </div>
                  ))}
                </div>
              </div>
              <div style={{ padding: "8px 18px 12px", display: "grid", gap: 4 }}>
                <Foot>Billed back to {d.billback_name}: {money(s.billback_ads)} of the shared ads (not a cost of any brand here).</Foot>
                {Math.abs(s.recovered) >= 0.5 && <Foot>{d.recover_label}: {money(s.recovered)}.</Foot>}
              </div>
            </Sheet>

            <Sheet title="The partners" right={<span style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>
              January to {through}</span>}>
              {s.partners.map((p) => (
                <div key={p.company} style={{ padding: "12px 18px", borderBottom: `1px solid ${L.ruleLine}`, display: "grid", gap: 6 }}>
                  <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
                    <span style={{ fontFamily: F.body, fontSize: 14, fontWeight: 600, color: L.deep }}>{p.person}</span>
                    <span style={{ fontFamily: F.body, fontSize: 12.5, color: L.muted }}>
                      {(p.rate * 100).toFixed(0)}% of {shortOf(ov, p.company)}</span>
                    <div style={{ flex: 1 }} />
                    {p.owed_back > 0 && <span style={{ fontFamily: F.body, fontSize: 13, color: L.flagInk }}>
                      owes back <strong style={{ fontFamily: F.num }}>{money(p.owed_back)}</strong></span>}
                    {p.owed_to > 0 && <span style={{ fontFamily: F.body, fontSize: 13, color: L.accentInk }}>
                      still owed <strong style={{ fontFamily: F.num }}>{money(p.owed_to)}</strong></span>}
                  </div>
                  <div className="ci-four">
                    {[["Operating income", p.noi], ["Salary first", p.salary], ["Earned", p.earned], ["Paid", p.paid]].map(([k, v]) => (
                      <div key={k} style={{ border: `1px solid ${L.ruleInner}`, borderRadius: 10, padding: "8px 11px" }}>
                        <div style={{ ...EYEBROW, color: L.ghost }}>{k}</div>
                        <div style={{ fontFamily: F.num, fontSize: 17, color: L.deep, fontVariantNumeric: "tabular-nums" }}>{money(v)}</div>
                      </div>
                    ))}
                  </div>
                  {p.note && <Foot>{p.note}</Foot>}
                </div>
              ))}
            </Sheet>
            <Foot>Corrected is what the books will say once the Corrections entries are posted. Moving the real money
              is a separate step: settle these in cash, then record the transfers.</Foot>
          </div>
        )}
      </StatePanel>
      <style>{`
        .ci-grid { display:grid; grid-template-columns: minmax(150px,1.2fr) repeat(9, minmax(76px,1fr)); gap:10px; }
        .ci-pair { cursor: pointer; }
        .ci-pair:hover { background: ${L.totalBand}; }
        .ci-pair:focus-visible { outline: 2px solid ${L.accent}; outline-offset: -2px; }
        .ci-four { display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap:8px; }
        @media (max-width: 640px) { .ci-four { grid-template-columns: repeat(2, minmax(0,1fr)); } }
      `}</style>
    </Panel>
  );
}
