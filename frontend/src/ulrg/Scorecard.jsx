/* The L10 Scorecard grid (SPEC 1.3). Week over week, newest LEFT (Part 0.3: stored ascending,
   reversed only at render), a collapsible cumulative panel, three-band color via band(), and a
   per-team Move card. All math is server-side; this renders data.groups[*].rows[*].cumulative. */
import { useState, Fragment } from "react";
import { useScorecard } from "./useScorecard.js";
import { C, FD, FB, FM, band } from "./scorecardMath.js";
import { Card, thL, thC, thCum, groupTh } from "./Parts.jsx";
import ScorecardRow from "./ScorecardRow.jsx";
import MoveCard from "./MoveCard.jsx";
import ShareButton from "./ShareButton.jsx";

const VIS_OPEN = 6, VIS_SHUT = 13;   // weeks shown when the panel is open / shut (Part 1.3)
const SRC_LABEL = { sisu: "Sisu", fub: "Follow Up Boss", ghl: "GoHighLevel", manual: "Entered by hand" };
const sourceLabel = (s) => SRC_LABEL[s] || s;

// colgroup percentages sum to 100 in every state, so freed width lands on the weeks (Part 1.3).
function widths(cum, nWeeks, earlier) {
  if (!cum) return [...[30, 3.5, 4.5, 2], ...Array(nWeeks).fill((100 - 40) / nWeeks)];
  const fixed = earlier ? [18, 3, 4, 8, 12.5, 4.5, 5.5, 14.5] : [18, 3, 4, 8, 13, 4.5, 5.5, 15];
  const used = fixed.reduce((a, b) => a + b, 0) + (earlier ? 2.5 : 0);
  return [...fixed, ...Array(nWeeks).fill((100 - used) / nWeeks), ...(earlier ? [2.5] : [])];
}

export default function Scorecard({ role }) {
  const { data } = useScorecard(13);
  const isAdmin = role === "owner" || role === "admin";
  const [win, setWin] = useState(13);        // window: 4 | 13 | "qtd"
  const [cum, setCum] = useState(true);      // cumulative panel open
  const [hideOk, setHideOk] = useState(false);
  const [open, setOpen] = useState({});

  if (!data) return <Card style={{ height: 280 }} pad={0}><div className="cc-skel" style={{ height: "100%" }} /></Card>;
  if (!data.groups || data.groups.length === 0) {
    return (
      <Card style={{ textAlign: "center", padding: "40px 28px" }}>
        <div style={{ fontFamily: FD, fontSize: 16, fontWeight: 600, color: C.ink }}>No measurables yet</div>
        <div style={{ fontFamily: FB, fontSize: 12.5, color: C.slate, marginTop: 8 }}>This scorecard hasn't been configured. Seed the measurables to see the grid.</div>
      </Card>
    );
  }

  const weeksAll = data.weeks || [];
  const vis = cum ? VIS_OPEN : VIS_SHUT;
  const weeksVisible = weeksAll.slice(-vis);
  const weeksDesc = weeksVisible.slice().reverse();          // newest first, for the header
  const offset = weeksAll.length - weeksVisible.length;
  const numeric = typeof win === "number";
  const wkey = numeric ? `w${win}` : "qtd";
  const counted = new Set(numeric
    ? weeksAll.slice(-win).map((w) => w.n)
    : weeksAll.filter((w) => w.start >= data.quarter.start).map((w) => w.n));
  const earlier = cum && numeric ? Math.max(0, win - vis) : 0;
  const cols = widths(cum, weeksVisible.length, earlier);
  const NCOL = 3 + (cum ? 5 + (earlier > 0 ? 1 : 0) : 1) + weeksVisible.length;
  const qtdThin = win === "qtd" && (data.quarter.weeks_closed || 0) < 3;
  const rowsOf = (g) => hideOk
    ? g.rows.filter((r) => { const c = r.cumulative && r.cumulative[wkey]; return !c || c.attain < 100; })
    : g.rows;
  const winLabel = (w) => (w === "qtd" ? "QTD" : `${w} wks`);

  return (
    <>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 18 }}>
        {data.groups.filter((g) => g.is_team_room).map((g) => (
          <MoveCard key={g.key} group={g} wkey={`w${data.default_window}`} />
        ))}
      </div>

      <Card pad={0}>
        <div style={{ padding: "15px 20px", borderBottom: `1px solid ${C.hair}`, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
            <span style={{ fontFamily: FD, fontSize: 15, fontWeight: 600, color: C.ink }}>L10 Scorecard</span>
            <span style={{ fontFamily: FB, fontSize: 11.5, color: C.slate }}>Week {data.current_week} newest. Click any measurable for the detail.</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
            <label style={{ display: "flex", alignItems: "center", gap: 7, fontFamily: FB, fontSize: 12, color: C.slate, cursor: "pointer" }}>
              <input type="checkbox" checked={hideOk} onChange={(e) => setHideOk(e.target.checked)} /> Only what is off
            </label>
            {isAdmin && <ShareButton />}
          </div>
        </div>

        {qtdThin && cum && (
          <div style={{ background: C.daffodilBg, borderBottom: `1px solid ${C.hair}`, padding: "13px 20px" }}>
            <div style={{ fontFamily: FM, fontSize: 9.5, letterSpacing: ".15em", textTransform: "uppercase", color: C.amberInk }}>Why this panel just went quiet</div>
            <div style={{ fontFamily: FB, fontSize: 12.5, color: C.body, lineHeight: 1.55, marginTop: 6 }}>
              The quarter turned over, so quarter to date is only {data.quarter.weeks_closed} week{data.quarter.weeks_closed === 1 ? "" : "s"} deep. It resets the
              cumulative read at the moment a multi-week hole matters most. Keep QTD for the rock review; use the 13-week window for the L10.
            </div>
          </div>
        )}

        <div style={{ overflowX: "auto" }}>
          <table style={{ borderCollapse: "collapse", width: "100%", tableLayout: "fixed", minWidth: cum ? 1120 : 860 }}>
            <colgroup>{cols.map((w, i) => <col key={i} style={{ width: `${w}%` }} />)}</colgroup>
            <thead>
              <tr>
                <th colSpan={3} style={{ ...groupTh, paddingLeft: 12 }}>Measurable</th>
                {cum ? (
                  <th colSpan={5 + (earlier > 0 ? 1 : 0)} style={{ ...groupTh, background: C.mist, borderLeft: `1px solid ${C.hair}`, borderRight: `1px solid ${C.hair}`, paddingLeft: 14 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 11, flexWrap: "wrap" }}>
                      <button onClick={() => setCum(false)} style={panelBtn} aria-expanded="true" title="Shut the panel, show more weeks">‹ Cumulative</button>
                      <span style={{ display: "flex", gap: 5 }}>
                        {(data.windows || [4, 13, "qtd"]).map((k) => (
                          <button key={String(k)} onClick={() => setWin(k)} style={segBtn(win === k)}>{winLabel(k)}</button>
                        ))}
                      </span>
                      <span style={{ fontFamily: FB, fontSize: 11, textTransform: "none", letterSpacing: 0, color: C.slate, fontWeight: 400 }}>
                        {earlier > 0 ? `${win} weeks counted, ${vis} shown` : "totals across the weeks marked below"}
                      </span>
                    </div>
                  </th>
                ) : (
                  <th style={{ ...groupTh, background: C.mist, borderLeft: `1px solid ${C.hair}`, borderRight: `1px solid ${C.hair}`, padding: 0 }}>
                    <button onClick={() => setCum(true)} title="Open the cumulative panel" style={{ ...panelBtn, padding: "6px 2px", width: "100%", justifyContent: "center" }} aria-expanded="false">›</button>
                  </th>
                )}
                <th colSpan={weeksVisible.length} style={groupTh}>Week by week · newest first · last {vis}</th>
              </tr>
              <tr>
                <th style={{ ...thL, paddingLeft: 32 }} />
                <th style={thC}>Own</th>
                <th style={thC}>Goal</th>
                {cum ? (
                  <>
                    <th style={{ ...thCum, borderLeft: `1px solid ${C.hair}`, paddingLeft: 14 }}>Actual</th>
                    <th style={thCum}>Pace to goal</th>
                    <th style={{ ...thCum, textAlign: "center" }}>4 wk</th>
                    <th style={{ ...thCum, textAlign: "right" }}>Gap</th>
                    <th style={{ ...thCum, paddingRight: 12 }}>To recover</th>
                  </>
                ) : (
                  <th style={{ ...thCum, padding: 0, borderLeft: `1px solid ${C.hair}`, borderRight: `1px solid ${C.hair}` }} />
                )}
                {weeksDesc.map((w) => {
                  const inWin = cum && counted.has(w.n);
                  return (
                    <th key={w.n} style={{ ...thC, background: inWin ? C.parchment : "transparent",
                      borderBottom: inWin ? `2px solid ${C.evergreen}` : `1px solid ${C.hair}` }}>
                      <div style={{ fontFamily: FM, fontSize: 9.5, color: inWin || !cum ? C.ink : C.muted }}>W{w.n}</div>
                      <div style={{ fontFamily: FB, fontSize: 10, color: C.muted, fontWeight: 400 }}>{w.label}</div>
                    </th>
                  );
                })}
                {earlier > 0 && (
                  <th title={`${earlier} counted weeks older than W${weeksVisible[0].n}`}
                    style={{ ...thC, background: C.parchment, borderBottom: `2px solid ${C.evergreen}`, padding: "8px 2px" }}>
                    <div style={{ fontFamily: FM, fontSize: 9, color: C.ink }}>+{earlier}</div>
                    <div style={{ fontFamily: FB, fontSize: 9.5, color: C.muted, fontWeight: 400 }}>older</div>
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {data.groups.map((g) => {
                const rows = rowsOf(g);
                const owner = g.owner && g.owner.name;
                return (
                  <FragmentGroup key={g.key} g={g} owner={owner} NCOL={NCOL}>
                    {rows.length === 0 && (
                      <tr><td colSpan={NCOL} style={{ ...thC, fontFamily: FB, fontSize: 12, color: C.meadowInk, padding: "14px 8px" }}>Everything at or above goal.</td></tr>
                    )}
                    {rows.map((r) => (
                      <ScorecardRow key={r.id} r={r} wkey={wkey} weeks={weeksVisible} offset={offset} counted={counted}
                        earlier={earlier} cum={cum} open={!!open[r.id]} sourceLabel={sourceLabel}
                        toggle={() => setOpen((o) => ({ ...o, [r.id]: !o[r.id] }))} />
                    ))}
                  </FragmentGroup>
                );
              })}
            </tbody>
          </table>
        </div>

        <div style={{ padding: "13px 20px 16px", borderTop: `1px solid ${C.hair}`, display: "flex", gap: 18, flexWrap: "wrap", alignItems: "center", fontFamily: FB, fontSize: 11.5, color: C.slate }}>
          <Legend b={band(105)} label="At or above" />
          <Legend b={band(90)} label="80 to 99" />
          <Legend b={band(60)} label="Under 80" />
          <span style={{ color: C.muted }}>Weeks run newest first. {cum ? `Panel open — the grid holds the last ${vis} weeks; the panel carries the longer history. Weeks under the dark rule feed the totals.` : `Panel shut — all ${vis} weeks on screen. The coloured tick carries each row's pace to goal.`}</span>
        </div>
      </Card>
    </>
  );
}

function FragmentGroup({ g, owner, NCOL, children }) {
  return (
    <Fragment>
      <tr>
        <td colSpan={NCOL} style={{ padding: 0 }}>
          <div style={{ background: C.evergreen, padding: "9px 14px", display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <span style={{ fontFamily: FD, fontSize: 12, fontWeight: 600, letterSpacing: ".07em", textTransform: "uppercase", color: C.onDark }}>{g.name}</span>
            <span style={{ fontFamily: FB, fontSize: 11, color: owner ? C.onDarkMute : C.daffodil }}>{owner || "Unassigned"}</span>
            {g.read && <span style={{ fontFamily: FB, fontSize: 11.5, color: C.onDarkMute, lineHeight: 1.45, flex: "1 1 320px", minWidth: 240 }}>{g.read}</span>}
          </div>
        </td>
      </tr>
      {children}
    </Fragment>
  );
}
function Legend({ b, label }) {
  return <span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
    <span style={{ width: 15, height: 15, borderRadius: 4, background: b.bg, border: `1px solid ${b.bar}` }} />{label}</span>;
}
const segBtn = (on) => ({ fontFamily: FB, fontSize: 11.5, padding: "4px 10px", borderRadius: 6, cursor: "pointer", letterSpacing: 0, textTransform: "none",
  border: `1px solid ${on ? C.evergreen : C.hair}`, background: on ? C.evergreen : C.surface, color: on ? C.onDark : C.slate, fontWeight: 400 });
const panelBtn = { display: "inline-flex", alignItems: "center", gap: 5, fontFamily: FM, fontSize: 9.5, letterSpacing: ".13em", textTransform: "uppercase",
  color: C.ink, background: "none", border: "none", cursor: "pointer", padding: 0 };
