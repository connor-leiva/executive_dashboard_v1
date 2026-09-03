import { useEffect, useMemo, useState } from "react";

import {
  COPY,
  DEFAULT_TILE_AUTH,
  DEFAULT_TILE_GROUP,
  TILE_AUTH_OPTIONS,
} from "../constants.js";
import {
  useCreateTile,
  useOrderTiles,
  usePatchTile,
  useRemoveTile,
  useRoles,
  useSaveTileRoles,
  useTiles,
} from "../queries.js";
import { Button, EmptyState, ErrorState, Field, LoadingState, Panel } from "../ui.jsx";

const NEW_TILE_ID = "new";

function initials(name) {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "T";
  return parts.slice(0, 2).map((part) => part[0]).join("").toUpperCase();
}

function formFromTile(tile, roles) {
  return {
    name: tile?.name || "",
    logo_key: tile?.logo_key || "",
    tile_group: tile?.tile_group || DEFAULT_TILE_GROUP,
    url: tile?.url || "",
    auth_type: tile?.auth_type || DEFAULT_TILE_AUTH,
    active: tile?.active ?? true,
    role_ids: tile?.role_ids || roles.map((role) => role.id),
  };
}

function roleName(roles, selectedRole, preview) {
  return preview?.preview_role?.name || roles.find((role) => role.key === selectedRole)?.name || selectedRole.replace(/_/g, " ");
}

function tilePayload(form) {
  return {
    name: form.name.trim(),
    logo_key: form.logo_key.trim() || null,
    tile_group: form.tile_group.trim() || DEFAULT_TILE_GROUP,
    url: form.url.trim(),
    auth_type: form.auth_type,
    active: Boolean(form.active),
  };
}

function TileRow({ tile, selected, index, total, busy, onSelect, onMove }) {
  return (
    <div className={`tile-row ${selected ? "selected" : ""} ${tile.active ? "" : "inactive"}`}>
      <button type="button" className="tile-row-main" onClick={() => onSelect(tile.id)}>
        <span className="tile-mark">{tile.logo_key ? tile.logo_key.slice(0, 2).toUpperCase() : initials(tile.name)}</span>
        <span>
          <strong>{tile.name}</strong>
          <small>{tile.tile_group} - {tile.auth_type}</small>
        </span>
      </button>
      <div className="tile-actions" aria-label={`${tile.name} order`}>
        <button
          type="button"
          title={COPY.moveUp}
          disabled={busy || index === 0}
          onClick={() => onMove(index, index - 1)}
        >
          {COPY.moveUp}
        </button>
        <button
          type="button"
          title={COPY.moveDown}
          disabled={busy || index === total - 1}
          onClick={() => onMove(index, index + 1)}
        >
          {COPY.moveDown}
        </button>
      </div>
    </div>
  );
}

function RoleChecks({ roles, selectedIds, onToggle }) {
  const selected = new Set(selectedIds);
  return (
    <div className="tile-role-list">
      {roles.map((role) => (
        <label key={role.id}>
          <input
            type="checkbox"
            checked={selected.has(role.id)}
            onChange={() => onToggle(role.id)}
          />
          <span>{role.name}</span>
        </label>
      ))}
    </div>
  );
}

function PreviewTile({ tile }) {
  return (
    <a className="preview-tile" href={tile.url} target="_blank" rel="noreferrer">
      <span className="tile-mark">{tile.logo_key ? tile.logo_key.slice(0, 2).toUpperCase() : initials(tile.name)}</span>
      <span>
        <strong>{tile.name}</strong>
        <small>{tile.tile_group} - {tile.auth_type}</small>
      </span>
    </a>
  );
}

export default function ToolLaunchpad({ selectedRole, preview, previewLoading }) {
  const tilesQuery = useTiles(true);
  const rolesQuery = useRoles(true);
  const createMutation = useCreateTile();
  const patchMutation = usePatchTile();
  const removeMutation = useRemoveTile();
  const rolesMutation = useSaveTileRoles();
  const orderMutation = useOrderTiles();
  const roles = rolesQuery.data?.items || [];
  const tiles = tilesQuery.data?.items || [];
  const [selectedId, setSelectedId] = useState("");
  const [form, setForm] = useState(() => formFromTile(null, []));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const selectedTile = useMemo(
    () => tiles.find((tile) => tile.id === selectedId) || null,
    [selectedId, tiles],
  );
  const isNew = selectedId === NEW_TILE_ID || !selectedId;
  const busy = createMutation.isPending
    || patchMutation.isPending
    || removeMutation.isPending
    || rolesMutation.isPending
    || orderMutation.isPending;
  const visibleCount = preview?.tiles?.total ?? 0;
  const totalCount = tilesQuery.data?.total ?? tiles.length;
  const previewTiles = preview?.tiles?.items || [];
  const currentRoleName = roleName(roles, selectedRole, preview);

  useEffect(() => {
    if (selectedId || tilesQuery.isPending || tilesQuery.error) return;
    setSelectedId(tiles.length ? tiles[0].id : NEW_TILE_ID);
  }, [selectedId, tiles, tilesQuery.isPending, tilesQuery.error]);

  useEffect(() => {
    if (!selectedId || selectedId === NEW_TILE_ID || tilesQuery.isPending || tilesQuery.error) return;
    if (tiles.some((tile) => tile.id === selectedId)) return;
    setSelectedId(tiles.length ? tiles[0].id : NEW_TILE_ID);
  }, [selectedId, tiles, tilesQuery.isPending, tilesQuery.error]);

  useEffect(() => {
    if (!selectedId) return;
    setError("");
    setForm(formFromTile(selectedId === NEW_TILE_ID ? null : selectedTile, roles));
  }, [selectedId, selectedTile, roles]);

  function selectTile(tileId) {
    setMessage("");
    setError("");
    setSelectedId(tileId);
  }

  function newTile() {
    setMessage("");
    setError("");
    setSelectedId(NEW_TILE_ID);
    setForm(formFromTile(null, roles));
  }

  function updateField(field, value) {
    setMessage("");
    setError("");
    setForm((current) => ({ ...current, [field]: value }));
  }

  function toggleRole(roleId) {
    updateField(
      "role_ids",
      form.role_ids.includes(roleId)
        ? form.role_ids.filter((id) => id !== roleId)
        : [...form.role_ids, roleId],
    );
  }

  async function save(event) {
    event.preventDefault();
    setMessage("");
    setError("");
    try {
      let tileId = selectedTile?.id;
      if (isNew) {
        const created = await createMutation.mutateAsync(tilePayload(form));
        tileId = created.item.id;
        await rolesMutation.mutateAsync({ tileId, body: { role_ids: form.role_ids } });
        setSelectedId(tileId);
        setMessage(COPY.launchpadCreated);
      } else {
        await patchMutation.mutateAsync({ tileId, body: tilePayload(form) });
        await rolesMutation.mutateAsync({ tileId, body: { role_ids: form.role_ids } });
        setMessage(COPY.launchpadSaved);
      }
    } catch (err) {
      setError(err.detail || err.message);
    }
  }

  async function removeSelected() {
    if (!selectedTile) return;
    setMessage("");
    setError("");
    try {
      await removeMutation.mutateAsync(selectedTile.id);
      setForm((current) => ({ ...current, active: false }));
      setMessage(COPY.launchpadSaved);
    } catch (err) {
      setError(err.detail || err.message);
    }
  }

  async function moveTile(from, to) {
    if (to < 0 || to >= tiles.length) return;
    const ids = tiles.map((tile) => tile.id);
    const [moved] = ids.splice(from, 1);
    ids.splice(to, 0, moved);
    setMessage("");
    setError("");
    try {
      await orderMutation.mutateAsync({ ids });
    } catch (err) {
      setError(err.detail || err.message);
    }
  }

  if (tilesQuery.isPending || rolesQuery.isPending) return <LoadingState />;

  if (tilesQuery.error || rolesQuery.error) {
    return (
      <ErrorState
        title={COPY.launchpadError}
        onRetry={() => {
          tilesQuery.refetch();
          rolesQuery.refetch();
        }}
      />
    );
  }

  return (
    <div className="launchpad-grid">
      <Panel
        title={COPY.launchpadTitle}
        action={<Button type="button" onClick={newTile}>{COPY.launchpadNew}</Button>}
      >
        <p className="launchpad-count">{totalCount} {COPY.configured}</p>
        {!tiles.length ? <EmptyState title={COPY.launchpadEmpty} /> : null}
        <div className="launchpad-list">
          {tiles.map((tile, index) => (
            <TileRow
              key={tile.id}
              tile={tile}
              selected={tile.id === selectedId}
              index={index}
              total={tiles.length}
              busy={busy}
              onSelect={selectTile}
              onMove={moveTile}
            />
          ))}
        </div>
      </Panel>

      <Panel title={isNew ? COPY.launchpadCreate : COPY.launchpadSave}>
        {!selectedId ? <EmptyState title={COPY.launchpadNoSelection} /> : null}
        {selectedId ? (
          <form className="tile-form" onSubmit={save}>
            <Field label={COPY.launchpadName}>
              <input value={form.name} onChange={(event) => updateField("name", event.target.value)} required />
            </Field>
            <Field label={COPY.launchpadLogo}>
              <input value={form.logo_key} onChange={(event) => updateField("logo_key", event.target.value)} />
            </Field>
            <Field label={COPY.launchpadGroup}>
              <input value={form.tile_group} onChange={(event) => updateField("tile_group", event.target.value)} required />
            </Field>
            <Field label={COPY.launchpadUrl}>
              <input value={form.url} onChange={(event) => updateField("url", event.target.value)} required />
            </Field>
            <Field label={COPY.launchpadAuth}>
              <select value={form.auth_type} onChange={(event) => updateField("auth_type", event.target.value)}>
                {TILE_AUTH_OPTIONS.map((option) => (
                  <option key={option.key} value={option.key}>{option.label}</option>
                ))}
              </select>
            </Field>
            <label className="tile-active">
              <input
                type="checkbox"
                checked={form.active}
                onChange={(event) => updateField("active", event.target.checked)}
              />
              <span>{COPY.launchpadActive}</span>
            </label>
            <section className="tile-role-panel">
              <h3>{COPY.launchpadRoles}</h3>
              <RoleChecks roles={roles} selectedIds={form.role_ids} onToggle={toggleRole} />
            </section>
            {message ? <p className="roster-form-message">{message}</p> : null}
            {error ? <p className="console-form-error">{error}</p> : null}
            <div className="tile-form-actions">
              <Button type="submit" tone="primary" busy={busy}>
                {isNew ? COPY.launchpadCreate : COPY.launchpadSave}
              </Button>
              {!isNew ? (
                <Button
                  type="button"
                  disabled={!selectedTile?.active}
                  busy={removeMutation.isPending}
                  onClick={removeSelected}
                >
                  {COPY.launchpadRemove}
                </Button>
              ) : null}
            </div>
          </form>
        ) : null}
      </Panel>

      <Panel title={COPY.launchpadPreview}>
        <div className="launchpad-preview">
          <div>
            <strong>{previewLoading ? COPY.loading : `${visibleCount} / ${totalCount}`}</strong>
            <span>{currentRoleName} - {COPY.launchpadVisible}</span>
          </div>
          {previewLoading ? <LoadingState /> : null}
          {!previewLoading && !previewTiles.length ? <EmptyState title={COPY.launchpadEmpty} /> : null}
          {!previewLoading && previewTiles.length ? (
            <div className="preview-tiles">
              {previewTiles.map((tile) => <PreviewTile key={tile.id} tile={tile} />)}
            </div>
          ) : null}
        </div>
      </Panel>
    </div>
  );
}
