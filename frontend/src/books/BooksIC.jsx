/* Books · Intercompany — the tie-out status, the due-to/due-from ledger, and the CFO's
   policy rules. Off-policy movement blocks the close until a person characterizes + ties. */
import { useState } from "react";
import { postJSON, patchJSON } from "../api";
import { useBooksIC } from "./useBooks.js";
import { Card, Eyebrow, StatePanel, EntityChip, CHAR_LABEL, DARK, font, usd, T } from "./ui.jsx";

const API = import.meta.env.VITE_API_BASE;
const STATUS = {
  tied: { dot: T.meadow, label: "Tied", policy: true }, auto_tied: { dot: T.meadow, label: "Auto-tied", policy: true },
  characterized: { dot: T.daffodil, label: "Ready to tie", policy: true }, matched: { dot: T.muted, label: "Matched", policy: false },
  unmatched: { dot: T.poppy, label: "Unmatched", policy: false }, escalated: { dot: T.poppy, label: "Off-policy", policy: false },
};
const st = (s) => STATUS[s] || STATUS.matched;

export default function BooksIC({ isCFO = false }) {
  const { data, loading, error, retry } = useBooksIC();
  const [tied, setTied] = useState(() => new Set());        // optimistic overrides
  const [ruleOverride, setRuleOverride] = useState({});
  const [busy, setBusy] = useState(null);

  const act = async (key, fn, after) => {
    setBusy(key); try { if (API) await fn(); after(); } finally { setBusy(null); }
  };
  const pairs = (data?.pairs || []).map((p) => tied.has(p.id) ? { ...p, status: "tied" } : p);
  const openCount = pairs.filter((p) => !st(p.status).policy).length;

  return (
    <StatePanel loading={loading} error={error} retry={retry}>
      {data && (
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <div style={{ borderRadius: 14, padding: "20px 24px", display: "flex", alignItems: "center",
            justifyContent: "space-between", flexWrap: "wrap", gap: 14, ...DARK }}>
            <div>
              <Eyebrow onDark>Intercompany tie-out</Eyebrow>
              <div style={{ fontFamily: font.head, fontSize: 22, fontWeight: 600, color: T.onDark, marginTop: 8 }}>
                {openCount ? `${openCount} unmatched ${openCount === 1 ? "item blocks" : "items block"} the close`
                           : "Every balance tied — nets to $0"}</div>
            </div>
            <span style={{ fontFamily: font.head, fontSize: 13, fontWeight: 700, color: openCount ? T.poppyText : T.meadowInk,
              background: openCount ? "rgba(250,128,105,0.16)" : "rgba(184,204,184,0.18)", borderRadius: 99, padding: "8px 18px" }}>
              {openCount ? "Action needed" : "Tied ✓"}</span>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.5fr) minmax(0,1fr)", gap: 18, alignItems: "start" }}>
            <Card>
              <Eyebrow>Due-to / due-from balances</Eyebrow>
              <div style={{ marginTop: 8 }}>
                {pairs.length === 0 && <div style={{ fontFamily: font.body, fontSize: 13, color: T.muted, padding: "10px 0" }}>No intercompany movement this period.</div>}
                {pairs.map((p, i) => {
                  const s = st(p.status);
                  return (
                    <div key={p.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 0",
                      borderTop: i ? `1px solid ${T.line}` : "none" }}>
                      <span style={{ width: 9, height: 9, borderRadius: 99, background: s.dot, flexShrink: 0 }} />
                      <div style={{ flex: 1 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                          <EntityChip k={p.from} />
                          <span style={{ fontFamily: font.body, fontSize: 12, color: T.muted }}>owes</span>
                          <EntityChip k={p.to} />
                        </div>
                        <div style={{ fontFamily: font.body, fontSize: 11.5, marginTop: 4,
                          color: s.policy ? T.muted : T.poppyText }}>
                          {p.date}{p.characterization ? ` · ${CHAR_LABEL[p.characterization] || p.characterization}` : ""} · {s.label}</div>
                      </div>
                      {p.status === "characterized" && (
                        <button disabled={busy === p.id} onClick={() => act(p.id,
                          () => postJSON(`/books/ic/${p.id}/tie`, {}), () => setTied((t) => new Set(t).add(p.id)))}
                          style={{ fontFamily: font.head, fontSize: 12, fontWeight: 600, color: T.white, background: T.evergreen,
                            border: "none", borderRadius: 8, padding: "6px 12px", cursor: "pointer", flexShrink: 0 }}>Mark tied</button>
                      )}
                      <span style={{ fontFamily: font.head, fontSize: 15, fontWeight: 700, color: T.ink,
                        fontVariantNumeric: "tabular-nums", minWidth: 74, textAlign: "right" }}>{usd(p.amount)}</span>
                    </div>
                  );
                })}
              </div>
            </Card>

            <Card>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                <Eyebrow>The policy</Eyebrow>
                <span style={{ fontFamily: font.body, fontSize: 11, color: T.muted }}>set by the CFO</span>
              </div>
              {(data.rules || []).map((r, i) => {
                const active = ruleOverride[r.id] ?? r.active;
                return (
                  <div key={r.id} style={{ display: "flex", gap: 11, padding: "11px 0",
                    borderTop: i ? `1px solid ${T.line}` : "none", alignItems: "flex-start" }}>
                    <span style={{ fontFamily: font.body, fontSize: 10, fontWeight: 700, textTransform: "uppercase",
                      letterSpacing: "0.05em", color: active ? T.meadowInk : T.muted,
                      background: active ? "rgba(97,131,94,0.1)" : T.parchment, borderRadius: 5, padding: "3px 8px",
                      flexShrink: 0, marginTop: 1 }}>{active ? "auto" : "off"}</span>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontFamily: font.body, fontSize: 12.5, fontWeight: 600, color: T.ink }}>{r.label}</div>
                      <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted, marginTop: 2 }}>
                        {CHAR_LABEL[r.characterization] || r.characterization}
                        {r.monthly_cap != null && ` · up to ${usd(r.monthly_cap)}/mo`}
                        {(r.from || r.to) && ` · ${r.from || "any"} → ${r.to || "any"}`}</div>
                    </div>
                    {isCFO && (
                      <button disabled={busy === r.id} onClick={() => act(r.id,
                        () => patchJSON(`/books/ic/rules/${r.id}`, { active: !active }),
                        () => setRuleOverride((o) => ({ ...o, [r.id]: !active })))}
                        style={{ fontFamily: font.body, fontSize: 11, fontWeight: 600, color: T.slate, background: T.parchment,
                          border: `1px solid ${T.line}`, borderRadius: 7, padding: "4px 9px", cursor: "pointer", flexShrink: 0 }}>
                        {active ? "Turn off" : "Turn on"}</button>
                    )}
                  </div>
                );
              })}
              <div style={{ display: "flex", gap: 11, padding: "11px 0", borderTop: `1px solid ${T.line}`, alignItems: "flex-start" }}>
                <span style={{ fontFamily: font.body, fontSize: 10, fontWeight: 700, textTransform: "uppercase",
                  letterSpacing: "0.05em", color: T.poppyText, background: "rgba(250,128,105,0.1)", borderRadius: 5,
                  padding: "3px 8px", flexShrink: 0, marginTop: 1 }}>stops</span>
                <div>
                  <div style={{ fontFamily: font.body, fontSize: 12.5, fontWeight: 600, color: T.ink }}>Everything else</div>
                  <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted, marginTop: 2 }}>
                    stops and escalates to the CFO — never guessed</div>
                </div>
              </div>
              <div style={{ fontFamily: font.body, fontSize: 11, color: T.muted, marginTop: 12, lineHeight: 1.55,
                borderTop: `1px solid ${T.line}`, paddingTop: 12 }}>
                Rule amounts are placeholders until confirmed — inactive rules don't gate anything.</div>
            </Card>
          </div>
        </div>
      )}
    </StatePanel>
  );
}
