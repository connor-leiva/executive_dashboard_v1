import React from "react";
import { api } from "../api.js";
import { ago, daysSince } from "../format.js";
import { Card, Chip, Dot, Empty, Loading, LoadError, Mono, useApi } from "../primitives.jsx";
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
    return <Chip state="stalled">Invited{d != null ? ` ${d}d` : ""}</Chip>;
  }
  return <Chip>Disabled</Chip>;
}

export { StatusChip };

export default function PeoplePane({ w, children }) {
  const people = useApi(() => api.people(w.slug), [w.slug]);
  if (people.loading && !people.data) return <Loading label="Reading accounts" />;
  if (people.error) return <LoadError error={people.error} onRetry={people.reload} />;

  const rows = people.data.people;
  const idle = rows.filter((p) => p.status === "invited").length;
  const owners = rows.filter((p) => p.role === "owner" && p.status === "active").length;
  return (
    <Card title="People" pad={0}
      sub={`${rows.length} ${rows.length === 1 ? "account" : "accounts"} on this workspace${idle ? `, ${idle} still holding an unaccepted invite` : ""}.`}
      right={typeof children === "function" ? children({ rows, reload: people.reload }) : null}>
      {rows.length === 0 ? (
        <Empty title="No accounts">This workspace has no users at all, not even an owner. It cannot be administered from inside.</Empty>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", minWidth: 680, borderCollapse: "collapse" }}>
            <thead><tr>
              {["Person", "Role", "Status", "Tab access", "Last sign-in", "2FA", ""].map((h, i) => (
                <th key={h || i} scope="col" style={{ ...th, paddingLeft: i === 0 ? 16 : 10 }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {rows.map((p) => (
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
                  <td style={{ ...td, textAlign: "right", paddingRight: 16 }} data-person={p.id} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ padding: "12px 16px", borderTop: `1px solid ${A.lineSoft}`, fontFamily: TYPE.text, fontSize: 11.5, color: A.mute, lineHeight: 1.6, textWrap: "pretty" }}>
        {owners === 0
          ? "No active owner. Until an owner invite is accepted nobody can administer this workspace from inside, which is why it reads as stalled on the fleet list."
          : "An active owner exists. The product refuses to disable or demote the last owner, because a workspace nobody can administer is a ticket that cannot be closed from inside."}
      </div>
    </Card>
  );
}
