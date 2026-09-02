import { Suspense, lazy, useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";

// Lazy so the operator console is its own chunk. A customer's browser has no reason to download
// the screen that suspends customers, and a static import puts it in everybody's bundle.
const PlatformConsole = lazy(() => import("./platform/PlatformConsole.jsx"));
import { T } from "./theme.js";
import { login, hasToken, getJSON } from "./api.js";
import CommandCenter from "./CommandCenter.jsx";
import Settings from "./Settings.jsx";
import { AcceptInvite, ResetPassword } from "./PublicAuth.jsx";
import ShareScorecard from "./ulrg/ShareScorecard.jsx";
import ShareDesk from "./ShareDesk.jsx";
import { SpringSignature, setBrand, ribbedHero } from "./Brand.jsx";
import { applyBrand, applyType } from "./palette.js";
import { PoweredByAcumyn } from "./brand/PoweredBy.jsx";

const API_BASE = import.meta.env.VITE_API_BASE;

/* The operator console lives at admin.<whatever this deployment is served from>, and nowhere
   else. Keyed on the first label rather than the full host so it needs no knowledge of the
   platform domain, which differs across local, staging and production — and so `admin.localhost`
   works in development with no special case. `admin` is reserved in backend tenancy.PLATFORM_HOSTS
   and can never resolve to a customer, so this host cannot collide with one. */
const IS_OPERATOR_HOST = window.location.hostname.split(".")[0] === "admin";

/* ── Login screen ──────────────────────────────────────────── */

export function Login({ onLogin }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // The sign-in screen has no session, so it asks who this host belongs to. Until that answers
  // it renders Acumyn's own identity, which is the truthful state rather than a placeholder:
  // before you sign in you are at the platform, not inside a workspace.
  //
  // WHY THE CACHE. /public/brand is a network round-trip, so the honest first paint is Acumyn's
  // identity and the workspace's arrives a few hundred milliseconds later — which reads as the
  // page changing its mind in front of you. The last answer for THIS host is kept and applied
  // synchronously, so a returning visitor never sees the swap; only a genuinely first visit does.
  //
  // Safe to cache per host because every workspace is its own subdomain and therefore its own
  // origin: one workspace's storage is not readable from another's, and nothing here is private
  // anyway — it is the branding painted on the page a moment later.
  const CACHE_KEY = `acu:brand:${window.location.hostname}`;
  const [chrome, setChrome] = useState(() => {
    try {
      const raw = window.localStorage.getItem(CACHE_KEY);
      if (!raw) return null;
      const b = JSON.parse(raw);
      applyBrand(b);
      applyType(b.type || {});
      setBrand(b);
      return b;
    } catch { return null; }          // private window, cleared storage, corrupt value
  });
  useEffect(() => {
    let live = true;
    if (!API_BASE) return undefined;
    getJSON("/public/brand")
      .then((b) => {
        if (!live || !b) return;
        applyBrand(b);
        applyType(b.type || {});
        setBrand(b);
        setChrome(b);
        try { window.localStorage.setItem(CACHE_KEY, JSON.stringify(b)); } catch { /* fine */ }
      })
      .catch(() => { /* unreachable API: keep whatever is already painted */ });
    return () => { live = false; };
  }, []);
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
    width: "100%", boxSizing: "border-box", fontFamily: "var(--font-text)", fontSize: 14,
    color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 9,
    padding: "11px 12px", marginTop: 6,
  };
  const label = { display: "block", fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate };

  return (
    <div className="login-root" style={{
      minHeight: "100vh", position: "relative", overflow: "hidden", display: "flex", alignItems: "center",
      padding: "24px 7vw", backgroundSize: "cover", backgroundPosition: "center",
      // A configured image, else the neutral ribbed hero over this workspace's own colour —
      // which is a real answer for any brand, not a stand-in for a missing file.
      ...(chrome && chrome.hero_image
        ? { backgroundImage: `url(${chrome.hero_image})` }
        : ribbedHero("evergreen")),
    }}>
      <style>{`
        .login-photo { position:absolute; top:0; right:0; bottom:0; width:48%;
          background:var(--login-photo) center 22%/cover no-repeat;
          -webkit-mask-image:linear-gradient(90deg, transparent 0%, #000 30%); mask-image:linear-gradient(90deg, transparent 0%, #000 30%); }
        @media (max-width:900px){ .login-photo{ display:none; } }
        .login-input:focus-visible, .login-btn:focus-visible { outline:2px solid ${T.teal}; outline-offset:2px; }
        .login-btn:hover:not(:disabled){ background:${T.poppyActive}; }
      `}</style>
      {chrome && chrome.photo
        ? <div className="login-photo" aria-hidden
               style={{ "--login-photo": `url(${chrome.photo})` }} />
        : null}
      <div style={{ position: "relative", zIndex: 1, width: "100%", maxWidth: 400 }}>
        <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 16, padding: "30px 28px", boxShadow: "0 24px 70px rgba(0,46,44,.16)" }}>
          <SpringSignature tone="dark" height={44} />
          <div style={{ fontFamily: "var(--font-display)", fontSize: 10, fontWeight: 700, letterSpacing: "0.18em", color: T.slate, marginTop: 12, textTransform: "uppercase" }}>Command Center</div>
          <form onSubmit={submit} style={{ marginTop: 24 }}>
            <label style={label}>Email
              <input className="login-input" style={field} type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </label>
            <div style={{ height: 14 }} />
            <label style={label}>Password
              <input className="login-input" style={field} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </label>
            {error && <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.poppyText, marginTop: 14 }}>{error}</div>}
            <button className="login-btn" type="submit" disabled={busy} style={{
              width: "100%", marginTop: 20, fontFamily: "var(--font-display)", fontSize: 14, fontWeight: 600,
              color: T.white, background: T.poppy, border: "none", borderRadius: 9, padding: "12px",
              cursor: busy ? "default" : "pointer", opacity: busy ? 0.7 : 1, transition: "background .15s ease",
            }}>
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>
        </div>
        <div style={{ textAlign: "center", marginTop: 16, fontFamily: "var(--font-text)", fontSize: 11.5 }}>
          <a href="/privacy.html" style={{ color: T.muted, textDecoration: "none" }}>Privacy Policy</a>
          <span style={{ color: T.muted, margin: "0 8px" }}>·</span>
          <a href="/eula.html" style={{ color: T.muted, textDecoration: "none" }}>Terms</a>
        </div>
        <PoweredByAcumyn tone="light" align="flex-start" />
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

  /* The operator host serves the console and NOTHING else — no tenant login, no dashboard, not
     even a redirect into one. Previously this was a /platform route inside the tenant app, which
     meant the operator login sat on every customer's domain and an operator could be signed into
     a customer's dashboard in one tab and the console in another, on the same origin. Separating
     the hosts separates the origins, so the two sessions cannot see each other's storage. */
  if (IS_OPERATOR_HOST) {
    return (
      <BrowserRouter>
        <Suspense fallback={null}>
          <Routes>
            <Route path="*" element={<PlatformConsole />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    );
  }

  return (
    <BrowserRouter>
      <Routes>
        {/* Public onboarding — always reachable, even before login */}
        <Route path="/accept-invite" element={<AcceptInvite onDone={() => setAuthed(true)} />} />
        <Route path="/reset-password" element={<ResetPassword onDone={() => setAuthed(true)} />} />
        <Route path="/share/:token" element={<ShareScorecard />} />       {/* public embed — no login */}
        <Route path="/desk/:token" element={<ShareDesk />} />             {/* rep's own Sales Desk — no login */}
        {needsLogin ? (
          <Route path="*" element={<Login onLogin={() => setAuthed(true)} />} />
        ) : (
          <>
            <Route path="/" element={<CommandCenter />} />
            <Route path="/settings/*" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </>
        )}
      </Routes>
    </BrowserRouter>
  );
}

export default App;
