import { useEffect, useMemo, useState } from "react";

import { downloadMarketingAttachment } from "../api.js";
import { COPY } from "../constants.js";
import {
  useMarketing,
  useMarketingRequests,
  usePatchMarketing,
  usePatchMarketingRequest,
  usePatchSlack,
  useRoles,
  useSlack,
  useTestMarketing,
} from "../queries.js";
import { Button, ErrorState, Field, LoadingState, Panel } from "../ui.jsx";

/* Marketing Requests configuration.
 *
 * The screen exists because this was the last tenant-visible intranet behaviour that could only
 * be changed by editing a JSON blob in the intranet router. It configures WHERE a request goes
 * and WHAT a submitter has to fill in.
 *
 * IT STILL DOES NOT CLAIM A CONNECTION. Requests are delivered now, so most of this file's
 * "not yet connected" copy has gone -- but the rule that produced it has not: the server reports
 * configuration completeness and delivery health as two separate facts, because a saved form
 * proves somebody typed a destination and nothing else. A complete configuration nobody has
 * tested reads "Untested", not "Live", and the way to move it on is to send a test.
 */

const DESTINATION_HELP = {
  none: "Requests are saved but not delivered anywhere.",
  slack: "A channel name, including the #. The bot token goes in the panel below.",
  email: "One address. Notification routing is separate, below.",
  webhook: "An https endpoint. It receives a JSON POST per request. https only, and it cannot "
           + "point inside a private network.",
};

// No `attachments` entry: the server no longer offers it as a requirable field, because the
// submit form cannot collect a file yet. It comes back with the upload.
const FIELD_LABELS = {
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

/* Fetch the bytes with the session's credentials, then hand the browser a blob to save. A bare
   href would send no Authorization header and 401; this is what downloadSopVersion already does. */
async function saveAttachment(requestId, attachment) {
  const blob = await downloadMarketingAttachment(requestId, attachment.id);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = attachment.filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

const STATUSES = ["New", "In Progress", "Blocked", "Done", "Cancelled"];

/* The queue.
 *
 * Status here is OPERATIONAL FACT, not staged configuration -- somebody either started the work
 * or they did not -- so it writes through immediately and does not join the publish batch. An
 * agent watching their request would otherwise see "New" while it was already finished.
 */
function RequestQueue() {
  const requests = useMarketingRequests(true);
  const move = usePatchMarketingRequest();
  const items = requests.data?.items || [];

  if (requests.isLoading) return <Panel title="Request queue"><LoadingState /></Panel>;
  if (requests.isError) {
    return (
      <Panel title="Request queue">
        <ErrorState title={COPY.loadFailed} onRetry={() => requests.refetch()} />
      </Panel>
    );
  }

  return (
    <Panel title={`Request queue${items.length ? ` · ${items.length}` : ""}`}>
      {!items.length ? (
        <p className="hint">No requests submitted yet.</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Request</th><th>From</th><th>Needed</th><th>Priority</th><th>Status</th>
            </tr>
          </thead>
          <tbody>
            {items.map((r) => (
              <tr key={r.id}>
                <td>
                  <strong>{r.title}</strong>
                  {r.listing && <div className="hint">{r.listing}</div>}
                  {/* Proxied download, never a storage URL: the bytes come back through the API
                      with the type the server sniffed and an attachment disposition. */}
                  {r.attachments?.length > 0 && (
                    <div className="hint">
                      {r.attachments.map((a) => (
                        <button key={a.id} type="button" className="attachment-link"
                                onClick={() => saveAttachment(r.id, a)}>
                          {a.filename}
                        </button>
                      ))}
                    </div>
                  )}
                </td>
                <td>{r.requester_label}</td>
                <td>{r.due_date || "—"}</td>
                <td>{r.priority}</td>
                <td>
                  <select
                    value={r.status}
                    disabled={move.isPending}
                    onChange={(e) => move.mutate({ id: r.id, body: { status: e.target.value } })}
                  >
                    {STATUSES.map((sv) => <option key={sv} value={sv}>{sv}</option>)}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="hint">
        Requests are recorded here and delivered to the destination within a minute. This list
        stays the record: it is where attachments are downloaded and where status is set, and
        the person who filed a request is emailed whenever that status changes.
      </p>
    </Panel>
  );
}

/* What the four delivery states mean in words an admin can act on. The server never collapses
   these into a "connected" boolean and neither does this. */
const DELIVERY_COPY = {
  off: "Nothing is delivered — there is no destination, or the form is switched off.",
  untested: "Requests will be delivered here. Nothing has been sent yet, so this is unproven — "
          + "send a test.",
  live: "Working. The last test was delivered to this destination.",
  failing: "The last test did not arrive. Requests are still queued and retried, but check the "
         + "destination below.",
};

/* The Slack bot token.
 *
 * Separate from the channel above because they are different KINDS of thing: the channel is
 * routing an admin should be able to read back, and the token is a credential. The token is
 * stored encrypted and never returned -- this panel can only say whether one is set. */
function SlackConnection() {
  const slack = useSlack(true);
  const save = usePatchSlack();
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const item = slack.data?.item;

  return (
    <Panel title="Slack connection">
      <p className="hint">
        A bot token from your Slack app, starting with xoxb-. The bot also has to be invited to
        the channel — Slack accepts the post and refuses it otherwise.
      </p>
      <dl className="kv">
        <dt>Token</dt>
        <dd>{item?.token_set ? "Stored" : "Not set"}</dd>
        {item?.last_error && (<><dt>Last error</dt><dd>{item.last_error}</dd></>)}
      </dl>
      <Field label={item?.token_set ? "Replace the token" : "Bot token"}>
        <input
          type="password"
          value={token}
          autoComplete="off"
          onChange={(e) => setToken(e.target.value)}
          placeholder="xoxb-…"
        />
      </Field>
      {error && <p className="error">{error}</p>}
      <div className="row">
        <Button
          tone="primary"
          busy={save.isPending}
          disabled={!token.trim()}
          onClick={async () => {
            setError("");
            try {
              await save.mutateAsync({ bot_token: token.trim() });
              setToken("");                     // never leave a credential in an input
            } catch (err) {
              setError(err?.detail || err?.message || "Could not save.");
            }
          }}
        >
          Save token
        </Button>
        {item?.token_set && (
          <Button
            busy={save.isPending}
            onClick={async () => {
              setError("");
              try { await save.mutateAsync({ clear: true }); }
              catch (err) { setError(err?.detail || err?.message || "Could not remove."); }
            }}
          >
            Remove
          </Button>
        )}
      </div>
    </Panel>
  );
}

export default function MarketingRequests() {
  const marketing = useMarketing(true);
  const roles = useRoles(true);
  const save = usePatchMarketing();
  const test = useTestMarketing();
  const [testResult, setTestResult] = useState(null);
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

      <RequestQueue />

      {form.destination_type === "slack" && <SlackConnection />}

      <Panel title="Status">
        {/* Two rows, never one. Configuration is something this screen can know for itself;
            delivery is only ever known from a delivery, and the difference is the whole point of
            reporting them apart. */}
        <dl className="kv">
          <dt>Configuration</dt>
          <dd>{item?.config_complete
            ? "Complete — destination set and the form is enabled."
            : "Incomplete — the intranet shows the request form as unavailable."}</dd>
          <dt>Delivery</dt>
          <dd>{DELIVERY_COPY[item?.delivery] || DELIVERY_COPY.off}</dd>
          <dt>Last tested</dt>
          <dd>{item?.last_tested_at
            ? `${new Date(item.last_tested_at).toLocaleString()} — ${item.last_test_detail || ""}`
            : "Never"}</dd>
        </dl>
        <div className="row">
          <Button
            busy={test.isPending}
            disabled={!item?.config_complete}
            onClick={async () => {
              setTestResult(null);
              try {
                setTestResult(await test.mutateAsync());
              } catch (err) {
                // A refused test is still an answer worth showing; only a broken request is an
                // error the admin cannot act on.
                setTestResult({ ok: false, detail: err?.detail || err?.message || "Failed." });
              }
            }}
          >
            Send a test message
          </Button>
        </div>
        {testResult && (
          <p className={testResult.ok ? "hint" : "error"}>
            {testResult.ok ? "Delivered. " : "Not delivered. "}{testResult.detail}
          </p>
        )}
        {!item?.config_complete && (
          <p className="hint">Set a destination and switch requests on before testing.</p>
        )}
      </Panel>
    </div>
  );
}
