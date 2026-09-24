import { useCallback, useEffect, useState } from "react";
import { Routes, Route, Navigate, NavLink, Link, useLocation } from "react-router-dom";
import { T, alpha, PROVIDER_NAME, relativeTime } from "./theme.js";
import { getJSON, postJSON, putJSON, patchJSON, delJSON, tenantHeaders, getToken, logout, getBlob } from "./api.js";
import { Icon, getBrand, setBrand } from "./Brand.jsx";
import { IntegrationMark } from "./brand/integrationMarks.jsx";
import { frontDoorUrl } from "./marketing/hosts.js";
import AISettings from "./AISettings.jsx";
import SecuritySettings from "./SecuritySettings.jsx";
import Appearance from "./Appearance.jsx";

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

// Members get a lone Account entry; owners/admins get the full management set.
//
// `badge` names a key in the /settings/badges payload. The Integrations one is an ATTENTION
// count and renders only when something is actually wrong -- a badge that is always present is
// furniture, and the eye stops reading it. Businesses is a plain count, which is what the
// mockup draws.
function subnavFor(role, aiOn, ulrgOn) {
  if (role === "member") return [{ to: "/settings/security", label: "Security" },
                                 { to: "/settings/account", label: "Account" }];
  const nav = [
    { to: "/settings/integrations", label: "Integrations", badge: "integrations_attention", attention: true },
    { to: "/settings/users", label: "Team" },
    { to: "/settings/businesses", label: "Businesses", badge: "businesses" },
    { to: "/settings/appearance", label: "Appearance" },
  ];
  // Only where the tab it configures exists. A workspace with no ULRG tab has no Recruiting tab
  // to configure, and an entry that leads to a page about a feature you do not have is noise.
  if (ulrgOn) nav.push({ to: "/settings/recruiting", label: "Recruiting" });
  if (aiOn) nav.push({ to: "/settings/ai", label: "AI Employees" });
  nav.push({ to: "/settings/security", label: "Security" });
  nav.push({ to: "/settings/account", label: "Account" });
  return nav;
}

function SettingsShell({ children, role, aiOn, ulrgOn, badges }) {
  const SUBNAV = subnavFor(role, aiOn, ulrgOn);
  // The breadcrumb's second half is the route's own nav label, so a page cannot be titled one
  // thing in the rail and another above it.
  const { pathname } = useLocation();
  const here = SUBNAV.find((n) => pathname.startsWith(n.to));
  const brand = getBrand();
  const workspace = brand.display_name || "Workspace";
  return (
    <div style={{ background: T.parchment, minHeight: "100vh", fontFamily: "var(--font-text)" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&family=Inter:wght@400;500;600&display=swap');
        input:focus-visible, select:focus-visible, textarea:focus-visible, button:focus-visible { outline: 2px solid ${T.teal}; outline-offset: 2px; }
        input[type="checkbox"], input[type="radio"] { accent-color: ${T.evergreen}; width: 15px; height: 15px; }
      `}</style>
      <div style={{
        display: "flex", alignItems: "center", gap: 14, padding: "14px 26px",
        borderBottom: `1px solid ${T.line}`, background: T.white,
      }}>
        <Link to="/" style={{ fontFamily: "var(--font-text)", fontSize: 13, fontWeight: 600, color: T.slate, textDecoration: "none" }}>← Command Center</Link>
        <span aria-hidden style={{ width: 1, height: 18, background: T.line }} />
        <span style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 600, color: T.ink }}>Settings</span>
        {here && (
          <>
            <span aria-hidden style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.muted }}>/</span>
            <span style={{ fontFamily: "var(--font-display)", fontSize: 16, color: T.slate }}>{here.label}</span>
          </>
        )}
        <span style={{ flex: 1 }} />
        {/* Identity, not a switcher: a user belongs to one workspace on its own subdomain, so
            there is nothing to switch to. It links to the finder, which is the one place that
            knows every workspace an email belongs to. */}
        <a href={frontDoorUrl()} title="Your workspaces" style={{
          display: "flex", alignItems: "center", gap: 8, textDecoration: "none",
          border: `1px solid ${T.line}`, borderRadius: 999, padding: "3px 12px 3px 3px",
          background: T.parchment,
        }}>
          <span aria-hidden style={{
            width: 26, height: 26, borderRadius: 999, background: T.evergreen, color: T.onDark,
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            fontFamily: "var(--font-display)", fontSize: 11.5, fontWeight: 600,
          }}>{workspace.trim().charAt(0).toUpperCase()}</span>
          <span style={{ fontFamily: "var(--font-text)", fontSize: 13, fontWeight: 500, color: T.secondary }}>{workspace}</span>
        </a>
      </div>
      <div style={{ display: "flex", gap: 26, padding: 26, maxWidth: 1100 }}>
        <nav style={{ width: 170, flexShrink: 0, display: "flex", flexDirection: "column", gap: 2 }}>
          {SUBNAV.map((n) => {
            const count = n.badge ? badges?.[n.badge] : null;
            const show = count != null && count > 0;
            return (
              <NavLink key={n.to} to={n.to} style={({ isActive }) => ({
                display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8,
                fontFamily: "var(--font-text)", fontSize: 13.5, fontWeight: isActive ? 600 : 500,
                color: isActive ? T.ink : T.slate, textDecoration: "none",
                background: isActive ? T.white : "transparent", border: `1px solid ${isActive ? T.line : "transparent"}`,
                borderRadius: 8, padding: "9px 12px",
              })}>
                <span>{n.label}</span>
                {show && (
                  <span style={{
                    fontFamily: "var(--font-data)", fontSize: 11, fontVariantNumeric: "tabular-nums",
                    color: n.attention ? T.poppyText : T.muted,
                  }}>{count}</span>
                )}
              </NavLink>
            );
          })}
        </nav>
        <div style={{ flex: 1, minWidth: 0 }}>{children}</div>
      </div>
    </div>
  );
}

function Card({ title, hint, children }) {
  return (
    <div style={{ background: T.white, border: `1px solid ${T.line}`, borderRadius: 14, padding: 22, marginBottom: 18 }}>
      {title && <div style={{ fontFamily: "var(--font-display)", fontSize: 15, fontWeight: 600, color: T.ink }}>{title}</div>}
      {hint && <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.muted, marginTop: 3, marginBottom: 14 }}>{hint}</div>}
      {children}
    </div>
  );
}

function btn(kind) {
  const base = {
    fontFamily: "var(--font-text)", fontSize: 12.5, fontWeight: 600, borderRadius: 8,
    padding: "6px 12px", cursor: "pointer", border: "none",
  };
  if (kind === "primary") return { ...base, color: T.onDark, background: T.evergreen };
  if (kind === "danger") return { ...base, color: T.poppyText, background: T.white, border: `1px solid ${T.line}` };
  if (kind === "disabled") return { ...base, color: T.muted, background: T.parchment, border: `1px solid ${T.line}`, cursor: "not-allowed" };
  return { ...base, color: T.slate, background: T.parchment, border: `1px solid ${T.line}` };
}

/* ── Go High Level connect form ────────────────────────────── */

const GHL_DEFAULT_TAGS = "inner circle active, the forum active, forumadmin, member: secondary, inner circle active add on";
const BC_DEFAULT_TAGS = "be collective financed, be collective payment complete, be collective won onboarded group 1";

function GhlConnectForm({ row, onClose, onDone }) {
  const cfg = row.config || {};
  const provider = row.provider || "ghl";
  const isBc = provider === "ghl_bc";
  const programName = row.name || (isBc ? "Go High Level · beCollective" : "Go High Level");
  const editing = row.status === "connected" || row.status === "error";
  const [token, setToken] = useState("");
  const [locationId, setLocationId] = useState(cfg.location_id || "");
  const [tags, setTags] = useState((cfg.member_tags && cfg.member_tags.join(", ")) || (isBc ? BC_DEFAULT_TAGS : GHL_DEFAULT_TAGS));
  const [eventTag, setEventTag] = useState(cfg.event_tag || "");
  const [eventName, setEventName] = useState(cfg.event_name || "");
  const [eventTitle, setEventTitle] = useState(cfg.event_title || "");
  const [eventDates, setEventDates] = useState(cfg.event_dates || "");
  const [eventDate, setEventDate] = useState(cfg.event_date || "");
  const [priorPace, setPriorPace] = useState(cfg.prior_event_pace ?? "");
  const [salesMatch, setSalesMatch] = useState(cfg.sales_pipeline_match || "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const member_tags = tags.split(",").map((t) => t.trim()).filter(Boolean);
      await postJSON("/integrations", {
        provider, business_key: row.business_key,
        token: token.trim() || undefined,   // blank on edit = keep the current key
        config: {
          ...cfg,                             // preserve segmentation + pipeline matchers
          location_id: locationId.trim(), member_tags,
          event_tag: eventTag.trim().toLowerCase() || null,
          event_name: eventName.trim() || null,
          event_title: eventTitle.trim() || null,
          event_dates: eventDates.trim() || null,
          event_date: eventDate || null,
          prior_event_pace: priorPace === "" ? null : Number(priorPace),
          sales_pipeline_match: salesMatch.trim().toLowerCase() || null,
        },
      });
      onDone();
    } catch {
      setErr("Couldn't save — double-check the token and Location ID.");
    } finally {
      setBusy(false);
    }
  }

  const field = { width: "100%", boxSizing: "border-box", fontFamily: "var(--font-text)", fontSize: 13, color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "9px 11px", marginTop: 5 };
  const label = { display: "block", fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate, marginTop: 14 };

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,46,44,0.34)", zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <form onClick={(e) => e.stopPropagation()} onSubmit={submit} style={{ width: "100%", maxWidth: 420, background: T.white, borderRadius: 14, padding: 22, boxShadow: "0 20px 60px rgba(0,46,44,.22)" }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 600, color: T.ink }}>{editing ? `Edit ${programName}` : `Connect ${programName}`}</div>
        <div style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.muted, marginTop: 3 }}>{isBc ? "beCollective · its own GHL location." : "The Forum · Go High Level."} The token is stored encrypted.</div>
        <label style={label}>Private Integration Token {editing && <span style={{ fontWeight: 400, color: T.muted }}>· leave blank to keep the current key</span>}
          <input style={field} type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder={editing ? "•••••••• (unchanged)" : ""} required={!editing} />
        </label>
        <label style={label}>Location ID
          <input style={field} value={locationId} onChange={(e) => setLocationId(e.target.value)} required />
        </label>
        <label style={label}>Active-member tags (comma-separated) <span style={{ fontWeight: 400, color: T.muted }}>· the official member count</span>
          <input style={field} value={tags} onChange={(e) => setTags(e.target.value)} />
        </label>
        <label style={label}>Next-event registration tag <span style={{ fontWeight: 400, color: T.muted }}>· drives Registered</span>
          <input style={field} value={eventTag} onChange={(e) => setEventTag(e.target.value)} placeholder="e.g. the forum q3 2026" />
        </label>
        <label style={label}>Next-event location <span style={{ fontWeight: 400, color: T.muted }}>· shown on the panel</span>
          <input style={field} value={eventName} onChange={(e) => setEventName(e.target.value)} placeholder="e.g. Park City, UT" />
        </label>
        <label style={label}>Next-event title <span style={{ fontWeight: 400, color: T.muted }}>· optional</span>
          <input style={field} value={eventTitle} onChange={(e) => setEventTitle(e.target.value)} placeholder="e.g. The Forum · Q3 Immersion" />
        </label>
        <div style={{ display: "flex", gap: 10 }}>
          <label style={{ ...label, flex: 1 }}>Event date <span style={{ fontWeight: 400, color: T.muted }}>· countdown</span>
            <input style={field} type="date" value={eventDate} onChange={(e) => setEventDate(e.target.value)} />
          </label>
          <label style={{ ...label, flex: 1 }}>Dates label <span style={{ fontWeight: 400, color: T.muted }}>· display</span>
            <input style={field} value={eventDates} onChange={(e) => setEventDates(e.target.value)} placeholder="Sep 18–20, 2026" />
          </label>
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <label style={{ ...label, flex: 1 }}>Prior-event pace <span style={{ fontWeight: 400, color: T.muted }}>· # registered</span>
            <input style={field} type="number" min="0" value={priorPace} onChange={(e) => setPriorPace(e.target.value)} placeholder="e.g. 34" />
          </label>
          <label style={{ ...label, flex: 1 }}>Sales pipeline match <span style={{ fontWeight: 400, color: T.muted }}>· recruiting</span>
            <input style={field} value={salesMatch} onChange={(e) => setSalesMatch(e.target.value)} placeholder="sales" />
          </label>
        </div>
        {err && <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.poppyText, marginTop: 12 }}>{err}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18 }}>
          <button type="button" onClick={onClose} style={btn()}>Cancel</button>
          <button type="submit" disabled={busy} style={busy ? btn("disabled") : btn("primary")}>{busy ? "Saving…" : editing ? "Save changes" : "Connect"}</button>
        </div>
      </form>
    </div>
  );
}

/* ── Arive connect form (three credentials → one encrypted blob) ── */

function AriveConnectForm({ row, onClose, onDone }) {
  const editing = row.status === "connected" || row.status === "error";
  const [clientId, setClientId] = useState((row.config || {}).client_id || "");
  const [secret, setSecret] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await postJSON("/integrations", {
        provider: "arive", business_key: row.business_key,
        client_id: clientId.trim() || undefined,     // blank on edit = keep current
        secret: secret.trim() || undefined,
        api_key: apiKey.trim() || undefined,
      });
      onDone();
    } catch {
      setErr("Couldn't save — double-check the Client ID, Secret Key, and API Key.");
    } finally {
      setBusy(false);
    }
  }

  const field = { width: "100%", boxSizing: "border-box", fontFamily: "var(--font-text)", fontSize: 13, color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "9px 11px", marginTop: 5 };
  const label = { display: "block", fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate, marginTop: 14 };
  const keep = editing && <span style={{ fontWeight: 400, color: T.muted }}>· leave blank to keep current</span>;

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,46,44,0.34)", zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <form onClick={(e) => e.stopPropagation()} onSubmit={submit} style={{ width: "100%", maxWidth: 420, background: T.white, borderRadius: 14, padding: 22, boxShadow: "0 20px 60px rgba(0,46,44,.22)" }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 600, color: T.ink }}>{editing ? "Edit Arive" : "Connect Arive"}</div>
        <div style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.muted, marginTop: 3 }}>From Arive → Settings → Integrations. All three are stored encrypted.</div>
        <label style={label}>Client ID
          <input style={field} value={clientId} onChange={(e) => setClientId(e.target.value)} autoComplete="off" required={!editing} placeholder={editing ? "•••• (unchanged)" : ""} />
        </label>
        <label style={label}>Secret Key {keep}
          <input style={field} type="password" value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="off" required={!editing} placeholder={editing ? "•••••••• (unchanged)" : ""} />
        </label>
        <label style={label}>API Key {keep}
          <input style={field} type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} autoComplete="off" required={!editing} placeholder={editing ? "•••••••• (unchanged)" : ""} />
        </label>
        {err && <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.poppyText, marginTop: 12 }}>{err}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18 }}>
          <button type="button" onClick={onClose} style={btn()}>Cancel</button>
          <button type="submit" disabled={busy} style={busy ? btn("disabled") : btn("primary")}>{busy ? "Saving…" : editing ? "Save changes" : "Connect"}</button>
        </div>
      </form>
    </div>
  );
}

/* ── Sisu connect form (Basic auth: username + API token) ─────
   Sisu and Follow Up Boss used to be process-wide env vars. Moving them onto per-tenant
   integration rows was correct — a shared key syncs one customer's book of business into
   another's dashboard — but it left the Settings page with no way to enter them, so a source
   that had "always been connected" became permanently disconnected with a dead button. */

function SisuConnectForm({ row, onClose, onDone }) {
  const editing = row.status === "connected" || row.status === "error";
  const [username, setUsername] = useState((row.config || {}).username || "");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await postJSON("/integrations", {
        provider: "sisu", business_key: row.business_key,
        username: username.trim() || undefined,
        token: token.trim() || undefined,          // blank on edit = keep current
      });
      onDone();
    } catch (e2) {
      setErr(e2?.detail || "Couldn't save — double-check the username and API token.");
    } finally {
      setBusy(false);
    }
  }

  const field = { width: "100%", boxSizing: "border-box", fontFamily: "var(--font-text)", fontSize: 13, color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "9px 11px", marginTop: 5 };
  const label = { display: "block", fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate, marginTop: 14 };

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,46,44,0.34)", zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <form onClick={(e) => e.stopPropagation()} onSubmit={submit} style={{ width: "100%", maxWidth: 420, background: T.white, borderRadius: 14, padding: 22, boxShadow: "0 20px 60px rgba(0,46,44,.22)" }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 600, color: T.ink }}>{editing ? "Edit Sisu" : "Connect Sisu"}</div>
        <div style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.muted, marginTop: 3 }}>Your Sisu login and an API token from Sisu → Settings → API. Both are stored encrypted; we never write to Sisu.</div>
        <label style={label}>Username
          <input style={field} value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" required />
        </label>
        <label style={label}>API token {editing && <span style={{ fontWeight: 400, color: T.muted }}>· leave blank to keep current</span>}
          <input style={field} type="password" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" required={!editing} placeholder={editing ? "•••••••• (unchanged)" : ""} />
        </label>
        {err && <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.poppyText, marginTop: 12 }}>{err}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18 }}>
          <button type="button" onClick={onClose} style={btn()}>Cancel</button>
          <button type="submit" disabled={busy} style={busy ? btn("disabled") : btn("primary")}>{busy ? "Saving…" : editing ? "Save changes" : "Connect"}</button>
        </div>
      </form>
    </div>
  );
}

/* ── Follow Up Boss connect form (one API key) ────────────────── */

function MetaAdsConnectForm({ row, onClose, onDone }) {
  const editing = row.status === "connected" || row.status === "error";
  const [token, setToken] = useState("");
  const [acct, setAcct] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      // TWO STEPS, because one System User token routinely carries several ad accounts. The
      // token is the integration; each account hangs off it.
      const integ = await postJSON("/integrations", {
        provider: "meta_ads", business_key: row.business_key,
        token: token.trim() || undefined,
      });
      const id = String(acct.trim());
      if (id) {
        await postJSON("/ads/accounts", {
          integration_id: integ?.id || row.integration_id,
          external_id: id.startsWith("act_") ? id : `act_${id}`,
        });
      }
      onDone();
    } catch (e2) {
      setErr(e2?.detail || "Couldn't save — check the token has ads_read on this account.");
    } finally {
      setBusy(false);
    }
  }

  const field = { width: "100%", boxSizing: "border-box", fontFamily: "var(--font-text)", fontSize: 13, color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "9px 11px", marginTop: 5 };
  const label = { display: "block", fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate, marginTop: 14 };

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,46,44,0.34)", zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <form onClick={(e) => e.stopPropagation()} onSubmit={submit} style={{ width: "100%", maxWidth: 460, background: T.white, borderRadius: 14, padding: 22, boxShadow: "0 20px 60px rgba(0,46,44,.22)" }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 600, color: T.ink }}>{editing ? "Edit Meta Ads" : "Connect Meta Ads"}</div>
        <div style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.muted, marginTop: 3, lineHeight: 1.5 }}>
          A <strong>System User</strong> token from Meta Business Settings → System Users. Grant it{" "}
          <strong>ads_read</strong> only — this module never writes, so ads_management is more
          access than it needs. Stored encrypted; the browser never calls Meta.
        </div>
        <label style={label}>System User token {editing && <span style={{ fontWeight: 400, color: T.muted }}>· leave blank to keep current</span>}
          <input style={field} type="password" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" required={!editing} placeholder={editing ? "•••••••• (unchanged)" : ""} />
        </label>
        <label style={label}>Ad account ID
          <input style={field} value={acct} onChange={(e) => setAcct(e.target.value)} autoComplete="off" placeholder="act_587749862890426" />
          <span style={{ display: "block", fontFamily: "var(--font-text)", fontSize: 11.5, color: T.muted, marginTop: 5, lineHeight: 1.5 }}>
            From Ads Manager, top left. One token can carry several accounts — add the others
            after this one.
          </span>
        </label>
        <div style={{ fontFamily: "var(--font-text)", fontSize: 11.5, color: T.muted, marginTop: 14, lineHeight: 1.5, background: T.parchment, borderRadius: 8, padding: "10px 12px" }}>
          <strong style={{ color: T.slate }}>Ad-level revenue needs one more thing.</strong> Your ad
          URLs need <code>utm_content=&#123;&#123;ad.id&#125;&#125;</code> and a matching{" "}
          <code>utm_content</code> field in GoHighLevel. Until both exist the funnel works at
          campaign level and the creative wall shows no revenue.
        </div>
        {err && <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.poppyText, marginTop: 12 }}>{err}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18 }}>
          <button type="button" onClick={onClose} style={btn()}>Cancel</button>
          <button type="submit" disabled={busy} style={busy ? btn("disabled") : btn("primary")}>{busy ? "Saving…" : editing ? "Save changes" : "Connect"}</button>
        </div>
      </form>
    </div>
  );
}


function FubConnectForm({ row, onClose, onDone }) {
  const editing = row.status === "connected" || row.status === "error";
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  // Said by the server when the key works but belongs to an agent: FUB shows an agent only their
  // own leads, so the rest of the team's follow-ups would be missing with nothing to say why. The
  // key IS saved; the form stays open until it has been read.
  const [warning, setWarning] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const out = await postJSON("/integrations", {
        provider: "fub", business_key: row.business_key,
        token: key.trim() || undefined,
      });
      if (out?.warning) {
        setWarning(out.warning);
        return;
      }
      onDone();
    } catch (e2) {
      setErr(e2?.detail || "Couldn't save — double-check the API key.");
    } finally {
      setBusy(false);
    }
  }

  const field = { width: "100%", boxSizing: "border-box", fontFamily: "var(--font-text)", fontSize: 13, color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "9px 11px", marginTop: 5 };
  const label = { display: "block", fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate, marginTop: 14 };

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,46,44,0.34)", zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <form onClick={(e) => e.stopPropagation()} onSubmit={submit} style={{ width: "100%", maxWidth: 420, background: T.white, borderRadius: 14, padding: 22, boxShadow: "0 20px 60px rgba(0,46,44,.22)" }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 600, color: T.ink }}>{editing ? "Edit Follow Up Boss" : "Connect Follow Up Boss"}</div>
        <div style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.muted, marginTop: 3 }}>An API key from Follow Up Boss → Admin → API. Stored encrypted; we only read.</div>
        <label style={label}>API key {editing && <span style={{ fontWeight: 400, color: T.muted }}>· leave blank to keep current</span>}
          <input style={field} type="password" value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" required={!editing} placeholder={editing ? "•••••••• (unchanged)" : ""} />
        </label>
        {err && <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.poppyText, marginTop: 12 }}>{err}</div>}
        {warning && (
          <div role="status" style={{ fontFamily: "var(--font-text)", fontSize: 12.5, lineHeight: 1.45, color: T.daffodilText, background: T.daffodilBg, borderRadius: 8, padding: "10px 12px", marginTop: 12 }}>
            <strong style={{ display: "block", marginBottom: 3 }}>Connected, with one catch</strong>
            {warning}
          </div>
        )}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18 }}>
          {warning ? (
            <button type="button" onClick={onDone} style={btn("primary")}>Done</button>
          ) : (
            <>
              <button type="button" onClick={onClose} style={btn()}>Cancel</button>
              <button type="submit" disabled={busy} style={busy ? btn("disabled") : btn("primary")}>{busy ? "Saving…" : editing ? "Save changes" : "Connect"}</button>
            </>
          )}
        </div>
      </form>
    </div>
  );
}

/* ── Legacy Stripe connect form (one read-only key) ──────────── */

function StripeLegacyConnectForm({ row, onClose, onDone }) {
  const editing = row.status === "connected" || row.status === "error";
  const cfg = row.config || {};
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await postJSON("/integrations", {
        provider: "stripe_legacy", business_key: row.business_key,
        token: key.trim() || undefined,   // blank on edit = keep the current key
        config: cfg,
      });
      onDone();
    } catch {
      setErr("Stripe rejected that key. Use a read-only restricted key (Charges: read, Customers: read).");
    } finally {
      setBusy(false);
    }
  }

  const field = { width: "100%", boxSizing: "border-box", fontFamily: "var(--font-text)", fontSize: 13, color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "9px 11px", marginTop: 5 };
  const label = { display: "block", fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate, marginTop: 14 };

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,46,44,0.34)", zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <form onClick={(e) => e.stopPropagation()} onSubmit={submit} style={{ width: "100%", maxWidth: 440, background: T.white, borderRadius: 14, padding: 22, boxShadow: "0 20px 60px rgba(0,46,44,.22)" }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 600, color: T.ink }}>{editing ? "Edit Legacy Stripe" : "Connect Legacy Stripe"}</div>
        <div style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.muted, marginTop: 3 }}>
          Your original Stripe account — the one that predates the current sub-account. Create a <b>restricted key</b> in Stripe (Developers → API keys → Create restricted key) with <b>Charges: Read</b>, <b>Customers: Read</b>, and <b>Subscriptions: Read</b> — nothing else. Stored encrypted; we never write to Stripe.
        </div>
        <label style={label}>Read-only restricted key {editing && <span style={{ fontWeight: 400, color: T.muted }}>· leave blank to keep the current key</span>}
          <input style={field} type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder={editing ? "•••••••• (unchanged)" : "rk_live_…"} required={!editing} />
        </label>
        {err && <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.poppyText, marginTop: 12 }}>{err}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18 }}>
          <button type="button" onClick={onClose} style={btn()}>Cancel</button>
          <button type="submit" disabled={busy} style={busy ? btn("disabled") : btn("primary")}>{busy ? "Verifying…" : editing ? "Save changes" : "Connect"}</button>
        </div>
      </form>
    </div>
  );
}

function StripeBcConnectForm({ row, onClose, onDone }) {
  const editing = row.status === "connected" || row.status === "error";
  const cfg = row.config || {};
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await postJSON("/integrations", {
        provider: "stripe_bc", business_key: row.business_key,
        token: key.trim() || undefined,   // blank on edit = keep the current key
        config: cfg,
      });
      onDone();
    } catch {
      setErr("Stripe rejected that key. Use a read-only restricted key (Charges: read, Customers: read, Subscriptions: read).");
    } finally {
      setBusy(false);
    }
  }

  const field = { width: "100%", boxSizing: "border-box", fontFamily: "var(--font-text)", fontSize: 13, color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "9px 11px", marginTop: 5 };
  const label = { display: "block", fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate, marginTop: 14 };

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,46,44,0.34)", zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <form onClick={(e) => e.stopPropagation()} onSubmit={submit} style={{ width: "100%", maxWidth: 440, background: T.white, borderRadius: 14, padding: 22, boxShadow: "0 20px 60px rgba(0,46,44,.22)" }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 600, color: T.ink }}>{editing ? "Edit beCollective Stripe" : "Connect beCollective Stripe"}</div>
        <div style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.muted, marginTop: 3 }}>
          A program's own Stripe account, where one processes separately from the rest. Create a <b>restricted key</b> in Stripe (Developers → API keys → Create restricted key) with <b>Charges: Read</b>, <b>Customers: Read</b>, and <b>Subscriptions: Read</b> — nothing else. Only membership payments are reported (event tickets and other products are filtered out). Stored encrypted; we never write to Stripe.
        </div>
        <label style={label}>Read-only restricted key {editing && <span style={{ fontWeight: 400, color: T.muted }}>· leave blank to keep the current key</span>}
          <input style={field} type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder={editing ? "•••••••• (unchanged)" : "rk_live_…"} required={!editing} />
        </label>
        {err && <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.poppyText, marginTop: 12 }}>{err}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18 }}>
          <button type="button" onClick={onClose} style={btn()}>Cancel</button>
          <button type="submit" disabled={busy} style={busy ? btn("disabled") : btn("primary")}>{busy ? "Verifying…" : editing ? "Save changes" : "Connect"}</button>
        </div>
      </form>
    </div>
  );
}

/* ── Old GHL connect form (read-only token + location id) ────── */

/* The recruiting location's connection. Deliberately only two fields.
 *
 * A GHL location is not one thing: the membership form beside this one asks for member tags,
 * event tags and a sales-pipeline match, and a brokerage's recruiting location has none of them.
 * What it has is a pipeline, a stage map, two custom fields and a set of calendars -- and all of
 * those are chosen from the location's REAL contents in Settings › Recruiting once the token can
 * read them. Asking for an id here that the next screen could offer as a list is how somebody
 * ends up pasting a pipeline id into a field labelled Location.
 */
function RecruitingConnectForm({ row, onClose, onDone }) {
  const editing = row.status === "connected" || row.status === "error";
  const cfg = row.config || {};
  const [token, setToken] = useState("");
  const [locationId, setLocationId] = useState(cfg.location_id || "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await postJSON("/integrations", {
        provider: "ghl_recruiting", business_key: row.business_key,
        token: token.trim() || undefined,   // blank on edit = keep the current token
        config: { ...cfg, location_id: locationId.trim() },
      });
      onDone();
    } catch {
      setErr("Couldn't save — check the token's read scopes and the location id.");
    } finally {
      setBusy(false);
    }
  }

  const field = { width: "100%", boxSizing: "border-box", fontFamily: "var(--font-text)", fontSize: 13, color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "9px 11px", marginTop: 5 };
  const label = { display: "block", fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate, marginTop: 14 };

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,46,44,0.34)", zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <form onClick={(e) => e.stopPropagation()} onSubmit={submit} style={{ width: "100%", maxWidth: 460, background: T.white, borderRadius: 14, padding: 22, boxShadow: "0 20px 60px rgba(0,46,44,.22)" }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 600, color: T.ink }}>{editing ? "Edit Recruiting" : "Connect Recruiting"}</div>
        <div style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.muted, marginTop: 3, lineHeight: 1.5 }}>
          The GHL location your <b>recruiting</b> pipeline lives in — not the one your membership
          programmes run on. A Private Integration Token with the <b>read</b> scopes:
          Contacts, Opportunities, Conversations, Calendars, Users and Locations. Stored encrypted.
        </div>
        <label style={label}>Private Integration Token {editing && <span style={{ fontWeight: 400, color: T.muted }}>· leave blank to keep the current token</span>}
          <input style={field} type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder={editing ? "•••••••• (unchanged)" : ""} required={!editing} />
        </label>
        <label style={label}>Location ID <span style={{ fontWeight: 400, color: T.muted }}>· from the recruiting location's GHL URL</span>
          <input style={field} value={locationId} onChange={(e) => setLocationId(e.target.value)} required />
        </label>
        <div style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.slate, marginTop: 14, background: T.page, border: `1px solid ${T.line}`, borderRadius: 9, padding: "10px 12px", lineHeight: 1.5 }}>
          Next: pick the pipeline, map its stages and name the seats in <b>Settings › Recruiting</b>.
          Nothing syncs until a pipeline is chosen, and nothing is ever sent to a candidate until
          write-back is turned on for the workspace and for that person.
        </div>
        {err && <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.poppyText, marginTop: 12 }}>{err}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18 }}>
          <button type="button" onClick={onClose} style={btn()}>Cancel</button>
          <button type="submit" disabled={busy} style={busy ? btn("disabled") : btn("primary")}>{busy ? "Saving…" : editing ? "Save changes" : "Connect"}</button>
        </div>
      </form>
    </div>
  );
}

function GhlLegacyConnectForm({ row, onClose, onDone }) {
  const editing = row.status === "connected" || row.status === "error";
  const cfg = row.config || {};
  const [token, setToken] = useState("");
  const [locationId, setLocationId] = useState(cfg.location_id || "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await postJSON("/integrations", {
        provider: "ghl_legacy", business_key: row.business_key,
        token: token.trim() || undefined,   // blank on edit = keep the current token
        config: { ...cfg, location_id: locationId.trim() },
      });
      onDone();
    } catch {
      setErr("Couldn't save — check the token (needs Invoices + Payments read) and the location id.");
    } finally {
      setBusy(false);
    }
  }

  const field = { width: "100%", boxSizing: "border-box", fontFamily: "var(--font-text)", fontSize: 13, color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "9px 11px", marginTop: 5 };
  const label = { display: "block", fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate, marginTop: 14 };

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,46,44,0.34)", zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <form onClick={(e) => e.stopPropagation()} onSubmit={submit} style={{ width: "100%", maxWidth: 440, background: T.white, borderRadius: 14, padding: 22, boxShadow: "0 20px 60px rgba(0,46,44,.22)" }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 600, color: T.ink }}>{editing ? "Edit Old GHL" : "Connect Old GHL"}</div>
        <div style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.muted, marginTop: 3 }}>
          The GHL location the legacy Stripe account is wired to. A <b>read-only</b> Private Integration Token with <b>Invoices: Read</b>, <b>Payments/Transactions: Read</b>, <b>Contacts: Read</b>. Supplies the real label for each legacy charge; stored encrypted.
        </div>
        <label style={label}>Private Integration Token {editing && <span style={{ fontWeight: 400, color: T.muted }}>· leave blank to keep the current token</span>}
          <input style={field} type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder={editing ? "•••••••• (unchanged)" : ""} required={!editing} />
        </label>
        <label style={label}>Location ID <span style={{ fontWeight: 400, color: T.muted }}>· from the old-location GHL URL</span>
          <input style={field} value={locationId} onChange={(e) => setLocationId(e.target.value)} required />
        </label>
        {err && <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.poppyText, marginTop: 12 }}>{err}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18 }}>
          <button type="button" onClick={onClose} style={btn()}>Cancel</button>
          <button type="submit" disabled={busy} style={busy ? btn("disabled") : btn("primary")}>{busy ? "Saving…" : editing ? "Save changes" : "Connect"}</button>
        </div>
      </form>
    </div>
  );
}

/* ── Legacy Stripe → GHL delta-import panel (generate + watermark) ── */

function LegacyDeltaPanel({ live }) {
  const [d, setD] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  function load() {
    if (!live) { setD({ connected: true, pending_count: 3, through: "2026-07-09", total_forum_charges: 261 }); return; }
    getJSON("/integrations/stripe_legacy/delta").then(setD).catch(() => setD(null));
  }
  useEffect(load, []);

  async function download() {
    if (!live) return;
    setBusy(true); setMsg(null);
    try {
      // Through getBlob, not a hand-built fetch. That read the token from localStorage only, so a
      // session without "remember me" sent `Bearer null` and every download "failed" with advice
      // to re-sync. getBlob sends the same headers as every other call, and an expired session is
      // announced like any other 401 instead of being blamed on the sync.
      const blob = new Blob([await getBlob("/integrations/stripe_legacy/delta.csv")],
                            { type: "text/csv" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `forum_legacy_delta_${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
      setMsg("Downloaded. Import it in GHL (don't open it in Excel first), then mark it imported.");
    } catch { setMsg("Download failed — try Sync now, then retry."); } finally { setBusy(false); }
  }

  async function markImported() {
    if (!live) return;
    if (!window.confirm("Mark these as imported? The next file will only include charges after them.")) return;
    setBusy(true); setMsg(null);
    try { const r = await postJSON("/integrations/stripe_legacy/delta/mark-imported"); setMsg(`Marked ${r.marked} imported · watermark now ${r.through || "—"}.`); load(); }
    catch { setMsg("Couldn't update the watermark."); } finally { setBusy(false); }
  }

  if (!d) return null;
  const pending = d.pending_count || 0;
  return (
    <div style={{ border: `1px solid ${T.line}`, borderRadius: 10, background: T.parchment, padding: "13px 15px", marginTop: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <Icon name="open" size={13} color={T.evergreen} />
        <span style={{ fontFamily: "var(--font-display)", fontSize: 12.5, fontWeight: 600, color: T.ink }}>GHL import file</span>
        <span style={{ flex: 1 }} />
        <span style={{ fontFamily: "var(--font-text)", fontSize: 11, color: T.muted }}>{d.total_forum_charges || 0} legacy charges synced</span>
      </div>
      <div style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.secondary, marginTop: 6, lineHeight: 1.5 }}>
        {pending > 0
          ? <><b style={{ color: T.ink }}>{pending}</b> new charge{pending === 1 ? "" : "s"} to import{d.through ? <> since {d.through}</> : ""}. GHL has no import API, so download the file and upload it in GHL manually — the dashboard already counts these; this just keeps GHL contacts current.</>
          : <>Up to date — no new charges to import{d.through ? <> since {d.through}</> : ""}.</>}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 11 }}>
        <SBtn small icon="open" disabled={!live || busy || pending === 0} onClick={download}>{busy ? "Working…" : "Download file"}</SBtn>
        <SBtn small icon="check_circled" disabled={!live || busy || pending === 0} onClick={markImported}>Mark imported</SBtn>
      </div>
      {msg && <div style={{ fontFamily: "var(--font-text)", fontSize: 11.5, color: T.slate, marginTop: 9 }}>{msg}</div>}
    </div>
  );
}

/* ── QuickBooks: connect each entity to its own QBO company ──── */

function QuickBooksConnect({ live }) {
  const [businesses, setBusinesses] = useState(null);
  const [rows, setRows] = useState([]);
  const [busy, setBusy] = useState(null);

  function load() {
    if (!live) { setBusinesses(SAMPLE_BUSINESSES); setRows([]); return; }
    Promise.all([getJSON("/businesses"), getJSON("/integrations")])
      .then(([b, i]) => { setBusinesses(b); setRows(i.filter((x) => x.provider === "qbo")); })
      .catch(() => setBusinesses([]));
  }
  useEffect(load, []);

  async function connect(biz) {
    try {
      const { url } = await getJSON(`/integrations/qbo/connect?business_key=${encodeURIComponent(biz.key)}`);
      window.location.href = url;               // hand off to Intuit's consent screen
    } catch { /* leave as-is; the user can retry */ }
  }

  async function syncNow(row) {
    setBusy(row.id);
    const prev = row.last_synced_at;
    try {
      await postJSON(`/integrations/${row.id}/sync`);
      for (let i = 0; i < 30; i++) {
        await sleep(5000);
        const fresh = (await getJSON("/integrations")).filter((x) => x.provider === "qbo");
        setRows(fresh);
        const r = fresh.find((x) => x.id === row.id);
        if (r && (r.last_synced_at !== prev || r.status === "error")) break;
      }
    } catch { /* the refresh surfaces any error */ } finally { setBusy(null); }
  }

  async function disconnect(row) {
    if (!window.confirm("Disconnect this QuickBooks company?")) return;
    setBusy(row.id);
    try { await postJSON(`/integrations/${row.id}/disconnect`); load(); } finally { setBusy(null); }
  }

  if (!businesses) return null;
  return (
    <Card title="QuickBooks" hint="Connect each entity to its QuickBooks company. Financial panels light up once connected and synced.">
      {!live && (
        <div style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.poppyText, background: alpha(T.poppyText, 0.08), border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 12px", marginBottom: 14 }}>
          Preview (sample) — connect to the live API to link QuickBooks.
        </div>
      )}
      {businesses.map((b) => {
        const row = rows.find((r) => r.business_key === b.key);
        const connected = row && (row.status === "connected" || row.status === "error");
        const syncing = row && busy === row.id;
        return (
          <div key={b.key} style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 0", borderTop: `1px solid ${T.line}` }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontFamily: "var(--font-text)", fontSize: 13.5, fontWeight: 600, color: T.ink }}>{b.name}</div>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 3 }}>
                {connected ? (
                  <>
                    <span style={{ width: 7, height: 7, borderRadius: 99, background: STATUS_DOT[row.status] || T.muted }} />
                    <span style={{ fontFamily: "var(--font-text)", fontSize: 11.5, color: T.slate }}>{STATUS_LABEL[row.status] || row.status}</span>
                    {row.last_synced_at && <span style={{ fontFamily: "var(--font-text)", fontSize: 11.5, color: T.muted }}>· synced {relativeTime(row.last_synced_at)}</span>}
                    {row.status === "error" && row.last_error && <span title={row.last_error} style={{ fontFamily: "var(--font-text)", fontSize: 11.5, color: T.poppyText }}>· {String(row.last_error).slice(0, 40)}</span>}
                  </>
                ) : (
                  <span style={{ fontFamily: "var(--font-text)", fontSize: 11.5, color: T.muted }}>Not connected</span>
                )}
              </div>
            </div>
            {connected ? (
              <>
                <button disabled={!live || syncing} onClick={() => syncNow(row)} style={live && !syncing ? btn() : btn("disabled")}>{syncing ? "Syncing…" : row.status === "error" ? "Retry" : "Sync now"}</button>
                <button disabled={!live || syncing} onClick={() => connect(b)} style={live && !syncing ? btn() : btn("disabled")} title="Re-authorize this QuickBooks company">Reconnect</button>
                <button disabled={!live || syncing} onClick={() => disconnect(row)} style={live && !syncing ? btn("danger") : btn("disabled")}>Disconnect</button>
              </>
            ) : (
              <button disabled={!live} onClick={() => connect(b)} style={live ? btn("primary") : btn("disabled")}>Connect QuickBooks</button>
            )}
          </div>
        );
      })}
    </Card>
  );
}

/* ── integrations (accordion revamp, spec v3 Part 1) ───────────── */

/* The words a status wears.
 *
 * `attention` counts what is actually broken rather than saying so vaguely: QuickBooks with one
 * dead entity out of five reads "1 needs action", which is the difference between a number
 * somebody can act on and an adjective they have to go and investigate.
 */
function statusLabel(src) {
  if (src.status === "ok") return "Healthy";
  if (src.status === "stale") return "Degraded";
  const broken = (src.entities || []).filter((e) => e.state !== "ok").length;
  return broken ? `${broken} needs action` : "Needs action";
}

const STATUS_TONE = {
  ok: { fg: T.meadowInk, bg: T.meadowBg, dot: T.meadow },
  stale: { fg: T.daffodilText, bg: T.daffodilBg, dot: T.daffodil },
  attention: { fg: T.poppyText, bg: alpha(T.poppyText, 0.1), dot: T.poppyText },
};

function StatusPill({ src }) {
  const tone = STATUS_TONE[src.status] || STATUS_TONE.attention;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 7, whiteSpace: "nowrap",
      padding: "4px 10px", borderRadius: 999, background: tone.bg, color: tone.fg,
      fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 500,
    }}>
      {/* Meaning never lives in colour alone: the dot is decoration, the word carries it. */}
      <span aria-hidden style={{ width: 6, height: 6, borderRadius: 999, background: tone.dot }} />
      {statusLabel(src)}
    </span>
  );
}

const Chip = ({ children }) => (
  <span style={{ fontFamily: "var(--font-text)", fontSize: 10.5, fontWeight: 600, color: T.slate,
    background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 5, padding: "2px 8px" }}>{children}</span>
);

/* FeedDots is gone. It rendered `feeds` -- a list of business KEYS -- as coloured dots beside
   the name, which asked a reader to know the workspace's colour code by heart. The drawer names
   those same businesses in full, with the same accent as a swatch, so the dots were a worse copy
   of a better list. `feeds` stays on the payload; one meaning per commit. */

function SBtn({ kind = "ghost", small, icon, children, onClick, disabled, title }) {
  const s = { ghost: { bg: T.white, color: T.slate, border: T.line }, primary: { bg: T.poppy, color: "#fff", border: T.poppy } }[kind];
  return (
    <button className={`si-btn ${kind}`} onClick={onClick} disabled={disabled} title={title} style={{
      display: "inline-flex", alignItems: "center", gap: 6, background: disabled ? T.parchment : s.bg,
      color: disabled ? T.muted : s.color, border: `1px solid ${disabled ? T.line : s.border}`, borderRadius: 8,
      padding: small ? "5px 11px" : "7px 14px", fontFamily: "var(--font-display)", fontSize: small ? 11.5 : 12,
      fontWeight: 600, cursor: disabled ? "not-allowed" : "pointer" }}>
      {icon && <Icon name={icon} size={12} color="currentColor" />}{children}
    </button>
  );
}

/* ── QBO entity: create (name → destination page → connect) or edit / re-route ── */
const ROUTABLE_LABELS = { forum: "The Forum", becollective: "beCollective", edge: "The Edge", ulrg: "ULRG + Team", sympli: "Sympli Mortgage" };
const NON_ROUTABLE = new Set(["portfolio", "flywheel", "books"]);
const routableLabel = (k) => ROUTABLE_LABELS[k] || k.charAt(0).toUpperCase() + k.slice(1);

function QboEntityForm({ mode, entity, onClose, onDone }) {
  const editing = mode === "edit";
  const [name, setName] = useState(editing ? entity.business_name : "");
  const [dest, setDest] = useState(editing ? (entity.display_tab || entity.business_key) : "");
  const [kind, setKind] = useState("membership");
  const [books, setBooks] = useState(editing ? entity.books_enabled !== false : true);
  const [inPortfolio, setInPortfolio] = useState(true);
  const [backfill, setBackfill] = useState("");
  const [tabs, setTabs] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  useEffect(() => {
    getJSON("/tenant/tabs")
      .then((t) => setTabs((t.tabs || []).filter((x) => !NON_ROUTABLE.has(x))))
      .catch(() => {});
  }, []);
  // in edit mode, make sure the entity's current page is a selectable option
  const options = tabs.includes(dest) || !dest ? tabs : [dest, ...tabs];

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      const display_tab = dest === "__new__" ? "" : dest;   // "" → backend gives it its own page
      if (editing) {
        await patchJSON(`/integrations/qbo/entities/${entity.business_key}`, {
          display_tab, include_in_portfolio: inPortfolio, books_enabled: books,
          name: name.trim() || undefined });
        onDone();
      } else {
        const { business_key } = await postJSON("/integrations/qbo/entities", {
          name: name.trim(), display_tab, kind, books_enabled: books,
          include_in_portfolio: inPortfolio, books_backfill_start: backfill || undefined });
        const { url } = await getJSON(`/integrations/qbo/connect?business_key=${encodeURIComponent(business_key)}`);
        window.location.href = url;   // hand off to Intuit's consent screen
      }
    } catch {
      setErr("Couldn't save — check the details and try again.");
      setBusy(false);
    }
  }

  const field = { width: "100%", boxSizing: "border-box", fontFamily: "var(--font-text)", fontSize: 13, color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "9px 11px", marginTop: 5 };
  const label = { display: "block", fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate, marginTop: 14 };
  const check = { display: "flex", alignItems: "center", gap: 8, marginTop: 14, fontFamily: "var(--font-text)", fontSize: 12.5, color: T.ink };

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,46,44,0.34)", zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <form onClick={(e) => e.stopPropagation()} onSubmit={submit} style={{ width: "100%", maxWidth: 430, background: T.white, borderRadius: 14, padding: 22, boxShadow: "0 20px 60px rgba(0,46,44,.22)" }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 600, color: T.ink }}>{editing ? `Edit ${entity.business_name}` : "Connect a QuickBooks entity"}</div>
        <div style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.muted, marginTop: 3 }}>
          {editing ? "Change where this entity's P&L shows and whether it feeds Books. Its ledger keys don't move."
                   : "Name it, pick which page its P&L shows on, then authorize in QuickBooks."}
        </div>
        <label style={label}>Entity name
          <input style={field} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. The Forum" required />
        </label>
        <label style={label}>Show its P&amp;L on
          <select style={field} value={dest} onChange={(e) => setDest(e.target.value)}>
            <option value="">Its own new page</option>
            {options.map((t) => <option key={t} value={t}>{routableLabel(t)}</option>)}
          </select>
        </label>
        {!editing && (
          <label style={label}>Entity type
            <select style={field} value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="membership">Membership / coaching (Booked P&amp;L)</option>
              <option value="holding">Holding company</option>
              <option value="real_estate">Real estate brokerage</option>
              <option value="commission_jv">Commission JV</option>
            </select>
          </label>
        )}
        <label style={check}>
          <input type="checkbox" checked={books} onChange={(e) => setBooks(e.target.checked)} />
          Feed this entity into Axcion Books (approval queue, close)
        </label>
        <label style={check}>
          <input type="checkbox" checked={inPortfolio} onChange={(e) => setInPortfolio(e.target.checked)} />
          Include in portfolio totals
        </label>
        {!editing && (
          <label style={label}>Backfill transactions from <span style={{ fontWeight: 400, color: T.muted }}>· optional (default: Jan 1)</span>
            <input style={field} type="date" value={backfill} onChange={(e) => setBackfill(e.target.value)} />
          </label>
        )}
        {err && <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.poppyText, marginTop: 12 }}>{err}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18 }}>
          <button type="button" onClick={onClose} style={btn()}>Cancel</button>
          <button type="submit" disabled={busy} style={busy ? btn("disabled") : btn("primary")}>
            {busy ? "Saving…" : editing ? "Save changes" : "Continue to QuickBooks"}</button>
        </div>
      </form>
    </div>
  );
}

/* CORE_ENTITIES is gone, and BIZ_DOT with it.
 *
 * CORE_ENTITIES listed one customer's three business keys and refused to offer Remove for them.
 * delete_qbo_entity already enforces the real rule -- the workspace's last business is protected,
 * and so is one with a non-QBO source attached -- per tenant, and its own docstring points out
 * that the hardcoded three "protected her and nobody else": a second workspace is provisioned
 * with a single business, so its whole dashboard was one confirm dialog away. The server says
 * whether Remove is on offer now, in EntityRow.actions, so there is one rule instead of two.
 *
 * BIZ_DOT mapped those same keys to colours. An entity's accent comes from the payload, which is
 * the workspace's own and is editable under Businesses. */

/* The per-entity overflow menu.
 *
 * ITS ITEMS COME FROM THE PAYLOAD. `EntityRow.actions` is built on the server, so the menu can
 * only offer what the API will actually accept for that row in that state -- which is the
 * dead-button failure this module shipped three times, closed at the source rather than by
 * remembering to keep two lists in step.
 */
function EntityMenu({ items, disabled }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return undefined;
    const close = () => setOpen(false);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [open]);
  if (!items.length) return null;
  return (
    <span style={{ position: "relative", flexShrink: 0 }}>
      {/* Disabled rather than hidden offline. A control that disappears in the preview cannot be
          reviewed there, and this page's whole offline mode exists to be reviewed. */}
      <button aria-label="More actions" aria-haspopup="menu" aria-expanded={open} disabled={disabled}
        onClick={(ev) => { ev.stopPropagation(); setOpen((o) => !o); }}
        style={{ width: 30, height: 30, border: `1px solid ${T.line}`, borderRadius: 8,
          background: T.white, cursor: disabled ? "default" : "pointer",
          color: disabled ? T.muted : T.slate, fontSize: 13, lineHeight: 1 }}>⋯</button>
      {open && (
        <span role="menu" style={{ position: "absolute", right: 0, top: 34, zIndex: 5, minWidth: 176,
          background: T.white, border: `1px solid ${T.line}`, borderRadius: 10, padding: 5,
          boxShadow: "0 10px 26px rgba(0,46,44,.14)", display: "flex", flexDirection: "column" }}>
          {items.map((it) => (
            <button key={it.label} role="menuitem" disabled={it.disabled}
              onClick={(ev) => { ev.stopPropagation(); setOpen(false); it.onClick(); }}
              style={{ textAlign: "left", border: 0, background: "transparent", cursor: "pointer",
                borderRadius: 7, padding: "7px 9px", fontFamily: "var(--font-text)", fontSize: 12.5,
                color: it.danger ? T.poppyText : T.slate }}>{it.label}</button>
          ))}
        </span>
      )}
    </span>
  );
}

/* One connection. QuickBooks has several of these; every other source has exactly one -- and
   until this phase that difference was the page's whole shape, with QuickBooks rendering an
   entity list and everything else rendering a bare card. */
function EntityRow({ e, live, busy, onAction }) {
  const err = e.state === "error";
  const disc = e.state === "disconnected";
  const can = (a) => (e.actions || []).includes(a);
  const routesTo = e.display_tab && e.display_tab !== e.business_key ? routableLabel(e.display_tab) : null;
  const stateText = err ? (e.detail || "Re-authorize to resume syncing")
    : disc ? "Disconnected — reconnect to resume"
    : (e.last_synced_at ? `Synced ${relativeTime(e.last_synced_at)}` : "Not synced");
  const menu = [];
  if (can("edit")) menu.push({ label: "Edit", onClick: () => onAction("edit", e) });
  if (can("disconnect")) menu.push({ label: "Disconnect", onClick: () => onAction("disconnect", e) });
  if (can("remove")) menu.push({ label: "Remove entirely", danger: true, onClick: () => onAction("remove", e) });
  const syncing = busy === e.integration_id;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap",
      padding: "11px 13px", borderBottom: `1px solid ${T.line}`, opacity: disc ? 0.7 : 1 }}>
      {/* The workspace's own accent for this business, straight off the payload. */}
      <span aria-hidden style={{ width: 8, height: 8, flexShrink: 0, borderRadius: 3,
        background: disc ? T.muted : (e.accent || T.muted) }} />
      <div style={{ minWidth: 0, flex: 1, display: "flex", flexDirection: "column", gap: 1 }}>
        <span style={{ fontFamily: "var(--font-display)", fontSize: 13.5, fontWeight: 600,
          color: T.ink, display: "flex", alignItems: "center", gap: 7, flexWrap: "wrap" }}>
          {e.business_name}
          {routesTo && <span style={{ fontFamily: "var(--font-text)", fontSize: 10.5, fontWeight: 600, color: T.slate, background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 5, padding: "1px 6px" }}>→ {routesTo}</span>}
          {e.books_enabled === false && <span style={{ fontFamily: "var(--font-text)", fontSize: 10.5, color: T.muted, border: `1px solid ${T.line}`, borderRadius: 5, padding: "1px 6px" }}>Books off</span>}
        </span>
        {e.realm_id && (
          <span style={{ fontFamily: "var(--font-data)", fontSize: 11, color: T.muted }}>ID {e.realm_id}</span>
        )}
        {/* This connection's own configuration, on this connection's own row. */}
        {e.config_summary?.length > 0 && (
          <span style={{ fontFamily: "var(--font-text)", fontSize: 11, color: T.muted }}>
            {e.config_summary.map(([k, v]) => `${k} ${v}`).join(" · ")}</span>
        )}
      </div>
      <span style={{ fontFamily: "var(--font-text)", fontSize: 12, flexShrink: 0,
        color: err ? T.poppyText : disc ? T.muted : T.slate, textAlign: "right" }}>{stateText}</span>
      <span style={{ display: "flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
        {can("sync") && (
          <SBtn small icon="sync" disabled={!live || syncing}
            onClick={() => onAction("sync", e)}>{syncing ? "Syncing…" : "Sync"}</SBtn>
        )}
        {can("reconnect") && (
          <SBtn kind="primary" small disabled={!live} onClick={() => onAction("reconnect", e)}>Reconnect</SBtn>
        )}
        <EntityMenu items={menu} disabled={!live} />
      </span>
    </div>
  );
}

/* "Add source" opens THIS rather than scrolling to the Available list.
 *
 * Everything we support is already on the page, so the button has no catalogue to fetch -- what
 * it has is the list of things not connected yet, which is a different question from "what is
 * broken" and deserves its own surface rather than a jump.
 *
 * It is also where the SECONDARY sources live. A second GHL location or a legacy Stripe belongs
 * to one workspace's arrangements; listed in Available they read as a catalogue of somebody
 * else's programmes, but a workspace that genuinely wants one has to be able to find it. Behind
 * a toggle is the difference between offering and hiding.
 */
function AddSourceModal({ sources, live, onPick, onClose }) {
  const [showLegacy, setShowLegacy] = useState(false);
  const plain = sources.filter((s) => !s.secondary);
  const legacy = sources.filter((s) => s.secondary);
  const shown = showLegacy ? [...plain, ...legacy] : plain;
  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 60, background: "rgba(0,32,30,.36)",
      display: "flex", alignItems: "flex-start", justifyContent: "center", padding: "8vh 16px" }}>
      <div role="dialog" aria-label="Add a source" onClick={(ev) => ev.stopPropagation()}
        style={{ width: "100%", maxWidth: 520, background: T.white, borderRadius: 14,
          border: `1px solid ${T.line}`, boxShadow: "0 24px 60px rgba(0,46,44,.22)", overflow: "hidden" }}>
        <div style={{ padding: "16px 18px", borderBottom: `1px solid ${T.line}` }}>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 15.5, fontWeight: 600, color: T.ink }}>Add a source</div>
          <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.muted, marginTop: 3 }}>
            Everything the Command Center can read from. Connecting one starts its first sync.</div>
        </div>
        <div style={{ maxHeight: "46vh", overflowY: "auto" }}>
          {shown.length === 0 && (
            <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.muted, padding: "20px 18px" }}>
              Everything we support is already connected.</div>
          )}
          {shown.map((s) => (
            <button key={s.provider} disabled={!live} onClick={() => onPick(s)}
              style={{ display: "flex", alignItems: "center", gap: 13, width: "100%", textAlign: "left",
                background: "transparent", border: "none", borderBottom: `1px solid ${T.line}`,
                padding: "13px 18px", cursor: live ? "pointer" : "default" }}>
              <IntegrationMark provider={s.provider} fallback={s.mono} size={30} />
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: "block", fontFamily: "var(--font-display)", fontSize: 13.5, fontWeight: 600, color: T.ink }}>
                  {s.title}{s.tag ? ` · ${s.tag}` : ""}</span>
                <span style={{ display: "block", fontFamily: "var(--font-text)", fontSize: 11.5, color: T.muted, marginTop: 2 }}>
                  {s.meta || s.desc(s)}</span>
              </span>
              <span style={{ fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.teal, flexShrink: 0 }}>Connect</span>
            </button>
          ))}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "12px 18px", flexWrap: "wrap" }}>
          {legacy.length > 0 && (
            <button onClick={() => setShowLegacy((v) => !v)} style={{ border: "none", background: "transparent",
              cursor: "pointer", fontFamily: "var(--font-text)", fontSize: 12, color: T.slate, padding: 0 }}>
              {showLegacy ? "Hide" : "Show"} legacy and second-account connectors ({legacy.length})</button>
          )}
          <span style={{ flex: 1 }} />
          <SBtn onClick={onClose}>Close</SBtn>
        </div>
      </div>
    </div>
  );
}

function SourceCard({ s, open, onToggle, live, busy, onEntityAction, onConnect }) {
  const collapsedLine = s.fresh;
  const entities = s.entities || [];
  return (
    <div className={`si-card ${open ? "on" : ""}`}>
      <div className="si-head" role="button" tabIndex={0} aria-expanded={open} onClick={onToggle}
        onKeyDown={(ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); onToggle(); } }}>
        <IntegrationMark provider={s.provider} fallback={s.mono} />
        <span style={{ flex: 1, minWidth: 0, textAlign: "left" }}>
          <span style={{ display: "flex", alignItems: "center", gap: 9 }}>
            <span style={{ fontFamily: "var(--font-display)", fontSize: 14.5, fontWeight: 600, color: T.ink }}>{s.title}</span>
            {/* Server-side, so the chip and the list beneath it cannot disagree about how many. */}
            {s.tag && (
              <span style={{ fontFamily: "var(--font-text)", fontSize: 11.5, fontWeight: 500,
                color: T.slate, background: T.parchment, borderRadius: 6, padding: "2px 8px",
                whiteSpace: "nowrap" }}>{s.tag}</span>
            )}
          </span>
          {/* The CATEGORY line, which is the same on every row of a kind -- what this source is
              for. The long description moved into the drawer, where there is room to read it. */}
          <span style={{ display: "block", fontFamily: "var(--font-text)", fontSize: 12, color: T.muted, marginTop: 3 }}>
            {s.status === "attention" && s.status_note ? s.status_note : (s.meta || collapsedLine)}</span>
        </span>
        <span style={{ display: "flex", alignItems: "center", gap: 16, flexShrink: 0 }}>
          <StatusPill src={s} />
          {/* A fixed column in the data face, so the ages line up down the page. */}
          <span style={{ fontFamily: "var(--font-data)", fontSize: 11.5, color: T.muted,
            width: 62, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{s.ago || ""}</span>
          <span aria-hidden style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 22, height: 22, flexShrink: 0 }}>
            <Icon name="chevron_down" size={14} color={T.muted} style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform .22s ease" }} />
          </span>
        </span>
      </div>
      <div className={`si-collapse ${open ? "open" : ""}`}>
        <div className="si-collapse-in">
          <div style={{ padding: "0 20px 18px" }}>
            {/* A vendor row's own description, not one member's: DESC is written per provider,
                so a two-location row would otherwise be described by whichever came first. */}
            <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.secondary, paddingBottom: 12, lineHeight: 1.5 }}>
              {(s.members?.length || 1) > 1 ? s.meta : s.desc(s)}</div>

            {entities.length > 0 && (
              <>
                <div style={{ display: "flex", alignItems: "center", gap: 10, paddingBottom: 9 }}>
                  <span style={{ fontFamily: "var(--font-data)", fontSize: 10, letterSpacing: ".14em", textTransform: "uppercase", color: T.muted }}>
                    {s.multi_entity ? `Entities · ${entities.length} · one connection each` : "Connection"}</span>
                  <span aria-hidden style={{ flex: 1, height: 1, background: T.line }} />
                </div>
                <div style={{ border: `1px solid ${T.line}`, borderRadius: 11, background: T.white, overflow: "hidden" }}>
                  {entities.map((e, i) => (
                    <div key={e.integration_id} style={i === entities.length - 1 ? { marginBottom: -1 } : undefined}>
                      <EntityRow e={e} live={live} busy={busy} onAction={onEntityAction} />
                    </div>
                  ))}
                </div>
              </>
            )}

            {(s.members || [s.provider]).includes("stripe_legacy") && <LegacyDeltaPanel live={live} />}

            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between",
              gap: 16, flexWrap: "wrap", marginTop: 14 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                {/* "Feeds" is what the mockup calls these, and it is the better word: it is what
                    this source puts INTO the dashboard. The payload still calls it `provides`. */}
                <span style={{ fontFamily: "var(--font-data)", fontSize: 10, fontWeight: 700, letterSpacing: ".14em", textTransform: "uppercase", color: T.muted }}>Feeds</span>
                {(s.provides || []).map((c) => <Chip key={c}>{c}</Chip>)}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                {s.last_run && <span style={{ fontFamily: "var(--font-data)", fontSize: 11, color: s.status === "stale" ? T.daffodilText : T.muted }}>{s.last_run}</span>}
                {/* Only where a second connection is actually possible -- `multi_entity` says so
                    on the payload rather than the page testing for QuickBooks by name. */}
                {s.multi_entity && (
                  <button disabled={!live} onClick={() => onConnect(s)} style={{
                    height: 32, padding: "0 13px", borderRadius: 9, cursor: live ? "pointer" : "default",
                    border: `1px dashed ${T.line}`, background: "transparent",
                    fontFamily: "var(--font-text)", fontSize: 12.5, color: T.slate, whiteSpace: "nowrap",
                  }}>+ Connect another entity</button>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

const MONO = { qbo: "QB", sisu: "Si", fub: "FB", ghl: "GH", ghl_bc: "bC", ghl_recruiting: "GR", arive: "Ar", stripe_legacy: "St", stripe_bc: "Sb", ghl_legacy: "GL", meta_ads: "Ma" };
const DESC = {
  qbo: () => "Financial source of truth · one connection per entity",
  sisu: () => "Real estate production — transactions, agents, GCI",
  fub: () => "CRM — leads and agent activity",
  ghl: () => "The Forum — members, renewals, subscriptions, events",
  ghl_bc: () => "beCollective — its own GHL location; members, cohort onboarding, events",
  ghl_recruiting: () => "The brokerage's recruiting location — candidates, appointments and stage history. Powers the Recruiting tab",
  arive: () => "Uses your Arive API key · lights up Sympli's pipeline and the referral flywheel",
  stripe_legacy: () => "Original Stripe · read-only. Backfills legacy dues the newer sub-account never sees, and feeds the GHL delta-import file",
  stripe_bc: () => "A program's own Stripe · read-only. Membership payments only (event tickets + other products filtered out) — powers the Cash & Billing view",
  ghl_legacy: () => "The legacy GHL location · read-only. Labels each legacy Stripe charge (join by charge id) so the classifier knows what it's for",
  meta_ads: () => "Paid social delivery, joined through to registrations and enrollments · read-only. Needs a System User token with ads_read — never ads_management",
};

/* A provider with no DESC entry used to WHITE-SCREEN this whole page.
 *
 * decorate() assigned `desc: DESC[s.provider]`, the card called `s.desc(s)`, and calling
 * undefined threw during render — React unmounted the tree and Settings went blank. It only
 * showed AFTER loading, because the crash needs the fetched source list to render.
 *
 * The trigger was adding meta_ads to the API's source list without adding it here. That is the
 * same shape as the Sisu and FUB dead buttons: backend support for a provider landed, the
 * frontend half did not, and the API test passed because it never rendered anything. So the
 * lookup falls back rather than assuming every provider the server can return is known here —
 * a new provider should look plain, not take the page down. */
const descFor = (provider) => DESC[provider] || (() => "");
const _src = (o) => ({ feeds: [], provides: [], entities: [], config_summary: [],
  last_run: null, integration_id: null, business_key: null, business_name: null,
  needs_kind: null, tag: null, ago: null, secondary: false, multi_entity: false, ...o });

/* The one-row entities[] a single-connection source carries, so the drawer renders one table for
   every source and QuickBooks stops being the shape the page is built around. */
const _one = (provider, id, key, name, accent, state = "ok", detail = null) => [{
  integration_id: id, business_key: key, business_name: name, accent, state, detail, provider,
  last_synced_at: state === "ok" ? new Date(Date.now() - 26 * 60000).toISOString() : null,
  actions: (state === "ok" ? ["sync"] : ["reconnect"]).concat(["edit", "disconnect"]),
}];

/* The offline payload, field for field with the live one.
 *
 * EVERY PROVIDER THE API CAN RETURN IS HERE, on purpose. A provider missing from this list is
 * why a white screen could not be reproduced locally: the crash needed the fetched source list
 * to render, and the sample was a different shape than production. */
/* The offline payload, field for field with the live one — VENDOR ROWS, the shape the server
 * sends since the merge. Every provider the API can return is here, as a row's own provider or
 * inside the `members` of the row that stands for it. A provider missing from this list is why a
 * white screen could not be reproduced locally: the crash needed the fetched list to render, so
 * the preview was exercising a different payload and looked fine. */
const SAMPLE_VIEW = {
  healthy: 2, total: 8, next_sync_in_min: 14, entities_mapped: 7, needs_attention: 2,
  alerts: [{ title: "Spring B lost its QuickBooks connection",
    detail: "Financials for this entity stopped updating. Last synced 3 days ago.",
    provider: "qbo", business_key: "springb", action: "reconnect" }],
  sources: [
    _src({ provider: "qbo", vendor: "QuickBooks", family: "qbo", category: "Financials",
      meta: "Financials · profit & loss, balance sheet", name: "QuickBooks",
      members: ["qbo"], connect_provider: "qbo", multi_entity: true,
      status: "attention", status_note: "1 of 3 entities needs reconnect", tag: "3 entities",
      ago: "7 min", feeds: ["ulrg", "springb", "sympli"],
      provides: ["Profit & Loss", "Balance Sheet"], last_run: "Last run · 2 entities · 4.2s",
      entities: [
        { integration_id: "e1", business_key: "ulrg", business_name: "ULRG + Team", state: "ok", provider: "qbo",
          accent: "#61835E", actions: ["sync", "edit", "disconnect"], config_summary: [],
          last_synced_at: new Date(Date.now() - 32 * 60000).toISOString(), realm_id: "9130 3540 11" },
        { integration_id: "e2", business_key: "springb", business_name: "Spring B", state: "error", provider: "qbo",
          accent: "#FA8069", actions: ["reconnect", "edit", "disconnect"], config_summary: [], detail: "Token expired Jun 29" },
        { integration_id: "e3", business_key: "sympli", business_name: "Sympli Mortgage", state: "ok", provider: "qbo",
          accent: "#227175", actions: ["sync", "edit", "disconnect", "remove"], config_summary: [],
          last_synced_at: new Date(Date.now() - 32 * 60000).toISOString(), realm_id: "9130 3541 88" },
      ] }),
    _src({ provider: "sisu", vendor: "Sisu", family: "sisu", category: "Production",
      meta: "Production · closings, agents and GCI", name: "Sisu", status: "ok",
      members: ["sisu"], connect_provider: "sisu",
      fresh: "Synced 26 min ago", ago: "26 min", feeds: ["ulrg"], business_name: "ULRG + Team",
      provides: ["Transactions", "Agents", "GCI"], last_run: "Last run · 412 records · 3.1s",
      integration_id: "s1", entities: _one("sisu", "s1", "ulrg", "ULRG + Team", "#61835E") }),
    _src({ provider: "fub", vendor: "Follow Up Boss", family: "fub", category: "CRM",
      meta: "CRM · leads and agent activity", name: "Follow Up Boss", status: "stale",
      members: ["fub"], connect_provider: "fub",
      fresh: "Synced 19 hours ago", ago: "19 hr", feeds: ["ulrg"], business_name: "ULRG + Team",
      provides: ["Leads", "Agents"], integration_id: "f1",
      entities: _one("fub", "f1", "ulrg", "ULRG + Team", "#61835E"),
      last_run: "Auto-sync has missed its last 37 runs — check the connection" }),
    /* ONE ROW, TWO PROVIDERS. Each connection keeps its own credentials and its own config, and
       carries that config on its own entity row — which is what makes the row honest. */
    _src({ provider: "ghl", vendor: "Go High Level", family: "ghl", category: "Marketing",
      meta: "Marketing · members, renewals and events", name: "Go High Level",
      // ghl_recruiting is the third member of this vendor row and is NOT connected in the
      // sample, which is why connect_provider names it: that is the state a brokerage is in
      // before it wires its recruiting location.
      members: ["ghl", "ghl_bc", "ghl_recruiting"], connect_provider: "ghl_recruiting",
      multi_entity: true,
      status: "ok", fresh: "Synced 1 hour ago", ago: "1 hr", tag: "2 entities",
      feeds: ["forum", "becollective"], provides: ["Members", "Subscriptions", "Events", "Onboarding"],
      last_run: "Last run · 142 members · 38 subscriptions · 2.4s", integration_id: "g1", config: {},
      entities: [
        { integration_id: "g1", business_key: "forum", business_name: "The Forum", state: "ok",
          provider: "ghl", accent: "#FFDD1F", actions: ["sync", "edit", "disconnect"],
          last_synced_at: new Date(Date.now() - 62 * 60000).toISOString(),
          config_summary: [["Location ID", "LqK4…f82"], ["Member tags", "5 tags"], ["Next event", "Park City, UT"]] },
        { integration_id: "gb1", business_key: "becollective", business_name: "beCollective", state: "ok",
          provider: "ghl_bc", accent: "#FFBA9F", actions: ["sync", "edit", "disconnect"],
          last_synced_at: new Date(Date.now() - 64 * 60000).toISOString(),
          config_summary: [["Location ID", "3JNm…Rnu"], ["Member tags", "3 tags"], ["Next event", "The Shift"]] },
      ] }),
    _src({ provider: "arive", vendor: "Arive", family: "arive", category: "Mortgage",
      meta: "Mortgage · pipeline and fundings", name: "Arive", status: "disconnected",
      members: ["arive"], connect_provider: "arive",
      provides: ["Loans", "Pipeline"], business_key: "sympli", business_name: "Sympli Mortgage" }),
    _src({ provider: "meta_ads", vendor: "Meta Ads", family: "meta", category: "Advertising",
      meta: "Advertising · spend, impressions and leads", name: "Meta Ads", status: "disconnected",
      members: ["meta_ads"], connect_provider: "meta_ads",
      provides: ["Spend", "Impressions", "Link clicks", "Leads"], business_key: "springb" }),
    _src({ provider: "stripe_legacy", vendor: "Stripe", family: "stripe", category: "Payments",
      meta: "Payments · legacy recurring dues", name: "Stripe", status: "disconnected",
      members: ["stripe_legacy", "stripe_bc"], connect_provider: "stripe_legacy",
      tag: "Legacy", secondary: true, business_key: "springb",
      provides: ["Legacy charges", "Recurring dues", "Membership payments"] }),
    _src({ provider: "ghl_legacy", vendor: "GHL charge labels", family: "ghl_legacy",
      category: "Mapping", meta: "Mapping · labels for legacy charges", name: "GHL charge labels",
      members: ["ghl_legacy"], connect_provider: "ghl_legacy",
      status: "disconnected", tag: "Legacy", secondary: true,
      business_key: "springb", provides: ["Charge labels", "Invoice line items"] }),
  ],
};

function IntegrationsPage() {
  const [view, setView] = useState(null);
  const [error, setError] = useState(false);
  const [open, setOpen] = useState({ qbo: true });
  const [busy, setBusy] = useState(null);
  const [syncingAll, setSyncingAll] = useState(false);
  const [connecting, setConnecting] = useState(null);
  const [filter, setFilter] = useState("All");
  const [adding, setAdding] = useState(false);
  const live = Boolean(API_BASE);

  /* The row's title is the VENDOR. One row per vendor now, so nothing needs disambiguating:
     "Go High Level" stands for both locations and each appears beneath it by name. This replaces
     META's hardcoded "Go High Level · The Forum", which named one customer's programmes to every
     workspace that opened the page. */
  function decorate(v) {
    return { ...v, sources: v.sources.map((s) => ({
      ...s,
      mono: MONO[s.provider] || (s.provider || "?").slice(0, 2),
      desc: descFor(s.provider),
      title: s.vendor || s.name,
    })) };
  }
  function load() {
    if (!live) { setView(decorate(SAMPLE_VIEW)); return; }
    getJSON("/settings/integrations").then((v) => setView(decorate(v))).catch(() => setError(true));
  }
  useEffect(load, []);

  async function qboConnect(businessKey) {
    try { const { url } = await getJSON(`/integrations/qbo/connect?business_key=${encodeURIComponent(businessKey)}`); window.location.href = url; } catch { /* retry */ }
  }
  async function syncOne(id) {
    if (!id) return;
    setBusy(id);
    try { await postJSON(`/integrations/${id}/sync`); for (let i = 0; i < 30; i++) { await sleep(5000); const v = await getJSON("/settings/integrations"); setView(decorate(v)); break; } }
    catch { /* refresh shows any error */ } finally { setBusy(null); load(); }
  }
  async function syncAll() {
    if (!live || syncingAll) return;
    setSyncingAll(true);
    try { const { job_id } = await postJSON(`/sync/all`); for (let i = 0; i < 40; i++) { await sleep(3000); const st = await getJSON(`/sync/status/${job_id}`); if (st.status === "ok" || st.status === "error") break; } load(); }
    catch { /* leave as-is */ } finally { setSyncingAll(false); }
  }
  /* ONE DISPATCHER for every per-entity action, because "reconnect" means two different things
     and the difference is the provider, not the button. QuickBooks re-authorizes through OAuth;
     every other source authenticates with a key somebody pastes, so its fix is its own form.
     The page used to answer this with five optional props, four of which were passed only when
     the provider happened to be qbo. */
  /* A vendor row can span two connections, so an action on one of them has to carry THAT
     connection's provider and config -- editing The Forum's location must not open beCollective's
     form with beCollective's location id in it. */
  const editConnection = (e) => setConnecting({
    provider: e.provider, name: e.business_name, config: e.config || {},
    business_key: e.business_key, integration_id: e.integration_id, status: "connected",
  });

  function entityAction(action, e, src) {
    if (action === "sync") return syncOne(e.integration_id);
    if (action === "reconnect") return e.provider === "qbo" ? qboConnect(e.business_key) : editConnection(e);
    if (action === "edit") return e.provider === "qbo" ? editEntity(e) : editConnection(e);
    if (action === "disconnect") return disconnectEntity(e);
    if (action === "remove") return deleteEntity(e);
    return undefined;
  }

  /* Which form a Connect button opens.
   *
   * GENERIC, and that is the point. This was a per-provider if-chain, and a provider without a
   * branch fell off the end returning undefined - a button that does nothing, with no error and
   * nothing in the console. That shipped twice: Sisu and Follow Up Boss, then Meta Ads. Adding a
   * tenth branch would only postpone the third.
   *
   * The hardcoded business keys are gone too. They read `s.business_key || "springb"`, which is
   * one customer's business key as a fallback for every workspace - the same defect the backend
   * CONNECTABLE map had. The API resolves the right business for THIS workspace by role and
   * returns it; if it returns none, there is nothing to attach the source to and the form would
   * fail anyway, so say so instead of opening it. */
  function connectSource(s) {
    if (s.provider === "qbo") return setConnecting({ provider: "qbo", mode: "create" });
    if (!s.business_key) {
      window.alert(
        `${s.name} attaches to a ${(s.needs_kind || "business").replace(/_/g, " ")} business, and `
        + `this workspace doesn't have one yet. Add it under Businesses first, then connect.`);
      return;
    }
    return setConnecting({
      // `connect_provider` is the member with no row yet, so connecting a workspace's SECOND
      // GHL location does not ask anybody to know it is called ghl_bc.
      provider: s.connect_provider || s.provider, name: s.name, config: s.config || {},
      business_key: s.business_key, integration_id: s.integration_id || null,
      status: s.status === "connected" || s.status === "error" ? s.status : "disconnected",
    });
  }

  // No DEFAULT_BIZ any more. It mapped each source to one customer's business key
  // ("arive" -> "sympli"), so every other workspace posted a key their API had never heard of
  // and got a 404 that the Stripe forms then reported as "Stripe rejected that key". The API
  // resolves the right business for THIS workspace by role and returns it as business_key.
  const editConfig = (s) => setConnecting({ provider: s.provider, name: s.name, config: s.config || {}, business_key: s.business_key, status: "connected" });
  const editEntity = (e) => setConnecting({ provider: "qbo", mode: "edit", entity: e });
  async function disconnectEntity(e) {
    if (!window.confirm(`Disconnect ${e.business_name}? Its tokens are removed; synced history stays (Remove deletes it entirely).`)) return;
    setBusy(e.integration_id);
    try { await postJSON(`/integrations/${e.integration_id}/disconnect`); } finally { setBusy(null); load(); }
  }
  async function deleteEntity(e) {
    if (!window.confirm(`Remove ${e.business_name} entirely? This deletes the connection and its synced ledger / P&L. This can't be undone.`)) return;
    setBusy(e.integration_id);
    try { await delJSON(`/integrations/qbo/entities/${e.business_key}`); } finally { setBusy(null); load(); }
  }

  if (error) return <Card title="Integrations"><div style={{ color: T.muted, fontSize: 13 }}>Couldn't load integrations.</div></Card>;
  if (!view) return <Card title="Integrations"><div style={{ color: T.muted, fontSize: 13 }}>Loading…</div></Card>;

  const toggle = (k) => setOpen((o) => ({ ...o, [k]: !o[k] }));
  const attention = view.needs_attention
    ?? view.sources.filter((s) => s.status === "attention" || s.status === "stale").length;
  const alerts = view.alerts || [];
  // The banner's button goes wherever that entity's own button would have gone.
  const fixAlert = (a) => {
    if (a.action === "reconnect" && a.business_key) return qboConnect(a.business_key);
    const src = view.sources.find((x) => x.provider === a.provider);
    if (src) connectSource(src);
  };

  /* Connected and Available are two different questions -- "is this working" and "could we use
     this" -- and one flat list answered neither. A SECONDARY source is a second account of a
     vendor that belongs to one workspace's arrangements (a second GHL location, a legacy
     Stripe); offered to every workspace they read as a catalogue of somebody else's programmes,
     so they appear only where a row already exists. */
  const connected = view.sources.filter((s) => s.status !== "disconnected");
  const available = view.sources.filter((s) => s.status === "disconnected"
    && (!s.secondary || s.integration_id));
  const addable = view.sources.filter((s) => s.status === "disconnected");
  const visible = connected.filter((s) => filter === "All" ? true
    : filter === "Healthy" ? s.status === "ok"
    : s.status !== "ok");

  return (
    <>
      <style>{`
        .si-card { background:${T.white}; border:1px solid ${T.line}; border-radius:14px; margin-bottom:12px; box-shadow:0 1px 2px rgba(0,46,44,.04); transition:box-shadow .2s ease, border-color .2s ease; }
        .si-card.on { box-shadow:0 2px 4px rgba(0,46,44,.05), 0 14px 30px rgba(0,46,44,.07); border-color:#E0D6C6; }
        .si-head { box-sizing:border-box; display:flex; align-items:center; gap:14px; width:100%; background:none; border:none; padding:15px 20px; cursor:pointer; }
        .si-mono { width:36px; height:36px; border-radius:10px; background:${T.parchment}; border:1px solid ${T.line}; display:inline-flex; align-items:center; justify-content:center; font-family:var(--font-display); font-size:12.5px; font-weight:700; color:${T.evergreen}; flex-shrink:0; }
        .si-collapse { display:grid; grid-template-rows:0fr; transition:grid-template-rows .26s cubic-bezier(.4,0,.2,1); }
        .si-collapse.open { grid-template-rows:1fr; }
        .si-collapse-in { overflow:hidden; }
        .si-btn { transition:background .14s ease, border-color .14s ease, color .14s ease; }
        .si-btn.ghost:hover:not(:disabled) { border-color:${T.teal}; color:${T.teal}; }
        .si-btn.primary:hover:not(:disabled) { background:${T.poppyActive}; border-color:${T.poppyActive}; }
        .si-danger { background:none; border:none; cursor:pointer; font-family:var(--font-text); font-size:11.5px; font-weight:600; color:${T.muted}; padding:4px 2px; }
        .si-danger:hover:not(:disabled) { color:${T.poppyText}; }
        .si-btn:focus-visible, .si-danger:focus-visible { outline:2px solid ${T.teal}; outline-offset:2px; border-radius:8px; }
        @media (prefers-reduced-motion: reduce) { .si-collapse { transition:none; } }
      `}</style>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 28, flexWrap: "wrap", marginBottom: 20 }}>
        <div style={{ maxWidth: 520 }}>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 10.5, fontWeight: 700, letterSpacing: ".13em", textTransform: "uppercase", color: T.poppyText, display: "flex", alignItems: "center", gap: 7 }}>
            <Icon name="puzzle" size={13} color={T.poppyText} />Settings · Integrations
          </div>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 24, fontWeight: 700, letterSpacing: "-.02em", marginTop: 6, color: T.ink }}>Data sources</div>
          <div style={{ fontSize: 13, color: T.slate, marginTop: 5, lineHeight: 1.5 }}>Every number in the Command Center traces back to one of these connections.</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <SBtn icon="sync" disabled={!live || syncingAll} onClick={syncAll}>{syncingAll ? "Syncing…" : "Sync all"}</SBtn>
          {/* Not gated on `live`: opening the list is read-only, and a trigger that is dead in
              the preview cannot be reviewed there. The Connect inside each row is gated. */}
          <SBtn kind="primary" onClick={() => setAdding(true)}>+ Add source</SBtn>
        </div>
      </div>

      {/* The strip reads left to right as one sentence about the whole list, which is what the
          right-aligned "N of M sources healthy" line could not do on its own. */}
      <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", background: T.white,
        border: `1px solid ${T.line}`, borderRadius: 12, overflow: "hidden", marginBottom: 18 }}>
        {[["healthy", view.healthy, T.meadow],
          ["needs attention", attention, T.poppyText],
          ["entities mapped", view.entities_mapped ?? 0, T.line]].map(([label, value, dot], i) => (
          <div key={label} style={{ display: "flex", flexDirection: "column", gap: 2,
            padding: "14px 20px", borderRight: i < 2 ? `1px solid ${T.line}` : "none" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span aria-hidden style={{ width: 8, height: 8, borderRadius: 999, background: dot }} />
              <span style={{ fontFamily: "var(--font-display)", fontSize: 19, fontWeight: 700, color: T.ink, letterSpacing: "-.02em", fontVariantNumeric: "tabular-nums" }}>{value}</span>
            </div>
            <div style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.slate }}>{label}</div>
          </div>
        ))}
        <span style={{ flex: 1 }} />
        <span style={{ padding: "0 20px", fontFamily: "var(--font-data)", fontSize: 11, letterSpacing: ".1em", textTransform: "uppercase", color: T.muted, whiteSpace: "nowrap" }}>
          {view.next_sync_in_min != null ? `Auto-sync in ${view.next_sync_in_min} min` : "Auto-sync every 30 min"}
        </span>
      </div>

      {/* One failure, named, with the button that fixes it. The server writes this sentence -- a
          client re-deriving it from a status enum is how the card's own summary line drifted. */}
      {alerts.length > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap",
          padding: "14px 16px", marginBottom: 18, borderRadius: 12,
          border: `1px solid ${alpha(T.poppyText, 0.3)}`, background: alpha(T.poppyText, 0.08) }}>
          <span aria-hidden style={{ width: 26, height: 26, flexShrink: 0, borderRadius: 8,
            background: T.poppyText, color: T.white, display: "flex", alignItems: "center",
            justifyContent: "center", fontFamily: "var(--font-display)", fontSize: 13, fontWeight: 700 }}>!</span>
          <div style={{ flex: 1, minWidth: 220 }}>
            <div style={{ fontFamily: "var(--font-text)", fontSize: 13.5, fontWeight: 600, color: T.poppyText }}>
              {alerts[0].title}{alerts.length > 1 ? ` · and ${alerts.length - 1} more` : ""}</div>
            {alerts[0].detail && <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.slate, marginTop: 2 }}>{alerts[0].detail}</div>}
          </div>
          <SBtn kind="primary" disabled={!live} onClick={() => fixAlert(alerts[0])}>
            {alerts[0].action === "reconnect" ? "Reconnect" : "Fix it"}</SBtn>
        </div>
      )}

      {!live && (
        <div style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.poppyText, background: alpha(T.poppyText, 0.08), border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 12px", marginBottom: 14 }}>
          Preview (sample) — connect to the live API to manage sources.
        </div>
      )}

      <div style={{ border: `1px solid ${T.line}`, borderRadius: 14, background: T.white, overflow: "hidden", marginBottom: 18 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap",
          padding: "11px 18px", borderBottom: `1px solid ${T.line}`, background: T.parchment }}>
          <span style={{ fontFamily: "var(--font-data)", fontSize: 10.5, letterSpacing: ".14em", textTransform: "uppercase", color: T.slate }}>
            Connected · {connected.length}</span>
          <span style={{ flex: 1 }} />
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            {["All", "Needs attention", "Healthy"].map((f) => (
              <button key={f} onClick={() => setFilter(f)} style={{
                border: 0, cursor: "pointer", fontFamily: "var(--font-text)", fontSize: 12.5,
                whiteSpace: "nowrap", fontWeight: filter === f ? 600 : 500, padding: "0 11px",
                height: 27, borderRadius: 999,
                background: filter === f ? T.evergreen : T.white,
                color: filter === f ? T.onDark : T.slate,
              }}>{f}</button>
            ))}
          </span>
        </div>
        {visible.length ? visible.map((s) => (
          <SourceCard key={s.provider} s={s} open={!!open[s.provider]} onToggle={() => toggle(s.provider)}
            live={live} busy={busy} onConnect={connectSource}
            onEntityAction={(action, e) => entityAction(action, e, s)} />
        )) : (
          <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.muted, padding: "22px 18px" }}>
            Nothing {filter === "Healthy" ? "healthy" : "needing attention"} right now.</div>
        )}
      </div>

      {available.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 18 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span style={{ fontFamily: "var(--font-data)", fontSize: 10.5, letterSpacing: ".14em", textTransform: "uppercase", color: T.slate }}>
              Available · {available.length}</span>
            <span aria-hidden style={{ flex: 1, height: 1, background: T.line }} />
          </div>
          {available.map((s) => (
            <div key={s.provider} style={{ display: "flex", alignItems: "center", gap: 14,
              flexWrap: "wrap", padding: "14px 18px", borderRadius: 12,
              border: `1px dashed ${T.line}`, background: T.white }}>
              <IntegrationMark provider={s.provider} fallback={s.mono} />
              <div style={{ flex: 1, minWidth: 180 }}>
                <div style={{ fontFamily: "var(--font-display)", fontSize: 14.5, fontWeight: 600, color: T.ink }}>{s.title}</div>
                <div style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.muted }}>{s.meta || s.desc(s)}</div>
              </div>
              <SBtn kind="primary" disabled={!live} onClick={() => connectSource(s)}>Connect</SBtn>
            </div>
          ))}
        </div>
      )}

      <div style={{ fontFamily: "var(--font-text)", fontSize: 11, color: T.muted, padding: "8px 2px" }}>
        Auto-sync runs every 30 minutes. Disconnecting removes stored tokens; historical data already synced stays in the dashboard.
      </div>

      {adding && (
        <AddSourceModal sources={addable} live={live} onClose={() => setAdding(false)}
          onPick={(s) => { setAdding(false); connectSource(s); }} />
      )}

      {connecting && (connecting.provider === "qbo"
        ? <QboEntityForm mode={connecting.mode} entity={connecting.entity} onClose={() => setConnecting(null)}
            onDone={() => { setConnecting(null); load(); }} />
        : connecting.provider === "arive"
        ? <AriveConnectForm row={connecting} onClose={() => setConnecting(null)}
            onDone={() => { setConnecting(null); load(); }} />
        : connecting.provider === "sisu"
        ? <SisuConnectForm row={connecting} onClose={() => setConnecting(null)}
            onDone={() => { setConnecting(null); load(); }} />
        : connecting.provider === "fub"
        ? <FubConnectForm row={connecting} onClose={() => setConnecting(null)}
            onDone={() => { setConnecting(null); load(); }} />
        : connecting.provider === "stripe_legacy"
        ? <StripeLegacyConnectForm row={connecting} onClose={() => setConnecting(null)}
            onDone={() => { setConnecting(null); load(); }} />
        : connecting.provider === "stripe_bc"
        ? <StripeBcConnectForm row={connecting} onClose={() => setConnecting(null)}
            onDone={() => { setConnecting(null); load(); }} />
        : connecting.provider === "ghl_legacy"
        ? <GhlLegacyConnectForm row={connecting} onClose={() => setConnecting(null)}
            onDone={() => { setConnecting(null); load(); }} />
        : connecting.provider === "meta_ads"
        ? <MetaAdsConnectForm row={connecting} onClose={() => setConnecting(null)}
            onDone={() => { setConnecting(null); load(); }} />
        : connecting.provider === "ghl_recruiting"
        ? <RecruitingConnectForm row={connecting} onClose={() => setConnecting(null)}
            onDone={() => { setConnecting(null); load(); }} />
        : connecting.provider === "ghl" || connecting.provider === "ghl_bc"
        ? <GhlConnectForm row={connecting} onClose={() => setConnecting(null)}
            onDone={() => { setConnecting(null); load(); }} />
        /* No fall-through form. This chain used to END in GhlConnectForm, so any provider
           without a branch opened a GoHighLevel dialog - which is how a Meta Ads button opened
           one. A wrong form is worse than no form: it looks like it works, and somebody pastes a
           Meta token into a field labelled Location ID. Rendering null means an unhandled
           provider is visibly broken rather than quietly wrong, and there is a test that fails
           before it can reach anybody. */
        : null)}
    </>
  );
}

/* ── account ───────────────────────────────────────────────── */

/* SIGN OUT READ localStorage ONLY, which is where every token lived when this was written. A
   session without "remember me" is kept in sessionStorage now, so for exactly those sessions this
   found no token -- the server-side logout was never sent -- cleared nothing, and landed back on a
   dashboard that was still signed in. getToken() reads both stores and logout() clears both.
   NOTE: /auth/logout revokes nothing today. Sessions are stateless JWTs, so a signed-out token
   stays valid until it expires; hard revocation is only a token_version bump. `keepalive` just
   stops the navigation on the next line from cancelling the request mid-flight. */
function doSignOut() {
  const token = getToken();
  if (API_BASE && token) fetch(`${API_BASE}/auth/logout`,
    { method: "POST", keepalive: true,
      headers: tenantHeaders({ Authorization: `Bearer ${token}` }) }).catch(() => {});
  logout();
  window.location.href = "/";
}

function AccountPage() {
  const [me, setMe] = useState(null);
  useEffect(() => {
    if (!API_BASE) { setMe({ name: "Spring Bengtzen", email: "spring@springb.com", role: "owner", tenant: "springb" }); return; }
    getJSON("/me").then(setMe).catch(() => {});
  }, []);
  const rowStyle = { display: "flex", justifyContent: "space-between", padding: "9px 0", borderTop: `1px solid ${T.line}`, fontFamily: "var(--font-text)", fontSize: 13 };
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
  { key: "ulrg", name: "ULRG + Team", tag: "Real estate", status: "healthy", accent: T.meadow, ink: "#4D6A4D", is_jv: false, jv_share: 1, watch_margin_below: null, per_loan_share: null, expense_run_rate_mode: "manual", expense_run_rate_manual: 96000, default_agent_split: 0.60 },
  { key: "springb", name: "Spring B", tag: "beCollective + The Forum", status: "watch", accent: T.poppy, ink: T.poppyText, is_jv: false, jv_share: 1, watch_margin_below: 25, per_loan_share: null },
  { key: "sympli", name: "Sympli Mortgage", tag: "Joint venture · 50% owned", status: "opportunity", accent: T.teal, ink: T.teal, is_jv: true, jv_share: 0.5, watch_margin_below: null, per_loan_share: 2100, lo_comp_rate: 0.55, opex_rate: 0.29 },
];

function BusinessEditForm({ biz, onClose, onDone }) {
  const [f, setF] = useState({
    name: biz.name || "", tag: biz.tag || "", status: biz.status || "healthy",
    accent: biz.accent || "#61835E", ink: biz.ink || "#4D6A4D", is_jv: !!biz.is_jv,
    jv_pct: Math.round((biz.jv_share ?? 1) * 100),
    watch_margin_below: biz.watch_margin_below ?? "",
    per_loan_share: biz.per_loan_share ?? "",
    capture_target: biz.capture_target ?? "",
    lo_comp_pct: biz.lo_comp_rate != null ? Math.round(biz.lo_comp_rate * 100) : "",
    opex_pct: biz.opex_rate != null ? Math.round(biz.opex_rate * 100) : "",
    expense_run_rate_mode: biz.expense_run_rate_mode || "trailing_3mo",
    expense_run_rate_manual: biz.expense_run_rate_manual ?? "",
    agent_split_pct: biz.default_agent_split != null ? Math.round(biz.default_agent_split * 100) : "",
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
        capture_target: numOrNull(f.capture_target),
        lo_comp_rate: f.lo_comp_pct === "" ? null : Math.max(0, Math.min(100, Number(f.lo_comp_pct) || 0)) / 100,
        opex_rate: f.opex_pct === "" ? null : Math.max(0, Math.min(100, Number(f.opex_pct) || 0)) / 100,
        expense_run_rate_mode: f.expense_run_rate_mode,
        expense_run_rate_manual: f.expense_run_rate_mode === "manual" ? numOrNull(f.expense_run_rate_manual) : null,
        default_agent_split: f.agent_split_pct === "" ? null : Math.max(0, Math.min(100, Number(f.agent_split_pct) || 0)) / 100,
      });
      onDone();
    } catch {
      setErr("Couldn't save — check the values and try again.");
    } finally { setBusy(false); }
  }

  const field = { width: "100%", boxSizing: "border-box", fontFamily: "var(--font-text)", fontSize: 13, color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "9px 11px", marginTop: 5 };
  const label = { display: "block", fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate, marginTop: 14 };
  const half = { display: "flex", gap: 10 };

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,46,44,0.34)", zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <form onClick={(e) => e.stopPropagation()} onSubmit={submit} style={{ width: "100%", maxWidth: 460, background: T.white, borderRadius: 14, padding: 22, boxShadow: "0 20px 60px rgba(0,46,44,.22)", maxHeight: "88vh", overflowY: "auto" }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 600, color: T.ink }}>Edit {biz.name}</div>
        <div style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.muted, marginTop: 3 }}>Brand and health config for this profit center.</div>
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
          <>
            <div style={half}>
              <label style={{ ...label, flex: 1 }}>JV share (%)
                <input style={field} type="number" step="1" value={f.jv_pct} onChange={set("jv_pct")} />
              </label>
              <label style={{ ...label, flex: 1 }}>Attach target (%) <span style={{ fontWeight: 400, color: T.muted }}>· flywheel goal</span>
                <input style={field} type="number" step="5" value={f.capture_target} onChange={set("capture_target")} placeholder="60" />
              </label>
            </div>
            <label style={label}>Revenue per funded loan ($) <span style={{ fontWeight: 400, color: T.muted }}>· prices the flywheel gap</span>
              <input style={field} type="number" step="50" value={f.per_loan_share} onChange={set("per_loan_share")} placeholder="Set to price the gap" />
            </label>
            <div style={{ ...half, marginTop: 2 }}>
              <label style={{ ...label, flex: 1 }}>LO comp (% of commission) <span style={{ fontWeight: 400, color: T.muted }}>· cost of sale</span>
                <input style={field} type="number" step="1" value={f.lo_comp_pct} onChange={set("lo_comp_pct")} placeholder="e.g. 55" />
              </label>
              <label style={{ ...label, flex: 1 }}>Operating cost (% of commission)
                <input style={field} type="number" step="1" value={f.opex_pct} onChange={set("opex_pct")} placeholder="e.g. 29" />
              </label>
            </div>
            <div style={{ fontFamily: "var(--font-text)", fontSize: 11, color: T.muted, marginTop: 6 }}>
              Reverse-engineered from a closed month's P&L. Drives the calculated Live / Projection lenses down to your JV share.
            </div>
          </>
        )}

        <div style={{ marginTop: 18, paddingTop: 14, borderTop: `1px solid ${T.line}`, fontFamily: "var(--font-display)", fontSize: 12.5, fontWeight: 600, color: T.slate }}>Financials (three-lens)</div>
        <div style={half}>
          <label style={{ ...label, flex: 1 }}>Expense run-rate
            <select style={field} value={f.expense_run_rate_mode} onChange={set("expense_run_rate_mode")}>
              <option value="trailing_3mo">Trailing 3 months</option>
              <option value="last_month">Last month</option>
              <option value="manual">Manual</option>
            </select>
          </label>
          {f.expense_run_rate_mode === "manual" && (
            <label style={{ ...label, flex: 1 }}>Monthly expenses ($)
              <input style={field} type="number" step="500" value={f.expense_run_rate_manual} onChange={set("expense_run_rate_manual")} placeholder="—" />
            </label>
          )}
        </div>
        <label style={label}>Default agent split (%) <span style={{ fontWeight: 400, color: T.muted }}>· commission fallback when a Sisu deal is missing it</span>
          <input style={field} type="number" step="1" value={f.agent_split_pct} onChange={set("agent_split_pct")} placeholder="e.g. 60" />
        </label>
        {err && <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.poppyText, marginTop: 12 }}>{err}</div>}
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
        <div style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.poppyText, background: alpha(T.poppyText, 0.08), border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 12px", marginBottom: 14 }}>
          Preview (sample) — connect to the live API to edit.
        </div>
      )}
      {rows ? rows.map((b) => (
        <div key={b.key} style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 0", borderTop: `1px solid ${T.line}` }}>
          <span style={{ width: 10, height: 10, borderRadius: 3, background: b.accent, flexShrink: 0 }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontFamily: "var(--font-text)", fontSize: 13.5, fontWeight: 600, color: T.ink }}>{b.name}</div>
            <div style={{ fontFamily: "var(--font-text)", fontSize: 11.5, color: T.muted }}>{b.tag}</div>
          </div>
          {b.is_jv && <span style={{ fontFamily: "var(--font-text)", fontSize: 11.5, color: T.slate }}>JV {Math.round(b.jv_share * 100)}%</span>}
          <span style={{ fontFamily: "var(--font-text)", fontSize: 11.5, color: T.slate, textTransform: "capitalize" }}>{b.status}</span>
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

/* ── Team (users, roles, tab grants) ───────────────────────── */

const TAB_META = {
  portfolio: { label: "Portfolio", dot: T.evergreen }, ulrg: { label: "ULRG", dot: T.meadow },
  forum: { label: "The Forum", dot: T.daffodil }, becollective: { label: "beCollective", dot: T.petal },
  edge: { label: "The Edge", dot: T.edge },
  sympli: { label: "Sympli", dot: T.teal }, flywheel: { label: "Flywheel", dot: T.poppy },
};
const ROLE_COLOR = { owner: T.evergreen, admin: T.meadow, member: T.slate };

function CopyLink({ url, caption }) {
  const [copied, setCopied] = useState(false);
  return (
    <div style={{ marginTop: 8 }}>
    {caption && <div style={{ fontFamily: "var(--font-text)", fontSize: 11.5, color: T.muted, marginBottom: 4 }}>{caption}</div>}
    <div style={{ display: "flex", gap: 8 }}>
      <input readOnly value={url} onFocus={(e) => e.target.select()} style={{ flex: 1, fontFamily: "var(--font-text)", fontSize: 12, color: T.slate, background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 10px" }} />
      <button onClick={() => { navigator.clipboard?.writeText(url); setCopied(true); setTimeout(() => setCopied(false), 1500); }} style={btn("primary")}>{copied ? "Copied" : "Copy"}</button>
    </div>
    </div>
  );
}

function TabDots({ user }) {
  if (user.all_tabs) return <span style={{ fontFamily: "var(--font-text)", fontSize: 11.5, color: T.muted }}>All tabs</span>;
  return (
    <span style={{ display: "inline-flex", gap: 5, flexWrap: "wrap" }}>
      {(user.tabs || []).map((t) => (
        <span key={t} title={TAB_META[t]?.label || t} style={{ width: 9, height: 9, borderRadius: 2, background: TAB_META[t]?.dot || T.muted }} />
      ))}
    </span>
  );
}

function Pill({ text, color, bg }) {
  return <span style={{ fontFamily: "var(--font-display)", fontSize: 9.5, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase", color, background: bg, borderRadius: 5, padding: "2px 7px" }}>{text}</span>;
}

function InviteModal({ tabs, me, onClose, onInvited }) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [grants, setGrants] = useState([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);
  const canGrantAdmin = me?.role === "owner";   // only an owner can create an admin
  const toggle = (t) => setGrants((g) => (g.includes(t) ? g.filter((x) => x !== t) : [...g, t]));

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      const r = await postJSON("/users/invite", { email: email.trim(), role, tab_access: role === "member" ? grants : null });
      setResult(r);
      onInvited();
    } catch (x) {
      if (x.status === 401) {   // dead/stale session — bounce to login rather than showing a form error
        return;
      }
      // Surface the server's real reason (e.g. "A user with that email already
      // exists"), falling back to a friendly line for a network error.
      setErr(x.detail || "Couldn't send the invite — please try again.");
    } finally { setBusy(false); }
  }

  const field = { width: "100%", boxSizing: "border-box", fontFamily: "var(--font-text)", fontSize: 13, color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "9px 11px", marginTop: 5 };
  const label = { display: "block", fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate, marginTop: 14 };

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,46,44,0.34)", zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <div onClick={(e) => e.stopPropagation()} style={{ width: "100%", maxWidth: 440, background: T.white, borderRadius: 14, padding: 22, boxShadow: "0 20px 60px rgba(0,46,44,.22)", maxHeight: "88vh", overflowY: "auto" }}>
        {result ? (
          <>
            <div style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 600, color: T.ink }}>Invite sent</div>
            <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.muted, marginTop: 4 }}>We emailed <b style={{ color: T.ink }}>{result.user.email}</b> an invite. It works once and expires in 7 days.</div>
            <CopyLink url={result.invite_url} caption="or copy the link" />
            <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 18 }}>
              <button onClick={onClose} style={btn("primary")}>Done</button>
            </div>
          </>
        ) : (
          <form onSubmit={submit}>
            <div style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 600, color: T.ink }}>Invite a teammate</div>
            <label style={label}>Email<input style={field} type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
            <label style={label}>Role
              <select style={field} value={role} onChange={(e) => setRole(e.target.value)}>
                <option value="member">Member — sees only granted tabs</option>
                {canGrantAdmin && <option value="admin">Admin — manages members + integrations</option>}
              </select>
            </label>
            {role === "member" && (
              <div style={{ marginTop: 14 }}>
                <div style={{ fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate }}>Tabs they can see</div>
                <div style={{ display: "flex", flexDirection: "column", gap: 7, marginTop: 8 }}>
                  {tabs.map((t) => (
                    <label key={t} style={{ display: "flex", alignItems: "center", gap: 9, fontFamily: "var(--font-text)", fontSize: 13, color: T.ink }}>
                      <input type="checkbox" checked={grants.includes(t)} onChange={() => toggle(t)} />
                      <span style={{ width: 9, height: 9, borderRadius: 2, background: TAB_META[t]?.dot || T.muted }} />
                      {TAB_META[t]?.label || t}
                    </label>
                  ))}
                </div>
              </div>
            )}
            {err && <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.poppyText, marginTop: 12 }}>{err}</div>}
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18 }}>
              <button type="button" onClick={onClose} style={btn()}>Cancel</button>
              <button type="submit" disabled={busy || (role === "member" && !grants.length)} style={(busy || (role === "member" && !grants.length)) ? btn("disabled") : btn("primary")}>{busy ? "Inviting…" : "Send invite"}</button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

function UserRow({ u, me, tabs, onChanged }) {
  const [open, setOpen] = useState(false);
  const [role, setRole] = useState(u.role);
  const [grants, setGrants] = useState(u.all_tabs ? [] : (u.tabs || []));
  const [link, setLink] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const isSelf = me && u.id === me.id;
  const canManage = me && (me.role === "owner" || (me.role === "admin" && u.role === "member"));
  const editable = canManage && !isSelf;
  const toggle = (t) => setGrants((g) => (g.includes(t) ? g.filter((x) => x !== t) : [...g, t]));

  async function save() {
    setBusy(true); setErr(null);
    try { await patchJSON(`/users/${u.id}`, { role, tab_access: role === "member" ? grants : null }); onChanged(); setOpen(false); }
    catch (x) {
      if (x.status === 401) { return; }
      setErr(x.detail || "Couldn't save — please try again.");
    } finally { setBusy(false); }
  }
  async function toggleStatus() {
    setBusy(true);
    try { await postJSON(`/users/${u.id}/${u.status === "disabled" ? "enable" : "disable"}`); onChanged(); }
    catch { /* invariant blocked (last owner) */ } finally { setBusy(false); }
  }
  async function sendInvite() { const r = await postJSON(`/users/${u.id}/resend-invite`); setLink(r.invite_url); }
  async function sendReset() { const r = await postJSON(`/users/${u.id}/reset-link`); setLink(r.reset_url); }

  return (
    <div style={{ borderTop: `1px solid ${T.line}` }}>
      <div onClick={() => setOpen((o) => !o)} style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 4px", cursor: "pointer" }}>
        <span style={{ width: 32, height: 32, borderRadius: 8, background: T.parchment, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "var(--font-display)", fontSize: 13, fontWeight: 700, color: T.slate, flexShrink: 0 }}>{(u.name || u.email || "?").slice(0, 1).toUpperCase()}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: "var(--font-text)", fontSize: 13.5, fontWeight: 600, color: T.ink }}>{u.name}{isSelf && <span style={{ color: T.muted, fontWeight: 400 }}> · you</span>}</div>
          <div style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.muted, overflow: "hidden", textOverflow: "ellipsis" }}>{u.email}</div>
        </div>
        <Pill text={u.role} color={ROLE_COLOR[u.role]} bg={u.role === "owner" ? T.meadowBg : T.parchment} />
        <span style={{ width: 90 }}><TabDots user={u} /></span>
        {u.status !== "active" && <Pill text={u.status} color={u.status === "invited" ? T.daffodilText : T.muted} bg={u.status === "invited" ? T.daffodilBg : T.parchment} />}
        <span style={{ fontFamily: "var(--font-text)", fontSize: 11, color: T.muted, width: 84, textAlign: "right" }}>{u.last_login_at ? relativeTime(u.last_login_at) : "—"}</span>
      </div>
      {open && (
        <div style={{ padding: "4px 4px 16px 48px" }}>
          {editable ? (
            <>
              <div style={{ display: "flex", gap: 14, alignItems: "flex-start", flexWrap: "wrap" }}>
                <label style={{ fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate }}>Role
                  <select value={role} onChange={(e) => setRole(e.target.value)} style={{ display: "block", marginTop: 5, fontFamily: "var(--font-text)", fontSize: 13, border: `1px solid ${T.line}`, borderRadius: 8, padding: "7px 10px", background: T.white }}>
                    {me.role === "owner" && <option value="owner">Owner</option>}
                    {me.role === "owner" && <option value="admin">Admin</option>}
                    <option value="member">Member</option>
                  </select>
                </label>
                <div style={{ flex: 1, minWidth: 200 }}>
                  <div style={{ fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate }}>Tabs</div>
                  {role === "member" ? (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "6px 14px", marginTop: 6 }}>
                      {tabs.map((t) => (
                        <label key={t} style={{ display: "flex", alignItems: "center", gap: 7, fontFamily: "var(--font-text)", fontSize: 12.5, color: T.ink }}>
                          <input type="checkbox" checked={grants.includes(t)} onChange={() => toggle(t)} />{TAB_META[t]?.label || t}
                        </label>
                      ))}
                    </div>
                  ) : <div style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.muted, marginTop: 6 }}>All tabs (owner/admin)</div>}
                </div>
              </div>
              <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap", alignItems: "center" }}>
                <button onClick={save} disabled={busy || (role === "member" && !grants.length)} style={btn("primary")}>Save</button>
                {u.status === "invited" ? <button onClick={sendInvite} style={btn()}>Send invite</button> : <button onClick={sendReset} style={btn()}>Send reset</button>}
                <span style={{ flex: 1 }} />
                <button onClick={toggleStatus} disabled={busy} style={btn("danger")}>{u.status === "disabled" ? "Enable" : "Disable"}</button>
              </div>
              {err && <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.poppyText, marginTop: 10 }}>{err}</div>}
              {link && <CopyLink url={link} caption="Sent. Or copy the link — email can bounce or land in spam." />}
            </>
          ) : (
            <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.muted }}>{isSelf ? "Manage your own name and password on the Account page." : "You can't manage this user."}</div>
          )}
        </div>
      )}
    </div>
  );
}

function UsersPage() {
  const [users, setUsers] = useState(null);
  const [tabs, setTabs] = useState([]);
  const [me, setMe] = useState(null);
  const [inviting, setInviting] = useState(false);
  const live = Boolean(API_BASE);

  function load() {
    if (!live) { setUsers([]); return; }
    Promise.all([getJSON("/users"), getJSON("/tenant/tabs"), getJSON("/me")])
      .then(([u, t, m]) => { setUsers(u); setTabs(t.tabs); setMe(m); })
      .catch(() => setUsers([]));
  }
  useEffect(load, []);   // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, marginBottom: 16 }}>
        <div>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 10.5, fontWeight: 700, letterSpacing: ".12em", textTransform: "uppercase", color: T.muted }}>Settings · Users</div>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 20, fontWeight: 600, color: T.ink, marginTop: 3 }}>Team</div>
          <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.muted, marginTop: 3 }}>Invite teammates and control exactly which tabs each one sees.</div>
        </div>
        <button onClick={() => setInviting(true)} style={{ fontFamily: "var(--font-display)", fontSize: 13, fontWeight: 600, color: T.white, background: T.poppy, border: "none", borderRadius: 9, padding: "9px 16px", cursor: "pointer", flexShrink: 0 }}>Invite user</button>
      </div>
      <Card>
        {!users ? <div style={{ color: T.muted, fontSize: 13 }}>Loading…</div>
          : users.length === 0 ? <div style={{ color: T.muted, fontSize: 13 }}>No users yet.</div>
            : users.map((u) => <UserRow key={u.id} u={u} me={me} tabs={tabs} onChanged={load} />)}
      </Card>
      {inviting && <InviteModal tabs={tabs} me={me} onClose={() => setInviting(false)} onInvited={load} />}
    </>
  );
}

/* ── Settings › Recruiting (RECRUITING-SPEC §9 Phase 1) ──────────────────────────────────────
 *
 * Where a workspace decides what the Recruiting tab COUNTS: which pipeline is the recruiting
 * one, what each stage means, and who the seats are. The tab shows numbers; this decides what
 * they are numbers of.
 *
 * Everything offered here is read live from the location, and everything stored is an ID.
 * That pairing is the point: the ids are what the product matches on, and the names are what a
 * person recognises, so the screen shows names and saves ids. A stage map built from names
 * empties itself the day somebody renames "Offer out" to "ICA sent" (D2).
 */
/* The five funnel groups, then the parked ones. Anything not in the funnel is held out of the
   Pipeline card and out of the chase rules, so the nurture BANDS are a cadence a brokerage picks
   -- Hot every 30 days, Cold every 180 -- rather than three stages that look active.
   This list is `recruiting_settings.KNOWN_GROUPS` on the server; a test holds the two together. */
const REC_GROUPS = ["Sourced", "Appointment set", "Met", "Offer out", "Signed",
                    "Nurture", "Hot Nurture", "Warm Nurture", "Cold Nurture"];

function RecruitingPage() {
  const live = Boolean(API_BASE);
  const [data, setData] = useState(null);
  const [draft, setDraft] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(() => {
    if (!live) { setData(RECRUITING_SAMPLE); setDraft(RECRUITING_SAMPLE.settings); return; }
    getJSON("/ulrg/recruiting/settings")
      .then((d) => { setData(d); setDraft(d.settings); })
      .catch(() => setErr("Couldn't load the recruiting settings."));
  }, [live]);
  useEffect(load, [load]);

  if (err && !data) return <Card><div style={{ fontFamily: "var(--font-text)", fontSize: 13, color: T.poppyText }}>{err}</div></Card>;
  if (!data) return <Card><div style={{ fontFamily: "var(--font-text)", fontSize: 13, color: T.muted }}>Loading…</div></Card>;

  if (!data.connected) {
    return (
      <Card>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 15.5, fontWeight: 600, color: T.ink }}>No recruiting location yet</div>
        <div style={{ fontFamily: "var(--font-text)", fontSize: 13, color: T.secondary, marginTop: 6, lineHeight: 1.55, maxWidth: 560 }}>
          Recruiting reads a Go High Level location of its own — the one your candidate pipeline
          lives in, not the one your membership programmes run on. Connect it under Integrations,
          then come back here to choose the pipeline.
        </div>
        <div style={{ marginTop: 14 }}><Link to="/settings/integrations" style={{ textDecoration: "none" }}><SBtn kind="primary">Go to Integrations</SBtn></Link></div>
      </Card>
    );
  }

  const pipelines = data.options?.pipelines || [];
  const picked = pipelines.find((x) => x.id === draft.pipeline_id);
  const stages = picked?.stages || [];
  const groups = draft.recruiting_stage_groups || [];
  const groupOf = (stageId) => (groups.find((g) => (g[2] || []).includes(stageId)) || [])[0] || "";

  function setStageGroup(stageId, label) {
    // A stage belongs to exactly ONE group: dropped from wherever it was, added where it is
    // going. Two groups claiming a stage would make every count that filters on group depend on
    // ordering — the server refuses it, and the UI should not be able to ask.
    //
    // ALWAYS BUILD THE FULL SET FIRST. A workspace part-way through mapping has only the groups
    // it has used so far, so `find(label)` came back undefined for every group it had not
    // reached yet and the dropdown silently did nothing. Found by using it, not by reading it.
    const have = new Map((groups || []).map((g) => [g[0], g]));
    const next = REC_GROUPS.map((lbl) => {
      const row = have.get(lbl)
        || [lbl, lbl === "Sourced" || lbl === "Appointment set" ? "sdr" : "team_leader", []];
      return [row[0], row[1], (row[2] || []).filter((x) => x !== stageId)];
    });
    // A group a workspace added beyond the six the product reasons about survives untouched.
    for (const [lbl, row] of have) {
      if (!REC_GROUPS.includes(lbl)) next.push([row[0], row[1], (row[2] || []).filter((x) => x !== stageId)]);
    }
    if (label) {
      const row = next.find((g) => g[0] === label);
      if (row) row[2] = [...row[2], stageId];
    }
    setDraft({ ...draft, recruiting_stage_groups: next });
    setSaved(false);
  }

  async function save() {
    setBusy(true); setErr(null);
    try {
      const out = await putJSON("/ulrg/recruiting/settings", draft);
      setDraft(out.settings);
      setSaved(true);
    } catch (e) {
      // The server names the field it refused. Showing that verbatim is the difference between
      // "couldn't save" and "two groups claim the same stage".
      setErr(String(e?.message || e) || "Couldn't save.");
    } finally { setBusy(false); }
  }

  const label = { display: "block", fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate, marginTop: 14 };
  const select = { width: "100%", boxSizing: "border-box", fontFamily: "var(--font-text)", fontSize: 13, color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 10px", marginTop: 5 };

  return (
    <>
      {Object.entries(data.options?.errors || {}).map(([k, v]) => (
        <Card key={k} style={{ borderLeft: `3px solid ${T.poppyText}` }}>
          <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.poppyText }}>{v}</div>
        </Card>
      ))}

      <Card>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 15.5, fontWeight: 600, color: T.ink }}>Pipeline</div>
        <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.secondary, marginTop: 4 }}>
          Which pipeline holds candidates, and what each of its stages means. Nothing syncs until a pipeline is chosen.
        </div>
        <label style={label}>Recruiting pipeline
          <select style={select} value={draft.pipeline_id || ""} disabled={!live}
                  onChange={(e) => { setDraft({ ...draft, pipeline_id: e.target.value || null }); setSaved(false); }}>
            <option value="">— pick a pipeline —</option>
            {pipelines.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>

        {picked && (
          <div style={{ marginTop: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <div style={{ fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate }}>Stage map</div>
              {data.suggested_groups && (
                <SBtn onClick={() => { setDraft({ ...draft, recruiting_stage_groups: data.suggested_groups }); setSaved(false); }}>
                  Use suggested map
                </SBtn>
              )}
              <span style={{ flex: 1 }} />
              <span style={{ fontFamily: "var(--font-data)", fontSize: 11, color: T.muted }}>
                {stages.filter((st) => groupOf(st.id)).length} of {stages.length} mapped
              </span>
            </div>
            {/* Suggestions are matched on stage NAME and are the only place in the product that
                happens. They do nothing until somebody presses Save. */}
            <div style={{ marginTop: 10, border: `1px solid ${T.line}`, borderRadius: 10, overflow: "hidden" }}>
              {stages.map((st, i) => (
                <div key={st.id} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 190px", gap: 12, alignItems: "center", padding: "9px 12px", borderTop: i ? `1px solid ${T.line}` : "none" }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontFamily: "var(--font-text)", fontSize: 13, color: T.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{st.name}</div>
                    <div style={{ fontFamily: "var(--font-data)", fontSize: 10.5, color: T.muted }}>{st.id}</div>
                  </div>
                  <select style={{ ...select, marginTop: 0 }} value={groupOf(st.id)} disabled={!live}
                          onChange={(e) => setStageGroup(st.id, e.target.value)}>
                    <option value="">— not used —</option>
                    {REC_GROUPS.map((g) => <option key={g} value={g}>{g}</option>)}
                  </select>
                </div>
              ))}
            </div>
            <div style={{ fontFamily: "var(--font-text)", fontSize: 11.5, color: T.muted, marginTop: 8, lineHeight: 1.5 }}>
              <b>Signed</b> is what “signed this month” counts. <b>Met</b> and <b>Offer out</b> are what the path to goal is
              drawn from. <b>Appointment set</b> is where the SDR hands over. The <b>Nurture</b> groups are parked — held out
              of the pipeline breakdown and off everybody’s daily list — and the Hot/Warm/Cold bands are there if you want a
              follow-up cadence rather than one bucket. A stage left unmapped is not counted anywhere.
            </div>
          </div>
        )}

        <label style={label}>Trailing GCI field
          <select style={select} value={draft.gci_field_id || ""} disabled={!live}
                  onChange={(e) => { setDraft({ ...draft, gci_field_id: e.target.value || null }); setSaved(false); }}>
            <option value="">— none —</option>
            {(data.options?.custom_fields || []).map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
        </label>
        <label style={label}>Current brokerage field
          <select style={select} value={draft.brokerage_field_id || ""} disabled={!live}
                  onChange={(e) => { setDraft({ ...draft, brokerage_field_id: e.target.value || null }); setSaved(false); }}>
            <option value="">— none —</option>
            {(data.options?.custom_fields || []).map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
        </label>

        {err && <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.poppyText, marginTop: 12 }}>{err}</div>}
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 16 }}>
          <SBtn kind="primary" disabled={!live || busy} onClick={save}>{busy ? "Saving…" : "Save"}</SBtn>
          {saved && <span style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.meadowInk }}>Saved.</span>}
        </div>
      </Card>

      <RecruitingGoals live={live} />
      <RecruitingWebhook data={data} live={live} onChanged={load} />
      <RecruitingRules data={data} draft={draft} setDraft={(d) => { setDraft(d); setSaved(false); }} live={live} />
      <RecruitingTemplates data={data} draft={draft} setDraft={(d) => { setDraft(d); setSaved(false); }} live={live} />
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <SBtn kind="primary" disabled={!live || busy} onClick={save}>{busy ? "Saving…" : "Save rules and templates"}</SBtn>
        {saved && <span style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.meadowInk }}>Saved.</span>}
      </div>

      <RecruitingRoster data={data} live={live} onChanged={load} />
    </>
  );
}

/* The roster. Three Team Leaders and an SDR, in this product's first shipping case — but the
   count is a workspace's business, so nothing here assumes it. */
/* Rules and Templates (RECRUITING-SPEC §6). Both live on the same config as the pipeline map, so
   they share one draft and one Save.

   THE SCHEMA COMES FROM THE SERVER. Labels, which fields a rule has and what each one's bounds
   are all arrive in `rule_meta`, because the engine owns them. A form that carried its own copy
   would keep refusing a value the server had started accepting, and nobody would know which half
   was wrong. */
/* Settings › Recruiting › Goals (RECRUITING-SPEC §9 Phase 5, D7).
 *
 * A GOAL IS A MONTH AND AN OWNER SETS IT; a commitment is a week and the person doing the work
 * sets it, on the tab. Keeping them in different places is not tidiness — a tool that let an
 * owner type somebody's weekly commitment would have turned a commitment into an assignment,
 * and the word for an assignment is goal.
 *
 * Per period, so setting October cannot change what September was judged against. A verdict
 * that moves after the fact is not a verdict.
 */
/* Settings › Recruiting › Faster updates (RECRUITING-SPEC §5.6, Phase 6b).
 *
 * OPTIONAL, AND THE COPY SAYS SO. Recruiting already reads GHL every five minutes; this only
 * shortens the wait. If the Workflow is never built, or somebody deletes it, or GHL stops
 * sending, nothing breaks — which is worth saying on the screen, because an admin deciding
 * whether to do fifteen minutes of setup deserves to know what happens if they do not.
 *
 * The secret is shown ONCE. It is a credential: if this screen could read it back, our stored
 * copy would be as useful as the one in GHL and rotating it would stop meaning anything.
 */
function RecruitingWebhook({ data, live, onChanged }) {
  const [secret, setSecret] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const on = Boolean(data.webhook_configured);
  const origin = typeof window === "undefined" ? "" : window.location.origin;

  async function rotate() {
    setBusy(true); setErr(null);
    try { setSecret((await postJSON("/ulrg/recruiting/webhook-secret", {})).secret); onChanged(); }
    catch (e) { setErr(String(e?.message || e)); }
    finally { setBusy(false); }
  }
  async function clear() {
    setBusy(true); setErr(null);
    try { await delJSON("/ulrg/recruiting/webhook-secret"); setSecret(null); onChanged(); }
    catch (e) { setErr(String(e?.message || e)); }
    finally { setBusy(false); }
  }

  const code = { fontFamily: "var(--font-data)", fontSize: 12, background: T.page,
                 border: `1px solid ${T.line}`, borderRadius: 6, padding: "3px 6px",
                 wordBreak: "break-all" };

  return (
    <Card>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 15.5, fontWeight: 600, color: T.ink }}>
          Faster updates <span style={{ fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 400, color: T.muted }}>· optional</span>
        </div>
        <span style={{ flex: 1 }} />
        <span style={{ fontFamily: "var(--font-data)", fontSize: 11, color: on ? T.meadowInk : T.muted }}>
          {on ? "On" : "Off"}
        </span>
      </div>
      <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.secondary, marginTop: 4, lineHeight: 1.55 }}>
        Recruiting already reads GHL every five minutes. A Workflow webhook makes a reply or a
        stage change show up in seconds instead. Nothing breaks without it — the poll carries on
        either way, so this is safe to turn off the moment it looks wrong.
      </div>

      <ol style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.secondary,
                   lineHeight: 1.7, paddingLeft: 18, marginTop: 14, marginBottom: 0 }}>
        <li>Press <b>Generate secret</b> below and copy it — it is shown once.</li>
        <li>In GHL, build a Workflow on the recruiting location with the triggers
          <b> Customer Replied</b>, <b>Appointment Status</b> and <b>Pipeline Stage Changed</b>.</li>
        <li>Add a <b>Webhook</b> action. Method <b>POST</b>, URL <span style={code}>{origin}/api/v1/webhooks/ghl-recruiting</span></li>
        <li>Add a custom header <span style={code}>X-Axcion-Secret</span> with the secret as its value.</li>
        <li>Publish the Workflow. Nothing else is needed — the body is not read.</li>
      </ol>
      <div style={{ fontFamily: "var(--font-text)", fontSize: 11.5, color: T.muted, marginTop: 10, lineHeight: 1.5 }}>
        Axcion ignores what the webhook sends and re-reads the change from GHL with its own
        credentials, so nothing in that payload can alter a record here.
      </div>

      {secret && (
        <div style={{ marginTop: 14, background: T.page, border: `1px solid ${T.line}`,
                      borderRadius: 9, padding: "11px 13px" }}>
          <div style={{ fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.ink }}>
            Copy this now — it cannot be shown again
          </div>
          <div style={{ ...code, marginTop: 6, padding: "7px 9px" }}>{secret}</div>
        </div>
      )}

      {err && <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.poppyText, marginTop: 12 }}>{err}</div>}
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 16, flexWrap: "wrap" }}>
        <SBtn kind="primary" disabled={!live || busy} onClick={rotate}>
          {busy ? "Working…" : on ? "Replace secret" : "Generate secret"}
        </SBtn>
        {on && <SBtn disabled={!live || busy} onClick={clear}>Turn off</SBtn>}
        {on && <span style={{ fontFamily: "var(--font-text)", fontSize: 11.5, color: T.muted }}>
          Replacing stops the old secret working immediately.</span>}
      </div>
    </Card>
  );
}

function RecruitingGoals({ live }) {
  const [period, setPeriod] = useState(() => new Date().toISOString().slice(0, 7));
  const [data, setData] = useState(null);
  const [draft, setDraft] = useState({});
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState(null);

  const load = useCallback(() => {
    if (!live) { setData(GOALS_SAMPLE); setDraft(flatten(GOALS_SAMPLE)); return; }
    getJSON(`/ulrg/recruiting/goals?period=${period}`)
      .then((d) => { setData(d); setDraft(flatten(d)); })
      .catch(() => setErr("Couldn't load the goals."));
  }, [live, period]);
  useEffect(load, [load]);

  function flatten(d) {
    const out = {};
    for (const [metric, value] of Object.entries(d.team || {})) out[`team:${metric}`] = value;
    for (const seat of d.seats || []) {
      for (const [metric, value] of Object.entries(seat.goals || {})) out[`${seat.seat_id}:${metric}`] = value;
    }
    return out;
  }

  const set = (key) => (e) => {
    setDraft({ ...draft, [key]: e.target.value === "" ? "" : Number(e.target.value) });
    setSaved(false);
  };

  async function save() {
    setBusy(true); setErr(null);
    try {
      const entries = Object.entries(draft).map(([key, goal]) => {
        const [who, metric] = key.split(":");
        return { seat_id: who === "team" ? null : who, metric, goal: goal === "" ? null : goal };
      });
      await putJSON("/ulrg/recruiting/goals", { period_key: data.period.key, entries });
      setSaved(true);
      load();
    } catch (e) {
      setErr(String(e?.message || e) || "Couldn't save.");
    } finally { setBusy(false); }
  }

  if (!data) return null;
  const num = { width: 88, boxSizing: "border-box", fontFamily: "var(--font-data)", fontSize: 13,
                color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 7,
                padding: "6px 8px" };

  /* Which metrics a seat is even asked about. An SDR has no signing target and a Team Leader has
     no dial target, and showing both to both would be a form that teaches people to skip fields. */
  const metricsFor = (role) => (role === "sdr"
    ? ["booked", "show_rate", "dials", "convos"]
    : ["signed"]);

  return (
    <Card>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 15.5, fontWeight: 600, color: T.ink }}>Goals</div>
          <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.secondary, marginTop: 4, lineHeight: 1.5 }}>
            The monthly targets the hero and the leaderboard are judged against. Weekly commitments
            are set by the people doing the work, on the tab itself.
          </div>
        </div>
        <span style={{ flex: 1 }} />
        <label style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.slate }}>
          Period{" "}
          <input type="month" value={period} disabled={!live}
                 onChange={(e) => { setPeriod(e.target.value); setSaved(false); }}
                 style={{ ...num, width: 140, marginLeft: 6 }} />
        </label>
      </div>

      <div style={{ marginTop: 16, borderTop: `1px solid ${T.line}`, paddingTop: 12,
                    display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <span style={{ fontFamily: "var(--font-text)", fontSize: 13, fontWeight: 600, color: T.ink, flex: "1 1 180px" }}>
          Team · signings this month
        </span>
        <input type="number" min="0" style={num} disabled={!live}
               value={draft["team:signed"] ?? ""} onChange={set("team:signed")} />
      </div>
      <div style={{ fontFamily: "var(--font-text)", fontSize: 11.5, color: T.muted, marginTop: 6, lineHeight: 1.5 }}>
        The team target and the seat targets are allowed to differ. An owner may carry a number the
        splits do not add up to, and the tab shows both rather than quietly reconciling them.
      </div>

      {(data.seats || []).map((seat) => (
        <div key={seat.seat_id} style={{ borderTop: `1px solid ${T.line}`, marginTop: 12, paddingTop: 12 }}>
          <div style={{ fontFamily: "var(--font-text)", fontSize: 13, fontWeight: 600, color: T.ink }}>
            {seat.name} <span style={{ fontWeight: 400, color: T.muted }}>· {seat.role === "sdr" ? "SDR" : "Team Leader"}</span>
          </div>
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginTop: 8 }}>
            {metricsFor(seat.role).map((metric) => (
              <label key={metric} style={{ fontFamily: "var(--font-text)", fontSize: 11.5, color: T.muted }}>
                <span style={{ display: "block", marginBottom: 3 }}>
                  {metric === "show_rate" ? "show rate %" : metric}
                </span>
                <input type="number" min="0" style={num} disabled={!live}
                       value={draft[`${seat.seat_id}:${metric}`] ?? ""}
                       onChange={set(`${seat.seat_id}:${metric}`)} />
              </label>
            ))}
          </div>
        </div>
      ))}

      {(data.seats || []).length === 0 && (
        <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.muted, marginTop: 14 }}>
          No seats yet. Add them in the Roster below, then come back and give them a number.
        </div>
      )}

      {err && <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.poppyText, marginTop: 12 }}>{err}</div>}
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 16 }}>
        <SBtn kind="primary" disabled={!live || busy} onClick={save}>{busy ? "Saving…" : `Save ${data.period.label} goals`}</SBtn>
        {saved && <span style={{ fontFamily: "var(--font-text)", fontSize: 12, color: T.meadowInk }}>Saved.</span>}
      </div>
    </Card>
  );
}

const GOALS_SAMPLE = {
  period: { key: "2026-09", label: "September" },
  metrics: ["signed", "booked", "show_rate", "dials", "convos"],
  team: { signed: 9 },
  seats: [
    { seat_id: "s1", name: "Jenna Ruiz", role: "team_leader", goals: { signed: 3 } },
    { seat_id: "s2", name: "Marcus Bell", role: "team_leader", goals: { signed: 3 } },
    { seat_id: "s3", name: "Cole Whittaker", role: "sdr", goals: { booked: 24, show_rate: 75 } },
  ],
};

function RecruitingRules({ data, draft, setDraft, live }) {
  const meta = data.rule_meta || [];
  const rules = draft.rules || {};
  const set = (key, patch) => setDraft({ ...draft, rules: { ...rules, [key]: { ...(rules[key] || {}), ...patch } } });
  const label = { display: "block", fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate };

  return (
    <Card>
      <div style={{ fontFamily: "var(--font-display)", fontSize: 15.5, fontWeight: 600, color: T.ink }}>Rules</div>
      <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.secondary, marginTop: 4, lineHeight: 1.5 }}>
        What lands on somebody’s list each morning. A rule that is off produces nothing; the others
        carry on. Items clear themselves when the work happens — including when it happens in GHL.
      </div>
      {meta.map((r) => {
        const cfg = rules[r.key] || {};
        const on = cfg.on !== false;
        return (
          <div key={r.key} style={{ borderTop: `1px solid ${T.line}`, padding: "12px 0",
                                    display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <label style={{ display: "inline-flex", alignItems: "center", gap: 8, minWidth: 0, flex: "1 1 220px" }}>
              <input type="checkbox" checked={on} disabled={!live}
                     onChange={(e) => set(r.key, { on: e.target.checked })} />
              <span style={{ fontFamily: "var(--font-text)", fontSize: 13, color: T.ink, opacity: on ? 1 : 0.55 }}>{r.label}</span>
            </label>
            <span style={{ display: "inline-flex", gap: 10, flexWrap: "wrap" }}>
              {r.fields.map((f) => (
                <label key={f.name} style={{ ...label, opacity: on ? 1 : 0.45 }}>
                  <span style={{ fontWeight: 400, fontSize: 11, color: T.muted }}>{f.name.replace(/_/g, " ")}</span>
                  <input type="number" min={f.min} max={f.max} disabled={!live || !on}
                         value={cfg[f.name] ?? ""}
                         onChange={(e) => set(r.key, { [f.name]: e.target.value === "" ? undefined : Number(e.target.value) })}
                         style={{ display: "block", width: 84, boxSizing: "border-box", marginTop: 3,
                                  fontFamily: "var(--font-data)", fontSize: 13, color: T.ink,
                                  background: T.white, border: `1px solid ${T.line}`, borderRadius: 7,
                                  padding: "6px 8px" }} />
                  <span style={{ fontWeight: 400, fontSize: 10.5, color: T.muted }}>{f.min}–{f.max}</span>
                </label>
              ))}
            </span>
          </div>
        );
      })}
    </Card>
  );
}

function RecruitingTemplates({ data, draft, setDraft, live }) {
  const t = draft.templates || {};
  const fields = data.merge_fields || [];
  const set = (key) => (e) => setDraft({ ...draft, templates: { ...t, [key]: e.target.value } });
  const box = { width: "100%", boxSizing: "border-box", fontFamily: "var(--font-text)", fontSize: 13,
                color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8,
                padding: "9px 11px", marginTop: 5 };
  const label = { display: "block", fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate, marginTop: 14 };

  return (
    <Card>
      <div style={{ fontFamily: "var(--font-display)", fontSize: 15.5, fontWeight: 600, color: T.ink }}>Templates</div>
      <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.secondary, marginTop: 4, lineHeight: 1.5 }}>
        The drafts the drawer offers. Written by a person, never generated — somebody is about to
        send this to a stranger and has to be able to check it.
      </div>
      <div style={{ fontFamily: "var(--font-data)", fontSize: 11, color: T.muted, marginTop: 8 }}>
        Merge fields: {fields.map((f) => `{${f}}`).join(" · ")}
      </div>
      <label style={label}>Text<textarea rows={3} style={box} value={t.text || ""} disabled={!live} onChange={set("text")} /></label>
      <label style={label}>Email subject<input style={box} value={t.email_subject || ""} disabled={!live} onChange={set("email_subject")} /></label>
      <label style={label}>Email body<textarea rows={5} style={box} value={t.email_body || ""} disabled={!live} onChange={set("email_body")} /></label>
    </Card>
  );
}

function RecruitingRoster({ data, live, onChanged }) {
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(null);
  const [err, setErr] = useState(null);
  const seats = data.seats || [];
  const users = data.options?.users || [];
  const calendars = data.options?.calendars || [];

  async function patch(seat, body) {
    setBusy(seat.id); setErr(null);
    try { await patchJSON(`/ulrg/recruiting/seats/${seat.id}`, body); onChanged(); }
    catch (e) { setErr(String(e?.message || e)); }
    finally { setBusy(null); }
  }

  const cell = { fontFamily: "var(--font-text)", fontSize: 12.5, color: T.ink };
  const select = { width: "100%", boxSizing: "border-box", fontFamily: "var(--font-text)", fontSize: 12.5, color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 7, padding: "6px 8px" };

  return (
    <Card>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 15.5, fontWeight: 600, color: T.ink }}>Roster</div>
          <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.secondary, marginTop: 4 }}>
            Who closes and who books. A seat owns candidates, carries a calendar, and is the unit write-back is turned on for.
          </div>
        </div>
        <span style={{ flex: 1 }} />
        <SBtn kind="primary" disabled={!live} onClick={() => setAdding(true)}>+ Add seat</SBtn>
      </div>

      {err && <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.poppyText, marginTop: 12 }}>{err}</div>}

      {seats.length === 0 && (
        <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.muted, marginTop: 14 }}>
          No seats yet. Until there is at least one Team Leader, nothing has an owner and the tab has nobody to attribute a signing to.
        </div>
      )}

      {seats.map((seat) => (
        <div key={seat.id} style={{ borderTop: `1px solid ${T.line}`, padding: "13px 0", opacity: seat.active ? 1 : 0.55 }}>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.3fr) minmax(0,1fr) minmax(0,1fr)", gap: 12, alignItems: "start" }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ ...cell, fontWeight: 600, fontSize: 13.5 }}>{seat.display_name}</div>
              <div style={{ fontFamily: "var(--font-data)", fontSize: 10.5, color: T.muted, textTransform: "uppercase", letterSpacing: ".06em", marginTop: 2 }}>
                {/* The title supersedes the role rather than joining it: a title is written as
                    the whole descriptor ("Team Leader · Draper"), so printing both gave
                    "TEAM LEADER · TEAM LEADER · DRAPER". */}
                {seat.title || (seat.role === "sdr" ? "SDR" : "Team Leader")}
              </div>
              {seat.from_number && <div style={{ fontFamily: "var(--font-data)", fontSize: 11, color: T.muted, marginTop: 3 }}>{seat.from_number}</div>}
            </div>
            <label style={{ minWidth: 0 }}>
              <span style={{ fontFamily: "var(--font-text)", fontSize: 11, color: T.muted }}>GHL user</span>
              {/* Matched by email as a SUGGESTION and confirmed here. An automatic match that
                  cannot be corrected from the UI is FUB-SPEC A7. */}
              <select style={select} value={seat.ghl_user_id || ""} disabled={!live || busy === seat.id}
                      onChange={(e) => patch(seat, { ghl_user_id: e.target.value || null })}>
                <option value="">— unmatched —</option>
                {users.map((u) => <option key={u.id} value={u.id}>{u.name || u.email}</option>)}
              </select>
            </label>
            <label style={{ minWidth: 0 }}>
              <span style={{ fontFamily: "var(--font-text)", fontSize: 11, color: T.muted }}>Recruiting calendar</span>
              <select style={select} value={seat.calendar_id || ""} disabled={!live || busy === seat.id}
                      onChange={(e) => patch(seat, { calendar_id: e.target.value || null })}>
                <option value="">— none —</option>
                {calendars.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </label>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 14, marginTop: 10, flexWrap: "wrap" }}>
            {/* Gate 3 of 4 (§5.2), and the reason the rollout can go one person at a time. */}
            <label style={{ display: "inline-flex", alignItems: "center", gap: 7, fontFamily: "var(--font-text)", fontSize: 12, color: T.secondary }}>
              <input type="checkbox" checked={Boolean(seat.writeback_enabled)} disabled={!live || busy === seat.id}
                     onChange={(e) => patch(seat, { writeback_enabled: e.target.checked })} />
              Can send from Axcion
            </label>
            <span style={{ flex: 1 }} />
            {seat.active
              ? <SBtn disabled={!live || busy === seat.id} onClick={() => patch(seat, { active: false })}>Deactivate</SBtn>
              : <SBtn disabled={!live || busy === seat.id} onClick={() => patch(seat, { active: true })}>Reactivate</SBtn>}
          </div>
        </div>
      ))}

      {adding && <RecruitingSeatModal users={users} calendars={calendars} live={live}
        onClose={() => setAdding(false)}
        onDone={() => { setAdding(false); onChanged(); }} />}
    </Card>
  );
}

function RecruitingSeatModal({ users, calendars, live, onClose, onDone }) {
  const [form, setForm] = useState({ role: "team_leader", display_name: "", title: "", ghl_user_id: "", calendar_id: "", from_number: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  async function submit(e) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      await postJSON("/ulrg/recruiting/seats", {
        ...form,
        ghl_user_id: form.ghl_user_id || null,
        calendar_id: form.calendar_id || null,
        from_number: form.from_number.trim() || null,
      });
      onDone();
    } catch (e2) {
      setErr(String(e2?.message || e2) || "Couldn't add the seat.");
    } finally { setBusy(false); }
  }

  const field = { width: "100%", boxSizing: "border-box", fontFamily: "var(--font-text)", fontSize: 13, color: T.ink, background: T.white, border: `1px solid ${T.line}`, borderRadius: 8, padding: "9px 11px", marginTop: 5 };
  const label = { display: "block", fontFamily: "var(--font-text)", fontSize: 12, fontWeight: 600, color: T.slate, marginTop: 14 };

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,46,44,0.34)", zIndex: 50, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
      <form onClick={(e) => e.stopPropagation()} onSubmit={submit} style={{ width: "100%", maxWidth: 440, background: T.white, borderRadius: 14, padding: 22, boxShadow: "0 20px 60px rgba(0,46,44,.22)" }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 600, color: T.ink }}>Add a seat</div>
        <label style={label}>Role
          <select style={field} value={form.role} onChange={set("role")}>
            <option value="team_leader">Team Leader — closes recruits, owns them from the meeting on</option>
            <option value="sdr">SDR — books appointments onto Team Leader calendars</option>
          </select>
        </label>
        <label style={label}>Name<input style={field} value={form.display_name} onChange={set("display_name")} required /></label>
        <label style={label}>Title <span style={{ fontWeight: 400, color: T.muted }}>· optional, e.g. “Team Leader · Draper”</span>
          <input style={field} value={form.title} onChange={set("title")} />
        </label>
        <label style={label}>GHL user <span style={{ fontWeight: 400, color: T.muted }}>· so their name lands on what they do</span>
          <select style={field} value={form.ghl_user_id} onChange={set("ghl_user_id")}>
            <option value="">— unmatched —</option>
            {users.map((u) => <option key={u.id} value={u.id}>{u.name || u.email}</option>)}
          </select>
        </label>
        <label style={label}>Recruiting calendar
          <select style={field} value={form.calendar_id} onChange={set("calendar_id")}>
            <option value="">— none —</option>
            {calendars.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
        <label style={label}>Sending number <span style={{ fontWeight: 400, color: T.muted }}>· optional, E.164 e.g. +18015550142</span>
          <input style={field} value={form.from_number} onChange={set("from_number")} placeholder="+1…" />
        </label>
        {err && <div style={{ fontFamily: "var(--font-text)", fontSize: 12.5, color: T.poppyText, marginTop: 12 }}>{err}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18 }}>
          <button type="button" onClick={onClose} style={btn()}>Cancel</button>
          <button type="submit" disabled={busy || !live} style={busy || !live ? btn("disabled") : btn("primary")}>{busy ? "Adding…" : "Add seat"}</button>
        </div>
      </form>
    </div>
  );
}

/* The offline payload, field for field with the live one. Same reason the Integrations sample
   exists: a missing key is a white screen, and the preview must exercise the shape production
   sends rather than a convenient subset. */
const RECRUITING_SAMPLE = {
  connected: true, location_id: "loc_rec",
  settings: { pipeline_id: "pipe_1", recruiting_stage_groups: [["Met", "team_leader", ["st_met"]]],
              gci_field_id: null, brokerage_field_id: null,
              quiet_hours: { start: 8, end: 21 }, writeback_enabled: false, dry_run: true, auto_clear: true,
              rules: { new_lead_untouched: { on: true, minutes: 60 }, appt_24h: { on: true, from_hours: 18, to_hours: 30 },
                       met_no_next_step: { on: true, hours: 48 }, offer_out_stale: { on: true, days: 5 },
                       no_touch_7d: { on: true, days: 7 }, stage_14d: { on: true, days: 14 },
                       appt_set_no_event: { on: true, hours: 24 } },
              templates: { text: "Hi {first} — {owner_first} here. Wanted to check in on where things stand.",
                           email_subject: "Following up, {first}",
                           email_body: "Hi {first},\n\nJust following up.\n\n{owner_first}" } },
  options: {
    pipelines: [{ id: "pipe_1", name: "Agent Recruiting", stages: [
      { id: "st_sourced", name: "Sourced" }, { id: "st_appt", name: "Appointment Set" },
      { id: "st_met", name: "Met" }, { id: "st_offer", name: "Offer Out" },
      { id: "st_signed", name: "Signed" }, { id: "st_nurture", name: "Nurture" }] }],
    calendars: [{ id: "cal_j", name: "Jenna · Recruiting" }, { id: "cal_m", name: "Marcus · Recruiting" }],
    users: [{ id: "u_j", name: "Jenna Ruiz", email: "j***@example.com" },
            { id: "u_c", name: "Cole Whittaker", email: "c***@example.com" }],
    custom_fields: [{ id: "cf_gci", name: "Trailing 12 GCI", data_type: "MONETORY", model: "opportunity" }],
    errors: {},
  },
  suggested_groups: null,
  webhook_configured: false,
  rule_meta: [
    { key: "new_lead_untouched", label: "New lead, no contact", fields: [{ name: "minutes", min: 5, max: 1440 }] },
    { key: "appt_24h", label: "Appointment tomorrow", fields: [{ name: "from_hours", min: 2, max: 72 }, { name: "to_hours", min: 4, max: 96 }] },
    { key: "met_no_next_step", label: "Met, no next step", fields: [{ name: "hours", min: 4, max: 336 }] },
    { key: "offer_out_stale", label: "Offer out, gone quiet", fields: [{ name: "days", min: 1, max: 60 }] },
    { key: "no_touch_7d", label: "No contact in a week", fields: [{ name: "days", min: 2, max: 90 }] },
    { key: "stage_14d", label: "Stuck in stage", fields: [{ name: "days", min: 3, max: 180 }] },
    { key: "appt_set_no_event", label: "Booked, no appointment", fields: [{ name: "hours", min: 2, max: 336 }] },
  ],
  merge_fields: ["first", "owner_first", "calendar_owner"],
  seats: [
    { id: "s1", role: "team_leader", display_name: "Jenna Ruiz", title: "Team Leader · Draper",
      ghl_user_id: "u_j", calendar_id: "cal_j", from_number: null, writeback_enabled: false, active: true, user_id: null },
    { id: "s2", role: "sdr", display_name: "Cole Whittaker", title: "SDR",
      ghl_user_id: "u_c", calendar_id: null, from_number: "+18015550142", writeback_enabled: false, active: true, user_id: null },
  ],
};

/* ── routes ────────────────────────────────────────────────── */

const SAMPLE_BADGES = { businesses: 5, integrations_attention: 1 };

export default function Settings() {
  const [role, setRole] = useState(null);
  const [aiOn, setAiOn] = useState(false);
  const [ulrgOn, setUlrgOn] = useState(!API_BASE);      // offline preview shows every page
  const [badges, setBadges] = useState(null);
  useEffect(() => {
    if (!API_BASE) { setRole("owner"); setBadges(SAMPLE_BADGES); return; }
    getJSON("/me").then((m) => {
      // Settings is a SIBLING route of CommandCenter, which is the only other caller of
      // setBrand -- so a deep link straight to /settings found an empty brand holder and the
      // workspace chip read "Workspace". Same shape as the palette not applying on this route.
      if (m.brand) setBrand(m.brand);
      setRole(m.role); setAiOn((m.tabs || []).includes("ai_employees"));
      setUlrgOn(Boolean(m.all_tabs) || (m.tabs || []).includes("ulrg"));
    }).catch(() => setRole("member"));
    // A badge is decoration: if this fails the nav simply carries no counts.
    getJSON("/settings/badges").then(setBadges).catch(() => {});
  }, []);
  // Wait for the role before mounting routes — else the catch-all redirect fires
  // with isAdmin=false and bounces a deep-link to /settings/users away.
  if (role === null) {
    return <SettingsShell role={null} ulrgOn={ulrgOn} badges={badges}><Card><div style={{ color: T.muted, fontSize: 13 }}>Loading…</div></Card></SettingsShell>;
  }
  const isAdmin = role === "owner" || role === "admin";
  const home = isAdmin ? "/settings/integrations" : "/settings/account";
  return (
    <SettingsShell role={role} aiOn={aiOn} ulrgOn={ulrgOn} badges={badges}>
      <Routes>
        <Route path="account" element={<AccountPage />} />
        <Route path="security" element={<SecuritySettings />} />
        {isAdmin && <Route path="integrations" element={<IntegrationsPage />} />}
        {isAdmin && <Route path="users" element={<UsersPage />} />}
        {isAdmin && <Route path="businesses" element={<BusinessesPage />} />}
        {isAdmin && <Route path="appearance" element={<Appearance />} />}
        {isAdmin && ulrgOn && <Route path="recruiting" element={<RecruitingPage />} />}
        {isAdmin && aiOn && <Route path="ai" element={<AISettings />} />}
        <Route path="*" element={<Navigate to={home} replace />} />
      </Routes>
    </SettingsShell>
  );
}
