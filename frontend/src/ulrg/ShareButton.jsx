/* Owner/admin control to mint a read-only ClickUp share link (SPEC 5.3 / Step 8). POSTs
   /ulrg/share and reveals the embeddable URL with a copy button. Read-only link — anyone with it
   sees the scorecard, so it's owner/admin only (also enforced server-side). */
import { useState } from "react";
import { postJSON } from "../api.js";
import { C, FB, FM } from "./scorecardMath.js";

export default function ShareButton() {
  const [url, setUrl] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(false);
  const [copied, setCopied] = useState(false);

  async function make() {
    setBusy(true); setErr(false);
    try {
      const r = await postJSON("/ulrg/share", { scope: "ulrg_scorecard" });
      setUrl(r.url);
    } catch (e) { setErr(true); }
    setBusy(false);
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true); setTimeout(() => setCopied(false), 1600);
    } catch (e) { /* clipboard blocked — the field is selectable as a fallback */ }
  }

  const btn = {
    fontFamily: FM, fontSize: 11.5, color: C.slate, background: "none",
    border: `1px solid ${C.hair}`, borderRadius: 8, padding: "5px 11px", cursor: "pointer",
  };

  if (!url) {
    return (
      <button onClick={make} disabled={busy} style={{ ...btn, opacity: busy ? 0.6 : 1 }}
              title="Create a read-only link to embed in ClickUp">
        {busy ? "Creating…" : err ? "Try again" : "Share to ClickUp"}
      </button>
    );
  }
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", maxWidth: 460 }}>
      <input readOnly value={url} onFocus={(e) => e.target.select()}
             style={{ fontFamily: FB, fontSize: 11.5, color: C.body, background: C.parchment,
                      border: `1px solid ${C.hair}`, borderRadius: 8, padding: "5px 9px", flex: "1 1 240px", minWidth: 180 }} />
      <button onClick={copy} style={{ ...btn, color: copied ? C.meadowInk : C.slate, borderColor: copied ? C.meadow : C.hair }}>
        {copied ? "Copied ✓" : "Copy"}
      </button>
    </div>
  );
}
