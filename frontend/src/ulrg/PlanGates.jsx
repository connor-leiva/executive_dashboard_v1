/* The plan's dated milestones, under a board's grid: a horizontal timeline, each gate done, due or
   missed with its owner. Status comes from the server. Owner/admin can mark a gate done; the embed
   (canEdit false) is read-only. */
import { useState } from "react";
import { patchJSON } from "../api.js";
import { T, NUM, LBL, FONT } from "./l10tokens.jsx";

const TONE = {
  done: { fg: T.good, bg: T.goodBg, label: "Done" },
  due: { fg: T.inkSoft, bg: T.lineSoft, label: "Due" },
  missed: { fg: T.bad, bg: T.badBg, label: "Missed" },
};

function shortDate(iso) {
  const d = new Date(`${iso}T12:00:00`);
  const sameYear = d.getFullYear() === new Date().getFullYear();
  return d.toLocaleDateString(undefined, sameYear ? { month: "short", day: "numeric" } : { month: "short", day: "numeric", year: "numeric" });
}

export default function PlanGates({ gates, canEdit, onChanged }) {
  const [busy, setBusy] = useState(null);
  const [err, setErr] = useState(null);

  async function toggle(g) {
    setBusy(g.id); setErr(null);
    try {
      await patchJSON(`/ulrg/gate/${g.id}`, { done: g.status !== "done" });
      onChanged && onChanged();
    } catch (e) { setErr(g.id); }
    setBusy(null);
  }

  return (
    <div style={{ marginTop: 18, background: T.paper, borderRadius: 12, border: `1px solid ${T.line}`, fontFamily: FONT, overflow: "hidden" }}>
      <div style={{ padding: "14px 20px", borderBottom: `1px solid ${T.line}`, display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h3 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: T.ink }}>Plan gates</h3>
        <span style={{ fontSize: 12, color: T.muted }}>
          {gates.filter((g) => g.status === "done").length} of {gates.length} done
          {gates.some((g) => g.status === "missed") ? ` · ${gates.filter((g) => g.status === "missed").length} missed` : ""}
        </span>
      </div>
      <div style={{ display: "flex", overflowX: "auto", padding: "16px 12px 18px" }}>
        {gates.map((g) => {
          const t = TONE[g.status] || TONE.due;
          return (
            <div key={g.id} style={{ flex: "1 0 188px", maxWidth: 260, padding: "0 8px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 8 }}>
                <span style={{ width: 10, height: 10, borderRadius: "50%", flexShrink: 0, background: t.fg }} />
                <span style={{ flex: 1, height: 1, background: T.line }} />
              </div>
              <div style={{ ...NUM, fontSize: 12.5, fontWeight: 600, color: T.ink }}>{shortDate(g.due)}</div>
              <div style={{ fontSize: 12.5, color: T.inkSoft, lineHeight: 1.4, marginTop: 4 }}>{g.label}</div>
              <div style={{ ...LBL, marginTop: 7 }}>{g.owner || "Unassigned"}</div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
                <span style={{ fontSize: 10.5, fontWeight: 600, color: t.fg, background: t.bg, borderRadius: 4, padding: "2px 7px" }}>{t.label}</span>
                {canEdit && (
                  <button onClick={() => toggle(g)} disabled={busy === g.id} className="l10-btn"
                    style={{ font: "inherit", fontSize: 11, color: err === g.id ? T.bad : T.muted, background: "none", border: "none", padding: 0, cursor: "pointer", textDecoration: "underline" }}>
                    {busy === g.id ? "Saving…" : err === g.id ? "Try again" : g.status === "done" ? "Undo" : "Mark done"}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
