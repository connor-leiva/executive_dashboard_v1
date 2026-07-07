import { useState } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { T } from "./theme.js";
import { login, hasToken } from "./api.js";
import CommandCenter from "./CommandCenter.jsx";
import Settings from "./Settings.jsx";
import { SpringSignature } from "./Brand.jsx";

const API_BASE = import.meta.env.VITE_API_BASE;

/* ── Login screen ──────────────────────────────────────────── */

export function Login({ onLogin }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      onLogin();
    } catch (err) {
      setError("That didn't work. Check your email and password.");
    } finally {
      setBusy(false);
    }
  }

  const field = {
    width: "100%", boxSizing: "border-box", fontFamily: "Inter,sans-serif", fontSize: 14,
    color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 9,
    padding: "11px 12px", marginTop: 6,
  };
  const label = { display: "block", fontFamily: "Inter,sans-serif", fontSize: 12, fontWeight: 600, color: T.slate };

  return (
    <div className="login-root" style={{
      minHeight: "100vh", position: "relative", overflow: "hidden", display: "flex", alignItems: "center",
      padding: "24px 7vw", backgroundImage: "url(/brand/photos/gradient_1.jpg)", backgroundSize: "cover", backgroundPosition: "center",
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&family=Inter:wght@400;500;600&display=swap');
        .login-photo { position:absolute; top:0; right:0; bottom:0; width:48%;
          background:url(/brand/photos/spring_pic_10.jpg) center 22%/cover no-repeat;
          -webkit-mask-image:linear-gradient(90deg, transparent 0%, #000 30%); mask-image:linear-gradient(90deg, transparent 0%, #000 30%); }
        @media (max-width:900px){ .login-photo{ display:none; } }
        .login-input:focus-visible, .login-btn:focus-visible { outline:2px solid ${T.teal}; outline-offset:2px; }
        .login-btn:hover:not(:disabled){ background:${T.poppyActive}; }
      `}</style>
      <div className="login-photo" aria-hidden />
      <div style={{ position: "relative", zIndex: 1, width: "100%", maxWidth: 400 }}>
        <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 16, padding: "30px 28px", boxShadow: "0 24px 70px rgba(0,46,44,.16)" }}>
          <SpringSignature tone="dark" height={44} />
          <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 10, fontWeight: 700, letterSpacing: "0.18em", color: T.slate, marginTop: 12, textTransform: "uppercase" }}>Command Center</div>
          <form onSubmit={submit} style={{ marginTop: 24 }}>
            <label style={label}>Email
              <input className="login-input" style={field} type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </label>
            <div style={{ height: 14 }} />
            <label style={label}>Password
              <input className="login-input" style={field} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </label>
            {error && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.poppyText, marginTop: 14 }}>{error}</div>}
            <button className="login-btn" type="submit" disabled={busy} style={{
              width: "100%", marginTop: 20, fontFamily: "Poppins,sans-serif", fontSize: 14, fontWeight: 600,
              color: T.white, background: T.poppy, border: "none", borderRadius: 9, padding: "12px",
              cursor: busy ? "default" : "pointer", opacity: busy ? 0.7 : 1, transition: "background .15s ease",
            }}>
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>
        </div>
        <div style={{ textAlign: "center", marginTop: 16, fontFamily: "Inter,sans-serif", fontSize: 11.5 }}>
          <a href="/privacy.html" style={{ color: T.muted, textDecoration: "none" }}>Privacy Policy</a>
          <span style={{ color: T.muted, margin: "0 8px" }}>·</span>
          <a href="/eula.html" style={{ color: T.muted, textDecoration: "none" }}>Terms</a>
        </div>
      </div>
    </div>
  );
}

/* ── App wrapper ───────────────────────────────────────────── */
/* Dev runs without a backend, so the dashboard handles its own sample
   fallback. Only gate behind Login when an API base is configured and
   there's no stored token; otherwise go straight to the dashboard. */

export function App() {
  const [authed, setAuthed] = useState(hasToken());
  const needsLogin = Boolean(API_BASE) && !authed;

  if (needsLogin) {
    return <Login onLogin={() => setAuthed(true)} />;
  }
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<CommandCenter />} />
        <Route path="/settings/*" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
