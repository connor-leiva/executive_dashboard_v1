import { useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, Navigate, useNavigate } from "react-router-dom";

import { T } from "./theme.js";
import { login, hasToken, getJSON, getPublic, logout, setToken } from "./api.js";
import CommandCenter from "./CommandCenter.jsx";
import Settings from "./Settings.jsx";
import { AcceptInvite, ResetPassword } from "./PublicAuth.jsx";
import ShareScorecard from "./ulrg/ShareScorecard.jsx";
import ShareDesk from "./ShareDesk.jsx";
import { loadBrandOnce } from "./palette.js";
import { AuthShell, ErrorNote, Field, Handoff, PasswordField, PrimaryButton, RememberMe,
         useChrome } from "./auth/AuthShell.jsx";
import { ForgotPassword } from "./auth/ForgotPassword.jsx";
import { GoogleSignIn, googleErrorFromUrl } from "./auth/GoogleSignIn.jsx";

const API_BASE = import.meta.env.VITE_API_BASE;

/* ── Google sign-in ────────────────────────────────────────── */

/* Coming back from Google, the session arrives in the URL FRAGMENT rather than the query string,
 * because a fragment is never sent to a server — it stays out of access logs, Referer headers and
 * anything sitting in front of the app. Consumed at module load, before <App> reads hasToken(),
 * so the first render is already signed in rather than flashing the login screen.
 *
 * replaceState, not pushState: a token left in history would come back on the back button and
 * survive in a bookmark. */
function consumeGoogleRedirect() {
  /* `session` is the same hand-off from the operator console's support access: a time-boxed,
     read-only account the server made in this workspace, opened in a new tab. */
  const found = (window.location.hash || "").match(/[#&](?:google_token|session)=([^&]+)/);
  if (!found) return;
  try {
    setToken(decodeURIComponent(found[1]));
  } catch { /* a malformed fragment is just not a sign-in */ }
  window.history.replaceState({}, "", window.location.pathname + window.location.search);
}
// Called for its effect, not its answer: it writes the session to storage, so <App>'s
// useState(hasToken()) is already true on the first render and no login screen flashes past.
consumeGoogleRedirect();

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
  // answers, the page truthfully wears Axcion's identity rather than a placeholder.
  const chrome = useChrome();
  // A Google round trip that failed comes back as ?google_error=<code>. Read once into the same
  // error slot the password form uses, so there is one place errors appear on this screen.
  const [error, setError] = useState(googleErrorFromUrl);
  // Separate from `error`, because the two fields must only go red when THEY are what was
  // wrong. A failed Google round trip outlined an empty email and password as if the person had
  // mistyped them, which points at the wrong thing entirely.
  const [rejected, setRejected] = useState(false);
  const [phase, setPhase] = useState("idle");        // idle | busy | done
  // A workspace can hide the control. Hidden means remembered — see RememberMe.
  const offerRemember = !chrome || chrome.remember_me !== false;
  const [remember, setRemember] = useState(false);
  const nav = useNavigate();

  async function submit(e) {
    e.preventDefault();
    setPhase("busy");
    setError(null);
    setRejected(false);
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
      setRejected(!suspended && !locked);
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
               autoFocus value={email} invalid={rejected}
               onChange={(e) => setEmail(e.target.value)} />
        <PasswordField id="login-password" label="Password" autoComplete="current-password"
                       required value={password} invalid={rejected}
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
      {/* Outside the form on purpose: it is a separate way in, not another way to submit this
          one, and a <button> inside a form is one stray `type` away from being its submit. */}
      <GoogleSignIn onError={setError} get={getPublic} app="app" />
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

  /* AN EXPIRED SESSION SIGNS YOU OUT, ONCE, IN PLACE.
     api.js announces any 401 outside /auth/* as `cc:session-expired`. This used to be handled in
     fourteen hooks, each clearing localStorage and reloading -- but a session without "remember
     me" lives in sessionStorage, which the token reader checks FIRST. So the reload found the dead
     token,
     the dashboard fetched again, got another 401 and reloaded again: an endless loop, hammering the
     API from every open tab. One listener now; logout() clears both stores, and the sign-in screen
     renders without a reload. */
  useEffect(() => {
    function onExpired() {
      logout();
      setAuthed(false);
    }
    window.addEventListener("cc:session-expired", onExpired);
    return () => window.removeEventListener("cc:session-expired", onExpired);
  }, []);

  // The workspace's colours, type and marks, applied for EVERY authenticated route. This used to
  // live inside CommandCenter, which Settings never mounts — so Settings rendered in the
  // platform's palette, and the only thing that fixed it was visiting Appearance and leaving
  // again, because its cleanup applied the brand on the way out.
  useEffect(() => {
    if (needsLogin || !API_BASE) return;
    loadBrandOnce(getJSON).catch(() => { /* the dashboard surfaces its own load failure */ });
  }, [needsLogin]);

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
