/* Books · Intercompany — the pair ledger and the CFO's policy rules. Off-policy movement
   blocks the close until a person characterizes and ties it. */
import { useState } from "react";
import { T } from "../theme.js";
import { postJSON, patchJSON } from "../api";
import { useBooksIC } from "./useBooks.js";
import { Card, Eyebrow, Pill, StatePanel, EntityChip, CHAR_LABEL, font, usd } from "./ui.jsx";

const API = import.meta.env.VITE_API_BASE;
const STATUS_TONE = { tied: "good", auto_tied: "good", characterized: "warn",
  matched: "muted", unmatched: "bad", escalated: "bad" };
const STATUS_LABEL = { tied: "Tied", auto_tied: "Auto-tied", characterized: "Ready to tie",
  matched: "Matched", unmatched: "Unmatched", escalated: "Off-policy" };

export default function BooksIC({ isCFO = false }) {
  const { data, loading, error, retry } = useBooksIC();
  const [busy, setBusy] = useState(null);

  const act = async (key, fn) => { setBusy(key); try { if (API) await fn(); retry(); } finally { setBusy(null); } };

  return (
    <StatePanel loading={loading} error={error} retry={retry}>
      {data && (
        <div style={{ display: "grid", gap: 16 }}>
          <Card style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
            <div>
              <Eyebrow>Intercompany tie-out</Eyebrow>
              <div style={{ fontFamily: font.head, fontSize: 22, fontWeight: 700,
                            color: data.blocking?.open ? T.poppyText : T.meadowInk }}>
                {data.blocking?.open || 0} open · {usd(data.blocking?.amount || 0)}</div>
            </div>
            <Pill tone={data.blocking?.open ? "bad" : "good"}>
              {data.blocking?.open ? "Blocks month-end close" : "Nets to zero"}</Pill>
          </Card>

          <Card>
            <Eyebrow>Pairs</Eyebrow>
            {(data.pairs || []).length === 0
              ? <div style={{ fontFamily: font.body, fontSize: 13, color: T.muted, padding: "8px 0" }}>No intercompany movement this period.</div>
              : (data.pairs || []).map((p) => (
                <div key={p.id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                  gap: 12, padding: "11px 0", borderBottom: `1px solid ${T.line}`, flexWrap: "wrap" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 220 }}>
                    <EntityChip k={p.from} />
                    <span style={{ color: T.sprout }}>→</span>
                    <EntityChip k={p.to} />
                    <span style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted }}>{p.date}</span>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                    {p.characterization && <span style={{ fontFamily: font.body, fontSize: 12, color: T.tertiary }}>
                      {CHAR_LABEL[p.characterization] || p.characterization}</span>}
                    <Pill tone={STATUS_TONE[p.status] || "muted"}>{STATUS_LABEL[p.status] || p.status}</Pill>
                    <span style={{ fontFamily: font.head, fontSize: 15, fontWeight: 700, color: T.ink,
                                   minWidth: 84, textAlign: "right" }}>{usd(p.amount)}</span>
                    {p.status === "characterized" && (
                      <button disabled={busy === p.id} onClick={() => act(p.id, () => postJSON(`/books/ic/${p.id}/tie`, {}))}
                        style={{ fontFamily: font.body, fontSize: 12, fontWeight: 600, color: T.onDark,
                          background: T.evergreen, border: "none", borderRadius: 8, padding: "5px 11px", cursor: "pointer" }}>
                        Mark tied</button>
                    )}
                  </div>
                </div>
              ))}
          </Card>

          <Card>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
              <Eyebrow>Policy rules</Eyebrow>
              <span style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted }}>
                Matching movement auto-ties; everything else escalates.</span>
            </div>
            {(data.rules || []).map((r) => (
              <div key={r.id} style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
                gap: 12, padding: "11px 0", borderBottom: `1px solid ${T.line}`, flexWrap: "wrap" }}>
                <div style={{ minWidth: 240, flex: 1 }}>
                  <div style={{ fontFamily: font.body, fontSize: 13, fontWeight: 600, color: T.secondary }}>{r.label}</div>
                  <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted, marginTop: 2 }}>
                    {CHAR_LABEL[r.characterization] || r.characterization}
                    {r.monthly_cap != null && <> · cap {usd(r.monthly_cap)}/mo</>}
                    {(r.from || r.to) && <> · {r.from || "any"} → {r.to || "any"}</>}
                  </div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <Pill tone={r.active ? "good" : "muted"}>{r.active ? "Active" : "Inactive"}</Pill>
                  {isCFO && (
                    <button disabled={busy === r.id}
                      onClick={() => act(r.id, () => patchJSON(`/books/ic/rules/${r.id}`, { active: !r.active }))}
                      style={{ fontFamily: font.body, fontSize: 12, fontWeight: 600, color: T.slate,
                        background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 8,
                        padding: "5px 11px", cursor: "pointer" }}>
                      {r.active ? "Deactivate" : "Activate"}</button>
                  )}
                </div>
              </div>
            ))}
            <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted, marginTop: 12 }}>
              Rule amounts are placeholders until confirmed — inactive rules don't gate anything.</div>
          </Card>
        </div>
      )}
    </StatePanel>
  );
}
