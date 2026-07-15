/* Books · P&L — the formal statement per entity (or consolidated). Totals come from the
   QuickBooks summary snapshot; the lines are the account-level detail behind it. */
import { useState } from "react";
import { useBooksPL } from "./useBooks.js";
import { Card, Eyebrow, Delta, Pill, StatePanel, font, usd, signed, T } from "./ui.jsx";

// Fallback entity tabs for the sample/offline view; live data supplies the real list
// (whatever Integrations has connected/routed), so this stays in sync automatically.
const DEFAULT_ENTITIES = [["ulrg", "ULRG + Team"], ["springb", "Spring B"], ["sympli", "Sympli"]];

function PLRow({ label, v, pv, kind, inverse, indent }) {
  const tot = kind === "tot", sub = kind === "sub", head = kind === "head";
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline",
      padding: tot ? "13px 0 5px" : head ? "14px 0 4px" : "8px 0", paddingLeft: indent ? 18 : 0,
      borderTop: tot ? `2px solid ${T.evergreen}` : "none", borderBottom: sub ? `1px solid ${T.line}` : "none",
      marginTop: tot ? 8 : 0 }}>
      <span style={{ fontFamily: head ? font.head : font.body, fontSize: tot ? 14 : head ? 11 : 13,
        fontWeight: tot || sub ? 700 : head ? 700 : 400, letterSpacing: head ? "0.1em" : 0,
        textTransform: head ? "uppercase" : "none", color: head ? T.secondary : indent ? T.tertiary : T.ink }}>{label}</span>
      {!head && (
        <span style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
          <Delta v={v} pv={pv} inverse={inverse} />
          <span style={{ fontFamily: font.head, fontSize: tot ? 19 : 13.5, fontWeight: tot ? 700 : sub ? 600 : 500,
            color: inverse && !tot && !sub ? T.tertiary : T.ink, fontVariantNumeric: "tabular-nums",
            minWidth: 88, textAlign: "right" }}>{inverse && !tot && !sub ? signed(-v) : usd(v)}</span>
        </span>
      )}
    </div>
  );
}

export default function BooksPL({ period = "mtd" }) {
  const [business, setBusiness] = useState("all");
  const [openCats, setOpenCats] = useState({});
  const { data, loading, error, retry } = useBooksPL(business, period);
  const t = data?.totals || {};
  const prior = t.prior || {};
  const entTabs = data?.entities ? data.entities.map((e) => [e.key, e.name]) : DEFAULT_ENTITIES;
  const tabs = [["all", "Consolidated"], ...entTabs];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        {tabs.map(([k, l]) => (
          <button key={k} onClick={() => setBusiness(k)} style={{ fontFamily: font.head, fontSize: 12.5,
            fontWeight: 600, color: business === k ? T.onDark : T.slate,
            background: business === k ? T.evergreen : T.white,
            border: `1px solid ${business === k ? T.evergreen : T.line}`, borderRadius: 99,
            padding: "7px 16px", cursor: "pointer" }}>{l}</button>
        ))}
        <span style={{ flex: 1 }} />
        {data?.period_label && (
          <span style={{ fontFamily: font.body, fontSize: 12, color: T.muted }}>
            {data.period_label} · booked · QuickBooks</span>
        )}
      </div>

      <StatePanel loading={loading} error={error} retry={retry}
        empty={data && !data.revenue?.length && !data.opex?.length && !data.cos?.length}
        emptyTitle="No P&L detail yet"
        emptyMsg="P&L lines appear once QuickBooks is connected and synced.">
        {data && (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 14 }}>
              {[["Revenue", t.revenue, prior.revenue, false], ["Gross profit", t.gross_profit, prior.gross_profit, false],
                ["Operating expenses", t.opex, prior.opex, true], ["Net operating income", t.noi, prior.noi, false]
              ].map(([l, v, pv, inv], i) => (
                <Card key={i} style={{ padding: "15px 17px" }}>
                  <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.secondary, marginBottom: 7 }}>{l}</div>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 9 }}>
                    <span style={{ fontFamily: font.head, fontSize: 23, fontWeight: 700, color: T.ink,
                      fontVariantNumeric: "tabular-nums" }}>{usd(v || 0)}</span>
                    <Delta v={v} pv={pv} inverse={inv} />
                  </div>
                </Card>
              ))}
            </div>

            <Card style={{ maxWidth: 800 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
                <Eyebrow>{tabs.find(([k]) => k === business)?.[1]} · profit & loss</Eyebrow>
                <span style={{ fontFamily: font.body, fontSize: 11, color: T.muted }}>Δ vs prior month · click a category</span>
              </div>

              <PLRow label="Revenue" kind="head" />
              {(data.revenue || []).map((r, i) => <PLRow key={i} label={r.label} v={r.v} pv={r.pv} />)}
              <PLRow label="Total revenue" v={t.revenue} pv={prior.revenue} kind="sub" />

              {data.cos?.length > 0 && <PLRow label="Cost of sale" kind="head" />}
              {(data.cos || []).map((r, i) => <PLRow key={i} label={r.label} v={r.v} pv={r.pv} inverse />)}
              <PLRow label="Gross profit" v={t.gross_profit} pv={prior.gross_profit} kind="sub" />

              <PLRow label="Operating expenses" kind="head" />
              {(data.opex || []).map((c) => (
                <div key={c.cat}>
                  <button onClick={() => setOpenCats({ ...openCats, [c.cat]: !openCats[c.cat] })}
                    style={{ width: "100%", background: "transparent", border: "none", padding: 0, cursor: c.lines?.length ? "pointer" : "default", textAlign: "left" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", padding: "8px 0" }}>
                      <span style={{ fontFamily: font.body, fontSize: 13, fontWeight: 600, color: T.ink,
                        display: "inline-flex", alignItems: "center", gap: 7 }}>
                        {c.lines?.length > 0 && <span style={{ fontSize: 10, color: T.muted,
                          transform: openCats[c.cat] ? "rotate(90deg)" : "none", transition: "transform .15s", display: "inline-block" }}>▶</span>}
                        {c.cat}
                      </span>
                      <span style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
                        <Delta v={c.v} pv={c.pv} inverse />
                        <span style={{ fontFamily: font.head, fontSize: 13.5, fontWeight: 600, color: T.tertiary,
                          fontVariantNumeric: "tabular-nums", minWidth: 88, textAlign: "right" }}>{signed(-c.v)}</span>
                      </span>
                    </div>
                  </button>
                  {openCats[c.cat] && c.lines?.length > 0 && (
                    <div style={{ background: T.parchment, borderRadius: 9, padding: "2px 14px", marginBottom: 6 }}>
                      {c.lines.map((l, j) => <PLRow key={j} label={l.label} v={l.v} pv={l.pv} inverse indent />)}
                    </div>
                  )}
                </div>
              ))}
              <PLRow label="Total operating expenses" v={t.opex} pv={prior.opex} kind="sub" inverse />
              <PLRow label="Net operating income" v={t.noi} pv={prior.noi} kind="tot" />

              {data.jv_share != null && (
                <div style={{ display: "flex", justifyContent: "space-between", padding: "9px 0 0",
                  borderTop: `1px dashed ${T.line}`, marginTop: 6 }}>
                  <span style={{ fontFamily: font.body, fontSize: 13, fontStyle: "italic", fontWeight: 600, color: T.teal }}>
                    Spring's JV share ({Math.round(data.jv_share * 100)}%)</span>
                  <span style={{ fontFamily: font.head, fontSize: 14, fontStyle: "italic", fontWeight: 600, color: T.teal,
                    fontVariantNumeric: "tabular-nums" }}>{usd((t.noi || 0) * data.jv_share)}</span>
                </div>
              )}
              {data.eliminations?.applied && (
                <div style={{ marginTop: 14 }}>
                  <Pill tone="muted">Intercompany eliminations applied · {usd(data.eliminations.amount)}</Pill>
                </div>
              )}
              <div style={{ fontFamily: font.body, fontSize: 11, color: T.muted, marginTop: 16, lineHeight: 1.5,
                borderTop: `1px solid ${T.line}`, paddingTop: 12 }}>
                Every figure traces to QuickBooks — the system of record.</div>
            </Card>
          </>
        )}
      </StatePanel>
    </div>
  );
}
