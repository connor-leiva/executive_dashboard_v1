/* AI Employees — Settings group (SPEC-ai-employees-tab §7). Owner/admin only.
   7.1 Employees · 7.2 Skills & cadence · 7.3 Triggers · 7.4 Approvals & writeback ·
   7.5 Connections & budget · 7.6 Roster defaults · 7.7 Data & export.
   Only controls whose settings the engine actually honors are exposed (status, skill
   enable/schedule/prompt, pace_check condition, quiet hours, writeback gate, roster
   weights, UTM pattern). Model + budget are env-controlled → shown read-only with the
   live usage meter. theme.js tokens, house Settings idiom (inline styles). */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { T } from "./theme.js";
import { getJSON, postJSON, patchJSON, delJSON } from "./api.js";

const API_BASE = import.meta.env.VITE_API_BASE;
const SWATCHES = [T.teal, T.meadow, T.poppy, T.petalDeep, T.evergreen, T.daffodilText];
const SCHED_PRESETS = [
  { label: "Manual only", cron: "manual" },
  { label: "Daily · 6:00", cron: "0 6 * * *" },
  { label: "Weekdays · 6:00", cron: "0 6 * * 1-5" },
  { label: "Weekly · Mon 7:00", cron: "0 7 * * 1" },
];
const KNOWN_CRON = Object.fromEntries(SCHED_PRESETS.map((p) => [p.cron, p.label]));
const cronLabel = (c) => (!c ? "Uses skill default" : KNOWN_CRON[c] || `Custom · ${c}`);

/* ── small UI ─────────────────────────────────────────────────── */
const label = { fontFamily: "Inter,sans-serif", fontSize: 11.5, fontWeight: 600, color: T.tertiary, display: "block", marginBottom: 5 };
const field = { width: "100%", fontFamily: "Inter,sans-serif", fontSize: 13, color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "7px 10px", boxSizing: "border-box" };
function Card({ title, hint, children, right }) {
  return (
    <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 14, padding: 20, marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 10 }}>
        <div>
          {title && <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 14.5, fontWeight: 600, color: T.ink }}>{title}</div>}
          {hint && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.muted, marginTop: 3 }}>{hint}</div>}
        </div>
        {right}
      </div>
      <div style={{ marginTop: title ? 14 : 0 }}>{children}</div>
    </div>
  );
}
function Btn({ kind = "ghost", children, onClick, disabled, small }) {
  const styles = { primary: { color: T.onDark, background: T.evergreen, border: `1px solid ${T.evergreen}` },
    danger: { color: T.poppyText, background: T.white, border: `1px solid ${T.line}` },
    ghost: { color: T.slate, background: T.parchment, border: `1px solid ${T.line}` } }[kind];
  return <button onClick={onClick} disabled={disabled} className="cc-nav" style={{ fontFamily: "Poppins,sans-serif",
    fontSize: small ? 11.5 : 12.5, fontWeight: 600, borderRadius: 8, padding: small ? "5px 11px" : "7px 13px",
    cursor: disabled ? "default" : "pointer", opacity: disabled ? 0.5 : 1, ...styles }}>{children}</button>;
}
function Toggle({ on, onChange, disabled }) {
  return <button onClick={() => !disabled && onChange(!on)} aria-pressed={on} disabled={disabled} style={{
    width: 38, height: 22, borderRadius: 999, border: "none", cursor: disabled ? "default" : "pointer",
    background: on ? T.meadow : T.line, position: "relative", flexShrink: 0, opacity: disabled ? 0.5 : 1 }}>
    <span style={{ position: "absolute", top: 2, left: on ? 18 : 2, width: 18, height: 18, borderRadius: 999, background: T.white, transition: "left .15s" }} /></button>;
}

/* ── 7.2 skills & cadence ─────────────────────────────────────── */
function SkillRow({ empId, sk, onChanged }) {
  const [open, setOpen] = useState(false);
  const [prompt, setPrompt] = useState(sk.prompt_override || "");
  const [busy, setBusy] = useState(false);
  const effective = sk.schedule_override != null ? sk.schedule_override : sk.default_schedule;
  const isCustom = effective && !KNOWN_CRON[effective];
  const [preset, setPreset] = useState(isCustom ? "custom" : (effective || "manual"));
  const [custom, setCustom] = useState(isCustom ? effective : "");

  async function patch(body) { setBusy(true); try { await patchJSON(`/ai/employees/${empId}/skills/${sk.key}`, body); onChanged(); } finally { setBusy(false); } }
  const saveSchedule = (val) => { setPreset(val); if (val !== "custom") patch({ schedule_override: val }); };

  return (
    <div style={{ borderTop: `1px solid ${T.line}`, padding: "12px 0" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Toggle on={sk.enabled} onChange={(v) => patch({ enabled: v })} disabled={busy} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600, color: T.ink }}>{sk.name}
            {sk.stale_override && <span title="The seed prompt changed since this override was written" style={{ marginLeft: 8, fontFamily: "Inter,sans-serif", fontSize: 10, fontWeight: 700, color: T.daffodilText, background: T.daffodilBg, borderRadius: 5, padding: "1px 6px" }}>override stale</span>}</div>
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted }}>{cronLabel(effective)}</div>
        </div>
        <button onClick={() => setOpen((v) => !v)} className="cc-nav" style={{ fontFamily: "Poppins,sans-serif", fontSize: 11, fontWeight: 600, color: T.slate, background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 7, padding: "4px 10px", cursor: "pointer" }}>{open ? "Close" : "Edit"}</button>
      </div>
      {open && (
        <div style={{ marginTop: 12, paddingLeft: 48 }}>
          <label style={label}>Cadence <span style={{ color: T.muted, fontWeight: 400 }}>· evaluated in the employee’s timezone</span></label>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
            <select value={preset} onChange={(e) => saveSchedule(e.target.value)} style={{ ...field, width: "auto" }}>
              {SCHED_PRESETS.map((p) => <option key={p.cron} value={p.cron}>{p.label}</option>)}
              <option value="custom">Custom cron…</option>
            </select>
            {preset === "custom" && (
              <>
                <input value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="0 6 * * 1-5" style={{ ...field, width: 160, fontFamily: "monospace" }} />
                <Btn small onClick={() => patch({ schedule_override: custom.trim() })} disabled={busy || !custom.trim()}>Set</Btn>
              </>
            )}
          </div>
          <label style={label}>Prompt override <span style={{ color: T.muted, fontWeight: 400 }}>· blank uses the seeded default</span></label>
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={4} placeholder={sk.default_prompt} style={{ ...field, fontFamily: "monospace", fontSize: 11.5, resize: "vertical" }} />
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <Btn small kind="primary" onClick={() => patch({ prompt_override: prompt.trim() || null })} disabled={busy}>Save prompt</Btn>
            {sk.prompt_override && <Btn small onClick={() => { setPrompt(""); patch({ prompt_override: null }); }} disabled={busy}>Restore default</Btn>}
          </div>
        </div>
      )}
    </div>
  );
}

/* ── page ─────────────────────────────────────────────────────── */
export default function AISettings() {
  const live = Boolean(API_BASE);
  const [list, setList] = useState(null);       // /ai/employees
  const [env, setEnv] = useState(null);         // /ai/settings
  const [skills, setSkills] = useState(null);
  const [selId, setSelId] = useState(null);
  const [cfg, setCfg] = useState({});           // editable copy of selected employee.config
  const [creating, setCreating] = useState(false);
  const [msg, setMsg] = useState(null);

  function loadTop() {
    if (!live) { setList({ employees: [], writeback_env_open: false }); return; }
    Promise.all([getJSON("/ai/employees"), getJSON("/ai/settings")])
      .then(([l, e]) => { setList(l); setEnv(e); setSelId((p) => p || (l.employees[0] && l.employees[0].id) || null); })
      .catch(() => setList({ employees: [] }));
  }
  useEffect(loadTop, []);   // eslint-disable-line react-hooks/exhaustive-deps
  const sel = list && list.employees.find((e) => e.id === selId);
  useEffect(() => {
    if (!live || !selId) { setSkills(null); return; }
    setCfg((sel && sel.config) || {});
    getJSON(`/ai/employees/${selId}/skills`).then((d) => setSkills(d.skills)).catch(() => setSkills([]));
  }, [selId, list]);   // eslint-disable-line react-hooks/exhaustive-deps

  async function patchEmp(body) { await patchJSON(`/ai/employees/${selId}`, body); flash("Saved."); loadTop(); }
  async function saveConfig(extra) { const next = { ...cfg, ...(extra || {}) }; setCfg(next); await patchJSON(`/ai/employees/${selId}`, { config: next }); flash("Saved."); }
  function flash(m) { setMsg(m); setTimeout(() => setMsg(null), 1800); }
  const setCfgKey = (k, v) => setCfg((c) => ({ ...c, [k]: v }));

  if (!list) return <div style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.muted }}>Loading…</div>;

  const employees = list.employees;
  const wbEnv = list.writeback_env_open;

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 10.5, fontWeight: 700, letterSpacing: ".12em", textTransform: "uppercase", color: T.muted }}>Settings · AI Employees</div>
        <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 20, fontWeight: 600, color: T.ink, marginTop: 3 }}>AI Employees</div>
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.muted, marginTop: 3 }}>Configure agents, their skills and cadence, triggers, and governance. Every run drafts for approval.</div>
      </div>
      {msg && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.meadowInk, background: T.meadowBg, borderRadius: 8, padding: "6px 12px", marginBottom: 12, display: "inline-block" }}>{msg}</div>}

      {/* 7.1 Employees */}
      <Card title="Employees" hint="Pause stops dispatch immediately; running jobs finish and land as drafts."
        right={<Btn kind="primary" onClick={() => setCreating(true)}>New employee</Btn>}>
        {employees.length === 0 ? <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.muted }}>No employees yet.</div>
          : employees.map((e) => (
            <div key={e.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 0", borderTop: `1px solid ${T.line}` }}>
              <span style={{ width: 26, height: 26, borderRadius: 7, background: e.avatar_color, color: T.onDark, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "Poppins,sans-serif", fontSize: 12, fontWeight: 700, flexShrink: 0 }}>{e.name.charAt(0).toUpperCase()}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontFamily: "Inter,sans-serif", fontSize: 13, fontWeight: 600, color: T.ink }}>{e.name}</div>
                <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted }}>{e.role_title}</div>
              </div>
              <button onClick={() => setSelId(e.id)} className="cc-nav" style={{ fontFamily: "Poppins,sans-serif", fontSize: 11, fontWeight: 600, color: e.id === selId ? T.teal : T.slate, background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 7, padding: "4px 10px", cursor: "pointer" }}>{e.id === selId ? "Configuring" : "Configure"}</button>
              <Btn small onClick={() => patchJSON(`/ai/employees/${e.id}`, { status: e.status === "active" ? "paused" : "active" }).then(loadTop)}>{e.status === "active" ? "Pause" : "Resume"}</Btn>
              <Btn small kind="danger" onClick={() => { if (confirm(`Archive ${e.name}? Runs are kept for the record.`)) delJSON(`/ai/employees/${e.id}`).then(() => { if (selId === e.id) setSelId(null); loadTop(); }); }}>Archive</Btn>
            </div>
          ))}
      </Card>

      {sel && (
        <>
          <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 12, fontWeight: 700, letterSpacing: ".04em", color: T.tertiary, textTransform: "uppercase", margin: "20px 0 10px" }}>Configuring · {sel.name}</div>

          {/* 7.2 Skills & cadence */}
          <Card title="Skills & cadence" hint="Enable a skill, set its schedule, and override its prompt. The seed prompt is never mutated.">
            {!skills ? <div style={{ color: T.muted, fontSize: 12.5 }}>Loading…</div>
              : skills.map((sk) => <SkillRow key={sk.key} empId={sel.id} sk={sk} onChanged={() => getJSON(`/ai/employees/${sel.id}/skills`).then((d) => setSkills(d.skills))} />)}
          </Card>

          {/* 7.3 Triggers */}
          <TriggersCard empId={sel.id} skills={skills} cfg={cfg} setCfgKey={setCfgKey} onSaveConfig={saveConfig} onReloadSkills={() => getJSON(`/ai/employees/${sel.id}/skills`).then((d) => setSkills(d.skills))} />

          {/* 7.4 Approvals & writeback */}
          <Card title="Approvals & writeback" hint="Approvals are limited to owners and admins. Writeback needs BOTH the environment gate and this toggle.">
            <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "6px 0" }}>
              <Toggle on={sel.writeback_enabled} onChange={(v) => patchEmp({ writeback_enabled: v })} disabled={!wbEnv} />
              <div>
                <div style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.ink }}>Allow writeback for {sel.name}</div>
                <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: wbEnv ? T.muted : T.daffodilText }}>
                  {wbEnv ? "Environment gate is open." : "Environment gate is closed — set AI_EMPLOYEES_WRITEBACK_ENABLED to enable. Approvals still work (export only)."}
                </div>
              </div>
            </div>
          </Card>

          {/* 7.6 Roster defaults */}
          <RosterCard cfg={cfg} setCfgKey={setCfgKey} onSave={saveConfig} />

          {/* 7.7 Data & export */}
          <Card title="Data & export" hint="Runs and artifacts are retained for the record. Export everything for this employee as JSON.">
            <Btn onClick={() => exportEmployee(sel)}>Export {sel.name} (JSON)</Btn>
          </Card>
        </>
      )}

      {/* 7.5 Connections & budget (tenant) */}
      <Card title="Connections & budget" hint="Model and budget are set in the environment for v1; the meter is live.">
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
          <Link to="/settings/integrations" style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.teal, textDecoration: "none", background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 8, padding: "7px 12px" }}>Go High Level connection →</Link>
        </div>
        {sel && (
          <div style={{ marginBottom: 14 }}>
            <label style={label}>UTM pattern <span style={{ color: T.muted, fontWeight: 400 }}>· {"{skill}"} and {"{date}"} placeholders</span></label>
            <div style={{ display: "flex", gap: 8 }}>
              <input value={cfg.utm_pattern || ""} onChange={(e) => setCfgKey("utm_pattern", e.target.value)} placeholder="{campaign} / ig-{skill}-{date}" style={{ ...field, fontFamily: "monospace", fontSize: 12 }} />
              <Btn onClick={() => saveConfig()}>Save</Btn>
            </div>
          </div>
        )}
        {env && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div>
              <label style={label}>Model (environment)</label>
              <div style={{ fontFamily: "monospace", fontSize: 12.5, color: T.ink, background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 10px" }}>{env.model}</div>
            </div>
            <div>
              <label style={label}>Monthly token budget</label>
              <UsageMeter used={env.tokens_used} budget={env.token_budget} />
            </div>
          </div>
        )}
      </Card>

      {creating && <CreateModal onClose={() => setCreating(false)} onSaved={() => { setCreating(false); loadTop(); }} />}
    </div>
  );
}

function UsageMeter({ used, budget }) {
  const unlimited = !budget;
  const pct = unlimited ? 0 : Math.min(100, Math.round((used / budget) * 100));
  const over = !unlimited && used >= budget;
  return (
    <div style={{ background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 10px" }}>
      <div style={{ fontFamily: "monospace", fontSize: 12, color: over ? T.poppyText : T.ink }}>
        {used.toLocaleString()} {unlimited ? "tokens (no cap)" : `/ ${budget.toLocaleString()} (${pct}%)`}
      </div>
      {!unlimited && <div style={{ height: 5, background: T.line, borderRadius: 99, marginTop: 6 }}>
        <div style={{ width: `${pct}%`, height: 5, background: over ? T.poppy : T.meadow, borderRadius: 99 }} /></div>}
    </div>
  );
}

/* ── 7.3 triggers (pace_check condition + quiet hours) ────────── */
function TriggersCard({ empId, skills, cfg, setCfgKey, onSaveConfig, onReloadSkills }) {
  const responders = (skills || []).filter((s) => s.key !== "measure");
  const [respKey, setRespKey] = useState("strategy");
  const [threshold, setThreshold] = useState(10);
  const [maxDay, setMaxDay] = useState(1);
  const [on, setOn] = useState(false);
  const q = cfg.quiet_hours || {};
  const [busy, setBusy] = useState(false);

  // Re-derive from the saved config once `skills` loads (it's null on first mount, so the
  // toggle can't read the carrier at useState time — that made it always render OFF).
  useEffect(() => {
    if (!skills) return;
    const carrier = skills.find((s) => (s.config || {}).condition && s.config.condition.type === "pace_check");
    const cond = carrier ? carrier.config.condition : null;
    setOn(Boolean(carrier));
    setRespKey(carrier ? carrier.key : "strategy");
    setThreshold(cond ? cond.threshold_pct_under_curve : 10);
    setMaxDay(cond ? (cond.max_per_day || 1) : 1);
  }, [skills]);

  async function save() {
    setBusy(true);
    try {
      // clear any prior carrier, then set on the chosen responder
      for (const s of (skills || [])) {
        if ((s.config || {}).condition && s.key !== respKey) await patchJSON(`/ai/employees/${empId}/skills/${s.key}`, { config: { ...(s.config || {}), condition: null } });
      }
      const target = (skills || []).find((s) => s.key === respKey) || {};
      const condition = on ? { type: "pace_check", metric_source: "launch_pacing", threshold_pct_under_curve: Number(threshold), response: "self", max_per_day: Number(maxDay) } : null;
      await patchJSON(`/ai/employees/${empId}/skills/${respKey}`, { config: { ...(target.config || {}), condition } });
      await onSaveConfig();   // persists quiet_hours edits in cfg
      onReloadSkills();
    } finally { setBusy(false); }
  }

  return (
    <Card title="Triggers" hint="A pace check reads the launch registration curve; if registrations fall this far under it, it queues the responding skill. The curve is back-loaded, so keep the threshold generous to avoid firing during the expected early lag.">
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
        <Toggle on={on} onChange={setOn} />
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.ink }}>Pace-check trigger</span>
      </div>
      {on && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 12 }}>
          <div>
            <label style={label}>Responding skill</label>
            <select value={respKey} onChange={(e) => setRespKey(e.target.value)} style={field}>
              {responders.map((s) => <option key={s.key} value={s.key}>{s.name}</option>)}
            </select>
          </div>
          <div>
            <label style={label}>Max fires / day</label>
            <input type="number" min={1} max={5} value={maxDay} onChange={(e) => setMaxDay(e.target.value)} style={field} />
          </div>
          <div style={{ gridColumn: "1 / -1" }}>
            <label style={label}>Threshold · {threshold}% under curve</label>
            <input type="range" min={5} max={40} value={threshold} onChange={(e) => setThreshold(e.target.value)} style={{ width: "100%", accentColor: T.teal }} />
          </div>
        </div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 10, alignItems: "end" }}>
        <div><label style={label}>Quiet hours start</label><input type="time" value={q.start || ""} onChange={(e) => setCfgKey("quiet_hours", { ...q, start: e.target.value })} style={field} /></div>
        <div><label style={label}>Quiet hours end</label><input type="time" value={q.end || ""} onChange={(e) => setCfgKey("quiet_hours", { ...q, end: e.target.value })} style={field} /></div>
        <Btn kind="primary" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save triggers"}</Btn>
      </div>
    </Card>
  );
}

/* ── 7.6 roster defaults ──────────────────────────────────────── */
function RosterCard({ cfg, setCfgKey, onSave }) {
  const w = cfg.roster_weights || { overlap: 1, offer: 1, perf: 1, launch_boost: 1.5 };
  const setW = (k, v) => setCfgKey("roster_weights", { ...w, [k]: Number(v) });
  const FIELDS = [["overlap", "Audience overlap"], ["offer", "Offer similarity"], ["perf", "Performance signal"], ["launch_boost", "In-launch boost ×"]];
  return (
    <Card title="Roster scoring" hint="Weights for the computed watch-roster priority. AI-suggested accounts always require confirmation (added as human review), consistent with the no-auto-commit posture.">
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12 }}>
        {FIELDS.map(([k, lbl]) => (
          <div key={k}><label style={label}>{lbl}</label>
            <input type="number" step="0.5" min={0} value={w[k]} onChange={(e) => setW(k, e.target.value)} style={field} /></div>
        ))}
      </div>
      <div style={{ marginTop: 12 }}><Btn kind="primary" onClick={() => onSave()}>Save weights</Btn></div>
    </Card>
  );
}

/* ── create modal ─────────────────────────────────────────────── */
function CreateModal({ onClose, onSaved }) {
  const [name, setName] = useState("");
  const [role, setRole] = useState("Social Media Manager");
  const [color, setColor] = useState(SWATCHES[0]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  async function save() {
    if (!name.trim()) { setErr("A name is required."); return; }
    setBusy(true);
    try { await postJSON("/ai/employees", { name: name.trim(), role_title: role.trim() || "Social Media Manager", avatar_color: color }); onSaved(); }
    catch (e) { setErr(e.detail || e.message || "Could not create."); setBusy(false); }
  }
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 40, background: "rgba(0,46,44,0.34)", display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "60px 16px" }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: "100%", maxWidth: 440, background: T.white, border: `1px solid ${T.line}`, borderRadius: 16, padding: 22 }}>
        <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 16, fontWeight: 600, color: T.ink, marginBottom: 16 }}>New AI Employee</div>
        <div style={{ marginBottom: 12 }}><label style={label}>Name</label><input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name this employee" style={field} autoFocus /></div>
        <div style={{ marginBottom: 12 }}><label style={label}>Role title</label><input value={role} onChange={(e) => setRole(e.target.value)} style={field} /></div>
        <div style={{ marginBottom: 8 }}><label style={label}>Avatar color</label>
          <div style={{ display: "flex", gap: 8 }}>{SWATCHES.map((c) => <button key={c} onClick={() => setColor(c)} style={{ width: 26, height: 26, borderRadius: 8, background: c, cursor: "pointer", border: color === c ? `2px solid ${T.ink}` : `2px solid ${T.line}` }} />)}</div>
        </div>
        {err && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.poppyText, marginTop: 10 }}>{err}</div>}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 18 }}>
          <Btn onClick={onClose}>Cancel</Btn><Btn kind="primary" onClick={save} disabled={busy}>{busy ? "Creating…" : "Create"}</Btn>
        </div>
      </div>
    </div>
  );
}

async function exportEmployee(emp) {
  const data = await getJSON(`/ai/employees/${emp.id}/export`);
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = `ai-employee-${emp.name}.json`; a.click();
  URL.revokeObjectURL(url);
}
