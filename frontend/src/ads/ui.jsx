/* Ads screen primitives. One accent, one type scale, scoped to this module.
   Colours come from adsTokens so a rebrand still has a single place to move. */
import { useEffect } from "react";

import { C, FIG, FONT, HEAD } from "./adsTokens.js";

export const SCREENS = [
  { id: "today", label: "Today" },
  { id: "performance", label: "Performance" },
  { id: "budget", label: "Budget" },
  { id: "activity", label: "Activity" },
  { id: "settings", label: "Settings" },
];

export function weeklyPace(data) {
  const groups = data?.groups || [];
  const plan = groups.reduce((sum, g) => sum + (Number(g.weekly_budget) || 0), 0);
  const spend = groups.reduce((sum, g) => sum + (Number(g.spend) || 0), 0);
  const days = data?.range?.days || 7;
  const expected = plan ? plan * days / 7 : 0;
  const pace = expected ? (spend / expected) * 100 : null;
  let status = "none";
  if (pace != null) status = pace < 90 ? "behind" : pace > 110 ? "ahead" : "on";
  const today = new Date().toISOString().slice(0, 10);
  const open = !!(data?.range?.end && data.range.end >= today);
  return { plan, spend, expected, pace, status, open, days };
}

/* The open week stays readable. Mix the status color toward paper instead of
   fading the whole bar, which would turn it grey. */
export function tone(status, open) {
  const solid = status === "behind" ? C.badBar : status === "ahead" ? C.goodBar : C.accent;
  const ink = status === "behind" ? C.badInk : status === "ahead" ? C.goodInk : C.accent;
  if (!open) return { bar: solid, ink };
  return {
    bar: `color-mix(in srgb, ${solid} 58%, ${C.parchment})`,
    ink: `color-mix(in srgb, ${ink} 62%, ${C.slate})`,
  };
}

export function PageTitle({ kicker, title, lede, children }) {
  return (
    <header style={{ display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap", alignItems: "flex-end" }}>
      <div>
        {kicker && <div style={{ fontFamily: FONT, fontSize: 12, fontWeight: 600, color: C.muted }}>{kicker}</div>}
        <h2 style={{ fontFamily: HEAD, fontSize: 22, fontWeight: 600, color: C.ink, margin: "4px 0 0" }}>{title}</h2>
        {lede && <p style={{ fontFamily: FONT, fontSize: 14, color: C.slate, margin: "6px 0 0", maxWidth: 640, lineHeight: 1.5 }}>{lede}</p>}
      </div>
      {children}
    </header>
  );
}

export function Card({ children, style }) {
  return (
    <section style={{ background: C.surface, border: `1px solid ${C.line}`, borderRadius: 14, padding: 16, ...style }}>
      {children}
    </section>
  );
}

export function Btn({ children, onClick, disabled, primary, title }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} title={title}
            style={{
              fontFamily: FONT, fontSize: 13, fontWeight: 600, borderRadius: 8, padding: "7px 12px",
              cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.55 : 1,
              border: `1px solid ${primary ? C.evergreen : C.line}`,
              background: primary ? C.evergreen : C.surface,
              color: primary ? C.onDark : C.ink,
            }}>
      {children}
    </button>
  );
}

export function Banner({ title, children, tone: kind }) {
  const bad = kind === "bad";
  return (
    <div style={{
      background: bad ? C.badBg : C.goodBg, border: `1px solid ${bad ? C.badBar : C.goodBar}`,
      borderRadius: 12, padding: "12px 14px",
    }}>
      <div style={{ fontFamily: HEAD, fontSize: 15, fontWeight: 600, color: C.ink }}>{title}</div>
      {children && <div style={{ fontFamily: FONT, fontSize: 13, color: C.slate, marginTop: 6, lineHeight: 1.5 }}>{children}</div>}
    </div>
  );
}

export function Empty({ children }) {
  return <p style={{ fontFamily: FONT, fontSize: 14, color: C.muted, margin: 0 }}>{children}</p>;
}

export function ErrorNote({ children, onRetry }) {
  return (
    <p style={{ fontFamily: FONT, fontSize: 13, color: C.badInk, margin: 0 }}>
      {children}{" "}
      {onRetry && <button type="button" onClick={onRetry} style={{ background: "none", border: "none", color: C.accent, cursor: "pointer", font: "inherit", padding: 0 }}>Try again</button>}
    </p>
  );
}

export function PaceBar({ pace }) {
  const t = tone(pace.status, pace.open);
  const width = pace.pace == null ? 0 : Math.max(4, Math.min(100, pace.pace > 100 ? 100 : pace.pace));
  const label = pace.status === "behind" ? "Behind" : pace.status === "ahead" ? "Ahead" : pace.status === "on" ? "On pace" : "No weekly plan";
  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "baseline" }}>
        <div style={{ fontFamily: HEAD, fontSize: 16, fontWeight: 600, color: C.ink }}>Weekly Pace</div>
        <div style={{ fontFamily: FONT, fontSize: 13, fontWeight: 600, color: t.ink }}>{label}{pace.open ? " · this week is still open" : ""}</div>
      </div>
      <div style={{ marginTop: 12, height: 10, borderRadius: 999, background: C.surface2, overflow: "hidden" }}>
        <div style={{ width: `${width}%`, height: "100%", background: t.bar, borderRadius: 999 }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontFamily: FIG, fontSize: 13, color: C.slate }}>
        <span>{pace.plan ? `$${Math.round(pace.spend).toLocaleString()} spent` : "Set a weekly plan on Budget"}</span>
        <span>{pace.expected ? `$${Math.round(pace.expected).toLocaleString()} of the plan for these ${pace.days} days` : ""}</span>
      </div>
    </Card>
  );
}

export function Drawer({ title, onClose, children }) {
  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape") onClose?.();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <aside style={{
      position: "fixed", top: 0, right: 0, bottom: 0, width: "min(420px, 100%)",
      background: C.surface, borderLeft: `1px solid ${C.line}`, zIndex: 40,
      padding: 20, overflow: "auto", boxShadow: "0 0 40px rgba(0,0,0,.08)",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
        <h3 style={{ fontFamily: HEAD, fontSize: 18, margin: 0, color: C.ink }}>{title}</h3>
        <Btn onClick={onClose}>Close</Btn>
      </div>
      <div style={{ marginTop: 16 }}>{children}</div>
    </aside>
  );
}

export function Field({ label, children }) {
  return (
    <label style={{ display: "grid", gap: 4, fontFamily: FONT, fontSize: 13, color: C.ink }}>
      <span style={{ fontWeight: 600, color: C.slate }}>{label}</span>
      {children}
    </label>
  );
}

export function sentenceStatus(status) {
  if (!status) return null;
  const s = String(status).toLowerCase().replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/* Yoga2 and ResultsH3 are headline variants of one video, not a new ad. Size
   tokens like 1080 stay, so a static crop is not folded into a different file. */
export function familyKey(name) {
  let s = String(name || "");
  const variant = /\bresults\s*h\d+\b/i.test(s) || /^\s*video\b/i.test(s);
  s = s.replace(/\bresults\s*h\d+\b/gi, "");
  s = s.replace(/\bv\d+\b/gi, "");
  if (variant) s = s.replace(/(\D)\d+\b/g, "$1");
  return s.replace(/[-–—_]+/g, " ").replace(/\s+/g, " ").trim().toLowerCase();
}

export function foldRows(rows) {
  const groups = [];
  const index = new Map();
  for (const row of rows) {
    const key = `${row.verdict}:${familyKey(row.name) || row.external_id}`;
    if (!index.has(key)) {
      const bucket = { key, rows: [] };
      index.set(key, bucket);
      groups.push(bucket);
    }
    index.get(key).rows.push(row);
  }
  return groups;
}

export const inputStyle = {
  fontFamily: FIG, fontSize: 14, padding: "7px 8px", borderRadius: 8,
  border: `1px solid ${C.line}`, color: C.ink, background: C.surface, width: 120,
};
