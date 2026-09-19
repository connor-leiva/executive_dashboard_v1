import { useEffect, useMemo, useState } from "react";

import ProfileEditor from "../ProfileEditor.jsx";

import {
  AUTH_SOURCE_OPTIONS,
  COPY,
  DEFAULT_ROSTER_FILTER,
  GUEST_AUTH_SOURCE,
  GUEST_ROLE_KEY,
  REMOVED_MEMBER_STATUS,
  ROSTER_FILTERS,
} from "../constants.js";
import {
  useCrmAgents,
  useInviteMember,
  useMembers,
  usePatchMember,
  useRemoveMember,
  useSendMemberInvite,
  useSyncMembers,
} from "../queries.js";
import { Button, EmptyState, ErrorState, Field, LoadingState, Panel } from "../ui.jsx";

function formatDate(iso) {
  if (!iso) return COPY.rosterNeverSynced;
  const value = new Date(iso);
  if (Number.isNaN(value.getTime())) return COPY.rosterNeverSynced;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(value);
}

function useDebouncedValue(value, delay) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

function StatCard({ label, value }) {
  return (
    <div className="roster-stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function StatusChip({ status }) {
  return <span className={`status-chip status-${String(status || "").toLowerCase()}`}>{status}</span>;
}

function InviteForm({ roles, inviteMutation }) {
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [market, setMarket] = useState("");
  const [authSource, setAuthSource] = useState(GUEST_AUTH_SOURCE);
  const [roleId, setRoleId] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (roleId || !roles.length) return;
    // Match the server's rule (console.guest_role_of): the least privileged role, meaning the
    // highest sort among non-leadership ones. The old fallback was roles[0] when "jv_partner"
    // was missing -- which on any workspace but Utah Life's meant this disabled box read
    // "Owner" while the server was quietly assigning the bottom role. A field that names the
    // wrong answer is worse than one that names none.
    const guestRole = roles.find((role) => role.key === GUEST_ROLE_KEY)
      || [...roles].sort((a, b) =>
           (a.is_leadership ? 1 : 0) - (b.is_leadership ? 1 : 0) || (b.sort || 0) - (a.sort || 0))[0];
    setRoleId(guestRole.id);
  }, [roleId, roles]);

  async function submit(event) {
    event.preventDefault();
    setMessage("");
    const body = {
      full_name: fullName,
      email,
      market: market || null,
      auth_source: authSource,
    };
    if (authSource !== GUEST_AUTH_SOURCE) body.role_id = roleId;
    try {
      const result = await inviteMutation.mutateAsync(body);
      setFullName("");
      setEmail("");
      setMarket("");
      setAuthSource(GUEST_AUTH_SOURCE);
      // ADDED, NOT INVITED: the account exists and nothing has been sent. Somebody who already
      // had an account can sign in as they are, so there is nothing to send them at all.
      setMessage(result?.item?.invite === "accepted"
        ? "Added. They already have an account, so there is no invite to send."
        : COPY.rosterInvited);
    } catch (err) {
      setMessage(err.detail || err.message);
    }
  }

  return (
    <Panel title={COPY.rosterInvite}>
      <form className="roster-form" onSubmit={submit}>
        <Field label={COPY.rosterName}>
          <input value={fullName} onChange={(event) => setFullName(event.target.value)} required />
        </Field>
        <Field label={COPY.rosterEmail}>
          <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required />
        </Field>
        <Field label={COPY.rosterMarket}>
          <input value={market} onChange={(event) => setMarket(event.target.value)} />
        </Field>
        <Field label={COPY.rosterAuth}>
          <select value={authSource} onChange={(event) => setAuthSource(event.target.value)}>
            {AUTH_SOURCE_OPTIONS.map((option) => (
              <option key={option.key} value={option.key}>{option.label}</option>
            ))}
          </select>
        </Field>
        <Field label={COPY.rosterRole}>
          <select
            value={roleId}
            onChange={(event) => setRoleId(event.target.value)}
            disabled={authSource === GUEST_AUTH_SOURCE || !roles.length}
          >
            {roles.map((role) => (
              <option key={role.id} value={role.id}>{role.name}</option>
            ))}
          </select>
        </Field>
        <p className="console-help">
          They get an account now and are not emailed. Send the invite from their row when you are
          ready; until then they cannot sign in and the workspace finder does not list them.
        </p>
        {message ? <p className="roster-form-message">{message}</p> : null}
        <Button type="submit" tone="primary" busy={inviteMutation.isPending}>{COPY.rosterInvite}</Button>
      </form>
    </Panel>
  );
}

/* WHO EACH PERSON IS IN THE CRM, and a way to say so when the email cannot.
 *
 * A member's own numbers and their follow-ups come from the Sisu agent and the Follow Up Boss user
 * matched to them, by email unless somebody links one. The portal has told agents "an admin can
 * set your address on the roster" since the numbers shipped, and there was no such control: the
 * override existed in the database and on no screen. This column shows every member's match and
 * how it was made, and the editor picks the user from the CRM's own list rather than asking an
 * admin to type an address they would have to go and look up. */
function crmLabel(match, source) {
  const name = source === "fub" ? "FUB" : "Sisu";
  if (!match) return `${name}: not matched`;
  const how = match.matched_by === "link" ? "linked" : match.matched_by === "agent_email" ? "by CRM email" : "by email";
  return `${name}: ${match.name} (${how})`;
}

function CrmCell({ member, open, onToggle }) {
  const crm = member.crm || {};
  return (
    <div className="roster-crm">
      <span className={crm.fub ? "matched" : "unmatched"}>{crmLabel(crm.fub, "fub")}</span>
      <span className={crm.sisu ? "matched" : "unmatched"}>{crmLabel(crm.sisu, "sisu")}</span>
      <button type="button" className="roster-crm-toggle" aria-expanded={open} onClick={onToggle}>
        {open ? "Close" : "Link"}
      </button>
    </div>
  );
}

function CrmPicker({ source, label, member, value, onChange }) {
  const query = useCrmAgents(source);
  const current = member.crm?.[source];
  const auto = current && current.matched_by !== "link"
    ? `Match by email (${current.name})`
    : "Match by email (none found)";
  if (query.isPending) return <Field label={label}><select disabled><option>Loading…</option></select></Field>;
  if (query.error) {
    return (
      <div className="console-field">
        <span>{label}</span>
        <p className="console-form-error">{COPY.loadFailed}</p>
      </div>
    );
  }
  const agents = query.data?.items || [];
  if (!agents.length) {
    return (
      <div className="console-field">
        <span>{label}</span>
        <p className="roster-crm-none">
          {source === "fub"
            ? "No Follow Up Boss users yet. Connect Follow Up Boss on the Acumyn dashboard; its users appear here after the first sync."
            : "No Sisu agents yet. Connect Sisu on the Acumyn dashboard; its agents appear here after the first sync."}
        </p>
      </div>
    );
  }
  return (
    <Field label={label}>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">{auto}</option>
        {agents.map((a) => (
          <option key={a.external_id} value={a.external_id}>
            {`${a.name}${a.email ? ` — ${a.email}` : ""}${a.active ? "" : " (inactive)"}`}
          </option>
        ))}
      </select>
    </Field>
  );
}

function CrmEditor({ member, busy, onSave, onClose }) {
  const [fub, setFub] = useState(member.crm?.links?.fub || "");
  const [sisu, setSisu] = useState(member.crm?.links?.sisu || "");
  const [error, setError] = useState("");
  async function save() {
    setError("");
    try {
      await onSave({ fub: fub || null, sisu: sisu || null });
      onClose();
    } catch (err) {
      setError(err.detail || err.message || COPY.loadFailed);
    }
  }
  return (
    <div className="roster-crm-editor">
      <h3>{`CRM identity: ${member.full_name}`}</h3>
      <p>
        {`Who ${member.full_name} is in each CRM. Leave a box on "Match by email" unless their CRM address differs from ${member.email}.`}
      </p>
      <div className="roster-crm-fields">
        <CrmPicker source="fub" label="Follow Up Boss user" member={member} value={fub} onChange={setFub} />
        <CrmPicker source="sisu" label="Sisu agent" member={member} value={sisu} onChange={setSisu} />
      </div>
      {error ? <p className="console-form-error">{error}</p> : null}
      <div className="roster-crm-actions">
        <Button type="button" tone="primary" busy={busy} onClick={save}>Save</Button>
        <Button type="button" onClick={onClose}>Cancel</Button>
      </div>
    </div>
  );
}

const INVITE_LABEL = { not_sent: "Not invited", sent: "Invite sent" };

function InviteChip({ member }) {
  const label = INVITE_LABEL[member.invite];
  if (!label || member.status === REMOVED_MEMBER_STATUS) return <StatusChip status={member.status} />;
  return <span className={`status-chip ${member.invite === "not_sent" ? "status-held" : "status-invited"}`}>{label}</span>;
}

function RosterTable({ members, roles, patchMutation, removeMutation }) {
  const [savingMember, setSavingMember] = useState("");
  const [linking, setLinking] = useState("");
  // Whose Who's Who profile is open: the same drawer the Who's Who screen uses.
  const [profileOf, setProfileOf] = useState("");
  const sendMutation = useSendMemberInvite();
  // The last invite sent from this table, with its link. An admin needs a way to hand it over
  // directly: the most common reason an invite "never arrived" is a spam folder, and the answer
  // to that should not be to send the same email again.
  const [sent, setSent] = useState(null);
  const [sendError, setSendError] = useState("");

  async function sendInvite(member) {
    setSavingMember(member.id);
    setSendError("");
    try {
      const result = await sendMutation.mutateAsync(member.id);
      setSent({ name: member.full_name, email: member.email, url: result?.invite_url || "" });
    } catch (err) {
      setSendError(err.detail || err.message);
    } finally {
      setSavingMember("");
    }
  }

  async function saveLinks(memberId, links) {
    setSavingMember(memberId);
    try {
      await patchMutation.mutateAsync({ memberId, body: { agent_links: links } });
    } finally {
      setSavingMember("");
    }
  }

  async function changeRole(memberId, roleId) {
    setSavingMember(memberId);
    try {
      await patchMutation.mutateAsync({ memberId, body: { role_id: roleId } });
    } finally {
      setSavingMember("");
    }
  }

  async function removeMember(memberId) {
    setSavingMember(memberId);
    try {
      await removeMutation.mutateAsync(memberId);
    } finally {
      setSavingMember("");
    }
  }

  const editing = members.find((m) => m.id === linking) || null;
  return (
    <>
      {sent ? (
        <div className="roster-sent">
          <p className="roster-form-message">{`Invite emailed to ${sent.name} (${sent.email}).`}</p>
          {sent.url ? (
            <>
              <p className="console-help" style={{ marginBottom: 6 }}>
                If it does not arrive, send them this link. It works once and expires in a week.
              </p>
              <input className="console-invite-link" readOnly value={sent.url}
                     onFocus={(event) => event.target.select()} />
            </>
          ) : null}
        </div>
      ) : null}
      {sendError ? <p className="console-form-error">{sendError}</p> : null}
      <div className="roster-table-wrap">
      <table className="roster-table">
        <thead>
          <tr>
            <th>{COPY.rosterName}</th>
            <th>{COPY.rosterEmail}</th>
            <th>{COPY.rosterRole}</th>
            <th>{COPY.rosterMarket}</th>
            <th>{COPY.rosterAuth}</th>
            <th>{COPY.rosterStatus}</th>
            <th>CRM</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {members.map((member) => (
            <tr key={member.id}>
              <td>{member.full_name}</td>
              <td>{member.email}</td>
              <td>
                <select
                  value={member.role_id || ""}
                  disabled={savingMember === member.id}
                  onChange={(event) => changeRole(member.id, event.target.value)}
                >
                  {roles.map((role) => (
                    <option key={role.id} value={role.id}>{role.name}</option>
                  ))}
                </select>
              </td>
              <td>{member.market || ""}</td>
              <td>{member.auth_source}</td>
              <td><InviteChip member={member} /></td>
              <td>
                <CrmCell member={member} open={linking === member.id}
                         onToggle={() => setLinking(linking === member.id ? "" : member.id)} />
              </td>
              <td>
                <div className="roster-row-actions">
                  {member.status !== REMOVED_MEMBER_STATUS
                    && ["not_sent", "sent", "no_account"].includes(member.invite) ? (
                    <Button type="button" tone={member.invite === "sent" ? undefined : "primary"}
                            busy={savingMember === member.id && sendMutation.isPending}
                            disabled={savingMember === member.id}
                            onClick={() => sendInvite(member)}>
                      {member.invite === "sent" ? "Resend invite" : "Send invite"}
                    </Button>
                  ) : null}
                  {member.status !== REMOVED_MEMBER_STATUS ? (
                    <Button type="button" onClick={() => setProfileOf(member.id)}>Edit profile</Button>
                  ) : null}
                  <Button
                    type="button"
                    disabled={member.status === REMOVED_MEMBER_STATUS || savingMember === member.id}
                    onClick={() => removeMember(member.id)}
                  >
                    {COPY.rosterRemove}
                  </Button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      {editing ? (
        <CrmEditor key={editing.id} member={editing} busy={savingMember === editing.id}
                   onSave={(links) => saveLinks(editing.id, links)}
                   onClose={() => setLinking("")} />
      ) : null}
      {profileOf && members.find((m) => m.id === profileOf) ? (
        <ProfileEditor key={profileOf} member={members.find((m) => m.id === profileOf)}
                       onClose={() => setProfileOf("")} />
      ) : null}
    </>
  );
}

export default function Roster({ overview }) {
  const [filter, setFilter] = useState(DEFAULT_ROSTER_FILTER);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 300);
  const params = useMemo(() => ({ filter, q: debouncedSearch }), [filter, debouncedSearch]);
  const membersQuery = useMembers(params, true);
  const inviteMutation = useInviteMember();
  const patchMutation = usePatchMember();
  const removeMutation = useRemoveMember();
  const syncMutation = useSyncMembers();
  const roles = membersQuery.data?.roles || [];
  const members = membersQuery.data?.items || [];
  const stats = membersQuery.data?.stats || {};
  const totalMembers = overview?.counts?.members ?? membersQuery.data?.total ?? 0;

  return (
    <div className="roster-grid">
      <section className="roster-stats" aria-label={COPY.rosterTitle}>
        <StatCard label={COPY.rosterActive} value={stats.active ?? 0} />
        <StatCard label={COPY.rosterPending} value={stats.pending ?? 0} />
        <StatCard label={COPY.rosterGuests} value={stats.guests ?? 0} />
        <StatCard label={COPY.rosterLeadership} value={stats.leadership ?? 0} />
        <StatCard label={COPY.rosterRemoved} value={stats.removed_this_month ?? 0} />
        <StatCard label={COPY.rosterLastSync} value={formatDate(stats.last_synced_at)} />
      </section>

      <Panel
        title={COPY.rosterTitle}
        action={(
          <Button type="button" busy={syncMutation.isPending} onClick={() => syncMutation.mutate()}>
            {COPY.rosterSync}
          </Button>
        )}
      >
        <div className="roster-controls">
          <div className="roster-tabs">
            {ROSTER_FILTERS.map((item) => (
              <button
                type="button"
                key={item.key}
                className={filter === item.key ? "active" : ""}
                onClick={() => setFilter(item.key)}
              >
                {item.label}
              </button>
            ))}
          </div>
          <input
            className="roster-search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={COPY.rosterSearch}
          />
        </div>

        {membersQuery.isPending ? <LoadingState /> : null}
        {membersQuery.error ? <ErrorState onRetry={() => membersQuery.refetch()} /> : null}
        {!membersQuery.isPending && !membersQuery.error && !members.length ? <EmptyState title={COPY.rosterEmpty} /> : null}
        {!membersQuery.isPending && !membersQuery.error && members.length ? (
          <>
            <p className="roster-count">{membersQuery.data.total} / {totalMembers}</p>
            <RosterTable
              members={members}
              roles={roles}
              patchMutation={patchMutation}
              removeMutation={removeMutation}
            />
          </>
        ) : null}
      </Panel>

      <InviteForm roles={roles} inviteMutation={inviteMutation} />
    </div>
  );
}
