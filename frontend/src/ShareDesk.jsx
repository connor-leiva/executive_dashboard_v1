/* Public, no-login page for a sales rep's PERSONAL Sales Desk link (own numbers only).
   Token-scoped: GET /share/{token}/desk returns just their leaderboard row, call board,
   and no-shows. No app nav, no route back in; a revoked token renders a dead-link note. */
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { getPublic } from "./api";
import { T, alpha } from "./theme.js";

const pct = (v) => (v == null ? "—" : `${Math.round(v)}%`);

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
  useEffect(() => {
    let alive = true;
    getPublic(`/share/${token}/desk`)
      .then((r) => alive && setD(r))
      .catch(() => alive && setErr(true));
    return () => { alive = false; };
  }, [token]);

  const stat = (label, value, sub) => (
    <div className="sh-stat" key={label}>
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
          {stat("Booked", r.booked, `${r.upcoming} upcoming`)}
          {stat("Held", r.held)}
          {stat("No-show", r.noshow)}
          {stat("Cancelled", r.cancelled)}
          {stat("Show rate", pct(r.show_rate))}
        </div>
        <div className="sh-grid">
          {stat("Likely Yes", r.likely_yes)}
          {stat("Likely No", r.likely_no)}
          {stat("Link Sent", r.link_sent)}
          {stat("Paid", r.paid, "cash in, unsigned")}
          {stat("Won", r.won, `close ${pct(r.close_rate)}`)}
        </div>
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
          <div className="sh-t">No-shows to chase <em>{chase.length}</em></div>
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
        @media (max-width:560px){ .sh-grid { grid-template-columns:repeat(3,1fr); } }
      `}</style>
      <div className="sh-wrap">{body()}</div>
    </div>
  );
}
