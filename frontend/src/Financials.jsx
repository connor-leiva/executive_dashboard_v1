import { useState } from "react";
import { useFinancials } from "./useFinancials.js";
import { SpringSignature } from "./Brand.jsx";

/* ULRG + Team — three-lens Financials (Live / Projection / Booked).
   Design source of truth: ulrg-financial-focus.jsx. Wired to the live API via
   useFinancials; per-lens accents/tags/descriptions are presentation constants.
   CSS is scoped under .fin-root so its generic class names can't collide. */

const C = {
  ink: "#002E2C", body: "#3B4B44", slate: "#5C6B62", muted: "#93A099",
  hair: "#ECE6DC", page: "#F0EBE3", surface: "#FFFFFF",
  meadow: "#5F7D5A", teal: "#1F6E72", evergreen: "#002E2C",
  poppyDeep: "#CE4E29", amber: "#9C6A1E", amberBg: "#F5EAD3",
  onDark: "#F4EFE7", onDarkMute: "#9FB4AE",
  dLive: "#9CC496", dProj: "#5FBFC4", dBooked: "#E4D9BF",
};

const PRES = {
  live: { name: "Live", tag: "Sisu · real-time", accent: C.meadow, dAccent: C.dLive, live: true,
          desc: "Earned month to date, ahead of the books" },
  projection: { name: "Projection", tag: "Sisu · forecast", accent: C.teal, dAccent: C.dProj,
                desc: "Where the period lands if pending holds" },
  booked: { name: "Booked", tag: "QuickBooks", accent: C.evergreen, dAccent: C.dBooked,
            desc: "Posted to QuickBooks so far" },
};
const ORDER = ["live", "projection", "booked"];
const FLAG_LABEL = { close_in_progress: "Close in progress" };

const fmt = (n) => Math.abs(Math.round(n)).toLocaleString("en-US");
const money = (n) => (n < 0 ? `($${fmt(n)})` : `$${fmt(n)}`);
// Hero / selector / legend: keep the sign visible (a loss must not read as a gain).
const signedMoney = (n) => (n < 0 ? `-$${fmt(n)}` : `$${fmt(n)}`);

function PL({ rows, onDrill }) {
  return (
    <div className="pl">
      {rows.map((r, i) => {
        const clickable = r.key && onDrill;
        const cls = `plr ${r.kind === "tot" ? "tot" : ""} ${r.kind === "sub" ? "sub" : ""} ${r.kind === "ded" ? "ded" : ""} ${clickable ? "clk" : ""}`;
        return (
          <div key={i} className={cls}
            onClick={clickable ? () => onDrill(r.key) : undefined}
            role={clickable ? "button" : undefined} tabIndex={clickable ? 0 : undefined}
            onKeyDown={clickable ? (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onDrill(r.key); } } : undefined}>
            <span className="pll">{r.l}{r.est && <em className="est">est</em>}{clickable && <span className="drill">↗</span>}</span>
            <span className="plv">{money(r.v)}</span>
          </div>
        );
      })}
    </div>
  );
}

function SplitLine({ closed, pending, closedUnits, pendingUnits }) {
  const total = closed + pending || 1;
  const cp = (closed / total) * 100;
  return (
    <div className="sl">
      <div className="sl-bar">
        <span className="sl-closed" style={{ width: `${cp}%` }} />
        <span className="sl-pending" style={{ width: `${100 - cp}%` }} />
      </div>
      <div className="sl-cap">
        <span><i className="sl-dot solid" />Closed <b>${fmt(closed)}</b> · {closedUnits} units</span>
        <span><i className="sl-dot light" />Pending <b>${fmt(pending)}</b> · {pendingUnits}</span>
      </div>
    </div>
  );
}

function Trajectory({ lenses, active }) {
  const projected = lenses.projection.profit || 0;
  const pct = (v) => (projected > 0 ? Math.max(0, Math.min(100, (v / projected) * 100)) : 0);
  const legend = [
    { k: "booked", label: "Booked", v: lenses.booked.profit, dot: C.dBooked },
    { k: "live", label: "Live", v: lenses.live.profit, dot: C.dLive },
    { k: "projection", label: "Projected", v: projected, dot: C.dProj },
  ];
  return (
    <div className="traj">
      <div className="traj-track">
        <div className="traj-forecast" />
        <div className="traj-live" style={{ width: `${pct(lenses.live.profit)}%` }} />
        <div className="traj-tick" style={{ left: `${pct(lenses.booked.profit)}%` }} />
      </div>
      <div className="traj-legend">
        {legend.map((x) => (
          <span key={x.k} className={`tl ${active === x.k ? "on" : ""}`}>
            <span className="tl-dot" style={{ background: x.dot }} />{x.label} <b>{signedMoney(x.v)}</b>
          </span>
        ))}
      </div>
    </div>
  );
}

export default function Financials({ businessKey = "ulrg", businessName = "ULRG + Team", period = "mtd", onDrill }) {
  const { data, loading, error, retry } = useFinancials(businessKey, period);
  const [active, setActive] = useState("projection");

  return (
    <div className="fin-root">
      <style>{FIN_CSS}</style>
      {loading ? (
        <div className="mod"><div className="hero skel" style={{ height: 260 }} /><div className="card skel" style={{ height: 220 }} /></div>
      ) : error ? (
        <div className="mod"><div className="card" style={{ borderRadius: 18, textAlign: "center" }}>
          <p style={{ color: C.slate, fontSize: 13 }}>Couldn't load financials.</p>
          <button className="retry" onClick={retry}>Retry</button>
        </div></div>
      ) : (
        <Loaded data={data} businessName={businessName} businessKey={businessKey} active={active} setActive={setActive} onDrill={onDrill} />
      )}
    </div>
  );
}

function Loaded({ data, businessName, businessKey, active, setActive, onDrill }) {
  const drill = onDrill ? (key) => onDrill(key, businessKey) : undefined;   // scope drill-downs to this entity
  const lenses = data.lenses;
  const recon = data.reconciliation;
  const monthYear = new Date(`${data.period.start}T00:00:00`).toLocaleString("en-US", { month: "long", year: "numeric" });
  const periodDesc = data.period.is_current ? "month to date" : (data.period.label || "").toLowerCase();

  const disp = (k) => {
    const api = lenses[k];
    const p = PRES[k];
    let units = null;
    if (k === "live") units = `${api.units} units closed`;
    else if (k === "projection") units = `${api.units} units · ${api.pending_units} pending`;
    const flag = api.flag ? (FLAG_LABEL[api.flag] || api.flag) : null;
    return { ...p, ...api, unitsLabel: units, flagLabel: flag };
  };
  const L = disp(active);
  const P = disp("projection");

  return (
    <div className="mod">
      <div className="ctx">
        <span className="ctx-bar" />
        <span className="ctx-h">Financials</span>
        <span className="ctx-s">{businessName} · {monthYear} · {periodDesc}</span>
      </div>

      {/* hero */}
      <div className="hero">
        <SpringSignature tone="light" height={42} aria-hidden style={{ position: "absolute", top: 14, right: 24, opacity: 0.12, pointerEvents: "none" }} />
        <div className="feat" key={active}>
          <div className="eyebrow" style={{ color: L.dAccent }}>
            <span className={`edot ${L.live ? "live" : ""}`} style={{ background: L.dAccent, color: L.dAccent }} />
            {L.name}<span className="etag">· {L.tag}</span>
          </div>
          <div className="hprofit">{signedMoney(L.profit)}</div>
          <div className="hdesc">{L.desc}</div>
        </div>

        <Trajectory lenses={lenses} active={active} />

        <div className="sel">
          {ORDER.map((k) => {
            const x = disp(k);
            return (
              <button key={k} className={`seg ${active === k ? "on" : ""}`} style={{ "--da": x.dAccent }}
                onClick={() => setActive(k)}>
                <span className="seg-name">
                  <span className={`seg-dot ${x.live ? "live" : ""}`} style={{ background: x.dAccent, color: x.dAccent }} />
                  {x.name}
                </span>
                <span className="seg-v">{signedMoney(x.profit)}</span>
                {x.flagLabel && <span className="seg-flag">{x.flagLabel}</span>}
              </button>
            );
          })}
        </div>
      </div>

      {/* focus card */}
      <div className="card">
        <div className="fc-body" key={active}>
          <div className="fc-head">
            <div>
              <div className="fc-label" style={{ color: L.accent }}>{L.name}</div>
              <div className="fc-tag">{L.tag}</div>
            </div>
            {L.flagLabel ? (
              <span className="flag"><span className="flag-dot" />{L.flagLabel}</span>
            ) : L.unitsLabel ? <span className="fc-units">{L.unitsLabel}</span> : null}
          </div>

          {active === "projection" ? (
            <>
              <PL rows={[P.rows[0]]} onDrill={drill} />
              <SplitLine closed={P.closed_gci} pending={P.pending_gci}
                         closedUnits={P.closed_units} pendingUnits={P.pending_units} />
              <PL rows={P.rows.slice(1)} onDrill={drill} />
            </>
          ) : (
            <PL rows={L.rows} onDrill={drill} />
          )}

          {active === "live" && (
            <div className="note">Ahead of the books. Sisu sees <b>${fmt(recon.sisu_closed)}</b> closed;
              {" "}<span className="gap">${fmt(recon.gap_gci)} not yet posted</span> to QuickBooks.</div>
          )}
          {active === "booked" && (
            <div className="note">Sisu shows <b>${fmt(recon.sisu_closed)}</b> closed · QuickBooks posted <b>${fmt(recon.qbo_booked)}</b> · <span className="gap">${fmt(recon.gap_gci)} (${fmt(recon.gap_profit)} profit) not yet booked</span>. Final at month close.</div>
          )}
        </div>
      </div>
    </div>
  );
}

const FIN_CSS = `
  .fin-root { font-family:Inter,sans-serif; color:${C.ink}; }
  .fin-root .mod { max-width:100%; }
  .fin-root .skel { background:linear-gradient(90deg, ${C.hair} 25%, ${C.page} 50%, ${C.hair} 75%); background-size:800px 100%; animation:finshimmer 1.4s linear infinite; border-radius:18px; }
  .fin-root .retry { margin-top:10px; font:600 12.5px Inter,sans-serif; color:${C.ink}; background:${C.surface}; border:1px solid ${C.hair}; border-radius:8px; padding:6px 12px; cursor:pointer; }
  .fin-root .ctx { display:flex; align-items:center; gap:11px; margin-bottom:16px; }
  .fin-root .ctx-bar { width:4px; height:19px; border-radius:2px; background:${C.meadow}; }
  .fin-root .ctx-h { font-family:Poppins,sans-serif; font-size:17px; font-weight:600; letter-spacing:-.01em; }
  .fin-root .ctx-s { font-size:12px; color:${C.muted}; }
  .fin-root .hero { position:relative; overflow:hidden; border-radius:18px 18px 0 0; padding:26px 28px 22px;
    background-color:${C.meadow};
    background-image:url(/brand/RibbedGradient_Meadow.jpg);
    background-size:cover; background-position:center; background-blend-mode:multiply;
    box-shadow:0 2px 6px rgba(0,46,44,.12), 0 18px 40px rgba(0,46,44,.13); }
  .fin-root .feat { min-height:104px; }
  .fin-root .eyebrow { font-family:Poppins,sans-serif; font-size:11px; font-weight:700; letter-spacing:.13em; text-transform:uppercase; display:inline-flex; align-items:center; gap:8px; }
  .fin-root .eyebrow .edot { width:8px; height:8px; border-radius:99px; }
  .fin-root .eyebrow .etag { color:${C.onDarkMute}; font-weight:500; letter-spacing:.02em; text-transform:none; font-size:12px; }
  .fin-root .hprofit { font-family:Poppins,sans-serif; font-size:44px; font-weight:700; letter-spacing:-.025em; color:${C.onDark}; line-height:1; margin:12px 0 6px; font-variant-numeric:tabular-nums; }
  .fin-root .hdesc { font-size:12.5px; color:${C.onDarkMute}; }
  .fin-root .traj { margin-top:20px; }
  .fin-root .traj-track { position:relative; height:9px; border-radius:6px; overflow:hidden; background:rgba(244,239,231,.14); }
  .fin-root .traj-forecast { position:absolute; inset:0; background:rgba(95,191,196,.34); }
  .fin-root .traj-live { position:absolute; left:0; top:0; bottom:0; background:${C.dLive}; border-radius:6px 0 0 6px; }
  .fin-root .traj-tick { position:absolute; top:-3px; bottom:-3px; width:2px; background:${C.onDark}; box-shadow:0 0 0 2px rgba(0,46,44,.5); }
  .fin-root .traj-legend { display:flex; gap:20px; margin-top:11px; flex-wrap:wrap; }
  .fin-root .tl { font-size:11.5px; color:${C.onDarkMute}; display:inline-flex; align-items:center; gap:6px; }
  .fin-root .tl b { color:${C.onDarkMute}; font-weight:600; font-family:Poppins,sans-serif; }
  .fin-root .tl.on { color:${C.onDark}; } .fin-root .tl.on b { color:${C.onDark}; }
  .fin-root .tl-dot { width:7px; height:7px; border-radius:99px; }
  .fin-root .sel { display:grid; grid-template-columns:repeat(3,1fr); gap:9px; margin-top:22px; }
  .fin-root .seg { background:rgba(244,239,231,.06); border:1px solid rgba(244,239,231,.12); border-radius:11px; padding:11px 13px; cursor:pointer; text-align:left; position:relative; transition:background .16s ease, border-color .16s ease; }
  .fin-root .seg:hover { background:rgba(244,239,231,.11); }
  .fin-root .seg.on { background:rgba(244,239,231,.14); border-color:rgba(244,239,231,.28); }
  .fin-root .seg.on::after { content:""; position:absolute; left:13px; right:13px; bottom:-1px; height:2px; border-radius:2px; background:var(--da); }
  .fin-root .seg-name { font-size:11.5px; font-weight:600; color:${C.onDark}; display:flex; align-items:center; gap:6px; }
  .fin-root .seg-dot { width:7px; height:7px; border-radius:99px; }
  .fin-root .seg-v { font-family:Poppins,sans-serif; font-size:18px; font-weight:700; color:${C.onDark}; margin-top:6px; letter-spacing:-.02em; font-variant-numeric:tabular-nums; display:block; }
  .fin-root .seg-flag { font-size:9.5px; font-weight:600; color:${C.dBooked}; margin-top:4px; display:block; }
  .fin-root .card { background:${C.surface}; border:1px solid ${C.hair}; border-top:none; border-radius:0 0 18px 18px; padding:24px 28px; box-shadow:0 12px 30px rgba(0,46,44,.06); }
  .fin-root .fc-head { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:4px; }
  .fin-root .fc-label { font-family:Poppins,sans-serif; font-size:11px; font-weight:700; letter-spacing:.13em; text-transform:uppercase; }
  .fin-root .fc-tag { font-size:12px; color:${C.muted}; margin-top:4px; }
  .fin-root .fc-units { font-size:12px; color:${C.muted}; }
  .fin-root .flag { display:inline-flex; align-items:center; gap:5px; font-size:10.5px; font-weight:600; color:${C.amber}; background:${C.amberBg}; border-radius:6px; padding:3px 9px; }
  .fin-root .flag-dot { width:5px; height:5px; border-radius:99px; background:${C.amber}; }
  .fin-root .pl { margin-top:8px; }
  .fin-root .plr { display:flex; justify-content:space-between; align-items:baseline; padding:8px 0; }
  .fin-root .pll { font-size:12.5px; color:${C.body}; }
  .fin-root .plv { font-family:Poppins,sans-serif; font-size:13.5px; font-weight:500; color:${C.ink}; font-variant-numeric:tabular-nums; }
  .fin-root .plr.ded .pll, .fin-root .plr.ded .plv { color:${C.slate}; font-weight:400; }
  .fin-root .plr.sub { border-top:1px solid ${C.hair}; margin-top:2px; padding-top:11px; }
  .fin-root .plr.sub .pll, .fin-root .plr.sub .plv { font-weight:600; }
  .fin-root .plr.tot { border-top:2px solid ${C.ink}; margin-top:4px; padding-top:12px; }
  .fin-root .plr.tot .pll { font-weight:700; font-size:13px; } .fin-root .plr.tot .plv { font-weight:700; font-size:17px; }
  .fin-root .plr.clk { cursor:pointer; margin:0 -10px; padding-left:10px; padding-right:10px; border-radius:8px; }
  .fin-root .plr.clk:hover { background:${C.page}; }
  .fin-root .plr.clk:focus-visible { outline:2px solid ${C.dProj}; outline-offset:-2px; }
  .fin-root .drill { color:${C.muted}; font-size:11px; margin-left:6px; }
  .fin-root .plr.clk:hover .drill { color:${C.teal}; }
  .fin-root .est { font-style:normal; font-size:9px; font-weight:600; letter-spacing:.05em; text-transform:uppercase; color:${C.muted}; border:1px solid ${C.hair}; border-radius:3px; padding:1px 4px; margin-left:6px; }
  .fin-root .sl { padding:2px 0 10px; }
  .fin-root .sl-bar { display:flex; height:9px; border-radius:5px; overflow:hidden; gap:2px; }
  .fin-root .sl-closed { background:${C.teal}; border-radius:5px 0 0 5px; }
  .fin-root .sl-pending { background:${C.teal}; opacity:.28; border-radius:0 5px 5px 0; }
  .fin-root .sl-cap { display:flex; justify-content:space-between; margin-top:8px; font-size:11px; color:${C.muted}; flex-wrap:wrap; gap:6px; }
  .fin-root .sl-cap b { color:${C.body}; font-weight:600; font-family:Poppins,sans-serif; }
  .fin-root .sl-dot { width:7px; height:7px; border-radius:2px; display:inline-block; margin-right:6px; font-style:normal; }
  .fin-root .sl-dot.solid { background:${C.teal}; }
  .fin-root .sl-dot.light { background:${C.teal}; opacity:.28; }
  .fin-root .note { margin-top:14px; padding-top:13px; border-top:1px solid ${C.hair}; font-size:12px; color:${C.slate}; line-height:1.5; }
  .fin-root .note b { color:${C.ink}; font-weight:600; } .fin-root .gap { color:${C.poppyDeep}; font-weight:600; }
  @media (prefers-reduced-motion:no-preference){
    .fin-root .edot.live, .fin-root .seg-dot.live { animation:finpulse 2.2s ease-out infinite; }
    .fin-root .feat, .fin-root .fc-body { animation:finfade .26s ease; }
  }
  @keyframes finpulse { 0%{ box-shadow:0 0 0 0 currentColor; } 70%{ box-shadow:0 0 0 5px rgba(0,0,0,0); } 100%{ box-shadow:0 0 0 0 rgba(0,0,0,0); } }
  @keyframes finfade { from{ opacity:0; transform:translateY(5px); } to{ opacity:1; transform:none; } }
  @keyframes finshimmer { 0%{ background-position:-400px 0; } 100%{ background-position:400px 0; } }
  .fin-root .seg:focus-visible { outline:2px solid ${C.dProj}; outline-offset:2px; }
  @media (max-width:600px){ .fin-root .sel{ grid-template-columns:1fr; } }
`;
