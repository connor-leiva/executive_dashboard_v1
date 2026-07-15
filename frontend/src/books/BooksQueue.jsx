/* Books · Queue — the bookkeeper's seat. Rows expand to Claude's reasoning + the actions
   (approve / change category / escalate). CFO characterizes intercompany escalations. */
import { useState } from "react";
import { postJSON } from "../api";
import { useBooksQueue } from "./useBooks.js";
import { Card, Eyebrow, Pill, StatePanel, EntityChip, Field, QboLink, ENTITY, CHAR_BY_LABEL, font, usd, T } from "./ui.jsx";

const API = import.meta.env.VITE_API_BASE;
const FLAG_LABEL = { over_band: "Unusual amount", first_vendor: "New vendor", possible_1099: "Possible 1099",
  anomaly: "Anomaly", multi_line: "Split txn", intercompany: "Intercompany" };

async function mutate(path, body) { if (API) await postJSON(path, body || {}); }
const flagsOf = (f) => Object.keys(f || {}).filter((k) => f[k]);

function actionBtn(kind) {
  const base = { fontFamily: font.head, fontSize: 12.5, fontWeight: 600, borderRadius: 8, padding: "8px 16px", cursor: "pointer" };
  if (kind === "primary") return { ...base, color: T.white, background: T.meadow, border: "none" };
  if (kind === "danger") return { ...base, color: T.poppyText, background: T.white, border: `1px solid ${T.line}` };
  return { ...base, color: T.slate, background: T.white, border: `1px solid ${T.line}` };
}

function StatCard({ label, value, color }) {
  return (
    <Card style={{ padding: "15px 17px" }}>
      <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.secondary, marginBottom: 7 }}>{label}</div>
      <div style={{ fontFamily: font.head, fontSize: 23, fontWeight: 700, color: color || T.ink, fontVariantNumeric: "tabular-nums" }}>{value}</div>
    </Card>
  );
}

function QueueRow({ tx, open, onToggle, onDone }) {
  const [editing, setEditing] = useState(false);
  const [cat, setCat] = useState(tx.suggest || "");
  const [busy, setBusy] = useState(false);
  const run = async (fn) => { setBusy(true); try { await fn(); onDone(tx.id); } finally { setBusy(false); } };
  return (
    <div style={{ borderTop: `1px solid ${T.line}` }}>
      <button onClick={onToggle} style={{ width: "100%", display: "flex", alignItems: "center", gap: 14,
        padding: "13px 4px", background: "transparent", border: "none", cursor: "pointer", textAlign: "left" }}>
        <span style={{ width: 44, fontFamily: font.body, fontSize: 11.5, color: T.muted, flexShrink: 0 }}>{tx.date}</span>
        <span style={{ width: 132, flexShrink: 0 }}><EntityChip k={tx.entity} /></span>
        <span style={{ flex: 1, fontFamily: font.body, fontSize: 13, fontWeight: 600, color: T.ink }}>{tx.vendor}</span>
        {tx.suggest && <span style={{ fontFamily: font.body, fontSize: 11.5, fontWeight: 600, color: T.teal,
          background: T.mist, borderRadius: 6, padding: "3px 9px", flexShrink: 0 }}>{tx.suggest}{tx.conf ? ` · ${tx.conf}` : ""}</span>}
        <span style={{ width: 82, textAlign: "right", fontFamily: font.head, fontSize: 13.5, fontWeight: 600,
          color: T.ink, fontVariantNumeric: "tabular-nums", flexShrink: 0 }}>{usd(tx.amount)}</span>
        <span style={{ color: T.muted, fontSize: 12, flexShrink: 0, transform: open ? "rotate(180deg)" : "none", transition: "transform .15s" }}>▾</span>
      </button>
      {open && (
        <div style={{ background: T.parchment, borderRadius: 10, padding: "14px 16px", margin: "0 0 13px" }}>
          {tx.reason && (
            <div style={{ fontFamily: font.body, fontSize: 12.5, color: T.secondary, lineHeight: 1.55 }}>
              <span style={{ fontWeight: 700, color: T.ink }}>Claude's read: </span>{tx.reason}</div>
          )}
          <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 7, flexWrap: "wrap" }}>
            <span style={{ fontFamily: font.body, fontSize: 11, color: T.muted }}>Source: {tx.source}</span>
            {flagsOf(tx.flags).map((k) => <Pill key={k} tone="warn">{FLAG_LABEL[k] || k}</Pill>)}
          </div>
          <div style={{ display: "grid", gap: 4, marginTop: 10 }}>
            <Field label="Type" value={tx.qbo_type} />
            <Field label="Memo" value={tx.memo} />
            <Field label="Currently on" value={tx.current_category} />
            <Field label="Paid from" value={tx.bank_account} />
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
          ) : (
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

export default function BooksQueue({ isCFO = false }) {
  const { data, loading, error, retry } = useBooksQueue();
  const [tab, setTab] = useState("queue");
  const [filter, setFilter] = useState("all");
  const [openId, setOpenId] = useState(null);
  const [done, setDone] = useState(() => new Set());
  const mark = (id) => { setDone((d) => new Set(d).add(id)); setOpenId(null); };

  const approvals = (data?.approvals || []).filter((r) => !done.has(r.id) && (filter === "all" || r.entity === filter));
  const escalations = (data?.escalations || []).filter((r) => !done.has(r.id));

  return (
    <StatePanel loading={loading} error={error} retry={retry}>
      {data && (
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 14 }}>
            <StatCard label="Awaiting approval" value={data.stats?.awaiting ?? approvals.length}
              color={(data.stats?.awaiting ?? approvals.length) ? T.daffodilText : T.meadowInk} />
            <StatCard label="Escalated to CFO" value={data.stats?.escalated ?? escalations.length}
              color={(data.stats?.escalated ?? escalations.length) ? T.poppyText : T.meadowInk} />
            <StatCard label="Approved this week" value={data.stats?.approved_7d ?? 0} color={T.meadowInk} />
          </div>

          <Card style={{ padding: "18px 22px 10px" }}>
            <div style={{ display: "flex", gap: 6, marginBottom: 6, flexWrap: "wrap", alignItems: "center" }}>
              {[["queue", `Approval queue · ${approvals.length}`], ["esc", `Escalations · ${escalations.length}`]].map(([k, l]) => (
                <button key={k} onClick={() => setTab(k)} style={{ fontFamily: font.head, fontSize: 12.5, fontWeight: 600,
                  color: tab === k ? T.ink : T.muted, background: tab === k ? T.parchment : "transparent", border: "none",
                  borderBottom: tab === k ? `2px solid ${k === "esc" ? T.poppy : T.daffodilText}` : "2px solid transparent",
                  borderRadius: "8px 8px 0 0", padding: "8px 14px", cursor: "pointer" }}>{l}</button>
              ))}
              <span style={{ flex: 1 }} />
              {tab === "queue" && [["all", "All"], ...Object.entries(ENTITY).filter(([k]) => ["ulrg", "springb", "sympli"].includes(k)).map(([k, e]) => [k, e.label])].map(([k, l]) => (
                <button key={k} onClick={() => setFilter(k)} style={{ fontFamily: font.body, fontSize: 11.5, fontWeight: 600,
                  color: filter === k ? T.ink : T.muted, background: filter === k ? T.parchment : "transparent",
                  border: `1px solid ${filter === k ? T.line : "transparent"}`, borderRadius: 99, padding: "5px 12px", cursor: "pointer" }}>{l}</button>
              ))}
            </div>

            {tab === "queue" && (approvals.length ? approvals.map((tx) => (
              <QueueRow key={tx.id} tx={tx} open={openId === tx.id}
                onToggle={() => setOpenId(openId === tx.id ? null : tx.id)} onDone={mark} />
            )) : (
              <div style={{ fontFamily: font.body, fontSize: 13, color: T.meadowInk, fontWeight: 600, padding: "26px 4px", borderTop: `1px solid ${T.line}` }}>
                ✓ Queue clear — everything's approved and posted.</div>
            ))}

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
