/* AI Employee detail — the run surface (SPEC-ai-employees-run-surface).
   Derives everything from real run state (GET /ai/runs/{id}), NOT the mockup's scripted
   timeline: trigger banner → pipeline strip → diagnosis reads → artifact rows with the six
   payload renderers → batched approve. Below: run history, roster, brief archive.
   Copy discipline (§2): no marketing narrative, no hardcoded names — every string is either
   a functional label or model output from the payload. Tokens per §6: teal=active/running,
   meadow=done, daffodil SATURATED only on the Approve button; lanes carry the soft palette.
   House pattern: inline styles + injected <style>, no CSS file. */
import { useState } from "react";
import { T, relativeTime } from "./theme.js";
import { Icon } from "./Brand.jsx";
import { postJSON } from "./api.js";
import { useAiEmployeeDetail } from "./useAiEmployeeDetail.js";

const MONO = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace";
// Lane palette — locked hexes from run-surface §6 (theme.js lacks the soft lane tints).
const LANE = {
  Intel:    { c: "#227175", bg: "#E6F0F1" },
  Strategy: { c: "#4D6A4D", bg: "#E9EFE7" },
  Creative: { c: "#B26248", bg: "#FCEDE5" },
  Tracking: { c: "#6D5336", bg: "#F6EFDC" },
};
const KIND_ICON = { audit: "search", trend: "growth_graph", strategy: "line_chart",
  design: "puzzle", script: "spark", measure: "link" };

// Pipeline: five steps; state derived from run.status (§4.3).
const STEPS = [
  { id: "signal", label: "Pace Check", sub: "Trigger", icon: "growth_graph" },
  { id: "read", label: "Diagnose", sub: "Read the signal", icon: "line_chart" },
  { id: "coordinate", label: "Coordinate", sub: "Draft the work", icon: "puzzle" },
  { id: "review", label: "Human Approves", sub: "Draft mode", icon: "check" },
  { id: "write", label: "Ship & Track", sub: "On approval", icon: "sync" },
];
// index of the first not-yet-done step per status (everything before = done, this one = active)
const ACTIVE_AT = { queued: 1, running: 2, awaiting_approval: 3, approved: 4, shipped: 5,
  failed: 3, dismissed: 5, skipped_budget: 1 };

function stepState(status, i) {
  const active = ACTIVE_AT[status] ?? 5;
  if (["dismissed", "skipped_budget"].includes(status)) return "muted";
  if (i < active) return "done";
  if (i === active) return status === "failed" ? "error" : "active";
  return "pending";
}

/* ── shared UI ────────────────────────────────────────────────── */
function Card({ children, style }) {
  return <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 16, boxShadow: "0 2px 10px rgba(0,46,44,.05)", ...style }}>{children}</div>;
}
function LaneChip({ lane }) {
  const l = LANE[lane] || { c: T.slate, bg: T.parchment };
  return <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 10.5, fontWeight: 700, letterSpacing: "0.04em",
    color: l.c, background: l.bg, borderRadius: 999, padding: "3px 9px" }}>{lane}</span>;
}
function StatusChip({ status }) {
  const map = {
    awaiting_approval: [T.daffodilText, T.daffodilBg, "Awaiting approval"],
    approved: [T.meadowInk, T.meadowBg, "Approved"], shipped: [T.meadowInk, T.meadowBg, "Shipped"],
    running: [T.teal, "#E6F0F1", "Running"], queued: [T.slate, T.parchment, "Queued"],
    failed: [T.poppyText, T.white, "Failed"], skipped_budget: [T.muted, T.parchment, "Skipped · budget"],
    dismissed: [T.muted, T.parchment, "Dismissed"],
  };
  const [c, bg, label] = map[status] || [T.slate, T.parchment, status];
  return <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, fontWeight: 700, color: c, background: bg,
    border: `1px solid ${T.line}`, borderRadius: 6, padding: "2px 8px" }}>{label}</span>;
}

/* ── header ───────────────────────────────────────────────────── */
function Header({ employee, onBack, writebackEnvOpen }) {
  const wb = writebackEnvOpen && employee.writeback_enabled;
  return (
    <div style={{ marginBottom: 16 }}>
      <button onClick={onBack} className="cc-nav" style={{ display: "inline-flex", alignItems: "center", gap: 6,
        background: "transparent", border: "none", cursor: "pointer", padding: "2px 0", marginBottom: 12,
        fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.slate }}>
        <Icon name="chevron_backward" size={13} color={T.slate} />All employees
      </button>
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <div style={{ width: 48, height: 48, borderRadius: 12, background: employee.avatar_color || T.teal,
          display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "Poppins,sans-serif",
          fontSize: 20, fontWeight: 700, color: T.onDark, flexShrink: 0 }}>
          {(employee.name || "?").trim().charAt(0).toUpperCase()}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 20, fontWeight: 700, color: T.ink }}>{employee.name}</div>
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.muted }}>{employee.role_title}</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: employee.status === "active" ? T.tertiary : T.muted,
            display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span className={employee.status === "active" ? "ai-pulse" : ""} style={{ width: 8, height: 8, borderRadius: 99,
              background: employee.status === "active" ? T.meadow : T.muted }} />
            {employee.status === "active" ? "Active" : "Paused"}</span>
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, fontWeight: 700, color: wb ? T.meadowInk : T.muted,
            background: wb ? T.meadowBg : T.parchment, border: `1px solid ${T.line}`, borderRadius: 6, padding: "2px 8px" }}>
            Writeback {wb ? "on" : "off"}</span>
        </div>
      </div>
    </div>
  );
}

/* ── pipeline strip ───────────────────────────────────────────── */
function Pipeline({ status }) {
  return (
    <div style={{ display: "flex", gap: 0, overflowX: "auto", padding: "4px 2px", marginBottom: 16 }}>
      {STEPS.map((st, i) => {
        const s = stepState(status, i);
        const color = { done: T.meadow, active: T.teal, error: T.poppy, pending: T.muted, muted: T.muted }[s];
        const bg = { done: T.meadowBg, active: "#E6F0F1", error: T.white, pending: T.parchment, muted: T.parchment }[s];
        return (
          <div key={st.id} style={{ display: "flex", alignItems: "center", flex: "1 0 auto" }}>
            <div aria-current={s === "active" ? "step" : undefined} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 10px" }}>
              <span className={s === "active" ? "ai-pulse" : ""} style={{ width: 26, height: 26, borderRadius: 8, background: bg,
                display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                <Icon name={s === "done" ? "check" : st.icon} size={13} color={color} /></span>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 11.5, fontWeight: 700,
                  color: s === "pending" || s === "muted" ? T.muted : T.ink, whiteSpace: "nowrap" }}>{st.label}</div>
                <div style={{ fontFamily: "Inter,sans-serif", fontSize: 10, color: T.muted, whiteSpace: "nowrap" }}>{st.sub}</div>
              </div>
            </div>
            {i < STEPS.length - 1 && <span style={{ width: 16, height: 2, background: i < (ACTIVE_AT[status] ?? 5) ? T.meadow : T.line, flexShrink: 0 }} />}
          </div>
        );
      })}
    </div>
  );
}

/* ── trigger banner ───────────────────────────────────────────── */
function TriggerBanner({ ctx, trigger }) {
  if (!ctx) return null;
  return (
    <Card style={{ padding: 16, marginBottom: 14, borderLeft: `3px solid ${T.teal}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
        {trigger === "condition" && (
          <span className="ai-trigger" style={{ fontFamily: "Poppins,sans-serif", fontSize: 9.5, fontWeight: 800, letterSpacing: "0.14em",
            color: T.teal, background: "#E6F0F1", borderRadius: 4, padding: "2px 6px" }}>TRIGGER</span>)}
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 10.5, fontWeight: 700, letterSpacing: "0.06em", color: T.tertiary, textTransform: "uppercase" }}>
          {ctx.source}{ctx.label ? ` · ${ctx.label}` : ""}</span>
      </div>
      <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 15.5, fontWeight: 600, color: T.ink, marginBottom: 10 }}>{ctx.title}</div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {(ctx.facts || []).map((f, i) => (
          <span key={i} style={{ fontFamily: MONO, fontSize: 11.5, color: T.secondary, background: T.parchment,
            border: `1px solid ${T.line}`, borderRadius: 6, padding: "3px 8px" }}>{f}</span>
        ))}
      </div>
    </Card>
  );
}

/* ── diagnosis ────────────────────────────────────────────────── */
function Diagnosis({ reads, name }) {
  if (!reads || !reads.length) return null;
  return (
    <Card style={{ padding: "14px 16px", marginBottom: 14 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
        <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600, color: T.ink }}>{name ? `${name} diagnosed the gap` : "Diagnosis"}</div>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, fontWeight: 700, color: T.tertiary, background: T.mist, borderRadius: 6, padding: "2px 8px" }}>{reads.length} reads</span>
      </div>
      {reads.map((r, i) => (
        <div key={i} style={{ display: "flex", gap: 8, alignItems: "flex-start", marginBottom: i < reads.length - 1 ? 6 : 0 }}>
          <span style={{ width: 5, height: 5, borderRadius: 99, background: T.teal, marginTop: 6, flexShrink: 0 }} />
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13, lineHeight: 1.5, color: T.secondary }}>{r}</span>
        </div>
      ))}
    </Card>
  );
}

/* ── the six preview renderers (payload shapes = Appendix A) ───── */
function Note({ children }) {
  return <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, fontStyle: "italic", color: T.muted, marginTop: 10 }}>{children}</div>;
}
function AuditPreview({ p }) {
  return (
    <div>
      <div style={{ fontFamily: MONO, fontSize: 12, color: T.teal, marginBottom: 10 }}>{p.handle}</div>
      {(p.top || []).map((t, i) => (
        <div key={i} style={{ marginBottom: 8 }}>
          <div style={{ display: "flex", justifyContent: "space-between", fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.ink, marginBottom: 3 }}>
            <span>{t.name}</span><span style={{ fontWeight: 700, fontFamily: MONO }}>{t.val}</span></div>
          <div style={{ height: 6, background: T.parchment, borderRadius: 99 }}>
            <div style={{ width: t.w, height: 6, background: LANE.Intel.c, borderRadius: 99 }} /></div>
        </div>
      ))}
      {(p.mechanics || []).length > 0 && (
        <ul style={{ margin: "10px 0 0", paddingLeft: 16 }}>
          {p.mechanics.map((m, i) => <li key={i} style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.secondary, marginBottom: 3 }}>{m}</li>)}
        </ul>)}
      {p.note && <Note>{p.note}</Note>}
    </div>
  );
}
function TrendPreview({ p }) {
  return (
    <div>
      {p.scanned && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted, marginBottom: 10 }}>{p.scanned}</div>}
      {(p.patterns || []).map((pt, i) => (
        <div key={i} style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center", padding: "6px 0", borderBottom: `1px solid ${T.line}` }}>
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.ink }}>{pt.p}</span>
          <span style={{ fontFamily: MONO, fontSize: 11.5, fontWeight: 700, color: LANE.Strategy.c, background: LANE.Strategy.bg, borderRadius: 6, padding: "2px 7px", flexShrink: 0 }}>{pt.d}</span>
        </div>
      ))}
      {(p.timely || []).length > 0 && (
        <ul style={{ margin: "10px 0 0", paddingLeft: 16 }}>
          {p.timely.map((t, i) => <li key={i} style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.secondary, marginBottom: 3 }}>{t}</li>)}
        </ul>)}
      {p.change && <div style={{ background: T.meadowBg, borderRadius: 10, padding: "10px 12px", marginTop: 10 }}>
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 10.5, fontWeight: 700, color: T.meadowInk, letterSpacing: "0.05em" }}>THE CHANGE  </span>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.secondary }}>{p.change}</span></div>}
      {p.note && <Note>{p.note}</Note>}
    </div>
  );
}
function StrategyPreview({ p }) {
  return (
    <div>
      {p.title && <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 13.5, fontWeight: 600, color: T.ink, marginBottom: 6 }}>{p.title}</div>}
      {p.shift && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.secondary, lineHeight: 1.5, marginBottom: 10 }}>{p.shift}</div>}
      {(p.plan || []).map((d, i) => (
        <div key={i} style={{ display: "flex", gap: 10, alignItems: "center", padding: "6px 0", borderBottom: `1px solid ${T.line}` }}>
          <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 10.5, fontWeight: 700, color: T.teal, background: "#E6F0F1", borderRadius: 6, padding: "2px 8px", flexShrink: 0, minWidth: 46, textAlign: "center" }}>{d.day}</span>
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.ink, flex: 1 }}>{d.what}</span>
          {d.cta && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, color: T.muted, flexShrink: 0 }}>{d.cta}</span>}
        </div>
      ))}
      {p.why && <Note>{p.why}</Note>}
    </div>
  );
}
function DesignPreview({ p }) {
  return (
    <div>
      <div style={{ display: "flex", gap: 10, overflowX: "auto", paddingBottom: 6 }}>
        {(p.slides || []).map((s, i) => (
          <div key={i} style={{ flex: "0 0 130px", height: 160, borderRadius: 12, background: s.bg, color: s.fg,
            border: `1px solid ${T.line}`, padding: 12, display: "flex", flexDirection: "column", justifyContent: "space-between" }}>
            <span style={{ fontFamily: MONO, fontSize: 9, opacity: 0.6 }}>{i + 1}/{p.slides.length}</span>
            <div>
              <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600, lineHeight: 1.25 }}>{s.h}</div>
              {s.sub && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, opacity: 0.85, marginTop: 4 }}>{s.sub}</div>}
            </div>
          </div>
        ))}
      </div>
      {p.caption && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.secondary, marginTop: 8 }}>{p.caption}</div>}
      {p.note && <Note>{p.note}</Note>}
    </div>
  );
}
function ScriptPreview({ p }) {
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        {p.hookType && <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 10, fontWeight: 700, color: LANE.Creative.c, background: LANE.Creative.bg, borderRadius: 999, padding: "2px 8px" }}>{p.hookType}</span>}
      </div>
      {p.hook && <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 15, fontWeight: 600, color: T.ink, lineHeight: 1.35, marginBottom: 12 }}>“{p.hook}”</div>}
      {(p.shots || []).map((sh, i) => (
        <div key={i} style={{ display: "flex", gap: 10, padding: "7px 0", borderTop: `1px solid ${T.line}` }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: T.muted, flexShrink: 0 }}>{i + 1}</span>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted }}>{sh.vis}</div>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.ink, marginTop: 2 }}>{sh.vo}</div>
          </div>
        </div>
      ))}
    </div>
  );
}
function MeasurePreview({ p }) {
  return (
    <div>
      {(p.tags || []).map((t, i) => (
        <div key={i} style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center", padding: "6px 0", borderBottom: `1px solid ${T.line}` }}>
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.ink, flexShrink: 0 }}>{t.asset}</span>
          <span style={{ fontFamily: MONO, fontSize: 11, color: LANE.Tracking.c, background: LANE.Tracking.bg, borderRadius: 6, padding: "3px 8px", overflowX: "auto" }}>{t.utm}</span>
        </div>
      ))}
      {p.note && <Note>{p.note}</Note>}
    </div>
  );
}
const PREVIEWS = { audit: AuditPreview, trend: TrendPreview, strategy: StrategyPreview,
  design: DesignPreview, script: ScriptPreview, measure: MeasurePreview };
function Preview({ kind, payload }) {
  const C = PREVIEWS[kind];
  if (!C) return <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.muted }}>No preview for “{kind}”.</div>;
  return <C p={payload || {}} />;
}

/* ── artifact row (expand) ────────────────────────────────────── */
function ArtifactRow({ a, open, onToggle, onDismiss, canManage, pending }) {
  const expandable = a.state === "draft";     // approved/shipped rows are read-only (§ Open Item 4)
  const shipped = a.state === "shipped", approved = a.state === "approved" || shipped;
  return (
    <div style={{ borderTop: `1px solid ${T.line}` }}>
      <div role="button" tabIndex={expandable ? 0 : -1} aria-expanded={expandable ? open : undefined}
        onClick={() => expandable && onToggle()}
        onKeyDown={(e) => { if (expandable && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); onToggle(); } }}
        style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 4px", cursor: expandable ? "pointer" : "default" }}>
        <LaneChip lane={a.lane} />
        <Icon name={KIND_ICON[a.kind] || "spark"} size={14} color={T.muted} />
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.ink, flex: 1, minWidth: 0 }}>{a.title}</span>
        {a.dest_label && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted, flexShrink: 0 }}>{a.dest_label}</span>}
        {pending ? <span className="ai-spin" style={{ width: 14, height: 14, borderRadius: 99, border: `2px solid ${T.line}`, borderTopColor: T.teal, flexShrink: 0 }} />
          : approved ? <Icon name="check" size={15} color={T.meadow} />
          : expandable ? <Icon name="chevron_down" size={14} color={T.muted} style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform .15s" }} />
          : null}
      </div>
      {expandable && open && (
        <div role="region" aria-label={a.title} style={{ padding: "4px 4px 16px" }}>
          <div style={{ background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 12, padding: 14 }}>
            <Preview kind={a.kind} payload={a.payload} />
          </div>
          {canManage && (
            <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8 }}>
              <button onClick={onDismiss} className="cc-nav" style={{ fontFamily: "Poppins,sans-serif", fontSize: 11.5, fontWeight: 600,
                color: T.muted, background: "transparent", border: `1px solid ${T.line}`, borderRadius: 8, padding: "5px 12px", cursor: "pointer" }}>Dismiss</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── approve bar ──────────────────────────────────────────────── */
function ApproveBar({ drafts, canManage, wbLine, onApproveAll, busy, err }) {
  return (
    <div style={{ marginTop: 14, padding: 14, borderRadius: 14, background: T.daffodilBg, border: `1px solid #F0E3AC` }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 12.5, fontWeight: 700, color: T.daffodilText }}>Review each item, or approve the batch.</div>
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.amber, marginTop: 2 }}>{wbLine}</div>
        </div>
        {canManage ? (
          <button onClick={onApproveAll} disabled={busy || drafts === 0} style={{ fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 700,
            color: T.ink, background: T.daffodil, border: "none", borderRadius: 10, padding: "9px 18px",
            cursor: busy || drafts === 0 ? "default" : "pointer", opacity: busy || drafts === 0 ? 0.6 : 1 }}>
            {busy ? "Approving…" : `Approve all (${drafts})`}</button>
        ) : (
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted }}>Approvals are limited to owners and admins.</span>
        )}
      </div>
      {err && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.poppyText, marginTop: 8 }}>{err}</div>}
    </div>
  );
}

/* ── run history / roster / briefs (below the surface) ────────── */
function HistoryPanel({ runs, currentId, onOpen }) {
  if (!runs || !runs.length) return null;
  return (
    <Card style={{ padding: 16, marginTop: 18 }}>
      <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 12.5, fontWeight: 700, color: T.ink, marginBottom: 10 }}>Run history</div>
      {runs.map((r) => (
        <div key={r.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 0", borderTop: `1px solid ${T.line}` }}>
          <StatusChip status={r.status} />
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted, textTransform: "capitalize", flexShrink: 0 }}>{r.trigger}</span>
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.secondary, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.summary || r.skill_key}</span>
          {r.created_at && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted, flexShrink: 0 }}>{relativeTime(r.created_at)}</span>}
          <button onClick={() => onOpen(r.id)} className="cc-nav" style={{ fontFamily: "Poppins,sans-serif", fontSize: 11, fontWeight: 600,
            color: r.id === currentId ? T.teal : T.slate, background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 7, padding: "4px 10px", cursor: "pointer", flexShrink: 0 }}>
            {r.id === currentId ? "Viewing" : "Open"}</button>
        </div>
      ))}
    </Card>
  );
}

function RosterPanel({ employeeId, roster, canManage, onChanged, onAudit }) {
  const [adding, setAdding] = useState(false);
  const [handle, setHandle] = useState("");
  const [why, setWhy] = useState("");
  const [busy, setBusy] = useState(false);
  async function add() {
    if (!handle.trim()) return;
    setBusy(true);
    try { await postJSON(`/ai/employees/${employeeId}/roster`, { handle: handle.trim(), why: why.trim() || null });
      setHandle(""); setWhy(""); setAdding(false); onChanged && onChanged(); }
    catch { /* surfaced by reload */ } finally { setBusy(false); }
  }
  return (
    <Card style={{ padding: 16, marginTop: 18 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 10 }}>
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 12.5, fontWeight: 700, color: T.ink }}>Watch roster</span>
        {canManage && <button onClick={() => setAdding((v) => !v)} className="cc-nav" style={{ fontFamily: "Poppins,sans-serif", fontSize: 11, fontWeight: 600,
          color: T.slate, background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 7, padding: "4px 10px", cursor: "pointer" }}>{adding ? "Cancel" : "Add account"}</button>}
      </div>
      {adding && (
        <div style={{ display: "flex", gap: 8, marginBottom: 10, flexWrap: "wrap" }}>
          <input value={handle} onChange={(e) => setHandle(e.target.value)} placeholder="@handle" style={{ flex: "1 1 120px", fontFamily: "Inter,sans-serif", fontSize: 12.5, border: `1px solid ${T.line}`, borderRadius: 8, padding: "7px 10px" }} />
          <input value={why} onChange={(e) => setWhy(e.target.value)} placeholder="Why watch them" style={{ flex: "2 1 180px", fontFamily: "Inter,sans-serif", fontSize: 12.5, border: `1px solid ${T.line}`, borderRadius: 8, padding: "7px 10px" }} />
          <button onClick={add} disabled={busy} style={{ fontFamily: "Poppins,sans-serif", fontSize: 12, fontWeight: 600, color: T.onDark, background: T.evergreen, border: "none", borderRadius: 8, padding: "7px 14px", cursor: "pointer" }}>Add</button>
        </div>
      )}
      {(!roster || !roster.length) ? (
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.muted, fontStyle: "italic" }}>No accounts watched yet.</div>
      ) : roster.map((r) => (
        <div key={r.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 0", borderTop: `1px solid ${T.line}` }}>
          <span style={{ fontFamily: MONO, fontSize: 12, color: T.ink, flexShrink: 0 }}>{r.handle}</span>
          {r.in_launch && <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 9.5, fontWeight: 700, color: T.teal, background: "#E6F0F1", borderRadius: 999, padding: "1px 7px" }}>IN LAUNCH</span>}
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.why}</span>
          <span title="priority" style={{ fontFamily: MONO, fontSize: 11, color: T.slate, flexShrink: 0 }}>▲ {r.priority}</span>
          {canManage && onAudit && (
            <button onClick={() => onAudit(r.handle)} className="cc-nav" title="Audit this account via Claude in Chrome"
              style={{ fontFamily: "Poppins,sans-serif", fontSize: 10.5, fontWeight: 600, color: T.teal, background: T.white,
                border: `1px solid ${T.line}`, borderRadius: 7, padding: "3px 9px", cursor: "pointer", flexShrink: 0 }}>Audit</button>
          )}
        </div>
      ))}
    </Card>
  );
}

function BriefsPanel({ briefs }) {
  const [openId, setOpenId] = useState(null);
  if (!briefs || !briefs.length) return null;
  return (
    <Card style={{ padding: 16, marginTop: 18 }}>
      <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 12.5, fontWeight: 700, color: T.ink, marginBottom: 10 }}>Trend brief archive</div>
      {briefs.map((b) => (
        <div key={b.id} style={{ borderTop: `1px solid ${T.line}` }}>
          <div role="button" tabIndex={0} onClick={() => setOpenId(openId === b.id ? null : b.id)}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpenId(openId === b.id ? null : b.id); } }}
            style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 2px", cursor: "pointer" }}>
            <LaneChip lane={b.lane || "Intel"} />
            <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.ink, flex: 1, minWidth: 0 }}>{b.title}</span>
            {b.shipped_at && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted, flexShrink: 0 }}>{relativeTime(b.shipped_at)}</span>}
            <Icon name="chevron_down" size={13} color={T.muted} style={{ transform: openId === b.id ? "rotate(180deg)" : "none" }} />
          </div>
          {openId === b.id && <div style={{ padding: "0 2px 14px" }}><div style={{ background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 12, padding: 14 }}><Preview kind="trend" payload={b.payload} /></div></div>}
        </div>
      ))}
    </Card>
  );
}

/* ── audit trigger (Claude-in-Chrome handoff → review → submit) ── */
const LBL = { fontFamily: "Inter,sans-serif", fontSize: 11.5, fontWeight: 600, color: T.tertiary, display: "block", marginBottom: 5 };
const INP = { width: "100%", fontFamily: "Inter,sans-serif", fontSize: 13, color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 10px", boxSizing: "border-box" };

function RunAuditModal({ empId, initialHandle, onClose, onQueued }) {
  const [handle, setHandle] = useState(initialHandle || "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const target = handle.trim().replace(/^@+/, "");
  async function launch() {
    if (!target) { setErr("Enter the account handle."); return; }
    setErr(null); setBusy(true);
    try {
      const r = await postJSON(`/ai/employees/${empId}/cowork-audit`, { handle: "@" + target });
      // fire the claude:// deep link — the OS hands it to Claude Desktop, which opens the
      // Cowork task pre-filled; Cowork browses IG via Chrome and posts the teardown back.
      const a = document.createElement("a");
      a.href = r.deep_link;
      document.body.appendChild(a);
      a.click();
      a.remove();
      onQueued(r.run_id);
    } catch (e) { setErr(e.detail || e.message || "Could not start the audit."); setBusy(false); }
  }
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 50, background: "rgba(0,46,44,0.4)", display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "40px 16px", overflowY: "auto" }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: "100%", maxWidth: 520, background: T.white, border: `1px solid ${T.line}`, borderRadius: 16 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "16px 20px", borderBottom: `1px solid ${T.line}` }}>
          <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 15.5, fontWeight: 600, color: T.ink }}>Audit an account</span>
          <button onClick={onClose} className="cc-nav" style={{ background: "transparent", border: "none", cursor: "pointer" }}><Icon name="close" size={15} color={T.muted} /></button>
        </div>
        <div style={{ padding: 20 }}>
          <label style={LBL}>Instagram handle</label>
          <input value={handle} onChange={(e) => setHandle(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") launch(); }} placeholder="@competitor" style={INP} autoFocus />
          <div style={{ background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 12, padding: 14, margin: "14px 0", fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.secondary, lineHeight: 1.55 }}>
            Opens a <span style={{ color: T.ink, fontWeight: 600 }}>Claude (Cowork) task</span> that browses
            {" "}<span style={{ fontFamily: MONO, color: T.teal }}>@{target || "handle"}</span> in Chrome, reads the recent posts, and files the teardown back here as a draft for your approval. You don’t paste anything.
          </div>
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted }}>Requires Claude Desktop with the Chrome connector. Keep this tab open — the draft lands here when Cowork finishes.</div>
          {err && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.poppyText, marginTop: 8 }}>{err}</div>}
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, padding: "0 20px 18px" }}>
          <button onClick={onClose} className="cc-nav" style={{ fontFamily: "Poppins,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.slate, background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 14px", cursor: "pointer" }}>Cancel</button>
          <button onClick={launch} disabled={busy} style={{ fontFamily: "Poppins,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.onDark, background: T.evergreen, border: "none", borderRadius: 8, padding: "8px 16px", cursor: busy ? "default" : "pointer", opacity: busy ? 0.6 : 1 }}>{busy ? "Opening Cowork…" : "Open in Cowork"}</button>
        </div>
      </div>
    </div>
  );
}

function RunResponseModal({ empId, name, onClose, onQueued }) {
  const [handle, setHandle] = useState("");
  const [material, setMaterial] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  async function submit() {
    setErr(null); setBusy(true);
    const body = {};
    if (material.trim()) body.material = material.trim();
    if (handle.trim()) body.handle = "@" + handle.trim().replace(/^@+/, "");
    try { const r = await postJSON(`/ai/employees/${empId}/respond`, body); onQueued(r.id); }
    catch (e) { setErr(e.detail || e.message || "Could not start the response."); setBusy(false); }
  }
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 50, background: "rgba(0,46,44,0.4)", display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "40px 16px", overflowY: "auto" }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: "100%", maxWidth: 540, background: T.white, border: `1px solid ${T.line}`, borderRadius: 16 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "16px 20px", borderBottom: `1px solid ${T.line}` }}>
          <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 15.5, fontWeight: 600, color: T.ink }}>Run {name}’s full response</span>
          <button onClick={onClose} className="cc-nav" style={{ background: "transparent", border: "none", cursor: "pointer" }}><Icon name="close" size={15} color={T.muted} /></button>
        </div>
        <div style={{ padding: 20 }}>
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.secondary, lineHeight: 1.55, marginBottom: 14 }}>
            {name} reads the current pace gap, then drafts the whole coordinated response —
            <span style={{ color: T.ink }}> audit · trend brief · strategy · carousel · reel · UTM tags</span> — and lands it all on one run for a single approval. It builds live; watch it fill in.
          </div>
          <div style={{ background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 12, padding: 14 }}>
            <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 10.5, fontWeight: 700, letterSpacing: ".05em", color: T.tertiary, textTransform: "uppercase", marginBottom: 8 }}>Optional · seed the audit step from Claude in Chrome</div>
            <input value={handle} onChange={(e) => setHandle(e.target.value)} placeholder="@competitor (optional)" style={{ ...INP, marginBottom: 8 }} />
            <textarea value={material} onChange={(e) => setMaterial(e.target.value)} rows={4} placeholder="Paste captured posts to audit a specific account this run (else the audit works from the roster)." style={{ ...INP, fontFamily: "monospace", fontSize: 11.5, resize: "vertical" }} />
          </div>
          {err && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.poppyText, marginTop: 10 }}>{err}</div>}
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, padding: "0 20px 18px" }}>
          <button onClick={onClose} className="cc-nav" style={{ fontFamily: "Poppins,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.slate, background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 14px", cursor: "pointer" }}>Cancel</button>
          <button onClick={submit} disabled={busy} style={{ fontFamily: "Poppins,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.onDark, background: T.evergreen, border: "none", borderRadius: 8, padding: "8px 16px", cursor: busy ? "default" : "pointer", opacity: busy ? 0.6 : 1 }}>{busy ? "Starting…" : "Run response"}</button>
        </div>
      </div>
    </div>
  );
}

/* ── container ────────────────────────────────────────────────── */
export default function AIEmployeeDetail({ employee, role, writebackEnvOpen, onBack }) {
  const canManage = role === "owner" || role === "admin";
  const { runs, roster, briefs, detail, runId, setRunId, error, reload } = useAiEmployeeDetail(employee.id);
  const [openId, setOpenId] = useState(null);
  const [pendingIds, setPendingIds] = useState([]);
  const [busy, setBusy] = useState(false);
  const [approveErr, setApproveErr] = useState(null);
  const [auditOpen, setAuditOpen] = useState(false);
  const [auditHandle, setAuditHandle] = useState("");
  const [respOpen, setRespOpen] = useState(false);
  const openAudit = (handle) => { setAuditHandle(handle || ""); setAuditOpen(true); };

  const run = detail && detail.run;
  const artifacts = (detail && detail.artifacts) || [];
  const drafts = artifacts.filter((a) => a.state === "draft");
  const status = run && run.status;
  const wbOn = writebackEnvOpen && employee.writeback_enabled;
  const wbLine = `Draft mode · Writeback ${wbOn ? "enabled" : "disabled"}`;

  async function approveAll() {
    if (!run) return;
    setApproveErr(null); setBusy(true);
    setPendingIds(drafts.map((a) => a.id));
    try { await postJSON(`/ai/runs/${run.id}/approve`); reload(); }
    catch (e) { setApproveErr(e.detail || e.message || "Could not approve."); setPendingIds([]); }
    finally { setBusy(false); }
  }
  async function dismiss(id) {
    setPendingIds((p) => [...p, id]);
    try { await postJSON(`/ai/artifacts/${id}/dismiss`); reload(); }
    catch { setPendingIds((p) => p.filter((x) => x !== id)); }
  }

  const styleBlock = (
    <style>{`
      @keyframes ai-pulse { 0%,100% { box-shadow: 0 0 0 0 ${T.meadow}55; } 50% { box-shadow: 0 0 0 4px ${T.meadow}00; } }
      .ai-pulse { animation: ai-pulse 2s ease-in-out infinite; }
      @keyframes ai-spin { to { transform: rotate(360deg); } }
      .ai-spin { animation: ai-spin .8s linear infinite; }
      @keyframes ai-trig { 0%,100% { opacity: 1; } 50% { opacity: .45; } }
      .ai-trigger { animation: ai-trig 1.4s ease-in-out infinite; }
      @media (prefers-reduced-motion: reduce) { .ai-pulse, .ai-spin, .ai-trigger { animation: none; } }
    `}</style>
  );

  return (
    <div>
      {styleBlock}
      <Header employee={employee} onBack={onBack} writebackEnvOpen={writebackEnvOpen} />

      {canManage && (
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginBottom: 12 }}>
          <button onClick={() => openAudit("")} className="cc-nav" style={{ display: "inline-flex", alignItems: "center", gap: 6,
            fontFamily: "Poppins,sans-serif", fontSize: 12, fontWeight: 600, color: T.slate, background: T.parchment,
            border: `1px solid ${T.line}`, borderRadius: 8, padding: "7px 13px", cursor: "pointer" }}>
            <Icon name="search" size={13} color={T.slate} />Run an audit
          </button>
          <button onClick={() => setRespOpen(true)} className="cc-nav" style={{ display: "inline-flex", alignItems: "center", gap: 6,
            fontFamily: "Poppins,sans-serif", fontSize: 12, fontWeight: 600, color: T.onDark, background: T.evergreen,
            border: `1px solid ${T.evergreen}`, borderRadius: 8, padding: "7px 14px", cursor: "pointer" }}>
            <Icon name="spark" size={13} color={T.onDark} />Run full response
          </button>
        </div>
      )}

      {error && !detail ? (
        <Card style={{ padding: 22, textAlign: "center" }}>
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.poppyText }}>Couldn’t load this employee.</div>
        </Card>
      ) : runs && runs.length === 0 ? (
        <Card style={{ padding: "34px 28px", textAlign: "center", maxWidth: 520, margin: "6px auto" }}>
          <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 16, fontWeight: 600, color: T.ink }}>No runs yet</div>
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.slate, marginTop: 8 }}>
            {employee.next_run_at ? "A scheduled run is queued — check back after it fires." : "This employee has no scheduled skills. Add a schedule in Settings."}
          </div>
        </Card>
      ) : !detail ? (
        <Card className="cc-skel" style={{ height: 260 }} />
      ) : status === "skipped_budget" ? (
        <Card style={{ padding: 20, background: T.parchment }}>
          <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 14, fontWeight: 600, color: T.ink }}>Run skipped — monthly token budget reached</div>
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.slate, marginTop: 6 }}>Scheduled runs resume next month, or raise the budget in Settings.</div>
        </Card>
      ) : status === "failed" ? (
        <Card style={{ padding: 20, borderLeft: `3px solid ${T.poppy}` }}>
          <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 14, fontWeight: 600, color: T.poppyText }}>This run failed</div>
          <div style={{ fontFamily: MONO, fontSize: 12, color: T.secondary, marginTop: 8, background: T.parchment, borderRadius: 8, padding: 10 }}>{run.error || "Unknown error"}</div>
          {run.finished_at && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted, marginTop: 6 }}>{relativeTime(run.finished_at)}</div>}
        </Card>
      ) : (
        <>
          <Pipeline status={status} />
          <TriggerBanner ctx={run.trigger_context} trigger={run.trigger} />
          <Diagnosis reads={run.reads} name={employee.name} />
          {(status === "running" || status === "queued") && artifacts.length === 0 && (
            <Card style={{ padding: 18, display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
              <span className="ai-spin" style={{ width: 16, height: 16, borderRadius: 99, border: `2px solid ${T.line}`, borderTopColor: T.teal, flexShrink: 0 }} />
              <div>
                <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600, color: T.ink }}>
                  {run.trigger_context && run.trigger_context.source === "Instagram · Cowork" ? "Cowork is auditing in Chrome…" : "Working…"}</div>
                <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted, marginTop: 2 }}>
                  {run.trigger_context && run.trigger_context.source === "Instagram · Cowork"
                    ? "The teardown lands here as a draft when it finishes — you can leave this open."
                    : "Drafting — the response fills in as each piece is produced."}</div>
              </div>
            </Card>
          )}
          {run.summary && ["approved", "shipped"].includes(status) && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.meadowInk, background: T.meadowBg, borderRadius: 10, padding: "10px 14px", marginBottom: 14 }}>
              <Icon name="check" size={14} color={T.meadow} />{run.summary}
            </div>
          )}
          {artifacts.length > 0 && (
            <Card style={{ padding: "4px 16px 8px" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 0 8px" }}>
                <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600, color: T.ink }}>{employee.name} built the response</span>
                {status === "awaiting_approval" && (
                  <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10, fontWeight: 700, letterSpacing: ".05em", color: T.daffodilText, background: T.daffodilBg, border: `1px solid #F0E3AC`, borderRadius: 6, padding: "2px 8px" }}>DRAFT MODE</span>
                )}
              </div>
              {artifacts.map((a) => (
                <ArtifactRow key={a.id} a={a} open={openId === a.id} onToggle={() => setOpenId(openId === a.id ? null : a.id)}
                  onDismiss={() => dismiss(a.id)} canManage={canManage} pending={pendingIds.includes(a.id)} />
              ))}
            </Card>
          )}
          {status === "awaiting_approval" && drafts.length > 0 && (
            <ApproveBar drafts={drafts.length} canManage={canManage} wbLine={wbLine} onApproveAll={approveAll} busy={busy} err={approveErr} />
          )}
        </>
      )}

      <HistoryPanel runs={runs} currentId={runId} onOpen={setRunId} />
      <RosterPanel employeeId={employee.id} roster={roster} canManage={canManage} onChanged={reload} onAudit={openAudit} />
      <BriefsPanel briefs={briefs} />

      {auditOpen && (
        <RunAuditModal empId={employee.id} initialHandle={auditHandle}
          onClose={() => setAuditOpen(false)}
          onQueued={(id) => { setAuditOpen(false); reload(); if (id) setRunId(id); }} />
      )}
      {respOpen && (
        <RunResponseModal empId={employee.id} name={employee.name}
          onClose={() => setRespOpen(false)}
          onQueued={(id) => { setRespOpen(false); reload(); if (id) setRunId(id); }} />
      )}
    </div>
  );
}
