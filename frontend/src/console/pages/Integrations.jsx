import { useEffect, useMemo, useState } from "react";

import { COPY } from "../constants.js";
import {
  useConnectIntegration,
  useIntegrations,
  usePatchIntegration,
  useTestIntegration,
} from "../queries.js";
import { Button, EmptyState, ErrorState, Field, LoadingState, Panel } from "../ui.jsx";

function formatDate(value) {
  if (!value) return COPY.integrationsNeverSynced;
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function statusClass(status) {
  return String(status || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

function configRows(config) {
  return Object.entries(config || {}).map(([key, value]) => ({
    id: `${key}-${String(value)}`,
    key,
    value: value === null || value === undefined ? "" : String(value),
  }));
}

function integrationForm(integration) {
  return {
    display_name: integration?.display_name || "",
    role_label: integration?.role_label || "",
    description: integration?.description || "",
    base_url: integration?.base_url || "",
    config_rows: configRows(integration?.config),
  };
}

function integrationPayload(form) {
  const config = {};
  form.config_rows.forEach((row) => {
    const key = row.key.trim();
    if (key) config[key] = row.value;
  });
  return {
    display_name: form.display_name.trim(),
    role_label: form.role_label.trim(),
    description: form.description.trim() || null,
    base_url: form.base_url.trim() || null,
    config,
  };
}

function statusLabel(status) {
  if (status === "Connected") return COPY.integrationsConnected;
  if (status === "Action Needed") return COPY.integrationsActionNeeded;
  return COPY.integrationsNotConnected;
}

function IntegrationList({ items, selectedId, onSelect }) {
  return (
    <Panel title={COPY.integrationsTitle}>
      {!items.length ? <EmptyState title={COPY.integrationsEmpty} /> : null}
      <div className="integration-list">
        {items.map((integration) => (
          <button
            type="button"
            key={integration.id}
            className={`integration-row ${integration.id === selectedId ? "selected" : ""}`}
            onClick={() => onSelect(integration.id)}
          >
            <span>{integration.role_label}</span>
            <strong>{integration.display_name}</strong>
            <small>{formatDate(integration.last_sync_at)}</small>
            <em className={`integration-status ${statusClass(integration.status)}`}>
              {statusLabel(integration.status)}
            </em>
          </button>
        ))}
      </div>
    </Panel>
  );
}

function ConfigRows({ rows, onChange, onAdd, onRemove }) {
  return (
    <div className="integration-config-list">
      {rows.map((row, index) => (
        <div className="integration-config-row" key={row.id || index}>
          <Field label={COPY.integrationsConfigKey}>
            <input value={row.key} onChange={(event) => onChange(index, "key", event.target.value)} />
          </Field>
          <Field label={COPY.integrationsConfigValue}>
            <input value={row.value} onChange={(event) => onChange(index, "value", event.target.value)} />
          </Field>
          <button type="button" onClick={() => onRemove(index)} aria-label="Remove config row">x</button>
        </div>
      ))}
      <Button type="button" onClick={onAdd}>{COPY.integrationsAddConfig}</Button>
    </div>
  );
}

function IntegrationDetail({ integration, onSave, onConnect, onTest, saving, connecting, testing }) {
  const [form, setForm] = useState(() => integrationForm(integration));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setForm(integrationForm(integration));
    setMessage("");
    setError("");
  }, [integration]);

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function updateConfig(index, field, value) {
    setForm((current) => {
      const rows = current.config_rows.map((row, rowIndex) => (
        rowIndex === index ? { ...row, [field]: value } : row
      ));
      return { ...current, config_rows: rows };
    });
  }

  function addConfig() {
    setForm((current) => ({
      ...current,
      config_rows: [...current.config_rows, { id: `new-${Date.now()}`, key: "", value: "" }],
    }));
  }

  function removeConfig(index) {
    setForm((current) => ({
      ...current,
      config_rows: current.config_rows.filter((_, rowIndex) => rowIndex !== index),
    }));
  }

  async function submit(event) {
    event.preventDefault();
    setMessage("");
    setError("");
    try {
      await onSave(integration.id, integrationPayload(form));
      setMessage(COPY.integrationsSaved);
    } catch (err) {
      setError(err.message || COPY.loadFailed);
    }
  }

  async function connect() {
    if (!integration.connect_available) return;
    await onConnect(integration.id, { config: integrationPayload(form).config });
  }

  async function test() {
    if (!integration.test_available) return;
    await onTest(integration.id);
  }

  return (
    <Panel title={COPY.integrationsSettings}>
      <form className="integration-detail-form" onSubmit={submit}>
        <div className="integration-fields">
          <Field label={COPY.integrationsName}>
            <input value={form.display_name} onChange={(event) => update("display_name", event.target.value)} required />
          </Field>
          <Field label={COPY.integrationsRole}>
            <input value={form.role_label} onChange={(event) => update("role_label", event.target.value)} required />
          </Field>
          <Field label={COPY.integrationsBaseUrl}>
            <input value={form.base_url} onChange={(event) => update("base_url", event.target.value)} />
          </Field>
          <Field label={COPY.integrationsDescription}>
            <textarea value={form.description} onChange={(event) => update("description", event.target.value)} />
          </Field>
        </div>

        <div className="integration-sync">
          <div>
            <span>{COPY.integrationsSync}</span>
            <strong className={`integration-status ${statusClass(integration.status)}`}>
              {statusLabel(integration.status)}
            </strong>
          </div>
          <div>
            <span>{COPY.rosterLastSync}</span>
            <strong>{formatDate(integration.last_sync_at)}</strong>
          </div>
          <div>
            <span>{COPY.integrationsConfig}</span>
            <strong>{Object.keys(integration.config || {}).length}</strong>
          </div>
        </div>

        {integration.last_error ? <p className="integration-error">{integration.last_error}</p> : null}

        <section className="integration-config">
          <h3>{COPY.integrationsConfig}</h3>
          <ConfigRows rows={form.config_rows} onChange={updateConfig} onAdd={addConfig} onRemove={removeConfig} />
        </section>

        <div className="integration-actions">
          <Button type="submit" tone="primary" busy={saving}>{COPY.integrationsSave}</Button>
          <Button type="button" busy={connecting} disabled={!integration.connect_available} onClick={connect}>
            {COPY.integrationsConnect}
          </Button>
          <Button type="button" busy={testing} disabled={!integration.test_available} onClick={test}>
            {COPY.integrationsTest}
          </Button>
          {!integration.connect_available || !integration.test_available ? <span>{COPY.integrationsUnavailable}</span> : null}
          {message ? <span>{message}</span> : null}
          {error ? <span className="brand-error">{error}</span> : null}
        </div>
      </form>
    </Panel>
  );
}

export default function Integrations() {
  const integrations = useIntegrations(true);
  const patchIntegration = usePatchIntegration();
  const connectIntegration = useConnectIntegration();
  const testIntegration = useTestIntegration();
  const [selectedId, setSelectedId] = useState("");

  const items = integrations.data?.items || [];
  useEffect(() => {
    if (!selectedId && items.length) setSelectedId(items[0].id);
  }, [items, selectedId]);

  const selected = useMemo(
    () => items.find((integration) => integration.id === selectedId) || items[0],
    [items, selectedId],
  );

  if (integrations.isLoading) return <LoadingState title={COPY.loading} />;
  if (integrations.isError) {
    return <ErrorState title={COPY.integrationsError} onRetry={() => integrations.refetch()} />;
  }

  return (
    <div className="integrations-grid">
      <IntegrationList items={items} selectedId={selected?.id} onSelect={setSelectedId} />
      {selected ? (
        <IntegrationDetail
          integration={selected}
          saving={patchIntegration.isPending}
          connecting={connectIntegration.isPending}
          testing={testIntegration.isPending}
          onSave={(integrationId, body) => patchIntegration.mutateAsync({ integrationId, body })}
          onConnect={(integrationId, body) => connectIntegration.mutateAsync({ integrationId, body })}
          onTest={(integrationId) => testIntegration.mutateAsync(integrationId)}
        />
      ) : null}
    </div>
  );
}
