import { useEffect, useMemo, useState } from "react";

import { BRAND_LOGO_SLOTS, BRAND_SWATCHES, COPY, DEFAULT_BRAND_PALETTE } from "../constants.js";
import { usePatchWorkspace, useUploadWorkspaceLogo, useWorkspace } from "../queries.js";
import { Button, ErrorState, Field, LoadingState, Panel } from "../ui.jsx";

function mergePalette(palette) {
  return { ...DEFAULT_BRAND_PALETTE, ...(palette || {}) };
}

function workspaceForm(workspace) {
  return {
    portal_name: workspace?.portal_name || "",
    tagline: workspace?.tagline || "",
    subdomain: workspace?.subdomain || "",
    palette: mergePalette(workspace?.palette),
  };
}

function hexValue(value, fallback = "#000000") {
  return /^#[0-9A-Fa-f]{6}$/.test(value || "") ? value : fallback;
}

function storageLabel(key) {
  if (!key) return COPY.brandNoLogo;
  return String(key).split(/[\\/]/).pop();
}

function initials(name) {
  return String(name || COPY.productName)
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase() || "UL";
}

function LogoSlot({ slot, workspace, busy, onUpload }) {
  const [file, setFile] = useState(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const key = workspace?.[slot.key];

  async function submit(event) {
    event.preventDefault();
    if (!file) return;
    // Captured BEFORE the await. React clears the synthetic event's currentTarget once
    // the handler yields, so reading it after an await is null -- which threw
    // "Cannot read properties of null (reading 'reset')" AFTER the upload had already
    // succeeded, painting a red error over work that worked.
    const form = event.currentTarget;
    setError("");
    setMessage("");
    try {
      await onUpload(slot.kind, file);
      setFile(null);
      form.reset();
      setMessage(COPY.brandStoredKey);
    } catch (err) {
      setError(err.message || COPY.loadFailed);
    }
  }

  return (
    <form className="brand-logo-slot" onSubmit={submit}>
      <div>
        <span>{slot.label}</span>
        <strong>{storageLabel(key)}</strong>
        {message ? <small>{message}</small> : null}
        {error ? <small className="brand-error">{error}</small> : null}
      </div>
      <input
        type="file"
        accept="image/png,image/jpeg,image/webp,image/svg+xml"
        onChange={(event) => setFile(event.target.files?.[0] || null)}
      />
      <Button type="submit" busy={busy} disabled={!file}>{COPY.brandUpload}</Button>
    </form>
  );
}

function PaletteField({ swatch, value, onChange }) {
  const fallback = DEFAULT_BRAND_PALETTE[swatch.key] || "#000000";
  return (
    <div className="brand-swatch-row">
      <span style={{ background: hexValue(value, fallback) }} aria-hidden="true" />
      <Field label={swatch.label}>
        <div className="brand-color-control">
          <input
            type="color"
            value={hexValue(value, fallback)}
            onChange={(event) => onChange(swatch.key, event.target.value.toUpperCase())}
          />
          <input
            value={value}
            pattern="^#[0-9A-Fa-f]{6}$"
            onChange={(event) => onChange(swatch.key, event.target.value)}
            required
          />
        </div>
      </Field>
    </div>
  );
}

function DomainStatus({ workspace }) {
  const verified = Boolean(workspace?.custom_domain_verified_at);
  return (
    <div className={`brand-domain-status ${verified ? "verified" : ""}`}>
      <span>{COPY.brandDomainStatus}</span>
      <strong>{verified ? COPY.brandVerified : COPY.brandNotVerified}</strong>
    </div>
  );
}

function BrandPreview({ form, domain }) {
  const palette = mergePalette(form.palette);
  const previewStyle = {
    "--preview-ink": hexValue(palette.ink, DEFAULT_BRAND_PALETTE.ink),
    "--preview-brand": hexValue(palette.brand, DEFAULT_BRAND_PALETTE.brand),
    "--preview-accent": hexValue(palette.accent, DEFAULT_BRAND_PALETTE.accent),
    "--preview-canvas": hexValue(palette.canvas, DEFAULT_BRAND_PALETTE.canvas),
    "--preview-gold": hexValue(palette.gold, DEFAULT_BRAND_PALETTE.gold),
  };

  return (
    <section className="brand-preview" style={previewStyle} aria-label={COPY.brandPreview}>
      <aside>
        <div className="brand-preview-logo">
          <span>{initials(form.portal_name)}</span>
          <div>
            <strong>{form.portal_name || COPY.productName}</strong>
            <small>Team Intranet</small>
          </div>
        </div>
        <nav>
          <strong>Home</strong>
          <span>Tool Launchpad</span>
          <span>Win the Day</span>
          <span>Team Calendar</span>
        </nav>
      </aside>
      <main>
        <div className="brand-preview-top">
          <span>{domain}</span>
          <strong>{COPY.brandPreview}</strong>
        </div>
        <section>
          <small>Workspace Hub</small>
          <h3>{form.portal_name || COPY.productName}</h3>
          <p>{form.tagline || "Everything your team needs, one place."}</p>
          <div>
            <button type="button">Open My Tools</button>
            <button type="button">My Numbers</button>
          </div>
        </section>
      </main>
    </section>
  );
}

export default function BrandIdentity() {
  const workspace = useWorkspace(true);
  const saveWorkspace = usePatchWorkspace();
  const uploadLogo = useUploadWorkspaceLogo();
  const [form, setForm] = useState(() => workspaceForm(null));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (workspace.data) setForm(workspaceForm(workspace.data));
  }, [workspace.data]);

  const domain = useMemo(() => {
    const custom = workspace.data?.custom_domain;
    if (custom) return custom;
    return `${form.subdomain || workspace.data?.subdomain || "workspace"}.acumyn.io`;
  }, [form.subdomain, workspace.data]);

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function updatePalette(key, value) {
    setForm((current) => ({
      ...current,
      palette: { ...current.palette, [key]: value },
    }));
  }

  async function submit(event) {
    event.preventDefault();
    setError("");
    setMessage("");
    try {
      await saveWorkspace.mutateAsync({
        portal_name: form.portal_name.trim(),
        tagline: form.tagline.trim() || null,
        subdomain: form.subdomain.trim(),
        palette: form.palette,
      });
      setMessage(COPY.brandSaved);
    } catch (err) {
      setError(err.message || COPY.loadFailed);
    }
  }

  async function onUpload(kind, file) {
    await uploadLogo.mutateAsync({ kind, file });
  }

  if (workspace.isLoading) return <LoadingState title={COPY.loading} />;
  if (workspace.isError) {
    return <ErrorState title={COPY.brandError} onRetry={() => workspace.refetch()} />;
  }

  return (
    <div className="brand-grid">
      <Panel title={COPY.brandIdentity}>
        <form className="brand-form" onSubmit={submit}>
          <div className="brand-fields">
            <Field label={COPY.brandPortalName}>
              <input
                value={form.portal_name}
                onChange={(event) => update("portal_name", event.target.value)}
                required
              />
            </Field>
            <Field label={COPY.brandTagline}>
              <input value={form.tagline} onChange={(event) => update("tagline", event.target.value)} />
            </Field>
            <Field label={COPY.brandSubdomain}>
              <input value={form.subdomain} onChange={(event) => update("subdomain", event.target.value)} required />
            </Field>
            <Field label={COPY.brandCustomDomain}>
              <input value={domain} readOnly />
            </Field>
          </div>
          <DomainStatus workspace={workspace.data} />
          <div className="brand-form-actions">
            <Button type="submit" tone="primary" busy={saveWorkspace.isPending}>{COPY.brandSave}</Button>
            {message ? <span>{message}</span> : null}
            {error ? <span className="brand-error">{error}</span> : null}
          </div>
        </form>
      </Panel>

      <Panel title={COPY.brandPreview}>
        <BrandPreview form={form} domain={domain} />
      </Panel>

      <Panel title={COPY.brandPalette}>
        <div className="brand-palette">
          {BRAND_SWATCHES.map((swatch) => (
            <PaletteField
              key={swatch.key}
              swatch={swatch}
              value={form.palette[swatch.key] || ""}
              onChange={updatePalette}
            />
          ))}
        </div>
      </Panel>

      <Panel title={COPY.brandLogos}>
        <div className="brand-logo-list">
          {BRAND_LOGO_SLOTS.map((slot) => (
            <LogoSlot
              key={slot.kind}
              slot={slot}
              workspace={workspace.data}
              busy={uploadLogo.isPending}
              onUpload={onUpload}
            />
          ))}
        </div>
      </Panel>
    </div>
  );
}
