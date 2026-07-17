/* Binder · Overview matrix + entity binder detail (SPEC Part 6 / 8 / 9).
   Rendered as a sub-surface of the Binder view. The matrix shows one status cell per
   (entity, obligation kind); clicking an entity opens its binder (obligations + documents
   + upload). theme.js tokens + Brand.jsx icons. */
import { useState, useEffect, useCallback, useRef } from "react";
import { T } from "./theme.js";
import { Icon } from "./Brand.jsx";
import { getJSON } from "./api.js";
import { useBinderMatrix } from "./useBinderMatrix.js";
import sampleEntityBinder from "./sampleEntityBinder.js";

const API = import.meta.env.VITE_API_BASE;

const OSTATUS = {
  current: { dot: T.meadow, text: T.tertiary, label: "current" },
  due_soon: { dot: T.daffodil, text: T.daffodilText, label: "due soon" },
  overdue: { dot: T.poppy, text: T.poppyText, label: "overdue" },
  in_progress: { dot: T.teal, text: T.teal, label: "in progress" },
  not_applicable: { dot: T.muted, text: T.muted, label: "n/a" },
  none: { dot: T.line, text: T.muted, label: "not configured" },
};

function Card({ children, style }) {
  return <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 14, padding: 20, ...style }}>{children}</div>;
}
function Eyebrow({ children }) {
  return <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 11, fontWeight: 700, letterSpacing: "0.12em", textTransform: "uppercase", color: T.tertiary }}>{children}</span>;
}
function Dot({ s, size = 8 }) {
  return <span style={{ width: size, height: size, borderRadius: 99, background: (OSTATUS[s] || OSTATUS.none).dot, display: "inline-block", flexShrink: 0 }} />;
}

/* ── the matrix grid ─────────────────────────────────────────── */
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
            <span key={k.key} style={{ flex: 1, fontFamily: "Poppins,sans-serif", fontSize: 9.5, fontWeight: 700, letterSpacing: "0.03em", textTransform: "uppercase", color: T.onDarkMute, textAlign: "center", lineHeight: 1.25 }}>{k.label}</span>
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
          No entities yet. Add one under the Entities tab, then obligations appear here as documents are confirmed.
        </Card>
      ) : (
        <ObligationsMatrix kinds={data.kinds} groups={data.groups} onOpen={onOpen} />
      )}

      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        {Object.entries(OSTATUS).map(([k, s]) => (
          <span key={k} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontFamily: "Inter,sans-serif", fontSize: 11, color: T.slate }}>
            <Dot s={k} size={7} />{s.label}
          </span>
        ))}
      </div>
    </div>
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
  const res = await fetch(`${API}/binder/documents/batch`, { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: fd });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function EntityBinder({ row, usingSample, onBack }) {
  const { data, error, reload } = useEntityBinder(row, usingSample);
  const fileRef = useRef(null);
  const [uploading, setUploading] = useState(false);
  const [note, setNote] = useState(null);

  async function onFile(ev) {
    const files = [...(ev.target.files || [])];
    ev.target.value = "";
    if (!files.length) return;
    if (usingSample) { setNote("Sample mode: connect the app to upload documents."); return; }
    setUploading(true); setNote(null);
    try {
      const res = await uploadDocuments(files, row.id);
      reload();
      const parts = [];
      if (res.created) parts.push(`${res.created} uploaded`);
      if (res.deduped) parts.push(`${res.deduped} already on file`);
      setNote(`${parts.join(", ") || "Done"}. Proposals appear under Review within a minute.`);
    } catch (e) { setNote(e.message || "Upload failed."); }
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
  const meta = [e.type && e.type.toUpperCase(), e.jurisdiction, e.ownership,
    e.ein_masked && `EIN ${e.ein_masked}`].filter(Boolean).join(" · ");
  return (
    <div>
      <BackBtn onBack={onBack} />
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 18 }}>
        <span style={{ width: 4, height: 22, borderRadius: 2, background: T.meadow }} />
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 20, fontWeight: 600, color: T.ink }}>{e.name}</span>
        {e.nickname && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.muted }}>{e.nickname}</span>}
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.muted }}>{meta}</span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.3fr) minmax(0,1fr)", gap: 18, alignItems: "start" }}>
        <Card>
          <Eyebrow>Obligations</Eyebrow>
          <div style={{ marginTop: 8 }}>
            {data.obligations.length === 0 && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.muted, padding: "10px 0" }}>No obligations yet. Confirm proposals in Review to track filings here.</div>}
            {data.obligations.map((o, i) => {
              const st = OSTATUS[o.status] || OSTATUS.none;
              return (
                <div key={o.id} style={{ display: "flex", alignItems: "center", gap: 11, padding: "11px 0", borderTop: i ? `1px solid ${T.line}` : "none" }}>
                  <Dot s={o.status} size={9} />
                  <span style={{ flex: 1, fontFamily: "Inter,sans-serif", fontSize: 13, fontWeight: 500, color: T.ink }}>{o.kind_label}</span>
                  {o.status === "in_progress" && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, color: T.teal, fontStyle: "italic" }}>waiting on Books close</span>}
                  <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, fontWeight: o.status === "overdue" ? 700 : 600, color: st.text,
                    background: o.status === "overdue" ? "rgba(250,128,105,0.12)" : o.status === "due_soon" ? T.daffodilBg : "transparent",
                    borderRadius: 5, padding: (o.status === "overdue" || o.status === "due_soon") ? "2px 8px" : 0 }}>
                    {o.status === "current" ? "current" : o.status === "not_applicable" ? "n/a" : o.label}
                  </span>
                </div>
              );
            })}
          </div>
        </Card>

        <Card>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
            <Eyebrow>Documents</Eyebrow>
            <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, color: T.muted }}>evidence behind obligations</span>
          </div>
          {data.documents.length === 0 && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.muted, padding: "8px 0" }}>No documents yet.</div>}
          {data.documents.map((grp, gi) => (
            <div key={gi} style={{ paddingTop: gi ? 12 : 8, borderTop: gi ? `1px solid ${T.line}` : "none", marginTop: gi ? 6 : 0 }}>
              <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 11, fontWeight: 700, color: T.tertiary, marginBottom: 6 }}>{grp.category}</div>
              {grp.items.map((it) => (
                <div key={it.id} style={{ display: "flex", alignItems: "center", gap: 9, padding: "5px 0" }}>
                  <Icon name="link" size={13} color={it.alert ? T.poppyText : T.muted} />
                  <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: it.alert ? T.poppyText : T.ink }}>{it.filename}</span>
                </div>
              ))}
            </div>
          ))}
          <input ref={fileRef} type="file" multiple onChange={onFile} style={{ display: "none" }} />
          <button onClick={() => fileRef.current?.click()} disabled={uploading} className="cc-nav" style={{
            marginTop: 12, display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 7,
            fontFamily: "Poppins,sans-serif", fontSize: 12, fontWeight: 600, color: T.slate, background: T.parchment,
            border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 14px", cursor: uploading ? "default" : "pointer", width: "100%", opacity: uploading ? 0.6 : 1 }}>
            <Icon name="download" size={14} color={T.slate} />{uploading ? "Uploading…" : "Upload documents"}
          </button>
          {note && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted, marginTop: 8 }}>{note}</div>}
        </Card>
      </div>
    </div>
  );
}

function BackBtn({ onBack }) {
  return (
    <button onClick={onBack} className="cc-nav" style={{ display: "inline-flex", alignItems: "center", gap: 5,
      fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.tertiary, background: "transparent",
      border: "none", cursor: "pointer", marginBottom: 12, padding: 0 }}>
      <Icon name="chevron_backward" size={14} color={T.tertiary} />Back to overview
    </button>
  );
}

/* ── surface ─────────────────────────────────────────────────── */
export default function BinderMatrix() {
  const matrix = useBinderMatrix();
  const [openRow, setOpenRow] = useState(null);
  const { data, error, loading, usingSample, reload } = matrix;

  if (openRow) return <EntityBinder row={openRow} usingSample={usingSample} onBack={() => setOpenRow(null)} />;
  if (loading) return <Card style={{ color: T.muted, fontFamily: "Inter,sans-serif", fontSize: 13 }}>Loading the matrix…</Card>;
  if (error && !data) return (
    <Card style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <Icon name="warning" size={16} color={T.poppyText} />
      <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.slate, flex: 1 }}>Could not load the matrix.</span>
      <button onClick={reload} className="cc-nav" style={{ fontFamily: "Poppins,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.slate, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 14px", cursor: "pointer" }}>Retry</button>
    </Card>
  );
  return <Overview matrix={matrix} onOpen={setOpenRow} />;
}
