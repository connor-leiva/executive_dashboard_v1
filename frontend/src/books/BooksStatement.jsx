/* Books · Statement — the P&L rendered through the standard chart, with provenance
   (SPEC-coa-mapping-provenance 7).

   A flagged row is one whose number was composed rather than observed: part of it was funded
   by another entity. The treatment is a faint wash, a solid left edge, a circular marker
   before the account name, and the intercompany share in its own mono column.

   THE MARKER IS NOT OPTIONAL. Roughly one man in twelve has some colour vision deficiency,
   and a faint tint on a cream ground is low contrast for everyone. Meaning never lives in
   colour alone — the marker and the share column both carry it independently. */
import { useMemo, useState } from "react";
import { useCoaEntities, useStatement } from "./useBooks.js";
import { Card, Eyebrow, Pill, StatePanel, DARK, font, T } from "./ui.jsx";

/* Provenance uses the daffodil family by Connor's decision (2026-08-20), a declared exception
   to the Forum colour law rather than a quiet reuse. Tokens, never literals — the acceptance
   criteria grep for a raw hex here, and rightly. */
const WASH = T.daffodilBg;
const EDGE = T.daffodil;

const money = (n) =>
  (n < 0 ? "(" : "") + "$" + Math.abs(Math.round(n)).toLocaleString("en-US") + (n < 0 ? ")" : "");

const btn = (on) => ({
  fontFamily: font.head, fontSize: 12, fontWeight: 600, borderRadius: 8, padding: "6px 12px",
  cursor: "pointer", whiteSpace: "nowrap",
  color: on ? T.meadowInk : T.slate, background: on ? T.meadowBg : T.parchment,
  border: `1px solid ${on ? T.sprout : T.line}`,
});

const NUM = { fontFamily: font.body, fontVariantNumeric: "tabular-nums", textAlign: "right" };

function Row({ label, value, strong, rule }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 12, padding: "9px 16px",
      borderTop: rule ? `1px solid ${T.line}` : "none" }}>
      <span style={{ flex: 1, fontFamily: font.head, fontSize: strong ? 13.5 : 13,
                     fontWeight: strong ? 700 : 600, color: T.ink }}>{label}</span>
      <span style={{ ...NUM, fontSize: strong ? 14.5 : 13.5, fontWeight: strong ? 700 : 600,
                     color: T.ink, minWidth: 120 }}>{money(value)}</span>
      <span style={{ width: 58 }} />
    </div>
  );
}

/* One account. Expandable only when flagged — there is nothing to reveal otherwise. */
function Line({ line, expanded, toggle }) {
  const on = line.flagged;
  const open = expanded === line.standard_account_id;
  const act = () => on && toggle(line.standard_account_id);
  return (
    <>
      <div
        {...(on ? { role: "button", tabIndex: 0, "aria-expanded": open } : {})}
        onClick={act}
        onKeyDown={(e) => {
          if (!on) return;
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); act(); }
        }}
        title={line.definition || undefined}
        style={{
          display: "flex", alignItems: "baseline", gap: 12, padding: "7px 16px 7px 13px",
          background: on ? WASH : "transparent",
          borderLeft: `3px solid ${on ? EDGE : "transparent"}`,
          cursor: on ? "pointer" : "default",
        }}>
        <span style={{ flex: 1, display: "flex", alignItems: "baseline", gap: 7, minWidth: 0 }}>
          {/* The non-colour carrier of the same meaning. */}
          <span aria-hidden="true" style={{ width: 7, height: 7, borderRadius: 99, flexShrink: 0,
            background: on ? EDGE : "transparent",
            border: on ? `1px solid ${T.daffodilText}` : "1px solid transparent" }} />
          <span style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted,
                         fontVariantNumeric: "tabular-nums", flexShrink: 0 }}>{line.code}</span>
          <span style={{ fontFamily: font.body, fontSize: 12.5, color: T.secondary,
                         overflowWrap: "anywhere" }}>
            {line.name}
            {on && <span style={{ color: T.daffodilText }}> · part funded by another entity</span>}
          </span>
        </span>
        <span style={{ ...NUM, fontSize: 12.5, color: T.ink, minWidth: 120 }}>
          {money(line.amount)}</span>
        {/* Blank, never "0.0%" — a zero there would read as a measured result. */}
        <span style={{ ...NUM, fontSize: 11.5, color: T.daffodilText, width: 58 }}>
          {line.ic_share_pct === null || line.ic_share_pct === undefined
            ? "" : `${line.ic_share_pct.toFixed(1)}%`}</span>
      </div>
      {open && <Composition line={line} />}
    </>
  );
}

/* Direct amount, then one row per contribution, then a total that ties to the line above it. */
function Composition({ line }) {
  const direct = line.as_booked;
  return (
    <div className="coa-comp" style={{ background: T.parchment, padding: "12px 16px 14px 32px",
      borderLeft: `3px solid ${EDGE}` }}>
      <Eyebrow>How this number is made</Eyebrow>
      <div style={{ marginTop: 8, display: "grid", gap: 6 }}>
        <div style={{ display: "flex", gap: 12, fontFamily: font.body, fontSize: 12 }}>
          <span style={{ flex: 1, color: T.secondary }}>This entity&rsquo;s own activity</span>
          <span style={{ ...NUM, color: T.ink, minWidth: 110 }}>{money(direct)}</span>
        </div>
        {(line.contributions || []).map((c, i) => (
          <div key={i} style={{ display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
            <span style={{ flex: 1, minWidth: 200, fontFamily: font.body, fontSize: 12,
                           color: T.secondary }}>
              {/* The word, not only the sign: direction must never rest on a minus alone. */}
              <strong style={{ color: T.ink }}>
                {c.direction === "in" ? "Allocated in" : "Allocated out"}
              </strong>{" "}
              {c.direction === "in" ? "from" : "to"} {c.counterparty_business}
              <span style={{ color: T.muted }}>
                {" · "}{c.pool_name}
                {c.basis ? ` · ${c.basis}` : " · no stated basis"}
                {c.driver_source ? ` · ${c.driver_source}` : ""}
                {c.approved_by ? ` · approved by ${c.approved_by}` : ""}
                {c.je_ref ? ` · ${c.je_ref}` : ""}
              </span>
            </span>
            <span style={{ ...NUM, fontSize: 12, color: T.daffodilText, minWidth: 110 }}>
              {money(c.amount)}</span>
          </div>
        ))}
        <div style={{ display: "flex", gap: 12, borderTop: `1px solid ${T.line}`, paddingTop: 6 }}>
          <span style={{ flex: 1, fontFamily: font.head, fontSize: 12, fontWeight: 700,
                         color: T.ink }}>As it appears above</span>
          <span style={{ ...NUM, fontSize: 12, fontWeight: 700, color: T.ink, minWidth: 110 }}>
            {money(line.amount)}</span>
        </div>
      </div>
    </div>
  );
}

export default function BooksStatement({ period = "mtd" }) {
  const entities = useCoaEntities();
  const [bizId, setBizId] = useState(null);
  const [mode, setMode] = useState("allocated");
  const [threshold, setThreshold] = useState(null);      // null = the tenant default
  const [expanded, setExpanded] = useState(null);

  const list = entities.data?.entities || [];
  const active = bizId || list[0]?.id || null;
  const view = useStatement(active, { period, mode, threshold });
  const d = view.data;

  const flaggedCount = useMemo(() => {
    if (!d) return 0;
    return d.sections.reduce((n, sec) => n + sec.buckets.reduce(
      (m, b) => m + b.lines.filter((l) => l.flagged).length, 0), 0);
  }, [d]);

  const T_ROWS = d ? [
    ["Net revenue", d.totals.net_revenue, false],
    ["Cost of sale", d.totals.cost_of_sale, false],
    [d.business.gross_profit_label || "Gross profit", d.totals.gross_profit, true],
    ["Operating expenses", d.totals.operating_expenses, false],
    ["Net operating income", d.totals.net_operating_income, true],
    ["Below the line", d.totals.below_the_line, false],
    ["Net income", d.totals.net_income, true],
  ] : [];

  return (
    <StatePanel loading={entities.loading} error={entities.error} retry={entities.retry}
      empty={!entities.loading && list.length === 0}
      emptyTitle="No entities yet"
      emptyMsg="Connect a QuickBooks company in Settings and the statement builds on the next sync.">
      <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>

        <div style={{ borderRadius: 14, padding: "18px 22px", ...DARK }}>
          <Eyebrow onDark>Statement</Eyebrow>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
            {list.map((e) => {
              const on = e.id === active;
              return (
                <button key={e.id} onClick={() => { setBizId(e.id); setExpanded(null); }}
                  style={{ fontFamily: font.head, fontSize: 12.5, fontWeight: 600, borderRadius: 99,
                    padding: "7px 14px", cursor: "pointer",
                    border: `1px solid ${on ? "transparent" : "rgba(243,238,231,0.28)"}`,
                    background: on ? T.onDark : "transparent",
                    color: on ? T.evergreen : T.onDark }}>{e.name}</button>
              );
            })}
          </div>
        </div>

        <StatePanel loading={view.loading} error={view.error} retry={view.retry}>
          {d && (
            <>
              <Card style={{ padding: "14px 20px" }}>
                <div style={{ display: "flex", gap: 18, flexWrap: "wrap", alignItems: "center" }}>
                  <div style={{ display: "flex", gap: 6 }}>
                    <button style={btn(mode === "allocated")}
                      onClick={() => setMode("allocated")}>As allocated</button>
                    <button style={btn(mode === "booked")}
                      onClick={() => setMode("booked")}>As booked</button>
                  </div>
                  <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <span style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted }}>
                      Flag at</span>
                    {[["Any", 0], ["5%", 5], ["10%", 10]].map(([l, v]) => (
                      <button key={l} style={btn(threshold === v ||
                          (threshold === null && v === d.threshold_pct))}
                        onClick={() => setThreshold(v)}>{l}</button>
                    ))}
                  </div>
                  <div style={{ flex: 1, minWidth: 180, fontFamily: font.body, fontSize: 11.5,
                                color: T.muted, textAlign: "right" }}>
                    {flaggedCount} composed {flaggedCount === 1 ? "line" : "lines"}
                    {" · "}
                    {d.period.books_closed ? "books closed" : "books open"}
                    {d.period.synced_at ? ` · synced ${d.period.synced_at.slice(0, 10)}` : ""}
                  </div>
                </div>
                <div style={{ marginTop: 10, display: "flex", gap: 10, alignItems: "center",
                              flexWrap: "wrap" }}>
                  <Pill tone={d.tie_out.delta === 0 ? "good" : "bad"}>
                    Ties to QuickBooks · delta {money(d.tie_out.delta)} of {money(d.tie_out.gross)}
                  </Pill>
                  {d.tie_out.excluded !== 0 && (
                    <Pill tone="warn">Excluded {money(d.tie_out.excluded)}</Pill>
                  )}
                  <span style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted,
                                 flex: 1, minWidth: 240 }}>{d.caveat}</span>
                </div>
              </Card>

              <Card style={{ padding: 0, overflow: "hidden" }}>
                {d.sections.map((sec) => (
                  <div key={sec.key}>
                    <div style={{ fontFamily: font.head, fontSize: 11, fontWeight: 700,
                      letterSpacing: "0.1em", textTransform: "uppercase", color: T.tertiary,
                      background: T.parchment, padding: "8px 16px" }}>{sec.label}</div>
                    {sec.buckets.map((b) => (
                      <div key={b.key}>
                        <div style={{ display: "flex", padding: "8px 16px",
                          borderTop: `1px solid ${T.line}` }}>
                          <span style={{ flex: 1, fontFamily: font.head, fontSize: 12.5,
                                         fontWeight: 600, color: T.slate }}>{b.label}</span>
                          <span style={{ ...NUM, fontSize: 12.5, fontWeight: 600, color: T.slate,
                                         minWidth: 120 }}>{money(b.total)}</span>
                          <span style={{ width: 58 }} />
                        </div>
                        {b.lines.map((l) => (
                          <Line key={l.standard_account_id} line={l} expanded={expanded}
                                toggle={(id) => setExpanded(expanded === id ? null : id)} />
                        ))}
                      </div>
                    ))}
                  </div>
                ))}
              </Card>

              {d.statement === "pl" && (
                <Card style={{ padding: 0, overflow: "hidden" }}>
                  {T_ROWS.map(([label, value, strong], i) => (
                    <Row key={label} label={label} value={value} strong={strong} rule={i > 0} />
                  ))}
                  <div style={{ padding: "10px 16px", borderTop: `1px solid ${T.line}`,
                    fontFamily: font.body, fontSize: 11.5, color: T.muted }}>
                    Net income as booked {money(d.net_income_booked)} · as allocated{" "}
                    {money(d.net_income_allocated)}. The toggle changes the numbers, not the
                    styling.
                  </div>
                </Card>
              )}

              {d.exclusions?.length > 0 && (
                <Card>
                  <Eyebrow>Left out of this statement</Eyebrow>
                  <div style={{ marginTop: 8 }}>
                    {d.exclusions.map((x) => (
                      <div key={x.qbo_account_id} style={{ display: "flex", gap: 12,
                        fontFamily: font.body, fontSize: 12, padding: "5px 0", flexWrap: "wrap" }}>
                        <span style={{ flex: 1, minWidth: 180, color: T.secondary }}>
                          {x.fqn} <span style={{ color: T.muted }}>· {x.reason}</span></span>
                        <span style={{ ...NUM, color: T.ink }}>{money(x.amount)}</span>
                      </div>
                    ))}
                  </div>
                </Card>
              )}
            </>
          )}
        </StatePanel>
      </div>
      {/* Row expansion only, and fast. A fading highlight on financial data reads as
          instability, so the wash itself never transitions. */}
      <style>{`
        .coa-comp { animation: coaOpen 130ms ease-out; }
        @keyframes coaOpen { from { opacity: 0; transform: translateY(-3px); } }
        @media (prefers-reduced-motion: reduce) { .coa-comp { animation: none; } }
      `}</style>
    </StatePanel>
  );
}
