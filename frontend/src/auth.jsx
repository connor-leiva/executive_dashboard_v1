import { Suspense, lazy, useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, Navigate, useNavigate } from "react-router-dom";

// Lazy so the operator console is its own chunk. A customer's browser has no reason to download
// the screen that suspends customers, and a static import puts it in everybody's bundle.
const PlatformConsole = lazy(() => import("./platform/PlatformConsole.jsx"));
import { T } from "./theme.js";
import { login, hasToken, getJSON, getPublic, setToken } from "./api.js";
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

/* ── Google sign-in ────────────────────────────────────────── */

/* Coming back from Google, the session arrives in the URL FRAGMENT rather than the query string,
 * because a fragment is never sent to a server — it stays out of access logs, Referer headers and
 * anything sitting in front of the app. Consumed at module load, before <App> reads hasToken(),
 * so the first render is already signed in rather than flashing the login screen.
 *
 * replaceState, not pushState: a token left in history would come back on the back button and
 * survive in a bookmark. */
function consumeGoogleRedirect() {
  const found = (window.location.hash || "").match(/[#&]google_token=([^&]+)/);
  if (!found) return;
  try {
    setToken(decodeURIComponent(found[1]));
  } catch { /* a malformed fragment is just not a sign-in */ }
  window.history.replaceState({}, "", window.location.pathname + window.location.search);
}
// Called for its effect, not its answer: it writes the session to storage, so <App>'s
// useState(hasToken()) is already true on the first render and no login screen flashes past.
consumeGoogleRedirect();

/* Every way the round trip can fail, said in a sentence somebody can act on. The server sends a
   short code rather than a message: the callback is shared infrastructure, and the specifics —
   which address was refused — are exactly what should not be echoed back through a URL. */
const GOOGLE_ERRORS = {
  cancelled: "Google sign-in was cancelled.",
  expired: "That sign-in attempt timed out. Try again.",
  unavailable: "This workspace isn't set up for Google sign-in.",
  google: "Google couldn't complete the sign-in. Try again, or ask your admin to check the connection.",
  domain: "That Google account isn't on a domain this workspace allows.",
  // Says plainly that there is no account, which the password form deliberately never does. The
  // difference is that this person has just proved to Google they own the address, so we are not
  // telling a stranger anything about somebody else's.
  no_account: "No account here matches that Google address. Ask your workspace admin to invite you.",
};

function GoogleMark() {
  return (
    <svg width="17" height="17" viewBox="0 0 48 48" aria-hidden style={{ flex: "none" }}>
      <path fill="#4285F4" d="M45.1 24.5c0-1.6-.1-2.8-.4-4H24v7.2h12.1c-.2 2-1.6 5-4.5 7l-.1.3 6.5 5 .5.1c4.1-3.8 6.6-9.4 6.6-15.6z" />
      <path fill="#34A853" d="M24 46c5.9 0 10.9-2 14.5-5.3l-6.9-5.4c-1.8 1.3-4.3 2.2-7.6 2.2-5.8 0-10.7-3.8-12.500-9.1l-.3.1-6.7 5.2-.1.3C7.9 41 15.4 46 24 46z" />
      <path fill="#FBBC05" d="M11.5 28.4c-.5-1.4-.7-2.9-.7-4.4s.3-3 .7-4.4v-.3l-6.8-5.3-.2.1A22 22 0 0 0 2 24c0 3.5.9 6.9 2.5 9.9l7-5.5z" />
      <path fill="#EA4335" d="M24 10.2c4.1 0 6.9 1.8 8.5 3.3l6.2-6C34.9 4 29.9 2 24 2 15.4 2 7.9 7 4.5 14.1l7 5.5c1.8-5.3 6.7-9.4 12.5-9.4z" />
    </svg>
  );
}

/** Offered only where the workspace has set Google up — asked of the server rather than assumed,
 *  so a team on passwords never sees a button that cannot work. */
function GoogleSignIn({ onError }) {
  const [available, setAvailable] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    getPublic("/auth/google/config")
      .then((r) => { if (live) setAvailable(Boolean(r && r.enabled)); })
      .catch(() => { /* no button is the right answer when we cannot tell */ });
    return () => { live = false; };
  }, []);

  if (!available) return null;

  async function go() {
    setBusy(true);
    try {
      const { url } = await getPublic("/auth/google/start");
      window.location.assign(url);
    } catch {
      onError("Couldn't reach Google just now. Try again, or sign in with your password.");
      setBusy(false);
    }
  }

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 12, margin: "22px 0 18px" }}>
        <span style={{ flex: 1, height: 1, background: T.line }} />
        <span style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.muted }}>or</span>
        <span style={{ flex: 1, height: 1, background: T.line }} />
      </div>
      <button type="button" className="acu-quiet" onClick={go} disabled={busy} style={{
        height: 48, display: "flex", alignItems: "center", justifyContent: "center", gap: 10,
        width: "100%", borderRadius: "var(--acu-btn-r, 999px)", background: T.white,
        border: `1px solid ${T.line}`, fontFamily: "var(--font-text)", fontWeight: 600,
        fontSize: 14.5, color: T.ink, cursor: busy ? "default" : "pointer",
      }}>
        <GoogleMark />
        {busy ? "Opening Google…" : "Continue with Google"}
      </button>
    </>
  );
}

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
  // A Google round trip that failed comes back as ?google_error=<code>. Read once into the same
  // error slot the password form uses, so there is one place errors appear on this screen.
  const [error, setError] = useState(() => {
    const code = new URLSearchParams(window.location.search).get("google_error");
    return code ? (GOOGLE_ERRORS[code] || GOOGLE_ERRORS.google) : null;
  });
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
      <GoogleSignIn onError={setError} />
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
