/* Scorecard settings (owner/admin) — self-service office config. Phase A: edit each office owner's
   full name and upload a headshot. Periods + per-period goals land here next. */
import { useState } from "react";
import { patchJSON, uploadFile, fileUrl } from "../api.js";
import { C, FD, FB, FM } from "./scorecardMath.js";

export default function ScorecardSettings({ groups, onClose, onChanged }) {
  return (
    <div style={{ background: C.surface, border: `1px solid ${C.hair}`, borderRadius: 12, padding: "16px 18px", marginBottom: 18 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 12 }}>
        <span style={{ fontFamily: FD, fontSize: 15, fontWeight: 600, color: C.ink }}>Scorecard settings · offices</span>
        <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", fontFamily: FM, fontSize: 12, color: C.slate }}>Done</button>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {(groups || []).map((g) => <OfficeRow key={g.id} g={g} onChanged={onChanged} />)}
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
