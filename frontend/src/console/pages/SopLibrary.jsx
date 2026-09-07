import { useEffect, useState } from "react";

import { COPY, SOP_STATE_OPTIONS } from "../constants.js";
import {
  useArchiveSop,
  useCreateSop,
  useCreateSopCategory,
  useDownloadSopVersion,
  useMembers,
  usePatchSop,
  usePatchSopCategory,
  useRemoveSopCategory,
  useSop,
  useSopCategories,
  useSops,
  useSopVersions,
  useUploadSopVersion,
} from "../queries.js";
import { Button, EmptyState, ErrorState, Field, LoadingState, Panel } from "../ui.jsx";

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
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function sopForm(sop, categories, members) {
  return {
    title: sop?.title || "",
    category_id: sop?.category_id || categories[0]?.id || "",
    owner_member_id: sop?.owner_member_id || members[0]?.id || "",
    state: sop?.state === "Archived" ? "Draft" : sop?.state || "Draft",
    review_due_on: dateValue(sop?.review_due_on),
  };
}

function sopPayload(form) {
  return {
    title: form.title.trim(),
    category_id: form.category_id,
    owner_member_id: form.owner_member_id || null,
    state: form.state,
    review_due_on: form.review_due_on || null,
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

function SopList({ sops, selectedId, onSelect, onNew }) {
  return (
    <Panel title={COPY.sopTitle} action={<Button type="button" onClick={onNew}>{COPY.sopNew}</Button>}>
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
            <small>{sop.state} - {formatDate(sop.review_due_on)} - {sop.version_count} versions</small>
          </button>
        ))}
      </div>
    </Panel>
  );
}

function CategoryRow({ category, busy, onSave, onDelete }) {
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
      <Button type="submit" busy={busy}>{COPY.sopSaveCategory}</Button>
      <Button type="button" disabled={busy || Boolean(category.sop_count)} onClick={() => onDelete(category.id)}>
        {COPY.sopDeleteCategory}
      </Button>
    </form>
  );
}

function CategoryPanel({ categories, busy, onCreate, onSave, onDelete }) {
  const [name, setName] = useState("");

  async function submit(event) {
    event.preventDefault();
    await onCreate({ name });
    setName("");
  }

  return (
    <Panel title={COPY.sopCategories}>
      <div className="sop-category-list">
        {categories.map((category) => (
          <CategoryRow
            key={category.id}
            category={category}
            busy={busy}
            onSave={onSave}
            onDelete={onDelete}
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

function VersionUpload({ busy, onUpload }) {
  const [versionLabel, setVersionLabel] = useState("");
  const [file, setFile] = useState(null);

  async function submit(event) {
    event.preventDefault();
    if (!file) return;
    // Same trap as the brand logo slots: currentTarget is null after the await, so it is read
    // now. Here it had no try/catch either, so the throw became an unhandled rejection on an
    // upload that had already stored the document.
    const form = event.currentTarget;
    await onUpload(versionLabel, file);
    setVersionLabel("");
    setFile(null);
    form.reset();
  }

  return (
    <form className="sop-upload" onSubmit={submit}>
      <Field label={COPY.sopVersionLabel}>
        <input value={versionLabel} onChange={(event) => setVersionLabel(event.target.value)} required />
      </Field>
      <Field label={COPY.sopFile}>
        <input type="file" accept=".pdf,.doc,.docx" onChange={(event) => setFile(event.target.files?.[0] || null)} required />
      </Field>
      <Button type="submit" tone="primary" busy={busy}>{COPY.sopUploadVersion}</Button>
    </form>
  );
}

function VersionsPanel({ sop, versions, busy, onUpload, onDownload }) {
  return (
    <section className="sop-versions">
      <header>
        <h3>{COPY.sopVersions}</h3>
        <span>{sop?.current_version?.version_label || COPY.sopNoVersion}</span>
      </header>
      <VersionUpload busy={busy} onUpload={onUpload} />
      <div className="sop-version-list">
        {versions.map((version) => (
          <div className="sop-version-row" key={version.id}>
            <div>
              <strong>{version.version_label}</strong>
              <small>{version.filename} - {formatBytes(version.byte_size)}</small>
            </div>
            {version.id === sop?.current_version_id ? <span>{COPY.sopCurrentVersion}</span> : <span />}
            <Button type="button" disabled={busy || !version.byte_size} onClick={() => onDownload(version)}>
              {COPY.sopDownload}
            </Button>
          </div>
        ))}
      </div>
    </section>
  );
}

function SopDetail({
  sop,
  categories,
  members,
  versions,
  isNew,
  form,
  setForm,
  busy,
  message,
  error,
  onSave,
  onArchive,
  onUpload,
  onDownload,
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
        {message ? <p className="roster-form-message">{message}</p> : null}
        {error ? <p className="console-form-error">{error}</p> : null}
        <div className="training-actions">
          <Button type="submit" tone="primary" busy={busy}>{isNew ? COPY.sopCreate : COPY.sopSave}</Button>
          {!isNew ? <Button type="button" disabled={busy} onClick={onArchive}>{COPY.sopArchive}</Button> : null}
        </div>
      </form>
      {!isNew ? (
        <VersionsPanel
          sop={sop}
          versions={versions}
          busy={busy}
          onUpload={onUpload}
          onDownload={onDownload}
        />
      ) : null}
    </Panel>
  );
}

export default function SopLibrary() {
  const sopsQuery = useSops(true);
  const categoriesQuery = useSopCategories(true);
  const membersQuery = useMembers({ filter: "active" }, true);
  const [selectedId, setSelectedId] = useState("");
  const [form, setForm] = useState(() => sopForm(null, [], []));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const createSop = useCreateSop();
  const patchSop = usePatchSop();
  const archiveSop = useArchiveSop();
  const createCategory = useCreateSopCategory();
  const patchCategory = usePatchSopCategory();
  const removeCategory = useRemoveSopCategory();
  const uploadVersion = useUploadSopVersion();
  const downloadVersion = useDownloadSopVersion();
  const sops = sopsQuery.data?.items || [];
  const health = sopsQuery.data?.health || {};
  const categories = categoriesQuery.data?.items || [];
  const members = membersQuery.data?.items || [];
  const isNew = selectedId === NEW_SOP_ID || !selectedId;
  const sopQuery = useSop(isNew ? "" : selectedId, Boolean(selectedId && !isNew));
  const detail = sopQuery.data || null;
  const versionsQuery = useSopVersions(detail?.id || "", Boolean(detail?.id));
  const versions = versionsQuery.data?.items || [];
  const busy = createSop.isPending
    || patchSop.isPending
    || archiveSop.isPending
    || createCategory.isPending
    || patchCategory.isPending
    || removeCategory.isPending
    || uploadVersion.isPending
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

  async function archiveSelected() {
    if (!detail) return;
    setMessage("");
    setError("");
    try {
      await archiveSop.mutateAsync(detail.id);
      setSelectedId("");
      setMessage(COPY.sopSaved);
    } catch (err) {
      setError(err.detail || err.message);
    }
  }

  async function addCategory(body) {
    setMessage("");
    setError("");
    try {
      await createCategory.mutateAsync(body);
      setMessage(COPY.sopSaved);
    } catch (err) {
      setError(err.detail || err.message);
      throw err;
    }
  }

  async function saveCategory(categoryId, body) {
    setMessage("");
    setError("");
    try {
      await patchCategory.mutateAsync({ categoryId, body });
      setMessage(COPY.sopSaved);
    } catch (err) {
      setError(err.detail || err.message);
    }
  }

  async function deleteCategory(categoryId) {
    setMessage("");
    setError("");
    try {
      await removeCategory.mutateAsync(categoryId);
      setMessage(COPY.sopSaved);
    } catch (err) {
      setError(err.detail || err.message);
    }
  }

  async function uploadSopVersion(versionLabel, file) {
    if (!detail) return;
    setMessage("");
    setError("");
    try {
      await uploadVersion.mutateAsync({ sopId: detail.id, versionLabel, file });
      setMessage(COPY.sopUploaded);
    } catch (err) {
      setError(err.detail || err.message);
      throw err;
    }
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
        <SopList sops={sops} selectedId={selectedId} onSelect={selectSop} onNew={newSop} />
        <CategoryPanel
          categories={categories}
          busy={busy}
          onCreate={addCategory}
          onSave={saveCategory}
          onDelete={deleteCategory}
        />
      </div>
      {sopQuery.isPending && !isNew ? <LoadingState /> : null}
      {sopQuery.error && !isNew ? <ErrorState title={COPY.sopError} onRetry={() => sopQuery.refetch()} /> : null}
      {/* Same bug as Training's course pane, same fix: a disabled query is permanently
          `isPending` in React Query v5, sopQuery is disabled exactly when isNew, so New SOP set
          the state and rendered nothing. See the note there. */}
      {isNew || detail ? (
        <SopDetail
          sop={detail}
          categories={categories}
          members={members}
          versions={versions}
          isNew={isNew}
          form={form}
          setForm={setForm}
          busy={busy || versionsQuery.isPending}
          message={message}
          error={error}
          onSave={saveSop}
          onArchive={archiveSelected}
          onUpload={uploadSopVersion}
          onDownload={downloadSopVersion}
        />
      ) : null}
    </div>
  );
}
