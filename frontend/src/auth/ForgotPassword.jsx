/* "I can't get in." Two screens: ask for the address, then tell them to go and look.
 *
 * THE SECOND SCREEN IS SHOWN WHATEVER HAPPENED. The API answers identically for an address that
 * exists and one that does not — deliberately, because a public form that says "no account with
 * that email" reads off the customer list one guess at a time, and these are work addresses at a
 * named brokerage. So this screen cannot say "sent!" as a fact. It says what was asked for and
 * what to do if nothing arrives, which is true either way and is also the more useful sentence
 * for the person who simply mistyped their own address.
 */
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { T } from "../theme.js";
import { postPublic } from "../api.js";
import { AuthShell, BackLink, Badge, Field, PrimaryButton, QuietButton,
         useChrome } from "./AuthShell.jsx";

const FONT = "var(--font-text)";

function MailGlyph() {
  return (
    <svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke={T.poppy}
         strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="2.5" y="5" width="19" height="14" rx="2.5" />
      <path d="M3.4 6.6 L12 13 L20.6 6.6" />
    </svg>
  );
}

export function ForgotPassword() {
  const chrome = useChrome();
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [resent, setResent] = useState(false);
  const nav = useNavigate();

  async function ask(e) {
    if (e) e.preventDefault();
    setBusy(true);
    try {
      await postPublic("/auth/forgot-password", { email: email.trim() });
    } catch {
      // Deliberately swallowed. The endpoint answers 200 for every address, so the only errors
      // reaching here are transport ones — and showing a failure would make an unreachable API
      // look like "that address is not registered", which is the one thing this must never say.
    } finally {
      setBusy(false);
      setSent(true);
    }
  }

  async function resend() {
    setResent(true);
    await ask();
  }

  const back = (
    <BackLink to="/" onClick={(e) => { e.preventDefault(); nav("/", { replace: true }); }}>
      Back to sign in
    </BackLink>
  );

  if (!sent) {
    return (
      <AuthShell chrome={chrome} back={back} title="Reset your password"
                 sub="Enter your work email and we'll send a link to set a new one.">
        <form onSubmit={ask} style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <Field id="forgot-email" label="Email" type="email" autoComplete="username" required
                 autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />
          <PrimaryButton type="submit" busy={busy} busyLabel="Sending">Send reset link</PrimaryButton>
        </form>
      </AuthShell>
    );
  }

  return (
    <AuthShell chrome={chrome} title="Check your email"
               above={<div style={{ margin: "0 auto 24px" }}>
                        <Badge size={46} radius={12}><MailGlyph /></Badge>
                      </div>}
               sub={<>If <strong style={{ color: T.ink, fontWeight: 600 }}>{email.trim()}</strong>{" "}
                    has an account, a reset link is on its way. It expires in 24 hours.</>}>
      <div style={{ display: "flex", flexDirection: "column" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <QuietButton type="button" onClick={resend} disabled={busy}>
            {busy ? "Sending…" : resent ? "Sent again" : "Resend link"}
          </QuietButton>
          <a href="/" onClick={(e) => { e.preventDefault(); nav("/", { replace: true }); }}
             style={{ textAlign: "center", fontFamily: FONT, fontSize: 13.5, fontWeight: 500,
                      color: T.muted, textDecoration: "none" }}>
            Back to sign in
          </a>
        </div>
        <p style={{ fontFamily: FONT, fontSize: 12.5, lineHeight: 1.5, color: T.muted,
                    margin: "26px 0 0", paddingTop: 18, borderTop: `1px solid ${T.line}`,
                    textWrap: "pretty" }}>
          Nothing in your inbox? Check spam, or ask your workspace admin to confirm the address on
          your account.
        </p>
      </div>
    </AuthShell>
  );
}

export default ForgotPassword;
