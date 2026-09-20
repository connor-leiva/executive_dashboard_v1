import { useEffect, useState } from "react";

import { COPY, SOP_STATE_OPTIONS } from "../constants.js";
import {
  useArchiveSop,
  useCreateSop,
  useCreateSopCategory,
  useDownloadSopVersion,
  useDraftSopBody,
  useMembers,
  usePatchSop,
  usePatchSopCategory,
  usePutSopBody,
  useRemoveSopCategory,
  useRestoreSop,
  useReviewSop,
  useReviseSop,
  useSetCurrentSopVersion,
  useSop,
  useSopCategories,
  useSopCategoryOrder,
  useSopReaders,
  useSops,
  useSopSuggestions,
  usePatchSopSuggestion,
  useSopVersions,
  useTiles,
  useUploadSopVersion,
} from "../queries.js";
import { Button, EmptyState, ErrorState, Field, LoadingState, Panel } from "../ui.jsx";

/* THE SOP LIBRARY (SOP-LIBRARY-SPEC.md, phases 1-2).
 *
 * An SOP used to be a title, a category, an owner and an uploaded file. It is now a procedure
 * somebody can read: an intro, numbered steps, and the one thing not to skip -- with the document
 * still here for the ones that are a document (D1).
 *
 * THE PROCEDURE IS THE ONE THING ON THIS PAGE THAT WAITS FOR PUBLISH (D2). Everything else is
 * live as it is saved, as the rest of this console is; a procedure halfway through a rewrite is
 * not the procedure the team should be following, so the text is held until somebody publishes. */

const NEW_SOP_ID = "new";

function dateValue(value) {
  return value ? String(value).slice(0, 10) : "";
}

function formatDate(value) {
  if (!value) return "-";
  const date = new Date(`${String(value).slice(0, 10)}T00:00:00`);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(date);
}

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function sopForm(sop, categories, members) {
  return {
    title: sop?.title || "",
    summary: sop?.summary || "",
    category_id: sop?.category_id || categories[0]?.id || "",
    owner_member_id: sop?.owner_member_id || members[0]?.id || "",
    applies_to: sop?.applies_to || "",
    required: Boolean(sop?.required),
    state: sop?.state === "Archived" ? "Draft" : sop?.state || "Draft",
    review_due_on: dateValue(sop?.review_due_on),
  };
}

function sopPayload(form) {
  return {
    title: form.title.trim(),
    summary: form.summary.trim(),
    category_id: form.category_id,
    owner_member_id: form.owner_member_id || null,
    applies_to: form.applies_to.trim(),
    required: Boolean(form.required),
    state: form.state,
    review_due_on: form.review_due_on || null,
  };
}

function bodyForm(sop) {
  const body = sop?.body || {};
  return {
    intro: body.intro || "",
    steps: (body.steps || []).map((step) => ({ title: step.title || "", text: step.text || "" })),
    callout: { label: body.callout?.label || "Do Not Skip", text: body.callout?.text || "" },
  };
}

function SopHealth({ health }) {
  return (
    <section className="sop-health" aria-label={COPY.sopTitle}>
      <div className="wtd-stat">
        <span>{COPY.sopCurrent}</span>
        <strong>{health.current ?? 0}</strong>
      </div>
      <div className="wtd-stat">
        <span>{COPY.sopDueSoon}</span>
        <strong>{health.due_soon ?? 0}</strong>
      </div>
      <div className="wtd-stat">
        <span>{COPY.sopOverdue}</span>
        <strong>{health.overdue ?? 0}</strong>
      </div>
    </section>
  );
}

function SopList({ sops, selectedId, onSelect, onNew, archived, onArchived }) {
  return (
    <Panel title={COPY.sopTitle} action={<Button type="button" onClick={onNew}>{COPY.sopNew}</Button>}>
      <label className="sop-archived-toggle">
        <input type="checkbox" checked={archived} onChange={(e) => onArchived(e.target.checked)} />
        <span>Show archived</span>
      </label>
      {!sops.length ? <EmptyState title={COPY.sopEmpty} /> : null}
      <div className="sop-list">
        {sops.map((sop) => (
          <button
            type="button"
            key={sop.id}
            className={`sop-row ${sop.id === selectedId ? "selected" : ""}`}
            onClick={() => onSelect(sop.id)}
          >
            <span>{sop.category_name || COPY.sopCategory}</span>
            <strong>{sop.title}</strong>
            <small>
              {[sop.state, sop.has_published_body ? "written" : "document only",
                `${sop.version_count} ${sop.version_count === 1 ? "revision" : "revisions"}`,
                sop.required ? "required" : "",
                sop.open_suggestions ? `${sop.open_suggestions} suggested` : ""]
                .filter(Boolean).join(" · ")}
            </small>
          </button>
        ))}
      </div>
    </Panel>
  );
}

function CategoryRow({ category, busy, onSave, onDelete, onMove, first, last }) {
  const [name, setName] = useState(category.name || "");

  useEffect(() => {
    setName(category.name || "");
  }, [category]);

  function submit(event) {
    event.preventDefault();
    onSave(category.id, { name, sort: category.sort });
  }

  return (
    <form className="sop-category-row" onSubmit={submit}>
      <Field label={COPY.sopCategoryName}>
        <input value={name} onChange={(event) => setName(event.target.value)} required />
      </Field>
      <span>{category.sop_count ?? 0}</span>
      <div className="tile-actions">
        <button type="button" disabled={busy || first} onClick={() => onMove(category.id, -1)}>{COPY.moveUp}</button>
        <button type="button" disabled={busy || last} onClick={() => onMove(category.id, 1)}>{COPY.moveDown}</button>
      </div>
      <Button type="submit" busy={busy}>{COPY.sopSaveCategory}</Button>
      <Button type="button" disabled={busy || Boolean(category.sop_count)} onClick={() => onDelete(category.id)}>
        {COPY.sopDeleteCategory}
      </Button>
    </form>
  );
}

function CategoryPanel({ categories, busy, onCreate, onSave, onDelete, onMove }) {
  const [name, setName] = useState("");

  async function submit(event) {
    event.preventDefault();
    await onCreate({ name });
    setName("");
  }

  return (
    <Panel title={COPY.sopCategories}>
      <p className="followup-hint">The rail down the left of the member&rsquo;s library, in this order.</p>
      <div className="sop-category-list">
        {categories.map((category, i) => (
          <CategoryRow
            key={category.id}
            category={category}
            busy={busy}
            onSave={onSave}
            onDelete={onDelete}
            onMove={onMove}
            first={i === 0}
            last={i === categories.length - 1}
          />
        ))}
      </div>
      <form className="sop-category-row sop-category-new" onSubmit={submit}>
        <Field label={COPY.sopCategoryName}>
          <input value={name} onChange={(event) => setName(event.target.value)} required />
        </Field>
        <span />
        <Button type="submit" tone="primary" busy={busy}>{COPY.sopAddCategory}</Button>
      </form>
    </Panel>
  );
}

/* The procedure itself. Saved on its own, because it is the one thing here that waits for
   Publish, and the page says which of the two the team is currently reading. */
function ProcedureEditor({ sop, busy, onSave }) {
  const [form, setForm] = useState(() => bodyForm(sop));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [pasting, setPasting] = useState(false);
  const [pasted, setPasted] = useState("");
  const drafting = useDraftSopBody();

  async function draft(text) {
    setMessage("");
    setError("");
    try {
      const out = await drafting.mutateAsync({ sopId: sop.id, text: text || "" });
      setForm(bodyForm({ body: out.body }));
      setPasting(false);
      setPasted("");
      setMessage(`Drafted from ${out.source}. Read it through, fix what it got wrong, then save.`);
    } catch (err) {
      setError(err.detail || err.message);
    }
  }

  useEffect(() => {
    setForm(bodyForm(sop));
    setMessage("");
    setError("");
  }, [sop?.id, sop?.body]);

  const setStep = (i, patch) => setForm((f) => ({
    ...f, steps: f.steps.map((s, j) => (j === i ? { ...s, ...patch } : s)),
  }));
  const move = (i, to) => setForm((f) => {
    if (to < 0 || to >= f.steps.length) return f;
    const steps = [...f.steps];
    const [moved] = steps.splice(i, 1);
    steps.splice(to, 0, moved);
    return { ...f, steps };
  });

  async function submit(event) {
    event.preventDefault();
    setMessage("");
    setError("");
    try {
      await onSave({
        intro: form.intro,
        steps: form.steps,
        callout: form.callout.text.trim() ? form.callout : null,
      });
      setMessage("Saved as a draft. Members keep reading the published one until you press Publish.");
    } catch (err) {
      setError(err.detail || err.message);
    }
  }

  return (
    <section className="sop-procedure">
      <header>
        <h3>The procedure</h3>
        <span>{sop?.body_live
          ? (sop?.has_published_body ? "Published" : "Nothing written yet")
          : "Unpublished changes"}</span>
      </header>
      {/* A first draft from the document this procedure already is, for a library that lives in
          a Drive folder today. It fills the boxes below and saves nothing. */}
      <div className="sop-draft">
        <Button type="button" busy={drafting.isPending}
                disabled={!sop?.current_version?.byte_size} onClick={() => draft("")}>
          Draft from the document
        </Button>
        <Button type="button" onClick={() => setPasting((v) => !v)}>
          {pasting ? "Never mind" : "Draft from pasted text"}
        </Button>
        {pasting ? (
          <div className="sop-draft-paste">
            <textarea rows={4} value={pasted} placeholder="Paste the procedure as it is written today"
                      onChange={(e) => setPasted(e.target.value)} />
            <Button type="button" tone="primary" busy={drafting.isPending}
                    disabled={!pasted.trim()} onClick={() => draft(pasted)}>
              Draft it
            </Button>
          </div>
        ) : null}
      </div>
      <form className="wtdc-form" onSubmit={submit}>
        <Field label="Opening paragraph">
          <textarea rows={3} value={form.intro} maxLength={1500}
                    placeholder="From the moment a seller says yes to the day the sign comes down, this is the sequence."
                    onChange={(e) => setForm((f) => ({ ...f, intro: e.target.value }))} />
        </Field>
        <div className="wtdc-repeat">
          {form.steps.map((step, i) => (
            <div className="wtdc-item" key={i}>
              <Field label={`Step ${i + 1}`}>
                <input value={step.title} maxLength={160} placeholder="Log the appointment in Sisu the same day"
                       onChange={(e) => setStep(i, { title: e.target.value })} />
              </Field>
              <Field label="What it means">
                <textarea rows={2} value={step.text} maxLength={1200}
                          placeholder="Set it as a listing appointment with the source."
                          onChange={(e) => setStep(i, { text: e.target.value })} />
              </Field>
              <div className="tile-actions">
                <button type="button" disabled={i === 0} onClick={() => move(i, i - 1)}>{COPY.moveUp}</button>
                <button type="button" disabled={i === form.steps.length - 1} onClick={() => move(i, i + 1)}>{COPY.moveDown}</button>
                <button type="button" onClick={() => setForm((f) => ({ ...f, steps: f.steps.filter((_, j) => j !== i) }))}>
                  Remove
                </button>
              </div>
            </div>
          ))}
          <div>
            <Button type="button" onClick={() => setForm((f) => ({ ...f, steps: [...f.steps, { title: "", text: "" }] }))}>
              Add a step
            </Button>
          </div>
        </div>
        <div className="wtdc-grid-2">
          <Field label="Callout label">
            <input value={form.callout.label} maxLength={40}
                   onChange={(e) => setForm((f) => ({ ...f, callout: { ...f.callout, label: e.target.value } }))} />
          </Field>
          <Field label="The one thing not to skip (blank: no callout)">
            <textarea rows={2} value={form.callout.text} maxLength={800}
                      placeholder="Never input a listing to the MLS before the signed agreement is uploaded."
                      onChange={(e) => setForm((f) => ({ ...f, callout: { ...f.callout, text: e.target.value } }))} />
          </Field>
        </div>
        <div className="followup-actions">
          <Button type="submit" tone="primary" busy={busy}>Save the procedure</Button>
          {message ? <span className="roster-form-message">{message}</span> : null}
          {error ? <span className="console-form-error">{error}</span> : null}
        </div>
      </form>
    </section>
  );
}

function ToolsPanel({ sop, tiles, busy, onSave }) {
  const [chosen, setChosen] = useState(() => new Set(sop?.tool_ids || []));
  const [message, setMessage] = useState("");

  useEffect(() => {
    setChosen(new Set(sop?.tool_ids || []));
    setMessage("");
  }, [sop?.id, sop?.tool_ids]);

  function toggle(id) {
    setChosen((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function save() {
    await onSave([...chosen]);
    setMessage("Saved.");
  }

  return (
    <section className="sop-tools">
      <header>
        <h3>Tools you&rsquo;ll need</h3>
        <span>{chosen.size ? `${chosen.size} chosen` : "None"}</span>
      </header>
      <p className="followup-hint">
        From the Tool Launchpad, so the links stay right. A member only sees the tools their role
        may open.
      </p>
      <div className="sop-tool-picker">
        {tiles.map((tile) => (
          <label key={tile.id} className={chosen.has(tile.id) ? "on" : ""}>
            <input type="checkbox" checked={chosen.has(tile.id)} onChange={() => toggle(tile.id)} />
            <span>{tile.name}</span>
          </label>
        ))}
        {!tiles.length ? <EmptyState title="No tools on the launchpad yet." /> : null}
      </div>
      <div className="followup-actions">
        <Button type="button" busy={busy} onClick={save}>Save the tools</Button>
        {message ? <span className="roster-form-message">{message}</span> : null}
      </div>
    </section>
  );
}

function SuggestionsPanel({ sopId }) {
  const suggestions = useSopSuggestions(sopId, true);
  const patch = usePatchSopSuggestion();
  const [note, setNote] = useState({});
  if (suggestions.isLoading || suggestions.error) return null;
  const items = suggestions.data?.items || [];
  if (!items.length) return null;
  const move = (item, status) => patch.mutate({
    suggestionId: item.id, sopId, body: { status, resolution_note: note[item.id] ?? item.resolution_note },
  });
  return (
    <section className="sop-suggestions">
      <header>
        <h3>What the team says</h3>
        <span>{suggestions.data.open ? `${suggestions.data.open} open` : "All handled"}</span>
      </header>
      <div className="sop-suggestion-list">
        {items.map((item) => (
          <div className={`sop-suggestion ${item.status.toLowerCase()}`} key={item.id}>
            <div className="sop-suggestion-head">
              <strong>{item.from || "Someone"}</strong>
              <span>{formatDate(item.created_at)}</span>
              <span className="sop-suggestion-status">{item.status}</span>
            </div>
            <p>{item.text}</p>
            <div className="sop-suggestion-row">
              <input value={note[item.id] ?? item.resolution_note ?? ""} placeholder="What you did about it"
                     maxLength={500} onChange={(e) => setNote((n) => ({ ...n, [item.id]: e.target.value }))} />
              {item.status === "New" ? (
                <Button type="button" busy={patch.isPending} onClick={() => move(item, "Read")}>Mark read</Button>
              ) : null}
              {item.status !== "Done" ? (
                <Button type="button" tone="primary" busy={patch.isPending} onClick={() => move(item, "Done")}>Done</Button>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function ReadersPanel({ sopId, enabled }) {
  const readers = useSopReaders(sopId, enabled);
  const [all, setAll] = useState(false);
  if (!enabled) return null;
  if (readers.isLoading) return <LoadingState />;
  if (readers.error) return null;
  const items = readers.data?.items || [];
  const outstanding = items.filter((i) => !i.acknowledged_at);
  const shown = all ? items : outstanding;
  return (
    <section className="sop-readers">
      <header>
        <h3>Who has read it</h3>
        <span>{`${readers.data.acknowledged} of ${items.length}${readers.data.version ? ` · ${readers.data.version}` : ""}`}</span>
      </header>
      <div className="tile-actions">
        <button type="button" className={all ? "" : "on"} onClick={() => setAll(false)}>Still to read ({outstanding.length})</button>
        <button type="button" className={all ? "on" : ""} onClick={() => setAll(true)}>Everyone</button>
      </div>
      <div className="sop-reader-list">
        {shown.map((person) => (
          <div className="sop-reader-row" key={person.member_id}>
            <strong>{person.name}</strong>
            <span>{person.acknowledged_at ? formatDate(person.acknowledged_at) : "Not yet"}</span>
          </div>
        ))}
        {!shown.length ? <EmptyState title="Everybody has read this revision." /> : null}
      </div>
    </section>
  );
}

function VersionUpload({ busy, onUpload, onRevise, canRevise }) {
  const [versionLabel, setVersionLabel] = useState("");
  const [file, setFile] = useState(null);

  async function submit(event) {
    event.preventDefault();
    // Same trap as the brand logo slots: currentTarget is null after the await, so it is read
    // now. Here it had no try/catch either, so the throw became an unhandled rejection on an
    // upload that had already stored the document.
    const form = event.currentTarget;
    if (file) await onUpload(versionLabel, file);
    else await onRevise(versionLabel);
    setVersionLabel("");
    setFile(null);
    form.reset();
  }

  return (
    <form className="sop-upload" onSubmit={submit}>
      <Field label={COPY.sopVersionLabel}>
        <input value={versionLabel} onChange={(event) => setVersionLabel(event.target.value)} required />
      </Field>
      <Field label="Document (optional once the procedure is written)">
        <input type="file" accept=".pdf,.doc,.docx" onChange={(event) => setFile(event.target.files?.[0] || null)} />
      </Field>
      <Button type="submit" tone="primary" busy={busy} disabled={!file && !canRevise}>
        {file ? COPY.sopUploadVersion : "New revision"}
      </Button>
    </form>
  );
}

function VersionsPanel({ sop, versions, busy, onUpload, onRevise, onDownload, onMakeCurrent }) {
  return (
    <section className="sop-versions">
      <header>
        <h3>{COPY.sopVersions}</h3>
        <span>{sop?.current_version?.version_label || COPY.sopNoVersion}</span>
      </header>
      <p className="followup-hint">
        A revision asks the team to read it again: whoever acknowledged the last one has not
        acknowledged this.
      </p>
      <VersionUpload busy={busy} onUpload={onUpload} onRevise={onRevise}
                     canRevise={Boolean(sop?.body)} />
      <div className="sop-version-list">
        {versions.map((version) => (
          <div className="sop-version-row" key={version.id}>
            <div>
              <strong>{version.version_label}</strong>
              <small>{version.filename
                ? `${version.filename} · ${formatBytes(version.byte_size)}`
                : "The written procedure"}</small>
            </div>
            {version.id === sop?.current_version_id
              ? <span>{COPY.sopCurrentVersion}</span>
              : <Button type="button" disabled={busy} onClick={() => onMakeCurrent(version)}>Make current</Button>}
            {version.byte_size ? (
              <Button type="button" disabled={busy} onClick={() => onDownload(version)}>
                {COPY.sopDownload}
              </Button>
            ) : <span />}
          </div>
        ))}
      </div>
    </section>
  );
}

function SopDetail({
  sop, categories, members, versions, tiles, isNew, form, setForm, busy, message, error,
  onSave, onArchive, onRestore, onUpload, onRevise, onDownload, onMakeCurrent, onSaveBody,
  onSaveTools, onReview,
}) {
  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  return (
    <Panel title={isNew ? COPY.sopNew : form.title || COPY.sopSelect}>
      <form className="sop-detail-form" onSubmit={onSave}>
        <div className="sop-fields">
          <Field label={COPY.sopTitleField}>
            <input value={form.title} onChange={(event) => update("title", event.target.value)} required />
          </Field>
          <Field label={COPY.sopCategory}>
            <select value={form.category_id} onChange={(event) => update("category_id", event.target.value)} required>
              {categories.map((category) => (
                <option key={category.id} value={category.id}>{category.name}</option>
              ))}
            </select>
          </Field>
          <Field label={COPY.sopOwner}>
            <select value={form.owner_member_id} onChange={(event) => update("owner_member_id", event.target.value)}>
              <option value="">Unassigned</option>
              {members.map((member) => (
                <option key={member.id} value={member.id}>{member.full_name}</option>
              ))}
            </select>
          </Field>
          <Field label="Applies to">
            <input value={form.applies_to} maxLength={120} placeholder="Listing Agents"
                   onChange={(event) => update("applies_to", event.target.value)} />
          </Field>
          <Field label={COPY.sopState}>
            <select value={form.state} onChange={(event) => update("state", event.target.value)}>
              {SOP_STATE_OPTIONS.map((option) => (
                <option key={option.key} value={option.key}>{option.label}</option>
              ))}
            </select>
          </Field>
          <Field label={COPY.sopReviewDue}>
            <input type="date" value={form.review_due_on} onChange={(event) => update("review_due_on", event.target.value)} />
          </Field>
        </div>
        <Field label="One line, on the card">
          <input value={form.summary} maxLength={200}
                 placeholder="From the seller saying yes to the sign coming down."
                 onChange={(event) => update("summary", event.target.value)} />
        </Field>
        <label className="sop-required">
          <input type="checkbox" checked={form.required}
                 onChange={(event) => update("required", event.target.checked)} />
          <span>Required reading — everybody is asked to read each new revision</span>
        </label>
        {message ? <p className="roster-form-message">{message}</p> : null}
        {error ? <p className="console-form-error">{error}</p> : null}
        <div className="training-actions">
          <Button type="submit" tone="primary" busy={busy}>{isNew ? COPY.sopCreate : COPY.sopSave}</Button>
          {!isNew && !sop?.archived_at ? (
            <>
              <Button type="button" disabled={busy} onClick={onReview}>
                {sop?.last_reviewed_on ? `Reviewed ${formatDate(sop.last_reviewed_on)} · mark reviewed` : "Mark reviewed"}
              </Button>
              <Button type="button" disabled={busy} onClick={onArchive}>{COPY.sopArchive}</Button>
            </>
          ) : null}
          {!isNew && sop?.archived_at ? (
            <Button type="button" disabled={busy} onClick={onRestore}>Restore</Button>
          ) : null}
        </div>
      </form>
      {!isNew && sop ? (
        <>
          <ProcedureEditor sop={sop} busy={busy} onSave={onSaveBody} />
          <ToolsPanel sop={sop} tiles={tiles} busy={busy} onSave={onSaveTools} />
          <VersionsPanel
            sop={sop}
            versions={versions}
            busy={busy}
            onUpload={onUpload}
            onRevise={onRevise}
            onDownload={onDownload}
            onMakeCurrent={onMakeCurrent}
          />
          <ReadersPanel sopId={sop.id} enabled={Boolean(sop.current_version_id)} />
          <SuggestionsPanel sopId={sop.id} />
        </>
      ) : null}
    </Panel>
  );
}

export default function SopLibrary() {
  const [archived, setArchived] = useState(false);
  const sopsQuery = useSops(true, archived);
  const categoriesQuery = useSopCategories(true);
  const membersQuery = useMembers({ filter: "active" }, true);
  const tilesQuery = useTiles(true);
  const [selectedId, setSelectedId] = useState("");
  const [form, setForm] = useState(() => sopForm(null, [], []));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const createSop = useCreateSop();
  const patchSop = usePatchSop();
  const archiveSop = useArchiveSop();
  const restoreSop = useRestoreSop();
  const reviewSop = useReviewSop();
  const putBody = usePutSopBody();
  const createCategory = useCreateSopCategory();
  const patchCategory = usePatchSopCategory();
  const removeCategory = useRemoveSopCategory();
  const categoryOrder = useSopCategoryOrder();
  const uploadVersion = useUploadSopVersion();
  const reviseSop = useReviseSop();
  const makeCurrent = useSetCurrentSopVersion();
  const downloadVersion = useDownloadSopVersion();
  const sops = sopsQuery.data?.items || [];
  const health = sopsQuery.data?.health || {};
  const categories = categoriesQuery.data?.items || [];
  const members = membersQuery.data?.items || [];
  const tiles = tilesQuery.data?.items || [];
  const isNew = selectedId === NEW_SOP_ID || !selectedId;
  const sopQuery = useSop(isNew ? "" : selectedId, Boolean(selectedId && !isNew));
  const detail = sopQuery.data || null;
  const versionsQuery = useSopVersions(detail?.id || "", Boolean(detail?.id));
  const versions = versionsQuery.data?.items || [];
  const busy = createSop.isPending
    || patchSop.isPending
    || archiveSop.isPending
    || restoreSop.isPending
    || reviewSop.isPending
    || putBody.isPending
    || createCategory.isPending
    || patchCategory.isPending
    || removeCategory.isPending
    || categoryOrder.isPending
    || uploadVersion.isPending
    || reviseSop.isPending
    || makeCurrent.isPending
    || downloadVersion.isPending;

  useEffect(() => {
    if (selectedId || sopsQuery.isPending || sopsQuery.error) return;
    setSelectedId(sops.length ? sops[0].id : NEW_SOP_ID);
  }, [selectedId, sops, sopsQuery.isPending, sopsQuery.error]);

  useEffect(() => {
    if (!selectedId || selectedId === NEW_SOP_ID || sopsQuery.isPending || sopsQuery.error) return;
    if (sops.some((sop) => sop.id === selectedId)) return;
    setSelectedId(sops.length ? sops[0].id : NEW_SOP_ID);
  }, [selectedId, sops, sopsQuery.isPending, sopsQuery.error]);

  useEffect(() => {
    if (!selectedId) return;
    if (isNew) setForm(sopForm(null, categories, members));
    else if (detail) setForm(sopForm(detail, categories, members));
  }, [selectedId, isNew, detail, categories, members]);

  function selectSop(sopId) {
    setMessage("");
    setError("");
    setSelectedId(sopId);
  }

  function newSop() {
    setMessage("");
    setError("");
    setSelectedId(NEW_SOP_ID);
    setForm(sopForm(null, categories, members));
  }

  async function saveSop(event) {
    event.preventDefault();
    setMessage("");
    setError("");
    try {
      if (isNew) {
        const created = await createSop.mutateAsync(sopPayload(form));
        setSelectedId(created.item.id);
        setMessage(COPY.sopCreated);
      } else {
        await patchSop.mutateAsync({ sopId: detail.id, body: sopPayload(form) });
        setMessage(COPY.sopSaved);
      }
    } catch (err) {
      setError(err.detail || err.message);
    }
  }

  async function run(fn, done) {
    setMessage("");
    setError("");
    try {
      await fn();
      if (done) setMessage(done);
    } catch (err) {
      setError(err.detail || err.message);
      throw err;
    }
  }

  const archiveSelected = () => detail && run(
    () => archiveSop.mutateAsync(detail.id).then(() => setSelectedId("")), COPY.sopSaved).catch(() => {});
  const restoreSelected = () => detail && run(
    () => restoreSop.mutateAsync(detail.id), "Restored as a draft.").catch(() => {});
  const reviewSelected = () => detail && run(
    () => reviewSop.mutateAsync({ sopId: detail.id, body: {} }), "Reviewed today.").catch(() => {});
  const addCategory = (body) => run(() => createCategory.mutateAsync(body), COPY.sopSaved);
  const saveCategory = (categoryId, body) => run(
    () => patchCategory.mutateAsync({ categoryId, body }), COPY.sopSaved).catch(() => {});
  const deleteCategory = (categoryId) => run(
    () => removeCategory.mutateAsync(categoryId), COPY.sopSaved).catch(() => {});
  const saveBody = (body) => putBody.mutateAsync({ sopId: detail.id, body });
  const saveTools = (toolIds) => run(
    () => patchSop.mutateAsync({ sopId: detail.id, body: { tool_ids: toolIds } })).catch(() => {});
  const uploadSopVersion = (versionLabel, file) => run(
    () => uploadVersion.mutateAsync({ sopId: detail.id, versionLabel, file }), COPY.sopUploaded);
  const reviseSopVersion = (versionLabel) => run(
    () => reviseSop.mutateAsync({ sopId: detail.id, versionLabel }),
    "A new revision. Everybody is asked to read it again.");
  const makeVersionCurrent = (version) => run(
    () => makeCurrent.mutateAsync({ sopId: detail.id, versionId: version.id }),
    `${version.version_label} is the current one.`).catch(() => {});

  function moveCategory(categoryId, by) {
    const ids = categories.map((c) => c.id);
    const from = ids.indexOf(categoryId);
    const to = from + by;
    if (from < 0 || to < 0 || to >= ids.length) return;
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    run(() => categoryOrder.mutateAsync({ ids }), COPY.sopSaved).catch(() => {});
  }

  async function downloadSopVersion(version) {
    if (!detail) return;
    setMessage("");
    setError("");
    try {
      const blob = await downloadVersion.mutateAsync({ sopId: detail.id, versionId: version.id });
      const href = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = href;
      link.download = version.filename || "sop";
      link.click();
      window.URL.revokeObjectURL(href);
    } catch (err) {
      setError(err.detail || err.message);
    }
  }

  if (sopsQuery.isPending || categoriesQuery.isPending || membersQuery.isPending) return <LoadingState />;
  if (sopsQuery.error || categoriesQuery.error || membersQuery.error) {
    return (
      <ErrorState
        title={COPY.sopError}
        onRetry={() => {
          sopsQuery.refetch();
          categoriesQuery.refetch();
          membersQuery.refetch();
        }}
      />
    );
  }

  return (
    <div className="sop-grid">
      <SopHealth health={health} />
      <div className="sop-sidebar">
        <SopList sops={sops} selectedId={selectedId} onSelect={selectSop} onNew={newSop}
                 archived={archived} onArchived={setArchived} />
        <CategoryPanel
          categories={categories}
          busy={busy}
          onCreate={addCategory}
          onSave={saveCategory}
          onDelete={deleteCategory}
          onMove={moveCategory}
        />
      </div>
      {sopQuery.isLoading ? <LoadingState /> : null}
      {sopQuery.error && !isNew ? <ErrorState title={COPY.sopError} onRetry={() => sopQuery.refetch()} /> : null}
      {/* `isLoading`, not `isPending`, on every query that can be switched off: in React Query v5
          `isPending` only means "no data", so a disabled query is pending FOREVER, while
          `isLoading` is `isPending && isFetching` and is honestly false while the query is off.
          The note that used to sit here warned about the render gate above and the line below it
          did the same thing in another shape -- `busy` -- which is how a workspace with no
          procedures got a permanently greyed-out Create SOP. Name the flag correctly instead of
          re-deriving the guard. */}
      {isNew || detail ? (
        <SopDetail
          sop={detail}
          categories={categories}
          members={members}
          versions={versions}
          tiles={tiles}
          isNew={isNew}
          form={form}
          setForm={setForm}
          busy={busy || versionsQuery.isLoading}
          message={message}
          error={error}
          onSave={saveSop}
          onArchive={archiveSelected}
          onRestore={restoreSelected}
          onReview={reviewSelected}
          onUpload={uploadSopVersion}
          onRevise={reviseSopVersion}
          onDownload={downloadSopVersion}
          onMakeCurrent={makeVersionCurrent}
          onSaveBody={saveBody}
          onSaveTools={saveTools}
        />
      ) : null}
    </div>
  );
}
