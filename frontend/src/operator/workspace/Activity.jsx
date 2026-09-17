import React, { useMemo, useState } from "react";
import { api } from "../api.js";
import { ago, titleCase } from "../format.js";
import { Card, Chip, Empty, Loading, LoadError, Mono, Seg, useApi } from "../primitives.jsx";
import { A, TYPE } from "../tokens.js";

const KIND = { user: "Their team", acumyn: "Acumyn", system: "System" };

/* The actions worth a sentence. Anything else falls back to the entry's own summary, then to its
   action name made readable, so an action added later still shows up as something. */
const PHRASES = {
  "tenant.created": "Created the workspace",
  "tenant.suspended": "Suspended the workspace",
  "tenant.resumed": "Resumed the workspace",
  "tenant.invite_resent": "Reissued the owner's invite",
  "auth.login": "Signed in",
  "auth.login_failed": "Failed sign-in",
  "auth.login_blocked": "Sign-in refused",
  "auth.google_denied": "Google sign-in refused",
  "auth.forgot_password": "Asked for a password reset",
  "auth.password_reset": "Reset a password",
  "auth.find_workspace": "Looked up their workspaces",
  "user.invited": "Invited a person",
  "user.reinvited": "Resent an invite",
  "user.accepted_invite": "Accepted an invite",
  "user.role_changed": "Changed a role",
  "user.disabled": "Disabled a person",
  "user.enabled": "Re-enabled a person",
  "user.reset_link": "Sent a password reset link",
  "totp.enabled": "Turned on two-factor",
  "totp.disabled": "Turned off two-factor",
  "step_up.granted": "Unlocked a protected section",
  "step_up.failed": "Failed a second-factor check",
  "integration.connected": "Connected a source",
  "integration.disconnected": "Disconnected a source",
  "access.member.activated": "Became active on the roster",
};

function describe(e) {
  if (PHRASES[e.action]) return PHRASES[e.action];
  if (e.summary) return e.summary;
  return titleCase(e.action);
}

function target(e) {
  const d = e.detail || {};
  const bits = [];
  if (d.reason) bits.push(`Reason: ${d.reason}`);
  if (d.plan) bits.push(`plan ${d.plan}`);
  if (d.email) bits.push(d.email);
  if (d.provider) bits.push(d.provider);
  if (!bits.length && e.target_type) bits.push(e.target_type);
  return bits.join(" · ");
}

/* Machine events collapse. Consecutive events of the same kind, by the same actor, doing the same
   thing become one line with a count, so a run of identical rows can never bury the one human
   action that mattered. */
function collapse(events) {
  const out = [];
  for (const e of events) {
    const prev = out[out.length - 1];
    if (prev && prev.action === e.action && prev.actor === e.actor && prev.actor_label === e.actor_label
        && describe(prev) === describe(e) && target(prev) === target(e)) {
      prev.count += 1;
      continue;
    }
    out.push({ ...e, count: 1 });
  }
  return out;
}

export default function ActivityPane({ w }) {
  const events = useApi(() => api.tenantAudit(w.slug, 200), [w.slug]);
  const [show, setShow] = useState("all");
  const rows = useMemo(() => collapse(events.data ? events.data.events : []), [events.data]);

  if (events.loading && !events.data) return <Loading label="Reading the audit log" />;
  if (events.error) return <LoadError error={events.error} onRetry={events.reload} />;

  const shown = show === "all" ? rows : rows.filter((r) => r.actor === show);
  return (
    <Card title="Activity" pad={0}
      sub="The workspace's own audit log, newest first. Repeated events collapse to one line with a count."
      right={<Seg label="Filter activity" value={show} onChange={setShow}
        options={[["all", "All"], ["user", "Their team"], ["acumyn", "Acumyn"], ["system", "System"]]} />}>
      {shown.length === 0 ? (
        <Empty title={rows.length ? "Nothing in this bucket" : "Nothing has happened here yet"}>
          {rows.length ? "No events of this kind in the latest 200. Choose another filter."
            : "The workspace exists and nobody has used it."}
        </Empty>
      ) : shown.map((r, i) => {
        const bad = r.action === "tenant.suspended" || /failed|error|locked/.test(r.action);
        return (
          <div key={`${r.at}-${i}`} style={{ display: "flex", borderTop: `1px solid ${A.lineSoft}` }}>
            <span style={{ width: 3, background: bad ? A.stop : "transparent", flexShrink: 0 }} aria-hidden />
            <div style={{ flex: 1, minWidth: 0, padding: "11px 16px" }}>
              <div style={{ display: "flex", gap: 12, justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap" }}>
                <div style={{ minWidth: 0, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  <Chip state={r.actor === "acumyn" ? "trial" : undefined}>{KIND[r.actor]}</Chip>
                  <span style={{ fontFamily: TYPE.text, fontSize: 12.5, fontWeight: 500, color: bad ? A.stop : A.ink }}>{describe(r)}</span>
                  {r.count > 1 ? <Mono size={10.5} c={A.mute}>×{r.count}</Mono> : null}
                </div>
                <Mono size={10.5} c={A.mute}>{ago(r.at)}</Mono>
              </div>
              <div style={{ fontFamily: TYPE.text, fontSize: 11.5, color: A.mute, marginTop: 3, overflowWrap: "anywhere" }}>
                {r.actor_label || KIND[r.actor]}{target(r) ? <> · <Mono size={11} c={A.body}>{target(r)}</Mono></> : null}
              </div>
            </div>
          </div>
        );
      })}
    </Card>
  );
}
