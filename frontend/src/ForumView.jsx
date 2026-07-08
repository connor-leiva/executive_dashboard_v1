import { useState } from "react";
import { T, usd, signed } from "./theme.js";

/* ──────────────────────────────────────────────────────────────
   The Forum — focused business view.

   Restructured from three stacked "systems" (a deep-dive deck and a
   separate Cash & Billing block, each with its own tiles + deck) into
   one governed hierarchy:
     0. Financial · P&L + operational tiles — the standing block at the
        top, kept consistent with every other tab.
     1. Executive summary — one band of ≤6 headline numbers. The
        distilled cross-system read (cash, MRR, ARR, renewals, event).
     2. Watch strip — the single daffodil action, when present.
     3. Collapsible SECTIONS (Members & Growth · Cash & Billing), each
        opening to a selector→focus deck. Progressive disclosure
        replaces the wall of ~20 numbers.

   Color rules unchanged: evergreen = structure, meadow = positive,
   DAFFODIL = the one "attention today" highlight, amber = watch text.
   No poppy. Data-driven from /api/v1/forum; drills reuse the audit
   drawer. Shared with beCollective (no billing → Money section is
   simply omitted; the exec band falls back to its KPIs).
   ────────────────────────────────────────────────────────────── */

const API_BASE = import.meta.env.VITE_API_BASE;
const ACCENT = T.evergreen;   // structure: label ticks, bars
const GOOD = T.meadow;        // positive progress fills

const kc = (n) => {
  const a = Math.abs(n || 0);
  if (a >= 1_000_000) return "$" + (a / 1_000_000).toFixed(2) + "M";
  if (a >= 1000) return "$" + (a / 1000).toFixed(a >= 100_000 ? 0 : 1) + "K";
  return "$" + Math.round(a);
};
const monthShort = (iso) =>
  iso ? new Date(`${iso}T00:00:00`).toLocaleString("en-US", { month: "short" }) : "";

/* ── small shared pieces ─────────────────────────────────────── */

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

/* daffodil = the one "do this today" highlight (used inside detail panels) */
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

/* ── executive summary band — the ≤6 must-read numbers ───────── */

function ExecSummary({ cells, onOpen }) {
  return (
    <div className="fexec">
      {cells.map((c, i) => {
        const click = !!c.drill;
        return (
          <button key={c.label} disabled={!click}
            onClick={click ? () => onOpen(c.drill, c.opts) : undefined}
            className={`fexec-cell ${click ? "click" : ""}`}
            style={{ borderLeft: i === 0 ? "none" : `1px solid ${T.line}` }}>
            <div className="fexec-l">{c.label}{click && <span aria-hidden className="fexec-arrow">↗</span>}</div>
            <div className="fexec-v">{c.value}</div>
            {c.sub && <div className="fexec-s">{c.sub}</div>}
          </button>
        );
      })}
    </div>
  );
}

/* ── watch strip — the single action, plus muted context ─────── */

function WatchStrip({ action, context, onOpen }) {
  if (!action && !context) {
    return (
      <div className="fwatch">
        <span className="fclear"><span className="fclear-dot" />All clear — nothing needs attention today</span>
      </div>
    );
  }
  return (
    <div className="fwatch">
      {action && (
        <>
          <span className="fflag"><span className="fflag-dot" />{action.label}</span>
          {action.drill && (
            <button className="fghost" onClick={() => onOpen(action.drill, action.opts)}>{action.cta} ↗</button>
          )}
        </>
      )}
      {context && <span className="fwatch-ctx">{context}</span>}
    </div>
  );
}

/* ── collapsible section + its selector→focus deck ───────────── */

function Section({ title, source, summary, watch, open, onToggle, children }) {
  return (
    <div className={`fsec ${open ? "on" : ""}`}>
      <button className="fsec-head" onClick={onToggle} aria-expanded={open}>
        <span className="fsec-bar" />
        <span className="fsec-title">{title}</span>
        <span className="fsec-sum">{summary}</span>
        {watch && <span className="fsec-watch"><span className="wd" />{watch}</span>}
        <span style={{ flex: 1 }} />
        {source && <Source name={source} />}
        <span aria-hidden className="fsec-chev">{open ? "▴" : "▾"}</span>
      </button>
      <div className={`fsec-collapse ${open ? "open" : ""}`}>
        <div className="fsec-collapse-in"><div className="fsec-body">{children}</div></div>
      </div>
    </div>
  );
}

function SectionDeck({ items }) {
  const [sel, setSel] = useState(items[0]?.key);
  if (!items.length) return null;
  const active = items.find((d) => d.key === sel) || items[0];
  return (
    <>
      <div className="fd-deck2" style={{ gridTemplateColumns: `repeat(${items.length}, minmax(0,1fr))` }}>
        {items.map((d) => (
          <button key={d.key} onClick={() => setSel(d.key)} className={`fd-scard ${active.key === d.key ? "on" : "off"}`}>
            <div className="fd-sname">{d.name}</div>
            <div className="fd-sstat">{d.stat}</div>
            <div className="fd-sline">{d.line}</div>
          </button>
        ))}
      </div>
      <div className="fd-focus"><div className="fd-body" key={active.key}>{active.render()}</div></div>
    </>
  );
}

/* ── detail panels (reused inside the section decks) ─────────── */

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
            <span style={{ width: 32, fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600, color: T.ink, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{s.v}</span>
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
                {signed(r.value)}
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
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.slate }}>Booked P&amp;L lights up when QuickBooks is connected — until then the Cash &amp; Billing section is the real-time truth.</div>
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

/* ── Cash & Billing panels (GHL Payments, Stripe-fed) ────────── */

const STREAM_COLORS = [T.evergreen, T.meadow, T.teal, T.sprout, T.muted];

function CashFlowPanel({ b, onOpen }) {
  const months = b.monthly || [];
  const max = Math.max(...months.map((m) => m.net), 1);
  return (
    <div>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 18, height: 130, padding: "6px 2px 0" }}>
        {months.map((m) => (
          <div key={m.month} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 7 }}>
            <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 12, fontWeight: 600, color: T.ink, fontVariantNumeric: "tabular-nums" }}>{kc(m.net)}</span>
            <div style={{ width: "100%", maxWidth: 84, height: `${Math.max(2, (m.net / max) * 88)}px`, background: m.mtd ? T.sprout : T.meadow, borderRadius: "6px 6px 0 0" }} />
            <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted }}>{m.month}{m.mtd ? " · MTD" : ""}</span>
          </div>
        ))}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 10, borderTop: `1px solid ${T.line}`, marginTop: 16, paddingTop: 13, fontFamily: "Inter,sans-serif", fontSize: 12, color: T.slate }}>
        <span>{usd(b.gross)} collected − {usd(b.refunded)} refunded = <b style={{ color: T.ink }}>{usd(b.net_cash)} net</b>
          {b.failed_amount > 0 && <span style={{ color: T.amber }}> · {usd(b.failed_amount)} failed (not counted)</span>}</span>
        <span onClick={() => onOpen("forum_payments")} style={{ color: T.meadow, fontWeight: 600, cursor: "pointer" }}>View all transactions ↗</span>
      </div>
    </div>
  );
}

function RecurringPanel({ b, onOpen }) {
  const inst = (b.installments || [])[0];
  const fwd = (b.next30?.schedule || []).slice(0, 3);
  return (
    <div className="fv-cols" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 26 }}>
      <div>
        <div className="fv-colhead">Perpetual · true MRR</div>
        <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 24, fontWeight: 700, color: T.ink }}>{usd(b.mrr)}<span style={{ fontSize: 13, color: T.muted, fontWeight: 500 }}>/mo</span></div>
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.slate, marginTop: 8 }}>{b.perpetual_count} perpetual subscriptions</div>
        <div onClick={() => onOpen("forum_mrr_subs")} style={{ color: T.meadow, fontWeight: 600, cursor: "pointer", fontSize: 11.5, marginTop: 8 }}>View all {b.perpetual_count} subscriptions ↗</div>
      </div>
      <div>
        <div className="fv-colhead">Installment · kept out of MRR</div>
        {inst ? (
          <>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.ink }}>{inst.name}</div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
              {[...Array(inst.total || 0)].map((_, i) => (
                <span key={i} style={{ flex: 1, height: 8, borderRadius: 4, background: i < (inst.collected || 0) ? T.meadow : T.parchment, border: `1px solid ${i < (inst.collected || 0) ? T.meadow : T.line}` }} />
              ))}
            </div>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate, marginTop: 8 }}>
              {usd(inst.amount)} × {inst.total} · {inst.collected} collected · final {inst.final_date || "—"}
            </div>
          </>
        ) : <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.muted }}>No installment plans.</div>}
        <div className="fv-colhead" style={{ marginTop: 18 }}>Forward billing · next 30 days</div>
        {fwd.map((r, i) => (
          <div key={i} className="fv-brow"><span style={{ flex: 1 }}>{new Date(`${r.date}T00:00:00`).toLocaleString("en-US", { month: "short", day: "numeric" })}</span><span style={{ color: T.muted }}>{r.who}</span><b>{usd(r.amount)}</b></div>
        ))}
        <div onClick={() => onOpen("forum_next30")} style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.meadow, fontWeight: 600, cursor: "pointer", paddingTop: 7 }}>
          {usd(b.next30?.amount || 0)} across {b.next30?.charges || 0} charges ↗
        </div>
      </div>
    </div>
  );
}

function ArrBridgePanel({ b, onOpen }) {
  const pct = b.arr_book ? Math.round((b.run_rate / b.arr_book) * 100) : 0;
  return (
    <div>
      {[["Renewal book", b.arr_book, T.evergreen, 100], ["Monthly run-rate", b.run_rate, T.meadow, pct]].map(([label, val, col, w]) => (
        <div key={label} style={{ display: "flex", alignItems: "center", gap: 12, padding: "7px 0" }}>
          <span style={{ width: 128, fontFamily: "Inter,sans-serif", fontSize: 12, color: T.slate }}>{label}</span>
          <div style={{ flex: 1, height: 16, background: T.parchment, borderRadius: 6, overflow: "hidden" }}><div style={{ width: `${w}%`, height: "100%", background: col, borderRadius: 6 }} /></div>
          <b style={{ width: 96, textAlign: "right", fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600, color: T.ink, fontVariantNumeric: "tabular-nums" }}>{usd(val)}</b>
        </div>
      ))}
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.slate, lineHeight: 1.55, borderTop: `1px solid ${T.line}`, marginTop: 14, paddingTop: 13 }}>
        The <b style={{ color: T.ink }}>{usd(b.arr_book - b.run_rate)}</b> gap is PIF and financed-annual members who aren't on monthly billing — expected, and why the renewal book is the headline while run-rate is only the billing slice. <span onClick={() => onOpen("renewal_book")} style={{ color: T.meadow, fontWeight: 600, cursor: "pointer" }}>memberships ↗</span>
      </div>
    </div>
  );
}

function StreamsPanel({ b, onOpen }) {
  const streams = b.streams || [];
  const total = streams.reduce((a, x) => a + x.amount, 0) || 1;
  return (
    <div>
      <div style={{ display: "flex", height: 14, borderRadius: 7, overflow: "hidden", gap: 2, marginBottom: 14 }}>
        {streams.map((x, i) => <div key={x.key} style={{ width: `${(x.amount / total) * 100}%`, background: STREAM_COLORS[i % STREAM_COLORS.length], minWidth: 8 }} />)}
      </div>
      {streams.map((x, i) => (
        <div key={x.key} onClick={() => onOpen("forum_streams", { stream: x.key })} className="fv-brow" style={{ cursor: "pointer" }}>
          <span style={{ flex: 1, display: "inline-flex", alignItems: "center", gap: 8 }}>
            <span style={{ width: 9, height: 9, borderRadius: 2, background: STREAM_COLORS[i % STREAM_COLORS.length] }} />{x.label}
          </span>
          <span style={{ color: T.muted }}>{x.pct}%</span><b>{usd(x.amount)}</b>
        </div>
      ))}
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted, marginTop: 12 }}>Classified from Stripe plan names · sums to net cash {usd(total)}</div>
    </div>
  );
}

/* ── section-content builders (data → deck items) ────────────── */

function membersItems(data, deckSlots, onOpen) {
  const has = (k) => (data.deck || []).some((x) => x.k === k);
  const out = [];
  if (has("pipeline") && data.funnel) {
    const d = (data.deck || []).find((x) => x.k === "pipeline") || {};
    out.push({ key: "pipeline", name: "Recruiting", stat: d.hero || String((data.funnel.stages || []).reduce((a, s) => a + s.v, 0)), line: d.hero_sub || "in pipeline", render: () => <PipelineDetail funnel={data.funnel} /> });
  }
  if (has("renewals") && data.renewals) {
    const s = data.renewals.summary || {};
    out.push({ key: "renewals", name: "Renewals · 90d", stat: s.value || "—", line: `${s.count || 0} members`, render: () => <RenewalsDetail renewals={data.renewals} onOpen={onOpen} /> });
  }
  if (has("event") && data.event) {
    const e = data.event;
    out.push({ key: "event", name: "Next event", stat: e.days_out != null ? `${e.days_out}d` : (e.where || "—"), line: e.registered != null ? `${e.registered}/${e.members} registered` : "readiness", render: () => <EventDetail event={e} onOpen={onOpen} /> });
  }
  // keep the deck ordered by the tab's configured slots
  const order = deckSlots.map((s) => s.k);
  return out.sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key));
}

function moneyItems(b, onOpen) {
  return [
    { key: "cash", name: "Cash flow", stat: kc(b.net_cash), line: "gross − refunds = net", render: () => <CashFlowPanel b={b} onOpen={onOpen} /> },
    { key: "recurring", name: "Recurring", stat: `${kc(b.mrr)}/mo`, line: "installments kept out of MRR", render: () => <RecurringPanel b={b} onOpen={onOpen} /> },
    { key: "arr", name: "ARR bridge", stat: kc(b.arr_book), line: `book vs ${kc(b.run_rate)} run-rate`, render: () => <ArrBridgePanel b={b} onOpen={onOpen} /> },
    { key: "streams", name: "By stream", stat: `${(b.streams || []).length} streams`, line: "dues · tickets · sponsorships", render: () => <StreamsPanel b={b} onOpen={onOpen} /> },
  ];
}

/* Executive-summary cells. Forum (has billing) gets the cross-system blend;
   beCollective (no billing) falls back to its curated KPI tiles. Capped at 6. */
function execCells(data) {
  const b = data.billing?.available ? data.billing : null;
  const kpi = (key) => (data.kpis || []).find((k) => k.key === key);
  const cells = [];
  const am = kpi("active_members") || kpi("bc_members");
  cells.push({ label: "Active members", value: String(data.members_total ?? am?.value ?? "—"), sub: am?.sub, drill: am?.drill });

  if (b) {
    cells.push({ label: "Net cash", value: kc(b.net_cash), sub: `since ${monthShort(b.span?.start)} · ${kc(b.refunded)} refunded`, drill: "forum_payments" });
    cells.push({ label: "MRR", value: kc(b.mrr), sub: `${b.perpetual_count} recurring`, drill: "forum_mrr_subs" });
    cells.push({ label: "ARR · renewal book", value: kc(b.arr_book), sub: `run-rate ${kc(b.run_rate)}`, drill: "renewal_book" });
  }
  if (data.renewals?.summary) {
    const s = data.renewals.summary;
    cells.push({ label: "Renewals · 90 days", value: s.value, sub: `${s.count} members`, drill: "renewal_book" });
  }
  if (data.event) {
    const e = data.event;
    cells.push({ label: "Next event", value: e.days_out != null ? `${e.days_out} days` : (e.where || "—"), sub: e.unregistered ? `${e.unregistered} unregistered` : (e.where || undefined), drill: e.unregistered ? "unregistered" : undefined });
  }
  // fill any remaining slots from the KPI tiles (beCollective path, mostly)
  for (const kp of (data.kpis || [])) {
    if (cells.length >= 6) break;
    if (["active_members", "bc_members"].includes(kp.key)) continue;
    if (cells.some((c) => c.label.toLowerCase() === (kp.label || "").toLowerCase())) continue;
    cells.push({ label: kp.label, value: kp.value, sub: kp.sub, drill: kp.drill });
  }
  return cells.slice(0, 6);
}

/* ── styles (scoped to fv-/fexec/fwatch/fsec/fd- classes) ────── */

const FORUM_CSS = `
  .fv-tile { transition: transform .15s ease, box-shadow .15s ease; }
  .fv-tile:hover { transform: translateY(-1px); box-shadow: 0 8px 20px rgba(0,46,44,.10); }
  .fv-link:hover { filter: brightness(0.97); }
  .fv-ops { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .fv-colhead { font-family: Poppins,sans-serif; font-size: 10.5px; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; color: ${T.slate}; margin-bottom: 8px; }
  .fv-brow { display: flex; align-items: baseline; gap: 10px; padding: 6px 0; font-family: Inter,sans-serif; font-size: 12.5px; color: ${T.secondary}; border-top: 1px solid ${T.page}; }
  .fv-brow b { font-family: Poppins,sans-serif; font-weight: 600; color: ${T.ink}; font-variant-numeric: tabular-nums; }
  .fv-pl { display: grid; grid-template-columns: minmax(0,5fr) minmax(0,7fr); gap: 18px; align-items: stretch; }
  .fv-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 26px; }

  /* executive summary band */
  .fexec { display: grid; grid-template-columns: repeat(6, 1fr); background: ${T.white};
    border: 1px solid ${T.line}; border-radius: 14px; overflow: hidden; box-shadow: 0 1px 2px rgba(0,46,44,.04); }
  .fexec-cell { padding: 15px 16px; text-align: left; background: none; border: none; }
  .fexec-cell.click { cursor: pointer; transition: background .15s ease; }
  .fexec-cell.click:hover { background: ${T.parchment}; }
  .fexec-l { display: flex; align-items: center; gap: 5px; font-family: Inter,sans-serif; font-size: 11px; color: ${T.slate}; font-weight: 500; }
  .fexec-arrow { color: ${T.muted}; font-size: 10px; }
  .fexec-v { font-family: Poppins,sans-serif; font-size: 25px; font-weight: 700; color: ${T.ink}; line-height: 1; margin-top: 8px; font-variant-numeric: tabular-nums; }
  .fexec-s { font-family: Inter,sans-serif; font-size: 10.5px; color: ${T.muted}; margin-top: 6px; }
  @media (max-width: 980px) { .fexec { grid-template-columns: repeat(3,1fr); } .fexec-cell { border-left: none !important; border-top: 1px solid ${T.line}; } }
  @media (max-width: 560px) { .fexec { grid-template-columns: repeat(2,1fr); } }

  /* watch strip */
  .fwatch { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .fflag { display: inline-flex; align-items: center; gap: 7px; background: ${T.daffodilBg}; border-radius: 8px;
    padding: 6px 12px; font-family: Inter,sans-serif; font-size: 12px; font-weight: 600; color: ${T.amber}; }
  .fflag-dot { width: 7px; height: 7px; border-radius: 99px; background: ${T.daffodil}; box-shadow: 0 0 0 1px ${T.line}; }
  .fghost { display: inline-flex; align-items: center; gap: 6px; background: ${T.white}; border: 1px solid ${T.line};
    border-radius: 8px; padding: 5px 11px; cursor: pointer; font-family: Poppins,sans-serif; font-size: 11.5px; font-weight: 600; color: ${T.slate}; }
  .fghost:hover { border-color: ${T.meadow}; color: ${T.meadow}; }
  .fclear { display: inline-flex; align-items: center; gap: 7px; font-family: Inter,sans-serif; font-size: 12px; font-weight: 600; color: ${T.meadowInk}; }
  .fclear-dot { width: 7px; height: 7px; border-radius: 99px; background: ${T.meadow}; }
  .fwatch-ctx { font-family: Inter,sans-serif; font-size: 11.5px; color: ${T.muted}; }

  /* collapsible sections */
  .fsec { background: ${T.white}; border: 1px solid ${T.line}; border-radius: 14px;
    box-shadow: 0 1px 2px rgba(0,46,44,.04); transition: box-shadow .2s ease, border-color .2s ease; }
  .fsec.on { box-shadow: 0 2px 4px rgba(0,46,44,.05), 0 16px 32px rgba(0,46,44,.06); border-color: #E0D6C6; }
  .fsec-head { display: flex; align-items: center; gap: 12px; width: 100%; background: none; border: none;
    padding: 16px 20px; cursor: pointer; text-align: left; }
  .fsec-bar { width: 5px; height: 20px; border-radius: 3px; background: ${T.evergreen}; flex-shrink: 0; }
  .fsec-title { font-family: Poppins,sans-serif; font-size: 15.5px; font-weight: 600; color: ${T.ink}; white-space: nowrap; }
  .fsec-sum { font-family: Inter,sans-serif; font-size: 12.5px; color: ${T.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fsec-watch { display: inline-flex; align-items: center; gap: 6px; font-family: Inter,sans-serif; font-size: 11px;
    font-weight: 600; color: ${T.amber}; background: ${T.daffodilBg}; border-radius: 6px; padding: 3px 9px; white-space: nowrap; }
  .fsec-watch .wd { width: 6px; height: 6px; border-radius: 99px; background: ${T.daffodil}; }
  .fsec-chev { font-size: 11px; color: ${T.muted}; }
  .fsec-collapse { display: grid; grid-template-rows: 0fr; transition: grid-template-rows .28s cubic-bezier(.4,0,.2,1); }
  .fsec-collapse.open { grid-template-rows: 1fr; }
  .fsec-collapse-in { overflow: hidden; }
  .fsec-body { padding: 2px 20px 20px; }

  /* selector→focus deck inside a section */
  .fd-deck2 { display: grid; gap: 10px; }
  .fd-scard { background: ${T.parchment}; border: 1px solid ${T.line}; border-radius: 11px; padding: 12px 14px;
    cursor: pointer; text-align: left; position: relative; transition: background .15s ease; }
  .fd-scard.on { background: ${T.white}; border-radius: 11px 11px 0 0; border-bottom-color: ${T.white}; z-index: 1; }
  .fd-scard.off:hover { background: ${T.white}; }
  .fd-sname { font-family: Inter,sans-serif; font-size: 10.5px; font-weight: 600; color: ${T.slate}; text-transform: uppercase; letter-spacing: .07em; }
  .fd-sstat { font-family: Poppins,sans-serif; font-size: 19px; font-weight: 700; color: ${T.ink}; margin-top: 5px; font-variant-numeric: tabular-nums; }
  .fd-sline { font-family: Inter,sans-serif; font-size: 10px; color: ${T.muted}; margin-top: 3px; }
  .fd-focus { background: ${T.white}; border: 1px solid ${T.line}; border-top: none; border-radius: 0 0 11px 11px; padding: 18px; margin-top: -1px; }
  .fd-body { animation: fdFade .28s ease; }
  @keyframes fdFade { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }

  @media (max-width: 900px) { .fv-pl { grid-template-columns: 1fr; } }
  @media (max-width: 700px) { .fd-deck2 { grid-template-columns: 1fr 1fr !important; } .fv-cols { grid-template-columns: 1fr; } }
  @media (max-width: 560px) { .fd-deck2 { grid-template-columns: 1fr !important; } .fv-ops { grid-template-columns: 1fr; } }
  button:focus-visible { outline: 2px solid ${T.teal}; outline-offset: 2px; }
  @media (prefers-reduced-motion: reduce) {
    .fv-tile, .fexec-cell, .fd-scard, .fsec { transition: none; }
    .fsec-collapse { transition: none; }
    .fd-body { animation: none; }
    .fv-tile:hover { transform: none; }
  }
`;

/* The three canonical Members deck slots for The Forum. */
const DECK_SLOTS = [
  { k: "pipeline", label: "Recruiting pipeline" },
  { k: "renewals", label: "Renewals · next 90 days" },
  { k: "event", label: "Next event" },
];

/* beCollective is a cohort program: recruiting funnel + next event (no monthly
   renewals, no subscription billing). */
export const BC_DECK_SLOTS = [
  { k: "pipeline", label: "Recruiting pipeline" },
  { k: "event", label: "Next event" },
];

/* ── Program view (The Forum / beCollective — same primitives, props differ) ── */

export default function ForumView({ data, area, onDrill, title = "The Forum",
  subtitle = "Mastermind", deckSlots = DECK_SLOTS, drillBusiness = "springb" }) {
  const [openState, setOpenState] = useState(null);
  if (!data) return null;

  const onOpen = (key, opts) => onDrill && onDrill(key, drillBusiness, null, null, opts);
  const b = data.billing?.available ? data.billing : null;

  const cells = execCells(data);
  const members = membersItems(data, deckSlots, onOpen);
  const pipelineTotal = data.funnel ? (data.funnel.stages || []).reduce((a, s) => a + s.v, 0) : null;
  const renewalCount = data.renewals?.summary?.count;
  const membersSummary = [
    `${data.members_total} active`,
    pipelineTotal != null ? `${pipelineTotal} recruiting` : null,
    renewalCount != null ? `${renewalCount} renewals` : null,
  ].filter(Boolean).join(" · ");

  const moneyWatch = b && (b.failed_count > 0 || b.past_due > 0);

  // watch strip: one action + muted context
  let action = null;
  const ctx = [];
  if (b && b.failed_count > 0) {
    action = { label: `${b.failed_count} failed charge${b.failed_count === 1 ? "" : "s"} · ${usd(b.failed_amount)} to recover`, cta: "View recovery list", drill: "forum_failed_payments" };
  } else if (data.event?.unregistered > 0) {
    action = { label: `${data.event.unregistered} not registered for ${data.event.where || "the next event"}`, cta: "The call list", drill: "unregistered" };
  }
  if (b && b.past_due > 0) ctx.push(`${b.past_due} subscription${b.past_due === 1 ? "" : "s"} past-due`);
  if (data.event?.unregistered > 0 && action?.drill !== "unregistered") ctx.push(`${data.event.unregistered} unregistered for the next event`);

  // default section open state (derived from data on first render; user toggles
  // override). One section leads: the one with the watch item, else Members.
  const defaults = { members: !moneyWatch, money: !!moneyWatch };
  const open = openState || defaults;
  const toggle = (id) => setOpenState({ ...open, [id]: !open[id] });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <style>{FORUM_CSS}</style>

      {/* header */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
        <span style={{ width: 5, height: 30, borderRadius: 3, background: ACCENT }} />
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 24, fontWeight: 600, color: T.ink }}>{title}</span>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.muted }}>{subtitle} · {data.members_total} members</span>
      </div>

      {/* Financial · P&L (+ operational leading indicators) — standing block at the
          top, consistent with every other tab. */}
      <div className="fv-pl">
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

      {/* Executive summary — the ≤6 must-read numbers */}
      <ExecSummary cells={cells} onOpen={onOpen} />

      {/* 2 — the single action */}
      <WatchStrip action={action} context={ctx.join(" · ") || null} onOpen={onOpen} />

      {/* 3 — progressive disclosure */}
      <Section title="Members & Growth" source="Go High Level" summary={membersSummary}
               open={!!open.members} onToggle={() => toggle("members")}>
        {members.length
          ? <SectionDeck items={members} />
          : <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.muted }}>No recruiting or event sections configured yet.</div>}
      </Section>

      {b && (
        <Section title="Cash & Billing" source="GHL Payments · Stripe"
                 summary={`${kc(b.net_cash)} collected · ${kc(b.mrr)} MRR · ${kc(b.arr_book)} ARR`}
                 watch={moneyWatch ? `${b.failed_count || b.past_due} to recover` : null}
                 open={!!open.money} onToggle={() => toggle("money")}>
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted, marginBottom: 14 }}>
            Cash basis · reconciles to QuickBooks as the Booked lens when connected
          </div>
          <SectionDeck items={moneyItems(b, onOpen)} />
        </Section>
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
