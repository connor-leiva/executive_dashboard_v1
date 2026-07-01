import { useState } from "react";
import { T, STATUS, usd, signed } from "./theme.js";
import { useDashboard } from "./useDashboard.js";

/* ──────────────────────────────────────────────────────────────
   Spring · Command Center — production
   Palette + type from Spring's Visual Identity System:
   Evergreen / Parchment / Poppy, Poppins + Inter, ribbed gradient.
   Driven by a fetched DashboardResponse (sample fallback in dev).
   ────────────────────────────────────────────────────────────── */

const API_BASE = import.meta.env.VITE_API_BASE;

// Map a scorecard's business_key to its dot color.
function dotFor(businessKey) {
  switch (businessKey) {
    case "portfolio":
      return T.evergreen;
    case "ulrg":
      return T.meadow;
    case "sympli":
      return T.teal;
    case "springb":
      return T.poppy;
    default:
      return T.evergreen;
  }
}

// "2026-03-31" → "Mar 31, 2026"
function formatAsOf(iso) {
  if (!iso) return "";
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

// "2026-03-31" → "March 2026"
function monthYear(iso) {
  if (!iso) return "";
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", { month: "long", year: "numeric" });
}

/* ── small pieces ──────────────────────────────────────────── */

function Spark({ data, color, w = 104, h = 38 }) {
  const max = Math.max(...data), min = Math.min(...data), rng = max - min || 1;
  const pts = data.map((d, i) => [(i / (data.length - 1)) * w, h - 4 - ((d - min) / rng) * (h - 8)]);
  const line = pts.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  const id = "g" + color.replace("#", "");
  return (
    <svg width={w} height={h} style={{ display: "block" }}>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.20" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon points={`0,${h} ${line} ${w},${h}`} fill={`url(#${id})`} />
      <polyline points={line} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r="2.6" fill={color} />
    </svg>
  );
}

function Dot({ status }) {
  const s = STATUS[status];
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span style={{ width: 7, height: 7, borderRadius: 99, background: s.dot }} />
      <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, fontWeight: 600, color: s.text }}>{s.label}</span>
    </span>
  );
}

function Source({ name }) {
  return (
    <span style={{
      fontFamily: "Inter,sans-serif", fontSize: 10.5, fontWeight: 600, color: T.slate,
      background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 5, padding: "2px 7px",
    }}>{name}</span>
  );
}

function Eyebrow({ children, onDark }) {
  return (
    <div style={{
      fontFamily: "Poppins,sans-serif", fontSize: 11, fontWeight: 700, letterSpacing: "0.14em",
      textTransform: "uppercase", color: onDark ? T.poppy : T.poppyText,
    }}>{children}</div>
  );
}

function PanelLabel({ children, accent }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
      <span style={{ width: 3, height: 14, borderRadius: 2, background: accent || T.evergreen }} />
      <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 11, fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", color: T.slate }}>
        {children}
      </span>
    </div>
  );
}

function Card({ children, style }) {
  return <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 14, padding: 22, ...style }}>{children}</div>;
}

/* ── P&L ───────────────────────────────────────────────────── */

function PLTable({ rows, area }) {
  return (
    <div>
      {rows.map((r, i) => {
        const tot = r.kind === "tot", sub = r.kind === "sub", share = r.kind === "share", ded = r.kind === "ded";
        return (
          <div key={i} style={{
            display: "flex", justifyContent: "space-between", alignItems: "baseline",
            padding: tot ? "13px 0 4px" : "9px 0",
            borderTop: tot ? `2px solid ${T.evergreen}` : share ? `1px dashed ${T.line}` : "none",
            borderBottom: sub ? `1px solid ${T.line}` : "none", marginTop: tot ? 6 : 0,
          }}>
            <span style={{
              fontFamily: "Inter,sans-serif", fontSize: tot ? 14 : 13,
              fontWeight: tot || sub ? 700 : share ? 600 : 400,
              color: ded ? T.slate : share ? area.ink : T.ink, fontStyle: share ? "italic" : "normal",
            }}>{r.label}</span>
            <span style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
              {r.note && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted, fontWeight: 600 }}>{r.note}</span>}
              <span style={{
                fontFamily: "Poppins,sans-serif", fontSize: tot ? 19 : 14, fontWeight: tot ? 700 : sub ? 600 : 500,
                color: tot ? area.ink : ded ? T.slate : share ? area.ink : T.ink,
                fontVariantNumeric: "tabular-nums", fontStyle: share ? "italic" : "normal",
              }}>{ded ? signed(r.value) : usd(r.value)}</span>
            </span>
          </div>
        );
      })}
    </div>
  );
}

function PLEmpty({ area }) {
  const businessId = area.id || area.key;
  const connectHref = API_BASE ? `${API_BASE}/integrations/qbo/connect?business_id=${businessId}` : null;
  return (
    <div style={{ padding: "16px 2px 4px" }}>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.muted, lineHeight: 1.6, marginBottom: 16 }}>
        Financials light up when QuickBooks is connected
      </div>
      <a
        href={connectHref || undefined}
        title={connectHref ? "Connect QuickBooks" : "Set VITE_API_BASE to enable QuickBooks connect"}
        aria-disabled={connectHref ? undefined : true}
        style={{
          display: "inline-block", fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600,
          color: connectHref ? T.onDark : T.onDarkMute,
          background: connectHref ? T.evergreen : "rgba(0,46,44,0.35)",
          borderRadius: 8, padding: "9px 14px", textDecoration: "none",
          cursor: connectHref ? "pointer" : "not-allowed", pointerEvents: connectHref ? "auto" : "none",
        }}
      >
        Connect QuickBooks
      </a>
    </div>
  );
}

function OpTile({ d }) {
  return (
    <div style={{ background: T.parchment, borderRadius: 10, padding: "12px 13px" }}>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.slate, marginBottom: 6, fontWeight: 500 }}>{d.label}</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 22, fontWeight: 600, color: T.ink, fontVariantNumeric: "tabular-nums" }}>{d.value}</span>
        {d.sub && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted }}>{d.sub}</span>}
      </div>
    </div>
  );
}

function Bars({ rows, accent }) {
  const max = Math.max(...rows.map((r) => r.v));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
      {rows.map((r, i) => (
        <div key={i} style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ width: 108, fontFamily: "Inter,sans-serif", fontSize: 12, color: T.slate, textAlign: "right" }}>{r.label}</span>
          <div style={{ flex: 1, height: 22, background: T.parchment, borderRadius: 5, overflow: "hidden" }}>
            <div style={{ width: `${(r.v / max) * 100}%`, height: "100%", background: accent, opacity: 0.4 + 0.6 * (r.v / max), borderRadius: 5 }} />
          </div>
          <span style={{ width: 42, fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600, color: T.ink, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{r.v}</span>
        </div>
      ))}
    </div>
  );
}

/* ── overview ──────────────────────────────────────────────── */

function CompositionBar({ composition }) {
  const segs = composition || [];
  if (!segs.length) return null;
  return (
    <div>
      <div style={{ display: "flex", height: 42, borderRadius: 8, overflow: "hidden", gap: 3 }}>
        {segs.map((s, i) => (
          <div key={i} style={{ width: `${s.pct}%`, background: s.accent, display: "flex", alignItems: "center", paddingLeft: 12, minWidth: 54 }}>
            <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 11.5, fontWeight: 700, color: "#fff" }}>{Math.round(s.pct)}%</span>
          </div>
        ))}
      </div>
      <div style={{ display: "flex", gap: 22, marginTop: 12, flexWrap: "wrap" }}>
        {segs.map((s, i) => (
          <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
            <span style={{ width: 9, height: 9, borderRadius: 2, background: s.accent }} />
            <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.onDark, fontWeight: 600 }}>{s.name}</span>
            <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 12.5, color: s.accent, fontVariantNumeric: "tabular-nums", fontWeight: 600 }}>{usd(s.revenue)}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function AreaCard({ area, onOpen }) {
  const a = area;
  const hasRevenue = a.revenue != null;
  return (
    <button onClick={() => onOpen(a.key)} className="cc-card" style={{
      textAlign: "left", background: T.white, border: `1px solid ${T.line}`, borderTop: `3px solid ${a.accent}`,
      borderRadius: 14, padding: 20, cursor: "pointer",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 16 }}>
        <div>
          <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 16, fontWeight: 600, color: T.ink }}>{a.name}</div>
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted, marginTop: 2 }}>{a.tag}</div>
        </div>
        <Dot status={a.status} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.slate, marginBottom: 3 }}>Revenue</div>
          {hasRevenue ? (
            <>
              <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 24, fontWeight: 600, color: T.ink, fontVariantNumeric: "tabular-nums" }}>{usd(a.revenue)}</div>
              <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.slate, marginTop: 4 }}>
                NOI <span style={{ color: a.ink, fontWeight: 700 }}>{usd(a.noi)}</span> · {a.margin}%
              </div>
            </>
          ) : (
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.muted, maxWidth: 150, lineHeight: 1.4 }}>
              Connect QuickBooks for revenue
            </div>
          )}
        </div>
        <Spark data={a.trend} color={a.accent} />
      </div>
    </button>
  );
}

function Overview({ data, onOpen }) {
  const { portfolio, scorecards, areas, flywheel, period } = data;
  const hasRevenue = portfolio.revenue != null;
  const fw = flywheel || {};
  const buyerClosings = fw.buyer_closings;
  const captured = fw.captured;
  const capturePct = fw.capture_pct != null
    ? fw.capture_pct
    : (buyerClosings ? Math.round((captured / buyerClosings) * 100) : null);
  const annualGap = fw.annual_gap != null
    ? fw.annual_gap
    : (fw.monthly_gap != null ? fw.monthly_gap * 12 : null);
  const periodLabel = monthYear(period?.as_of) || period?.label;
  const orderedCards = [areas.ulrg, areas.springb, areas.sympli].filter(Boolean);
  const fwAvailable = fw.available !== false && buyerClosings != null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      {/* Hero */}
      <div style={{
        position: "relative", overflow: "hidden", borderRadius: 16, padding: 28,
        backgroundColor: T.evergreen,
        backgroundImage: `repeating-linear-gradient(90deg, rgba(255,255,255,0.055) 0px, rgba(255,255,255,0.055) 1.5px, rgba(255,255,255,0) 1.5px, rgba(255,255,255,0) 13px), radial-gradient(135% 130% at 88% -15%, rgba(97,131,94,0.50) 0%, rgba(0,46,44,0) 55%)`,
      }}>
        <span aria-hidden style={{ position: "absolute", top: 8, right: 26, fontFamily: "Sacramento,cursive", fontSize: 60, color: "rgba(248,245,242,0.13)", lineHeight: 1, pointerEvents: "none" }}>Spring</span>
        <Eyebrow onDark>Portfolio · {periodLabel}</Eyebrow>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 34, flexWrap: "wrap", margin: "16px 0 24px" }}>
          <div>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.onDarkMute, marginBottom: 5 }}>Portfolio revenue · month to date</div>
            {hasRevenue ? (
              <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
                <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 46, fontWeight: 700, color: T.onDark, lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>{usd(portfolio.revenue)}</span>
                {portfolio.mom != null && (
                  <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600, color: portfolio.mom < 0 ? T.petal : T.sprout, background: "rgba(184,204,184,0.14)", borderRadius: 6, padding: "3px 8px" }}>{portfolio.mom >= 0 ? "+" : ""}{portfolio.mom}% MoM</span>
                )}
              </div>
            ) : (
              <div style={{ fontFamily: "Inter,sans-serif", fontSize: 16, fontWeight: 600, color: T.sprout, lineHeight: 1.4, maxWidth: 360 }}>
                Connect QuickBooks for the financial picture
              </div>
            )}
          </div>
          <div style={{ paddingBottom: 4 }}>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.onDarkMute, marginBottom: 5 }}>Cash on hand</div>
            <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 26, fontWeight: 600, color: T.onDark, fontVariantNumeric: "tabular-nums" }}>{portfolio.cash != null ? usd(portfolio.cash) : "—"}</span>
          </div>
        </div>
        <CompositionBar composition={portfolio.composition} />
      </div>

      {/* Scorecards */}
      <div>
        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", margin: "2px 2px 12px" }}>
          <Eyebrow>At a glance</Eyebrow>
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted }}>across all three businesses</span>
        </div>
        <div className="cc-score">
          {scorecards.map((s, i) => (
            <div key={i} style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 12, padding: "16px 16px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 9 }}>
                <span style={{ width: 7, height: 7, borderRadius: 2, background: dotFor(s.business_key) }} />
                <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate, fontWeight: 500 }}>{s.label}</span>
              </div>
              <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 30, fontWeight: 700, color: T.ink, lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>{s.value}</div>
              <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted, marginTop: 6 }}>{s.sub}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Business cards — equal, one row */}
      <div className="cc-cards">
        {orderedCards.map((a) => <AreaCard key={a.key} area={a} onOpen={onOpen} />)}
      </div>

      {/* Flywheel alert */}
      <button onClick={() => onOpen("flywheel")} className="cc-card" style={{
        textAlign: "left", cursor: "pointer", background: T.evergreen, border: "none", borderRadius: 14, padding: 22,
        display: "flex", alignItems: "center", justifyContent: "space-between", gap: 20, flexWrap: "wrap",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <span style={{ width: 9, height: 9, borderRadius: 99, background: T.poppy, flexShrink: 0 }} />
          <div>
            <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 16, fontWeight: 600, color: T.onDark }}>
              {fwAvailable ? "The referral flywheel is leaking" : "The referral flywheel"}
            </div>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.onDarkMute, marginTop: 3 }}>
              {fwAvailable
                ? `Sympli financed ${captured} of ${buyerClosings} ULRG buyer closings this month · ${capturePct}% capture`
                : "Connect Arive to see how many ULRG buyers Sympli financed (Phase 3)"}
            </div>
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          {fwAvailable && annualGap != null && (
            <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 26, fontWeight: 700, color: T.poppy, fontVariantNumeric: "tabular-nums" }}>~{usd(annualGap)}</div>
          )}
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.onDarkMute }}>
            {fwAvailable ? "/yr unrealized · view flywheel →" : "view flywheel →"}
          </div>
        </div>
      </button>
    </div>
  );
}

/* ── area detail ───────────────────────────────────────────── */

function AreaDetail({ area }) {
  const a = area;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
        <span style={{ width: 5, height: 30, borderRadius: 3, background: a.accent }} />
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 24, fontWeight: 600, color: T.ink }}>{a.name}</span>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.muted }}>{a.tag}</span>
        <span style={{ flex: 1 }} />
        <Dot status={a.status} />
      </div>

      <div className="cc-twocol">
        <Card style={{ flex: "1 1 340px", minWidth: 300 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <PanelLabel accent={a.accent}>Financial · P&amp;L</PanelLabel>
            <Source name="QuickBooks" />
          </div>
          {a.pl && a.pl.length > 0 ? <PLTable rows={a.pl} area={a} /> : <PLEmpty area={a} />}
        </Card>
        <Card style={{ flex: "1 1 340px", minWidth: 300 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <PanelLabel accent={a.accent}>Operational · leading indicators</PanelLabel>
            <span style={{ display: "flex", gap: 6 }}>{a.sources.filter((s) => s !== "QuickBooks").map((s) => <Source key={s} name={s} />)}</span>
          </div>
          <div className="cc-ops">{a.ops.map((d, i) => <OpTile key={i} d={d} />)}</div>
        </Card>
      </div>

      {a.funnel && (
        <Card>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <PanelLabel accent={a.accent}>{a.key === "sympli" ? "Loan pipeline" : "Lead-to-close funnel"}</PanelLabel>
            <Source name={a.key === "sympli" ? "Arive" : "Follow Up Boss · Sisu"} />
          </div>
          <Bars rows={a.funnel} accent={a.accent} />
        </Card>
      )}
    </div>
  );
}

/* ── flywheel ──────────────────────────────────────────────── */

function FlowNode({ color, big, label, sub, alt }) {
  return (
    <div style={{ flex: "1 1 130px", minWidth: 118 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ width: 9, height: 9, borderRadius: 2, background: color }} />
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 34, fontWeight: 700, color: T.ink, fontVariantNumeric: "tabular-nums" }}>{big}</span>
      </div>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.ink, fontWeight: 600, marginTop: 4 }}>{label}</div>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: alt ? T.poppyText : T.muted, marginTop: 1 }}>{sub}</div>
    </div>
  );
}

function Flywheel({ flywheel }) {
  const fw = flywheel || {};
  const available = fw.available !== false;
  const buyerClosings = fw.buyer_closings ?? 0;
  const captured = fw.captured ?? 0;
  const perLoanShare = fw.per_loan_share ?? 0;
  const agents = fw.agents || [];
  const uncaptured = buyerClosings - captured;
  const pct = fw.capture_pct != null
    ? fw.capture_pct
    : (buyerClosings ? Math.round((captured / buyerClosings) * 100) : 0);
  const monthlyGap = fw.monthly_gap != null ? fw.monthly_gap : uncaptured * perLoanShare;
  const maxRef = Math.max(...agents.map((a) => a.refs), 1);
  return (
    <div style={{ position: "relative", display: "flex", flexDirection: "column", gap: 18 }}>
      {!available && (
        <div style={{
          position: "absolute", inset: -8, zIndex: 5, borderRadius: 16,
          background: "rgba(248,245,242,0.72)", backdropFilter: "blur(1.5px)",
          display: "flex", alignItems: "center", justifyContent: "center", padding: 24,
        }}>
          <div style={{
            background: T.evergreen, borderRadius: 14, padding: "18px 24px", textAlign: "center",
            boxShadow: "0 12px 30px rgba(0,46,44,.18)",
          }}>
            <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 15, fontWeight: 600, color: T.onDark }}>Unlocks when Arive is connected</div>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.onDarkMute, marginTop: 4 }}>Phase 3</div>
          </div>
        </div>
      )}
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
        <span style={{ width: 5, height: 30, borderRadius: 3, background: `linear-gradient(${T.meadow},${T.teal})` }} />
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 24, fontWeight: 600, color: T.ink }}>The Referral Flywheel</span>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.muted }}>ULRG → Sympli · the connection QuickBooks can't see</span>
      </div>

      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <PanelLabel>Buyer-side capture · this month</PanelLabel>
          <span style={{ display: "flex", gap: 6 }}><Source name="Sisu" /><Source name="Follow Up Boss" /><Source name="Arive" /></span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <FlowNode color={T.meadow} big={buyerClosings} label="ULRG buyer closings" sub="financeable deals" />
          <span style={{ fontSize: 22, color: T.slate, padding: "0 4px" }}>→</span>
          <FlowNode color={T.teal} big={captured} label="Financed via Sympli" sub={`${pct}% capture`} />
          <span style={{ fontSize: 22, color: T.muted, padding: "0 4px" }}>→</span>
          <FlowNode color={T.poppy} big={uncaptured} label="Financed elsewhere" sub="walked out the door" alt />
        </div>
        <div style={{ marginTop: 22 }}>
          <div style={{ height: 14, background: T.parchment, borderRadius: 7, overflow: "hidden", display: "flex" }}>
            <div style={{ width: `${pct}%`, background: T.meadow }} />
            <div style={{ width: `${100 - pct}%`, background: T.poppy }} />
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8 }}>
            <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: "#4F6A4D", fontWeight: 600 }}>{pct}% captured</span>
            <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.poppyText, fontWeight: 600 }}>{100 - pct}% lost · target 60%+</span>
          </div>
        </div>
      </Card>

      <div className="cc-twocol">
        <Card style={{ flex: "1 1 240px", background: T.evergreen, border: "none" }}>
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.onDarkMute, marginBottom: 8 }}>Revenue left on the table</div>
          <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 40, fontWeight: 700, color: T.poppy, lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>{usd(monthlyGap)}</div>
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.onDarkMute, marginTop: 8 }}>
            this month · <span style={{ color: T.onDark, fontWeight: 600 }}>{usd(monthlyGap * 12)}/yr</span> at current pace
          </div>
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.onDarkMute, marginTop: 14, lineHeight: 1.5 }}>
            {uncaptured} uncaptured deals × {usd(perLoanShare)} JV share per loan. Revenue Spring already co-owns and isn't collecting.
          </div>
        </Card>
        <Card style={{ flex: "1 1 340px", minWidth: 300 }}>
          <PanelLabel accent={T.meadow}>Who refers, who doesn't</PanelLabel>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {agents.map((ag, i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <span style={{ width: 96, fontFamily: "Inter,sans-serif", fontSize: 12.5, color: ag.gap ? T.poppyText : T.ink, fontWeight: ag.gap ? 600 : 500 }}>{ag.name}</span>
                <div style={{ flex: 1, height: 18, background: T.parchment, borderRadius: 5, overflow: "hidden" }}>
                  <div style={{ width: `${(ag.refs / maxRef) * 100}%`, height: "100%", background: ag.gap ? T.poppy : T.meadow, borderRadius: 5 }} />
                </div>
                <span style={{ width: 56, fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600, color: ag.gap ? T.poppyText : T.ink, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{ag.gap ? "0 ⚠" : ag.refs}</span>
              </div>
            ))}
          </div>
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate, marginTop: 14, lineHeight: 1.5 }}>
            Eight producing agents sent zero loan referrals this month. That's the list to work, not a vague "improve capture."
          </div>
        </Card>
      </div>
    </div>
  );
}

/* ── splash ────────────────────────────────────────────────── */

function Splash({ label, tone }) {
  return (
    <div style={{
      minHeight: "100vh", background: T.evergreen, display: "flex", alignItems: "center", justifyContent: "center",
      fontFamily: "Inter,sans-serif", padding: 24,
      backgroundImage: `repeating-linear-gradient(90deg, rgba(255,255,255,0.045) 0px, rgba(255,255,255,0.045) 1.5px, rgba(255,255,255,0) 1.5px, rgba(255,255,255,0) 13px), radial-gradient(135% 130% at 50% -15%, rgba(97,131,94,0.50) 0%, rgba(0,46,44,0) 55%)`,
    }}>
      <style>{`@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&family=Inter:wght@400;500;600&family=Sacramento&display=swap');`}</style>
      <div style={{ textAlign: "center" }}>
        <div style={{ fontFamily: "Sacramento,cursive", fontSize: 56, color: T.onDark, lineHeight: 1 }}>Spring</div>
        <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 10, fontWeight: 700, letterSpacing: "0.18em", color: T.sprout, marginTop: 8, textTransform: "uppercase" }}>Command Center</div>
        <div style={{ marginTop: 22, fontFamily: "Inter,sans-serif", fontSize: 14, color: tone === "error" ? T.petal : T.onDarkMute }}>{label}</div>
      </div>
    </div>
  );
}

/* ── shell ─────────────────────────────────────────────────── */

const NAV = [
  { k: "overview", label: "Portfolio", dot: T.parchment },
  { k: "ulrg", label: "ULRG + Team", dot: T.meadow },
  { k: "springb", label: "Spring B", dot: T.poppy },
  { k: "sympli", label: "Sympli Mortgage", dot: T.teal },
  { k: "flywheel", label: "Referral Flywheel", dot: T.poppy, divide: true },
];

export default function CommandCenter() {
  const { data, loading, error, usingSample } = useDashboard("mtd");
  const [view, setView] = useState("overview");

  if (loading) return <Splash label="Loading your numbers…" />;
  if (error || !data) return <Splash label="Couldn't reach the API." tone="error" />;

  const { areas, flywheel, sources, period } = data;

  return (
    <div style={{ background: T.parchment, minHeight: "100%", fontFamily: "Inter,sans-serif" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&family=Inter:wght@400;500;600&family=Sacramento&display=swap');
        .cc-card { transition: transform .15s ease, box-shadow .15s ease; }
        .cc-card:hover { transform: translateY(-2px); box-shadow: 0 12px 30px rgba(0,46,44,.10); }
        .cc-nav { transition: background .12s ease; }
        .cc-nav:focus-visible, .cc-card:focus-visible { outline: 2px solid ${T.poppy}; outline-offset: 2px; }
        .cc-cards { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 16px; }
        .cc-score { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 12px; }
        .cc-ops { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
        .cc-twocol { display: flex; gap: 18px; flex-wrap: wrap; align-items: stretch; }
        @media (max-width: 900px) { .cc-score { grid-template-columns: repeat(2, minmax(0,1fr)); } }
        @media (max-width: 820px) { .cc-cards { grid-template-columns: 1fr; } }
        @media (max-width: 560px) { .cc-score { grid-template-columns: 1fr; } }
        @media (prefers-reduced-motion: reduce) { .cc-card, .cc-nav { transition: none; } .cc-card:hover { transform: none; } }
      `}</style>

      <div style={{ display: "flex", minHeight: "100%" }}>
        {/* Rail */}
        <aside style={{ width: 224, background: T.evergreen, padding: "24px 16px", display: "flex", flexDirection: "column", flexShrink: 0 }}>
          <div style={{ padding: "0 8px 22px" }}>
            <div style={{ fontFamily: "Sacramento,cursive", fontSize: 34, color: T.onDark, lineHeight: 1 }}>Spring</div>
            <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 10, fontWeight: 700, letterSpacing: "0.18em", color: T.sprout, marginTop: 6, textTransform: "uppercase" }}>Command Center</div>
          </div>
          {NAV.map((n) => {
            const active = view === n.k;
            return (
              <button key={n.k} className="cc-nav" onClick={() => setView(n.k)} style={{
                display: "flex", alignItems: "center", gap: 10, width: "100%", textAlign: "left",
                background: active ? "rgba(248,245,242,0.10)" : "transparent", border: "none",
                borderLeft: active ? `3px solid ${T.poppy}` : "3px solid transparent", borderRadius: 8,
                padding: "10px 10px", cursor: "pointer", marginTop: n.divide ? 14 : 2,
                borderTop: n.divide ? "1px solid rgba(248,245,242,0.10)" : "none", paddingTop: n.divide ? 16 : 10,
              }}>
                <span style={{ width: 8, height: 8, borderRadius: 2, background: n.dot, flexShrink: 0 }} />
                <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13.5, fontWeight: active ? 600 : 500, color: active ? T.onDark : T.onDarkMute }}>{n.label}</span>
              </button>
            );
          })}
          <div style={{ flex: 1 }} />
          {usingSample && (
            <div style={{ padding: "0 8px" }}>
              <span style={{
                display: "inline-block", fontFamily: "Inter,sans-serif", fontSize: 10, fontWeight: 700,
                letterSpacing: "0.08em", textTransform: "uppercase", color: T.sprout,
                background: "rgba(184,204,184,0.12)", border: "1px solid rgba(184,204,184,0.20)",
                borderRadius: 5, padding: "3px 8px",
              }}>Sample data</span>
            </div>
          )}
        </aside>

        {/* Main */}
        <main style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "14px 26px", borderBottom: `1px solid ${T.line}`, background: T.white, flexWrap: "wrap", gap: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.slate }}>As of</span>
              <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 13.5, fontWeight: 600, color: T.ink }}>{formatAsOf(period?.as_of)}</span>
              <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, fontWeight: 600, color: T.slate, background: T.parchment, borderRadius: 5, padding: "3px 9px", border: `1px solid ${T.line}` }}>
                {period?.label === "MTD" ? "Month to date" : period?.label}
              </span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted, marginRight: 2 }}>Live from</span>
              {(sources || []).map((s) => (
                <span key={s.name} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                  <span style={{ width: 6, height: 6, borderRadius: 99, background: s.status === "connected" ? T.meadow : T.muted }} /><Source name={s.name} />
                </span>
              ))}
            </div>
          </div>

          <div style={{ padding: 26, maxWidth: 1100 }}>
            {view === "overview" && <Overview data={data} onOpen={setView} />}
            {(view === "ulrg" || view === "springb" || view === "sympli") && <AreaDetail area={areas[view]} />}
            {view === "flywheel" && <Flywheel flywheel={flywheel} />}
          </div>
        </main>
      </div>
    </div>
  );
}
