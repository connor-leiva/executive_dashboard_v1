import { useState, useEffect, useRef } from "react";
import { usd, signed } from "./theme.js";
import { A } from "./forumIcons.js";

/* ──────────────────────────────────────────────────────────────
   The Forum — ELEVATED view (SPEC-forum-tab-elevated).
   Depth + time: surfaces at real elevation tiers, light from above,
   headline numbers count up, blocks stagger in, data viz grows on
   section open. One summary split by function — a dark Money Hero
   (cash lens) + an Operational Pulse — so no metric appears twice.
   Forum color law: zero poppy, daffodil = the one action, meadow =
   positive, evergreen = anchor. All motion respects reduced-motion.
   Data-driven from /api/v1/forum; drills reuse the audit drawer.
   Shared with beCollective (no billing → no Money Hero / Cash section).
   ────────────────────────────────────────────────────────────── */

const API_BASE = import.meta.env.VITE_API_BASE;

const C = {
  page: "#F6F0E9", pageTop: "#FBF7F1", surface: "#FFFFFF", parchment: "#F8F5F2", hair: "#EAE1D6",
  ink: "#002E2C", body: "#334733", slate: "#4D6A4D", muted: "#89A989",
  meadow: "#61835E", sprout: "#B8CCB8", mist: "#DCE7E9", mistDeep: "#67A5AA", evergreen: "#002E2C",
  flagBg: "#FFF9D6", flagDot: "#FFDD1F", flagText: "#6D5336", amber: "#B5792A",
  heroDeep: "#00211F", heroMid: "#013B38", heroText: "#EAF3EE", heroMut: "rgba(217,232,225,.58)",
  petal: "#FFBA9F", petalDeep: "#E08863", poppy: "#FA8069",
};

const kc = (n) => {
  const a = Math.abs(n || 0);
  if (a >= 1_000_000) return "$" + (a / 1_000_000).toFixed(2) + "M";
  if (a >= 1000) return "$" + (a / 1000).toFixed(a >= 100_000 ? 0 : 1) + "K";
  return "$" + Math.round(a);
};
const MON_ABBR = { "01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "May", "06": "Jun", "07": "Jul", "08": "Aug", "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec" };
const twoInts = (s) => { const m = (s || "").match(/(\d[\d,]*)\D+(\d[\d,]*)/); return m ? [m[1], m[2]] : null; };

/* ── hooks ─────────────────────────────────────────────────────── */
function useReducedMotion() {
  const [r, setR] = useState(false);
  useEffect(() => {
    const m = window.matchMedia("(prefers-reduced-motion: reduce)");
    const on = () => setR(m.matches); on();
    m.addEventListener && m.addEventListener("change", on);
    return () => m.removeEventListener && m.removeEventListener("change", on);
  }, []);
  return r;
}
function useCountUp(target, duration = 850) {
  const reduced = useReducedMotion();
  const [val, setVal] = useState(0);
  useEffect(() => {
    if (reduced) { setVal(target); return; }
    let raf, done = false; const t0 = performance.now();
    const ease = (t) => 1 - Math.pow(1 - t, 3);
    const tick = (now) => {
      const p = Math.min((now - t0) / duration, 1);
      setVal(target * ease(p));
      if (p < 1) raf = requestAnimationFrame(tick); else done = true;
    };
    raf = requestAnimationFrame(tick);
    // Safety net: rAF is throttled/paused in hidden tabs, which would freeze the
    // value at 0. Guarantee we land on the target regardless.
    const safety = setTimeout(() => { if (!done) setVal(target); }, duration + 400);
    return () => { cancelAnimationFrame(raf); clearTimeout(safety); };
  }, [target, duration, reduced]);
  return val;
}

/* ── primitives ────────────────────────────────────────────────── */
function Icon({ src, size = 15, color = "currentColor", style, className }) {
  const url = `url(${src})`;
  return <span aria-hidden className={className} style={{ display: "inline-block", width: size, height: size,
    backgroundColor: color, WebkitMaskImage: url, maskImage: url, WebkitMaskSize: "contain",
    maskSize: "contain", WebkitMaskRepeat: "no-repeat", maskRepeat: "no-repeat",
    WebkitMaskPosition: "center", maskPosition: "center", flexShrink: 0, ...style }} />;
}
function Chip({ src, tint = C.meadow, size = 30, icon = 15, onDark }) {
  return (
    <span className="chip" style={{ width: size, height: size,
      background: onDark ? "rgba(184,204,184,.14)" : `linear-gradient(155deg, ${tint}26, ${tint}12)`,
      boxShadow: onDark ? "inset 0 1px 0 rgba(255,255,255,.08)" : `inset 0 1px 0 rgba(255,255,255,.55), 0 1px 2px ${tint}26` }}>
      <Icon src={src} size={icon} color={onDark ? C.sprout : tint} />
    </span>
  );
}
function Count({ to, fmt, prefix = "", suffix = "", duration }) {
  const v = useCountUp(to, duration);
  return <>{prefix}{fmt ? fmt(v) : Math.round(v).toLocaleString("en-US")}{suffix}</>;
}
function Glow({ className = "", tint = "rgba(97,131,94,.12)", children, style, ...rest }) {
  const ref = useRef(null);
  const onMove = (e) => {
    const el = ref.current; if (!el) return;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--mx", `${e.clientX - r.left}px`);
    el.style.setProperty("--my", `${e.clientY - r.top}px`);
  };
  return <div ref={ref} onMouseMove={onMove} className={`glow ${className}`}
    style={{ "--glow": tint, ...style }} {...rest}>{children}</div>;
}
function Spark({ points, w = 66, h = 22, stroke = C.meadow, fill, fillId, pad = 2 }) {
  if (!points || points.length < 2) return null;
  const max = Math.max(...points), min = Math.min(...points), rng = max - min || 1;
  const xy = points.map((p, i) => [(i / (points.length - 1)) * (w - pad * 2) + pad,
    h - pad - ((p - min) / rng) * (h - pad * 2)]);
  const d = xy.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const area = `${d} L ${w - pad} ${h} L ${pad} ${h} Z`;
  return (
    <svg width={w} height={h} className="spark" style={{ display: "block", overflow: "visible" }}>
      {fill && <path d={area} fill={fill} className="spark-area" />}
      <path d={d} fill="none" stroke={stroke} strokeWidth={1.7} strokeLinecap="round"
        strokeLinejoin="round" pathLength="1" className="spark-line" />
    </svg>
  );
}
function Ring({ pct, size = 44, stroke = 5, color = C.meadow, track = "rgba(0,46,44,.10)", children }) {
  const r = (size - stroke) / 2, c = size / 2;
  return (
    <div style={{ position: "relative", width: size, height: size, flexShrink: 0 }}>
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
        <circle cx={c} cy={c} r={r} fill="none" stroke={track} strokeWidth={stroke} />
        <circle cx={c} cy={c} r={r} fill="none" stroke={color} strokeWidth={stroke} strokeLinecap="round"
          pathLength="1" strokeDasharray="1" style={{ "--to": (1 - (pct || 0) / 100).toFixed(3) }} className="ring-arc" />
      </svg>
      <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center",
        justifyContent: "center", fontFamily: "Poppins,sans-serif", fontSize: 11, fontWeight: 700, color: C.ink }}>{children}</div>
    </div>
  );
}
function Bar({ label, pct, val, open, from = C.meadow, to = C.sprout, track = C.parchment, wide }) {
  return (
    <div className="bar">
      <span className="bar-l" style={wide ? { width: 110 } : undefined}>{label}</span>
      <div className="bt" style={{ background: track }}>
        <div className="bt-fill" style={{ width: open ? `${pct}%` : 0, background: `linear-gradient(90deg, ${from}, ${to})` }} />
      </div>
      <b>{val}</b>
    </div>
  );
}
const Row = ({ a, b, v }) => (
  <div className="row"><span>{a}</span><span style={{ color: C.muted }}>{b}</span><b>{v}</b></div>
);
const Dot = ({ c, t }) => (
  <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
    <span style={{ width: 8, height: 8, borderRadius: 3, background: c, flexShrink: 0 }} />{t}</span>
);
function Drill({ children, onClick }) {
  return <span className="drill" role="button" tabIndex={0} onClick={onClick}
    onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onClick && onClick())}>
    {children}<Icon src={A.open} size={11} color="currentColor" /></span>;
}

/* ── P&L card — the Booked Lens ─────────────────────────────────── */
const PL_KIND = { tot: "tot", sub: "sub", ded: "ded" };
function PnlCard({ area, billing }) {
  const rows = area?.pl;
  const netCash = billing?.available ? billing.net_cash : null;
  const opex = rows?.find((r) => /operating expense|opex/i.test(r.label || ""))?.value;
  return (
    <div className="card pnl-card fin">
      <div className="kick"><span className="kb" />Financial · P&amp;L
        <span className="lens">Booked Lens</span><span className="src">QuickBooks</span></div>
      {rows && rows.length ? (
        rows.map((r, i) => (
          <div key={i} className={`prow ${PL_KIND[r.kind] || ""}`}>
            <span>{r.label}</span><b>{signed(r.value)}</b>
          </div>
        ))
      ) : (
        <>
          {["Revenue — Membership + Events", "Cost of Sale — Production, Venue, Speakers", "Gross Profit"].map((l, i) => (
            <div key={l} className={`prow ${i === 2 ? "sub" : ""}`}>
              <span>{l}</span><b style={{ color: C.muted, fontWeight: i === 2 ? 600 : 400 }}>awaiting QBO</b>
            </div>
          ))}
          <ConnectQbo area={area} />
        </>
      )}
      {netCash != null && (
        <div className="pnl-note">Booked lights up once QuickBooks connects (split by class). Until then the <b>cash lens</b> to the right is the real-time truth — <b>{usd(netCash)}</b> net cash{opex != null ? <> vs <b>{usd(opex)}</b> expenses</> : null}.</div>
      )}
    </div>
  );
}
function ConnectQbo() {
  // Connecting/routing a QBO entity lives in Settings › Integrations (the self-service
  // flow); this just points there rather than hitting the auth'd connect endpoint raw.
  return (
    <a href="/settings/integrations" style={{
      display: "inline-block", marginTop: 12, fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600,
      color: C.heroText, background: C.evergreen, borderRadius: 9, padding: "9px 15px", textDecoration: "none",
    }}>Connect in Settings</a>
  );
}

/* ── Money Hero — the Cash Lens (real-time) ─────────────────────── */
function MoneyHero({ b, rangeLabel, spark, months, onOpen, isBc }) {
  return (
    <Glow className={isBc ? "hero bc" : "hero"} tint={isBc ? "rgba(224,136,99,.16)" : "rgba(103,165,170,.18)"}>
      <div className="hero-eyebrow"><span className="hero-dot" />Cash · Real-Time Truth
        <span className="hero-range">Cash Lens{rangeLabel ? ` · ${rangeLabel}` : ""}</span></div>
      <div className="hero-body">
        <div className="hero-primary">
          <div className="hero-plabel">Net Cash{months[0] ? ` · Since ${months[0]}` : ""}</div>
          <div className="hero-pval"><Count to={b.net_cash} fmt={kc} duration={950} /></div>
          <div className="hero-chart">
            <Spark points={spark} w={300} h={58} stroke={isBc ? C.meadow : C.mistDeep} fill="url(#hg)" pad={3} />
            <svg width="0" height="0"><defs>
              <linearGradient id="hg" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={isBc ? C.meadow : C.mistDeep} stopOpacity={isBc ? "0.28" : "0.34"} />
                <stop offset="100%" stopColor={isBc ? C.meadow : C.mistDeep} stopOpacity="0" />
              </linearGradient></defs></svg>
            <div className="hero-months">{months.map((m) => <span key={m}>{m}</span>)}</div>
          </div>
          <div className="hero-psub">{usd(b.gross)} collected − {usd(b.refunded)} · <b className="hero-drill" onClick={() => onOpen("forum_payments")}>{b.txn_count} transactions ↗</b></div>
        </div>
        <div className="hero-side">
          <Glow className="hero-scell" tint={isBc ? "rgba(224,136,99,.18)" : "rgba(103,165,170,.16)"} role="button" tabIndex={0}
            onClick={() => onOpen("forum_mrr_subs")} onKeyDown={(e) => (e.key === "Enter") && onOpen("forum_mrr_subs")}>
            <Chip src={A.growth} onDark={!isBc} tint={C.meadow} size={26} icon={13} />
            <div className="hero-sval"><Count to={b.mrr} fmt={kc} duration={880} /><span>/mo</span></div>
            <div className="hero-slabel">MRR · True Recurring</div>
            <div className="hero-ssub">{b.perpetual_count} perpetual subscriptions</div>
          </Glow>
          <Glow className="hero-scell" tint={isBc ? "rgba(224,136,99,.18)" : "rgba(103,165,170,.16)"} role="button" tabIndex={0}
            onClick={() => onOpen("renewal_book")} onKeyDown={(e) => (e.key === "Enter") && onOpen("renewal_book")}>
            <Chip src={A.crown} onDark={!isBc} tint={C.meadow} size={26} icon={13} />
            <div className="hero-sval"><Count to={b.arr_book} fmt={kc} duration={940} /></div>
            <div className="hero-slabel">ARR · Renewal Book</div>
            <div className="hero-ssub">run-rate {kc(b.run_rate)}</div>
          </Glow>
        </div>
      </div>
    </Glow>
  );
}

/* ── Operational Pulse — leading indicators, no money ───────────── */
function pulseTiles(data, onOpen) {
  const kpi = (k) => (data.kpis || []).find((x) => x.key === k);
  const funnelTotal = data.funnel ? (data.funnel.stages || []).reduce((a, s) => a + s.v, 0) : null;
  const vip = data.funnel ? (data.funnel.stages || []).find((s) => /vip/i.test(s.label))?.v : null;
  const tiles = [];
  const am = kpi("active_members") || kpi("bc_members");
  tiles.push({ icon: A.leaf, label: "Active Members", value: String(data.members_total ?? am?.value ?? "—"), sub: am?.sub, drill: am?.drill });

  if (funnelTotal != null) {
    tiles.push({ icon: A.growth, label: "Recruiting Pipeline", value: String(funnelTotal), sub: vip ? `${vip} in VIP guest` : "in pipeline" });
  }
  if (data.renewals?.summary) {
    const s = data.renewals.summary;
    tiles.push({ icon: A.reload, label: "Renewals Due", value: String(s.count), sub: `next 90 days · ${s.value} book`, drill: "renewal_book" });
  }
  if (data.event) {
    const e = data.event;
    const pct = e.members ? Math.round((e.registered / e.members) * 100) : 0;
    tiles.push({ icon: A.pin, label: "Event Readiness", value: e.days_out != null ? `${e.days_out}d` : (e.where || "—"),
      sub: e.registered != null ? `${e.registered} of ${e.members} registered` : e.where, ring: pct, drill: e.unregistered ? "unregistered" : undefined });
  }
  // fill from remaining KPIs (beCollective path) up to 4
  for (const kp of (data.kpis || [])) {
    if (tiles.length >= 4) break;
    if (["active_members", "bc_members"].includes(kp.key)) continue;
    if (tiles.some((t) => t.label.toLowerCase() === (kp.label || "").toLowerCase())) continue;
    tiles.push({ icon: A.leaf, label: kp.label, value: kp.value, sub: kp.sub, drill: kp.drill });
  }
  return tiles.slice(0, 4);
}
function OpsPulse({ data, onOpen }) {
  const tiles = pulseTiles(data, onOpen);
  return (
    <div className="pulse">
      <div className="pulse-head"><span className="kb kb-m" />Operational Pulse · Leading Indicators
        <span className="src">Go High Level</span></div>
      <div className="pulse-grid">
        {tiles.map((t) => {
          const clickable = !!t.drill;
          return (
            <Glow key={t.label} className={`ptile lift ${clickable ? "click" : ""}`} tint="rgba(97,131,94,.10)"
              role={clickable ? "button" : undefined} tabIndex={clickable ? 0 : undefined}
              onClick={clickable ? () => onOpen(t.drill) : undefined}
              onKeyDown={clickable ? (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onOpen(t.drill)) : undefined}>
              <div className="ptile-top"><Chip src={t.icon} tint={C.meadow} size={28} icon={14} />
                <span className="ptile-label">{t.label}</span>
                {clickable && <Icon src={A.open} size={11} color={C.muted} style={{ marginLeft: "auto" }} />}</div>
              <div className="ptile-row">
                <div>
                  <div className="ptile-v">{t.value}</div>
                  <div className="ptile-s">{t.sub}</div>
                </div>
                {t.ring != null && <Ring pct={t.ring} size={44} stroke={4} color={C.meadow}>{t.ring}%</Ring>}
              </div>
            </Glow>
          );
        })}
      </div>
    </div>
  );
}

/* ── Watch Strip — the single action ────────────────────────────── */
function WatchStrip({ b, data, onOpen }) {
  const failed = b && b.failed_count > 0;         // month-to-date card failures
  const pastDue = b && b.past_due > 0;            // subscriptions stuck past due
  if (!failed && !pastDue) {
    // no money action: fall back to the event call-list if it's behind
    if (data.event?.unregistered > 0) {
      return (
        <div className="watchstrip">
          <span className="flag"><span className="flag-dot" /><Icon src={A.warn} size={13} color={C.flagText} />{data.event.unregistered} not registered for {data.event.where || "the next event"}</span>
          <button className="ghost lift" onClick={() => onOpen("unregistered")}>The Call List<Icon src={A.open} size={11} color="currentColor" /></button>
        </div>
      );
    }
    return (
      <div className="watchstrip">
        <span style={{ display: "inline-flex", alignItems: "center", gap: 7, fontFamily: "Inter,sans-serif", fontSize: 12, fontWeight: 600, color: C.meadow }}>
          <span style={{ width: 7, height: 7, borderRadius: 99, background: C.meadow }} />All clear — nothing needs attention today</span>
      </div>
    );
  }
  return (
    <div className="watchstrip">
      {failed && (
        <span className="watchpair">
          <span className="flag"><span className="flag-dot" /><Icon src={A.warn} size={13} color={C.flagText} />{b.failed_count} failed charge{b.failed_count === 1 ? "" : "s"} this month · {usd(b.failed_amount)} to recover</span>
          <button className="ghost lift" onClick={() => onOpen("forum_failed_payments")}>Review failed charges<Icon src={A.open} size={11} color="currentColor" /></button>
        </span>
      )}
      {pastDue && (
        <span className="watchpair">
          <span className="flag"><span className="flag-dot" /><Icon src={A.warn} size={13} color={C.flagText} />{b.past_due} subscription{b.past_due === 1 ? "" : "s"} past-due{b.past_due_amount ? ` · ${usd(b.past_due_amount)}/mo to recover` : ""}</span>
          <button className="ghost lift" onClick={() => onOpen("pastdue")}>Review past-due<Icon src={A.open} size={11} color="currentColor" /></button>
        </span>
      )}
    </div>
  );
}

/* ── Section + Deck ─────────────────────────────────────────────── */
function Section({ icon, tint = C.meadow, title, summary, watch, live, sub, open, onToggle, children }) {
  return (
    <div className={`sec ${open ? "on" : ""}`}>
      <button className="sec-head" onClick={onToggle} aria-expanded={open}>
        <Chip src={icon} tint={open ? tint : C.slate} size={38} icon={18} />
        <span className="sec-title">{title}</span>
        <span className="sec-sum">{summary}</span>
        {watch && <span className="sec-watch"><span className="wd" />{watch}</span>}
        <span style={{ flex: 1 }} />
        <span className="chev-wrap"><Icon src={A.chev} size={14} color={C.muted} style={{ transform: open ? "rotate(180deg)" : "none" }} /></span>
      </button>
      {open && (
        <div className="sec-body">
          {sub && <div className="sec-sub">{live && <span className="live"><span className="live-dot" />Live</span>}{sub}</div>}
          {children}
        </div>
      )}
    </div>
  );
}
function Deck({ items }) {
  const [sel, setSel] = useState(items[0]?.key);
  if (!items.length) return null;
  const active = items.find((d) => d.key === sel) || items[0];
  return (
    <>
      <div className="deck" style={{ gridTemplateColumns: `repeat(${items.length}, minmax(0,1fr))` }}>
        {items.map((d) => (
          <Glow key={d.key} className={`dcard ${active.key === d.key ? "on" : "off"}`} tint="rgba(97,131,94,.10)"
            onClick={() => setSel(d.key)} role="button" tabIndex={0}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), setSel(d.key))}>
            {active.key === d.key && <span className="dcard-accent" style={{ background: d.accent || C.meadow }} />}
            <div className="dhead"><Chip src={d.icon} tint={active.key === d.key ? (d.accent || C.meadow) : C.slate} size={24} icon={12} /><span className="dname">{d.name}</span></div>
            <div className="dstat">{d.stat}</div>
            <div className="dline">{d.line}</div>
          </Glow>
        ))}
      </div>
      <div className="focus"><div className="fbody" key={active.key}>{active.render()}</div></div>
    </>
  );
}

/* ── Members & Growth deck panels ───────────────────────────────── */
function membersItems(data, open, onOpen, deckSlots, rosterKey = "forum_roster") {
  const has = (k) => (data.deck || []).some((x) => x.k === k) || (k === "roster");
  const order = ["roster", ...deckSlots.map((s) => s.k)];
  const items = [];

  // Roster — always
  const am = (data.kpis || []).find((x) => x.key === "active_members" || x.key === "bc_members");
  const arr = (data.kpis || []).find((x) => x.key === "forum_arr" || x.key === "bc_arr");
  const newM = (data.kpis || []).find((x) => x.key === "new_members" || x.key === "bc_new_members");
  const renD = (data.kpis || []).find((x) => x.key === "renewals_due");
  const rs = data.roster || {};
  const mix = rs.payment_mix || {};
  const mixTot = (mix.monthly || 0) + (mix.quarterly || 0) + (mix.pif || 0) + (mix.installments || 0) || 1;
  const mpct = (n) => `${((n || 0) / mixTot) * 100}%`;
  const compLine = rs.primary != null ? [`${rs.primary} primary`, `${rs.add_on} add-on`,
    rs.admin ? `${rs.admin} admin` : null, rs.unspecified ? `${rs.unspecified} unset` : null].filter(Boolean).join(" · ") : null;
  items.push({ key: "roster", icon: A.users, name: "Roster", stat: String(data.members_total ?? "—"), line: compLine || "active members", accent: C.meadow, render: () => (
    <div className="cols">
      <div>
        <div className="colhead">Composition</div>
        {compLine
          ? <Row a="Members" b={compLine} v={arr ? `${arr.value} book` : ""} />
          : <Row a="Members" b="active" v={arr?.value || ""} />}
        <div style={{ marginTop: 10 }}><Drill onClick={() => onOpen(rosterKey)}>View all {data.members_total} members</Drill></div>
      </div>
      <div>
        {(mix.pif || mix.monthly || mix.quarterly || mix.installments) ? <>
          <div className="colhead">Payment Mix</div>
          <div className="stack">
            {mix.pif > 0 && <div className="stack-seg" style={{ width: mpct(mix.pif), background: C.evergreen }} title={`${mix.pif} paid in full`} />}
            {mix.monthly > 0 && <div className="stack-seg" style={{ width: mpct(mix.monthly), background: C.meadow }} title={`${mix.monthly} monthly`} />}
            {mix.quarterly > 0 && <div className="stack-seg" style={{ width: mpct(mix.quarterly), background: C.sprout }} title={`${mix.quarterly} quarterly`} />}
            {mix.installments > 0 && <div className="stack-seg" style={{ width: mpct(mix.installments), background: C.mistDeep }} title={`${mix.installments} installments`} />}
          </div>
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontFamily: "Inter,sans-serif", fontSize: 11, color: C.muted, marginBottom: 4 }}>
            <Dot c={C.evergreen} t={`${mix.pif || 0} PIF`} />
            <Dot c={C.meadow} t={`${mix.monthly || 0} Monthly`} />
            {mix.quarterly > 0 && <Dot c={C.sprout} t={`${mix.quarterly} Quarterly`} />}
            {mix.installments > 0 && <Dot c={C.mistDeep} t={`${mix.installments} Installments`} />}
          </div>
        </> : null}
        <div className="colhead" style={{ marginTop: 10 }}>Movement · MTD</div>
        <Row a="New Members" b="joined" v={newM?.value ?? "0"} />
        {renD && <Row a="Renewals Due" b="this month" v={renD.value} />}
      </div>
    </div>) });

  if (has("pipeline") && data.funnel) {
    const stages = data.funnel.stages || [];
    const max = Math.max(...stages.map((s) => s.v), 1);
    const total = stages.reduce((a, s) => a + s.v, 0);
    const d = (data.deck || []).find((x) => x.k === "pipeline");
    items.push({ key: "pipeline", icon: A.growth, name: "Recruiting", stat: d?.hero || String(total), line: "in pipeline", accent: C.meadow, render: () => (
      <div>
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: C.slate, marginBottom: 12 }}>
          {total} prospects across the recruiting pipeline{d?.salient ? ` · ${d.salient}` : ""}
        </div>
        {stages.map((s) => <Bar key={s.label} label={s.label} pct={(s.v / max) * 100} val={s.v} open={open} from={C.evergreen} to={C.meadow} wide />)}
        {data.funnel.footer && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: C.muted, borderTop: `1px solid ${C.hair}`, marginTop: 12, paddingTop: 11, lineHeight: 1.5 }}>{data.funnel.footer}</div>}
      </div>) });
  }
  if (has("renewals") && data.renewals) {
    const s = data.renewals.summary || {};
    const seg = s.segments || {};
    items.push({ key: "renewals", icon: A.reload, name: "Renewals · 90d", stat: s.value || "—", line: `${s.count || 0} members`, accent: C.mistDeep, render: () => (
      <div>
        {(seg.F != null) && <Row a="The Forum" b={`${seg.F} renewal${seg.F === 1 ? "" : "s"}`} v="" />}
        {(seg.IC != null) && <Row a="Inner Circle" b={`${seg.IC} renewal${seg.IC === 1 ? "" : "s"}`} v="" />}
        {(data.renewals.rows || []).slice(0, 6).map((r, i) => <Row key={i} a={r.name} b={r.month} v={r.value} />)}
        <div style={{ marginTop: 10 }}><Drill onClick={() => onOpen("renewal_book")}>View the {s.count} renewals</Drill></div>
      </div>) });
  }
  if (has("event") && data.event && !data.renewals) {
    const e = data.event;
    const pct = e.members ? Math.round((e.registered / e.members) * 100) : 0;
    items.push({ key: "event", icon: A.pin, name: "Next Event", stat: e.days_out != null ? `${e.days_out}d` : (e.where || "—"), line: "readiness", accent: C.mistDeep, render: () => (
      <div>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <Ring pct={pct} size={56} stroke={5} color={C.meadow}>{pct}%</Ring>
          <div>
            <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 15, fontWeight: 600, color: C.ink }}>{e.where}</div>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: C.muted }}>{e.title}{e.when ? ` · ${e.when}` : ""}</div>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: C.body, marginTop: 4 }}>{e.registered} of {e.members} registered{e.guests > 0 ? ` · +${e.guests} guests` : ""}</div>
          </div>
        </div>
        {e.unregistered > 0 && <div style={{ marginTop: 12 }}><Drill onClick={() => onOpen("unregistered")}>{e.unregistered} not yet registered</Drill></div>}
      </div>) });
  }
  return items.sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key));
}

/* ── Cash & Billing deck panels ─────────────────────────────────── */
function CashFlowPanel({ b, open, onOpen }) {
  const months = b.monthly || [];
  const max = Math.max(...months.map((m) => m.net), 1);
  const fc = b.forecast || {};
  return (
    <div>
      <div className="cashcols">
        {months.map((m) => {
          const totalH = m.net ? (m.net / max) * 100 : 0;
          const actShare = m.net ? (m.actual / m.net) * 100 : 0;
          const tip = m.mtd ? `${m.month}: ${usd(m.actual)} collected + ${usd(m.projected)} scheduled = ${usd(m.net)} expected — click for records`
            : m.is_projected ? `${m.month} · projected: ${usd(m.net)} — click for records` : `${m.month}: ${usd(m.net)} collected — click for records`;
          return (
            <button key={m.month} className="cashcol" title={tip} onClick={() => onOpen("forum_cashflow", { month: m.ym })}>
              <span className="cashcol-v">{m.net ? kc(m.net) : ""}</span>
              <div className="cashcol-track">
                <div className="cashcol-fill" style={{
                  height: open ? `${totalH}%` : 0,
                  background: m.is_projected ? C.parchment
                    : m.mtd ? `linear-gradient(180deg, ${C.sprout}, ${C.mist})`
                      : `linear-gradient(180deg, ${C.meadow}, ${C.evergreen})`,
                  border: m.is_projected ? `1px dashed ${C.meadow}` : "none", boxSizing: "border-box",
                }}>
                  {m.mtd && m.projected > 0 && <div style={{ height: `${100 - actShare}%`, background: C.parchment, borderBottom: `1px dashed ${C.meadow}`, boxSizing: "border-box", opacity: 0.85 }} />}
                </div>
              </div>
              <span className="cashcol-m">{m.month}{m.mtd ? " MTD" : ""}</span>
            </button>);
        })}
      </div>
      <div style={{ display: "flex", gap: 16, fontFamily: "Inter,sans-serif", fontSize: 10.5, color: C.muted, marginBottom: 10, flexWrap: "wrap" }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}><span style={{ width: 10, height: 10, borderRadius: 3, background: `linear-gradient(180deg, ${C.meadow}, ${C.evergreen})` }} />Actual</span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}><span style={{ width: 10, height: 10, borderRadius: 3, background: C.parchment, border: `1px dashed ${C.meadow}`, boxSizing: "border-box" }} />Projected from active subscriptions</span>
      </div>
      <div style={{ borderTop: `1px solid ${C.hair}`, paddingTop: 11, fontFamily: "Inter,sans-serif", fontSize: 11.5, color: C.slate }}>
        {usd(b.gross)} collected − {usd(b.refunded)} refunded = <b style={{ color: C.ink }}>{usd(b.net_cash)} net</b> · <Drill onClick={() => onOpen("forum_payments")}>{b.txn_count} transactions</Drill>
      </div>
      {(fc.next_90 > 0 || fc.rest_of_year > 0) && (
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: C.slate, marginTop: 7 }}>
          Projected inflow · <b style={{ color: C.ink }}>{kc(fc.next_30 || 0)}</b> next 30d · <b style={{ color: C.ink }}>{kc(fc.next_90 || 0)}</b> next 90d · <b style={{ color: C.ink }}>{kc(fc.rest_of_year || 0)}</b> rest of year · <Drill onClick={() => onOpen("forum_next30")}>upcoming charges</Drill>
        </div>
      )}
    </div>
  );
}
function moneyItems(b, open, onOpen, isBc) {
  const inst = (b.installments || [])[0];
  const streams = b.streams || [];
  const stotal = streams.reduce((a, x) => a + x.amount, 0) || 1;
  const scolors = [C.evergreen, C.meadow, C.mistDeep, C.sprout, C.muted];
  const arrPct = b.arr_book ? Math.round((b.run_rate / b.arr_book) * 100) : 0;
  return [
    { key: "cash", icon: A.cash, name: "Cash Flow", stat: kc(b.full_year), line: "full year + forecast", accent: C.meadow, render: () => <CashFlowPanel b={b} open={open} onOpen={onOpen} /> },
    { key: "recurring", icon: A.reload, name: "Recurring", stat: `${kc(b.mrr)}/mo`, line: "installments apart", accent: C.meadow, render: () => (
      <div className="cols">
        <div><div className="colhead">Perpetual · True MRR</div>
          <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 24, fontWeight: 700, color: C.ink, letterSpacing: "-.01em" }}>{usd(b.mrr)}<span style={{ fontSize: 12, color: C.muted, fontWeight: 500 }}>/mo · {b.perpetual_count} subs</span></div>
          <div style={{ marginTop: 8 }}><Drill onClick={() => onOpen("forum_mrr_subs")}>View subscriptions</Drill></div></div>
        <div><div className="colhead">Installment · Kept Out of MRR</div>
          {inst ? <>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: C.ink, fontWeight: 600 }}>{inst.name}</div>
            <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
              {[...Array(inst.total || 0)].map((_, i) => <span key={i} style={{ flex: 1, height: 7, borderRadius: 4,
                background: i < (inst.collected || 0) ? `linear-gradient(90deg, ${C.evergreen}, ${C.meadow})` : C.parchment,
                border: `1px solid ${i < (inst.collected || 0) ? C.meadow : C.hair}` }} />)}
            </div>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: C.slate, marginTop: 7 }}>{usd(inst.amount)} × {inst.total} · {inst.collected} collected · final {inst.final_date || "—"}</div>
          </> : <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: C.muted }}>No installment plans.</div>}</div>
      </div>) },
    { key: "arr", icon: A.crown, name: "ARR Bridge", stat: kc(b.arr_book), line: `book vs ${kc(b.run_rate)}`, accent: C.evergreen, render: () => (
      <div>
        <Bar label="Renewal Book" pct={100} val={kc(b.arr_book)} open={open} from={C.evergreen} to={C.meadow} wide />
        <Bar label="Run-Rate" pct={arrPct} val={kc(b.run_rate)} open={open} from={C.meadow} to={C.sprout} wide />
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: C.slate, borderTop: `1px solid ${C.hair}`, marginTop: 12, paddingTop: 11, lineHeight: 1.5 }}>
          The <b style={{ color: C.ink }}>{usd(b.arr_book - b.run_rate)}</b> gap is PIF and financed-annual members off monthly billing — why the book is the headline, run-rate the billing slice. <Drill onClick={() => onOpen("renewal_book")}>memberships</Drill>
        </div>
      </div>) },
    { key: "streams", icon: A.line, name: "By Stream", stat: `${streams.length} streams`, line: "dues · events", accent: C.mistDeep, render: () => (
      <div>
        <div className="stack">
          {streams.map((x, i) => <div key={x.key} className="stack-seg" style={{ width: open ? `${(x.amount / stotal) * 100}%` : 0, background: scolors[i % scolors.length] }} />)}
        </div>
        {streams.map((x, i) => (
          <div key={x.key} className="row" role="button" tabIndex={0} style={{ cursor: "pointer" }}
            onClick={() => onOpen("forum_streams", { stream: x.key })}
            onKeyDown={(e) => (e.key === "Enter") && onOpen("forum_streams", { stream: x.key })}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}><span style={{ width: 9, height: 9, borderRadius: 3, background: scolors[i % scolors.length] }} />{x.label}</span>
            <span style={{ color: C.muted }}>{x.pct}%</span><b>{usd(x.amount)}</b></div>
        ))}
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: C.muted, marginTop: 10 }}>Classified within {isBc ? "beCollective" : "The Forum"} · sums to net cash {usd(stotal)}</div>
      </div>) },
  ];
}

/* ── styles ─────────────────────────────────────────────────────── */
const CSS = `
  .fe { position:relative; margin:-26px; padding:26px 26px 44px; min-height:calc(100vh - 60px); color:${C.ink};
    background:
      radial-gradient(1200px 540px at 84% -8%, ${C.mist}55, transparent 60%),
      radial-gradient(1000px 500px at -8% 6%, ${C.sprout}44, transparent 55%),
      linear-gradient(180deg, ${C.pageTop}, ${C.page} 44%);
    --sh-rest:0 1px 2px rgba(0,46,44,.05), 0 1px 3px rgba(0,46,44,.04);
    --sh-raise:0 2px 6px rgba(0,46,44,.06), 0 12px 26px rgba(0,46,44,.05);
    --sh-lift:0 8px 16px rgba(0,46,44,.09), 0 26px 52px rgba(0,46,44,.11);
    --hl:inset 0 1px 0 rgba(255,255,255,.65); }
  .fe * { box-sizing:border-box; }
  .fe .wrap { max-width:1060px; margin:0 auto; }
  .fe-head { display:flex; align-items:center; gap:14px; margin-bottom:18px; }
  .fe-bar { width:4px; height:30px; border-radius:2px; background:linear-gradient(180deg,${C.meadow},${C.evergreen}); }
  .fe-title { font-family:Poppins,sans-serif; font-size:23px; font-weight:700; letter-spacing:-.015em; color:${C.ink}; }
  .fe-sub { font-family:Inter,sans-serif; font-size:12.5px; color:${C.muted}; }
  .fe-syncpill { margin-left:auto; display:inline-flex; align-items:center; gap:7px; font-family:Inter,sans-serif; font-size:11px; font-weight:600; color:${C.slate};
    background:${C.surface}; border:1px solid ${C.hair}; border-radius:99px; padding:6px 12px; box-shadow:var(--hl); }
  .fe-syncdot { width:7px; height:7px; border-radius:99px; background:${C.meadow}; box-shadow:0 0 0 3px ${C.meadow}22; animation:breathe 2.4s ease-in-out infinite; }
  @media (max-width:640px){ .fe-syncpill { display:none; } }

  .card { background:linear-gradient(180deg,#fff,#fffdfb); border:1px solid ${C.hair}; border-radius:16px; box-shadow:var(--sh-raise), var(--hl); }
  @keyframes rise { from { opacity:0; transform:translateY(14px); } to { opacity:1; transform:none; } }
  .enter { animation:rise .55s cubic-bezier(.22,1,.36,1) both; }

  .glow { position:relative; isolation:isolate; }
  .glow::before { content:""; position:absolute; inset:0; border-radius:inherit; pointer-events:none;
    background:radial-gradient(180px circle at var(--mx,50%) var(--my,50%), var(--glow), transparent 62%); opacity:0; transition:opacity .3s ease; z-index:0; }
  .glow:hover::before { opacity:1; }
  .glow > * { position:relative; z-index:1; }
  .lift { transition:transform .3s cubic-bezier(.34,1.25,.64,1), box-shadow .3s ease; }
  .lift:hover { transform:translateY(-3px); }
  .lift:active { transform:translateY(-1px) scale(.995); }

  .hero { border-radius:20px; padding:22px 24px; overflow:hidden; color:${C.heroText}; display:flex; flex-direction:column;
    background:
      radial-gradient(620px 320px at 12% -20%, rgba(103,165,170,.22), transparent 60%),
      repeating-linear-gradient(118deg, rgba(255,255,255,.022) 0 2px, transparent 2px 10px),
      linear-gradient(152deg, ${C.heroMid}, ${C.evergreen} 52%, ${C.heroDeep});
    box-shadow:0 10px 24px rgba(0,33,31,.28), 0 30px 64px rgba(0,33,31,.32), inset 0 1px 0 rgba(255,255,255,.10); border:1px solid rgba(0,33,31,.5); }
  .hero-eyebrow { display:flex; align-items:center; gap:8px; font-family:Poppins,sans-serif; font-size:12px; font-weight:700; letter-spacing:.005em; color:rgba(217,232,225,.8); margin-bottom:16px; }
  .hero-dot { width:6px; height:6px; border-radius:99px; background:${C.mistDeep}; box-shadow:0 0 0 3px rgba(103,165,170,.28); animation:breathe 2.4s ease-in-out infinite; }
  .hero-range { margin-left:auto; font-family:Inter,sans-serif; font-weight:600; font-size:11px; color:rgba(217,232,225,.6); background:rgba(255,255,255,.06); border:1px solid rgba(255,255,255,.08); border-radius:99px; padding:3px 11px; }
  .hero-body { display:grid; grid-template-columns:minmax(0,1.22fr) minmax(0,1fr); gap:22px; flex:1; align-items:stretch; }
  @media (max-width:820px){ .hero-body{ grid-template-columns:1fr; gap:18px; } }
  .hero-primary { display:flex; flex-direction:column; }
  .hero-plabel { font-family:Inter,sans-serif; font-size:12px; color:${C.heroMut}; font-weight:500; }
  .hero-pval { font-family:Poppins,sans-serif; font-weight:700; font-size:58px; line-height:.98; letter-spacing:-.03em; margin-top:6px;
    background:linear-gradient(160deg, #fff, ${C.sprout} 70%, ${C.mistDeep}); -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; font-variant-numeric:tabular-nums; }
  .hero-chart { margin-top:14px; }
  .hero-months { display:flex; justify-content:space-between; margin-top:5px; font-family:Inter,sans-serif; font-size:10px; color:rgba(217,232,225,.45); }
  .hero-psub { font-family:Inter,sans-serif; font-size:11.5px; color:${C.heroMut}; margin-top:auto; padding-top:16px; }
  .hero-psub b { color:${C.heroText}; font-weight:600; }
  .hero-drill { cursor:pointer; }
  .hero-side { display:flex; flex-direction:column; gap:1px; background:rgba(255,255,255,.07); border-radius:14px; overflow:hidden; border:1px solid rgba(255,255,255,.07); }
  .hero-scell { background:linear-gradient(160deg, rgba(255,255,255,.04), rgba(255,255,255,.01)); padding:14px 15px; flex:1; display:flex; flex-direction:column; justify-content:center; cursor:pointer; }
  .hero-sval { font-family:Poppins,sans-serif; font-weight:700; font-size:24px; letter-spacing:-.01em; color:${C.heroText}; margin-top:9px; font-variant-numeric:tabular-nums; }
  .hero-sval span { font-size:12px; font-weight:500; color:${C.heroMut}; margin-left:2px; }
  .hero-slabel { font-family:Inter,sans-serif; font-size:11px; color:rgba(217,232,225,.72); margin-top:3px; font-weight:500; }
  .hero-ssub { font-family:Inter,sans-serif; font-size:10px; color:rgba(217,232,225,.42); margin-top:2px; }

  /* beCollective Cash card — official petal ribbed gradient (light surface, dark ink). */
  .hero.bc { color:${C.ink};
    background-color:${C.petal};
    background-image:url(/brand/RibbedGradient_Petal.jpg);
    background-size:cover; background-position:center; background-blend-mode:multiply;
    box-shadow:0 2px 6px ${C.evergreen}1A, 0 16px 38px ${C.evergreen}1F, inset 0 1px 0 rgba(255,255,255,.35);
    border:1px solid ${C.petalDeep}55; }
  .hero.bc .hero-eyebrow { color:${C.evergreen}; }
  .hero.bc .hero-dot { background:${C.poppy}; box-shadow:0 0 0 3px ${C.poppy}33; }
  .hero.bc .hero-range { color:${C.slate}; background:rgba(255,255,255,.5); border:1px solid ${C.evergreen}1A; }
  .hero.bc .hero-plabel { color:${C.slate}; }
  .hero.bc .hero-pval { background:linear-gradient(160deg, ${C.evergreen}, ${C.meadow} 78%); -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; }
  .hero.bc .hero-months { color:${C.muted}; }
  .hero.bc .hero-psub { color:${C.slate}; }
  .hero.bc .hero-psub b { color:${C.ink}; }
  .hero.bc .hero-side { background:rgba(255,255,255,.42); border:1px solid ${C.evergreen}14; }
  .hero.bc .hero-scell { background:linear-gradient(160deg, rgba(255,255,255,.5), rgba(255,255,255,.2)); }
  .hero.bc .hero-sval { color:${C.ink}; }
  .hero.bc .hero-sval span { color:${C.muted}; }
  .hero.bc .hero-slabel { color:${C.slate}; }
  .hero.bc .hero-ssub { color:${C.muted}; }

  .topgrid { display:grid; grid-template-columns:minmax(0,.82fr) minmax(0,1.18fr); gap:14px; align-items:stretch; margin-bottom:14px; }
  @media (max-width:900px){ .topgrid{ grid-template-columns:1fr; } }
  .lens { font-family:Inter,sans-serif; font-size:10px; font-weight:600; color:${C.slate}; background:${C.mist}66; border:1px solid ${C.hair}; border-radius:99px; padding:2px 8px; margin-left:8px; }
  .fin { position:relative; } .fin::before { content:""; position:absolute; left:0; top:16px; bottom:16px; width:3px; border-radius:0 3px 3px 0; background:linear-gradient(180deg,${C.evergreen},${C.meadow}); }
  .kick { display:flex; align-items:center; gap:8px; font-family:Poppins,sans-serif; font-size:12px; font-weight:700; letter-spacing:.005em; color:${C.slate}; margin-bottom:14px; }
  .kb { width:3px; height:13px; border-radius:2px; background:${C.evergreen}; } .kb-m { background:${C.meadow}; }
  .src { margin-left:auto; font-family:Inter,sans-serif; font-size:10.5px; font-weight:600; color:${C.slate}; background:${C.parchment}; border:1px solid ${C.hair}; border-radius:6px; padding:2px 8px; }
  .pnl-card { padding:20px 22px 20px 26px; }
  .prow { display:flex; justify-content:space-between; align-items:baseline; padding:8px 0; font-family:Inter,sans-serif; font-size:12.5px; color:${C.body}; }
  .prow b { font-family:Poppins,sans-serif; font-weight:500; color:${C.ink}; font-variant-numeric:tabular-nums; }
  .prow.sub { border-top:1px solid ${C.hair}; font-weight:600; } .prow.sub span { font-weight:600; }
  .prow.ded span, .prow.ded b { color:${C.slate}; font-weight:400; }
  .prow.tot { border-top:2px solid ${C.ink}; margin-top:2px; padding-top:11px; }
  .prow.tot span { font-weight:700; color:${C.ink}; } .prow.tot b { font-weight:700; font-size:15px; }
  .pnl-note { font-family:Inter,sans-serif; font-size:11px; color:${C.muted}; line-height:1.5; border-top:1px solid ${C.hair}; margin-top:12px; padding-top:12px; }
  .pnl-note b { color:${C.body}; font-weight:600; }

  .pulse { position:relative; background:linear-gradient(180deg,#fff,${C.parchment}); border:1px solid ${C.hair}; border-radius:16px; padding:15px 18px 18px 22px; margin-bottom:18px; box-shadow:var(--sh-rest), var(--hl); }
  .pulse::before { content:""; position:absolute; left:0; top:16px; bottom:16px; width:3px; border-radius:0 3px 3px 0; background:linear-gradient(180deg,${C.meadow},${C.sprout}); }
  .pulse-head { display:flex; align-items:center; gap:8px; font-family:Poppins,sans-serif; font-size:12px; font-weight:700; letter-spacing:.005em; color:${C.slate}; margin-bottom:14px; }
  .pulse-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; }
  @media (max-width:820px){ .pulse-grid{ grid-template-columns:1fr 1fr; } }
  @media (max-width:520px){ .pulse-grid{ grid-template-columns:1fr; } }
  .ptile { background:#fff; border:1px solid ${C.hair}; border-radius:12px; padding:13px 14px; box-shadow:var(--sh-rest), var(--hl); cursor:default; }
  .ptile.click { cursor:pointer; }
  .ptile:hover { box-shadow:var(--sh-lift), var(--hl); border-color:#E3D9C9; }
  .ptile-top { display:flex; align-items:center; gap:8px; margin-bottom:11px; }
  .ptile-label { font-family:Inter,sans-serif; font-size:11.5px; color:${C.slate}; font-weight:600; }
  .ptile-row { display:flex; align-items:flex-end; justify-content:space-between; gap:8px; }
  .ptile-v { font-family:Poppins,sans-serif; font-size:27px; font-weight:700; color:${C.ink}; letter-spacing:-.02em; font-variant-numeric:tabular-nums; }
  .ptile-s { font-family:Inter,sans-serif; font-size:10.5px; color:${C.muted}; margin-top:3px; }

  .watchstrip { display:flex; align-items:center; gap:12px 16px; flex-wrap:wrap; margin-bottom:20px; }
  .watchpair { display:inline-flex; align-items:center; gap:10px; }
  .flag { display:inline-flex; align-items:center; gap:8px; background:${C.flagBg}; border-radius:9px; padding:7px 13px; font-family:Inter,sans-serif; font-size:12px; font-weight:600; color:${C.flagText}; box-shadow:inset 0 1px 0 rgba(255,255,255,.5), 0 1px 2px rgba(181,121,42,.12); }
  .flag-dot { width:7px; height:7px; border-radius:99px; background:${C.flagDot}; box-shadow:0 0 0 3px ${C.flagDot}44; animation:breathe 2s ease-in-out infinite; }
  .ghost { display:inline-flex; align-items:center; gap:6px; background:${C.surface}; border:1px solid ${C.hair}; border-radius:9px; padding:6px 12px; cursor:pointer; font-family:Poppins,sans-serif; font-size:11.5px; font-weight:600; color:${C.slate}; box-shadow:var(--sh-rest), var(--hl); }
  .ghost:hover { border-color:${C.meadow}; color:${C.meadow}; box-shadow:var(--sh-lift), var(--hl); }

  .sec { background:#fff; border:1px solid ${C.hair}; border-radius:16px; margin-bottom:13px; box-shadow:var(--sh-rest), var(--hl); transition:box-shadow .35s cubic-bezier(.22,1,.36,1), border-color .35s ease; }
  .sec.on { box-shadow:var(--sh-lift), var(--hl); border-color:#E3D9C9; }
  .sec-head { display:flex; align-items:center; gap:13px; width:100%; background:none; border:none; padding:16px 20px; cursor:pointer; text-align:left; border-radius:16px; transition:background .25s ease; }
  .sec-head:hover { background:linear-gradient(180deg,#fff, ${C.parchment}88); }
  .chip { display:inline-flex; align-items:center; justify-content:center; border-radius:10px; flex-shrink:0; transition:transform .3s cubic-bezier(.34,1.4,.64,1); }
  .sec-head:hover .chip { transform:scale(1.06); }
  .sec-title { font-family:Poppins,sans-serif; font-size:15.5px; font-weight:600; color:${C.ink}; letter-spacing:-.01em; white-space:nowrap; }
  .sec-sum { font-family:Inter,sans-serif; font-size:12.5px; color:${C.muted}; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .sec-watch { display:inline-flex; align-items:center; gap:6px; font-family:Inter,sans-serif; font-size:11px; font-weight:600; color:${C.flagText}; background:${C.flagBg}; border-radius:7px; padding:3px 9px; white-space:nowrap; }
  .wd { width:6px; height:6px; border-radius:99px; background:${C.flagDot}; animation:breathe 2s ease-in-out infinite; }
  .chev-wrap { width:26px; height:26px; border-radius:8px; display:inline-flex; align-items:center; justify-content:center; background:${C.parchment}; border:1px solid ${C.hair}; }
  .chev-wrap span { transition:transform .34s cubic-bezier(.34,1.35,.64,1); }
  .sec-body { padding:2px 22px 22px; animation:secReveal .34s cubic-bezier(.22,1,.36,1); }
  @keyframes secReveal { from { transform:translateY(-6px); } to { transform:none; } }
  .sec-sub { font-family:Inter,sans-serif; font-size:11.5px; color:${C.muted}; margin-bottom:14px; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
  .live { display:inline-flex; align-items:center; gap:5px; font-weight:600; color:${C.meadow}; background:${C.meadow}14; border-radius:6px; padding:2px 8px; }
  .live-dot { width:6px; height:6px; border-radius:99px; background:${C.meadow}; animation:breathe 2.2s ease-in-out infinite; }

  .deck { display:grid; gap:10px; }
  .dcard { background:linear-gradient(160deg,#fff,${C.parchment}); border:1px solid ${C.hair}; border-radius:12px; padding:12px 14px 13px; cursor:pointer; text-align:left; overflow:hidden; box-shadow:var(--sh-rest); transition:transform .28s cubic-bezier(.34,1.25,.64,1), box-shadow .28s ease, background .25s ease; }
  .dcard.off:hover { transform:translateY(-3px); box-shadow:var(--sh-lift); }
  .dcard.on { background:#fff; border-radius:12px 12px 0 0; border-bottom-color:#fff; box-shadow:var(--sh-raise); }
  .dcard-accent { position:absolute; top:0; left:0; right:0; height:3px; border-radius:3px 3px 0 0; z-index:2; }
  .dhead { display:flex; align-items:center; gap:7px; margin-bottom:3px; }
  .dname { font-family:Poppins,sans-serif; font-size:12px; font-weight:600; color:${C.slate}; }
  .dstat { font-family:Poppins,sans-serif; font-size:20px; font-weight:700; color:${C.ink}; margin-top:5px; letter-spacing:-.01em; font-variant-numeric:tabular-nums; }
  .dline { font-family:Inter,sans-serif; font-size:10px; color:${C.muted}; margin-top:3px; }
  .dcard.on::after { content:""; position:absolute; left:0; right:0; bottom:-1px; height:1px; background:#fff; z-index:3; }
  .focus { background:#fff; border:1px solid ${C.hair}; border-top:none; border-radius:0 0 12px 12px; padding:18px 20px; box-shadow:var(--sh-raise); margin-top:-1px; }
  @keyframes fadeUp { from{ opacity:0; transform:translateY(6px);} to{ opacity:1; transform:none;} }
  .fbody { animation:fadeUp .32s cubic-bezier(.22,1,.36,1); }

  .cols { display:grid; grid-template-columns:1fr 1fr; gap:26px; }
  @media (max-width:640px){ .cols{ grid-template-columns:1fr; } }
  .colhead { font-family:Poppins,sans-serif; font-size:11.5px; font-weight:700; letter-spacing:.005em; color:${C.slate}; margin-bottom:8px; }
  .row { display:flex; align-items:baseline; gap:10px; padding:7px 0; font-family:Inter,sans-serif; font-size:12.5px; color:${C.body}; border-top:1px solid ${C.parchment}; }
  .row span:first-child { flex:1; }
  .row b { font-family:Poppins,sans-serif; font-weight:600; color:${C.ink}; font-variant-numeric:tabular-nums; }

  .bar { display:flex; align-items:center; gap:12px; padding:5px 0; }
  .bar-l { width:96px; font-family:Inter,sans-serif; font-size:12px; color:${C.slate}; }
  .bt { flex:1; height:16px; border-radius:6px; overflow:hidden; box-shadow:inset 0 1px 2px rgba(0,46,44,.06); }
  .bt-fill { height:100%; border-radius:6px; transition:width .8s cubic-bezier(.22,1,.36,1); box-shadow:inset 0 1px 0 rgba(255,255,255,.25); }
  .bar b { min-width:34px; text-align:right; font-family:Poppins,sans-serif; font-size:12.5px; font-weight:600; font-variant-numeric:tabular-nums; }

  .cashcols { display:flex; align-items:flex-end; gap:8px; height:132px; margin-bottom:10px; }
  .cashcol { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:flex-end; gap:6px; height:100%; background:none; border:none; padding:0; cursor:pointer; }
  .cashcol-v { font-family:Poppins,sans-serif; font-size:9.5px; font-weight:600; color:${C.ink}; min-height:12px; }
  .cashcol-track { width:100%; max-width:44px; flex:1; display:flex; align-items:flex-end; background:${C.parchment}; border-radius:7px; overflow:hidden; box-shadow:inset 0 1px 2px rgba(0,46,44,.05); }
  .cashcol-fill { width:100%; border-radius:7px 7px 0 0; transition:height .8s cubic-bezier(.22,1,.36,1); box-shadow:inset 0 1px 0 rgba(255,255,255,.3); overflow:hidden; }
  .cashcol:hover .cashcol-fill { filter:brightness(.94); }
  .cashcol-m { font-family:Inter,sans-serif; font-size:9.5px; color:${C.muted}; }

  .stack { display:flex; height:14px; border-radius:7px; overflow:hidden; gap:2px; margin-bottom:12px; box-shadow:inset 0 1px 2px rgba(0,46,44,.06); }
  .stack-seg { transition:width .8s cubic-bezier(.22,1,.36,1); box-shadow:inset 0 1px 0 rgba(255,255,255,.2); min-width:2px; }

  .drill { display:inline-flex; align-items:center; gap:4px; color:${C.meadow}; font-weight:600; cursor:pointer; font-size:12px; }
  .drill:hover { text-decoration:underline; }

  /* operational-refinement (v9) motion + hover */
  @keyframes fvGrowX { from { transform:scaleX(0); } to { transform:scaleX(1); } }
  @keyframes fvGrowY { from { transform:scaleY(0); } to { transform:scaleY(1); } }
  .fe .fvGrow { animation:fvGrowX .7s cubic-bezier(.22,1,.36,1) both; }
  .fe .fvGrowY { animation:fvGrowY .7s cubic-bezier(.22,1,.36,1) both; }
  .fe .fvRow { transition:background .15s ease; }
  .fe .fvRow:hover { background:${C.parchment}; }
  .fe .ptile { transition:transform .18s ease, box-shadow .18s ease, border-color .18s ease; }
  .fe .ptile:hover { transform:translateY(-2px); box-shadow:0 8px 22px rgba(0,46,44,.09); border-color:${C.sprout}; }
  .fe .oproster { transition:filter .15s ease; }
  .fe .oproster:hover { filter:brightness(1.12); }
  .opgrid4 { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; }
  .opgrowgrid { display:grid; grid-template-columns:minmax(0,1fr) 208px; gap:16px; align-items:stretch; }
  .opfacts { display:grid; grid-template-columns:1.05fr 1fr; gap:22px; align-items:start; }
  .opmotions { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:16px; }
  .opcb { display:grid; grid-template-columns:minmax(0,1.4fr) minmax(0,1fr); gap:16px; }
  @media (max-width: 900px) {
    .opgrid4 { grid-template-columns:repeat(2,1fr); }
    .opgrowgrid, .opfacts, .opmotions, .opcb { grid-template-columns:1fr; }
  }

  @keyframes drawline { from { stroke-dashoffset:1; } to { stroke-dashoffset:0; } }
  @keyframes areaIn { from { opacity:0; } to { opacity:1; } }
  .spark-line { stroke-dasharray:1; stroke-dashoffset:0; animation:drawline 1s cubic-bezier(.4,0,.2,1) both; }
  .spark-area { animation:areaIn .9s ease both; animation-delay:.2s; }
  @keyframes ringdraw { from { stroke-dashoffset:1; } to { stroke-dashoffset:var(--to); } }
  .ring-arc { stroke-dashoffset:var(--to); animation:ringdraw 1.1s cubic-bezier(.4,0,.2,1) both; }
  @keyframes breathe { 0%,100%{ opacity:.5; transform:scale(.85);} 50%{ opacity:1; transform:scale(1.05);} }
  .fe button:focus-visible, .fe [role=button]:focus-visible { outline:2px solid ${C.meadow}; outline-offset:2px; }

  @media (prefers-reduced-motion: reduce) {
    .fe *, .fe *::before, .fe *::after { animation:none !important; transition:none !important; }
    .fe .spark-line { stroke-dashoffset:0 !important; } .fe .spark-area { opacity:1 !important; }
    .fe .ring-arc { stroke-dashoffset:var(--to) !important; }
  }
`;

/* deck slot registries — what the Members deck can show per program */
const DECK_SLOTS = [{ k: "pipeline" }, { k: "renewals" }, { k: "event" }];
export const BC_DECK_SLOTS = [{ k: "pipeline" }, { k: "event" }];

/* ══ Operational Refinement (v9) — layout ported from SPEC-forum-operational,
   styled to the existing Forum identity (same C tokens, Poppins/Inter, tabular
   figures). Color law: zero poppy; meadow = calm, amber = needs-attention,
   daffodil = the one action marker, evergreen = structure. ═══════════════ */
const PLAN_LABEL = { pif: "PIF", monthly: "Monthly", quarterly: "Quarterly", installments: "Installments" };
const PLAN_DOT = { pif: C.evergreen, monthly: C.meadow, quarterly: C.sprout, installments: C.mistDeep };

function Ic({ name, size = 16, color = "currentColor", sw = 1.7 }) {
  const p = {
    members: <><path d="M16 19v-1a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1" /><circle cx="9" cy="7" r="3.4" /><path d="M22 19v-1a4 4 0 0 0-3-3.87" /><path d="M16 3.3a4 4 0 0 1 0 7.4" /></>,
    growth: <><polyline points="3 17 9 11 13 15 21 6" /><polyline points="15 6 21 6 21 12" /></>,
    renew: <><path d="M3.5 12a8.5 8.5 0 0 1 14.5-6L21 8.5" /><polyline points="21 3 21 8.5 15.5 8.5" /><path d="M20.5 12a8.5 8.5 0 0 1-14.5 6L3 15.5" /><polyline points="3 21 3 15.5 8.5 15.5" /></>,
    event: <><path d="M21 10.5c0 6.6-9 12.5-9 12.5S3 17.1 3 10.5a9 9 0 0 1 18 0Z" /><circle cx="12" cy="10.2" r="2.7" /></>,
    ext: <><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /><polyline points="15 3 21 3 21 9" /><line x1="10.5" y1="13.5" x2="21" y2="3" /></>,
    alert: <><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" /><line x1="12" y1="9" x2="12" y2="13.5" /><line x1="12" y1="17" x2="12.01" y2="17" /></>,
    up: <><line x1="12" y1="19" x2="12" y2="6" /><polyline points="6 12 12 6 18 12" /></>,
    resign: <><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5Z" /></>,
    arrow: <><line x1="5" y1="12" x2="19" y2="12" /><polyline points="12 5 19 12 12 19" /></>,
  }[name];
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0, display: "block" }}>{p}</svg>;
}
function IcChip({ name, tint = C.slate, size = 28, icon = 14, bg }) {
  return <span style={{ width: size, height: size, borderRadius: 9, background: bg || "rgba(97,131,94,.10)", display: "inline-flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}><Ic name={name} size={icon} color={tint} /></span>;
}
function opSpark(data, w, h, pad = 3) {
  const min = Math.min(...data), max = Math.max(...data), span = max - min || 1;
  const x = (i) => pad + (i / (data.length - 1)) * (w - 2 * pad);
  const y = (v) => h - pad - ((v - min) / span) * (h - 2 * pad);
  const line = data.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  return { line, area: `${line} L${x(data.length - 1).toFixed(1)} ${h} L${x(0).toFixed(1)} ${h} Z`, lx: x(data.length - 1), ly: y(data[data.length - 1]) };
}
function OpSpark({ data, w = 104, h = 38, color = C.meadow }) {
  if (!data || data.length < 2) return null;
  const { line, area, lx, ly } = opSpark(data, w, h);
  const id = "os" + color.replace(/[^a-z0-9]/gi, "");
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} style={{ display: "block" }}>
      <defs><linearGradient id={id} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={color} stopOpacity="0.16" /><stop offset="1" stopColor={color} stopOpacity="0" /></linearGradient></defs>
      <path d={area} fill={`url(#${id})`} />
      <path className="spark-line" d={line} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={lx} cy={ly} r="2.6" fill={color} />
    </svg>
  );
}
function OpFunnel({ stages, color = C.evergreen, w = 112 }) {
  const max = Math.max(...stages, 1);
  return <div style={{ display: "flex", flexDirection: "column", gap: 4, width: w }}>{stages.map((v, i) => <div key={i} className="fvGrow" style={{ height: 6, borderRadius: 3, width: `${Math.max(14, (v / max) * 100)}%`, background: color, opacity: 1 - i * 0.17, transformOrigin: "left" }} title={`${v}`} />)}</div>;
}
function DuoBar({ auto, needsYou, h = 9 }) {
  const tot = auto + needsYou || 1;
  return (
    <div style={{ display: "flex", height: h, borderRadius: 99, overflow: "hidden", background: C.hair }}>
      <div className="fvGrow" style={{ width: `${(auto / tot) * 100}%`, background: C.meadow, transformOrigin: "left" }} title="auto-renews" />
      <div className="fvGrow" style={{ width: `${(needsYou / tot) * 100}%`, background: C.amber, transformOrigin: "left" }} title="needs a touch" />
    </div>
  );
}
function OpRing({ pct, size = 48, sw = 5, color = C.meadow }) {
  const r = (size - sw) / 2, c = 2 * Math.PI * r;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={C.hair} strokeWidth={sw} />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={sw} strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - pct / 100)} transform={`rotate(-90 ${size / 2} ${size / 2})`} style={{ transition: "stroke-dashoffset .9s cubic-bezier(.22,1,.36,1)" }} />
      <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle" style={{ fontFamily: "Poppins,sans-serif", fontSize: size * 0.28, fontWeight: 600, fill: C.ink }}>{pct}%</text>
    </svg>
  );
}
const opBig = { fontFamily: "Poppins,sans-serif", fontWeight: 600, color: C.ink, fontVariantNumeric: "tabular-nums", lineHeight: 1 };
function PulseTile({ children, onClick }) {
  return <div onClick={onClick} role={onClick ? "button" : undefined} tabIndex={onClick ? 0 : undefined} onKeyDown={(e) => onClick && (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onClick())} className="ptile" style={{ background: C.surface, border: `1px solid ${C.hair}`, borderRadius: 15, padding: "15px 16px 14px", cursor: onClick ? "pointer" : "default", display: "flex", flexDirection: "column", gap: 11, minWidth: 0 }}>{children}</div>;
}
function TileHead({ icon, tint, label, link }) {
  return <div style={{ display: "flex", alignItems: "center", gap: 9 }}><IcChip name={icon} tint={tint} size={28} icon={14} /><span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, fontWeight: 600, color: C.slate }}>{label}</span>{link && <span style={{ marginLeft: "auto", opacity: .5 }}><Ic name="ext" size={13} color={C.muted} /></span>}</div>;
}
function PulseStrip({ pulse, onOpen, rosterKey = "forum_roster" }) {
  const m = pulse.members, pp = pulse.pipeline, rn = pulse.renewals, ev = pulse.event;
  return (
    <div className="opgrid4">
      <PulseTile onClick={() => onOpen(rosterKey)}>
        <TileHead icon="members" tint={C.meadow} label="Active Members" link />
        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            <span style={{ ...opBig, fontSize: 34 }}>{m.value}</span>
            {m.delta ? <span style={{ display: "inline-flex", alignItems: "center", gap: 2, fontFamily: "Inter,sans-serif", fontSize: 11.5, fontWeight: 700, color: m.delta >= 0 ? C.meadow : C.amber }}><Ic name="up" size={11} color={m.delta >= 0 ? C.meadow : C.amber} sw={2.4} />{Math.abs(m.delta)}</span> : null}
          </div>
          {m.spark && m.spark.length > 1 && <OpSpark data={m.spark} w={104} h={38} color={C.meadow} />}
        </div>
      </PulseTile>

      <PulseTile onClick={pp ? () => onOpen("registered") : undefined}>
        <TileHead icon="growth" tint={C.evergreen} label="Recruiting Pipeline" />
        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 12 }}>
          <span style={{ ...opBig, fontSize: 34 }}>{pp ? pp.value : "—"}</span>
          {pp && <OpFunnel stages={pp.stages} color={C.evergreen} w={112} />}
        </div>
      </PulseTile>

      <PulseTile>
        <TileHead icon="renew" tint={C.meadow} label="Renewals · 90d" />
        <div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 9 }}>
            <span style={{ ...opBig, fontSize: 30 }}>{kc(rn.book)}</span>
            <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: C.muted }}>{rn.count} due</span>
          </div>
          <DuoBar auto={rn.auto} needsYou={rn.needsYou} />
          <div style={{ display: "flex", alignItems: "center", gap: 5, marginTop: 8 }}>
            <span style={{ width: 6, height: 6, borderRadius: 99, background: C.flagDot, border: `1px solid ${C.amber}` }} />
            <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 12, fontWeight: 600, color: C.amber, fontVariantNumeric: "tabular-nums" }}>{kc(rn.needsYou)}</span>
            <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: C.muted }}>needs a touch</span>
          </div>
        </div>
      </PulseTile>

      <PulseTile>
        <TileHead icon="event" tint={C.evergreen} label="Event Readiness" />
        {ev ? (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 3 }}><span style={{ ...opBig, fontSize: 34 }}>{ev.days != null ? ev.days : "—"}</span>{ev.days != null && <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 17, fontWeight: 500, color: C.muted }}>d</span>}</div>
            <OpRing pct={ev.pct} size={48} />
          </div>
        ) : <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: C.muted }}>No event configured.</div>}
      </PulseTile>
    </div>
  );
}
function ActionRowV9({ action, onOpen }) {
  if (!action || !action.failed) return null;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
      <div style={{ display: "inline-flex", alignItems: "center", gap: 9, padding: "9px 14px", borderRadius: 10, background: C.flagBg, border: "1px solid rgba(181,121,42,.22)" }}>
        <span style={{ width: 7, height: 7, borderRadius: 99, background: C.flagDot, border: `1px solid ${C.amber}` }} /><Ic name="alert" size={14} color={C.amber} />
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: C.flagText }}><strong style={{ fontWeight: 700 }}>{action.failed} failed charge{action.failed === 1 ? "" : "s"}</strong> this month · {kc(action.recover)} to recover</span>
      </div>
      <button onClick={() => onOpen("forum_failed_payments")} style={{ display: "inline-flex", alignItems: "center", gap: 7, padding: "9px 15px", borderRadius: 10, background: C.surface, border: `1px solid ${C.hair}`, fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600, color: C.ink, cursor: "pointer" }}>Review failed charges <Ic name="ext" size={13} color={C.slate} /></button>
    </div>
  );
}

/* ── Growth ── */
function GrowthChart({ g }) {
  const { months, total, joined, lost } = g;
  const N = total.length;
  if (!N) return null;
  const x = (i) => 20 + i * (580 / (N - 1 || 1));
  const lo = Math.min(...total) - 1, hi = Math.max(...total) + 1;
  const yT = (v) => 112 - ((v - lo) / (hi - lo || 1)) * 92;
  const line = total.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${yT(v).toFixed(1)}`).join(" ");
  const area = `${line} L${x(N - 1).toFixed(1)} 120 L${x(0).toFixed(1)} 120 Z`;
  const maxJ = Math.max(...joined, 1), maxL = Math.max(...lost, 1), axis = 172;
  return (
    <svg width="100%" viewBox="0 0 620 210" preserveAspectRatio="xMidYMid meet" style={{ display: "block", width: "100%", maxHeight: 220 }}>
      <defs><linearGradient id="opggrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={C.meadow} stopOpacity=".18" /><stop offset="1" stopColor={C.meadow} stopOpacity="0" /></linearGradient></defs>
      <line x1="20" y1="120" x2="600" y2="120" stroke={C.hair} strokeWidth="1" />
      <path className="spark-area" d={area} fill="url(#opggrad)" />
      <path d={line} fill="none" stroke={C.meadow} strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={x(N - 1)} cy={yT(total[N - 1])} r="3.6" fill={C.meadow} />
      <text x={x(N - 1)} y={yT(total[N - 1]) - 10} textAnchor="middle" style={{ fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600, fill: C.ink }}>{total[N - 1]}</text>
      <text x={x(0)} y={yT(total[0]) - 9} textAnchor="middle" style={{ fontFamily: "Inter,sans-serif", fontSize: 10, fill: C.muted }}>{total[0]}</text>
      <line x1="20" y1={axis} x2="600" y2={axis} stroke={C.hair} strokeWidth="1" />
      {joined.map((v, i) => <rect key={"j" + i} x={x(i) - 6} y={axis - (v / maxJ) * 28} width="12" height={(v / maxJ) * 28} rx="2" fill={C.meadow} />)}
      {lost.map((v, i) => v > 0 ? <rect key={"l" + i} x={x(i) - 6} y={axis} width="12" height={(v / maxL) * 15} rx="2" fill={C.amber} /> : null)}
      {months.map((mo, i) => <text key={mo + i} x={x(i)} y="202" textAnchor="middle" style={{ fontFamily: "Inter,sans-serif", fontSize: 8.5, fill: i === N - 1 ? C.slate : C.muted, fontWeight: i === N - 1 ? 600 : 400 }}>{mo}</text>)}
    </svg>
  );
}
function GKpi({ label, value, valColor = C.ink, sub }) {
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center", padding: "12px 15px", border: `1px solid ${C.hair}`, borderRadius: 12, background: C.parchment }}>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 10, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: C.muted }}>{label}</div>
      <div style={{ ...opBig, fontSize: 24, color: valColor, marginTop: 4 }}>{value}</div>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, color: C.muted, marginTop: 3 }}>{sub}</div>
    </div>
  );
}
function GrowthBlock({ g }) {
  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 10 }}>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10, fontWeight: 700, letterSpacing: ".07em", textTransform: "uppercase", color: C.muted }}>Growth</span>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, color: C.muted }}>active members · trailing 12 months</span>
      </div>
      <div className="opgrowgrid">
        <div style={{ border: `1px solid ${C.hair}`, borderRadius: 14, background: C.surface, padding: "12px 14px 6px", display: "flex", flexDirection: "column" }}>
          <GrowthChart g={g} />
          <div style={{ display: "flex", gap: 14, justifyContent: "center", marginTop: 4 }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontFamily: "Inter,sans-serif", fontSize: 10, color: C.slate }}><span style={{ width: 8, height: 8, borderRadius: 2, background: C.meadow }} />joined</span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontFamily: "Inter,sans-serif", fontSize: 10, color: C.slate }}><span style={{ width: 8, height: 8, borderRadius: 2, background: C.amber }} />lost</span>
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <GKpi label="Net new · MTD" value={`${g.netMTD >= 0 ? "+" : ""}${g.netMTD}`} valColor={g.netMTD >= 0 ? C.meadow : C.amber} sub={`${g.joinedMTD} joined · ${g.lostMTD} lost`} />
          <GKpi label="Growth · 12 mo" value={`${g.net12 >= 0 ? "+" : ""}${g.net12}`} valColor={g.net12 >= 0 ? C.meadow : C.amber} sub={`${g.ratePct >= 0 ? "+" : ""}${g.ratePct}% · ${g.joined12} joined`} />
          <GKpi label="Retention · 12 mo" value={`${g.retentionPct}%`} sub={`${g.lost12} members lapsed`} />
        </div>
      </div>
    </div>
  );
}
function PayMixMini({ pay }) {
  const tot = pay.pif + pay.monthly + pay.quarterly + pay.installments || 1;
  const segs = [["PIF", pay.pif, PLAN_DOT.pif], ["Monthly", pay.monthly, PLAN_DOT.monthly], ["Quarterly", pay.quarterly, PLAN_DOT.quarterly], ["Installments", pay.installments, PLAN_DOT.installments]];
  return (
    <div>
      <div style={{ display: "flex", height: 8, borderRadius: 99, overflow: "hidden", background: C.hair, marginBottom: 8 }}>
        {segs.map(([k, v, c]) => v > 0 && <div key={k} className="fvGrow" style={{ width: `${(v / tot) * 100}%`, background: c, transformOrigin: "left" }} title={`${v} ${k}`} />)}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 11px" }}>
        {segs.map(([k, v, c]) => <span key={k} style={{ display: "inline-flex", alignItems: "center", gap: 4, fontFamily: "Inter,sans-serif", fontSize: 10, color: C.slate }}><span style={{ width: 6, height: 6, borderRadius: 2, background: c }} />{v} {k}</span>)}
      </div>
    </div>
  );
}
const RENEW_STATE = {
  auto: { label: "Auto-renews", dot: C.meadow, val: C.ink, tone: "calm" },
  resign: { label: "Needs re-sign", dot: C.flagDot, val: C.amber, tone: "act", icon: "resign" },
  failing: { label: "Payment failing", dot: C.flagDot, val: C.amber, tone: "act", icon: "alert" },
};
function RenewalRow({ r }) {
  const m = RENEW_STATE[r.state] || RENEW_STATE.auto;
  return (
    <div className="fvRow" style={{ display: "grid", gridTemplateColumns: "1fr auto 150px 22px", alignItems: "center", gap: 10, padding: "10px 14px", borderTop: `1px solid ${C.hair}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
        <span style={{ width: 7, height: 7, borderRadius: 99, background: m.dot, border: m.tone === "act" ? `1px solid ${C.amber}` : "none", flexShrink: 0 }} />
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600, color: C.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.name}</span>
        {r.first && <span title="First renewal, higher churn risk" style={{ fontFamily: "Inter,sans-serif", fontSize: 9, fontWeight: 700, letterSpacing: ".03em", color: C.amber, background: C.flagBg, borderRadius: 4, padding: "1px 5px" }}>1ST</span>}
      </div>
      <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600, color: m.val, fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>{kc(r.v)}</span>
      <span style={{ display: "flex", alignItems: "center", gap: 6, justifySelf: "start" }}>
        {m.icon ? <Ic name={m.icon} size={12} color={C.amber} /> : <span style={{ width: 12 }} />}
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: m.tone === "act" ? C.amber : C.muted }}>{m.label}</span>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, color: C.muted }}>· {r.date}</span>
      </span>
      {r.source_url ? <a href={r.source_url} target="_blank" rel="noreferrer" title="Open in Go High Level" style={{ justifySelf: "end", opacity: .55 }}><Ic name="ext" size={13} color={C.slate} /></a> : <span />}
    </div>
  );
}
function RenewalQueue({ rn }) {
  const [tab, setTab] = useState("all");
  const rank = { failing: 0, resign: 1, auto: 2 };
  const rows = (rn.rows || []).filter((r) => tab === "all" ? true : tab === "act" ? r.state !== "auto" : r.state === "auto").sort((a, b) => rank[a.state] - rank[b.state] || b.v - a.v);
  const nAct = (rn.rows || []).filter((r) => r.state !== "auto").length;
  const nAuto = (rn.rows || []).length - nAct;
  const chip = (k, label, count, c) => {
    const on = tab === k;
    return <button onClick={() => setTab(k)} style={{ fontFamily: "Inter,sans-serif", fontSize: 11, fontWeight: 600, cursor: "pointer", borderRadius: 7, padding: "4px 10px", border: `1px solid ${on ? c : C.hair}`, background: on ? "rgba(97,131,94,.08)" : C.surface, color: on ? C.ink : C.slate, display: "inline-flex", alignItems: "center", gap: 6 }}>{k !== "all" && <span style={{ width: 6, height: 6, borderRadius: 2, background: c }} />}{label}<span style={{ color: C.muted }}>{count}</span></button>;
  };
  return (
    <div style={{ border: `1px solid ${C.hair}`, borderRadius: 14, background: C.surface, overflow: "hidden" }}>
      <div style={{ padding: "15px 16px 14px" }}>
        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 12, marginBottom: 12 }}>
          <div>
            <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, fontWeight: 700, letterSpacing: ".02em", color: C.ink }}>Renewals · Next 90 Days</span>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginTop: 5 }}>
              <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 26, fontWeight: 600, color: C.ink, fontVariantNumeric: "tabular-nums" }}>{kc(rn.book)}</span>
              <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: C.muted }}>across {rn.count} members</span>
            </div>
          </div>
          <div style={{ textAlign: "right" }}>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, color: C.muted, marginBottom: 3 }}>needs a manual touch</div>
            <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 18, fontWeight: 600, color: C.amber, fontVariantNumeric: "tabular-nums" }}>{kc(rn.needsYou)}</div>
          </div>
        </div>
        <DuoBar auto={rn.auto} needsYou={rn.needsYou} />
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 7, fontFamily: "Inter,sans-serif", fontSize: 10.5, color: C.muted }}>
          <span><span style={{ color: C.meadow, fontWeight: 600 }}>{kc(rn.auto)}</span> auto-renews</span>
          <span style={{ color: C.amber, fontWeight: 600 }}>{rn.resigns} re-signs · {rn.failing} failing</span>
        </div>
        <div style={{ display: "flex", gap: 7, marginTop: 12, flexWrap: "wrap" }}>
          {chip("all", "All", (rn.rows || []).length, C.slate)}
          {chip("act", "Needs a touch", nAct, C.amber)}
          {chip("auto", "Auto-renews", nAuto, C.meadow)}
        </div>
      </div>
      <div>{rows.length ? rows.map((r, i) => <RenewalRow key={r.name + i} r={r} />) : <div style={{ padding: "18px", textAlign: "center", fontFamily: "Inter,sans-serif", fontSize: 12, color: C.muted, borderTop: `1px solid ${C.hair}` }}>None in this view.</div>}</div>
    </div>
  );
}
function FunnelStat({ label, value, sub, accent }) {
  return (
    <div style={{ flex: 1 }}>
      <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 18, fontWeight: 600, color: accent ? C.meadow : C.ink, fontVariantNumeric: "tabular-nums" }}>{value}</div>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 10, fontWeight: 600, color: C.slate, marginTop: 2 }}>{label}</div>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 9.5, color: C.muted }}>{sub}</div>
    </div>
  );
}
function RecruitingPanel({ r }) {
  const max = (r.stages[0] && r.stages[0].n) || 1;
  return (
    <div style={{ border: `1px solid ${C.hair}`, borderRadius: 14, background: C.surface, padding: "15px 16px 16px", display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ marginBottom: 6 }}>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, fontWeight: 700, letterSpacing: ".02em", color: C.ink }}>Recruiting · Pipeline</span>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginTop: 5 }}>
          <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 26, fontWeight: 600, color: C.ink, fontVariantNumeric: "tabular-nums" }}>{r.total}</span>
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: C.muted }}>in pipeline</span>
        </div>
      </div>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center", gap: 16, padding: "6px 0" }}>
        {r.stages.map((s, i) => {
          const last = i === r.stages.length - 1;
          const prev = i > 0 ? r.stages[i - 1].n : null;
          const conv = prev ? Math.round((s.n / prev) * 100) : null;
          return (
            <div key={s.label} style={{ display: "grid", gridTemplateColumns: "84px 1fr 64px", alignItems: "center", gap: 10 }}>
              <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: C.slate, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{s.label}</span>
              <div style={{ height: 22, borderRadius: 6, background: C.hair, overflow: "hidden" }}>
                <div className="fvGrow" style={{ width: `${Math.max(7, (s.n / max) * 100)}%`, height: "100%", background: last ? C.meadow : C.evergreen, opacity: last ? 1 : 1 - i * 0.14, transformOrigin: "left" }} title={`${s.n}`} />
              </div>
              <span style={{ display: "flex", alignItems: "baseline", gap: 6, justifyContent: "flex-end" }}>
                <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 13.5, fontWeight: 600, color: C.ink, fontVariantNumeric: "tabular-nums" }}>{s.n}</span>
                {conv != null && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10, color: C.muted }}>{conv}%</span>}
              </span>
            </div>
          );
        })}
      </div>
      <div style={{ display: "flex", gap: 10, marginTop: 4, paddingTop: 14, borderTop: `1px solid ${C.hair}` }}>
        <FunnelStat label="VIP guests" value={r.vipGuests} sub="event-invited" />
        <FunnelStat label="Committed" value={r.committed} sub="verbal / applied" />
        <FunnelStat label="Expected" value={`~${r.expected}`} sub="est. likely to join" accent />
      </div>
    </div>
  );
}
function RenewalCalendar({ cal }) {
  const max = Math.max(...cal.map((c) => c.v), 1);
  return (
    <div style={{ border: `1px solid ${C.hair}`, borderRadius: 14, background: C.surface, padding: "14px 16px 12px" }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 12 }}>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, fontWeight: 700, color: C.ink }}>Renewal Calendar</span>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, color: C.muted }}>book by month · 6 mo</span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: `repeat(${cal.length}, 1fr)`, gap: 8, alignItems: "end", height: 92 }}>
        {cal.map((c, i) => {
          const win = i < 3;
          return (
            <div key={c.m + i} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6, height: "100%", justifyContent: "flex-end" }}>
              <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 10, fontWeight: 600, color: win ? C.ink : C.muted, fontVariantNumeric: "tabular-nums" }}>{c.v ? kc(c.v) : ""}</span>
              <div className="fvGrowY" style={{ width: "72%", height: `${Math.max(6, (c.v / max) * 66)}px`, borderRadius: "4px 4px 0 0", background: win ? C.meadow : C.sprout, transformOrigin: "bottom" }} title={`${c.m}: ${kc(c.v)}`} />
              <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, color: win ? C.slate : C.muted, fontWeight: win ? 600 : 400 }}>{c.m}</span>
            </div>
          );
        })}
      </div>
      <div style={{ marginTop: 10, fontFamily: "Inter,sans-serif", fontSize: 10.5, color: C.muted }}><span style={{ color: C.meadow, fontWeight: 600 }}>Shaded</span> = inside the 90-day window.</div>
    </div>
  );
}
function RecoverPanel({ recover, action, onOpen }) {
  return (
    <div style={{ border: `1px solid ${C.hair}`, borderRadius: 14, background: C.surface, padding: "15px 16px" }}>
      <div style={{ marginBottom: 12 }}>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, fontWeight: 700, color: C.ink }}>Money to Recover</span>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginTop: 5 }}>
          <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 22, fontWeight: 600, color: C.amber, fontVariantNumeric: "tabular-nums" }}>{kc(action.recover)}</span>
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: C.muted }}>{action.failed} failed charge{action.failed === 1 ? "" : "s"}</span>
        </div>
      </div>
      <div style={{ borderTop: `1px solid ${C.hair}` }}>
        {(recover || []).map((r, i) => (
          <div key={r.name + i} className="fvRow" style={{ display: "grid", gridTemplateColumns: "1fr auto auto 22px", alignItems: "center", gap: 10, padding: "9px 4px", borderBottom: `1px solid ${C.hair}` }}>
            <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ width: 6, height: 6, borderRadius: 99, background: C.flagDot, border: `1px solid ${C.amber}` }} />
              <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600, color: C.ink }}>{r.name}</span>
            </span>
            <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, color: C.muted }}>{r.attempts} tr{r.attempts === 1 ? "y" : "ies"}</span>
            <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 12.5, fontWeight: 600, color: C.amber, fontVariantNumeric: "tabular-nums" }}>{usd(r.amt)}</span>
            {r.source_url ? <a href={r.source_url} target="_blank" rel="noreferrer" title="Open in Go High Level" style={{ justifySelf: "end", opacity: .55 }}><Ic name="ext" size={12} color={C.slate} /></a> : <span />}
          </div>
        ))}
      </div>
    </div>
  );
}
const OpCtxLabel = ({ children }) => <div style={{ fontFamily: "Inter,sans-serif", fontSize: 10, fontWeight: 700, letterSpacing: ".07em", textTransform: "uppercase", color: C.muted, marginBottom: 9 }}>{children}</div>;
function MembersGrowthBody({ mg, recruiting, onOpen }) {
  const pay = mg.pay || {};
  return (
    <div>
      {mg.growth && <GrowthBlock g={mg.growth} />}
      <div className="opfacts" style={{ paddingBottom: 20, borderBottom: `1px solid ${C.hair}`, marginBottom: 20 }}>
        <div>
          <OpCtxLabel>Membership Mix</OpCtxLabel>
          <div style={{ display: "flex", alignItems: "baseline", gap: 18, marginBottom: 11 }}>
            <span><span style={{ ...opBig, fontSize: 30 }}>{mg.primary}</span><span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: C.slate, marginLeft: 6 }}>primary</span></span>
            <span><span style={{ fontFamily: "Poppins,sans-serif", fontSize: 22, fontWeight: 600, color: C.slate, fontVariantNumeric: "tabular-nums" }}>{mg.addOn}</span><span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: C.muted, marginLeft: 6 }}>add-on</span></span>
          </div>
          <div style={{ display: "flex", height: 9, borderRadius: 99, overflow: "hidden", background: C.hair, marginBottom: 9 }}>
            <div className="fvGrow" style={{ width: `${(mg.primary / (mg.primary + mg.addOn || 1)) * 100}%`, background: C.meadow, transformOrigin: "left" }} title={`${mg.primary} primary`} />
            <div className="fvGrow" style={{ width: `${(mg.addOn / (mg.primary + mg.addOn || 1)) * 100}%`, background: C.sprout, transformOrigin: "left" }} title={`${mg.addOn} add-on`} />
          </div>
          {mg.tenure && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, color: C.muted }}>{mg.tenure.avg} mo avg tenure{mg.tenure.first ? <> · <span style={{ color: C.amber, fontWeight: 600 }}>{mg.tenure.first}</span> at first renewal, watch closely</> : null}</div>}
        </div>
        <div>
          <OpCtxLabel>Payment Cadence</OpCtxLabel>
          <PayMixMini pay={pay} />
          {pay.lump ? <div style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, color: C.slate, marginTop: 9, lineHeight: 1.4 }}>
            <span style={{ fontFamily: "Poppins,sans-serif", fontWeight: 600, color: C.ink }}>{kc(pay.lump)}</span> ({pay.lumpPct}%) is PIF: no auto-renew, so every one is a manual re-sign each year.
          </div> : null}
        </div>
      </div>
      <div className="opmotions">
        <RenewalQueue rn={mg.renewals || { rows: [], book: 0, count: 0, auto: 0, needsYou: 0, resigns: 0, failing: 0 }} />
        {recruiting ? <RecruitingPanel r={recruiting} /> : <div style={{ border: `1px solid ${C.hair}`, borderRadius: 14, background: C.surface, padding: 16, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "Inter,sans-serif", fontSize: 12, color: C.muted }}>Recruiting pipeline not available.</div>}
      </div>
      <button onClick={() => onOpen("forum_roster")} className="oproster" style={{ marginTop: 16, width: "100%", display: "flex", alignItems: "center", justifyContent: "center", gap: 9, padding: "14px", borderRadius: 12, background: C.evergreen, border: "none", cursor: "pointer", fontFamily: "Inter,sans-serif", fontSize: 13, fontWeight: 600, color: C.heroText }}>
        Open full roster <Ic name="arrow" size={15} color={C.heroText} />
      </button>
    </div>
  );
}

/* ── Program view ───────────────────────────────────────────────── */
export default function ForumView({ data, area, onDrill, title = "The Forum",
  subtitle = "Mastermind", deckSlots = DECK_SLOTS, drillBusiness = "springb",
  rosterKey = "forum_roster", drillMap = null }) {
  const [openState, setOpenState] = useState(null);
  if (!data) return null;

  // When reused for another program (beCollective), drillMap remaps every Forum drill key
  // to that program's own (bc_*) key in one place, so no drill can leak Forum data.
  const isBc = !!drillMap;
  const onOpen = (key, opts) =>
    onDrill && onDrill((drillMap && drillMap[key]) || key, drillBusiness, null, null, opts);
  const b = data.billing?.available ? data.billing : null;

  // hero cash trend: actual months from first non-zero through the current month
  const monthly = b?.monthly || [];
  const firstIdx = monthly.findIndex((m) => m.actual > 0);
  const mtdIdx = monthly.findIndex((m) => m.mtd);
  const trend = firstIdx >= 0 && mtdIdx >= firstIdx ? monthly.slice(firstIdx, mtdIdx + 1) : [];
  const heroSpark = trend.map((m) => m.actual);
  const heroMonths = trend.map((m) => m.month);
  const rangeLabel = heroMonths.length ? `${heroMonths[0]}–${heroMonths[heroMonths.length - 1]}` : "";

  const members = membersItems(data, true, onOpen, deckSlots, rosterKey);
  const moneyWatch = b && (b.failed_count > 0 || b.past_due > 0);

  const pipelineTotal = data.funnel ? (data.funnel.stages || []).reduce((a, s) => a + s.v, 0) : null;
  const renewalCount = data.renewals?.summary?.count;
  const membersSummary = [`${data.members_total} active`,
    pipelineTotal != null ? `${pipelineTotal} recruiting` : null,
    renewalCount != null ? `${renewalCount} renewals due` : null].filter(Boolean).join(" · ");

  // Forum carries the v9 operational payload (pulse/mg/recruiting); beCollective does
  // not, so it keeps the existing OpsPulse + deck rendering. Every new block degrades.
  const hasOps = !!data.pulse;
  const g12 = data.mg?.growth?.ratePct;
  const mgSummary = data.mg
    ? [`${data.mg.active} active`, g12 != null ? `${g12 >= 0 ? "+" : ""}${g12}% / 12 mo` : null,
       data.mg.renewals?.needsYou ? `${kc(data.mg.renewals.needsYou)} renewals need a touch` : null].filter(Boolean).join(" · ")
    : membersSummary;

  const defaults = { members: true, money: !!b };
  const open = openState || defaults;
  const toggle = (id) => setOpenState({ ...open, [id]: !open[id] });

  return (
    <div className="fe">
      <style>{CSS}</style>
      <div className="wrap">
        {/* title */}
        <div className="fe-head">
          <span className="fe-bar" />
          <div>
            <div className="fe-title">{title}</div>
            <div className="fe-sub">{subtitle} · {data.members_total} members{rangeLabel ? ` · ${rangeLabel} 2026` : ""}</div>
          </div>
          <span className="fe-syncpill"><span className="fe-syncdot" />Live · Go High Level</span>
        </div>

        {/* top row: P&L (booked) + Money Hero (cash) */}
        <div className="enter topgrid">
          <PnlCard area={area} billing={b} />
          {b
            ? <MoneyHero b={b} rangeLabel={rangeLabel} spark={heroSpark} months={heroMonths} onOpen={onOpen} isBc={isBc} />
            : <div className="card" style={{ padding: 22, display: "flex", flexDirection: "column", justifyContent: "center" }}>
                <div className="kick"><span className="kb kb-m" />Cash · Real-Time Truth<span className="src">Go High Level</span></div>
                <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: C.muted, lineHeight: 1.6 }}>The cash lens lights up when this program's Go High Level Payments connection is live.</div>
              </div>}
        </div>

        {/* operational pulse */}
        <div className="enter" style={{ animationDelay: "90ms" }}>
          {hasOps ? <PulseStrip pulse={data.pulse} onOpen={onOpen} rosterKey={rosterKey} /> : <OpsPulse data={data} onOpen={onOpen} />}
        </div>

        {/* single action row (failed charges) / watch strip — the row itself carried no
            vertical rhythm, so it sat flush between the pulse and the section (smushed) */}
        <div className="enter" style={hasOps ? { animationDelay: "150ms", margin: "14px 0 16px" } : { animationDelay: "150ms" }}>
          {hasOps ? <ActionRowV9 action={data.action} onOpen={onOpen} /> : <WatchStrip b={b} data={data} onOpen={onOpen} />}
        </div>

        {/* members & growth */}
        <div className="enter" style={{ animationDelay: "210ms" }}>
          <Section icon={A.users} tint={C.meadow} title="Members & Growth" summary={mgSummary}
                   open={!!open.members} onToggle={() => toggle("members")}>
            {data.mg
              ? <MembersGrowthBody mg={data.mg} recruiting={data.recruiting} onOpen={onOpen} />
              : <Deck items={membersItems(data, !!open.members, onOpen, deckSlots)} />}
          </Section>
        </div>

        {/* cash & billing (only when billing is available) */}
        {b && (
          <div className="enter" style={{ animationDelay: "270ms" }}>
            <Section icon={A.cash} tint={C.meadow} title="Cash & Billing"
                     summary={`${kc(b.net_cash)} collected · ${kc(b.mrr)} MRR · ${kc(b.arr_book)} ARR`}
                     watch={moneyWatch ? `${(b.failed_count || 0) + (b.past_due || 0)} to recover` : null}
                     live sub={`Cash basis · ${isBc ? "Stripe · beCollective account" : "Stripe via Go High Level"} · reconciles to QuickBooks as the Booked lens when connected`}
                     open={!!open.money} onToggle={() => toggle("money")}>
              <Deck items={moneyItems(b, !!open.money, onOpen, isBc)} />
              {(() => {
                const cal = data.mg?.calendar?.length ? <RenewalCalendar cal={data.mg.calendar} /> : null;
                const rec = data.recover && data.recover.length
                  ? <RecoverPanel recover={data.recover} action={data.action} onOpen={onOpen} /> : null;
                if (!cal && !rec) return null;
                // Two cells only when both are present; a lone panel spans the full width
                // (otherwise it's squished into the narrow right column).
                return cal && rec
                  ? <div className="opcb" style={{ marginTop: 16 }}>{cal}{rec}</div>
                  : <div style={{ marginTop: 16 }}>{cal || rec}</div>;
              })()}
            </Section>
          </div>
        )}
      </div>
    </div>
  );
}

/* beCollective placeholder (kept for the standalone stub entry point). */
export function BeCollectivePlaceholder() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <span style={{ width: 5, height: 30, borderRadius: 3, background: C.mistDeep }} />
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 24, fontWeight: 600, color: C.ink }}>beCollective</span>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: C.muted }}>Community · separate GHL segment</span>
      </div>
      <div className="card" style={{ maxWidth: 560, padding: 22 }}>
        <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 16, fontWeight: 600, color: C.ink }}>Its own space is coming</div>
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: C.slate, marginTop: 8, lineHeight: 1.6 }}>
          Connect the beCollective Go High Level account in Settings to light this up.
        </div>
      </div>
    </div>
  );
}
