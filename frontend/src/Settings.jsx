import { useEffect, useState } from "react";
import { Routes, Route, Navigate, NavLink, Link } from "react-router-dom";
import { T, PROVIDER_NAME, relativeTime } from "./theme.js";
import { getJSON, postJSON, putJSON } from "./api.js";
import { Icon } from "./Brand.jsx";

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
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700;800&family=Inter:wght@400;500;600&display=swap');
        input:focus-visible, select:focus-visible, textarea:focus-visible, button:focus-visible { outline: 2px solid ${T.teal}; outline-offset: 2px; }
        input[type="checkbox"], input[type="radio"] { accent-color: ${T.evergreen}; width: 15px; height: 15px; }
      `}</style>
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
        provider: "ghl", business_key: row.business_key || "springb",
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
        {err && <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12.5, color: T.poppyText, marginTop: 12 }}>{err}</div>}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 18 }}>
          <button type="button" onClick={onClose} style={btn()}>Cancel</button>
          <button type="submit" disabled={busy} style={busy ? btn("disabled") : btn("primary")}>{busy ? "Saving…" : editing ? "Save changes" : "Connect"}</button>
        </div>
      </form>
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
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.poppyText, background: "rgba(250,128,105,0.08)", border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 12px", marginBottom: 14 }}>
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
              <div style={{ fontFamily: "Inter,sans-serif", fontSize: 13.5, fontWeight: 600, color: T.ink }}>{b.name}</div>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 3 }}>
                {connected ? (
                  <>
                    <span style={{ width: 7, height: 7, borderRadius: 99, background: STATUS_DOT[row.status] || T.muted }} />
                    <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.slate }}>{STATUS_LABEL[row.status] || row.status}</span>
                    {row.last_synced_at && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted }}>· synced {relativeTime(row.last_synced_at)}</span>}
                    {row.status === "error" && row.last_error && <span title={row.last_error} style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.poppyText }}>· {String(row.last_error).slice(0, 40)}</span>}
                  </>
                ) : (
                  <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted }}>Not connected</span>
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

const BIZ_DOT = { ulrg: T.meadow, springb: T.poppy, forum: T.daffodil, becollective: T.petal, sympli: T.teal };

function StatusPill({ status }) {
  const map = {
    ok: { bg: "transparent", dot: T.meadow, text: T.slate, border: "transparent", label: "Connected" },
    stale: { bg: T.daffodilBg, dot: T.daffodil, text: T.daffodilText, border: "transparent", label: "Behind schedule" },
    attention: { bg: "#FFF0EB", dot: T.poppyText, text: T.poppyText, border: "transparent", label: "Action needed" },
    disconnected: { bg: "transparent", dot: "#C9AF92", text: T.muted, border: T.line, label: "Not connected" },
  }[status] || {};
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, background: map.bg,
      border: `1px solid ${map.border}`, borderRadius: 6, padding: "3px 9px",
      fontFamily: "Inter,sans-serif", fontSize: 11, fontWeight: 600, color: map.text, whiteSpace: "nowrap" }}>
      <span style={{ width: 6, height: 6, borderRadius: 99, background: map.dot }} />{map.label}
    </span>
  );
}

const Chip = ({ children }) => (
  <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, fontWeight: 600, color: T.slate,
    background: T.parchment, border: `1px solid ${T.line}`, borderRadius: 5, padding: "2px 8px" }}>{children}</span>
);

const FeedDots = ({ feeds }) => (
  <span style={{ display: "inline-flex", gap: 4, marginLeft: 8 }}>
    {(feeds || []).map((f) => <span key={f} title={f} style={{ width: 7, height: 7, borderRadius: 2, background: BIZ_DOT[f] || T.muted }} />)}
  </span>
);

function SBtn({ kind = "ghost", small, icon, children, onClick, disabled, title }) {
  const s = { ghost: { bg: T.white, color: T.slate, border: T.line }, primary: { bg: T.poppy, color: "#fff", border: T.poppy } }[kind];
  return (
    <button className={`si-btn ${kind}`} onClick={onClick} disabled={disabled} title={title} style={{
      display: "inline-flex", alignItems: "center", gap: 6, background: disabled ? T.parchment : s.bg,
      color: disabled ? T.muted : s.color, border: `1px solid ${disabled ? T.line : s.border}`, borderRadius: 8,
      padding: small ? "5px 11px" : "7px 14px", fontFamily: "Poppins,sans-serif", fontSize: small ? 11.5 : 12,
      fontWeight: 600, cursor: disabled ? "not-allowed" : "pointer" }}>
      {icon && <Icon name={icon} size={12} color="currentColor" />}{children}
    </button>
  );
}

function EntityRow({ e, live, busy, onSync, onReconnect }) {
  const err = e.state === "error";
  const meta = e.realm_id ? `Realm ${e.realm_id}` : "";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "11px 0", borderTop: `1px solid ${T.line}` }}>
      <span style={{ width: 8, height: 8, borderRadius: 2, background: BIZ_DOT[e.business_key] || T.muted, flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.ink }}>{e.business_name}</div>
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: err ? T.poppyText : T.muted, marginTop: 2, display: "flex", alignItems: "center", gap: 5 }}>
          <Icon name={err ? "warning" : "check_circled"} size={11} color={err ? T.poppyText : T.meadow} />
          {err ? (e.detail || "Re-authorize to resume syncing") : (e.last_synced_at ? `Synced ${relativeTime(e.last_synced_at)}` : "Not synced")}
          {meta && <span style={{ color: T.muted }}>· {meta}</span>}
        </div>
      </div>
      {err
        ? <SBtn kind="primary" small icon="sync" disabled={!live} onClick={() => onReconnect(e)}>Reconnect</SBtn>
        : <SBtn small icon="sync" disabled={!live || busy === e.integration_id} onClick={() => onSync(e.integration_id)}>{busy === e.integration_id ? "Syncing…" : "Sync now"}</SBtn>}
    </div>
  );
}

function SourceCard({ s, open, onToggle, live, busy, onSync, onReconnect, onDisconnect, onConnect, onEdit }) {
  const dis = s.status === "disconnected";
  const collapsedLine = s.status === "attention" ? s.status_note
    : dis ? s.desc(s) : s.fresh;
  return (
    <div className={`si-card ${open ? "on" : ""}`}>
      <div className="si-head" role="button" tabIndex={0} aria-expanded={open} onClick={onToggle}
        onKeyDown={(ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); onToggle(); } }}>
        <span className="si-mono">{s.mono}</span>
        <span style={{ flex: 1, minWidth: 0, textAlign: "left" }}>
          <span style={{ display: "flex", alignItems: "center" }}>
            <span style={{ fontFamily: "Poppins,sans-serif", fontSize: 14.5, fontWeight: 600, color: T.ink }}>{s.name}</span>
            <FeedDots feeds={s.feeds} />
          </span>
          <span style={{ display: "block", fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted, marginTop: 3 }}>{collapsedLine}</span>
        </span>
        <span style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {dis ? <SBtn kind="primary" small disabled={!live} onClick={(ev) => { ev.stopPropagation(); onConnect(s); }}>Connect</SBtn> : <StatusPill status={s.status} />}
          <Icon name="chevron_down" size={13} color={T.muted} style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform .22s ease" }} />
        </span>
      </div>
      <div className={`si-collapse ${open ? "open" : ""}`}>
        <div className="si-collapse-in">
          <div style={{ padding: "0 20px 18px" }}>
            <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.secondary, paddingBottom: 12 }}>{s.desc(s)}</div>
            {s.entities?.length > 0 && (
              <div style={{ marginBottom: 4 }}>
                {s.entities.map((e) => <EntityRow key={e.integration_id} e={e} live={live} busy={busy} onSync={onSync} onReconnect={onReconnect} />)}
              </div>
            )}
            {s.config_summary?.length > 0 && (
              <div style={{ borderTop: `1px solid ${T.line}`, padding: "11px 0 3px" }}>
                {s.config_summary.map(([k, v], i) => (
                  <div key={i} style={{ display: "flex", gap: 14, padding: "4px 0", fontFamily: "Inter,sans-serif", fontSize: 12 }}>
                    <span style={{ width: 130, color: T.muted }}>{k}</span>
                    <span style={{ color: T.secondary, fontVariantNumeric: "tabular-nums" }}>{v}</span>
                  </div>
                ))}
              </div>
            )}
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", borderTop: `1px solid ${T.line}`, paddingTop: 13, marginTop: 8 }}>
              <span style={{ fontFamily: "Inter,sans-serif", fontSize: 10.5, fontWeight: 700, letterSpacing: ".08em", textTransform: "uppercase", color: T.muted, marginRight: 2 }}>Provides</span>
              {(s.provides || []).map((c) => <Chip key={c}>{c}</Chip>)}
              <span style={{ flex: 1 }} />
              {s.last_run && <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: s.status === "stale" ? T.daffodilText : T.muted }}>{s.last_run}</span>}
            </div>
            {dis ? (
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 14 }}>
                <SBtn kind="primary" icon="open" disabled={!live} onClick={() => onConnect(s)}>Connect {s.name}</SBtn>
                <span style={{ fontFamily: "Inter,sans-serif", fontSize: 11.5, color: T.muted }}>{s.desc(s)}</span>
              </div>
            ) : (
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 14 }}>
                {!s.entities?.length && s.provider !== "ghl" && <SBtn small icon="sync" disabled={!live || busy === s.integration_id} onClick={() => onSync(s.integration_id)}>{busy === s.integration_id ? "Syncing…" : "Sync now"}</SBtn>}
                {s.provider === "ghl" && <SBtn small icon="sync" disabled={!live || busy === s.integration_id} onClick={() => onSync(s.integration_id)}>{busy === s.integration_id ? "Syncing…" : "Sync now"}</SBtn>}
                {s.entities?.length > 0 && <SBtn small icon="open" disabled={!live} onClick={() => onConnect(s)}>Connect another entity</SBtn>}
                {s.provider === "ghl" && <SBtn small icon="tune" disabled={!live} onClick={() => onEdit(s)}>Edit configuration</SBtn>}
                <span style={{ flex: 1 }} />
                {s.integration_id && <button className="si-danger" disabled={!live} onClick={() => onDisconnect(s)}>Disconnect {s.name}</button>}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

const MONO = { qbo: "QB", sisu: "Si", fub: "FB", ghl: "GH", arive: "Ar" };
const DESC = {
  qbo: () => "Financial source of truth · one connection per entity",
  sisu: () => "Real estate production — transactions, agents, GCI",
  fub: () => "CRM — leads and agent activity",
  ghl: () => "beCollective + The Forum — members, subscriptions, events",
  arive: () => "Uses your Arive API key · lights up Sympli's pipeline and the referral flywheel",
};
const SAMPLE_VIEW = {
  healthy: 3, total: 5, next_sync_in_min: 14,
  sources: [
    { provider: "qbo", name: "QuickBooks", mono: "QB", status: "attention", status_note: "1 of 3 entities needs reconnect", feeds: ["ulrg", "springb", "sympli"], provides: ["Profit & Loss", "Balance Sheet"], last_run: "Last run · 2 entities · 4.2s",
      entities: [
        { integration_id: "e1", business_key: "ulrg", business_name: "ULRG + Team", state: "ok", last_synced_at: new Date(Date.now() - 32 * 60000).toISOString(), realm_id: "9130 3540 11" },
        { integration_id: "e2", business_key: "springb", business_name: "Spring B", state: "error", detail: "Token expired Jun 29" },
        { integration_id: "e3", business_key: "sympli", business_name: "Sympli Mortgage", state: "ok", last_synced_at: new Date(Date.now() - 32 * 60000).toISOString(), realm_id: "9130 3541 88" },
      ] },
    { provider: "sisu", name: "Sisu", mono: "Si", status: "ok", fresh: "Synced 26 min ago", feeds: ["ulrg"], provides: ["Transactions", "Agents", "GCI"], last_run: "Last run · 412 records · 3.1s", integration_id: "s1" },
    { provider: "fub", name: "Follow Up Boss", mono: "FB", status: "stale", fresh: "Synced 19 hours ago", feeds: ["ulrg"], provides: ["Leads", "Agents"], last_run: "Auto-sync has missed its last 37 runs — check the connection", integration_id: "f1" },
    { provider: "ghl", name: "Go High Level", mono: "GH", status: "ok", fresh: "Synced 1 hour ago", feeds: ["springb"], provides: ["Members", "Subscriptions", "Events"], last_run: "Last run · 142 members · 38 subscriptions · 2.4s", integration_id: "g1", config: {}, config_summary: [["Location ID", "LqK4…f82"], ["Member tags", "5 tags"], ["Next event", "Park City, UT"]] },
    { provider: "arive", name: "Arive", mono: "Ar", status: "disconnected", feeds: [], provides: ["Loans", "Pipeline"], business_key: "sympli" },
  ],
};

function IntegrationsPage() {
  const [view, setView] = useState(null);
  const [error, setError] = useState(false);
  const [open, setOpen] = useState({ qbo: true });
  const [busy, setBusy] = useState(null);
  const [syncingAll, setSyncingAll] = useState(false);
  const [connecting, setConnecting] = useState(null);
  const live = Boolean(API_BASE);

  function decorate(v) {
    return { ...v, sources: v.sources.map((s) => ({ ...s, mono: MONO[s.provider], desc: DESC[s.provider] })) };
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
  async function disconnectSource(s) {
    if (!window.confirm(`Disconnect ${s.name}? Stored tokens are removed; synced history stays.`)) return;
    setBusy(s.integration_id);
    try { await postJSON(`/integrations/${s.integration_id}/disconnect`); } finally { setBusy(null); load(); }
  }
  function connectSource(s) {
    if (s.provider === "qbo") return qboConnect(s.entities?.[0]?.business_key || "ulrg");
    if (s.provider === "ghl") return setConnecting({ config: s.config || {}, business_key: s.business_key || "springb", status: "disconnected" });
    if (s.provider === "arive") { window.alert("Arive connects with your API key — wiring lands in Phase 3."); return; }
  }
  const editConfig = (s) => setConnecting({ config: s.config || {}, business_key: s.business_key || "springb", status: "connected" });
  const reconnectEntity = (e) => qboConnect(e.business_key);

  if (error) return <Card title="Integrations"><div style={{ color: T.muted, fontSize: 13 }}>Couldn't load integrations.</div></Card>;
  if (!view) return <Card title="Integrations"><div style={{ color: T.muted, fontSize: 13 }}>Loading…</div></Card>;

  const toggle = (k) => setOpen((o) => ({ ...o, [k]: !o[k] }));
  const attention = view.sources.filter((s) => s.status === "attention" || s.status === "stale").length;

  return (
    <>
      <style>{`
        .si-card { background:${T.white}; border:1px solid ${T.line}; border-radius:14px; margin-bottom:12px; box-shadow:0 1px 2px rgba(0,46,44,.04); transition:box-shadow .2s ease, border-color .2s ease; }
        .si-card.on { box-shadow:0 2px 4px rgba(0,46,44,.05), 0 14px 30px rgba(0,46,44,.07); border-color:#E0D6C6; }
        .si-head { display:flex; align-items:center; gap:14px; width:100%; background:none; border:none; padding:15px 20px; cursor:pointer; }
        .si-mono { width:36px; height:36px; border-radius:10px; background:${T.parchment}; border:1px solid ${T.line}; display:inline-flex; align-items:center; justify-content:center; font-family:Poppins,sans-serif; font-size:12.5px; font-weight:700; color:${T.evergreen}; flex-shrink:0; }
        .si-collapse { display:grid; grid-template-rows:0fr; transition:grid-template-rows .26s cubic-bezier(.4,0,.2,1); }
        .si-collapse.open { grid-template-rows:1fr; }
        .si-collapse-in { overflow:hidden; }
        .si-btn { transition:background .14s ease, border-color .14s ease, color .14s ease; }
        .si-btn.ghost:hover:not(:disabled) { border-color:${T.teal}; color:${T.teal}; }
        .si-btn.primary:hover:not(:disabled) { background:${T.poppyActive}; border-color:${T.poppyActive}; }
        .si-danger { background:none; border:none; cursor:pointer; font-family:Inter,sans-serif; font-size:11.5px; font-weight:600; color:${T.muted}; padding:4px 2px; }
        .si-danger:hover:not(:disabled) { color:${T.poppyText}; }
        .si-btn:focus-visible, .si-danger:focus-visible { outline:2px solid ${T.teal}; outline-offset:2px; border-radius:8px; }
        @media (prefers-reduced-motion: reduce) { .si-collapse { transition:none; } }
      `}</style>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 16, flexWrap: "wrap", paddingBottom: 18, marginBottom: 18, borderBottom: `1px solid ${T.line}` }}>
        <div>
          <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 10.5, fontWeight: 700, letterSpacing: ".13em", textTransform: "uppercase", color: T.poppyText, display: "flex", alignItems: "center", gap: 7 }}>
            <Icon name="puzzle" size={13} color={T.poppyText} />Settings · Integrations
          </div>
          <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 22, fontWeight: 700, letterSpacing: "-.01em", marginTop: 6, color: T.ink }}>Data sources</div>
          <div style={{ fontSize: 12.5, color: T.slate, marginTop: 5, maxWidth: 520, lineHeight: 1.5 }}>Every number in the Command Center traces to one of these connections. Expand a source to manage its accounts, configuration, and sync.</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <div style={{ textAlign: "right" }}>
            <div style={{ fontFamily: "Poppins,sans-serif", fontSize: 13, fontWeight: 600, color: T.ink, display: "flex", alignItems: "center", gap: 6, justifyContent: "flex-end" }}>
              <Icon name="check_circled" size={13} color={T.meadow} />{view.healthy} of {view.total} sources healthy
            </div>
            <div style={{ fontSize: 11, color: T.muted, marginTop: 3 }}>
              {attention > 0 ? `${attention} need${attention === 1 ? "s" : ""} attention · ` : ""}{view.next_sync_in_min != null ? `next auto-sync in ${view.next_sync_in_min} min` : "auto-sync every 30 min"}
            </div>
          </div>
          <SBtn icon="sync" disabled={!live || syncingAll} onClick={syncAll}>{syncingAll ? "Syncing…" : "Sync all"}</SBtn>
        </div>
      </div>

      {!live && (
        <div style={{ fontFamily: "Inter,sans-serif", fontSize: 12, color: T.poppyText, background: "rgba(250,128,105,0.08)", border: `1px solid ${T.line}`, borderRadius: 8, padding: "8px 12px", marginBottom: 14 }}>
          Preview (sample) — connect to the live API to manage sources.
        </div>
      )}

      {view.sources.map((s) => (
        <SourceCard key={s.provider} s={s} open={!!open[s.provider]} onToggle={() => toggle(s.provider)}
          live={live} busy={busy} onSync={syncOne} onReconnect={reconnectEntity}
          onDisconnect={disconnectSource} onConnect={connectSource} onEdit={editConfig} />
      ))}

      <div style={{ fontFamily: "Inter,sans-serif", fontSize: 11, color: T.muted, padding: "8px 2px" }}>
        Auto-sync runs every 30 minutes. Disconnecting removes stored tokens; historical data already synced stays in the dashboard.
      </div>

      {connecting && (
        <GhlConnectForm row={connecting} onClose={() => setConnecting(null)}
          onDone={() => { setConnecting(null); load(); }} />
      )}
    </>
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
  { key: "ulrg", name: "ULRG + Team", tag: "Real estate", status: "healthy", accent: T.meadow, ink: "#4D6A4D", is_jv: false, jv_share: 1, watch_margin_below: null, per_loan_share: null, expense_run_rate_mode: "manual", expense_run_rate_manual: 96000, default_agent_split: 0.60 },
  { key: "springb", name: "Spring B", tag: "beCollective + The Forum", status: "watch", accent: T.poppy, ink: T.poppyText, is_jv: false, jv_share: 1, watch_margin_below: 25, per_loan_share: null },
  { key: "sympli", name: "Sympli Mortgage", tag: "Joint venture · 50% owned", status: "opportunity", accent: T.teal, ink: T.teal, is_jv: true, jv_share: 0.5, watch_margin_below: null, per_loan_share: 2100 },
];

function BusinessEditForm({ biz, onClose, onDone }) {
  const [f, setF] = useState({
    name: biz.name || "", tag: biz.tag || "", status: biz.status || "healthy",
    accent: biz.accent || "#61835E", ink: biz.ink || "#4D6A4D", is_jv: !!biz.is_jv,
    jv_pct: Math.round((biz.jv_share ?? 1) * 100),
    watch_margin_below: biz.watch_margin_below ?? "",
    per_loan_share: biz.per_loan_share ?? "",
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
        expense_run_rate_mode: f.expense_run_rate_mode,
        expense_run_rate_manual: f.expense_run_rate_mode === "manual" ? numOrNull(f.expense_run_rate_manual) : null,
        default_agent_split: f.agent_split_pct === "" ? null : Math.max(0, Math.min(100, Number(f.agent_split_pct) || 0)) / 100,
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

        <div style={{ marginTop: 18, paddingTop: 14, borderTop: `1px solid ${T.line}`, fontFamily: "Poppins,sans-serif", fontSize: 12.5, fontWeight: 600, color: T.slate }}>Financials (three-lens)</div>
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
