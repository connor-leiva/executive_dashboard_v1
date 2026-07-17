const API = import.meta.env.VITE_API_BASE; // e.g. https://api.springb.com/api/v1
const TOKEN_KEY = "cc_token";

// Turn a non-2xx response into an Error carrying both the HTTP status and the
// server's `detail` string, so callers can show the real reason ("A user with
// that email already exists") instead of a generic message.
async function throwFor(res) {
  const raw = await res.text();
  let detail = raw;
  try { detail = JSON.parse(raw).detail ?? raw; } catch { /* not JSON */ }
  const err = new Error(`${res.status} ${detail}`);
  err.status = res.status;
  err.detail = detail;
  throw err;
}

export async function getJSON(path) {
  const token = localStorage.getItem(TOKEN_KEY);
  const res = await fetch(`${API}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) await throwFor(res);
  return res.json();
}

// Fetch a binary response (e.g. a stored document) with auth, as a Blob the caller can turn
// into an object URL for inline preview. Same error surfacing as getJSON.
export async function getBlob(path) {
  const token = localStorage.getItem(TOKEN_KEY);
  const res = await fetch(`${API}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) await throwFor(res);
  return res.blob();
}

export async function postJSON(path, body) {
  const token = localStorage.getItem(TOKEN_KEY);
  const res = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) await throwFor(res);
  return res.json();
}

export async function putJSON(path, body) {
  const token = localStorage.getItem(TOKEN_KEY);
  const res = await fetch(`${API}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) await throwFor(res);
  return res.json();
}

export async function login(email, password) {
  const res = await fetch(`${API}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new Error("Login failed");
  const { token } = await res.json();
  localStorage.setItem(TOKEN_KEY, token);
  return token;
}

export async function patchJSON(path, body) {
  const token = localStorage.getItem(TOKEN_KEY);
  const res = await fetch(`${API}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
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
  const token = localStorage.getItem(TOKEN_KEY);
  const res = await fetch(`${API}${path}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) await throwFor(res);
  return res.json();
}

// Public endpoints (accept-invite / reset-password) — no auth header, and surface
// the server's error message so expired-link guidance reaches the user.
export async function postPublic(path, body) {
  const res = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
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

export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function logout() {
  localStorage.removeItem(TOKEN_KEY);
}

export function hasToken() {
  return Boolean(localStorage.getItem(TOKEN_KEY));
}
