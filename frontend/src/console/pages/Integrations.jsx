import { useEffect, useMemo, useState } from "react";

import { COPY } from "../constants.js";
import {
  useConnectIntegration,
  useGoogleSignin,
  useIntegrations,
  usePatchGoogleSignin,
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

/* HOW YOUR TEAM SIGNS IN — first on the page, because it is the only thing here that decides
   whether anyone can get in at all, and because it is the one a new workspace has to fill in.

   The client secret is write-only in both directions: the server reports whether one is stored
   and never returns it, and an empty box here means "leave it alone" rather than "clear it". So
   an admin can edit the domain list a year later without digging out a secret they no longer
   have. */
function GoogleSignInPanel() {
  const query = useGoogleSignin(true);
  const save = usePatchGoogleSignin();
  const item = query.data?.item;

  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [domains, setDomains] = useState("");
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!item) return;
    setClientId(item.client_id || "");
    setDomains((item.allowed_domains || []).join(", "));
  }, [item]);

  if (query.isLoading) return <LoadingState title={COPY.loading} />;
  if (query.isError) return <ErrorState title="Couldn't load sign-in settings" onRetry={() => query.refetch()} />;

  async function submit(enabled) {
    setError(null);
    setSaved(false);
    try {
      await save.mutateAsync({
        client_id: clientId.trim(),
        // Only sent when the box has something in it — see the note above.
        ...(secret.trim() ? { client_secret: secret.trim() } : {}),
        allowed_domains: domains.split(",").map((d) => d.trim()).filter(Boolean),
        enabled,
      });
      setSecret("");
      setSaved(true);
    } catch (err) {
      setError(err?.detail || err?.message || "Couldn't save those settings.");
    }
  }

  const on = Boolean(item?.enabled);
  return (
    <Panel title="Google sign-in">
      <p className="console-help">
        Let your team sign in with the Google accounts they already have. Create an OAuth client
        in your own Google Cloud project, then paste it here — the consent screen your staff see
        will carry your name, not ours.
      </p>
      <p className="console-help">
        Add this exact address to <strong>Authorised redirect URIs</strong> in Google Cloud:
        <br />
        <code>{item?.redirect_uri}</code>
      </p>

      {error ? <p className="console-form-error">{error}</p> : null}
      {saved && !error ? <p className="console-help">Saved.</p> : null}

      <Field label="Client ID">
        <input value={clientId} onChange={(e) => setClientId(e.target.value)}
               placeholder="000000000000-xxxxxxxx.apps.googleusercontent.com" />
      </Field>
      <Field label={item?.secret_set ? "Client secret (stored — leave blank to keep)" : "Client secret"}>
        <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)}
               autoComplete="new-password"
               placeholder={item?.secret_set ? "••••••••" : ""} />
      </Field>
      <Field label="Allowed email domains (comma separated — leave blank to allow any)">
        <input value={domains} onChange={(e) => setDomains(e.target.value)}
               placeholder="utahliferealestate.com" />
      </Field>

      <div className="console-actions">
        <Button onClick={() => submit(on)} busy={save.isPending}>Save</Button>
        <Button tone={on ? "danger" : "primary"} busy={save.isPending}
                onClick={() => submit(!on)}>
          {on ? "Turn off Google sign-in" : "Turn on Google sign-in"}
        </Button>
      </div>
      <p className="console-help">
        {on
          ? "Your team sees a Continue with Google button on the sign-in page."
          : "Off — nobody is shown a Google button. Turning it on needs a client ID and secret."}
      </p>
      <p className="console-help">
        Signing in with Google matches an address to someone you have already invited. It never
        creates an account on its own, so a Google address nobody invited still cannot get in.
      </p>
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
    <>
      <GoogleSignInPanel />
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
    </>
  );
}
