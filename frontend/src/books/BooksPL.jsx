/* Books · P&L — account-level statement per entity (or consolidated), with MoM deltas.
   Totals come from the QuickBooks summary snapshot; the lines are the detail behind it. */
import { useState } from "react";
import { T } from "../theme.js";
import { useBooksPL } from "./useBooks.js";
import { Card, Eyebrow, Delta, Pill, StatePanel, ent, font, usd } from "./ui.jsx";

const TABS = [["all", "Consolidated"], ["ulrg", "ULRG"], ["springb", "Spring B"], ["sympli", "Sympli"]];

function Selector({ value, onChange }) {
  return (
    <div style={{ display: "inline-flex", gap: 4, background: T.parchment, border: `1px solid ${T.line}`,
                  borderRadius: 10, padding: 3 }}>
      {TABS.map(([k, label]) => (
        <button key={k} onClick={() => onChange(k)} style={{
          fontFamily: font.body, fontSize: 12.5, fontWeight: 600, cursor: "pointer", borderRadius: 8,
          padding: "6px 12px", border: "none",
          color: value === k ? T.ink : T.slate, background: value === k ? T.white : "transparent",
          boxShadow: value === k ? "0 1px 3px rgba(0,46,44,.08)" : "none" }}>{label}</button>
      ))}
    </div>
  );
}

function Stat({ label, v, pv, invert }) {
  return (
    <div style={{ flex: 1, minWidth: 150 }}>
      <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted }}>{label}</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginTop: 3 }}>
        <span style={{ fontFamily: font.head, fontSize: 24, fontWeight: 700, color: T.ink }}>{usd(v || 0)}</span>
        <Delta v={v} pv={pv} invert={invert} />
      </div>
    </div>
  );
}

function LineRow({ label, v, pv, invert, indent }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                  padding: "7px 0", borderBottom: `1px solid ${T.line}`, paddingLeft: indent ? 16 : 0 }}>
      <span style={{ fontFamily: font.body, fontSize: 13, color: indent ? T.tertiary : T.secondary,
                     fontWeight: indent ? 400 : 500 }}>{label}</span>
      <span style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <Delta v={v} pv={pv} invert={invert} />
        <span style={{ fontFamily: font.body, fontSize: 13, fontWeight: 600, color: T.ink,
                       minWidth: 92, textAlign: "right" }}>{usd(v)}</span>
      </span>
    </div>
  );
}

function OpexGroup({ g }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ borderBottom: `1px solid ${T.line}` }}>
      <div onClick={() => setOpen((o) => !o)} style={{ display: "flex", alignItems: "center",
        justifyContent: "space-between", padding: "7px 0", cursor: g.lines?.length ? "pointer" : "default" }}>
        <span style={{ fontFamily: font.body, fontSize: 13, fontWeight: 600, color: T.secondary }}>
          {g.lines?.length ? (open ? "▾ " : "▸ ") : ""}{g.cat}</span>
        <span style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Delta v={g.v} pv={g.pv} invert />
          <span style={{ fontFamily: font.body, fontSize: 13, fontWeight: 600, color: T.ink, minWidth: 92, textAlign: "right" }}>{usd(g.v)}</span>
        </span>
      </div>
      {open && (g.lines || []).map((ln, i) => (
        <LineRow key={i} label={ln.label} v={ln.v} pv={ln.pv} invert indent />
      ))}
    </div>
  );
}

function Section({ title, rows, invert }) {
  if (!rows?.length) return null;
  return (
    <Card style={{ marginTop: 16 }}>
      <Eyebrow>{title}</Eyebrow>
      {rows.map((r, i) => <LineRow key={i} label={r.label} v={r.v} pv={r.pv} invert={invert} />)}
    </Card>
  );
}

export default function BooksPL({ period = "mtd" }) {
  const [business, setBusiness] = useState("all");
  const { data, loading, error, retry } = useBooksPL(business, period);
  const t = data?.totals || {};
  const prior = t.prior || {};
  return (
    <div style={{ display: "grid", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
        <Selector value={business} onChange={setBusiness} />
        {data?.period_label && (
          <span style={{ fontFamily: font.body, fontSize: 12.5, color: T.muted }}>
            {data.period_label} · booked (QuickBooks)</span>
        )}
      </div>

      <StatePanel loading={loading} error={error} retry={retry}
        empty={data && !data.revenue?.length && !data.opex?.length}
        emptyTitle="No P&L detail yet"
        emptyMsg="P&L lines appear once QuickBooks is connected and synced.">
        {data && (
          <>
            <Card>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 20 }}>
                <Stat label="Revenue" v={t.revenue} pv={prior.revenue} />
                <Stat label="Gross profit" v={t.gross_profit} pv={prior.gross_profit} />
                <Stat label="Operating expenses" v={t.opex} pv={prior.opex} invert />
                <Stat label="Net operating income" v={t.noi} pv={prior.noi} />
              </div>
              {data.jv_share != null && (
                <div style={{ marginTop: 14, paddingTop: 12, borderTop: `1px solid ${T.line}`,
                              display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontFamily: font.body, fontSize: 13, color: T.secondary }}>
                    Spring's JV share ({Math.round(data.jv_share * 100)}%)</span>
                  <span style={{ fontFamily: font.head, fontSize: 16, fontWeight: 700, color: T.meadowInk }}>
                    {usd((t.noi || 0) * data.jv_share)}</span>
                </div>
              )}
              {data.eliminations?.applied && (
                <div style={{ marginTop: 14 }}>
                  <Pill tone="muted">Intercompany eliminations applied · {usd(data.eliminations.amount)}</Pill>
                </div>
              )}
            </Card>

            <Section title="Revenue" rows={data.revenue} />
            <Section title="Cost of sale" rows={data.cos} invert />
            {data.opex?.length > 0 && (
              <Card style={{ marginTop: 16 }}>
                <Eyebrow>Operating expenses</Eyebrow>
                {data.opex.map((g, i) => <OpexGroup key={i} g={g} />)}
              </Card>
            )}
          </>
        )}
      </StatePanel>
    </div>
  );
}
