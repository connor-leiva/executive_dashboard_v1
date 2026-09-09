import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  addLessonAttachment,
  connectIntegration,
  createCalendarCategory,
  createCourse,
  createLesson,
  createPage,
  createPageSection,
  createSop,
  createSopCategory,
  createTile,
  deleteCalendarCategory,
  deleteCourse,
  deleteLesson,
  deleteLessonAttachment,
  deleteMember,
  deletePage,
  deletePageSection,
  deleteSop,
  deleteSopCategory,
  deleteTile,
  discardChanges,
  downloadSopVersion,
  getAi,
  getAiQuestions,
  getAudit,
  getCalendarCategories,
  getContentGaps,
  getCourse,
  getCourses,
  getGoogleSignin,
  getIntegrations,
  getMarketing,
  getMarketingRequests,
  getMembers,
  getOverview,
  getPage,
  getPages,
  getPendingChanges,
  getPermissions,
  getPreview,
  getRoles,
  getSetupTasks,
  getSlack,
  getSop,
  getSopCategories,
  getSops,
  getSopVersions,
  getTiles,
  getWorkspace,
  getWtdLists,
  inviteMember,
  login,
  patchAiSettings,
  patchAiSource,
  patchCalendarCategory,
  patchContentGap,
  patchCourse,
  patchGoogleSignin,
  patchIntegration,
  patchLesson,
  patchMarketing,
  patchMarketingRequest,
  patchMember,
  patchPage,
  patchPageSection,
  patchSetupTask,
  patchSlack,
  patchSop,
  patchSopCategory,
  patchTile,
  patchWorkspace,
  patchWtdList,
  publishChanges,
  putCourseRoles,
  putLessonOrder,
  createSection,
  patchSection,
  deleteSection,
  putSectionOrder,
  postLessonImage,
  putPermissions,
  putTileOrder,
  putTileRoles,
  putWtdOrder,
  syncMembers,
  testIntegration,
  testMarketing,
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
  integrations: ["console", "integrations"],
  pages: ["console", "pages"],
  googleSignin: ["console", "google-signin"],
  marketing: ["console", "marketing"],
  slack: ["console", "slack"],
  marketingRequests: ["console", "marketing-requests"],
  ai: ["console", "ai"],
  contentGaps: ["console", "content-gaps"],
  aiQuestions: ["console", "ai-questions"],
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

function invalidateIntegrations(queryClient) {
  invalidateOverview(queryClient);
  queryClient.invalidateQueries({ queryKey: keys.integrations });
}

function invalidateAi(queryClient) {
  invalidateOverview(queryClient);
  queryClient.invalidateQueries({ queryKey: keys.ai });
  queryClient.invalidateQueries({ queryKey: keys.contentGaps });
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

export function useAudit(params, enabled = true) {
  const queryParams = typeof params === "boolean" ? { limit: 6 } : (params || { limit: 6 });
  const isEnabled = typeof params === "boolean" ? params : enabled;
  return useQuery({
    queryKey: [...keys.audit, queryParams],
    queryFn: () => getAudit(queryParams),
    enabled: isEnabled,
  });
}

export function useLoadAudit() {
  return useMutation({
    mutationFn: getAudit,
  });
}

export function usePreview(role, enabled) {
  return useQuery({
    queryKey: keys.preview(role),
    queryFn: () => getPreview(role),
    enabled: enabled && Boolean(role),
  });
}

export function useMarketingRequests(enabled) {
  return useQuery({
    queryKey: keys.marketingRequests,
    queryFn: () => getMarketingRequests(),
    enabled,
  });
}

export function usePatchMarketingRequest() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }) => patchMarketingRequest(id, body),
    onSuccess: () => {
      // Status is operational, not draft config, so this invalidates the queue and the audit
      // trail but nothing publish-related -- there is nothing to publish.
      queryClient.invalidateQueries({ queryKey: keys.marketingRequests });
      queryClient.invalidateQueries({ queryKey: keys.audit });
    },
  });
}

export function useMarketing(enabled) {
  return useQuery({ queryKey: keys.marketing, queryFn: getMarketing, enabled });
}

export function usePatchMarketing() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: patchMarketing,
    onSuccess: () => {
      invalidateOverview(queryClient);
      queryClient.invalidateQueries({ queryKey: keys.marketing });
      queryClient.invalidateQueries({ queryKey: ["console", "preview"] });
    },
  });
}

export function useTestMarketing() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: testMarketing,
    // A test writes last_test_ok on the setting AND the Slack integration's status, so both
    // refetch. Without the second one the Integrations screen keeps saying "Action Needed" about
    // a connection this very click just proved works.
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.marketing });
      queryClient.invalidateQueries({ queryKey: keys.slack });
      queryClient.invalidateQueries({ queryKey: keys.integrations });
    },
  });
}

export function useSlack(enabled) {
  return useQuery({ queryKey: keys.slack, queryFn: getSlack, enabled });
}

export function usePatchSlack() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: patchSlack,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: keys.slack });
      queryClient.invalidateQueries({ queryKey: keys.integrations });
    },
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

export function useGoogleSignin(enabled) {
  return useQuery({
    queryKey: keys.googleSignin,
    queryFn: getGoogleSignin,
    enabled,
  });
}

export function usePatchGoogleSignin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body) => patchGoogleSignin(body),
    // Sign-in configuration is live the moment it saves -- it is not staged for publish like
    // content is -- so the overview's setup checklist should reflect it immediately.
    onSuccess: () => invalidateIntegrations(queryClient),
  });
}

function invalidatePages(queryClient) {
  invalidateOverview(queryClient);
  queryClient.invalidateQueries({ queryKey: keys.pages });
}

export function usePages(enabled) {
  return useQuery({ queryKey: keys.pages, queryFn: getPages, enabled });
}

export function usePage(pageId) {
  return useQuery({
    queryKey: [...keys.pages, pageId],
    queryFn: () => getPage(pageId),
    enabled: Boolean(pageId),
  });
}

export function useCreatePage() {
  const queryClient = useQueryClient();
  return useMutation({ mutationFn: createPage,
                       onSuccess: () => invalidatePages(queryClient) });
}

export function usePatchPage() {
  const queryClient = useQueryClient();
  return useMutation({ mutationFn: ({ pageId, body }) => patchPage(pageId, body),
                       onSuccess: () => invalidatePages(queryClient) });
}

export function useDeletePage() {
  const queryClient = useQueryClient();
  return useMutation({ mutationFn: deletePage,
                       onSuccess: () => invalidatePages(queryClient) });
}

export function useCreatePageSection() {
  const queryClient = useQueryClient();
  return useMutation({ mutationFn: ({ pageId, body }) => createPageSection(pageId, body),
                       onSuccess: () => invalidatePages(queryClient) });
}

export function usePatchPageSection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ pageId, sectionId, body }) => patchPageSection(pageId, sectionId, body),
    onSuccess: () => invalidatePages(queryClient),
  });
}

export function useDeletePageSection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ pageId, sectionId }) => deletePageSection(pageId, sectionId),
    onSuccess: () => invalidatePages(queryClient),
  });
}

export function useIntegrations(enabled) {
  return useQuery({
    queryKey: keys.integrations,
    queryFn: getIntegrations,
    enabled,
  });
}

export function useAi(enabled) {
  return useQuery({
    queryKey: keys.ai,
    queryFn: getAi,
    enabled,
  });
}

export function useAiQuestions(enabled) {
  return useQuery({ queryKey: keys.aiQuestions, queryFn: getAiQuestions, enabled });
}

export function useContentGaps(enabled) {
  return useQuery({
    queryKey: keys.contentGaps,
    queryFn: getContentGaps,
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

export function usePatchIntegration() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ integrationId, body }) => patchIntegration(integrationId, body),
    onSuccess: () => invalidateIntegrations(queryClient),
  });
}

export function useConnectIntegration() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ integrationId, body }) => connectIntegration(integrationId, body),
    onSuccess: () => invalidateIntegrations(queryClient),
  });
}

export function useTestIntegration() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: testIntegration,
    onSuccess: () => invalidateIntegrations(queryClient),
  });
}

export function usePatchAiSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: patchAiSettings,
    onSuccess: () => invalidateAi(queryClient),
  });
}

export function usePatchAiSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sourceId, body }) => patchAiSource(sourceId, body),
    onSuccess: () => invalidateAi(queryClient),
  });
}

export function usePatchContentGap() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ gapId, body }) => patchContentGap(gapId, body),
    onSuccess: () => invalidateAi(queryClient),
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

export function useAddLessonAttachment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ courseId, lessonId, fields }) =>
      addLessonAttachment(courseId, lessonId, fields),
    // The course detail is what carries lessons and their handouts, so that is what has to
    // refetch -- invalidating the course LIST would leave the editor showing the old list.
    onSuccess: (_data, { courseId }) => {
      queryClient.invalidateQueries({ queryKey: keys.course(courseId) });
      invalidateOverview(queryClient);
    },
  });
}

export function useDeleteLessonAttachment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ courseId, lessonId, attachmentId }) =>
      deleteLessonAttachment(courseId, lessonId, attachmentId),
    onSuccess: (_data, { courseId }) => {
      queryClient.invalidateQueries({ queryKey: keys.course(courseId) });
      invalidateOverview(queryClient);
    },
  });
}

export function useRemoveLesson() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ courseId, lessonId }) => deleteLesson(courseId, lessonId),
    onSuccess: (_data, vars) => invalidateTraining(queryClient, vars.courseId),
  });
}

export function useCreateSection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ courseId, body }) => createSection(courseId, body || {}),
    onSuccess: (_data, vars) => invalidateTraining(queryClient, vars.courseId),
  });
}

export function usePatchSection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ courseId, sectionId, body }) => patchSection(courseId, sectionId, body),
    onSuccess: (_data, vars) => invalidateTraining(queryClient, vars.courseId),
  });
}

export function useDeleteSection() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ courseId, sectionId }) => deleteSection(courseId, sectionId),
    onSuccess: (_data, vars) => invalidateTraining(queryClient, vars.courseId),
  });
}

export function useOrderSections() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ courseId, body }) => putSectionOrder(courseId, body),
    onSuccess: (_data, vars) => invalidateTraining(queryClient, vars.courseId),
  });
}

/* NOT invalidating the course. An image upload happens mid-sentence inside the editor, and a
   refetch would resync the lesson row underneath the cursor -- the body being typed is newer than
   anything the server can send back. The caller inserts the returned key itself. */
export function usePostLessonImage() {
  return useMutation({
    mutationFn: ({ courseId, lessonId, fields }) => postLessonImage(courseId, lessonId, fields),
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
