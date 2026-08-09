/* One measurable row + its expand detail (SPEC 1.3). Reads the server's cumulative block — the
   client never recomputes attainment/gap/required. Reverses values ONLY here, at render (Part
   0.3): storage/API stay ascending, so every derivation upstream is chronological. */
import { Fragment } from "react";
import { C, FD, FB, FM, band, verdictStyle, fmtV, sgn, isPct } from "./scorecardMath.js";
import { Avatar, Chip, tdL, tdC, cumCell } from "./Parts.jsx";

export default function ScorecardRow({ r, wkey, weeks, offset, counted, earlier, cum, open, toggle, sourceLabel }) {
  const cRaw = r.cumulative ? r.cumulative[wkey] : null;
  const c = cRaw && cRaw.attain != null ? cRaw : null;    // no scoreable goal → render as not-cumulative
  const b = c && band(c.attain), v = c && verdictStyle(c.verdict);
  const na = <span style={{ fontFamily: FB, fontSize: 11.5, color: C.muted }}>–</span>;
  const rate = r.type === "rate";
  const vals = r.values.slice(offset).slice().reverse();   // render newest first; storage ascending
  const wgoals = (r.week_goals || []).slice(offset).slice().reverse();   // each week's period goal
  const wk = weeks.slice().reverse();                      // index-aligned to vals
  const hand = !r.auto;      // HAND only on rows still entered by hand (no live resolver)
  const snap = r.type === "snapshot";

  return (
    <Fragment>
      <tr style={{ background: open ? C.hairSoft : "transparent" }}>
        <td style={{ ...tdL, paddingLeft: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <button onClick={toggle} aria-label="Show detail" aria-expanded={open} style={{ background: "none", border: "none", cursor: "pointer", padding: 0, width: 12,
              fontFamily: FM, fontSize: 12, color: C.muted, transform: open ? "rotate(90deg)" : "none", transition: "transform .15s" }}>›</button>
            {r.stage && <span style={{ fontFamily: FM, fontSize: 9, color: C.muted }}>{r.stage}</span>}
            <span style={{ fontFamily: FB, fontSize: 13, color: C.ink }}>{r.measurable}</span>
            {snap && <Chip dash>SNAPSHOT</Chip>}
            {hand && <Chip dash>HAND</Chip>}
            {r.cumulative_goal != null && <Chip dash>{`${r.cumulative_goal}/QTR`}</Chip>}
            {c && r.streak >= 3 && <Chip ink={C.poppyInk} bg={C.poppyBg}>IDS {r.streak}</Chip>}
          </div>
          {r.note && <div style={{ fontFamily: FB, fontSize: 10.5, color: C.muted, marginTop: 2, paddingLeft: 20 }}>{r.note}</div>}
        </td>
        <td style={tdC}><Avatar t={r.owner && r.owner.initials} /></td>
        <td style={{ ...tdC, fontFamily: FM, fontSize: 11.5, color: C.slate }}>≥{r.goal}{isPct(r) ? "%" : ""}</td>

        {cum ? (
          <>
            <td style={{ ...cumCell, borderLeft: `1px solid ${C.hair}`, paddingLeft: 14, fontFamily: FM, fontSize: 11.5, color: C.body }}>
              {c ? (rate ? `${c.actual.toFixed(1)}% avg` : `${c.actual} of ${c.target}`) : na}
            </td>
            <td style={cumCell}>
              {c ? (
                <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
                  <div style={{ position: "relative", height: 9, background: C.hairSoft, borderRadius: 99, overflow: "hidden", flex: "1 1 auto", minWidth: 44 }}>
                    <div style={{ width: `${(Math.min(125, Math.max(0, c.attain)) / 125) * 100}%`, height: "100%", background: b.bar, borderRadius: 99 }} />
                    <div style={{ position: "absolute", left: "80%", top: 0, width: 1, height: "100%", background: C.slate, opacity: .4 }} />
                  </div>
                  <span style={{ fontFamily: FD, fontSize: 15, fontWeight: 600, color: b.ink, fontVariantNumeric: "tabular-nums", flex: "0 0 auto" }}>{c.attain.toFixed(0)}%</span>
                </div>
              ) : na}
            </td>
            <td style={{ ...cumCell, textAlign: "center" }}>{c ? <DirInline d={r.trend_4v4} /> : na}</td>
            <td style={{ ...cumCell, textAlign: "right", fontFamily: FM, fontSize: 12, color: !c ? C.muted : c.gap >= 0 ? C.meadowInk : C.poppyInk }}>
              {c ? (rate ? `${sgn(c.gap, 1)} pt` : sgn(c.gap)) : na}
            </td>
            <td style={{ ...cumCell, paddingRight: 12 }}>
              {c ? (
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  {v && v.t !== "Ahead" && (
                    <span style={{ fontFamily: FM, fontSize: 11.5, color: C.slate, flex: "0 0 auto" }}>
                      {rate ? `${c.required.toFixed(0)}% avg` : `${c.required.toFixed(0)}/wk`}
                    </span>
                  )}
                  {v && <Chip ink={v.ink} bg={v.bg}>{v.t.toUpperCase()}</Chip>}
                </div>
              ) : <span style={{ fontFamily: FB, fontSize: 11.5, color: C.muted }}>not cumulative</span>}
            </td>
          </>
        ) : (
          <td style={{ padding: 0, borderBottom: `1px solid ${C.hairSoft}`, borderLeft: `1px solid ${C.hair}`, borderRight: `1px solid ${C.hair}`, textAlign: "center" }}>
            {c && <span title={`${c.attain.toFixed(0)}% cumulative`} style={{ display: "inline-block", width: 3, height: 16, borderRadius: 99, background: b.bar }} />}
          </td>
        )}

        {vals.map((val, i) => {
          const wgoal = wgoals[i] == null ? r.goal : wgoals[i];   // that week's period goal (honor a 0)
          const bb = val === null || val === undefined || !wgoal ? null : band((val / wgoal) * 100);
          const inWin = counted.has(wk[i].n);
          return (
            <td key={i} style={{ ...tdC, fontFamily: FD, fontSize: 13, fontWeight: 600, fontVariantNumeric: "tabular-nums",
              color: bb ? bb.ink : C.muted, background: bb ? bb.bg : "transparent", opacity: cum && !inWin ? .4 : 1 }}>
              {fmtV(r, val)}
            </td>
          );
        })}
        {earlier > 0 && <td style={{ padding: 0, borderBottom: `1px solid ${C.hairSoft}`, background: C.parchment }} />}
      </tr>

      {open && (
        <tr>
          <td colSpan={3 + (cum ? 5 + (earlier > 0 ? 1 : 0) : 1) + weeks.length} style={{ padding: 0, borderBottom: `1px solid ${C.hair}` }}>
            <div style={{ background: C.hairSoft, padding: "14px 22px 14px 34px", display: "flex", gap: 30, flexWrap: "wrap" }}>
              <div style={{ flex: "1 1 320px", minWidth: 260 }}>
                <Lbl>Direction</Lbl>
                <div style={{ fontFamily: FB, fontSize: 12.5, color: C.body, lineHeight: 1.6, marginTop: 7 }}>
                  {snap ? "Snapshot metric. It is a level, not a weekly count, so there is nothing to accumulate."
                    : r.trend_4v4 === null || r.trend_4v4 === undefined ? "Not enough history to compare four week blocks."
                    : Math.abs(r.trend_4v4) < 3 ? "Flat. The last four weeks match the four before."
                    : r.trend_4v4 > 0 ? `Improving. The last four weeks ran ${r.trend_4v4.toFixed(0)} points better than the four before.`
                    : `Still falling. The last four weeks ran ${Math.abs(r.trend_4v4).toFixed(0)} points worse than the four before, so the gap is widening.`}
                </div>
                {c && r.streak > 0 && (
                  <div style={{ fontFamily: FB, fontSize: 12.5, color: r.streak >= 3 ? C.poppyInk : C.slate, marginTop: 7 }}>
                    Off goal {r.streak} {r.streak === 1 ? "week" : "weeks"} running.{r.streak >= 3 ? " EOS says take it to IDS." : ""}
                  </div>
                )}
              </div>
              {c && (
                <div style={{ flex: "1 1 300px", minWidth: 260 }}>
                  <Lbl>The recovery maths</Lbl>
                  <div style={{ fontFamily: FB, fontSize: 12.5, color: C.body, lineHeight: 1.6, marginTop: 7 }}>
                    {c.gap >= 0
                      ? `Running ${sgn(c.gap, rate ? 1 : 0)}${rate ? " points" : ""} ${c.period ? "ahead of pace" : "above goal"} across ${c.n} weeks. Hold ${rate ? r.goal : (c.pace ?? r.goal)}${rate ? "%" : ""} a week and it stays there.`
                      : c.required == null
                      ? `No weeks left ${c.period ? "in the period" : "this quarter"} to close the ${Math.abs(c.gap).toFixed(rate ? 1 : 0)}${rate ? " point" : ""} gap.`
                      : `${c.required.toFixed(0)}${rate ? "%" : ""} a week closes the ${Math.abs(c.gap).toFixed(rate ? 1 : 0)}${rate ? " point" : ""} gap. The best single week in this window was ${c.best}${rate ? "%" : ""}.`}
                  </div>
                </div>
              )}
              <div style={{ flex: "0 1 170px" }}>
                <Lbl>Source</Lbl>
                <div style={{ fontFamily: FB, fontSize: 12.5, color: C.body, marginTop: 7 }}>{sourceLabel(r.source)}</div>
                <div style={{ fontFamily: FB, fontSize: 11.5, color: C.muted, marginTop: 3 }}>
                  {hand ? "No feed, typed each Monday" : (r.source_synced_at ? "Synced" : "Auto-sourced")}
                </div>
              </div>
            </div>
          </td>
        </tr>
      )}
    </Fragment>
  );
}

function Lbl({ children }) {
  return <div style={{ fontFamily: FM, fontSize: 9.5, letterSpacing: ".15em", textTransform: "uppercase", color: C.muted }}>{children}</div>;
}
function DirInline({ d }) {
  if (d === undefined || d === null) return <span style={{ fontFamily: FM, fontSize: 11, color: C.muted }}>–</span>;
  const flat = Math.abs(d) < 3, col = flat ? C.muted : d > 0 ? C.meadow : C.poppyInk;
  return <span style={{ display: "inline-flex", alignItems: "center", gap: 3, fontFamily: FM, fontSize: 11, color: col }}>
    <span style={{ fontSize: 12, lineHeight: 1 }}>{flat ? "→" : d > 0 ? "↑" : "↓"}</span>{!flat && <span>{Math.abs(d).toFixed(0)}</span>}</span>;
}
