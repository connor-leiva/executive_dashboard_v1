import React, { useState } from "react";
import { api } from "../api.js";
import { Btn, Card, inputStyle, Notice } from "../primitives.jsx";
import { A, TYPE } from "../tokens.js";

/* Irreversible and disruptive actions. Each names what it does to the workspace before the button
   that does it. */
export default function DangerPane({ w, reload }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(null);
  const [note, setNote] = useState(null);
  const suspended = w.status === "suspended";

  async function act(key, fn, success) {
    setBusy(key);
    setNote(null);
    try {
      await fn();
      setNote({ tone: "info", text: success });
      setReason("");
      reload();
    } catch (e) {
      setNote({ tone: "error", text: e.message });
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card title="Disruptive actions" sub="Each one names what it does to the workspace before it names its button.">
      <div style={{ display: "flex", gap: 16, alignItems: "flex-start", justifyContent: "space-between", padding: "4px 0 13px", flexWrap: "wrap" }}>
        <div style={{ minWidth: 0, flex: "1 1 340px" }}>
          <div style={{ fontFamily: TYPE.text, fontSize: 13, fontWeight: 500, color: A.ink }}>{suspended ? "Resume" : "Suspend"}</div>
          <div style={{ fontFamily: TYPE.text, fontSize: 11.5, color: A.mute, marginTop: 3, lineHeight: 1.6, textWrap: "pretty" }}>
            {suspended
              ? "Lets people sign in again and puts the workspace back on the scheduled sync. Nothing was deleted while it was suspended."
              : `Blocks every sign-in immediately, ends every live session with a message saying the workspace is suspended, and takes it off the scheduled sync. Data is untouched. Public share links keep serving until they are revoked separately${w.share_links_live ? ` (${w.share_links_live} live now)` : ""}.`}
          </div>
          {!suspended ? (
            <div style={{ marginTop: 10, maxWidth: 440 }}>
              <label htmlFor="ac-suspend-reason" style={{ display: "block", fontFamily: TYPE.text, fontSize: 11, fontWeight: 600, color: A.body, marginBottom: 6 }}>
                Reason, recorded and shown on the fleet list
              </label>
              <input id="ac-suspend-reason" value={reason} onChange={(e) => setReason(e.target.value)}
                placeholder="e.g. Billing lapsed after four failed charges" style={inputStyle} />
            </div>
          ) : null}
        </div>
        {suspended ? (
          <Btn small kind="solid" busy={busy === "resume"}
            onClick={() => act("resume", () => api.resume(w.slug), "Resumed. People can sign in again.")}>Resume workspace</Btn>
        ) : (
          <Btn small kind="danger" disabled={!reason.trim()} busy={busy === "suspend"}
            onClick={() => act("suspend", () => api.suspend(w.slug, reason.trim()), "Suspended. Sign-in is refused and live sessions have ended.")}>
            Suspend workspace
          </Btn>
        )}
      </div>
      {note ? <Notice tone={note.tone}>{note.text}</Notice> : null}
    </Card>
  );
}
