/* Drill-down drawer for the L10 grid: click any figure → see what's behind it. Auto (Sisu-sourced)
   metrics show the underlying deals for the window; hand-entered metrics show each week's value,
   editable in place (owner/admin) via the manual-entry endpoint. Never shown in the public embed
   (records carry client names). */
import { useEffect, useState } from "react";
import { getJSON, postJSON } from "../api.js";
import { T, NUM, FONT, LBL } from "./l10tokens.jsx";

const muted = { fontSize: 12.5, color: T.muted, padding: "8px 0" };

export default function DrillDrawer({ drill, canEdit, onClose, onSaved }) {
  const { r, ws, we, label } = drill;   // r=row, ws/we=ISO window, label=window title, weeks=[{n,start,end,label,value}]
  return (
    <>
      <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(18,41,31,0.35)", zIndex: 60 }} />
      <div style={{ position: "fixed", top: 0, right: 0, bottom: 0, width: "min(440px, 94vw)", background: T.paper, zIndex: 61,
        boxShadow: "-8px 0 30px -12px rgba(20,35,28,0.45)", display: "flex", flexDirection: "column", fontFamily: FONT }}>
        <div style={{ padding: "16px 20px", borderBottom: `1px solid ${T.line}`, display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 15, fontWeight: 700, color: T.ink }}>{r.measurable}</div>
            <div style={{ fontSize: 12, color: T.muted, marginTop: 2 }}>{label}{r.owner && r.owner.initials ? ` · ${r.owner.initials}` : ""}</div>
          </div>
          <button onClick={onClose} aria-label="Close" style={{ border: "none", background: "none", cursor: "pointer", fontSize: 22, color: T.muted, lineHeight: 1, flexShrink: 0 }}>×</button>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "12px 20px 24px" }}>
          {r.auto ? <Records r={r} ws={ws} we={we} /> : <ManualWeeks drill={drill} canEdit={canEdit} onSaved={onSaved} />}
        </div>
      </div>
    </>
  );
}

function Records({ r, ws, we }) {
  const [recs, setRecs] = useState(null);
  const [sort, setSort] = useState("name");   // alphabetical by default; toggle to date
  useEffect(() => {
    let ok = true;
    setRecs(null);
    getJSON(`/ulrg/metric/${r.id}/records?week_start=${ws}&week_end=${we}`)
      .then((d) => { if (ok) setRecs(d.records || []); })
      .catch(() => { if (ok) setRecs([]); });
    return () => { ok = false; };
  }, [r.id, ws, we]);

  if (recs === null) return <div style={muted}>Loading records…</div>;
  if (!recs.length) return <div style={muted}>No records from Sisu in this window.</div>;
  // attach-rate drill → the denominator deals, each flagged `captured` (the numerator subset), so the
  // list reads back as the rate. Count metrics have no `captured` field → plain "N records".
  const attach = "captured" in recs[0];
  const vendor = /sympli/i.test(r.measurable) ? "Sympli" : /meraki/i.test(r.measurable) ? "Meraki" : "Attached";
  const capped = recs.filter((x) => x.captured).length;
  const pct = recs.length ? Math.round((capped / recs.length) * 1000) / 10 : 0;
  // Why the denominator differs from Homes Sold: Sympli is buyer-side only (you can't attach a mortgage
  // to a listing); Meraki (title) applies to every closing. Stated so the two drawers reconcile.
  const denomNote = !attach ? null
    : /sympli/i.test(r.measurable) ? "Buyer-side closings — a listing has no buyer to finance"
    : /meraki/i.test(r.measurable) ? "Closings that recorded a title company"
    : null;
  const sorted = [...recs].sort((a, b) =>
    sort === "date"
      ? String(b.date || "").localeCompare(String(a.date || ""))            // newest first
      : String(a.client || "~").localeCompare(String(b.client || "~"), undefined, { sensitivity: "base" }));  // A→Z, blanks last
  return (
    <>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, marginBottom: 8 }}>
        <div style={LBL}>{attach
          ? `${capped} of ${recs.length} used ${vendor} · ${pct}%`
          : `${recs.length} record${recs.length === 1 ? "" : "s"} · from Sisu`}</div>
        <div style={{ display: "flex", gap: 2, background: T.shell, borderRadius: 6, padding: 2, flexShrink: 0 }}>
          {["name", "date"].map((k) => (
            <button key={k} onClick={() => setSort(k)} style={{
              fontFamily: FONT, fontSize: 10.5, textTransform: "capitalize", border: "none", cursor: "pointer",
              padding: "3px 9px", borderRadius: 4, fontWeight: sort === k ? 600 : 500,
              background: sort === k ? T.paper : "transparent", color: sort === k ? T.ink : T.muted,
              boxShadow: sort === k ? "0 1px 2px rgba(20,35,28,0.1)" : "none" }}>{k}</button>
          ))}
        </div>
      </div>
      {denomNote && <div style={{ fontSize: 11, color: T.muted, marginTop: -2, marginBottom: 8 }}>{denomNote}</div>}
      {sorted.map((x) => (
        <div key={x.id} style={{ padding: "10px 0", borderBottom: `1px solid ${T.lineSoft}` }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: T.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{x.client || "—"}</span>
            <span style={{ ...NUM, fontSize: 11.5, color: T.muted, flexShrink: 0 }}>{fmtDate(x.date)}</span>
          </div>
          <div style={{ fontSize: 11.5, color: T.inkSoft, marginTop: 3, display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            {attach && <Tag on={x.captured} label={x.captured ? vendor : "Other"} />}
            {x.side === "sell" && <Tag on={false} label="Listing" />}
            <span>{x.agent || "—"}{x.address ? ` · ${x.address}` : ""}{x.sale_price ? ` · $${Math.round(x.sale_price).toLocaleString()}` : ""}</span>
          </div>
        </div>
      ))}
    </>
  );
}

function ManualWeeks({ drill, canEdit, onSaved }) {
  const { r, weeks } = drill;
  const rate = r.type === "rate";
  const [vals, setVals] = useState(() => Object.fromEntries(weeks.map((w) => [w.start, w.value == null ? "" : String(w.value)])));
  const [saving, setSaving] = useState(null);   // week.start currently saving
  const [saved, setSaved] = useState(null);
  const [err, setErr] = useState(null);

  async function save(weekStart) {
    setSaving(weekStart); setErr(null); setSaved(null);
    const raw = (vals[weekStart] ?? "").trim();
    const value = raw === "" ? null : parseFloat(raw);
    if (raw !== "" && !Number.isFinite(value)) { setErr(weekStart); setSaving(null); return; }
    try {
      await postJSON("/ulrg/scorecard/values", { metric_id: r.id, week_start: weekStart, value });
      setSaving(null); setSaved(weekStart); onSaved && onSaved(); setTimeout(() => setSaved(null), 1400);
    } catch (e) { setSaving(null); setErr(weekStart); }
  }

  return (
    <>
      <div style={{ ...LBL, marginBottom: 6 }}>Hand-entered{canEdit ? " · edit any week" : ""}</div>
      {weeks.map((w) => (
        <div key={w.start} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 0", borderBottom: `1px solid ${T.lineSoft}` }}>
          <span style={{ flex: 1, fontSize: 12.5, color: T.inkSoft }}>W{w.n} · {w.label}</span>
          {canEdit ? (
            <>
              <input type="number" step="0.1" value={vals[w.start]} aria-label={`Value for week ${w.label}`}
                onChange={(e) => setVals({ ...vals, [w.start]: e.target.value })}
                style={{ width: 84, textAlign: "right", fontFamily: FONT, fontSize: 13, color: T.ink, background: T.rail,
                  border: `1px solid ${err === w.start ? T.bad : T.line}`, borderRadius: 6, padding: "5px 8px" }} />
              <button onClick={() => save(w.start)} disabled={saving === w.start}
                style={{ fontFamily: FONT, fontSize: 11.5, borderRadius: 6, padding: "5px 10px", cursor: "pointer",
                  border: `1px solid ${T.line}`, background: "transparent",
                  color: saved === w.start ? T.good : err === w.start ? T.bad : T.inkSoft }}>
                {saving === w.start ? "…" : saved === w.start ? "✓" : "Save"}
              </button>
            </>
          ) : (
            <span style={{ ...NUM, fontSize: 13, color: T.ink }}>{w.value == null ? "—" : `${w.value}${rate ? "%" : ""}`}</span>
          )}
        </div>
      ))}
      {!canEdit && <div style={{ ...muted, marginTop: 10 }}>This measurable is typed by hand — no source records to open.</div>}
    </>
  );
}

function Tag({ on, label }) {
  return (
    <span style={{
      fontFamily: FONT, fontSize: 9.5, fontWeight: 700, letterSpacing: "0.03em", textTransform: "uppercase",
      padding: "1px 6px", borderRadius: 10, whiteSpace: "nowrap", flexShrink: 0,
      color: on ? T.good : T.muted, background: "transparent",
      border: `1px solid ${on ? T.good : T.line}` }}>
      {on ? `✓ ${label}` : label}
    </span>
  );
}

function fmtDate(iso) {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-");
  return `${Number(m)}/${d}`;
}
