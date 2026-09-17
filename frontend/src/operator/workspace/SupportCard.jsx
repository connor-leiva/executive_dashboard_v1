import React, { useState } from "react";
import { api } from "../api.js";
import { ago } from "../format.js";
import { Btn, Card, Chip, Eyebrow, Field, inputStyle, Loading, LoadError, Mono, Notice, useAction, useApi } from "../primitives.jsx";
import { A, TYPE } from "../tokens.js";

const PHRASE = { "support.access_opened": "Opened", "support.access_ended": "Ended early", "support.access_expired": "Expired" };

/* The only door from this console into a workspace's dashboard, and a narrow one. It signs the
   operator in as a real, named, read-only account in the workspace that ends on its own; the owners
   are emailed with the reason. The session URL is opened, never shown. */
export default function SupportCard({ w, reload }) {
  const data = useApi(() => api.supportSessions(w.slug), [w.slug]);
  const [reason, setReason] = useState("");
  const [minutes, setMinutes] = useState("30");
  const action = useAction();
  if (data.loading && !data.data) return <Card title="Support access"><Loading label="Reading support sessions" /></Card>;
  if (data.error) return <LoadError error={data.error} onRetry={data.reload} />;

  const mine = data.data.open.find((o) => o.mine);
  const suspended = w.status === "suspended";

  async function open() {
    /* Opened before the request so the browser treats it as the click's own window rather than a
       popup, then pointed at the session once the server answers. */
    const tab = window.open("about:blank", "_blank");
    const out = await action.run("open", () => api.openSupport(w.slug, { reason: reason.trim(), minutes: Number(minutes) }),
      (r) => `Open until ${new Date(r.expires_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}. ${r.owners_emailed.length ? `Emailed ${r.owners_emailed.join(", ")}.` : "The workspace has no active owner to email."}`);
    if (out && tab) {
      tab.opener = null;
      tab.location.href = out.url;
    } else if (tab) {
      tab.close();
    }
    if (out) { setReason(""); data.reload(); reload(); }
  }

  return (
    <Card title="Support access" style={{ marginBottom: 16 }}
      sub="The only door from this console into a workspace's dashboard. Time-boxed, read-only, needs a reason, and the owners are emailed when it opens.">
      {mine ? (
        <div style={{ display: "flex", gap: 10, alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", marginBottom: 14 }}>
          <div style={{ fontFamily: TYPE.text, fontSize: 12.5, color: A.ink }}>
            <Chip state="trial">Open</Chip> as <Mono c={A.ink}>{mine.account}</Mono> until {new Date(mine.expires_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
          </div>
          <Btn small kind="danger" busy={action.busy === "end"}
            onClick={async () => { if (await action.run("end", () => api.endSupport(w.slug), "Ended. The support account is disabled and its session no longer works.")) { data.reload(); reload(); } }}>
            End now
          </Btn>
        </div>
      ) : (
        <div style={{ display: "grid", gap: 12, maxWidth: 560 }}>
          <Field label="Why you need in" htmlFor="sa-reason"
            note="Written to both audit trails and included in the email the owners receive. Nothing opens without one.">
            <input id="sa-reason" value={reason} onChange={(e) => setReason(e.target.value)} style={inputStyle}
              placeholder="e.g. The owner reports the Forum tab is blank after reconnecting QuickBooks" />
          </Field>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <select aria-label="Session length" value={minutes} onChange={(e) => setMinutes(e.target.value)} style={{ ...inputStyle, width: "auto", cursor: "pointer" }}>
              <option value="15">15 minutes</option><option value="30">30 minutes</option><option value="60">1 hour</option>
            </select>
            <span style={{ fontFamily: TYPE.text, fontSize: 11.5, color: A.mute, flex: "1 1 220px" }}>
              Read-only: the server refuses every change from a support session. It opens in a new tab and replaces any session
              this browser already holds on that workspace.
            </span>
            <Btn kind="primary" small disabled={!reason.trim() || suspended} busy={action.busy === "open"} onClick={open}
              title={suspended ? "Nobody can sign in to a suspended workspace" : undefined}>
              Open session
            </Btn>
          </div>
        </div>
      )}
      {action.result ? <div style={{ marginTop: 10 }}><Notice tone={action.result.tone}>{action.result.text}</Notice></div> : null}
      <div style={{ marginTop: 16, paddingTop: 14, borderTop: `1px solid ${A.lineSoft}` }}>
        <Eyebrow>Past sessions</Eyebrow>
        {data.data.history.length === 0 ? (
          <div style={{ fontFamily: TYPE.text, fontSize: 12, color: A.mute, marginTop: 8 }}>Nobody from Acumyn has opened support access here.</div>
        ) : data.data.history.map((h, i) => (
          <div key={`${h.at}-${i}`} style={{ display: "flex", justifyContent: "space-between", gap: 12, marginTop: 8, fontFamily: TYPE.text, fontSize: 12, color: A.body, flexWrap: "wrap" }}>
            <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>
              {PHRASE[h.action] || h.action}{h.who ? ` by ${h.who}` : ""}{h.minutes ? ` · ${h.minutes} min` : ""}{h.reason ? ` · "${h.reason}"` : ""}
            </span>
            <Mono size={10.5} c={A.mute}>{ago(h.at)}</Mono>
          </div>
        ))}
      </div>
    </Card>
  );
}
