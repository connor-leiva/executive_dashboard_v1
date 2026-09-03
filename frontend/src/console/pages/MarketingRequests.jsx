import { useEffect, useMemo, useState } from "react";

import { COPY } from "../constants.js";
import { useMarketing, usePatchMarketing, useRoles } from "../queries.js";
import { Button, ErrorState, Field, LoadingState, Panel } from "../ui.jsx";

/* Marketing Requests configuration.
 *
 * The screen exists because this was the last tenant-visible intranet behaviour that could only
 * be changed by editing a JSON blob in the intranet router. It configures WHERE a request goes
 * and WHAT a submitter has to fill in; the request record itself is Phase 10.
 *
 * IT DOES NOT CLAIM A CONNECTION. The server reports configuration completeness and delivery
 * health as two separate facts and this screen keeps them separate, because a saved form proves
 * only that somebody typed a destination -- not that anything can be delivered to it. Until the
 * delivery path exists, "Configured" is the honest ceiling and the screen says so in as many
 * words rather than showing a green dot.
 */

const DESTINATION_HELP = {
  none: "Requests are saved but not delivered anywhere.",
  slack: "A channel name, including the #. The Slack connection itself lives in Integrations.",
  email: "One address. Notification routing is separate, below.",
  webhook: "An https endpoint. It receives a POST per request once delivery is built.",
};

const FIELD_LABELS = {
  attachments: "Attachments",
  client: "Client",
  description: "Description",
  due_date: "Due date",
  listing: "Listing",
  priority: "Priority",
  request_type: "Request type",
};

function formFor(item) {
  return {
    enabled: item?.enabled ?? false,
    destination_type: item?.destination_type || "none",
    destination: item?.destination || "",
    default_role_id: item?.default_role_id || "",
    required_fields: item?.required_fields || [],
    notify: item?.notify || "",
  };
}

export default function MarketingRequests() {
  const marketing = useMarketing(true);
  const roles = useRoles(true);
  const save = usePatchMarketing();
  const item = marketing.data?.item;
  const [form, setForm] = useState(() => formFor(null));
  const [error, setError] = useState("");

  // Re-seed from the server whenever it answers, so the form shows what is stored rather than
  // whatever was last typed into an unsaved draft.
  useEffect(() => { if (item) setForm(formFor(item)); }, [item]);

  const fieldOptions = marketing.data?.field_options || [];
  const destinationTypes = marketing.data?.destination_types || ["none"];
  const roleList = roles.data?.items || [];

  const set = (patch) => setForm((prev) => ({ ...prev, ...patch }));
  const toggleField = (key) => set({
    required_fields: form.required_fields.includes(key)
      ? form.required_fields.filter((f) => f !== key)
      : [...form.required_fields, key],
  });

  // The server refuses `enabled` without a destination. Mirroring the rule here means the button
  // explains itself before the round trip instead of surfacing a 422 the user has to interpret.
  const canEnable = form.destination_type !== "none" && form.destination.trim().length > 0;

  const dirty = useMemo(() => {
    if (!item) return false;
    const a = formFor(item);
    return JSON.stringify({ ...a, required_fields: [...a.required_fields].sort() })
        !== JSON.stringify({ ...form, required_fields: [...form.required_fields].sort() });
  }, [item, form]);

  async function submit(event) {
    event.preventDefault();
    setError("");
    try {
      await save.mutateAsync({
        enabled: Boolean(form.enabled && canEnable),
        destination_type: form.destination_type,
        destination: form.destination.trim() || null,
        default_role_id: form.default_role_id || null,
        required_fields: form.required_fields,
        notify: form.notify.trim() || null,
      });
    } catch (err) {
      setError(err?.detail || err?.message || "Could not save.");
    }
  }

  if (marketing.isLoading) return <LoadingState />;
  if (marketing.isError) {
    return <ErrorState title={COPY.loadFailed} onRetry={() => marketing.refetch()} />;
  }

  return (
    <div className="stack">
      <Panel title="Where requests go">
        <form className="stack" onSubmit={submit}>
          <Field label="Destination type">
            <select
              value={form.destination_type}
              onChange={(e) => set({
                destination_type: e.target.value,
                // The destination means something different per type, so carrying the old value
                // across a type change would offer a Slack channel as a webhook URL.
                destination: "",
                enabled: e.target.value === "none" ? false : form.enabled,
              })}
            >
              {destinationTypes.map((t) => (
                <option key={t} value={t}>{t === "none" ? "Not set" : t}</option>
              ))}
            </select>
          </Field>
          <p className="hint">{DESTINATION_HELP[form.destination_type]}</p>

          {form.destination_type !== "none" && (
            <Field label="Destination">
              <input
                value={form.destination}
                onChange={(e) => set({ destination: e.target.value })}
                placeholder={{
                  slack: "#marketing",
                  email: "marketing@example.com",
                  webhook: "https://example.com/hooks/marketing",
                }[form.destination_type]}
              />
            </Field>
          )}

          <Field label="Picked up by">
            <select
              value={form.default_role_id}
              onChange={(e) => set({ default_role_id: e.target.value })}
            >
              <option value="">Nobody assigned yet</option>
              {roleList.map((role) => (
                <option key={role.id} value={role.id}>{role.name}</option>
              ))}
            </select>
          </Field>

          <Field label="Also notify">
            <input
              value={form.notify}
              onChange={(e) => set({ notify: e.target.value })}
              placeholder="Optional second channel or address"
            />
          </Field>

          <label className="check">
            <input
              type="checkbox"
              checked={Boolean(form.enabled && canEnable)}
              disabled={!canEnable}
              onChange={(e) => set({ enabled: e.target.checked })}
            />
            <span>
              Show the request form in the intranet
              {!canEnable && " — set a destination first"}
            </span>
          </label>

          {error && <p className="error">{error}</p>}
          <div className="row">
            <Button type="submit" tone="primary" busy={save.isPending} disabled={!dirty}>
              {dirty ? "Save changes" : "Saved"}
            </Button>
          </div>
        </form>
      </Panel>

      <Panel title="What a submitter must fill in">
        <p className="hint">
          Every request always carries a title and a requester. These are the extra fields this
          workspace makes mandatory.
        </p>
        <div className="chips">
          {fieldOptions.map((key) => (
            <label key={key} className="check">
              <input
                type="checkbox"
                checked={form.required_fields.includes(key)}
                onChange={() => toggleField(key)}
              />
              <span>{FIELD_LABELS[key] || key}</span>
            </label>
          ))}
        </div>
      </Panel>

      <Panel title="Status">
        {/* Two rows, never one. Configuration is something this screen can know; delivery is not,
            and the difference is the whole point of reporting them apart. */}
        <dl className="kv">
          <dt>Configuration</dt>
          <dd>{item?.config_complete
            ? "Complete — destination set and the form is enabled."
            : "Incomplete — the intranet shows the request form as unavailable."}</dd>
          <dt>Delivery</dt>
          <dd>
            Not yet connected. Configuration is saved and will be used the moment the delivery
            path ships; nothing has been sent to this destination and it has never been tested.
          </dd>
          <dt>Last tested</dt>
          <dd>{item?.last_tested_at
            ? new Date(item.last_tested_at).toLocaleString()
            : "Never"}</dd>
        </dl>
      </Panel>
    </div>
  );
}
