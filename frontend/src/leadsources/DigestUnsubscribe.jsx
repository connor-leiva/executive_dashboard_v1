/* /digest/unsubscribe?token=… -- the link at the bottom of every lead-source digest email.

   Public, like the password-reset pages it is built from (AuthShell): the signed token names the
   digest and the address, so nobody signs in to stop an email. One press confirms it; a link
   somebody forwarded cannot unsubscribe anyone by being opened. */
import { useState } from "react";
import { T } from "../theme.js";
import { postPublic } from "../api.js";
import { AuthShell, Badge, ErrorNote, PrimaryButton, Tick, useChrome } from "../auth/AuthShell.jsx";

export default function DigestUnsubscribe() {
  const chrome = useChrome();
  const token = new URLSearchParams(window.location.search).get("token") || "";
  const [state, setState] = useState("idle");    // idle | busy | done | error
  const [email, setEmail] = useState(null);
  const [error, setError] = useState(null);

  async function confirm() {
    setState("busy");
    try {
      const out = await postPublic("/public/lead-sources/digest/unsubscribe", { token });
      setEmail(out.email);
      setState("done");
    } catch (e) {
      setError((e && e.detail) || "This link has expired or isn't valid.");
      setState("error");
    }
  }

  if (state === "done") {
    return (
      <AuthShell chrome={chrome} title="You're unsubscribed"
        above={<div style={{ margin: "0 auto 24px" }}><Badge size={46} radius={12}><Tick size={22} /></Badge></div>}
        sub={<><strong style={{ color: T.ink, fontWeight: 600 }}>{email}</strong> won't get this weekly digest again. An admin can add you back in Settings.</>} />
    );
  }
  return (
    <AuthShell chrome={chrome} title="Stop the weekly digest?"
      sub="You'll stop getting this lead-source coaching email. Nothing else changes.">
      {state === "error" && <ErrorNote>{error}</ErrorNote>}
      <PrimaryButton type="button" onClick={confirm} busy={state === "busy"} busyLabel="Unsubscribing" disabled={!token}>
        Unsubscribe
      </PrimaryButton>
    </AuthShell>
  );
}
