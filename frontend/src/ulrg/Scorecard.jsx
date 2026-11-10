/* The L10 Scorecard grid (SPEC 1.3) — v2 layout from the approved mockup: a frozen rail, an animated
   collapsible cumulative panel, edge-fade shadows and a single horizontal scroller so every week and
   the cumulative pane are reachable without hiding columns. Newest week LEFT (Part 0.3: stored
   ascending, reversed only at render). All math is server-side; this renders data.groups[*].rows[*].
   Office headshots are kept in the group bar (the mockup dropped them). */
import { useState, useMemo, useRef, useEffect, useLayoutEffect, useCallback } from "react";
import { useScorecard } from "./useScorecard.js";
import { fileUrl, postJSON } from "../api.js";
import {
  T, FONT, NUM, EASE, DUR, LBL, Chevron,
  W_GOAL, RAIL, C_ACTUAL, C_PACE, C_TREND, C_GAP, C_REC, CUM, WK,
} from "./l10tokens.jsx";
import { titleCase } from "./scorecardMath.js";
import ScorecardRow from "./ScorecardRow.jsx";
import DrillDrawer from "./DrillDrawer.jsx";
import MoveCard from "./MoveCard.jsx";
import ShareButton from "./ShareButton.jsx";
import ScorecardSettings from "./ScorecardSettings.jsx";
import PlanGates from "./PlanGates.jsx";

const SOURCE_NAME = { arive: "Arive", sisu: "Sisu", ghl: "GHL", fub: "Follow Up Boss" };

function ago(iso) {
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (mins < 60) return `${mins}m ago`;
  if (mins < 2880) return `${Math.round(mins / 60)}h ago`;
  return `${Math.round(mins / 1440)}d ago`;
}

function addDays(iso, n) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  const [y, m, day] = d.toISOString().slice(0, 10).split("-");
  return `${y}-${m}-${day}`;
}

function isoWeek(iso) {
  const d = new Date(`${iso}T00:00:00Z`);
  const date = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const day = date.getUTCDay() || 7;
  date.setUTCDate(date.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(date.getUTCFullYear(), 0, 1));
  return Math.ceil((((date - yearStart) / 86400000) + 1) / 7);
}

function weekStub(iso, closed) {
  const [, m, day] = iso.split("-");
  return { n: isoWeek(iso), start: iso, end: addDays(iso, 6), label: `${Number(m)}/${day}`, complete: closed };
}

export default function Scorecard({ role, shareToken = null, scope = "ulrg", fixture = null }) {
  const { data: live, reload: liveReload } = useScorecard(13, shareToken, scope);
  const data = fixture || live;
  const reload = fixture ? () => {} : liveReload;
  const isAdmin = !shareToken && (role === "owner" || role === "admin");   // no Share/Settings inside an embed
  const canEditValues = !shareToken;   // hand-entered KPIs are self-serve: anyone with scorecard access edits them (server enforces the same; auto rows stay admin-only)

  const [cumOpen, setCumOpen] = useState(true);
  const [rangeId, setRangeId] = useState("13");   // "4" | "13" | "qtd" | "todate" | "full" | "past"
  const [pastKey, setPastKey] = useState("");
  const [onlyOff, setOnlyOff] = useState(false);
  const [lens, setLens] = useState("l10");
  const [deptKey, setDeptKey] = useState("enrollment");
  const [personKey, setPersonKey] = useState("J");
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
  const sprints = (data && data.sprints) || [];
  const bootedSprint = useRef(false);
  useEffect(() => {
    if (bootedSprint.current || !sprints.length) return;
    bootedSprint.current = true;
    setRangeId("todate");
  }, [sprints.length]);

  const winView = useMemo(() => {
    const done = weeksDesc.filter((w) => w.complete !== false);
    if (!sprints.length) {
      const picked = rangeId === "qtd"
        ? done.filter((w) => quarterStart && w.start >= quarterStart)
        : done.slice(0, Number(rangeId) || 13);
      return {
        shownDesc: weeksDesc,
        counted: new Set(picked.map((w) => w.start)),
        countedN: picked.length,
        wkey: rangeId === "qtd" ? "qtd" : `w${rangeId}`,
        cumLabel: rangeId === "qtd" ? "Quarter to date" : `Last ${rangeId} weeks`,
        goalKey: null,
      };
    }
    const current = sprints.find((s) => s.current) || sprints[sprints.length - 1];
    if (rangeId === "4") {
      const picked = done.slice(0, 4);
      const open = weeksDesc.find((w) => w.complete === false);
      const shown = open ? [open, ...picked] : picked;
      return { shownDesc: shown, counted: new Set(shown.map((w) => w.start)), countedN: picked.length,
        wkey: "w4", cumLabel: "Last 4 weeks", goalKey: null };
    }
    const key = rangeId === "past" && pastKey ? pastKey : current.key;
    const sp = sprints.find((s) => s.key === key) || current;
    const byStart = new Map(weeks.map((w) => [w.start, w]));
    const openStarts = weeks.filter((w) => w.complete === false).map((w) => w.start).sort();
    const cur = openStarts[0];
    let chain = (sp.mondays || []).map((start) => byStart.get(start) || weekStub(start, !cur || start < cur));
    if (rangeId !== "past" && rangeId !== "full") {
      if (cur) chain = chain.filter((w) => w.start <= cur);
    }
    const shownDesc = chain.slice().reverse();
    const countedN = shownDesc.filter((w) => w.complete !== false).length;
    const label = rangeId === "full" ? `${sp.key} · full` : rangeId === "past" ? sp.key : `${sp.key} to date`;
    return { shownDesc, counted: new Set(shownDesc.map((w) => w.start)), countedN,
      wkey: `sprint:${sp.key}`, cumLabel: label, goalKey: sp.key };
  }, [sprints, rangeId, pastKey, weeks, weeksDesc, quarterStart]);
  const shownDesc = winView.shownDesc;
  const counted = winView.counted;
  const countedN = winView.countedN;
  const wkey = winView.wkey;
  const cumLabel = winView.cumLabel;

  const enterprise = !!(data && data.enterprise);
  const depts = useMemo(() => {
    const out = [];
    for (const g of (data && data.groups) || []) {
      if (g.lens !== "dept" || !g.dept_key || out.some((d) => d.key === g.dept_key)) continue;
      out.push({ key: g.dept_key, name: g.dept_name || g.name, lead: g.lead_name, reviewed: g.reviewed_in });
    }
    return out;
  }, [data]);
  const shownGroups = useMemo(() => {
    const groups = (data && data.groups) || [];
    if (!enterprise) return groups;
    if (lens === "l10") return groups.filter((g) => g.lens === "l10");
    if (lens === "departments") return groups.filter((g) => g.lens === "dept" && g.dept_key === deptKey);
    if (lens === "elsewhere") return groups.filter((g) => g.lens === "elsewhere");
    if (lens === "people") {
      const person = ((data && data.people) || []).find((p) => p.initials === personKey);
      const mine = groups.flatMap((g) => (g.rows || []).filter((r) => r.owner && r.owner.initials === personKey).map((r) => ({ ...r, _reviewed: g.reviewed_in })));
      const pack = (key, name, rows) => rows.length ? { key, name, owner: person ? { name: person.name } : null, rows, season: null } : null;
      return [
        pack("p-l10", "On the L10", mine.filter((r) => r.tier === "l10")),
        pack("p-dept", "Department", mine.filter((r) => r.tier === "dept" || r.tier === "monthly" || r.tier === "event")),
        pack("p-tasks", "On-Time Tasks", mine.filter((r) => r.tier === "person")),
      ].filter(Boolean);
    }
    return groups;
  }, [data, enterprise, lens, deptKey, personKey]);

  const cumAttain = (r) => (r.cumulative && r.cumulative[wkey] && r.cumulative[wkey].attain != null ? r.cumulative[wkey].attain : null);
  const view = useMemo(() => {
    return shownGroups.map((g) => {
      const off = g.rows.filter((r) => { const a = cumAttain(r); return a != null && a < 100; }).length;
      // "Only what is off" keeps rows with no scoreable pace (snapshots / goal-0 / no data) visible,
      // as before — they aren't "off", so hiding them would falsely read as all-clear.
      const rows = onlyOff ? g.rows.filter((r) => { const a = cumAttain(r); return a == null || a < 100; }) : g.rows;
      return { ...g, rows, off, total: g.rows.length };   // total: rows before "only what is off" filters
    });
  }, [shownGroups, wkey, onlyOff]);

  if (!data) return <div style={{ fontFamily: FONT }}><div className="cc-skel" style={{ height: 320, borderRadius: 12 }} /></div>;
  if (!data.groups || data.groups.length === 0) {
    return (
      <div className="l10" style={{ fontFamily: FONT, color: T.ink }}>
        <div style={{ background: T.paper, borderRadius: 12, border: `1px solid ${T.line}`, padding: "44px 28px", textAlign: "center" }}>
          <div style={{ fontSize: 16, fontWeight: 600 }}>No Measurables Yet</div>
          <div style={{ fontSize: 13, color: T.muted, marginTop: 8 }}>This scorecard hasn't been configured. Seed the measurables to see the grid.</div>
        </div>
      </div>
    );
  }

  const totalW = RAIL + (cumOpen ? CUM : 0) + shownDesc.length * WK;
  const liveStart = (weeks.find((w) => w.complete === false) || {}).start;
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

      <div className="l10" style={{ fontFamily: FONT, color: T.ink, minWidth: 0, maxWidth: "100%" }}>
        <style>{`
          .l10, .l10 *, .l10 *::before, .l10 *::after { box-sizing: border-box; }
          .l10-scroll{
            overflow-x:auto; overflow-y:hidden; width:100%; min-width:0;
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

        <div style={{ background: T.paper, borderRadius: 12, border: `1px solid ${T.line}`, boxShadow: "0 1px 2px rgba(20,35,28,0.04), 0 8px 24px -12px rgba(20,35,28,0.10)", overflow: "hidden", minWidth: 0, maxWidth: "100%" }}>
          {/* --------------------------- title bar --------------------------- */}
          {enterprise && lens === "l10" && <EscalationStrip escalations={data.escalations || []} checks={data.checks || []} idsAfter={data.ids_after || 2} />}
          {enterprise && lens === "departments" && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", padding: "10px 20px", borderBottom: `1px solid ${T.line}` }}>
              {depts.map((d) => (
                <button key={d.key} type="button" onClick={() => setDeptKey(d.key)} className="l10-btn" aria-pressed={deptKey === d.key}
                  style={{ font: "inherit", fontSize: 12, fontWeight: deptKey === d.key ? 600 : 500, padding: "4px 10px", borderRadius: 6, cursor: "pointer", border: `1px solid ${deptKey === d.key ? T.forest : T.line}`, background: deptKey === d.key ? T.paper : "transparent", color: deptKey === d.key ? T.ink : T.inkSoft }}>
                  {titleCase(d.name)}
                </button>
              ))}
            </div>
          )}
          {enterprise && lens === "people" && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", padding: "10px 20px", borderBottom: `1px solid ${T.line}` }}>
              {(data.people || []).map((p) => (
                <button key={p.initials} type="button" onClick={() => setPersonKey(p.initials)} className="l10-btn" aria-pressed={personKey === p.initials}
                  style={{ font: "inherit", fontSize: 12, fontWeight: personKey === p.initials ? 600 : 500, padding: "4px 10px", borderRadius: 6, cursor: "pointer", border: `1px solid ${personKey === p.initials ? T.forest : T.line}`, background: personKey === p.initials ? T.paper : "transparent", color: personKey === p.initials ? T.ink : T.inkSoft }}>
                  {p.name}
                </button>
              ))}
            </div>
          )}
          <div style={{ display: "flex", alignItems: "center", gap: 12, rowGap: 10, padding: "12px 16px", borderBottom: `1px solid ${T.line}`, flexWrap: "wrap" }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 12, flex: "0 0 auto" }}>
              <h2 style={{ margin: 0, fontSize: 17, fontWeight: 600, letterSpacing: "-0.015em", whiteSpace: "nowrap", flexShrink: 0 }}>
                {enterprise ? (lens === "l10" ? "L10 Scorecard" : lens === "departments" ? titleCase((depts.find((d) => d.key === deptKey) || {}).name) || "Departments" : lens === "people" ? "People" : "Elsewhere") : "L10 Scorecard"}
              </h2>
              <span style={{ fontSize: 13, color: T.muted, whiteSpace: "nowrap" }}>
                {enterprise && lens === "departments" && (depts.find((d) => d.key === deptKey) || {}).reviewed
                  ? (depts.find((d) => d.key === deptKey) || {}).reviewed
                  : `Week ${liveN} is newest. Select any measurable to open its detail.`}
              </span>
            </div>
            {enterprise && (
              <div style={{ display: "flex", background: T.shell, borderRadius: 7, padding: 2, gap: 2 }}>
                {[["l10", "L10"], ["departments", "Departments"], ["people", "People"], ["elsewhere", "Elsewhere"]].map(([k, label]) => (
                  <button key={k} type="button" onClick={() => setLens(k)} className="l10-btn" aria-pressed={lens === k}
                    style={{ border: "none", font: "inherit", fontSize: 12, fontWeight: lens === k ? 600 : 500, padding: "4px 11px", borderRadius: 5, cursor: "pointer", background: lens === k ? T.paper : "transparent", color: lens === k ? T.ink : T.muted, boxShadow: lens === k ? "0 1px 2px rgba(20,35,28,0.10)" : "none" }}>
                    {label}
                  </button>
                ))}
              </div>
            )}

            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <div style={{ display: "flex", background: T.shell, borderRadius: 7, padding: 2, gap: 2 }}>
                {sprints.length ? (
                  <>
                    {[["4", "4 Wks"], ["full", "Full Sprint"], ["todate", `${(sprints.find((s) => s.current) || sprints[sprints.length - 1]).key} to Date`]].map(([id, label]) => {
                      const on = id === rangeId;
                      return (
                        <button key={id} onClick={() => setRangeId(id)} className="l10-btn" style={{
                          border: "none", font: "inherit", fontSize: 12, fontWeight: on ? 600 : 500,
                          padding: "4px 11px", borderRadius: 5, cursor: "pointer", whiteSpace: "nowrap",
                          background: on ? T.paper : "transparent", color: on ? T.ink : T.muted,
                          boxShadow: on ? "0 1px 2px rgba(20,35,28,0.10)" : "none",
                        }}>{label}</button>
                      );
                    })}
                    {sprints.filter((s) => !s.current).length > 0 && (
                      <select aria-label="Earlier Sprint" value={rangeId === "past" ? pastKey : ""}
                        onChange={(e) => { setPastKey(e.target.value); setRangeId(e.target.value ? "past" : "todate"); }}
                        style={{ font: "inherit", fontSize: 12, border: "none", background: rangeId === "past" ? T.paper : "transparent", color: rangeId === "past" ? T.ink : T.muted, borderRadius: 5, padding: "4px 8px", cursor: "pointer" }}>
                        <option value="">Earlier Sprint</option>
                        {sprints.filter((s) => !s.current).map((s) => <option key={s.key} value={s.key}>{s.key}</option>)}
                      </select>
                    )}
                  </>
                ) : windows.map((k) => {
                  const id = String(k);
                  const on = id === rangeId;
                  return (
                    <button
                      key={id}
                      onClick={() => setRangeId(id)}
                      className="l10-btn"
                      style={{
                        border: "none", font: "inherit", fontSize: 12, fontWeight: on ? 600 : 500,
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
              <span style={{ ...NUM, fontSize: 12, color: T.faint, whiteSpace: "nowrap" }}>
                {countedN === shownDesc.length ? `${countedN} Week${countedN === 1 ? "" : "s"} Counted` : `${countedN} of ${shownDesc.length} Weeks Counted`}{qtdThin ? " · quarter just turned" : ""}
              </span>
            </div>

            <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10, flexShrink: 0, flexWrap: "wrap" }}>
              <label style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 13, color: T.inkSoft, cursor: "pointer", whiteSpace: "nowrap" }}>
                <input type="checkbox" checked={onlyOff} onChange={(e) => setOnlyOff(e.target.checked)} style={{ width: 14, height: 14, accentColor: T.forest, cursor: "pointer" }} />
                Only What Is Off
              </label>
              {isAdmin && (
                <button
                  onClick={() => setSettingsOpen((v) => !v)}
                  className="l10-btn"
                  style={{ font: "inherit", fontSize: 13, fontWeight: 500, padding: "6px 13px", borderRadius: 6, border: `1px solid ${settingsOpen ? T.forest : T.line}`, background: "transparent", color: settingsOpen ? T.forest : T.inkSoft, cursor: "pointer", whiteSpace: "nowrap" }}
                >
                  Settings
                </button>
              )}
              {isAdmin && <ShareButton scope={scope} />}
            </div>
          </div>

          {/* --------------------------- scroll region --------------------------- */}
          <div style={{ position: "relative" }}>
            <div ref={scroller} className="l10-scroll" onScroll={measure}>
              <div style={{ width: totalW, minWidth: "100%" }}>
                <ColumnHeaders cumOpen={cumOpen} onToggleCum={toggleCum} weeksDesc={shownDesc} liveStart={liveStart} />
                {view.map((g) => (
                  <div key={g.key}>
                    <GroupBar group={g} width={totalW} off={g.off} />
                    {g.rows.length === 0 && (
                      <div style={{ display: "flex", borderBottom: `1px solid ${T.lineSoft}` }}>
                        <div style={{ position: "sticky", left: 0, width: RAIL, flexShrink: 0, padding: "12px 16px", background: T.paper, fontSize: 12, color: g.total ? T.good : T.muted, borderRight: `1px solid ${T.line}` }}>{g.total ? "Everything at or above goal." : "No numbers yet. Add them in Settings."}</div>
                        <div style={{ flex: 1, background: T.paper }} />
                      </div>
                    )}
                    {g.rows.map((r) => {
                      const key = `${g.key}-${r.id}`;
                      const asc = shownDesc.slice().reverse();
                      const sprintVals = winView.goalKey && r.sprint_values && r.sprint_values[winView.goalKey];
                      const sp = sprintVals && sprints.find((s) => s.key === winView.goalKey);
                      const pos = sp ? new Map((sp.mondays || []).map((start, i) => [start, i])) : null;
                      const idx = new Map(weeks.map((w, i) => [w.start, i]));
                      const same = shownDesc === weeksDesc && !winView.goalKey;
                      const shownRow = same ? r : {
                        ...r,
                        values: asc.map((w) => {
                          if (pos) { const i = pos.get(w.start); return i == null ? null : sprintVals[i]; }
                          const i = idx.get(w.start); return i == null ? null : (r.values || [])[i];
                        }),
                        week_goals: asc.map((w) => {
                          const goals = pos ? ((r.sprint_week_goals || {})[winView.goalKey] || []) : (r.week_goals || []);
                          const i = (pos || idx).get(w.start);
                          return i == null ? null : goals[i];
                        }),
                        goal: winView.goalKey && r.sprint_goals && r.sprint_goals[winView.goalKey] != null ? r.sprint_goals[winView.goalKey] : r.goal,
                      };
                      return (
                        <ScorecardRow
                          key={key}
                          r={shownRow}
                          initials={(r.owner && r.owner.initials) || ""}
                          wkey={wkey}
                          weeksDesc={shownDesc}
                          counted={counted}
                          cumOpen={cumOpen}
                          expanded={expanded === key}
                          onToggle={() => setExpanded(expanded === key ? null : key)}
                          onDrill={shareToken ? null : setDrill}
                          cumLabel={cumLabel}
                          idsAfter={data.ids_after || 3}
                          dim={!!g.season}
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
            {[["At or Above Goal", T.good], ["Within 20%", T.warn], ["Off Pace", T.bad]].map(([label, c]) => (
              <div key={label} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ width: 8, height: 8, borderRadius: 2, background: c, opacity: 0.85 }} />
                <span style={{ fontSize: 12, color: T.muted }}>{label}</span>
              </div>
            ))}
            <span style={{ marginLeft: "auto", fontSize: 12, color: T.faint }}>
              {(data.sources || []).map((x) => `${SOURCE_NAME[x.provider] || x.provider} synced ${ago(x.synced_at)}`).join(" · ")}
              {(data.sources || []).length ? " · " : ""}Cumulative totals count only weeks with data.
            </span>
          </div>
        </div>
        {enterprise && (lens === "l10" || lens === "departments") && (
          <UpdatesPanel
            groups={(data.groups || []).filter((g) => lens === "l10"
              ? (g.lens === "monthly" || g.lens === "event")
              : (g.lens === "dept" && g.dept_key === deptKey))}
            weeks={data.weeks || []}
            canEdit={canEditValues}
            onSaved={reload}
            onlyNonWeekly={lens === "departments"}
          />
        )}
        {data.gates && data.gates.length > 0 && <PlanGates gates={data.gates} canEdit={isAdmin} onChanged={reload} />}
      </div>
    </>
  );
}

/* --------------------------------- header --------------------------------- */

function ColumnHeaders({ cumOpen, onToggleCum, weeksDesc, liveStart }) {
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
            border: `1px solid ${T.forest}`, background: T.forest,
            color: T.paper, borderRadius: 5, cursor: "pointer", font: "inherit",
            fontSize: 12, fontWeight: 600,
          }}
        >
          <Chevron open={cumOpen} size={10} color={T.paper} />
          Cumulative
        </button>
        <div style={{ marginLeft: "auto", marginRight: 10, display: "flex", alignItems: "center", gap: 12 }}>
          <span style={LBL}>Own</span>
          <span style={{ ...LBL, width: W_GOAL - 22, textAlign: "right" }}>Goal</span>
        </div>
      </div>

      {cumOpen && (
        <div style={{ width: CUM, flexShrink: 0, height: "100%", display: "flex", alignItems: "center", background: T.shell, borderRight: `1px solid ${T.line}` }}>
          <div style={{ width: C_ACTUAL, flexShrink: 0, paddingLeft: 14, ...LBL, whiteSpace: "nowrap" }}>Actual</div>
          <div style={{ width: C_PACE, flexShrink: 0, ...LBL, whiteSpace: "nowrap" }}>Pace to Goal</div>
          <div style={{ width: C_TREND, flexShrink: 0, ...LBL, textAlign: "right", whiteSpace: "nowrap" }}>Trend</div>
          <div style={{ width: C_GAP, flexShrink: 0, ...LBL, textAlign: "right", whiteSpace: "nowrap" }}>Gap</div>
          <div style={{ width: C_REC, flexShrink: 0, paddingLeft: 12, ...LBL, whiteSpace: "nowrap" }}>To Recover</div>
        </div>
      )}

      {weeksDesc.map((w) => {
        const live = w.start === liveStart;
        return (
          <div
            key={w.start}
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
            <span style={{ ...NUM, fontSize: 12, color: T.faint }}>{w.label}</span>
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
          : <span style={{ width: 22, height: 22, borderRadius: "50%", background: "rgba(255,253,249,0.15)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 600, color: T.paper, flexShrink: 0 }}>{((o.name || "·")[0] || "·").toUpperCase()}</span>)}
        <span style={{ fontSize: 12, fontWeight: 600, color: T.paper, whiteSpace: "nowrap" }}>{titleCase(group.name)}</span>
        {o && o.name && <span style={{ fontSize: 12, color: "rgba(255,253,249,0.5)", whiteSpace: "nowrap" }}>{o.name}</span>}
        <span style={{ ...NUM, marginLeft: "auto", fontSize: 12, fontWeight: 600, whiteSpace: "nowrap", color: group.season || off ? "rgba(255,253,249,0.7)" : "rgba(255,253,249,0.4)" }}>
          {group.season ? titleCase(group.season) : `${off} Off Pace`}
        </span>
      </div>
    </div>
  );
}

function mondayOf(iso) {
  const d = new Date(`${iso}T12:00:00Z`);
  const diff = (d.getUTCDay() + 6) % 7;
  d.setUTCDate(d.getUTCDate() - diff);
  return d.toISOString().slice(0, 10);
}

function EscalationStrip({ escalations, checks, idsAfter }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", borderBottom: `1px solid ${T.line}`, background: T.rail }}>
      <div style={{ flex: "1 1 280px", padding: "12px 20px" }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>Escalated From the 1:1s</div>
        {escalations.length === 0 ? (
          <p style={{ margin: "4px 0 0", fontSize: 12.5, color: T.muted, lineHeight: 1.45 }}>
            Nothing yet. A department or personal number goes here after {idsAfter} weeks off its goal.
          </p>
        ) : (
          <ul style={{ margin: "6px 0 0", padding: 0, listStyle: "none" }}>
            {escalations.map((e) => (
              <li key={e.id} style={{ fontSize: 12.5, color: T.inkSoft, padding: "3px 0" }}>
                <strong style={{ color: T.ink }}>{titleCase(e.measurable)}</strong> · {e.streak} weeks · {e.owner || "—"}
              </li>
            ))}
          </ul>
        )}
      </div>
      {checks.length > 0 && (
        <div style={{ flex: "2 1 360px", padding: "12px 20px", borderLeft: `1px solid ${T.line}` }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>Data Checks</div>
          <ul style={{ margin: "6px 0 0", padding: 0, listStyle: "none" }}>
            {checks.map((c) => (
              <li key={c.id} style={{ fontSize: 12.5, color: T.inkSoft, lineHeight: 1.45, padding: "3px 0" }}>
                <strong style={{ color: T.ink }}>{titleCase(c.measurable)}.</strong> {c.check}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function UpdatesPanel({ groups, weeks, canEdit, onSaved, onlyNonWeekly }) {
  const rows = groups.flatMap((g) => (g.rows || []).filter((r) => !onlyNonWeekly || (r.cadence && r.cadence !== "weekly")).map((r) => ({ ...r, _group: g.name })));
  const current = weeks.length ? weeks[weeks.length - 1].start : null;
  const [drafts, setDrafts] = useState({});
  const [busy, setBusy] = useState(null);
  if (!rows.length) return null;

  function valueOn(r, start) {
    const i = weeks.findIndex((w) => w.start === start);
    return i >= 0 ? r.values[i] : null;
  }

  async function save(r, start) {
    const raw = drafts[`${r.id}:${start}`];
    if (raw == null || raw === "" || !start) return;
    const value = Number(raw);
    if (!Number.isFinite(value)) return;
    setBusy(r.id);
    try {
      await postJSON("/ulrg/scorecard/values", { metric_id: r.id, week_start: start, value });
      onSaved && onSaved();
    } finally { setBusy(null); }
  }

  return (
    <div style={{ marginTop: 16, background: T.paper, borderRadius: 12, border: `1px solid ${T.line}`, overflow: "hidden" }}>
      <div style={{ padding: "14px 20px", borderBottom: `1px solid ${T.line}` }}>
        <div style={{ fontSize: 15, fontWeight: 600 }}>Reviewed When It Updates</div>
        <div style={{ fontSize: 12.5, color: T.muted, marginTop: 2 }}>Monthly and per-event numbers. Type a figure and save it onto that week.</div>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
          <tbody>
            {rows.map((r) => {
              const anchors = (r.anchors && r.anchors.length) ? r.anchors : [{ label: "This Period", date: current }];
              return (
                <tr key={r.id} style={{ borderTop: `1px solid ${T.lineSoft}` }}>
                  <td style={{ padding: "10px 16px", fontWeight: 500, whiteSpace: "nowrap" }}>{titleCase(r.measurable)}</td>
                  <td style={{ padding: "10px 8px", color: T.muted, whiteSpace: "nowrap" }}>{(r.owner && r.owner.initials) || ""}</td>
                  <td style={{ padding: "10px 8px", color: T.faint }}>{r.goal == null ? "Goal Not Set" : `${r.direction === "lte" ? "≤" : "≥"} ${r.goal}`}</td>
                  {anchors.map((a) => {
                    const start = a.date ? mondayOf(a.date) : current;
                    const have = valueOn(r, start);
                    const key = `${r.id}:${start}`;
                    return (
                      <td key={a.label} style={{ padding: "8px" }}>
                        <div style={{ fontSize: 11, color: T.muted, marginBottom: 4 }}>{titleCase(a.label)}{have != null ? ` · ${have}` : ""}</div>
                        {canEdit && !r.auto && (
                          <span style={{ display: "inline-flex", gap: 6 }}>
                            <input aria-label={`${r.measurable} ${a.label}`} value={drafts[key] || ""} onChange={(e) => setDrafts({ ...drafts, [key]: e.target.value })}
                              style={{ width: 88, font: "inherit", fontSize: 13, padding: "4px 6px", border: `1px solid ${T.line}`, borderRadius: 4 }} />
                            <button type="button" disabled={busy === r.id} onClick={() => save(r, start)}
                              style={{ font: "inherit", fontSize: 12, padding: "4px 8px", borderRadius: 4, border: `1px solid ${T.line}`, background: T.paper, cursor: "pointer" }}>
                              Save
                            </button>
                          </span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
