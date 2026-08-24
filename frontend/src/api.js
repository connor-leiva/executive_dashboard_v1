const API = import.meta.env.VITE_API_BASE; // e.g. https://api.springb.com/api/v1
const TOKEN_KEY = "cc_token";

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

function authHeaders(path, extra) {
  const token = localStorage.getItem(TOKEN_KEY);
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

export async function login(email, password) {
  const res = await fetch(`${API}/auth/login`, {
    method: "POST",
    headers: tenantHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new Error("Login failed");
  const { token } = await res.json();
  localStorage.setItem(TOKEN_KEY, token);
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

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function logout() {
  localStorage.removeItem(TOKEN_KEY);
  // Never leave a section unlocked for whoever logs in next on this machine.
  Object.keys(sessionStorage).filter((k) => k.startsWith("cc_stepup_"))
    .forEach((k) => sessionStorage.removeItem(k));
}

export function hasToken() {
  return Boolean(localStorage.getItem(TOKEN_KEY));
}
