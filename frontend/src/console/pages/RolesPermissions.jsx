import { useEffect, useMemo, useState } from "react";

import {
  CONSOLE_ACCESS_KEY,
  CONSOLE_ACCESS_LEVELS,
  COPY,
  DEFAULT_PERMISSION_LEVEL,
  LEVEL_CLASS,
  PERMISSION_LEVELS,
} from "../constants.js";
import { usePermissions, useSavePermissions } from "../queries.js";
import { Button, EmptyState, ErrorState, LoadingState, Panel } from "../ui.jsx";

function pairKey(capabilityId, roleId) {
  return `${capabilityId}:${roleId}`;
}

function nextLevel(current, capabilityKey) {
  const levels = capabilityKey === CONSOLE_ACCESS_KEY ? CONSOLE_ACCESS_LEVELS : PERMISSION_LEVELS;
  const currentIndex = levels.indexOf(current);
  return levels[(currentIndex + 1) % levels.length];
}

function matrixMap(items) {
  const out = new Map();
  for (const item of items || []) {
    out.set(pairKey(item.capability_id, item.role_id), item);
  }
  return out;
}

export default function RolesPermissions() {
  const permissionsQuery = usePermissions(true);
  const saveMutation = useSavePermissions();
  const [dirty, setDirty] = useState({});
  const [message, setMessage] = useState("");
  const data = permissionsQuery.data;
  const roles = data?.roles || [];
  const capabilities = data?.capabilities || [];
  const original = useMemo(() => matrixMap(data?.items || []), [data?.items]);
  const dirtyCount = Object.keys(dirty).length;

  useEffect(() => {
    setDirty({});
  }, [data?.items]);

  function originalLevel(capabilityId, roleId) {
    return original.get(pairKey(capabilityId, roleId))?.level || DEFAULT_PERMISSION_LEVEL;
  }

  function levelFor(capabilityId, roleId) {
    const key = pairKey(capabilityId, roleId);
    return dirty[key] || originalLevel(capabilityId, roleId);
  }

  function cycle(capability, role) {
    const key = pairKey(capability.id, role.id);
    const next = nextLevel(levelFor(capability.id, role.id), capability.key);
    const initial = originalLevel(capability.id, role.id);
    setMessage("");
    setDirty((current) => {
      const updated = { ...current };
      if (next === initial) delete updated[key];
      else updated[key] = next;
      return updated;
    });
  }

  async function save() {
    setMessage("");
    const items = capabilities.flatMap((capability) => roles.map((role) => ({
      capability_id: capability.id,
      role_id: role.id,
      level: levelFor(capability.id, role.id),
    })));
    try {
      await saveMutation.mutateAsync({ items });
      setDirty({});
      setMessage(COPY.permissionsSaved);
    } catch (err) {
      setMessage(err.detail || err.message);
    }
  }

  return (
    <Panel
      title={COPY.permissionsTitle}
      action={(
        <Button
          type="button"
          tone="primary"
          disabled={!dirtyCount}
          busy={saveMutation.isPending}
          onClick={save}
        >
          {saveMutation.isPending ? COPY.permissionsSaving : COPY.permissionsSave}
        </Button>
      )}
    >
      {permissionsQuery.isPending ? <LoadingState /> : null}
      {permissionsQuery.error ? <ErrorState title={COPY.permissionsError} onRetry={() => permissionsQuery.refetch()} /> : null}
      {!permissionsQuery.isPending && !permissionsQuery.error && (!roles.length || !capabilities.length) ? (
        <EmptyState title={COPY.permissionsEmpty} />
      ) : null}
      {!permissionsQuery.isPending && !permissionsQuery.error && roles.length && capabilities.length ? (
        <>
          <div className={`permissions-savebar ${dirtyCount ? "dirty" : ""}`}>
            <strong>{dirtyCount ? COPY.permissionsDirty : COPY.permissionsClean}</strong>
            {message ? <span>{message}</span> : null}
          </div>
          <div className="permissions-table-wrap">
            <table className="permissions-table">
              <thead>
                <tr>
                  <th>{COPY.permissionsCapability}</th>
                  {roles.map((role) => (
                    <th key={role.id}>{role.name}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {capabilities.map((capability) => (
                  <tr key={capability.id}>
                    <th scope="row">
                      <span>{capability.name}</span>
                      <small>{capability.description}</small>
                    </th>
                    {roles.map((role) => {
                      const level = levelFor(capability.id, role.id);
                      const key = pairKey(capability.id, role.id);
                      return (
                        <td key={role.id}>
                          <button
                            type="button"
                            className={`permission-cell level-${LEVEL_CLASS[level]} ${dirty[key] ? "dirty" : ""}`}
                            onClick={() => cycle(capability, role)}
                          >
                            {level}
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </Panel>
  );
}
