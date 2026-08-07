import { useEffect, useMemo, useState } from "react";
import { T, usd } from "./theme.js";
import { getJSON } from "./api.js";
import sampleForum from "./sampleForum.js";
import sampleBecollective from "./sampleBecollective.js";
import sampleEdge from "./sampleEdge.js";

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
  if (seg !== "F" && seg !== "IC") return null;   // programs without an F/IC split (beCollective)
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

/* a payment cell — amount over date (last / next payment). The amount links into the
   source system (Stripe charge / GHL subscription / renewal) when a url is available. */
function PayCell({ p }) {
  if (!p || (!p.amount && !p.date)) return <span style={{ color: T.muted }}>—</span>;
  const amt = p.amount ? usd(p.amount) : "—";
  const amtStyle = { fontFamily: "Poppins,sans-serif", fontSize: 12.5, fontWeight: 600, fontVariantNumeric: "tabular-nums" };
  return (
    <div style={{ whiteSpace: "nowrap" }}>
      {p.url
        ? <a href={p.url} target="_blank" rel="noreferrer" title="Open in the source system"
            style={{ ...amtStyle, color: T.teal, textDecoration: "none" }}>{amt} ↗</a>
        : <span style={{ ...amtStyle, color: T.ink }}>{amt}</span>}
      <div style={{ fontSize: 10.5, color: T.muted }}>{fmtDate(p.date)}</div>
    </div>
  );
}

/* ── header filtering: funnel-popover, multi-select, chips (spec §4.7) ──────
   Each header is a label + a funnel button; the funnel opens a fixed-positioned
   popover (never clipped by table overflow) with sort + a column-typed filter.
   No native <select> — custom checkboxes / radios. */
const PLAN_DOT = { pif: T.evergreen, monthly: T.meadow, quarterly: T.sprout, installments: T.mistDeep };
const PLAN_SHORT = { pif: "PIF", monthly: "Monthly", quarterly: "Quarterly", installments: "Installments" };
const PLAN_ORD = { monthly: 0, quarterly: 1, installments: 2, pif: 3 };
const RCOLS = [
  { key: "member", label: "Member", type: "text" },
  { key: "plan", label: "Plan", type: "set", opts: [["pif", "PIF"], ["monthly", "Monthly"], ["quarterly", "Quarterly"], ["installments", "Installments"]] },
  { key: "value", label: "Value", type: "bucket", align: "right", opts: [["u10", "Under $10k"], ["mid", "$10k – $25k"], ["o25", "$25k or more"]] },
  { key: "last", label: "Last payment", type: "date", dir: "past" },
  { key: "next", label: "Next payment", type: "date", dir: "future" },
  { key: "enrolled", label: "Enrolled", type: "date", dir: "past" },
  { key: "renews", label: "Renews", type: "date", dir: "future" },
];
const DATE_OPTS = {
  future: [["30", "Next 30 days"], ["90", "Next 90 days"], ["overdue", "Overdue"], ["none", "No date"]],
  past: [["30", "Last 30 days"], ["90", "Last 90 days"], ["year", "This year"], ["none", "No date"]],
};
const isISO = (v) => /^\d{4}-\d{2}-\d{2}/.test(v || "");
const _iso = (d) => d.toISOString().slice(0, 10);
const shiftISO = (days) => { const d = new Date(); d.setDate(d.getDate() + days); return _iso(d); };
const bucketOf = (v) => ((v || 0) < 10000 ? "u10" : (v || 0) < 25000 ? "mid" : "o25");
const dateOf = (r, key) => key === "last" ? (r.last_payment && r.last_payment.date)
  : key === "next" ? (r.next_payment && r.next_payment.date) : key === "enrolled" ? r.enrolled : r.renews;
const sortVal = (r, key) => key === "member" ? (r.name || "").toLowerCase()
  : key === "plan" ? (PLAN_ORD[r.payment] ?? 9) : key === "value" ? (r.amount || 0) : (dateOf(r, key) || "");
const filterActive = (col, filters) => {
  const f = filters[col.key];
  return (col.type === "set" || col.type === "bucket") ? (f && f.length > 0) : !!f;
};
function passDate(iso, f, dir) {
  const d = isISO(iso) ? iso.slice(0, 10) : null;
  if (f === "none") return !d;
  if (!d) return false;
  const t = _iso(new Date());
  if (f === "year") return d.slice(0, 4) === t.slice(0, 4);
  if (f === "overdue") return d < t;
  if (dir === "future") return f === "30" ? (d >= t && d <= shiftISO(30)) : (d >= t && d <= shiftISO(90));
  return f === "30" ? (d <= t && d >= shiftISO(-30)) : (d <= t && d >= shiftISO(-90));
}
const linkBtn = { fontFamily: "Inter,sans-serif", fontSize: 10.5, fontWeight: 600, color: T.meadow, background: "transparent", border: "none", cursor: "pointer", padding: "2px 4px" };

function DrIc({ name, size = 13, color = "currentColor", sw = 1.8 }) {
  const p = {
    funnel: <path d="M3 4.5h18l-7 8.2V19l-4 2v-8.3l-7-8.2Z" />,
    check: <polyline points="20 6 9 17 4 12" />,
    close: <><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></>,
    search: <><circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.5" y2="16.5" /></>,
  }[name];
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" style={{ display: "block", flexShrink: 0 }}>{p}</svg>;
}
function Checkbox({ on, onClick, label, dot }) {
  return (
    <button onClick={onClick} className="roster-opt" style={{ display: "flex", alignItems: "center", gap: 9, width: "100%", padding: "6px 8px", background: "transparent", border: "none", cursor: "pointer", borderRadius: 7, textAlign: "left" }}>
      <span style={{ width: 16, height: 16, borderRadius: 5, border: `1.5px solid ${on ? T.meadow : T.line}`, background: on ? T.meadow : T.white, display: "inline-flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>{on && <DrIc name="check" size={11} color="#fff" sw={2.6} />}</span>
      {dot && <span style={{ width: 8, height: 8, borderRadius: 2, background: dot }} />}
      <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.body || T.ink }}>{label}</span>
    </button>
  );
}
function Radio({ on, onClick, label }) {
  return (
    <button onClick={onClick} className="roster-opt" style={{ display: "flex", alignItems: "center", gap: 9, width: "100%", padding: "6px 8px", background: "transparent", border: "none", cursor: "pointer", borderRadius: 7, textAlign: "left" }}>
      <span style={{ width: 15, height: 15, borderRadius: 99, border: `1.5px solid ${on ? T.meadow : T.line}`, display: "inline-flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>{on && <span style={{ width: 7, height: 7, borderRadius: 99, background: T.meadow }} />}</span>
      <span style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.body || T.ink }}>{label}</span>
    </button>
  );
}
function FunnelMenu({ col, pos, filters, setFilters, sort, setSort, onClose }) {
  const left = Math.min(pos.x, (typeof window !== "undefined" ? window.innerWidth : 1200) - 226);
  const setF = (v) => setFilters((f) => ({ ...f, [col.key]: v }));
  const toggleSet = (val) => { const cur = filters[col.key] || []; setF(cur.includes(val) ? cur.filter((x) => x !== val) : [...cur, val]); };
  const sortRow = (loLbl, hiLbl) => (
    <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
      {[["asc", loLbl], ["desc", hiLbl]].map(([dd, lbl]) => {
        const on = sort && sort.col === col.key && sort.dir === dd;
        return <button key={dd} onClick={() => setSort(on ? null : { col: col.key, dir: dd })} style={{ flex: 1, fontFamily: "Inter,sans-serif", fontSize: 11, fontWeight: 600, cursor: "pointer", borderRadius: 7, padding: "6px 8px", border: `1px solid ${on ? T.meadow : T.line}`, background: on ? "rgba(97,131,94,.08)" : T.white, color: on ? T.ink : T.slate }}>{lbl}</button>;
      })}
    </div>
  );
  let body;
  if (col.type === "text") {
    body = <input autoFocus value={filters[col.key] || ""} onChange={(e) => setF(e.target.value)} placeholder="Contains…" style={{ width: "100%", boxSizing: "border-box", fontFamily: "Inter,sans-serif", fontSize: 12, padding: "8px 10px", borderRadius: 8, border: `1px solid ${T.line}`, outline: "none", color: T.ink }} />;
  } else if (col.type === "set" || col.type === "bucket") {
    const cur = filters[col.key] || [];
    body = (<>
      <div style={{ display: "flex", justifyContent: "space-between", padding: "0 2px 6px" }}>
        <button onClick={() => setF(col.opts.map((o) => o[0]))} style={linkBtn}>Select all</button>
        <button onClick={() => setF([])} style={linkBtn}>Clear</button>
      </div>
      {col.opts.map(([v, l]) => <Checkbox key={v} on={cur.includes(v)} onClick={() => toggleSet(v)} label={l} dot={col.type === "set" ? PLAN_DOT[v] : undefined} />)}
    </>);
  } else {
    const cur = filters[col.key] || "";
    body = (<>
      <Radio on={!cur} onClick={() => setF("")} label="Any date" />
      {DATE_OPTS[col.dir].map(([v, l]) => <Radio key={v} on={cur === v} onClick={() => setF(v)} label={l} />)}
    </>);
  }
  return (
    <>
      <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 60 }} />
      <div style={{ position: "fixed", top: pos.y, left, width: 210, zIndex: 61, background: T.white, border: `1px solid ${T.line}`, borderRadius: 12, boxShadow: "0 16px 42px rgba(0,46,44,.20)", padding: 10 }}>
        {sortRow(col.type === "text" ? "A → Z" : "Low → High", col.type === "text" ? "Z → A" : "High → Low")}
        <div style={{ height: 1, background: T.line, margin: "2px 0 8px" }} />
        <div style={{ maxHeight: 220, overflowY: "auto" }}>{body}</div>
      </div>
    </>
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

const TAG = { fontFamily: "Inter,sans-serif", fontSize: 9.5, fontWeight: 700, letterSpacing: "0.04em",
  borderRadius: 4, padding: "1px 5px", textTransform: "uppercase", flexShrink: 0 };
const td = { padding: "11px 14px", verticalAlign: "middle" };
const dcell = { ...td, fontSize: 12.5, color: T.slate, whiteSpace: "nowrap" };

/* one member row — shared by the grouped and the sorted (flat) views. Staff (admins)
   drop the Forum/IC program chip; they carry the Admin tag instead. */
function MemberRow({ r }) {
  return (
    <tr className="roster-row" style={{ borderTop: `1px solid ${T.line}` }}>
      <td style={{ ...td, padding: "11px 14px 11px 24px", minWidth: 210 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
          <span style={{ width: 32, height: 32, borderRadius: 99, flexShrink: 0, background: T.meadowBg,
            color: T.meadowInk, fontFamily: "Poppins,sans-serif", fontSize: 11, fontWeight: 700,
            display: "flex", alignItems: "center", justifyContent: "center" }}>{initials(r.name)}</span>
          <div style={{ minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
              <span style={{ fontSize: 13.5, fontWeight: 600, color: T.ink, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.name}</span>
              {r.kind !== "admin" && <Seg seg={r.seg} />}
              {r.kind === "add_on" && <span style={{ ...TAG, color: T.slate, background: T.parchment, border: `1px solid ${T.line}` }}>Add-on</span>}
              {r.kind === "admin" && <span style={{ ...TAG, color: T.teal, background: T.mist }}>Admin</span>}
            </div>
            <div style={{ fontSize: 11, color: T.muted, marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {r.stripe_account || "—"}
            </div>
          </div>
        </div>
      </td>
      <td style={td}><Plan payment={r.payment} /></td>
      <td style={{ ...td, textAlign: "right", fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600,
        color: r.amount ? T.ink : T.muted, fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>{r.amount ? usd(r.amount) : "—"}</td>
      <td style={td}><PayCell p={r.last_payment} /></td>
      <td style={td}><PayCell p={r.next_payment} /></td>
      <td style={dcell}>{fmtDate(r.enrolled)}</td>
      <td style={dcell}>{fmtDate(r.renews)}</td>
      <td style={{ ...td, padding: "11px 24px 11px 14px", textAlign: "right" }}>
        {r.source_url && <a href={r.source_url} target="_blank" rel="noreferrer" title="Open in Go High Level" style={{ fontSize: 14, color: T.teal, textDecoration: "none" }}>↗</a>}
      </td>
    </tr>
  );
}

export default function RosterDrawer({ business, period, metricKey = "forum_roster", onClose }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState(false);
  const [prog, setProg] = useState("all");     // all | F | IC | ADMIN
  const [qtext, setQ] = useState("");
  const [sort, setSort] = useState(null);       // { col, dir } | null (null = grouped)
  const [filters, setFilters] = useState({ plan: [], value: [] });
  const [menu, setMenu] = useState(null);       // { col, x, y } — open funnel popover

  useEffect(() => {
    setD(null); setErr(false); setProg("all"); setQ(""); setSort(null); setFilters({ plan: [], value: [] }); setMenu(null);
    if (!API_BASE) {
      setD((metricKey === "bc_roster" ? sampleBecollective
        : metricKey === "edge_roster" ? sampleEdge : sampleForum).roster_detail);
      return;
    }
    const q = `/metrics/${metricKey}/detail?period=${period}`
      + (business ? `&business=${encodeURIComponent(business)}` : "");
    getJSON(q).then(setD).catch(() => setErr(true));
  }, [business, period, metricKey]);

  const rows = d?.rows || [];
  const sm = d?.summary || {};
  const mix = sm.payment_mix || {};
  const activeCols = RCOLS.filter((c) => filterActive(c, filters));
  // Quick-find (name / plan / stripe) AND every active per-column filter.
  const filtered = useMemo(() => {
    const t = qtext.trim().toLowerCase();
    return rows.filter((r) => {
      if (t && ![r.name, r.stripe_account, r.member_type, r.payment].some((v) => (v || "").toString().toLowerCase().includes(t))) return false;
      if (filters.member && !(r.name || "").toLowerCase().includes(filters.member.toLowerCase())) return false;
      if (filters.plan.length && !filters.plan.includes(r.payment)) return false;
      if (filters.value.length && !filters.value.includes(bucketOf(r.amount))) return false;
      for (const c of RCOLS) if (c.type === "date" && filters[c.key] && !passDate(dateOf(r, c.key), filters[c.key], c.dir)) return false;
      return true;
    });
  }, [rows, qtext, filters]);

  // Admins are staff seats — shown as their own group, kept out of the member lists.
  const isAdmin = (r) => r.kind === "admin";
  // beCollective / The Edge have no Forum/Inner-Circle split — one member group, no F/IC chips.
  const single = metricKey === "bc_roster" ? "BC" : metricKey === "edge_roster" ? "EDGE" : null;
  const isBc = Boolean(single);
  const inScope = filtered.filter((r) =>
    prog === "all" ? !isAdmin(r) : prog === "ADMIN" ? isAdmin(r) : (r.seg === prog && !isAdmin(r)));
  const groups = prog === "ADMIN" ? ["ADMIN"]
    : single ? [single] : prog === "IC" ? ["IC"] : prog === "F" ? ["F"] : ["F", "IC"];
  const inGroup = (r, g) => (g === "ADMIN" ? isAdmin(r) : (g === "BC" || g === "EDGE") ? !isAdmin(r) : (r.seg === g && !isAdmin(r)));
  const GROUP_LABEL = { F: "The Forum", IC: "Inner Circle", ADMIN: "Admins", BC: "Members", EDGE: "Members" };

  const sorted = useMemo(() => {
    if (!sort) return null;
    const dir = sort.dir === "asc" ? 1 : -1;
    return [...inScope].sort((a, b) => {
      const ka = sortVal(a, sort.col), kb = sortVal(b, sort.col);
      if (ka === "" && kb === "") return 0;
      if (ka === "") return 1;
      if (kb === "") return -1;
      return (ka < kb ? -1 : ka > kb ? 1 : 0) * dir;
    });
  }, [inScope, sort]);

  const tab = (k, on) => ({
    fontFamily: "Inter,sans-serif", fontSize: 12, fontWeight: 600, cursor: "pointer", border: "none",
    borderRadius: 7, padding: "6px 12px", background: on ? T.white : "transparent",
    color: on ? T.ink : T.muted, boxShadow: on ? "0 1px 3px rgba(0,46,44,.12)" : "none",
  });
  const subset = sm.total != null && rows.length < sm.total;
  const openMenu = (col, e) => { const r = e.currentTarget.getBoundingClientRect(); setMenu(menu && menu.col === col.key ? null : { col: col.key, x: r.left, y: r.bottom + 6 }); };
  const clearCol = (key) => setFilters((f) => ({ ...f, [key]: (key === "plan" || key === "value") ? [] : "" }));
  const th = { padding: "0 14px", position: "sticky", top: 0, background: T.white,
    borderBottom: `1px solid ${T.line}`, zIndex: 2 };
  // Active-filter chip text per column.
  const chipText = (c) => {
    const f = filters[c.key];
    if (c.type === "set") return `${c.label}: ` + f.map((v) => PLAN_SHORT[v]).join(", ");
    if (c.type === "bucket") return `${c.label}: ` + f.map((v) => c.opts.find((o) => o[0] === v)[1]).join(", ");
    if (c.type === "date") return `${c.label}: ` + (DATE_OPTS[c.dir].find((o) => o[0] === f) || ["", "No date"])[1];
    return `${c.label}: "${f}"`;
  };

  return (
    <>
      <style>{`
        @keyframes rosterIn { from { transform: translateX(24px); opacity: 0; } to { transform: none; opacity: 1; } }
        .roster-aside { animation: rosterIn .32s cubic-bezier(.22,1,.36,1) both; }
        .roster-row:hover { background: ${T.parchment}; }
        .roster-opt:hover { background: ${T.parchment}; }
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
              <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 19, fontWeight: 600, color: T.ink, marginTop: 8 }}>{isBc ? "beCollective · Roster" : "The Forum · Roster"}</div>
            </div>
            <button onClick={onClose} aria-label="Close" style={{ fontSize: 17, color: T.slate, background: "transparent", border: "none", cursor: "pointer", lineHeight: 1 }}>✕</button>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 18, marginTop: 16 }}>
            <Stat label="Members"><span style={{ fontSize: 22 }}>{sm.total ?? rows.length}</span></Stat>
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
            {[["all", "All"], ...(isBc ? [] : [["F", "The Forum"], ["IC", "Inner Circle"]]), ...(sm.admin ? [["ADMIN", "Admins"]] : [])].map(([k, label]) => (
              <button key={k} onClick={() => setProg(k)} style={tab(k, prog === k)}>{label}</button>
            ))}
          </div>
          <div style={{ position: "relative", flex: 1, minWidth: 160 }}>
            <span style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", opacity: .5 }}><DrIc name="search" size={14} color={T.slate} /></span>
            <input value={qtext} onChange={(e) => setQ(e.target.value)} placeholder="Search members…"
              style={{ width: "100%", boxSizing: "border-box", fontFamily: "Inter,sans-serif", fontSize: 13, color: T.ink,
                background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 12px 8px 32px", outline: "none" }} />
          </div>
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted }}>
            {inScope.length} shown{subset ? ` · sample of ${sm.total}` : ""}
          </span>
        </div>
        {activeCols.length > 0 && (
          <div style={{ display: "flex", alignItems: "center", gap: 7, padding: "10px 24px", borderBottom: `1px solid ${T.line}`, flexWrap: "wrap", background: T.parchment }}>
            {activeCols.map((c) => (
              <span key={c.key} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontFamily: "Inter,sans-serif", fontSize: 11, fontWeight: 600, color: T.ink, background: "rgba(97,131,94,.10)", border: `1px solid ${T.meadow}`, borderRadius: 7, padding: "4px 6px 4px 9px" }}>
                {chipText(c)}
                <button onClick={() => clearCol(c.key)} aria-label="Remove filter" style={{ background: "transparent", border: "none", cursor: "pointer", opacity: .6, display: "inline-flex" }}><DrIc name="close" size={11} color={T.slate} sw={2.2} /></button>
              </span>
            ))}
            <button onClick={() => setFilters({ plan: [], value: [] })} style={{ ...linkBtn, color: T.slate }}>Clear all</button>
          </div>
        )}

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
          ) : inScope.length === 0 ? (
            <div style={{ padding: 24, color: T.muted, fontSize: 13 }}>No members match.</div>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", minWidth: 1000, borderCollapse: "collapse" }}>
                <thead>
                  <tr>
                    {RCOLS.map((c, i) => {
                      const on = filterActive(c, filters);
                      const isSorted = sort && sort.col === c.key;
                      return (
                        <th key={c.key} style={{ ...th, ...(i === 0 ? { paddingLeft: 24 } : {}) }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "13px 0", justifyContent: c.align === "right" ? "flex-end" : "flex-start" }}>
                            <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: on || isSorted ? T.ink : T.muted, whiteSpace: "nowrap" }}>{c.label}{isSorted ? (sort.dir === "asc" ? " ↑" : " ↓") : ""}</span>
                            <button onClick={(e) => openMenu(c, e)} title="Sort & filter" style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 22, height: 22, borderRadius: 6, cursor: "pointer", background: on ? T.meadow : "transparent", border: `1px solid ${on ? T.meadow : T.line}` }}>
                              <DrIc name="funnel" size={12} color={on ? "#fff" : T.slate} sw={1.9} />
                            </button>
                          </div>
                        </th>
                      );
                    })}
                    <th style={{ ...th, paddingRight: 24 }} />
                  </tr>
                </thead>
                {sorted ? (
                  <tbody>{sorted.map((r) => <MemberRow key={r.id} r={r} />)}</tbody>
                ) : groups.map((g) => {
                  const gr = inScope.filter((r) => inGroup(r, g));
                  if (!gr.length) return null;
                  return (
                    <tbody key={g}>
                      <tr>
                        <td colSpan={8} style={{ padding: "14px 24px 6px", fontFamily: "Poppins,sans-serif",
                          fontSize: 12, fontWeight: 600, color: T.slate, background: T.page }}>
                          {GROUP_LABEL[g]}
                          <span style={{ color: T.muted, fontWeight: 500 }}> · {gr.length}</span>
                        </td>
                      </tr>
                      {gr.map((r) => <MemberRow key={r.id} r={r} />)}
                    </tbody>
                  );
                })}
              </table>
            </div>
          )}
        </div>
      </aside>
      {menu && <FunnelMenu col={RCOLS.find((c) => c.key === menu.col)} pos={menu} filters={filters} setFilters={setFilters} sort={sort} setSort={setSort} onClose={() => setMenu(null)} />}
    </>
  );
}
