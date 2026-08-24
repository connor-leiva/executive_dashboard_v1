/* Binder · browse surface (SPEC Part 6 / 8 / 9), rendered to the delivered mockup.
   scope="all"      -> the obligations matrix (one status cell per entity/kind) + attention tile.
   scope="operating|holding" -> a clean, clickable entity list (rollup status + chevron).
   Opening an entity (from either) swaps to its binder in place: the FULL obligation set (every
   kind, editable, "not configured" where nothing is tracked yet) + the full document tree with
   inline preview. theme.js tokens + Brand.jsx icons (only the shipped icon set). */
import { useState, useEffect, useCallback, useRef } from "react";
import { T } from "./theme.js";
import { Icon } from "./Brand.jsx";
import { getJSON, getBlob, postJSON, delJSON, tenantHeaders } from "./api.js";
import { useBinderMatrix } from "./useBinderMatrix.js";
import sampleEntityBinder from "./sampleEntityBinder.js";

const API = import.meta.env.VITE_API_BASE;

const OSTATUS = {
  current: { dot: T.meadow, text: T.tertiary, label: "Current" },
  due_soon: { dot: T.daffodil, text: T.daffodilText, label: "Due Soon" },
  overdue: { dot: T.poppy, text: T.poppyText, label: "Overdue" },
  in_progress: { dot: T.teal, text: T.teal, label: "In Progress" },
  not_applicable: { dot: T.muted, text: T.muted, label: "N/A" },
  none: { dot: T.line, text: T.muted, label: "Not Configured" },
};
// Plain-language definitions shown on hover over each obligation header (detail + matrix column).
const KIND_DEFS = {
  annual_report: "A yearly filing with the state to keep the entity in good standing (its name, address, and registered agent on record).",
  registered_agent: "The person or company designated to receive legal and state notices on the entity's behalf.",
  insurance: "Liability, E&O, and other coverage the entity is expected to keep active.",
  boi: "Beneficial Ownership Information report to FinCEN identifying the people who own or control the entity.",
  federal_tax: "The entity's federal income tax return (for example an 1120-S or 1065).",
  state_tax: "The entity's state income or franchise tax return.",
  estimated_payments: "Quarterly estimated tax payments to the IRS and state.",
  lease: "Real estate or equipment leases the entity holds.",
};
const CADENCES = [["annual", "Annual"], ["quarterly", "Quarterly"], ["biennial", "Biennial"],
  ["one_time", "One time"], ["none", "None"]];
const TYPE_LABEL = { llc: "LLC", s_corp: "S-Corp", c_corp: "C-Corp", partnership: "Partnership", trust: "Trust" };
const IMG_EXT = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"];

function Card({ children, style }) {
  return <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 14, padding: 20, ...style }}>{children}</div>;
}
function Eyebrow({ children }) {
  return <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 11, fontWeight: 700, letterSpacing: "0.12em", textTransform: "uppercase", color: T.tertiary }}>{children}</span>;
}
function Dot({ s, size = 8 }) {
  return <span style={{ width: size, height: size, borderRadius: 99, background: (OSTATUS[s] || OSTATUS.none).dot, display: "inline-block", flexShrink: 0 }} />;
}
// A card eyebrow with a hover-for-definition info icon (replaces the old descriptive sub-label).
function CardHeader({ title, info }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
      <Eyebrow>{title}</Eyebrow>
      {info && <span title={info} style={{ display: "inline-flex", cursor: "help" }}><Icon name="info" size={12} color={T.muted} /></span>}
    </div>
  );
}
const docIconBtn = { display: "inline-flex", alignItems: "center", justifyContent: "center",
  width: 24, height: 24, background: "transparent", border: `1px solid ${T.line}`, borderRadius: 6,
  cursor: "pointer", flexShrink: 0, padding: 0 };
function Legend() {
  return (
    <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
      {Object.entries(OSTATUS).map(([k, s]) => (
        <span key={k} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontFamily: "Inter,sans-serif", fontSize: 11, color: T.slate }}>
          <Dot s={k} size={7} />{s.label}
        </span>
      ))}
    </div>
  );
}

/* ── the matrix grid (scope=all) ─────────────────────────────── */
function Cell({ code, label }) {
  const st = OSTATUS[code] || OSTATUS.none;
  return (
    <span style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", gap: 5, minWidth: 0 }}>
      <Dot s={code} size={7} />
      <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, fontWeight: code === "overdue" ? 700 : 500,
        color: code === "none" || code === "not_applicable" ? T.muted : st.text, whiteSpace: "nowrap" }}>{label}</span>
    </span>
  );
}

function ObligationsMatrix({ kinds, groups, onOpen }) {
  const GroupHeader = ({ label, count }) => (
    <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "9px 16px", background: T.meadowBg, borderTop: `1px solid ${T.line}` }}>
      <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 10.5, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: T.tertiary }}>{label}</span>
      <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, fontWeight: 600, color: T.meadow }}>· {count}</span>
    </div>
  );
  const Row = ({ e, first }) => (
    <button onClick={() => onOpen(e)} className="cc-nav" style={{
      width: "100%", display: "flex", alignItems: "center", gap: 8, padding: "11px 16px",
      background: T.white, border: "none", borderTop: first ? "none" : `1px solid ${T.line}`, cursor: "pointer", textAlign: "left" }}>
      <span style={{ width: 196, flexShrink: 0, fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.ink,
        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{e.name}</span>
      {kinds.map((k) => { const cell = e.cells[k.key] || { status: "none", label: "—" }; return <Cell key={k.key} code={cell.status} label={cell.label} />; })}
    </button>
  );
  return (
    <div style={{ overflowX: "auto", border: `1px solid ${T.line}`, borderRadius: 14, background: T.white }}>
      <div style={{ minWidth: 780 }}>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 8, padding: "12px 16px", background: T.evergreen, borderRadius: "13px 13px 0 0" }}>
          <span style={{ width: 196, flexShrink: 0, fontFamily: "Poppins,sans-serif", fontSize: 11, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: T.onDark }}>Entity</span>
          {kinds.map((k) => (
            <span key={k.key} title={KIND_DEFS[k.key]} style={{ flex: 1, fontFamily: "Poppins,sans-serif", fontSize: 9.5, fontWeight: 700, letterSpacing: "0.03em", textTransform: "uppercase", color: T.onDarkMute, textAlign: "center", lineHeight: 1.25, cursor: KIND_DEFS[k.key] ? "help" : "default" }}>{k.label}</span>
          ))}
        </div>
        {groups.map((g) => g.entities.length > 0 && (
          <div key={g.group}>
            <GroupHeader label={g.group} count={g.entities.length} />
            {g.entities.map((e, i) => <Row key={e.id} e={e} first={i === 0} />)}
          </div>
        ))}
      </div>
    </div>
  );
}

function Overview({ matrix, onOpen }) {
  const { data } = matrix;
  const flags = data.flags || { overdue: 0, due_soon: 0, attention: [] };
  const totalEntities = data.groups.reduce((n, g) => n + g.entities.length, 0);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      {flags.attention.length > 0 && (
        <Card style={{ padding: "16px 20px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <Icon name="warning" size={15} color={T.poppyText} />
            <Eyebrow>Needs attention</Eyebrow>
            <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted }}>· {flags.attention.length} of {totalEntities} entities</span>
          </div>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            {flags.attention.map((a) => (
              <button key={a.id} onClick={() => onOpen({ id: a.id, name: a.name })} className="cc-nav" style={{
                display: "inline-flex", alignItems: "center", gap: 8, padding: "7px 12px", background: T.white,
                border: `1px solid ${T.line}`, borderRadius: 10, borderLeft: `3px solid ${a.worst === "overdue" ? T.poppy : T.daffodil}`, cursor: "pointer" }}>
                <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.ink }}>{a.name}</span>
                <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, fontWeight: 700, color: a.worst === "overdue" ? T.poppyText : T.daffodilText }}>{a.open} to handle</span>
              </button>
            ))}
          </div>
        </Card>
      )}

      {totalEntities === 0 ? (
        <Card style={{ color: T.muted, fontFamily: "Inter,sans-serif", fontSize: 13, textAlign: "center", padding: "34px" }}>
          No entities yet. Add one under the Manage tab, then obligations appear here as documents are confirmed.
        </Card>
      ) : (
        <ObligationsMatrix kinds={data.kinds} groups={data.groups} onOpen={onOpen} />
      )}
      <Legend />
    </div>
  );
}

/* ── entity list (scope=operating|holding) ───────────────────── */
function EntityListView({ rows, scope, onOpen }) {
  if (rows.length === 0) return (
    <Card style={{ color: T.muted, fontFamily: "Inter,sans-serif", fontSize: 13, textAlign: "center", padding: "30px" }}>
      No {scope} entities yet. Add one under the Manage tab.
    </Card>
  );
  return (
    <Card style={{ padding: 0, overflow: "hidden" }}>
      {rows.map((e, i) => {
        const w = e.worst || "none";
        const open = e.open || 0;
        const sub = [e.ein_masked && `EIN ${e.ein_masked}`, e.ownership].filter(Boolean).join(" · ");
        return (
          <button key={e.id} onClick={() => onOpen(e)} className="cc-nav" style={{
            width: "100%", display: "flex", alignItems: "center", gap: 12, padding: "13px 18px",
            background: i % 2 ? T.parchment : T.white, border: "none",
            borderTop: i ? `1px solid ${T.line}` : "none", cursor: "pointer", textAlign: "left" }}>
            <Dot s={w} size={10} />
            <span style={{ width: 220, flexShrink: 0, fontFamily: "Inter,sans-serif", fontSize: 13.5, fontWeight: 600, color: T.ink,
              whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{e.name}</span>
            <span style={{ width: 160, flexShrink: 0, fontFamily: "Inter,sans-serif", fontSize: 12, color: T.slate,
              whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{e.business_name || e.nickname || ""}</span>
            <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted }}>{sub}</span>
            <span style={{ flex: 1 }} />
            {open > 0 ? (
              <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, fontWeight: 700, color: w === "overdue" ? T.poppyText : T.daffodilText,
                background: w === "overdue" ? "rgba(250,128,105,0.14)" : T.daffodilBg, borderRadius: 6, padding: "3px 10px" }}>
                {open} to Handle
              </span>
            ) : (
              <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, fontWeight: 600, color: T.tertiary }}>All Current</span>
            )}
            <Icon name="chevron_backward" size={15} color={T.muted} style={{ transform: "scaleX(-1)", marginLeft: 4 }} />
          </button>
        );
      })}
    </Card>
  );
}

/* ── entity binder detail ────────────────────────────────────── */
function useEntityBinder(row, usingSample) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const load = useCallback(() => {
    if (!row) return;
    if (usingSample || !API) { setData(sampleEntityBinder(row)); return; }
    setError(null);
    getJSON(`/binder/entity/${row.id}`).then(setData).catch(setError);
  }, [row, usingSample]);
  useEffect(() => { setData(null); load(); }, [load]);
  return { data, error, reload: load };
}

async function uploadDocuments(files, entityId) {
  const token = localStorage.getItem("cc_token");
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  if (entityId) fd.append("entity_id", entityId);
  const res = await fetch(`${API}/binder/documents/batch`,
    { method: "POST", headers: tenantHeaders({ Authorization: `Bearer ${token}` }), body: fd });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/* Inline editor for one obligation. Save routes through the manual upsert endpoint, which
   handles both first-time configure and edits of an existing row (records the user either way). */
function labelStyle() {
  return { fontFamily: "Inter,sans-serif", fontSize: 10.5, fontWeight: 600, color: T.tertiary, marginBottom: 4, display: "block" };
}
function inputStyle() {
  return { width: "100%", fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.ink, background: T.white,
    border: `1px solid ${T.line}`, borderRadius: 7, padding: "6px 8px", boxSizing: "border-box" };
}

function ObligationEditor({ o, entityId, usingSample, onClose, onSaved }) {
  const [f, setF] = useState({
    applicable: o.applicable == null ? true : o.applicable,
    due_date: o.due_date || "", cadence: o.cadence || "annual",
    lead_days: o.lead_days == null ? 45 : o.lead_days, notes: o.notes || "",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const set = (k) => (ev) => setF((s) => ({ ...s, [k]: ev.target.value }));

  async function save() {
    if (usingSample) { setErr("Sample mode: connect the app to configure obligations."); return; }
    setBusy(true); setErr(null);
    try {
      await postJSON(`/binder/entities/${entityId}/obligations`, {
        kind: o.kind, applicable: f.applicable, due_date: f.due_date || null,
        cadence: f.cadence, lead_days: Number(f.lead_days) || 0, notes: f.notes.trim() || null,
      });
      onSaved();
    } catch (e) { setErr(e.detail || e.message || "Could not save."); setBusy(false); }
  }
  async function complete() {
    if (usingSample || !o.id) { setErr("Sample mode: connect the app to mark complete."); return; }
    setBusy(true); setErr(null);
    try { await postJSON(`/binder/obligations/${o.id}/complete`); onSaved(); }
    catch (e) { setErr(e.detail || e.message || "Could not complete."); setBusy(false); }
  }

  return (
    <div style={{ background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 10, padding: 12, marginTop: 4 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
        <label style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer", fontFamily: "Inter,sans-serif", fontSize: 12, color: T.slate }}>
          <input type="checkbox" checked={f.applicable} onChange={(ev) => setF((s) => ({ ...s, applicable: ev.target.checked }))} />
          Applies to This Entity
        </label>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, color: T.muted }}>
          {f.applicable ? "" : "unchecking marks this N/A"}
        </span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10, opacity: f.applicable ? 1 : 0.5 }}>
        <div><label style={labelStyle()}>Due Date</label><input type="date" value={f.due_date} onChange={set("due_date")} style={inputStyle()} disabled={!f.applicable} /></div>
        <div><label style={labelStyle()}>Cadence</label>
          <select value={f.cadence} onChange={set("cadence")} style={inputStyle()} disabled={!f.applicable}>
            {CADENCES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </div>
        <div><label style={labelStyle()}>Reminder Lead (Days)</label><input type="number" min="0" value={f.lead_days} onChange={set("lead_days")} style={inputStyle()} disabled={!f.applicable} /></div>
      </div>
      <div style={{ marginTop: 10 }}><label style={labelStyle()}>Notes</label><input value={f.notes} onChange={set("notes")} placeholder="Optional Context" style={inputStyle()} /></div>
      {err && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.poppyText, marginTop: 8 }}>{err}</div>}
      <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 8, marginTop: 10 }}>
        {o.configured && o.id && (
          <button onClick={complete} disabled={busy} className="cc-nav" style={btnStyle(T.slate, T.white)}>Mark Complete</button>
        )}
        <span style={{ flex: 1 }} />
        <button onClick={onClose} disabled={busy} className="cc-nav" style={btnStyle(T.slate, T.white)}>Cancel</button>
        <button onClick={save} disabled={busy} className="cc-nav" style={btnStyle(T.onDark, T.evergreen, T.evergreen)}>{busy ? "Saving…" : "Save"}</button>
      </div>
    </div>
  );
}
function btnStyle(color, bg, border = T.line) {
  return { fontFamily: "Poppins,sans-serif", fontSize: 12, fontWeight: 600, color, background: bg,
    border: `1px solid ${border}`, borderRadius: 8, padding: "7px 13px", cursor: "pointer" };
}

function ObligationRow({ o, first, entityId, usingSample, onSaved, onPreview }) {
  const [editing, setEditing] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const st = OSTATUS[o.status] || OSTATUS.none;
  const showLabel = o.status === "current" ? "Current" : o.status === "not_applicable" ? "N/A" : o.status === "none" ? "Not Configured" : o.label;
  const def = KIND_DEFS[o.kind];
  return (
    <div style={{ padding: "11px 0", borderTop: first ? "none" : `1px solid ${T.line}` }}>
      <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
        <button onClick={() => setExpanded((v) => !v)} title={expanded ? "Hide details" : "Show details"} className="cc-nav" style={{
          background: "transparent", border: "none", cursor: "pointer", padding: 2, display: "inline-flex" }}>
          <Icon name="chevron_down" size={13} color={T.muted} style={{ transform: expanded ? "none" : "rotate(-90deg)" }} />
        </button>
        <Dot s={o.status} size={9} />
        <span title={def} style={{ flex: 1, display: "inline-flex", alignItems: "center", gap: 5,
          fontFamily: "Inter,sans-serif", fontSize: 13, fontWeight: 500, color: o.status === "none" ? T.muted : T.ink, cursor: def ? "help" : "default" }}>
          {o.kind_label}
          {def && <Icon name="info" size={12} color={T.muted} />}
        </span>
        {o.status === "in_progress" && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, color: T.teal, fontStyle: "italic" }}>Waiting on Books Close</span>}
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, fontWeight: o.status === "overdue" ? 700 : 600, color: st.text,
          background: o.status === "overdue" ? "rgba(250,128,105,0.12)" : o.status === "due_soon" ? T.daffodilBg : "transparent",
          borderRadius: 5, padding: (o.status === "overdue" || o.status === "due_soon") ? "2px 8px" : 0 }}>
          {showLabel}
        </span>
        <button onClick={() => setEditing((v) => !v)} className="cc-nav" style={{
          fontFamily: "Poppins,sans-serif", fontSize: 11.5, fontWeight: 600, color: o.configured ? T.slate : T.tertiary,
          background: o.configured ? T.white : T.meadowBg, border: `1px solid ${T.line}`, borderRadius: 7, padding: "4px 10px", cursor: "pointer" }}>
          {editing ? "Close" : o.configured ? "Edit" : "Configure"}
        </button>
      </div>
      {expanded && <ObligationDetail o={o} usingSample={usingSample} onPreview={onPreview} />}
      {editing && (
        <ObligationEditor o={o} entityId={entityId} usingSample={usingSample}
          onClose={() => setEditing(false)} onSaved={() => { setEditing(false); onSaved(); }} />
      )}
    </div>
  );
}

/* Document preview modal — fetches the blob with auth, shows PDF/image inline. */
function DocPreview({ doc, usingSample, onClose, onReplace, onDelete }) {
  const [url, setUrl] = useState(null);
  const [state, setState] = useState("loading");   // loading | ready | sample | error
  useEffect(() => {
    if (usingSample || !doc.can_preview) { setState("sample"); return; }
    let dead = false, made = null;
    getBlob(`/binder/documents/${doc.id}/raw`)
      .then((b) => { if (dead) return; made = URL.createObjectURL(b); setUrl(made); setState("ready"); })
      .catch(() => { if (!dead) setState("error"); });
    return () => { dead = true; if (made) URL.revokeObjectURL(made); };
  }, [doc, usingSample]);
  const name = doc.filename || "document";
  const ext = (name.split(".").pop() || "").toLowerCase();
  const isPdf = ext === "pdf", isImg = IMG_EXT.includes(ext);
  const msg = (t) => <div style={{ margin: "auto", fontFamily: "Inter,sans-serif", fontSize: 13, color: T.muted }}>{t}</div>;
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 50, background: "rgba(0,46,44,0.42)",
      display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: "100%", maxWidth: 940, height: "84vh", background: T.white,
        border: `1px solid ${T.line}`, borderRadius: 14, boxShadow: "0 24px 60px rgba(0,46,44,.28)", display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 16px", borderBottom: `1px solid ${T.line}` }}>
          <Icon name="open" size={15} color={T.slate} />
          <span style={{ flex: 1, fontFamily: "Inter,sans-serif", fontSize: 13, fontWeight: 600, color: T.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{name}</span>
          {url && <a href={url} download={name} className="cc-nav" style={{ ...btnStyle(T.slate, T.white), textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 6 }}><Icon name="download" size={13} color={T.slate} />Download</a>}
          {onReplace && <button onClick={onReplace} className="cc-nav" style={{ ...btnStyle(T.slate, T.white), display: "inline-flex", alignItems: "center", gap: 6 }}><Icon name="sync" size={13} color={T.slate} />Replace</button>}
          {onDelete && <button onClick={onDelete} className="cc-nav" style={btnStyle(T.poppyText, T.white)}>Delete</button>}
          <button onClick={onClose} className="cc-nav" style={{ background: "transparent", border: "none", cursor: "pointer", padding: 4 }}><Icon name="close" size={16} color={T.muted} /></button>
        </div>
        <div style={{ flex: 1, display: "flex", background: T.parchment, overflow: "auto" }}>
          {state === "loading" && msg("Loading preview…")}
          {state === "sample" && msg("Preview is available when the app is connected.")}
          {state === "error" && msg("This document could not be loaded (the file may not be stored).")}
          {state === "ready" && isPdf && <iframe src={url} title={name} style={{ width: "100%", height: "100%", border: 0 }} />}
          {state === "ready" && isImg && <img src={url} alt={name} style={{ margin: "auto", maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }} />}
          {state === "ready" && !isPdf && !isImg && (
            <div style={{ margin: "auto", textAlign: "center" }}>
              <div style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.slate, marginBottom: 10 }}>This file type can't be previewed inline.</div>
              <a href={url} download={name} className="cc-nav" style={{ ...btnStyle(T.onDark, T.evergreen, T.evergreen), textDecoration: "none" }}>Download {name}</a>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* One document line: preview + a year/expired badge + replace/delete. */
function DocRow({ it, onPreview, onReplace, onDelete }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
      <button onClick={() => onPreview(it)} title="Preview" className="cc-nav" style={{
        flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 9, padding: "5px 6px", margin: "0 -6px",
        background: "transparent", border: "none", borderRadius: 7, cursor: "pointer", textAlign: "left" }}>
        <Icon name="open" size={13} color={it.expired ? T.poppyText : T.muted} />
        <span style={{ flex: 1, minWidth: 0, fontFamily: "Inter,sans-serif", fontSize: 12.5, color: it.expired ? T.poppyText : T.ink,
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{it.filename}</span>
        {it.expired
          ? <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10, fontWeight: 700, color: T.poppyText, background: "rgba(250,128,105,0.12)", borderRadius: 5, padding: "1px 6px", flexShrink: 0 }}>Expired</span>
          : (it.year ? <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, color: T.muted, flexShrink: 0 }}>{it.year}</span> : null)}
      </button>
      <button onClick={() => onReplace(it.id)} title="Replace" className="cc-nav" style={docIconBtn}><Icon name="sync" size={12} color={T.muted} /></button>
      <button onClick={() => onDelete(it.id)} title="Delete" className="cc-nav" style={docIconBtn}><Icon name="close" size={12} color={T.muted} /></button>
    </div>
  );
}

/* One category: active documents, then a collapsed-by-default Historical disclosure (expired or
   superseded docs, newest first). */
function DocGroup({ grp, first, onPreview, onReplace, onDelete }) {
  const [showHist, setShowHist] = useState(false);
  const cur = grp.current || grp.items || [];
  const hist = grp.historical || [];
  const rowProps = { onPreview, onReplace, onDelete };
  return (
    <div style={{ paddingTop: first ? 8 : 12, borderTop: first ? "none" : `1px solid ${T.line}`, marginTop: first ? 0 : 6 }}>
      <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 11, fontWeight: 700, color: T.tertiary, marginBottom: 6 }}>{grp.category}</div>
      {cur.length === 0 && (
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted, fontStyle: "italic", padding: "2px 0 4px" }}>
          {hist.length === 0 ? "None on File Yet" : "No active documents"}
        </div>
      )}
      {cur.map((it) => <DocRow key={it.id} it={it} {...rowProps} />)}
      {hist.length > 0 && (
        <div style={{ marginTop: 4 }}>
          <button onClick={() => setShowHist((v) => !v)} className="cc-nav" style={{
            display: "inline-flex", alignItems: "center", gap: 5, fontFamily: "Inter,sans-serif", fontSize: 11, fontWeight: 600,
            color: T.tertiary, background: "transparent", border: "none", cursor: "pointer", padding: "3px 0" }}>
            <Icon name="chevron_down" size={12} color={T.tertiary} style={{ transform: showHist ? "none" : "rotate(-90deg)" }} />
            Historical ({hist.length})
          </button>
          {showHist && <div style={{ opacity: 0.9 }}>{hist.map((it) => <DocRow key={it.id} it={it} {...rowProps} />)}</div>}
        </div>
      )}
    </div>
  );
}

/* The obligation "why" panel: computed reason + the documents behind it + an on-demand AI note. */
function ObligationDetail({ o, usingSample, onPreview }) {
  const [ai, setAi] = useState(o.ai_summary || null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  async function explain() {
    if (usingSample) { setErr("Sample mode: connect the app for AI explanations."); return; }
    setBusy(true); setErr(null);
    try { const r = await postJSON(`/binder/obligations/${o.id}/explain`); setAi(r.ai_summary); }
    catch (e) { setErr(e.detail || e.message || "Could not generate an explanation."); }
    finally { setBusy(false); }
  }
  const rel = o.related_documents || [];
  return (
    <div style={{ background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 10, padding: 12, marginTop: 4 }}>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.slate, lineHeight: 1.5 }}>{o.status_reason}</div>
      {rel.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 10, fontWeight: 700, letterSpacing: "0.08em", textTransform: "uppercase", color: T.tertiary, marginBottom: 4 }}>Related Documents</div>
          {rel.map((d) => (
            <button key={d.id} onClick={() => onPreview(d)} className="cc-nav" style={{
              width: "100%", display: "flex", alignItems: "center", gap: 8, padding: "4px 6px", margin: "0 -6px",
              background: "transparent", border: "none", borderRadius: 6, cursor: "pointer", textAlign: "left" }}>
              <Icon name="open" size={12} color={d.expired ? T.poppyText : T.muted} />
              <span style={{ flex: 1, minWidth: 0, fontFamily: "Inter,sans-serif", fontSize: 12, color: d.expired ? T.poppyText : T.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{d.filename}</span>
              {d.expired ? <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10, fontWeight: 700, color: T.poppyText, flexShrink: 0 }}>Expired</span> : (d.year ? <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, color: T.muted, flexShrink: 0 }}>{d.year}</span> : null)}
            </button>
          ))}
        </div>
      )}
      {o.id && (
        <div style={{ marginTop: 10, borderTop: `1px solid ${T.line}`, paddingTop: 10 }}>
          {ai ? (
            <div style={{ display: "flex", gap: 8 }}>
              <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 9.5, fontWeight: 700, color: T.teal, background: T.meadowBg, borderRadius: 5, padding: "2px 6px", height: "fit-content" }}>AI</span>
              <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.slate, lineHeight: 1.5 }}>{ai}</span>
            </div>
          ) : (
            <button onClick={explain} disabled={busy} className="cc-nav" style={{ ...btnStyle(T.slate, T.white), display: "inline-flex", alignItems: "center", gap: 6 }}>
              <Icon name="spark" size={13} color={T.slate} />{busy ? "Thinking…" : "Explain with AI"}
            </button>
          )}
          {err && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.poppyText, marginTop: 6 }}>{err}</div>}
        </div>
      )}
    </div>
  );
}

function EntityBinder({ row, usingSample, onBack, onChanged }) {
  const { data, error, reload } = useEntityBinder(row, usingSample);
  const fileRef = useRef(null);
  const replaceRef = useRef(null);
  const replaceId = useRef(null);
  const [uploading, setUploading] = useState(false);
  const [note, setNote] = useState(null);
  const [preview, setPreview] = useState(null);

  const refresh = () => { reload(); onChanged && onChanged(); };

  async function onFile(ev) {
    const files = [...(ev.target.files || [])];
    ev.target.value = "";
    if (!files.length) return;
    if (usingSample) { setNote("Sample mode: connect the app to upload documents."); return; }
    setUploading(true); setNote(null);
    try {
      const res = await uploadDocuments(files, row.id);
      refresh();
      const parts = [];
      if (res.created) parts.push(`${res.created} uploaded`);
      if (res.deduped) parts.push(`${res.deduped} already on file`);
      setNote(`${parts.join(", ") || "Done"}. Proposals appear under Review within a minute.`);
    } catch (e) { setNote(e.message || "Upload failed."); }
    finally { setUploading(false); }
  }

  function askReplace(docId) {
    if (usingSample) { setNote("Sample mode: connect the app to replace documents."); return; }
    replaceId.current = docId;
    replaceRef.current?.click();
  }
  async function onReplaceFile(ev) {
    const files = [...(ev.target.files || [])];
    ev.target.value = "";
    const oldId = replaceId.current; replaceId.current = null;
    if (!files.length || !oldId) return;
    setUploading(true); setNote(null);
    try {
      await uploadDocuments([files[0]], row.id);   // the replacement file
      await delJSON(`/binder/documents/${oldId}`); // then drop the old one
      refresh();
      setNote("Document replaced.");
    } catch (err) { setNote(err.message || "Replace failed."); }
    finally { setUploading(false); }
  }
  async function deleteDoc(docId) {
    if (usingSample) { setNote("Sample mode: connect the app to delete documents."); return; }
    if (!window.confirm("Delete this document? This cannot be undone.")) return;
    setUploading(true); setNote(null);
    try { await delJSON(`/binder/documents/${docId}`); refresh(); setNote("Document deleted."); }
    catch (err) { setNote(err.message || "Delete failed."); }
    finally { setUploading(false); }
  }

  if (error && !data) return (
    <div>
      <BackBtn onBack={onBack} />
      <Card style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <Icon name="warning" size={16} color={T.poppyText} />
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.slate, flex: 1 }}>Could not load this entity.</span>
      </Card>
    </div>
  );
  if (!data) return (<div><BackBtn onBack={onBack} /><Card style={{ color: T.muted, fontFamily: "Inter,sans-serif", fontSize: 13 }}>Loading…</Card></div>);

  const e = data.entity;
  const meta = [e.type && (TYPE_LABEL[e.type] || e.type.toUpperCase()), e.jurisdiction, e.ownership,
    e.ein_masked && `EIN ${e.ein_masked}`].filter(Boolean).join(" · ");
  return (
    <div>
      <BackBtn onBack={onBack} />
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 18 }}>
        <span style={{ width: 4, height: 22, borderRadius: 2, background: T.meadow }} />
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 20, fontWeight: 600, color: T.ink }}>{e.name}</span>
        {e.nickname && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.muted }}>{e.nickname}</span>}
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.muted }}>{meta}</span>
        {e.business_name && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.muted }}>· {e.business_name}</span>}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.3fr) minmax(0,1fr)", gap: 18, alignItems: "start" }}>
        <Card>
          <CardHeader title="Obligations" info="Every filing this entity owes." />
          <div style={{ marginTop: 4 }}>
            {data.obligations.map((o, i) => (
              <ObligationRow key={o.kind} o={o} first={i === 0} entityId={e.id} usingSample={usingSample}
                onSaved={refresh} onPreview={setPreview} />
            ))}
          </div>
        </Card>

        <Card>
          <CardHeader title="Documents" info="Evidence behind obligations." />
          {data.documents.map((grp, gi) => (
            <DocGroup key={grp.category_key || gi} grp={grp} first={gi === 0}
              onPreview={setPreview} onReplace={askReplace} onDelete={deleteDoc} />
          ))}
          <input ref={fileRef} type="file" multiple onChange={onFile} style={{ display: "none" }} />
          <input ref={replaceRef} type="file" onChange={onReplaceFile} style={{ display: "none" }} />
          <button onClick={() => fileRef.current?.click()} disabled={uploading} className="cc-nav" style={{
            marginTop: 12, display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 7,
            fontFamily: "Poppins,sans-serif", fontSize: 12, fontWeight: 600, color: T.slate, background: T.parchment,
            border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 14px", cursor: uploading ? "default" : "pointer", width: "100%", opacity: uploading ? 0.6 : 1 }}>
            <Icon name="download" size={14} color={T.slate} />{uploading ? "Uploading…" : "Upload Documents"}
          </button>
          {note && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted, marginTop: 8 }}>{note}</div>}
        </Card>
      </div>

      {preview && <DocPreview doc={preview} usingSample={usingSample} onClose={() => setPreview(null)}
        onReplace={() => { const id = preview.id; setPreview(null); askReplace(id); }}
        onDelete={() => { const id = preview.id; setPreview(null); deleteDoc(id); }} />}
    </div>
  );
}

function BackBtn({ onBack }) {
  return (
    <button onClick={onBack} className="cc-nav" style={{ display: "inline-flex", alignItems: "center", gap: 5,
      fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.tertiary, background: "transparent",
      border: "none", cursor: "pointer", marginBottom: 12, padding: 0 }}>
      <Icon name="chevron_backward" size={14} color={T.tertiary} />Back
    </button>
  );
}

/* ── browse surface: matrix (all) or entity list (operating|holding) ─────────── */
export default function BinderBrowse({ scope = "all" }) {
  const matrix = useBinderMatrix();
  const { data, error, loading, usingSample, reload } = matrix;
  const [openRow, setOpenRow] = useState(null);
  useEffect(() => { setOpenRow(null); }, [scope]);   // switching browse tabs closes the detail

  if (openRow) return <EntityBinder row={openRow} usingSample={usingSample} onBack={() => setOpenRow(null)} onChanged={reload} />;
  if (loading) return <Card style={{ color: T.muted, fontFamily: "Inter,sans-serif", fontSize: 13 }}>Loading…</Card>;
  if (error && !data) return (
    <Card style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <Icon name="warning" size={16} color={T.poppyText} />
      <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.slate, flex: 1 }}>Could not load the Binder.</span>
      <button onClick={reload} className="cc-nav" style={btnStyle(T.slate, T.white)}>Retry</button>
    </Card>
  );

  if (scope === "all") return <Overview matrix={matrix} onOpen={setOpenRow} />;
  const group = (data.groups || []).find((g) => g.group === scope);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <EntityListView rows={group ? group.entities : []} scope={scope} onOpen={setOpenRow} />
      <Legend />
    </div>
  );
}
