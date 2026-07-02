import { useState } from "react";
import { T, usd, signed } from "./theme.js";

/* ──────────────────────────────────────────────────────────────
   The Forum — focused business view (production port of
   the-forum-view-v2 mockup). Data-driven from /api/v1/forum.
   No poppy in this view: structure is evergreen, progress is
   meadow, DAFFODIL is the one "attention today" highlight, and
   watch states are amber. Nothing reads as alarm.
   ────────────────────────────────────────────────────────────── */

const API_BASE = import.meta.env.VITE_API_BASE;
const ACCENT = T.evergreen;   // structure: label ticks, bars
const GOOD = T.meadow;        // positive progress fills

/* ── small pieces ──────────────────────────────────────────── */

function Source({ name }) {
  return (
    <span style={{
      fontFamily: "Inter,sans-serif", fontSize: 10.5, fontWeight: 600, color: T.slate,
      background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 5, padding: "2px 7px",
    }}>{name}</span>
  );
}

function PanelLabel({ children, accent }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
      <span style={{ width: 3, height: 14, borderRadius: 2, background: accent || ACCENT }} />
      <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 11, fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", color: T.slate }}>
        {children}
      </span>
    </div>
  );
}

function Card({ children, style }) {
  return <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 14, padding: 22, ...style }}>{children}</div>;
}

function SegChip({ seg }) {
  const f = seg === "F" || seg === "Forum";
  return (
    <span style={{
      fontFamily: "Poppins,sans-serif", fontSize: 9, fontWeight: 700, letterSpacing: "0.06em",
      color: f ? T.evergreen : T.teal, background: f ? T.daffodil : T.mist,
      borderRadius: 4, padding: "2px 6px", textTransform: "uppercase", flexShrink: 0,
    }}>{f ? "Forum" : "IC"}</span>
  );
}

/* daffodil = the one "do this today" highlight */
function ActionRow({ label, cta, onClick }) {
  return (
    <button className="fv-link" onClick={onClick} style={{
      display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%",
      background: T.daffodilBg, border: `1px solid ${T.daffodil}`, borderRadius: 9,
      padding: "10px 13px", marginTop: 14, cursor: "pointer", gap: 10,
    }}>
      <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.ink, textAlign: "left" }}>{label}</span>
      <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, fontWeight: 600, color: T.amber, whiteSpace: "nowrap" }}>{cta} →</span>
    </button>
  );
}

function KpiTile({ d, onOpen }) {
  const clickable = !!d.drill;
  return (
    <button className={clickable ? "fv-tile" : ""} disabled={!clickable}
      onClick={clickable ? () => onOpen(d.drill) : undefined} style={{
        background: T.parchment, borderRadius: 10, padding: "12px 13px", border: "none",
        textAlign: "left", cursor: clickable ? "pointer" : "default", position: "relative", width: "100%",
      }}>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.slate, marginBottom: 6, fontWeight: 500 }}>{d.label}</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 22, fontWeight: 600, color: T.ink, fontVariantNumeric: "tabular-nums" }}>{d.value}</span>
        {d.sub && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted }}>{d.sub}</span>}
      </div>
      {clickable && <span aria-hidden style={{ position: "absolute", top: 10, right: 11, fontSize: 10, color: T.muted }}>↗</span>}
    </button>
  );
}

/* ── deck micro-visuals (fixed lane so cards stay equal) ─────── */

function MicroBars({ stages }) {
  const max = Math.max(...stages.map((s) => s.v), 1);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3, height: 21 }}>
      {stages.map((s, i) => (
        <div key={i} style={{ height: 3, width: `${(s.v / max) * 100}%`, background: ACCENT, opacity: 0.35 + 0.65 * (s.v / max), borderRadius: 2 }} />
      ))}
    </div>
  );
}

function MicroSplit({ parts }) {
  const total = parts.reduce((a, p) => a + p.v, 0) || 1;
  return (
    <div style={{ display: "flex", height: 8, borderRadius: 4, overflow: "hidden", gap: 2, margin: "6px 0 7px" }}>
      {parts.map((p, i) => (
        <div key={i} style={{ width: `${(p.v / total) * 100}%`, background: p.c, opacity: p.o ?? 1 }} />
      ))}
    </div>
  );
}

function MicroProgress({ pct, fill }) {
  return (
    <div style={{ height: 8, borderRadius: 4, background: T.parchment, overflow: "hidden", margin: "6px 0 7px" }}>
      <div style={{ width: `${pct}%`, height: "100%", background: fill, borderRadius: 4 }} />
    </div>
  );
}

function microFor(k, data) {
  const { funnel, renewals, event, revq } = data;
  if (k === "pipeline" && funnel) return <MicroBars stages={funnel.stages} />;
  if (k === "renewals" && renewals) {
    const seg = renewals.summary.segments || {};
    return <MicroSplit parts={[
      { v: seg.F || 0, c: GOOD },
      { v: seg.IC || 0, c: T.teal, o: 0.5 },
    ]} />;
  }
  if (k === "event" && event) {
    const pct = event.members ? Math.round((event.registered / event.members) * 100) : 0;
    return <MicroProgress pct={pct} fill={GOOD} />;
  }
  if (k === "revq" && revq) {
    return <MicroSplit parts={[
      { v: revq.pif?.value || 0, c: ACCENT },
      { v: revq.monthly?.value || 0, c: ACCENT, o: 0.28 },
    ]} />;
  }
  return <div style={{ height: 8, margin: "6px 0 7px" }} />;
}

/* ── deck card (collapsed summary — the salient highlight) ───── */

function DeckCard({ d, data, active, onSelect }) {
  const watch = d.tone === "watch";
  return (
    <button className={`fd-card ${active ? "on" : ""}`} onClick={onSelect}
      aria-expanded={active} style={{
        position: "relative", textAlign: "left", background: T.white, cursor: "pointer",
        border: `1px solid ${active ? T.evergreen : T.line}`, borderRadius: 14, padding: "16px 16px 14px",
        display: "flex", flexDirection: "column", gap: 7, minHeight: 148,
      }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 10.5, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: T.slate }}>{d.label}</span>
        <span aria-hidden className="fd-chev" style={{ fontSize: 10, color: T.muted }}>{active ? "▴" : "▾"}</span>
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 7 }}>
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 27, fontWeight: 700, color: T.ink, lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>{d.hero}</span>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted }}>{d.hero_sub}</span>
      </div>
      <div style={{ marginTop: "auto" }}>
        {microFor(d.k, data)}
        <span style={{
          display: "inline-flex", alignItems: "center", gap: 6,
          fontFamily: "Inter,sans-serif", fontSize: 11.5, fontWeight: 600,
          color: watch ? T.amber : T.meadowInk,
        }}>
          {watch && <span style={{ width: 6, height: 6, borderRadius: 99, background: T.daffodil, border: `1.5px solid ${T.amber}`, flexShrink: 0 }} />}
          {d.salient}
        </span>
      </div>
      <span aria-hidden className="fd-underline" />
    </button>
  );
}

/* ── expanded details ────────────────────────────────────────── */

function PipelineDetail({ funnel }) {
  if (!funnel) return null;
  const max = Math.max(...funnel.stages.map((s) => s.v), 1);
  return (
    <div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10, maxWidth: 640 }}>
        {funnel.stages.map((s, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span style={{ width: 150, fontFamily: "Inter,sans-serif", fontSize: 12, color: T.slate, textAlign: "right" }}>{s.label}</span>
            <div style={{ flex: 1, height: 22, background: T.parchment, borderRadius: 5, overflow: "hidden" }}>
              <div style={{ width: `${(s.v / max) * 100}%`, height: "100%", background: ACCENT, opacity: 0.35 + 0.65 * (s.v / max), borderRadius: 5 }} />
            </div>
            <span style={{ width: 26, fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600, color: T.ink, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{s.v}</span>
            <span style={{ width: 52, fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{s.value}</span>
          </div>
        ))}
      </div>
      {funnel.footer && (
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate, marginTop: 16, paddingTop: 13, borderTop: `1px solid ${T.line}`, lineHeight: 1.5 }}>
          {funnel.footer}
        </div>
      )}
    </div>
  );
}

function RenewalsDetail({ renewals, onOpen }) {
  if (!renewals) return null;
  const { rows, summary } = renewals;
  return (
    <div>
      <div style={{ maxWidth: 640 }}>
        {rows.map((r, i) => (
          <div key={i} style={{
            display: "flex", alignItems: "center", gap: 10, padding: "8px 0",
            borderTop: i === 0 ? "none" : `1px solid ${T.parchment}`,
          }}>
            <span style={{ width: 28, fontFamily: "Poppins,sans-serif", fontSize: 10.5, fontWeight: 700, color: T.muted, textTransform: "uppercase", letterSpacing: "0.04em" }}>{r.month}</span>
            <span style={{ flex: 1, fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.name}</span>
            <SegChip seg={r.seg} />
            <span style={{ width: 56, fontFamily: "Poppins,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.ink, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{r.value}</span>
          </div>
        ))}
      </div>
      <button className="fv-link" onClick={() => onOpen("renewal_book")} style={{
        display: "flex", justifyContent: "space-between", alignItems: "baseline", width: "100%", maxWidth: 640,
        border: "none", background: "transparent", cursor: "pointer", padding: "13px 0 0",
        borderTop: `1px solid ${T.line}`, marginTop: 12, gap: 10,
      }}>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate, textAlign: "left" }}>{summary.retention || "renewal book"}</span>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, fontWeight: 600, color: T.meadowInk, whiteSpace: "nowrap" }}>
          all {summary.count} · {summary.value} →
        </span>
      </button>
    </div>
  );
}

function EventDetail({ event, onOpen }) {
  if (!event) return null;
  const pct = event.members ? Math.round((event.registered / event.members) * 100) : 0;
  return (
    <div style={{ maxWidth: 640 }}>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 17, fontWeight: 600, color: T.ink }}>{event.where}</div>
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.muted, marginTop: 2 }}>{event.title}{event.when ? ` · ${event.when}` : ""}</div>
        </div>
        {event.days_out != null && (
          <div style={{ textAlign: "right" }}>
            <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 26, fontWeight: 700, color: T.ink, fontVariantNumeric: "tabular-nums" }}>{event.days_out}</span>
            <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted, marginLeft: 5 }}>days out</span>
          </div>
        )}
      </div>
      <div style={{ marginTop: 16 }}>
        <div style={{ display: "flex", height: 12, borderRadius: 6, overflow: "hidden", background: T.parchment }}>
          <div style={{ width: `${pct}%`, background: GOOD, borderRadius: 6 }} />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, flexWrap: "wrap", gap: 6 }}>
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.ink, fontWeight: 600 }}>
            {event.registered} of {event.members} members · {pct}%
          </span>
          {event.guests > 0 && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.muted }}>+ {event.guests} guests <span style={{ color: T.teal, fontWeight: 600 }}>(prospect seats)</span></span>}
        </div>
      </div>
      {event.unregistered > 0 && (
        <ActionRow label={`${event.unregistered} members not yet registered`} cta="the call list" onClick={() => onOpen("unregistered")} />
      )}
      {event.behind_pace && event.pace_note && (
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.amber, marginTop: 12, lineHeight: 1.5 }}>
          Behind pace — {event.pace_note}.
        </div>
      )}
    </div>
  );
}

function RevQDetail({ revq, onOpen }) {
  if (!revq) return null;
  const pifVal = revq.pif?.value || 0;
  const monVal = revq.monthly?.value || 0;
  const total = pifVal + monVal || 1;
  const pifPct = (pifVal / total) * 100;
  const k = (n) => "$" + Math.round(n / 1000) + "K";
  return (
    <div style={{ maxWidth: 640 }}>
      {/* payment mix — solid is collected, lighter is still collecting */}
      <div style={{ display: "flex", height: 12, borderRadius: 6, overflow: "hidden", gap: 2 }}>
        <div style={{ width: `${pifPct}%`, background: ACCENT, borderRadius: "6px 0 0 6px" }} />
        <div style={{ width: `${100 - pifPct}%`, background: ACCENT, opacity: 0.28, borderRadius: "0 6px 6px 0" }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, flexWrap: "wrap", gap: 6 }}>
        {revq.pif && (
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate }}>
            <b style={{ color: T.ink, fontFamily: "Poppins,sans-serif" }}>{k(pifVal)}</b> paid in full · {revq.pif.count}
          </span>
        )}
        {revq.monthly && (
          <button className="fv-link" onClick={() => onOpen("monthly")} style={{ border: "none", background: "transparent", cursor: "pointer", padding: 0, fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate }}>
            <b style={{ color: T.ink, fontFamily: "Poppins,sans-serif" }}>{k(monVal)}</b> monthly · {revq.monthly.count}{revq.monthly.sub ? ` · ${revq.monthly.sub}` : ""} ↗
          </button>
        )}
      </div>
      {revq.past_due && revq.past_due.count > 0 && (
        <ActionRow label={`${revq.past_due.count} subscriptions past due · ${revq.past_due.value}`} cta="recover" onClick={() => onOpen("pastdue")} />
      )}
      {revq.bridge && (
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginTop: 14, paddingTop: 13, borderTop: `1px solid ${T.line}`, flexWrap: "wrap" }}>
          {revq.bridge.map((b, i) => (
            <span key={i} style={{ display: "inline-flex", alignItems: "baseline", gap: 8 }}>
              {i > 0 && <span style={{ fontSize: 11, color: T.muted }}>→</span>}
              <span>
                <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10, color: T.muted, display: "block" }}>{b.label}</span>
                <span style={{
                  fontFamily: "Poppins,sans-serif", fontSize: b.tot ? 15 : 12.5, fontWeight: b.tot ? 700 : 600,
                  color: b.soft ? T.slate : b.tot ? T.meadowInk : T.ink, fontVariantNumeric: "tabular-nums",
                }}>{b.value}</span>
              </span>
            </span>
          ))}
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, color: T.muted, marginLeft: "auto" }}>ARR bridge · YTD</span>
        </div>
      )}
    </div>
  );
}

const DETAIL_META = {
  pipeline: { title: "Recruiting pipeline · sales funnel", C: (p) => <PipelineDetail funnel={p.data.funnel} /> },
  renewals: { title: "Renewals · next 90 days", C: (p) => <RenewalsDetail renewals={p.data.renewals} onOpen={p.onOpen} /> },
  event: { title: "Next event · readiness", C: (p) => <EventDetail event={p.data.event} onOpen={p.onOpen} /> },
  revq: { title: "Revenue quality", C: (p) => <RevQDetail revq={p.data.revq} onOpen={p.onOpen} /> },
};

/* ── Financial · P&L (shared Spring B entity) ────────────────── */

function PLMini({ area }) {
  const rows = area?.pl;
  if (rows && rows.length) {
    return (
      <div>
        {rows.map((r, i) => {
          const tot = r.kind === "tot", sub = r.kind === "sub", ded = r.kind === "ded";
          return (
            <div key={i} style={{
              display: "flex", justifyContent: "space-between", alignItems: "baseline",
              padding: tot ? "12px 0 4px" : "8px 0",
              borderTop: tot ? `2px solid ${T.evergreen}` : "none",
              borderBottom: sub ? `1px solid ${T.line}` : "none", marginTop: tot ? 6 : 0,
            }}>
              <span style={{ fontFamily: "Inter,sans-serif", fontSize: tot ? 14 : 13, fontWeight: tot || sub ? 700 : 400, color: ded ? T.slate : T.ink }}>{r.label}</span>
              <span style={{ fontFamily: "Poppins,sans-serif", fontSize: tot ? 18 : 14, fontWeight: tot ? 700 : sub ? 600 : 500, color: tot ? T.meadowInk : ded ? T.slate : T.ink, fontVariantNumeric: "tabular-nums" }}>
                {ded ? signed(r.value) : usd(r.value)}
              </span>
            </div>
          );
        })}
      </div>
    );
  }
  // Not connected → Connect QuickBooks placeholder (real href on the live app).
  const businessId = area?.id || area?.key || "springb";
  const href = API_BASE ? `${API_BASE}/integrations/qbo/connect?business_id=${businessId}` : null;
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center", gap: 14 }}>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.slate }}>Financials light up when QuickBooks is connected</div>
      <a href={href || undefined} aria-disabled={href ? undefined : true} style={{
        alignSelf: "flex-start", fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600,
        color: href ? T.onDark : T.onDarkMute, background: href ? T.evergreen : "rgba(0,46,44,0.35)",
        borderRadius: 9, padding: "10px 16px", textDecoration: "none",
        pointerEvents: href ? "auto" : "none", cursor: href ? "pointer" : "not-allowed",
      }}>Connect QuickBooks</a>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted, lineHeight: 1.5 }}>
        Spring B entity · Forum revenue splits from beCollective by QuickBooks class.
      </div>
    </div>
  );
}

/* ── styles (scoped to fv-/fd- classes) ─────────────────────── */

const FORUM_CSS = `
  .fv-tile { transition: transform .15s ease, box-shadow .15s ease; }
  .fv-tile:hover { transform: translateY(-1px); box-shadow: 0 8px 20px rgba(0,46,44,.10); }
  .fv-link:hover { filter: brightness(0.97); }
  .fv-row1 { display: grid; grid-template-columns: minmax(0,5fr) minmax(0,7fr); gap: 18px; align-items: stretch; }
  .fv-ops { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .fd-deck { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 14px; }
  .fd-card { transition: transform .15s ease, box-shadow .15s ease, border-color .15s ease; }
  .fd-card:hover { transform: translateY(-2px); box-shadow: 0 10px 24px rgba(0,46,44,.10); }
  .fd-card.on { box-shadow: 0 10px 24px rgba(0,46,44,.08); }
  .fd-underline { position: absolute; left: 14px; right: 14px; bottom: -1px; height: 3px; border-radius: 3px;
    background: ${T.daffodil}; transform: scaleX(0); transform-origin: left; transition: transform .22s ease; }
  .fd-card.on .fd-underline { transform: scaleX(1); }
  .fd-body { animation: fdFade .28s ease; }
  @keyframes fdFade { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
  @media (prefers-reduced-motion: no-preference) { .fv-pulse { animation: fvPulse 2.4s ease-out infinite; } }
  @keyframes fvPulse { 0% { box-shadow: 0 0 0 0 currentColor; } 70% { box-shadow: 0 0 0 5px rgba(0,0,0,0); } 100% { box-shadow: 0 0 0 0 rgba(0,0,0,0); } }
  .fv-tile:focus-visible, .fv-link:focus-visible, .fd-card:focus-visible { outline: 2px solid ${T.teal}; outline-offset: 2px; }
  @media (max-width: 1000px) { .fd-deck { grid-template-columns: repeat(2, minmax(0,1fr)); } .fv-row1 { grid-template-columns: 1fr; } }
  @media (max-width: 560px) { .fd-deck { grid-template-columns: 1fr; } .fv-ops { grid-template-columns: 1fr; } }
  @media (prefers-reduced-motion: reduce) {
    .fv-tile, .fd-card, .fd-underline { transition: none; }
    .fd-body { animation: none; }
    .fv-tile:hover, .fd-card:hover { transform: none; }
  }
`;

/* ── The Forum view ────────────────────────────────────────── */

export default function ForumView({ data, area, onDrill }) {
  const [sel, setSel] = useState(null);
  if (!data) return null;
  const onOpen = (key) => onDrill && onDrill(key, "springb");
  const meta = sel ? DETAIL_META[sel] : null;
  const watchCount = data.watch?.count || 0;
  const deck = data.deck || [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <style>{FORUM_CSS}</style>

      {/* header */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
        <span style={{ width: 5, height: 30, borderRadius: 3, background: ACCENT }} />
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 24, fontWeight: 600, color: T.ink }}>The Forum</span>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.muted }}>Mastermind · {data.members_total} members</span>
        <span style={{ flex: 1 }} />
        {watchCount > 0 && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span className="fv-pulse" style={{ width: 7, height: 7, borderRadius: 99, background: T.amber, color: T.amber }} />
            <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, fontWeight: 600, color: T.amber }}>{watchCount} item{watchCount === 1 ? "" : "s"} to watch</span>
          </span>
        )}
      </div>

      {/* row 1 — P&L (shared Spring B entity) + KPI tiles */}
      <div className="fv-row1">
        <Card style={{ display: "flex", flexDirection: "column" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <PanelLabel>Financial · P&amp;L</PanelLabel>
            <Source name="QuickBooks" />
          </div>
          <PLMini area={area} />
        </Card>
        <Card>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <PanelLabel>Operational · leading indicators</PanelLabel>
            <Source name="Go High Level" />
          </div>
          <div className="fv-ops">
            {(data.kpis || []).map((d, i) => <KpiTile key={i} d={d} onOpen={onOpen} />)}
          </div>
        </Card>
      </div>

      {/* deep-dive deck */}
      {deck.length > 0 && (
        <div>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", margin: "2px 2px 12px" }}>
            <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 11, fontWeight: 700, letterSpacing: "0.14em", textTransform: "uppercase", color: T.slate }}>Deep dives</span>
            <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted }}>select a card to expand</span>
          </div>
          <div className="fd-deck">
            {deck.map((d) => (
              <DeckCard key={d.k} d={d} data={data} active={sel === d.k}
                onSelect={() => setSel(sel === d.k ? null : d.k)} />
            ))}
          </div>
          {meta && (
            <Card style={{ marginTop: 14 }}>
              <div className="fd-body" key={sel}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <PanelLabel>{meta.title}</PanelLabel>
                  <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <Source name="Go High Level" />
                    <button className="fv-link" onClick={() => setSel(null)} aria-label="Collapse" style={{
                      border: `1px solid ${T.line}`, background: T.parchment, color: T.slate, borderRadius: 7,
                      fontFamily: "Inter,sans-serif", fontSize: 11, fontWeight: 600, padding: "4px 10px", cursor: "pointer",
                    }}>Collapse ▴</button>
                  </span>
                </div>
                <meta.C data={data} onOpen={onOpen} />
              </div>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}

/* beCollective — its own space (own accent + GHL segment). Placeholder until
   its focused view is built out (spec Part 3: areas.becollective). */
export function BeCollectivePlaceholder() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
        <span style={{ width: 5, height: 30, borderRadius: 3, background: T.petal }} />
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 24, fontWeight: 600, color: T.ink }}>beCollective</span>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.muted }}>Community · separate GHL segment</span>
      </div>
      <Card style={{ maxWidth: 560 }}>
        <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 16, fontWeight: 600, color: T.ink }}>Its own space is coming</div>
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.slate, marginTop: 8, lineHeight: 1.6 }}>
          beCollective lives in a separate Go High Level account, so it gets its own view — membership tiers,
          community engagement, and its own funnel — rather than being blended into The Forum. Connect the
          beCollective GHL account in Settings to light this up.
        </div>
      </Card>
    </div>
  );
}
