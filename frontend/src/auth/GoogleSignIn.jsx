/* "Continue with Google", for every surface that signs somebody in.
 *
 * Lifted out of the dashboard's login when the admin console needed it too. A second copy would
 * have drifted the way the session rule did -- and this one has a detail worth keeping in one
 * place: the button is offered only where the SERVER says the workspace has Google configured,
 * so nobody is shown a way in that cannot work.
 *
 * `app` names the surface the person started from. It is a KEY, not a path: the server owns the
 * mapping (auth.GOOGLE_RETURN) because the callback carries a session in its fragment, and a
 * callback that redirects wherever it is told is an open redirect.
 *
 * `get` is the caller's own public fetcher. The console can be pointed at a workspace that is not
 * the host it is served from, and its fetcher knows that where the dashboard's does not.
 */
import { useEffect, useState } from "react";

import { T } from "../theme.js";

/* Every way the round trip can fail, said in a sentence somebody can act on. The server sends a
   short code rather than a message: the callback is shared infrastructure, and the specifics —
   which address was refused — are exactly what should not be echoed back through a URL. */
export const GOOGLE_ERRORS = {
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

/** The error a Google round trip came back with, read from the URL once, or null. */
export function googleErrorFromUrl() {
  const code = new URLSearchParams(window.location.search).get("google_error");
  return code ? (GOOGLE_ERRORS[code] || GOOGLE_ERRORS.google) : null;
}

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

/** Offered wherever Axcion's Google app is configured and this workspace has not turned it off —
 *  asked of the server rather than assumed, so nobody is shown a button that cannot work. */
export function GoogleSignIn({ onError, get, app = "app" }) {
  const [available, setAvailable] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    get("/auth/google/config")
      .then((r) => { if (live) setAvailable(Boolean(r && r.enabled)); })
      .catch(() => { /* no button is the right answer when we cannot tell */ });
    return () => { live = false; };
  }, [get]);

  if (!available) return null;

  async function go() {
    setBusy(true);
    try {
      const { url } = await get(`/auth/google/start?app=${encodeURIComponent(app)}`);
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
