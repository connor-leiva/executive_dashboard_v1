/* Books Cleanup · Budget -- what each company's cost lines were (the corrected year so far, a month at the
   last three months' pace, a share of revenue) beside what they need to be. A projected company's plan is
   editable (owners and admins) and feeds its Forecast, Cash flow and the Statement's future months; for the
   file's own, the retreats and the commissions are planned on Forecast. With its members counted, each line
   reads as what it costs to serve one member a month. Every figure is the server's. */
import { useState } from "react";
import { StatePanel } from "../books/ui.jsx";
import { useCleanupForward } from "./useCleanup.js";
import { Bar, Chip, EYEBROW, F, L, NUM, Note, NumberField, Panel, SaveLine, Sheet, money, shortOf } from "./kit.jsx";
import { CF_CSS, LiveError, Tile, useSaver } from "./CleanupForecast.jsx";

export default function CleanupBudget({ ov, rev, onSaved }) {
  const fw = useCleanupForward(rev);
  const can = !!fw.data?.can_decide;
  const [co, setCo] = useState(ov.forward_company || ov.companies[0]);
  const [state, save] = useSaver(onSaved);
  const b = fw.data?.budgets?.[co];
  const f = (fw.data?.forwards || []).find((x) => x.company === co) || null;
  const planned = !!f;
  // projected from the members' records, but the projection didn't build this time: no plan to show, and why
  const unbuilt = !planned && !!fw.data?.live_error && (ov.forward_companies || []).includes(co);
  const per = planned && f.members ? f.members : 0;
  const fromForecast = (line) => (planned ? f.plan_from?.[line] : null);
  const planOf = (line) => {
    if (!planned) return null;
    if (fromForecast(line)) return (f.future_by_line[line] || 0) / (f.months_ahead || 1);
    return f.plan[line] ?? null;
  };
  const lines = b ? b.lines : [];
  const monthly = lines.reduce((n, l) => n + (planOf(l.line) || 0), 0);
  const recent = lines.reduce((n, l) => n + l.recent, 0);
  const heads = ["Cost line", "So far (corrected)", "% of revenue", "A month, last 3 months", "Plan a month",
                 ...(per ? ["A member a month"] : []), `Plan through ${f?.statement?.through || "the horizon"}`, "Why"];
  return (
    <Panel>
      <Bar label="Budget" right="what it was, and what it needs to be">
        {ov.companies.map((c) => <Chip key={c} dark on={c === co} onClick={() => setCo(c)}>{shortOf(ov, c)}</Chip>)}
      </Bar>
      <StatePanel loading={fw.loading} error={fw.error} retry={fw.retry}>
        <LiveError fw={fw} />
        {b && (
          <div className="cf-page" style={{ padding: "18px 26px 26px", display: "grid", gap: 14 }}>
            <div className="cf-tiles4">
              <Tile label="Revenue so far (corrected)" value={b.revenue} />
              <Tile label="Net operating income so far" value={b.noi} loss />
              <Tile label="Costs a month, last 3 months" value={recent}
                    sub={per ? `${money(recent / per)} a member, at ${per} members` : null} />
              <Tile label="Costs a month, planned" value={planned ? monthly : recent}
                    sub={unbuilt ? "no plan: its projection didn't build" : !planned ? "the plan for this company comes next"
                      : per ? `${money(monthly / per)} a member · feeds Forecast and Cash flow` : "feeds Forecast and Cash flow"} />
            </div>
            <Sheet right={<SaveLine state={state} />} title={planned ? `${shortOf(ov, co)}'s plan, line by line`
              : `${shortOf(ov, co)}'s cost lines`}>
              <div style={{ overflowX: "auto" }}>
                <table style={{ borderCollapse: "collapse", width: "100%", minWidth: per ? 1000 : 900, fontFamily: F.body, fontSize: 13 }}>
                  <thead><tr>{heads.map((h, i) => (
                    <th key={h} style={{ ...EYEBROW, textAlign: i === 0 || i === heads.length - 1 ? "left" : "right",
                                         padding: "9px 12px", background: L.colHead }}>{h}</th>))}</tr></thead>
                  <tbody>{lines.map((l) => {
                    const p = planOf(l.line);
                    const editable = planned && can && !fromForecast(l.line);
                    const hot = l.pct !== null && l.pct >= 15;
                    return (
                      <tr key={l.line} style={{ borderTop: `1px solid ${L.ruleLine}`, background: hot ? L.flagBg : L.paper }}>
                        <td style={{ padding: "8px 12px", color: L.deep, fontWeight: 600 }}>{l.line}</td>
                        <td style={{ ...NUM, padding: "8px 12px", color: L.deep }}>{money(l.ytd)}</td>
                        <td style={{ ...NUM, padding: "8px 12px", color: hot ? L.flagInk : L.muted, fontWeight: hot ? 700 : 400 }}>
                          {l.pct === null ? "—" : `${l.pct.toFixed(0)}%`}</td>
                        <td style={{ ...NUM, padding: "8px 12px", color: L.bodyDim }}>{money(l.recent)}</td>
                        <td style={{ ...NUM, padding: "8px 12px" }}>
                          {editable ? <NumberField value={Math.round(p || 0)} prefix="$" width={90} label={`${l.line}: plan a month`}
                                                   onSave={(v) => save([{ path: [...f.paths.budget, l.line], value: v }])} />
                            : p === null ? <span style={{ color: L.ghost }}>—</span>
                              : <span style={{ color: L.infoInk }}>{money(p)}</span>}</td>
                        {per ? <td style={{ ...NUM, padding: "8px 12px", color: L.bodyDim }}>
                          {p === null ? <span style={{ color: L.ghost }}>—</span> : money(p / per)}</td> : null}
                        <td style={{ ...NUM, padding: "8px 12px", color: L.deep }}>
                          {p === null ? <span style={{ color: L.ghost }}>—</span> : money(p * (f.months_ahead || 0))}</td>
                        <td style={{ padding: "8px 12px", color: L.muted, fontSize: 12 }}>
                          {planned ? (fromForecast(l.line) ? `${f.notes?.[l.line] || ""} Set on Forecast.`.trim() : f.notes?.[l.line] || "") : ""}</td>
                      </tr>);
                  })}</tbody>
                </table>
              </div>
            </Sheet>
            <Note tone="gold">Highlighted: lines at 15% of revenue or more so far.
              {per ? ` A member a month is the plan over the ${per} members ${f.source === "live" ? "on the records today" : "in the contracts"}.` : ""}
              {planned && !can ? " Only an owner or admin changes the plan." : ""}</Note>
          </div>
        )}
      </StatePanel>
      <style>{CF_CSS}</style>
    </Panel>
  );
}
