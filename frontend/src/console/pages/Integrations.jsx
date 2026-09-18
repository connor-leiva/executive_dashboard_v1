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
            <input value={form.base_url} onChange={(event) => update("base_url", event.target.value)}
                   placeholder={integration.default_base_url || "https://yourteam.followupboss.com/2/people/list/"} />
          </Field>
          {/* Follow Up Boss says which account a key opens, so its smart lists link without a
              typed base URL. Say so rather than leave an empty box looking like a missing step. */}
          {integration.default_base_url ? (
            <p className="integration-hint">
              {`Optional. Left blank, Win the Day lists open in ${integration.default_base_url.replace(/^https:\/\//, "").split("/")[0]}.`}
            </p>
          ) : null}
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
        {integration.dashboard_error ? (
          <p className="integration-error">{`Last sync on the dashboard failed: ${integration.dashboard_error}`}</p>
        ) : null}

        {/* NOT FOR A CONNECTION THE DASHBOARD OWNS. This free-form editor is where a live Follow Up
            Boss key was once typed, under "API Key", and stored in plaintext on a row nothing
            reads. The credential belongs on the dashboard, where it is encrypted and used. */}
        {integration.inherited ? null : (
          <section className="integration-config">
            <h3>{COPY.integrationsConfig}</h3>
            <ConfigRows rows={form.config_rows} onChange={updateConfig} onAdd={addConfig} onRemove={removeConfig} />
          </section>
        )}

        <div className="integration-actions">
          <Button type="submit" tone="primary" busy={saving}>{COPY.integrationsSave}</Button>
          <Button type="button" busy={connecting} disabled={!integration.connect_available} onClick={connect}>
            {COPY.integrationsConnect}
          </Button>
          <Button type="button" busy={testing} disabled={!integration.test_available} onClick={test}>
            {COPY.integrationsTest}
          </Button>
          {/* WHERE THE CONNECTION LIVES, not just a dead button. The server has sent `inherited`
              and `inherited_from` for exactly this since the two surfaces were joined up, and this
              page read neither -- so Sisu and Follow Up Boss showed a disabled Connect button
              above the words "Not yet available", which says the opposite of the truth: they are
              connected on the dashboard, and that is where to do it. */}
          {integration.inherited ? (
            <span>
              {`Connected on the ${integration.inherited_from}, not here — connect it there and `
                + "this workspace picks it up."}
            </span>
          ) : !integration.connect_available || !integration.test_available ? (
            <span>{COPY.integrationsUnavailable}</span>
          ) : null}
          {message ? <span>{message}</span> : null}
          {error ? <span className="brand-error">{error}</span> : null}
        </div>
      </form>
    </Panel>
  );
}

/* HOW YOUR TEAM SIGNS IN — first on the page, because it is the only thing here that decides
   whether anyone can get in at all.

   NOTHING TO SET UP. Every workspace signs in through Acumyn's own Google app, so this is two
   choices rather than a form: whether the button is offered, and optionally which email domains
   may use it. It used to ask for a client ID and secret from the workspace's own Google Cloud
   project — a wall no team buying a portal should have to climb before their agents can sign in. */
function GoogleSignInPanel() {
  const query = useGoogleSignin(true);
  const save = usePatchGoogleSignin();
  const item = query.data?.item;

  const [domains, setDomains] = useState("");
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!item) return;
    setDomains((item.allowed_domains || []).join(", "));
  }, [item]);

  if (query.isLoading) return <LoadingState title={COPY.loading} />;
  if (query.isError) return <ErrorState title="Couldn't load sign-in settings" onRetry={() => query.refetch()} />;

  async function submit(enabled) {
    setError(null);
    setSaved(false);
    try {
      await save.mutateAsync({
        allowed_domains: domains.split(",").map((d) => d.trim()).filter(Boolean),
        enabled,
      });
      setSaved(true);
    } catch (err) {
      setError(err?.detail || err?.message || "Couldn't save those settings.");
    }
  }

  const on = Boolean(item?.enabled);
  return (
    <Panel title="Google sign-in">
      <p className="console-help">
        Let your team sign in with the Google accounts they already have. There is nothing to set
        up in Google — it is on for every workspace unless you turn it off here.
      </p>

      {error ? <p className="console-form-error">{error}</p> : null}
      {saved && !error ? <p className="console-help">Saved.</p> : null}

      <Field label="Allowed email domains (comma separated — leave blank to allow any)">
        <input value={domains} onChange={(e) => setDomains(e.target.value)}
               placeholder="yourteam.com" />
      </Field>

      <div className="console-actions">
        <Button onClick={() => submit(on)} busy={save.isPending}>Save</Button>
        <Button tone={on ? "danger" : "primary"} busy={save.isPending}
                onClick={() => submit(!on)}>
          {on ? "Turn off Google sign-in" : "Turn on Google sign-in"}
        </Button>
      </div>
      <p className="console-help">
        {!item?.available
          ? "Google sign-in isn't available yet, so your team signs in with a password for now."
          : on
            ? "Your team sees a Continue with Google button on the sign-in page."
            : "Off — nobody is shown a Google button."}
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
