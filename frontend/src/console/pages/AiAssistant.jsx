import { useEffect, useState } from "react";

import { AI_BEHAVIOUR_FIELDS, COPY, GAP_STATUS_OPTIONS } from "../constants.js";
import {
  useAi,
  useContentGaps,
  useMembers,
  usePatchAiSettings,
  usePatchAiSource,
  usePatchContentGap,
  useRoles,
} from "../queries.js";
import { Button, EmptyState, ErrorState, Field, LoadingState, Panel } from "../ui.jsx";

function settingsForm(settings) {
  return {
    always_cite: settings?.always_cite ?? true,
    refuse_without_source: settings?.refuse_without_source ?? true,
    offer_escalation: settings?.offer_escalation ?? true,
    learn_from_corrections: settings?.learn_from_corrections ?? false,
    escalation_channel: settings?.escalation_channel || "",
  };
}

function sourceForm(source) {
  return {
    enabled: source?.enabled ?? true,
    min_role_id: source?.min_role_id || "",
  };
}

function gapForm(gap) {
  return {
    status: gap?.status || "Open",
    assigned_member_id: gap?.assigned_member_id || "",
    resolution_note: gap?.resolution_note || "",
  };
}

function formatDate(value) {
  if (!value) return COPY.aiNotIndexed;
  return new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function Toggle({ label, checked, onChange }) {
  return (
    <label className="ai-toggle">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

function BehaviourPanel({ settings, busy, onSave }) {
  const [form, setForm] = useState(() => settingsForm(settings));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setForm(settingsForm(settings));
  }, [settings]);

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function submit(event) {
    event.preventDefault();
    setMessage("");
    setError("");
    try {
      await onSave({
        ...form,
        escalation_channel: form.escalation_channel.trim() || null,
      });
      setMessage(COPY.aiSaved);
    } catch (err) {
      setError(err.message || COPY.loadFailed);
    }
  }

  return (
    <Panel title={COPY.aiBehaviour}>
      <form className="ai-behaviour-form" onSubmit={submit}>
        <div className="ai-toggle-grid">
          {AI_BEHAVIOUR_FIELDS.map((field) => (
            <Toggle
              key={field.key}
              label={field.label}
              checked={Boolean(form[field.key])}
              onChange={(value) => update(field.key, value)}
            />
          ))}
        </div>
        <Field label={COPY.aiEscalationChannel}>
          <input value={form.escalation_channel} onChange={(event) => update("escalation_channel", event.target.value)} />
        </Field>
        <div className="ai-actions">
          <Button type="submit" tone="primary" busy={busy}>{COPY.aiSaveBehaviour}</Button>
          {message ? <span>{message}</span> : null}
          {error ? <span className="brand-error">{error}</span> : null}
        </div>
      </form>
    </Panel>
  );
}

function SourceRow({ source, roles, busy, onSave }) {
  const [form, setForm] = useState(() => sourceForm(source));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setForm(sourceForm(source));
    setMessage("");
    setError("");
  }, [source]);

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function submit(event) {
    event.preventDefault();
    setMessage("");
    setError("");
    try {
      await onSave(source.id, {
        enabled: Boolean(form.enabled),
        min_role_id: form.min_role_id || null,
      });
      setMessage(COPY.aiSourceSaved);
    } catch (err) {
      setError(err.message || COPY.loadFailed);
    }
  }

  return (
    <form className={`ai-source-row ${form.enabled ? "" : "disabled"}`} onSubmit={submit}>
      <div className="ai-source-name">
        <span>{source.source_kind}</span>
        <strong>{source.name}</strong>
        <small>{source.description}</small>
      </div>
      <div className="ai-source-index">
        <span>{COPY.aiIndexed}</span>
        <strong>{source.indexed_item_count}</strong>
        <small>{formatDate(source.last_crawled_at)}</small>
      </div>
      <Field label={COPY.aiMinimumRole}>
        <select value={form.min_role_id} onChange={(event) => update("min_role_id", event.target.value)}>
          <option value="">{COPY.aiEveryUser}</option>
          {roles.map((role) => (
            <option key={role.id} value={role.id}>{role.name}</option>
          ))}
        </select>
      </Field>
      <Toggle label={COPY.aiEnabled} checked={Boolean(form.enabled)} onChange={(value) => update("enabled", value)} />
      <div className="ai-actions">
        <Button type="submit" busy={busy}>{COPY.aiSaveSource}</Button>
        {message ? <span>{message}</span> : null}
        {error ? <span className="brand-error">{error}</span> : null}
      </div>
    </form>
  );
}

function SourcesPanel({ sources, roles, busy, onSave }) {
  return (
    <Panel title={COPY.aiSources}>
      <div className="ai-source-list">
        {sources.map((source) => (
          <SourceRow key={source.id} source={source} roles={roles} busy={busy} onSave={onSave} />
        ))}
      </div>
    </Panel>
  );
}

function GapRow({ gap, members, busy, onSave }) {
  const [form, setForm] = useState(() => gapForm(gap));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setForm(gapForm(gap));
    setMessage("");
    setError("");
  }, [gap]);

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function submit(event) {
    event.preventDefault();
    setMessage("");
    setError("");
    try {
      await onSave(gap.id, {
        status: form.status,
        assigned_member_id: form.assigned_member_id || null,
        resolution_note: form.resolution_note.trim() || null,
      });
      setMessage(COPY.aiGapSaved);
    } catch (err) {
      setError(err.message || COPY.loadFailed);
    }
  }

  return (
    <form className="ai-gap-row" onSubmit={submit}>
      <div className="ai-gap-question">
        <span>{COPY.aiAsks}: {gap.ask_count}</span>
        <strong>{gap.question}</strong>
      </div>
      <Field label={COPY.aiGapStatus}>
        <select value={form.status} onChange={(event) => update("status", event.target.value)}>
          {GAP_STATUS_OPTIONS.map((option) => (
            <option key={option.key} value={option.key}>{option.label}</option>
          ))}
        </select>
      </Field>
      <Field label={COPY.aiAssignee}>
        <select value={form.assigned_member_id} onChange={(event) => update("assigned_member_id", event.target.value)}>
          <option value="">{COPY.aiNoAssignee}</option>
          {members.map((member) => (
            <option key={member.id} value={member.id}>{member.full_name}</option>
          ))}
        </select>
      </Field>
      <Field label={COPY.aiResolution}>
        <input value={form.resolution_note} onChange={(event) => update("resolution_note", event.target.value)} />
      </Field>
      <div className="ai-actions">
        <Button type="submit" busy={busy}>{COPY.aiSaveGap}</Button>
        {message ? <span>{message}</span> : null}
        {error ? <span className="brand-error">{error}</span> : null}
      </div>
    </form>
  );
}

function ContentGapsPanel({ gaps, members, busy, onSave }) {
  return (
    <Panel title={COPY.aiContentGaps}>
      {!gaps.length ? <EmptyState title={COPY.aiNoGaps} /> : null}
      <div className="ai-gap-list">
        {gaps.map((gap) => (
          <GapRow key={gap.id} gap={gap} members={members} busy={busy} onSave={onSave} />
        ))}
      </div>
    </Panel>
  );
}

export default function AiAssistant() {
  const ai = useAi(true);
  const roles = useRoles(true);
  const gaps = useContentGaps(true);
  const members = useMembers({ filter: "active" }, true);
  const saveSettings = usePatchAiSettings();
  const saveSource = usePatchAiSource();
  const saveGap = usePatchContentGap();

  const loading = ai.isLoading || roles.isLoading || gaps.isLoading || members.isLoading;
  const error = ai.isError || roles.isError || gaps.isError || members.isError;

  if (loading) return <LoadingState title={COPY.loading} />;
  if (error) {
    return (
      <ErrorState
        title={COPY.aiError}
        onRetry={() => {
          ai.refetch();
          roles.refetch();
          gaps.refetch();
          members.refetch();
        }}
      />
    );
  }

  return (
    <div className="ai-grid">
      <BehaviourPanel
        settings={ai.data?.settings}
        busy={saveSettings.isPending}
        onSave={(body) => saveSettings.mutateAsync(body)}
      />
      <SourcesPanel
        sources={ai.data?.sources?.items || []}
        roles={roles.data?.items || []}
        busy={saveSource.isPending}
        onSave={(sourceId, body) => saveSource.mutateAsync({ sourceId, body })}
      />
      <ContentGapsPanel
        gaps={gaps.data?.items || []}
        members={members.data?.items || []}
        busy={saveGap.isPending}
        onSave={(gapId, body) => saveGap.mutateAsync({ gapId, body })}
      />
    </div>
  );
}
