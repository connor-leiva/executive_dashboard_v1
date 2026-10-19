/* Books Cleanup · Intercompany -- what the companies owe each other once the costs sit where they
   belong, each brand's year on a cash basis, and what the partners earned against what they were paid.
   All three move with the decisions; every figure is the server's. */
import { StatePanel } from "../books/ui.jsx";
import { useCleanupInterco } from "./useCleanup.js";
import { Bar, EYEBROW, Foot, L, F, NUM, Panel, Sheet, money, periodLabel, shortOf } from "./kit.jsx";

export default function CleanupInterco({ ov, rev }) {
  const ic = useCleanupInterco(rev);
  const d = ic.data;
  const s = d?.summary;
  const through = s ? ov.period.months.find((m) => m.m === s.months_through)?.label : "";
  const pairGrid = { display: "grid", gridTemplateColumns: "minmax(0,1.4fr) 120px 120px 132px", gap: 12 };
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
                    <span style={EYEBROW}>Between</span>
                    <span style={{ ...EYEBROW, textAlign: "right" }}>As booked</span>
                    <span style={{ ...EYEBROW, textAlign: "right" }}>Cleanup</span>
                    <span style={{ ...EYEBROW, textAlign: "right" }}>Corrected</span>
                  </div>
                  {d.pairs.map((p) => (
                    <div key={p.pair} style={{ ...pairGrid, padding: "10px 18px", alignItems: "baseline",
                                               borderBottom: `1px solid ${L.ruleLine}` }}>
                      <span style={{ fontFamily: F.body, fontSize: 13.5, color: L.deep, fontWeight: 600 }}>{p.pair}
                        <span style={{ display: "block", fontWeight: 400, fontSize: 12, color: L.muted }}>{p.reads}</span></span>
                      <span style={{ ...NUM, fontSize: 13, color: L.muted }}>{money(p.booked)}</span>
                      <span style={{ ...NUM, fontSize: 13, color: Math.abs(p.corrections) >= 1 ? L.flagInk : L.ghost }}>
                        {Math.abs(p.corrections) < 0.5 ? "–" : money(p.corrections)}</span>
                      <span style={{ ...NUM, fontSize: 15, fontWeight: 700, color: L.deep }}>{money(p.corrected)}</span>
                    </div>
                  ))}
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
        .ci-four { display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap:8px; }
        @media (max-width: 640px) { .ci-four { grid-template-columns: repeat(2, minmax(0,1fr)); } }
      `}</style>
    </Panel>
  );
}
