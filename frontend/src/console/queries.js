import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  discardChanges,
  getAudit,
  getOverview,
  getPendingChanges,
  getPreview,
  getSetupTasks,
  login,
  patchSetupTask,
  publishChanges,
} from "./api.js";

export const keys = {
  overview: ["console", "overview"],
  setupTasks: ["console", "setup-tasks"],
  pending: ["console", "publish", "pending"],
  audit: ["console", "audit"],
  preview: (role) => ["console", "preview", role],
};

function invalidateOverview(queryClient) {
  queryClient.invalidateQueries({ queryKey: keys.overview });
  queryClient.invalidateQueries({ queryKey: keys.setupTasks });
  queryClient.invalidateQueries({ queryKey: keys.pending });
  queryClient.invalidateQueries({ queryKey: keys.audit });
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

export function useLogin() {
  return useMutation({
    mutationFn: ({ email, password, tenantHost }) => login(email, password, tenantHost),
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
