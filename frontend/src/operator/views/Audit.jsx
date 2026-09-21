import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { ago } from "../format.js";
import { collapseBy, describe, target } from "../phrases.js";
import { Btn, Card, Chip, Empty, Loading, LoadError, Mono, Notice, Seg } from "../primitives.jsx";
import { A, TYPE } from "../tokens.js";

const th = { fontFamily: TYPE.text, fontSize: 10, fontWeight: 600, letterSpacing: ".14em", textTransform: "uppercase", color: A.mute, textAlign: "left", padding: "0 10px 9px", whiteSpace: "nowrap" };
const td = { padding: "11px 10px", borderTop: `1px solid ${A.lineSoft}`, fontFamily: TYPE.text, fontSize: 12.5, color: A.ink, verticalAlign: "top" };

const SCOPES = [["all", "Everything"], ["axcion", "Axcion staff"], ["tenants", "Workspace teams"]];

/* An event's own scope, as the API reported it. The API emits "axcion"; before the September
   2026 rename it emitted "acumyn", and the API and this console are separate Railway services
   that do not deploy together. During that window this console can be reading an old API's
   rows, so both spellings mean the operator trail. Drop the "acumyn" arm at Phase 10 of
   AXCION-REBRAND-SPEC.md.

   A mismatch here is silent and wrong rather than broken: every operator action would be
   labelled "Workspace team", which is precisely the distinction this column exists to draw. */
const isOperatorTrail = (s) => s === "axcion" || s === "acumyn";
const BAD = /suspended|syncs_frozen|revoked|deleted|failed/;

function csvCell(value) {
  const s = value == null ? "" : String(value);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/* The rows on screen, as a file. Built in the browser from what was already read, so an export can
   never contain more than the operator has looked at. */
function exportCsv(events) {
  const head = ["when", "scope", "workspace", "who", "action", "detail", "reason", "from"];
  const lines = events.map((e) => [e.at, e.scope, e.tenant_slug, e.who, describe(e), target(e, { withReason: false }), e.reason, e.ip]
    .map(csvCell).join(","));
  const blob = new Blob([[head.join(","), ...lines].join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `axcion-audit-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function AuditView({ onOpen }) {
  const [scope, setScope] = useState("all");
  const [events, setEvents] = useState([]);
  const [next, setNext] = useState(null);
  const [retention, setRetention] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const seq = useRef(0);

  const load = useCallback(async (before) => {
    const mine = ++seq.current;
    setLoading(true);
    setError(null);
    try {
      const r = await api.audit({ scope, before });
      if (mine !== seq.current) return;
      setEvents((prev) => (before ? [...prev, ...r.events] : r.events));
      setNext(r.next_before);
      setRetention(r.retention_days);
    } catch (e) {
      if (mine === seq.current) setError(e);
    } finally {
      if (mine === seq.current) setLoading(false);
    }
  }, [scope]);

  useEffect(() => { setEvents([]); load(null); }, [load]);

  const rows = collapseBy(events, (e) => [e.scope, e.who, e.action, e.tenant_slug, describe(e), target(e)].join("|"));

  if (loading && !events.length && !error) return <Loading label="Reading the audit trail" />;
  if (error && !events.length) return <LoadError error={error} onRetry={() => load(null)} />;

  return (
    <Card title="Audit" pad={0}
      sub={`Every change across the platform: what Axcion staff did, from the operator trail, and what each workspace's own team did, from its audit log. Sign-ins and second-factor checks are left out; they are not changes.${retention ? ` Operator entries are kept ${retention} days.` : ""}`}
      right={<Seg label="Filter the audit trail" value={scope} onChange={setScope} options={SCOPES} />}>
      {events.length === 0 ? (
        <Empty title="Nothing recorded">
          {scope === "axcion" ? "No operator has changed anything yet." : "No changes have been recorded in this scope."}
        </Empty>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", minWidth: 720, borderCollapse: "collapse" }}>
            <thead><tr>
              {["When", "Who", "Action", "Target", "From"].map((h, i) => (
                <th key={h} scope="col" style={{ ...th, paddingLeft: i === 0 ? 16 : 10, paddingRight: i === 4 ? 16 : 10 }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {rows.map((e) => {
                const bad = BAD.test(e.action);
                const detail = target(e, { withReason: false });
                return (
                  <tr key={e.id} className="ac-row">
                    <td style={{ ...td, paddingLeft: 16, whiteSpace: "nowrap" }}>
                      <Mono size={11} c={A.mute}><span title={new Date(e.at).toLocaleString()}>{ago(e.at)}</span></Mono>
                    </td>
                    <td style={td}>
                      <div style={{ fontWeight: 500, overflowWrap: "anywhere" }}>{e.who || "Unknown"}</div>
                      <div style={{ marginTop: 3 }}>
                        <Chip state={isOperatorTrail(e.scope) ? "trial" : undefined}>{isOperatorTrail(e.scope) ? "Axcion" : "Workspace team"}</Chip>
                      </div>
                    </td>
                    <td style={{ ...td, color: bad ? A.stop : A.ink, fontWeight: 500 }}>
                      {describe(e)}{e.count > 1 ? <Mono size={10.5} c={A.mute}> ×{e.count}</Mono> : null}
                      {e.reason ? (
                        <div style={{ fontFamily: TYPE.text, fontSize: 11, color: A.mute, fontWeight: 400, marginTop: 3, lineHeight: 1.5, textWrap: "pretty" }}>Reason: {e.reason}</div>
                      ) : null}
                    </td>
                    <td style={td}>
                      {e.tenant_slug ? (
                        <button type="button" className="ac-tag" onClick={() => onOpen(e.tenant_slug, "activity")} style={{
                          fontFamily: TYPE.data, fontSize: 10.5, color: A.body, background: A.chip, border: "none",
                          borderRadius: 4, padding: "2px 6px", cursor: "pointer", marginBottom: detail ? 4 : 0,
                        }}>{e.tenant_slug}</button>
                      ) : null}
                      {detail ? <div><Mono size={11} c={A.body} style={{ overflowWrap: "anywhere" }}>{detail}</Mono></div> : null}
                    </td>
                    <td style={{ ...td, paddingRight: 16 }}><Mono size={11} c={A.mute}>{e.ip || "—"}</Mono></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {error && events.length ? <div style={{ padding: "0 16px 12px" }}><Notice tone="error">{error.message}</Notice></div> : null}
      <div style={{ padding: "12px 16px", borderTop: `1px solid ${A.lineSoft}`, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        {next ? <Btn small busy={loading} onClick={() => load(next)}>Load older</Btn> : null}
        <Btn small kind="quiet" disabled={!events.length} onClick={() => exportCsv(events)}>Export these rows as CSV</Btn>
        <span style={{ fontFamily: TYPE.text, fontSize: 11, color: A.mute }}>
          {events.length} {events.length === 1 ? "entry" : "entries"} shown{next ? ", more available" : ""}. "From" is the network address an operator acted from; it is not recorded for workspace teams.
        </span>
      </div>
    </Card>
  );
}
