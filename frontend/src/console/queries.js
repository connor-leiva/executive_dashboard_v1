import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createCourse,
  createLesson,
  createTile,
  deleteCourse,
  deleteLesson,
  deleteTile,
  deleteMember,
  discardChanges,
  getAudit,
  getCourse,
  getCourses,
  getMembers,
  getOverview,
  getPendingChanges,
  getPermissions,
  getPreview,
  getRoles,
  getSetupTasks,
  getTiles,
  getWtdLists,
  inviteMember,
  login,
  patchMember,
  patchSetupTask,
  patchTile,
  patchCourse,
  patchLesson,
  patchWtdList,
  publishChanges,
  putCourseRoles,
  putLessonOrder,
  putPermissions,
  putTileOrder,
  putTileRoles,
  putWtdOrder,
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
  courses: ["console", "courses"],
  course: (courseId) => ["console", "courses", courseId],
  wtdLists: ["console", "wtd-lists"],
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

function invalidateWtd(queryClient) {
  invalidateOverview(queryClient);
  queryClient.invalidateQueries({ queryKey: keys.wtdLists });
  queryClient.invalidateQueries({ queryKey: ["console", "preview"] });
}

function invalidateTraining(queryClient, courseId) {
  invalidateOverview(queryClient);
  queryClient.invalidateQueries({ queryKey: keys.courses });
  if (courseId) queryClient.invalidateQueries({ queryKey: keys.course(courseId) });
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

export function useWtdLists(enabled) {
  return useQuery({
    queryKey: keys.wtdLists,
    queryFn: getWtdLists,
    enabled,
  });
}

export function useCourses(enabled) {
  return useQuery({
    queryKey: keys.courses,
    queryFn: getCourses,
    enabled,
  });
}

export function useCourse(courseId, enabled) {
  return useQuery({
    queryKey: keys.course(courseId),
    queryFn: () => getCourse(courseId),
    enabled: enabled && Boolean(courseId),
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

export function usePatchWtdList() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ listId, body }) => patchWtdList(listId, body),
    onSuccess: () => invalidateWtd(queryClient),
  });
}

export function useOrderWtdLists() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: putWtdOrder,
    onSuccess: () => invalidateWtd(queryClient),
  });
}

export function useCreateCourse() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createCourse,
    onSuccess: () => invalidateTraining(queryClient),
  });
}

export function usePatchCourse() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ courseId, body }) => patchCourse(courseId, body),
    onSuccess: (_data, vars) => invalidateTraining(queryClient, vars.courseId),
  });
}

export function useArchiveCourse() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteCourse,
    onSuccess: (_data, courseId) => invalidateTraining(queryClient, courseId),
  });
}

export function useSaveCourseRoles() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ courseId, body }) => putCourseRoles(courseId, body),
    onSuccess: (_data, vars) => invalidateTraining(queryClient, vars.courseId),
  });
}

export function useCreateLesson() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ courseId, body }) => createLesson(courseId, body),
    onSuccess: (_data, vars) => invalidateTraining(queryClient, vars.courseId),
  });
}

export function usePatchLesson() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ courseId, lessonId, body }) => patchLesson(courseId, lessonId, body),
    onSuccess: (_data, vars) => invalidateTraining(queryClient, vars.courseId),
  });
}

export function useRemoveLesson() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ courseId, lessonId }) => deleteLesson(courseId, lessonId),
    onSuccess: (_data, vars) => invalidateTraining(queryClient, vars.courseId),
  });
}

export function useOrderLessons() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ courseId, body }) => putLessonOrder(courseId, body),
    onSuccess: (_data, vars) => invalidateTraining(queryClient, vars.courseId),
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
