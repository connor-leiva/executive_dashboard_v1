import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";

import { API_BASE, getBlob, getJSON, hasToken, logout, patchJSON, postJSON, putJSON, uploadFile } from "../api.js";
import {
  NAV_GROUPS,
  ONBOARDING,
  PRIORITY_ITEMS,
  QUICK_LAUNCH,
  ROLE_OPTIONS,
  TOOL_GROUPS,
  WTD_BLOCKS,
} from "./constants.js";

const DEFAULT_CONFIG = {
  calendar: { google_calendar_url: "" },
  marketing_requests: { url: "", label: "Marketing requests" },
  // Mirrors what the API sends. Not available and delivery pending is the honest default for a
  // workspace nobody has configured, and it is what the offline fallback should show too.
  marketing: { available: false, required_fields: [], assigned_role: null, delivery_pending: true },
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
        tenant_name: "Your Workspace",
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
      document.title = `${user.tenant_name || user.tenant || "Intranet"} Intranet`;
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

function Shell({ me, config, children }) {
  // The workspace's own name, from its console configuration. Never a constant: this string
  // appears in the rail, the tab title and the assistant button, and it belongs to whoever
  // bought the product rather than to the customer it was first built for.
  const workspaceName = config?.workspace?.name || me?.tenant_name || "Intranet";
  const askLabel = `Ask ${workspaceName}`;
  // Two reasons a nav item is not offered, applied together: the workspace has not connected the
  // vendor behind it, or this role's capability level is None. The server filters the content
  // either way -- this stops the rail advertising a page it would then refuse, which reads as the
  // product being broken rather than as access somebody was never given.
  const levels = config?.content?.capabilities || {};
  const visible = (item) =>
    (!VENDOR_NAV[item.id] || connected(config, VENDOR_NAV[item.id]))
    && (!item.capability || levels[item.capability] !== "None");
  const [navOpen, setNavOpen] = useState(false);
  // The workspace's own roles. ROLE_OPTIONS was four real-estate titles compiled in, so a
  // salon or a law firm buying this product got "Buyer Agent" in their role switcher.
  const roleOptions = (config?.content?.roles || []).map((r) => r.name);
  const [roleView, setRoleView] = useState(null);
  // Settle on the first role once the workspace's own roles arrive. Not a default in useState:
  // the config is fetched, so at first render there are no roles to choose from yet.
  useEffect(() => {
    setRoleView((prev) => (prev && roleOptions.includes(prev) ? prev : roleOptions[0] || null));
  }, [roleOptions.join("|")]);
  // Empty, not "buyer consultation". That was the mockup's sample query sitting in the box as a
  // real value, so every user opened the intranet with somebody else's search already typed in --
  // and pressing enter would have run it. The mockup's text belongs in the placeholder.
  const [search, setSearch] = useState("");
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
      <aside className={`ut-rail ${navOpen ? "open" : ""}`} aria-label={`${workspaceName} intranet navigation`}>
        <div className="ut-logo-lockup">
          <div className="ut-logo-text">{workspaceName}</div>
          <div className="ut-logo-kicker">Team Intranet</div>
        </div>
        <nav className="ut-nav">
          {/* The nav STRUCTURE is the product's -- every workspace gets Home, Training,
              SOPs and so on. The assistant's LABEL is the workspace's, because it carries
              their name. Anything below that is content, and comes from their console. */}
          {NAV_GROUPS.map((group) => ({ ...group, items: group.items.filter(visible) }))
            .filter((group) => group.items.length)
            .map((group) => (
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
                  <span>{item.id === "ask" ? askLabel : item.label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        {/* The mockup's rail footer, which had no counterpart here. It is the one place in the
            product that tells somebody what to do when the intranet does not answer their
            question, and on a tool aimed at new agents that is not decoration. Sits below the nav
            on margin-top:auto, so it stays pinned to the bottom of a short rail and scrolls
            naturally on a tall one. */}
        <div className="ut-rail-footer">
          <div className="ut-rail-footer-label">Need A Hand?</div>
          <p>
            {/* `#help` is emphasised text, not an anchor. The mockup links it, but no help
                channel is configured for this workspace and an <a href="#"> that goes nowhere is
                a link that lies about being one. It becomes a real link when there is somewhere
                to point it. */}
            Ask in the <b>#help</b> channel or text ops. Someone always answers.
          </p>
        </div>
      </aside>
      <main className="ut-main">
        <header className="ut-topbar">
          <button className="ut-menu" type="button" onClick={() => setNavOpen(true)} aria-label="Open navigation">
            <span /><span /><span />
          </button>
          <label className="ut-search">
            <span aria-hidden />
            <input value={search} onChange={(e) => setSearch(e.target.value)}
                   placeholder="Search training, SOPs, people, tools…" aria-label="Search" />
          </label>
          <NavLink className="ut-ask-top" to="/ask" title={askLabel}>
            <span aria-hidden />
            {/* Truncated in CSS rather than shortened here. The mockup's "Ask Utah Life" fits
                because that name is short; a product cannot assume every customer's is, and
                guessing at an abbreviation would mangle somebody's name. A configurable
                assistant label is the real fix. */}
            <b>{askLabel}</b>
            <kbd>Ctrl K</kbd>
          </NavLink>
          {/* Hidden entirely when the workspace has no published roles yet. A "Viewing As"
              label with nothing after it reads as broken rather than as unconfigured. */}
          {roleOptions.length > 0 && (
          <div className="ut-role-control" role="group" aria-label="Viewing as">
            <span>Viewing As</span>
            <div>
              {roleOptions.map((role) => (
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
          )}
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
      <div className="ut-access-logo">Intranet</div>
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
  // From the workspace's published courses, not a constant, and keyed on lesson id -- the same
  // key the Training page ticks. Counting a hardcoded list meant this progress bar described one
  // customer's curriculum to every other customer.
  const lessons = ((config.content && config.content.courses) || [])
    .flatMap((c) => (c.lessons || []).map((l) => l.id));
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

      {/* A VENDOR PANEL, NOT A PRODUCT FEATURE. Sunburst is a coaching product one customer
          buys, and it lives inside Sisu -- so it appears only where that workspace has
          actually connected Sisu. Compiled in, it would have put another company's brand on
          every customer's home screen. */}
      {connected(config, "sisu") && <SunburstBanner />}

      <section className="ut-lower-grid">
        <NeedsYouToday config={config} />
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

/* Whether this workspace has a given provider connected. Vendor-specific surfaces ask this
   rather than assuming; "connected" is the console's own status value. */
/* Nav items that belong to a VENDOR rather than to the product. Each appears only where that
   workspace has the relevant integration connected -- Sunburst is a coaching product sold inside
   Sisu, and a permanent nav entry for it would put one customer's vendor in everybody's rail. */
const VENDOR_NAV = { sunburst: "sisu" };

function connected(config, providerKey) {
  return (config?.content?.integrations || {})[providerKey] === "connected";
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

function NeedsYouToday({ config }) {
  const fubConnected = connected(config, "follow_up_boss");
  return (
    <Panel title="Needs You Today"
           kicker={fubConnected ? "Pulled From Follow Up Boss" : "No CRM connected"}
           action={fubConnected ? <NavLink to="/wtd">Open list {"->"}</NavLink> : null}>
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

/* Quick Launch and the Tool Launchpad both render the WORKSPACE'S tiles.
 *
 * These were a compiled-in list -- Follow Up Boss, Sisu, Slack, Sunburst, PLACE, Skool, Canva --
 * which is one customer's actual tool stack. The console has always had a Tool Launchpad screen
 * writing tiles into a tenant-scoped table; nothing read it. Every workspace on the platform
 * would have seen that customer's tools with their own URLs bolted on underneath.
 */
function tilesFrom(config) {
  return config?.content?.tool_groups || [];
}

function ToolCard({ tool }) {
  const body = (
    <>
      <span className="ut-tool-mark">{tool.name.slice(0, 2)}</span>
      <div>
        <strong>{tool.name}</strong>
        <em>{tool.url ? "Open tool" : "No destination set"}</em>
      </div>
    </>
  );
  return tool.url
    ? <a className="ut-quick-card" href={tool.url} target="_blank" rel="noreferrer">{body}</a>
    : <div className="ut-quick-card muted">{body}</div>;
}

function QuickLaunch({ config }) {
  // The first few tiles the workspace configured, in their own order. No separate "quick" list:
  // a second list to curate is a second thing to forget, and the console has one ordering.
  const tools = tilesFrom(config).flatMap((g) => g.tools).slice(0, 6);
  return (
    <Panel title="Quick Launch" action={<NavLink to="/tools">All Tools {"->"}</NavLink>}>
      {tools.length === 0 ? (
        <Empty title="No tools yet">
          Tools added in the admin console under Tool Launchpad will appear here.
        </Empty>
      ) : (
        <div className="ut-quick-grid">
          {tools.map((tool) => <ToolCard key={tool.key} tool={tool} />)}
        </div>
      )}
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
  const groups = tilesFrom(config);
  return (
    <Page title="Tool Launchpad" subtitle="The tools this workspace runs on.">
      {groups.length === 0 ? (
        <Panel title="Tools">
          <Empty title="No tools configured">
            An admin can add tools in the console under Tool Launchpad. They appear here once
            published.
          </Empty>
        </Panel>
      ) : groups.map((group) => (
        <Panel key={group.id} title={group.label}>
          <div className="ut-tool-grid">
            {group.tools.map((tool) => <ToolCard key={tool.key} tool={tool} />)}
          </div>
        </Panel>
      ))}
    </Page>
  );
}

function WinTheDay({ state, setState, config }) {
  // The workspace's own call lists, from the console. `config.links.fub_lists` was an older
  // parallel map keyed by hardcoded list names -- the console never wrote to it.
  const lists = config?.content?.wtd_lists || [];
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
      <Panel title="Call Lists" kicker="Configured in the admin console">
        {!lists.length ? (
          <p className="ut-empty">
            {deniedBy(config, "wtd")
              ? "Your role does not have access to Win the Day."
              : "Call lists appear here once an admin adds them in the console under Win the Day."}
          </p>
        ) : (
        <div className="ut-fub-grid">
          {lists.map((item) => {
            return (
              <div className="ut-fub-card" key={item.id}>
                <strong>{item.name}</strong>
                {item.script_name || item.daily_target ? (
                  <em>
                    {item.script_name}
                    {item.script_name && item.daily_target ? " · " : ""}
                    {item.daily_target ? `${item.daily_target}/day` : ""}
                  </em>
                ) : null}
                {item.url
                  ? <a href={item.url} target="_blank" rel="noreferrer noopener">Open list</a>
                  : <span>No list ID configured</span>}
              </div>
            );
          })}
        </div>
        )}
      </Panel>
    </Page>
  );
}

/* THE WORKSPACE'S OWN COURSES, from the API. This read a compiled-in TRAINING constant -- one
 * customer's four courses with lesson titles and nothing else -- because the member payload only
 * ever carried titles, so there was nothing here to render. Now that it carries source, duration
 * and required, a lesson is something you can open. */
function Training({ state, setState, config }) {
  const courses = (config?.content?.courses) || [];
  const toggle = (key) => setState((s) => ({ ...s, done: { ...(s.done || {}), [key]: !s.done?.[key] } }));

  if (!courses.length) {
    const denied = deniedBy(config, "training_library");
    return (
      <Page title="Training Library" subtitle="Courses this workspace has published.">
        <Panel title={denied ? "Not available to your role" : "Nothing published yet"}>
          <p className="ut-empty">
            {denied
              ? "Your role does not have access to the training library. Ask an admin if that looks wrong."
              : "Courses appear here once an admin publishes them in the console."}
          </p>
        </Panel>
      </Page>
    );
  }

  return (
    <Page title="Training Library" subtitle="Courses this workspace has published.">
      <div className="ut-two-grid">
        {courses.map((course) => (
          <Panel key={course.id} title={course.title}>
            {course.description ? <p className="ut-empty">{course.description}</p> : null}
            <div className="ut-check-list">
              {course.lessons.map((lesson) => (
                <label key={lesson.id} className="ut-check">
                  <input type="checkbox" checked={Boolean(state.done?.[lesson.id])}
                         onChange={() => toggle(lesson.id)} />
                  <span>
                    {lesson.source_ref
                      ? <a href={lesson.source_ref} target="_blank" rel="noreferrer noopener"
                           onClick={(e) => e.stopPropagation()}>{lesson.title}</a>
                      : lesson.title}
                    {lesson.duration_minutes ? <em> · {lesson.duration_minutes} min</em> : null}
                    {lesson.required ? <em> · required</em> : null}
                  </span>
                </label>
              ))}
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

/* "Nothing published yet" and "your role cannot see this" look identical from here -- both are
   an empty list -- and telling somebody their workspace has published nothing when it has
   published plenty sends them to ask an admin the wrong question. The payload reports the level,
   so the page can say which it is. Naming the restriction does reveal that content exists, which
   inside a team's own portal is not a secret worth keeping at the cost of the confusion. */
function deniedBy(config, capability) {
  return (config?.content?.capabilities || {})[capability] === "None";
}

function shortDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "" : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

/* Fetched with the session and handed over as a download rather than linked directly: the bytes
   are proxied so a URL cannot outlive the reader's access to the workspace, which means a plain
   <a href> would 401. */
async function saveSop(sop) {
  const blob = await getBlob(sop.file_url);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = sop.filename || "sop";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/* The workspace's own SOPs. This read a compiled-in SOPS constant -- six of one customer's
 * procedure names -- because the payload carried only a title and a category. The documents had
 * been uploaded and stored the whole time with no member route to serve them. */
function Sops({ config }) {
  const sops = (config?.content?.sops) || [];
  const [error, setError] = useState(null);
  // Acknowledgements the server has confirmed since this page loaded, laid over what the payload
  // arrived with. Avoids refetching the whole workspace config to reflect one tick.
  const [justAcked, setJustAcked] = useState({});
  const [saving, setSaving] = useState(null);

  async function open(sop) {
    setError(null);
    try {
      await saveSop(sop);
    } catch {
      setError("That document could not be opened. It may have been replaced — reload and try again.");
    }
  }

  async function acknowledge(sop) {
    setError(null);
    setSaving(sop.id);
    try {
      const r = await postJSON(`/intranet/sops/${sop.id}/acknowledge`, {});
      setJustAcked((m) => ({ ...m, [sop.id]: r.acknowledged_at }));
    } catch {
      setError("That didn't save. Try again in a moment.");
    } finally {
      setSaving(null);
    }
  }

  if (!sops.length) {
    const denied = deniedBy(config, "sop_library");
    return (
      <Page title="SOPs" subtitle="Standard operating procedures for this workspace.">
        <Panel title={denied ? "Not available to your role" : "Nothing published yet"}>
          <p className="ut-empty">
            {denied
              ? "Your role does not have access to the SOP library. Ask an admin if that looks wrong."
              : "SOPs appear here once an admin publishes them in the console."}
          </p>
        </Panel>
      </Page>
    );
  }

  return (
    <Page title="SOPs" subtitle="Standard operating procedures for this workspace.">
      <Panel title="Standard Operating Procedures">
        {error ? <p className="ut-empty">{error}</p> : null}
        <div className="ut-table">
          {sops.map((sop) => (
            <div className="ut-table-row" key={sop.id}>
              <span>{sop.category || "—"}</span>
              <strong>
                {sop.file_url
                  ? <button type="button" className="ut-linkish" onClick={() => open(sop)}>
                      {sop.title}
                    </button>
                  : sop.title}
                {sop.version ? <em> · {sop.version}</em> : null}
                {sop.owner ? <em> · {sop.owner}</em> : null}
              </strong>
              {/* A BUTTON, not a checkbox. Acknowledging a procedure is an assertion recorded
                  against a specific version -- there is no un-acknowledge -- so it must not be
                  something a stray click can toggle off. Once done it stops being a control and
                  becomes a fact with a date on it. */}
              {(justAcked[sop.id] || sop.acknowledged_at) ? (
                <span className="ut-acked">
                  Acknowledged {shortDate(justAcked[sop.id] || sop.acknowledged_at)}
                </span>
              ) : (
                <button type="button" className="ut-button" disabled={saving === sop.id}
                        onClick={() => acknowledge(sop)}>
                  {saving === sop.id ? "Saving…" : "Acknowledge"}
                </button>
              )}
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

/* Requests.
 *
 * THE EDITOR THAT USED TO LIVE HERE IS GONE. This page carried its own "Request URL" form, so the
 * destination could be set in two places -- here and, once the console screen existed, there --
 * with nothing keeping them in step. The handoff is explicit that tenant-configurable behaviour
 * belongs in the admin console, and one setting with two editors is how they end up disagreeing.
 *
 * A workspace's existing `marketing_requests.url` still renders, so nothing anybody configured
 * has disappeared; it is just no longer edited from inside the product it configures.
 */
const REQUEST_FIELD_LABELS = {
  client: "Client",
  description: "What do you need?",
  due_date: "Needed by",
  listing: "Listing",
  priority: "Priority",
  request_type: "Type of request",
};

/* The submit form.
 *
 * It renders the workspace's REQUIRED fields as required and offers the rest as optional, from
 * the same list the server enforces -- so the form and the rule behind it cannot drift. The
 * server checks them again regardless: a required field validated only here is a suggestion. */
function RequestForm({ required, onDone }) {
  const [form, setForm] = useState({ title: "" });
  const [picked, setPicked] = useState([]);
  const [state, setState] = useState("idle");
  const [error, setError] = useState("");
  const set = (k, v) => setForm((prev) => ({ ...prev, [k]: v }));
  const optional = ["request_type", "listing", "client", "due_date", "description"]
    .filter((f) => !required.includes(f));

  async function submit(event) {
    event.preventDefault();
    setState("saving");
    setError("");
    try {
      // Multipart, because the request and its files are saved in one transaction. A
      // create-then-upload flow whose second step fails leaves a request that reads as complete
      // with the photograph it was about missing.
      const fd = new FormData();
      Object.entries(form).forEach(([k, v]) => {
        if (String(v || "").trim() !== "") fd.append(k, v);
      });
      picked.forEach((file) => fd.append("files", file));
      await uploadFile("/intranet/marketing/requests", fd);
      setForm({ title: "" });
      setPicked([]);
      setState("done");
      onDone();
    } catch (err) {
      setState("idle");
      setError(err?.detail || err?.message || "Could not submit.");
    }
  }

  const field = (key, req) => (
    <label className="ut-field" key={key}>
      <span>{REQUEST_FIELD_LABELS[key]}{req ? " *" : ""}</span>
      {key === "description"
        ? <textarea rows={3} required={req} value={form[key] || ""}
                    onChange={(e) => set(key, e.target.value)} />
        : key === "due_date"
          ? <input type="date" required={req} value={form[key] || ""}
                   onChange={(e) => set(key, e.target.value)} />
          : key === "priority"
            ? (
              <select value={form[key] || "Normal"} onChange={(e) => set(key, e.target.value)}>
                {["Low", "Normal", "High"].map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            )
            : <input required={req} value={form[key] || ""}
                     onChange={(e) => set(key, e.target.value)} />}
    </label>
  );

  return (
    <form className="ut-request-form" onSubmit={submit}>
      <label className="ut-field">
        <span>Title *</span>
        <input required value={form.title}
               onChange={(e) => set("title", e.target.value)}
               placeholder="Listing flyer for 12 Oak St" />
      </label>
      {required.filter((k) => k !== "attachments").map((k) => field(k, true))}
      {optional.map((k) => field(k, false))}
      <label className="ut-field">
        <span>Files{required.includes("attachments") ? " *" : ""}</span>
        <input
          type="file"
          multiple
          accept="image/png,image/jpeg,image/webp,application/pdf"
          onChange={(e) => setPicked([...e.target.files])}
        />
        {/* The accept attribute is a convenience for the picker, not the rule. The server sniffs
            the bytes: a file renamed to .png is still rejected, and an HTML file labelled
            image/png never becomes something the next person downloads. */}
        <em className="ut-hint">PNG, JPEG, WebP or PDF · up to 5 files, 10 MB each</em>
      </label>
      {error && <p className="ut-error">{error}</p>}
      <button className="ut-button primary" type="submit" disabled={state === "saving"}>
        {state === "saving" ? "Sending…" : "Submit request"}
      </button>
    </form>
  );
}

/* Requests.
 *
 * THE EDITOR THAT USED TO LIVE HERE IS GONE. This page carried its own "Request URL" form, so the
 * destination could be set in two places -- here and, once the console screen existed, there --
 * with nothing keeping them in step. Tenant-configurable behaviour belongs in the admin console,
 * and one setting with two editors is how they end up disagreeing.
 *
 * A workspace's existing `marketing_requests.url` still renders, so nothing anybody configured
 * has disappeared; it is just no longer edited from inside the product it configures.
 */
function Marketing({ config, canConfigure }) {
  const legacy = config.marketing_requests || {};
  const marketing = config.marketing || {};
  const [mine, setMine] = useState(null);

  const load = useCallback(() => {
    if (!API_BASE || !marketing.available) return;
    getJSON("/intranet/marketing/requests")
      .then((r) => setMine(r.items || []))
      .catch(() => setMine([]));
  }, [marketing.available]);
  useEffect(() => { load(); }, [load]);

  return (
    <Page title="Requests" subtitle="Marketing requests for listings, events and collateral.">
      <Panel title="Requests and Turnaround">
        {marketing.available ? (
          <>
            <p className="ut-note">
              Requests are open{marketing.assigned_role ? ` and picked up by ${marketing.assigned_role}` : ""}.
              {marketing.delivery_pending
                /* Said out loud. The request IS saved and the team can see it in the console;
                   what does not happen yet is automatic delivery to the destination. Promising
                   otherwise is the one thing this screen must not do. */
                ? " Your request is recorded for the team; automatic routing to their channel is not switched on yet."
                : ""}
            </p>
            <RequestForm required={marketing.required_fields || []} onDone={load} />
          </>
        ) : legacy.url ? (
          <a className="ut-open-request" href={legacy.url} target="_blank" rel="noreferrer">
            {legacy.label || "Open request form"}
          </a>
        ) : (
          <Empty title="Requests are not set up">
            No destination has been configured for this workspace yet.
            {canConfigure && " Set one in the admin console under Marketing Requests."}
          </Empty>
        )}
      </Panel>

      {marketing.available && (
        <Panel title="My requests">
          {mine === null ? <Empty title="Loading…">Fetching your requests.</Empty>
            : mine.length === 0
              ? <Empty title="Nothing submitted yet">Requests you file will be listed here.</Empty>
              : (
                <ul className="ut-request-list">
                  {mine.map((r) => (
                    <li key={r.id}>
                      <div>
                        <strong>{r.title}</strong>
                        {r.listing && <em>{r.listing}</em>}
                        {r.attachment_count > 0 && (
                          <em>{r.attachment_count} file{r.attachment_count === 1 ? "" : "s"}</em>
                        )}
                      </div>
                      <span className="ut-request-status">{r.status}</span>
                    </li>
                  ))}
                </ul>
              )}
        </Panel>
      )}
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
    <Page title="Brand Kit" subtitle="Marks, colours and type for this workspace.">
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

function Ask({ config, me }) {
  // The assistant carries the workspace's name, so this needs the same source the rail uses.
  // It previously said "Ask Utah Life" from a constant; taking askLabel from an enclosing scope
  // would have been a runtime ReferenceError, which a build does not catch.
  const askLabel = `Ask ${config?.workspace?.name || me?.tenant_name || "us"}`;
  return (
    <Page title={askLabel} subtitle="Answers drawn from this workspace’s own documents.">
      <Panel title={askLabel}>
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
    <Shell me={boot.me} config={boot.config}>
      <Routes>
        <Route path="/" element={<Home config={boot.config} wtd={wtd} training={training} onboarding={onboarding} me={boot.me} />} />
        <Route path="/tools" element={<Tools config={boot.config} />} />
        <Route path="/wtd" element={<WinTheDay state={wtd} setState={setWtd} config={boot.config} />} />
        <Route path="/training" element={<Training state={training} setState={setTraining} config={boot.config} />} />
        <Route path="/onboarding" element={<Onboarding state={onboarding} setState={setOnboarding} />} />
        <Route path="/sops" element={<Sops config={boot.config} />} />
        <Route path="/numbers" element={<Numbers config={boot.config} />} />
        <Route path="/calendar" element={<Calendar config={boot.config} canConfigure={boot.canConfigure} saveConfig={boot.saveConfig} />} />
        <Route path="/marketing" element={<Marketing config={boot.config} canConfigure={boot.canConfigure} />} />
        <Route path="/directory" element={<Directory />} />
        <Route path="/brand" element={<BrandKit config={boot.config} />} />
        <Route path="/ask" element={<Ask config={boot.config} me={boot.me} />} />
        <Route path="/sunburst" element={<SunburstPage />} />
        <Route path="/phone" element={<PlaceholderPage title="On The Phone" subtitle="Team phone activity shell." />} />
        <Route path="/listing" element={<PlaceholderPage title="Listing Marketing" subtitle="Listing marketing content remains tenant configurable." />} />
        <Route path="/partners" element={<PlaceholderPage title="JV Partners" subtitle="Partner links remain tenant configurable." />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
