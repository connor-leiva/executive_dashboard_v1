import { useEffect, useState } from "react";

import { COPY } from "../constants.js";
import { useOrderWtdLists, usePatchWtdList, useWtdLists } from "../queries.js";
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

export default function WinTheDay() {
  const wtdQuery = useWtdLists(true);
  const patchMutation = usePatchWtdList();
  const orderMutation = useOrderWtdLists();
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
    </div>
  );
}
