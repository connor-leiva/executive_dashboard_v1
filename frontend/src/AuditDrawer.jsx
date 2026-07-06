import { useEffect, useState } from "react";
import { T, usd } from "./theme.js";
import { getJSON } from "./api.js";

const API_BASE = import.meta.env.VITE_API_BASE;

/* Forum segment chip — daffodil Forum / mist IC (never poppy). */
function SegChip({ seg }) {
  if (seg === "BC") return (
    <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 9, fontWeight: 700, letterSpacing: "0.06em",
      color: T.evergreen, background: T.petal, borderRadius: 4, padding: "2px 6px", textTransform: "uppercase", flexShrink: 0 }}>BC</span>
  );
  if (seg === "MTG") return (
    <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 9, fontWeight: 700, letterSpacing: "0.06em",
      color: T.teal, background: T.mist, borderRadius: 4, padding: "2px 6px", textTransform: "uppercase", flexShrink: 0 }}>Loan</span>
  );
  const f = seg === "F" || seg === "Forum";
  return (
    <span style={{
      fontFamily: "Poppins,sans-serif", fontSize: 9, fontWeight: 700, letterSpacing: "0.06em",
      color: f ? T.evergreen : T.teal, background: f ? T.daffodil : T.mist,
      borderRadius: 4, padding: "2px 6px", textTransform: "uppercase", flexShrink: 0,
    }}>{f ? "Forum" : "IC"}</span>
  );
}

/* Right-side drawer showing the records behind a KPI (the trust layer). */
export default function AuditDrawer({ metricKey, business, period, onClose }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    if (!metricKey) return;
    setD(null);
    setErr(false);
    if (!API_BASE) return; // sample mode → show the note below
    const q = `/metrics/${metricKey}/detail?period=${period}${business ? `&business=${encodeURIComponent(business)}` : ""}`;
    getJSON(q).then(setD).catch(() => setErr(true));
  }, [metricKey, business, period]);

  if (!metricKey) return null;

  const title = d?.label || metricKey.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  const muted = { fontFamily: "Inter,sans-serif", fontSize: 13, color: T.muted, lineHeight: 1.6 };
  const amountFor = (r) =>
    metricKey === "gci" && r.gci != null ? usd(r.gci)
      : r.sale_price != null ? usd(r.sale_price)
        : r.gci != null ? usd(r.gci)
          : r.status;

  return (
    <>
      <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,46,44,0.30)", zIndex: 40 }} />
      <aside style={{
        position: "fixed", top: 0, right: 0, bottom: 0, width: "min(460px, 92vw)", background: T.white,
        borderLeft: `1px solid ${T.line}`, boxShadow: "-16px 0 44px rgba(0,46,44,.16)", zIndex: 41,
        display: "flex", flexDirection: "column", fontFamily: "Inter,sans-serif",
      }}>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", padding: "18px 20px", borderBottom: `1px solid ${T.line}` }}>
          <div>
            {d?.source && (
              <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, fontWeight: 600, color: T.slate, background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 5, padding: "2px 7px" }}>{d.source}</span>
            )}
            <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 17, fontWeight: 600, color: T.ink, marginTop: 8 }}>{title}</div>
          </div>
          <button onClick={onClose} aria-label="Close" style={{ fontSize: 16, color: T.slate, background: "transparent", border: "none", cursor: "pointer", lineHeight: 1 }}>✕</button>
        </div>

        <div style={{ padding: 20, overflowY: "auto" }}>
          {!API_BASE ? (
            <div style={muted}>Drill-down runs on the live app — it lists the exact records behind this number with links into the source system.</div>
          ) : err ? (
            <div style={muted}>Couldn't load the details.</div>
          ) : !d ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {[0, 1, 2, 3].map((i) => <span key={i} className="cc-skel" style={{ height: 34, borderRadius: 8 }} />)}
            </div>
          ) : (
            <>
              <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.slate, lineHeight: 1.55 }}>{d.computed_as}</div>
              {d.report_url && (
                <a href={d.report_url} target="_blank" rel="noreferrer" style={{ display: "inline-block", marginTop: 12, fontSize: 12.5, fontWeight: 600, color: T.teal, textDecoration: "none" }}>Open the P&amp;L in QuickBooks ↗</a>
              )}
              {d.count != null && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted, margin: "16px 0 4px" }}>{d.count} record{d.count === 1 ? "" : "s"}</div>}
              {d.rows.length === 0 && !d.report_url && <div style={{ ...muted, marginTop: 14 }}>No records for this period.</div>}
              <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
                {d.rows.map((r) => {
                  // Forum rows carry {seg, l2, r1, r2, tone}; other drills keep the
                  // financial shape. tone:"watch" → daffodil dot + amber value (never red).
                  const forum = r.seg !== undefined || r.l2 !== undefined || r.r1 !== undefined;
                  const watch = r.tone === "watch";
                  return (
                    <li key={r.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 0", borderTop: `1px solid ${T.line}` }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <span style={{ fontSize: 13, color: T.ink, fontWeight: forum ? 600 : 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.name}</span>
                          {r.seg && <SegChip seg={r.seg} />}
                          {watch && <span aria-hidden style={{ width: 6, height: 6, borderRadius: 99, background: T.daffodil, border: `1.5px solid ${T.amber}`, flexShrink: 0 }} />}
                        </div>
                        {forum
                          ? (r.l2 && <div style={{ fontSize: 11, color: T.muted, marginTop: 2 }}>{r.l2}</div>)
                          : ((r.close_date || r.address || r.side || r.company_dollar != null) && <div style={{ fontSize: 11, color: T.muted }}>{[r.close_date, r.address, r.side, r.company_dollar != null ? `net ${usd(r.company_dollar)}` : null].filter(Boolean).join(" · ")}</div>)}
                      </div>
                      <div style={{ textAlign: "right", flexShrink: 0 }}>
                        <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600, color: watch ? T.amber : T.ink, fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>{forum ? r.r1 : amountFor(r)}</div>
                        {forum && r.r2 && <div style={{ fontSize: 10.5, color: T.muted, marginTop: 2 }}>{r.r2}</div>}
                      </div>
                      {r.source_url && <a href={r.source_url} target="_blank" rel="noreferrer" title="Open in the source system" style={{ fontSize: 14, color: T.teal, textDecoration: "none" }}>↗</a>}
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </div>
      </aside>
    </>
  );
}
