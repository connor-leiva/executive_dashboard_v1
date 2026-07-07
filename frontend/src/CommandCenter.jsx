import { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import { T, STATUS, usd, signed, relativeTime } from "./theme.js";
import { useDashboard } from "./useDashboard.js";
import { useForum } from "./useForum.js";
import { useBecollective } from "./useBecollective.js";
import { getJSON, postJSON } from "./api.js";
import AuditDrawer from "./AuditDrawer.jsx";
import Financials from "./Financials.jsx";
import ForumView, { BeCollectivePlaceholder, BC_DECK_SLOTS } from "./ForumView.jsx";
import { SpringSignature, ribbedHero, Icon } from "./Brand.jsx";

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
    case "forum":
      return T.daffodil;
    case "becollective":
      return T.petal;
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
              }}>{signed(r.value)}</span>
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

function OpTile({ d, onDrill }) {
  const clickable = Boolean(d.key) && Boolean(onDrill);
  const Tag = clickable ? "button" : "div";
  return (
    <Tag onClick={clickable ? () => onDrill(d.key) : undefined} className={clickable ? "cc-card" : undefined}
      style={{ display: "block", textAlign: "left", width: "100%", minWidth: 0, boxSizing: "border-box", margin: 0, font: "inherit",
        border: "none", background: T.parchment, borderRadius: 10, padding: "12px 13px", cursor: clickable ? "pointer" : "default" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.slate, fontWeight: 500 }}>{d.label}</span>
        {clickable && <span style={{ marginLeft: "auto", fontSize: 10.5, color: T.muted }}>↗</span>}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 22, fontWeight: 600, color: T.ink, fontVariantNumeric: "tabular-nums" }}>{d.value}</span>
        {d.sub && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted }}>{d.sub}</span>}
      </div>
    </Tag>
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
                NOI <span style={{ color: a.ink, fontWeight: 700 }}>{signed(a.noi)}</span> · {a.margin}%
              </div>
            </>
          ) : a.key === "becollective" ? (
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.muted, maxWidth: 160, lineHeight: 1.4 }}>
              Operational view coming · GHL segment configured
            </div>
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

function Overview({ data, onOpen, onDrill }) {
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
  // MTD → the current month name; other periods → the descriptive label.
  const periodLabel = (period?.label === "Month to date")
    ? monthYear(period?.as_of)
    : (period?.label || monthYear(period?.as_of));
  const orderedCards = [areas.ulrg, areas.forum, areas.becollective, areas.sympli].filter(Boolean);
  const fwAvailable = fw.available !== false && buyerClosings != null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      {/* Hero */}
      <div style={{
        position: "relative", overflow: "hidden", borderRadius: 16, padding: 28,
        ...ribbedHero("evergreen"),
      }}>
        <SpringSignature tone="light" height={46} aria-hidden style={{ position: "absolute", top: 14, right: 26, opacity: 0.12, pointerEvents: "none" }} />
        <Eyebrow onDark>Portfolio · {periodLabel}</Eyebrow>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 34, flexWrap: "wrap", margin: "16px 0 24px" }}>
          <div>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.onDarkMute, marginBottom: 5 }}>Portfolio revenue · {(period?.label || "month to date").toLowerCase()}</div>
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
          {scorecards.map((s, i) => {
            const clickable = Boolean(s.key) && Boolean(onDrill);
            const Tag = clickable ? "button" : "div";
            return (
              <Tag key={i} onClick={clickable ? () => onDrill(s.key) : undefined}
                className={clickable ? "cc-card" : undefined}
                style={{
                  textAlign: "left", width: "100%", minWidth: 0, boxSizing: "border-box", margin: 0, font: "inherit",
                  background: T.white, border: `1px solid ${T.line}`,
                  borderRadius: 12, padding: "16px 16px", cursor: clickable ? "pointer" : "default",
                }}>
                <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 9 }}>
                  <span style={{ width: 7, height: 7, borderRadius: 2, background: dotFor(s.business_key) }} />
                  <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate, fontWeight: 500 }}>{s.label}</span>
                  {clickable && <span style={{ marginLeft: "auto", fontSize: 11, color: T.muted }}>↗</span>}
                </div>
                <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 30, fontWeight: 700, color: T.ink, lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>{s.value}</div>
                <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted, marginTop: 6 }}>{s.sub}</div>
              </Tag>
            );
          })}
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

/* Per-LO performance — Sympli only. Each officer's funded / volume / avg / gross
   commission / pull-through, drillable to their funded loans. */
function LoanOfficers({ los, onDrill }) {
  const maxRev = Math.max(...los.map((l) => l.revenue), 1);
  const cell = (w) => ({ width: w, textAlign: "right", fontVariantNumeric: "tabular-nums" });
  const head = { fontFamily: "Inter,sans-serif", fontSize: 10, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color: T.muted };
  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <PanelLabel accent={T.teal}>Loan officers · this period</PanelLabel>
        <Source name="Arive" />
      </div>
      <div style={{ overflowX: "auto" }}>
        <div style={{ minWidth: 560 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "7px 4px", borderBottom: `1px solid ${T.line}` }}>
            <span style={{ ...head, flex: "1 1 130px", minWidth: 110 }}>Officer</span>
            <span style={{ ...head, ...cell(48) }}>Funded</span>
            <span style={{ ...head, ...cell(74) }}>Volume</span>
            <span style={{ ...head, ...cell(74) }}>Avg loan</span>
            <span style={{ ...head, flex: "1 1 130px", minWidth: 110, textAlign: "left" }}>Gross commission</span>
            <span style={{ ...head, ...cell(64) }}>Pull-thru</span>
          </div>
          {los.map((lo) => (
            <div key={lo.email} onClick={onDrill ? () => onDrill("sympli_commission", "sympli", null, lo.email) : undefined}
              className={onDrill ? "cc-card" : undefined} title={onDrill ? `${lo.name}'s funded loans` : undefined}
              style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 4px", margin: "0 -4px", borderRadius: 6, cursor: onDrill ? "pointer" : "default", borderBottom: `1px solid ${T.line}` }}>
              <span style={{ flex: "1 1 130px", minWidth: 110, fontFamily: "Inter,sans-serif", fontSize: 13, fontWeight: 600, color: T.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{lo.name}</span>
              <span style={{ ...cell(48), fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600, color: T.ink }}>{lo.funded}</span>
              <span style={{ ...cell(74), fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.slate }}>{usd(lo.volume)}</span>
              <span style={{ ...cell(74), fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.slate }}>{usd(lo.avg_loan)}</span>
              <span style={{ flex: "1 1 130px", minWidth: 110, display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ flex: 1, height: 14, background: T.parchment, borderRadius: 5, overflow: "hidden" }}>
                  <span style={{ display: "block", width: `${(lo.revenue / maxRev) * 100}%`, height: "100%", background: T.teal, borderRadius: 5 }} />
                </span>
                <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.ink, fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>{usd(lo.revenue)}</span>
              </span>
              <span style={{ ...cell(64), fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600, color: lo.pull_through >= 60 ? T.meadow : T.slate }}>{lo.pull_through}%</span>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

function AreaDetail({ area, onDrill, period }) {
  const a = area;
  // Three-lens financials for ULRG (Sisu) and Sympli (Arive commissions vs booked).
  const hasThreeLens = a.key === "ulrg" || a.key === "sympli";

  const opsCard = (
    <Card style={{ flex: "1 1 340px", minWidth: 300 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <PanelLabel accent={a.accent}>Operational · leading indicators</PanelLabel>
        <span style={{ display: "flex", gap: 6 }}>{a.sources.filter((s) => s !== "QuickBooks").map((s) => <Source key={s} name={s} />)}</span>
      </div>
      <div className="cc-ops">{a.ops.map((d, i) => <OpTile key={i} d={d} onDrill={onDrill} />)}</div>
    </Card>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
        <span style={{ width: 5, height: 30, borderRadius: 3, background: a.accent }} />
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 24, fontWeight: 600, color: T.ink }}>{a.name}</span>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.muted }}>{a.tag}</span>
        <span style={{ flex: 1 }} />
        <Dot status={a.status} />
      </div>

      {hasThreeLens ? (
        <>
          {/* Three-lens financial view (Live / Projection / Booked) replaces the single P&L pane. */}
          <Financials businessKey={a.key} businessName={a.name} period={period} onDrill={onDrill} />
          {opsCard}
          {a.loan_officers?.length > 0 && <LoanOfficers los={a.loan_officers} onDrill={onDrill} />}
        </>
      ) : (
        <div className="cc-twocol">
          <Card style={{ flex: "1 1 340px", minWidth: 300 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <PanelLabel accent={a.accent}>Financial · P&amp;L</PanelLabel>
              <Source name="QuickBooks" />
            </div>
            {a.pl && a.pl.length > 0 ? <PLTable rows={a.pl} area={a} /> : <PLEmpty area={a} />}
          </Card>
          {opsCard}
        </div>
      )}

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

/* ── flywheel v2 (spec: flywheel-view-v2) ──────────────────────── */

function ReconRow({ label, value, good, warn }) {
  const color = warn ? T.poppyText : good ? "#4D6A4D" : T.ink;
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
      <span style={{ color: T.slate }}>{label}</span>
      <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 14, fontWeight: 600, color, fontVariantNumeric: "tabular-nums" }}>{warn ? `${value} ⚠` : value}</span>
    </div>
  );
}

/* one colored capture component (dot + big number + label + subline), drillable */
function FwStat({ color, big, label, sub, subColor, onClick }) {
  return (
    <div onClick={onClick} className={onClick ? "cc-card" : undefined}
      style={{ cursor: onClick ? "pointer" : "default", borderRadius: 8, padding: onClick ? "4px 8px" : 0, margin: onClick ? "-4px -8px" : 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ width: 9, height: 9, borderRadius: 2, background: color }} />
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 32, fontWeight: 700, color: T.ink, lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>{big}</span>
      </div>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.ink, marginTop: 5 }}>{label}</div>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: subColor || T.muted, marginTop: 1 }}>{sub}</div>
    </div>
  );
}

const FwDrill = ({ onClick, children }) => (
  <span onClick={onClick} style={{ color: T.teal, fontWeight: 600, cursor: onClick ? "pointer" : "default" }}>
    {children}{onClick && <Icon name="open" size={11} color={T.teal} style={{ marginLeft: 4, verticalAlign: "-1px" }} />}
  </span>
);

const GhostDark = ({ children }) => (
  <Link to="/settings/businesses" style={{ display: "inline-flex", alignItems: "center", gap: 6, marginTop: 16,
    background: "rgba(248,245,242,.08)", border: "1px solid rgba(248,245,242,.2)", borderRadius: 8, padding: "6px 12px",
    fontFamily: "Poppins,sans-serif", fontSize: 11.5, fontWeight: 600, color: T.onDark, textDecoration: "none" }}>
    <Icon name="tune" size={12} color={T.onDark} />{children}
  </Link>
);

/* Row 1 left — the headline attach-rate KPI with progress-to-target + delta. */
function AttachTile({ fw }) {
  const pct = fw.capture_pct ?? 0;
  const target = fw.capture_target ?? 60;
  const delta = fw.attach_delta_pts;
  const label = fw.period_label || "this period";
  return (
    <Card style={{ flex: "0 1 320px", minWidth: 280 }}>
      <PanelLabel>Attach rate · {label}</PanelLabel>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 46, fontWeight: 700, color: T.ink, lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>{pct}%</span>
        {delta != null && (
          <span title="vs the prior comparable period" style={{ fontFamily: "Inter,sans-serif", fontSize: 12, fontWeight: 600, color: delta >= 0 ? T.meadow : T.slate }}>
            {delta >= 0 ? "+" : ""}{delta} pts
          </span>
        )}
      </div>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.slate, marginTop: 6 }}>of ULRG buyer closings financed via Sympli</div>
      <div style={{ marginTop: 18 }}>
        <div style={{ position: "relative", height: 9, borderRadius: 6, background: T.parchment, border: `1px solid ${T.line}` }}>
          <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${Math.min(pct, 100)}%`, background: T.teal, borderRadius: 6 }} />
          <div title={`target ${target}%`} style={{ position: "absolute", left: `${target}%`, top: -4, bottom: -4, width: 2, background: T.evergreen, borderRadius: 2 }} />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted }}>
          <span style={{ color: T.teal, fontWeight: 600 }}>{pct}% today</span>
          <span>target {target}%</span>
        </div>
      </div>
      {fw.per_point_value != null && (
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate, marginTop: 16, lineHeight: 1.5 }}>
          Every point of attach ≈ <b style={{ color: T.ink }}>{usd(fw.per_point_value)}</b>/yr in JV revenue at current volume.
        </div>
      )}
    </Card>
  );
}

/* Row 1 right — the shortened capture flow: two colored figures + bar. */
function CaptureCard({ fw, onDrill }) {
  const closings = fw.buyer_closings ?? 0;
  const captured = fw.captured ?? 0;
  const lost = fw.lost ?? (closings - captured);
  const pct = fw.capture_pct ?? 0;
  const target = fw.capture_target ?? 60;
  const label = fw.period_label || "this period";
  const drill = (k) => (onDrill ? () => onDrill(k, "sympli") : undefined);
  return (
    <Card style={{ flex: "1 1 380px", minWidth: 300 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <PanelLabel>Buyer-side capture · {label}</PanelLabel>
        <span style={{ display: "flex", gap: 6 }}><Source name="Sisu" /><Source name="Follow Up Boss" /><Source name="Arive" /></span>
      </div>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.slate, marginBottom: 14 }}>
        Of <FwDrill onClick={drill("flywheel_buyers")}>{closings} financeable buyer closings</FwDrill> {label}…
      </div>
      <div style={{ display: "flex", gap: 34, flexWrap: "wrap", marginBottom: 16 }}>
        <FwStat color={T.teal} big={captured} label="Financed via Sympli" sub={`${pct}% capture`} onClick={drill("flywheel_captured")} />
        <FwStat color={T.poppy} big={lost} label="Financed elsewhere" sub="walked out the door" subColor={T.poppyText} onClick={drill("flywheel_uncaptured")} />
      </div>
      <div style={{ position: "relative" }}>
        <div style={{ display: "flex", height: 12, borderRadius: 7, overflow: "hidden", gap: 2 }}>
          <div style={{ width: `${pct}%`, background: T.teal }} />
          <div style={{ width: `${100 - pct}%`, background: T.poppy, opacity: 0.85 }} />
        </div>
        <div title={`capture target ${target}%`} style={{ position: "absolute", left: `${target}%`, top: -4, bottom: -4, width: 2, background: T.evergreen, borderRadius: 2 }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontFamily: "Inter,sans-serif", fontSize: 11.5 }}>
        <span style={{ color: T.teal, fontWeight: 600 }}>{pct}% captured · target {target}%+</span>
        <span style={{ color: T.poppyText, fontWeight: 600 }}>{100 - pct}% lost</span>
      </div>
    </Card>
  );
}

/* Row 2 left — config-aware money card. Setup prompt when JV share is unset. */
function MoneyCard({ fw }) {
  const share = fw.per_loan_share;    // null/0 → unset
  const label = fw.period_label || "this period";
  const target = fw.capture_target ?? 60;
  const cardStyle = { flex: "0 1 320px", minWidth: 280, background: T.evergreen, border: "none" };
  if (!share) {
    return (
      <Card style={cardStyle}>
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.onDarkMute, marginBottom: 10 }}>Revenue left on the table</div>
        <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 16, fontWeight: 600, color: T.onDark, lineHeight: 1.45 }}>
          Set Sympli's JV share per loan to price the gap.
        </div>
        <GhostDark>Set JV share per loan</GhostDark>
      </Card>
    );
  }
  return (
    <Card style={cardStyle}>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.onDarkMute, marginBottom: 8 }}>Revenue left on the table</div>
      <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 38, fontWeight: 700, color: T.poppy, lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>{usd(fw.gap_dollars ?? 0)}</div>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.onDarkMute, marginTop: 8 }}>{label} at {fw.capture_pct ?? 0}% capture</div>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.onDarkMute, marginTop: 14, lineHeight: 1.55 }}>
        {fw.lost ?? 0} uncaptured deals × {usd(share)} JV share per loan — revenue Spring already co-owns and isn't
        collecting. At the {target}% target, this shrinks to <b style={{ color: T.onDark }}>{usd(fw.gap_at_target ?? 0)}</b>.
      </div>
      <GhostDark>JV share {usd(share)} · edit</GhostDark>
    </Card>
  );
}

/* Row 2 right — leaderboard: top-N referrers, expand to the full reconciling list. */
function Leaderboard({ fw, onDrill }) {
  const [expanded, setExpanded] = useState(false);
  const referrers = fw.referrers || [];
  const zeroAgents = fw.zero_agents || [];
  const rows = expanded ? referrers : referrers.slice(0, 6);
  const max = Math.max(...referrers.map((a) => a.refs), 1);
  const totalLoans = referrers.reduce((a, r) => a + r.refs, 0);
  const label = fw.period_label || "this period";
  return (
    <Card style={{ flex: "1 1 380px", minWidth: 300 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <PanelLabel accent={T.meadow}>Who refers, who doesn't</PanelLabel>
        <span style={{ display: "flex", gap: 6 }}><Source name="Sisu" /><Source name="Arive" /></span>
      </div>
      <div style={expanded ? { maxHeight: 296, overflowY: "auto", paddingRight: 4, display: "flex", flexDirection: "column", gap: 9 } : { display: "flex", flexDirection: "column", gap: 9 }}>
        {rows.map((ag) => (
          <div key={ag.id || ag.name} onClick={onDrill ? () => onDrill("flywheel_agent_referrals", "sympli", ag.id) : undefined}
            className={onDrill ? "cc-card" : undefined} title={onDrill ? "See this agent's Sympli referrals" : undefined}
            style={{ display: "flex", alignItems: "center", gap: 12, cursor: onDrill ? "pointer" : "default", borderRadius: 6, padding: "2px 5px", margin: "0 -5px" }}>
            <span style={{ width: 118, fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{ag.name}</span>
            <div style={{ flex: 1, height: 16, background: T.parchment, borderRadius: 5, overflow: "hidden" }}>
              <div style={{ width: `${(ag.refs / max) * 100}%`, height: "100%", background: T.meadow, borderRadius: 5 }} />
            </div>
            <span style={{ width: 22, fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600, color: T.ink, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{ag.refs}</span>
          </div>
        ))}
      </div>
      {referrers.length > 6 && (
        <button onClick={() => setExpanded(!expanded)} aria-expanded={expanded}
          style={{ display: "inline-flex", alignItems: "center", gap: 7, background: "none", border: "none", cursor: "pointer", marginTop: 12, padding: "4px 2px", fontFamily: "Poppins,sans-serif", fontSize: 11.5, fontWeight: 600, color: T.slate }}>
          <Icon name="chevron_down" size={12} color={T.slate} style={{ transform: expanded ? "rotate(180deg)" : "none", transition: "transform .2s ease" }} />
          {expanded ? "Show top referrers" : `Show all ${referrers.length} referring agents · ${totalLoans} loans`}
        </button>
      )}
      {zeroAgents.length > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginTop: 14, paddingTop: 14, borderTop: `1px solid ${T.line}` }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 7, background: T.daffodilBg, borderRadius: 7, padding: "5px 11px", fontFamily: "Inter,sans-serif", fontSize: 11.5, fontWeight: 600, color: T.daffodilText }}>
            <Icon name="warning" size={12} color={T.daffodilText} />{zeroAgents.length} producing agents · zero referrals {label}
          </span>
          <button onClick={onDrill ? () => onDrill("flywheel_zero_referrals", "sympli") : undefined}
            style={{ display: "inline-flex", alignItems: "center", gap: 6, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "6px 12px", cursor: "pointer", fontFamily: "Poppins,sans-serif", fontSize: 11.5, fontWeight: 600, color: T.slate }}>
            View the list<Icon name="open" size={11} color={T.slate} />
          </button>
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted }}>that's the call list, not "improve capture"</span>
        </div>
      )}
    </Card>
  );
}

function Flywheel({ flywheel, onDrill }) {
  const fw = flywheel || {};
  const available = fw.available !== false;
  const drill = available ? onDrill : null;
  const lostTo = fw.lost_to || [];
  return (
    <div style={{ position: "relative", display: "flex", flexDirection: "column", gap: 16 }}>
      {!available && (
        <div style={{ position: "absolute", inset: -8, zIndex: 5, borderRadius: 16, background: "rgba(248,245,242,0.72)", backdropFilter: "blur(1.5px)", display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
          <div style={{ background: T.evergreen, borderRadius: 14, padding: "18px 24px", textAlign: "center", boxShadow: "0 12px 30px rgba(0,46,44,.18)" }}>
            <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 15, fontWeight: 600, color: T.onDark }}>Unlocks when Arive is connected</div>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.onDarkMute, marginTop: 4 }}>Connect Sympli's Arive to light up the flywheel</div>
          </div>
        </div>
      )}
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 2 }}>
        <Icon name="spark" size={20} color={T.poppyText} />
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 22, fontWeight: 700, color: T.ink, letterSpacing: "-.01em" }}>The Referral Flywheel</span>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.muted }}>ULRG → Sympli · the connection QuickBooks can't see</span>
      </div>

      <div className="cc-twocol"><AttachTile fw={fw} /><CaptureCard fw={fw} onDrill={drill} /></div>
      <div className="cc-twocol"><MoneyCard fw={fw} /><Leaderboard fw={fw} onDrill={drill} /></div>

      {(lostTo.length > 0 || fw.sympli_referred != null) && (
        <div className="cc-twocol">
          {lostTo.length > 0 && (
            <Card style={{ flex: "1 1 340px", minWidth: 300 }}>
              <PanelLabel accent={T.poppy}>Where the rest went</PanelLabel>
              <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
                {lostTo.map((l, i) => {
                  const max = Math.max(...lostTo.map((x) => x.count), 1);
                  return (
                    <div key={i} style={{ display: "flex", alignItems: "center", gap: 12 }}>
                      <span title={l.name} style={{ width: 160, fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{l.name}</span>
                      <div style={{ flex: 1, height: 16, background: T.parchment, borderRadius: 5, overflow: "hidden" }}>
                        <div style={{ width: `${(l.count / max) * 100}%`, height: "100%", background: T.poppy, borderRadius: 5 }} />
                      </div>
                      <span style={{ width: 26, textAlign: "right", fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600, color: T.ink, fontVariantNumeric: "tabular-nums" }}>{l.count}</span>
                    </div>
                  );
                })}
              </div>
              <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate, marginTop: 12, lineHeight: 1.5 }}>
                The lenders winning ULRG's buyers — from the mortgage vendor each agent selected in Sisu.
              </div>
            </Card>
          )}
          {fw.sympli_referred != null && (
            <Card style={{ flex: "1 1 300px", minWidth: 280 }}>
              <PanelLabel accent={T.teal}>Cross-check with Sympli</PanelLabel>
              <div style={{ display: "flex", flexDirection: "column", gap: 11, fontFamily: "Inter,sans-serif", fontSize: 12.5 }}>
                <ReconRow label="Sympli loans credited to Utah Life" value={fw.sympli_referred} />
                <ReconRow label="…matched to a ULRG closing" value={fw.sympli_referred_linked} good />
                {fw.vendor_no_loan > 0 && <ReconRow label="Picked Sympli, no loan found" value={fw.vendor_no_loan} warn />}
                {fw.referral_no_deal > 0 && <ReconRow label="Sympli logged us, no ULRG deal" value={fw.referral_no_deal} warn />}
              </div>
              <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate, marginTop: 12, lineHeight: 1.5 }}>
                Triangulated from three signals — the agent's vendor pick, the borrower match, and Sympli's own referral record. Gaps are deals to reconcile.
              </div>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}

/* ── splash ────────────────────────────────────────────────── */

function Splash({ label, tone }) {
  return (
    <div style={{
      minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
      fontFamily: "Inter,sans-serif", padding: 24, ...ribbedHero("evergreen"),
    }}>
      <style>{`@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&family=Inter:wght@400;500;600&display=swap');`}</style>
      <div style={{ textAlign: "center" }}>
        <SpringSignature tone="light" height={52} />

        <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 10, fontWeight: 700, letterSpacing: "0.18em", color: T.sprout, marginTop: 8, textTransform: "uppercase" }}>Command Center</div>
        <div style={{ marginTop: 22, fontFamily: "Inter,sans-serif", fontSize: 14, color: tone === "error" ? T.petal : T.onDarkMute }}>{label}</div>
      </div>
    </div>
  );
}

/* ── states: skeletons, error, user menu ─────────────────────── */

function Skel({ w = "100%", h = 12, r = 8, style }) {
  return <span className="cc-skel" style={{ display: "block", width: w, height: h, borderRadius: r, ...style }} />;
}

function SkeletonCard({ h = 96 }) {
  return (
    <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 12, padding: 16 }}>
      <Skel w="55%" h={10} style={{ marginBottom: 14 }} />
      <Skel w="72%" h={h > 110 ? 30 : 24} />
    </div>
  );
}

function SkeletonDashboard() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }} aria-busy="true">
      <Skel h={150} r={16} />
      <div className="cc-score">{Array.from({ length: 8 }).map((_, i) => <SkeletonCard key={i} />)}</div>
      <div className="cc-cards">{Array.from({ length: 3 }).map((_, i) => <SkeletonCard key={i} h={130} />)}</div>
    </div>
  );
}

function ErrorState({ onRetry }) {
  return (
    <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 14, padding: 40, textAlign: "center" }}>
      <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 16, fontWeight: 600, color: T.ink }}>Couldn't reach the API</div>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.muted, marginTop: 6 }}>The dashboard service didn't respond.</div>
      <button onClick={onRetry} className="cc-nav" style={{
        marginTop: 16, fontFamily: "Inter,sans-serif", fontSize: 13, fontWeight: 600, color: T.onDark,
        background: T.evergreen, border: "none", borderRadius: 8, padding: "9px 16px", cursor: "pointer",
      }}>Retry</button>
    </div>
  );
}

function signOut() {
  const token = localStorage.getItem("cc_token");
  if (API_BASE && token) {
    fetch(`${API_BASE}/auth/logout`, { method: "POST", headers: { Authorization: `Bearer ${token}` } }).catch(() => {});
  }
  localStorage.removeItem("cc_token");
  window.location.reload();
}

function UserMenu({ user }) {
  const [open, setOpen] = useState(false);
  const first = (user?.name || "Account").split(" ")[0];
  return (
    <div className="cc-usermenu">
      <button onClick={() => setOpen((o) => !o)} className="cc-nav" style={{
        display: "inline-flex", alignItems: "center", gap: 7, fontFamily: "Inter,sans-serif",
        fontSize: 12.5, fontWeight: 600, color: T.slate, background: T.parchment,
        border: `1px solid ${T.line}`, borderRadius: 8, padding: "5px 10px", cursor: "pointer",
      }}>
        <span style={{
          width: 20, height: 20, borderRadius: 99, background: T.evergreen, color: T.onDark,
          display: "inline-flex", alignItems: "center", justifyContent: "center", fontSize: 10, fontWeight: 700,
        }}>{(user?.name || "?").slice(0, 1).toUpperCase()}</span>
        {first} ▾
      </button>
      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: "fixed", inset: 0, zIndex: 15 }} />
          <div className="cc-menu">
            <div style={{ padding: "4px 10px 8px", borderBottom: `1px solid ${T.line}` }}>
              <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.ink }}>{user?.name || "Account"}</div>
              {user?.email && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted }}>{user.email}</div>}
            </div>
            <Link to="/settings/integrations" onClick={() => setOpen(false)} style={{
              display: "flex", alignItems: "center", gap: 8, fontFamily: "Inter,sans-serif", fontSize: 13, fontWeight: 500, color: T.ink,
              textDecoration: "none", borderRadius: 6, padding: "8px 10px", marginTop: 4,
            }}><Icon name="settings" size={15} color={T.slate} />Settings</Link>
            <button onClick={signOut} style={{
              width: "100%", display: "flex", alignItems: "center", gap: 8, textAlign: "left", fontFamily: "Inter,sans-serif", fontSize: 13, fontWeight: 500,
              color: T.poppyText, background: "transparent", border: "none", borderRadius: 6,
              padding: "8px 10px", cursor: "pointer", marginTop: 4,
            }}><Icon name="logout" size={15} color={T.poppyText} />Sign out</button>
          </div>
        </>
      )}
    </div>
  );
}

function useMe() {
  const [user, setUser] = useState(null);
  useEffect(() => {
    if (!API_BASE) {
      setUser({ name: "Spring Bengtzen", email: "spring@springb.com" });
      return;
    }
    let alive = true;
    getJSON("/me").then((u) => alive && setUser(u)).catch(() => {});
    return () => { alive = false; };
  }, []);
  return user;
}

/* ── shell ─────────────────────────────────────────────────── */

const PERIODS = [
  { k: "mtd", label: "Month" },
  { k: "qtd", label: "Quarter" },
  { k: "ytd", label: "Year" },
  { k: "last_month", label: "Last month" },
];

function PeriodSelector({ value, onChange }) {
  return (
    <div style={{ display: "inline-flex", background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 8, padding: 2, gap: 2 }}>
      {PERIODS.map((p) => {
        const active = p.k === value;
        return (
          <button key={p.k} onClick={() => onChange(p.k)} className="cc-nav" style={{
            fontFamily: "Inter,sans-serif", fontSize: 12, fontWeight: 600,
            color: active ? T.onDark : T.slate, background: active ? T.evergreen : "transparent",
            border: "none", borderRadius: 6, padding: "5px 11px", cursor: "pointer",
          }}>{p.label}</button>
        );
      })}
    </div>
  );
}

const NAV = [
  { k: "overview", label: "Portfolio", dot: T.parchment },
  { k: "ulrg", label: "ULRG + Team", dot: T.meadow },
  { k: "forum", label: "The Forum", dot: T.daffodil },
  { k: "becollective", label: "beCollective", dot: T.petal },
  { k: "sympli", label: "Sympli Mortgage", dot: T.teal },
  { k: "flywheel", label: "Referral Flywheel", dot: T.poppy, divide: true },
];

export default function CommandCenter() {
  const [periodKey, setPeriodKey] = useState("mtd");
  const { data, loading, error, usingSample, retry } = useDashboard(periodKey);
  const forum = useForum(periodKey);
  const becollective = useBecollective(periodKey);
  const [view, setView] = useState("overview");
  const [refreshing, setRefreshing] = useState(false);
  const [drill, setDrill] = useState(null);       // { key, business, agentId, lo } for the audit drawer
  const onDrill = (key, business, agentId, lo) => setDrill(key ? { key, business, agentId, lo } : null);
  const user = useMe();

  const { areas, flywheel, sources, period } = data || {};
  const busy = loading && !data;
  const updated = (sources || []).map((s) => s.last_synced).filter(Boolean).sort().slice(-1)[0];

  async function doRefresh() {
    if (!API_BASE || refreshing) return;
    setRefreshing(true);
    try {
      const { job_id } = await postJSON(`/sync/all?period=${periodKey}`);
      for (let i = 0; i < 60; i++) {          // poll up to ~2 min
        await new Promise((r) => setTimeout(r, 2000));
        const st = await getJSON(`/sync/status/${job_id}`);
        if (st.status === "ok" || st.status === "error") break;
      }
      retry();                                 // refetch the dashboard
    } catch {
      /* leave the dashboard as-is on failure */
    } finally {
      setRefreshing(false);
    }
  }

  // Spring B is now split into The Forum + beCollective; keep any old springb
  // deep-link (e.g. the overview card) landing on The Forum.
  const activeView = view === "springb" ? "forum" : view;

  let content;
  if (busy) content = <SkeletonDashboard />;
  else if (error && !data) content = <ErrorState onRetry={retry} />;
  else if (activeView === "overview") content = <Overview data={data} onOpen={setView} onDrill={onDrill} />;
  else if (activeView === "forum") content = forum.data
    ? <ForumView data={forum.data} area={areas?.forum} onDrill={onDrill} />
    : <SkeletonDashboard />;
  else if (activeView === "becollective") content = becollective.data
    ? <ForumView data={becollective.data} area={areas?.becollective} onDrill={onDrill}
        title="beCollective" subtitle="Community" deckSlots={BC_DECK_SLOTS} drillBusiness="springb" />
    : <SkeletonDashboard />;
  else if (activeView === "ulrg" || activeView === "sympli") content = <AreaDetail area={areas[activeView]} onDrill={onDrill} period={periodKey} />;
  else if (activeView === "flywheel") content = <Flywheel flywheel={flywheel} onDrill={onDrill} />;

  return (
    <div style={{ background: T.parchment, minHeight: "100vh", fontFamily: "Inter,sans-serif" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&family=Inter:wght@400;500;600&display=swap');
        .cc-card { transition: transform .15s ease, box-shadow .15s ease; }
        .cc-card:hover { transform: translateY(-2px); box-shadow: 0 12px 30px rgba(0,46,44,.10); }
        .cc-nav { transition: background .12s ease; }
        .cc-nav:focus-visible, .cc-card:focus-visible { outline: 2px solid ${T.teal}; outline-offset: 2px; }
        .cc-cards { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 16px; }
        .cc-score { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 12px; }
        .cc-ops { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
        .cc-twocol { display: flex; gap: 18px; flex-wrap: wrap; align-items: stretch; }
        .cc-skel { background: linear-gradient(90deg, ${T.line} 25%, ${T.parchment} 50%, ${T.line} 75%); background-size: 800px 100%; animation: cc-shimmer 1.4s linear infinite; }
        @keyframes cc-shimmer { 0% { background-position: -400px 0; } 100% { background-position: 400px 0; } }
        @keyframes cc-spin { to { transform: rotate(360deg); } }
        .cc-usermenu { position: relative; }
        .cc-menu { position: absolute; right: 0; top: calc(100% + 8px); z-index: 20; min-width: 190px; background: ${T.white}; border: 1px solid ${T.line}; border-radius: 10px; box-shadow: 0 12px 30px rgba(0,46,44,.12); padding: 6px; }
        @media (max-width: 900px) { .cc-score { grid-template-columns: repeat(2, minmax(0,1fr)); } }
        @media (max-width: 820px) { .cc-cards { grid-template-columns: 1fr; } }
        @media (max-width: 560px) { .cc-score { grid-template-columns: 1fr; } }
        @media (prefers-reduced-motion: reduce) { .cc-card, .cc-nav { transition: none; } .cc-card:hover { transform: none; } .cc-skel { animation: none; } }
      `}</style>

      <div style={{ display: "flex", minHeight: "100vh" }}>
        {/* Rail */}
        <aside style={{ width: 224, background: T.evergreen, padding: "24px 16px", display: "flex", flexDirection: "column", flexShrink: 0 }}>
          <div style={{ padding: "0 8px 22px" }}>
            <SpringSignature tone="light" height={30} />
            <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 10, fontWeight: 700, letterSpacing: "0.18em", color: T.sprout, marginTop: 6, textTransform: "uppercase" }}>Command Center</div>
          </div>
          {NAV.map((n) => {
            const active = activeView === n.k;
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
              {period
                ? <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 13.5, fontWeight: 600, color: T.ink }}>{formatAsOf(period.as_of)}</span>
                : <Skel w={92} h={14} style={{ display: "inline-block" }} />}
              <PeriodSelector value={periodKey} onChange={setPeriodKey} />
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted, marginRight: 2 }}>Live from</span>
              {period
                ? (sources || []).map((s) => {
                    const stale = s.status === "connected" && s.last_synced &&
                      Date.now() - new Date(s.last_synced).getTime() > 2 * 3600 * 1000;
                    const dot = s.status !== "connected" ? T.muted : stale ? "#FFDD1F" : T.meadow;
                    const tip = s.last_synced ? `${s.name} · synced ${relativeTime(s.last_synced)}` : `${s.name} · ${s.status}`;
                    return (
                      <span key={s.name} title={tip} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
                        <span style={{ width: 6, height: 6, borderRadius: 99, background: dot }} /><Source name={s.name} />
                      </span>
                    );
                  })
                : [0, 1, 2].map((i) => <Skel key={i} w={72} h={18} style={{ display: "inline-block" }} />)}
              <span style={{ width: 6 }} />
              {updated && !refreshing && (
                <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted }}>Updated {relativeTime(updated)}</span>
              )}
              <button
                onClick={doRefresh}
                disabled={!API_BASE || refreshing}
                title={API_BASE ? "Sync all sources" : "Available on the live app"}
                className="cc-nav"
                style={{
                  display: "inline-flex", alignItems: "center", gap: 6, fontFamily: "Inter,sans-serif",
                  fontSize: 12, fontWeight: 600, color: API_BASE ? T.slate : T.muted,
                  background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 8,
                  padding: "5px 10px", cursor: API_BASE && !refreshing ? "pointer" : "not-allowed",
                }}
              >
                <span style={{ display: "inline-flex", animation: refreshing ? "cc-spin 0.9s linear infinite" : "none" }}><Icon name="sync" size={13} color={API_BASE ? T.slate : T.muted} /></span>
                {refreshing ? "Refreshing…" : "Refresh"}
              </button>
              <UserMenu user={user} />
            </div>
          </div>

          <div style={{ padding: 26, maxWidth: 1100 }}>{content}</div>
        </main>
      </div>

      <AuditDrawer metricKey={drill?.key} business={drill?.business} agentId={drill?.agentId} lo={drill?.lo} period={periodKey} onClose={() => setDrill(null)} />
    </div>
  );
}
