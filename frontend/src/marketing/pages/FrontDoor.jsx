import React, { useEffect, useRef, useState } from "react";
import { AxcionLockup, CADET, CORE, NEUTRAL, TYPE } from "../../brand/axcion.jsx";
import { marketingOrigin } from "../hosts.js";
import { body, h1Type, label, Styles, WHITE } from "../ui.jsx";

/* app.axcion.io — the workspace finder.
 *
 * A workspace IS its host (utah-life.axcion.io), so there is no single page anybody can sign in
 * on. What there can be is this: somebody enters their work email, and the addresses of their
 * workspaces are EMAILED to them. Emailing is the whole security property. A page that showed
 * the answer would let anyone walk a list of brokerage email addresses and learn who is a
 * customer, so the page says the same thing whatever the address — and the API answers the same
 * thing too, so there is nothing here to branch on even by accident.
 *
 * It is also where a Google sign-in lands when it fails before a workspace is known (the API's
 * APP_PUBLIC_URL), which is why it reads ?google_error=.
 *
 * Calls the API with fetch rather than src/api.js on purpose: that module carries the tenant
 * app's session handling, and this bundle is the public marketing site. It calls exactly one
 * endpoint, which takes no session.
 */

const API = String(import.meta.env.VITE_API_BASE || "http://localhost:8000/api/v1").replace(/\/+$/, "");

/* The Google sign-in failures, as the tenant sign-in page words them (src/auth.jsx). Copied, not
   imported: importing auth.jsx would pull the whole tenant app into this bundle. Only the first
   two can reach this host — every later failure knows its workspace and returns there — but a
   code this page does not recognise still gets a sentence rather than nothing. */
const GOOGLE_ERRORS = {
  cancelled: "Google sign-in was cancelled.",
  expired: "That sign-in attempt timed out. Try again.",
  unavailable: "This workspace isn't set up for Google sign-in.",
  google: "Google couldn't complete the sign-in. Try again, or ask your admin to check the connection.",
  domain: "That Google account isn't on a domain this workspace allows.",
  no_account: "No account here matches that Google address. Ask your workspace admin to invite you.",
};
const GOOGLE_FALLBACK = "Google sign-in didn't complete. Try again from your workspace's sign-in page.";

function readGoogleError() {
  try {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("google_error");
    if (!code) return null;
    /* Off the address bar, so a reload or a shared link does not replay a stale failure. */
    params.delete("google_error");
    const rest = params.toString();
    window.history.replaceState({}, "", window.location.pathname + (rest ? `?${rest}` : ""));
    return GOOGLE_ERRORS[code] || GOOGLE_FALLBACK;
  } catch {
    return null;
  }
}

const field = {
  width: "100%", boxSizing: "border-box", fontFamily: TYPE.text, fontSize: 16, color: CORE.ink,
  background: WHITE, border: `1px solid ${NEUTRAL[300]}`, borderRadius: 10, padding: "13px 14px",
};

const submit = {
  width: "100%", display: "flex", alignItems: "center", justifyContent: "center",
  fontFamily: TYPE.text, fontWeight: 600, fontSize: 15, color: WHITE, background: CORE.ink,
  border: `1px solid ${CORE.ink}`, borderRadius: 999, padding: "14px 28px", cursor: "pointer",
};

const note = (tone) => ({
  ...body, fontSize: 14, borderRadius: 10, padding: "12px 14px", margin: 0,
  ...(tone === "error"
    ? { color: "#7A2E22", background: "#FBEFEC", border: "1px solid #EFC9C0" }
    : { color: CADET[700], background: CADET[50], border: `1px solid ${CADET[200]}` }),
});

export default function FrontDoor() {
  const site = marketingOrigin();
  const [googleError] = useState(readGoogleError);
  const [email, setEmail] = useState("");
  const [state, setState] = useState("idle");      // idle | sending | sent
  const [problem, setProblem] = useState(null);
  const sentHeading = useRef(null);

  useEffect(() => { document.title = "Find your workspace — Axcion"; }, []);
  useEffect(() => { if (state === "sent") sentHeading.current?.focus(); }, [state]);

  async function onSubmit(e) {
    e.preventDefault();
    setProblem(null);
    setState("sending");
    try {
      const res = await fetch(`${API}/auth/find-workspace`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim() }),
      });
      /* Only failures that say nothing about the ADDRESS are told apart: a malformed address, a
         rate limit, an unreachable API. Success is one answer whatever the address matched. */
      if (res.ok) { setState("sent"); return; }
      setState("idle");
      if (res.status === 422) setProblem("Enter a complete email address, like name@company.com.");
      else if (res.status === 429) setProblem("Too many lookups from this network. Wait a few minutes and try again.");
      else setProblem("Axcion couldn't send that just now. Try again in a moment.");
    } catch {
      setState("idle");
      setProblem("Axcion couldn't be reached. Check your connection and try again.");
    }
  }

  return (
    <div style={{ background: WHITE, minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <Styles />
      <header style={{ borderBottom: `1px solid ${NEUTRAL[100]}` }}>
        <div style={{
          maxWidth: 1120, margin: "0 auto", padding: "0 24px", height: 68,
          display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16,
        }}>
          <a href={`${site}/`} aria-label="Axcion home" style={{ textDecoration: "none", display: "inline-flex" }}>
            <AxcionLockup size={26} />
          </a>
          <a href={`${site}/`} className="acu-link" style={{ fontFamily: TYPE.text, fontWeight: 600, fontSize: 14 }}>
            Back to axcion.io
          </a>
        </div>
      </header>

      <main id="main" style={{ flex: 1, display: "flex", justifyContent: "center", padding: "clamp(48px, 10vh, 112px) 24px 64px" }}>
        <div style={{ width: "100%", maxWidth: 440 }}>
          {state === "sent" ? (
            <div aria-live="polite">
              <p style={{ ...label, color: CORE.cadet }}>Check your email</p>
              <h1 ref={sentHeading} tabIndex={-1}
                style={{ ...h1Type, fontSize: "clamp(28px, 4vw, 38px)", marginTop: 16, outline: "none" }}>
                If that address is on a workspace, we&rsquo;ve sent the link.
              </h1>
              <p style={{ ...body, fontSize: 16, marginTop: 18 }}>
                The email lists every workspace <strong style={{ fontWeight: 600, color: CORE.ink }}>{email.trim()}</strong> can
                sign in to. It can take a minute to arrive; if it doesn&rsquo;t, check spam, or ask
                whoever runs your team to send you an invitation.
              </p>
              <button type="button" className="acu-btn acu-btn-ghost"
                onClick={() => { setState("idle"); setEmail(""); }}
                style={{ ...submit, marginTop: 28, color: CORE.ink, background: "transparent", border: `1px solid ${NEUTRAL[200]}` }}>
                Use a different address
              </button>
            </div>
          ) : (
            <>
              <p style={{ ...label, color: CORE.cadet }}>Sign in</p>
              <h1 style={{ ...h1Type, fontSize: "clamp(30px, 4.4vw, 42px)", marginTop: 16 }}>Find your workspace</h1>
              <p style={{ ...body, fontSize: 16, marginTop: 16 }}>
                Every team on Axcion signs in at its own address. Enter your work email and
                we&rsquo;ll email you a link to each workspace it belongs to.
              </p>

              {googleError ? <p role="status" style={{ ...note("info"), marginTop: 24 }}>{googleError}</p> : null}

              <form onSubmit={onSubmit} style={{ marginTop: 28, display: "grid", gap: 14 }}>
                <label htmlFor="fd-email" style={{ fontFamily: TYPE.text, fontWeight: 600, fontSize: 14, color: CORE.ink }}>
                  Work email
                </label>
                <input
                  id="fd-email" className="acu-field" type="email" name="email" required
                  autoComplete="email" inputMode="email" placeholder="name@company.com"
                  value={email} onChange={(e) => setEmail(e.target.value)}
                  aria-describedby={problem ? "fd-problem" : undefined}
                  style={field}
                />
                {problem ? <p id="fd-problem" role="alert" style={note("error")}>{problem}</p> : null}
                <button type="submit" className="acu-btn acu-btn-primary" disabled={state === "sending"}
                  style={{ ...submit, marginTop: 4, opacity: state === "sending" ? 0.7 : 1 }}>
                  {state === "sending" ? "Sending…" : "Email me my workspace links"}
                </button>
              </form>
            </>
          )}
        </div>
      </main>

      <footer style={{ borderTop: `1px solid ${NEUTRAL[100]}`, padding: "24px 0" }}>
        <div style={{
          maxWidth: 1120, margin: "0 auto", padding: "0 24px",
          display: "flex", flexWrap: "wrap", gap: 20, justifyContent: "space-between", alignItems: "center",
        }}>
          <span style={{ fontFamily: TYPE.text, fontSize: 13.5, color: NEUTRAL[500] }}>&copy; {new Date().getFullYear()} Axcion</span>
          <span style={{ display: "flex", gap: 22 }}>
            <a href={`${site}/privacy`} className="acu-navlink" style={{ fontFamily: TYPE.text, fontSize: 13.5, color: NEUTRAL[600], textDecoration: "none" }}>Privacy</a>
            <a href={`${site}/terms`} className="acu-navlink" style={{ fontFamily: TYPE.text, fontSize: 13.5, color: NEUTRAL[600], textDecoration: "none" }}>Terms</a>
          </span>
        </div>
      </footer>
    </div>
  );
}
