/* Scorecard settings (owner/admin) — self-service office config. Phase A: edit each office owner's
   full name and upload a headshot. Periods + per-period goals land here next. */
import { useState, useEffect } from "react";
import { patchJSON, putJSON, postJSON, getJSON, uploadFile, fileUrl } from "../api.js";
import { C, FD, FB, FM } from "./scorecardMath.js";

const _label = { fontFamily: FM, fontSize: 11, letterSpacing: ".08em", textTransform: "uppercase", color: C.muted, marginBottom: 8 };
const _field = { fontFamily: FB, fontSize: 13, color: C.ink, background: C.parchment, border: `1px solid ${C.hair}`, borderRadius: 8, padding: "6px 9px" };
const _btn = { fontFamily: FM, fontSize: 11.5, borderRadius: 8, padding: "6px 12px", cursor: "pointer", border: `1px solid ${C.hair}`, color: C.slate, background: "none" };
const _colHdr = { width: 92, textAlign: "right", fontFamily: FM, fontSize: 9.5, letterSpacing: ".08em", textTransform: "uppercase", color: C.muted };

export default function ScorecardSettings({ groups, scope = "ulrg", onClose, onChanged }) {
  const gs = groups || [];
  async function move(i, dir) {
    const j = i + dir;
    if (j < 0 || j >= gs.length) return;
    const a = gs[i], b = gs[j];                       // swap the two offices' sort_order
    try {
      await patchJSON(`/ulrg/group/${a.id}`, { sort_order: b.sort_order ?? j });
      await patchJSON(`/ulrg/group/${b.id}`, { sort_order: a.sort_order ?? i });
      onChanged && onChanged();
    } catch (e) { /* leave order as-is on failure */ }
  }
  return (
    <div style={{ background: C.surface, border: `1px solid ${C.hair}`, borderRadius: 12, padding: "16px 18px", marginBottom: 18 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 12 }}>
        <span style={{ fontFamily: FD, fontSize: 15, fontWeight: 600, color: C.ink }}>Scorecard settings</span>
        <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", fontFamily: FM, fontSize: 12, color: C.slate }}>Done</button>
      </div>
      <div style={_label}>Offices</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {gs.map((g, i) => <OfficeRow key={g.id} g={g} canUp={i > 0} canDown={i < gs.length - 1}
                                     onMove={(d) => move(i, d)} onChanged={onChanged} />)}
      </div>
      <AddOffice scope={scope} onChanged={onChanged} />
      <MeasurablesEditor scope={scope} groups={gs} onChanged={onChanged} />
      <RoutingEditor scope={scope} onChanged={onChanged} />
      <PeriodsEditor onChanged={onChanged} />
      <GoalsEditor scope={scope} onChanged={onChanged} />
    </div>
  );
}

function AddOffice({ scope, onChanged }) {
  const [name, setName] = useState("");
  const [state, setState] = useState("idle");
  async function add() {
    if (!name.trim()) return;
    setState("saving");
    try { await postJSON("/ulrg/group", { name: name.trim(), scope }); setName(""); setState("idle"); onChanged && onChanged(); }
    catch (e) { setState("error"); }
  }
  return (
    <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center" }}>
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="New office name" aria-label="New office name"
             onKeyDown={(e) => { if (e.key === "Enter") add(); }} style={{ ..._field, flex: "1 1 200px" }} />
      <button onClick={add} disabled={state === "saving" || !name.trim()}
              style={{ ..._btn, color: state === "error" ? C.poppy : C.ink, borderColor: state === "error" ? C.poppy : C.hair }}>
        {state === "saving" ? "Adding…" : state === "error" ? "Retry" : "+ Add office"}
      </button>
    </div>
  );
}

/* Rename measurables (owner/admin), self-service — so a static number ("130 Homes Sold Q2") never
   goes stale in the label. Names are global (not per-period); auto-sourcing is unaffected. */
function MeasurablesEditor({ scope = "ulrg", groups = [], onChanged }) {
  const [rows, setRows] = useState(null);       // [{metric_id, name, group, dirty}]
  const [state, setState] = useState("idle");
  const [confirmId, setConfirmId] = useState(null);   // row awaiting a remove confirm
  const [busy, setBusy] = useState(null);             // row currently removing
  const [add, setAdd] = useState({ group_id: "", name: "", type: "flow" });
  const [addState, setAddState] = useState("idle");

  // reuse the goals endpoint for the metric list (any period works — names aren't period-scoped)
  async function load() {
    try {
      const d = await getJSON("/ulrg/periods");
      const p = (d.periods || [])[0];
      const q = p ? `?period=${encodeURIComponent(p.key)}` : "?period=_";
      const g = await getJSON(`/ulrg/goals${q}&scope=${scope}`);
      setRows((g.goals || []).map((x) => ({ metric_id: x.metric_id, name: x.name, group: x.group })));
    } catch (e) { setRows([]); }
  }
  useEffect(() => { load(); }, []);

  async function addMetric() {
    if (!add.group_id || !add.name.trim()) return;
    setAddState("saving");
    try {
      await postJSON("/ulrg/metric", { group_id: add.group_id, name: add.name.trim(), type: add.type });
      setAdd({ ...add, name: "" }); setAddState("idle");
      await load();                 // the new row appears in the rename list
      onChanged && onChanged();     // and on the grid
    } catch (e) { setAddState("error"); }
  }

  async function remove(metric_id) {
    setBusy(metric_id);
    try {
      await patchJSON(`/ulrg/metric/${metric_id}`, { active: false });   // soft delete — history kept
      setRows(rows.filter((r) => r.metric_id !== metric_id));
      setConfirmId(null); setBusy(null);
      onChanged && onChanged();                                          // refresh the scorecard grid
    } catch (e) { setBusy(null); setState("error"); }
  }

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
      <div style={_label}>Measurables · add, rename or remove</div>
      <div style={{ fontFamily: FB, fontSize: 11, color: C.muted, marginBottom: 8 }}>
        Add a measurable to an office (starts manual — set its goal in Goals). Removing hides a row and keeps its history.
      </div>
      <div style={{ display: "flex", gap: 6, marginBottom: 12, flexWrap: "wrap", alignItems: "center" }}>
        <select value={add.group_id} onChange={(e) => setAdd({ ...add, group_id: e.target.value })}
                aria-label="Office for new measurable" style={{ ..._field, flex: "0 0 132px" }}>
          <option value="">Office…</option>
          {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
        </select>
        <input value={add.name} onChange={(e) => setAdd({ ...add, name: e.target.value })} placeholder="New measurable"
               aria-label="New measurable name" onKeyDown={(e) => { if (e.key === "Enter") addMetric(); }}
               style={{ ..._field, flex: "1 1 150px" }} />
        <select value={add.type} onChange={(e) => setAdd({ ...add, type: e.target.value })}
                aria-label="Measurable type" style={{ ..._field, flex: "0 0 104px" }}>
          <option value="flow">flow</option>
          <option value="rate">rate (%)</option>
          <option value="snapshot">snapshot</option>
        </select>
        <button onClick={addMetric} disabled={addState === "saving" || !add.group_id || !add.name.trim()}
                style={{ ..._btn, color: addState === "error" ? C.poppy : C.ink, borderColor: addState === "error" ? C.poppy : C.hair }}>
          {addState === "saving" ? "Adding…" : addState === "error" ? "Retry" : "+ Add"}
        </button>
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

/* Auto-sync routing (owner/admin) — point a measurable at a data source + filters (Phase 3). The
   dropdowns are built from the engine's whitelist (/routing/catalog), so nothing here can produce a
   spec the engine would reject; a live preview shows the spec's numbers next to the current resolver's
   before you save. */
function _blankSpec(ds) {
  return { source: ds.source, dataset: ds.dataset, date_field: ds.date_fields[0],
           filters: [], aggregate: { fn: "count" }, attribution: ds.attribution[0] };
}
const _specSummary = (s) => `${s.source}.${s.dataset} · by ${s.date_field} · ${s.filters?.length || 0} filter(s) · ${s.aggregate?.fn || "count"}${s.attribution === "office" ? " · per office" : ""}`;
const _pill = (bg, fg) => ({ fontFamily: FM, fontSize: 9.5, letterSpacing: ".06em", textTransform: "uppercase", padding: "2px 7px", borderRadius: 99, background: bg, color: fg });

function RoutingEditor({ scope = "ulrg", onChanged }) {
  const [catalog, setCatalog] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [openId, setOpenId] = useState(null);

  async function reload() {
    try { const rt = await getJSON(`/ulrg/routing?scope=${scope}`); setMetrics(rt.metrics || []); } catch (e) { /* keep */ }
  }
  useEffect(() => {
    Promise.all([getJSON("/ulrg/routing/catalog"), getJSON(`/ulrg/routing?scope=${scope}`)])
      .then(([cat, rt]) => { setCatalog(cat); setMetrics(rt.metrics || []); })
      .catch(() => { setCatalog({ datasets: [], ops: [], aggregates: [] }); setMetrics([]); });
  }, [scope]);

  if (metrics === null) return <div style={{ ..._label, marginTop: 18 }}>Loading routing…</div>;
  return (
    <div style={{ marginTop: 20, borderTop: `1px solid ${C.hair}`, paddingTop: 16 }}>
      <div style={_label}>Auto-sync routing · what feeds each measurable</div>
      <div style={{ fontFamily: FB, fontSize: 11, color: C.muted, marginBottom: 10 }}>
        Point a measurable at a data source and filters. The number doesn’t change until the next sync; preview first to see it matches.
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {metrics.map((m) => {
          const open = openId === m.metric_id;
          const tag = m.routed ? _pill(C.meadow, C.meadowInk) : (m.resolver_key ? _pill(C.mist, C.slate) : _pill(C.parchment, C.muted));
          const label = m.routed ? "Routed" : (m.resolver_key ? "Standard" : "Manual");
          return (
            <div key={m.metric_id} style={{ border: `1px solid ${C.hair}`, borderRadius: 8, background: open ? C.parchment : "none" }}>
              <div onClick={() => setOpenId(open ? null : m.metric_id)} role="button" aria-expanded={open}
                   style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px", cursor: "pointer" }}>
                <span style={{ color: C.muted, fontFamily: FM, fontSize: 11, width: 12 }}>{open ? "▾" : "▸"}</span>
                <span style={{ flex: "1 1 auto", fontFamily: FB, fontSize: 12.5, color: C.body }}>
                  <span style={{ color: C.muted, fontSize: 11 }}>{m.group} · </span>{m.name}
                </span>
                <span style={tag}>{label}</span>
              </div>
              {open && catalog && <MetricRouting catalog={catalog} m={m} onSaved={() => { reload(); onChanged && onChanged(); }} />}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function MetricRouting({ catalog, m, onSaved }) {
  const datasets = catalog.datasets || [];
  const [spec, setSpec] = useState(() => m.source_spec || m.default_spec || _blankSpec(datasets[0] || { source: "", dataset: "", date_fields: [""], attribution: ["none"] }));
  const [preview, setPreview] = useState(null);
  const [state, setState] = useState("idle");       // idle | previewing | saving | saved | error
  const ds = datasets.find((d) => d.source === spec.source && d.dataset === spec.dataset) || datasets[0];
  const fieldType = (name) => (ds.fields.find((f) => f.name === name) || {}).type;
  const arity = (op) => (catalog.ops.find((o) => o.op === op) || {}).arity;
  const numFields = ds.fields.filter((f) => f.type === "num").map((f) => f.name);
  const set = (patch) => { setSpec({ ...spec, ...patch }); setPreview(null); };

  function changeDataset(key) {
    const nd = datasets.find((d) => `${d.source}.${d.dataset}` === key);
    if (nd) { setSpec(_blankSpec(nd)); setPreview(null); }
  }
  const setFilter = (i, patch) => set({ filters: spec.filters.map((f, j) => (j === i ? { ...f, ...patch } : f)) });
  const addFilter = () => set({ filters: [...(spec.filters || []), { field: ds.fields[0].name, op: "eq", value: "" }] });
  const rmFilter = (i) => set({ filters: spec.filters.filter((_, j) => j !== i) });

  function cleanSpec() {
    const filters = (spec.filters || []).map((f) => {
      const a = arity(f.op), t = fieldType(f.field);
      if (a === "none") return { field: f.field, op: f.op };
      if (a === "list") {
        const parts = String(f.value ?? "").split(",").map((x) => x.trim()).filter((x) => x !== "");
        return { field: f.field, op: f.op, value: t === "num" ? parts.map(Number) : parts };
      }
      return { field: f.field, op: f.op, value: t === "num" ? Number(f.value) : f.value };
    });
    const agg = spec.aggregate.fn === "count"
      ? { fn: "count" }
      : { fn: spec.aggregate.fn, field: spec.aggregate.field || numFields[0] };
    return { source: spec.source, dataset: spec.dataset, date_field: spec.date_field,
             filters, aggregate: agg, attribution: spec.attribution };
  }

  async function doPreview() {
    setState("previewing"); setPreview(null);
    try { setPreview(await postJSON("/ulrg/routing/preview", { metric_id: m.metric_id, spec: cleanSpec(), weeks: 8 })); setState("idle"); }
    catch (e) { setState("error"); }
  }
  async function save() {
    setState("saving");
    try { await putJSON(`/ulrg/metric/${m.metric_id}/routing`, { spec: cleanSpec() }); setState("saved"); onSaved && onSaved(); setTimeout(() => setState("idle"), 1500); }
    catch (e) { setState("error"); }
  }
  async function turnOff() {
    setState("saving");
    try { await putJSON(`/ulrg/metric/${m.metric_id}/routing`, { spec: null }); setState("saved"); onSaved && onSaved(); setTimeout(() => setState("idle"), 1500); }
    catch (e) { setState("error"); }
  }

  const cell = (v) => (v === null || v === undefined ? "—" : v === "unavailable" ? "n/a" : v);
  const allMatch = preview && preview.valid && preview.has_current && preview.weeks.length && preview.weeks.every((w) => w.match);

  return (
    <div style={{ padding: "4px 12px 14px", display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <select aria-label="Source" value={`${spec.source}.${spec.dataset}`} onChange={(e) => changeDataset(e.target.value)} style={{ ..._field, flex: "1 1 190px" }}>
          {datasets.map((d) => <option key={`${d.source}.${d.dataset}`} value={`${d.source}.${d.dataset}`}>{d.label}</option>)}
        </select>
        <label style={{ ..._label, margin: 0, alignSelf: "center" }}>by</label>
        <select aria-label="Date field" value={spec.date_field} onChange={(e) => set({ date_field: e.target.value })} style={{ ..._field, flex: "0 0 150px" }}>
          {ds.date_fields.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{ ..._label, margin: 0 }}>Filters</div>
        {(spec.filters || []).map((f, i) => (
          <div key={i} style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
            <select aria-label="Field" value={f.field} onChange={(e) => setFilter(i, { field: e.target.value })} style={{ ..._field, flex: "0 0 140px" }}>
              {ds.fields.map((x) => <option key={x.name} value={x.name}>{x.name}</option>)}
            </select>
            <select aria-label="Operator" value={f.op} onChange={(e) => setFilter(i, { op: e.target.value })} style={{ ..._field, flex: "0 0 92px" }}>
              {catalog.ops.map((o) => <option key={o.op} value={o.op}>{o.op}</option>)}
            </select>
            {arity(f.op) === "none"
              ? <span style={{ flex: "1 1 120px", color: C.muted, fontFamily: FB, fontSize: 12 }}>—</span>
              : <input aria-label="Value" value={f.value ?? ""} placeholder={arity(f.op) === "list" ? "a, b, c" : "value"}
                       onChange={(e) => setFilter(i, { value: e.target.value })} style={{ ..._field, flex: "1 1 120px" }} />}
            <button onClick={() => rmFilter(i)} aria-label="Remove filter" style={{ ..._icon, fontSize: 16 }}>×</button>
          </div>
        ))}
        <button onClick={addFilter} style={{ ..._btn, alignSelf: "flex-start", padding: "4px 10px", fontSize: 11 }}>+ Add filter</button>
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <label style={{ ..._label, margin: 0, alignSelf: "center" }}>Aggregate</label>
        <select aria-label="Aggregate" value={spec.aggregate.fn} onChange={(e) => set({ aggregate: e.target.value === "count" ? { fn: "count" } : { fn: e.target.value, field: spec.aggregate.field || numFields[0] } })} style={{ ..._field, flex: "0 0 128px" }}>
          {catalog.aggregates.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        {spec.aggregate.fn !== "count" && (
          <select aria-label="Aggregate field" value={spec.aggregate.field || numFields[0] || ""} onChange={(e) => set({ aggregate: { fn: spec.aggregate.fn, field: e.target.value } })} style={{ ..._field, flex: "0 0 140px" }}>
            {(spec.aggregate.fn === "sum" ? numFields : ds.fields.map((f) => f.name)).map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        )}
        {ds.attribution.length > 1 && <>
          <label style={{ ..._label, margin: 0, alignSelf: "center" }}>Scope</label>
          <select aria-label="Attribution" value={spec.attribution} onChange={(e) => set({ attribution: e.target.value })} style={{ ..._field, flex: "0 0 128px" }}>
            {ds.attribution.map((a) => <option key={a} value={a}>{a === "office" ? "per office" : "whole business"}</option>)}
          </select>
        </>}
      </div>

      <div style={{ fontFamily: FM, fontSize: 10.5, color: C.muted }}>{_specSummary(spec)}</div>

      {preview && (
        <div style={{ background: C.surface, border: `1px solid ${C.hair}`, borderRadius: 8, padding: "8px 10px" }}>
          {!preview.valid
            ? <div style={{ fontFamily: FB, fontSize: 12, color: C.poppy }}>Can’t run this: {preview.errors.join("; ")}</div>
            : <>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                  <span style={{ ..._label, margin: 0 }}>Preview · last {preview.weeks.length} weeks</span>
                  {preview.has_current && <span style={allMatch ? _pill(C.meadow, C.meadowInk) : _pill("#fbe3df", C.poppy)}>{allMatch ? "matches current ✓" : "differs from current"}</span>}
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 2, fontFamily: FM, fontSize: 11.5 }}>
                  <div style={{ display: "flex", color: C.muted }}>
                    <span style={{ flex: "1 1 auto" }}>week</span>
                    <span style={{ width: 70, textAlign: "right" }}>this spec</span>
                    {preview.has_current && <span style={{ width: 70, textAlign: "right" }}>current</span>}
                  </div>
                  {preview.weeks.map((w) => (
                    <div key={w.week_start} style={{ display: "flex", color: C.body }}>
                      <span style={{ flex: "1 1 auto", color: C.slate }}>{w.week_start}</span>
                      <span style={{ width: 70, textAlign: "right" }}>{cell(w.spec)}</span>
                      {preview.has_current && <span style={{ width: 70, textAlign: "right", color: w.match ? C.body : C.poppy }}>{cell(w.current)}</span>}
                    </div>
                  ))}
                </div>
              </>}
        </div>
      )}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button onClick={doPreview} disabled={state === "previewing"} style={{ ..._btn, color: C.ink }}>
          {state === "previewing" ? "Previewing…" : "Preview"}
        </button>
        <button onClick={save} disabled={state === "saving"} style={{ ..._btn,
          color: state === "saved" ? C.meadowInk : state === "error" ? C.poppy : C.ink,
          borderColor: state === "error" ? C.poppy : C.hair }}>
          {state === "saving" ? "Saving…" : state === "saved" ? "Saved ✓" : state === "error" ? "Retry" : "Save routing"}
        </button>
        {m.has_twin && <button onClick={() => { setSpec(m.default_spec); setPreview(null); }} style={{ ..._btn, color: C.slate }}>Load standard</button>}
        {m.routed && <button onClick={turnOff} style={{ ..._btn, color: C.poppy, borderColor: C.hair }}>Turn off routing</button>}
      </div>
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

const _icon = { border: "none", background: "none", cursor: "pointer", color: C.slate, fontSize: 15, lineHeight: 1, padding: "2px 5px" };

function OfficeRow({ g, canUp, canDown, onMove, onChanged }) {
  const [name, setName] = useState(g.name || "");
  const [owner, setOwner] = useState((g.owner && g.owner.name) || "");
  const [state, setState] = useState("idle");   // idle | saving | saved | error
  const [confirm, setConfirm] = useState(false);
  const src = g.owner && g.owner.photo_url ? fileUrl(g.owner.photo_url) : null;

  async function save() {
    setState("saving");
    try { await patchJSON(`/ulrg/group/${g.id}`, { name: name.trim(), owner_name: owner }); setState("saved"); onChanged && onChanged(); setTimeout(() => setState("idle"), 1400); }
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
  async function remove() {
    try { await patchJSON(`/ulrg/group/${g.id}`, { active: false }); onChanged && onChanged(); }
    catch (e) { setState("error"); }
  }

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
      <label style={{ cursor: "pointer", flex: "0 0 auto" }} title="Upload a headshot">
        {src
          ? <img src={src} alt="" style={{ width: 40, height: 40, borderRadius: 99, objectFit: "cover", border: `1px solid ${C.hair}` }} />
          : <span style={{ width: 40, height: 40, borderRadius: 99, display: "inline-flex", alignItems: "center", justifyContent: "center", background: C.mist, color: C.muted, fontFamily: FM, fontSize: 16 }}>{(owner[0] || name[0] || "·").toUpperCase()}</span>}
        <input type="file" accept="image/*" onChange={upload} style={{ display: "none" }} />
      </label>
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Office name" aria-label="Office name"
             style={{ ..._field, flex: "0 0 148px" }} />
      <input value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="Owner full name" aria-label="Owner name"
             style={{ ..._field, flex: "1 1 180px", minWidth: 140 }} />
      <button onClick={save} disabled={state === "saving"} style={{ ..._btn,
        borderColor: state === "error" ? C.poppy : C.hair,
        color: state === "saved" ? C.meadowInk : state === "error" ? C.poppy : C.slate }}>
        {state === "saving" ? "Saving…" : state === "saved" ? "Saved ✓" : state === "error" ? "Retry" : "Save"}
      </button>
      <button onClick={() => onMove(-1)} disabled={!canUp} title="Move up" aria-label={`Move ${g.name} up`} style={{ ..._icon, opacity: canUp ? 1 : .3 }}>↑</button>
      <button onClick={() => onMove(1)} disabled={!canDown} title="Move down" aria-label={`Move ${g.name} down`} style={{ ..._icon, opacity: canDown ? 1 : .3 }}>↓</button>
      {confirm
        ? <span style={{ display: "flex", gap: 4 }}>
            <button onClick={remove} aria-label={`Confirm remove ${g.name}`} style={{ ..._btn, padding: "4px 9px", fontSize: 11, color: C.poppy, borderColor: C.poppy }}>Remove</button>
            <button onClick={() => setConfirm(false)} style={{ ..._btn, padding: "4px 9px", fontSize: 11, color: C.muted }}>Cancel</button>
          </span>
        : <button onClick={() => setConfirm(true)} title="Remove office" aria-label={`Remove office ${g.name}`} style={{ ..._icon, fontSize: 17 }}>×</button>}
    </div>
  );
}
