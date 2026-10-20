/* Books Cleanup · Transactions -- every transaction in the year, the way the Statement's drawers show a
   line's: Date · Payee and the account it was paid from · For and the line it's filed to · What it was ·
   Amount, month by month, with a card under each (CleanupTx.jsx).

   One read for the year (GET /cleanup/transactions, gzipped); searching and filtering happen here, on
   what the server sent. A company's transactions are the ones it paid AND the ones the corrected books
   put on it: "anything to do with beCollective". */
import { useMemo, useState } from "react";
import { StatePanel } from "../books/ui.jsx";
import { useCleanupTransactions } from "./useCleanup.js";
import { Bar, Chip, Controls, F, L, NUM, Panel, Sheet, inputStyle, money, periodLabel, shortOf } from "./kit.jsx";
import { Legend, TxList, rowsFromList } from "./CleanupTx.jsx";

const FILTERS = [["all", "Everything"], ["needs", "Needs you"], ["changed", "Changed by the cleanup"]];

export default function CleanupTransactions({ ov, rev }) {
  const tx = useCleanupTransactions(rev);
  const [q, setQ] = useState("");
  const [co, setCo] = useState("all");
  const [only, setOnly] = useState("all");
  const [tidy, setTidy] = useState(false);
  const d = tx.data;
  const short = (x) => shortOf(ov, x);
  const year = (ov.period?.start || "").slice(0, 4);
  const rows = useMemo(() => {
    if (!d) return [];
    return rowsFromList(d.rows, short).map((r, i) => {
      const place = d.rows[i].placement || [];
      return { ...r, companies: new Set([d.rows[i].company, ...place.filter((p) => p.brand).map((p) => d.brand_company[p.brand])]) };
    });
  }, [d]); // eslint-disable-line react-hooks/exhaustive-deps
  const needs = (r) => r.status === "decision" || r.status === "duplicate";
  const ofCo = (r) => co === "all" || r.companies.has(co);
  const words = q.trim().toLowerCase();
  const list = rows.filter((r) => ofCo(r)
    && (only === "all" || (only === "needs" && needs(r)) || (only === "changed" && r.status === "changed"))
    && (!words || [r.payee, r.what, r.paidLine, r.line, r.forWho.join(" "), r.qbo?.payee, r.qbo?.category]
      .join(" ").toLowerCase().includes(words)));
  const nNeeds = rows.filter((r) => ofCo(r) && needs(r)).length;
  return (
    <Panel>
      <Bar label="Company" right={periodLabel(ov.period)}>
        <Chip dark on={co === "all"} onClick={() => setCo("all")}>All</Chip>
        {ov.companies.map((c) => <Chip key={c} dark on={co === c} onClick={() => setCo(c)}>{short(c)}</Chip>)}
      </Bar>
      <Controls>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search a vendor or what it was"
               aria-label="Search the transactions"
               style={{ ...inputStyle, fontFamily: F.body, height: 32, width: "min(300px, 100%)" }} />
        {FILTERS.map(([k, label]) => (
          <Chip key={k} on={only === k} onClick={() => setOnly(k)}>{k === "needs" ? `${label} (${nNeeds})` : label}</Chip>
        ))}
        <div style={{ flex: 1 }} />
        {d && <span style={{ fontFamily: F.num, fontSize: 11, color: L.ghost, whiteSpace: "nowrap" }}>
          {list.length} of {rows.length} shown</span>}
      </Controls>
      <StatePanel loading={tx.loading} error={tx.error} retry={tx.retry}>
        {d && (
          <div className="cx-page" style={{ padding: "18px 26px 26px", display: "grid", gap: 14 }}>
            <Sheet right={<Legend />} title={<>Transactions <span style={{ fontFamily: F.body, fontSize: 12.5,
              fontWeight: 400, color: L.muted }}>· by month, oldest first</span></>}>
              {list.length ? <TxList rows={list} signed year={year} /> : (
                <div style={{ padding: 16, fontFamily: F.body, fontSize: 13, color: L.muted }}>Nothing matches.</div>
              )}
            </Sheet>
            {d.vendor_names?.length ? (
              <Sheet>
                <button onClick={() => setTidy(!tidy)} aria-expanded={tidy} style={{ width: "100%", display: "flex",
                  gap: 10, alignItems: "baseline", flexWrap: "wrap", padding: "11px 18px", border: 0, cursor: "pointer",
                  background: L.colHead, textAlign: "left" }}>
                  <span style={{ fontFamily: F.num, fontSize: 14, fontWeight: 700, color: L.deep }}>
                    Vendors QuickBooks spells more than one way <span style={{ color: L.code }}>{tidy ? "▾" : "▸"}</span></span>
                  <span style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>
                    {d.vendor_names.length} vendors · the list above already shows one name each</span>
                </button>
                {tidy ? (
                  <div style={{ padding: "8px 18px 12px", display: "grid", gap: 6, borderTop: `1px solid ${L.ruleHeader}` }}>
                    {d.vendor_names.map((v) => (
                      <div key={v.names.join("|")} style={{ display: "flex", gap: 10, alignItems: "baseline",
                        flexWrap: "wrap", fontFamily: F.body, fontSize: 12.5 }}>
                        <span style={{ flex: 1, minWidth: 220, color: L.body }}>{v.names.map((n) => `“${n}”`).join(" · ")}</span>
                        <span style={{ ...NUM, color: L.muted }}>{money(v.spend)} spent</span>
                      </div>
                    ))}
                    <div style={{ fontFamily: F.body, fontSize: 12, color: L.muted, marginTop: 4 }}>
                      Merging them in QuickBooks (Vendors → merge) tidies QuickBooks too.</div>
                  </div>
                ) : null}
              </Sheet>
            ) : null}
          </div>
        )}
      </StatePanel>
      <style>{`@media (max-width: 640px) { .cx-page { padding: 14px 10px 18px !important; } }`}</style>
    </Panel>
  );
}
