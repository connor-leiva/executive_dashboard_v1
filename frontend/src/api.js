const API = import.meta.env.VITE_API_BASE; // e.g. https://api.springb.com/api/v1
const TOKEN_KEY = "cc_token";

/* WHERE THE SESSION LIVES, and why it is not always the same place.
 *
 * "Remember me" is a promise about a shared machine, so honouring it only on the server would
 * be honouring half of it: a short-lived token still sitting in localStorage is readable by
 * whoever opens the browser next. Unchecked, the token goes in sessionStorage and dies with
 * the tab; checked, it goes in localStorage and the server gives it a month.
 *
 * Reads try sessionStorage first, because it is the more recent decision: signing in without
 * the box on a machine where somebody once ticked it must not resurrect the old long session.
 * setToken clears both before writing for the same reason.
 *
 * Every read goes through getToken(). Two components used to call localStorage.getItem
 * directly, which would have worked for exactly as long as there was only one place to look.
 */
export function getToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;                     // private window, or storage blocked entirely
  }
}

function storeToken(token, remember) {
  try { localStorage.removeItem(TOKEN_KEY); } catch { /* nothing to clear */ }
  try { sessionStorage.removeItem(TOKEN_KEY); } catch { /* nothing to clear */ }
  try {
    (remember ? localStorage : sessionStorage).setItem(TOKEN_KEY, token);
  } catch {
    // Storage refused. The in-memory session still works until the page reloads, which is a
    // better outcome than throwing out of a successful sign-in.
  }
}

/* ── step-up (second factor) grants ──────────────────────────────────────────────
   A section behind a second factor (Binder) answers 428 until the request carries a
   live grant. Grants live in sessionStorage, NOT localStorage: closing the tab
   re-locks the section, and the grant never outlives the browsing session. */
const stepUpKey = (scope) => `cc_stepup_${scope}`;
export const setStepUp = (scope, token) => sessionStorage.setItem(stepUpKey(scope), token);
export const getStepUp = (scope) => sessionStorage.getItem(stepUpKey(scope));
export const clearStepUp = (scope) => sessionStorage.removeItem(stepUpKey(scope));
export const STEP_UP_STATUS = 428;

/* Which step-up scope a request belongs to. The assistant is included on purpose: it can
   read Binder data, so it must carry the same proof (the server decides what to include). */
function scopeForPath(path) {
  if (path.startsWith("/binder")) return "binder";
  if (path.startsWith("/assistant")) return "binder";
  return null;
}

/* ── which tenant this browser is ─────────────────────────────────────────────────
   The SPA is served from the tenant's own host but calls the API on a different origin,
   so the API's `Host` header names the API, not the tenant. Every request therefore
   declares the hostname the app was loaded from, and the server resolves the tenant from
   that (see backend app/tenancy.py). Unauthenticated calls need it MOST: login has no
   token to read a tenant from, so without this header it would authenticate against
   whichever tenant the server happened to fall back to. */
export function tenantHeaders(extra) {
  const h = { ...(extra || {}) };
  if (typeof window !== "undefined" && window.location && window.location.hostname) {
    h["X-Tenant-Host"] = window.location.hostname;
  }
  return h;
}

// Exported so a multipart upload (which cannot go through postJSON's JSON body) still sends
// exactly the same headers — a second copy would miss the step-up grant or the tenant host
// the first time either changes.
export function authHeaders(path, extra) {
  const token = getToken();
  const h = { ...tenantHeaders(extra), Authorization: `Bearer ${token}` };
  const scope = scopeForPath(path);
  const grant = scope ? getStepUp(scope) : null;
  if (grant) h["X-Step-Up"] = grant;
  return h;
}

export const API_BASE = API;

// Absolute URL for a server-relative media/file path (the API already returns a token-gated
// query string), so an <img src> can load it directly. Null-safe for the no-API sample mode.
export function fileUrl(relPath) {
  return relPath ? `${API}${relPath}` : null;
}

// Multipart upload (media library). No Content-Type header — the browser sets the boundary.
export async function uploadFile(path, formData) {
  const res = await fetch(`${API}${path}`, {
    method: "POST",
    headers: authHeaders(path),
    body: formData,
  });
  if (!res.ok) await throwFor(res, path);
  return res.json();
}

// Turn a non-2xx response into an Error carrying both the HTTP status and the
// server's `detail` string, so callers can show the real reason ("A user with
// that email already exists") instead of a generic message.
async function throwFor(res, path) {
  const raw = await res.text();
  let detail = raw;
  try { detail = JSON.parse(raw).detail ?? raw; } catch { /* not JSON */ }
  const err = new Error(`${res.status} ${detail}`);
  err.status = res.status;
  err.detail = detail;
  // A grant can expire mid-session. Drop it and tell the gate to re-lock, so the user gets
  // the code prompt instead of a dead screen full of errors.
  if (res.status === STEP_UP_STATUS) {
    const scope = scopeForPath(path || "");
    if (scope) {
      clearStepUp(scope);
      window.dispatchEvent(new CustomEvent("cc:step-up-required", { detail: { scope } }));
    }
  }
  throw err;
}

export async function getJSON(path) {
  const res = await fetch(`${API}${path}`, {
    headers: authHeaders(path),
  });
  if (!res.ok) await throwFor(res, path);
  return res.json();
}

// Fetch a binary response (e.g. a stored document) with auth, as a Blob the caller can turn
// into an object URL for inline preview. Same error surfacing as getJSON.
export async function getBlob(path) {
  const res = await fetch(`${API}${path}`, {
    headers: authHeaders(path),
  });
  if (!res.ok) await throwFor(res, path);
  return res.blob();
}

export async function postJSON(path, body) {
  const res = await fetch(`${API}${path}`, {
    method: "POST",
    headers: authHeaders(path, { "Content-Type": "application/json" }),
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) await throwFor(res, path);
  return res.json();
}

export async function putJSON(path, body) {
  const res = await fetch(`${API}${path}`, {
    method: "PUT",
    headers: authHeaders(path, { "Content-Type": "application/json" }),
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) await throwFor(res, path);
  return res.json();
}

export async function login(email, password, remember = false) {
  const res = await fetch(`${API}/auth/login`, {
    method: "POST",
    headers: tenantHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ email, password, remember }),
  });
  if (!res.ok) {
    // Carry the status: 423 is a lockout and 403 a suspended workspace, and the sign-in screen
    // says something different for each. It used to throw one bare "Login failed" for all three,
    // so a locked-out person was told to check a password that was correct.
    const data = await res.json().catch(() => ({}));
    const err = new Error(data.detail || `${res.status}`);
    err.status = res.status;
    err.detail = data.detail;
    throw err;
  }
  const { token } = await res.json();
  storeToken(token, remember);
  return token;
}

export async function patchJSON(path, body) {
  const res = await fetch(`${API}${path}`, {
    method: "PATCH",
    headers: authHeaders(path, { "Content-Type": "application/json" }),
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = new Error(`${res.status} ${await res.text()}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

export async function delJSON(path) {
  const res = await fetch(`${API}${path}`, {
    method: "DELETE",
    headers: authHeaders(path),
  });
  if (!res.ok) await throwFor(res, path);
  return res.json();
}

// Public endpoints (accept-invite / reset-password) — no auth header, and surface
// the server's error message so expired-link guidance reaches the user.
export async function postPublic(path, body) {
  const res = await fetch(`${API}${path}`, {
    method: "POST",
    headers: tenantHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.detail || `${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

// Public GET (no auth header) — for token-scoped share endpoints an embed viewer hits without login.
export async function getPublic(path) {
  const res = await fetch(`${API}${path}`, { headers: tenantHeaders() });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.detail || `${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

export function setToken(token, remember = true) {
  storeToken(token, remember);
}

export function logout() {
  try { localStorage.removeItem(TOKEN_KEY); } catch { /* nothing to clear */ }
  try { sessionStorage.removeItem(TOKEN_KEY); } catch { /* nothing to clear */ }
  // Never leave a section unlocked for whoever logs in next on this machine.
  Object.keys(sessionStorage).filter((k) => k.startsWith("cc_stepup_"))
    .forEach((k) => sessionStorage.removeItem(k));
}

export function hasToken() {
  return Boolean(getToken());
}
