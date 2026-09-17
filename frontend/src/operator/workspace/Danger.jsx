import React, { useState } from "react";
import { api } from "../api.js";
import { Btn, Card, inputStyle, Notice } from "../primitives.jsx";
import { A, TYPE } from "../tokens.js";

/* One disruptive action: what it does to the workspace, then (when it needs one) the reason, then
   the button. The words come before the button on purpose. */
function DangerRow({ title, children, reasonLabel, reasonId, reason, setReason, placeholder, button, first }) {
  return (
    <div style={{
      display: "flex", gap: 16, alignItems: "flex-start", justifyContent: "space-between", flexWrap: "wrap",
      padding: first ? "2px 0 13px" : "13px 0", borderTop: first ? "none" : `1px solid ${A.lineSoft}`,
    }}>
      <div style={{ minWidth: 0, flex: "1 1 340px" }}>
        <div style={{ fontFamily: TYPE.text, fontSize: 13, fontWeight: 500, color: A.ink }}>{title}</div>
        <div style={{ fontFamily: TYPE.text, fontSize: 11.5, color: A.mute, marginTop: 3, lineHeight: 1.6, textWrap: "pretty" }}>{children}</div>
        {setReason ? (
          <div style={{ marginTop: 10, maxWidth: 440 }}>
            <label htmlFor={reasonId} style={{ display: "block", fontFamily: TYPE.text, fontSize: 11, fontWeight: 600, color: A.body, marginBottom: 6 }}>
              {reasonLabel}
            </label>
            <input id={reasonId} value={reason} onChange={(e) => setReason(e.target.value)} placeholder={placeholder} style={inputStyle} />
          </div>
        ) : null}
      </div>
      {button}
    </div>
  );
}

export default function DangerPane({ w, reload }) {
  const [suspendReason, setSuspendReason] = useState("");
  const [freezeReason, setFreezeReason] = useState("");
  const [busy, setBusy] = useState(null);
  const [note, setNote] = useState(null);
  const suspended = w.status === "suspended";
  const frozen = Boolean(w.syncs_frozen);

  async function act(key, fn, success) {
    setBusy(key);
    setNote(null);
    try {
      await fn();
      setNote({ tone: "info", text: success });
      setSuspendReason("");
      setFreezeReason("");
      reload();
    } catch (e) {
      setNote({ tone: "error", text: e.message });
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card title="Disruptive actions" sub="Each one names what it does to the workspace before it names its button.">
      {note ? <div style={{ marginBottom: 12 }}><Notice tone={note.tone}>{note.text}</Notice></div> : null}

      <DangerRow first title={suspended ? "Resume" : "Suspend"}
        reasonLabel="Reason, recorded and shown on the fleet list" reasonId="ac-suspend-reason"
        reason={suspendReason} setReason={suspended ? null : setSuspendReason}
        placeholder="e.g. Billing lapsed after four failed charges"
        button={suspended ? (
          <Btn small kind="solid" busy={busy === "resume"}
            onClick={() => act("resume", () => api.resume(w.slug), "Resumed. People can sign in again.")}>Resume workspace</Btn>
        ) : (
          <Btn small kind="danger" disabled={!suspendReason.trim()} busy={busy === "suspend"}
            onClick={() => act("suspend", () => api.suspend(w.slug, suspendReason.trim()), "Suspended. Sign-in is refused and live sessions have ended.")}>
            Suspend workspace
          </Btn>
        )}>
        {suspended
          ? "Lets people sign in again and puts the workspace back on the scheduled sync. Nothing was deleted while it was suspended."
          : `Blocks every sign-in immediately, ends every live session with a message saying the workspace is suspended, and stops every sync. Data is untouched. Public share links keep serving until they are revoked separately${w.share_links_live ? ` (${w.share_links_live} live now)` : ""}.`}
      </DangerRow>

      <DangerRow title={frozen ? "Unfreeze syncs" : "Freeze syncs"}
        reasonLabel="Reason, recorded and shown while syncs stay frozen" reasonId="ac-freeze-reason"
        reason={freezeReason} setReason={frozen ? null : setFreezeReason}
        placeholder="e.g. Their Follow Up Boss key was posted in a public channel"
        button={frozen ? (
          <Btn small kind="solid" busy={busy === "unfreeze"}
            onClick={() => act("unfreeze", () => api.unfreezeSyncs(w.slug), "Unfrozen. The next scheduled sync pulls from every source again.")}>
            Unfreeze syncs
          </Btn>
        ) : (
          <Btn small kind="danger" disabled={!freezeReason.trim()} busy={busy === "freeze"}
            onClick={() => act("freeze", () => api.freezeSyncs(w.slug, freezeReason.trim()), "Frozen. Nothing is pulled from any source until syncs are unfrozen.")}>
            Freeze syncs
          </Btn>
        )}>
        {frozen
          ? "Puts the workspace back on the scheduled sync and lets its own Sync buttons work again. Unfreeze once whatever prompted the freeze is fixed: usually a credential rotated and reconnected."
          : "Leaves people signed in but stops pulling from every source: the scheduled sync, the daily roster and ads jobs, and the Sync buttons inside the workspace and here. Use it when a credential may be compromised and the first job is to stop it being used."}
      </DangerRow>
    </Card>
  );
}
