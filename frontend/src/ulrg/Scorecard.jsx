/* The L10 Scorecard grid (SPEC 1.3) — v2 layout from the approved mockup: a frozen rail, an animated
   collapsible cumulative panel, edge-fade shadows and a single horizontal scroller so every week and
   the cumulative pane are reachable without hiding columns. Newest week LEFT (Part 0.3: stored
   ascending, reversed only at render). All math is server-side; this renders data.groups[*].rows[*].
   Office headshots are kept in the group bar (the mockup dropped them). */
import { useState, useMemo, useRef, useEffect, useLayoutEffect, useCallback } from "react";
import { useScorecard } from "./useScorecard.js";
import { fileUrl } from "../api.js";
import {
  T, FONT, NUM, EASE, DUR, LBL, Chevron,
  W_GOAL, RAIL, C_ACTUAL, C_PACE, C_TREND, C_GAP, C_REC, CUM, WK,
} from "./l10tokens.jsx";
import ScorecardRow from "./ScorecardRow.jsx";
import DrillDrawer from "./DrillDrawer.jsx";
import MoveCard from "./MoveCard.jsx";
import ShareButton from "./ShareButton.jsx";
import ScorecardSettings from "./ScorecardSettings.jsx";

export default function Scorecard({ role, shareToken = null, scope = "ulrg" }) {
  const { data, reload } = useScorecard(13, shareToken, scope);
  const isAdmin = !shareToken && (role === "owner" || role === "admin");   // no Share/Settings inside an embed
  const canEditValues = !shareToken;   // hand-entered KPIs are self-serve: anyone with scorecard access edits them (server enforces the same; auto rows stay admin-only)

  const [cumOpen, setCumOpen] = useState(true);
  const [rangeId, setRangeId] = useState("13");   // "4" | "13" | "qtd"
  const [onlyOff, setOnlyOff] = useState(false);
  const [expanded, setExpanded] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [drill, setDrill] = useState(null);   // {r, ws, we, label, weeks} — the clicked figure's drill-down
  const [edge, setEdge] = useState({ left: false, right: false });

  const scroller = useRef(null);
  const raf = useRef(0);

  /* DM Sans used to be fetched here — a fifth family, loaded by this component alone, that no
     workspace could change and no pairing offered. The board reads the workspace's own typeface
     now, which typefaces.js has already requested, so there is nothing left for this to load. */

  const measure = useCallback(() => {
    const el = scroller.current;
    if (!el) return;
    setEdge({ left: el.scrollLeft > 2, right: el.scrollLeft + el.clientWidth < el.scrollWidth - 2 });
  }, []);

  /* keep measuring across the width animation so the fades never go stale */
  const trackAnimation = useCallback(() => {
    cancelAnimationFrame(raf.current);
    const start = performance.now();
    const tick = () => {
      measure();
      if (performance.now() - start < DUR + 60) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
  }, [measure]);

  useLayoutEffect(() => {
    measure();
    const el = scroller.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => { ro.disconnect(); cancelAnimationFrame(raf.current); };
  }, [measure, data, cumOpen, rangeId, onlyOff]);

  const toggleCum = () => { setCumOpen((o) => !o); trackAnimation(); };

  const weeks = (data && data.weeks) || [];
  const weeksDesc = weeks.slice().reverse();                     // newest first (Part 0.3)
  const quarterStart = data && data.quarter ? data.quarter.start : null;
  const windows = (data && data.windows) || [4, 13, "qtd"];
  const win = rangeId === "qtd" ? "qtd" : Number(rangeId);
  const wkey = rangeId === "qtd" ? "qtd" : `w${rangeId}`;
  const cumLabel = rangeId === "qtd" ? "Quarter to date" : `Last ${rangeId} weeks`;

  // The in-progress week (complete === false) shows but never counts — matches the server's pace math.
  const counted = useMemo(() => {
    const done = weeksDesc.filter((w) => w.complete !== false);   // newest-first, completed weeks only
    if (rangeId === "qtd") return new Set(done.filter((w) => quarterStart && w.start >= quarterStart).map((w) => w.n));
    return new Set(done.slice(0, Number(rangeId)).map((w) => w.n));
  }, [rangeId, weeksDesc, quarterStart]);
  const countedN = counted.size;

  const cumAttain = (r) => (r.cumulative && r.cumulative[wkey] && r.cumulative[wkey].attain != null ? r.cumulative[wkey].attain : null);
  const view = useMemo(() => {
    if (!data || !data.groups) return [];
    return data.groups.map((g) => {
      const off = g.rows.filter((r) => { const a = cumAttain(r); return a != null && a < 100; }).length;
      // "Only what is off" keeps rows with no scoreable pace (snapshots / goal-0 / no data) visible,
      // as before — they aren't "off", so hiding them would falsely read as all-clear.
      const rows = onlyOff ? g.rows.filter((r) => { const a = cumAttain(r); return a == null || a < 100; }) : g.rows;
      return { ...g, rows, off };
    });
  }, [data, wkey, onlyOff]);

  if (!data) return <div style={{ fontFamily: FONT }}><div className="cc-skel" style={{ height: 320, borderRadius: 12 }} /></div>;
  if (!data.groups || data.groups.length === 0) {
    return (
      <div className="l10" style={{ fontFamily: FONT, color: T.ink }}>
        <div style={{ background: T.paper, borderRadius: 12, border: `1px solid ${T.line}`, padding: "44px 28px", textAlign: "center" }}>
          <div style={{ fontSize: 16, fontWeight: 700 }}>No measurables yet</div>
          <div style={{ fontSize: 12.5, color: T.muted, marginTop: 8 }}>This scorecard hasn't been configured. Seed the measurables to see the grid.</div>
        </div>
      </div>
    );
  }

  const totalW = RAIL + (cumOpen ? CUM : 0) + weeksDesc.length * WK;
  const liveN = data.current_week;
  const qtdThin = rangeId === "qtd" && data.quarter && (data.quarter.weeks_closed || 0) < 3;

  return (
    <>
      {drill && (
        <DrillDrawer drill={drill} canEdit={canEditValues} onClose={() => setDrill(null)} onSaved={reload} />
      )}
      {isAdmin && settingsOpen && (
        <ScorecardSettings groups={data.groups} scope={scope} onClose={() => setSettingsOpen(false)} onChanged={reload} />
      )}

      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 18 }}>
        {data.groups.filter((g) => g.is_team_room).map((g) => (
          <MoveCard key={g.key} group={g} wkey={`w${data.default_window}`} />
        ))}
      </div>

      <div className="l10" style={{ fontFamily: FONT, color: T.ink }}>
        <style>{`
          .l10, .l10 *, .l10 *::before, .l10 *::after { box-sizing: border-box; }
          .l10-scroll{
            overflow-x:auto; overflow-y:hidden;
            scroll-snap-type:x proximity; scroll-padding-left:${RAIL}px;
            overscroll-behavior-x:contain; scrollbar-width:thin; scrollbar-color:#DCD5C7 transparent;
          }
          .l10-scroll::-webkit-scrollbar{ height:9px; }
          .l10-scroll::-webkit-scrollbar-track{ background:transparent; }
          .l10-scroll::-webkit-scrollbar-thumb{ background:#DCD5C7; border-radius:5px; border:2px solid ${T.paper}; }
          .l10-scroll::-webkit-scrollbar-thumb:hover{ background:#C9C0AE; }
          .l10-wkcol{ scroll-snap-align:start; }
          /* the cumulative panel is CONDITIONALLY RENDERED (present when open, absent when collapsed),
             not width-animated: animating a flex item's size to collapse is unreliable in Chromium
             (the transition sticks), which is what broke the toggle. Rendering it in/out always works. */
          .l10-cell{ position:relative; }
          .l10-cell::after{ content:''; position:absolute; inset:0; pointer-events:none; background:rgba(18,41,31,0.05); opacity:0; transition:opacity 110ms ease; }
          .l10-row:hover .l10-cell::after{ opacity:1; }
          .l10-row:hover .l10-rail{ background:#FAF7F0; }
          .l10-btn:focus-visible{ outline:2px solid ${T.forest}; outline-offset:2px; border-radius:5px; }
          @media (prefers-reduced-motion: reduce){ .l10 *, .l10 *::after{ transition-duration:1ms !important; animation-duration:1ms !important; } }
        `}</style>

        <div style={{ background: T.paper, borderRadius: 12, border: `1px solid ${T.line}`, boxShadow: "0 1px 2px rgba(20,35,28,0.04), 0 8px 24px -12px rgba(20,35,28,0.10)", overflow: "hidden" }}>
          {/* --------------------------- title bar --------------------------- */}
          <div style={{ display: "flex", alignItems: "center", gap: 16, padding: "16px 20px", borderBottom: `1px solid ${T.line}`, flexWrap: "wrap" }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 12, minWidth: 0 }}>
              <h2 style={{ margin: 0, fontSize: 17, fontWeight: 700, letterSpacing: "-0.015em", whiteSpace: "nowrap" }}>L10 Scorecard</h2>
              <span style={{ fontSize: 12.5, color: T.muted, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                Week {liveN} is newest. Select any measurable to open its detail.
              </span>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={{ display: "flex", background: T.shell, borderRadius: 7, padding: 2, gap: 2 }}>
                {windows.map((k) => {
                  const id = String(k);
                  const on = id === rangeId;
                  return (
                    <button
                      key={id}
                      onClick={() => setRangeId(id)}
                      className="l10-btn"
                      style={{
                        border: "none", font: "inherit", fontSize: 11.5, fontWeight: on ? 600 : 500,
                        padding: "4px 11px", borderRadius: 5, cursor: "pointer",
                        background: on ? T.paper : "transparent", color: on ? T.ink : T.muted,
                        boxShadow: on ? "0 1px 2px rgba(20,35,28,0.10)" : "none",
                        transition: `background 180ms ${EASE}, color 180ms ${EASE}`,
                      }}
                    >
                      {k === "qtd" ? "QTD" : `${k} wks`}
                    </button>
                  );
                })}
              </div>
              <span style={{ ...NUM, fontSize: 11.5, color: T.faint, whiteSpace: "nowrap" }}>
                {countedN} week{countedN === 1 ? "" : "s"} counted{qtdThin ? " · quarter just turned" : ""}
              </span>
            </div>

            <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 14 }}>
              <label style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12.5, color: T.inkSoft, cursor: "pointer", whiteSpace: "nowrap" }}>
                <input type="checkbox" checked={onlyOff} onChange={(e) => setOnlyOff(e.target.checked)} style={{ width: 14, height: 14, accentColor: T.forest, cursor: "pointer" }} />
                Only what is off
              </label>
              {isAdmin && (
                <button
                  onClick={() => setSettingsOpen((v) => !v)}
                  className="l10-btn"
                  style={{ font: "inherit", fontSize: 12.5, fontWeight: 500, padding: "6px 13px", borderRadius: 6, border: `1px solid ${settingsOpen ? T.forest : T.line}`, background: "transparent", color: settingsOpen ? T.forest : T.inkSoft, cursor: "pointer", whiteSpace: "nowrap" }}
                >
                  Settings
                </button>
              )}
              {isAdmin && scope === "ulrg" && <ShareButton />}
            </div>
          </div>

          {/* --------------------------- scroll region --------------------------- */}
          <div style={{ position: "relative" }}>
            <div ref={scroller} className="l10-scroll" onScroll={measure}>
              <div style={{ width: totalW, minWidth: "100%" }}>
                <ColumnHeaders cumOpen={cumOpen} onToggleCum={toggleCum} weeksDesc={weeksDesc} liveN={liveN} />
                {view.map((g) => (
                  <div key={g.key}>
                    <GroupBar group={g} width={totalW} off={g.off} />
                    {g.rows.length === 0 && (
                      <div style={{ display: "flex", borderBottom: `1px solid ${T.lineSoft}` }}>
                        <div style={{ position: "sticky", left: 0, width: RAIL, flexShrink: 0, padding: "12px 16px", background: T.paper, fontSize: 12, color: T.good, borderRight: `1px solid ${T.line}` }}>Everything at or above goal.</div>
                        <div style={{ flex: 1, background: T.paper }} />
                      </div>
                    )}
                    {g.rows.map((r) => {
                      const key = `${g.key}-${r.id}`;
                      return (
                        <ScorecardRow
                          key={key}
                          r={r}
                          initials={(r.owner && r.owner.initials) || ""}
                          wkey={wkey}
                          weeksDesc={weeksDesc}
                          counted={counted}
                          cumOpen={cumOpen}
                          expanded={expanded === key}
                          onToggle={() => setExpanded(expanded === key ? null : key)}
                          onDrill={shareToken ? null : setDrill}
                          cumLabel={cumLabel}
                        />
                      );
                    })}
                  </div>
                ))}
              </div>
            </div>

            <div style={{ position: "absolute", top: 0, bottom: 0, left: RAIL, width: 24, pointerEvents: "none", background: "linear-gradient(90deg, rgba(20,35,28,0.08), rgba(20,35,28,0))", opacity: edge.left ? 1 : 0, transition: "opacity 200ms ease" }} />
            <div style={{ position: "absolute", top: 0, bottom: 0, right: 0, width: 44, pointerEvents: "none", background: `linear-gradient(270deg, ${T.paper} 18%, rgba(255,253,249,0))`, opacity: edge.right ? 1 : 0, transition: "opacity 200ms ease" }} />
          </div>

          {/* -------------------------------- footer ----------------------------- */}
          <div style={{ display: "flex", alignItems: "center", gap: 18, padding: "10px 20px", borderTop: `1px solid ${T.line}`, background: T.rail, flexWrap: "wrap" }}>
            {[["At or above goal", T.good], ["Within 20%", T.warn], ["Off pace", T.bad]].map(([label, c]) => (
              <div key={label} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ width: 8, height: 8, borderRadius: 2, background: c, opacity: 0.85 }} />
                <span style={{ fontSize: 11.5, color: T.muted }}>{label}</span>
              </div>
            ))}
            <span style={{ marginLeft: "auto", fontSize: 11.5, color: T.faint }}>Cumulative totals count only weeks with data.</span>
          </div>
        </div>
      </div>
    </>
  );
}

/* --------------------------------- header --------------------------------- */

function ColumnHeaders({ cumOpen, onToggleCum, weeksDesc, liveN }) {
  return (
    <div style={{ display: "flex", height: 46, borderBottom: `1px solid ${T.line}` }}>
      <div
        className="l10-rail"
        style={{ position: "sticky", left: 0, zIndex: 6, width: RAIL, flexShrink: 0, display: "flex", alignItems: "center", background: T.rail, borderRight: `1px solid ${T.line}` }}
      >
        <button
          onClick={onToggleCum}
          className="l10-btn"
          aria-expanded={cumOpen}
          style={{
            marginLeft: 12, display: "flex", alignItems: "center", gap: 6, height: 26, padding: "0 9px 0 7px",
            border: `1px solid ${cumOpen ? T.forest : T.line}`, background: cumOpen ? T.forest : "transparent",
            color: cumOpen ? T.paper : T.inkSoft, borderRadius: 5, cursor: "pointer", font: "inherit",
            fontSize: 10, fontWeight: 600, letterSpacing: "0.075em", textTransform: "uppercase",
            transition: `background 220ms ${EASE}, border-color 220ms ${EASE}, color 220ms ${EASE}`,
          }}
        >
          <Chevron open={cumOpen} size={10} color={cumOpen ? T.paper : T.muted} />
          Cumulative
        </button>
        <div style={{ marginLeft: "auto", marginRight: 10, display: "flex", alignItems: "center", gap: 12 }}>
          <span style={LBL}>Own</span>
          <span style={{ ...LBL, width: W_GOAL - 22, textAlign: "right" }}>Goal</span>
        </div>
      </div>

      {cumOpen && (
        <div style={{ width: CUM, flexShrink: 0, height: "100%", display: "flex", alignItems: "center", background: T.shell, borderRight: `1px solid ${T.line}` }}>
          <div style={{ width: C_ACTUAL, flexShrink: 0, paddingLeft: 14, ...LBL }}>Actual</div>
          <div style={{ width: C_PACE, flexShrink: 0, ...LBL }}>Pace to goal</div>
          <div style={{ width: C_TREND, flexShrink: 0, ...LBL, textAlign: "right" }}>Trend</div>
          <div style={{ width: C_GAP, flexShrink: 0, ...LBL, textAlign: "right" }}>Gap</div>
          <div style={{ width: C_REC, flexShrink: 0, paddingLeft: 14, ...LBL }}>To recover</div>
        </div>
      )}

      {weeksDesc.map((w) => {
        const live = w.n === liveN;
        return (
          <div
            key={w.n}
            className="l10-wkcol"
            style={{
              width: WK, flexShrink: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 1,
              background: live ? T.shell : "transparent", borderLeft: `1px solid ${live ? T.line : "transparent"}`,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
              {live && <span style={{ width: 4, height: 4, borderRadius: "50%", background: T.good }} />}
              <span style={{ ...NUM, ...LBL, color: live ? T.inkSoft : T.faint }}>W{w.n}</span>
            </div>
            <span style={{ ...NUM, fontSize: 9.5, color: T.faint }}>{w.label}</span>
          </div>
        );
      })}
    </div>
  );
}

/* --------------------------------- group ---------------------------------- */

function GroupBar({ group, width, off }) {
  const o = group.owner;
  const src = o && o.photo_url ? fileUrl(o.photo_url) : null;
  return (
    <div style={{ display: "flex", height: 34, background: T.forest, width, alignItems: "center" }}>
      <div style={{ position: "sticky", left: 0, zIndex: 5, width: RAIL, flexShrink: 0, height: "100%", display: "flex", alignItems: "center", gap: 9, padding: "0 12px 0 14px", background: T.forest }}>
        {o && (src
          ? <img src={src} alt="" style={{ width: 22, height: 22, borderRadius: "50%", objectFit: "cover", border: "1px solid rgba(255,253,249,0.25)", flexShrink: 0 }} />
          : <span style={{ width: 22, height: 22, borderRadius: "50%", background: "rgba(255,253,249,0.15)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 9, fontWeight: 600, color: T.paper, flexShrink: 0 }}>{((o.name || "·")[0] || "·").toUpperCase()}</span>)}
        <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.11em", textTransform: "uppercase", color: T.paper, whiteSpace: "nowrap" }}>{group.name}</span>
        {o && o.name && <span style={{ fontSize: 11.5, color: "rgba(255,253,249,0.5)", whiteSpace: "nowrap" }}>{o.name}</span>}
        <span style={{ ...NUM, marginLeft: "auto", fontSize: 9.5, fontWeight: 600, letterSpacing: "0.06em", textTransform: "uppercase", whiteSpace: "nowrap", color: off ? "rgba(255,253,249,0.7)" : "rgba(255,253,249,0.4)" }}>
          {off} off pace
        </span>
      </div>
    </div>
  );
}
