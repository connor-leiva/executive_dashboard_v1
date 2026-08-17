/* PeriodNav — the Command Center top bar.

   Built from the provided design with the brand deferrals applied: colours/type come from
   theme.js (no local palette, no font imports, no hex literals), and the source list collapses
   into a status chip + drawer instead of always spending header width.

   The period is owned as { grain, anchor } — a granularity plus a date inside the window —
   which is what makes the ‹ › stepper possible (walk back through past months/quarters, not
   just the fixed to-date windows). periodKey() then encodes that pair as the ONE period
   string the whole product already speaks, preferring a canonical key (mtd/qtd/ytd/year/
   last_month/next_month) whenever the window matches one, so QuickBooks-backed surfaces keep
   their snapshot; anything else rides the custom "c:START:END" encoding. */
import { useMemo, useState } from "react";
import { T, alpha, relativeTime } from "./theme.js";
import { Icon } from "./Brand.jsx";

export const GRAINS = [
  ["month", "Month"], ["quarter", "Quarter"], ["ytd", "YTD"], ["year", "Year"],
];
const ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const eom = (y, m) => new Date(y, m + 1, 0);

/** Resolve { grain, anchor } into the concrete window it covers. */
export function resolve(grain, anchor) {
  const y = anchor.getFullYear(), m = anchor.getMonth();
  if (grain === "year") return { start: new Date(y, 0, 1), end: new Date(y, 11, 31) };
  if (grain === "ytd") return { start: new Date(y, 0, 1), end: eom(y, m) };
  if (grain === "quarter") {
    const q = Math.floor(m / 3);
    return { start: new Date(y, q * 3, 1), end: eom(y, q * 3 + 2) };
  }
  return { start: new Date(y, m, 1), end: eom(y, m) };
}

/** Step one window forward/back without changing granularity. */
export function step(grain, anchor, delta) {
  const y = anchor.getFullYear(), m = anchor.getMonth();
  if (grain === "year" || grain === "ytd") return new Date(y + delta, m, 1);
  if (grain === "quarter") return new Date(y, m + delta * 3, 1);
  return new Date(y, m + delta, 1);
}

/** The period string the API speaks. Canonical keys win so booked P&L keeps its snapshot. */
export function periodKey(grain, anchor, custom = null, now = new Date()) {
  // An arbitrary range can't be expressed as grain+anchor, so it's carried alongside them.
  if (custom && custom.from && custom.to) return `c:${custom.from}:${custom.to}`;
  const y = anchor.getFullYear(), m = anchor.getMonth();
  const sameYear = y === now.getFullYear();
  const monthsApart = (y - now.getFullYear()) * 12 + (m - now.getMonth());
  if (grain === "month") {
    if (monthsApart === 0) return "mtd";
    if (monthsApart === -1) return "last_month";
    if (monthsApart === 1) return "next_month";
  }
  if (grain === "quarter" && sameYear && Math.floor(m / 3) === Math.floor(now.getMonth() / 3)) return "qtd";
  if (grain === "ytd" && sameYear) return "ytd";
  if (grain === "year" && sameYear) return "year";
  const { start, end } = resolve(grain, anchor);
  return `c:${iso(start)}:${iso(end)}`;
}

/** Reverse: the { grain, anchor } a stored period string represents (for first paint). */
export function fromPeriodKey(key, now = new Date()) {
  if (key === "qtd") return { grain: "quarter", anchor: now };
  if (key === "ytd") return { grain: "ytd", anchor: now };
  if (key === "year") return { grain: "year", anchor: now };
  if (key === "last_month") return { grain: "month", anchor: new Date(now.getFullYear(), now.getMonth() - 1, 1) };
  if (key === "next_month") return { grain: "month", anchor: new Date(now.getFullYear(), now.getMonth() + 1, 1) };
  if (typeof key === "string" && key.startsWith("c:")) {
    const [from, to] = key.slice(2).split(":");
    const [yy, mm, dd] = (from || "").split("-").map(Number);
    if (yy) return { grain: "month", anchor: new Date(yy, mm - 1, dd), custom: { from, to } };
  }
  return { grain: "month", anchor: now };
}

const fmtWindow = ({ start, end }) =>
  `${ABBR[start.getMonth()]} ${start.getDate()} – ${ABBR[end.getMonth()]} ${end.getDate()}, ${end.getFullYear()}`;

/* A source is live, going stale (>2h since a sync), or not connected at all. */
function srcStatus(s) {
  if (s.status !== "connected") return "error";
  if (s.last_synced && Date.now() - new Date(s.last_synced).getTime() > 2 * 3600 * 1000) return "stale";
  return "live";
}
const STATUS_COLOR = { live: T.meadow, stale: T.daffodil, error: T.muted };

export default function PeriodNav({
  grain, anchor, custom = null, onChange,
  serverLabel,                      // what the SERVER says the window is (authoritative)
  sources = [], loading = false,
  refreshing = false, onRefresh, apiEnabled = true, updated,
  accountSlot, menuSlot,
}) {
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const win = useMemo(() => (
    custom && custom.from && custom.to
      ? { start: new Date(`${custom.from}T00:00:00`), end: new Date(`${custom.to}T00:00:00`) }
      : resolve(grain, anchor)
  ), [grain, anchor, custom]);

  // Stepping or switching granularity leaves a custom range behind — it's a different mode.
  const emit = (g, a) => onChange({ grain: g, anchor: a, custom: null, key: periodKey(g, a) });
  // Switching granularity keeps the anchor (Quarter from a June anchor means Q2), so wandering
  // is easy and getting home isn't — hence an explicit way back, shown only when it's needed.
  const now = new Date();
  const isNow = !custom && anchor.getFullYear() === now.getFullYear()
    && (grain === "year" || grain === "ytd" ? true
      : grain === "quarter" ? Math.floor(anchor.getMonth() / 3) === Math.floor(now.getMonth() / 3)
        : anchor.getMonth() === now.getMonth());
  const applyRange = () => {
    if (!from || !to || to < from) return;
    onChange({ grain, anchor: new Date(`${from}T00:00:00`), custom: { from, to },
               key: `c:${from}:${to}` });
    setPickerOpen(false);
  };

  const nOff = sources.filter((s) => srcStatus(s) === "error").length;
  const nStale = sources.filter((s) => srcStatus(s) === "stale").length;
  const worst = nOff ? "error" : nStale ? "stale" : "live";
  // Name the problem rather than the severity — "1 offline" beats "sources error".
  const srcLabel = nOff ? `${sources.length} sources · ${nOff} offline`
    : nStale ? `${sources.length} sources · ${nStale} stale`
      : `${sources.length} source${sources.length === 1 ? "" : "s"} live`;
  const oldest = sources.reduce((acc, s) => {
    const t = s.last_synced ? new Date(s.last_synced).getTime() : null;
    return t && (!acc || t < acc) ? t : acc;
  }, null);

  const font = "Inter,sans-serif";
  const bar = {
    display: "flex", alignItems: "center", justifyContent: "space-between", gap: 20,
    minHeight: 64, padding: "0 24px", background: T.white,
    borderBottom: `1px solid ${T.line}`, flexWrap: "wrap",
  };
  const arrow = (side) => ({
    width: 34, height: 38, border: 0, background: "transparent", cursor: "pointer",
    color: T.slate, fontSize: 16, lineHeight: 1,
    [side === "left" ? "borderRight" : "borderLeft"]: `1px solid ${T.line}`,
  });
  const seg = (on) => ({
    height: 30, padding: "0 13px", border: 0, borderRadius: 8, cursor: "pointer",
    fontFamily: font, fontSize: 13, fontWeight: on ? 700 : 500, whiteSpace: "nowrap",
    background: on ? T.evergreen : "transparent", color: on ? T.onDark : T.slate,
    transition: "background .14s, color .14s",
  });
  const chip = {
    display: "inline-flex", alignItems: "center", gap: 8, height: 34, padding: "0 12px",
    border: `1px solid ${T.line}`, borderRadius: 10, background: T.parchment,
    cursor: "pointer", fontFamily: font,
  };
  // No global border-box reset in this app, so width:100% + padding + border would overflow
  // the popover and shove the native calendar icon past its edge.
  const dateInput = {
    width: "100%", boxSizing: "border-box", fontFamily: font, fontSize: 12,
    padding: "7px 9px", border: `1px solid ${T.line}`, borderRadius: 7,
    color: T.ink, background: T.white, accentColor: T.evergreen,
  };

  return (
    <header>
      <style>{`
        /* On a phone the bar wraps to ~270px tall — a third of the screen. Drop the pieces
           that repeat what the window button already says, and tighten the rest. */
        @media (max-width: 700px) {
          .pn-note { display: none !important; }
          .pn-bar { padding: 0 14px !important; gap: 10px !important; }
          .pn-left, .pn-right { padding: 8px 0 !important; gap: 8px !important; }
          .pn-srclabel { font-size: 12px !important; }
        }
        @media (max-width: 430px) {
          /* the chip keeps its status dot; the words go */
          .pn-srclabel { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
        }
      `}</style>
      <div className="pn-bar" style={bar}>
        <div className="pn-left" style={{ display: "flex", alignItems: "center", gap: 14, minWidth: 0, flexWrap: "wrap", padding: "12px 0" }}>
          {menuSlot}
          {/* value + stepper — walking periods without changing granularity */}
          <div style={{ position: "relative", display: "flex" }}>
            <div style={{
              display: "flex", alignItems: "center", height: 38, overflow: "hidden",
              border: `1px solid ${T.line}`, borderRadius: 11, background: T.parchment,
            }}>
              <button className="cc-nav" style={arrow("left")} aria-label="Previous period"
                onClick={() => emit(grain, step(grain, anchor, -1))}>‹</button>
              <button className="cc-nav" onClick={() => setPickerOpen((v) => !v)} aria-haspopup="dialog"
                aria-expanded={pickerOpen} aria-label={`Change period, currently ${fmtWindow(win)}`}
                style={{
                  height: "100%", border: 0, background: "transparent", padding: "0 14px",
                  cursor: "pointer", display: "flex", alignItems: "center", gap: 9,
                }}>
                <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 15, fontWeight: 600, color: T.ink, letterSpacing: "-0.01em", whiteSpace: "nowrap" }}>
                  {fmtWindow(win)}
                </span>
                <span style={{ fontSize: 9, color: T.muted }} aria-hidden>▾</span>
              </button>
              <button className="cc-nav" style={arrow("right")} aria-label="Next period"
                onClick={() => emit(grain, step(grain, anchor, 1))}>›</button>
            </div>

            {pickerOpen && (
              <>
                <div onClick={() => setPickerOpen(false)} style={{ position: "fixed", inset: 0, zIndex: 40 }} />
                <div style={{
                  position: "absolute", top: "calc(100% + 6px)", left: 0, zIndex: 41, width: 250,
                  background: T.white, border: `1px solid ${T.line}`, borderRadius: 12, padding: 12,
                  boxShadow: `0 18px 40px ${alpha(T.evergreen, 0.16)}`,
                }}>
                  <div style={{ fontFamily: font, fontSize: 10, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: T.tertiary, marginBottom: 7 }}>
                    Custom range
                  </div>
                  <label style={{ display: "block", fontFamily: font, fontSize: 10.5, color: T.muted, marginBottom: 2 }}>From</label>
                  <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} style={{ ...dateInput, marginBottom: 7 }} />
                  <label style={{ display: "block", fontFamily: font, fontSize: 10.5, color: T.muted, marginBottom: 2 }}>To</label>
                  <input type="date" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} style={{ ...dateInput, marginBottom: 10 }} />
                  <button onClick={applyRange} disabled={!from || !to || to < from} style={{
                    width: "100%", fontFamily: font, fontSize: 12, fontWeight: 700, color: T.onDark,
                    background: (!from || !to || to < from) ? T.muted : T.evergreen,
                    border: "none", borderRadius: 7, padding: "7px 0",
                    cursor: (!from || !to || to < from) ? "default" : "pointer",
                  }}>Apply range</button>
                  <div style={{ fontFamily: font, fontSize: 10, color: T.muted, marginTop: 8, lineHeight: 1.45 }}>
                    Booked (QuickBooks) P&amp;L is snapshot per standard period — a custom or future
                    window shows the live/cash view instead.
                  </div>
                </div>
              </>
            )}
          </div>

          {/* granularity — a control, not a value */}
          <div style={{ display: "flex", alignItems: "center", gap: 2, padding: 3, background: T.parchment, borderRadius: 11 }}
               role="tablist" aria-label="Period granularity">
            {GRAINS.map(([k, label]) => (
              <button key={k} role="tab" aria-selected={k === grain} className="cc-nav"
                style={seg(k === grain)} onClick={() => emit(k, anchor)}>{label}</button>
            ))}
          </div>

          {!isNow && (
            <button className="cc-nav" onClick={() => emit(grain, new Date())}
              title="Jump back to the current period"
              style={{
                fontFamily: font, fontSize: 12, fontWeight: 600, color: T.teal,
                background: "transparent", border: `1px solid ${alpha(T.teal, 0.3)}`,
                borderRadius: 8, padding: "5px 10px", cursor: "pointer", whiteSpace: "nowrap",
              }}>Today</button>
          )}

          <div className="pn-note" style={{ width: 1, height: 22, background: T.line }} aria-hidden />
          {/* what the SERVER actually computed — the honest label for the data on screen */}
          <div className="pn-note" style={{
            fontFamily: font, fontSize: 11, letterSpacing: ".08em", textTransform: "uppercase",
            color: T.muted, whiteSpace: "nowrap",
          }}>{serverLabel || (loading ? "Loading…" : "")}</div>
        </div>

        <div className="pn-right" style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0, padding: "12px 0" }}>
          <button style={chip} className="cc-nav" onClick={() => setSourcesOpen((v) => !v)}
            aria-expanded={sourcesOpen} aria-controls="cc-source-drawer">
            <span aria-hidden style={{
              width: 7, height: 7, borderRadius: 99, background: STATUS_COLOR[worst],
              boxShadow: `0 0 0 3px ${alpha(STATUS_COLOR[worst], 0.18)}`,
            }} />
            <span className="pn-srclabel" style={{ fontSize: 12.5, fontWeight: 600, color: T.slate }}>{srcLabel}</span>
            {oldest && <span className="pn-note" style={{ fontSize: 11.5, color: T.muted }}>· {relativeTime(new Date(oldest).toISOString())}</span>}
          </button>

          <button onClick={onRefresh} disabled={!apiEnabled || refreshing} className="cc-nav"
            title={apiEnabled ? "Sync all sources" : "Available on the live app"} aria-label="Refresh data"
            style={{
              width: 34, height: 34, display: "inline-flex", alignItems: "center", justifyContent: "center",
              border: `1px solid ${T.line}`, borderRadius: 10, background: T.parchment,
              cursor: apiEnabled && !refreshing ? "pointer" : "not-allowed",
            }}>
            <span style={{ display: "inline-flex", animation: refreshing ? "cc-spin 0.9s linear infinite" : "none" }}>
              <Icon name="sync" size={14} color={apiEnabled ? T.slate : T.muted} />
            </span>
          </button>

          {accountSlot}
        </div>
      </div>

      {sourcesOpen && (
        <div id="cc-source-drawer" style={{
          display: "flex", alignItems: "center", flexWrap: "wrap", gap: 8,
          padding: "11px 24px", background: T.parchment, borderBottom: `1px solid ${T.line}`,
        }}>
          <span style={{
            fontFamily: font, fontSize: 11, letterSpacing: ".08em", textTransform: "uppercase", color: T.muted, marginRight: 2,
          }}>Live from</span>
          {sources.map((s) => (
            <span key={s.name} style={{
              display: "inline-flex", alignItems: "center", gap: 7, padding: "5px 10px",
              border: `1px solid ${T.line}`, borderRadius: 8, background: T.white,
              fontFamily: font, fontSize: 12.5, color: T.slate,
            }}>
              <span aria-hidden style={{ width: 6, height: 6, borderRadius: 99, background: STATUS_COLOR[srcStatus(s)] }} />
              {s.name}
              <span style={{ fontSize: 11.5, color: T.muted }}>
                {s.last_synced ? relativeTime(s.last_synced) : s.status}
              </span>
            </span>
          ))}
          {updated && !refreshing && (
            <span style={{ fontFamily: font, fontSize: 11, color: T.muted, marginLeft: 2 }}>
              Synced {relativeTime(updated)}
            </span>
          )}
        </div>
      )}
    </header>
  );
}
