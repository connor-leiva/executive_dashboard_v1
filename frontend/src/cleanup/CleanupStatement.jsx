/* Books Cleanup · Statement -- the audited year's P&L, in the Books Statement's form
   (books/BooksStatement.jsx): the entity bar, the controls strip, the summary band, the collapsible
   ledger, and a drawer under every line that shows how its number is made.

   NO ARITHMETIC LIVES HERE. Every figure is the server's (services/books_cleanup.py): QuickBooks'
   figure, the change the cleanup makes and the corrected one, per line and per total. The only
   derived value is percent of revenue, a ratio of two numbers the server sent.

   A FLAGGED LINE is one the cleanup changes by a dollar or more. It carries the Statement's gold
   wash, a solid left edge and a ringed marker, and its change is in its own column, so the meaning
   never lives in colour alone. */
import { useMemo, useState } from "react";
import { StatePanel } from "../books/ui.jsx";
import { useCleanupForward, useCleanupStatement, useCleanupLine } from "./useCleanup.js";
import { Bar, Chip, Controls, EYEBROW, Foot, L, F, Loss, NUM, OkPill, Panel, Seg, Sheet, Toggle, cents, change,
         money, pctOf, periodLabel, shortOf } from "./kit.jsx";
import { Legend, TxList, rowsFromLine } from "./CleanupTx.jsx";
import { AskAbout } from "./CleanupAsk.jsx";

/* ── the drawer: how the number is made, then what's in it ─────────────────────────────── */

/* Each kind of correction, said both ways round: [when it adds to the line, when it takes away]. The
   server names the kinds (services/books_cleanup.py COMPONENTS); a kind this map doesn't know is said
   with the server's own label. */
const PLAIN = {
  moved: ["came over from other companies' books", "moved to the company it belongs to"],
  recat: ["moved in from other lines", "moved out to other lines"],
  never: ["added back entries that did happen", "removed entries that never happened"],
  old: ["added back what the old bookkeeper's journal entries took out", "undid the old bookkeeper's journal entries"],
  outside: ["brought in what another company paid", "took out what another company paid"],
  split: ["added this company's share of shared costs (your splits)", "took out the share other companies carry (your splits)"],
  dues: ["counted dues as members earn them", "held back dues not yet earned"],
  timing: ["counted it in the month it belongs to", "moved it to the month it belongs to"],
  own: ["added the owner's charges", "took out the owner's own charges"],
  chargeline: ["moved the owner's charges here", "moved the owner's charges to another line"],
  partner: ["added the partner's share as profit builds", "took back partner share as profit fell"],
  yours: ["added what you moved here", "took out what you moved away"],
};

function HowMade({ d, monthLabel }) {
  const why = (d.why || []).filter((w) => Math.abs(w.amount) >= 0.5);
  const named = why.reduce((n, w) => n + w.amount, 0);
  const rest = (d.corrections || 0) - named;            // parts under 50 cents, summed rather than dropped
  const said = why.map((w) => {
    const pair = PLAIN[w.component];
    const words = pair ? pair[w.amount >= 0 ? 0 : 1] : w.label.toLowerCase();
    return `${words} (${w.amount >= 0 ? "+" : "−"}${money(Math.abs(w.amount))})`;
  });
  if (Math.abs(rest) >= 0.5) said.push(`smaller items (${rest >= 0 ? "+" : "−"}${money(Math.abs(rest))})`);
  const body = said.length
    ? ` The cleanup ${said.slice(0, -1).join(", ")}${said.length > 1 ? ", and " : ""}${said[said.length - 1]}.`
    : " The cleanup doesn’t change it: QuickBooks has it right.";
  return (
    <div>
      <div style={EYEBROW}>How this number is made{monthLabel ? ` · ${monthLabel}` : ""}</div>
      <div style={{ marginTop: 6, fontFamily: F.body, fontSize: 14, color: L.deep, lineHeight: 1.55, maxWidth: 900 }}>
        QuickBooks shows {money(d.qbo)}.{body} That leaves <strong>{money(d.corrected)}</strong>.
      </div>
    </div>
  );
}

/* The staff line, person by person: what each one cost this company, month by month. */
function People({ people, months, viewShort }) {
  const cell = { ...NUM, padding: "7px 6px", fontSize: 12.5 };
  return (
    <Sheet title={`Who's in it: ${viewShort}'s share, person by person`}>
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", width: "100%", minWidth: 200 + months.length * 76 }}>
          <thead><tr>
            <th style={{ ...EYEBROW, textAlign: "left", padding: "8px 16px" }}>Person</th>
            {months.map((m) => <th key={m.m} style={{ ...EYEBROW, textAlign: "right", padding: "8px 6px" }}>{m.label}</th>)}
            <th style={{ ...EYEBROW, textAlign: "right", padding: "8px 16px" }}>Total</th>
          </tr></thead>
          <tbody>{people.map((p) => (
            <tr key={p.name} style={{ borderTop: `1px solid ${L.ruleLine}` }}>
              <td style={{ padding: "7px 16px", fontFamily: F.body, fontSize: 13, color: L.deep, fontWeight: 600 }}>
                {p.name}{p.follows_wages ? <span style={{ fontWeight: 400, color: L.muted }}> · follows W-2 wages</span> : null}</td>
              {months.map((m) => {
                const v = p.months[String(m.m)];
                return <td key={m.m} style={{ ...cell, color: v ? L.body : L.ghost }}>{v ? money(v) : "–"}</td>;
              })}
              <td style={{ ...cell, padding: "7px 16px", fontWeight: 700, color: L.deep }}>{money(p.total)}</td>
            </tr>))}
          </tbody>
        </table>
      </div>
      <div style={{ padding: "8px 16px 12px", fontFamily: F.body, fontSize: 12, color: L.muted }}>
        Each person&rsquo;s split, month by month, is on Decisions.</div>
    </Sheet>
  );
}

function LineDetail({ ov, view, line, month, monthLabel, rev, brandView, income }) {
  const { data, error, loading, retry } = useCleanupLine(view, line, month, rev);
  const short = (x) => shortOf(ov, x);
  const year = (ov.period?.start || "").slice(0, 4);
  const rows = useMemo(() => (data ? rowsFromLine(data, { viewShort: short(view), hubShort: short(ov.hub), short, income })
    : []), [data]); // eslint-disable-line react-hooks/exhaustive-deps
  if (loading) return <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.muted }}>
    Loading the dollars behind this line…</div>;
  if (error) {
    return (
      <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.flagInk }}>
        Couldn&rsquo;t load the detail.{" "}
        <button onClick={retry} style={{ height: 24, padding: "0 10px", borderRadius: 8, cursor: "pointer",
          fontFamily: F.body, fontSize: 12, border: `1px solid ${L.pillEdge}`, background: L.paper,
          color: L.bodyDim }}>Retry</button>
      </div>
    );
  }
  if (!data) return null;
  const off = (data.rows_total || 0) - (data.corrected || 0);
  const tx = rows.filter((r) => r.kind === "tx").length;
  const adj = rows.length - tx;
  const months = month ? ov.period.months.filter((m) => m.m === month) : ov.period.months;
  return (
    <div style={{ display: "grid", gap: 14 }}>
      {!brandView && <HowMade d={data} monthLabel={monthLabel} />}
      {data.people && data.people.length ? <People people={data.people} months={months} viewShort={short(view)} /> : null}
      <Sheet right={<Legend />} title={<>Month by month <span style={{ fontFamily: F.body, fontSize: 12.5, fontWeight: 400,
        color: L.muted }}>· {tx} {income ? (tx === 1 ? "payment" : "payments") : (tx === 1 ? "charge" : "charges")}
        {adj ? `, ${adj} adjustment${adj === 1 ? "" : "s"}` : ""} · oldest first</span></>}>
        {rows.length ? <TxList rows={rows} income={income} year={year} /> : (
          <div style={{ padding: "12px 16px", fontFamily: F.body, fontSize: 12.5, color: L.muted }}>
            Nothing on this line{monthLabel ? ` in ${monthLabel}` : ""}.</div>
        )}
        <div style={{ padding: "9px 16px", borderTop: `1px solid ${L.ruleInner}`, display: "flex", gap: 12,
                      alignItems: "center", flexWrap: "wrap", background: L.totalBand }}>
          <OkPill ok={Math.abs(off) < 0.5}>
            {Math.abs(off) < 0.5 ? `Together ${money(data.rows_total)}: the line's corrected total`
              : off < 0 ? `Short of the line by ${money(-off)}` : `Over the line by ${money(off)}`}</OkPill>
        </div>
      </Sheet>
      <AskAbout about={{ kind: "line", view, line, month }} label="Ask about this line"
                placeholder={`Why is ${line} what it is${monthLabel ? ` in ${monthLabel}` : ""}?`} />
    </div>
  );
}

/* ── ledger rows ─────────────────────────────────────────────────────────────────────────── */

function Nums({ v, revenue, one, strong, big, flag, hide, loss }) {
  const col = big ? L.paper : strong ? L.deep : L.body;
  const size = big ? 20 : strong ? 16 : 13.5;
  return one ? (
    <>
      <span style={{ ...NUM, fontSize: size, fontWeight: big || strong || flag ? 700 : 400, color: col }}>
        {hide ? "" : money(v.corrected)}{!hide && loss ? <Loss v={v.corrected} color={big ? L.onDeepLoss : undefined} /> : null}</span>
      <span className="cu-pct" style={{ ...NUM, fontSize: 12, color: big ? L.onDeepPct : L.muted }}>
        {hide ? "" : pctOf(v.corrected, revenue)}</span>
    </>
  ) : (
    <>
      <span className="cu-qbo" style={{ ...NUM, fontSize: big ? 15 : 13, color: big ? L.onDeepPct : L.muted }}>
        {hide ? "" : money(v.qbo)}</span>
      <span className="cu-chg" style={{ ...NUM, fontSize: big ? 15 : 13,
        color: big ? L.onDeepPct : Math.abs(v.corrections || 0) >= 1 ? L.flagInk : L.ghost }}>
        {hide ? "" : Math.abs(v.corrections || 0) < 0.5 ? "–" : change(v.corrections)}</span>
      <span style={{ ...NUM, fontSize: size, fontWeight: big || strong || flag ? 700 : 400, color: col,
                     letterSpacing: big ? "-.02em" : 0 }}>{hide ? "" : money(v.corrected)}
        {!hide && loss ? <Loss v={v.corrected} color={big ? L.onDeepLoss : undefined} /> : null}</span>
      <span className="cu-pct" style={{ ...NUM, fontSize: 12, color: big ? L.onDeepPct : L.muted }}>
        {hide ? "" : pctOf(v.corrected, revenue)}</span>
    </>
  );
}

function SectionRow({ sec, open, onToggle, revenue, one }) {
  const n = sec.lines.length;
  return (
    <button className={`cu-row${one ? " cu-one" : ""}`} onClick={onToggle} aria-expanded={open} style={{
      width: "100%", border: 0, borderTop: `1px solid ${L.ruleSection}`, borderBottom: `1px solid ${L.ruleInner}`,
      background: L.sectionBand, padding: "12px 20px 12px 18px", cursor: "pointer", textAlign: "left",
      fontFamily: F.body, alignItems: "center" }}>
      <span style={{ display: "flex", alignItems: "center", gap: 9, minWidth: 0 }}>
        <span aria-hidden="true" style={{ width: 0, height: 0, flex: "none", marginRight: 1,
          borderTop: "4.5px solid transparent", borderBottom: "4.5px solid transparent",
          borderLeft: `6px solid ${L.faint}`, transform: open ? "rotate(90deg)" : "none",
          transformOrigin: "35% 50%" }} />
        <span style={{ fontFamily: F.num, fontSize: 15.5, fontWeight: 700, letterSpacing: ".02em", color: L.deep }}>
          {sec.label}</span>
        <span style={{ fontFamily: F.body, fontSize: 11.5, color: L.ghost, whiteSpace: "nowrap" }}>
          {n} {n === 1 ? "line" : "lines"}</span>
      </span>
      {/* A collapsed section still shows what it's worth, or collapsing hides the number. */}
      <Nums v={sec.total} revenue={revenue} one={one} strong hide={open} />
    </button>
  );
}

function LineRow({ line, open, onToggle, revenue, one, children }) {
  const on = line.flagged;
  return (
    <>
      <div className={`cu-row${one ? " cu-one" : ""} cu-line${on ? " cu-flagged" : ""}`} role="button" tabIndex={0}
        aria-expanded={open} onClick={onToggle}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggle(); } }}
        style={{ alignItems: "center", padding: "8px 20px 8px 32px", borderBottom: `1px solid ${L.ruleLine}`,
                 boxShadow: on ? `inset 3px 0 0 ${L.flagEdge}` : "none", cursor: "pointer" }}>
        <span style={{ display: "flex", alignItems: "center", gap: 9, minWidth: 0 }}>
          <span aria-hidden="true" style={on
            ? { width: 7, height: 7, borderRadius: "50%", border: `2px solid ${L.flagRing}`, flex: "none",
                boxSizing: "border-box" }
            : { width: 7, height: 7, flex: "none" }} />
          <span style={{ fontFamily: F.body, fontSize: 13.5, color: L.body, whiteSpace: "nowrap", overflow: "hidden",
                         textOverflow: "ellipsis", minWidth: 0 }}>
            {line.line}
            {on && <span style={{ color: L.flagInk }}> · changed by the cleanup</span>}
          </span>
          <span aria-hidden="true" style={{ width: 0, height: 0, flex: "none",
            borderTop: "3.5px solid transparent", borderBottom: "3.5px solid transparent",
            borderLeft: `5px solid ${L.code}`, transform: open ? "rotate(90deg)" : "none",
            transformOrigin: "35% 50%" }} />
        </span>
        <Nums v={line} revenue={revenue} one={one} flag={on} />
      </div>
      {open && (
        <div className="cu-drawer" style={{ background: L.totalBand, borderBottom: `1px solid ${L.ruleInner}`,
          boxShadow: `inset 3px 0 0 ${on ? L.flagEdge : L.ruleSection}`, padding: "14px 20px 16px 32px" }}>
          {children}
        </div>
      )}
    </>
  );
}

function TotalRow({ label, v, revenue, one, tone, loss }) {
  const big = tone === "big", strong = tone === "strong";
  return (
    <div className={`cu-row${one ? " cu-one" : ""}`} style={{ alignItems: "center",
      padding: big ? "16px 20px 16px 18px" : "12px 20px 12px 18px", background: big ? L.deep : L.totalBand,
      borderTop: big ? "none" : strong ? `1.5px solid ${L.deep}` : `1px solid ${L.ruleInner}`,
      borderBottom: big ? "none" : `1px solid ${L.ruleInner}` }}>
      <span style={{ fontFamily: F.body, fontSize: big ? 16 : strong ? 15 : 14, fontWeight: big || strong ? 700 : 600,
                     letterSpacing: "-.01em", color: big ? L.onDeep : strong ? L.deep : L.bodyDim }}>{label}</span>
      <Nums v={v} revenue={revenue} one={one} strong={strong} big={big} loss={loss} />
    </div>
  );
}

/* ── the screen ─────────────────────────────────────────────────────────────────────────── */

/* The projected company's year with the months ahead (GET /cleanup/forward): the corrected year so far, what
   the contracts, the plan and the forecast add, and the year through the horizon. Every figure is the server's. */
function FutureStatement({ ov, rev }) {
  const fw = useCleanupForward(rev);
  const st = fw.data?.forward?.statement;
  const t = st?.totals;
  const cols = (strong) => ({ ...NUM, fontSize: strong ? 14 : 13.5, fontWeight: strong ? 700 : 400 });
  const rowOf = (label, a, b, c, tone, loss) => (
    <div key={label} className="cu-frow" style={{ padding: tone === "big" ? "14px 20px 14px 18px" : "8px 20px 8px 32px",
      alignItems: "center", background: tone === "big" ? L.deep : tone === "strong" ? L.totalBand : L.paper,
      borderTop: `1px solid ${tone ? L.ruleInner : L.ruleLine}` }}>
      <span style={{ fontFamily: F.body, fontSize: 13.5, fontWeight: tone ? 700 : 400,
                     color: tone === "big" ? L.onDeep : tone ? L.deep : L.body }}>{label}</span>
      <span className="wide-only" style={{ ...cols(false), color: tone === "big" ? L.onDeepPct : L.muted }}>{money(a)}</span>
      <span className="wide-only" style={{ ...cols(false), color: tone === "big" ? L.onDeepPct : L.infoInk }}>{money(b)}</span>
      <span style={{ ...cols(true), color: tone === "big" ? L.paper : L.deep }}>{money(c)}
        {loss ? <Loss v={c} color={tone === "big" ? L.onDeepLoss : undefined} /> : null}</span>
    </div>
  );
  return (
    <StatePanel loading={fw.loading} error={fw.error} retry={fw.retry}>
      {st && (
        <div className="cu-page" style={{ padding: "20px 26px 26px", display: "flex", flexDirection: "column", gap: 18 }}>
          <div className="cu-summary" style={{ display: "grid", gap: 12 }}>
            {[["Revenue", "total_revenue"], ["Costs", "total_costs"], ["Net operating income", "net_operating_income"],
              ["Net income", "net_income"]].map(([label, k]) => (
              <div key={k} style={{ border: `1px solid ${L.ruleInner}`, borderRadius: 12, background: L.paper,
                                    padding: "13px 15px 14px", display: "flex", flexDirection: "column", gap: 6 }}>
                <div style={{ ...EYEBROW, color: L.ghost }}>{label}, through {st.through}</div>
                <div style={{ fontFamily: F.num, fontVariantNumeric: "tabular-nums", fontSize: 23, color: L.deep,
                              letterSpacing: "-.02em" }}>{money(t.through[k])}
                  {k.startsWith("net") ? <Loss v={t.through[k]} size={13} /> : null}</div>
                <div style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted }}>
                  {money(t.so_far[k])} so far · {money(t.ahead[k])} still to come</div>
              </div>))}
          </div>
          <div style={{ border: `1px solid ${L.ruleInner}`, borderRadius: 13, background: L.paper, overflow: "hidden" }}>
            <div className="cu-frow" style={{ padding: "9px 20px 9px 18px", background: L.colHead,
                                              borderBottom: `1px solid ${L.ruleHeader}` }}>
              <span style={EYEBROW}>Line</span>
              <span className="wide-only" style={{ ...EYEBROW, textAlign: "right" }}>So far (corrected)</span>
              <span className="wide-only" style={{ ...EYEBROW, textAlign: "right" }}>Still to come</span>
              <span style={{ ...EYEBROW, textAlign: "right" }}>Through {st.through}</span>
            </div>
            {st.sections.map((sec) => (
              <div key={sec.key}>
                {sec.lines.length ? (
                  <div style={{ padding: "11px 20px 11px 18px", background: L.sectionBand, borderTop: `1px solid ${L.ruleSection}`,
                                fontFamily: F.num, fontSize: 15.5, fontWeight: 700, color: L.deep }}>{sec.label}</div>) : null}
                {sec.lines.map((l) => rowOf(l.line, l.so_far, l.ahead, l.through))}
                {sec.key === "revenue" ? rowOf("Total revenue", t.so_far.total_revenue, t.ahead.total_revenue,
                                                t.through.total_revenue, "strong") : null}
                {sec.key === "costs" ? [rowOf("Total costs", t.so_far.total_costs, t.ahead.total_costs, t.through.total_costs, "strong"),
                  rowOf("Net operating income", t.so_far.net_operating_income, t.ahead.net_operating_income,
                        t.through.net_operating_income, "strong", true)] : null}
              </div>
            ))}
            {rowOf("Net income", t.so_far.net_income, t.ahead.net_income, t.through.net_income, "big", true)}
          </div>
          <Foot>Through {st.through}: the contracts still to be earned, the plan&rsquo;s monthly costs (Budget), and the
            retreats, commissions and partner share (Forecast). No new members are assumed.</Foot>
        </div>
      )}
    </StatePanel>
  );
}

export default function CleanupStatement({ ov, rev }) {
  // The workbook's order: each company, with the brand run inside the hub right after it.
  const views = [];
  ov.companies.forEach((c) => {
    views.push(c);
    if (ov.inside_brand && c === ov.hub) views.push(ov.inside_brand);
  });
  views.push("all");

  const [view, setView] = useState(views[0]);
  const [month, setMonth] = useState(null);            // null = the year so far
  const [expanded, setExpanded] = useState(null);
  const [closed, setClosed] = useState({});
  const [ahead, setAhead] = useState(false);           // the projected company, with the months after the books
  const future = ahead && view === ov.forward_company;
  const st = useCleanupStatement(view, month, rev);
  const d = st.data;
  const one = d?.kind === "brand";
  const months = ov.period.months;
  const monthLabel = month ? months.find((m) => m.m === month)?.label : null;

  const keys = d ? d.sections.map((s) => s.key) : [];
  const allClosed = keys.length > 0 && keys.every((k) => closed[k]);
  const t = d?.totals;
  const revenue = t?.total_revenue?.corrected ?? 0;
  const flagged = d ? d.sections.reduce((n, s) => n + s.lines.filter((l) => l.flagged).length, 0) : 0;

  const CLOSERS = {
    revenue: [["Total revenue", "total_revenue", "strong"]],
    costs: [["Total costs", "total_costs", null], ["Net operating income", "net_operating_income", "strong"]],
    below: [["Net income", "net_income", "big"]],
  };

  const rows = [];
  if (d) {
    d.sections.forEach((sec) => {
      const open = !closed[sec.key];
      // A brand has nothing below the line (no partner share of its own): no empty section, but its
      // net income still closes the statement.
      if (sec.lines.length) {
        rows.push(<SectionRow key={`s-${sec.key}`} sec={sec} open={open} revenue={revenue} one={one}
                              onToggle={() => setClosed((p) => ({ ...p, [sec.key]: open }))} />);
      }
      if (open) {
        sec.lines.forEach((l) => {
          const id = `${sec.key}|${l.line}`;
          rows.push(
            <LineRow key={id} line={l} revenue={revenue} one={one} open={expanded === id}
                     onToggle={() => setExpanded(expanded === id ? null : id)}>
              <LineDetail ov={ov} view={view} line={l.line} month={month} monthLabel={monthLabel} rev={rev}
                          brandView={one} income={sec.key === "revenue"} />
            </LineRow>);
        });
      }
      (CLOSERS[sec.key] || []).forEach(([label, k, tone]) => {
        if (tone === null && closed[sec.key]) return;    // a section's own sum, under a shut section, echoes it
        rows.push(<TotalRow key={`t-${k}`} label={label} v={t[k]} revenue={revenue} one={one} tone={tone}
                            loss={k === "net_operating_income" || k === "net_income"} />);
      });
    });
  }

  const tiles = t ? [
    ["Revenue", t.total_revenue, L.deep, revenue > 0 ? 1 : 0],
    ["Costs", t.total_costs, L.accentMuted, revenue > 0 ? t.total_costs.corrected / revenue : 0],
    ["Net operating income", t.net_operating_income, L.accentInk,
     revenue > 0 ? Math.max(t.net_operating_income.corrected / revenue, 0) : 0],
    ["Net income", t.net_income, L.accent, revenue > 0 ? Math.max(t.net_income.corrected / revenue, 0) : 0],
  ] : [];
  const tie = d?.ties_to_quickbooks;

  return (
    <Panel>
      <Bar label="Entity" right={periodLabel(ov.period)}>
        {views.map((v) => (
          <Chip key={v} dark on={v === view} title={v === ov.inside_brand ? `A brand inside ${ov.hub}` : undefined}
                onClick={() => { setView(v); setExpanded(null); }}>{shortOf(ov, v)}</Chip>
        ))}
      </Bar>
      <Controls>
        {view === ov.forward_company ? (
          <Seg options={[["Year so far", false], ["+ Future months", true]]} value={future}
               onChange={(v) => { setAhead(v); setMonth(null); setExpanded(null); }} />) : null}
        {!future ? (
        <div style={{ display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap" }}>
          <Toggle on={month === null} onClick={() => { setMonth(null); setExpanded(null); }}>Year so far</Toggle>
          {months.map((m) => (
            <Toggle key={m.m} on={month === m.m} onClick={() => { setMonth(m.m); setExpanded(null); }}>{m.label}</Toggle>
          ))}
        </div>) : null}
        <button onClick={() => setClosed(allClosed ? {} : Object.fromEntries(keys.map((k) => [k, true])))} style={{
          height: 28, padding: "0 12px", borderRadius: 8, cursor: "pointer", fontFamily: F.body, fontSize: 12.5,
          fontWeight: 500, whiteSpace: "nowrap", border: `1px solid ${L.pillEdge}`, background: L.paper,
          color: L.bodyDim }}>{allClosed ? "Expand all" : "Collapse all"}</button>
        <div style={{ flex: 1 }} />
        {tie && (
          <OkPill ok={Math.abs(tie.delta) < 0.01}
                  title="QuickBooks' own P&L report, against the lines this statement starts from">
            Starts from QuickBooks&rsquo; P&amp;L · delta {cents(tie.delta)}</OkPill>
        )}
        {d && !one && (
          <span style={{ fontFamily: F.num, fontSize: 11, color: L.ghost, whiteSpace: "nowrap" }}>
            {flagged} {flagged === 1 ? "line" : "lines"} changed</span>
        )}
      </Controls>

      {future ? <FutureStatement ov={ov} rev={rev} /> : (
      <StatePanel loading={st.loading} error={st.error} retry={st.retry}>
        {d && (
          <div className="cu-page" style={{ padding: "20px 26px 26px", display: "flex", flexDirection: "column", gap: 18 }}>
            <div className="cu-summary" style={{ display: "grid", gap: 12 }}>
              {tiles.map(([label, v, tone, ratio]) => (
                <div key={label} style={{ border: `1px solid ${L.ruleInner}`, borderRadius: 12, background: L.paper,
                  padding: "13px 15px 14px", display: "flex", flexDirection: "column", gap: 6 }}>
                  <div style={{ ...EYEBROW, color: L.ghost }}>{label}</div>
                  <div style={{ fontFamily: F.num, fontVariantNumeric: "tabular-nums", fontSize: 23, fontWeight: 500,
                                color: L.deep, letterSpacing: "-.02em" }}>{money(v.corrected)}
                    {label.startsWith("Net") ? <Loss v={v.corrected} size={13} /> : null}</div>
                  <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                    <div style={{ flex: 1, height: 5, borderRadius: 999, background: L.track, overflow: "hidden" }}>
                      <div style={{ width: `${Math.min(Math.max(ratio, 0), 1) * 100}%`, height: "100%",
                                    borderRadius: 999, background: tone }} />
                    </div>
                    <span style={{ fontFamily: F.num, fontSize: 11, color: L.faint, flex: "none" }}>
                      {pctOf(v.corrected, revenue)}</span>
                  </div>
                  {!one && (
                    <div style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted }}>
                      QuickBooks {money(v.qbo)}
                      {Math.abs(v.corrections) >= 1 && <span style={{ color: L.flagInk }}> · {change(v.corrections)}</span>}
                    </div>
                  )}
                </div>
              ))}
            </div>

            <div style={{ border: `1px solid ${L.ruleInner}`, borderRadius: 13, background: L.paper, overflow: "hidden" }}>
              <div className={`cu-row${one ? " cu-one" : ""}`} style={{ alignItems: "center", padding: "9px 20px 9px 18px",
                background: L.colHead, borderBottom: `1px solid ${L.ruleHeader}` }}>
                <span style={EYEBROW}>Line</span>
                {one ? (
                  <span style={{ ...EYEBROW, textAlign: "right" }}>Amount</span>
                ) : (
                  <>
                    <span className="cu-qbo" style={{ ...EYEBROW, textAlign: "right" }}>QuickBooks</span>
                    <span className="cu-chg" style={{ ...EYEBROW, textAlign: "right" }}>Cleanup</span>
                    <span style={{ ...EYEBROW, textAlign: "right" }}>Corrected</span>
                  </>
                )}
                <span className="cu-pct" style={{ ...EYEBROW, textAlign: "right" }}>% rev</span>
              </div>
              {rows}
            </div>

            <Foot>
              {one
                ? `${shortOf(ov, view)} is a brand inside ${ov.hub}, so it has no QuickBooks of its own. This is its year on a cash basis: what came in and went out for it, with its share of the shared costs, and no timing or partner shares.`
                : `Corrected is QuickBooks plus the cleanup's entries${month ? ` for ${monthLabel}` : ""}. Open any line to see how its number is made and every dollar in it.`}
              {(d.memo || []).map((m) => ` ${m.label}: ${money(m.amount)}.`)}
            </Foot>
          </div>
        )}
      </StatePanel>)}

      <style>{`
        .cu-row { display:grid; grid-template-columns: minmax(0,1fr) 112px 104px 124px 62px; gap:12px; }
        .cu-row.cu-one { grid-template-columns: minmax(0,1fr) 160px 82px; }
        .cu-line { background:${L.paper}; }
        .cu-flagged { background:${L.flagBg}; }
        .cu-line:hover { background:${L.totalBand}; }
        .cu-flagged:hover { background:${L.flagBgHover}; }
        .cu-summary { grid-template-columns: repeat(4, minmax(0,1fr)); }
        .cu-frow { display:grid; grid-template-columns: minmax(0,1fr) 130px 130px 150px; gap:12px; }
        .cu-drawer { animation: cuOpen 130ms ease-out; }
        @keyframes cuOpen { from { opacity:0; transform:translateY(-3px); } }
        @media (max-width: 1080px) { .cu-summary { grid-template-columns: repeat(2, minmax(0,1fr)); } }
        @media (max-width: 900px) {
          .cu-row { grid-template-columns: minmax(0,1fr) 100px 112px; gap:10px; }
          .cu-row.cu-one { grid-template-columns: minmax(0,1fr) 116px; }
          .cu-qbo, .cu-pct { display:none; }
        }
        @media (max-width: 560px) {
          .cu-drawer { padding: 12px 8px 14px 12px !important; }
          .wide-only { display: none !important; }
          .cu-frow { grid-template-columns: minmax(0,1fr) 120px; padding-left: 12px !important; padding-right: 12px !important; }
          .cu-page { padding: 14px 10px 18px !important; }
          .cu-row, .cu-row.cu-one { grid-template-columns: minmax(0,1fr) 104px; }
          .cu-chg { display:none; }
          .cu-summary { grid-template-columns: minmax(0,1fr); }
        }
        @media (prefers-reduced-motion: reduce) { .cu-drawer { animation: none; } }
      `}</style>
    </Panel>
  );
}
