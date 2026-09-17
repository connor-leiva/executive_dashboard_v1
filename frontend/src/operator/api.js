/* The operator API client. Bearer token only.
 *
 * NO X-Tenant-Host, ever. That header selects a TENANT realm, and the operator realm has none:
 * admin.acumyn.io resolves to no workspace by design, and every route this client calls names its
 * tenant in the path. A backend test fails if the header appears in this module.
 *
 * The session lives under its own storage key, apart from any workspace session a person might
 * also hold in this browser. The server does not rely on that (a tenant token and an operator
 * token are rejected by each other's guards), but sharing a key would mean signing in here
 * silently signed somebody out of a customer dashboard.
 */

const API = String(import.meta.env.VITE_API_BASE || "http://localhost:8000/api/v1").replace(/\/+$/, "");
const TOKEN_KEY = "cc_platform_token";
const NAME_KEY = "cc_platform_name";

function read(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function write(key, value) {
  try {
    if (value == null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch { /* storage blocked: the in-memory session still works until reload */ }
}

export const hasSession = () => Boolean(read(TOKEN_KEY));
export const operatorName = () => read(NAME_KEY) || "";

export function signOut() {
  write(TOKEN_KEY, null);
  write(NAME_KEY, null);
  window.dispatchEvent(new Event("op:signed-out"));
}

/* Errors carry the server's status and a message that is always a STRING.
 *
 * FastAPI's 422 detail is a list of objects, and the tenant console once passed an object straight
 * into JSX: React threw #31 and the whole console went blank on a validation error, the most
 * ordinary thing a form produces. Flattened here, at the source, so no view can repeat it. */
export class OpError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function detailText(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((e) => {
        const field = Array.isArray(e?.loc) ? e.loc.filter((p) => p !== "body").join(" ") : "";
        return field ? `${field}: ${e.msg}` : e?.msg || "";
      })
      .filter(Boolean)
      .join(" ");
  }
  const errors = detail && detail.errors;
  if (Array.isArray(errors)) {
    return errors.map((e) => (e.field ? `${e.field}: ${e.message}` : e.message)).filter(Boolean).join(" ");
  }
  return "";
}

async function call(path, { method = "GET", body, raw = false } = {}) {
  let res;
  try {
    res = await fetch(`${API}/platform${path}`, {
      method,
      headers: {
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(read(TOKEN_KEY) ? { Authorization: `Bearer ${read(TOKEN_KEY)}` } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new OpError(0, "The API could not be reached.");
  }
  if (res.status === 401 && path !== "/login") {
    signOut();
    throw new OpError(401, "Your operator session ended. Sign in again.");
  }
  if (raw && res.ok) return res;
  let data = null;
  try { data = await res.json(); } catch { /* an empty body */ }
  if (!res.ok) {
    throw new OpError(res.status, detailText(data && data.detail) || `Request failed (${res.status}).`);
  }
  return data;
}

export async function signIn(email, password) {
  const r = await call("/login", { method: "POST", body: { email, password } });
  write(TOKEN_KEY, r.token);
  write(NAME_KEY, r.name || r.email || "");
  return r;
}

const slugPath = (slug) => `/tenants/${encodeURIComponent(slug)}`;
const post = (path, body = {}) => call(path, { method: "POST", body });

export const api = {
  me: () => call("/me"),
  plans: () => call("/plans"),
  tenants: () => call("/tenants"),
  tenant: (slug) => call(slugPath(slug)),
  tenantAudit: (slug, limit = 200) => call(`${slugPath(slug)}/audit?limit=${limit}`),
  fleet: () => call("/fleet"),
  providers: () => call("/fleet/providers"),
  incidents: () => call("/fleet/incidents"),
  people: (slug) => call(`${slugPath(slug)}/people`),
  sources: (slug) => call(`${slugPath(slug)}/sources`),
  modules: (slug) => call(`${slugPath(slug)}/modules`),
  usage: (slug) => call(`${slugPath(slug)}/usage`),
  shareLinks: (slug) => call(`${slugPath(slug)}/share-links`),
  security: (slug) => call(`${slugPath(slug)}/security`),
  createTenant: (body) => call("/tenants", { method: "POST", body }),
  suspend: (slug, reason) => call(`${slugPath(slug)}/suspend`, { method: "POST", body: { reason } }),
  resume: (slug) => call(`${slugPath(slug)}/resume`, { method: "POST", body: {} }),
  resendOwnerInvite: (slug) => call(`${slugPath(slug)}/resend-invite`, { method: "POST", body: {} }),

  /* Phase 3: the write actions. Every one is recorded in the workspace's own audit log. */
  syncTenant: (slug) => post(`${slugPath(slug)}/sync`),
  syncSource: (slug, id) => post(`${slugPath(slug)}/sources/${encodeURIComponent(id)}/sync`),
  reconnectLink: (slug, id) => post(`${slugPath(slug)}/sources/${encodeURIComponent(id)}/reconnect-link`),
  setupLink: (slug) => post(`${slugPath(slug)}/sources/setup-link`),
  freezeSyncs: (slug, reason) => post(`${slugPath(slug)}/freeze-syncs`, { reason }),
  unfreezeSyncs: (slug) => post(`${slugPath(slug)}/unfreeze-syncs`),
  resendIdleInvites: (slug) => post(`${slugPath(slug)}/people/resend-idle`),
  resendInvite: (slug, id) => post(`${slugPath(slug)}/people/${encodeURIComponent(id)}/resend`),
  unlockPerson: (slug, id) => post(`${slugPath(slug)}/people/${encodeURIComponent(id)}/unlock`),
  sendResetLink: (slug, id) => post(`${slugPath(slug)}/people/${encodeURIComponent(id)}/reset-link`),
  revokeShareLinks: (slug) => post(`${slugPath(slug)}/share-links/revoke-all`),
};

export { call as rawCall, slugPath };
