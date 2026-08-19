/* beCollective · Sales Desk — the rep-throughput view (SPEC-becollective-salesdesk §8).
   Built from becollective-salesdesk-v2.jsx with the brand deferrals applied: tokens come
   from theme.js by role (no local C, no font import, no hex literals). Everything is
   pre-computed server-side from the SalesCall event log; this component only renders it. */
import { createContext, useContext, useEffect, useState } from "react";
import { T, alpha } from "./theme.js";
import { getJSON, putJSON, postJSON, delJSON } from "./api";
import { DrillRecords, DrillCalc } from "./LaunchSection.jsx";

const kM = (n) => {
  const a = Math.abs(Math.round(n || 0));
  if (a >= 1_000_000) return `$${(a / 1_000_000).toFixed(a % 1_000_000 === 0 ? 0 : 1)}M`;
  if (a >= 1_000) return `$${Math.round(a / 1000)}K`;
  return `$${a}`;
};
const pct = (v) => (v == null ? "—" : `${Math.round(v)}%`);
const OUT_TONE = { Showed: "good", "No Show": "warn", Cancelled: "warn", Rescheduled: "" };

/* prose day/time in the launch's tz — "Today · 10:00 AM" / "Tomorrow · …" / "Fri · …" */
function fmtCall(iso, tz, now) {
  if (!iso) return { day: "Unscheduled", time: "" };
  const d = new Date(iso);
  const ymd = (x) => new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(x);
  const today = ymd(now), tmrw = ymd(new Date(now.getTime() + 864e5)), that = ymd(d);
  const day = that === today ? "Today" : that === tmrw ? "Tomorrow"
    : new Intl.DateTimeFormat("en-US", { timeZone: tz, weekday: "short", month: "short", day: "numeric" }).format(d);
  return { day, time: new Intl.DateTimeFormat("en-US", { timeZone: tz, hour: "numeric", minute: "2-digit" }).format(d) };
}

/* Every figure drills into its calls/opps or its formula — same pattern as the Launch tab.
   <N m="metric" rep="email?"> wraps a value; the context carries the opener. */
const SDDrillCtx = createContext(null);
function N({ m, rep, children, title }) {
  const open = useContext(SDDrillCtx);
  if (!open || !m) return <>{children}</>;
  const fire = () => open({ metric: m, rep });
  return (
    <span className="num" role="button" tabIndex={0} title={title || "Drill in"}
      onClick={(e) => { e.stopPropagation(); fire(); }}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); fire(); } }}>
      {children}
    </span>
  );
}

function SDDrawer({ drill, businessKey, usingSample, onClose }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let alive = true;
    if (usingSample) { setErr("sample"); return () => { alive = false; }; }
    setD(null); setErr(null);
    const q = drill.rep ? `?rep=${encodeURIComponent(drill.rep)}` : "";
    getJSON(`/businesses/${businessKey}/launches/active/sales-desk/drill/${encodeURIComponent(drill.metric)}${q}`)
      .then((r) => alive && setD(r))
      .catch((e) => alive && setErr(e.detail || e.message || "Failed to load"));
    return () => { alive = false; };
  }, [drill, businessKey, usingSample]);
  return (
    <div className="drx-scrim" onClick={onClose}>
      <div className="drx" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="drx-head">
          <span className="drx-title">{d?.title || "Drill-down"}</span>
          <button className="drx-x" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="drx-body">
          {err === "sample" && <div className="drx-note">Drill-down loads with live data (this is sample mode).</div>}
          {err && err !== "sample" && <div className="drx-note">{err}</div>}
          {!d && !err && <div className="drx-note">Loading…</div>}
          {d && d.type === "records" && <DrillRecords d={d} />}
          {d && d.type === "calc" && <DrillCalc d={d} />}
        </div>
      </div>
    </div>
  );
}

const repKey = (r) => (r.unassigned ? "__unassigned__" : r.rep_email);
const RepName = ({ email, name, unmapped, unassigned }) =>
  unassigned ? <>Unassigned <span className="flagchip">no host</span></>
    : name ? name
      : <><span className="rawmail">{email}</span>{unmapped && <span className="flagchip">unmapped</span>}</>;

export default function SalesDeskSection({ data, usingSample, role, businessKey = "springb", onSaved }) {
  const [sel, setSel] = useState(null);          // selected rep_email (client-side board filter)
  const [rosterOpen, setRosterOpen] = useState(false);
  const [drill, setDrill] = useState(null);      // {metric, rep} currently drilled into
  const [openRep, setOpenRep] = useState(null);  // rep card expanded on phones
  const isEditor = !role || role === "owner" || role === "admin";
  const t = data.totals;
  const tz = data.default_tz || "America/Denver";
  const now = new Date(data.as_of || Date.now());
  const anyCalls = t.booked > 0 || (data.reps || []).length > 0;

  const calls = sel ? (data.calls || []).filter((c) => (c.rep_email || "__unassigned__") === sel) : (data.calls || []);
  const noshows = sel ? (data.no_shows || []).filter((n) => (n.rep_email || "__unassigned__") === sel) : (data.no_shows || []);
  const mixTotal = (data.payment_mix || []).reduce((a, m) => a + (m.count || 0), 0);
  const barTone = [T.meadow, T.meadowBg, alpha(T.teal, 0.6), T.line];

  return (
    <SDDrillCtx.Provider value={setDrill}>
    <div className="sd-root">
      <style>{`
        .sd-root { color:${T.ink}; }
        .sd-mod { max-width:920px; } .sd-root * { box-sizing:border-box; }
        .sd-ctx { display:flex; align-items:center; gap:11px; margin:4px 0 16px; flex-wrap:wrap; }
        .sd-ctx-bar { width:4px; height:20px; border-radius:2px; background:${T.petal}; }
        .sd-ctx-h { font-family:Poppins,sans-serif; font-size:17px; font-weight:600; letter-spacing:-.01em; }
        .sd-ctx-s { font-size:12px; color:${T.muted}; }
        .sd-spacer { flex:1; }
        .sd-pill { display:inline-flex; align-items:center; gap:7px; font-size:11px; font-weight:600;
          border-radius:99px; padding:5px 12px; color:${T.meadow}; background:${T.meadowBg}; border:1px solid ${alpha(T.meadow, .28)}; }
        .sd-pill .pdot { width:7px; height:7px; border-radius:99px; background:${T.meadow}; }
        .sd-sample { color:${T.amber}; background:${T.amberBg}; border:1px solid ${alpha(T.amber, .28)};
          font-size:11px; border-radius:8px; padding:6px 11px; margin-bottom:14px; }

        .sd-kpis { display:grid; grid-template-columns:repeat(4,1fr); gap:11px; margin-bottom:14px; }
        .sd-kpi { background:${T.white}; border:1px solid ${T.line}; border-radius:13px; padding:13px 15px; }
        .sd-kpi-l { font-size:10px; font-weight:700; letter-spacing:.09em; text-transform:uppercase; color:${T.tertiary}; }
        .sd-kpi-v { font-family:Poppins,sans-serif; font-size:24px; font-weight:700; margin-top:4px; font-variant-numeric:tabular-nums; }
        .sd-kpi-v.good { color:${T.meadow}; } .sd-kpi-v.warn { color:${T.amber}; }
        .sd-kpi-s { font-size:10.5px; color:${T.muted}; margin-top:2px; }

        .sd-card { background:${T.white}; border:1px solid ${T.line}; border-radius:16px; padding:18px 20px 16px;
          box-shadow:0 12px 30px ${alpha(T.evergreen, .05)}; margin-bottom:14px; }
        .sd-head { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:12px; gap:10px; flex-wrap:wrap; }
        .sd-title { font-family:Poppins,sans-serif; font-size:11px; font-weight:700; letter-spacing:.13em; text-transform:uppercase; }
        .sd-sub { font-size:11px; color:${T.muted}; }
        .sd-clear { border:none; background:none; font:inherit; font-size:11px; font-weight:600; color:${T.petalDeep}; cursor:pointer; padding:0; }

        .sd-tblwrap { overflow-x:auto; }
        .sd-cards { display:none; }
        .sd-c { border:1px solid ${T.line}; border-radius:12px; padding:11px 12px; margin-bottom:9px; }
        .sd-c.sel { border-color:${T.petalDeep}; background:${alpha(T.petal, .07)}; }
        .sd-c-top { display:flex; align-items:center; gap:8px; margin-bottom:9px; }
        .sd-c-name { flex:1; min-width:0; text-align:left; font:inherit; font-size:13.5px; font-weight:600;
          color:${T.ink}; background:none; border:none; padding:0; cursor:pointer; }
        .sd-c-more { flex:none; font:inherit; font-size:11px; font-weight:600; color:${T.petalDeep};
          background:none; border:none; padding:6px 2px; cursor:pointer; min-height:34px; }
        .sd-c-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; }
        .sd-c-grid.more { grid-template-columns:repeat(3,minmax(0,1fr)); margin-top:10px;
          padding-top:10px; border-top:1px solid ${alpha(T.line, .7)}; }
        .sd-c-stat { min-width:0; }
        .sd-c-l { display:block; font-size:9.5px; font-weight:700; letter-spacing:.07em;
          text-transform:uppercase; color:${T.tertiary}; }
        .sd-c-v { display:block; font-family:Poppins,sans-serif; font-size:17px; font-weight:700;
          margin-top:2px; font-variant-numeric:tabular-nums; color:${T.ink}; }
        .sd-c-foot { font-size:10.5px; color:${T.tertiary}; line-height:1.5; margin-top:4px; }
        @media (max-width:700px) {
          .sd-tblwrap { display:none; }      /* 12 columns never read well on a phone */
          .sd-cards { display:block; }
        }
        .sd-tbl { width:100%; border-collapse:collapse; }
        .sd-tbl th { font-size:10px; font-weight:700; letter-spacing:.07em; text-transform:uppercase; color:${T.muted};
          text-align:right; padding:6px 7px; border-bottom:1px solid ${T.line}; white-space:nowrap; }
        .sd-tbl th:first-child { text-align:left; }
        .sd-tbl td { font-size:12.5px; padding:9px 7px; border-bottom:1px solid ${alpha(T.line, .5)}; text-align:right;
          font-variant-numeric:tabular-nums; color:${T.secondary}; }
        .sd-tbl td:first-child { text-align:left; font-weight:600; color:${T.ink}; }
        .sd-tbl tr.sel td { background:${alpha(T.line, .5)}; }
        .sd-tbl tr.click { cursor:pointer; } .sd-tbl tr.click:hover td { background:${alpha(T.line, .5)}; }
        .sd-tbl td .em { font-family:Poppins,sans-serif; font-weight:700; color:${T.ink}; }
        .rate { font-weight:700; } .rate.good { color:${T.meadow}; } .rate.warn { color:${T.amber}; }
        .flagchip { display:inline-block; margin-left:7px; font-size:9.5px; font-weight:700; color:${T.amber};
          background:${T.amberBg}; border-radius:5px; padding:2px 6px; }
        .rawmail { font-family:'Courier New',monospace; font-size:11px; color:${T.tertiary}; font-weight:500; }
        .sd-tfoot td { border-bottom:none; color:${T.tertiary}; font-size:11px; padding-top:10px; text-align:left; }

        .sd-rec { margin-left:6px; font-size:12px; text-decoration:none; color:${T.teal}; }
        .sd-rec.wait { color:${T.amber}; cursor:default; }
        .sd-cols { display:grid; grid-template-columns:minmax(0,1.35fr) minmax(0,1fr); gap:14px; align-items:start; }
        .sd-scroll { max-height:360px; overflow-y:auto; margin-right:-8px; padding-right:8px; }
        .sd-scroll::-webkit-scrollbar { width:7px; }
        .sd-scroll::-webkit-scrollbar-thumb { background:${alpha(T.tertiary, .35)}; border-radius:99px; }
        .sd-scroll::-webkit-scrollbar-track { background:transparent; }
        .sd-lr { display:flex; align-items:center; gap:10px; padding:9px 2px; border-bottom:1px solid ${alpha(T.line, .5)}; }
        .sd-lr:last-child { border-bottom:none; }
        .sd-lr-t { font-size:11px; color:${T.muted}; width:118px; flex:none; }
        .sd-lr-who { font-size:12.5px; font-weight:600; color:${T.ink}; flex:1; min-width:0; }
        .sd-lr-who em { font-style:normal; font-weight:500; color:${T.muted}; font-size:11px; margin-left:7px; }
        .sd-oc { font-size:10.5px; font-weight:700; flex:none; width:76px; text-align:right; }
        .sd-oc.good { color:${T.meadow}; } .sd-oc.warn { color:${T.amber}; } .sd-oc.pend { color:${T.muted}; } .sd-oc.flag { color:${T.petalDeep}; }
        .sd-nsd { font-size:10.5px; color:${T.muted}; flex:none; }
        .sd-nst { font-size:9.5px; font-weight:700; flex:none; border-radius:5px; padding:2px 7px; }
        .sd-nst.yes { color:${T.meadow}; background:${T.meadowBg}; }
        .sd-nst.no { color:${T.amber}; background:${T.amberBg}; }

        .sd-mix { display:grid; grid-template-columns:repeat(4,1fr); gap:11px; }
        .sd-mixcell { border:1px solid ${T.line}; border-radius:11px; padding:11px 13px; }
        .sd-mix-t { font-size:11.5px; font-weight:700; color:${T.ink}; }
        .sd-mix-n { font-family:Poppins,sans-serif; font-size:20px; font-weight:700; margin-top:2px; font-variant-numeric:tabular-nums; }
        .sd-mix-s { font-size:10.5px; color:${T.muted}; margin-top:2px; line-height:1.4; }
        .prov { display:inline-block; font-size:9px; font-weight:700; color:${T.amber}; background:${T.amberBg};
          border-radius:4px; padding:1px 5px; margin-left:5px; }
        .sd-bar { display:flex; height:8px; border-radius:5px; overflow:hidden; margin:12px 0 8px; background:${alpha(T.line, .5)}; }
        .sd-bar span { display:block; height:100%; }
        .sd-mixfoot { font-size:11px; color:${T.tertiary}; } .sd-mixfoot b { font-family:Poppins,sans-serif; color:${T.ink}; }

        .sd-health { background:${T.white}; border:1px solid ${alpha(T.amber, .3)}; border-radius:16px; padding:16px 20px; }
        .sd-health .sd-title { color:${T.amber}; }
        .sd-hrow { display:flex; gap:10px; align-items:baseline; padding:7px 0; border-bottom:1px solid ${alpha(T.line, .5)}; }
        .sd-hrow:last-child { border-bottom:none; }
        .sd-hn { font-family:Poppins,sans-serif; font-size:17px; font-weight:700; color:${T.amber}; width:24px; flex:none; text-align:right; }
        .sd-hl { font-size:12.5px; font-weight:600; color:${T.ink}; }
        .sd-hint { display:block; font-size:11px; color:${T.muted}; margin-top:1px; }
        .sd-empty { font-size:12.5px; color:${T.muted}; padding:10px 2px; }

        .sd-root .num { cursor:pointer; border-radius:3px; box-shadow:inset 0 -1px 0 ${alpha(T.muted, 0)}; }
        .sd-root .num:hover { box-shadow:inset 0 -1.5px 0 currentColor; }
        .sd-root .num:focus-visible { outline:2px solid ${T.petal}; outline-offset:2px; }
        .drx-scrim { position:fixed; inset:0; background:${alpha(T.evergreen, .32)}; z-index:70;
          display:flex; justify-content:flex-end; }
        .drx { width:min(440px,92vw); height:100%; background:${T.white}; display:flex; flex-direction:column;
          box-shadow:-24px 0 60px ${alpha(T.evergreen, .18)}; }
        .drx-head { display:flex; justify-content:space-between; align-items:center; padding:16px 20px;
          border-bottom:1px solid ${T.line}; }
        .drx-title { font-family:Poppins,sans-serif; font-size:14px; font-weight:700; color:${T.ink}; }
        .drx-x { border:none; background:none; font-size:15px; color:${T.tertiary}; cursor:pointer; }
        .drx-body { padding:18px 20px; overflow-y:auto; }
        .drx-val { font-family:Poppins,sans-serif; font-size:30px; font-weight:700; color:${T.ink};
          letter-spacing:-.02em; font-variant-numeric:tabular-nums; }
        .drx-sub { font-size:12px; color:${T.muted}; margin:2px 0 12px; }
        .drx-steps { border:1px solid ${T.line}; border-radius:10px; overflow:hidden; margin-bottom:12px; }
        .drx-step { display:flex; justify-content:space-between; gap:12px; padding:9px 13px; font-size:12.5px;
          border-bottom:1px solid ${alpha(T.line, .5)}; }
        .drx-step:last-child { border-bottom:none; }
        .drx-step span { color:${T.secondary}; } .drx-step b { color:${T.ink}; font-family:Poppins,sans-serif;
          font-weight:600; text-align:right; }
        .drx-formula { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11.5px; color:${T.teal};
          background:${alpha(T.teal, .07)}; border-radius:8px; padding:8px 11px; margin-bottom:10px; }
        .drx-note, .drx-note2 { font-size:11.5px; color:${T.muted}; line-height:1.5; }
        .drx-empty { font-size:12.5px; color:${T.muted}; padding:12px 0; }
        .drx-tblwrap { overflow-x:auto; }
        .drx-tbl { width:100%; border-collapse:collapse; font-size:12px; margin-top:6px; }
        .drx-tbl th { text-align:left; font-size:10px; font-weight:700; letter-spacing:.05em;
          text-transform:uppercase; color:${T.muted}; padding:6px 8px; border-bottom:1px solid ${T.line}; }
        .drx-tbl td { padding:7px 8px; border-bottom:1px solid ${alpha(T.line, .5)}; color:${T.secondary}; }
        .drx-tbl a { color:${T.teal}; text-decoration:none; font-weight:600; }
        .sd-foot { font-size:11px; color:${T.muted}; margin-top:16px; line-height:1.6; } .sd-foot b { color:${T.tertiary}; }
        @media (max-width:760px){ .sd-kpis, .sd-mix { grid-template-columns:minmax(0,1fr) minmax(0,1fr); }
          .sd-cols { grid-template-columns:minmax(0,1fr); } }
      `}</style>

      <div className="sd-mod">
        {usingSample && <div className="sd-sample">Illustrative sample data — connect the backend to see live figures.</div>}

        <div className="sd-ctx">
          <span className="sd-ctx-bar" />
          <span className="sd-ctx-h">beCollective</span>
          <span className="sd-ctx-s">Sales Desk · rep throughput &amp; call schedule · launch to date</span>
          <span className="sd-spacer" />
          <span className="sd-pill"><span className="pdot" />In launch window</span>
        </div>

        <div className="sd-kpis">
          <div className="sd-kpi"><div className="sd-kpi-l">Calls booked</div>
            <div className="sd-kpi-v"><N m="kpi.booked">{t.booked}</N></div>
            <div className="sd-kpi-s"><N m="kpi.upcoming">{t.upcoming}</N> still upcoming</div></div>
          <div className="sd-kpi"><div className="sd-kpi-l">Show rate</div>
            <div className={`sd-kpi-v ${t.show_rate == null ? "" : t.show_rate >= 70 ? "good" : "warn"}`}>
              <N m="kpi.show_rate">{pct(t.show_rate)}</N></div>
            <div className="sd-kpi-s"><N m="kpi.held">{t.held} held</N> · <N m="kpi.no_show">{t.no_show} no-show</N> · <N m="kpi.cancelled">{t.cancelled} cancelled</N></div></div>
          <div className="sd-kpi"><div className="sd-kpi-l">Close rate</div>
            <div className="sd-kpi-v"><N m="kpi.close_rate">{pct(t.close_rate)}</N></div>
            <div className="sd-kpi-s"><N m="kpi.won">{t.won} won</N> of <N m="kpi.held">{t.held} held</N></div></div>
          <div className="sd-kpi"><div className="sd-kpi-l">On the table</div>
            <div className="sd-kpi-v"><N m="kpi.on_the_table">{kM(t.on_the_table)}</N></div>
            <div className="sd-kpi-s"><N m="kpi.in_play">{t.deciding} deciding</N> × <N m="kpi.blended">{kM(t.blended)}{t.blended_provisional ? " *" : ""} blended</N></div></div>
        </div>

        {!anyCalls ? (
          <div className="sd-card"><div className="sd-empty">Call data appears here once the first booking syncs from the sales pipeline.</div></div>
        ) : (
          <>
            <div className="sd-card">
              <div className="sd-head">
                <span className="sd-title">Rep Leaderboard</span>
                <span className="sd-sub">
                  {sel ? <>filtering · <button className="sd-clear" onClick={() => setSel(null)}>clear</button></>
                    : "click a rep to filter the boards below"}
                  {isEditor && <> · <button className="sd-clear" onClick={() => setRosterOpen(true)}>manage reps</button></>}
                </span>
              </div>
              {/* Phones get a card per rep instead of a 12-column table: the four numbers you'd
                  actually check standing up, with the rest one tap away. Both render; the
                  media query shows exactly one. */}
              <div className="sd-cards">
                {data.reps.map((r) => {
                  const k = repKey(r);
                  const openCard = openRep === (k || "__u");
                  const stat = (label, node) => (
                    <div className="sd-c-stat" key={label}>
                      <span className="sd-c-l">{label}</span>
                      <span className="sd-c-v">{node}</span>
                    </div>
                  );
                  return (
                    <div className={`sd-c${sel === k ? " sel" : ""}`} key={k || "u"}>
                      <div className="sd-c-top">
                        <button className="sd-c-name" onClick={() => setSel(sel === k ? null : k)}>
                          <RepName email={r.rep_email} name={r.display_name} unmapped={r.unmapped} unassigned={r.unassigned} />
                        </button>
                        <button className="sd-c-more" aria-expanded={openCard}
                          aria-label={openCard ? "Hide detail" : "Show detail"}
                          onClick={() => setOpenRep(openCard ? null : (k || "__u"))}>
                          {openCard ? "Less ⌃" : "More ⌄"}
                        </button>
                      </div>
                      <div className="sd-c-grid">
                        {stat("Booked", <N m="kpi.booked" rep={k}>{r.booked}</N>)}
                        {stat("Held", <N m="kpi.held" rep={k}>{r.held}</N>)}
                        {stat("Show %", r.show_rate == null ? "-" :
                          <N m="kpi.show_rate" rep={k}><span className={`rate ${r.show_rate >= 70 ? "good" : "warn"}`}>{Math.round(r.show_rate)}%</span></N>)}
                        {stat("Won", <N m="kpi.won" rep={k}><span className="em">{r.won}</span></N>)}
                      </div>
                      {openCard && (
                        <div className="sd-c-grid more">
                          {stat("No-show", <N m="kpi.no_show" rep={k}>{r.noshow}</N>)}
                          {stat("Canc", <N m="kpi.cancelled" rep={k}>{r.cancelled}</N>)}
                          {stat("Likely Yes", <N m="kpi.likely_yes" rep={k}>{r.likely_yes}</N>)}
                          {stat("Likely No", <N m="kpi.likely_no" rep={k}>{r.likely_no}</N>)}
                          {stat("Link Sent", <N m="kpi.link_sent" rep={k}>{r.link_sent}</N>)}
                          {stat("Paid", <N m="kpi.paid" rep={k}>{r.paid}</N>)}
                          {stat("Close %", r.close_rate == null ? "-" :
                            <N m="kpi.close_rate" rep={k}><span className={`rate ${r.close_rate >= 50 ? "good" : ""}`}>{Math.round(r.close_rate)}%</span></N>)}
                        </div>
                      )}
                    </div>
                  );
                })}
                <div className="sd-c-foot">
                  Counts are launch-to-date calls from Acumyn's own event log, so a rebooked no-show
                  still counts as a no-show. Show rate = held / (held + no-show + cancelled).
                </div>
              </div>

              <div className="sd-tblwrap">
                <table className="sd-tbl">
                  <thead><tr>
                    <th>Rep</th><th>Booked</th><th>Held</th><th>No-show</th><th>Canc</th>
                    <th>Show %</th><th>Likely Yes</th><th>Likely No</th><th>Link Sent</th>
                    <th>Paid</th><th>Won</th><th>Close %</th>
                  </tr></thead>
                  <tbody>
                    {data.reps.map((r) => {
                      const k = repKey(r);
                      return (
                        <tr key={k || "u"} className={`click ${sel === k ? "sel" : ""}`}
                            onClick={() => setSel(sel === k ? null : k)}>
                          <td><RepName email={r.rep_email} name={r.display_name} unmapped={r.unmapped} unassigned={r.unassigned} /></td>
                          <td><N m="kpi.booked" rep={k}>{r.booked}</N></td>
                          <td><N m="kpi.held" rep={k}>{r.held}</N></td>
                          <td><N m="kpi.no_show" rep={k}>{r.noshow}</N></td>
                          <td><N m="kpi.cancelled" rep={k}>{r.cancelled}</N></td>
                          <td>{r.show_rate == null ? "-" : <N m="kpi.show_rate" rep={k}><span className={`rate ${r.show_rate >= 70 ? "good" : "warn"}`}>{Math.round(r.show_rate)}%</span></N>}</td>
                          <td><N m="kpi.likely_yes" rep={k}>{r.likely_yes}</N></td>
                          <td><N m="kpi.likely_no" rep={k}>{r.likely_no}</N></td>
                          <td><N m="kpi.link_sent" rep={k}>{r.link_sent}</N></td>
                          <td><N m="kpi.paid" rep={k}>{r.paid}</N></td>
                          <td><N m="kpi.won" rep={k}><span className="em">{r.won}</span></N></td>
                          <td>{r.close_rate == null ? "-" : <N m="kpi.close_rate" rep={k}><span className={`rate ${r.close_rate >= 50 ? "good" : ""}`}>{Math.round(r.close_rate)}%</span></N>}</td>
                        </tr>
                      );
                    })}
                    <tr className="sd-tfoot"><td colSpan="12">
                      Counts are launch-to-date calls from Acumyn's own event log, so a rebooked no-show still
                      counts as a no-show. Show rate = held / (held + no-show + cancelled).
                    </td></tr>
                  </tbody>
                </table>
              </div>
            </div>

            <div className="sd-cols">
              <div className="sd-card">
                <div className="sd-head"><span className="sd-title">Call Board</span>
                  <span className="sd-sub">next 48 hours{sel ? " · filtered" : ""}{calls.length ? ` · ${calls.length}` : ""}</span></div>
                {calls.length === 0 && <div className="sd-empty">No calls for this rep in the window.</div>}
                <div className="sd-scroll">
                  {calls.map((c, i) => {
                    const f = fmtCall(c.call_time_utc, tz, now);
                    return (
                      <div className="sd-lr" key={i}>
                        <span className="sd-lr-t">{c.unscheduled ? "Unscheduled" : `${f.day} · ${f.time}`}</span>
                        <span className="sd-lr-who">{c.contact_name}
                          <em className={c.unmapped ? "rawmail" : ""}>{c.display_name || c.rep_email || "Unassigned"}</em></span>
                        <span className={`sd-oc ${!c.rep_email ? "flag" : c.outcome ? OUT_TONE[c.outcome] : "pend"}`}>
                          {!c.rep_email ? "No rep" : c.outcome || "Pending"}</span>
                        {/* Recording, when there is one. Absent for every call until bots are
                            switched on, so the row is unchanged for anyone not using them. */}
                        {c.recording_url
                          ? <a className="sd-rec" href={c.recording_url} target="_blank" rel="noreferrer"
                               title="Watch the recording">▶</a>
                          : c.recording_status === "waiting"
                            ? <span className="sd-rec wait" title="Bot is in the waiting room — admit it">◷</span>
                            : null}
                      </div>
                    );
                  })}
                </div>
              </div>

              <div>
                <div className="sd-card">
                  <div className="sd-head"><span className="sd-title">No-Show Recovery</span>
                    <span className="sd-sub"><N m="recovery.chase">{noshows.filter((n) => !n.rebooked).length} to chase</N></span></div>
                  {noshows.length === 0 && <div className="sd-empty">None.</div>}
                  {noshows.map((n, i) => (
                    <div className="sd-lr" key={i}>
                      <span className="sd-lr-who">{n.contact_name}
                        <em className={n.unmapped ? "rawmail" : ""}>{n.display_name || n.rep_email || "Unassigned"}</em></span>
                      <span className="sd-nsd">{n.days_since}d</span>
                      <span className={`sd-nst ${n.rebooked ? "yes" : "no"}`}>{n.rebooked ? "rebooked" : "no rebook"}</span>
                    </div>
                  ))}
                </div>

                <div className="sd-health" style={{ marginTop: 14 }}>
                  <div className="sd-head" style={{ marginBottom: 6 }}><span className="sd-title">Data Health</span></div>
                  {(data.warnings || []).length === 0 && <div className="sd-empty">All clear.</div>}
                  {(data.warnings || []).map((h, i) => (
                    <div className="sd-hrow" key={i}>
                      <span className="sd-hn"><N m={h.key}>{h.n}</N></span>
                      <span><N m={h.key}><span className="sd-hl">{h.label}</span></N><span className="sd-hint">{h.hint}</span></span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="sd-card">
              <div className="sd-head"><span className="sd-title">Payment Mix</span>
                <span className="sd-sub">{mixTotal} enrolled · how they pay</span></div>
              <div className="sd-mix">
                {data.payment_mix.map((m) => (
                  <div className="sd-mixcell" key={m.type}>
                    <div className="sd-mix-t">{m.type}{m.provisional && <span className="prov">provisional</span>}</div>
                    <div className="sd-mix-n"><N m={`mix.${m.type}`}>{m.count}</N></div>
                    <div className="sd-mix-s">{m.note}</div>
                  </div>
                ))}
              </div>
              <div className="sd-bar">
                {data.payment_mix.map((m, i) => (
                  <span key={m.type} style={{ width: `${mixTotal ? (m.count / mixTotal) * 100 : 0}%`, background: barTone[i] || T.line }} />
                ))}
              </div>
              <div className="sd-mixfoot">
                <N m="money.upfront"><b>{kM(data.upfront_total)}</b></N> collected at signing across these members, against{" "}
                <N m="money.priced_arr"><b>{kM(data.priced_arr)}</b></N> of priced annual value. Monthly is annualized pending confirmation;
                Custom is unpriced and excluded from both figures.
              </div>
            </div>
          </>
        )}

        <div className="sd-foot">
          <b>Where the numbers come from:</b> the booking webhook writes rep, booking ID, and call time onto each
          deal, and outcomes route through the sales workflow. Acumyn logs every change it observes, so history
          survives even though GHL stores only the latest value. This desk reads; it never writes to GHL.
        </div>

        {rosterOpen && (
          <RepRosterDrawer businessKey={businessKey} usingSample={usingSample}
            onClose={() => setRosterOpen(false)} onSaved={onSaved} />
        )}
        {drill && (
          <SDDrawer drill={drill} businessKey={businessKey} usingSample={usingSample}
            onClose={() => setDrill(null)} />
        )}
      </div>
    </div>
    </SDDrillCtx.Provider>
  );
}

/* Owner/admin roster editor — display names for the leaderboard (§11.7). Emails come from
   the booking and are read-only; the roster auto-seeds from the team directory. */
function RepRosterDrawer({ businessKey, usingSample, onClose, onSaved }) {
  const [rows, setRows] = useState(null);
  const [saving, setSaving] = useState(false);
  const [shares, setShares] = useState({});      // rep email (lower) -> personal share URL
  const [copied, setCopied] = useState(null);
  useEffect(() => {
    if (usingSample) { setRows([]); return; }
    let alive = true;
    getJSON(`/businesses/${businessKey}/sales-desk/reps`)
      .then((d) => { if (alive) setRows(d); })
      .catch(() => { if (alive) setRows([]); });
    getJSON(`/businesses/${businessKey}/sales-desk/reps/share`)
      .then((d) => { if (alive) setShares(d || {}); })
      .catch(() => {});                           // non-owner or offline — just no share column
    return () => { alive = false; };
  }, [businessKey, usingSample]);

  const copy = async (email, url) => {
    try { await navigator.clipboard.writeText(url); setCopied(email); setTimeout(() => setCopied(null), 1500); }
    catch (e) { window.prompt("Copy the link:", url); }
  };
  const makeLink = async (email) => {
    const r = await postJSON(`/businesses/${businessKey}/sales-desk/reps/share`, { email });
    setShares((c) => ({ ...c, [email.toLowerCase()]: r.url }));
    copy(email, r.url);
  };
  const revoke = async (email) => {
    await delJSON(`/businesses/${businessKey}/sales-desk/reps/share?email=${encodeURIComponent(email)}`);
    setShares((c) => { const n = { ...c }; delete n[email.toLowerCase()]; return n; });
  };

  const patch = (i, k, v) => setRows((r) => r.map((x, j) => (j === i ? { ...x, [k]: v } : x)));
  // "still needs a name" clears live as you type, so the banner/chip/border reflect the draft.
  const stillNeeds = (r) => r.unmapped && !(r.display_name && r.display_name.trim());
  const needing = (rows || []).filter(stillNeeds).length;
  const save = async () => {
    setSaving(true);
    try { await putJSON(`/businesses/${businessKey}/sales-desk/reps`, { reps: rows }); onSaved && onSaved(); onClose(); }
    catch (e) { /* leave the drawer open on failure */ }
    finally { setSaving(false); }
  };

  return (
    <div className="sd-modal" onClick={onClose}>
      <style>{`
        .sd-modal { position:fixed; inset:0; background:${alpha(T.evergreen, .32)}; display:flex;
          align-items:flex-start; justify-content:center; padding:64px 16px; z-index:60; }
        .sd-box { background:${T.white}; border:1px solid ${T.line}; border-radius:16px; padding:18px 20px;
          width:100%; max-width:520px; max-height:80vh; overflow:auto; box-shadow:0 24px 60px ${alpha(T.evergreen, .18)}; }
        .sd-needs { font-size:11.5px; color:${T.amber}; background:${T.amberBg};
          border:1px solid ${alpha(T.amber, .28)}; border-radius:9px; padding:8px 11px; margin-bottom:10px; line-height:1.45; }
        .sd-rrow { display:flex; align-items:center; gap:10px; padding:8px 0 2px; }
        .sd-rrow2 { border-bottom:1px solid ${alpha(T.line, .5)}; padding-bottom:6px; }
        .sd-rrow2:last-of-type { border-bottom:none; }
        .sd-rrow2.needs .sd-input { border-color:${alpha(T.amber, .55)}; }
        .sd-sharerow { font-size:10.5px; color:${T.muted}; padding-left:2px; }
        .sd-sharerow .sd-clear.warn { color:${T.amber}; }
        .sd-rmail { flex:1; min-width:0; display:flex; align-items:center; gap:7px; overflow:hidden; }
        .sd-rmail .rawmail { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .sd-input { font:inherit; font-size:12.5px; color:${T.ink}; border:1px solid ${T.line}; border-radius:8px;
          padding:6px 9px; width:190px; flex:none; background:${T.white}; }
        .sd-input:focus { outline:none; border-color:${T.petalDeep}; }
        .sd-active { font-size:11px; color:${T.tertiary}; display:flex; align-items:center; gap:5px; flex:none; }
        .sd-active.off { opacity:.4; }
        @media (pointer: coarse), (max-width:700px) {
          .sd-input { font-size:16px; padding:9px 10px; }        /* no iOS focus zoom */
          .sd-rrow { flex-wrap:wrap; }
          .sd-input { width:100%; }
        }
        .sd-btn { font:inherit; font-size:12px; font-weight:700; border:none; border-radius:9px; padding:8px 15px;
          background:${T.evergreen}; color:${T.onDark}; cursor:pointer; }
        .sd-btn:disabled { opacity:.5; cursor:default; }
        .sd-btn.ghost { background:transparent; color:${T.tertiary}; border:1px solid ${T.line}; }
      `}</style>
      <div className="sd-box" onClick={(e) => e.stopPropagation()}>
        <div className="sd-head"><span className="sd-title">Rep Roster</span>
          <button className="sd-clear" onClick={onClose}>close</button></div>
        <div className="sd-sub" style={{ marginBottom: 10 }}>
          Display names shown on the leaderboard. The email is the rep's own <b>Sales Rep</b> value on the
          booking (read-only) — it isn't always a team-directory address, so anyone booking calls appears here.
          Name them or deactivate a rep who's left.
        </div>
        {needing > 0 && (
          <div className="sd-needs">
            {needing} rep{needing > 1 ? "s are" : " is"} booking calls but not named yet — listed first. Give them
            a name and they'll replace the raw email on the leaderboard.
          </div>
        )}
        {rows === null && <div className="sd-empty">Loading…</div>}
        {rows && rows.length === 0 && (
          <div className="sd-empty">{usingSample ? "Editing is disabled in sample mode." : "No reps yet — they appear once bookings sync."}</div>
        )}
        {(rows || []).map((r, i) => {
          const url = shares[(r.email || "").toLowerCase()];
          return (
          <div className={`sd-rrow2${stillNeeds(r) ? " needs" : ""}`} key={r.email}>
            <div className="sd-rrow">
              <span className="sd-rmail">
                <span className="rawmail">{r.email}</span>
                {stillNeeds(r) && <span className="flagchip">on calls · unnamed</span>}
              </span>
              <input className="sd-input" value={r.display_name || ""} placeholder={r.unmapped ? "Add a name" : r.email}
                onChange={(e) => patch(i, "display_name", e.target.value)} />
              {/* 'active' has no roster row to persist to until the rep is named, so it's disabled there. */}
              <label className={`sd-active${stillNeeds(r) ? " off" : ""}`}>
                <input type="checkbox" checked={r.is_active !== false} disabled={stillNeeds(r)}
                  onChange={(e) => patch(i, "is_active", e.target.checked)} /> active</label>
            </div>
            <div className="sd-sharerow">
              {url ? (
                <>personal link · <button className="sd-clear" onClick={() => copy(r.email, url)}>
                  {copied === r.email ? "copied ✓" : "copy"}</button> · {" "}
                <button className="sd-clear warn" onClick={() => revoke(r.email)}>revoke</button></>
              ) : (
                <button className="sd-clear" onClick={() => makeLink(r.email)}>create personal link</button>
              )}
            </div>
          </div>
          );
        })}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 14 }}>
          <button className="sd-btn ghost" onClick={onClose}>Cancel</button>
          <button className="sd-btn" disabled={saving || usingSample || !rows || rows.length === 0} onClick={save}>
            {saving ? "Saving…" : "Save"}</button>
        </div>
      </div>
    </div>
  );
}
