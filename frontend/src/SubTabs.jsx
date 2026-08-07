import { T } from "./theme.js";

/* One shared sub-tab switcher (SPEC-ulrg-scorecard 1.1) — used by beCollective (Overview |
   Launch) and ULRG (Overview | Scorecard | teams…). `tabs` items: { k, label, sub?, unassigned? }.
   The optional `sub` line reads the leader; `unassigned` tints it as a prompt. */
export default function SubTabs({ tabs, active, onChange }) {
  const items = (tabs || []).filter(Boolean);
  if (items.length < 2) return null;                   // nothing to switch to
  return (
    <div style={{ display: "flex", gap: 4, borderBottom: `1px solid ${T.line}`, marginBottom: 18, flexWrap: "wrap" }}>
      {items.map((t) => {
        const on = active === t.k;
        return (
          <button key={t.k} onClick={() => onChange(t.k)} style={{
            fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600, textAlign: "left", lineHeight: 1.2,
            color: on ? T.ink : T.muted, background: "transparent", border: "none",
            borderBottom: on ? `2.5px solid ${T.meadow}` : "2.5px solid transparent",
            padding: t.sub ? "7px 15px 9px" : "9px 15px 11px", cursor: "pointer", marginBottom: -1 }}>
            <div>{t.label}</div>
            {t.sub && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 10, fontWeight: 500,
              color: t.unassigned ? T.daffodilText : T.muted, marginTop: 1 }}>{t.sub}</div>}
          </button>
        );
      })}
    </div>
  );
}
