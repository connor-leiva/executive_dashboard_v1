/* Books Cleanup · Forecast -- the months after the books end, for the company the file projects (beCollective):
   its memberships still to be earned and collected, what the plan and the assumptions cost, and its partner's
   share as the year's profit builds (GET /cleanup/forward; services/books_cleanup.py Books.forward).

   Every figure is the server's. The assumptions (commissions on installments, the retreats, the partner's pay)
   are saved like any decision -- owners and admins -- and the page re-reads. No new members are assumed. */
import { useState } from "react";
import { StatePanel } from "../books/ui.jsx";
import { saveChanges, useCleanupForward } from "./useCleanup.js";
import { Bar, Button, Chip, EYEBROW, F, L, Loss, NUM, Note, NumberField, Panel, SaveLine, Sheet, inputStyle, keyLabel,
         money, reasonOf, shortOf } from "./kit.jsx";

/* Save one or more assumptions, then re-read everything (a projection moves the Statement and Cash flow too). */
export function useSaver(onSaved) {
  const [state, setState] = useState(null);
  const save = async (changes) => {
    setState({ busy: true });
    try { onSaved(await saveChanges(changes)); setState({ saved: "Saved." }); }
    catch (e) { setState({ error: reasonOf(e) }); }
  };
  return [state, save];
}

export function Tile({ label, value, sub, loss }) {
  return (
    <div style={{ border: `1px solid ${L.ruleInner}`, borderRadius: 12, background: L.paper, padding: "12px 14px",
                  display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
      <div style={{ ...EYEBROW, color: L.ghost }}>{label}</div>
      <div style={{ fontFamily: F.num, fontVariantNumeric: "tabular-nums", fontSize: 21, color: L.deep, letterSpacing: "-.02em" }}>
        {money(value)}{loss ? <Loss v={value} size={12} /> : null}</div>
      {sub ? <div style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted }}>{sub}</div> : null}
    </div>
  );
}

/* The revenue still to be earned, month by month: the dark part was already paid, the light part is still to come. */
function Bars({ schedule }) {
  const max = Math.max(1, ...schedule.map((r) => r.from_paid + r.from_future));
  const W = 900, H = 190, bw = W / schedule.length;
  return (
    <svg viewBox={`0 0 ${W} ${H + 34}`} style={{ width: "100%", height: "auto", display: "block" }} role="img"
         aria-label="Revenue still to be earned, month by month: already paid, and installments still to come">
      {schedule.map((r, i) => {
        const hp = (r.from_paid / max) * H, hf = (r.from_future / max) * H;
        return (
          <g key={r.key} transform={`translate(${i * bw + 6},0)`}>
            <rect x="0" y={H - hp - hf} width={bw - 12} height={hf} fill={L.accentMuted} opacity="0.55" rx="3" />
            <rect x="0" y={H - hp} width={bw - 12} height={hp} fill={L.accentInk} rx="3" />
            <text x={(bw - 12) / 2} y={H - hp - hf - 5} textAnchor="middle" fontSize="11" fontFamily={F.num} fill={L.bodyDim}>
              {Math.round((r.from_paid + r.from_future) / 1000)}k</text>
            <text x={(bw - 12) / 2} y={H + 16} textAnchor="middle" fontSize="10.5" fontFamily={F.num} fill={L.faint}>
              {i === 0 ? r.label.split(",")[0] : r.label.replace(" 20", " ’")}</text>
          </g>);
      })}
    </svg>
  );
}

const row = { display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" };

function Assumptions({ f, can, onSaved }) {
  const [state, save] = useSaver(onSaved);
  const a = f.assumptions;
  const months = f.months;
  const setRetreats = (list) => save([{ path: ["forward", "retreats"], value: list }]);
  const sel = { ...inputStyle, fontFamily: F.body };
  return (
    <Sheet title="Assumptions" right={can && f.meeting ? (
      <Button onClick={() => save([{ path: ["forward", "commission_pct"], value: f.meeting.commission_pct },
                                  { path: ["forward", "retreats"], value: a.retreats.map((r) => ({ ...r, on: !!f.meeting.retreats_on })) }])}>
        {f.meeting.label}</Button>) : null}>
      <div style={{ padding: "12px 18px", display: "grid", gap: 12, fontFamily: F.body, fontSize: 13, color: L.body }}>
        <div style={row}><span>Monthly costs, from Budget</span>
          <strong style={{ ...NUM, color: L.deep }}>{money(f.running)}</strong></div>
        <div style={row}><span>Sales commissions, on installments collected</span>
          <NumberField value={a.commission_pct} suffix="%" width={64} disabled={!can} label="Commission percent"
                       onSave={(v) => save([{ path: ["forward", "commission_pct"], value: v }])} /></div>
        <div style={{ display: "grid", gap: 6 }}>
          <span>Retreats</span>
          {a.retreats.map((r, i) => (
            <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <input type="checkbox" checked={!!r.on} disabled={!can} aria-label={`Count ${r.label}`}
                     onChange={(e) => setRetreats(a.retreats.map((x, j) => (j === i ? { ...x, on: e.target.checked } : x)))} />
              <span style={{ flex: 1, minWidth: 140 }}>{r.label}</span>
              <select value={r.key} disabled={!can} aria-label={`${r.label}: month`} style={{ ...sel, width: 120 }}
                      onChange={(e) => setRetreats(a.retreats.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))}>
                {months.map((m) => <option key={m.key} value={m.key}>{keyLabel(m.key)}</option>)}</select>
              <NumberField value={r.amount} prefix="$" width={86} disabled={!can} label={`${r.label}: cost`}
                           onSave={(v) => setRetreats(a.retreats.map((x, j) => (j === i ? { ...x, amount: v } : x)))} />
            </div>))}
        </div>
        {f.partner ? (
          <div style={row}><span>{f.partner}&rsquo;s pay</span>
            <span style={{ display: "inline-flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
              <NumberField value={a.partner_pay} prefix="$" width={86} disabled={!can} label={`${f.partner}'s pay a month`}
                           onSave={(v) => save([{ path: ["forward", "partner_pay"], value: v }])} /> a month through
              <select value={a.partner_end} disabled={!can} aria-label={`${f.partner}'s pay ends`} style={{ ...sel, width: 120 }}
                      onChange={(e) => save([{ path: ["forward", "partner_end"], value: e.target.value }])}>
                {months.map((m) => <option key={m.key} value={m.key}>{keyLabel(m.key)}</option>)}</select>
            </span></div>) : null}
        <div style={{ ...row, color: L.ghost, fontSize: 11.5 }}>
          <span>No new members are assumed. Everyone on a plan keeps paying.</span><SaveLine state={state} /></div>
        {!can ? <div style={{ fontSize: 11.5, color: L.ghost }}>Only an owner or admin changes these.</div> : null}
      </div>
    </Sheet>
  );
}

export default function CleanupForecast({ ov, rev, onSaved }) {
  const fw = useCleanupForward(rev);
  const f = fw.data?.forward;
  const can = !!fw.data?.can_decide;
  const short = (c) => shortOf(ov, c);
  const s = f?.summary;
  const through = f?.statement?.through;
  return (
    <Panel>
      <Bar label="Forecast" right={f ? `${short(f.company)} · through ${through}` : ""}>
        {f ? <Chip dark on>{short(f.company)}</Chip> : null}
      </Bar>
      <StatePanel loading={fw.loading} error={fw.error} retry={fw.retry}>
        {fw.data && !f ? <div style={{ padding: 24, fontFamily: F.body, color: L.muted }}>This file projects nothing ahead.</div> : null}
        {f && (
          <div className="cf-page" style={{ padding: "18px 26px 26px", display: "grid", gap: 16 }}>
            <div className="cf-tiles5">
              <Tile label="Contracted" value={f.contracts.contracted} sub={`${f.contracts.members} members`} />
              <Tile label="Collected" value={f.contracts.collected} sub="so far" />
              <Tile label="Still to collect" value={f.contracts.still_to_collect} sub="installments still to come" />
              <Tile label="Earned so far" value={f.contracts.earned_so_far} sub="counted as income" />
              <Tile label="Still to earn" value={f.contracts.still_to_earn} sub="in the months ahead" />
            </div>
            <Note>
              <strong>In plain words:</strong> a membership counts as income a little each day for its 12 months. Of the
              {" "}{money(f.contracts.still_to_earn)} still to be counted, the dark part of each bar is money members already
              paid (it&rsquo;s in the bank, or was spent); the light part comes from installments they haven&rsquo;t paid yet.
            </Note>
            <Sheet title="Revenue still to be earned, month by month">
              <div style={{ padding: "8px 14px 4px", overflowX: "auto" }}>
                <div style={{ minWidth: 560 }}><Bars schedule={f.schedule} /></div></div>
              <div style={{ display: "flex", gap: 16, padding: "0 18px 12px", fontFamily: F.body, fontSize: 12, color: L.bodyDim }}>
                <span><span style={{ display: "inline-block", width: 10, height: 10, background: L.accentInk, borderRadius: 2 }} /> already paid</span>
                <span><span style={{ display: "inline-block", width: 10, height: 10, background: L.accentMuted, opacity: 0.55, borderRadius: 2 }} />
                  {" "}installments still to come</span>
              </div>
            </Sheet>
            <div className="cf-two">
              <Assumptions f={f} can={can} onSaved={onSaved} />
              <Sheet title={`By ${through}`}>
                <div style={{ padding: "12px 18px", display: "grid", gap: 8, fontFamily: F.body, fontSize: 13.5, color: L.deep }}>
                  {[[`${short(f.company)}'s operating profit, ${keyLabel(f.rows[0]?.key)} – ${through}`, s.profit, true],
                    ...(f.partner ? [[`${f.partner} earns (${Math.round(f.rate * 100)}% of the profit)`, s.earned],
                                     [`${f.partner} is paid`, s.paid], ["Paid beyond the share earned", s.ahead]] : [])
                  ].map(([label, v, loss], i, all) => (
                    <div key={label} style={{ ...row, borderTop: i === all.length - 1 && f.partner ? `1.5px solid ${L.deep}` : "none",
                                              paddingTop: i === all.length - 1 && f.partner ? 8 : 0 }}>
                      <span style={{ fontWeight: i === all.length - 1 && f.partner ? 700 : 400 }}>{label}</span>
                      <span style={{ ...NUM, fontWeight: 600, color: i === all.length - 1 && v > 0 ? L.loss : L.deep }}>
                        {money(v)}{loss ? <Loss v={v} /> : null}</span>
                    </div>))}
                  {f.partner ? (
                    <Note tone={s.ahead > 0 ? "red" : "plain"} style={{ marginTop: 6 }}>
                      {s.ahead > 0
                        ? `At these assumptions ${f.partner} would be paid ${money(s.ahead)} more than the share earned by ${through}: an advance to be paid back.`
                        : `At these assumptions ${f.partner}'s share covers what's been paid.`}</Note>) : null}
                </div>
              </Sheet>
            </div>
            <Sheet title="Month by month" right={<span style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>
              shaded rows are projected</span>}>
              <div style={{ overflowX: "auto" }}>
                <table style={{ borderCollapse: "collapse", width: "100%", minWidth: 880, fontFamily: F.body, fontSize: 12.5 }}>
                  <thead><tr>{["Month", "Revenue", "Costs", "Profit", "Year so far",
                               ...(f.partner ? [`${f.partner}'s ${Math.round(f.rate * 100)}%`, "Earned so far", "Paid so far", "Paid beyond earned"] : [])]
                    .map((h, i) => <th key={h} style={{ ...EYEBROW, textAlign: i ? "right" : "left", padding: "8px 12px",
                                                       background: L.colHead }}>{h}</th>)}</tr></thead>
                  <tbody>{f.rows.map((r) => (
                    <tr key={r.key} style={{ borderTop: `1px solid ${L.ruleLine}`, background: r.projected ? L.infoBg : L.paper }}>
                      <td style={{ padding: "6px 12px", color: L.deep, fontWeight: 600 }}>{keyLabel(r.key)}</td>
                      {[r.rev, r.cost, r.profit, r.ytd, ...(f.partner ? [r.share, r.earned, r.paid, r.ahead] : [])].map((v, i) => (
                        <td key={i} style={{ ...NUM, padding: "6px 12px", color: [2, 3].includes(i) && v < 0 ? L.loss : L.deep }}>
                          {money(v)}</td>))}
                    </tr>))}</tbody>
                </table>
              </div>
              <div style={{ padding: "8px 16px 12px", fontFamily: F.body, fontSize: 12, color: L.muted }}>
                The month the books end in is actual so far and projected after. {f.partner ? `${f.partner}'s share counts only once the year's profit is above zero; each new year starts fresh.` : ""}</div>
            </Sheet>
            {f.launches.length ? (
              <Sheet title="What each launch cost, and what it contracted">
                <div className="cf-two" style={{ padding: "12px 16px" }}>
                  {f.launches.map((l) => (
                    <div key={l.name} style={{ border: `1px solid ${L.ruleInner}`, borderRadius: 12, padding: "12px 14px",
                                               fontFamily: F.body, fontSize: 13, color: L.body }}>
                      <div style={{ fontFamily: F.num, fontWeight: 700, color: L.deep }}>{l.name}</div>
                      <div style={{ fontSize: 12, color: L.muted }}>{l.window} · {l.members} members</div>
                      <div style={{ ...row, marginTop: 8 }}><span>Contracted</span><strong style={NUM}>{money(l.contracted)}</strong></div>
                      {Object.entries(l.costs).map(([k, v]) => (
                        <div key={k} style={{ ...row, fontSize: 12.5, color: L.bodyDim }}><span>{k}</span><span style={NUM}>{money(v)}</span></div>))}
                      <div style={{ ...row, borderTop: `1px solid ${L.ruleInner}`, marginTop: 6, paddingTop: 6 }}>
                        <span>Launch costs</span><strong style={NUM}>{money(l.cost_total)}</strong></div>
                      {l.per_dollar ? <div style={{ fontSize: 12.5, color: L.accentInk, marginTop: 6 }}>
                        ${l.per_dollar.toFixed(2)} contracted for every $1 of launch cost</div> : null}
                    </div>))}
                </div>
                <div style={{ padding: "0 16px 12px", fontFamily: F.body, fontSize: 12, color: L.muted }}>
                  Launches by date window: members&rsquo; first payments, and the ads, commissions, gifts and events in the
                  same months.</div>
              </Sheet>) : null}
          </div>
        )}
      </StatePanel>
      <style>{CF_CSS}</style>
    </Panel>
  );
}

export const CF_CSS = `
  .cf-tiles5 { display:grid; grid-template-columns: repeat(5, minmax(0,1fr)); gap:12px; }
  .cf-tiles4 { display:grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap:12px; }
  .cf-two { display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap:14px; }
  @media (max-width: 1080px) { .cf-tiles5, .cf-tiles4 { grid-template-columns: repeat(2, minmax(0,1fr)); }
                               .cf-two { grid-template-columns: minmax(0,1fr); } }
  @media (max-width: 560px) { .cf-page { padding: 14px 10px 18px !important; } .cf-tiles5, .cf-tiles4 { grid-template-columns: minmax(0,1fr); } }
`;
