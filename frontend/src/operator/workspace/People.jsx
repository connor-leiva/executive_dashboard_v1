import React from "react";
import { api } from "../api.js";
import { ago, daysSince, joinAnd } from "../format.js";
import { Btn, Card, Chip, Dot, Empty, Loading, LoadError, Mono, Notice, useAction, useApi } from "../primitives.jsx";
import { A, TYPE } from "../tokens.js";

const th = { fontFamily: TYPE.text, fontSize: 10, fontWeight: 600, letterSpacing: ".14em", textTransform: "uppercase", color: A.mute, textAlign: "left", padding: "0 10px 9px", whiteSpace: "nowrap" };
const td = { padding: "11px 10px", borderTop: `1px solid ${A.lineSoft}`, fontFamily: TYPE.text, fontSize: 12.5, color: A.ink, verticalAlign: "middle" };

function StatusChip({ p }) {
  if (p.expires_at) return <Chip state="trial">Support · until {new Date(p.expires_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</Chip>;
  if (p.locked) return <Chip state="broken">Locked out</Chip>;
  if (p.status === "active") return <Chip state="healthy">Active</Chip>;
  if (p.status === "invited") {
    if (p.invite_expired) return <Chip state="stalled">Invite expired</Chip>;
    const d = daysSince(p.invited_at);
    return <Chip state="stalled">{d === 0 ? "Invited today" : `Invited${d != null ? ` ${d}d ago` : ""}`}</Chip>;
  }
  return <Chip>Disabled</Chip>;
}

export { StatusChip };

/* The one action a row offers, chosen by the account's state. Every link these send goes to the
   person, never back to this screen: an invite or a reset is a way to sign in as them. */
function rowAction(p) {
  if (p.expires_at) return null;
  if (p.status === "invited") {
    return { key: "resend", label: "Resend invite", run: (slug) => api.resendInvite(slug, p.id),
      said: (r) => `A fresh invite went to ${r.email}.` };
  }
  if (p.status !== "active") return null;
  if (p.locked) {
    return { key: "unlock", label: "Unlock", run: (slug) => api.unlockPerson(slug, p.id),
      said: (r) => `${r.email} can sign in again. Their second factor, if they have one, is untouched.` };
  }
  return { key: "reset", label: "Send reset link", run: (slug) => api.sendResetLink(slug, p.id),
    said: (r) => `A password reset link went to ${r.email}. It works until ${new Date(r.expires_at).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })}.` };
}

export default function PeoplePane({ w, reload }) {
  const people = useApi(() => api.people(w.slug), [w.slug]);
  const action = useAction();
  if (people.loading && !people.data) return <Loading label="Reading accounts" />;
  if (people.error) return <LoadError error={people.error} onRetry={people.reload} />;

  const rows = people.data.people;
  const invited = rows.filter((p) => p.status === "invited").length;
  const owners = rows.filter((p) => p.role === "owner" && p.status === "active").length;
  /* Idle is the server's call (the fleet's idle-invites signal), so this button and that triage row
     can never disagree about who is idle. */
  const idleSignal = (w.signals || []).find((sig) => sig.key.endsWith(":people:idle_invites"));

  async function run(key, fn, said) {
    const out = await action.run(key, fn, said);
    if (out) { people.reload(); reload(); }
  }

  return (
    <Card title="People" pad={0}
      sub={`${rows.length} ${rows.length === 1 ? "account" : "accounts"} on this workspace${invited ? `, ${invited} still holding an unaccepted invite` : ""}.`}
      right={
        <Btn small disabled={!idleSignal} busy={action.busy === "idle"}
          title={idleSignal ? idleSignal.title : "No invite has been waiting long enough to count as idle"}
          onClick={() => run("idle", () => api.resendIdleInvites(w.slug), (r) => `Fresh invites went to ${joinAnd(r.sent_to)}.`)}>
          Resend idle invites
        </Btn>
      }>
      {action.result ? (
        <div style={{ padding: "12px 16px 0" }}><Notice tone={action.result.tone}>{action.result.text}</Notice></div>
      ) : null}
      {rows.length === 0 ? (
        <Empty title="No accounts">This workspace has no users at all, not even an owner. It cannot be administered from inside.</Empty>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", minWidth: 760, borderCollapse: "collapse" }}>
            <thead><tr>
              {["Person", "Role", "Status", "Tab access", "Last sign-in", "2FA", ""].map((h, i) => (
                <th key={h || i} scope="col" style={{ ...th, paddingLeft: i === 0 ? 16 : 10 }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {rows.map((p) => {
                const act = rowAction(p);
                const key = act ? `${act.key}:${p.id}` : null;
                return (
                  <tr key={p.id} className="ac-row">
                    <td style={{ ...td, paddingLeft: 16 }}>
                      <div style={{ fontWeight: 500 }}>{p.name}</div>
                      <Mono size={10.5} c={A.mute}>{p.email}</Mono>
                    </td>
                    <td style={td}><Chip>{p.role}</Chip></td>
                    <td style={td}><StatusChip p={p} /></td>
                    <td style={{ ...td, color: p.tabs === "all" ? A.mute : A.body }}>
                      {p.tabs === "all" ? "Everything (role)" : p.tabs.length ? p.tabs.join(", ") : "No tabs granted"}
                    </td>
                    <td style={{ ...td, color: p.last_login_at ? A.body : A.stop }}>{ago(p.last_login_at)}</td>
                    <td style={td}>
                      <span title={p.two_factor ? "Two-factor on" : "No second factor"} aria-label={p.two_factor ? "Two-factor on" : "No second factor"}>
                        <Dot c={p.two_factor ? A.ink : A.lineMid} />
                      </span>
                    </td>
                    <td style={{ ...td, textAlign: "right", paddingRight: 16, whiteSpace: "nowrap" }}>
                      {act ? (
                        <Btn small busy={action.busy === key} disabled={Boolean(action.busy) && action.busy !== key}
                          onClick={() => run(key, () => act.run(w.slug), act.said)}>{act.label}</Btn>
                      ) : p.status === "disabled" ? (
                        <span style={{ fontFamily: TYPE.text, fontSize: 11, color: A.mute }}>Only the workspace re-enables</span>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ padding: "12px 16px", borderTop: `1px solid ${A.lineSoft}`, fontFamily: TYPE.text, fontSize: 11.5, color: A.mute, lineHeight: 1.6, textWrap: "pretty" }}>
        {owners === 0
          ? "No active owner. Until an owner invite is accepted nobody can administer this workspace from inside, which is why it reads as stalled on the fleet list."
          : "An active owner exists. The product refuses to disable or demote the last owner, because a workspace nobody can administer is a ticket that cannot be closed from inside."}
        {" "}Invites and reset links go to the person by email and never appear here.
      </div>
    </Card>
  );
}
