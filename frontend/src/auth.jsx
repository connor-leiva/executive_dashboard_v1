import { useState } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { T } from "./theme.js";
import { login, hasToken } from "./api.js";
import CommandCenter from "./CommandCenter.jsx";
import Settings from "./Settings.jsx";

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
    color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8,
    padding: "11px 12px", marginTop: 6,
  };
  const label = {
    fontFamily: "Inter,sans-serif", fontSize: 12, fontWeight: 600, color: T.onDarkMute,
  };

  return (
    <div style={{
      minHeight: "100vh", background: T.evergreen, display: "flex", alignItems: "center", justifyContent: "center",
      padding: 24,
      backgroundImage: `repeating-linear-gradient(90deg, rgba(255,255,255,0.045) 0px, rgba(255,255,255,0.045) 1.5px, rgba(255,255,255,0) 1.5px, rgba(255,255,255,0) 13px), radial-gradient(135% 130% at 50% -15%, rgba(97,131,94,0.50) 0%, rgba(0,46,44,0) 55%)`,
    }}>
      <style>{`@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&family=Inter:wght@400;500;600&family=Sacramento&display=swap');`}</style>
      <div style={{ width: "100%", maxWidth: 360 }}>
        <div style={{ textAlign: "center", marginBottom: 26 }}>
          <div style={{ fontFamily: "Sacramento,cursive", fontSize: 52, color: T.onDark, lineHeight: 1 }}>Spring</div>
          <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 10, fontWeight: 700, letterSpacing: "0.18em", color: T.sprout, marginTop: 8, textTransform: "uppercase" }}>Command Center</div>
        </div>
        <form onSubmit={submit} style={{ background: "rgba(248,245,242,0.04)", border: "1px solid rgba(248,245,242,0.10)", borderRadius: 14, padding: 22 }}>
          <label style={label}>
            Email
            <input style={field} type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </label>
          <div style={{ height: 14 }} />
          <label style={label}>
            Password
            <input style={field} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </label>
          {error && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.petal, marginTop: 14 }}>{error}</div>}
          <button type="submit" disabled={busy} style={{
            width: "100%", marginTop: 18, fontFamily: "Poppins,sans-serif", fontSize: 14, fontWeight: 600,
            color: T.evergreen, background: T.sprout, border: "none", borderRadius: 8, padding: "11px 12px",
            cursor: busy ? "default" : "pointer", opacity: busy ? 0.7 : 1,
          }}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
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
