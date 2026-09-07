import { DEFAULT_TENANT_HOST, TENANT_HOST_KEY, TOKEN_KEY } from "./constants.js";

const FALLBACK_API_BASE = "http://localhost:8000/api/v1";

function trimSlash(value) {
  return String(value || "").replace(/\/+$/, "");
}

function apiV1Base() {
  return trimSlash(import.meta.env.VITE_API_BASE || FALLBACK_API_BASE);
}

function apiRoot() {
  return apiV1Base().replace(/\/api\/v1$/, "");
}

function query(path, params) {
  const entries = Object.entries(params || {}).filter(([, value]) => value !== undefined && value !== null && value !== "");
  if (!entries.length) return path;
  return `${path}?${new URLSearchParams(entries).toString()}`;
}

function defaultTenantHost() {
  if (typeof window === "undefined") return DEFAULT_TENANT_HOST;
  const host = window.location.hostname || "";
  if (host === "localhost" || host === "127.0.0.1" || host.endsWith(".localhost")) {
    return DEFAULT_TENANT_HOST;
  }
  return host || DEFAULT_TENANT_HOST;
}

export function getTenantHost() {
  if (typeof window === "undefined") return DEFAULT_TENANT_HOST;
  return localStorage.getItem(TENANT_HOST_KEY) || defaultTenantHost();
}

export function setTenantHost(host) {
  if (typeof window === "undefined") return;
  const value = String(host || "").trim().toLowerCase();
  if (value) localStorage.setItem(TENANT_HOST_KEY, value);
  else localStorage.removeItem(TENANT_HOST_KEY);
}

export function hasToken() {
  return Boolean(typeof window !== "undefined" && localStorage.getItem(TOKEN_KEY));
}

export function logout() {
  if (typeof window === "undefined") return;
  localStorage.removeItem(TOKEN_KEY);
}

export function tenantHeaders(extra) {
  const headers = { ...(extra || {}) };
  headers["X-Tenant-Host"] = getTenantHost();
  return headers;
}

export function authHeaders(extra) {
  const headers = tenantHeaders(extra);
  const token = typeof window !== "undefined" ? localStorage.getItem(TOKEN_KEY) : null;
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

/* THE SERVER'S 422 DETAIL IS NOT A STRING. `_unprocessable` answers
   {"detail": {"errors": [{"field": ..., "message": ...}]}}, while every page in this console
   does `setError(err.detail || err.message)` and renders the result. So an object went straight
   into JSX, React threw #31 ("objects are not valid as a React child") and the ENTIRE console
   went blank — on a validation error, which is the most ordinary thing a form can produce.
   Eight call sites carried the same line, so flattening it at the source fixes all of them and
   means a page added later cannot reintroduce it. */
function detailText(detail) {
  if (typeof detail === "string") return detail;
  const errors = detail && detail.errors;
  if (Array.isArray(errors) && errors.length) {
    return errors.map((e) => e && e.message).filter(Boolean).join(" ");
  }
  return "";
}

async function throwFor(res) {
  const raw = await res.text();
  let parsed = raw;
  try {
    parsed = JSON.parse(raw).detail ?? raw;
  } catch {
    parsed = raw;
  }
  const text = detailText(parsed) || `${res.status} ${res.statusText}`;
  const err = new Error(text);
  err.status = res.status;
  err.detail = text;                                // always renderable
  // Kept structured for anything that wants to mark the offending field rather than print it.
  err.errors = (parsed && parsed.errors) || null;
  throw err;
}

async function request(base, path, options = {}) {
  const res = await fetch(`${base}${path}`, options);
  if (!res.ok) await throwFor(res);
  if (res.status === 204) return null;
  return res.json();
}

function consoleGet(path) {
  return request(apiRoot(), `/api/console${path}`, { headers: authHeaders() });
}

function consolePath(path) {
  return `${apiRoot()}/api/console${path}`;
}

function consoleSend(method, path, body) {
  return request(apiRoot(), `/api/console${path}`, {
    method,
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function consoleUpload(path, fields) {
  const form = new FormData();
  Object.entries(fields || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null) form.append(key, value);
  });
  return request(apiRoot(), `/api/console${path}`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
}

export async function login(email, password, tenantHost) {
  setTenantHost(tenantHost);
  const data = await request(apiV1Base(), "/auth/login", {
    method: "POST",
    headers: tenantHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ email, password }),
  });
  localStorage.setItem(TOKEN_KEY, data.token);
  return data;
}

export const getConfig = () => consoleGet("/config");
export const getOverview = () => consoleGet("/overview");
export const getWorkspace = () => consoleGet("/workspace");
export const patchWorkspace = (body) => consoleSend("PATCH", "/workspace", body);
export const uploadWorkspaceLogo = (kind, file) => consoleUpload("/workspace/logo", { kind, file });
export const getMarketing = () => consoleGet("/marketing");
export const getMarketingRequests = (params) => consoleGet(query("/marketing/requests", params));
export const patchMarketingRequest = (id, body) => consoleSend("PATCH", `/marketing/requests/${id}`, body);
export const patchMarketing = (body) => consoleSend("PATCH", "/marketing", body);
export const testMarketing = () => consoleSend("POST", "/marketing/test", {});
export const getSlack = () => consoleGet("/slack");
export const patchSlack = (body) => consoleSend("PATCH", "/slack", body);
export const getRoles = () => consoleGet("/roles");
export const patchRole = (roleId, body) => consoleSend("PATCH", `/roles/${roleId}`, body);
export const getPermissions = () => consoleGet("/permissions");
export const putPermissions = (body) => consoleSend("PUT", "/permissions", body);
export const getMembers = (params) => consoleGet(query("/members", params));
export const inviteMember = (body) => consoleSend("POST", "/members/invite", body);
export const patchMember = (memberId, body) => consoleSend("PATCH", `/members/${memberId}`, body);
export const deleteMember = (memberId) => consoleSend("DELETE", `/members/${memberId}`);
export const syncMembers = () => consoleSend("POST", "/members/sync");
export const getCourses = () => consoleGet("/courses");
export const getCourse = (courseId) => consoleGet(`/courses/${courseId}`);
export const createCourse = (body) => consoleSend("POST", "/courses", body);
export const patchCourse = (courseId, body) => consoleSend("PATCH", `/courses/${courseId}`, body);
export const deleteCourse = (courseId) => consoleSend("DELETE", `/courses/${courseId}`);
export const putCourseRoles = (courseId, body) => consoleSend("PUT", `/courses/${courseId}/roles`, body);
export const putLessonOrder = (courseId, body) => consoleSend("PUT", `/courses/${courseId}/lessons/order`, body);
export const createLesson = (courseId, body) => consoleSend("POST", `/courses/${courseId}/lessons`, body);
export const patchLesson = (courseId, lessonId, body) => consoleSend("PATCH", `/courses/${courseId}/lessons/${lessonId}`, body);
export const deleteLesson = (courseId, lessonId) => consoleSend("DELETE", `/courses/${courseId}/lessons/${lessonId}`);
export const getSopCategories = () => consoleGet("/sop-categories");
export const createSopCategory = (body) => consoleSend("POST", "/sop-categories", body);
export const patchSopCategory = (categoryId, body) => consoleSend("PATCH", `/sop-categories/${categoryId}`, body);
export const deleteSopCategory = (categoryId) => consoleSend("DELETE", `/sop-categories/${categoryId}`);
export const getSops = () => consoleGet("/sops");
export const getSop = (sopId) => consoleGet(`/sops/${sopId}`);
export const createSop = (body) => consoleSend("POST", "/sops", body);
export const patchSop = (sopId, body) => consoleSend("PATCH", `/sops/${sopId}`, body);
export const deleteSop = (sopId) => consoleSend("DELETE", `/sops/${sopId}`);
export const getSopVersions = (sopId) => consoleGet(`/sops/${sopId}/versions`);
export const uploadSopVersion = (sopId, versionLabel, file) => (
  consoleUpload(`/sops/${sopId}/versions`, { version_label: versionLabel, file })
);
export async function downloadSopVersion(sopId, versionId) {
  const res = await fetch(consolePath(`/sops/${sopId}/versions/${versionId}/download`), {
    headers: authHeaders(),
  });
  if (!res.ok) await throwFor(res);
  return res.blob();
}
export async function downloadMarketingAttachment(requestId, attachmentId) {
  // An authenticated fetch, not an <a href>: a bare link carries no Authorization header and
  // would 401. Same shape as downloadSopVersion, which solved this first.
  const res = await fetch(
    consolePath(`/marketing/requests/${requestId}/attachments/${attachmentId}`),
    { headers: authHeaders() });
  if (!res.ok) await throwFor(res);
  return res.blob();
}
export const getPages = () => consoleGet("/pages");
export const getPage = (pageId) => consoleGet(`/pages/${pageId}`);
export const createPage = (body) => consoleSend("POST", "/pages", body);
export const patchPage = (pageId, body) => consoleSend("PATCH", `/pages/${pageId}`, body);
export const deletePage = (pageId) => consoleSend("DELETE", `/pages/${pageId}`);
export const createPageSection = (pageId, body) =>
  consoleSend("POST", `/pages/${pageId}/sections`, body);
export const patchPageSection = (pageId, sectionId, body) =>
  consoleSend("PATCH", `/pages/${pageId}/sections/${sectionId}`, body);
export const deletePageSection = (pageId, sectionId) =>
  consoleSend("DELETE", `/pages/${pageId}/sections/${sectionId}`);
export const getWtdLists = () => consoleGet("/wtd-lists");
export const patchWtdList = (listId, body) => consoleSend("PATCH", `/wtd-lists/${listId}`, body);
export const putWtdOrder = (body) => consoleSend("PUT", "/wtd-lists/order", body);
export const getTiles = () => consoleGet("/tiles");
export const createTile = (body) => consoleSend("POST", "/tiles", body);
export const patchTile = (tileId, body) => consoleSend("PATCH", `/tiles/${tileId}`, body);
export const deleteTile = (tileId) => consoleSend("DELETE", `/tiles/${tileId}`);
export const putTileRoles = (tileId, body) => consoleSend("PUT", `/tiles/${tileId}/roles`, body);
export const putTileOrder = (body) => consoleSend("PUT", "/tiles/order", body);
export const getCalendarCategories = () => consoleGet("/calendar-categories");
export const createCalendarCategory = (body) => consoleSend("POST", "/calendar-categories", body);
export const patchCalendarCategory = (categoryId, body) => consoleSend("PATCH", `/calendar-categories/${categoryId}`, body);
export const deleteCalendarCategory = (categoryId) => consoleSend("DELETE", `/calendar-categories/${categoryId}`);
export const getGoogleSignin = () => consoleGet("/google-signin");
export const patchGoogleSignin = (body) => consoleSend("PATCH", "/google-signin", body);
export const getIntegrations = () => consoleGet("/integrations");
export const patchIntegration = (integrationId, body) => consoleSend("PATCH", `/integrations/${integrationId}`, body);
export const connectIntegration = (integrationId, body) => consoleSend("POST", `/integrations/${integrationId}/connect`, body);
export const testIntegration = (integrationId) => consoleSend("POST", `/integrations/${integrationId}/test`);
export const getAi = () => consoleGet("/ai");
export const patchAiSettings = (body) => consoleSend("PATCH", "/ai/settings", body);
export const patchAiSource = (sourceId, body) => consoleSend("PATCH", `/ai/sources/${sourceId}`, body);
export const getContentGaps = () => consoleGet("/content-gaps");
export const patchContentGap = (gapId, body) => consoleSend("PATCH", `/content-gaps/${gapId}`, body);
export const getSetupTasks = () => consoleGet("/setup-tasks");
export const patchSetupTask = (key, body) => consoleSend("PATCH", `/setup-tasks/${key}`, body);
export const getPendingChanges = () => consoleGet("/publish/pending");
export const publishChanges = (body) => consoleSend("POST", "/publish", body || {});
export const discardChanges = () => consoleSend("POST", "/publish/discard");
export const getPublishBatches = () => consoleGet("/publish/batches");
export const rollbackPublishBatch = (batchId) => consoleSend("POST", `/publish/batches/${batchId}/rollback`);
export const getAudit = (params) => consoleGet(query("/audit", params));
export const getPreview = (role) => consoleGet(query("/preview", { role }));
