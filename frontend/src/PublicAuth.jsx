import { useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { T } from "./theme.js";
import { postPublic, setToken } from "./api.js";
import { SpringSignature } from "./Brand.jsx";

/* Public onboarding pages — set-your-password (invite) and reset. Login-card
   layout; on success we store the JWT and drop straight into the app. */

const field = {
  width: "100%", boxSizing: "border-box", fontFamily: "Inter,sans-serif", fontSize: 14,
  color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 9,
  padding: "11px 12px", marginTop: 6,
};
const label = { display: "block", fontFamily: "Inter,sans-serif", fontSize: 12, fontWeight: 600, color: T.slate, marginTop: 14 };

function Shell({ title, sub, children }) {
  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
      background: `linear-gradient(135deg, ${T.parchment}, #FBEDE6)`, padding: 24, fontFamily: "Inter,sans-serif" }}>
      <div style={{ width: "100%", maxWidth: 400, background: T.white, border: `1px solid ${T.line}`,
        borderRadius: 16, padding: "30px 28px", boxShadow: "0 24px 70px rgba(0,46,44,.16)" }}>
        <SpringSignature tone="dark" height={30} />
        <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 10, fontWeight: 700, letterSpacing: "0.18em", color: T.muted, marginTop: 6, textTransform: "uppercase" }}>Command Center</div>
        <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 19, fontWeight: 600, color: T.ink, marginTop: 20 }}>{title}</div>
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.muted, marginTop: 4, lineHeight: 1.5 }}>{sub}</div>
        {children}
      </div>
    </div>
  );
}

function btn(busy) {
  return {
    width: "100%", marginTop: 20, color: T.white, background: busy ? T.poppyActive : T.poppy,
    border: "none", borderRadius: 9, padding: "12px", cursor: busy ? "default" : "pointer",
    fontFamily: "Poppins,sans-serif", fontSize: 14, fontWeight: 600, opacity: busy ? 0.7 : 1,
  };
}

function Err({ children }) {
  return <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.poppyText, marginTop: 14, lineHeight: 1.5 }}>{children}</div>;
}

export function AcceptInvite({ onDone }) {
  const [sp] = useSearchParams();
  const token = sp.get("token");
  const [name, setName] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const nav = useNavigate();

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      const { token: jwt } = await postPublic("/auth/accept-invite", { token, name, password: pw });
      setToken(jwt);
      onDone && onDone();
      nav("/", { replace: true });
    } catch (x) {
      setErr(x.message || "Couldn't accept the invite. Ask your admin to resend it.");
    } finally { setBusy(false); }
  }

  if (!token) return <Shell title="Invalid invite link" sub="This link is missing its token. Ask your admin to resend the invite." />;
  return (
    <Shell title="Set up your account" sub="Choose a name and password to accept your invite.">
      <form onSubmit={submit}>
        <label style={label}>Your name<input style={field} value={name} onChange={(e) => setName(e.target.value)} required /></label>
        <label style={label}>Password <span style={{ fontWeight: 400, color: T.muted }}>· at least 10 characters</span>
          <input style={field} type="password" value={pw} onChange={(e) => setPw(e.target.value)} minLength={10} required /></label>
        {err && <Err>{err}</Err>}
        <button type="submit" disabled={busy} style={btn(busy)}>{busy ? "Setting up…" : "Accept invite"}</button>
      </form>
    </Shell>
  );
}

export function ResetPassword({ onDone }) {
  const [sp] = useSearchParams();
  const token = sp.get("token");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const nav = useNavigate();

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      const { token: jwt } = await postPublic("/auth/reset-password", { token, new_password: pw });
      setToken(jwt);
      onDone && onDone();
      nav("/", { replace: true });
    } catch (x) {
      setErr(x.message || "Couldn't reset your password. Ask your admin for a new link.");
    } finally { setBusy(false); }
  }

  if (!token) return <Shell title="Invalid reset link" sub="This link is missing its token. Ask your admin for a new one." />;
  return (
    <Shell title="Choose a new password" sub="Set a new password to get back into your account.">
      <form onSubmit={submit}>
        <label style={label}>New password <span style={{ fontWeight: 400, color: T.muted }}>· at least 10 characters</span>
          <input style={field} type="password" value={pw} onChange={(e) => setPw(e.target.value)} minLength={10} required /></label>
        {err && <Err>{err}</Err>}
        <button type="submit" disabled={busy} style={btn(busy)}>{busy ? "Saving…" : "Reset password"}</button>
      </form>
    </Shell>
  );
}
