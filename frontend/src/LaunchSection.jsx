/* beCollective — Launch section (production).

   Ports the mockup's instrument to the live payload (SPEC-becollective-launch §3/§8): all
   derivation is server-side (compute_launch), colors come from theme.js tokens, and the
   hero watermark is the production Spring mark (SpringSignature) rather than a mocked
   script wordmark. ARR ("annualized revenue added" — Spring's loose usage) is the headline;
   cash collected is a demoted line. The settings drawer PUTs config and refetches. */
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { T, alpha } from "./theme.js";
import { SpringSignature } from "./Brand.jsx";
import { putJSON, postJSON, getJSON } from "./api.js";

/* Every number on the tab is a drill target. A context carries the opener so any nested
   number can trigger it without prop-threading; <Num metric="…"> wraps the value. */
const DrillCtx = createContext(null);
function Num({ metric, children, title }) {
  const open = useContext(DrillCtx);
  if (!open || !metric) return <>{children}</>;
  return (
    <span className="num" role="button" tabIndex={0} title={title || "Drill in"}
      onClick={(e) => { e.stopPropagation(); open(metric); }}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(metric); } }}>
      {children}
    </span>
  );
}

/* The August 2026 cohort template — the create payload for the "New launch" button.
   Prices are provisional ($12k/$14k → ~$1.3M at 100 members); set the real price after.
   Module-local (not exported) so React Fast Refresh can hot-reload this file. */
const AUGUST_TEMPLATE = {
  name: "August 2026 Cohort", program: "beCollective",
  event_start: "2026-08-11", event_end: "2026-08-13",
  window_start: "2026-08-11", window_end: "2026-09-12",
  goal_arr: 1000000, ticket_pif: 12000, ticket_plan: 14000, plan_installments: 12, mix_pif: 0.5,
  pipeline_match: "Be Collective August 2026 Sales Funnel", cohort_value: "Aug 2026",
  goal_basis: "seats", seat_goal: 100,
  shift_name: "The Shift", shift_event_date: "2026-08-11", shift_goal: 2000,
  shift_reg_tag: "the shift", shift_actual: 175, shift_pace_tolerance: 0.08,
  shift_pace_curve: { "14": 0.19, "13": 0.22, "12": 0.247, "11": 0.275, "10": 0.309, "9": 0.348,
    "8": 0.39, "7": 0.432, "6": 0.481, "5": 0.584, "4": 0.67, "3": 0.734, "2": 0.801, "1": 0.864, "0": 0.94 },
};

/* Empty state shown to owners/admins when no launch exists yet — one click to stand up
   the August cohort (uses the current session; no terminal, no credentials to handle). */
export function LaunchEmpty({ businessKey = "springb", onCreated }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [tag, setTag] = useState(AUGUST_TEMPLATE.shift_reg_tag);
  const box = { fontFamily: "Inter,sans-serif", color: T.ink };
  async function create() {
    setBusy(true); setErr(null);
    try {
      await postJSON(`/businesses/${businessKey}/launches`, { ...AUGUST_TEMPLATE, shift_reg_tag: tag.trim() || null });
      onCreated && onCreated();
    } catch (e) {
      setErr(e.detail || e.message || "Create failed");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div style={{ ...box, maxWidth: 620, margin: "8px auto", background: T.white, border: `1px solid ${T.line}`,
      borderRadius: 16, padding: "28px 30px", boxShadow: `0 12px 30px ${alpha(T.evergreen, 0.06)}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
        <span style={{ width: 4, height: 20, borderRadius: 2, background: T.petal }} />
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 18, fontWeight: 600 }}>Set up the August 2026 launch</span>
      </div>
      <p style={{ fontSize: 13, color: T.slate, lineHeight: 1.6, margin: "6px 0 18px" }}>
        Stands up the beCollective August cohort: goal <b>100 members</b> (~$1M), the lead-up webinar
        <b> The Shift</b> (2,000 registrants, pacing on your empirical curve), and the five-stage sales funnel.
        You can fine-tune everything afterward in ⚙ Launch settings.
      </p>
      <label style={{ display: "block", fontSize: 12, fontWeight: 600, color: T.secondary, marginBottom: 5 }}>
        GHL Shift registration tag
      </label>
      <input value={tag} onChange={(e) => setTag(e.target.value)} placeholder="the shift"
        style={{ width: "100%", border: `1px solid ${T.line}`, borderRadius: 8, padding: "9px 11px",
          fontFamily: "inherit", fontSize: 13, color: T.ink, background: T.white, boxSizing: "border-box" }} />
      <div style={{ fontSize: 11, color: T.muted, marginTop: 5 }}>
        Contacts with this tag are counted as registrants (live). Until the tag syncs, a manual seed of 175 shows.
      </div>
      {err && <div style={{ fontSize: 12, color: T.poppyText, marginTop: 12 }}>{err}</div>}
      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 18 }}>
        <button onClick={create} disabled={busy} style={{ fontFamily: "inherit", fontSize: 13, fontWeight: 600,
          borderRadius: 9, padding: "10px 18px", border: "none", cursor: busy ? "default" : "pointer",
          background: busy ? T.muted : T.evergreen, color: T.onDark }}>
          {busy ? "Creating…" : "Create August launch"}
        </button>
      </div>
    </div>
  );
}

const kMoney = (n) => {
  const a = Math.abs(Math.round(n));
  if (a >= 1_000_000) return `$${(a / 1_000_000).toFixed(a % 1_000_000 === 0 ? 0 : 1)}M`;
  if (a >= 1_000) return `$${Math.round(a / 1000)}K`;
  return `$${a}`;
};
const parse = (s) => { const [y, m, d] = String(s).split("-").map(Number); return new Date(y, m - 1, d); };
const fmtDate = (s) => (s ? parse(s).toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "—");

/* ── the signature instrument: goal bar with a soft linear-pace reference ── */
function GoalBar({ cfg, D, seatPrimary }) {
  const fill = Math.min(100, D.pctPrimary * 100);
  const committedEnd = Math.min(100, D.committedPctPrimary * 100);
  const paceLeft = Math.min(100, D.prop * 100);
  const money = !seatPrimary;
  return (
    <div className="gb">
      <div className="gb-track">
        <span className="gb-committed" style={{ width: `${committedEnd}%` }} />
        <span className="gb-fill" style={{ width: `${fill}%` }} />
        {!D.isPre && <span className="gb-pace" style={{ left: `${paceLeft}%` }} title="On-pace reference for today (by time in the cart)" />}
        <span className="gb-goalcap" />
      </div>
      <div className="gb-legend">
        <span><i className="d meadow" />Enrolled <b><Num metric={money ? "enrolled.arr" : "enrolled.seats"}>{money ? kMoney(D.enrolledArr) : D.enrolledSeats}</Num></b></span>
        <span><i className="d meadowLt" />Committed <b><Num metric={money ? "committed.arr" : "committed.seats"}>{money ? kMoney(D.committedArr) : D.committedSeats}</Num></b></span>
        {!D.isPre && money && <span><i className="tick" />On-pace <b>{kMoney(D.expectedArr)}</b></span>}
        <span className="right"><i className="d goal" />Goal <b><Num metric="seat_target">{money ? kMoney(cfg.goal_arr) : `${D.seatTarget} members`}</Num></b></span>
      </div>
    </div>
  );
}

function MiniStat({ label, value, sub, tone, metric }) {
  return (
    <div className="ms">
      <div className="ms-l">{label}</div>
      <div className={`ms-v ${tone || ""}`}><Num metric={metric}>{value}</Num></div>
      {sub && <div className="ms-s">{sub}</div>}
    </div>
  );
}

/* ── funnel: active-pipeline bars + enrolled outcome + off-funnel side chips ── */
function Funnel({ cfg, data, D }) {
  const active = data.funnel;
  const max = Math.max(1, ...active.map((s) => s.count));
  return (
    <div className="fn">
      <div className="fn-head">
        <span className="fn-title">Active pipeline</span>
        <span className="fn-sub">live count by stage · {cfg.pipeline_match}</span>
      </div>
      {active.map((s) => (
        <div key={s.key} className="fr">
          <div className="fr-top">
            <span className="fr-label">{s.label}<em className="fr-owner">{s.owner}</em></span>
            <span className="fr-n"><Num metric={`funnel.${s.key}`}>{s.count}</Num></span>
          </div>
          <div className="fr-bar"><span style={{ width: `${(s.count / max) * 100}%` }} /></div>
          {s.tag && <div className={`fr-tag ${s.key === "deciding" ? "teal" : ""}`}>{s.tag}</div>}
        </div>
      ))}

      <div className="fn-out">
        <div className="fn-out-top">
          <span className="fn-out-label"><i className="d meadow" />Enrolled</span>
          <span className="fn-out-n"><Num metric="funnel.enrolled">{D.enrolledSeats}</Num><em>/ {D.seatTarget} seats</em></span>
        </div>
        <div className="fn-out-bar"><span style={{ width: `${Math.min(100, (D.enrolledSeats / D.seatTarget) * 100)}%` }} /></div>
        <div className="fn-out-sub"><b><Num metric="enrolled.arr">{kMoney(D.enrolledArr)}</Num></b> ARR added · {Math.round(D.pctToGoal * 100)}% of goal · {D.seatsRemaining} seats to go</div>
      </div>

      <div className="side">
        <div className="side-chip">
          <span className="side-n"><Num metric="side.no_show">{data.side.no_show}</Num></span>
          <span className="side-l">awaiting rebook</span>
          <span className="side-note">no-show / cancel — a real lever, not a dead end</span>
        </div>
        <div className="side-chip">
          <span className="side-n"><Num metric="side.nurture">{data.side.nurture}</Num></span>
          <span className="side-l">warm reserve</span>
          <span className="side-note">future-cohort nurture — the pool to re-engage</span>
        </div>
      </div>
    </div>
  );
}

/* hand-rolled sparkline (no deps) */
function Spark({ data, up }) {
  const w = 64, h = 20, max = Math.max(1, ...data), min = Math.min(...data);
  const rng = max - min || 1;
  const pts = data.map((v, i) => {
    const x = (i / Math.max(1, data.length - 1)) * (w - 2) + 1;
    const y = h - 2 - ((v - min) / rng) * (h - 4);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  return (
    <svg width={w} height={h} className="spark" aria-hidden="true">
      <polyline points={pts} fill="none" stroke={up ? T.meadow : T.slate} strokeWidth="1.6"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Momentum({ mom }) {
  const rows = [
    { label: "New opt-ins", series: mom.optins, metric: "momentum.optins" },
    { label: "Calls held", series: mom.calls, metric: "momentum.calls" },
    { label: "Closes", series: mom.closes, metric: "momentum.closes" },
  ].filter((r) => (r.series || []).length);
  if (!rows.length) return null;
  return (
    <div className="mom">
      <div className="mom-head">
        <span className="mom-title">Momentum</span>
        <span className="mom-sub">this week vs last · {rows[0].series.length}-week trend{mom.calls_source === "proxy" ? " · calls are a proxy" : ""}</span>
      </div>
      <div className="mom-grid">
        {rows.map((r) => {
          const cur = r.series[r.series.length - 1];
          const prev = r.series.length > 1 ? r.series[r.series.length - 2] : cur;
          const delta = cur - prev;
          const up = delta > 0, flat = delta === 0;
          return (
            <div key={r.label} className="mom-cell">
              <div className="mom-l">{r.label}</div>
              <div className="mom-row">
                <span className="mom-v"><Num metric={r.metric}>{cur}</Num></span>
                <span className={`mom-d ${up ? "up" : flat ? "flat" : "dn"}`}>
                  {up ? "▲" : flat ? "—" : "▼"} {flat ? "" : Math.abs(delta)}
                </span>
              </div>
              <div className="mom-foot">
                <Spark data={r.series} up={up} />
                <span className="mom-prev">was {prev}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CashLine({ cfg, D, cash }) {
  // Base = the priced value of everyone who has PAID (committed = cash received, unsigned;
  // enrolled = signed). Measuring collected against enrolled-only zeroes out the moment a
  // payer sits in Committed awaiting signature.
  const paidArr = (D.enrolledArr || 0) + (D.committedArr || 0);
  const pct = paidArr ? Math.min(100, (cash.collected / paidArr) * 100) : 0;
  return (
    <div className="cash">
      <div className="cash-top">
        <span className="cash-l">Cash collected{cash.source === "estimate" ? " · est." : ""}</span>
        <span className="cash-v"><b><Num metric="cash">{kMoney(cash.collected)}</Num></b> <em>of {kMoney(paidArr)} committed + enrolled</em></span>
      </div>
      <div className="cash-bar"><span className="cash-fill" style={{ width: `${pct}%` }} /></div>
      <div className="cash-note">
        PIF lands in full at enrollment; plan seats add ~{kMoney(cfg.ticket_plan / (cfg.plan_installments || 1))}/mo over {cfg.plan_installments}.
        Balance arrives across the schedule — <em>secondary to ARR</em>.
      </div>
    </div>
  );
}

const fmtN = (n) => Math.round(n || 0).toLocaleString("en-US");

/* ── The Shift — the lead-up webinar layer that feeds memberships. The signature is the
   curved "where you should be" pace line (pace_model="curve") with the actual below it. ── */
function ShiftCurve({ shift, onHover, onPick }) {
  const [hi, setHi] = useState(null);
  const W = 560, H = 158, padL = 6, padR = 10, padT = 16, padB = 20;
  const iw = W - padL - padR, ih = H - padT - padB;
  const goal = shift.goal || 1;
  const curve = (shift.curve || []).slice().sort((a, b) => b.d - a.d); // day 14 → 0 (left → right)
  const dMax = curve.length ? curve[0].d : 14;
  const x = (d) => padL + ((dMax - d) / (dMax || 1)) * iw;
  const y = (c) => padT + (1 - Math.min(1, c / goal)) * ih;
  const path = curve.map((p, i) => `${i ? "L" : "M"}${x(p.d).toFixed(1)},${y(p.count).toFixed(1)}`).join(" ");
  const area = curve.length
    ? `${path} L${x(curve[curve.length - 1].d).toFixed(1)},${(padT + ih).toFixed(1)} L${x(curve[0].d).toFixed(1)},${(padT + ih).toFixed(1)} Z`
    : "";
  const dte = shift.days_to_event == null ? dMax : Math.max(0, Math.min(dMax, shift.days_to_event));
  const xNow = x(dte);
  const behind = shift.state === "behind";
  const actualColor = behind ? T.petalDeep : T.meadow;
  const move = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const vbx = ((e.clientX - rect.left) / rect.width) * W;
    let best = null, bd = 1e9;
    for (const p of curve) { const dd = Math.abs(x(p.d) - vbx); if (dd < bd) { bd = dd; best = p; } }
    setHi(best); onHover && onHover(best);
  };
  const leave = () => { setHi(null); onHover && onHover(null); };
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="shift-svg" preserveAspectRatio="none" role="img"
      aria-label="Shift registration pace curve — hover for the target on any day"
      onMouseMove={move} onMouseLeave={leave} onClick={() => onPick && onPick()}>
      {/* goal cap */}
      <line x1={padL} y1={y(goal)} x2={padL + iw} y2={y(goal)} stroke={T.petal} strokeWidth="1.5"
        strokeDasharray="3 3" opacity="0.8" />
      <text x={padL + iw} y={y(goal) - 4} textAnchor="end" className="shift-svg-lbl">{fmtN(goal)} goal</text>
      {/* expected curve + soft fill */}
      {area && <path d={area} fill={T.meadow} opacity="0.07" />}
      <path d={path} fill="none" stroke={T.meadow} strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round" />
      {/* hovered day — the "where should we be" point */}
      {hi && <>
        <line x1={x(hi.d)} y1={padT} x2={x(hi.d)} y2={padT + ih} stroke={T.teal} strokeWidth="1" opacity="0.55" />
        <circle cx={x(hi.d)} cy={y(hi.count)} r="4" fill={T.teal} />
      </>}
      {/* today marker + expected vs actual */}
      <line x1={xNow} y1={padT - 4} x2={xNow} y2={padT + ih} stroke={T.line} strokeWidth="1" />
      <line x1={xNow} y1={y(shift.expected)} x2={xNow} y2={y(shift.registrants)} stroke={actualColor}
        strokeWidth="1.5" strokeDasharray="2 2" />
      <circle cx={xNow} cy={y(shift.expected)} r="4" fill={T.white} stroke={T.meadow} strokeWidth="2" />
      <circle cx={xNow} cy={y(shift.registrants)} r="4.5" fill={actualColor} />
      <text x={Math.min(xNow + 7, padL + iw)} y={y(shift.registrants) + 4} className="shift-svg-now"
        fill={actualColor} textAnchor={xNow > padL + iw * 0.7 ? "end" : "start"}>
        {fmtN(shift.registrants)}
      </text>
      {/* x-axis ends */}
      <text x={padL} y={H - 5} className="shift-svg-lbl">{dMax}d out</text>
      <text x={padL + iw} y={H - 5} textAnchor="end" className="shift-svg-lbl">event</text>
    </svg>
  );
}

function ShiftStat({ label, value, tone, metric }) {
  return (
    <div className="sh-stat">
      <div className="sh-stat-l">{label}</div>
      <div className={`sh-stat-v ${tone || ""}`}><Num metric={metric}>{value}</Num></div>
    </div>
  );
}

const CHANNEL_COLOR = {
  meta: T.teal, google: T.petalDeep, tiktok: T.poppy, paid_other: T.petal,
  email: T.mist, comped: T.daffodil, organic: T.meadow,
};

/* "Where they came from" — the acquisition-channel split of Shift registrants (organic vs
   paid), from GHL contact UTM. Each channel drills to its registrant list. */
function ShiftSources({ sources }) {
  if (!sources || !sources.total) return null;
  const { total, paid, organic, comped, channels } = sources;
  const pct = (n) => Math.round((n / total) * 100);
  return (
    <div className="shift-src">
      <div className="shift-src-head">
        <span className="shift-src-title">Where they came from</span>
        <span className="shift-src-sub">
          {pct(paid)}% paid · {pct(organic)}% organic{comped ? ` · ${pct(comped)}% comped` : ""}
        </span>
      </div>
      <div className="shift-src-bar">
        {channels.filter((c) => c.count > 0).map((ch) => (
          <span key={ch.key} style={{ width: `${ch.pct}%`, background: CHANNEL_COLOR[ch.key] || T.muted }}
            title={`${ch.label}: ${fmtN(ch.count)} (${ch.pct}%)`} />
        ))}
      </div>
      <div className="shift-src-legend">
        {channels.filter((c) => c.count > 0).map((ch) => (
          <Num key={ch.key} metric={`shift.source.${ch.key}`} title={`${ch.label} registrants`}>
            <span className="shift-src-item"><i style={{ background: CHANNEL_COLOR[ch.key] || T.muted }} />
              {ch.label} <b>{fmtN(ch.count)}</b> <em>{ch.pct}%</em></span>
          </Num>
        ))}
      </div>
    </div>
  );
}

function TheShift({ shift }) {
  const open = useContext(DrillCtx);
  const [hov, setHov] = useState(null);
  const pill = {
    behind: { txt: "Behind the curve", tone: "behind" },
    onpace: { txt: "On the curve", tone: "good" },
    ahead: { txt: "Ahead of the curve", tone: "good" },
    pending: { txt: "Not started", tone: "" },
    done: { txt: "Event passed", tone: "" },
  }[shift.state] || { txt: shift.state, tone: "" };
  const gapTone = shift.gap < 0 ? "behind" : "good";
  const gapTxt = `${shift.gap < 0 ? "−" : "+"}${fmtN(Math.abs(shift.gap))}`;
  return (
    <div className="shift">
      <div className="shift-head">
        <span className="shift-bar" />
        <span className="shift-title">{shift.name}<em>lead-up webinar · feeds memberships</em></span>
        <span className="shift-spacer" />
        <span className={`pill ${pill.tone}`}><span className="pdot" />{pill.txt}</span>
      </div>
      <div className="shift-body">
        <div className="shift-left">
          <div className="shift-num"><Num metric="shift.registrants">{fmtN(shift.registrants)}</Num><span className="of">/ {fmtN(shift.goal)} registered</span></div>
          <div className="shift-sub">
            <Num metric="shift.pct">{Math.round(shift.pct_to_goal * 100)}% to goal</Num>
            {shift.days_to_event != null && ` · ${shift.days_to_event}d to the Shift`}
            {shift.source === "manual" && " · manual count"}
          </div>
          <div className="shift-stats">
            <ShiftStat label="On-curve today" metric="shift.expected" value={`${fmtN(shift.expected)} · ${Math.round(shift.expected_pct * 100)}%`} />
            <ShiftStat label="Gap to pace" metric="shift.gap" value={gapTxt} tone={gapTone} />
            <ShiftStat label="Projects to" metric="shift.projected" value={`${shift.projected_members} / ${shift.members_at_goal}`} tone={shift.projected_members < shift.members_at_goal ? "behind" : "good"} />
          </div>
        </div>
        <div className="shift-chart">
          <div className={`shift-cap ${hov ? "on" : ""}`}>
            {hov
              ? <><b>{hov.d} day{hov.d === 1 ? "" : "s"} out</b> → should be at <b>{fmtN(hov.count)}</b> of {fmtN(shift.goal)} <span className="shift-cap-pct">({Math.round(hov.pct * 100)}%)</span></>
              : "Hover the curve to see where we should be on any day →"}
          </div>
          <ShiftCurve shift={shift} onHover={setHov} onPick={() => open && open("shift.expected")} />
        </div>
      </div>
      {shift.sources && <ShiftSources sources={shift.sources} />}
      <div className="shift-foot">
        {fmtN(shift.goal)} registrants → {shift.members_at_goal} members · ~{Math.round(shift.reg_to_member * 100)}% historical conversion · pacing vs your last Shift
      </div>
    </div>
  );
}

/* ── settings drawer: the configurable surface (owner/admin) ── */
function Field({ label, children, hint }) {
  return (
    <label className="fld">
      <span className="fld-l">{label}</span>
      {children}
      {hint && <span className="fld-h">{hint}</span>}
    </label>
  );
}

/* The editable stage-grouping rows: [stage_map key, label, owner, hint]. Matching is
   case-insensitive substring against the GHL stage name; comma-separate multiple. */
const STAGE_GROUPS = [
  ["leads", "Leads", "marketing", "opted in, no call yet"],
  ["booked", "Booked", "setters", "call scheduled"],
  ["booked_app", "— app in", "sub-signal", "still Booked; counts toward “apps in”"],
  ["deciding", "Deciding", "closers", "call held or payment link out — on the table"],
  ["committed", "Committed", "payment ops", "cash received, agreement not signed"],
  ["enrolled", "Enrolled", "won", "signed + onboarded — the only “won”"],
  ["noshow", "No-show / cancel", "outside funnel", "feeds awaiting-rebook"],
  ["nurture", "Warm reserve", "outside funnel", "future-cohort nurture"],
  ["lost", "Lost", "outside funnel", "DQ / abandon"],
  ["likely_yes", "— Likely Yes", "desk column", "deciding sub-signal for the rep leaderboard"],
  ["likely_no", "— Likely No", "desk column", "deciding sub-signal for the rep leaderboard"],
  ["link_sent", "— Link Sent", "desk column", "deciding sub-signal for the rep leaderboard"],
];

function SettingsDrawer({ cfg, launchId, businessKey, canPersist, onClose, onSaved }) {
  // Local editable copy; mix stored as a fraction on the server, edited as a percent here.
  const [f, setF] = useState({
    name: cfg.name, window_start: cfg.window_start, window_end: cfg.window_end,
    event_end: cfg.event_end || "", goal_arr: cfg.goal_arr, ticket_pif: cfg.ticket_pif,
    ticket_plan: cfg.ticket_plan, plan_installments: cfg.plan_installments,
    mix_pct: Math.round((cfg.mix_pif || 0) * 100), pipeline_match: cfg.pipeline_match,
    cohort_value: cfg.cohort_value || "",
    goal_basis: cfg.goal_basis || "arr", seat_goal: cfg.seat_goal || 100,
    shift_name: cfg.shift_name || "The Shift", shift_event_date: cfg.shift_event_date || "",
    shift_goal: cfg.shift_goal || 0, shift_reg_tag: cfg.shift_reg_tag || "",
    shift_actual: cfg.shift_actual ?? "",
  });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState(null);
  const set = (patch) => setF((c) => ({ ...c, ...patch }));
  // Stage grouping — edited as comma-separated substrings, stored as arrays on stage_map.
  const [sm, setSm] = useState(() => Object.fromEntries(
    STAGE_GROUPS.map(([k]) => [k, ((cfg.stage_map || {})[k] || []).join(", ")])));
  const setSmKey = (k, v) => setSm((c) => ({ ...c, [k]: v }));

  const mixPif = f.mix_pct / 100;
  const blended = mixPif * f.ticket_pif + (1 - mixPif) * f.ticket_plan;
  const seatTarget = f.goal_basis === "seats"
    ? (+f.seat_goal || 0)
    : (blended > 0 ? Math.max(1, Math.ceil(f.goal_arr / blended)) : 0);
  const curvePts = Object.keys(cfg.shift_pace_curve || {}).length;

  async function save() {
    setSaving(true); setErr(null);
    try {
      await putJSON(`/businesses/${businessKey}/launches/${launchId}`, {
        name: f.name, window_start: f.window_start, window_end: f.window_end,
        event_end: f.event_end || null, goal_arr: f.goal_arr, ticket_pif: f.ticket_pif,
        ticket_plan: f.ticket_plan, plan_installments: f.plan_installments,
        mix_pif: mixPif, pipeline_match: f.pipeline_match, cohort_value: f.cohort_value || null,
        goal_basis: f.goal_basis, seat_goal: +f.seat_goal || null,
        shift_name: f.shift_name || null, shift_event_date: f.shift_event_date || null,
        shift_goal: +f.shift_goal || null, shift_reg_tag: f.shift_reg_tag || null,
        shift_actual: f.shift_actual === "" ? null : +f.shift_actual,
        stage_map: { ...(cfg.stage_map || {}), ...Object.fromEntries(STAGE_GROUPS.map(([k]) =>
          [k, sm[k].split(",").map((x) => x.trim().toLowerCase()).filter(Boolean)])) },
      });
      onSaved && onSaved();
      onClose();
    } catch (e) {
      setErr(e.detail || e.message || "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="drawer">
      <div className="dr-head">
        <span className="dr-title">Launch settings</span>
        <button className="dr-x" onClick={onClose} aria-label="Close settings">✕</button>
      </div>
      <div className="dr-body">
        <div className="dr-sec">Cohort</div>
        <Field label="Launch name">
          <input className="in" value={f.name} onChange={(e) => set({ name: e.target.value })} />
        </Field>
        <div className="grid2">
          <Field label="Cart opens" hint="= launch event start">
            <input className="in" type="date" value={f.window_start} onChange={(e) => set({ window_start: e.target.value })} />
          </Field>
          <Field label="Cart closes" hint="event end + open days">
            <input className="in" type="date" value={f.window_end} onChange={(e) => set({ window_end: e.target.value })} />
          </Field>
        </div>
        <Field label="Launch event ends" hint="the live event window">
          <input className="in" type="date" value={f.event_end} onChange={(e) => set({ event_end: e.target.value })} />
        </Field>

        <div className="dr-sec">Goal &amp; pricing</div>
        <Field label="Goal basis" hint="seat-primary targets a member count; ARR-primary targets a dollar figure">
          <div className="seg">
            <button type="button" className={f.goal_basis === "seats" ? "on" : ""}
              onClick={() => set({ goal_basis: "seats" })}>Members</button>
            <button type="button" className={f.goal_basis === "arr" ? "on" : ""}
              onClick={() => set({ goal_basis: "arr" })}>ARR ($)</button>
          </div>
        </Field>
        {f.goal_basis === "seats" && (
          <Field label="Member goal" hint={`${kMoney(blended)} blended → ~${kMoney((+f.seat_goal || 0) * blended)} ARR at goal`}>
            <input className="in" type="number" min="1" step="1" value={f.seat_goal}
              onChange={(e) => set({ seat_goal: Math.max(1, +e.target.value) })} />
          </Field>
        )}
        <Field label="ARR goal" hint={f.goal_basis === "seats"
          ? "the dollar headline (secondary to the member goal)"
          : `${seatTarget} seats at ${kMoney(blended)} blended`}>
          <div className="in-money"><span>$</span>
            <input className="in" type="number" step="10000" value={f.goal_arr}
              onChange={(e) => set({ goal_arr: Math.max(0, +e.target.value) })} />
          </div>
        </Field>
        <div className="grid2">
          <Field label="PIF price">
            <div className="in-money"><span>$</span>
              <input className="in" type="number" step="500" value={f.ticket_pif}
                onChange={(e) => set({ ticket_pif: Math.max(0, +e.target.value) })} />
            </div>
          </Field>
          <Field label="Plan total">
            <div className="in-money"><span>$</span>
              <input className="in" type="number" step="500" value={f.ticket_plan}
                onChange={(e) => set({ ticket_plan: Math.max(0, +e.target.value) })} />
            </div>
          </Field>
        </div>
        <Field label={`Assumed mix — ${f.mix_pct}% PIF / ${100 - f.mix_pct}% plan`}
          hint="assumption for the seat target; actuals come from real counts">
          <input className="range" type="range" min="0" max="100" step="5" value={f.mix_pct}
            onChange={(e) => set({ mix_pct: +e.target.value })} />
        </Field>
        <Field label="Plan installments" hint="Number of Payments — sets the collected-cash curve">
          <input className="in" type="number" min="1" step="1" value={f.plan_installments}
            onChange={(e) => set({ plan_installments: Math.max(1, +e.target.value) })} />
        </Field>

        <div className="dr-sec">The Shift <em className="ro">lead-up webinar</em></div>
        <Field label="Webinar name">
          <input className="in" value={f.shift_name} onChange={(e) => set({ shift_name: e.target.value })} />
        </Field>
        <div className="grid2">
          <Field label="Shift event date" hint="the '0 days to event' anchor">
            <input className="in" type="date" value={f.shift_event_date}
              onChange={(e) => set({ shift_event_date: e.target.value })} />
          </Field>
          <Field label="Registrant goal">
            <input className="in" type="number" step="50" value={f.shift_goal}
              onChange={(e) => set({ shift_goal: Math.max(0, +e.target.value) })} />
          </Field>
        </div>
        <div className="grid2">
          <Field label="GHL registration tag" hint="contacts with this tag = registrants (live)">
            <input className="in" value={f.shift_reg_tag}
              onChange={(e) => set({ shift_reg_tag: e.target.value })} />
          </Field>
          <Field label="Manual count" hint="fallback / seed until the tag syncs">
            <input className="in" type="number" step="1" placeholder="auto" value={f.shift_actual}
              onChange={(e) => set({ shift_actual: e.target.value })} />
          </Field>
        </div>
        <div className="map-note">
          Pace curve: {curvePts}-point empirical (days-to-event → % of goal) from your last Shift — the
          curved reference line. Editable via API.
        </div>

        <div className="dr-sec">Data source</div>
        <Field label="Pipeline match" hint="which GHL pipeline this launch reads (name or id)">
          <input className="in" value={f.pipeline_match} onChange={(e) => set({ pipeline_match: e.target.value })} />
        </Field>
        <Field label="Cohort field value" hint="stamped on every opp entering Won in the window">
          <input className="in" value={f.cohort_value} onChange={(e) => set({ cohort_value: e.target.value })} />
        </Field>

        <div className="dr-sec">Stage grouping <em className="ro">substring match, case-insensitive · comma-separate</em></div>
        <div className="map">
          {STAGE_GROUPS.map(([k, label, owner, hint]) => (
            <div key={k} className="map-row">
              <span className="map-g" title={hint}>{label}<em>{owner}</em></span>
              <input className="in map-in" value={sm[k]} placeholder="no stages map here"
                onChange={(e) => setSmKey(k, e.target.value)} />
            </div>
          ))}
        </div>
        <div className="map-note">A GHL stage joins a group when its name contains any listed phrase.
          Committed = cash received (unsigned) · Enrolled = signed + onboarded. No-show, nurture, and
          lost sit outside the funnel by design.</div>

        {err && <div className="dr-err">{err}</div>}
        <div className="dr-actions">
          <button className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn primary" onClick={save} disabled={saving || !canPersist}
            title={canPersist ? "" : "Connect the pipeline to edit"}>
            {saving ? "Saving…" : canPersist ? "Save changes" : "Read-only"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── drill drawer: shows what's behind a clicked number (records or calc) ──
   Exported: the Sales Desk drawer renders the same two payload shapes. */
/* ── Recording playback ────────────────────────────────────────────────────────────────────
   Recall hands back a signed S3 URL to the raw mp4 and offers no player of its own. Opening
   that URL in a tab is at the browser's mercy - Content-Disposition made it DOWNLOAD rather
   than play. Used as the src of a <video> it streams instead, because S3 serves range
   requests, so the call plays in place and nothing lands in anyone's Downloads folder.

   The URL expires after 5 hours, so it is fetched when the player opens - never stored - and
   refetched once if playback fails, which is what an expiry looks like mid-session.

   Styles are inline on purpose: the .drx-* drawer CSS is scoped per-file and this component
   is rendered from both the Launch and Sales Desk tabs. */
function RecordingPlayer({ businessKey, callId, title, onClose }) {
  const [url, setUrl] = useState(null);
  const [err, setErr] = useState(null);
  const [retried, setRetried] = useState(false);

  const load = async () => {
    setErr(null);
    try {
      const r = await getJSON(`/businesses/${businessKey}/launches/active/sales-desk/recording/${callId}`);
      setUrl(r.url);
    } catch (e) {
      setErr(e.detail || e.message || "No recording available");
    }
  };
  useEffect(() => { load(); }, [callId]);

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // A signed URL that expired while the drawer sat open fails as a media error, not an HTTP
  // one - so retry once with a fresh link before telling anyone it is broken.
  const onVideoError = () => {
    if (retried) { setErr("This recording could not be played."); return; }
    setRetried(true); setUrl(null); load();
  };

  return (
    <div onClick={onClose} style={{
      position: "fixed", inset: 0, zIndex: 90, background: alpha(T.evergreen, 0.55),
      display: "flex", alignItems: "center", justifyContent: "center", padding: 20,
    }}>
      <div onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" style={{
        width: "min(920px, 96vw)", background: T.white, borderRadius: 16, overflow: "hidden",
        boxShadow: `0 24px 60px ${alpha(T.evergreen, 0.3)}`,
      }}>
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          gap: 12, padding: "13px 16px", borderBottom: `1px solid ${T.line}`,
        }}>
          <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 14, fontWeight: 600, color: T.ink }}>
            {title || "Call recording"}
          </span>
          <button onClick={onClose} aria-label="Close" style={{
            border: "none", background: "none", cursor: "pointer", fontSize: 15, color: T.muted,
          }}>✕</button>
        </div>

        <div style={{ background: "#000", minHeight: 240, display: "flex", alignItems: "center", justifyContent: "center" }}>
          {url && (
            <video src={url} controls autoPlay preload="metadata" onError={onVideoError}
                   style={{ width: "100%", maxHeight: "68vh", display: "block" }} />
          )}
          {!url && !err && <span style={{ color: T.onDark, fontSize: 12.5, padding: 40 }}>Loading recording…</span>}
          {err && <span style={{ color: T.onDark, fontSize: 12.5, padding: 40, textAlign: "center" }}>{err}</span>}
        </div>

        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12,
          padding: "10px 16px", fontSize: 11, color: T.muted,
        }}>
          <span>Streamed from Recall — the link expires after a few hours and is re-issued each time.</span>
          {url && <a href={url} download style={{ color: T.teal, fontWeight: 600, whiteSpace: "nowrap" }}>Download</a>}
        </div>
      </div>
    </div>
  );
}

/* The link that opens it. The media URL is never stored - it is minted per click. */
export function WatchLink({ businessKey, callId, label = "Watch ↗", title }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <a href="#" onClick={(e) => { e.preventDefault(); setOpen(true); }}>{label}</a>
      {open && <RecordingPlayer businessKey={businessKey} callId={callId} title={title}
                                onClose={() => setOpen(false)} />}
    </>
  );
}

export function DrillRecords({ d, businessKey = "springb" }) {
  const cols = d.columns || [];
  return (
    <>
      <div className="drx-sub">{d.subtitle}</div>
      {d.rows.length === 0 && <div className="drx-empty">No records.</div>}
      {d.rows.length > 0 && (
        <div className="drx-tblwrap">
          <table className="drx-tbl">
            <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
            <tbody>
              {d.rows.map((r, i) => (
                <tr key={i}>
                  {cols.map((c) => (
                    <td key={c}>
                      {c === "url"
                        ? (r.url ? <a href={r.url} target="_blank" rel="noreferrer">GHL ↗</a> : "—")
                        /* "rec:<call id>" — the link is minted on click, never stored. Recall
                           signs its media URLs and they expire after 5 hours, so anything
                           baked into this payload would be dead by tomorrow. */
                        : (typeof r[c] === "string" && r[c].startsWith("rec:"))
                          ? <WatchLink businessKey={businessKey} callId={r[c].slice(4)}
                                        title={r.contact ? `${r.contact} — call recording` : undefined} />
                          : (typeof r[c] === "string" && /^https?:\/\//.test(r[c]))
                            ? <a href={r[c]} target="_blank" rel="noreferrer">Watch ↗</a>
                            : (r[c] === true ? "✓" : r[c] === false || r[c] == null || r[c] === "" ? "—" : r[c])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

export function DrillCalc({ d }) {
  return (
    <>
      <div className="drx-val">{d.value}</div>
      <div className="drx-sub">{d.title}</div>
      {(d.steps || []).length > 0 && (
        <div className="drx-steps">
          {d.steps.map((s, i) => (
            <div key={i} className="drx-step"><span>{s.label}</span><b>{String(s.value ?? "—")}</b></div>
          ))}
        </div>
      )}
      {d.formula && <div className="drx-formula">{d.formula}</div>}
      {d.note && <div className="drx-note2">{d.note}</div>}
      {d.table && d.table.length > 0 && (
        <div className="drx-tblwrap">
          <div className="drx-sub" style={{ marginTop: 4 }}>Where we should be, day by day</div>
          <table className="drx-tbl">
            <thead><tr><th>days out</th><th>% of goal</th><th>should be</th></tr></thead>
            <tbody>
              {d.table.map((t) => (
                <tr key={t.day} className={t.today ? "on" : ""}>
                  <td>{t.day}{t.today ? " · today" : ""}</td><td>{Math.round(t.pct * 100)}%</td><td>{fmtN(t.expected)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function LaunchDrawer({ metric, businessKey, usingSample, onClose }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let alive = true;
    if (usingSample) { setErr("sample"); return; }
    setD(null); setErr(null);
    getJSON(`/businesses/${businessKey}/launches/active/drill/${encodeURIComponent(metric)}`)
      .then((r) => alive && setD(r))
      .catch((e) => alive && setErr(e.detail || e.message || "Failed to load"));
    return () => { alive = false; };
  }, [metric, businessKey, usingSample]);
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

export default function LaunchSection({ data, usingSample, role, businessKey = "springb", onSaved }) {
  const [showSettings, setShowSettings] = useState(false);
  const [drill, setDrill] = useState(null);   // metric currently drilled into
  const cfg = data.launch;
  const isEditor = !role || role === "owner" || role === "admin";
  const canPersist = isEditor && !usingSample;

  const seatPrimary = data.goal_basis === "seats";
  const D = useMemo(() => {
    const enrolledArr = data.enrolled.arr, committedArr = data.committed.arr;
    const goal = cfg.goal_arr || 1;
    const seatTarget = data.seat_target || 1;
    const enrolledSeats = data.enrolled.seats, committedSeats = data.committed.seats;
    // Progress bar / hero track: seats when seat-primary, ARR otherwise.
    const pctPrimary = seatPrimary ? enrolledSeats / seatTarget : enrolledArr / goal;
    const committedPctPrimary = seatPrimary
      ? (enrolledSeats + committedSeats) / seatTarget
      : (enrolledArr + committedArr) / goal;
    return {
      enrolledArr, committedArr, enrolledSeats, committedSeats, seatTarget,
      seatsRemaining: data.seats_remaining, pctToGoal: data.pct_to_goal,
      pctPrimary, committedPctPrimary, committedPct: (enrolledArr + committedArr) / goal,
      prop: data.window_days ? data.days_elapsed / data.window_days : 0,
      isPre: data.status === "pre", expectedArr: data.pace.expected_arr,
      paceGapArr: data.pace.gap_arr,
    };
  }, [data, cfg, seatPrimary]);

  let pill;
  if (data.status === "pre") pill = { txt: `Opens ${fmtDate(cfg.window_start)} · ${data.days_to_open}d out`, tone: "pre" };
  else if (data.days_remaining > 0) pill = { txt: `In launch window · ${data.days_remaining} days left`, tone: "open" };
  else pill = { txt: "Cart closed", tone: "closed" };

  const paceStat = {
    onpace: { value: "On pace", sub: `within ${kMoney(Math.abs(D.paceGapArr))} of the line`, tone: "good" },
    behind: { value: `−${kMoney(D.paceGapArr)}`, sub: "behind the line", tone: "behind" },
    ahead: { value: `+${kMoney(D.paceGapArr)}`, sub: "ahead of the line", tone: "good" },
    pending: { value: "—", sub: "cart not yet open", tone: "" },
  }[data.pace.state] || { value: "—", sub: "", tone: "" };

  return (
    <DrillCtx.Provider value={setDrill}>
    <div className="bcl">
      <style>{`
        .bcl { font-family:Inter,sans-serif; color:${T.ink}; }
        .bcl * { box-sizing:border-box; }
        .bcl .mod { max-width:900px; }

        .bcl .ph { display:flex; align-items:center; gap:9px; font-size:11.5px; color:${T.amber};
          background:${T.amberBg}; border:1px solid ${alpha(T.amber, .3)}; border-radius:9px; padding:8px 13px; margin-bottom:16px; }
        .bcl .ph b { font-weight:700; } .bcl .ph em { font-style:normal; color:${T.slate}; }
        .bcl .ph-dot { width:6px; height:6px; border-radius:99px; background:${T.amber}; flex:none; }

        .bcl .ctx { display:flex; align-items:center; gap:11px; margin-bottom:16px; flex-wrap:wrap; }
        .bcl .ctx-bar { width:4px; height:20px; border-radius:2px; background:${T.petal}; }
        .bcl .ctx-h { font-family:Poppins,sans-serif; font-size:17px; font-weight:600; letter-spacing:-.01em; }
        .bcl .ctx-s { font-size:12px; color:${T.muted}; }
        .bcl .ctx-spacer { flex:1; }
        .bcl .pill { display:inline-flex; align-items:center; gap:7px; font-size:11px; font-weight:600;
          border-radius:99px; padding:5px 12px; border:1px solid ${T.line};
          color:${T.slate}; background:${T.parchment}; }
        .bcl .pill .pdot { width:7px; height:7px; border-radius:99px; background:${T.muted}; }
        .bcl .pill.open, .bcl .pill.good { color:${T.meadow}; background:${T.meadowBg}; border-color:${alpha(T.meadow, .25)}; }
        .bcl .pill.open .pdot, .bcl .pill.good .pdot { background:${T.meadow}; }
        .bcl .pill.behind { color:${T.petalDeep}; background:${alpha(T.petal, .18)}; border-color:${alpha(T.petalDeep, .3)}; }
        .bcl .pill.behind .pdot { background:${T.petalDeep}; }
        .bcl .pill.pre { color:${T.petalDeep}; background:${alpha(T.petal, .18)}; border-color:${alpha(T.petalDeep, .3)}; }
        .bcl .pill.pre .pdot { background:${T.petalDeep}; }
        .bcl .pill.closed { color:${T.slate}; background:${T.parchment}; border-color:${T.line}; }
        .bcl .pill.closed .pdot { background:${T.muted}; }
        .bcl .gear { border:1px solid ${T.line}; background:${T.white}; border-radius:9px; padding:6px 12px;
          font-size:11.5px; font-weight:600; color:${T.secondary}; cursor:pointer; font-family:inherit;
          display:inline-flex; align-items:center; gap:6px; transition:border-color .15s ease, background .15s ease; }
        .bcl .gear:hover { border-color:${T.petal}; background:${T.parchment}; }
        .bcl .gear.on { border-color:${T.petalDeep}; color:${T.petalDeep}; }

        /* The Shift card (top-of-funnel layer) */
        .bcl .shift { background:${T.white}; border:1px solid ${T.line}; border-radius:16px; padding:18px 20px 15px;
          box-shadow:0 10px 26px ${alpha(T.evergreen, .06)}; margin-bottom:14px; }
        .bcl .shift-head { display:flex; align-items:center; gap:10px; margin-bottom:15px; flex-wrap:wrap; }
        .bcl .shift-bar { width:4px; height:18px; border-radius:2px; background:${T.teal}; }
        .bcl .shift-title { font-family:Poppins,sans-serif; font-size:14px; font-weight:600; color:${T.ink}; }
        .bcl .shift-title em { font-style:normal; font-size:11px; font-weight:500; color:${T.muted}; margin-left:9px; }
        .bcl .shift-spacer { flex:1; }
        .bcl .shift-body { display:grid; grid-template-columns:minmax(190px,1fr) 1.35fr; gap:22px; align-items:center; }
        .bcl .shift-num { font-family:Poppins,sans-serif; font-size:34px; font-weight:700; color:${T.ink};
          letter-spacing:-.02em; font-variant-numeric:tabular-nums; line-height:1; }
        .bcl .shift-num .of { font-size:13px; font-weight:500; color:${T.muted}; margin-left:8px; letter-spacing:0; }
        .bcl .shift-sub { font-size:12px; color:${T.slate}; margin-top:7px; }
        .bcl .shift-stats { display:flex; gap:20px; margin-top:15px; flex-wrap:wrap; }
        .bcl .sh-stat-l { font-size:10px; font-weight:600; letter-spacing:.06em; text-transform:uppercase; color:${T.muted}; }
        .bcl .sh-stat-v { font-family:Poppins,sans-serif; font-size:15px; font-weight:700; color:${T.ink}; margin-top:3px; font-variant-numeric:tabular-nums; }
        .bcl .sh-stat-v.behind { color:${T.petalDeep}; }
        .bcl .sh-stat-v.good { color:${T.meadow}; }
        .bcl .shift-chart { min-width:0; }
        .bcl .shift-svg { width:100%; height:auto; display:block; overflow:visible; }
        .bcl .shift-svg-lbl { font-family:Inter,sans-serif; font-size:9px; fill:${T.muted}; }
        .bcl .shift-svg-now { font-family:Poppins,sans-serif; font-size:11px; font-weight:700; }
        .bcl .shift-foot { font-size:11px; color:${T.muted}; margin-top:15px; padding-top:12px; border-top:1px solid ${T.line}; }
        .bcl .shift-src { margin-top:16px; padding-top:14px; border-top:1px solid ${T.line}; }
        .bcl .shift-src-head { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:9px; flex-wrap:wrap; gap:4px; }
        .bcl .shift-src-title { font-size:11px; font-weight:700; letter-spacing:.08em; text-transform:uppercase; color:${T.slate}; }
        .bcl .shift-src-sub { font-size:11px; color:${T.muted}; }
        .bcl .shift-src-bar { display:flex; height:9px; border-radius:5px; overflow:hidden; background:${T.parchment}; }
        .bcl .shift-src-bar span { display:block; height:100%; transition:width .4s ease; }
        .bcl .shift-src-legend { display:flex; flex-wrap:wrap; gap:8px 16px; margin-top:11px; }
        .bcl .shift-src-item { display:inline-flex; align-items:center; gap:6px; font-size:11.5px; color:${T.secondary}; }
        .bcl .shift-src-item i { width:9px; height:9px; border-radius:3px; flex:none; }
        .bcl .shift-src-item b { font-family:Poppins,sans-serif; font-weight:600; color:${T.ink}; font-variant-numeric:tabular-nums; }
        .bcl .shift-src-item em { font-style:normal; color:${T.muted}; }
        .bcl .shift-cap { font-size:11.5px; color:${T.muted}; margin-bottom:7px; min-height:16px; }
        .bcl .shift-cap.on { color:${T.teal}; }
        .bcl .shift-cap b { color:${T.ink}; font-weight:600; }
        .bcl .shift-cap.on b { color:${T.teal}; }
        .bcl .shift-cap-pct { color:${T.muted}; }
        .bcl .shift-svg { cursor:pointer; }

        /* drill affordance — every number is clickable */
        .bcl .num { cursor:pointer; border-radius:3px; box-shadow:inset 0 -1px 0 ${alpha(T.muted, 0)};
          transition:box-shadow .12s ease; }
        .bcl .num:hover { box-shadow:inset 0 -1.5px 0 currentColor; }
        .bcl .num:focus-visible { outline:2px solid ${T.petal}; outline-offset:2px; }

        /* drill drawer */
        .bcl .drx-scrim { position:fixed; inset:0; background:${alpha(T.evergreen, .32)}; z-index:60;
          display:flex; justify-content:flex-end; }
        .bcl .drx { width:min(440px,92vw); height:100%; background:${T.white}; display:flex; flex-direction:column;
          box-shadow:-12px 0 40px ${alpha(T.evergreen, .2)}; animation:drx-in .18s ease; }
        @keyframes drx-in { from{ transform:translateX(20px); opacity:.6; } to{ transform:none; opacity:1; } }
        .bcl .drx-head { display:flex; justify-content:space-between; align-items:center; padding:16px 20px;
          border-bottom:1px solid ${T.line}; background:${T.parchment}; }
        .bcl .drx-title { font-family:Poppins,sans-serif; font-size:14px; font-weight:700; color:${T.ink}; }
        .bcl .drx-x { border:none; background:none; font-size:15px; color:${T.slate}; cursor:pointer; }
        .bcl .drx-body { padding:18px 20px; overflow-y:auto; }
        .bcl .drx-val { font-family:Poppins,sans-serif; font-size:30px; font-weight:700; color:${T.ink}; letter-spacing:-.02em; font-variant-numeric:tabular-nums; }
        .bcl .drx-sub { font-size:12px; color:${T.muted}; margin:2px 0 12px; }
        .bcl .drx-steps { border:1px solid ${T.line}; border-radius:10px; overflow:hidden; margin-bottom:12px; }
        .bcl .drx-step { display:flex; justify-content:space-between; gap:12px; padding:9px 13px; font-size:12.5px; border-bottom:1px solid ${T.parchment}; }
        .bcl .drx-step:last-child { border-bottom:none; }
        .bcl .drx-step span { color:${T.slate}; } .bcl .drx-step b { color:${T.ink}; font-family:Poppins,sans-serif; font-weight:600; text-align:right; }
        .bcl .drx-formula { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11.5px; color:${T.teal}; background:${T.mist}; border-radius:8px; padding:8px 11px; margin-bottom:10px; }
        .bcl .drx-note, .bcl .drx-note2 { font-size:11.5px; color:${T.muted}; line-height:1.5; }
        .bcl .drx-empty { font-size:12.5px; color:${T.muted}; padding:12px 0; }
        .bcl .drx-tblwrap { overflow-x:auto; }
        .bcl .drx-tbl { width:100%; border-collapse:collapse; font-size:12px; margin-top:6px; }
        .bcl .drx-tbl th { text-align:left; font-size:10px; font-weight:700; letter-spacing:.05em; text-transform:uppercase; color:${T.muted}; padding:6px 8px; border-bottom:1px solid ${T.line}; }
        .bcl .drx-tbl td { padding:7px 8px; border-bottom:1px solid ${T.parchment}; color:${T.secondary}; }
        .bcl .drx-tbl tr.on td { background:${T.meadowBg}; color:${T.ink}; font-weight:600; }
        .bcl .drx-tbl a { color:${T.teal}; text-decoration:none; font-weight:600; }

        /* Hero — official petal ribbed gradient (beCollective's brand colorway), light surface. */
        .bcl .hero { position:relative; overflow:hidden; border-radius:18px 18px 0 0; padding:26px 28px 24px;
          background-color:${T.petal};
          background-image:url(/brand/RibbedGradient_Petal.jpg);
          background-size:cover; background-position:center; background-blend-mode:multiply;
          box-shadow:0 2px 6px ${alpha(T.evergreen, .10)}, 0 16px 38px ${alpha(T.evergreen, .12)}; }
        .bcl .eyebrow { font-family:Poppins,sans-serif; font-size:11px; font-weight:700; letter-spacing:.13em;
          text-transform:uppercase; display:inline-flex; align-items:center; gap:8px; color:${T.evergreen}; }
        .bcl .eyebrow .edot { width:8px; height:8px; border-radius:99px; background:${T.poppy}; }
        .bcl .hnum { font-family:Poppins,sans-serif; font-weight:700; letter-spacing:-.025em; color:${T.evergreen};
          line-height:1; margin:12px 0 6px; font-variant-numeric:tabular-nums; font-size:44px; display:flex; align-items:baseline; gap:12px; }
        .bcl .hnum .of { font-size:15px; font-weight:500; color:${T.slate}; letter-spacing:0; }
        .bcl .hdesc { font-size:12.5px; color:${T.slate}; }

        .bcl .gb { margin-top:20px; }
        .bcl .gb-track { position:relative; height:11px; border-radius:6px; background:${alpha(T.evergreen, .1)}; overflow:visible; }
        .bcl .gb-committed { position:absolute; left:0; top:0; bottom:0; background:${T.meadow}; opacity:.3; border-radius:6px; }
        .bcl .gb-fill { position:absolute; left:0; top:0; bottom:0; background:${T.meadow}; border-radius:6px 0 0 6px; }
        .bcl .gb-pace { position:absolute; top:-4px; bottom:-4px; width:2px; background:${T.evergreen};
          box-shadow:0 0 0 2px ${alpha(T.white, .7)}; }
        .bcl .gb-pace::after { content:""; position:absolute; top:-4px; left:-3px; width:8px; height:8px;
          border-radius:99px; background:${T.evergreen}; }
        .bcl .gb-goalcap { position:absolute; right:0; top:-3px; bottom:-3px; width:3px; border-radius:2px; background:${T.poppy}; }
        .bcl .gb-legend { display:flex; gap:18px; margin-top:12px; flex-wrap:wrap; align-items:center; }
        .bcl .gb-legend span { font-size:11.5px; color:${T.slate}; display:inline-flex; align-items:center; gap:6px; }
        .bcl .gb-legend b { color:${T.ink}; font-weight:600; font-family:Poppins,sans-serif; }
        .bcl .gb-legend .right { margin-left:auto; }
        .bcl .gb-legend .d { width:8px; height:8px; border-radius:99px; }
        .bcl .gb-legend .d.meadow { background:${T.meadow}; }
        .bcl .gb-legend .d.meadowLt { background:${T.meadow}; opacity:.35; }
        .bcl .gb-legend .d.goal { background:${T.poppy}; }
        .bcl .gb-legend .tick { width:2px; height:11px; background:${T.evergreen}; border-radius:1px; }

        .bcl .hstats { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-top:22px; }
        .bcl .ms { background:${alpha(T.white, .5)}; border:1px solid ${alpha(T.evergreen, .1)}; border-radius:11px; padding:11px 13px; }
        .bcl .ms-l { font-size:10px; font-weight:600; letter-spacing:.08em; text-transform:uppercase; color:${T.tertiary}; }
        .bcl .ms-v { font-family:Poppins,sans-serif; font-size:19px; font-weight:700; color:${T.evergreen}; margin-top:5px;
          letter-spacing:-.02em; font-variant-numeric:tabular-nums; }
        .bcl .ms-v.behind { color:${T.poppyText}; }
        .bcl .ms-v.good { color:${T.meadow}; }
        .bcl .ms-s { font-size:10.5px; color:${T.muted}; margin-top:2px; }

        .bcl .card { background:${T.white}; border:1px solid ${T.line}; border-top:none; border-radius:0 0 18px 18px;
          padding:22px 26px 24px; box-shadow:0 12px 30px ${alpha(T.evergreen, .06)}; }

        .bcl .fn-head { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:14px; flex-wrap:wrap; gap:4px; }
        .bcl .fn-title { font-family:Poppins,sans-serif; font-size:11px; font-weight:700; letter-spacing:.13em; text-transform:uppercase; color:${T.ink}; }
        .bcl .fn-sub { font-size:11px; color:${T.muted}; }
        .bcl .fr { margin-bottom:13px; }
        .bcl .fr-top { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:5px; }
        .bcl .fr-label { font-size:12.5px; font-weight:600; color:${T.secondary}; }
        .bcl .fr-owner { font-style:normal; font-size:10px; font-weight:500; letter-spacing:.04em; text-transform:uppercase;
          color:${T.muted}; margin-left:8px; }
        .bcl .fr-n { font-family:Poppins,sans-serif; font-size:15px; font-weight:600; color:${T.ink}; font-variant-numeric:tabular-nums; }
        .bcl .fr-bar { height:8px; border-radius:5px; background:${T.parchment}; overflow:hidden; }
        .bcl .fr-bar span { display:block; height:100%; background:${T.teal}; opacity:.55; border-radius:5px; transition:width .4s ease; }
        .bcl .fr-tag { font-size:11px; color:${T.muted}; margin-top:5px; }
        .bcl .fr-tag.teal { color:${T.teal}; font-weight:600; }

        .bcl .fn-out { margin-top:16px; padding-top:16px; border-top:1px dashed ${T.line}; }
        .bcl .fn-out-top { display:flex; justify-content:space-between; align-items:baseline; }
        .bcl .fn-out-label { font-size:13px; font-weight:700; color:${T.ink}; display:inline-flex; align-items:center; gap:7px; }
        .bcl .fn-out-label .d { width:9px; height:9px; border-radius:99px; background:${T.meadow}; }
        .bcl .fn-out-n { font-family:Poppins,sans-serif; font-size:22px; font-weight:700; color:${T.ink}; font-variant-numeric:tabular-nums; }
        .bcl .fn-out-n em { font-style:normal; font-size:12px; font-weight:500; color:${T.muted}; margin-left:6px; }
        .bcl .fn-out-bar { height:10px; border-radius:6px; background:${T.parchment}; overflow:hidden; margin:9px 0 7px; }
        .bcl .fn-out-bar span { display:block; height:100%; background:${T.meadow}; border-radius:6px; transition:width .5s ease; }
        .bcl .fn-out-sub { font-size:12px; color:${T.slate}; } .bcl .fn-out-sub b { color:${T.meadow}; font-family:Poppins,sans-serif; font-weight:600; }

        .bcl .side { display:grid; grid-template-columns:1fr 1fr; gap:11px; margin-top:18px; }
        .bcl .side-chip { background:${T.parchment}; border:1px solid ${T.line}; border-radius:11px; padding:12px 14px; }
        .bcl .side-n { font-family:Poppins,sans-serif; font-size:20px; font-weight:700; color:${T.ink}; }
        .bcl .side-l { font-size:12px; font-weight:600; color:${T.secondary}; margin-left:7px; }
        .bcl .side-note { display:block; font-size:10.5px; color:${T.muted}; margin-top:4px; line-height:1.4; }

        .bcl .mom { margin-top:16px; }
        .bcl .mom-head { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:12px; flex-wrap:wrap; gap:4px; }
        .bcl .mom-title { font-family:Poppins,sans-serif; font-size:11px; font-weight:700; letter-spacing:.13em; text-transform:uppercase; color:${T.ink}; }
        .bcl .mom-sub { font-size:11px; color:${T.muted}; }
        .bcl .mom-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:11px; }
        .bcl .mom-cell { background:${T.white}; border:1px solid ${T.line}; border-radius:11px; padding:12px 13px; }
        .bcl .mom-l { font-size:11px; font-weight:600; color:${T.slate}; }
        .bcl .mom-row { display:flex; align-items:baseline; gap:8px; margin-top:4px; }
        .bcl .mom-v { font-family:Poppins,sans-serif; font-size:22px; font-weight:700; color:${T.ink}; font-variant-numeric:tabular-nums; }
        .bcl .mom-d { font-size:11px; font-weight:700; }
        .bcl .mom-d.up { color:${T.meadow}; } .bcl .mom-d.dn { color:${T.petalDeep}; } .bcl .mom-d.flat { color:${T.muted}; }
        .bcl .mom-foot { display:flex; align-items:center; justify-content:space-between; margin-top:6px; }
        .bcl .spark { display:block; } .bcl .mom-prev { font-size:10px; color:${T.muted}; }

        .bcl .cash { margin-top:18px; padding-top:16px; border-top:1px solid ${T.line}; }
        .bcl .cash-top { display:flex; justify-content:space-between; align-items:baseline; }
        .bcl .cash-l { font-size:11px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:${T.slate}; }
        .bcl .cash-v { font-size:13px; color:${T.secondary}; } .bcl .cash-v b { font-family:Poppins,sans-serif; font-weight:600; color:${T.ink}; } .bcl .cash-v em { font-style:normal; color:${T.muted}; }
        .bcl .cash-bar { height:7px; border-radius:4px; background:${T.parchment}; overflow:hidden; margin:9px 0 7px; }
        .bcl .cash-fill { display:block; height:100%; background:${T.teal}; opacity:.55; border-radius:4px; }
        .bcl .cash-note { font-size:11px; color:${T.muted}; line-height:1.5; } .bcl .cash-note em { font-style:normal; color:${T.slate}; font-weight:600; }

        .bcl .drawer { margin-bottom:16px; background:${T.white}; border:1px solid ${T.line}; border-radius:16px;
          box-shadow:0 12px 30px ${alpha(T.evergreen, .08)}; overflow:hidden; }
        .bcl .dr-head { display:flex; justify-content:space-between; align-items:center; padding:15px 20px;
          border-bottom:1px solid ${T.line}; background:${T.parchment}; }
        .bcl .dr-title { font-family:Poppins,sans-serif; font-size:13px; font-weight:700; color:${T.ink}; }
        .bcl .dr-x { border:none; background:none; font-size:14px; color:${T.slate}; cursor:pointer; padding:2px 6px; }
        .bcl .dr-body { padding:18px 20px 22px; }
        .bcl .dr-sec { font-size:10px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:${T.petalDeep};
          margin:18px 0 10px; } .bcl .dr-sec:first-child { margin-top:0; } .bcl .dr-sec .ro { font-weight:500; color:${T.muted}; text-transform:none; letter-spacing:0; font-style:normal; margin-left:8px; }
        .bcl .fld { display:block; margin-bottom:13px; }
        .bcl .fld-l { display:block; font-size:11.5px; font-weight:600; color:${T.secondary}; margin-bottom:5px; }
        .bcl .fld-h { display:block; font-size:10.5px; color:${T.muted}; margin-top:4px; }
        .bcl .in { width:100%; border:1px solid ${T.line}; border-radius:8px; padding:8px 10px; font-family:inherit;
          font-size:12.5px; color:${T.ink}; background:${T.white}; }
        .bcl .in:focus { outline:2px solid ${T.petal}; outline-offset:1px; border-color:${T.petal}; }
        .bcl .in-money { position:relative; } .bcl .in-money span { position:absolute; left:10px; top:50%; transform:translateY(-50%); font-size:12.5px; color:${T.muted}; }
        .bcl .in-money .in { padding-left:20px; }
        .bcl .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:11px; }
        .bcl .range { width:100%; accent-color:${T.petalDeep}; }
        .bcl .seg { display:inline-flex; border:1px solid ${T.line}; border-radius:8px; overflow:hidden; }
        .bcl .seg button { font-family:inherit; font-size:12px; font-weight:600; padding:7px 14px; border:none;
          background:${T.white}; color:${T.muted}; cursor:pointer; }
        .bcl .seg button.on { background:${T.evergreen}; color:${T.onDark}; }
        .bcl .map { border:1px solid ${T.line}; border-radius:10px; overflow:hidden; }
        .bcl .map-row { display:flex; justify-content:space-between; align-items:center; padding:9px 13px; border-bottom:1px solid ${T.parchment}; }
        .bcl .map-row:last-child { border-bottom:none; }
        .bcl .map-g { font-size:12px; font-weight:600; color:${T.secondary}; } .bcl .map-g em { font-style:normal; font-size:10px; text-transform:uppercase; letter-spacing:.04em; color:${T.muted}; margin-left:8px; }
        .bcl .map-m { font-size:11px; color:${T.muted}; }
        .bcl .map-in { width:58%; flex:none; font-size:11.5px; padding:5px 9px; }
        .bcl .map-note { font-size:10.5px; color:${T.muted}; margin-top:8px; line-height:1.5; }
        .bcl .dr-err { font-size:11.5px; color:${T.poppyText}; margin-top:12px; }
        .bcl .dr-actions { display:flex; justify-content:flex-end; gap:8px; margin-top:16px; }
        .bcl .btn { font-family:inherit; font-size:12px; font-weight:600; border-radius:8px; padding:8px 14px; cursor:pointer; border:1px solid transparent; }
        .bcl .btn.ghost { background:${T.white}; border-color:${T.line}; color:${T.secondary}; }
        .bcl .btn.primary { background:${T.evergreen}; color:${T.onDark}; }
        .bcl .btn.primary:disabled { background:${T.muted}; cursor:not-allowed; }

        @media (prefers-reduced-motion:no-preference){
          .bcl .edot, .bcl .pill.open .pdot { animation:bcl-pulse 2.2s ease-out infinite; }
        }
        @keyframes bcl-pulse { 0%{ box-shadow:0 0 0 0 ${alpha(T.poppy, .6)}; } 70%{ box-shadow:0 0 0 5px ${alpha(T.poppy, 0)}; } 100%{ box-shadow:0 0 0 0 ${alpha(T.poppy, 0)}; } }

        @media (max-width:640px){
          .bcl .hstats, .bcl .mom-grid { grid-template-columns:1fr; }
          .bcl .side, .bcl .shift-body { grid-template-columns:1fr; }
          .bcl .grid2 { grid-template-columns:1fr; }
          .bcl .hnum { font-size:36px; }
        }
      `}</style>

      <div className="mod">
        {usingSample && (
          <div className="ph">
            <span className="ph-dot" />
            <span><b>Sample data.</b> <em>A simulated in-window snapshot. Wires to the “{cfg.pipeline_match}” pipeline on sync.</em></span>
          </div>
        )}
        {!usingSample && data.warnings && data.warnings.length > 0 && (
          <div className="ph">
            <span className="ph-dot" />
            <span><b>Heads up.</b> <em>{data.warnings.join(" · ")}. Check the stage mapping in Launch settings.</em></span>
          </div>
        )}

        <div className="ctx">
          <span className="ctx-bar" />
          <span className="ctx-h">{cfg.program}</span>
          <span className="ctx-s">{cfg.name} · Launch · event {fmtDate(cfg.event_start)}–{fmtDate(cfg.event_end)} · cart through {fmtDate(cfg.window_end)}</span>
          <span className="ctx-spacer" />
          <span className={`pill ${pill.tone}`}><span className="pdot" />{pill.txt}</span>
          {isEditor && (
            <button className={`gear ${showSettings ? "on" : ""}`} onClick={() => setShowSettings((s) => !s)}>
              ⚙ Launch settings
            </button>
          )}
        </div>

        {showSettings && isEditor && (
          <SettingsDrawer cfg={cfg} launchId={data.id} businessKey={businessKey} canPersist={canPersist}
            onClose={() => setShowSettings(false)} onSaved={onSaved} />
        )}

        {data.shift && <TheShift shift={data.shift} />}

        <div className="hero">
          <SpringSignature tone="dark" height={40}
            style={{ position: "absolute", top: 18, right: 24, opacity: 0.12, pointerEvents: "none" }} />
          <div className="eyebrow"><span className="edot" />{seatPrimary ? "Members enrolled · to goal" : "ARR added · to goal"}</div>
          <div className="hnum">
            <Num metric={seatPrimary ? "funnel.enrolled" : "enrolled.arr"}>{seatPrimary ? D.enrolledSeats : kMoney(D.enrolledArr)}</Num>
            <span className="of">of <Num metric="seat_target">{seatPrimary ? `${D.seatTarget} members` : kMoney(cfg.goal_arr)}</Num></span>
          </div>
          <div className="hdesc">
            {seatPrimary
              ? `${kMoney(D.enrolledArr)} ARR added`
              : `${D.enrolledSeats} of ${D.seatTarget} seats enrolled`} · {data.days_remaining} days left in the cart
          </div>

          <GoalBar cfg={cfg} D={D} seatPrimary={seatPrimary} />

          <div className="hstats">
            <MiniStat label="Days left" metric="days_left" value={`${data.days_remaining}d`} sub={`of ${data.window_days}-day cart`} />
            <MiniStat label={seatPrimary ? "Members enrolled" : "Seats enrolled"} metric="funnel.enrolled"
              value={`${D.enrolledSeats} / ${D.seatTarget}`}
              sub={`${Math.round((seatPrimary ? D.pctPrimary : D.pctToGoal) * 100)}% of goal`} tone="good" />
            <MiniStat label="Pace" value={paceStat.value} sub={paceStat.sub} tone={paceStat.tone} />
          </div>
        </div>

        <div className="card">
          <Funnel cfg={cfg} data={data} D={D} />
          <Momentum mom={data.momentum} />
          <CashLine cfg={cfg} D={D} cash={data.cash} />
        </div>
      </div>
      {drill && <LaunchDrawer metric={drill} businessKey={businessKey} usingSample={usingSample}
        onClose={() => setDrill(null)} />}
    </div>
    </DrillCtx.Provider>
  );
}
