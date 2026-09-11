/* Books · Queue — the bookkeeper's seat. Rows expand to Claude's reasoning + the actions
   (approve / change category / escalate). CFO characterizes intercompany escalations. */
import { useEffect, useMemo, useState } from "react";
import { postJSON } from "../api";
import { useBooksQueue } from "./useBooks.js";
import { Card, Eyebrow, Pill, StatePanel, EntityChip, Field, QboLink, CHAR_BY_LABEL, font, usd, T } from "./ui.jsx";

const API = import.meta.env.VITE_API_BASE;
const FLAG_LABEL = { over_band: "Unusual amount", first_vendor: "New vendor", possible_1099: "Possible 1099",
  anomaly: "Anomaly", multi_line: "Split txn", intercompany: "Intercompany" };

async function mutate(path, body) { if (API) await postJSON(path, body || {}); }
const flagsOf = (f) => Object.keys(f || {}).filter((k) => f[k]);

/* Sign-off timestamps are ISO from the server. Day precision is what a reviewer needs — the
   minute somebody clicked Approve is noise on a statement read weeks later. */
const fmtWhen = (iso) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" :
    d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
};

function actionBtn(kind) {
  const base = { fontFamily: font.head, fontSize: 12.5, fontWeight: 600, borderRadius: 8, padding: "8px 16px", cursor: "pointer" };
  if (kind === "primary") return { ...base, color: T.white, background: T.meadow, border: "none" };
  if (kind === "danger") return { ...base, color: T.poppyText, background: T.white, border: `1px solid ${T.line}` };
  return { ...base, color: T.slate, background: T.white, border: `1px solid ${T.line}` };
}

/* `scope` is not decoration. These three tiles count the whole backlog while the stage chips
   below count the selected window, so without it the screen shows 1129 next to 1127 next to 9
   next to 11 and every one of them looks like a bug. */
function StatCard({ label, value, color, scope }) {
  return (
    <Card style={{ padding: "15px 17px" }}>
      <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.secondary, marginBottom: 7 }}>
        {label}
        {scope && <span style={{ color: T.muted }}> · {scope}</span>}
      </div>
      <div style={{ fontFamily: font.head, fontSize: 23, fontWeight: 700, color: color || T.ink, fontVariantNumeric: "tabular-nums" }}>{value}</div>
    </Card>
  );
}

/* A split's "category" is where it ALREADY sits, stored with confidence 0.0 — not a proposal.
   Rendering that as "Uncategorized Asset · 0%" reads as a terrible guess when in fact the
   system deliberately declined to guess. is_proposal is what separates the two. */
/* ULRG codes four levels deep — "62000 Lead Generation:62200 Listing Management:62220
   Photography & Virtual Tours" — so the chip shows the leaf and carries the full path in its
   tooltip. Rendered whole at flexShrink:0 it shoved the amount and the buttons off the row. */
const leafOf = (s) => {
  const parts = String(s || "").split(":");
  return parts.length > 1 ? `… ${parts[parts.length - 1].trim()}` : s;
};

function CategoryChip({ tx }) {
  const base = { fontFamily: font.body, fontSize: 11.5, borderRadius: 6, padding: "3px 9px",
    maxWidth: 210, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
    flexShrink: 1, minWidth: 0 };
  if (tx.is_proposal && tx.suggest) {
    return (
      <span title={tx.suggest} style={{ ...base, fontWeight: 600, color: T.teal, background: T.mist }}>
        {leafOf(tx.suggest)}{tx.conf ? ` · ${tx.conf}` : ""}</span>
    );
  }
  if (!tx.current_category) return null;
  return (
    <span title={tx.current_category}
      style={{ ...base, color: T.muted, border: `1px solid ${T.line}` }}>
      on {leafOf(tx.current_category)}</span>
  );
}

/* How much evidence stands behind the category, as three notches plus a word. The word is what
   carries it: a history match on 195 priors and one on 3 look identical without it, and those
   are the two cases a reviewer most needs to tell apart. `strength` is computed server-side so
   the filter, this badge and the drawer cannot each decide what "weak" means. */
function Evidence({ tx, compact = false }) {
  const fill = { strong: 3, thin: 2, weak: 1, na: 0 }[tx.strength] ?? 0;
  const detail = tx.basis === "history_match" || tx.basis === "over_band"
    ? (tx.priors != null ? `${tx.priors} prior${tx.priors === 1 ? "" : "s"}` : "")
    : tx.basis === "claude" ? (tx.conf || "")
    : tx.basis === "split" && compact ? "no guess" : "";
  // The row says "History · 38 priors"; the drawer, which has room, says "Matched from history".
  const label = compact ? (BASIS_SHORT[tx.basis] || tx.basis) : (tx.basis_label || tx.basis);
  /* display:flex, not inline-flex. An inline-flex box is sized by its CONTENT, so on a narrow row
     it grew past its column and painted over the amount — the WEAK badge landed on "$30,000".
     A block-level flex box fills the column it is given, and overflow:hidden is the guarantee. */
  return (
    <span style={{ display: "flex", alignItems: "center", gap: 7, minWidth: 0, overflow: "hidden" }}>
      <span aria-hidden="true" style={{ display: "inline-flex", gap: 2, flexShrink: 0 }}>
        {[1, 2, 3].map((n) => (
          <span key={n} style={{ width: 7, height: 13, borderRadius: 2, boxSizing: "border-box",
            background: tx.strength !== "na" && fill >= n ? T.ink : "transparent",
            border: tx.strength === "na" ? `1px dashed ${T.line}`
              : fill >= n ? "none" : `1px solid ${T.muted}` }} />
        ))}
      </span>
      {/* min-width:0 is what lets the ellipsis ever happen. A flex item's minimum defaults to its
          full text width, so without it the text refused to shrink and shoved the badge outward. */}
      <span style={{ fontFamily: font.body, fontSize: 11, color: T.secondary, overflow: "hidden",
        textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0, flex: "0 1 auto" }}>
        {BASIS_GLYPH[tx.basis] || ""} {label}{detail ? ` · ${detail}` : ""}
      </span>
      {(tx.strength === "weak" || tx.strength === "thin") && (
        <span style={{ fontFamily: font.head, fontSize: 9.5, fontWeight: 700, flexShrink: 0,
          letterSpacing: "0.07em", borderRadius: 4, padding: "1px 5px", background: T.white,
          color: tx.strength === "weak" ? T.ink : T.secondary,
          border: `1px solid ${tx.strength === "weak" ? T.ink : T.muted}` }}>
          {tx.strength === "weak" ? "WEAK" : "THIN"}</span>
      )}
    </span>
  );
}

function QueueRow({ tx, open, onToggle, onDone, selected = false, onSelect, focused = false, ent }) {
  const [editing, setEditing] = useState(false);
  const [cat, setCat] = useState(tx.suggest || "");
  const [busy, setBusy] = useState(false);
  const run = async (fn) => { setBusy(true); try { await fn(); onDone(tx.id); } finally { setBusy(false); } };
  const quiet = { fontFamily: font.head, fontSize: 11.5, fontWeight: 600, borderRadius: 7,
                  padding: "5px 11px", cursor: "pointer", whiteSpace: "nowrap" };
  return (
    <div style={{ borderTop: `1px solid ${T.line}`,
      background: focused ? T.parchment : selected ? T.mist : "transparent" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "13px 4px" }}>
        <input type="checkbox" checked={selected} onChange={onSelect} disabled={busy}
          aria-label={`Select ${tx.vendor || "transaction"}`}
          style={{ width: 15, height: 15, flexShrink: 0, cursor: "pointer", accentColor: T.meadow }} />
        <button onClick={onToggle} style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center",
          gap: 14, padding: 0, background: "transparent", border: "none", cursor: "pointer", textAlign: "left" }}>
          <span style={{ width: 44, fontFamily: font.body, fontSize: 11.5, color: T.muted, flexShrink: 0 }}>{tx.date}</span>
          {/* Entity name and colour come from the payload — each workspace's own businesses. */}
          <span style={{ width: 124, flexShrink: 0, display: "inline-flex", alignItems: "center",
            gap: 6, minWidth: 0 }}>
            <span aria-hidden="true" style={{ width: 8, height: 8, borderRadius: 2, flexShrink: 0,
              background: ent?.accent || T.muted }} />
            <span style={{ fontFamily: font.body, fontSize: 11.5, fontWeight: 600, color: T.secondary,
              overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {ent?.name || tx.entity}</span>
          </span>
          <span style={{ width: 190, minWidth: 0, fontFamily: font.body, fontSize: 13, fontWeight: 600,
            color: tx.vendor ? T.ink : T.secondary, overflow: "hidden", textOverflow: "ellipsis",
            whiteSpace: "nowrap" }}>{tx.vendor || tx.memo}</span>
          {tx.signed_off && <span style={{ fontFamily: font.body, fontSize: 11, fontWeight: 600,
            color: T.meadowInk, flexShrink: 0 }}>✓ {tx.decided_by || "reviewed"}</span>}
          <CategoryChip tx={tx} />
          <span style={{ flex: 1, minWidth: 0, overflow: "hidden" }}><Evidence tx={tx} compact /></span>
          <span style={{ width: 82, textAlign: "right", fontFamily: font.head, fontSize: 13.5, fontWeight: 600,
            color: T.ink, fontVariantNumeric: "tabular-nums", flexShrink: 0 }}>{usd(tx.amount)}</span>
          <span style={{ color: T.muted, fontSize: 12, flexShrink: 0, transform: open ? "rotate(180deg)" : "none", transition: "transform .15s" }}>▾</span>
        </button>
        {/* Quick action without selecting first — most decisions are one row at a time. */}
        <span style={{ display: "flex", gap: 6, flexShrink: 0 }}>
          {tx.scan_state === "needs_approval" && (
            <button disabled={busy} title="Approve" style={{ ...quiet, color: T.white, background: T.meadow, border: "none" }}
              onClick={() => run(() => mutate(`/books/txn/${tx.id}/approve`))}>Approve</button>
          )}
          {!tx.signed_off && (
            <button disabled={busy} title="Seen and fine — hide from next Friday"
              style={{ ...quiet, color: T.slate, background: T.white, border: `1px solid ${T.line}` }}
              onClick={() => run(() => mutate(`/books/txn/${tx.id}/acknowledge`))}>Reviewed</button>
          )}
        </span>
      </div>
      {open && (
        <div style={{ background: T.parchment, borderRadius: 10, padding: "14px 16px", margin: "0 0 13px" }}>
          {/* Why it is here — then how much that is worth. Reason and verdict were one grey
              line before, which buried the only thing on screen worth reading. */}
          <div style={{ fontFamily: font.body, fontSize: 12.5, color: T.secondary, lineHeight: 1.55 }}>
            <span style={{ fontWeight: 700, color: T.ink }}>{tx.basis_label || "How it got here"}: </span>
            {tx.reason || (tx.basis === "none"
              ? "No pass has reached this transaction yet, so nothing has formed an opinion about it."
              : tx.basis === "split"
              ? "It spans more than one account, so the system refused to guess — the category shown is where it already sits."
              : "No reason was recorded.")}
          </div>
          <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 8, flexWrap: "wrap" }}>
            <Evidence tx={tx} />
            <span style={{ fontFamily: font.body, fontSize: 11, color: T.muted }}>{tx.strength_rule}</span>
          </div>
          <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 7, flexWrap: "wrap" }}>
            <span style={{ fontFamily: font.body, fontSize: 11, color: T.muted }}>Source: {tx.source}</span>
            {flagsOf(tx.flags).map((k) => <Pill key={k} tone="warn">{FLAG_LABEL[k] || k}</Pill>)}
          </div>
          <div style={{ display: "grid", gap: 4, marginTop: 10 }}>
            <Field label="Type" value={tx.qbo_type} />
            <Field label="Memo" value={tx.memo} />
            <Field label="Currently on" value={tx.current_category} />
            <Field label="Paid from" value={tx.bank_account} />
            {/* The sign-off trail. This is what an outside accountant is here to read, and
                "approved" with no name against it is an assertion rather than a record. */}
            {tx.signed_off && <Field label="Signed off by"
              value={[tx.decided_by, tx.signed_off_at && fmtWhen(tx.signed_off_at)]
                .filter(Boolean).join(" · ") || "—"} />}
            {tx.decision === "recategorize" && tx.decided_category && (
              <Field label="Changed to" value={`${tx.decided_category} — not yet changed in QuickBooks`} />
            )}
          </div>
          {tx.qbo_url && <div style={{ marginTop: 9 }}><QboLink url={tx.qbo_url} entity={tx.entity} /></div>}
          {editing ? (
            <div style={{ display: "flex", gap: 9, marginTop: 13, flexWrap: "wrap" }}>
              <input value={cat} onChange={(e) => setCat(e.target.value)} placeholder="Account name"
                style={{ flex: 1, minWidth: 200, fontFamily: font.body, fontSize: 13, color: T.ink, background: T.white,
                  border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 11px" }} />
              <button disabled={busy || !cat.trim()} style={actionBtn("primary")}
                onClick={() => run(() => mutate(`/books/txn/${tx.id}/recategorize`, { category: cat.trim() }))}>Save</button>
              <button style={actionBtn()} onClick={() => setEditing(false)}>Cancel</button>
            </div>
          ) : !tx.signed_off && (
            /* Nothing to decide on a row somebody already signed off. Offering Approve there
               invites a second sign-off over the first, and the Approved stage is a record
               an outside reviewer reads rather than a worklist they act on. */
            <div style={{ display: "flex", gap: 9, marginTop: 13, flexWrap: "wrap" }}>
              <button disabled={busy} style={actionBtn("primary")}
                onClick={() => run(() => mutate(`/books/txn/${tx.id}/approve`))}>
                Approve{tx.suggest ? ` as ${tx.suggest}` : ""}</button>
              <button disabled={busy} style={actionBtn()} onClick={() => setEditing(true)}>Change category</button>
              <button disabled={busy} style={actionBtn("danger")}
                onClick={() => run(() => mutate(`/books/txn/${tx.id}/escalate`))}>Escalate to Connor</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function EscRow({ e, open, onToggle, onDone, isCFO }) {
  const [busy, setBusy] = useState(false);
  const resolve = async (label) => {
    setBusy(true);
    try { await mutate(`/books/ic/${e.id}/characterize`, { characterization: CHAR_BY_LABEL[label] || "loan" }); onDone(e.id); }
    finally { setBusy(false); }
  };
  return (
    <div style={{ borderTop: `1px solid ${T.line}` }}>
      <button onClick={onToggle} style={{ width: "100%", display: "flex", alignItems: "center", gap: 14,
        padding: "13px 4px", background: "transparent", border: "none", cursor: "pointer", textAlign: "left" }}>
        <span style={{ width: 44, fontFamily: font.body, fontSize: 11.5, color: T.muted, flexShrink: 0 }}>{e.date}</span>
        <span style={{ width: 9, height: 9, borderRadius: 99, background: T.poppy, flexShrink: 0 }} />
        <span style={{ flex: 1, fontFamily: font.body, fontSize: 13, fontWeight: 600, color: T.ink }}>{e.label}</span>
        <span style={{ fontFamily: font.body, fontSize: 11, fontWeight: 700, letterSpacing: "0.06em",
          textTransform: "uppercase", color: T.poppyText, flexShrink: 0 }}>Intercompany · off-policy</span>
        <span style={{ width: 82, textAlign: "right", fontFamily: font.head, fontSize: 13.5, fontWeight: 600,
          color: T.ink, fontVariantNumeric: "tabular-nums", flexShrink: 0 }}>{usd(e.amount)}</span>
        <span style={{ color: T.muted, fontSize: 12, flexShrink: 0, transform: open ? "rotate(180deg)" : "none", transition: "transform .15s" }}>▾</span>
      </button>
      {open && (
        <div style={{ background: T.parchment, borderRadius: 10, padding: "14px 16px", margin: "0 0 13px" }}>
          <div style={{ fontFamily: font.body, fontSize: 12.5, color: T.secondary, lineHeight: 1.55 }}>
            <span style={{ fontWeight: 700, color: T.ink }}>Why it stopped: </span>{e.reason}</div>
          {(e.txns || []).map((t, i) => (
            <div key={i} style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 9, padding: "10px 12px", marginTop: 10 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6, flexWrap: "wrap", gap: 8 }}>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                  <EntityChip k={t.entity} />
                  <span style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted }}>{t.qbo_type} · {t.date}</span>
                </span>
                <span style={{ fontFamily: font.head, fontSize: 13, fontWeight: 700, color: T.ink }}>{usd(t.amount)}</span>
              </div>
              <div style={{ display: "grid", gap: 4 }}>
                <Field label="Payee" value={t.payee} />
                <Field label="Memo" value={t.memo} />
                <Field label="Account" value={t.account} />
                <Field label="Bank/card" value={t.bank_account} />
              </div>
              {t.qbo_url && <div style={{ marginTop: 8 }}><QboLink url={t.qbo_url} entity={t.entity} /></div>}
            </div>
          ))}
          {e.tax_note && <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.daffodilText, background: T.daffodilBg,
            borderRadius: 7, padding: "7px 11px", marginTop: 10, lineHeight: 1.5 }}>{e.tax_note}</div>}
          {isCFO ? (
            <div style={{ display: "flex", gap: 9, marginTop: 13, flexWrap: "wrap" }}>
              {(e.options || []).map((o, i) => (
                <button key={i} disabled={busy} onClick={() => resolve(o)} style={{ fontFamily: font.head, fontSize: 12.5,
                  fontWeight: 600, color: i === 0 ? T.white : T.slate, background: i === 0 ? T.evergreen : T.white,
                  border: i === 0 ? "none" : `1px solid ${T.line}`, borderRadius: 8, padding: "8px 16px", cursor: "pointer" }}>{o}</button>
              ))}
            </div>
          ) : (
            <div style={{ fontFamily: font.body, fontSize: 12.5, color: T.muted, marginTop: 12 }}>
              Awaiting CFO characterization — this is Connor's call.</div>
          )}
        </div>
      )}
    </div>
  );
}

/* The funnel from the Books home screen, as filters. "All" is Captured; "Auto-categorized"
   cross-cuts the rest rather than sitting inside the partition, which is why it is a lens
   rather than a bucket. */
const STAGES = [
  ["all", "All"], ["auto", "Auto-categorized"], ["cleared", "Cleared"],
  ["needs_approval", "Needs approval"], ["escalated", "Escalated"],
  ["pending", "Not yet reached"], ["approved", "Approved"],
];

/* The second axis: what DECIDED the category. Labels come from the server so the filter chip
   and the row's own explanation can never drift apart. */
const BASES = [
  ["any", "Any"], ["history_match", "History"], ["claude", "Claude"],
  ["over_band", "Over range"], ["split", "Split"], ["none", "Not analyzed"],
];
const BASIS_SHORT = Object.fromEntries(BASES);   // the row's short labels = the chip vocabulary
/* Meaning never lives in colour alone — a hard rule here. Every facet carries its word, and
   the basis chips carry a glyph as well, so the axis survives a monochrome screenshot. */
const BASIS_GLYPH = { history_match: "↻", claude: "◆", over_band: "↕", split: "÷", none: "○" };

function AxisRow({ label, children }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", minWidth: 0 }}>
      <span style={{ width: 78, flexShrink: 0, fontFamily: font.head, fontSize: 10,
        fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", color: T.secondary }}>
        {label}</span>
      {children}
    </div>
  );
}

function Facet({ label, count, active, onClick, glyph, ink, title }) {
  const dead = !active && count === 0;      // nothing there — say so quietly rather than hide it
  return (
    <button onClick={onClick} title={title || undefined}
      style={{ display: "inline-flex", alignItems: "center", gap: 6, fontFamily: font.body,
        fontSize: 11.5, fontWeight: 600, whiteSpace: "nowrap", cursor: "pointer",
        borderRadius: 99, padding: "5px 12px",
        color: active ? T.white : dead ? T.muted : T.secondary,
        background: active ? (ink ? T.evergreen : T.meadow) : T.white,
        border: `1px solid ${active ? (ink ? T.evergreen : T.meadow) : T.line}` }}>
      {glyph && <span aria-hidden="true" style={{ fontSize: 11, lineHeight: 1 }}>{glyph}</span>}
      {label}
      {count != null && <span style={{ fontVariantNumeric: "tabular-nums",
        color: active ? T.white : T.muted }}>{count}</span>}
    </button>
  );
}

/* Lenses cross-cut both axes rather than replacing a selection, so they read as checkboxes
   instead of pills — a different shape for a different kind of filter. */
function Lens({ label, count, on, onClick, title }) {
  return (
    <button onClick={onClick} title={title || undefined}
      style={{ display: "inline-flex", alignItems: "center", gap: 7, fontFamily: font.body,
        fontSize: 11.5, fontWeight: on ? 700 : 600, whiteSpace: "nowrap", cursor: "pointer",
        border: "none", borderRadius: 8, padding: "5px 10px",
        color: on ? T.ink : T.secondary, background: on ? T.mist : "transparent" }}>
      <span aria-hidden="true" style={{ width: 12, height: 12, borderRadius: 3, flexShrink: 0,
        display: "inline-flex", alignItems: "center", justifyContent: "center", fontSize: 9,
        fontWeight: 700, lineHeight: 1, color: T.white,
        background: on ? T.evergreen : T.white,
        border: `1px solid ${on ? T.evergreen : T.muted}` }}>{on ? "✓" : ""}</span>
      {label}
      {count != null && <span style={{ fontVariantNumeric: "tabular-nums", color: T.muted }}>{count}</span>}
    </button>
  );
}

export default function BooksQueue({ isCFO = false, period = "mtd" }) {
  const [stage, setStage] = useState("needs_approval");
  const [basis, setBasis] = useState("any");
  const [autoOnly, setAutoOnly] = useState(false);
  const [weakOnly, setWeakOnly] = useState(false);
  const [showSignedOff, setShowSignedOff] = useState(false);
  const { data, loading, error, retry, refresh } =
    useBooksQueue({ period, state: stage, basis, autoOnly, weakOnly,
                    includeSignedOff: showSignedOff });
  const [tab, setTab] = useState("queue");
  const [filter, setFilter] = useState("all");
  const [openId, setOpenId] = useState(null);
  const [done, setDone] = useState(() => new Set());
  const [sel, setSel] = useState(() => new Set());
  const [cursor, setCursor] = useState(0);
  const [pending, setPending] = useState(null);          // a bulk action awaiting confirmation
  const [busyBulk, setBusyBulk] = useState(false);
  const mark = (id) => { setDone((d) => new Set(d).add(id)); setOpenId(null); };

  const stages = data?.stages || {};
  const lenses = data?.lenses || {};
  const bases = data?.bases || {};
  const basisLabels = data?.basis_labels || {};
  /* Entity identity comes from the payload, keyed by business, with each workspace's own accent.
     The constant this replaces held one customer's five companies and hand-picked dots — two of
     which were the same colour, so Spring B and The Forum were indistinguishable. */
  const entities = useMemo(() => {
    const m = {};
    for (const e of data?.entities || []) m[e.key] = e;
    return m;
  }, [data]);
  const entOf = (k) => entities[k] || { key: k, name: k || "—", accent: null };
  const rows = useMemo(() => (data?.rows || [])
    .filter((r) => !done.has(r.id) && (filter === "all" || r.entity === filter)),
  [data, done, filter]);

  // A selection may only ever contain rows that are actually on screen. Without this, changing
  // the stage or entity filter would silently widen what the next bulk action touches.
  useEffect(() => {
    setSel((prev) => {
      if (!prev.size) return prev;
      const visible = new Set(rows.map((r) => r.id));
      const next = new Set([...prev].filter((id) => visible.has(id)));
      return next.size === prev.size ? prev : next;
    });
    setPending(null);
  }, [rows]);

  const selRows = rows.filter((r) => sel.has(r.id));
  const selTotal = selRows.reduce((a, r) => a + Math.abs(Number(r.amount) || 0), 0);
  const allShown = rows.length > 0 && selRows.length === rows.length;
  const toggleSel = (id) => setSel((p) => {
    const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n;
  });
  const toggleAll = () => setSel(allShown ? new Set() : new Set(rows.map((r) => r.id)));

  const runBulk = async (action) => {
    const ids = selRows.map((r) => r.id);
    if (!ids.length) return;
    setBusyBulk(true);
    try {
      await mutate("/books/txn/bulk", { ids, action });
      setDone((d) => { const n = new Set(d); ids.forEach((i) => n.add(i)); return n; });
      setSel(new Set()); setPending(null); refresh();
    } finally { setBusyBulk(false); }
  };

  // Going line by line with three people watching, reaching for the mouse is what makes the
  // meeting long. Movement and selection are keyed; nothing destructive is.
  useEffect(() => {
    const onKey = (e) => {
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if (tab !== "queue" || !rows.length) return;
      const at = rows[Math.min(cursor, rows.length - 1)];
      if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(c + 1, rows.length - 1)); }
      else if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
      else if (e.key === "x" && at) { e.preventDefault(); toggleSel(at.id); }
      else if (e.key === "Enter" && at) { e.preventDefault(); setOpenId((o) => (o === at.id ? null : at.id)); }
      else if (e.key === "a" && at && at.scan_state === "needs_approval") {
        e.preventDefault(); mutate(`/books/txn/${at.id}/approve`).then(() => mark(at.id));
      } else if (e.key === "r" && at && !at.signed_off) {
        e.preventDefault(); mutate(`/books/txn/${at.id}/acknowledge`).then(() => mark(at.id));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rows, cursor, tab]);

  const escalations = (data?.escalations || []).filter((r) => !done.has(r.id));

  return (
    <StatePanel loading={loading} error={error} retry={retry}>
      {data && (
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 14 }}>
            <StatCard label="Awaiting approval" scope="all time" value={data.stats?.awaiting ?? 0}
              color={(data.stats?.awaiting ?? 0) ? T.daffodilText : T.meadowInk} />
            <StatCard label="Escalated to CFO" scope="all time" value={data.stats?.escalated ?? escalations.length}
              color={(data.stats?.escalated ?? escalations.length) ? T.poppyText : T.meadowInk} />
            <StatCard label="Approved" scope="last 7 days" value={data.stats?.approved_7d ?? 0} color={T.meadowInk} />
          </div>

          <Card style={{ padding: "18px 22px 10px" }}>
            <div style={{ display: "flex", gap: 6, marginBottom: 6, flexWrap: "wrap", alignItems: "center" }}>
              {[["queue", `Review · ${rows.length}`], ["esc", `Escalations · ${escalations.length}`]].map(([k, l]) => (
                <button key={k} onClick={() => setTab(k)} style={{ fontFamily: font.head, fontSize: 12.5, fontWeight: 600,
                  color: tab === k ? T.ink : T.muted, background: tab === k ? T.parchment : "transparent", border: "none",
                  borderBottom: tab === k ? `2px solid ${k === "esc" ? T.poppy : T.daffodilText}` : "2px solid transparent",
                  borderRadius: "8px 8px 0 0", padding: "8px 14px", cursor: "pointer" }}>{l}</button>
              ))}
              <span style={{ flex: 1 }} />
              {/* Every business this workspace has, from the payload. The hardcoded list here
                  named three of Connor's five, so rows for The Forum and beCollective were
                  visible but unfilterable — and a second tenant got somebody else's companies. */}
              {tab === "queue" && [["all", "All"], ...(data?.entities || []).map((e) => [e.key, e.name])].map(([k, l]) => (
                <button key={k} onClick={() => setFilter(k)} style={{ fontFamily: font.body, fontSize: 11.5, fontWeight: 600,
                  color: filter === k ? T.ink : T.muted, background: filter === k ? T.parchment : "transparent",
                  border: `1px solid ${filter === k ? T.line : "transparent"}`, borderRadius: 99, padding: "5px 12px", cursor: "pointer" }}>{l}</button>
              ))}
            </div>

            {tab === "queue" && (
              <>
                {/* Two axes. Stage is where a transaction sits; Decided by is what put the
                    category on it. Counts are faceted server-side: each chip's number already
                    has the OTHER filters applied, so it predicts what clicking it returns
                    rather than promising rows the list then refuses to show. */}
                <div style={{ display: "flex", flexDirection: "column", gap: 8,
                  padding: "8px 0 10px", borderTop: `1px solid ${T.line}` }}>
                  <AxisRow label="Stage">
                    {STAGES.map(([k, l]) => (
                      <Facet key={k} label={l} count={stages[k]} active={stage === k}
                        onClick={() => { setStage(k); setCursor(0); }} />
                    ))}
                  </AxisRow>
                  <AxisRow label="Decided by">
                    {BASES.map(([k, l]) => (
                      <Facet key={k} label={l} count={bases[k]} active={basis === k} ink
                        glyph={BASIS_GLYPH[k]} title={basisLabels[k]}
                        onClick={() => { setBasis(k); setCursor(0); }} />
                    ))}
                    <span style={{ flex: 1, minWidth: 12 }} />
                    <Lens label="From QuickBooks" count={lenses.auto} on={autoOnly}
                      title="Arrived already sitting on a real account"
                      onClick={() => { setAutoOnly((v) => !v); setCursor(0); }} />
                    <Lens label="Weak evidence" count={lenses.weak} on={weakOnly}
                      title="Thin history or low confidence — the calls worth spot-checking"
                      onClick={() => { setWeakOnly((v) => !v); setCursor(0); }} />
                    <Lens label="Reviewed" count={lenses.reviewed} on={showSignedOff}
                      title="Show what has already been signed off"
                      onClick={() => { setShowSignedOff((v) => !v); setCursor(0); }} />
                  </AxisRow>
                </div>

                {rows.length > 0 && (
                  <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "9px 4px",
                    borderTop: `1px solid ${T.line}`, minHeight: 34 }}>
                    <input type="checkbox" checked={allShown} onChange={toggleAll}
                      aria-label="Select every transaction shown"
                      style={{ width: 15, height: 15, cursor: "pointer", accentColor: T.meadow }} />
                    {selRows.length === 0 ? (
                      <span style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted }}>
                        {data?.period?.label} · j/k to move, x to select, a approve, r reviewed
                      </span>
                    ) : pending ? (
                      // Naming the count AND the money before it commits — a bulk action on a
                      // financial record should never be one unconsidered click.
                      <>
                        <span style={{ fontFamily: font.body, fontSize: 12.5, color: T.ink, fontWeight: 600 }}>
                          {pending === "approve" ? "Approve" : "Mark reviewed"} {selRows.length}{" "}
                          transaction{selRows.length === 1 ? "" : "s"} totalling {usd(selTotal)}?
                        </span>
                        <button disabled={busyBulk} style={actionBtn("primary")}
                          onClick={() => runBulk(pending)}>Yes, {pending === "approve" ? "approve" : "mark reviewed"}</button>
                        <button disabled={busyBulk} style={actionBtn()} onClick={() => setPending(null)}>Cancel</button>
                      </>
                    ) : (
                      <>
                        <span style={{ fontFamily: font.body, fontSize: 12.5, color: T.ink, fontWeight: 600 }}>
                          {selRows.length} selected · {usd(selTotal)}
                        </span>
                        <button style={actionBtn("primary")} onClick={() => setPending("approve")}>Approve</button>
                        <button style={actionBtn()} onClick={() => setPending("acknowledge")}>Mark reviewed</button>
                        <button style={actionBtn()} onClick={() => setSel(new Set())}>Clear</button>
                      </>
                    )}
                  </div>
                )}

                {rows.length ? rows.map((tx, i) => (
                  <QueueRow key={tx.id} tx={tx} open={openId === tx.id} ent={entOf(tx.entity)}
                    selected={sel.has(tx.id)} onSelect={() => toggleSel(tx.id)}
                    focused={i === Math.min(cursor, rows.length - 1)}
                    onToggle={() => { setCursor(i); setOpenId(openId === tx.id ? null : tx.id); }}
                    onDone={mark} />
                )) : (
                  <div style={{ fontFamily: font.body, fontSize: 13, color: T.meadowInk, fontWeight: 600, padding: "26px 4px", borderTop: `1px solid ${T.line}` }}>
                    ✓ Nothing here for {data?.period?.label || "this window"}.</div>
                )}
              </>
            )}

            {tab === "esc" && (escalations.length ? escalations.map((e) => (
              <EscRow key={e.id} e={e} open={openId === e.id} isCFO={isCFO}
                onToggle={() => setOpenId(openId === e.id ? null : e.id)} onDone={mark} />
            )) : (
              <div style={{ fontFamily: font.body, fontSize: 13, color: T.meadowInk, fontWeight: 600, padding: "26px 4px", borderTop: `1px solid ${T.line}` }}>
                ✓ No escalations — intercompany is clean.</div>
            ))}
          </Card>
        </div>
      )}
    </StatePanel>
  );
}
