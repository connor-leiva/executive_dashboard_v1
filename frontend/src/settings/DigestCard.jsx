/* Settings › Lead sources › Weekly digest (docs/specs/ulrg-reporting-dashboard-port.md §4.6, phase 6).

   Every value the legacy digest kept in code or environment variables is a field here: when it
   sends, to whom, how many agents it names, the coaching bar it scores them against, the ClickUp
   list its tasks go to, and the model that may rewrite its sentences. Off until somebody turns it
   on; a test send works either way. Built from Settings' kit and the Recruiting page's inputs. */
import { useCallback, useEffect, useState } from "react";
import { T } from "../theme.js";
import { getJSON, postJSON, putJSON } from "../api.js";
import { Card, SBtn } from "./kit.jsx";
import { Pill } from "../books/ui.jsx";

const FB = "var(--font-text)", FD = "var(--font-display)", FM = "var(--font-data)";
const label = { display: "block", fontFamily: FB, fontSize: 12, fontWeight: 600, color: T.slate, marginTop: 14 };
const input = { width: "100%", boxSizing: "border-box", fontFamily: FB, fontSize: 13, color: T.ink, background: T.white,
                border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 10px", marginTop: 4 };
const num = { ...input, fontFamily: FM, width: 88, textAlign: "right" };
const meta = { fontFamily: FB, fontSize: 12, color: T.muted, lineHeight: 1.5 };
const h3 = { fontFamily: FB, fontSize: 13, fontWeight: 600, color: T.ink, marginTop: 24 };
const errOf = (e, f) => String((e && (e.detail || e.message)) || f);
const pct = (r) => (r == null || r === "" ? "" : String(Math.round(r * 1000) / 10));
const pad = (n) => String(n).padStart(2, "0");
const STATUS_TONE = { sent: "good", queued: "warn", failed: "bad", skipped: "muted" };

function toDraft(v) {
  return {
    is_active: v.is_active, send_weekday: v.send_weekday, time: `${pad(v.send_hour)}:${pad(v.send_minute)}`,
    timezone: v.timezone || "", recipients: v.recipients.join("\n"), max_agents: v.max_agents, min_leads: v.min_leads,
    include_reinforce: v.include_reinforce, benchmarks: JSON.parse(JSON.stringify(v.benchmarks)),
    clickup_list_id: v.clickup_list_id || "", clickup_assignee: v.clickup_assignee || "", clickup_token: "", model: v.model,
  };
}

export default function DigestCard({ bizKey, group }) {
  const base = `/businesses/${bizKey}/lead-sources/${group.key}/digest`;
  const [view, setView] = useState(null);
  const [d, setD] = useState(null);
  const [history, setHistory] = useState([]);
  const [busy, setBusy] = useState(null);
  const [err, setErr] = useState(null);
  const [note, setNote] = useState(null);

  const load = useCallback(() => {
    getJSON(base).then((v) => { setView(v); setD(toDraft(v)); }).catch((e) => setErr(errOf(e, "Couldn't load the digest.")));
    getJSON(`${base}/deliveries`).then((h) => setHistory(h.deliveries)).catch(() => {});
  }, [base]);
  useEffect(load, [load]);

  if (!view || !d) {
    return <Card><div style={{ fontFamily: FD, fontSize: 16, fontWeight: 600, color: T.ink }}>Weekly digest</div>
      <div style={{ ...meta, marginTop: 4, color: err ? T.poppyText : T.muted }}>{err || "Loading…"}</div></Card>;
  }
  const set = (k) => (e) => { setD({ ...d, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }); setNote(null); };
  const setBar = (path, side) => (e) => {
    const b = JSON.parse(JSON.stringify(d.benchmarks));
    const [group0, key] = path;
    const raw = e.target.value;
    const v = raw === "" ? "" : Number(raw);
    if (key) b[group0][key][side] = v === "" ? "" : v / 100;
    else b[group0][side] = v;
    setD({ ...d, benchmarks: b }); setNote(null);
  };

  async function save() {
    setBusy("save"); setErr(null); setNote(null);
    const [h, m] = (d.time || "06:00").split(":").map(Number);
    try {
      const body = {
        is_active: d.is_active, send_weekday: Number(d.send_weekday), send_hour: h, send_minute: m,
        timezone: d.timezone.trim() || null, recipients: d.recipients.split(/[\n,]/).map((x) => x.trim()).filter(Boolean),
        max_agents: Number(d.max_agents), min_leads: Number(d.min_leads), include_reinforce: d.include_reinforce,
        benchmarks: d.benchmarks, clickup_list_id: d.clickup_list_id.trim() || null,
        clickup_assignee: d.clickup_assignee.trim() || null, model: d.model.trim(),
      };
      if (d.clickup_token) body.clickup_token = d.clickup_token;
      const v = await putJSON(base, body);
      setView(v); setD(toDraft(v)); setNote("Saved.");
    } catch (e) { setErr(errOf(e, "Couldn't save the digest.")); }
    finally { setBusy(null); }
  }

  async function test(kind) {
    setBusy(kind); setErr(null); setNote(null);
    try {
      if (kind === "test") {
        const r = await postJSON(`${base}/test`, {});
        setNote(r.status === "sent" ? `Test sent to ${r.recipients.join(", ")}.` : `The test didn't send: ${r.last_error || r.status}.`);
      } else {
        await postJSON(`${base}/clickup-test`, {});
        setNote("A test task is in the ClickUp list.");
      }
      load();
    } catch (e) { setErr(errOf(e, "That didn't work.")); }
    finally { setBusy(null); }
  }

  const tz = view.timezone || view.workspace_timezone;
  const transitions = Object.entries(d.benchmarks.transitions);
  const bars = [["speed_first_touch_min", "Minutes to first touch", "min", true], ["pct_5plus_attempts", "Leads with 5+ attempts", "%"],
                ["multichannel_24h_pct", "Two channels in the first day", "%"]];
  return (
    <Card>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 320px" }}>
          <div style={{ fontFamily: FD, fontSize: 16, fontWeight: 600, color: T.ink }}>Weekly digest · {group.display_name}</div>
          <div style={{ fontFamily: FB, fontSize: 13, color: T.secondary, marginTop: 4, lineHeight: 1.5, maxWidth: 640 }}>
            A coaching email for whoever runs the team: who to sit down with, who to mention, who to recognize. Scored on the last
            30 days of this source's leads against the bar below.
          </div>
        </div>
        <Pill tone={view.is_active ? "good" : "muted"}>{view.is_active ? `On · ${view.weekdays[view.send_weekday]}s` : "Off"}</Pill>
      </div>

      <label style={{ ...label, display: "inline-flex", alignItems: "center", gap: 8, marginTop: 16 }}>
        <input type="checkbox" checked={d.is_active} onChange={set("is_active")} /> Send it every week
      </label>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 180px), 1fr))", columnGap: 16 }}>
        <label style={label}>Day
          <select style={input} value={d.send_weekday} onChange={set("send_weekday")}>
            {view.weekdays.map((w, i) => <option key={w} value={i}>{w}</option>)}
          </select>
        </label>
        <label style={label}>Time
          <input type="time" style={input} value={d.time} onChange={set("time")} />
        </label>
        <label style={label}>Time zone
          <input style={input} value={d.timezone} onChange={set("timezone")} placeholder={`Workspace · ${view.workspace_timezone}`} />
        </label>
      </div>
      <div style={{ ...meta, marginTop: 6 }}>Sends at {d.time} {tz}. A week's digest goes once, however many times the scheduler looks.</div>

      <label style={label}>Recipients
        <textarea rows={3} style={{ ...input, fontFamily: FM, lineHeight: 1.6 }} value={d.recipients} onChange={set("recipients")}
          placeholder="One address per line" />
      </label>
      {view.unsubscribed.length > 0 && (
        <div style={{ ...meta, marginTop: 6 }}>Unsubscribed, so skipped: {view.unsubscribed.join(", ")}</div>
      )}

      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label style={label}>Agents per email
          <input type="number" min="1" max="12" style={num} value={d.max_agents} onChange={set("max_agents")} />
        </label>
        <label style={label}>Minimum leads to be scored
          <input type="number" min="1" max="100" style={num} value={d.min_leads} onChange={set("min_leads")} />
        </label>
        <label style={{ ...label, display: "inline-flex", alignItems: "center", gap: 8, paddingBottom: 10 }}>
          <input type="checkbox" checked={d.include_reinforce} onChange={set("include_reinforce")} /> Recognize one agent each week
        </label>
      </div>

      <div style={h3}>Coaching bar</div>
      <div style={{ ...meta, marginTop: 4 }}>
        The rate each step has to hold. Agents are scored against the floor; the saved floors compound to a {view.floor_close_pct}% close
        rate and the targets to {view.target_close_pct}%.
      </div>
      <div style={{ marginTop: 10, border: `1px solid ${T.line}`, borderRadius: 10, overflow: "hidden" }}>
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 100px 100px", gap: 12, padding: "8px 12px",
          background: T.parchment, borderBottom: `1px solid ${T.line}` }}>
          {["Step", "Floor", "Target"].map((x, i) => <span key={x} style={{ fontFamily: FB, fontSize: 12, fontWeight: 600, color: T.muted, textAlign: i ? "right" : "left" }}>{x}</span>)}
        </div>
        {transitions.map(([k, v], i) => (
          <div key={k} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 100px 100px", gap: 12, alignItems: "center",
            padding: "6px 12px", borderTop: i ? `1px solid ${T.line}` : "none" }}>
            <span style={{ fontFamily: FB, fontSize: 13, color: T.ink }}>{view.transition_labels[k] || k}</span>
            {["floor", "target"].map((side) => (
              <span key={side} style={{ display: "inline-flex", alignItems: "center", justifyContent: "flex-end", gap: 4 }}>
                <input type="number" min="1" max="100" step="1" style={{ ...num, marginTop: 0, width: 64 }} aria-label={`${view.transition_labels[k]} ${side}`}
                  value={pct(v[side])} onChange={setBar(["transitions", k], side)} />
                <span style={meta}>%</span>
              </span>
            ))}
          </div>
        ))}
        {bars.map(([k, lbl, unit]) => (
          <div key={k} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 100px 100px", gap: 12, alignItems: "center",
            padding: "6px 12px", borderTop: `1px solid ${T.line}` }}>
            <span style={{ fontFamily: FB, fontSize: 13, color: T.ink }}>{lbl}</span>
            {["floor", "target"].map((side) => (
              <span key={side} style={{ display: "inline-flex", alignItems: "center", justifyContent: "flex-end", gap: 4 }}>
                <input type="number" min="0" style={{ ...num, marginTop: 0, width: 64 }} aria-label={`${lbl} ${side}`}
                  value={d.benchmarks[k][side]} onChange={setBar([k], side)} />
                <span style={meta}>{unit}</span>
              </span>
            ))}
          </div>
        ))}
      </div>

      <div style={h3}>ClickUp</div>
      <div style={{ ...meta, marginTop: 4 }}>With a list set, every coaching note also becomes a task, after the email has gone. Leave the list empty to send email only.</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 200px), 1fr))", columnGap: 16 }}>
        <label style={label}>List id
          <input style={{ ...input, fontFamily: FM }} value={d.clickup_list_id} onChange={set("clickup_list_id")} placeholder="Email only" />
        </label>
        <label style={label}>Default assignee (user id)
          <input style={{ ...input, fontFamily: FM }} value={d.clickup_assignee} onChange={set("clickup_assignee")} placeholder="Unassigned" />
        </label>
        <label style={label}>API token
          <input type="password" autoComplete="off" style={{ ...input, fontFamily: FM }} value={d.clickup_token} onChange={set("clickup_token")}
            placeholder={view.clickup_token_set ? "Saved · type to replace" : "Not set"} />
        </label>
      </div>

      <div style={h3}>Writing</div>
      <label style={{ ...label, marginTop: 8 }}>Model
        <input style={{ ...input, fontFamily: FM, maxWidth: 320 }} value={d.model} onChange={set("model")} />
      </label>
      <div style={{ ...meta, marginTop: 6 }}>
        {view.model_key_set
          ? "May rewrite each note in plainer words. A rewrite that changes or adds a number is thrown away and the written template is sent."
          : "No Anthropic key is configured, so the email uses its written templates. The model applies once a key is set."}
      </div>

      {err && <div style={{ fontFamily: FB, fontSize: 13, color: T.poppyText, marginTop: 12 }}>{err}</div>}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 16, flexWrap: "wrap" }}>
        <SBtn kind="primary" disabled={Boolean(busy)} onClick={save}>{busy === "save" ? "Saving…" : "Save digest"}</SBtn>
        <SBtn disabled={Boolean(busy) || !view.recipients.length} onClick={() => test("test")}
          title={view.recipients.length ? "Send this week's digest now, marked as a test" : "Save a recipient first"}>
          {busy === "test" ? "Sending…" : "Send a test"}
        </SBtn>
        {view.clickup_list_id && <SBtn disabled={Boolean(busy)} onClick={() => test("clickup")}>{busy === "clickup" ? "Creating…" : "Test ClickUp task"}</SBtn>}
        {note && <span style={{ fontFamily: FB, fontSize: 12, color: T.meadowInk }}>{note}</span>}
      </div>

      <div style={{ ...h3, paddingTop: 16, borderTop: `1px solid ${T.line}` }}>Deliveries</div>
      {history.length === 0 ? (
        <div style={{ ...meta, marginTop: 6 }}>Nothing sent yet.</div>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse", marginTop: 8, fontFamily: FB, fontSize: 13 }}>
          <thead><tr>{["Sent", "Week ending", "Status", "To", "Agents named", ""].map((x, i) => (
            <th key={i} style={{ fontSize: 12, fontWeight: 600, color: T.muted, textAlign: "left", padding: "4px 12px 6px 0" }}>{x}</th>))}</tr></thead>
          <tbody>
            {history.map((r) => (
              <tr key={r.id} style={{ borderTop: `1px solid ${T.line}` }}>
                <td style={{ padding: "8px 12px 8px 0", color: T.slate, whiteSpace: "nowrap" }}>
                  {new Date(r.sent_at || r.created_at).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}
                  {r.is_test && <span style={{ ...meta, marginLeft: 6 }}>test</span>}
                </td>
                <td style={{ padding: "8px 12px 8px 0", fontFamily: FM, color: T.slate }}>{r.week_ending}</td>
                <td style={{ padding: "8px 12px 8px 0" }} title={r.last_error || undefined}><Pill tone={STATUS_TONE[r.status] || "muted"}>{r.status}{r.attempts > 1 ? ` · ${r.attempts} tries` : ""}</Pill></td>
                <td style={{ padding: "8px 12px 8px 0", color: T.slate }}>{r.recipients.length || "—"}</td>
                <td style={{ padding: "8px 12px 8px 0", color: T.slate }}>{r.agents.join(", ") || "—"}</td>
                <td style={{ padding: "8px 0", ...meta }}>{r.model_used === "template" ? "written template" : r.model_used || ""}{r.clickup_tasks.length ? ` · ${r.clickup_tasks.length} ClickUp` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
