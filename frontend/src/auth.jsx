import { Suspense, lazy, useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, Navigate, useNavigate } from "react-router-dom";

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
import { loadBrandOnce } from "./palette.js";
import { AuthShell, ErrorNote, Field, Handoff, PasswordField, PrimaryButton, RememberMe,
         useChrome } from "./auth/AuthShell.jsx";
import { ForgotPassword } from "./auth/ForgotPassword.jsx";

const API_BASE = import.meta.env.VITE_API_BASE;

/* The operator console lives at admin.<whatever this deployment is served from>, and nowhere
   else. Keyed on the first label rather than the full host so it needs no knowledge of the
   platform domain, which differs across local, staging and production — and so `admin.localhost`
   works in development with no special case. `admin` is reserved in backend tenancy.PLATFORM_HOSTS
   and can never resolve to a customer, so this host cannot collide with one. */
const IS_OPERATOR_HOST = window.location.hostname.split(".")[0] === "admin";

/* ── Login screen ──────────────────────────────────────────── */

/* The four states of one screen, not four screens: idle, rejected, submitting, and the handoff
   while the dashboard loads behind the panel. The handoff matters more than it looks — without
   it, a correct password leaves you staring at a spinner inside a form you have finished with,
   and the slowest part of signing in (fetching a workspace of data) reads as the sign-in itself
   having stalled. */

export function Login({ onLogin }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // Before you sign in you are at the platform, not inside a workspace — so until /public/brand
  // answers, the page truthfully wears Acumyn's identity rather than a placeholder.
  const chrome = useChrome();
  const [error, setError] = useState(null);
  const [phase, setPhase] = useState("idle");        // idle | busy | done
  // A workspace can hide the control. Hidden means remembered — see RememberMe.
  const offerRemember = !chrome || chrome.remember_me !== false;
  const [remember, setRemember] = useState(false);
  const nav = useNavigate();

  async function submit(e) {
    e.preventDefault();
    setPhase("busy");
    setError(null);
    try {
      await login(email, password, offerRemember ? remember : true);
      // Stay on "done" rather than unmounting immediately: onLogin swaps the route to the
      // dashboard, and the overlay covers the gap while that mounts.
      setPhase("done");
      onLogin();
    } catch (err) {
      // The API answers 401 for a wrong password, an unknown address, an account not yet
      // accepted and a disabled one — all the same, on purpose. So this sentence must cover all
      // four without hinting which, and still tell somebody what to try next.
      const locked = err && err.status === 423;
      const suspended = err && err.status === 403;
      setError(suspended
        ? (err.detail || "This workspace is suspended. Contact your administrator.")
        : locked
          ? "Too many attempts. Wait a few minutes and try again."
          : "That email and password don't match. Check both, or reset your password.");
      setPhase("idle");
    }
  }

  return (
    <AuthShell
      chrome={chrome}
      title="Sign in"
      sub="Use the email address your workspace set up for you."
      overlay={phase === "done"
        ? <Handoff title="Signed in" sub="Loading your dashboard." />
        : null}
    >
      {error ? <ErrorNote>{error}</ErrorNote> : null}
      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 18 }}>
        <Field id="login-email" label="Email" type="email" autoComplete="username" required
               autoFocus value={email} invalid={Boolean(error)}
               onChange={(e) => setEmail(e.target.value)} />
        <PasswordField id="login-password" label="Password" autoComplete="current-password"
                       required value={password} invalid={Boolean(error)}
                       onChange={(e) => setPassword(e.target.value)} />
        <div style={{ display: "flex", alignItems: "center", gap: 16, marginTop: 2 }}>
          {offerRemember ? <RememberMe checked={remember} onChange={setRemember} /> : null}
          <a href="/forgot-password" style={{
            marginLeft: "auto", fontFamily: "var(--font-text)", fontSize: 13.5, fontWeight: 500,
            color: T.poppy, textDecoration: "none",
          }} onClick={(e) => { e.preventDefault(); nav("/forgot-password"); }}>Forgot password?</a>
        </div>
        <PrimaryButton type="submit" busy={phase !== "idle"} busyLabel="Signing in">
          Sign in
        </PrimaryButton>
      </form>
    </AuthShell>
  );
}

/* ── App wrapper ───────────────────────────────────────────── */
/* Dev runs without a backend, so the dashboard handles its own sample
   fallback. Only gate behind Login when an API base is configured and
   there's no stored token; otherwise go straight to the dashboard. */

export function App() {
  const [authed, setAuthed] = useState(hasToken());
  const needsLogin = Boolean(API_BASE) && !authed;

  // The workspace's colours, type and marks, applied for EVERY authenticated route. This used to
  // live inside CommandCenter, which Settings never mounts — so Settings rendered in the
  // platform's palette, and the only thing that fixed it was visiting Appearance and leaving
  // again, because its cleanup applied the brand on the way out.
  useEffect(() => {
    if (needsLogin || !API_BASE) return;
    loadBrandOnce(getJSON).catch(() => { /* the dashboard surfaces its own load failure */ });
  }, [needsLogin]);

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
        {/* Reachable signed-out AND signed-in: somebody who is logged in on one device and
            locked out on another still needs it, and a redirect to the dashboard here would
            look like the link was broken. */}
        <Route path="/forgot-password" element={<ForgotPassword />} />
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
