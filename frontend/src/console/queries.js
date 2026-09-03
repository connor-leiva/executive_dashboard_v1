import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createCalendarCategory,
  createCourse,
  createLesson,
  createSop,
  createSopCategory,
  createTile,
  deleteCalendarCategory,
  deleteCourse,
  deleteLesson,
  deleteSop,
  deleteSopCategory,
  deleteTile,
  deleteMember,
  discardChanges,
  downloadSopVersion,
  getAudit,
  getCalendarCategories,
  getCourse,
  getCourses,
  getMembers,
  getOverview,
  getPendingChanges,
  getPermissions,
  getPreview,
  getRoles,
  getSetupTasks,
  getSop,
  getSopCategories,
  getSops,
  getSopVersions,
  getTiles,
  getWorkspace,
  getWtdLists,
  inviteMember,
  login,
  patchCalendarCategory,
  patchMember,
  patchSetupTask,
  patchSop,
  patchSopCategory,
  patchTile,
  patchCourse,
  patchLesson,
  patchWorkspace,
  patchWtdList,
  publishChanges,
  putCourseRoles,
  putLessonOrder,
  putPermissions,
  putTileOrder,
  putTileRoles,
  putWtdOrder,
  syncMembers,
  uploadSopVersion,
  uploadWorkspaceLogo,
} from "./api.js";

export const keys = {
  overview: ["console", "overview"],
  setupTasks: ["console", "setup-tasks"],
  pending: ["console", "publish", "pending"],
  audit: ["console", "audit"],
  workspace: ["console", "workspace"],
  calendarCategories: ["console", "calendar-categories"],
  members: (params) => ["console", "members", params],
  permissions: ["console", "permissions"],
  roles: ["console", "roles"],
  tiles: ["console", "tiles"],
  courses: ["console", "courses"],
  course: (courseId) => ["console", "courses", courseId],
  sopCategories: ["console", "sop-categories"],
  sops: ["console", "sops"],
  sop: (sopId) => ["console", "sops", sopId],
  sopVersions: (sopId) => ["console", "sops", sopId, "versions"],
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

function invalidateWorkspace(queryClient) {
  invalidateOverview(queryClient);
  queryClient.invalidateQueries({ queryKey: keys.workspace });
  queryClient.invalidateQueries({ queryKey: ["console", "preview"] });
}

function invalidateCalendar(queryClient) {
  invalidateOverview(queryClient);
  queryClient.invalidateQueries({ queryKey: keys.calendarCategories });
  queryClient.invalidateQueries({ queryKey: keys.workspace });
  queryClient.invalidateQueries({ queryKey: ["console", "preview"] });
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

function invalidateSops(queryClient, sopId) {
  invalidateOverview(queryClient);
  queryClient.invalidateQueries({ queryKey: keys.sops });
  queryClient.invalidateQueries({ queryKey: keys.sopCategories });
  if (sopId) {
    queryClient.invalidateQueries({ queryKey: keys.sop(sopId) });
    queryClient.invalidateQueries({ queryKey: keys.sopVersions(sopId) });
  }
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

export function useWorkspace(enabled) {
  return useQuery({
    queryKey: keys.workspace,
    queryFn: getWorkspace,
    enabled,
  });
}

export function useCalendarCategories(enabled) {
  return useQuery({
    queryKey: keys.calendarCategories,
    queryFn: getCalendarCategories,
    enabled,
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

export function useSops(enabled) {
  return useQuery({
    queryKey: keys.sops,
    queryFn: getSops,
    enabled,
  });
}

export function useSop(sopId, enabled) {
  return useQuery({
    queryKey: keys.sop(sopId),
    queryFn: () => getSop(sopId),
    enabled: enabled && Boolean(sopId),
  });
}

export function useSopCategories(enabled) {
  return useQuery({
    queryKey: keys.sopCategories,
    queryFn: getSopCategories,
    enabled,
  });
}

export function useSopVersions(sopId, enabled) {
  return useQuery({
    queryKey: keys.sopVersions(sopId),
    queryFn: () => getSopVersions(sopId),
    enabled: enabled && Boolean(sopId),
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

export function usePatchWorkspace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: patchWorkspace,
    onSuccess: () => invalidateWorkspace(queryClient),
  });
}

export function useUploadWorkspaceLogo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ kind, file }) => uploadWorkspaceLogo(kind, file),
    onSuccess: () => invalidateWorkspace(queryClient),
  });
}

export function useCreateCalendarCategory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createCalendarCategory,
    onSuccess: () => invalidateCalendar(queryClient),
  });
}

export function usePatchCalendarCategory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ categoryId, body }) => patchCalendarCategory(categoryId, body),
    onSuccess: () => invalidateCalendar(queryClient),
  });
}

export function useRemoveCalendarCategory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteCalendarCategory,
    onSuccess: () => invalidateCalendar(queryClient),
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

export function useCreateSopCategory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createSopCategory,
    onSuccess: () => invalidateSops(queryClient),
  });
}

export function usePatchSopCategory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ categoryId, body }) => patchSopCategory(categoryId, body),
    onSuccess: () => invalidateSops(queryClient),
  });
}

export function useRemoveSopCategory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteSopCategory,
    onSuccess: () => invalidateSops(queryClient),
  });
}

export function useCreateSop() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createSop,
    onSuccess: () => invalidateSops(queryClient),
  });
}

export function usePatchSop() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sopId, body }) => patchSop(sopId, body),
    onSuccess: (_data, vars) => invalidateSops(queryClient, vars.sopId),
  });
}

export function useArchiveSop() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteSop,
    onSuccess: (_data, sopId) => invalidateSops(queryClient, sopId),
  });
}

export function useUploadSopVersion() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sopId, versionLabel, file }) => uploadSopVersion(sopId, versionLabel, file),
    onSuccess: (_data, vars) => invalidateSops(queryClient, vars.sopId),
  });
}

export function useDownloadSopVersion() {
  return useMutation({
    mutationFn: ({ sopId, versionId }) => downloadSopVersion(sopId, versionId),
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
