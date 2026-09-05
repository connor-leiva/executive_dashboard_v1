/* The two screens somebody reaches from a link in their email: accept an invite, or set a new
 * password after a reset. Both end with a live session, so both are as much "sign in" as the
 * sign-in page is — which is why they wear the same shell rather than a card of their own.
 *
 * THE RULES ARE SHOWN, NOT ENFORCED BY SURPRISE. The requirement list ticks as you type. The
 * alternative — a form that accepts what you typed and then says "too short" — is the version
 * that makes somebody try four passwords before finding one the product will take.
 */
import { useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";

import { T } from "./theme.js";
import { postPublic, setToken } from "./api.js";
import {
  AuthShell, ErrorNote, Field, Handoff, PasswordField, PrimaryButton, useChrome,
} from "./auth/AuthShell.jsx";

const FONT = "var(--font-text)";

// Must match backend security.MIN_PASSWORD_LEN. Shown rather than discovered on submit.
const MIN_LEN = 10;

/* What the server will and won't accept, as a list you can watch turn green. Each row is a real
   check — a decorative one that ticks regardless teaches people to ignore the whole panel. */
function Requirements({ value, confirm, needsConfirm }) {
  const rows = [
    { ok: value.length >= MIN_LEN, text: `At least ${MIN_LEN} characters` },
    { ok: /\d/.test(value), text: "Contains a number", advisory: true },
  ];
  if (needsConfirm) {
    rows.push({ ok: Boolean(value) && value === confirm, text: "Both entries match" });
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 9, padding: "14px 15px",
                  borderRadius: 10, background: T.parchment }}>
      {rows.map((r) => (
        <div key={r.text} style={{ display: "flex", alignItems: "center", gap: 9,
                                   fontFamily: FONT, fontSize: 13,
                                   color: r.ok ? T.secondary : T.muted }}>
          {r.ok ? (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={T.poppy}
                 strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden
                 style={{ flex: "none" }}><path d="M4 12.5 L9.5 18 L20 6.5" /></svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={T.muted}
                 strokeWidth="2.6" strokeLinecap="round" aria-hidden
                 style={{ flex: "none" }}><path d="M6 12 H18" /></svg>
          )}
          {r.text}
          {/* Named as guidance rather than left ambiguous: the server does not require a digit,
              and a rule that blocks nothing while looking like it does is a small lie. */}
          {r.advisory ? <span style={{ color: T.muted, fontSize: 12 }}>· recommended</span> : null}
        </div>
      ))}
    </div>
  );
}

/** A link that arrived without its token — worth its own screen, because "invalid or expired"
 *  sends people hunting for a fresh link when the real problem is a truncated paste. */
function BrokenLink({ chrome, what }) {
  return (
    <AuthShell chrome={chrome} title={`This ${what} link is incomplete`}
               sub={`The address is missing its token, which usually means the link was cut short
                     when it was copied. Open it straight from the email, or ask your workspace
                     admin to send a new one.`} />
  );
}

export function AcceptInvite({ onDone }) {
  const chrome = useChrome();
  const [sp] = useSearchParams();
  const token = sp.get("token");
  const [name, setName] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState(null);
  const [phase, setPhase] = useState("idle");
  const nav = useNavigate();

  async function submit(e) {
    e.preventDefault();
    setPhase("busy"); setErr(null);
    try {
      const { token: jwt } = await postPublic("/auth/accept-invite",
                                              { token, name, password: pw });
      setToken(jwt);
      setPhase("done");
      onDone && onDone();
      nav("/", { replace: true });
    } catch (x) {
      setErr(x.detail || x.message || "Couldn't accept the invite. Ask your admin to resend it.");
      setPhase("idle");
    }
  }

  if (!token) return <BrokenLink chrome={chrome} what="invite" />;
  return (
    <AuthShell chrome={chrome} title="Set up your account"
               sub="Choose how your name appears and a password, and you're in."
               overlay={phase === "done"
                 ? <Handoff title="You're in" sub="Loading your dashboard." /> : null}>
      {err ? <ErrorNote>{err}</ErrorNote> : null}
      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 18 }}>
        <Field id="invite-name" label="Your name" autoComplete="name" required autoFocus
               value={name} onChange={(e) => setName(e.target.value)} />
        <PasswordField id="invite-password" label="Password" autoComplete="new-password" required
                       minLength={MIN_LEN} value={pw} onChange={(e) => setPw(e.target.value)} />
        <Requirements value={pw} />
        <PrimaryButton type="submit" busy={phase !== "idle"} busyLabel="Setting up">
          Accept invite
        </PrimaryButton>
      </form>
    </AuthShell>
  );
}

export function ResetPassword({ onDone }) {
  const chrome = useChrome();
  const [sp] = useSearchParams();
  const token = sp.get("token");
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState(null);
  const [phase, setPhase] = useState("idle");
  const nav = useNavigate();

  const mismatch = Boolean(confirm) && pw !== confirm;

  async function submit(e) {
    e.preventDefault();
    // Caught here rather than by the API: the server has no second field to compare against, so
    // a typo in one of them would otherwise become a password nobody knows.
    if (pw !== confirm) { setErr("Those two passwords don't match."); return; }
    setPhase("busy"); setErr(null);
    try {
      const { token: jwt } = await postPublic("/auth/reset-password",
                                              { token, new_password: pw });
      setToken(jwt);
      setPhase("done");
      onDone && onDone();
      nav("/", { replace: true });
    } catch (x) {
      setErr(x.detail || x.message || "Couldn't reset your password. Ask for a new link.");
      setPhase("idle");
    }
  }

  if (!token) return <BrokenLink chrome={chrome} what="reset" />;
  return (
    <AuthShell chrome={chrome} title="Set a new password"
               sub="Choose something you haven't used here before. Signing in with it takes effect
                    on every device."
               overlay={phase === "done"
                 ? <Handoff title="Password changed" sub="Loading your dashboard." /> : null}>
      {err ? <ErrorNote>{err}</ErrorNote> : null}
      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 18 }}>
        <PasswordField id="reset-password" label="New password" autoComplete="new-password"
                       required autoFocus minLength={MIN_LEN} value={pw}
                       onChange={(e) => setPw(e.target.value)} />
        <PasswordField id="reset-confirm" label="Confirm password" autoComplete="new-password"
                       required value={confirm} invalid={mismatch}
                       onChange={(e) => setConfirm(e.target.value)} />
        <Requirements value={pw} confirm={confirm} needsConfirm />
        <PrimaryButton type="submit" busy={phase !== "idle"} busyLabel="Saving">
          Save and sign in
        </PrimaryButton>
      </form>
    </AuthShell>
  );
}
