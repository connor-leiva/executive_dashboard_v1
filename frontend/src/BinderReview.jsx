/* Binder · Review queue — the ingest → propose → confirm loop (SPEC Part 5.1 / 9).
   Rendered as a sub-surface of the Binder view (no own rail). Claude proposes; a human
   confirms or dismisses EVERY item before it becomes a tracked obligation. Confidence only
   styles/sorts; it never auto-commits. theme.js tokens + Brand.jsx icons. */
import { useState } from "react";
import { T } from "./theme.js";
import { Icon } from "./Brand.jsx";
import { postJSON } from "./api.js";

const VIA_LABEL = { upload: "Uploaded", email: "Forwarded", folder: "Folder scan" };

function confColor(c) {
  if (c >= 0.9) return { c: T.meadowInk, bg: T.meadowBg, label: "high" };
  if (c >= 0.75) return { c: T.daffodilText, bg: T.daffodilBg, label: "medium" };
  return { c: T.poppyText, bg: "rgba(250,128,105,0.14)", label: "low" };
}

function Card({ children, style }) {
  return <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 14, padding: 20, ...style }}>{children}</div>;
}
function Eyebrow({ children }) {
  return <span style={{ fontFamily: "var(--font-display)", fontSize: 11, fontWeight: 700, letterSpacing: "0.12em", textTransform: "uppercase", color: T.tertiary }}>{children}</span>;
}
function MethodTag({ method }) {
  const rule = method === "rule";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontFamily: "var(--font-text)",
      fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.04em",
      color: rule ? T.meadowInk : T.teal, background: rule ? T.meadowBg : "rgba(34,113,117,0.10)",
      borderRadius: 5, padding: "2px 8px" }}>
      {rule && <Icon name="spark" size={11} color={T.meadowInk} />}
      {rule ? "rule-derived" : "read from doc"}
    </span>
  );
}
const selStyle = { fontFamily: "var(--font-text)", fontSize: 12.5, color: T.ink, background: T.white,
  border: `1px solid ${T.line}`, borderRadius: 8, padding: "7px 9px" };

function ProposalRow({ p, open, onToggle, entities, onConfirm, onDismiss, busy }) {
  const [pick, setPick] = useState(p.entity_id || "");
  const cf = confColor(p.confidence || 0);
  const dateColor = p.flavor === "gap" || (p.date || "").toLowerCase() === "overdue" ? T.poppyText : T.ink;
  const options = (p.candidates && p.candidates.length ? p.candidates.map((c) => ({ id: c.entity_id, name: c.name }))
    : (entities || []).map((e) => ({ id: e.id, name: e.legal_name })));
  const needPick = p.ambiguous && !pick;

  return (
    <div style={{ borderTop: `1px solid ${T.line}` }}>
      <button onClick={onToggle} className="cc-nav" style={{ width: "100%", display: "flex", alignItems: "center",
        gap: 13, padding: "13px 6px", background: "transparent", border: "none", cursor: "pointer", textAlign: "left" }}>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontFamily: "var(--font-text)", fontSize: 13, fontWeight: 600, color: T.ink }}>{p.kind}</span>
            <MethodTag method={p.method} />
            {p.ambiguous && <span style={{ fontFamily: "var(--font-text)", fontSize: 10, fontWeight: 700, color: T.poppyText }}>· entity unclear</span>}
            {p.flavor === "gap" && <span style={{ fontFamily: "var(--font-text)", fontSize: 10, fontWeight: 700, color: T.poppyText }}>· possible gap</span>}
            {p.flavor === "renewal" && <span style={{ fontFamily: "var(--font-text)", fontSize: 10, fontWeight: 700, color: T.teal }}>· renewal</span>}
          </span>
          <span style={{ display: "block", fontFamily: "var(--font-text)", fontSize: 11.5, color: T.muted, marginTop: 3,
            whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            {p.entity || "unmatched"} · from {p.document} · {VIA_LABEL[p.via] || p.via}
          </span>
        </span>
        <span style={{ textAlign: "right", flexShrink: 0 }}>
          <span style={{ fontFamily: "var(--font-display)", fontSize: 13, fontWeight: 700, color: dateColor }}>{p.date || "human-set"}</span>
          <span style={{ display: "block", fontFamily: "var(--font-text)", fontSize: 10.5, fontWeight: 700, color: cf.c, marginTop: 2 }}>{cf.label} confidence</span>
        </span>
        <span style={{ color: T.muted, fontSize: 12, transform: open ? "rotate(180deg)" : "none", transition: "transform .15s" }}>▾</span>
      </button>

      {open && (
        <div style={{ background: T.parchment, borderRadius: 10, padding: "15px 16px", margin: "0 0 13px" }}>
          <div style={{ display: "flex", gap: 9, marginBottom: 13 }}>
            <Icon name="spark" size={15} color={T.teal} style={{ marginTop: 1 }} />
            <span style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.slate, lineHeight: 1.55 }}>
              <span style={{ fontWeight: 700, color: T.ink }}>How this was derived: </span>{p.basis}
            </span>
          </div>

          <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 9, overflow: "hidden", marginBottom: 13 }}>
            {(p.fields || []).map(([k, v], i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 13px", borderTop: i ? `1px solid ${T.line}` : "none" }}>
                <span style={{ width: 118, flexShrink: 0, fontFamily: "var(--font-text)", fontSize: 11.5, color: T.muted }}>{k}</span>
                <span style={{ flex: 1, fontFamily: "var(--font-text)", fontSize: 12.5, fontWeight: 600, color: T.ink }}>{v}</span>
              </div>
            ))}
          </div>

          {p.ambiguous && (
            <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 13 }}>
              <Icon name="warning" size={14} color={T.poppyText} />
              <span style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.slate }}>Pick the entity this belongs to:</span>
              <select value={pick} onChange={(e) => setPick(e.target.value)} style={selStyle}>
                <option value="">Choose entity…</option>
                {options.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
              </select>
            </div>
          )}

          <div style={{ display: "flex", gap: 9, flexWrap: "wrap", alignItems: "center" }}>
            <button onClick={() => onConfirm(p, pick || null)} disabled={busy || needPick} className="cc-nav" style={{
              display: "inline-flex", alignItems: "center", gap: 7, fontFamily: "var(--font-display)", fontSize: 12.5, fontWeight: 600,
              color: T.white, background: T.meadow, border: "none", borderRadius: 8, padding: "9px 16px",
              cursor: (busy || needPick) ? "default" : "pointer", opacity: (busy || needPick) ? 0.5 : 1 }}>
              <Icon name="check" size={14} color={T.white} />Confirm &amp; track
            </button>
            <button onClick={() => onDismiss(p)} disabled={busy} className="cc-nav" style={{
              fontFamily: "var(--font-display)", fontSize: 12.5, fontWeight: 600, color: T.poppyText,
              background: "transparent", border: "none", borderRadius: 8, padding: "9px 10px", cursor: busy ? "default" : "pointer" }}>Dismiss</button>
            <span style={{ flex: 1 }} />
            <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontFamily: "var(--font-text)", fontSize: 10.5, color: T.muted }}>
              <Icon name="link" size={12} color={T.muted} />the document files as evidence
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

export default function BinderReview({ review, entities }) {
  const { data, error, loading, usingSample, reload } = review;
  const [openId, setOpenId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [localResolved, setLocalResolved] = useState({});   // sample-mode only

  const stats = data?.stats || { awaiting: 0, confirmed_this_pass: 0, entity_unclear: 0, gaps: 0 };
  const proposals = (data?.proposals || []).filter((p) => !localResolved[p.id]);
  const filed = data?.filed_no_obligation || [];

  async function act(fn, p, kind) {
    if (usingSample) { setLocalResolved((s) => ({ ...s, [p.id]: kind })); return; }
    setBusy(true);
    try { await fn(); reload(); } catch { /* surfaced on next load */ } finally { setBusy(false); }
  }
  const confirm = (p, entityId) =>
    act(() => postJSON(`/binder/review/${p.id}/confirm`, entityId ? { entity_id: entityId } : {}), p, "confirmed");
  const dismiss = (p) => act(() => postJSON(`/binder/review/${p.id}/dismiss`), p, "dismissed");

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap", marginBottom: 16 }}>
        <span style={{ width: 5, height: 28, borderRadius: 3, background: T.teal }} />
        <span style={{ fontFamily: "var(--font-display)", fontSize: 22, fontWeight: 600, color: T.ink }}>
          Binder <span style={{ color: T.muted, fontWeight: 500 }}>/ Review</span></span>
        <span style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.muted }}>confirm what Claude pulled from your documents</span>
      </div>

      {loading && <Card style={{ color: T.muted, fontFamily: "var(--font-text)", fontSize: 13 }}>Loading the review queue…</Card>}
      {error && !data && (
        <Card style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Icon name="warning" size={16} color={T.poppyText} />
          <span style={{ fontFamily: "var(--font-text)", fontSize: 13, color: T.slate, flex: 1 }}>Could not load the review queue.</span>
          <button onClick={reload} className="cc-nav" style={{ fontFamily: "var(--font-display)", fontSize: 12.5, fontWeight: 600, color: T.slate, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 14px", cursor: "pointer" }}>Retry</button>
        </Card>
      )}

      {data && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: 14, marginBottom: 18 }}>
            {[["Awaiting confirmation", proposals.length, proposals.length ? T.daffodilText : T.meadowInk],
              ["Confirmed this pass", stats.confirmed_this_pass + Object.values(localResolved).filter((v) => v === "confirmed").length, T.meadowInk],
              ["Entity unclear", proposals.filter((p) => p.ambiguous).length, T.poppyText],
              ["Possible gaps", proposals.filter((p) => p.flavor === "gap").length, T.poppyText]].map(([l, v, c], i) => (
              <Card key={i} style={{ padding: "14px 16px" }}>
                <div style={{ fontFamily: "var(--font-text)", fontSize: 11.5, color: T.slate, marginBottom: 6 }}>{l}</div>
                <div style={{ fontFamily: "var(--font-display)", fontSize: 23, fontWeight: 700, color: c }}>{v}</div>
              </Card>
            ))}
          </div>

          <Card style={{ padding: "8px 16px 12px" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 6px 4px" }}>
              <Eyebrow>Proposed obligations</Eyebrow>
              <span style={{ fontFamily: "var(--font-text)", fontSize: 11, color: T.muted }}>nothing is tracked until you confirm it</span>
            </div>
            {proposals.length ? proposals.map((p) => (
              <ProposalRow key={p.id} p={p} open={openId === p.id} entities={entities} busy={busy}
                onToggle={() => setOpenId(openId === p.id ? null : p.id)}
                onConfirm={confirm} onDismiss={dismiss} />
            )) : (
              <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "22px 6px", fontFamily: "var(--font-text)", fontSize: 13, fontWeight: 600, color: T.meadowInk, borderTop: `1px solid ${T.line}` }}>
                <Icon name="check" size={16} color={T.meadow} />No proposals awaiting review. Upload or forward documents to get started.
              </div>
            )}
          </Card>

          {filed.length > 0 && (
            <div style={{ marginTop: 18 }}>
              <Eyebrow>Filed, no obligation</Eyebrow>
              <div style={{ fontFamily: "var(--font-text)", fontSize: 11.5, color: T.muted, margin: "4px 0 10px" }}>Documents stored as evidence but carrying no dated obligation.</div>
              <Card style={{ padding: "6px 16px" }}>
                {filed.map((n, i) => (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 11, padding: "11px 4px", borderTop: i ? `1px solid ${T.line}` : "none" }}>
                    <Icon name="link" size={14} color={T.muted} />
                    <span style={{ fontFamily: "var(--font-text)", fontSize: 12.5, fontWeight: 600, color: T.ink }}>{n.filename}</span>
                    {n.entity && <span style={{ fontFamily: "var(--font-text)", fontSize: 11.5, color: T.muted }}>· {n.entity}</span>}
                    <span style={{ flex: 1 }} />
                    <span style={{ fontFamily: "var(--font-text)", fontSize: 11, color: T.tertiary }}>{n.note}</span>
                  </div>
                ))}
              </Card>
            </div>
          )}

          {usingSample && (
            <div style={{ fontFamily: "var(--font-text)", fontSize: 11, color: T.muted, textAlign: "center", marginTop: 20 }}>
              Sample data · every proposal is human-confirmed before it becomes a tracked obligation
            </div>
          )}
        </>
      )}
    </div>
  );
}
