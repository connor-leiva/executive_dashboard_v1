/* Step-up gate — a section behind a second factor.

   Tab access says who MAY see a section; this says they proved it again, recently. The
   server is the real gate (it answers 428 without a live grant); this component just makes
   that legible: prompt for a code, store the short-lived grant in sessionStorage, render the
   section. Closing the tab re-locks it. */
import { useEffect, useState } from "react";
import { T, alpha } from "./theme.js";
import { getJSON, postJSON, setStepUp, getStepUp, clearStepUp } from "./api.js";

export default function StepUpGate({ scope, title, blurb, usingSample, children }) {
  // Sample mode has no backend to verify against — never lock a demo screen.
  const [unlocked, setUnlocked] = useState(() => usingSample || !!getStepUp(scope));
  const [enrolled, setEnrolled] = useState(null);      // null = unknown yet
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (unlocked || usingSample) return;
    let alive = true;
    getJSON("/me/totp")
      .then((d) => alive && setEnrolled(!!d.enabled))
      .catch(() => alive && setEnrolled(false));
    return () => { alive = false; };
  }, [unlocked, usingSample]);

  // A grant can expire while the section is open; the child's 428 bubbles up here.
  useEffect(() => {
    const onLocked = (e) => {
      if (e.detail?.scope === scope) { clearStepUp(scope); setUnlocked(false); }
    };
    window.addEventListener("cc:step-up-required", onLocked);
    return () => window.removeEventListener("cc:step-up-required", onLocked);
  }, [scope]);

  if (unlocked) return children;

  const submit = async (e) => {
    e?.preventDefault?.();
    if (!code.trim() || busy) return;
    setBusy(true); setErr(null);
    try {
      const r = await postJSON(`/step-up/${scope}`, { code: code.trim() });
      setStepUp(scope, r.token);
      setCode("");
      setUnlocked(true);
    } catch (e2) {
      setErr(e2.detail || e2.message || "That didn't work");
    } finally {
      setBusy(false);
    }
  };

  const input = {
    width: "100%", fontFamily: "Inter,sans-serif", fontSize: 16, letterSpacing: ".18em",
    textAlign: "center", padding: "11px 12px", border: `1px solid ${T.line}`,
    borderRadius: 9, color: T.ink, background: T.white,
  };

  return (
    <div style={{ display: "flex", justifyContent: "center", padding: "56px 20px" }}>
      <div style={{
        width: "100%", maxWidth: 400, background: T.white, border: `1px solid ${T.line}`,
        borderRadius: 16, padding: "26px 24px",
        boxShadow: `0 18px 44px ${alpha(T.evergreen, 0.09)}`,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 4 }}>
          <span style={{ width: 4, height: 20, borderRadius: 2, background: T.petal }} />
          <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 17, fontWeight: 600, letterSpacing: "-.01em", color: T.ink }}>
            {title}
          </span>
        </div>
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.muted, lineHeight: 1.55, margin: "6px 0 18px" }}>
          {blurb}
        </div>

        {enrolled === false ? (
          <div style={{
            fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.ink, lineHeight: 1.6,
            background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 10, padding: "12px 14px",
          }}>
            You haven't set up an authenticator app yet. Open{" "}
            <a href="/settings/security" style={{ color: T.teal, fontWeight: 600 }}>Settings → Security</a>{" "}
            to turn on two-factor, then come back here.
          </div>
        ) : (
          <form onSubmit={submit}>
            <label style={{ display: "block", fontFamily: "Inter,sans-serif", fontSize: 11, fontWeight: 600, color: T.tertiary, marginBottom: 6 }}>
              6-digit code from your authenticator app
            </label>
            <input autoFocus inputMode="numeric" autoComplete="one-time-code" placeholder="000000"
              value={code} onChange={(e) => setCode(e.target.value)} style={input} />
            {err && (
              <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.amber, marginTop: 9 }}>{err}</div>
            )}
            <button type="submit" disabled={busy || !code.trim()} style={{
              width: "100%", marginTop: 14, fontFamily: "Inter,sans-serif", fontSize: 13, fontWeight: 700,
              color: T.onDark, background: busy || !code.trim() ? T.muted : T.evergreen,
              border: "none", borderRadius: 9, padding: "10px 0",
              cursor: busy || !code.trim() ? "default" : "pointer",
            }}>{busy ? "Checking…" : "Unlock"}</button>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted, marginTop: 11, lineHeight: 1.5 }}>
              Lost your phone? Enter one of your recovery codes instead — each works once.
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
