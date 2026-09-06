/* The two screens somebody reaches from a link in their email: accept an invite, or set a new
 * password after a reset. Both end with a live session, so both are as much "sign in" as the
 * sign-in page is — which is why they wear the same shell rather than a card of their own.
 *
 * THE RULES ARE SHOWN, NOT ENFORCED BY SURPRISE. The requirement list ticks as you type. The
 * alternative — a form that accepts what you typed and then says "too short" — is the version
 * that makes somebody try four passwords before finding one the product will take.
 */
import { useEffect, useState } from "react";
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

/* WHICH ACCOUNT this link is for, asked once when the screen opens and without spending the
 * link. The address is not decoration: it is the thing you type to sign in tomorrow, and until
 * now the only copy of it was in the email that carried the link. Somebody with two addresses
 * had no way to tell which one they had just set a password for, and the sign-in page cannot
 * help — it answers "Invalid email or password" for every guess on purpose.
 */
function useLinkAccount(token, purpose) {
  const [state, setState] = useState({ phase: token ? "loading" : "ready", account: null });
  useEffect(() => {
    if (!token) return undefined;
    let live = true;
    postPublic("/auth/link-info", { token, purpose })
      .then((a) => { if (live) setState({ phase: "ready", account: a }); })
      // 400 is the server saying this link is spent or expired — worth saying NOW rather than
      // after somebody has chosen a password. Nothing else may be read that way: on a blip or
      // an offline browser the form still renders, and the submit gets the real answer.
      .catch((x) => {
        if (live) setState({ phase: x.status === 400 ? "dead" : "ready", account: null });
      });
    return () => { live = false; };
  }, [token, purpose]);
  return state;
}

/** The address the link belongs to. Shown so you know what you will sign in with, and carried
 *  as a real `username` input so the browser's password manager files the new password against
 *  an account rather than against nothing — a credential saved with no username cannot be
 *  offered back, which is what turns "I don't remember" into "I am locked out".
 *
 *  Read-only: the invite decides the address. A field that looked editable but was ignored on
 *  submit would be worse than not showing one. */
function SignInAs({ email, hint }) {
  return (
    <Field id="link-email" name="username" type="email" autoComplete="username"
           label="You'll sign in with" value={email} readOnly hint={hint} />
  );
}

/** A link that is genuine but no longer usable. Its own screen rather than an error on the
 *  form, because the remedy is usually nothing at all: the account is already set up and the
 *  person simply needs the sign-in page. */
function SpentLink({ chrome, what }) {
  return (
    <AuthShell chrome={chrome} title={`This ${what} link is no longer valid`}
               sub={`${what === "invite" ? "Invite" : "Reset"} links work once and expire after a
                     few days. If your account is already set up, sign in instead — otherwise ask
                     your workspace admin to send a new one.`}>
      <a href="/" style={{ fontFamily: FONT, fontSize: 14, fontWeight: 600, color: T.poppyText,
                           textDecoration: "none" }}>Go to sign in</a>
    </AuthShell>
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
  const link = useLinkAccount(token, "invite");
  const [name, setName] = useState("");
  const [pw, setPw] = useState("");
  const [err, setErr] = useState(null);
  const [phase, setPhase] = useState("idle");
  const nav = useNavigate();

  // If whoever sent the invite already recorded a name, start with it. `v || ...` rather than a
  // plain set: the field is autofocused, so somebody who types before the lookup lands must not
  // have it overwritten underneath them.
  useEffect(() => {
    if (link.account && link.account.name) setName((v) => v || link.account.name);
  }, [link.account]);

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
  if (link.phase === "dead") return <SpentLink chrome={chrome} what="invite" />;
  if (link.phase === "loading") {
    return <AuthShell chrome={chrome} title="Set up your account" sub="Checking your invite…" />;
  }
  return (
    <AuthShell chrome={chrome} title="Set up your account"
               sub="Choose how your name appears and a password, and you're in."
               overlay={phase === "done"
                 ? <Handoff title="You're in" sub="Loading your dashboard." /> : null}>
      {err ? <ErrorNote>{err}</ErrorNote> : null}
      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 18 }}>
        {link.account ? (
          <SignInAs email={link.account.email}
                    hint="Set by your invite. Ask your workspace admin if it should be a
                          different address." />
        ) : null}
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
  const link = useLinkAccount(token, "reset");
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
  if (link.phase === "dead") return <SpentLink chrome={chrome} what="reset" />;
  if (link.phase === "loading") {
    return <AuthShell chrome={chrome} title="Set a new password" sub="Checking your link…" />;
  }
  return (
    <AuthShell chrome={chrome} title="Set a new password"
               sub="Choose something you haven't used here before. Signing in with it takes effect
                    on every device."
               overlay={phase === "done"
                 ? <Handoff title="Password changed" sub="Loading your dashboard." /> : null}>
      {err ? <ErrorNote>{err}</ErrorNote> : null}
      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 18 }}>
        {link.account ? (
          <SignInAs email={link.account.email}
                    hint="The account this link resets." />
        ) : null}
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
