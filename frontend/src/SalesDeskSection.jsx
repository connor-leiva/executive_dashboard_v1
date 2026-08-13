/* beCollective · Sales Desk — the rep-throughput view (SPEC-becollective-salesdesk §8).
   Built from becollective-salesdesk-v2.jsx with the brand deferrals applied: tokens come
   from theme.js by role (no local C, no font import, no hex literals). Everything is
   pre-computed server-side from the SalesCall event log; this component only renders it. */
import { useEffect, useState } from "react";
import { T, alpha } from "./theme.js";
import { getJSON, putJSON } from "./api";

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

const repKey = (r) => (r.unassigned ? "__unassigned__" : r.rep_email);
const RepName = ({ email, name, unmapped, unassigned }) =>
  unassigned ? <>Unassigned <span className="flagchip">no host</span></>
    : name ? name
      : <><span className="rawmail">{email}</span>{unmapped && <span className="flagchip">unmapped</span>}</>;

export default function SalesDeskSection({ data, usingSample, role, businessKey = "springb", onSaved }) {
  const [sel, setSel] = useState(null);          // selected rep_email (client-side board filter)
  const [rosterOpen, setRosterOpen] = useState(false);
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

        .sd-cols { display:grid; grid-template-columns:1.35fr 1fr; gap:14px; }
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
        .sd-foot { font-size:11px; color:${T.muted}; margin-top:16px; line-height:1.6; } .sd-foot b { color:${T.tertiary}; }
        @media (max-width:760px){ .sd-kpis, .sd-mix { grid-template-columns:1fr 1fr; } .sd-cols { grid-template-columns:1fr; } }
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
          <div className="sd-kpi"><div className="sd-kpi-l">Calls booked</div><div className="sd-kpi-v">{t.booked}</div>
            <div className="sd-kpi-s">{t.upcoming} still upcoming</div></div>
          <div className="sd-kpi"><div className="sd-kpi-l">Show rate</div>
            <div className={`sd-kpi-v ${t.show_rate == null ? "" : t.show_rate >= 70 ? "good" : "warn"}`}>{pct(t.show_rate)}</div>
            <div className="sd-kpi-s">{t.held} held · {t.no_show} no-show · {t.cancelled} cancelled</div></div>
          <div className="sd-kpi"><div className="sd-kpi-l">Close rate</div><div className="sd-kpi-v">{pct(t.close_rate)}</div>
            <div className="sd-kpi-s">{t.won} won of {t.held} held</div></div>
          <div className="sd-kpi"><div className="sd-kpi-l">On the table</div><div className="sd-kpi-v">{kM(t.on_the_table)}</div>
            <div className="sd-kpi-s">{t.deciding} deciding × {kM(t.blended)}{t.blended_provisional ? " *" : ""} blended</div></div>
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
              <div className="sd-tblwrap">
                <table className="sd-tbl">
                  <thead><tr>
                    <th>Rep</th><th>Booked</th><th>Held</th><th>No-show</th><th>Canc</th><th>Resch</th>
                    <th>Show %</th><th>In play</th><th>Won</th><th>Close %</th>
                  </tr></thead>
                  <tbody>
                    {data.reps.map((r) => {
                      const k = repKey(r);
                      return (
                        <tr key={k || "u"} className={`click ${sel === k ? "sel" : ""}`}
                            onClick={() => setSel(sel === k ? null : k)}>
                          <td><RepName email={r.rep_email} name={r.display_name} unmapped={r.unmapped} unassigned={r.unassigned} /></td>
                          <td>{r.booked}</td><td>{r.held}</td><td>{r.noshow}</td><td>{r.cancelled}</td><td>{r.resched}</td>
                          <td>{r.show_rate == null ? "-" : <span className={`rate ${r.show_rate >= 70 ? "good" : "warn"}`}>{Math.round(r.show_rate)}%</span>}</td>
                          <td>{r.inplay}</td><td><span className="em">{r.won}</span></td>
                          <td>{r.close_rate == null ? "-" : <span className={`rate ${r.close_rate >= 50 ? "good" : ""}`}>{Math.round(r.close_rate)}%</span>}</td>
                        </tr>
                      );
                    })}
                    <tr className="sd-tfoot"><td colSpan="10">
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
                  <span className="sd-sub">next 48 hours{sel ? " · filtered" : ""}</span></div>
                {calls.length === 0 && <div className="sd-empty">No calls for this rep in the window.</div>}
                {calls.map((c, i) => {
                  const f = fmtCall(c.call_time_utc, tz, now);
                  return (
                    <div className="sd-lr" key={i}>
                      <span className="sd-lr-t">{c.unscheduled ? "Unscheduled" : `${f.day} · ${f.time}`}</span>
                      <span className="sd-lr-who">{c.contact_name}
                        <em className={c.unmapped ? "rawmail" : ""}>{c.display_name || c.rep_email || "Unassigned"}</em></span>
                      <span className={`sd-oc ${!c.rep_email ? "flag" : c.outcome ? OUT_TONE[c.outcome] : "pend"}`}>
                        {!c.rep_email ? "No rep" : c.outcome || "Pending"}</span>
                    </div>
                  );
                })}
              </div>

              <div>
                <div className="sd-card">
                  <div className="sd-head"><span className="sd-title">No-Show Recovery</span>
                    <span className="sd-sub">{noshows.filter((n) => !n.rebooked).length} to chase</span></div>
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
                      <span className="sd-hn">{h.n}</span>
                      <span><span className="sd-hl">{h.label}</span><span className="sd-hint">{h.hint}</span></span>
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
                    <div className="sd-mix-n">{m.count}</div>
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
                <b>{kM(data.upfront_total)}</b> collected at signing across these members, against{" "}
                <b>{kM(data.priced_arr)}</b> of priced annual value. Monthly is annualized pending confirmation;
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
      </div>
    </div>
  );
}

/* Owner/admin roster editor — display names for the leaderboard (§11.7). Emails come from
   the booking and are read-only; the roster auto-seeds from the team directory. */
function RepRosterDrawer({ businessKey, usingSample, onClose, onSaved }) {
  const [rows, setRows] = useState(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (usingSample) { setRows([]); return; }
    let alive = true;
    getJSON(`/businesses/${businessKey}/sales-desk/reps`)
      .then((d) => { if (alive) setRows(d); })
      .catch(() => { if (alive) setRows([]); });
    return () => { alive = false; };
  }, [businessKey, usingSample]);

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
        .sd-rrow { display:flex; align-items:center; gap:10px; padding:8px 0; border-bottom:1px solid ${alpha(T.line, .5)}; }
        .sd-rrow:last-of-type { border-bottom:none; }
        .sd-rrow.needs .sd-input { border-color:${alpha(T.amber, .55)}; }
        .sd-rmail { flex:1; min-width:0; display:flex; align-items:center; gap:7px; overflow:hidden; }
        .sd-rmail .rawmail { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .sd-input { font:inherit; font-size:12.5px; color:${T.ink}; border:1px solid ${T.line}; border-radius:8px;
          padding:6px 9px; width:190px; flex:none; background:${T.white}; }
        .sd-input:focus { outline:none; border-color:${T.petalDeep}; }
        .sd-active { font-size:11px; color:${T.tertiary}; display:flex; align-items:center; gap:5px; flex:none; }
        .sd-active.off { opacity:.4; }
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
        {(rows || []).map((r, i) => (
          <div className={`sd-rrow${stillNeeds(r) ? " needs" : ""}`} key={r.email}>
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
        ))}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 14 }}>
          <button className="sd-btn ghost" onClick={onClose}>Cancel</button>
          <button className="sd-btn" disabled={saving || usingSample || !rows || rows.length === 0} onClick={save}>
            {saving ? "Saving…" : "Save"}</button>
        </div>
      </div>
    </div>
  );
}
