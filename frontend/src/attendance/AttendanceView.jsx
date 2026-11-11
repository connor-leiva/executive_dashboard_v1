/* Session Attendance. Topics land in a type the same way ad campaigns land in a brand. */
import { useEffect, useState } from "react";

import { API_BASE, getJSON, patchJSON, postJSON } from "../api";
import { T } from "../theme.js";

export const sampleAttendance = {
  connected: true,
  last_synced_at: "2026-11-11T12:00:00+00:00",
  last_error: null,
  backfilled: true,
  using_defaults: true,
  rules: null,
  defaults: [
    { match: "contains", value: "edge", label: "Edge Webinar" },
    { match: "contains", value: "forum", label: "Forum" },
  ],
  by_type: [
    { name: "Edge Webinar", sessions: 6, participants: 84 },
    { name: "Forum", sessions: 2, participants: 31 },
    { name: "Unmapped", sessions: 1, participants: 4 },
  ],
  unmapped: ["Weekly Huddle"],
  session_count: 9,
  sessions: [
    { id: "1", topic: "Edge Weekly Workshop", kind: "webinar", type: "Edge Webinar",
      host: "justin@springb.com", started_at: "2026-11-10T18:00:00Z", duration_minutes: 60, participants: 22 },
    { id: "2", topic: "Forum Dallas Office Hours", kind: "meeting", type: "Forum",
      host: "sarah@liveutah.com", started_at: "2026-11-06T17:00:00Z", duration_minutes: 45, participants: 14 },
    { id: "3", topic: "Weekly Huddle", kind: "meeting", type: "Unmapped",
      host: "connor@springb.com", started_at: "2026-11-04T16:00:00Z", duration_minutes: 25, participants: 4 },
  ],
};

function when(iso) {
  if (!iso) return "Not yet";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export default function AttendanceView({ fixture }) {
  const [data, setData] = useState(fixture || null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [rules, setRules] = useState(null);
  const [note, setNote] = useState(null);

  async function load() {
    if (fixture || !API_BASE) {
      setData(fixture || sampleAttendance);
      return;
    }
    try {
      const d = await getJSON("/attendance");
      setData(d);
      setError(null);
    } catch (e) {
      setError(e.message || "Couldn't load Session Attendance.");
    }
  }

  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (data) setRules(data.rules || data.defaults || []);
  }, [data]);

  async function sync(backfill) {
    setBusy(true);
    setNote(null);
    try {
      if (!API_BASE) {
        setNote(backfill ? "This preview has no Zoom connection." : "This preview has no Zoom connection.");
        return;
      }
      const out = await postJSON("/attendance/sync", { backfill });
      setNote(`Synced ${out.sessions || 0} sessions, ${out.participants || 0} participants.`);
      await load();
    } catch (e) {
      setError(e.message || "Zoom sync failed.");
    } finally {
      setBusy(false);
    }
  }

  async function saveRules(next) {
    setBusy(true);
    try {
      if (!API_BASE) {
        setNote("This preview has no API, so the mapping is not saved.");
        return;
      }
      await patchJSON("/attendance/rules", { rules: next });
      await load();
    } catch (e) {
      setError(e.message || "Couldn't save the mapping.");
    } finally {
      setBusy(false);
    }
  }

  if (!data) {
    return <p style={{ fontFamily: "var(--font-text)", color: T.slate }}>Loading Session Attendance…</p>;
  }

  return (
    <div style={{ padding: 8, display: "grid", gap: 16 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "baseline" }}>
        <div>
          <h1 style={{ fontFamily: "var(--font-display)", fontSize: 22, margin: 0, color: T.ink }}>Session Attendance</h1>
          <p style={{ fontFamily: "var(--font-text)", fontSize: 13, color: T.slate, margin: "6px 0 0" }}>
            Last Synced {when(data.last_synced_at)}
            {data.backfilled ? " · Backfill complete" : ""}
            {data.last_error ? ` · ${data.last_error}` : ""}
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" disabled={busy} onClick={() => sync(false)}
                  style={btn(false)}>{busy ? "Syncing" : "Sync Now"}</button>
          <button type="button" disabled={busy} onClick={() => sync(true)} style={btn(true)}>Backfill</button>
        </div>
      </div>
      {error && <p style={{ color: T.poppyText, fontFamily: "var(--font-text)", fontSize: 13 }}>{error}</p>}
      {note && <p style={{ color: T.slate, fontFamily: "var(--font-text)", fontSize: 13 }}>{note}</p>}

      <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
        {(data.by_type || []).map((g) => (
          <div key={g.name} style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 12, padding: "12px 14px", minWidth: 140 }}>
            <div style={{ fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.muted }}>{g.name}</div>
            <div style={{ fontFamily: "var(--font-display)", fontSize: 20, color: T.ink, marginTop: 4 }}>{g.participants}</div>
            <div style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.slate }}>{g.sessions} sessions</div>
          </div>
        ))}
      </div>

      {(data.unmapped || []).length > 0 && (
        <div style={{ background: "#F8E6E1", borderRadius: 12, padding: "12px 14px" }}>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 15, color: T.ink }}>Unmapped Sessions</div>
          <p style={{ fontFamily: "var(--font-text)", fontSize: 13, color: T.slate, margin: "6px 0 0" }}>
            {data.unmapped.join(" · ")}. Add a rule below and they move on the next read.
          </p>
        </div>
      )}

      <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 12, overflow: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "var(--font-text)", fontSize: 13 }}>
          <thead>
            <tr style={{ color: T.muted, textAlign: "left" }}>
              {["Session", "Type", "Host", "When", "People"].map((h) => (
                <th key={h} style={{ padding: "10px 12px", fontWeight: 600 }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(data.sessions || []).map((row) => (
              <tr key={row.id} style={{ borderTop: `1px solid ${T.line}` }}>
                <td style={{ padding: "10px 12px", color: T.ink }}>{row.topic}</td>
                <td style={{ padding: "10px 12px" }}>{row.type}</td>
                <td style={{ padding: "10px 12px", color: T.slate }}>{row.host || "—"}</td>
                <td style={{ padding: "10px 12px" }}>{when(row.started_at)}</td>
                <td style={{ padding: "10px 12px", textAlign: "right" }}>{row.participants}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 12, padding: 14 }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 15, color: T.ink }}>Session Type Mapping</div>
        <p style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.slate, margin: "4px 0 10px" }}>
          {data.using_defaults ? "Using the default rules." : "Using rules saved for this workspace."}
        </p>
        {(rules || []).map((rule, i) => (
          <div key={i} style={{ display: "flex", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
            <select value={rule.match} aria-label="Match kind"
                    onChange={(e) => setRules(rules.map((r, j) => j === i ? { ...r, match: e.target.value } : r))}
                    style={field}>
              <option value="contains">contains</option>
              <option value="prefix">prefix</option>
            </select>
            <input aria-label="Match text" value={rule.value}
                   onChange={(e) => setRules(rules.map((r, j) => j === i ? { ...r, value: e.target.value } : r))}
                   style={{ ...field, flex: 1 }} />
            <input aria-label="Session type" value={rule.label}
                   onChange={(e) => setRules(rules.map((r, j) => j === i ? { ...r, label: e.target.value } : r))}
                   style={{ ...field, flex: 1 }} />
          </div>
        ))}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button type="button" style={btn(false)} onClick={() => setRules([...(rules || []), { match: "contains", value: "", label: "" }])}>Add Rule</button>
          <button type="button" style={btn(true)} disabled={busy} onClick={() => saveRules(rules)}>Save Mapping</button>
          <button type="button" style={btn(false)} disabled={busy} onClick={() => saveRules(null)}>Use Defaults</button>
        </div>
      </div>
    </div>
  );
}

const field = { fontFamily: "var(--font-text)", fontSize: 13, padding: "6px 8px", border: `1px solid ${T.line}`, borderRadius: 8, color: T.ink, background: T.white };
function btn(primary) {
  return {
    fontFamily: "var(--font-text)", fontSize: 13, fontWeight: 600, borderRadius: 8, padding: "7px 12px",
    cursor: "pointer", border: `1px solid ${primary ? T.evergreen : T.line}`,
    background: primary ? T.evergreen : T.white, color: primary ? T.parchment : T.ink,
  };
}
