import { useEffect, useState } from "react";

import { COPY } from "../constants.js";
import {
  useCreateWtdList,
  useFollowUpSettings,
  useFubSmartLists,
  useOrderWtdLists,
  usePatchFollowUpSettings,
  usePatchWtdList,
  useWtdLists,
} from "../queries.js";
import { Button, EmptyState, ErrorState, Field, LoadingState, Panel } from "../ui.jsx";

function targetValue(value) {
  return value === null || value === undefined ? "" : String(value);
}

function listUrl(integration, externalId) {
  if (!integration || integration.status !== "Connected" || !integration.base_url || !externalId) return "";
  const base = integration.base_url.endsWith("/") ? integration.base_url : `${integration.base_url}/`;
  return `${base}${encodeURIComponent(externalId)}`;
}

function providerLabel(provider, integrations) {
  return integrations[provider]?.display_name || provider.replace(/_/g, " ");
}

function WtdStat({ label, value }) {
  return (
    <div className="wtd-stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function WtdRow({ list, integrations, index, total, busy, onSave, onToggle, onMove }) {
  const [draft, setDraft] = useState({
    name: list.name || "",
    provider: list.provider || "",
    external_list_id: list.external_list_id || "",
    script_name: list.script_name || "",
    daily_target: targetValue(list.daily_target),
    active: Boolean(list.active),
  });
  const integration = integrations[list.provider] || null;
  const href = listUrl(integration, list.external_list_id);

  useEffect(() => {
    setDraft({
      name: list.name || "",
      provider: list.provider || "",
      external_list_id: list.external_list_id || "",
      script_name: list.script_name || "",
      daily_target: targetValue(list.daily_target),
      active: Boolean(list.active),
    });
  }, [list]);

  function update(field, value) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  function submit(event) {
    event.preventDefault();
    const target = draft.daily_target === "" ? null : Number(draft.daily_target);
    onSave(list.id, {
      name: draft.name,
      provider: draft.provider,
      external_list_id: draft.external_list_id || null,
      script_name: draft.script_name || null,
      daily_target: target,
      active: draft.active,
    });
  }

  async function toggleActive(event) {
    const active = event.target.checked;
    setDraft((current) => ({ ...current, active }));
    await onToggle(list.id, active);
  }

  return (
    <form className={`wtd-row ${draft.active ? "" : "inactive"}`} onSubmit={submit}>
      <div className="wtd-position">
        <strong>{list.position}</strong>
        <div className="tile-actions" aria-label={`${list.name} order`}>
          <button type="button" title={COPY.moveUp} disabled={busy || index === 0} onClick={() => onMove(index, index - 1)}>
            {COPY.moveUp}
          </button>
          <button type="button" title={COPY.moveDown} disabled={busy || index === total - 1} onClick={() => onMove(index, index + 1)}>
            {COPY.moveDown}
          </button>
        </div>
      </div>
      <Field label={COPY.wtdName}>
        <input value={draft.name} onChange={(event) => update("name", event.target.value)} required />
      </Field>
      <Field label={COPY.wtdExternalId}>
        <span className="wtd-external">
          <input value={draft.external_list_id} onChange={(event) => update("external_list_id", event.target.value)} />
          {href ? (
            <a href={href} target="_blank" rel="noreferrer">{COPY.wtdLink}</a>
          ) : (
            <small>{COPY.wtdDisconnected}</small>
          )}
        </span>
      </Field>
      <Field label={COPY.wtdScript}>
        <input value={draft.script_name} onChange={(event) => update("script_name", event.target.value)} />
      </Field>
      <Field label={COPY.wtdTarget}>
        <input
          value={draft.daily_target}
          onChange={(event) => update("daily_target", event.target.value)}
          inputMode="numeric"
          min="1"
          placeholder="-"
          type="number"
        />
      </Field>
      <div className="wtd-provider">
        <span>{COPY.wtdProvider}</span>
        <strong>{providerLabel(list.provider, integrations)}</strong>
      </div>
      <label className="tile-active">
        <input checked={draft.active} disabled={busy} type="checkbox" onChange={toggleActive} />
        <span>{COPY.wtdActive}</span>
      </label>
      <Button type="submit" tone="primary" busy={busy}>{COPY.wtdSave}</Button>
    </form>
  );
}

/* WHAT NEEDS YOU TODAY LISTS, set by the workspace rather than compiled in. The rules are Follow Up
 * Boss's own idea of a follow-up (services/follow_ups); these are the three numbers and the one
 * switch a team would reasonably disagree about. Going cold stays off until somebody picks the
 * stages it applies to, because they are this account's own names and a default would be a
 * guess. */
function numberOr(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function FollowUpSettings() {
  const query = useFollowUpSettings();
  const save = usePatchFollowUpSettings();
  const [form, setForm] = useState(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (query.data) setForm(query.data.settings);
  }, [query.data]);

  if (query.isPending) return <Panel title="Needs You Today"><LoadingState /></Panel>;
  if (query.error) return <Panel title="Needs You Today"><ErrorState onRetry={() => query.refetch()} /></Panel>;
  if (!form) return null;
  const data = query.data;
  const bounds = data.bounds || {};
  const stages = data.stages || [];
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));
  const toggleStage = (stage) => set("cold_stages", form.cold_stages.includes(stage)
    ? form.cold_stages.filter((s) => s !== stage)
    : [...form.cold_stages, stage]);

  async function submit(event) {
    event.preventDefault();
    setMessage("");
    setError("");
    try {
      await save.mutateAsync({
        new_lead_days: numberOr(form.new_lead_days, 7),
        overdue_max_days: numberOr(form.overdue_max_days, 30),
        cold_enabled: Boolean(form.cold_enabled),
        cold_days: numberOr(form.cold_days, 14),
        cold_stages: form.cold_stages,
      });
      setMessage("Saved. The portal uses these on the next page load.");
    } catch (err) {
      setError(err.detail || err.message || COPY.loadFailed);
    }
  }

  const conn = data.connection || {};
  return (
    <Panel title="Needs You Today">
      <form className="followup-settings" onSubmit={submit}>
        <p className="followup-intro">
          What each agent sees at the top of the portal, from Follow Up Boss: new leads nobody has
          contacted, and tasks due today or overdue. Leaders see the whole team.
          {conn.state === "not_connected" ? " Follow Up Boss is not connected yet — connect it on the Acumyn dashboard under Settings, Integrations." : ""}
        </p>
        <div className="followup-fields">
          <Field label="New leads from the last (days)">
            <input type="number" min={bounds.new_lead_days?.min} max={bounds.new_lead_days?.max}
                   value={form.new_lead_days} onChange={(e) => set("new_lead_days", e.target.value)} />
          </Field>
          <Field label="Overdue tasks from the last (days; 0 lists every one)">
            <input type="number" min={bounds.overdue_max_days?.min} max={bounds.overdue_max_days?.max}
                   value={form.overdue_max_days} onChange={(e) => set("overdue_max_days", e.target.value)} />
          </Field>
        </div>
        {data.older_overdue ? (
          <p className="followup-hint">
            {`${data.older_overdue.toLocaleString()} older overdue tasks across the team are outside this window and not listed.`}
          </p>
        ) : null}
        <label className="followup-switch">
          <input type="checkbox" checked={Boolean(form.cold_enabled)}
                 onChange={(e) => set("cold_enabled", e.target.checked)} />
          <span>Also list leads going cold</span>
        </label>
        {form.cold_enabled ? (
          <div className="followup-cold">
            <Field label="Quiet for at least (days)">
              <input type="number" min={bounds.cold_days?.min} max={bounds.cold_days?.max}
                     value={form.cold_days} onChange={(e) => set("cold_days", e.target.value)} />
            </Field>
            <fieldset className="followup-stages">
              <legend>In these stages</legend>
              {stages.length ? stages.map((s) => (
                <label key={s.stage}>
                  <input type="checkbox" checked={form.cold_stages.includes(s.stage)}
                         onChange={() => toggleStage(s.stage)} />
                  <span>{s.stage}</span>
                  <em>{s.people.toLocaleString()}</em>
                </label>
              )) : <p className="followup-hint">Stages appear here after Follow Up Boss has synced.</p>}
            </fieldset>
            {!form.cold_stages.length ? <p className="followup-hint">Pick at least one stage; until then nothing is listed as going cold.</p> : null}
          </div>
        ) : null}
        <div className="followup-actions">
          <Button type="submit" tone="primary" busy={save.isPending}>Save</Button>
          {message ? <span className="roster-form-message">{message}</span> : null}
          {error ? <span className="console-form-error">{error}</span> : null}
        </div>
      </form>
    </Panel>
  );
}

/* A NEW LIST. The console could edit and reorder Win the Day lists but never create one, so a
   workspace that had none -- every workspace but the first -- had no way to get any. The list id
   is picked from the account's own smart lists where Follow Up Boss can be asked for them. */
function AddWtdList({ onCreate, busy }) {
  const smart = useFubSmartLists();
  const [form, setForm] = useState({ name: "", external_list_id: "", script_name: "", daily_target: "" });
  const [error, setError] = useState("");
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));
  const lists = smart.data?.items || [];

  function pick(id) {
    const chosen = lists.find((l) => String(l.id) === id);
    setForm((f) => ({ ...f, external_list_id: id, name: f.name || (chosen ? chosen.name : "") }));
  }

  async function submit(event) {
    event.preventDefault();
    setError("");
    try {
      await onCreate({
        name: form.name.trim(),
        external_list_id: form.external_list_id.trim() || null,
        script_name: form.script_name.trim() || null,
        daily_target: form.daily_target ? Number(form.daily_target) : null,
      });
      setForm({ name: "", external_list_id: "", script_name: "", daily_target: "" });
    } catch (err) {
      setError(err.detail || err.message || COPY.loadFailed);
    }
  }

  return (
    <Panel title="Add a call list">
      <form className="wtd-add" onSubmit={submit}>
        {lists.length ? (
          <Field label="Follow Up Boss smart list">
            <select value={form.external_list_id} onChange={(e) => pick(e.target.value)}>
              <option value="">Pick a smart list</option>
              {lists.map((l) => <option key={l.id} value={String(l.id)}>{l.name}</option>)}
            </select>
          </Field>
        ) : (
          <Field label="Smart list id">
            <input value={form.external_list_id} onChange={(e) => set("external_list_id", e.target.value)}
                   placeholder="The number at the end of the smart list's address" />
          </Field>
        )}
        <Field label="Name">
          <input value={form.name} required onChange={(e) => set("name", e.target.value)} />
        </Field>
        <Field label="Script (optional)">
          <input value={form.script_name} onChange={(e) => set("script_name", e.target.value)} />
        </Field>
        <Field label="Daily target (optional)">
          <input type="number" min="1" value={form.daily_target} onChange={(e) => set("daily_target", e.target.value)} />
        </Field>
        <div className="followup-actions">
          <Button type="submit" tone="primary" busy={busy} disabled={!form.name.trim()}>Add list</Button>
          {error ? <span className="console-form-error">{error}</span> : null}
        </div>
        <p className="followup-hint">New lists go live when you publish.</p>
      </form>
    </Panel>
  );
}

export default function WinTheDay() {
  const wtdQuery = useWtdLists(true);
  const patchMutation = usePatchWtdList();
  const orderMutation = useOrderWtdLists();
  const createMutation = useCreateWtdList();
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const lists = wtdQuery.data?.items || [];
  const stats = wtdQuery.data?.stats || {};
  const integrations = wtdQuery.data?.integrations || {};
  const busy = patchMutation.isPending || orderMutation.isPending;

  async function saveList(listId, body) {
    setMessage("");
    setError("");
    try {
      await patchMutation.mutateAsync({ listId, body });
      setMessage(COPY.wtdSaved);
    } catch (err) {
      setError(err.detail || err.message);
    }
  }

  async function toggleList(listId, active) {
    setMessage("");
    setError("");
    try {
      await patchMutation.mutateAsync({ listId, body: { active } });
      setMessage(COPY.wtdSaved);
    } catch (err) {
      setError(err.detail || err.message);
    }
  }

  async function createList(body) {
    setMessage("");
    setError("");
    await createMutation.mutateAsync(body);
    setMessage("List added. Publish to put it in front of the team.");
  }

  async function moveList(from, to) {
    if (to < 0 || to >= lists.length) return;
    const ids = lists.map((list) => list.id);
    const [moved] = ids.splice(from, 1);
    ids.splice(to, 0, moved);
    setMessage("");
    setError("");
    try {
      await orderMutation.mutateAsync({ ids });
      setMessage(COPY.wtdSaved);
    } catch (err) {
      setError(err.detail || err.message);
    }
  }

  if (wtdQuery.isPending) return <LoadingState />;
  if (wtdQuery.error) return <ErrorState title={COPY.wtdError} onRetry={() => wtdQuery.refetch()} />;

  return (
    <div className="wtd-grid">
      <section className="wtd-stats" aria-label={COPY.wtdTitle}>
        <WtdStat label={COPY.wtdListsInRun} value={stats.lists_in_run ?? 0} />
        <WtdStat label={COPY.wtdPairedScripts} value={stats.paired_scripts ?? 0} />
        <WtdStat label={COPY.wtdDailyTarget} value={stats.daily_touch_target ?? 0} />
      </section>
      <Panel title={COPY.wtdTitle}>
        {message ? <p className="roster-form-message">{message}</p> : null}
        {error ? <p className="console-form-error">{error}</p> : null}
        {!lists.length ? <EmptyState title={COPY.wtdEmpty} /> : null}
        {lists.length ? (
          <div className="wtd-list">
            {lists.map((list, index) => (
              <WtdRow
                key={list.id}
                list={list}
                integrations={integrations}
                index={index}
                total={lists.length}
                busy={busy}
                onSave={saveList}
                onToggle={toggleList}
                onMove={moveList}
              />
            ))}
          </div>
        ) : null}
      </Panel>
      <AddWtdList onCreate={createList} busy={createMutation.isPending} />
      <FollowUpSettings />
    </div>
  );
}
