/* Binder — legal-entity compliance module, rendered as a Command Center view.
   Step 2 scope: entity management (the list, the first-run empty state, and the
   add/edit form). The obligations matrix + entity detail arrive with later build
   steps; this surface is where a user configures the entities everything else
   hangs off. theme.js tokens + Brand.jsx icons, no forked palette. */
import { useMemo, useState } from "react";
import { T } from "./theme.js";
import { Icon } from "./Brand.jsx";
import { useBinder } from "./useBinder.js";
import { useBinderReview } from "./useBinderReview.js";
import BinderReview from "./BinderReview.jsx";
import BinderBrowse from "./BinderMatrix.jsx";
import BinderRules from "./BinderRules.jsx";
import { postJSON, patchJSON } from "./api.js";

const ENTITY_TYPES = [
  ["llc", "LLC"], ["s_corp", "S-Corp"], ["c_corp", "C-Corp"],
  ["partnership", "Partnership"], ["trust", "Trust"],
];
const TYPE_LABEL = Object.fromEntries(ENTITY_TYPES);
const STATES = ["AL","AK","AZ","AR","CA","CO","CT","DE","DC","FL","GA","HI","ID","IL","IN",
  "IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY",
  "NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY"];

/* ── small shared UI ─────────────────────────────────────────── */
function Card({ children, style }) {
  return <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 14, padding: 20, ...style }}>{children}</div>;
}
function Eyebrow({ children }) {
  return <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 11, fontWeight: 700, letterSpacing: "0.12em", textTransform: "uppercase", color: T.tertiary }}>{children}</span>;
}
function StatusPill({ ready }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 6, fontFamily: "Inter,sans-serif",
      fontSize: 11, fontWeight: 700, borderRadius: 6, padding: "3px 9px",
      color: ready ? T.tertiary : T.daffodilText, background: ready ? T.meadowBg : T.daffodilBg,
    }}>
      <span style={{ width: 7, height: 7, borderRadius: 99, background: ready ? T.meadow : T.daffodil }} />
      {ready ? "Tracking" : "Add Details"}
    </span>
  );
}
function Btn({ kind = "ghost", children, onClick, disabled, type = "button" }) {
  const styles = {
    primary: { color: T.onDark, background: T.evergreen, border: `1px solid ${T.evergreen}` },
    danger: { color: T.poppyText, background: T.white, border: `1px solid ${T.line}` },
    ghost: { color: T.slate, background: T.white, border: `1px solid ${T.line}` },
  }[kind];
  return (
    <button type={type} onClick={onClick} disabled={disabled} className="cc-nav" style={{
      fontFamily: "Poppins,sans-serif", fontSize: 12.5, fontWeight: 600, borderRadius: 8,
      padding: "8px 14px", cursor: disabled ? "default" : "pointer", opacity: disabled ? 0.5 : 1, ...styles,
    }}>{children}</button>
  );
}

function tabStyle(active) {
  return {
    fontFamily: "Poppins,sans-serif", fontSize: 13.5, fontWeight: 600,
    color: active ? T.ink : T.muted, background: "transparent", border: "none",
    borderBottom: active ? `2.5px solid ${T.teal}` : "2.5px solid transparent",
    padding: "9px 15px 11px", cursor: "pointer", marginBottom: -1,
  };
}

/* ── entity row ──────────────────────────────────────────────── */
function EntityRow({ e, first, onEdit, onDeactivate }) {
  const meta = [e.entity_type && TYPE_LABEL[e.entity_type], e.jurisdiction].filter(Boolean).join(" · ");
  const sub = [e.ein_masked && `EIN ${e.ein_masked}`, e.ownership, e.business_name && `→ ${e.business_name}`]
    .filter(Boolean).join(" · ");
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 12, padding: "13px 18px",
      borderTop: first ? "none" : `1px solid ${T.line}`, background: T.white,
    }}>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13.5, fontWeight: 600, color: T.ink }}>{e.legal_name}</span>
          {e.nickname && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.muted }}>{e.nickname}</span>}
        </div>
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.slate, marginTop: 2 }}>
          {meta || <span style={{ color: T.muted, fontStyle: "italic" }}>type and state not set</span>}
          {sub && <span style={{ color: T.muted }}>{"  ·  "}{sub}</span>}
        </div>
        {!e.tracking_ready && e.nudge && (
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.daffodilText, marginTop: 4 }}>{e.nudge}</div>
        )}
      </div>
      <StatusPill ready={e.tracking_ready} />
      <button onClick={() => onEdit(e)} className="cc-nav" title="Edit entity" style={{
        fontFamily: "Poppins,sans-serif", fontSize: 12, fontWeight: 600, color: T.slate,
        background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 8, padding: "6px 12px", cursor: "pointer" }}>
        Edit
      </button>
      <button onClick={() => onDeactivate(e)} className="cc-nav" title="Deactivate entity" style={{
        display: "inline-flex", alignItems: "center", background: "transparent", border: "none",
        cursor: "pointer", padding: 6, borderRadius: 8 }}>
        <Icon name="close" size={14} color={T.muted} />
      </button>
    </div>
  );
}

/* ── empty first-run state ───────────────────────────────────── */
function EmptyState({ onAdd }) {
  return (
    <Card style={{ padding: "40px 32px", textAlign: "center", maxWidth: 560, margin: "8px auto" }}>
      <div style={{ display: "inline-flex", width: 46, height: 46, borderRadius: 12, alignItems: "center",
        justifyContent: "center", background: T.meadowBg, marginBottom: 14 }}>
        <Icon name="puzzle" size={22} color={T.meadow} />
      </div>
      <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 18, fontWeight: 600, color: T.ink }}>Set Up Your Binder</div>
      <p style={{ fontFamily: "Inter,sans-serif", fontSize: 13.5, lineHeight: 1.55, color: T.slate, margin: "10px auto 20px", maxWidth: 420 }}>
        The Binder tracks every legal entity you own and the filings each one owes (annual reports,
        BOI, taxes, insurance). Start by adding your first entity. A name is enough to begin;
        add the state, type, and formation date when you have them and filing tracking turns on.
      </p>
      <Btn kind="primary" onClick={onAdd}>Add Your First Entity</Btn>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted, marginTop: 14 }}>
        Dropping in a formation document to read the details automatically is coming next.
      </div>
    </Card>
  );
}

/* ── add / edit form (modal) ─────────────────────────────────── */
const BLANK = { legal_name: "", nickname: "", description: "", entity_type: "", jurisdiction: "",
  formation_date: "", ein: "", entity_group: "operating", ownership: "", business_id: "" };

function labelStyle() {
  return { fontFamily: "Inter,sans-serif", fontSize: 11.5, fontWeight: 600, color: T.tertiary, marginBottom: 5, display: "block" };
}
function inputStyle() {
  return { width: "100%", fontFamily: "Inter,sans-serif", fontSize: 13, color: T.ink, background: T.white,
    border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 10px", boxSizing: "border-box" };
}
function Field({ label, hint, children }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <label style={labelStyle()}>{label}{hint && <span style={{ color: T.muted, fontWeight: 400 }}>  {hint}</span>}</label>
      {children}
    </div>
  );
}

function EntityForm({ entity, businesses, usingSample, onClose, onSaved }) {
  const editing = Boolean(entity);
  const [f, setF] = useState(() => editing ? {
    ...BLANK,
    legal_name: entity.legal_name || "", nickname: entity.nickname || "",
    description: entity.description || "", entity_type: entity.entity_type || "",
    jurisdiction: entity.jurisdiction || "", formation_date: entity.formation_date || "",
    ein: "", entity_group: entity.entity_group || "operating", ownership: entity.ownership || "",
    business_id: entity.business_id || "",
  } : { ...BLANK });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState(null);
  const set = (k) => (ev) => setF((s) => ({ ...s, [k]: ev.target.value }));

  const ready = Boolean(f.entity_type && f.jurisdiction && f.formation_date);

  async function save() {
    if (!f.legal_name.trim()) { setErr("Legal name is required."); return; }
    setErr(null);
    setSaving(true);
    // Build payload: omit blank optional text; EIN only when typed (write-only).
    const p = { legal_name: f.legal_name.trim(), entity_group: f.entity_group,
      nickname: f.nickname.trim() || null, description: f.description.trim() || null,
      entity_type: f.entity_type || null, jurisdiction: f.jurisdiction || null,
      formation_date: f.formation_date || null, ownership: f.ownership.trim() || null,
      business_id: f.business_id || null };
    if (f.ein.trim()) p.ein = f.ein.trim();
    try {
      if (usingSample) { onSaved(); return; }   // preview mode: layout only, no persistence
      if (editing) await patchJSON(`/binder/entities/${entity.id}`, p);
      else await postJSON("/binder/entities", p);
      onSaved();
    } catch (e) {
      setErr(e.detail || e.message || "Could not save.");
      setSaving(false);
    }
  }

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 40, background: "rgba(0,46,44,0.34)",
      display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "48px 16px", overflowY: "auto" }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: "100%", maxWidth: 560, background: T.white,
        border: `1px solid ${T.line}`, borderRadius: 16, boxShadow: "0 20px 50px rgba(0,46,44,.22)" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "18px 22px", borderBottom: `1px solid ${T.line}` }}>
          <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 16, fontWeight: 600, color: T.ink }}>
            {editing ? "Edit Entity" : "Add Entity"}
          </span>
          <button onClick={onClose} className="cc-nav" style={{ background: "transparent", border: "none", cursor: "pointer", padding: 4 }}>
            <Icon name="close" size={16} color={T.muted} />
          </button>
        </div>

        <div style={{ padding: 22 }}>
          <Field label="Legal Name" hint="(required)">
            <input value={f.legal_name} onChange={set("legal_name")} placeholder="Utah Life Real Estate Group, LLC" style={inputStyle()} />
          </Field>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <Field label="Nickname"><input value={f.nickname} onChange={set("nickname")} placeholder="The Team" style={inputStyle()} /></Field>
            <Field label="Ownership"><input value={f.ownership} onChange={set("ownership")} placeholder="100%" style={inputStyle()} /></Field>
          </div>
          <Field label="Description"><input value={f.description} onChange={set("description")} placeholder="What this entity is" style={inputStyle()} /></Field>

          {/* Required-to-track group, visually set apart */}
          <div style={{ background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 12, padding: "14px 14px 4px", margin: "6px 0 12px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
              <Eyebrow>Required to Track Filings</Eyebrow>
              <span style={{ width: 7, height: 7, borderRadius: 99, background: ready ? T.meadow : T.daffodil }} />
              <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: ready ? T.tertiary : T.daffodilText }}>
                {ready ? "ready to track" : "dormant until all three are set"}
              </span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
              <Field label="State">
                <select value={f.jurisdiction} onChange={set("jurisdiction")} style={inputStyle()}>
                  <option value="">-</option>
                  {STATES.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </Field>
              <Field label="Entity Type">
                <select value={f.entity_type} onChange={set("entity_type")} style={inputStyle()}>
                  <option value="">-</option>
                  {ENTITY_TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
              </Field>
              <Field label="Formation Date">
                <input type="date" value={f.formation_date} onChange={set("formation_date")} style={inputStyle()} />
              </Field>
            </div>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <Field label="EIN" hint={editing && entity.has_ein ? "(leave blank to keep)" : "(optional)"}>
              <input value={f.ein} onChange={set("ein")} placeholder={editing && entity.ein_masked ? entity.ein_masked : "87-4123456"} style={inputStyle()} />
            </Field>
            <Field label="Group">
              <select value={f.entity_group} onChange={set("entity_group")} style={inputStyle()}>
                <option value="operating">Operating</option>
                <option value="holding">Holding</option>
              </select>
            </Field>
          </div>
          <Field label="Linked Business" hint="(optional, ties the tax lifecycle to Books)">
            <select value={f.business_id} onChange={set("business_id")} style={inputStyle()}>
              <option value="">None</option>
              {(businesses || []).map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          </Field>

          {usingSample && (
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted, marginTop: 4 }}>
              Sample mode: changes are not saved.
            </div>
          )}
          {err && (
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.poppyText, background: "rgba(250,128,105,0.12)",
              border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 12px", marginTop: 8 }}>{err}</div>
          )}
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, padding: "16px 22px", borderTop: `1px solid ${T.line}` }}>
          <Btn kind="ghost" onClick={onClose}>Cancel</Btn>
          <Btn kind="primary" onClick={save} disabled={saving || !f.legal_name.trim()}>
            {saving ? "Saving..." : editing ? "Save Changes" : "Add Entity"}
          </Btn>
        </div>
      </div>
    </div>
  );
}

/* ── the view ────────────────────────────────────────────────── */
export default function Binder({ role }) {
  const { data, error, loading, usingSample, reload } = useBinder();
  const review = useBinderReview();
  const [surface, setSurface] = useState("all");   // all | operating | holding | manage | review | rules
  const [form, setForm] = useState(null);   // null | {} (create) | { entity } (edit)
  const toReview = review.data?.stats?.awaiting || 0;
  const isAdmin = !role || role === "owner" || role === "admin";   // Rules is owner/admin-only
  const isBrowse = surface === "all" || surface === "operating" || surface === "holding";

  const entities = data?.entities || [];
  const operating = useMemo(() => entities.filter((e) => e.entity_group === "operating"), [entities]);
  const holding = useMemo(() => entities.filter((e) => e.entity_group === "holding"), [entities]);
  const counts = data?.counts || { total: 0, operating: 0, holding: 0, dormant: 0 };

  async function deactivate(e) {
    if (!window.confirm(`Deactivate "${e.legal_name}"? Its documents and history are kept; it is hidden from the matrix.`)) return;
    if (usingSample) return;
    try { await postJSON(`/binder/entities/${e.id}/deactivate`); reload(); } catch { /* surfaced on next load */ }
  }

  const Group = ({ label, rows }) => rows.length === 0 ? null : (
    <div style={{ marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <Eyebrow>{label}</Eyebrow>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted }}>· {rows.length}</span>
      </div>
      <Card style={{ padding: 0, overflow: "hidden" }}>
        {rows.map((e, i) => (
          <EntityRow key={e.id} e={e} first={i === 0} onEdit={(x) => setForm({ entity: x })} onDeactivate={deactivate} />
        ))}
      </Card>
    </div>
  );

  return (
    <div>
      {/* header */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap", marginBottom: 16 }}>
        <span style={{ width: 5, height: 28, borderRadius: 3, background: T.teal }} />
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 22, fontWeight: 600, color: T.ink }}>Binder</span>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.muted }}>
          Legal entities and the documents behind their filings
        </span>
        <span style={{ flex: 1 }} />
        {surface === "manage" && entities.length > 0 && <Btn kind="primary" onClick={() => setForm({})}>Add Entity</Btn>}
      </div>

      {/* surface sub-nav: browse tabs (All / Operating / Holding) left; Manage / Review / Rules right */}
      <div style={{ display: "flex", alignItems: "flex-end", gap: 4, borderBottom: `1px solid ${T.line}`, marginBottom: 18, flexWrap: "wrap" }}>
        {[["all", "All Entities", counts.total], ["operating", "Operating", counts.operating],
          ["holding", "Holding", counts.holding]].map(([k, l, n]) => (
          <button key={k} onClick={() => setSurface(k)} className="cc-nav" style={tabStyle(surface === k)}>
            {l} <span style={{ color: T.muted, fontWeight: 500 }}>· {n}</span>
          </button>
        ))}
        <span style={{ flex: 1 }} />
        {[["manage", "Manage"], ["review", "Review"], ...(isAdmin ? [["rules", "Rules"]] : [])].map(([k, l]) => (
          <button key={k} onClick={() => setSurface(k)} className="cc-nav" style={{ ...tabStyle(surface === k), display: "inline-flex", alignItems: "center", gap: 7 }}>
            {l}
            {k === "review" && toReview > 0 && (
              <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, fontWeight: 700, color: T.poppyText,
                background: "rgba(250,128,105,0.14)", borderRadius: 99, padding: "1px 8px" }}>{toReview}</span>
            )}
          </button>
        ))}
      </div>

      {isBrowse && <BinderBrowse scope={surface} />}

      {surface === "rules" && isAdmin && <BinderRules />}

      {surface === "review" && <BinderReview review={review} entities={entities} />}

      {surface === "manage" && (<>
      {loading && (
        <Card style={{ color: T.muted, fontFamily: "Inter,sans-serif", fontSize: 13 }}>Loading entities...</Card>
      )}
      {error && !data && (
        <Card style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Icon name="warning" size={16} color={T.poppyText} />
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.slate, flex: 1 }}>Could not load the Binder.</span>
          <Btn kind="ghost" onClick={reload}>Retry</Btn>
        </Card>
      )}

      {data && entities.length === 0 && <EmptyState onAdd={() => setForm({})} />}

      {data && entities.length > 0 && (
        <>
          {counts.dormant > 0 && (
            <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
              <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, fontWeight: 700, color: T.daffodilText,
                background: T.daffodilBg, borderRadius: 6, padding: "3px 10px" }}>{counts.dormant} Need Details</span>
            </div>
          )}
          <Group label="Operating" rows={operating} />
          <Group label="Holding" rows={holding} />
        </>
      )}
      </>)}

      {form && (
        <EntityForm
          entity={form.entity}
          businesses={data?.businesses}
          usingSample={usingSample}
          onClose={() => setForm(null)}
          onSaved={() => { setForm(null); reload(); }}
        />
      )}
    </div>
  );
}
