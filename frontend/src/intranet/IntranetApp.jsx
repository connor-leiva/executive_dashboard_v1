import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";

import { API_BASE, getJSON, hasToken, logout, patchJSON, putJSON } from "../api.js";
import {
  FUB_LISTS,
  NAV_GROUPS,
  ONBOARDING,
  PRIORITY_ITEMS,
  QUICK_LAUNCH,
  ROLE_OPTIONS,
  SOPS,
  TOOL_GROUPS,
  TRAINING,
  WTD_BLOCKS,
} from "./constants.js";

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
    appointments_held: 0,
    contracts_pending: 0,
    closed_units: 0,
    closed_volume: 0,
    gci_ytd: 0,
    annual_goal_units: 0,
    team_units_ytd: 0,
    team_volume_ytd: 0,
    team_agents: 0,
  },
};

const DEFAULT_WTD = { checked: {}, tallies: { calls: 0, conversations: 0, appointments: 0, notes: 0 } };
const DEFAULT_PROGRESS = { done: {} };
const DEFAULT_ACKS = { acked: {} };
/* THE DATE LINE AND THE GREETING COME FROM THE VIEWER'S CLOCK.
 *
 * Both were the mockup's frozen moment: a MOCK_DATE constant holding the mockup's own weekday
 * and date, and a hardcoded "Good Morning". A mockup is entitled to one instant. A product
 * somebody opens every morning is not, and a screen naming last August's Monday on a Thursday in
 * September is the same kind of untruth as a fake metric -- it just wears a date instead of a
 * number. check-no-mock-data.sh now fails on a frozen date literal, which is why this note
 * describes the old value rather than quoting it.
 *
 * The VIEWER's clock rather than the server's or the workspace's timezone, deliberately, and for
 * the reason Win the Day already resets on the user's local day: the person reading "Good
 * Morning" is the one whose morning it is.
 */
function localDateLine(now) {
  // Locale-formatted so it reads correctly outside en-US; the CSS does the uppercasing.
  return now.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
}

function greetingFor(now) {
  const h = now.getHours();
  if (h < 12) return "Good Morning";
  if (h < 17) return "Good Afternoon";
  return "Good Evening";
}

/* A tab left open overnight would otherwise still say Thursday, and still say Good Evening at
   nine the next morning -- this product is one people leave open all day. The tick is a minute,
   and it only re-renders when one of the two strings actually changes. */
function useLocalNow() {
  const [stamp, setStamp] = useState(() => {
    const now = new Date();
    return { date: localDateLine(now), greeting: greetingFor(now) };
  });
  useEffect(() => {
    const id = setInterval(() => {
      const now = new Date();
      const next = { date: localDateLine(now), greeting: greetingFor(now) };
      setStamp((prev) => (prev.date === next.date && prev.greeting === next.greeting ? prev : next));
    }, 60000);
    return () => clearInterval(id);
  }, []);
  return stamp;
}

const ALL_NAV = NAV_GROUPS.flatMap((group) => group.items);

function todayKey() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function timezone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "local";
}

function isPlainObject(value) {
  return value && typeof value === "object" && !Array.isArray(value);
}

function mergeDeep(base, incoming) {
  if (!isPlainObject(base)) return incoming ?? base;
  const source = isPlainObject(incoming) ? incoming : {};
  const out = { ...base, ...source };
  for (const key of Object.keys(base)) {
    if (isPlainObject(base[key])) out[key] = mergeDeep(base[key], source[key]);
  }
  return out;
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString("en-US");
}

function formatMoney(value) {
  const n = Number(value || 0);
  if (Math.abs(n) >= 1000000) return `$${(n / 1000000).toFixed(1)}M`;
  if (Math.abs(n) >= 1000) return `$${(n / 1000).toFixed(1)}k`;
  return `$${formatNumber(n)}`;
}

function displayName(me) {
  const name = (me?.name || "").trim();
  return name && name !== "Preview" ? name : "Jordan Hale";
}

function firstName(me) {
  return displayName(me).split(/\s+/)[0] || "Jordan";
}

function initials(name) {
  const parts = (name || "Jordan Hale").trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] || "J") + (parts[1]?.[0] || "H")).toUpperCase();
}

function activeIdForPath(pathname) {
  return pathname.split("/").filter(Boolean)[0] || "home";
}

function useBootstrap() {
  const [status, setStatus] = useState("loading");
  const [me, setMe] = useState(null);
  const [config, setConfig] = useState(DEFAULT_CONFIG);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (!API_BASE) {
      setMe({
        name: "Jordan Hale",
        email: "",
        role: "admin",
        tenant_name: "Utah Life",
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
      document.title = `${user.tenant_name || user.tenant || "Utah Life"} Intranet`;
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

  return {
    status,
    me,
    config,
    error,
    canConfigure: me?.role === "owner" || me?.role === "admin",
    refresh,
    saveConfig,
  };
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
  const [navOpen, setNavOpen] = useState(false);
  const [roleView, setRoleView] = useState(ROLE_OPTIONS[0]);
  const [search, setSearch] = useState("buyer consultation");
  const location = useLocation();
  const active = activeIdForPath(location.pathname);
  const current = ALL_NAV.find((item) => item.id === active) || ALL_NAV[0];
  const name = displayName(me);

  const signOut = () => {
    logout();
    window.location.href = "/";
  };

  return (
    <div className="ut-shell">
      {navOpen && <button className="ut-scrim" aria-label="Close navigation" onClick={() => setNavOpen(false)} />}
      <aside className={`ut-rail ${navOpen ? "open" : ""}`} aria-label="Utah Life intranet navigation">
        <div className="ut-logo-lockup">
          <div className="ut-logo-text">UTAH LIFE</div>
          <div className="ut-logo-powered">POWERED BY PLACE | exp</div>
          <div className="ut-logo-kicker">Team Intranet</div>
        </div>
        <nav className="ut-nav">
          {NAV_GROUPS.map((group) => (
            <div className="ut-nav-group" key={group.label}>
              <div className="ut-nav-label">{group.label}</div>
              {group.items.map((item) => (
                <NavLink
                  key={item.id}
                  to={item.id === "home" ? "/" : `/${item.id}`}
                  end={item.id === "home"}
                  onClick={() => setNavOpen(false)}
                  className={({ isActive }) => `ut-nav-item ${isActive || active === item.id ? "active" : ""}`}
                >
                  <span>{item.label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
      </aside>
      <main className="ut-main">
        <header className="ut-topbar">
          <button className="ut-menu" type="button" onClick={() => setNavOpen(true)} aria-label="Open navigation">
            <span /><span /><span />
          </button>
          <label className="ut-search">
            <span aria-hidden />
            <input value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search" />
          </label>
          <NavLink className="ut-ask-top" to="/ask">
            <span aria-hidden />
            Ask Utah Life
            <kbd>Ctrl K</kbd>
          </NavLink>
          <div className="ut-role-control" role="group" aria-label="Viewing as">
            <span>Viewing As</span>
            <div>
              {ROLE_OPTIONS.map((role) => (
                <button
                  key={role}
                  type="button"
                  className={role === roleView ? "active" : ""}
                  onClick={() => setRoleView(role)}
                >
                  {role}
                </button>
              ))}
            </div>
          </div>
          <button className="ut-profile" type="button" onClick={signOut} title="Sign out">
            <span>{initials(name)}</span>
            <strong>{name}</strong>
            <em>{roleView}</em>
          </button>
        </header>
        <div className="ut-page-frame" data-page={current?.id || "home"}>
          {children}
        </div>
        <NavLink className="ut-floating-ask" to="/ask">
          <span aria-hidden />
          Ask about anything
        </NavLink>
      </main>
    </div>
  );
}

function AccessState({ title, message, action }) {
  return (
    <div className="ut-access">
      <div className="ut-access-logo">UTAH LIFE</div>
      <h1>{title}</h1>
      <p>{message}</p>
      {action}
    </div>
  );
}

function Panel({ title, kicker, action, children, className = "" }) {
  return (
    <section className={`ut-panel ${className}`}>
      <div className="ut-panel-head">
        <div>
          <h2>{title}</h2>
          {kicker && <span>{kicker}</span>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function Empty({ title, children }) {
  return (
    <div className="ut-empty">
      <strong>{title}</strong>
      {children && <p>{children}</p>}
    </div>
  );
}

function SourcePill({ children = "SISU" }) {
  return <span className="ut-source-pill">{children}</span>;
}

function StatCard({ label, value, sub, note, source = "SISU" }) {
  return (
    <article className="ut-stat-card">
      <div className="ut-stat-top">
        <span>{label}</span>
        <SourcePill>{source}</SourcePill>
      </div>
      <strong>{value}</strong>
      {sub && <p>{sub}</p>}
      <div className="ut-stat-line" />
      <small>{note}</small>
    </article>
  );
}

function Meter({ value, total, className = "" }) {
  const pct = total ? Math.max(0, Math.min(100, Math.round((Number(value || 0) / total) * 100))) : 0;
  return (
    <div className={`ut-meter ${className}`}>
      <span style={{ width: `${pct}%` }} />
    </div>
  );
}

function Home({ config, wtd, training, onboarding, me }) {
  const now = useLocalNow();
  const numbers = config.numbers || DEFAULT_CONFIG.numbers;
  const totalTasks = WTD_BLOCKS.flatMap((b) => b.items).length;
  const doneToday = Object.values(wtd.checked || {}).filter(Boolean).length;
  const lessons = TRAINING.flatMap((c) => c.lessons.map((l) => `${c.key}:${l}`));
  const lessonsDone = lessons.filter((k) => training.done?.[k]).length;
  const onboardDone = ONBOARDING.filter((item) => onboarding.done?.[item.key]).length;

  return (
    <div className="ut-home">
      <section className="ut-hero">
        <div>
          <div className="ut-date">{now.date}</div>
          <h1>{now.greeting}, {firstName(me)}.</h1>
          <p>Your priority queue and production story will populate as tenant sources are configured.</p>
        </div>
        <div className="ut-hero-actions">
          <NavLink className="ut-button light" to="/tools">Open My Tools</NavLink>
          <NavLink className="ut-button primary" to="/numbers">My Numbers</NavLink>
        </div>
      </section>

      <section className="ut-stat-grid">
        <StatCard
          label="Units Closed YTD"
          value={formatNumber(numbers.closed_units)}
          sub={`${formatMoney(numbers.closed_volume)} in volume`}
          note="API integrations pending"
        />
        <StatCard
          label="Pending"
          value={formatNumber(numbers.contracts_pending)}
          sub={`${formatMoney(0)} in volume`}
          note="Tenant source not connected"
        />
        <StatCard
          label="Appointments Held"
          value={formatNumber(numbers.appointments_held || numbers.appointments_set)}
          sub="Month to date"
          note="Goal source pending"
        />
        <StatCard
          label="GCI YTD"
          value={formatMoney(numbers.gci_ytd)}
          sub="$0 net to you"
          note="0% of your annual goal"
        />
      </section>

      <GoalSnapshot numbers={numbers} />

      <SunburstBanner />

      <section className="ut-lower-grid">
        <NeedsYouToday />
        <QuickLaunch config={config} />
      </section>

      <section className="ut-lower-grid">
        <ProgressPanel label="Training Library" done={lessonsDone} total={lessons.length} />
        <ProgressPanel label="Your First 30 Days" done={onboardDone} total={ONBOARDING.length} secondary={`${doneToday}/${totalTasks} Win the Day`} />
      </section>
    </div>
  );
}

function GoalSnapshot({ numbers }) {
  const closed = Number(numbers.closed_units || 0);
  const pending = Number(numbers.contracts_pending || 0);
  const goal = Number(numbers.annual_goal_units || 0);
  const remaining = Math.max(0, goal - closed - pending);

  return (
    <section className="ut-goal-card">
      <div className="ut-goal-main">
        <div className="ut-goal-head">
          <strong>2026 goal &middot; {formatNumber(goal)} units</strong>
          <span>{formatNumber(closed)} closed &middot; {formatNumber(pending)} pending &middot; {formatNumber(remaining)} to go</span>
        </div>
        <div className="ut-goal-meter" aria-label="Goal progress">
          <span className="closed" style={{ width: goal ? `${Math.min(100, (closed / goal) * 100)}%` : "0%" }} />
          <span className="pending" style={{ width: goal ? `${Math.min(100, (pending / goal) * 100)}%` : "0%" }} />
        </div>
        <div className="ut-goal-legend">
          <span><i className="closed" />Closed</span>
          <span><i className="pending" />Pending</span>
        </div>
      </div>
      <div className="ut-team-ytd">
        <span>Team, Year To Date</span>
        <div>
          <strong>{formatNumber(numbers.team_units_ytd)}</strong>
          <em>units</em>
        </div>
        <div>
          <strong>{formatMoney(numbers.team_volume_ytd)}</strong>
          <em>volume</em>
        </div>
        <div>
          <strong>{formatNumber(numbers.team_agents)}</strong>
          <em>agents</em>
        </div>
      </div>
    </section>
  );
}

function SunburstBanner() {
  const prompts = [
    "Walk me through last week",
    "Build this week's plan",
    "Am I on pace for my goal?",
  ];
  return (
    <section className="ut-sunburst">
      <div className="ut-sunburst-copy">
        <div className="ut-sunburst-brand"><span />Sunburst</div>
        <div className="ut-sunburst-kicker">Your AI business partner, inside SISU</div>
        <h2>Your Weekly Check-in is Ready.</h2>
        <p>Last week: 0 appointments set, 0 held, 0 under contract. Configure your sources and Sunburst will walk you through what worked, what slipped, and what this week needs to look like.</p>
        <div className="ut-sunburst-actions">
          <NavLink className="ut-button inverse" to="/sunburst">Start My Check-in</NavLink>
          <NavLink className="ut-button outline-dark" to="/ask">All Prompts</NavLink>
        </div>
      </div>
      <div className="ut-sunburst-prompts">
        {prompts.map((prompt) => (
          <NavLink key={prompt} to="/sunburst">
            {prompt}
            <span>{"->"}</span>
          </NavLink>
        ))}
      </div>
    </section>
  );
}

function NeedsYouToday() {
  return (
    <Panel title="Needs You Today" kicker="Pulled From Follow Up Boss" action={<NavLink to="/wtd">Open FUB {"->"}</NavLink>}>
      <div className="ut-priority-list">
        {PRIORITY_ITEMS.map((item) => (
          <div className="ut-priority-row" key={item.title}>
            <div>
              <strong>{item.title}</strong>
              <span>{item.source}</span>
            </div>
            <em>{item.note}</em>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function QuickLaunch({ config }) {
  const links = config.links?.tools || {};
  return (
    <Panel title="Quick Launch" action={<NavLink to="/tools">All Tools {"->"}</NavLink>}>
      <div className="ut-quick-grid">
        {QUICK_LAUNCH.map((tool) => {
          const url = links[tool.key] || "";
          const body = (
            <>
              <span className={`ut-tool-mark mark-${tool.key}`}>{tool.name.slice(0, 2)}</span>
              <div>
                <strong>{tool.name}</strong>
                <em>{url ? "Open tool" : tool.note || "Configure URL"}</em>
              </div>
            </>
          );
          return url ? (
            <a className="ut-quick-card" href={url} target="_blank" rel="noreferrer" key={tool.key}>{body}</a>
          ) : (
            <div className="ut-quick-card muted" key={tool.key}>{body}</div>
          );
        })}
      </div>
    </Panel>
  );
}

function ProgressPanel({ label, done, total, secondary }) {
  return (
    <Panel title={label} kicker={secondary || "Progress"}>
      <div className="ut-progress-panel">
        <div>
          <strong>{done}/{total}</strong>
          <span>Complete</span>
        </div>
        <Meter value={done} total={total} />
      </div>
    </Panel>
  );
}

function Tools({ config }) {
  const links = config.links?.tools || {};
  return (
    <Page title="Tool Launchpad" subtitle="Universal tools stay visible; tenant-specific destinations remain configurable.">
      {TOOL_GROUPS.map((group) => (
        <Panel key={group.id} title={group.label}>
          <div className="ut-tool-grid">
            {group.tools.map((tool) => {
              const url = links[tool.key] || "";
              return (
                <div className="ut-tool-card" key={tool.key}>
                  <div>
                    <span className={`ut-tool-mark mark-${tool.key}`}>{tool.name.slice(0, 2)}</span>
                    <strong>{tool.name}</strong>
                    <p>{tool.note || "Tenant configurable"}</p>
                  </div>
                  {url
                    ? <a className="ut-button primary small" href={url} target="_blank" rel="noreferrer">Open</a>
                    : <span className="ut-status-pill">Configure URL</span>}
                </div>
              );
            })}
          </div>
        </Panel>
      ))}
    </Page>
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
    <Page title="Win the Day" subtitle="Daily checklist state persists per user and resets by local date.">
      <section className="ut-wtd-hero">
        <div>
          <span>Local Day Reset</span>
          <strong>{done}/{total} complete</strong>
        </div>
        <Meter value={done} total={total} />
      </section>
      <div className="ut-two-grid">
        {blocks.map((block) => (
          <Panel key={block.id} title={block.title}>
            <div className="ut-check-list">
              {block.items.map((item) => {
                const key = `${block.id}:${item}`;
                return (
                  <label key={key} className="ut-check">
                    <input type="checkbox" checked={Boolean(state.checked?.[key])} onChange={() => toggle(key)} />
                    <span>{item}</span>
                  </label>
                );
              })}
            </div>
          </Panel>
        ))}
      </div>
      <Panel title="Daily Tallies" kicker="Manual until source integrations are configured">
        <div className="ut-tally-grid">
          {Object.keys(DEFAULT_WTD.tallies).map((key) => (
            <label key={key}>
              <span>{key.replace("_", " ")}</span>
              <input type="number" min="0" value={state.tallies?.[key] ?? 0} onChange={(e) => setTally(key, e.target.value)} />
            </label>
          ))}
        </div>
      </Panel>
      <Panel title="Follow Up Boss Lists" kicker="Tenant configured">
        <div className="ut-fub-grid">
          {FUB_LISTS.map((item) => {
            const url = links[item.key] || "";
            return (
              <div className="ut-fub-card" key={item.key}>
                <strong>{item.name}</strong>
                {url
                  ? <a href={url} target="_blank" rel="noreferrer">Open list</a>
                  : <span>No URL configured</span>}
              </div>
            );
          })}
        </div>
      </Panel>
    </Page>
  );
}

function Training({ state, setState }) {
  const toggle = (key) => setState((s) => ({ ...s, done: { ...(s.done || {}), [key]: !s.done?.[key] } }));
  return (
    <Page title="Training Library" subtitle="Proxy course shell using the Utah Life navigation and card language.">
      <div className="ut-two-grid">
        {TRAINING.map((course) => (
          <Panel key={course.key} title={course.title}>
            <div className="ut-check-list">
              {course.lessons.map((lesson) => {
                const key = `${course.key}:${lesson}`;
                return (
                  <label key={key} className="ut-check">
                    <input type="checkbox" checked={Boolean(state.done?.[key])} onChange={() => toggle(key)} />
                    <span>{lesson}</span>
                  </label>
                );
              })}
            </div>
          </Panel>
        ))}
      </div>
    </Page>
  );
}

function Onboarding({ state, setState }) {
  const toggle = (key) => setState((s) => ({ ...s, done: { ...(s.done || {}), [key]: !s.done?.[key] } }));
  return (
    <Page title="Your First 30 Days" subtitle="Onboarding shell with per-user saved progress.">
      <Panel title="First 30 Days">
        <div className="ut-steps">
          {ONBOARDING.map((item, idx) => (
            <label key={item.key} className={`ut-step ${state.done?.[item.key] ? "done" : ""}`}>
              <input type="checkbox" checked={Boolean(state.done?.[item.key])} onChange={() => toggle(item.key)} />
              <span>{idx + 1}</span>
              <strong>{item.title}</strong>
            </label>
          ))}
        </div>
      </Panel>
    </Page>
  );
}

function Sops({ state, setState }) {
  const toggle = (key) => setState((s) => ({ ...s, acked: { ...(s.acked || {}), [key]: !s.acked?.[key] } }));
  return (
    <Page title="SOPs" subtitle="Standard operating procedures shell.">
      <Panel title="Standard Operating Procedures">
        <div className="ut-table">
          {SOPS.map((sop) => (
            <div className="ut-table-row" key={sop.key}>
              <span>{sop.area}</span>
              <strong>{sop.title}</strong>
              <label className="ut-check inline">
                <input type="checkbox" checked={Boolean(state.acked?.[sop.key])} onChange={() => toggle(sop.key)} />
                <span>Acknowledged</span>
              </label>
            </div>
          ))}
        </div>
      </Panel>
    </Page>
  );
}

function Numbers({ config }) {
  const n = config.numbers || DEFAULT_CONFIG.numbers;
  return (
    <Page title="My Numbers" subtitle="Production numbers remain zero until tenant API integrations are configured.">
      <section className="ut-stat-grid">
        <StatCard label="Units Closed YTD" value={formatNumber(n.closed_units)} sub={`${formatMoney(n.closed_volume)} in volume`} note="Source pending" />
        <StatCard label="Pending" value={formatNumber(n.contracts_pending)} sub="$0 in volume" note="Source pending" />
        <StatCard label="Appointments Held" value={formatNumber(n.appointments_held || n.appointments_set)} sub="Month to date" note="Source pending" />
        <StatCard label="GCI YTD" value={formatMoney(n.gci_ytd)} sub="$0 net to you" note="Source pending" />
      </section>
      <GoalSnapshot numbers={n} />
    </Page>
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
    <form className="ut-config-form" onSubmit={submit}>
      <label>
        <span>{label}</span>
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={placeholder} />
      </label>
      <button className="ut-button primary" type="submit">{state === "saving" ? "Saving" : "Save"}</button>
      {state === "saved" && <small>Saved</small>}
      {state === "error" && <small className="bad">Could not save</small>}
    </form>
  );
}

function Calendar({ config, canConfigure, saveConfig }) {
  const url = config.calendar?.google_calendar_url || "";
  return (
    <Page title="Team Calendar" subtitle="Google Calendar remains empty until an admin supplies the tenant calendar URL.">
      <Panel title="Google Calendar">
        {url
          ? <iframe className="ut-calendar" title="Team calendar" src={url} />
          : <Empty title="Calendar not connected">Add a Google Calendar URL when the team calendar is ready.</Empty>}
      </Panel>
      <ConfigUrlForm
        canConfigure={canConfigure}
        label="Google Calendar URL"
        value={url}
        placeholder="https://calendar.google.com/calendar/..."
        onSave={(next) => saveConfig({ calendar: { google_calendar_url: next } })}
      />
    </Page>
  );
}

function Marketing({ config, canConfigure, saveConfig }) {
  const request = config.marketing_requests || {};
  return (
    <Page title="Requests" subtitle="Marketing request destination remains tenant configurable.">
      <Panel title="Requests and Turnaround">
        {request.url
          ? <a className="ut-open-request" href={request.url} target="_blank" rel="noreferrer">{request.label || "Open request form"}</a>
          : <Empty title="Request destination not connected">Add a request URL when the workflow is ready.</Empty>}
      </Panel>
      <ConfigUrlForm
        canConfigure={canConfigure}
        label="Request URL"
        value={request.url || ""}
        placeholder="https://..."
        onSave={(next) => saveConfig({ marketing_requests: { url: next, label: request.label || "Marketing requests" } })}
      />
    </Page>
  );
}

function Directory() {
  return (
    <Page title="Who's Who" subtitle="Team roster shell.">
      <Panel title="Directory">
        <Empty title="Directory is empty">Team profiles will come from tenant configuration or a connected roster source.</Empty>
      </Panel>
    </Page>
  );
}

function BrandKit({ config }) {
  const brand = config.brand || DEFAULT_CONFIG.brand;
  return (
    <Page title="Brand Kit" subtitle="Close proxy fonts are active until the final TT font files and Utah Life mark are attached.">
      <div className="ut-two-grid">
        <Panel title="Brand Mark">
          {brand.mark_url
            ? <img className="ut-brand-preview" src={brand.mark_url} alt="Brand mark" />
            : <Empty title="No mark connected">The logo lockup is a UI placeholder until the final mark is available.</Empty>}
        </Panel>
        <Panel title="Type">
          <div className="ut-type-samples">
            <div style={{ fontFamily: "var(--font-display)" }}>TT Drugs proxy</div>
            <div style={{ fontFamily: "var(--font-text)" }}>TT Norms Pro proxy</div>
            <div style={{ fontFamily: "var(--font-ui)" }}>Utility proxy</div>
          </div>
        </Panel>
      </div>
    </Page>
  );
}

function Ask() {
  return (
    <Page title="Ask Utah Life" subtitle="Assistant surface ships as a shell until the real service is connected.">
      <Panel title="Ask Utah Life">
        <div className="ut-ask-shell">
          <input disabled placeholder="Ask about anything" />
          <button disabled>Ask</button>
        </div>
        <Empty title="Assistant not connected">This surface is ready for the future Ask implementation.</Empty>
      </Panel>
    </Page>
  );
}

function SunburstPage() {
  return (
    <Page title="Sunburst Coaching" subtitle="Weekly coaching shell in the mockup's dark panel treatment.">
      <SunburstBanner />
    </Page>
  );
}

function PlaceholderPage({ title, subtitle }) {
  return (
    <Page title={title} subtitle={subtitle}>
      <Panel title={title}>
        <Empty title="Configuration pending">This section is kept as a placeholder until tenant content is provided.</Empty>
      </Panel>
    </Page>
  );
}

function Page({ title, subtitle, children }) {
  const now = useLocalNow();
  return (
    <div className="ut-page">
      <section className="ut-page-title">
        <div>
          <div className="ut-date">{now.date}</div>
          <h1>{title}</h1>
          {subtitle && <p>{subtitle}</p>}
        </div>
      </section>
      <div className="ut-page-stack">{children}</div>
    </div>
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
    return <AccessState title="Sign in required" message="Use your workspace account to open the intranet." action={<a className="ut-button primary" href="/">Sign in</a>} />;
  }
  if (boot.status === "disabled") {
    return <AccessState title="Intranet unavailable" message="This workspace does not have the intranet module enabled." />;
  }
  if (boot.status === "error") {
    return <AccessState title="Could not load intranet" message={boot.error || "Try again shortly."} action={<button className="ut-button primary" onClick={boot.refresh}>Retry</button>} />;
  }

  return (
    <Shell me={boot.me}>
      <Routes>
        <Route path="/" element={<Home config={boot.config} wtd={wtd} training={training} onboarding={onboarding} me={boot.me} />} />
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
        <Route path="/sunburst" element={<SunburstPage />} />
        <Route path="/phone" element={<PlaceholderPage title="On The Phone" subtitle="Team phone activity shell." />} />
        <Route path="/listing" element={<PlaceholderPage title="Listing Marketing" subtitle="Listing marketing content remains tenant configurable." />} />
        <Route path="/partners" element={<PlaceholderPage title="JV Partners" subtitle="Partner links remain tenant configurable." />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
