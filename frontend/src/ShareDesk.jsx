/* Public, no-login page for a sales rep's PERSONAL Sales Desk link (own numbers only).
   Token-scoped: GET /share/{token}/desk returns just their leaderboard row, call board,
   and no-shows. No app nav, no route back in; a revoked token renders a dead-link note. */
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { getPublic } from "./api";
import { T, alpha } from "./theme.js";
import { DrillRecords, DrillCalc } from "./LaunchSection.jsx";

const pct = (v) => (v == null ? "—" : `${Math.round(v)}%`);

/* Drill drawer for the rep's own numbers — token-scoped server-side; same records/calc
   shapes the dashboard renders. */
function ShDrawer({ token, metric, onClose }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let alive = true;
    setD(null); setErr(null);
    getPublic(`/share/${token}/desk/drill/${encodeURIComponent(metric)}`)
      .then((r) => alive && (r && (r.type === "records" || r.type === "calc")
        ? setD(r) : setErr("Couldn't load this one.")))
      .catch(() => alive && setErr("Couldn't load this one."));
    return () => { alive = false; };
  }, [token, metric]);
  return (
    <div className="drx-scrim" onClick={onClose}>
      <div className="drx" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="drx-head">
          <span className="drx-title">{d?.title || "Who's behind this"}</span>
          <button className="drx-x" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="drx-body">
          {err && <div className="drx-note">{err}</div>}
          {!d && !err && <div className="drx-note">Loading…</div>}
          {d && d.type === "records" && <DrillRecords d={d} />}
          {d && d.type === "calc" && <DrillCalc d={d} />}
        </div>
      </div>
    </div>
  );
}

function fmtCall(iso, tz, now) {
  if (!iso) return { day: "Unscheduled", time: "" };
  const d = new Date(iso);
  const ymd = (x) => new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(x);
  const today = ymd(now), tmrw = ymd(new Date(now.getTime() + 864e5)), that = ymd(d);
  const day = that === today ? "Today" : that === tmrw ? "Tomorrow"
    : new Intl.DateTimeFormat("en-US", { timeZone: tz, weekday: "short", month: "short", day: "numeric" }).format(d);
  return { day, time: new Intl.DateTimeFormat("en-US", { timeZone: tz, hour: "numeric", minute: "2-digit" }).format(d) };
}

export default function ShareDesk() {
  const { token } = useParams();
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  const [drill, setDrill] = useState(null);      // metric currently drilled into
  useEffect(() => {
    let alive = true;
    getPublic(`/share/${token}/desk`)
      .then((r) => alive && setD(r))
      .catch(() => alive && setErr(true));
    return () => { alive = false; };
  }, [token]);

  const stat = (label, value, sub, metric) => (
    <div className={`sh-stat${metric ? " click" : ""}`} key={label} title={metric ? "Tap to see who" : undefined}
      role={metric ? "button" : undefined} tabIndex={metric ? 0 : undefined}
      onClick={metric ? () => setDrill(metric) : undefined}
      onKeyDown={metric ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setDrill(metric); } } : undefined}>
      <div className="sh-l">{label}</div><div className="sh-v">{value}</div>
      {sub && <div className="sh-s">{sub}</div>}
    </div>
  );

  const body = () => {
    if (err || (d && !d.rep)) return <div className="sh-dead">This link is no longer active. Ask your admin for a new one.</div>;
    if (!d) return <div className="sh-dead">Loading…</div>;
    const r = d.rep, tz = d.default_tz || "America/Denver", now = new Date(d.as_of || Date.now());
    const chase = (d.no_shows || []).filter((n) => !n.rebooked);
    return (
      <>
        <div className="sh-head">
          <span className="sh-bar" />
          <div>
            <div className="sh-name">{d.display_name}</div>
            <div className="sh-sub">{d.launch_name} · your Sales Desk · launch to date</div>
          </div>
        </div>
        <div className="sh-grid">
          {stat("Booked", r.booked, `${r.upcoming} upcoming`, "kpi.booked")}
          {stat("Held", r.held, null, "kpi.held")}
          {stat("No-show", r.noshow, null, "kpi.no_show")}
          {stat("Cancelled", r.cancelled, null, "kpi.cancelled")}
          {stat("Show rate", pct(r.show_rate), null, "kpi.show_rate")}
        </div>
        <div className="sh-grid">
          {stat("Likely Yes", r.likely_yes, null, "kpi.likely_yes")}
          {stat("Likely No", r.likely_no, null, "kpi.likely_no")}
          {stat("Link Sent", r.link_sent, null, "kpi.link_sent")}
          {stat("Paid", r.paid, "cash in, unsigned", "kpi.paid")}
          {stat("Won", r.won, `close ${pct(r.close_rate)}`, "kpi.won")}
        </div>
        <div className="sh-tap">Tap any number to see exactly who's behind it.</div>
        <div className="sh-card">
          <div className="sh-t">Your call board</div>
          {(d.calls || []).length === 0 && <div className="sh-empty">No calls on the board.</div>}
          {(d.calls || []).map((c, i) => {
            const f = fmtCall(c.call_time_utc, tz, now);
            return (
              <div className="sh-row" key={i}>
                <span className="sh-when">{c.unscheduled ? "Unscheduled" : `${f.day} · ${f.time}`}</span>
                <span className="sh-who">{c.contact_name}</span>
                <span className={`sh-oc ${c.outcome ? "" : "pend"}`}>{c.outcome || "Pending"}</span>
              </div>
            );
          })}
        </div>
        <div className="sh-card">
          <div className="sh-t">No-shows to chase{" "}
            <em role="button" tabIndex={0} style={{ cursor: "pointer" }}
              onClick={() => setDrill("recovery.chase")}>{chase.length}</em></div>
          {chase.length === 0 && <div className="sh-empty">None — clean slate.</div>}
          {chase.map((n, i) => (
            <div className="sh-row" key={i}>
              <span className="sh-who">{n.contact_name}</span>
              <span className="sh-when">{n.days_since}d since the miss</span>
            </div>
          ))}
        </div>
        <div className="sh-foot">Times shown in {tz.replace("_", " ")}. Read-only — updated as the dashboard syncs.</div>
      </>
    );
  };

  return (
    <div className="sh-root">
      <style>{`
        .sh-root { min-height:100vh; background:${T.parchment}; color:${T.ink};
          font-family:Inter,system-ui,sans-serif; padding:26px 16px 48px; }
        .sh-root * { box-sizing:border-box; }
        .sh-wrap { max-width:640px; margin:0 auto; }
        .sh-head { display:flex; gap:12px; align-items:center; margin-bottom:16px; }
        .sh-bar { width:4px; height:34px; border-radius:2px; background:${T.petal}; }
        .sh-name { font-family:Poppins,sans-serif; font-size:20px; font-weight:700; letter-spacing:-.01em; }
        .sh-sub { font-size:12px; color:${T.muted}; }
        .sh-grid { display:grid; grid-template-columns:repeat(5,1fr); gap:8px; margin-bottom:10px; }
        .sh-stat { background:${T.white}; border:1px solid ${T.line}; border-radius:12px; padding:10px 12px; }
        .sh-l { font-size:9.5px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:${T.tertiary}; }
        .sh-v { font-family:Poppins,sans-serif; font-size:20px; font-weight:700; margin-top:2px; font-variant-numeric:tabular-nums; }
        .sh-s { font-size:10px; color:${T.muted}; margin-top:1px; }
        .sh-card { background:${T.white}; border:1px solid ${T.line}; border-radius:14px; padding:14px 16px; margin-top:12px; }
        .sh-t { font-family:Poppins,sans-serif; font-size:11px; font-weight:700; letter-spacing:.12em;
          text-transform:uppercase; margin-bottom:8px; }
        .sh-t em { font-style:normal; color:${T.amber}; margin-left:6px; }
        .sh-row { display:flex; align-items:center; gap:10px; padding:8px 0; border-bottom:1px solid ${alpha(T.line, .5)}; }
        .sh-row:last-child { border-bottom:none; }
        .sh-when { font-size:11px; color:${T.muted}; width:130px; flex:none; }
        .sh-who { font-size:13px; font-weight:600; flex:1; }
        .sh-oc { font-size:10.5px; font-weight:700; } .sh-oc.pend { color:${T.muted}; }
        .sh-empty { font-size:12.5px; color:${T.muted}; padding:6px 0; }
        .sh-dead { max-width:420px; margin:80px auto; text-align:center; font-size:14px; color:${T.muted}; }
        .sh-foot { font-size:10.5px; color:${T.muted}; margin-top:14px; text-align:center; }
        .sh-tap { font-size:10.5px; color:${T.tertiary}; margin:2px 2px 0; }
        .sh-stat.click { cursor:pointer; transition:border-color .12s; }
        .sh-stat.click:hover, .sh-stat.click:focus-visible { border-color:${T.petalDeep}; outline:none; }
        .sh-stat.click .sh-v { box-shadow:inset 0 -1.5px 0 ${alpha(T.petalDeep, .45)}; width:fit-content; }
        @media (max-width:560px){ .sh-grid { grid-template-columns:repeat(3,1fr); } }

        .drx-scrim { position:fixed; inset:0; background:${alpha(T.evergreen, .32)}; z-index:70;
          display:flex; justify-content:flex-end; }
        .drx { width:min(440px,94vw); height:100%; background:${T.white}; display:flex; flex-direction:column;
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
      `}</style>
      <div className="sh-wrap">{body()}</div>
      {drill && <ShDrawer token={token} metric={drill} onClose={() => setDrill(null)} />}
    </div>
  );
}
