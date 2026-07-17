/* Binder · Rules — the jurisdiction-rules admin view (SPEC Part 4 / 9.5). Owner/admin only.
   Shows each rule's derivation + freshness; stale rules (older than the window) are flagged so
   a changed rule — BOI is the cautionary tale — resurfaces rather than silently misfiring.
   "Mark verified" stamps last_verified=today. theme.js tokens + Brand.jsx icons. */
import { useState } from "react";
import { T } from "./theme.js";
import { Icon } from "./Brand.jsx";
import { useBinderRules } from "./useBinderRules.js";
import { postJSON } from "./api.js";

function Card({ children, style }) {
  return <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 14, padding: 20, ...style }}>{children}</div>;
}

function RuleRow({ r, first, onVerify, busy }) {
  const scope = [r.jurisdiction, r.entity_type].filter(Boolean).join(" · ") || "any";
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 12, padding: "13px 16px",
      borderTop: first ? "none" : `1px solid ${T.line}`, background: r.stale ? T.daffodilBg : T.white }}>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13.5, fontWeight: 600, color: T.ink }}>{r.kind_label}</span>
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate }}>{scope}</span>
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, color: T.muted, background: T.parchment, borderRadius: 5, padding: "2px 7px" }}>{r.derivation}</span>
          {r.stale && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, fontWeight: 700, color: T.daffodilText }}>needs re-verify</span>}
        </div>
        {r.source_note && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate, marginTop: 4, lineHeight: 1.5 }}>{r.source_note}</div>}
      </div>
      <div style={{ textAlign: "right", flexShrink: 0 }}>
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: r.stale ? T.daffodilText : T.muted }}>
          verified {r.last_verified || "never"}
        </div>
        <button onClick={() => onVerify(r)} disabled={busy} className="cc-nav" style={{
          marginTop: 6, fontFamily: "Poppins,sans-serif", fontSize: 11.5, fontWeight: 600, color: T.slate,
          background: T.white, border: `1px solid ${T.line}`, borderRadius: 7, padding: "5px 11px",
          cursor: busy ? "default" : "pointer" }}>Mark verified</button>
      </div>
    </div>
  );
}

export default function BinderRules() {
  const { data, error, loading, usingSample, reload } = useBinderRules();
  const [busy, setBusy] = useState(false);

  async function verify(r) {
    if (usingSample) return;
    setBusy(true);
    try { await postJSON(`/binder/rules/${r.id}/verify`); reload(); } catch { /* surfaced on next load */ }
    finally { setBusy(false); }
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap", marginBottom: 16 }}>
        <span style={{ width: 5, height: 28, borderRadius: 3, background: T.teal }} />
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 22, fontWeight: 600, color: T.ink }}>
          Binder <span style={{ color: T.muted, fontWeight: 500 }}>/ Rules</span></span>
        <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.muted }}>
          how filing dates are derived, and when each rule was last checked
        </span>
      </div>

      {loading && <Card style={{ color: T.muted, fontFamily: "Inter,sans-serif", fontSize: 13 }}>Loading rules…</Card>}
      {error && !data && (
        <Card style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Icon name="warning" size={16} color={T.poppyText} />
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 13, color: T.slate, flex: 1 }}>Could not load the rules.</span>
          <button onClick={reload} className="cc-nav" style={{ fontFamily: "Poppins,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.slate, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 14px", cursor: "pointer" }}>Retry</button>
        </Card>
      )}

      {data && (
        <>
          {data.stale_count > 0 && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, fontFamily: "Inter,sans-serif",
              fontSize: 12, color: T.daffodilText, background: T.daffodilBg, border: `1px solid ${T.line}`, borderRadius: 10, padding: "9px 14px" }}>
              <Icon name="warning" size={14} color={T.daffodilText} />
              {data.stale_count} rule(s) not verified in over {data.stale_after_months} months — re-check before relying on them.
            </div>
          )}
          <Card style={{ padding: 0, overflow: "hidden" }}>
            {data.rules.map((r, i) => <RuleRow key={r.id} r={r} first={i === 0} onVerify={verify} busy={busy} />)}
          </Card>
        </>
      )}
    </div>
  );
}
