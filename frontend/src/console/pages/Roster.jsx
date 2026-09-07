import { useEffect, useMemo, useState } from "react";

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
  useInviteMember,
  useMembers,
  usePatchMember,
  useRemoveMember,
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
  // The invite link, kept after a successful invite. An admin needs a way to hand it over
  // directly: the most common reason an invite "never arrived" is a spam folder, and the answer
  // to that should not be to send the same email again.
  const [inviteUrl, setInviteUrl] = useState("");

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
    setInviteUrl("");
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
      // No link means this address already had an account, so nothing was emailed and there is
      // nothing to hand over -- they can already sign in. Saying so is more use than a bare
      // "Invited", which would leave an admin waiting for an email that is not coming.
      setInviteUrl(result?.invite_url || "");
      setMessage(result?.invite_url
        ? COPY.rosterInvited
        : "Added. They already had an account, so no invite was needed.");
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
        {message ? <p className="roster-form-message">{message}</p> : null}
        {inviteUrl ? (
          <>
            <p className="console-help" style={{ marginBottom: 6 }}>
              Their invite has been emailed. If it does not arrive, send them this link — it
              works once and expires in a week.
            </p>
            <input className="console-invite-link" readOnly value={inviteUrl}
                   onFocus={(event) => event.target.select()} />
          </>
        ) : null}
        <Button type="submit" tone="primary" busy={inviteMutation.isPending}>{COPY.rosterInvite}</Button>
      </form>
    </Panel>
  );
}

function RosterTable({ members, roles, patchMutation, removeMutation }) {
  const [savingMember, setSavingMember] = useState("");

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

  return (
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
              <td><StatusChip status={member.status} /></td>
              <td>
                <Button
                  type="button"
                  disabled={member.status === REMOVED_MEMBER_STATUS || savingMember === member.id}
                  onClick={() => removeMember(member.id)}
                >
                  {COPY.rosterRemove}
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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
