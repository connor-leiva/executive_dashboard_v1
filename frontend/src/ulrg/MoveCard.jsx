/* This week's move (SPEC 1.4) — two items, never seven: the constraint (earliest funnel stage
   below goal) and the free win (lowest-attainment behavior row). Both metric ids are computed
   server-side and returned in group.move; here we just read those rows and render. */
import { C, FD, FB, band, verdictStyle } from "./scorecardMath.js";
import { Card, Chip, Eyebrow, Bar, Dir } from "./Parts.jsx";

export default function MoveCard({ group, wkey }) {
  const byId = (id) => group.rows.find((r) => r.id === id) || null;
  const cr = byId(group.move && group.move.constraint_metric_id);
  const fw = byId(group.move && group.move.free_win_metric_id);
  const crC = cr && cr.cumulative && cr.cumulative[wkey];
  const fwC = fw && fw.cumulative && fw.cumulative[wkey];
  const v = crC && verdictStyle(crC.verdict);
  const owner = group.owner && group.owner.name;

  return (
    <Card style={{ flex: "1 1 300px", minWidth: 280, borderTop: `3px solid ${crC ? band(crC.attain).bar : C.meadow}` }} pad={18}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div style={{ fontFamily: FD, fontSize: 13, fontWeight: 600, letterSpacing: ".07em", textTransform: "uppercase", color: C.ink }}>{group.name}</div>
        <span style={{ fontFamily: FB, fontSize: 11.5, color: owner ? C.slate : C.amberInk }}>{owner || "Unassigned"}</span>
      </div>
      {crC ? (
        <>
          <div style={{ marginTop: 14 }}>
            <Eyebrow>The constraint</Eyebrow>
            <div style={{ fontFamily: FD, fontSize: 17, fontWeight: 600, color: C.ink, marginTop: 6 }}>{cr.measurable}</div>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 7, marginBottom: 9 }}>
              <span style={{ fontFamily: FD, fontSize: 26, fontWeight: 600, color: band(crC.attain).ink, fontVariantNumeric: "tabular-nums" }}>{crC.attain.toFixed(0)}%</span>
              <Dir d={cr.trend_4v4} showLabel />
              <span style={{ fontFamily: FB, fontSize: 12, color: C.slate }}>{Math.abs(crC.gap)} behind over {crC.n} weeks</span>
            </div>
            <Bar pct={crC.attain} />
          </div>
          {v && (
            <div style={{ marginTop: 14, background: v.bg, borderRadius: 9, padding: "11px 13px" }}>
              <div style={{ fontFamily: FB, fontSize: 12.5, color: C.body, lineHeight: 1.55 }}>
                Closing this needs <strong style={{ fontWeight: 600, color: v.ink }}>{crC.required.toFixed(0)} a week</strong>. Best week in the window was {crC.best}.
              </div>
              <div style={{ marginTop: 8 }}><Chip ink={v.ink} bg={C.surface}>{v.t.toUpperCase()}</Chip></div>
            </div>
          )}
        </>
      ) : <div style={{ marginTop: 16, fontFamily: FB, fontSize: 13, color: C.meadowInk }}>Every funnel stage at or above goal.</div>}
      {fw && fwC && (
        <div style={{ marginTop: 14, paddingTop: 13, borderTop: `1px solid ${C.hairSoft}` }}>
          <Eyebrow>Winnable without more volume</Eyebrow>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 7, gap: 8 }}>
            <span style={{ fontFamily: FB, fontSize: 13, color: C.ink }}>{fw.measurable}</span>
            <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <Dir d={fw.trend_4v4} />
              <span style={{ fontFamily: "JetBrains Mono,monospace", fontSize: 12, color: band(fwC.attain).ink }}>{fwC.attain.toFixed(0)}%</span>
            </span>
          </div>
          <div style={{ fontFamily: FB, fontSize: 11.5, color: C.slate, marginTop: 5, lineHeight: 1.5 }}>
            A conversation on deals already in hand. No extra appointments required.
          </div>
        </div>
      )}
    </Card>
  );
}
