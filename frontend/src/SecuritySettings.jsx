/* Settings → Security: enrol / remove the authenticator app that unlocks stepped-up
   sections (Binder). The secret is shown exactly once, as a QR plus a typed fallback; the
   recovery codes are shown exactly once too — losing both means an admin has to reset it. */
import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { T, alpha } from "./theme.js";
import { getJSON, postJSON, API_BASE } from "./api.js";

const box = {
  background: T.white, border: `1px solid ${T.line}`, borderRadius: 14, padding: 20, marginBottom: 16,
};
const label = { fontFamily: "Inter,sans-serif", fontSize: 11, fontWeight: 600, color: T.tertiary, marginBottom: 6, display: "block" };
const input = {
  // 16px: iOS zooms the whole page when a focused field is smaller, which then leaves the
  // layout scrolled sideways. maxWidth so it can't overflow a phone-width card.
  fontFamily: "Inter,sans-serif", fontSize: 16, padding: "9px 11px", boxSizing: "border-box",
  border: `1px solid ${T.line}`, borderRadius: 9, color: T.ink, background: T.white,
  width: 190, maxWidth: "100%",
};
const button = (tone = "primary", disabled = false) => ({
  fontFamily: "Inter,sans-serif", fontSize: 13, fontWeight: 700, border: "none", borderRadius: 9,
  padding: "9px 16px", cursor: disabled ? "default" : "pointer",
  color: tone === "danger" ? T.amber : T.onDark,
  background: disabled ? T.muted : tone === "danger" ? alpha(T.amber, 0.12) : T.evergreen,
});

export default function SecuritySettings() {
  const [status, setStatus] = useState(null);
  const [enroll, setEnroll] = useState(null);      // { secret, otpauth_uri, qr }
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState(null);        // recovery codes, shown once
  const [pw, setPw] = useState("");
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = () => {
    if (!API_BASE) { setStatus({ enabled: false, recovery_remaining: 0, sample: true }); return; }
    getJSON("/me/totp").then(setStatus).catch(() => setStatus({ enabled: false }));
  };
  useEffect(load, []);

  const start = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await postJSON("/me/totp/start");
      const qr = await QRCode.toString(r.otpauth_uri, { type: "svg", margin: 1, width: 190 });
      setEnroll({ ...r, qr });
    } catch (e) { setErr(e.detail || e.message); } finally { setBusy(false); }
  };

  const confirm = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await postJSON("/me/totp/confirm", { code: code.trim() });
      setCodes(r.recovery_codes);
      setEnroll(null); setCode("");
      load();
    } catch (e) { setErr(e.detail || e.message); } finally { setBusy(false); }
  };

  const disable = async () => {
    if (!window.confirm("Turn off two-factor? Binder will open with just your password again.")) return;
    setBusy(true); setErr(null);
    try {
      await postJSON("/me/totp/disable", { password: pw });
      setPw(""); load();
    } catch (e) { setErr(e.detail || e.message); } finally { setBusy(false); }
  };

  if (!status) return <div style={{ color: T.muted, fontSize: 13 }}>Loading…</div>;

  return (
    <div>
      <div style={box}>
        <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 14, fontWeight: 700, color: T.ink, marginBottom: 4 }}>
          Two-factor authentication
        </div>
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.muted, lineHeight: 1.6, marginBottom: 14 }}>
          An authenticator app (1Password, Google Authenticator, Authy) protects the sections that
          hold sensitive records. Today that's <b style={{ color: T.ink }}>Binder</b> — your legal-entity
          and compliance files. You'll be asked for a code when you open it; the unlock lasts 20 minutes.
        </div>

        {status.sample ? (
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.muted }}>
            Connect the backend to manage two-factor.
          </div>
        ) : status.enabled ? (
          <>
            <div style={{
              display: "inline-flex", alignItems: "center", gap: 7, fontFamily: "Inter,sans-serif",
              fontSize: 12, fontWeight: 700, color: T.meadow, background: T.meadowBg,
              borderRadius: 7, padding: "5px 11px", marginBottom: 12,
            }}>
              <span style={{ width: 7, height: 7, borderRadius: 99, background: T.meadow }} /> On
            </div>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.slate, marginBottom: 14 }}>
              {status.recovery_remaining} recovery code{status.recovery_remaining === 1 ? "" : "s"} left.
              {status.recovery_remaining === 0 && " Turn it off and set it up again to get a new set."}
            </div>
            <label style={label}>Password (to turn it off)</label>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} style={input} />
              <button onClick={disable} disabled={busy || !pw} style={button("danger", busy || !pw)}>
                Turn off
              </button>
            </div>
          </>
        ) : enroll ? (
          <>
            <div style={{ display: "flex", gap: 20, flexWrap: "wrap", alignItems: "flex-start" }}>
              <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 12, padding: 10 }}
                   dangerouslySetInnerHTML={{ __html: enroll.qr }} />
              <div style={{ flex: 1, minWidth: 220 }}>
                <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.slate, lineHeight: 1.6, marginBottom: 10 }}>
                  Scan this with your authenticator app, then enter the 6-digit code it shows.
                </div>
                <label style={label}>Can't scan? Enter this key by hand</label>
                <code style={{
                  display: "block", fontFamily: "ui-monospace,Menlo,monospace", fontSize: 12,
                  background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 8,
                  padding: "8px 10px", wordBreak: "break-all", color: T.ink, marginBottom: 12,
                }}>{enroll.secret}</code>
                <label style={label}>6-digit code</label>
                <div style={{ display: "flex", gap: 8 }}>
                  <input inputMode="numeric" placeholder="000000" value={code}
                         onChange={(e) => setCode(e.target.value)} style={{ ...input, width: 120, letterSpacing: ".16em" }} />
                  <button onClick={confirm} disabled={busy || !code.trim()} style={button("primary", busy || !code.trim())}>
                    {busy ? "Checking…" : "Turn on"}
                  </button>
                </div>
              </div>
            </div>
          </>
        ) : (
          <button onClick={start} disabled={busy} style={button("primary", busy)}>
            {busy ? "Preparing…" : "Set up two-factor"}
          </button>
        )}

        {err && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.amber, marginTop: 12 }}>{err}</div>}
      </div>

      {codes && (
        <div style={{ ...box, borderColor: alpha(T.amber, 0.4) }}>
          <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 14, fontWeight: 700, color: T.ink, marginBottom: 4 }}>
            Save your recovery codes
          </div>
          <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.muted, lineHeight: 1.6, marginBottom: 12 }}>
            This is the only time these are shown. Each opens Binder once if you lose your phone —
            store them somewhere safe and offline.
          </div>
          <div style={{
            display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))", gap: 8, marginBottom: 12,
          }}>
            {codes.map((c) => (
              <code key={c} style={{
                fontFamily: "ui-monospace,Menlo,monospace", fontSize: 12.5, color: T.ink,
                background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 7,
                padding: "7px 9px", textAlign: "center",
              }}>{c}</code>
            ))}
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={() => navigator.clipboard?.writeText(codes.join("\n"))} style={button()}>
              Copy all
            </button>
            <button onClick={() => setCodes(null)} style={{ ...button(), background: "transparent", color: T.slate, border: `1px solid ${T.line}` }}>
              I've saved them
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
