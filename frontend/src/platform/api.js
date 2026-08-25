/* Operator API client — a DIFFERENT REALM from the tenant app, and kept apart on purpose.
 *
 * The operator token lives under its own storage key. If it shared `cc_token` with the tenant
 * session, signing into the console would silently end the operator's tenant session and vice
 * versa, and a stray operator token would be sent on every dashboard request. They are separate
 * credentials for separate realms, so they get separate storage.
 *
 * The server does not rely on any of that: a tenant token carries `tid` and no `pu`, a platform
 * token carries `pu` and no `tid`, and each guard rejects the other shape (backend app/deps.py).
 * Keeping them apart here is about not confusing the human, not about enforcement.
 */
const API = import.meta.env.VITE_API_BASE;
const TOKEN_KEY = "cc_platform_token";
const NAME_KEY = "cc_platform_name";

export const PLATFORM_API_BASE = API;
export const opToken = () => localStorage.getItem(TOKEN_KEY);
export const opName = () => localStorage.getItem(NAME_KEY) || "";
export const hasOpToken = () => Boolean(opToken());

export function clearOpSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(NAME_KEY);
}

/* Errors carry the server's status AND its message. The tenant login collapses every failure
 * into "check your email and password", which is exactly how a tenant-resolution outage reached
 * the operator as a mystery instead of as an error. Not repeating that here. */
export class OpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function call(path, { method = "GET", body } = {}) {
  if (!API) throw new OpError(0, "No API base is configured for this build.");
  let res;
  try {
    res = await fetch(`${API}/platform${path}`, {
      method,
      headers: {
        ...(body ? { "Content-Type": "application/json" } : {}),
        ...(opToken() ? { Authorization: `Bearer ${opToken()}` } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new OpError(0, "Could not reach the API.");
  }
  if (res.status === 401 && path !== "/login") {
    clearOpSession();
    throw new OpError(401, "Your operator session expired. Sign in again.");
  }
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new OpError(res.status, (data && data.detail) || `Request failed (${res.status}).`);
  return data;
}

export async function opLogin(email, password) {
  const r = await call("/login", { method: "POST", body: { email, password } });
  localStorage.setItem(TOKEN_KEY, r.token);
  localStorage.setItem(NAME_KEY, r.name || r.email || "");
  return r;
}

export const listTenants = () => call("/tenants");
export const getTenant = (slug) => call(`/tenants/${encodeURIComponent(slug)}`);
export const tenantAudit = (slug) => call(`/tenants/${encodeURIComponent(slug)}/audit?limit=50`);
export const createTenant = (body) => call("/tenants", { method: "POST", body });
export const suspendTenant = (slug) => call(`/tenants/${encodeURIComponent(slug)}/suspend`, { method: "POST" });
export const resumeTenant = (slug) => call(`/tenants/${encodeURIComponent(slug)}/resume`, { method: "POST" });
export const resendInvite = (slug) => call(`/tenants/${encodeURIComponent(slug)}/resend-invite`, { method: "POST" });
