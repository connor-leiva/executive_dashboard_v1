import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createTile,
  deleteTile,
  deleteMember,
  discardChanges,
  getAudit,
  getMembers,
  getOverview,
  getPendingChanges,
  getPermissions,
  getPreview,
  getRoles,
  getSetupTasks,
  getTiles,
  inviteMember,
  login,
  patchMember,
  patchSetupTask,
  patchTile,
  publishChanges,
  putPermissions,
  putTileOrder,
  putTileRoles,
  syncMembers,
} from "./api.js";

export const keys = {
  overview: ["console", "overview"],
  setupTasks: ["console", "setup-tasks"],
  pending: ["console", "publish", "pending"],
  audit: ["console", "audit"],
  members: (params) => ["console", "members", params],
  permissions: ["console", "permissions"],
  roles: ["console", "roles"],
  tiles: ["console", "tiles"],
  preview: (role) => ["console", "preview", role],
};

function invalidateOverview(queryClient) {
  queryClient.invalidateQueries({ queryKey: keys.overview });
  queryClient.invalidateQueries({ queryKey: keys.setupTasks });
  queryClient.invalidateQueries({ queryKey: keys.pending });
  queryClient.invalidateQueries({ queryKey: keys.audit });
}

function invalidateRoster(queryClient) {
  invalidateOverview(queryClient);
  queryClient.invalidateQueries({ queryKey: ["console", "members"] });
}

function invalidateLaunchpad(queryClient) {
  invalidateOverview(queryClient);
  queryClient.invalidateQueries({ queryKey: keys.tiles });
  queryClient.invalidateQueries({ queryKey: ["console", "preview"] });
}

export function useOverview(enabled) {
  return useQuery({
    queryKey: keys.overview,
    queryFn: getOverview,
    enabled,
  });
}

export function useSetupTasks(enabled) {
  return useQuery({
    queryKey: keys.setupTasks,
    queryFn: getSetupTasks,
    enabled,
  });
}

export function usePendingChanges(enabled) {
  return useQuery({
    queryKey: keys.pending,
    queryFn: getPendingChanges,
    enabled,
  });
}

export function useAudit(enabled) {
  return useQuery({
    queryKey: keys.audit,
    queryFn: () => getAudit({ limit: 6 }),
    enabled,
  });
}

export function usePreview(role, enabled) {
  return useQuery({
    queryKey: keys.preview(role),
    queryFn: () => getPreview(role),
    enabled: enabled && Boolean(role),
  });
}

export function useMembers(params, enabled) {
  return useQuery({
    queryKey: keys.members(params),
    queryFn: () => getMembers(params),
    enabled,
  });
}

export function usePermissions(enabled) {
  return useQuery({
    queryKey: keys.permissions,
    queryFn: getPermissions,
    enabled,
  });
}

export function useRoles(enabled) {
  return useQuery({
    queryKey: keys.roles,
    queryFn: getRoles,
    enabled,
  });
}

export function useTiles(enabled) {
  return useQuery({
    queryKey: keys.tiles,
    queryFn: getTiles,
    enabled,
  });
}

export function useLogin() {
  return useMutation({
    mutationFn: ({ email, password, tenantHost }) => login(email, password, tenantHost),
  });
}

export function useInviteMember() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: inviteMember,
    onSuccess: () => invalidateRoster(queryClient),
  });
}

export function usePatchMember() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ memberId, body }) => patchMember(memberId, body),
    onSuccess: () => invalidateRoster(queryClient),
  });
}

export function useRemoveMember() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteMember,
    onSuccess: () => invalidateRoster(queryClient),
  });
}

export function useSyncMembers() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: syncMembers,
    onSuccess: () => invalidateRoster(queryClient),
  });
}

export function useSavePermissions() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: putPermissions,
    onSuccess: () => {
      invalidateOverview(queryClient);
      queryClient.invalidateQueries({ queryKey: keys.permissions });
    },
  });
}

export function useCreateTile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createTile,
    onSuccess: () => invalidateLaunchpad(queryClient),
  });
}

export function usePatchTile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ tileId, body }) => patchTile(tileId, body),
    onSuccess: () => invalidateLaunchpad(queryClient),
  });
}

export function useRemoveTile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteTile,
    onSuccess: () => invalidateLaunchpad(queryClient),
  });
}

export function useSaveTileRoles() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ tileId, body }) => putTileRoles(tileId, body),
    onSuccess: () => invalidateLaunchpad(queryClient),
  });
}

export function useOrderTiles() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: putTileOrder,
    onSuccess: () => invalidateLaunchpad(queryClient),
  });
}

export function usePatchSetupTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ key, completed }) => patchSetupTask(key, { completed }),
    onSuccess: () => invalidateOverview(queryClient),
  });
}

export function usePublish() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: publishChanges,
    onSuccess: () => invalidateOverview(queryClient),
  });
}

export function useDiscardPending() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: discardChanges,
    onSuccess: () => invalidateOverview(queryClient),
  });
}
