import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";

import AppSwitcher from "../AppSwitcher.jsx";
import { API_BASE, getJSON, hasToken, logout, patchJSON, putJSON } from "../api.js";
import { FUB_LISTS, NAV, ONBOARDING, SOPS, TOOL_GROUPS, TRAINING, WTD_BLOCKS } from "./content.js";

const DEFAULT_CONFIG = {
  calendar: { google_calendar_url: "" },
  marketing_requests: { url: "", label: "Marketing requests" },
  links: { tools: {}, fub_lists: {} },
  brand: {
    font_mode: "proxy",
    mark_url: "",
    display_font: "Playfair Display",
    text_font: "DM Sans",
    utility_font: "Archivo",
  },
  numbers: {
    calls_today: 0,
    appointments_set: 0,
    contracts_pending: 0,
    closed_units: 0,
    closed_volume: 0,
  },
};

const DEFAULT_WTD = { checked: {}, tallies: { calls: 0, conversations: 0, appointments: 0, notes: 0 } };
const DEFAULT_PROGRESS = { done: {} };
const DEFAULT_ACKS = { acked: {} };

function todayKey() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function timezone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "local";
}

function mergeDeep(base, incoming) {
  const out = { ...base, ...(incoming || {}) };
  for (const key of Object.keys(base)) {
    if (base[key] && typeof base[key] === "object" && !Array.isArray(base[key])) {
      out[key] = { ...base[key], ...((incoming || {})[key] || {}) };
    }
  }
  return out;
}

function formatNumber(n) {
  return Number(n || 0).toLocaleString("en-US");
}

function useBootstrap() {
  const [status, setStatus] = useState("loading");
  const [me, setMe] = useState(null);
  const [config, setConfig] = useState(DEFAULT_CONFIG);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (!API_BASE) {
      setMe({
        name: "Preview",
        email: "",
        role: "admin",
        tenant_name: "Workspace",
        apps: [
          { id: "dashboard", name: "Dashboard", href: "/" },
          { id: "intranet", name: "Intranet", href: "/intranet/" },
        ],
      });
      setConfig(DEFAULT_CONFIG);
      setStatus("ready");
      return;
    }
    if (!hasToken()) {
      setStatus("login");
      return;
    }
    setStatus("loading");
    setError("");
    try {
      const [user, intranet] = await Promise.all([getJSON("/me"), getJSON("/intranet/config")]);
      setMe(user);
      setConfig(mergeDeep(DEFAULT_CONFIG, intranet.config || {}));
      document.title = `${user.tenant_name || user.tenant || "Workspace"} Intranet`;
      setStatus("ready");
    } catch (err) {
      if (err.status === 401) setStatus("login");
      else if (err.status === 403) setStatus("disabled");
      else {
        setStatus("error");
        setError(err.detail || err.message || "Could not load intranet.");
      }
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const saveConfig = useCallback(async (patch) => {
    if (!API_BASE) {
      setConfig((prev) => mergeDeep(prev, patch));
      return;
    }
    const next = await patchJSON("/intranet/config", patch);
    setConfig(mergeDeep(DEFAULT_CONFIG, next.config || {}));
  }, []);

  return { status, me, config, error, canConfigure: me?.role === "owner" || me?.role === "admin", refresh, saveConfig };
}

function useScopedState(scope, stateKey, initial, ready) {
  const [value, setValue] = useState(initial);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!ready) return undefined;
    let alive = true;
    setLoaded(false);
    if (!API_BASE) {
      try {
        const raw = window.localStorage.getItem(`intranet:${scope}:${stateKey}`);
        if (raw) setValue(mergeDeep(initial, JSON.parse(raw)));
        else setValue(initial);
      } catch {
        setValue(initial);
      }
      setLoaded(true);
      return undefined;
    }
    getJSON(`/intranet/state/${scope}?state_key=${encodeURIComponent(stateKey)}`)
      .then((res) => {
        if (!alive) return;
        setValue(mergeDeep(initial, res.value || {}));
        setLoaded(true);
      })
      .catch(() => {
        if (alive) setLoaded(true);
      });
    return () => { alive = false; };
  }, [scope, stateKey, ready]);

  useEffect(() => {
    if (!ready || !loaded) return undefined;
    const t = window.setTimeout(() => {
      if (!API_BASE) {
        try { window.localStorage.setItem(`intranet:${scope}:${stateKey}`, JSON.stringify(value)); } catch { /* storage optional */ }
        return;
      }
      putJSON(`/intranet/state/${scope}`, { state_key: stateKey, timezone: timezone(), value }).catch(() => {});
    }, 350);
    return () => window.clearTimeout(t);
  }, [scope, stateKey, value, ready, loaded]);

  return [value, setValue, loaded];
}

function Shell({ me, children }) {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const active = location.pathname.split("/").filter(Boolean)[0] || "home";
  const signOut = () => {
    logout();
    window.location.href = "/";
  };
  return (
    <div className="intra-shell">
      {open && <button className="intra-scrim" aria-label="Close navigation" onClick={() => setOpen(false)} />}
      <aside className={`intra-rail ${open ? "open" : ""}`} aria-label="Intranet sections">
        <div className="intra-brand">
          <div className="intra-mark" aria-hidden>{(me?.tenant_name || "A").slice(0, 1).toUpperCase()}</div>
          <div>
            <div className="intra-brand-name">{me?.tenant_name || "Workspace"}</div>
            <div className="intra-product">Intranet</div>
          </div>
        </div>
        <nav className="intra-nav">
          {NAV.map((n) => (
            <NavLink key={n.id} to={n.id === "home" ? "/" : `/${n.id}`} onClick={() => setOpen(false)}
              className={({ isActive }) => `intra-nav-item ${isActive || active === n.id ? "active" : ""}`}>
              <span className="intra-nav-icon" aria-hidden>{n.icon}</span>
              <span>{n.label}</span>
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="intra-main">
        <header className="intra-topbar">
          <button className="intra-menu" type="button" onClick={() => setOpen(true)} aria-label="Open navigation">
            <span /><span /><span />
          </button>
          <div>
            <div className="intra-eyebrow">{todayKey()}</div>
            <h1>{NAV.find((n) => n.id === active)?.label || "Intranet"}</h1>
          </div>
          <div className="intra-top-actions">
            <AppSwitcher user={me} />
            <div className="intra-account">
              <span>{(me?.name || "?").slice(0, 1).toUpperCase()}</span>
              <strong>{me?.name || "Account"}</strong>
            </div>
            <button className="intra-icon-btn" type="button" onClick={signOut} title="Sign out" aria-label="Sign out">
              <span aria-hidden>&gt;</span>
            </button>
          </div>
        </header>
        <div className="intra-content">{children}</div>
      </main>
    </div>
  );
}

function AccessState({ title, message, action }) {
  return (
    <div className="intra-access">
      <div className="intra-mark large" aria-hidden>A</div>
      <h1>{title}</h1>
      <p>{message}</p>
      {action}
    </div>
  );
}

function Tile({ label, value, note }) {
  return (
    <div className="intra-tile">
      <span>{label}</span>
      <strong>{value}</strong>
      {note && <small>{note}</small>}
    </div>
  );
}

function Panel({ title, action, children }) {
  return (
    <section className="intra-panel">
      <div className="intra-panel-head">
        <h2>{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

function Empty({ title, children }) {
  return (
    <div className="intra-empty">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
    </div>
  );
}

function Home({ config, wtd, training, onboarding }) {
  const numbers = config.numbers || DEFAULT_CONFIG.numbers;
  const totalTasks = WTD_BLOCKS.flatMap((b) => b.items).length;
  const doneToday = Object.values(wtd.checked || {}).filter(Boolean).length;
  const lessons = TRAINING.flatMap((c) => c.lessons.map((l) => `${c.key}:${l}`));
  const lessonsDone = lessons.filter((k) => training.done?.[k]).length;
  const onboardDone = ONBOARDING.filter((item) => onboarding.done?.[item.key]).length;
  return (
    <div className="intra-stack">
      <section className="intra-home-band">
        <div>
          <div className="intra-eyebrow">Workspace hub</div>
          <h2>Start here, then work the day.</h2>
        </div>
        <div className="intra-home-score">
          <strong>{doneToday}/{totalTasks}</strong>
          <span>WTD</span>
        </div>
      </section>
      <div className="intra-grid four">
        <Tile label="Calls today" value={formatNumber(numbers.calls_today)} />
        <Tile label="Appointments set" value={formatNumber(numbers.appointments_set)} />
        <Tile label="Contracts pending" value={formatNumber(numbers.contracts_pending)} />
        <Tile label="Closed units" value={formatNumber(numbers.closed_units)} />
      </div>
      <div className="intra-grid two">
        <Panel title="Progress">
          <div className="intra-progress-list">
            <ProgressRow label="Training" done={lessonsDone} total={lessons.length} />
            <ProgressRow label="First 30 Days" done={onboardDone} total={ONBOARDING.length} />
            <ProgressRow label="Win the Day" done={doneToday} total={totalTasks} />
          </div>
        </Panel>
        <Panel title="Open Items">
          <div className="intra-list compact">
            <span>Calendar not connected</span>
            <span>Marketing request destination not connected</span>
            <span>API integrations pending</span>
          </div>
        </Panel>
      </div>
    </div>
  );
}

function ProgressRow({ label, done, total }) {
  const pct = total ? Math.round((done / total) * 100) : 0;
  return (
    <div className="intra-progress-row">
      <div><strong>{label}</strong><span>{done}/{total}</span></div>
      <div className="intra-meter"><span style={{ width: `${pct}%` }} /></div>
    </div>
  );
}

function Tools({ config }) {
  const links = config.links?.tools || {};
  return (
    <div className="intra-stack">
      {TOOL_GROUPS.map((group) => (
        <Panel key={group.id} title={group.label}>
          <div className="intra-card-grid">
            {group.tools.map((tool) => {
              const url = links[tool.key] || "";
              return (
                <div className="intra-tool" key={tool.key}>
                  <div>
                    <strong>{tool.name}</strong>
                    <span>{tool.note}</span>
                  </div>
                  {url
                    ? <a className="intra-button small" href={url} target="_blank" rel="noreferrer">Open</a>
                    : <span className="intra-muted-pill">No URL</span>}
                </div>
              );
            })}
          </div>
        </Panel>
      ))}
    </div>
  );
}

function WinTheDay({ state, setState, config }) {
  const links = config.links?.fub_lists || {};
  const blocks = WTD_BLOCKS;
  const total = blocks.flatMap((b) => b.items).length;
  const done = Object.values(state.checked || {}).filter(Boolean).length;
  const toggle = (key) => setState((s) => ({ ...s, checked: { ...(s.checked || {}), [key]: !s.checked?.[key] } }));
  const setTally = (key, value) => setState((s) => ({
    ...s,
    tallies: { ...(s.tallies || {}), [key]: Math.max(0, Number(value || 0)) },
  }));
  return (
    <div className="intra-stack">
      <section className="intra-runner">
        <div>
          <div className="intra-eyebrow">Local day reset</div>
          <h2>{done}/{total} complete</h2>
        </div>
        <div className="intra-meter wide"><span style={{ width: `${total ? Math.round((done / total) * 100) : 0}%` }} /></div>
      </section>
      <div className="intra-grid two">
        {blocks.map((block) => (
          <Panel key={block.id} title={block.title}>
            <div className="intra-check-list">
              {block.items.map((item) => {
                const key = `${block.id}:${item}`;
                return (
                  <label key={key} className="intra-check">
                    <input type="checkbox" checked={Boolean(state.checked?.[key])} onChange={() => toggle(key)} />
                    <span>{item}</span>
                  </label>
                );
              })}
            </div>
          </Panel>
        ))}
      </div>
      <Panel title="Daily Tallies">
        <div className="intra-tally-grid">
          {Object.keys(DEFAULT_WTD.tallies).map((key) => (
            <label key={key}>
              <span>{key.replace("_", " ")}</span>
              <input type="number" min="0" value={state.tallies?.[key] ?? 0} onChange={(e) => setTally(key, e.target.value)} />
            </label>
          ))}
        </div>
      </Panel>
      <Panel title="Follow Up Boss Lists">
        <div className="intra-card-grid">
          {FUB_LISTS.map((item) => {
            const url = links[item.key] || "";
            return (
              <div className="intra-list-card" key={item.key}>
                <strong>{item.name}</strong>
                {url
                  ? <a href={url} target="_blank" rel="noreferrer">Open list</a>
                  : <span>No tenant URL</span>}
              </div>
            );
          })}
        </div>
      </Panel>
    </div>
  );
}

function Training({ state, setState }) {
  const toggle = (key) => setState((s) => ({ ...s, done: { ...(s.done || {}), [key]: !s.done?.[key] } }));
  return (
    <div className="intra-grid two">
      {TRAINING.map((course) => (
        <Panel key={course.key} title={course.title}>
          <div className="intra-check-list">
            {course.lessons.map((lesson) => {
              const key = `${course.key}:${lesson}`;
              return (
                <label key={key} className="intra-check">
                  <input type="checkbox" checked={Boolean(state.done?.[key])} onChange={() => toggle(key)} />
                  <span>{lesson}</span>
                </label>
              );
            })}
          </div>
        </Panel>
      ))}
    </div>
  );
}

function Onboarding({ state, setState }) {
  const toggle = (key) => setState((s) => ({ ...s, done: { ...(s.done || {}), [key]: !s.done?.[key] } }));
  return (
    <Panel title="First 30 Days">
      <div className="intra-steps">
        {ONBOARDING.map((item, idx) => (
          <label key={item.key} className={`intra-step ${state.done?.[item.key] ? "done" : ""}`}>
            <input type="checkbox" checked={Boolean(state.done?.[item.key])} onChange={() => toggle(item.key)} />
            <span>{idx + 1}</span>
            <strong>{item.title}</strong>
          </label>
        ))}
      </div>
    </Panel>
  );
}

function Sops({ state, setState }) {
  const toggle = (key) => setState((s) => ({ ...s, acked: { ...(s.acked || {}), [key]: !s.acked?.[key] } }));
  return (
    <Panel title="Standard Operating Procedures">
      <div className="intra-table">
        {SOPS.map((sop) => (
          <div className="intra-table-row" key={sop.key}>
            <span>{sop.area}</span>
            <strong>{sop.title}</strong>
            <label className="intra-check inline">
              <input type="checkbox" checked={Boolean(state.acked?.[sop.key])} onChange={() => toggle(sop.key)} />
              <span>Acknowledged</span>
            </label>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function Numbers({ config }) {
  const n = config.numbers || DEFAULT_CONFIG.numbers;
  return (
    <div className="intra-stack">
      <div className="intra-grid four">
        <Tile label="Calls" value={formatNumber(n.calls_today)} />
        <Tile label="Appointments" value={formatNumber(n.appointments_set)} />
        <Tile label="Pending" value={formatNumber(n.contracts_pending)} />
        <Tile label="Closed volume" value={`$${formatNumber(n.closed_volume)}`} />
      </div>
      <Empty title="Production data is empty">Numbers will populate from configured API integrations.</Empty>
    </div>
  );
}

function ConfigUrlForm({ canConfigure, label, value, placeholder, onSave }) {
  const [draft, setDraft] = useState(value || "");
  const [state, setState] = useState("idle");
  useEffect(() => setDraft(value || ""), [value]);
  if (!canConfigure) return null;
  const submit = async (e) => {
    e.preventDefault();
    setState("saving");
    try {
      await onSave(draft);
      setState("saved");
    } catch {
      setState("error");
    }
  };
  return (
    <form className="intra-config-form" onSubmit={submit}>
      <label>
        <span>{label}</span>
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={placeholder} />
      </label>
      <button className="intra-button" type="submit">{state === "saving" ? "Saving" : "Save"}</button>
      {state === "saved" && <small>Saved</small>}
      {state === "error" && <small className="bad">Could not save</small>}
    </form>
  );
}

function Calendar({ config, canConfigure, saveConfig }) {
  const url = config.calendar?.google_calendar_url || "";
  return (
    <div className="intra-stack">
      <Panel title="Google Calendar">
        {url
          ? <iframe className="intra-calendar" title="Team calendar" src={url} />
          : <Empty title="No calendar connected">Add a Google Calendar URL when it is ready.</Empty>}
      </Panel>
      <ConfigUrlForm
        canConfigure={canConfigure}
        label="Google Calendar URL"
        value={url}
        placeholder="https://calendar.google.com/calendar/..."
        onSave={(next) => saveConfig({ calendar: { google_calendar_url: next } })}
      />
    </div>
  );
}

function Marketing({ config, canConfigure, saveConfig }) {
  const request = config.marketing_requests || {};
  return (
    <div className="intra-stack">
      <Panel title="Marketing Requests">
        {request.url
          ? <a className="intra-open-request" href={request.url} target="_blank" rel="noreferrer">{request.label || "Open request form"}</a>
          : <Empty title="No request destination connected">Add a request URL when the workflow is ready.</Empty>}
      </Panel>
      <ConfigUrlForm
        canConfigure={canConfigure}
        label="Request URL"
        value={request.url || ""}
        placeholder="https://..."
        onSave={(next) => saveConfig({ marketing_requests: { url: next, label: request.label || "Marketing requests" } })}
      />
    </div>
  );
}

function Directory() {
  return (
    <Panel title="Directory">
      <Empty title="Directory is empty">Team profiles will come from tenant configuration or a connected roster source.</Empty>
    </Panel>
  );
}

function BrandKit({ config }) {
  const brand = config.brand || DEFAULT_CONFIG.brand;
  return (
    <div className="intra-grid two">
      <Panel title="Brand Mark">
        {brand.mark_url
          ? <img className="intra-brand-preview" src={brand.mark_url} alt="Brand mark" />
          : <Empty title="No mark connected">Use the proxy fonts until the final mark is attached.</Empty>}
      </Panel>
      <Panel title="Type">
        <div className="intra-type-samples">
          <div style={{ fontFamily: "var(--font-intra-display)" }}>TT Drugs proxy</div>
          <div style={{ fontFamily: "var(--font-intra-text)" }}>TT Norms Pro proxy</div>
          <div style={{ fontFamily: "var(--font-intra-ui)" }}>Utility proxy</div>
        </div>
      </Panel>
    </div>
  );
}

function Ask() {
  return (
    <Panel title="Ask">
      <div className="intra-ask">
        <input disabled placeholder="Assistant shell" />
        <button disabled>Ask</button>
      </div>
      <Empty title="Assistant not connected">This ships as a shell until the real backend is wired.</Empty>
    </Panel>
  );
}

export default function IntranetApp() {
  const boot = useBootstrap();
  const ready = boot.status === "ready";
  const day = useMemo(() => todayKey(), []);
  const [wtd, setWtd] = useScopedState("wtd", day, DEFAULT_WTD, ready);
  const [training, setTraining] = useScopedState("training", "global", DEFAULT_PROGRESS, ready);
  const [onboarding, setOnboarding] = useScopedState("onboarding", "global", DEFAULT_PROGRESS, ready);
  const [sops, setSops] = useScopedState("sops", "global", DEFAULT_ACKS, ready);

  if (boot.status === "loading") {
    return <AccessState title="Loading intranet" message="Checking your workspace access." />;
  }
  if (boot.status === "login") {
    return <AccessState title="Sign in required" message="Use your workspace account to open the intranet." action={<a className="intra-button" href="/">Sign in</a>} />;
  }
  if (boot.status === "disabled") {
    return <AccessState title="Intranet unavailable" message="This workspace does not have the intranet module enabled." />;
  }
  if (boot.status === "error") {
    return <AccessState title="Could not load intranet" message={boot.error || "Try again shortly."} action={<button className="intra-button" onClick={boot.refresh}>Retry</button>} />;
  }

  return (
    <Shell me={boot.me}>
      <Routes>
        <Route path="/" element={<Home config={boot.config} wtd={wtd} training={training} onboarding={onboarding} />} />
        <Route path="/tools" element={<Tools config={boot.config} />} />
        <Route path="/wtd" element={<WinTheDay state={wtd} setState={setWtd} config={boot.config} />} />
        <Route path="/training" element={<Training state={training} setState={setTraining} />} />
        <Route path="/onboarding" element={<Onboarding state={onboarding} setState={setOnboarding} />} />
        <Route path="/sops" element={<Sops state={sops} setState={setSops} />} />
        <Route path="/numbers" element={<Numbers config={boot.config} />} />
        <Route path="/calendar" element={<Calendar config={boot.config} canConfigure={boot.canConfigure} saveConfig={boot.saveConfig} />} />
        <Route path="/marketing" element={<Marketing config={boot.config} canConfigure={boot.canConfigure} saveConfig={boot.saveConfig} />} />
        <Route path="/directory" element={<Directory />} />
        <Route path="/brand" element={<BrandKit config={boot.config} />} />
        <Route path="/ask" element={<Ask />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
