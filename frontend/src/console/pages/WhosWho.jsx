import { useEffect, useMemo, useState } from "react";

import ProfileEditor from "../ProfileEditor.jsx";
import { namedError } from "../api.js";
import { COPY } from "../constants.js";
import {
  useDirectory,
  useLeadershipOrder,
  useMembers,
  usePatchDirectory,
  usePatchProfile,
} from "../queries.js";
import { Button, EmptyState, ErrorState, Field, LoadingState, Panel } from "../ui.jsx";

/* WHO'S WHO, AS THE WORKSPACE SETS IT UP (WHOS-WHO-WIN-THE-DAY-SPEC.md, phase 4).
 *
 * The page's own settings -- the intro, the featured person and their stats, how many agents show
 * before "Show all" -- and where everybody sits: Leadership in its order, Agents, or hidden. Each
 * person's profile opens in the same drawer People & Roster uses. Immediate, like the roster. */

const SOURCE_LABEL = {
  manual: "Typed",
  team_size: "Team size (counted)",
  sisu_units_ytd: "Units, year to date (Sisu)",
  sisu_volume_ytd: "Volume, year to date (Sisu)",
};

// A refused field, named as this page labels it ("stats.1.value" -> "Number 2, value").
const FIELD_LABEL = {
  intro: "Intro", featured_label: "Eyebrow", preview_count: "Agents to show",
  featured_member_id: "Featured person", ids: "Leadership order", directory_placement: "Shown in",
  stats: "The team's numbers",
};
const STAT_PART = { label: "label", source: "figure", value: "value" };

function fieldName(key) {
  const [head, index, part] = String(key || "").split(".");
  if (index === undefined) return FIELD_LABEL[head] || null;
  if (head === "stats") return `Number ${Number(index) + 1}${part ? `, ${STAT_PART[part] || part}` : ""}`;
  return null;
}

function errText(err) {
  return namedError(err, fieldName) || COPY.loadFailed;
}

function PageSettings({ directory, onSaved }) {
  const save = usePatchDirectory();
  const [intro, setIntro] = useState(directory.settings.intro || "");
  const [preview, setPreview] = useState(directory.settings.preview_count ?? 9);
  const [error, setError] = useState("");
  useEffect(() => {
    setIntro(directory.settings.intro || "");
    setPreview(directory.settings.preview_count ?? 9);
  }, [directory.settings.intro, directory.settings.preview_count]);
  async function submit(event) {
    event.preventDefault();
    setError("");
    try {
      await save.mutateAsync({ intro, preview_count: Number(preview) || 0 });
      onSaved("The page is saved.");
    } catch (err) {
      setError(errText(err));
    }
  }
  return (
    <Panel title="The page">
      <form className="wtdc-form" onSubmit={submit}>
        <Field label="Intro under the title">
          <textarea value={intro} maxLength={300} rows={2}
                    placeholder="{N} agents, one team. Start with the people whose whole job is helping you close more."
                    onChange={(e) => setIntro(e.target.value)} />
        </Field>
        <p className="followup-hint">{`{N} becomes the team's size in words ("Eighty-six"); {n} in digits. Today that is ${directory.team_size}.`}</p>
        <Field label="Agents to show before Show all (0 shows everyone)">
          <input type="number" min={0} max={60} value={preview} onChange={(e) => setPreview(e.target.value)} />
        </Field>
        <div className="followup-actions">
          <Button type="submit" tone="primary" busy={save.isPending}>Save</Button>
          {error ? <span className="console-form-error">{error}</span> : null}
        </div>
      </form>
    </Panel>
  );
}

function Featured({ directory, onSaved }) {
  const save = usePatchDirectory();
  const settings = directory.settings;
  const [person, setPerson] = useState(settings.featured_member_id || "");
  const [label, setLabel] = useState(settings.featured_label || "");
  const [stats, setStats] = useState(settings.stats || []);
  const [error, setError] = useState("");
  useEffect(() => {
    setPerson(settings.featured_member_id || "");
    setLabel(settings.featured_label || "");
    setStats(settings.stats || []);
  }, [settings.featured_member_id, settings.featured_label, settings.stats]);
  // The saved person stays in the list even once hidden, so the select does not claim "Nobody"
  // while the setting still names them -- the portal draws no band for a hidden person.
  const listed = directory.people.filter((p) => p.shown_in !== "hidden" || p.id === settings.featured_member_id);
  const sources = Object.keys(SOURCE_LABEL).filter((s) => directory.sisu_connected || !s.startsWith("sisu_"));
  const setStat = (i, patch) => setStats((all) => all.map((s, j) => (j === i ? { ...s, ...patch } : s)));

  async function submit(event) {
    event.preventDefault();
    setError("");
    try {
      const body = {
        featured_label: label,
        stats: stats.map((s) => ({ label: s.label, source: s.source || "manual",
                                   value: (s.source || "manual") === "manual" ? s.value : null })),
      };
      // Only when it changed: re-sending a person who has since been hidden would be refused,
      // and stop the eyebrow or the numbers being saved.
      if ((person || null) !== (settings.featured_member_id || null)) body.featured_member_id = person || null;
      await save.mutateAsync(body);
      onSaved("The featured band is saved.");
    } catch (err) {
      setError(errText(err));
    }
  }

  return (
    <Panel title="Featured">
      <form className="wtdc-form" onSubmit={submit}>
        <p className="followup-intro">
          The dark band at the top: one person, their quote and photo, and up to three of the
          team&rsquo;s numbers. They appear here only, not again in the sections below.
        </p>
        <div className="wtdc-grid-2">
          <Field label="Featured person">
            <select value={person} onChange={(e) => setPerson(e.target.value)}>
              <option value="">Nobody (no band)</option>
              {listed.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.shown_in === "hidden" ? `${p.name} (hidden, so no band shows)` : p.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Eyebrow (blank: their title)">
            <input value={label} maxLength={60} placeholder="Team Leader" onChange={(e) => setLabel(e.target.value)} />
          </Field>
        </div>
        <h3 className="wtdc-subhead">The team&rsquo;s numbers (up to three)</h3>
        {!directory.sisu_connected ? (
          <p className="followup-hint">Sisu is not connected to this workspace, so its totals are not offered. Type a figure instead.</p>
        ) : null}
        <div className="wtdc-repeat">
          {stats.map((st, i) => (
            <div className="wtdc-item" key={i}>
              <div className="wtdc-grid-3">
                <Field label="Label">
                  <input value={st.label || ""} maxLength={40} placeholder="Units, year to date"
                         onChange={(e) => setStat(i, { label: e.target.value })} />
                </Field>
                <Field label="Figure">
                  <select value={st.source || "manual"} onChange={(e) => setStat(i, { source: e.target.value })}>
                    {sources.map((s) => <option key={s} value={s}>{SOURCE_LABEL[s]}</option>)}
                  </select>
                </Field>
                {(st.source || "manual") === "manual" ? (
                  <Field label="Value">
                    <input value={st.value || ""} maxLength={24} placeholder="612" onChange={(e) => setStat(i, { value: e.target.value })} />
                  </Field>
                ) : <div className="followup-hint wtdc-counted">Counted when the page loads.</div>}
              </div>
              <div><Button type="button" onClick={() => setStats((all) => all.filter((_, j) => j !== i))}>Remove</Button></div>
            </div>
          ))}
          {stats.length < 3 ? (
            <div><Button type="button" onClick={() => setStats((all) => [...all, { label: "", source: "manual", value: "" }])}>Add a number</Button></div>
          ) : null}
        </div>
        <div className="followup-actions">
          <Button type="submit" tone="primary" busy={save.isPending}>Save</Button>
          {error ? <span className="console-form-error">{error}</span> : null}
        </div>
      </form>
    </Panel>
  );
}

function Leadership({ directory, onEdit, onSaved }) {
  const order = useLeadershipOrder();
  const [error, setError] = useState("");
  const featured = directory.settings.featured_member_id;
  const leaders = directory.people
    .filter((p) => p.shown_in === "leadership" && p.id !== featured)
    .sort((a, b) => (a.order ?? 999) - (b.order ?? 999) || a.name.localeCompare(b.name));

  async function move(from, to) {
    if (to < 0 || to >= leaders.length) return;
    const ids = leaders.map((p) => p.id);
    const [moved] = ids.splice(from, 1);
    ids.splice(to, 0, moved);
    setError("");
    try {
      await order.mutateAsync({ ids });
      onSaved("Leadership order saved.");
    } catch (err) {
      setError(errText(err));
    }
  }

  return (
    <Panel title="Leadership">
      <p className="followup-intro">
        {`The dark cards under the band, in this order. People in a leadership role (${directory.leadership_roles.join(", ") || "none"}) land here automatically; move anyone in or out with Shown in.`}
      </p>
      {error ? <p className="console-form-error">{error}</p> : null}
      {!leaders.length ? <EmptyState title="Nobody is in Leadership." /> : null}
      <div className="wtdc-lists">
        {leaders.map((p, i) => (
          <div className="wtdc-list whoc-row" key={p.id}>
            <div className="wtdc-list-head">
              <strong>{i + 1}</strong>
              <span className="whoc-name">{p.name}<small>{[p.title, p.market].filter(Boolean).join(" · ")}</small></span>
              <div className="tile-actions">
                <button type="button" disabled={order.isPending || i === 0} onClick={() => move(i, i - 1)}>{COPY.moveUp}</button>
                <button type="button" disabled={order.isPending || i === leaders.length - 1} onClick={() => move(i, i + 1)}>{COPY.moveDown}</button>
              </div>
              <Button type="button" onClick={() => onEdit(p.id)}>Edit profile</Button>
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function Everyone({ directory, onEdit, onSaved }) {
  const patch = usePatchProfile();
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const shown = directory.people.filter((p) => !query
    || `${p.name} ${p.email} ${p.title || ""} ${p.market || ""}`.toLowerCase().includes(query.toLowerCase()));

  async function place(person, value) {
    setBusy(person.id);
    setError("");
    try {
      await patch.mutateAsync({ memberId: person.id, body: { directory_placement: value } });
      onSaved(`${person.name} is saved.`);
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy("");
    }
  }

  return (
    <Panel title="Everyone on the roster">
      <div className="wtdc-form">
      <p className="followup-intro">
        Everyone not removed is on Who&rsquo;s Who, whether or not they have signed in yet. Hide
        anyone who should not be, such as an owner who is not on the team.
      </p>
      <Field label="Find someone">
        <input value={query} placeholder="Name, email, title or market" onChange={(e) => setQuery(e.target.value)} />
      </Field>
      {error ? <p className="console-form-error">{error}</p> : null}
      <div className="wtdc-table-wrap">
        <table className="wtdc-table">
          <thead>
            <tr><th>Person</th><th>Shown in</th><th>Tag</th><th>Photo</th><th /></tr>
          </thead>
          <tbody>
            {shown.map((p) => (
              <tr key={p.id}>
                <th scope="row">
                  {p.name}
                  <small>{` · ${[p.title || p.role_name, p.status !== "Active" ? p.status : ""].filter(Boolean).join(" · ")}`}</small>
                </th>
                <td>
                  <select value={p.placement} disabled={busy === p.id} aria-label={`Where ${p.name} is shown`}
                          onChange={(e) => place(p, e.target.value)}>
                    <option value="auto">{`Automatic (${p.auto_shown_in === "leadership" ? "Leadership" : "Agents"})`}</option>
                    <option value="leadership">Leadership</option>
                    <option value="agents">Agents</option>
                    <option value="hidden">Hidden</option>
                  </select>
                </td>
                <td>{p.tag || ""}</td>
                <td>{p.has_photo ? "Yes" : "—"}</td>
                <td><Button type="button" onClick={() => onEdit(p.id)}>Edit profile</Button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      </div>
    </Panel>
  );
}

export default function WhosWho() {
  const directory = useDirectory();
  const members = useMembers({}, true);
  const [editing, setEditing] = useState("");
  const [message, setMessage] = useState("");
  const byId = useMemo(() => Object.fromEntries((members.data?.items || []).map((m) => [m.id, m])),
                       [members.data]);

  if (directory.isPending) return <LoadingState />;
  if (directory.error) return <ErrorState onRetry={() => directory.refetch()} />;
  const data = directory.data;
  const editingMember = editing ? byId[editing] : null;

  return (
    <div className="wtd-grid">
      {message ? <p className="roster-form-message">{message}</p> : null}
      <PageSettings directory={data} onSaved={setMessage} />
      <Featured directory={data} onSaved={setMessage} />
      <Leadership directory={data} onEdit={setEditing} onSaved={setMessage} />
      <Everyone directory={data} onEdit={setEditing} onSaved={setMessage} />
      {editingMember ? <ProfileEditor member={editingMember} onClose={() => setEditing("")} /> : null}
      {editing && !editingMember && members.isPending ? <LoadingState /> : null}
    </div>
  );
}
