/* Books · Queue — the bookkeeper's seat. Approve / recategorize / escalate each txn;
   CFO characterizes intercompany escalations. Every action is recorded server-side. */
import { useState } from "react";
import { T } from "../theme.js";
import { postJSON } from "../api";
import { useBooksQueue } from "./useBooks.js";
import { Card, Eyebrow, Pill, StatePanel, EntityChip, CHAR_LABEL, font, usd } from "./ui.jsx";

const API = import.meta.env.VITE_API_BASE;
const FLAG_LABEL = { over_band: "Unusual amount", first_vendor: "New vendor",
  possible_1099: "Possible 1099", anomaly: "Anomaly", multi_line: "Split txn", intercompany: "Intercompany" };

async function mutate(path, body) {          // no-op in offline/sample mode
  if (!API) return;
  await postJSON(path, body || {});
}

function actionBtn(kind) {
  const base = { fontFamily: font.body, fontSize: 12, fontWeight: 600, borderRadius: 8,
                 padding: "6px 12px", cursor: "pointer" };
  if (kind === "primary") return { ...base, color: T.onDark, background: T.evergreen, border: "none" };
  return { ...base, color: T.slate, background: T.parchment, border: `1px solid ${T.line}` };
}

function StatCard({ label, value }) {
  return (
    <Card style={{ flex: 1, minWidth: 140, padding: 16 }}>
      <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted }}>{label}</div>
      <div style={{ fontFamily: font.head, fontSize: 24, fontWeight: 700, color: T.ink, marginTop: 3 }}>{value}</div>
    </Card>
  );
}

function QueueRow({ row, onDone }) {
  const [editing, setEditing] = useState(false);
  const [cat, setCat] = useState(row.suggest || "");
  const [busy, setBusy] = useState(false);

  const run = async (fn) => { setBusy(true); try { await fn(); onDone(row.id); } finally { setBusy(false); } };

  return (
    <div style={{ padding: "14px 0", borderBottom: `1px solid ${T.line}` }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div style={{ minWidth: 220, flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <span style={{ fontFamily: font.head, fontSize: 14.5, fontWeight: 600, color: T.ink }}>{row.vendor}</span>
            <EntityChip k={row.entity} />
            <span style={{ fontFamily: font.body, fontSize: 12, color: T.muted }}>{row.date} · {row.source}</span>
          </div>
          <div style={{ fontFamily: font.body, fontSize: 12.5, color: T.tertiary, marginTop: 5 }}>
            Suggested: <strong style={{ color: T.secondary }}>{row.suggest || "—"}</strong>
            {row.conf && <> · {row.conf} confidence</>}
          </div>
          {row.reason && <div style={{ fontFamily: font.body, fontSize: 12, color: T.muted, marginTop: 3 }}>{row.reason}</div>}
          {row.flags && Object.keys(row.flags).some((k) => row.flags[k]) && (
            <div style={{ display: "flex", gap: 6, marginTop: 7, flexWrap: "wrap" }}>
              {Object.keys(row.flags).filter((k) => row.flags[k]).map((k) => (
                <Pill key={k} tone="warn">{FLAG_LABEL[k] || k}</Pill>
              ))}
            </div>
          )}
        </div>
        <div style={{ fontFamily: font.head, fontSize: 16, fontWeight: 700,
                      color: row.amount < 0 ? T.ink : T.meadowInk, whiteSpace: "nowrap" }}>
          {row.amount < 0 ? "(" + usd(row.amount) + ")" : usd(row.amount)}</div>
      </div>

      {editing ? (
        <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
          <input value={cat} onChange={(e) => setCat(e.target.value)} placeholder="Account name"
            style={{ flex: 1, minWidth: 200, fontFamily: font.body, fontSize: 13, color: T.ink,
                     background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "7px 10px" }} />
          <button disabled={busy || !cat.trim()} style={actionBtn("primary")}
            onClick={() => run(() => mutate(`/books/txn/${row.id}/recategorize`, { category: cat.trim() }))}>
            Save as “{cat.trim().slice(0, 24)}”</button>
          <button style={actionBtn()} onClick={() => setEditing(false)}>Cancel</button>
        </div>
      ) : (
        <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
          <button disabled={busy} style={actionBtn("primary")}
            onClick={() => run(() => mutate(`/books/txn/${row.id}/approve`))}>
            Approve{row.suggest ? ` as ${row.suggest.slice(0, 22)}` : ""}</button>
          <button disabled={busy} style={actionBtn()} onClick={() => setEditing(true)}>Recategorize</button>
          <button disabled={busy} style={actionBtn()}
            onClick={() => run(() => mutate(`/books/txn/${row.id}/escalate`))}>Escalate</button>
        </div>
      )}
    </div>
  );
}

function EscRow({ row, isCFO, onDone }) {
  const [char, setChar] = useState("loan");
  const [busy, setBusy] = useState(false);
  const run = async () => { setBusy(true); try {
    await mutate(`/books/ic/${row.id}/characterize`, { characterization: char }); onDone(row.id);
  } finally { setBusy(false); } };
  return (
    <div style={{ padding: "14px 0", borderBottom: `1px solid ${T.line}` }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontFamily: font.head, fontSize: 14.5, fontWeight: 600, color: T.ink }}>{row.label}</div>
          <div style={{ fontFamily: font.body, fontSize: 12, color: T.muted, marginTop: 3 }}>{row.date} · {row.reason}</div>
        </div>
        <div style={{ fontFamily: font.head, fontSize: 16, fontWeight: 700, color: T.poppyText }}>{usd(row.amount)}</div>
      </div>
      <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.daffodilText, background: T.daffodilBg,
                    borderRadius: 8, padding: "7px 10px", marginTop: 8 }}>{row.tax_note}</div>
      {isCFO ? (
        <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
          <select value={char} onChange={(e) => setChar(e.target.value)} style={{ fontFamily: font.body,
            fontSize: 12.5, color: T.ink, background: T.white, border: `1px solid ${T.line}`,
            borderRadius: 8, padding: "7px 10px" }}>
            {Object.keys(CHAR_LABEL).map((k) => <option key={k} value={k}>{CHAR_LABEL[k]}</option>)}
          </select>
          <button disabled={busy} style={actionBtn("primary")} onClick={run}>Characterize</button>
        </div>
      ) : (
        <div style={{ fontFamily: font.body, fontSize: 12, color: T.muted, marginTop: 8 }}>
          Awaiting CFO characterization.</div>
      )}
    </div>
  );
}

export default function BooksQueue({ isCFO = false }) {
  const { data, loading, error, retry } = useBooksQueue();
  const [done, setDone] = useState(() => new Set());
  const mark = (id) => setDone((d) => new Set(d).add(id));

  const approvals = (data?.approvals || []).filter((r) => !done.has(r.id));
  const escalations = (data?.escalations || []).filter((r) => !done.has(r.id));

  return (
    <StatePanel loading={loading} error={error} retry={retry}>
      {data && (
        <div style={{ display: "grid", gap: 16 }}>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            <StatCard label="Awaiting approval" value={data.stats?.awaiting ?? approvals.length} />
            <StatCard label="Escalated to CFO" value={data.stats?.escalated ?? escalations.length} />
            <StatCard label="Approved this week" value={data.stats?.approved_7d ?? 0} />
          </div>

          <Card>
            <Eyebrow>Awaiting approval</Eyebrow>
            {approvals.length === 0
              ? <div style={{ fontFamily: font.body, fontSize: 13, color: T.muted, padding: "10px 0" }}>All clear — nothing waiting.</div>
              : approvals.map((r) => <QueueRow key={r.id} row={r} onDone={mark} />)}
          </Card>

          {escalations.length > 0 && (
            <Card>
              <Eyebrow>Escalations · CFO decision</Eyebrow>
              {escalations.map((r) => <EscRow key={r.id} row={r} isCFO={isCFO} onDone={mark} />)}
            </Card>
          )}
        </div>
      )}
    </StatePanel>
  );
}
