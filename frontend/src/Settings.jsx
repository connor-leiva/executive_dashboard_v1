import { useEffect, useState } from "react";
import { Routes, Route, Navigate, NavLink, Link } from "react-router-dom";
import { T, PROVIDER_NAME, relativeTime } from "./theme.js";
import { getJSON, postJSON, putJSON } from "./api.js";

const API_BASE = import.meta.env.VITE_API_BASE;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const STATUS_DOT = { connected: T.meadow, error: T.poppy, disconnected: T.muted };
const STATUS_LABEL = { connected: "Connected", error: "Error", disconnected: "Disconnected" };

// Shown in the dev/sample preview when there's no API to query.
const SAMPLE_INTEGRATIONS = [
  { id: "s", provider: "sisu", business_key: "ulrg", status: "connected", last_synced_at: new Date(Date.now() - 240000).toISOString() },
  { id: "f", provider: "fub", business_key: "ulrg", status: "disconnected", last_synced_at: null },
  { id: "g", provider: "ghl", business_key: "springb", status: "disconnected", last_synced_at: null },
  { id: "q", provider: "qbo", business_key: "ulrg", status: "disconnected", last_synced_at: null },
  { id: "a", provider: "arive", business_key: "sympli", status: "disconnected", last_synced_at: null },
];

function providerLabel(row) {
  const name = PROVIDER_NAME[row.provider] || row.provider;
  return row.provider === "qbo" && row.business_key ? `${name} · ${row.business_key}` : name;
}

// Token-based sources that should always be offer-able even if no row exists
// yet (the Connect form creates the integration on first connect).
const CONNECTABLE = [
  { provider: "ghl", business_key: "springb" },
  { provider: "arive", business_key: "sympli" },
];

function ensureProviders(rows) {
  const out = [...rows];
  for (const req of CONNECTABLE) {
    if (!out.some((r) => r.provider === req.provider)) {
      out.push({ id: `new-${req.provider}`, provider: req.provider, business_key: req.business_key, status: "disconnected", last_synced_at: null });
    }
  }
  return out;
}

/* ── shell ─────────────────────────────────────────────────── */

const SUBNAV = [
  { to: "/settings/integrations", label: "Integrations" },
  { to: "/settings/account", label: "Account" },
  { to: "/settings/businesses", label: "Businesses" },
];

function SettingsShell({ children }) {
  return (
    <div style={{ background: T.parchment, minHeight: "100vh", fontFamily: "Inter,sans-serif" }}>
      <style>{`@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&family=Inter:wght@400;500;600&family=Sacramento&display=swap');`}</style>
      <div style={{
        display: "flex", alignItems: "center", gap: 14, padding: "14px 26px",
        borderBottom: `1px solid ${T.line}`, background: T.white,
      }}>
        <Link to="/" style={{ fontFamily: "Inter,sans-serif", fontSize: 13, fontWeight: 600, color: T.slate, textDecoration: "none" }}>← Command Center</Link>
        <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 16, fontWeight: 600, color: T.ink }}>Settings</span>
      </div>
      <div style={{ display: "flex", gap: 26, padding: 26, maxWidth: 1000 }}>
        <nav style={{ width: 170, flexShrink: 0, display: "flex", flexDirection: "column", gap: 2 }}>
          {SUBNAV.map((n) => (
            <NavLink key={n.to} to={n.to} style={({ isActive }) => ({
              fontFamily: "Inter,sans-serif", fontSize: 13.5, fontWeight: isActive ? 600 : 500,
              color: isActive ? T.ink : T.slate, textDecoration: "none",
              background: isActive ? T.white : "transparent", border: `1px solid ${isActive ? T.line : "transparent"}`,
              borderRadius: 8, padding: "9px 12px",
            })}>{n.label}</NavLink>
          ))}
        </nav>
        <div style={{ flex: 1, minWidth: 0 }}>{children}</div>
      </div>
    </div>
  );
}

function Card({ title, hint, children }) {
  return (
    <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 14, padding: 22, marginBottom: 18 }}>
      {title && <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 15, fontWeight: 600, color: T.ink }}>{title}</div>}
      {hint && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.muted, marginTop: 3, marginBottom: 14 }}>{hint}</div>}
      {children}
    </div>
  );
}

function btn(kind) {
  const base = {
    fontFamily: "Inter,sans-serif", fontSize: 12.5, fontWeight: 600, borderRadius: 8,
    padding: "6px 12px", cursor: "pointer", border: "none",
  };
  if (kind === "primary") return { ...base, color: T.onDark, background: T.evergreen };
  if (kind === "danger") return { ...base, color: T.poppyText, background: T.white, border: `1px solid ${T.line}` };
  if (kind === "disabled") return { ...base, color: T.muted, background: T.parchment, border: `1px solid ${T.line}`, cursor: "not-allowed" };
  return { ...base, color: T.slate, background: T.parchment, border: `1px solid ${T.line}` };
}

/* ── Go High Level connect form ────────────────────────────── */

const GHL_DEFAULT_TAGS = "inner circle active, the forum active, forumadmin, member: secondary, inner circle active add on";

function GhlConnectForm({ row, onClose, onDone }) {
  const cfg = row.config || {};
  const editing = row.status === "connected" || row.status === "error";
  const [token, setToken] = useState("");
  const [locationId, setLocationId] = useState(cfg.location_id || "");
  const [tags, setTags] = useState((cfg.member_tags && cfg.member_tags.join(", ")) || GHL_DEFAULT_TAGS);
  const [calendarId, setCalendarId] = useState(cfg.forum_calendar_id || "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const member_tags = tags.split(",").map((t) => t.trim()).filter(Boolean);
      await postJSON("/integrations", {
        provider: "ghl", business_key: row.business_key || "springb",
        token: token.trim() || undefined,   // blank on edit = keep the current key
        config: {
          location_id: locationId.trim(), member_tags,
          forum_tags: cfg.forum_tags || [], becollective_tags: cfg.becollective_tags || [],
          forum_calendar_id: calendarId.trim() || null,
        },
      });
      onDone();
    } catch {
      setErr("Couldn't save — double-check the token and Location ID.");
    } finally {
      setBusy(false);
    }
  }

  const field = { width: "100%", boxSizing: "border-box", fontFamily: "Inter,sans-serif", fontSize: 13, color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "9px 11px", marginTop: 5 };
  const label = { display: "block", fontFamily: "Inter,sans-serif", fontSize: 12, fontWeight: 600, color: T.slate, marginTop: 14 };

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,46,44,0.34)", zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <form onClick={(e) => e.stopPropagation()} onSubmit={submit} style={{ width: "100%", maxWidth: 420, background: T.white, borderRadius: 14, padding: 22, boxShadow: "0 20px 60px rgba(0,46,44,.22)" }}>
        <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 16, fontWeight: 600, color: T.ink }}>{editing ? "Edit Go High Level" : "Connect Go High Level"}</div>
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.muted, marginTop: 3 }}>Spring B · beCollective + The Forum. The token is stored encrypted.</div>
        <label style={label}>Private Integration Token {editing && <span style={{ fontWeight: 400, color: T.muted }}>· leave blank to keep the current key</span>}
          <input style={field} type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder={editing ? "•••••••• (unchanged)" : ""} required={!editing} />
        </label>
        <label style={label}>Location ID
          <input style={field} value={locationId} onChange={(e) => setLocationId(e.target.value)} required />
        </label>
        <label style={label}>Active-member tags (comma-separated)
          <input style={field} value={tags} onChange={(e) => setTags(e.target.value)} />
        </label>
        <label style={label}>Forum Calendar ID <span style={{ fontWeight: 400, color: T.muted }}>· optional (lights up Next Forum Event + Registered)</span>
          <input style={field} value={calendarId} onChange={(e) => setCalendarId(e.target.value)} placeholder="Leave blank to skip" />
        </label>
        {err && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.poppyText, marginTop: 12 }}>{err}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18 }}>
          <button type="button" onClick={onClose} style={btn()}>Cancel</button>
          <button type="submit" disabled={busy} style={busy ? btn("disabled") : btn("primary")}>{busy ? "Saving…" : editing ? "Save changes" : "Connect"}</button>
        </div>
      </form>
    </div>
  );
}

/* ── integrations ──────────────────────────────────────────── */

function IntegrationsPage() {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(null);
  const [connecting, setConnecting] = useState(null);
  const live = Boolean(API_BASE);

  function load() {
    if (!live) { setRows(ensureProviders(SAMPLE_INTEGRATIONS)); return; }
    getJSON("/integrations").then((r) => setRows(ensureProviders(r))).catch(() => setError(true));
  }
  useEffect(load, []);

  async function syncNow(row) {
    setBusy(row.id);
    const prev = row.last_synced_at;
    try {
      await postJSON(`/integrations/${row.id}/sync`);
      for (let i = 0; i < 30; i++) {          // poll up to ~2.5 min
        await sleep(5000);
        const fresh = await getJSON("/integrations");
        setRows(fresh);
        const r = fresh.find((x) => x.id === row.id);
        if (r && (r.last_synced_at !== prev || r.status === "error")) break;
      }
    } catch {
      /* leave status as-is; the list refresh shows any error */
    } finally {
      setBusy(null);
    }
  }

  async function disconnect(row) {
    if (!window.confirm(`Disconnect ${providerLabel(row)}?`)) return;
    setBusy(row.id);
    try {
      await postJSON(`/integrations/${row.id}/disconnect`);
      const fresh = await getJSON("/integrations");
      setRows(fresh);
    } finally {
      setBusy(null);
    }
  }

  if (error) return <Card title="Integrations"><div style={{ color: T.muted, fontSize: 13 }}>Couldn't load integrations.</div></Card>;
  if (!rows) return <Card title="Integrations"><div style={{ color: T.muted, fontSize: 13 }}>Loading…</div></Card>;

  return (
    <Card title="Integrations" hint="Where each business's numbers come from. Sync a source now, or disconnect it.">
      {!live && (
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.poppyText, background: "rgba(250,128,105,0.08)", border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 12px", marginBottom: 14 }}>
          Preview (sample) — connect to the live API to manage sources.
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column" }}>
        {rows.map((row) => {
          const syncing = busy === row.id;
          const connected = row.status === "connected" || row.status === "error";
          return (
            <div key={row.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 0", borderTop: `1px solid ${T.line}` }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontFamily: "Inter,sans-serif", fontSize: 13.5, fontWeight: 600, color: T.ink }}>{providerLabel(row)}</div>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 3 }}>
                  <span style={{ width: 7, height: 7, borderRadius: 99, background: STATUS_DOT[row.status] || T.muted }} />
                  <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate }}>{STATUS_LABEL[row.status] || row.status}</span>
                  {row.last_synced_at && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted }}>· synced {relativeTime(row.last_synced_at)}</span>}
                  {row.status === "error" && row.last_error && <span title={row.last_error} style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.poppyText }}>· {String(row.last_error).slice(0, 40)}</span>}
                </div>
              </div>
              {connected ? (
                <>
                  <button disabled={!live || syncing} onClick={() => syncNow(row)} style={live && !syncing ? btn() : btn("disabled")}>
                    {syncing ? "Syncing…" : row.status === "error" ? "Retry" : "Sync now"}
                  </button>
                  {row.provider === "ghl" && (
                    <button disabled={!live || syncing} onClick={() => setConnecting(row)} style={live && !syncing ? btn() : btn("disabled")} title="Rotate the key or update tags / calendar">Edit</button>
                  )}
                  <button disabled={!live || syncing} onClick={() => disconnect(row)} style={live && !syncing ? btn("danger") : btn("disabled")}>Disconnect</button>
                </>
              ) : row.provider === "ghl" && live ? (
                <button onClick={() => setConnecting(row)} style={btn("primary")}>Connect</button>
              ) : (
                <button disabled title="Available when this source is wired up" style={btn("disabled")}>Connect</button>
              )}
            </div>
          );
        })}
      </div>
      {connecting && (
        <GhlConnectForm row={connecting} onClose={() => setConnecting(null)}
          onDone={() => { setConnecting(null); load(); }} />
      )}
    </Card>
  );
}

/* ── account ───────────────────────────────────────────────── */

function doSignOut() {
  const token = localStorage.getItem("cc_token");
  if (API_BASE && token) fetch(`${API_BASE}/auth/logout`, { method: "POST", headers: { Authorization: `Bearer ${token}` } }).catch(() => {});
  localStorage.removeItem("cc_token");
  window.location.href = "/";
}

function AccountPage() {
  const [me, setMe] = useState(null);
  useEffect(() => {
    if (!API_BASE) { setMe({ name: "Spring Bengtzen", email: "spring@springb.com", role: "owner", tenant: "springb" }); return; }
    getJSON("/me").then(setMe).catch(() => {});
  }, []);
  const rowStyle = { display: "flex", justifyContent: "space-between", padding: "9px 0", borderTop: `1px solid ${T.line}`, fontFamily: "Inter,sans-serif", fontSize: 13 };
  return (
    <Card title="Account">
      {me ? (
        <div>
          <div style={{ ...rowStyle, borderTop: "none" }}><span style={{ color: T.slate }}>Name</span><span style={{ color: T.ink, fontWeight: 600 }}>{me.name}</span></div>
          <div style={rowStyle}><span style={{ color: T.slate }}>Email</span><span style={{ color: T.ink }}>{me.email}</span></div>
          <div style={rowStyle}><span style={{ color: T.slate }}>Role</span><span style={{ color: T.ink, textTransform: "capitalize" }}>{me.role}</span></div>
          <button onClick={doSignOut} style={{ ...btn("danger"), marginTop: 18 }}>Sign out</button>
        </div>
      ) : <div style={{ color: T.muted, fontSize: 13 }}>Loading…</div>}
    </Card>
  );
}

/* ── businesses (read-only for now) ────────────────────────── */

const SAMPLE_BUSINESSES = [
  { key: "ulrg", name: "ULRG + Team", tag: "Real estate", status: "healthy", accent: T.meadow, ink: "#4F6A4D", is_jv: false, jv_share: 1, watch_margin_below: null, per_loan_share: null },
  { key: "springb", name: "Spring B", tag: "beCollective + The Forum", status: "watch", accent: T.poppy, ink: T.poppyText, is_jv: false, jv_share: 1, watch_margin_below: 25, per_loan_share: null },
  { key: "sympli", name: "Sympli Mortgage", tag: "Joint venture · 50% owned", status: "opportunity", accent: T.teal, ink: T.teal, is_jv: true, jv_share: 0.5, watch_margin_below: null, per_loan_share: 2100 },
];

function BusinessEditForm({ biz, onClose, onDone }) {
  const [f, setF] = useState({
    name: biz.name || "", tag: biz.tag || "", status: biz.status || "healthy",
    accent: biz.accent || "#61835E", ink: biz.ink || "#4F6A4D", is_jv: !!biz.is_jv,
    jv_pct: Math.round((biz.jv_share ?? 1) * 100),
    watch_margin_below: biz.watch_margin_below ?? "",
    per_loan_share: biz.per_loan_share ?? "",
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const numOrNull = (v) => (v === "" || v === null ? null : Number(v));

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      await putJSON(`/businesses/${biz.key}`, {
        name: f.name.trim(), tag: f.tag.trim(), status: f.status,
        accent: f.accent.trim(), ink: f.ink.trim(), is_jv: f.is_jv,
        jv_share: Math.max(0, Math.min(100, Number(f.jv_pct) || 0)) / 100,
        watch_margin_below: numOrNull(f.watch_margin_below),
        per_loan_share: numOrNull(f.per_loan_share),
      });
      onDone();
    } catch {
      setErr("Couldn't save — check the values and try again.");
    } finally { setBusy(false); }
  }

  const field = { width: "100%", boxSizing: "border-box", fontFamily: "Inter,sans-serif", fontSize: 13, color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "9px 11px", marginTop: 5 };
  const label = { display: "block", fontFamily: "Inter,sans-serif", fontSize: 12, fontWeight: 600, color: T.slate, marginTop: 14 };
  const half = { display: "flex", gap: 10 };

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,46,44,0.34)", zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <form onClick={(e) => e.stopPropagation()} onSubmit={submit} style={{ width: "100%", maxWidth: 460, background: T.white, borderRadius: 14, padding: 22, boxShadow: "0 20px 60px rgba(0,46,44,.22)", maxHeight: "88vh", overflowY: "auto" }}>
        <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 16, fontWeight: 600, color: T.ink }}>Edit {biz.name}</div>
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.muted, marginTop: 3 }}>Brand and health config for this profit center.</div>
        <label style={label}>Name<input style={field} value={f.name} onChange={set("name")} required /></label>
        <label style={label}>Tagline<input style={field} value={f.tag} onChange={set("tag")} /></label>
        <div style={half}>
          <label style={{ ...label, flex: 1 }}>Status
            <select style={field} value={f.status} onChange={set("status")}>
              <option value="healthy">Healthy</option>
              <option value="watch">Watch</option>
              <option value="opportunity">Opportunity</option>
            </select>
          </label>
          <label style={{ ...label, flex: 1 }}>Watch when margin below (%)
            <input style={field} type="number" step="0.5" value={f.watch_margin_below} onChange={set("watch_margin_below")} placeholder="—" />
          </label>
        </div>
        <div style={half}>
          <label style={{ ...label, flex: 1 }}>Accent
            <span style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 5 }}>
              <input type="color" value={f.accent} onChange={set("accent")} style={{ width: 34, height: 34, border: `1px solid ${T.line}`, borderRadius: 8, background: T.white, padding: 2 }} />
              <input style={{ ...field, marginTop: 0 }} value={f.accent} onChange={set("accent")} />
            </span>
          </label>
          <label style={{ ...label, flex: 1 }}>Ink
            <span style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 5 }}>
              <input type="color" value={f.ink} onChange={set("ink")} style={{ width: 34, height: 34, border: `1px solid ${T.line}`, borderRadius: 8, background: T.white, padding: 2 }} />
              <input style={{ ...field, marginTop: 0 }} value={f.ink} onChange={set("ink")} />
            </span>
          </label>
        </div>
        <label style={{ ...label, display: "flex", alignItems: "center", gap: 8 }}>
          <input type="checkbox" checked={f.is_jv} onChange={set("is_jv")} /> Joint venture
        </label>
        {f.is_jv && (
          <div style={half}>
            <label style={{ ...label, flex: 1 }}>JV share (%)
              <input style={field} type="number" step="1" value={f.jv_pct} onChange={set("jv_pct")} />
            </label>
            <label style={{ ...label, flex: 1 }}>Revenue per funded loan ($)
              <input style={field} type="number" step="50" value={f.per_loan_share} onChange={set("per_loan_share")} placeholder="—" />
            </label>
          </div>
        )}
        {err && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.poppyText, marginTop: 12 }}>{err}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18 }}>
          <button type="button" onClick={onClose} style={btn()}>Cancel</button>
          <button type="submit" disabled={busy} style={busy ? btn("disabled") : btn("primary")}>{busy ? "Saving…" : "Save changes"}</button>
        </div>
      </form>
    </div>
  );
}

function BusinessesPage() {
  const [rows, setRows] = useState(null);
  const [editing, setEditing] = useState(null);
  const live = Boolean(API_BASE);

  function load() {
    if (!live) { setRows(SAMPLE_BUSINESSES); return; }
    getJSON("/businesses").then(setRows).catch(() => setRows(SAMPLE_BUSINESSES));
  }
  useEffect(load, []);

  return (
    <Card title="Businesses" hint="Brand and health config per profit center. Status flags 'watch' automatically when the margin drops below the threshold.">
      {!live && (
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.poppyText, background: "rgba(250,128,105,0.08)", border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 12px", marginBottom: 14 }}>
          Preview (sample) — connect to the live API to edit.
        </div>
      )}
      {rows ? rows.map((b) => (
        <div key={b.key} style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 0", borderTop: `1px solid ${T.line}` }}>
          <span style={{ width: 10, height: 10, borderRadius: 3, background: b.accent, flexShrink: 0 }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 13.5, fontWeight: 600, color: T.ink }}>{b.name}</div>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted }}>{b.tag}</div>
          </div>
          {b.is_jv && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate }}>JV {Math.round(b.jv_share * 100)}%</span>}
          <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate, textTransform: "capitalize" }}>{b.status}</span>
          <button disabled={!live} onClick={() => setEditing(b)} style={live ? btn() : btn("disabled")}>Edit</button>
        </div>
      )) : <div style={{ color: T.muted, fontSize: 13 }}>Loading…</div>}
      {editing && (
        <BusinessEditForm biz={editing} onClose={() => setEditing(null)}
          onDone={() => { setEditing(null); load(); }} />
      )}
    </Card>
  );
}

/* ── routes ────────────────────────────────────────────────── */

export default function Settings() {
  return (
    <SettingsShell>
      <Routes>
        <Route path="integrations" element={<IntegrationsPage />} />
        <Route path="account" element={<AccountPage />} />
        <Route path="businesses" element={<BusinessesPage />} />
        <Route path="*" element={<Navigate to="/settings/integrations" replace />} />
      </Routes>
    </SettingsShell>
  );
}
