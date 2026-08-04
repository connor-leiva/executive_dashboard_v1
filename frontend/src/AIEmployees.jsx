/* AI Employees — the rail tab's list surface (SPEC-ai-employees-tab §6, Build Step 5).
   Employee cards + first-run empty state + create flow. The per-employee run surface
   (the six artifact renderers, pipeline rail, approval) is the next build step; this
   view is where a tenant creates employees and sees what's awaiting approval.
   theme.js tokens + Brand.jsx icons, dashboard type system — no forked palette. */
import { useState } from "react";
import { T } from "./theme.js";
import { relativeTime } from "./theme.js";
import { Icon } from "./Brand.jsx";
import { postJSON } from "./api.js";
import AIEmployeeDetail from "./AIEmployeeDetail.jsx";

const AVATAR_SWATCHES = [T.teal, T.meadow, T.poppy, T.petalDeep, T.evergreen, T.daffodilText];

/* ── small shared UI (house idiom) ───────────────────────────── */
function Card({ children, style, ...rest }) {
  return <div {...rest} style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 14, ...style }}>{children}</div>;
}
function Btn({ kind = "ghost", children, onClick, disabled, type = "button" }) {
  const styles = {
    primary: { color: T.onDark, background: T.evergreen, border: `1px solid ${T.evergreen}` },
    ghost: { color: T.slate, background: T.white, border: `1px solid ${T.line}` },
  }[kind];
  return (
    <button type={type} onClick={onClick} disabled={disabled} className="cc-nav" style={{
      fontFamily: "Poppins,sans-serif", fontSize: 12.5, fontWeight: 600, borderRadius: 8,
      padding: "8px 14px", cursor: disabled ? "default" : "pointer", opacity: disabled ? 0.5 : 1, ...styles,
    }}>{children}</button>
  );
}
function Avatar({ name, color, size = 42 }) {
  return (
    <div style={{
      width: size, height: size, borderRadius: 11, flexShrink: 0, background: color || T.teal,
      display: "flex", alignItems: "center", justifyContent: "center",
      fontFamily: "Poppins,sans-serif", fontSize: size * 0.42, fontWeight: 700, color: T.onDark,
    }}>{(name || "?").trim().charAt(0).toUpperCase()}</div>
  );
}
function StatusDot({ status }) {
  const active = status === "active";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontFamily: "Inter,sans-serif", fontSize: 11.5, color: active ? T.tertiary : T.muted }}>
      <span className={active ? "ai-pulse" : ""} style={{ width: 8, height: 8, borderRadius: 99, background: active ? T.meadow : T.muted, flexShrink: 0 }} />
      {active ? "Active" : "Paused"}
    </span>
  );
}
function AwaitingPill({ n }) {
  // DRAFT-state indicator → daffodil family (per §6 mapping). Not an action control.
  return (
    <span style={{
      fontFamily: "Inter,sans-serif", fontSize: 11.5, fontWeight: 700, borderRadius: 6,
      padding: "3px 9px", color: T.daffodilText, background: T.daffodilBg,
    }}>{n} awaiting approval</span>
  );
}
function RunChip({ status }) {
  const map = {
    awaiting_approval: [T.daffodilText, T.daffodilBg, "Awaiting approval"],
    approved: [T.tertiary, T.meadowBg, "Approved"],
    shipped: [T.tertiary, T.meadowBg, "Shipped"],
    running: [T.teal, T.mist, "Running"],
    queued: [T.slate, T.parchment, "Queued"],
    failed: [T.poppyText, T.white, "Failed"],
    skipped_budget: [T.muted, T.parchment, "Skipped (budget)"],
    dismissed: [T.muted, T.parchment, "Dismissed"],
  };
  const [c, bg, label] = map[status] || [T.slate, T.parchment, status];
  return (
    <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, fontWeight: 700, letterSpacing: "0.03em",
      color: c, background: bg, border: `1px solid ${T.line}`, borderRadius: 5, padding: "2px 7px" }}>{label}</span>
  );
}

// A compact label for a FUTURE timestamp (relativeTime is past-only → "just now").
function futureLabel(iso) {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  const mins = Math.round((t - Date.now()) / 60000);
  if (mins <= 1) return "any moment";
  if (mins < 60) return `in ${mins} min`;
  if (mins < 60 * 24) return `in ${Math.round(mins / 60)} hr`;
  return new Date(iso).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
}

/* ── employee card ───────────────────────────────────────────── */
function EmployeeCard({ e, onOpen }) {
  const last = e.last_run;
  const nextLabel = e.next_run_at ? `Next run ${futureLabel(e.next_run_at) || "soon"}` : "No scheduled runs";
  return (
    <Card className="cc-card" onClick={onOpen} role="button" tabIndex={0}
      onKeyDown={(ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); onOpen(); } }}
      style={{ padding: 18, display: "flex", flexDirection: "column", gap: 12, cursor: "pointer" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <Avatar name={e.name} color={e.avatar_color} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 15, fontWeight: 600, color: T.ink }}>{e.name}</div>
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.muted }}>{e.role_title}</div>
        </div>
        <StatusDot status={e.status} />
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        {e.awaiting_approval > 0
          ? <AwaitingPill n={e.awaiting_approval} />
          : <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted }}>Nothing awaiting approval</span>}
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate, display: "inline-flex", alignItems: "center", gap: 5 }}>
          <Icon name="sync" size={12} color={T.muted} />{nextLabel}
        </span>
      </div>

      <div style={{ borderTop: `1px solid ${T.line}`, paddingTop: 10 }}>
        {last ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <RunChip status={last.status} />
            <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.slate, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {last.summary || "No summary"}
            </span>
            {last.created_at && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted, flexShrink: 0, marginLeft: "auto" }}>{relativeTime(last.created_at)}</span>}
          </div>
        ) : (
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.muted, fontStyle: "italic" }}>No runs yet</span>
        )}
      </div>
    </Card>
  );
}

/* ── first-run empty state ───────────────────────────────────── */
function EmptyState({ canManage, onAdd }) {
  return (
    <Card style={{ padding: "40px 32px", textAlign: "center", maxWidth: 560, margin: "8px auto" }}>
      <div style={{ display: "inline-flex", width: 46, height: 46, borderRadius: 12, alignItems: "center",
        justifyContent: "center", background: T.mist, marginBottom: 14 }}>
        <Icon name="spark" size={22} color={T.teal} />
      </div>
      <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 18, fontWeight: 600, color: T.ink }}>Hire Your First AI Employee</div>
      <p style={{ fontFamily: "Inter,sans-serif", fontSize: 13.5, lineHeight: 1.55, color: T.slate, margin: "10px auto 20px", maxWidth: 430 }}>
        An AI employee runs skills on a schedule (or when a metric crosses a line), drafts the
        work — audits, briefs, strategy, creative, attribution — and hands it to you for approval.
        Nothing ships without your sign-off. Start with a Social Media Manager.
      </p>
      {canManage
        ? <Btn kind="primary" onClick={onAdd}>Create Your First Employee</Btn>
        : <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.muted }}>Ask an owner or admin to create one.</div>}
    </Card>
  );
}

/* ── create modal ────────────────────────────────────────────── */
function labelStyle() {
  return { fontFamily: "Inter,sans-serif", fontSize: 11.5, fontWeight: 600, color: T.tertiary, marginBottom: 5, display: "block" };
}
function inputStyle() {
  return { width: "100%", fontFamily: "Inter,sans-serif", fontSize: 13, color: T.ink, background: T.white,
    border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 10px", boxSizing: "border-box" };
}
function CreateModal({ onClose, onSaved }) {
  const [name, setName] = useState("");
  const [role, setRole] = useState("Social Media Manager");
  const [color, setColor] = useState(AVATAR_SWATCHES[0]);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState(null);

  async function save() {
    if (!name.trim()) { setErr("A name is required."); return; }
    setErr(null);
    setSaving(true);
    try {
      await postJSON("/ai/employees", { name: name.trim(), role_title: role.trim() || "Social Media Manager", avatar_color: color });
      onSaved();
    } catch (e) {
      setErr(e.detail || e.message || "Could not create employee.");
      setSaving(false);
    }
  }

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 40, background: "rgba(0,46,44,0.34)",
      display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "60px 16px", overflowY: "auto" }}>
      <div onClick={(ev) => ev.stopPropagation()} style={{ width: "100%", maxWidth: 480, background: T.white,
        border: `1px solid ${T.line}`, borderRadius: 16, boxShadow: "0 20px 50px rgba(0,46,44,.22)" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "18px 22px", borderBottom: `1px solid ${T.line}` }}>
          <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 16, fontWeight: 600, color: T.ink }}>New AI Employee</span>
          <button onClick={onClose} className="cc-nav" style={{ background: "transparent", border: "none", cursor: "pointer", padding: 4 }}>
            <Icon name="close" size={16} color={T.muted} />
          </button>
        </div>
        <div style={{ padding: 22 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 16 }}>
            <Avatar name={name || "?"} color={color} size={48} />
            <div style={{ flex: 1 }}>
              <label style={labelStyle()}>Name</label>
              <input value={name} onChange={(ev) => setName(ev.target.value)} placeholder="Name this employee" style={inputStyle()} autoFocus />
            </div>
          </div>
          <div style={{ marginBottom: 16 }}>
            <label style={labelStyle()}>Role title</label>
            <input value={role} onChange={(ev) => setRole(ev.target.value)} placeholder="Social Media Manager" style={inputStyle()} />
          </div>
          <div style={{ marginBottom: 8 }}>
            <label style={labelStyle()}>Avatar color</label>
            <div style={{ display: "flex", gap: 8 }}>
              {AVATAR_SWATCHES.map((c) => (
                <button key={c} onClick={() => setColor(c)} title={c} style={{
                  width: 26, height: 26, borderRadius: 8, background: c, cursor: "pointer",
                  border: color === c ? `2px solid ${T.ink}` : `2px solid ${T.line}`,
                  outline: color === c ? `2px solid ${T.white}` : "none", outlineOffset: -4,
                }} />
              ))}
            </div>
          </div>
          {err && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.poppyText, marginTop: 12 }}>{err}</div>}
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, padding: "0 22px 20px" }}>
          <Btn onClick={onClose}>Cancel</Btn>
          <Btn kind="primary" onClick={save} disabled={saving}>{saving ? "Creating…" : "Create employee"}</Btn>
        </div>
      </div>
    </div>
  );
}

/* ── page ────────────────────────────────────────────────────── */
export default function AIEmployees({ data, loading, error, reload, role }) {
  const [creating, setCreating] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const canManage = role === "owner" || role === "admin" || Boolean(data && data.can_manage);
  const employees = (data && data.employees) || [];

  // Employee detail (run surface) — selecting a card swaps the list for the detail view.
  const selected = selectedId && employees.find((e) => e.id === selectedId);
  if (selected) {
    return (
      <AIEmployeeDetail employee={selected} role={role}
        writebackEnvOpen={Boolean(data && data.writeback_env_open)}
        onBack={() => { setSelectedId(null); reload && reload(); }} />
    );
  }

  return (
    <div>
      <style>{`
        .ai-emp-grid { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 16px; }
        @media (max-width: 820px) { .ai-emp-grid { grid-template-columns: 1fr; } }
        @keyframes ai-pulse { 0%,100% { box-shadow: 0 0 0 0 ${T.meadow}66; } 50% { box-shadow: 0 0 0 4px ${T.meadow}00; } }
        .ai-pulse { animation: ai-pulse 2s ease-in-out infinite; }
        @media (prefers-reduced-motion: reduce) { .ai-pulse { animation: none; } }
      `}</style>

      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", marginBottom: 18, flexWrap: "wrap", gap: 10 }}>
        <div>
          <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 22, fontWeight: 700, color: T.ink }}>AI Employees</div>
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.slate, marginTop: 3 }}>
            Configured agents that draft work for your approval. Nothing ships without a human sign-off.
          </div>
        </div>
        {canManage && employees.length > 0 && <Btn kind="primary" onClick={() => setCreating(true)}>New employee</Btn>}
      </div>

      {data && data.awaiting_total > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, background: T.daffodilBg, border: `1px solid ${T.line}`,
          borderRadius: 10, padding: "10px 14px", marginBottom: 16 }}>
          <Icon name="notification" size={14} color={T.daffodilText} />
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.daffodilText }}>
            {data.awaiting_total} artifact{data.awaiting_total === 1 ? "" : "s"} awaiting your approval across your employees.
          </span>
        </div>
      )}

      {error ? (
        <Card style={{ padding: 22, textAlign: "center" }}>
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.poppyText }}>Couldn’t load AI Employees.</div>
          <div style={{ marginTop: 10 }}><Btn onClick={reload}>Retry</Btn></div>
        </Card>
      ) : loading && !data ? (
        <div className="ai-emp-grid">
          {[0, 1].map((i) => <Card key={i} className="cc-skel" style={{ height: 150 }} />)}
        </div>
      ) : employees.length === 0 ? (
        <EmptyState canManage={canManage} onAdd={() => setCreating(true)} />
      ) : (
        <div className="ai-emp-grid">
          {employees.map((e) => <EmployeeCard key={e.id} e={e} onOpen={() => setSelectedId(e.id)} />)}
        </div>
      )}

      {creating && <CreateModal onClose={() => setCreating(false)} onSaved={() => { setCreating(false); reload && reload(); }} />}
    </div>
  );
}
