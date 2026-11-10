/* One measurable row for the L10 grid (v2 mockup), wired to the server payload. Values are stored
   ascending and reversed ONLY here at render (Part 0.3). All arithmetic — cumulative / pace / gap /
   required / verdict / trend — is server-side; this renders r.cumulative[wkey]. */
import { Fragment } from "react";
import { sgn } from "./scorecardMath.js";
import {
  T, NUM, LBL, tone, Chevron, Pip, PaceBar, StatusTag,
  W_IDX, W_NAME, W_OWN, W_GOAL, RAIL, C_ACTUAL, C_PACE, C_TREND, C_GAP, C_REC, CUM, WK, ROW_H,
} from "./l10tokens.jsx";

// Dollar rows (the Portfolio board) run to six and seven figures: separators from 1,000, and a
// compact 1.2M from a million, so the widest value still fits a week cell.
const bigNum = (v) => (Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(1)}M`
  : Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("en-US") : v);

function fmtCell(r, v) {
  if (v === null || v === undefined) return "—";
  if (r.unit === "usd") {
    const n = Math.round(v);
    const sign = n < 0 ? "−" : "";
    return `${sign}$${Math.abs(n).toLocaleString("en-US")}`;
  }
  if (r.unit === "pct" || r.type === "rate") return `${v % 1 ? Number(v).toFixed(1) : v}%`;
  return bigNum(v);
}

const PROV = {
  live: (r) => `${r.provenance_from || "Live"} today`,
  hand: () => "Hand",
  sync: () => "Needs sync",
};

export default function ScorecardRow({ r, initials, wkey, weeksDesc, counted, cumOpen, expanded, onToggle, onDrill, cumLabel, idsAfter = 3, dim = false }) {
  const cRaw = r.cumulative ? r.cumulative[wkey] : null;
  const c = cRaw && cRaw.attain != null ? cRaw : null;    // no scoreable goal → not a cumulative row
  const rate = r.type === "rate";
  const pct = c ? Math.round(c.attain) : null;
  const dp = rate && r.goal && r.goal < 10 ? 2 : 1;        // a 2.25% goal needs its second decimal

  const vals = (r.values || []).slice().reverse();          // newest first (storage ascending)
  const goals = (r.week_goals || []).slice().reverse();
  const t = r.trend_4v4;
  const nonNull = (r.values || []).filter((x) => x !== null && x !== undefined);

  // Drill-down: click any figure → the records behind it (auto) or its weekly value(s) to edit (manual).
  const _wk = (w, v) => ({ n: w.n, start: w.start, end: w.end, label: w.label, value: v == null ? null : v });
  const cellDrill = (i) => onDrill && onDrill({ r, ws: weeksDesc[i].start, we: weeksDesc[i].end,
    label: `W${weeksDesc[i].n} · ${weeksDesc[i].label}`, weeks: [_wk(weeksDesc[i], vals[i])] });
  const cumDrill = () => {
    if (!onDrill) return;
    const cw = weeksDesc.map((w, i) => ({ w, v: vals[i] })).filter(({ w }) => counted.has(w.n))
      .sort((a, b) => (a.w.start < b.w.start ? -1 : 1));
    if (!cw.length) return;
    onDrill({ r, ws: cw[0].w.start, we: cw[cw.length - 1].w.end, label: cumLabel || "Cumulative",
      weeks: cw.map(({ w, v }) => _wk(w, v)) });
  };

  return (
    <Fragment>
      <div className="l10-row" style={{ display: "flex", height: r.provenance ? ROW_H + 10 : ROW_H, borderBottom: `1px solid ${T.lineSoft}`, opacity: dim ? 0.55 : 1 }}>
        {/* ------------------------------- rail ------------------------------ */}
        <div
          className="l10-rail l10-cell"
          style={{ position: "sticky", left: 0, zIndex: 4, width: RAIL, flexShrink: 0, display: "flex", alignItems: "center", background: T.paper, borderRight: `1px solid ${T.line}` }}
        >
          <button
            onClick={onToggle}
            className="l10-btn"
            aria-expanded={expanded}
            aria-label={`Detail for ${r.measurable}`}
            style={{ width: W_IDX, flexShrink: 0, height: "100%", border: "none", background: "transparent", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", padding: 0 }}
          >
            <Chevron open={expanded} />
          </button>

          <div style={{ width: W_NAME, flexShrink: 0, minWidth: 0, display: "flex", flexDirection: "column", justifyContent: "center", gap: 3, paddingRight: 8 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
              <span title={r.measurable} style={{ fontSize: 14, fontWeight: 500, color: T.ink, letterSpacing: "-0.005em", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {r.measurable}
              </span>
              {r.cumulative_goal != null && (
                <span style={{ ...NUM, flexShrink: 0, fontSize: 12, fontWeight: 600, color: T.inkSoft, background: T.lineSoft, borderRadius: 3, padding: "1.5px 4px" }}>{r.cumulative_goal}/QTR</span>
              )}
              {r.streak >= idsAfter && (
                <span style={{ ...NUM, flexShrink: 0, fontSize: 12, fontWeight: 600, color: T.warn, background: T.warnBg, borderRadius: 3, padding: "1.5px 4px" }}>IDS {r.streak}</span>
              )}
            </div>
            {(r.provenance || r.target_by || (r.cadence && r.cadence !== "weekly") || r.check) && (
              <div style={{ display: "flex", gap: 4, minWidth: 0, overflow: "hidden" }}>
                {r.provenance && (
                  <span title={r.provenance_detail || ""} style={{ fontSize: 9, fontWeight: 600, letterSpacing: "0.04em", textTransform: "uppercase", color: T.inkSoft, border: `1px solid ${T.line}`, borderRadius: 3, padding: "1px 4px", whiteSpace: "nowrap" }}>
                    {(PROV[r.provenance] || PROV.hand)(r)}
                  </span>
                )}
                {r.cadence === "monthly" && <span style={{ fontSize: 9, fontWeight: 600, color: T.muted, whiteSpace: "nowrap" }}>Monthly</span>}
                {r.cadence === "event" && <span style={{ fontSize: 9, fontWeight: 600, color: T.muted, whiteSpace: "nowrap" }}>{r.cadence_label || "Per event"}</span>}
                {r.target_by && <span style={{ fontSize: 9, fontWeight: 600, color: T.muted, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.target_by}</span>}
                {r.check && <span title={r.check} style={{ fontSize: 9, fontWeight: 600, color: T.warn, whiteSpace: "nowrap" }}>Check</span>}
              </div>
            )}
          </div>

          <div style={{ width: W_OWN, flexShrink: 0, display: "flex", justifyContent: "center" }}>
            <div
              style={{
                width: 22, height: 22, borderRadius: "50%", background: T.shell, border: `1px solid ${T.line}`,
                display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 600, color: T.inkSoft,
              }}
            >
              {r.owner && r.owner.initials}
            </div>
          </div>

          <div style={{ width: W_GOAL, flexShrink: 0, textAlign: "right", paddingRight: 10 }}>
            {(r.goal == null || (r.goal === 0 && r.scored === false)) ? (
              <span title="No goal yet" style={{ fontSize: 10, fontWeight: 500, color: T.faint, border: `1px dashed ${T.faint}`, borderRadius: 4, padding: "1px 4px" }}>Not set</span>
            ) : (
              <>
                <span style={{ fontSize: 12, color: T.faint, marginRight: 1 }}>{r.direction === "lte" ? "≤" : "≥"}</span>
                <span style={{ ...NUM, fontSize: 13, fontWeight: 500, color: T.inkSoft }}>{fmtCell(r, r.goal)}</span>
              </>
            )}
          </div>

          <Pip pct={pct} />
        </div>

        {/* ---------------------------- cumulative --------------------------- */}
        {cumOpen && (
          <div
            className="l10-cell"
            onClick={onDrill ? cumDrill : undefined}
            title={onDrill ? "Click to see what's behind this" : undefined}
            style={{ width: CUM, flexShrink: 0, height: "100%", display: "flex", alignItems: "center", background: T.rail, borderRight: `1px solid ${T.line}`, cursor: onDrill ? "pointer" : "default" }}
          >
            {c ? (
              <>
                <div style={{ width: C_ACTUAL, flexShrink: 0, paddingLeft: 14, display: "flex", alignItems: "baseline", gap: 4 }}>
                  <span style={{ ...NUM, fontSize: 14, fontWeight: 600, color: T.ink }}>{r.unit === "usd" ? fmtCell(r, c.actual) : rate ? `${c.actual.toFixed(dp)}%` : c.actual}</span>
                  <span style={{ ...NUM, fontSize: 12, color: T.faint }}>{rate ? "avg" : `of ${c.target}`}</span>
                </div>
                <PaceBar pct={pct} />
                <div style={{ width: C_TREND, flexShrink: 0, textAlign: "right", ...NUM, fontSize: 12, color: t == null || Math.abs(t) < 3 ? T.faint : t > 0 ? T.good : T.bad }}>
                  {t == null || Math.abs(t) < 3 ? "—" : `${t > 0 ? "↑" : "↓"}${Math.abs(Math.round(t))}`}
                </div>
                <div style={{ width: C_GAP, flexShrink: 0, textAlign: "right", ...NUM, fontSize: 13, fontWeight: 500, color: c.gap >= 0 ? T.good : T.bad }}>
                  {rate ? `${sgn(c.gap, dp)}` : sgn(c.gap)}
                </div>
                <div style={{ width: C_REC, flexShrink: 0, paddingLeft: 14, display: "flex", alignItems: "center", gap: 7 }}>
                  <span style={{ ...NUM, fontSize: 12, fontWeight: 500, color: (c.verdict !== "ahead" && c.required != null) ? T.inkSoft : T.faint }}>
                    {c.verdict !== "ahead" && c.required != null ? (rate ? `${c.required.toFixed(dp === 2 ? 2 : 0)}% avg` : `${c.required.toFixed(0)}/wk`) : "—"}
                  </span>
                  <StatusTag verdict={c.verdict} />
                </div>
              </>
            ) : (
              <div style={{ paddingLeft: 14, fontSize: 12, color: T.faint }}>not cumulative</div>
            )}
          </div>
        )}

        {/* ------------------------------- weeks ----------------------------- */}
        {weeksDesc.map((w, i) => {
          const v = vals[i];
          const wgoal = goals[i] == null ? r.goal : goals[i];
          const tn = tone(v, wgoal, r.direction, wgoal === 0 && r.scored);
          const inWin = counted.has(w.n);
          const isNull = v === null || v === undefined;
          return (
            <div
              key={w.n}
              className="l10-wkcol l10-cell"
              onClick={onDrill ? () => cellDrill(i) : undefined}
              style={{
                width: WK, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center",
                background: tn.bg, borderLeft: `1px solid ${T.paper}`, opacity: inWin ? 1 : 0.3, transition: "opacity 260ms ease",
                cursor: onDrill ? "pointer" : "default",
              }}
            >
              <span style={{ ...NUM, fontSize: 13, fontWeight: isNull ? 400 : 600, color: tn.fg, letterSpacing: "-0.01em" }}>
                {isNull ? "—" : fmtCell(r, v)}
              </span>
            </div>
          );
        })}
      </div>

      {/* ------------------------------ drill-in ----------------------------- */}
      {expanded && (
        <div style={{ display: "flex", borderBottom: `1px solid ${T.line}`, background: T.shell }}>
          <div style={{ position: "sticky", left: 0, zIndex: 4, width: RAIL + (cumOpen ? CUM : 0), flexShrink: 0, padding: "13px 18px", background: T.shell, borderRight: `1px solid ${T.line}` }}>
            <div style={{ ...LBL, marginBottom: 5 }}>{r.measurable} · detail</div>
            <div style={{ fontSize: 13, color: T.inkSoft, lineHeight: 1.5 }}>
              {r.note && <div style={{ marginBottom: 6 }}>{r.note}</div>}
              {r.why && <div style={{ marginBottom: 6 }}>{r.why}</div>}
              {r.provenance_detail && <div style={{ marginBottom: 6 }}>{r.provenance_detail}</div>}
              {r.check && <div style={{ marginBottom: 6, color: T.warn }}>{r.check}</div>}
              {nonNull.length ? (
                <>
                  Best <strong style={NUM}>{fmtCell(r, Math.max(...nonNull))}</strong> · worst{" "}
                  <strong style={NUM}>{fmtCell(r, Math.min(...nonNull))}</strong> ·{" "}
                  <strong style={NUM}>{nonNull.length}</strong> week{nonNull.length === 1 ? "" : "s"} with data · {r.auto ? "auto-sourced" : "hand-entered"}, owned by {initials || (r.owner && r.owner.name) || "—"}.
                </>
              ) : (
                <>No data yet · {r.auto ? "auto-sourced" : "hand-entered"}, owned by {initials || (r.owner && r.owner.name) || "—"}.</>
              )}
            </div>
          </div>
        </div>
      )}
    </Fragment>
  );
}
