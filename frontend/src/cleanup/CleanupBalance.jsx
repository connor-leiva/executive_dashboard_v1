/* Books Cleanup · Balance sheet -- each company on the last day, as QuickBooks has it and as the
   corrections leave it. The figures, the totals and both "does it balance" answers are the server's.

   An account the corrections need that doesn't exist yet, or one QuickBooks has set up as the wrong
   type, carries its note in the Statement's gold, with the ring, so it reads without colour too. */
import { useState } from "react";
import { StatePanel } from "../books/ui.jsx";
import { useCleanupBalance } from "./useCleanup.js";
import { Bar, Chip, EYEBROW, Foot, L, F, NUM, OkPill, Panel, Sheet, cents, periodLabel, shortOf } from "./kit.jsx";

const GRID = { display: "grid", gridTemplateColumns: "minmax(0,1fr) 128px 120px 132px", gap: 12 };

function Row({ r }) {
  const flag = !!r.note;
  return (
    <div className="cb-row" style={{ ...GRID, padding: "8px 18px", alignItems: "baseline", borderBottom: `1px solid ${L.ruleLine}`,
                                     background: flag ? L.flagBg : L.paper,
                                     boxShadow: flag ? `inset 3px 0 0 ${L.flagEdge}` : "none" }}>
      <span style={{ minWidth: 0, fontFamily: F.body, fontSize: 13, color: L.body, overflowWrap: "anywhere" }}>
        {flag && <span aria-hidden="true" style={{ display: "inline-block", width: 7, height: 7, borderRadius: "50%",
                       border: `2px solid ${L.flagRing}`, boxSizing: "border-box", marginRight: 7 }} />}
        {r.account}
        {r.type ? <span style={{ color: L.muted, fontSize: 11.5 }}> · {r.type}</span> : null}
        {flag && <span style={{ display: "block", color: L.flagInk, fontSize: 12 }}>{r.note}</span>}
      </span>
      <span className="cb-qbo" style={{ ...NUM, fontSize: 13, color: L.muted }}>{cents(r.qbo)}</span>
      <span className="cb-chg" style={{ ...NUM, fontSize: 13, color: Math.abs(r.corrections) >= 0.5 ? L.flagInk : L.ghost }}>
        {Math.abs(r.corrections) < 0.005 ? "–" : cents(r.corrections)}</span>
      <span style={{ ...NUM, fontSize: 13, fontWeight: 600, color: L.deep }}>{cents(r.corrected)}</span>
    </div>
  );
}

function Total({ label, v, strong }) {
  return (
    <div className="cb-row" style={{ ...GRID, padding: "11px 18px", background: L.totalBand,
                                     borderTop: strong ? `1.5px solid ${L.deep}` : `1px solid ${L.ruleInner}` }}>
      <span style={{ fontFamily: F.body, fontSize: 14, fontWeight: 700, color: L.deep }}>{label}</span>
      <span className="cb-qbo" style={{ ...NUM, fontSize: 14, fontWeight: 600, color: L.bodyDim }}>{cents(v.qbo)}</span>
      <span className="cb-chg" style={{ ...NUM, fontSize: 14, fontWeight: 600, color: L.flagInk }}>
        {Math.abs(v.corrections) < 0.005 ? "–" : cents(v.corrections)}</span>
      <span style={{ ...NUM, fontSize: 15, fontWeight: 700, color: L.deep }}>{cents(v.corrected)}</span>
    </div>
  );
}

export default function CleanupBalance({ ov, rev }) {
  const [company, setCompany] = useState(ov.companies[0]);
  const bs = useCleanupBalance(company, rev);
  const d = bs.data;
  const notes = d ? d.sections.reduce((n, s) => n + s.rows.filter((r) => r.note).length, 0) : 0;
  return (
    <Panel>
      <Bar label="Company" right={`As of ${periodLabel(ov.period).split(" – ")[1] || ""}`}>
        {ov.companies.map((x) => <Chip key={x} dark on={x === company} onClick={() => setCompany(x)}>{shortOf(ov, x)}</Chip>)}
      </Bar>
      <StatePanel loading={bs.loading} error={bs.error} retry={bs.retry}>
        {d && (
          <div style={{ padding: "20px 26px 26px", display: "grid", gap: 16 }}>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              <OkPill ok={d.balanced.qbo}>{d.balanced.qbo ? "Balances as booked" : "Doesn't balance as booked"}</OkPill>
              <OkPill ok={d.balanced.corrected}>{d.balanced.corrected ? "Balances corrected" : "Doesn't balance corrected"}</OkPill>
              {notes > 0 && <span style={{ fontFamily: F.body, fontSize: 12.5, color: L.flagInk }}>
                {notes} {notes === 1 ? "account needs" : "accounts need"} setting up or fixing in QuickBooks</span>}
            </div>
            <Sheet>
              <div className="cb-row" style={{ ...GRID, padding: "9px 18px", background: L.colHead,
                                               borderBottom: `1px solid ${L.ruleHeader}` }}>
                <span style={EYEBROW}>Account</span>
                <span className="cb-qbo" style={{ ...EYEBROW, textAlign: "right" }}>QuickBooks</span>
                <span className="cb-chg" style={{ ...EYEBROW, textAlign: "right" }}>Cleanup</span>
                <span style={{ ...EYEBROW, textAlign: "right" }}>Corrected</span>
              </div>
              {d.sections.map((s) => (
                <div key={s.key}>
                  <div style={{ padding: "11px 18px", background: L.sectionBand, borderTop: `1px solid ${L.ruleSection}`,
                                borderBottom: `1px solid ${L.ruleInner}`, fontFamily: F.num, fontSize: 15, fontWeight: 700,
                                color: L.deep }}>{s.label}</div>
                  {s.rows.map((r) => <Row key={r.account} r={r} />)}
                  <Total label={`Total ${s.label.toLowerCase()}`} v={s.total} />
                </div>
              ))}
              <Total label="Liabilities and equity" v={d.liabilities_plus_equity} strong />
            </Sheet>
            <Foot>QuickBooks is each account&rsquo;s balance on the last day as booked; the cleanup column is what the
              Corrections entries add. Equity includes the year&rsquo;s net income, corrected on the Statement.</Foot>
          </div>
        )}
      </StatePanel>
      <style>{`
        @media (max-width: 760px) {
          .cb-row { grid-template-columns: minmax(0,1fr) 120px !important; }
          .cb-qbo, .cb-chg { display:none; }
        }
      `}</style>
    </Panel>
  );
}
