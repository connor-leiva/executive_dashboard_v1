import { useEffect, useMemo, useState } from "react";
import { T, usd } from "./theme.js";
import { getJSON } from "./api.js";
import sampleForum from "./sampleForum.js";

const API_BASE = import.meta.env.VITE_API_BASE;

/* The roster drawer is deliberately unlike the other audit drawers: wider, and laid
   out as a clean, grouped "retelling" of the CRM membership record rather than a flat
   list of amounts. Fed by the forum_roster drill (rows + summary). */

const PLAN = {
  monthly: { label: "Monthly", fg: T.meadowInk, bg: T.meadowBg },
  quarterly: { label: "Quarterly", fg: T.secondary, bg: T.sprout },
  pif: { label: "Paid in full", fg: T.teal, bg: T.mist },
  installments: { label: "Installments", fg: T.amber, bg: T.daffodilBg },
};

const compact = (n) => {
  const a = Math.abs(n || 0);
  if (a >= 1e6) return "$" + (n / 1e6).toFixed(a >= 1e7 ? 0 : 2).replace(/\.?0+$/, "") + "M";
  if (a >= 1e3) return "$" + Math.round(n / 1e3) + "K";
  return usd(n || 0);
};

function fmtDate(v) {
  if (!v) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(v);
  if (!m) return v;                        // already a label (e.g. "Sep")
  return new Date(+m[1], +m[2] - 1, +m[3])
    .toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function initials(name) {
  const p = (name || "").trim().split(/\s+/);
  return ((p[0]?.[0] || "") + (p[1]?.[0] || "")).toUpperCase() || "·";
}

function Seg({ seg }) {
  const f = seg === "F";
  return (
    <span style={{
      fontFamily: "Poppins,sans-serif", fontSize: 9, fontWeight: 700, letterSpacing: "0.06em",
      color: f ? T.evergreen : T.teal, background: f ? T.daffodil : T.mist,
      borderRadius: 4, padding: "2px 6px", textTransform: "uppercase", flexShrink: 0,
    }}>{f ? "Forum" : "IC"}</span>
  );
}

function Plan({ payment }) {
  const p = PLAN[payment];
  if (!p) return <span style={{ color: T.muted, fontSize: 12 }}>—</span>;
  return (
    <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, fontWeight: 600, color: p.fg,
      background: p.bg, borderRadius: 5, padding: "3px 8px", whiteSpace: "nowrap" }}>{p.label}</span>
  );
}

/* one stat cell in the summary band */
function Stat({ label, children }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 10, fontWeight: 700, letterSpacing: "0.08em",
        textTransform: "uppercase", color: T.muted, marginBottom: 4 }}>{label}</div>
      <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 15, fontWeight: 600, color: T.ink,
        display: "flex", alignItems: "baseline", gap: 6, flexWrap: "wrap" }}>{children}</div>
    </div>
  );
}

export default function RosterDrawer({ business, period, onClose }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(false);
  const [prog, setProg] = useState("all");     // all | F | IC
  const [qtext, setQ] = useState("");

  useEffect(() => {
    setD(null); setErr(false); setProg("all"); setQ("");
    if (!API_BASE) { setD(sampleForum.roster_detail); return; }
    const q = `/metrics/forum_roster/detail?period=${period}`
      + (business ? `&business=${encodeURIComponent(business)}` : "");
    getJSON(q).then(setD).catch(() => setErr(true));
  }, [business, period]);

  const rows = d?.rows || [];
  const sm = d?.summary || {};
  const mix = sm.payment_mix || {};
  const filtered = useMemo(() => {
    const t = qtext.trim().toLowerCase();
    return rows.filter((r) => !t || (r.name || "").toLowerCase().includes(t) || (r.brokerage || "").toLowerCase().includes(t));
  }, [rows, qtext]);

  // Admins are staff seats — shown as their own group, kept out of the Forum/IC lists.
  const isAdmin = (r) => r.kind === "admin";
  const inScope = filtered.filter((r) =>
    prog === "all" ? true : prog === "ADMIN" ? isAdmin(r) : (r.seg === prog && !isAdmin(r)));
  const groups = prog === "ADMIN" ? ["ADMIN"] : prog === "IC" ? ["IC"] : prog === "F" ? ["F"] : ["F", "IC", "ADMIN"];
  const inGroup = (r, g) => (g === "ADMIN" ? isAdmin(r) : (r.seg === g && !isAdmin(r)));
  const GROUP_LABEL = { F: "The Forum", IC: "Inner Circle", ADMIN: "Admins" };
  const tab = (k, on) => ({
    fontFamily: "Inter,sans-serif", fontSize: 12, fontWeight: 600, cursor: "pointer", border: "none",
    borderRadius: 7, padding: "6px 12px", background: on ? T.white : "transparent",
    color: on ? T.ink : T.muted, boxShadow: on ? "0 1px 3px rgba(0,46,44,.12)" : "none",
  });
  const th = { fontFamily: "Inter,sans-serif", fontSize: 10, fontWeight: 700, letterSpacing: "0.06em",
    textTransform: "uppercase", color: T.muted, textAlign: "left", padding: "0 14px 8px", whiteSpace: "nowrap" };
  const subset = sm.total != null && rows.length < sm.total;

  return (
    <>
      <style>{`
        @keyframes rosterIn { from { transform: translateX(24px); opacity: 0; } to { transform: none; opacity: 1; } }
        .roster-aside { animation: rosterIn .32s cubic-bezier(.22,1,.36,1) both; }
        .roster-row:hover { background: ${T.parchment}; }
        @media (prefers-reduced-motion: reduce) { .roster-aside { animation: none; } }
      `}</style>
      <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,46,44,0.34)", zIndex: 40 }} />
      <aside className="roster-aside" style={{
        position: "fixed", top: 0, right: 0, bottom: 0, width: "min(1040px, 96vw)", background: T.page,
        borderLeft: `1px solid ${T.line}`, boxShadow: "-24px 0 60px rgba(0,46,44,.20)", zIndex: 41,
        display: "flex", flexDirection: "column", fontFamily: "Inter,sans-serif",
      }}>
        {/* Header band — title + live composition summary (the CRM at a glance) */}
        <div style={{ padding: "18px 24px 16px", borderBottom: `1px solid ${T.line}`, background: T.white }}>
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16 }}>
            <div>
              <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, fontWeight: 600, color: T.slate,
                background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 5, padding: "2px 7px" }}>Go High Level</span>
              <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 19, fontWeight: 600, color: T.ink, marginTop: 8 }}>The Forum · Roster</div>
            </div>
            <button onClick={onClose} aria-label="Close" style={{ fontSize: 17, color: T.slate, background: "transparent", border: "none", cursor: "pointer", lineHeight: 1 }}>✕</button>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 18, marginTop: 16 }}>
            <Stat label="Members"><span style={{ fontSize: 22 }}>{sm.total ?? rows.length}</span></Stat>
            <Stat label="Program">{sm.forum ?? 0} Forum<span style={{ color: T.muted, fontWeight: 500 }}>·</span>{sm.inner_circle ?? 0} IC</Stat>
            <Stat label="Composition">
              <span style={{ fontSize: 13 }}>{sm.primary ?? 0} primary<span style={{ color: T.muted }}> · </span>{sm.add_on ?? 0} add-on{sm.admin ? <><span style={{ color: T.muted }}> · </span>{sm.admin} admin</> : null}{sm.unspecified ? <span style={{ color: T.muted }}> · {sm.unspecified} unset</span> : null}</span>
            </Stat>
            <Stat label="Payment mix">
              <span style={{ fontSize: 13 }}>{mix.monthly || 0} Monthly<span style={{ color: T.muted }}> · </span>{mix.quarterly || 0} Qtr<span style={{ color: T.muted }}> · </span>{mix.pif || 0} PIF<span style={{ color: T.muted }}> · </span>{mix.installments || 0} Inst</span>
            </Stat>
            <Stat label="Membership value">{compact(sm.book)}</Stat>
          </div>
        </div>

        {/* Controls — program tabs + quick find */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 24px", borderBottom: `1px solid ${T.line}`, flexWrap: "wrap" }}>
          <div style={{ display: "inline-flex", gap: 3, background: T.parchment, borderRadius: 9, padding: 3 }}>
            {[["all", "All"], ["F", "The Forum"], ["IC", "Inner Circle"], ...(sm.admin ? [["ADMIN", "Admins"]] : [])].map(([k, label]) => (
              <button key={k} onClick={() => setProg(k)} style={tab(k, prog === k)}>{label}</button>
            ))}
          </div>
          <input value={qtext} onChange={(e) => setQ(e.target.value)} placeholder="Find a member or brokerage…"
            style={{ flex: 1, minWidth: 160, fontFamily: "Inter,sans-serif", fontSize: 13, color: T.ink,
              background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 12px", outline: "none" }} />
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted }}>
            {inScope.length} shown{subset ? ` · sample of ${sm.total}` : ""}
          </span>
        </div>

        {/* Roster table — grouped by program. minWidth:0 lets this flex child shrink
            below the table's 720px min-width so the overflow-x wrapper actually clips
            (and horizontally scrolls) on narrow viewports instead of pushing the page. */}
        <div style={{ flex: 1, overflowY: "auto", minWidth: 0 }}>
          {err ? (
            <div style={{ padding: 24, color: T.muted, fontSize: 13 }}>Couldn't load the roster.</div>
          ) : !d ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 8, padding: 24 }}>
              {[0, 1, 2, 3, 4, 5].map((i) => <span key={i} className="cc-skel" style={{ height: 46, borderRadius: 8 }} />)}
            </div>
          ) : filtered.length === 0 ? (
            <div style={{ padding: 24, color: T.muted, fontSize: 13 }}>No members match.</div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", minWidth: 720, borderCollapse: "collapse" }}>
                <thead>
                  <tr>
                    <th style={{ ...th, paddingLeft: 24 }}>Member</th>
                    <th style={th}>Plan</th>
                    <th style={{ ...th, textAlign: "right" }}>Value</th>
                    <th style={th}>Enrolled</th>
                    <th style={th}>Renews</th>
                    <th style={{ ...th, paddingRight: 24 }}></th>
                  </tr>
                </thead>
                {groups.map((g) => {
                  const gr = inScope.filter((r) => inGroup(r, g));
                  if (!gr.length) return null;
                  return (
                    <tbody key={g}>
                      <tr>
                        <td colSpan={6} style={{ padding: "12px 24px 6px", fontFamily: "Poppins,sans-serif",
                          fontSize: 12, fontWeight: 600, color: T.slate, background: T.page }}>
                          {GROUP_LABEL[g]}
                          <span style={{ color: T.muted, fontWeight: 500 }}> · {gr.length}</span>
                        </td>
                      </tr>
                      {gr.map((r) => (
                        <tr key={r.id} className="roster-row" style={{ borderTop: `1px solid ${T.line}` }}>
                          <td style={{ padding: "11px 14px 11px 24px", minWidth: 220 }}>
                            <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
                              <span style={{ width: 32, height: 32, borderRadius: 99, flexShrink: 0, background: T.meadowBg,
                                color: T.meadowInk, fontFamily: "Poppins,sans-serif", fontSize: 11, fontWeight: 700,
                                display: "flex", alignItems: "center", justifyContent: "center" }}>{initials(r.name)}</span>
                              <div style={{ minWidth: 0 }}>
                                <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                                  <span style={{ fontSize: 13.5, fontWeight: 600, color: T.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.name}</span>
                                  <Seg seg={r.seg} />
                                  {r.kind === "add_on" && (
                                    <span style={{ fontFamily: "Inter,sans-serif", fontSize: 9.5, fontWeight: 700, letterSpacing: "0.04em",
                                      color: T.slate, background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 4, padding: "1px 5px", textTransform: "uppercase" }}>Add-on</span>
                                  )}
                                  {r.kind === "admin" && (
                                    <span style={{ fontFamily: "Inter,sans-serif", fontSize: 9.5, fontWeight: 700, letterSpacing: "0.04em",
                                      color: T.teal, background: T.mist, borderRadius: 4, padding: "1px 5px", textTransform: "uppercase" }}>Admin</span>
                                  )}
                                </div>
                                <div style={{ fontSize: 11, color: T.muted, marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                                  {[r.brokerage, r.stripe_account].filter(Boolean).join(" · ") || "—"}
                                </div>
                              </div>
                            </div>
                          </td>
                          <td style={{ padding: "11px 14px" }}><Plan payment={r.payment} /></td>
                          <td style={{ padding: "11px 14px", textAlign: "right", fontFamily: "Poppins,sans-serif",
                            fontSize: 13, fontWeight: 600, color: r.amount ? T.ink : T.muted, fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
                            {r.amount ? usd(r.amount) : "—"}</td>
                          <td style={{ padding: "11px 14px", fontSize: 12.5, color: T.slate, whiteSpace: "nowrap" }}>{fmtDate(r.enrolled)}</td>
                          <td style={{ padding: "11px 14px", fontSize: 12.5, color: T.slate, whiteSpace: "nowrap" }}>{fmtDate(r.renews)}</td>
                          <td style={{ padding: "11px 24px 11px 14px", textAlign: "right" }}>
                            {r.source_url && <a href={r.source_url} target="_blank" rel="noreferrer" title="Open in Go High Level" style={{ fontSize: 14, color: T.teal, textDecoration: "none" }}>↗</a>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  );
                })}
              </table>
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
