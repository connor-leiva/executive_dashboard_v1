/* Scorecard settings (owner/admin) — self-service office config. Phase A: edit each office owner's
   full name and upload a headshot. Periods + per-period goals land here next. */
import { useState, useEffect } from "react";
import { patchJSON, putJSON, getJSON, uploadFile, fileUrl } from "../api.js";
import { C, FD, FB, FM } from "./scorecardMath.js";

const _label = { fontFamily: FM, fontSize: 11, letterSpacing: ".08em", textTransform: "uppercase", color: C.muted, marginBottom: 8 };
const _field = { fontFamily: FB, fontSize: 13, color: C.ink, background: C.parchment, border: `1px solid ${C.hair}`, borderRadius: 8, padding: "6px 9px" };
const _btn = { fontFamily: FM, fontSize: 11.5, borderRadius: 8, padding: "6px 12px", cursor: "pointer", border: `1px solid ${C.hair}`, color: C.slate, background: "none" };
const _colHdr = { width: 92, textAlign: "right", fontFamily: FM, fontSize: 9.5, letterSpacing: ".08em", textTransform: "uppercase", color: C.muted };

export default function ScorecardSettings({ groups, scope = "ulrg", onClose, onChanged }) {
  return (
    <div style={{ background: C.surface, border: `1px solid ${C.hair}`, borderRadius: 12, padding: "16px 18px", marginBottom: 18 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 12 }}>
        <span style={{ fontFamily: FD, fontSize: 15, fontWeight: 600, color: C.ink }}>Scorecard settings</span>
        <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", fontFamily: FM, fontSize: 12, color: C.slate }}>Done</button>
      </div>
      <div style={_label}>Offices</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {(groups || []).map((g) => <OfficeRow key={g.id} g={g} onChanged={onChanged} />)}
      </div>
      <MeasurablesEditor scope={scope} onChanged={onChanged} />
      <PeriodsEditor onChanged={onChanged} />
      <GoalsEditor scope={scope} onChanged={onChanged} />
    </div>
  );
}

/* Rename measurables (owner/admin), self-service — so a static number ("130 Homes Sold Q2") never
   goes stale in the label. Names are global (not per-period); auto-sourcing is unaffected. */
function MeasurablesEditor({ scope = "ulrg", onChanged }) {
  const [rows, setRows] = useState(null);       // [{metric_id, name, group, dirty}]
  const [state, setState] = useState("idle");
  const [confirmId, setConfirmId] = useState(null);   // row awaiting a remove confirm
  const [busy, setBusy] = useState(null);             // row currently removing

  async function remove(metric_id) {
    setBusy(metric_id);
    try {
      await patchJSON(`/ulrg/metric/${metric_id}`, { active: false });   // soft delete — history kept
      setRows(rows.filter((r) => r.metric_id !== metric_id));
      setConfirmId(null); setBusy(null);
      onChanged && onChanged();                                          // refresh the scorecard grid
    } catch (e) { setBusy(null); setState("error"); }
  }

  useEffect(() => {
    // reuse the goals endpoint for the metric list (any period works — names aren't period-scoped)
    getJSON("/ulrg/periods").then((d) => {
      const p = (d.periods || [])[0];
      const q = p ? `?period=${encodeURIComponent(p.key)}` : "?period=_";
      return getJSON(`/ulrg/goals${q}&scope=${scope}`);
    }).then((d) => setRows((d.goals || []).map((g) => ({ metric_id: g.metric_id, name: g.name, group: g.group }))))
      .catch(() => setRows([]));
  }, []);

  async function save() {
    setState("saving");
    try {
      const dirty = rows.filter((r) => r.dirty && String(r.name).trim() !== "");
      for (const r of dirty) await patchJSON(`/ulrg/metric/${r.metric_id}`, { name: r.name.trim() });
      setRows(rows.map((r) => ({ ...r, dirty: false })));
      setState("saved"); onChanged && onChanged(); setTimeout(() => setState("idle"), 1600);
    } catch (e) { setState("error"); }
  }

  if (rows === null) return <div style={{ ..._label, marginTop: 18 }}>Loading measurables…</div>;
  return (
    <div style={{ marginTop: 20, borderTop: `1px solid ${C.hair}`, paddingTop: 16 }}>
      <div style={_label}>Measurables · rename or remove</div>
      <div style={{ fontFamily: FB, fontSize: 11, color: C.muted, marginBottom: 8 }}>
        Removing hides a row from the scorecard and keeps its history — an admin can restore it.
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 300, overflowY: "auto" }}>
        {rows.map((r, i) => (
          <div key={r.metric_id} style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ flex: "0 0 92px", fontFamily: FM, fontSize: 10.5, letterSpacing: ".05em", textTransform: "uppercase", color: C.slate }}>{r.group}</span>
            <input value={r.name} aria-label="Measurable name"
                   onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, name: e.target.value, dirty: true } : x))}
                   style={{ ..._field, flex: "1 1 auto" }} />
            {confirmId === r.metric_id ? (
              <span style={{ display: "flex", gap: 4, flexShrink: 0 }}>
                <button onClick={() => remove(r.metric_id)} disabled={busy === r.metric_id} aria-label={`Confirm remove ${r.name}`}
                        style={{ ..._btn, padding: "4px 9px", fontSize: 11, color: C.poppy, borderColor: C.poppy }}>
                  {busy === r.metric_id ? "…" : "Remove"}
                </button>
                <button onClick={() => setConfirmId(null)} style={{ ..._btn, padding: "4px 9px", fontSize: 11, color: C.muted }}>Cancel</button>
              </span>
            ) : (
              <button onClick={() => setConfirmId(r.metric_id)} aria-label={`Remove ${r.name}`} title="Remove this row"
                      style={{ flexShrink: 0, border: "none", background: "none", cursor: "pointer", color: C.slate, fontSize: 17, lineHeight: 1, padding: "0 6px" }}>×</button>
            )}
          </div>
        ))}
      </div>
      <button onClick={save} disabled={state === "saving"} style={{ ..._btn, marginTop: 10,
        color: state === "saved" ? C.meadowInk : state === "error" ? C.poppy : C.ink,
        borderColor: state === "error" ? C.poppy : C.hair }}>
        {state === "saving" ? "Saving…" : state === "saved" ? "Saved ✓" : state === "error" ? "Retry" : "Save names"}
      </button>
    </div>
  );
}

function GoalsEditor({ scope = "ulrg", onChanged }) {
  const [periods, setPeriods] = useState(null);
  const [period, setPeriod] = useState("");
  const [goals, setGoals] = useState(null);
  const [state, setState] = useState("idle");

  useEffect(() => {
    getJSON("/ulrg/periods").then((d) => {
      const ps = d.periods || [];
      setPeriods(ps);
      if (ps.length) setPeriod(ps[ps.length - 1].key);   // default to the latest period
    }).catch(() => setPeriods([]));
  }, []);
  useEffect(() => {
    if (!period) return;
    setGoals(null);
    getJSON(`/ulrg/goals?period=${encodeURIComponent(period)}&scope=${scope}`).then((d) => setGoals(d.goals || [])).catch(() => setGoals([]));
  }, [period]);

  async function save() {
    setState("saving");
    try {
      // send only fields with a real weekly number (0 is a valid track-only goal); a cleared weekly
      // field is skipped, so it keeps its current goal instead of silently becoming 0. The cumulative
      // (period total) is optional and flow-only; blank / non-positive clears it.
      const payload = goals
        .filter((g) => String(g.goal).trim() !== "" && Number.isFinite(parseFloat(g.goal)))
        .map((g) => {
          const cg = g.supports_cumulative ? parseFloat(g.cumulative_goal) : NaN;
          return { metric_id: g.metric_id, goal: parseFloat(g.goal),
                   cumulative_goal: Number.isFinite(cg) && cg > 0 ? cg : null };
        });
      await putJSON("/ulrg/goals", { period, goals: payload });
      setState("saved"); onChanged && onChanged(); setTimeout(() => setState("idle"), 1600);
    } catch (e) { setState("error"); }
  }

  if (periods === null) return null;
  if (!periods.length) return (
    <div style={{ marginTop: 20, borderTop: `1px solid ${C.hair}`, paddingTop: 16 }}>
      <div style={_label}>Goals per period</div>
      <div style={{ fontFamily: FB, fontSize: 12, color: C.muted }}>Add a measurement period first.</div>
    </div>
  );

  return (
    <div style={{ marginTop: 20, borderTop: `1px solid ${C.hair}`, paddingTop: 16 }}>
      <div style={_label}>Goals per period</div>
      <select value={period} onChange={(e) => setPeriod(e.target.value)} style={{ ..._field, minWidth: 220 }}>
        {periods.map((p) => <option key={p.key} value={p.key}>{p.key} ({p.start} → {p.end})</option>)}
      </select>
      <div style={{ fontFamily: FB, fontSize: 11, color: C.muted, marginTop: 6, lineHeight: 1.5 }}>
        <b style={{ color: C.slate }}>Weekly</b> colours each week's cell. <b style={{ color: C.slate }}>Cumulative</b> is the
        whole-period total (e.g. 130 homes/quarter); the running total tracks toward it. Leave cumulative blank to track vs weekly × weeks.
      </div>
      {goals === null
        ? <div style={{ ..._label, marginTop: 12 }}>Loading goals…</div>
        : (
          <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6, maxHeight: 360, overflowY: "auto" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, position: "sticky", top: 0, background: C.surface, paddingBottom: 4 }}>
              <span style={{ flex: "1 1 auto" }} />
              <span style={{ ..._colHdr }}>Weekly</span>
              <span style={{ ..._colHdr }}>Cumulative</span>
            </div>
            {goals.map((g) => {
              const upd = (k, v) => setGoals(goals.map((x) => x.metric_id === g.metric_id ? { ...x, [k]: v } : x));
              return (
                <div key={g.metric_id} style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{ flex: "1 1 auto", fontFamily: FB, fontSize: 12.5, color: C.body }}>
                    <span style={{ color: C.muted, fontSize: 11 }}>{g.group} · </span>{g.name}
                    {g.type === "rate" && <span style={{ color: C.muted, fontSize: 11 }}> (%)</span>}
                  </span>
                  <input type="number" step="0.1" value={g.goal} aria-label={`${g.name} weekly goal`}
                         onChange={(e) => upd("goal", e.target.value)}
                         style={{ ..._field, width: 92, textAlign: "right" }} />
                  {g.supports_cumulative
                    ? <input type="number" step="1" value={g.cumulative_goal ?? ""} placeholder="—" aria-label={`${g.name} period total`}
                             onChange={(e) => upd("cumulative_goal", e.target.value)}
                             style={{ ..._field, width: 92, textAlign: "right" }} />
                    : <span style={{ width: 92, textAlign: "center", color: C.muted, fontFamily: FM, fontSize: 12 }}>—</span>}
                </div>
              );
            })}
            <button onClick={save} disabled={state === "saving"} style={{ ..._btn, alignSelf: "flex-start", marginTop: 8,
              color: state === "saved" ? C.meadowInk : state === "error" ? C.poppy : C.ink,
              borderColor: state === "error" ? C.poppy : C.hair }}>
              {state === "saving" ? "Saving…" : state === "saved" ? "Saved ✓" : state === "error" ? "Retry" : `Save goals for ${period}`}
            </button>
          </div>
        )}
    </div>
  );
}

function PeriodsEditor({ onChanged }) {
  const [periods, setPeriods] = useState(null);
  const [state, setState] = useState("idle");
  useEffect(() => { getJSON("/ulrg/periods").then((d) => setPeriods(d.periods || [])).catch(() => setPeriods([])); }, []);

  if (periods === null) return <div style={{ ...( _label), marginTop: 18 }}>Loading periods…</div>;
  const upd = (i, k, v) => setPeriods(periods.map((p, j) => (j === i ? { ...p, [k]: v } : p)));

  async function save() {
    setState("saving");
    try { const r = await putJSON("/ulrg/periods", { periods }); setPeriods(r.periods); setState("saved"); onChanged && onChanged(); setTimeout(() => setState("idle"), 1600); }
    catch (e) { setState("error"); }
  }

  return (
    <div style={{ marginTop: 20, borderTop: `1px solid ${C.hair}`, paddingTop: 16 }}>
      <div style={_label}>Measurement periods · sprints</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {periods.map((p, i) => (
          <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <input value={p.key} onChange={(e) => upd(i, "key", e.target.value)} placeholder="Name (e.g. 2026Q4)"
                   style={{ ..._field, flex: "0 0 150px" }} />
            <input type="date" value={p.start} onChange={(e) => upd(i, "start", e.target.value)} style={_field} />
            <span style={{ color: C.muted }}>→</span>
            <input type="date" value={p.end} onChange={(e) => upd(i, "end", e.target.value)} style={_field} />
            <button onClick={() => setPeriods(periods.filter((_, j) => j !== i))}
                    style={{ ..._btn, color: C.poppy, border: "none" }}>Remove</button>
          </div>
        ))}
      </div>
      <div style={{ display: "flex", gap: 10, marginTop: 12 }}>
        <button onClick={() => setPeriods([...periods, { key: "", start: "", end: "" }])} style={_btn}>+ Add sprint</button>
        <button onClick={save} disabled={state === "saving"} style={{ ..._btn,
          color: state === "saved" ? C.meadowInk : state === "error" ? C.poppy : C.ink,
          borderColor: state === "error" ? C.poppy : C.hair }}>
          {state === "saving" ? "Saving…" : state === "saved" ? "Saved ✓" : state === "error" ? "Retry" : "Save periods"}
        </button>
      </div>
    </div>
  );
}

function OfficeRow({ g, onChanged }) {
  const [name, setName] = useState((g.owner && g.owner.name) || "");
  const [state, setState] = useState("idle");   // idle | saving | saved | error
  const src = g.owner && g.owner.photo_url ? fileUrl(g.owner.photo_url) : null;

  async function saveName() {
    setState("saving");
    try { await patchJSON(`/ulrg/group/${g.id}`, { owner_name: name }); setState("saved"); onChanged && onChanged(); setTimeout(() => setState("idle"), 1400); }
    catch (e) { setState("error"); }
  }
  async function upload(e) {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    setState("saving");
    const fd = new FormData(); fd.append("file", file);
    try { await uploadFile(`/ulrg/group/${g.id}/photo`, fd); setState("saved"); onChanged && onChanged(); setTimeout(() => setState("idle"), 1400); }
    catch (err) { setState("error"); }
  }

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
      <label style={{ cursor: "pointer", flex: "0 0 auto" }} title="Upload a headshot">
        {src
          ? <img src={src} alt="" style={{ width: 40, height: 40, borderRadius: 99, objectFit: "cover", border: `1px solid ${C.hair}` }} />
          : <span style={{ width: 40, height: 40, borderRadius: 99, display: "inline-flex", alignItems: "center", justifyContent: "center", background: C.mist, color: C.muted, fontFamily: FM, fontSize: 16 }}>{(name[0] || g.name[0] || "·").toUpperCase()}</span>}
        <input type="file" accept="image/*" onChange={upload} style={{ display: "none" }} />
      </label>
      <span style={{ fontFamily: FM, fontSize: 11, letterSpacing: ".05em", textTransform: "uppercase", color: C.slate, flex: "0 0 92px" }}>{g.name}</span>
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Owner full name"
             style={{ fontFamily: FB, fontSize: 13, color: C.ink, background: C.parchment, border: `1px solid ${C.hair}`, borderRadius: 8, padding: "6px 10px", flex: "1 1 200px", minWidth: 160 }} />
      <button onClick={saveName} disabled={state === "saving"} style={{
        fontFamily: FM, fontSize: 11.5, borderRadius: 8, padding: "6px 12px", cursor: "pointer",
        border: `1px solid ${state === "error" ? C.poppy : C.hair}`,
        color: state === "saved" ? C.meadowInk : state === "error" ? C.poppy : C.slate, background: "none" }}>
        {state === "saving" ? "Saving…" : state === "saved" ? "Saved ✓" : state === "error" ? "Retry" : "Save"}
      </button>
    </div>
  );
}
