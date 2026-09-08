import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useParams } from "react-router-dom";

import { API_BASE, fileUrl, getBlob, getJSON, hasToken, logout, patchJSON, postJSON, putJSON, uploadFile } from "../api.js";
import { applyPortalPalette } from "./palette.js";
import { search as search_ } from "./search.js";
import {
  NAV_GROUPS,
  ONBOARDING,
  PRIORITY_ITEMS,
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

/* A REAL PERSON, OR NOTHING INVENTED. This fell back to "Jordan Hale" -- a name belonging to
   nobody -- so an account whose name was never filled in was greeted, labelled and initialled as
   somebody who does not exist. The email's local part is the honest next-best thing: it is
   theirs, and they recognise it. */
function displayName(me) {
  const name = (me?.name || "").trim();
  if (name && name !== "Preview") return name;
  const email = (me?.email || "").trim();
  return email ? email.split("@")[0] : "";
}

function firstName(me) {
  // Empty rather than a stand-in. "Good morning." is a fine greeting; "Good morning, Jordan."
  // to somebody who is not Jordan is not.
  return displayName(me).split(/\s+/)[0] || "";
}

function initials(name) {
  const parts = (name || "").trim().split(/\s+/).filter(Boolean);
  // One initial is correct for a one-word name, and an empty avatar is better than somebody
  // else's monogram. "JH" was Jordan Hale's.
  return (parts.slice(0, 2).map((p) => p[0]).join("") || "").toUpperCase();
}

/* The library's own sentence. Generic on purpose -- this ships to every workspace, so it says
   something true of any team's training rather than anything about one of them. */
const TRAINING_BLURB =
  "Everything we teach, in the order we teach it. Start where you are, not where you think you should be.";

function activeIdForPath(pathname) {
  const parts = pathname.split("/").filter(Boolean);
  // An authored page is /p/<key>, and its nav item is identified the same way -- the first
  // segment alone would be "p" for every one of them and highlight nothing.
  if (parts[0] === "p" && parts[1]) return `p/${parts[1]}`;
  return parts[0] || "home";
}

function useBootstrap() {
  const [status, setStatus] = useState("loading");
  const [me, setMe] = useState(null);
  const [config, setConfig] = useState(DEFAULT_CONFIG);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (!API_BASE) {
      setMe({
        // Offline preview only, and named as what it is rather than as a person.
        name: "Preview User",
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
      // Applied here rather than in a component so it happens once, at the same moment as the
      // rest of the workspace's identity. A palette that arrives later repaints the whole shell
      // in front of the reader.
      applyPortalPalette(intranet.config?.workspace?.palette);
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
    applyPortalPalette(next.config?.workspace?.palette);
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
  const ws = config?.workspace || {};
  // fileUrl prefixes the API base; the payload sends a path so there is one place that knows
  // where the API lives. Empty string rather than undefined so the fallback below is a clean
  // boolean rather than "undefined" reaching an <img src>.
  const logoUrl = fileUrl(ws.logo_light_url || ws.logo_mark_url || "") || "";
  // Two reasons a nav item is not offered, applied together: the workspace has not connected the
  // vendor behind it, or this role's capability level is None. The server filters the content
  // either way -- this stops the rail advertising a page it would then refuse, which reads as the
  // product being broken rather than as access somebody was never given.
  /* THE RAIL IS PART BUILT-IN, PART THE WORKSPACE'S. NAV_GROUPS is the product's own structure;
     authored pages join the group each one names, so a workspace can add "JV Partners" under
     Partners without us shipping a screen for it. A group nobody built in -- a name a workspace
     invented -- becomes a group of its own rather than being dropped, because refusing to show
     a page because its heading is unfamiliar is the same failure as hardcoding the headings. */
  const authored = config?.content?.pages || [];
  const navGroups = useMemo(() => {
    const groups = NAV_GROUPS.map((g) => ({ ...g, items: [...g.items] }));
    const byLabel = new Map(groups.map((g) => [g.label, g]));
    [...authored].sort((a, b) => (a.sort ?? 0) - (b.sort ?? 0)).forEach((page) => {
      const item = { id: `p/${page.key}`, label: page.title };
      const label = page.nav_group || "Workspace";
      const group = byLabel.get(label);
      if (group) {
        group.items.push(item);
      } else {
        const created = { label, items: [item] };
        byLabel.set(label, created);
        groups.push(created);
      }
    });
    return groups;
  }, [authored]);

  const levels = config?.content?.capabilities || {};
  const visible = (item) =>
    (!VENDOR_READY[item.id] || VENDOR_READY[item.id](config))
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
  // Searched in the browser, over the payload the server already filtered -- see search.js for
  // why that is the design and not a shortcut. useMemo because this runs on every keystroke.
  const results = useMemo(() => search_(config?.content, search), [config, search]);
  const searching = search.trim().length >= 2;
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
          {/* The uploaded mark if there is one, the workspace's name set in type if not. The
              rail is dark, so the LIGHT wordmark is the one that belongs on it -- falling back
              to the standalone mark, then to text. alt is empty because the name is already
              read out by the kicker below and by the rail's own aria-label; announcing it twice
              is noise to a screen reader. */}
          {logoUrl
            ? <img className="ut-logo-img" src={logoUrl} alt=""
                   onError={(e) => { e.currentTarget.style.display = "none"; }} />
            : <div className="ut-logo-text">{workspaceName}</div>}
          <div className="ut-logo-kicker">Team Intranet</div>
        </div>
        <nav className="ut-nav">
          {/* The nav STRUCTURE is the product's -- every workspace gets Home, Training,
              SOPs and so on. The assistant's LABEL is the workspace's, because it carries
              their name. Anything below that is content, and comes from their console. */}
          {navGroups.map((group) => ({ ...group, items: group.items.filter(visible) }))
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
                   onKeyDown={(e) => { if (e.key === "Escape") setSearch(""); }}
                   placeholder="Search training, SOPs, people, tools…" aria-label="Search" />
            {results.length ? (
              <div className="ut-search-results" role="listbox">
                {results.map((group) => (
                  <div className="ut-search-group" key={group.type}>
                    <p>{group.type}</p>
                    {group.items.map((item) => (
                      <NavLink key={`${item.type}:${item.title}:${item.to}`} to={item.to}
                               onClick={() => setSearch("")}>
                        <strong>{item.title}</strong>
                        {item.detail ? <em>{item.detail}</em> : null}
                      </NavLink>
                    ))}
                  </div>
                ))}
              </div>
            ) : searching ? (
              <div className="ut-search-results">
                <p className="ut-search-none">Nothing matches “{search.trim()}”.</p>
              </div>
            ) : null}
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
          <h1>{firstName(me) ? `${now.greeting}, ${firstName(me)}.` : `${now.greeting}.`}</h1>
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

      {/* Ungated: Sunburst ships with the platform. The panel still tells the truth about what it
          knows -- an agent we could not match to Sisu is told so rather than shown zeroes. */}
      <SunburstBanner config={config} />

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
/* Nothing is gated on a vendor any more. Sunburst was hidden unless Sisu was connected, then
   unless a link was configured; it ships with the platform for every workspace, so both gates are
   gone. Kept as an empty map rather than deleted because the nav filter reads it, and a page that
   genuinely needs a precondition later belongs here rather than in a new mechanism. */
const VENDOR_READY = {};

function connected(config, providerKey) {
  return (config?.content?.integrations || {})[providerKey] === "connected";
}

function SunburstBanner({ config }) {
  const n = config?.numbers || {};
  // WAS "Last week: 0 appointments set, 0 held, 0 under contract" -- three literal zeroes and an
  // instruction to configure sources, shown to everybody forever. It is their actual week now,
  // and an unmatched agent gets a sentence about that instead of a row of noughts.
  const line = n.sisu_connected
    ? `Last week: ${n.appointments_set ?? 0} appointments set, ${n.appointments_held ?? 0} held, `
      + `${n.new_contracts ?? 0} under contract. Sunburst walks you through what worked, what `
      + "slipped, and what this week needs to look like."
    : "Sunburst reads your Sisu activity. We have not matched your account to an agent yet, so "
      + "it will not know your week until an admin sets your Sisu address on the roster.";
  return (
    <section className="ut-sunburst">
      <div className="ut-sunburst-copy">
        <div className="ut-sunburst-brand">Sunburst</div>
        <div className="ut-sunburst-kicker">Your AI business partner, inside Sisu</div>
        <h2>Your weekly check-in is ready.</h2>
        <p>{line}</p>
        <div className="ut-sunburst-actions">
          <NavLink className="ut-button inverse" to="/sunburst">Start my check-in</NavLink>
        </div>
      </div>
      <div className="ut-sunburst-prompts">
        {SUNBURST_PROMPTS.map((p) => (
          <NavLink key={p.kind} to="/sunburst">
            {p.title}
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
/* "2h 40m", "55m". Never "0m" for a course whose author simply never filled the minutes in --
   an unknown length reads as an instant one. */
function runtime(minutes) {
  const total = Number(minutes) || 0;
  if (!total) return null;
  const hours = Math.floor(total / 60);
  const mins = total % 60;
  return hours ? `${hours}h ${String(mins).padStart(2, "0")}m` : `${mins}m`;
}

/* Where somebody stands in one course, in the three shapes the card needs at once. */
function progressOf(course, done) {
  const lessons = course.lessons || [];
  const complete = lessons.filter((l) => done?.[l.id]).length;
  const total = lessons.length;
  const pct = total ? Math.round((complete / total) * 100) : 0;
  return {
    complete,
    total,
    pct,
    started: complete > 0,
    finished: total > 0 && complete === total,
    // The first lesson they have not done -- what "Resume" and "Continue" actually mean.
    next: lessons.find((l) => !done?.[l.id]) || null,
  };
}

/* The shelf.
 *
 * A stack of title-and-bar panels told somebody what existed and nothing about what to do next,
 * which for a library of a dozen courses is the whole question. So: one course resumed at the
 * top, filters for a team whose library has outgrown a single screen, and cards that say what
 * kind of thing each course is and how far in you are.
 *
 * EVERY NUMBER HERE IS DERIVED, none stored. Course length is the sum of its lessons, the badge
 * comes from their sources, progress comes from the same per-lesson state the player writes. A
 * "percent complete" column would be a second copy of a fact that is already true elsewhere, and
 * the day it disagreed the card would be the thing people believed.
 */
function Training({ state, config }) {
  const courses = (config?.content?.courses) || [];
  const [filter, setFilter] = useState("All");
  const done = state.done || {};

  // Fixed order, and only categories that actually have a course -- a chip that filters to an
  // empty shelf is a dead end somebody has to discover by pressing it.
  const categories = useMemo(() => {
    const seen = [];
    courses.forEach((c) => {
      const name = (c.category || "").trim();
      if (name && !seen.includes(name)) seen.push(name);
    });
    return seen.sort((a, b) => a.localeCompare(b));
  }, [courses]);

  // Furthest in without being finished. Somebody with three courses on the go wants the one they
  // were actually working through, not whichever the admin happened to sort first.
  const resume = useMemo(() => {
    const started = courses
      .map((c) => ({ course: c, p: progressOf(c, done) }))
      .filter((x) => x.p.started && !x.p.finished && x.p.next);
    started.sort((a, b) => b.p.complete - a.p.complete);
    return started[0] || null;
  }, [courses, done]);

  const shown = filter === "All"
    ? courses
    : courses.filter((c) => (c.category || "").trim() === filter);

  if (!courses.length) {
    const denied = deniedBy(config, "training_library");
    return (
      <Page title="Training Library" subtitle={TRAINING_BLURB}>
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
    <Page title="Training Library" subtitle={TRAINING_BLURB}>
      {resume && (
        <NavLink className="ut-resume"
                 to={`/training/${resume.course.id}/${resume.p.next.id}`}>
          <span className="ut-kicker">Pick up where you left off</span>
          <strong>{resume.course.title}</strong>
          <em>
            Lesson {resume.p.complete + 1} of {resume.p.total} · {resume.p.next.title}
          </em>
          <div className="ut-resume-bar">
            <i style={{ width: `${resume.p.pct}%` }} />
          </div>
          <span className="ut-button light">Resume lesson</span>
        </NavLink>
      )}

      {categories.length > 1 && (
        <div className="ut-chips">
          {["All", ...categories].map((name) => (
            <button type="button" key={name}
                    className={`ut-chip${filter === name ? " on" : ""}`}
                    onClick={() => setFilter(name)}>
              {name}
            </button>
          ))}
        </div>
      )}

      <div className="ut-course-grid">
        {shown.map((course) => {
          const p = progressOf(course, done);
          const length = runtime(course.total_duration_minutes);
          return (
            <NavLink className="ut-course-card" key={course.id} to={`/training/${course.id}`}>
              <span className="ut-course-cover">
                <span className="ut-course-tags">
                  <em>{course.media || "Course"}</em>
                  <em>{p.finished ? "Complete"
                    : p.started ? `${p.complete} of ${p.total}`
                      : "Not started"}</em>
                </span>
              </span>
              <span className="ut-course-body">
                {course.category ? <span className="ut-kicker">{course.category}</span> : null}
                <strong>{course.title}</strong>
                <em>
                  {p.total} {p.total === 1 ? "lesson" : "lessons"}
                  {length ? ` · ${length}` : ""}
                </em>
              </span>
              <span className="ut-course-foot">
                <span className="ut-course-bar"><i style={{ width: `${p.pct}%` }} /></span>
                <em>{p.finished ? "Complete" : p.started ? `${p.pct}% complete` : "Not started"}</em>
              </span>
            </NavLink>
          );
        })}
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

/* One course, in order. The list page is a shelf; this is the thing you work through.
 *
 * SEQUENTIAL IS HONOURED HERE. The console has authored `sequential` per course since it shipped
 * and nothing read it, so a course an admin marked as ordered was a wall of checkboxes anybody
 * could tick from the bottom. Locked lessons are shown rather than hidden -- somebody needs to
 * see what is ahead of them -- and the gate is presentational, because a training list is a
 * prompt and not a permission. The permissions matrix is what actually restricts access, and it
 * has already decided whether this page loads at all.
 */
function CourseDetail({ state, setState, config }) {
  const { courseId } = useParams();
  const courses = (config?.content?.courses) || [];
  const course = courses.find((c) => c.id === courseId);
  const toggle = (key) => setState((s) => ({ ...s, done: { ...(s.done || {}), [key]: !s.done?.[key] } }));

  if (!course) {
    return (
      <Page title="Training" subtitle="Courses this workspace has published.">
        <Panel title="Course not found">
          <p className="ut-empty">
            That course is not in your library. It may have been unpublished, or it may not be
            available to your role. <NavLink to="/training">Back to the library</NavLink>
          </p>
        </Panel>
      </Page>
    );
  }

  const lessons = course.lessons || [];
  const done = lessons.filter((l) => state.done?.[l.id]).length;
  const firstUndone = lessons.findIndex((l) => !state.done?.[l.id]);

  return (
    <Page title={course.title}
          subtitle={course.description || "Work through the lessons in order."}>
      <section className="ut-wtd-hero">
        <div>
          <span>{course.category || "Course"}</span>
          <strong>{done}/{lessons.length} complete</strong>
        </div>
        <Meter value={done} total={lessons.length} />
      </section>
      <Panel title="Lessons"
             kicker={course.sequential ? "In order — finish one to open the next" : null}>
        {/* A lesson OPENS, it is not a checkbox with a link in it. The old row sent people to
            loom.com in a new tab, which left the portal behind along with the description, the
            handouts and the rest of the course. The tick is still here for marking something
            done without watching it again, but the row itself goes to the player. */}
        <div className="ut-lesson-rows">
          {lessons.map((lesson, i) => {
            // Everything up to the first unfinished lesson is open; beyond it is not yet.
            const locked = course.sequential && firstUndone !== -1 && i > firstUndone;
            const done = Boolean(state.done?.[lesson.id]);
            return (
              <div key={lesson.id} className={`ut-lesson-row${locked ? " locked" : ""}`}>
                <button type="button" className={`ut-tick${done ? " on" : ""}`}
                        aria-label={done ? "Mark not complete" : "Mark complete"}
                        disabled={locked} onClick={() => toggle(lesson.id)}>
                  {done ? "✓" : ""}
                </button>
                {locked ? (
                  <span className="ut-lesson-link locked">
                    <strong>{lesson.title}</strong>
                    <em>Finish the lesson before this one first</em>
                  </span>
                ) : (
                  <NavLink className="ut-lesson-link" to={`/training/${course.id}/${lesson.id}`}>
                    <strong>{lesson.title}</strong>
                    {lesson.taught_by || lesson.description
                      ? <em>{[lesson.taught_by, lesson.description].filter(Boolean).join(" · ")}</em>
                      : null}
                  </NavLink>
                )}
                <span className="ut-lesson-meta">
                  {lesson.required ? <span className="ut-req">Required</span> : null}
                  {lesson.duration_minutes ? `${lesson.duration_minutes} min` : ""}
                </span>
              </div>
            );
          })}
        </div>
      </Panel>
      {course.issues_certificate && done === lessons.length && lessons.length ? (
        <Panel title="Finished">
          <p className="ut-empty">
            You have completed every lesson. This course issues a certificate — your admin can
            confirm it from the console.
          </p>
        </Panel>
      ) : null}
      <p className="ut-empty"><NavLink to="/training">Back to the library</NavLink></p>
    </Page>
  );
}

/* One lesson, playing.
 *
 * This is the screen the training library existed for and did not have. The course page was a
 * checklist whose titles opened loom.com in a new tab -- which left the portal, and with it the
 * lesson's own copy, its handouts, and any sense of where you were in the course.
 *
 * WHAT PLAYS IN THE PAGE IS THE SERVER'S ANSWER, not a guess made here. `lesson.player` arrives
 * as {mode, url, reason}: Loom, YouTube, Vimeo and PDFs frame, a video file plays natively, and
 * Skool, PLACE and eXp are logged-in products that refuse to be framed at all. Rendering an
 * iframe at one of those produces a blank rectangle or a browser refusal -- a player that looks
 * broken, which is worse than the link it replaced. So those get a launch card that says where
 * the lesson lives. See services/lesson_media.
 */
function LessonPlayer({ state, setState, config }) {
  const { courseId, lessonId } = useParams();
  const courses = (config?.content?.courses) || [];
  const course = courses.find((c) => c.id === courseId);
  const lessons = course?.lessons || [];
  const index = lessons.findIndex((l) => l.id === lessonId);
  const lesson = index >= 0 ? lessons[index] : null;

  const doneMap = state.done || {};
  const done = lessons.filter((l) => doneMap[l.id]).length;
  const firstUndone = lessons.findIndex((l) => !doneMap[l.id]);
  const locked = Boolean(course?.sequential) && firstUndone !== -1 && index > firstUndone;

  if (!course || !lesson) {
    return (
      <Page title="Training" subtitle="Courses this workspace has published.">
        <Panel title="Lesson not found">
          <p className="ut-empty">
            That lesson is not in your library. It may have been unpublished, or it may not be
            available to your role. <NavLink to="/training">Back to the library</NavLink>
          </p>
        </Panel>
      </Page>
    );
  }

  if (locked) {
    return (
      <Page title={lesson.title} subtitle={course.title}>
        <Panel title="Not yet">
          <p className="ut-empty">
            This course runs in order. Finish the lessons before this one first.{" "}
            <NavLink to={`/training/${course.id}`}>Back to {course.title}</NavLink>
          </p>
        </Panel>
      </Page>
    );
  }

  const mark = (value) => setState((s) => ({
    ...s, done: { ...(s.done || {}), [lesson.id]: value },
  }));
  const next = lessons[index + 1];
  const player = lesson.player || { mode: "none" };

  return (
    <Page title={lesson.title}
          subtitle={`${course.title} · Lesson ${index + 1} of ${lessons.length}`}>
      <p className="ut-crumb"><NavLink to={`/training/${course.id}`}>← {course.title}</NavLink></p>

      <div className="ut-lesson-grid">
        <div className="ut-lesson-main">
          <div className="ut-player">
            {player.mode === "video" ? (
              <video controls preload="metadata" src={player.url} />
            ) : player.mode === "iframe" ? (
              <iframe title={lesson.title} src={player.url} allowFullScreen
                      allow="accelerometer; autoplay; clipboard-write; encrypted-media; picture-in-picture" />
            ) : (
              /* Said plainly rather than framed and hoped for. */
              <div className="ut-player-link">
                <p>{player.reason || "This lesson opens elsewhere."}</p>
                {player.url ? (
                  <a className="ut-button" href={player.url}
                     target="_blank" rel="noreferrer noopener">
                    {/* The server's display name, not the stored enum -- this read "Open SKOOL"
                        until the payload carried one. */}
                    Open {lesson.source_label || player.label || "the lesson"} ↗
                  </a>
                ) : null}
              </div>
            )}
          </div>

          <div className="ut-lesson-body">
            <span className="ut-kicker">
              {course.category || "Course"} · Lesson {index + 1} of {lessons.length}
              {lesson.taught_by ? ` · ${lesson.taught_by}` : ""}
            </span>
            <h2>{lesson.title}</h2>
            {lesson.description
              ? <p>{lesson.description}</p>
              : <p className="ut-note">No description has been written for this lesson yet.</p>}
          </div>

          {lesson.attachments?.length > 0 && (
            <Panel title="Attachments">
              <div className="ut-attach-list">
                {lesson.attachments.map((a) => (
                  <LessonAttachment key={a.id} attachment={a} />
                ))}
              </div>
            </Panel>
          )}

          <div className="ut-lesson-actions">
            <button type="button" className="ut-button primary"
                    onClick={() => { mark(!doneMap[lesson.id]); }}>
              {doneMap[lesson.id] ? "Mark not complete" : "Mark lesson complete"}
            </button>
            {next ? (
              <NavLink className="ut-button outline-dark"
                       to={`/training/${course.id}/${next.id}`}
                       onClick={() => mark(true)}>
                {/* Moving on IS finishing this one -- asking for two clicks to express one
                    intention is how progress bars end up wrong. */}
                Next lesson →
              </NavLink>
            ) : (
              <NavLink className="ut-button outline-dark" to={`/training/${course.id}`}>
                Back to the course
              </NavLink>
            )}
          </div>
        </div>

        <Panel title={course.title} kicker={`${done} of ${lessons.length} lessons complete`}>
          <Meter value={done} total={lessons.length} />
          <div className="ut-lesson-side">
            {lessons.map((l, i) => {
              const isLocked = Boolean(course.sequential) && firstUndone !== -1 && i > firstUndone;
              const isDone = Boolean(doneMap[l.id]);
              const here = l.id === lesson.id;
              if (isLocked) {
                return (
                  <span key={l.id} className="ut-side-row locked">
                    <span className={`ut-tick${isDone ? " on" : ""}`}>{isDone ? "✓" : ""}</span>
                    <span>{l.title}</span>
                    <em>{l.duration_minutes ? `${l.duration_minutes} min` : ""}</em>
                  </span>
                );
              }
              return (
                <NavLink key={l.id} className={`ut-side-row${here ? " here" : ""}`}
                         to={`/training/${course.id}/${l.id}`}>
                  <span className={`ut-tick${isDone ? " on" : ""}`}>{isDone ? "✓" : ""}</span>
                  <span>{l.title}</span>
                  <em>{l.duration_minutes ? `${l.duration_minutes} min` : ""}</em>
                </NavLink>
              );
            })}
          </div>
        </Panel>
      </div>
    </Page>
  );
}

/* A handout. A link is the author's URL; a file is fetched through our own authenticated route,
   because a bare href sends no Authorization header and 401s. */
function LessonAttachment({ attachment }) {
  const [busy, setBusy] = useState(false);
  const note = attachment.note
    || (attachment.byte_size ? `${Math.max(1, Math.round(attachment.byte_size / 1024))} KB` : "");
  const badge = attachment.kind === "link"
    ? "Link"
    : (attachment.content_type || "").includes("pdf") ? "PDF" : "File";

  if (attachment.kind === "link") {
    return (
      <a className="ut-attach" href={attachment.url} target="_blank" rel="noreferrer noopener">
        <span className="ut-attach-badge">{badge}</span>
        <strong>{attachment.title}</strong>
        <em>{note}</em>
      </a>
    );
  }

  async function download() {
    if (busy) return;
    setBusy(true);
    try {
      const blob = await getBlob(attachment.url);
      const href = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = href;
      a.download = attachment.title;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(href);
    } finally {
      setBusy(false);
    }
  }

  return (
    <button type="button" className="ut-attach" onClick={download} disabled={busy}>
      <span className="ut-attach-badge">{badge}</span>
      <strong>{attachment.title}</strong>
      <em>{busy ? "Downloading…" : note}</em>
    </button>
  );
}

/* A page the workspace wrote for itself.
 *
 * NAMESPACED UNDER /p/ ON PURPOSE. An authored page at a top-level path would collide the day
 * somebody adds a built-in route with the same name -- and the built-in would win, silently
 * hiding a customer's page. Adding a feature must not break a workspace's content, so authored
 * pages live in their own namespace where nothing we ship later can land on them.
 */
/* An admin adds a section and fills it in afterwards. Until they do, it has no heading, no body
   and no links -- and rendering that is an empty card on a live page, titled with the page's own
   name because the heading fallback had nothing else to use. Skipped instead. */
function hasContent(section) {
  return Boolean((section.heading || "").trim() || (section.body || "").trim()
                 || (section.links || []).length);
}

function AuthoredPage({ config }) {
  const { pageKey } = useParams();
  const page = ((config?.content?.pages) || []).find((x) => x.key === pageKey);

  if (!page) {
    return (
      <Page title="Page not found" subtitle="">
        <Panel title="Not available">
          <p className="ut-empty">
            That page is not in your workspace. It may have been unpublished, or it may not be
            available to your role. <NavLink to="/">Back to Home</NavLink>
          </p>
        </Panel>
      </Page>
    );
  }

  return (
    <Page title={page.title} subtitle={page.subtitle || ""}>
      {!page.sections.filter(hasContent).length ? (
        <Panel title="Nothing here yet">
          <p className="ut-empty">
            This page has no published sections. An admin adds them in the console.
          </p>
        </Panel>
      ) : page.sections.filter(hasContent).map((section) => (
        <Panel key={section.id} title={section.heading || page.title}>
          {section.body
            ? section.body.split(/\n{2,}/).map((para, i) => (
                <p className="ut-page-body" key={i}>{para}</p>
              ))
            : null}
          {section.links?.length ? (
            <div className="ut-page-links">
              {section.links.map((link) => (
                <a key={link.url} href={link.url} target="_blank" rel="noreferrer noopener">
                  {link.label}
                </a>
              ))}
            </div>
          ) : null}
        </Panel>
      ))}
    </Page>
  );
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
              {/* This used to warn that nothing was routed anywhere, which was true and worth
                  saying. Requests are delivered now, so it says the useful thing instead: what
                  happens next, and that they will hear back without chasing anyone. */}
              {" Your request goes straight to the team, and you will be emailed when its status changes."}
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

/* THE ROSTER WAS ALREADY THERE. This screen said "Directory is empty" while intranet_member held
 * the whole team -- admin-only, with no member-facing read. It is the first thing a new starter
 * looks for and it told them their workspace had nobody in it.
 *
 * Leadership first, then everyone else alphabetically. Not a ranking: a new agent looking for
 * "who do I ask" needs the people whose job that is at the top, and everyone else in an order
 * they can scan.
 */
function Directory({ config }) {
  const people = config?.content?.directory || [];
  const ordered = [...people].sort((a, b) =>
    (b.is_leadership ? 1 : 0) - (a.is_leadership ? 1 : 0) || a.name.localeCompare(b.name));

  if (!ordered.length) {
    return (
      <Page title="Who's Who" subtitle="Everyone in this workspace.">
        <Panel title="Nobody yet">
          <p className="ut-empty">
            People appear here once they are added to the roster in the console.
          </p>
        </Panel>
      </Page>
    );
  }

  return (
    <Page title="Who's Who" subtitle={`${ordered.length} ${ordered.length === 1 ? "person" : "people"} in this workspace.`}>
      <div className="ut-directory">
        {ordered.map((person) => (
          <div className="ut-person" key={person.id}>
            <div className="ut-person-head">
              {person.photo_url
                ? <img className="ut-person-photo" src={fileUrl(person.photo_url)} alt=""
                       onError={(e) => { e.currentTarget.style.display = "none"; }} />
                : <span className="ut-person-initials">{initials(person.name)}</span>}
              <div>
                <strong>{person.name}</strong>
                <em>{person.title || person.role}{person.market ? ` · ${person.market}` : ""}</em>
              </div>
            </div>
            {person.owns ? <p className="ut-person-owns"><span>Owns</span> {person.owns}</p> : null}
            {person.bio ? <p className="ut-page-body">{person.bio}</p> : null}
            <div className="ut-person-contact">
              {/* mailto and tel rather than plain text: on a phone this page IS how somebody
                  calls a colleague, and making them retype a number is the difference between
                  a directory and a list of names. */}
              {person.email ? <a href={`mailto:${person.email}`}>{person.email}</a> : null}
              {person.phone ? <a href={`tel:${person.phone}`}>{person.phone}</a> : null}
            </div>
          </div>
        ))}
      </div>
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
  const [status, setStatus] = useState(null);
  const [question, setQuestion] = useState("");
  // A transcript rather than one answer. People ask follow-ups, and replacing the previous
  // answer as they type the next question loses the thing they were reading.
  const [thread, setThread] = useState([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!API_BASE) { setStatus({ available: false, configured: false }); return; }
    getJSON("/intranet/assistant")
      .then(setStatus)
      .catch(() => setStatus({ available: false, configured: false }));
  }, []);

  async function submit(event) {
    event.preventDefault();
    const asked = question.trim();
    if (!asked || busy) return;
    setBusy(true);
    setQuestion("");
    setThread((prev) => [...prev, { asked, pending: true }]);
    try {
      const r = await postJSON("/intranet/ask", { question: asked });
      setThread((prev) => prev.map((t, i) => (i === prev.length - 1
        ? { asked, answer: r.answer, citations: r.citations || [], answered: r.answered }
        : t)));
    } catch (err) {
      setThread((prev) => prev.map((t, i) => (i === prev.length - 1
        ? { asked, error: err?.detail || err?.message || "Could not answer just now." }
        : t)));
    } finally {
      setBusy(false);
    }
  }

  // Three different noes, kept apart. "Not on your plan" sent to somebody whose admin has no
  // upgrade to make, or "ask your admin" when the platform key is missing, both send people to
  // waste somebody's afternoon.
  const unavailable = status && !status.available && (
    !status.permitted
      ? "Your role does not have access to the assistant."
      : !status.on_plan
        ? "The assistant is not included in this workspace's plan."
        : "The assistant is not available right now.");

  return (
    <Page title={askLabel} subtitle="Answers drawn from this workspace’s own documents.">
      <Panel title={askLabel}>
        <form className="ut-ask-shell" onSubmit={submit}>
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            disabled={!status?.available || busy}
            placeholder={status?.available ? "Ask about anything" : "Assistant unavailable"}
          />
          <button type="submit" disabled={!status?.available || busy || !question.trim()}>
            {busy ? "Asking…" : "Ask"}
          </button>
        </form>

        {unavailable && <Empty title="Assistant unavailable">{unavailable}</Empty>}

        {status?.available && !thread.length && (
          <p className="ut-note">
            It reads this workspace{"\u2019"}s SOPs, training, tools, pages and people — the same
            things you can see. It has not read the inside of uploaded documents, so it will point
            you at one rather than summarise it. Your questions are recorded, so whoever runs this
            portal can see what people needed and write down what is missing.
          </p>
        )}

        {thread.map((turn, i) => (
          <div className="ut-ask-turn" key={i}>
            <p className="ut-ask-q">{turn.asked}</p>
            {turn.pending && <p className="ut-note">Looking…</p>}
            {turn.error && <p className="ut-ask-error">{turn.error}</p>}
            {turn.answer && <p className="ut-ask-a">{turn.answer}</p>}
            {turn.citations?.length > 0 && (
              <div className="ut-ask-cites">
                {turn.citations.map((c) => (
                  <NavLink className="ut-ask-cite" key={c.ref + c.title} to={c.ref}>
                    <span className="ut-ask-cite-kind">{c.kind}</span>
                    {c.title}
                  </NavLink>
                ))}
              </div>
            )}
            {turn.answer && turn.answered === false && !turn.citations?.length && (
              /* Said plainly. An answer with nothing behind it looks exactly like one with a
                 document behind it, and only one of them is safe to act on. */
              <p className="ut-note">Nothing in the portal covers this yet — it has been flagged
                for whoever maintains it.</p>
            )}
          </div>
        ))}
      </Panel>
    </Page>
  );
}

/* The prompts the cards offer. Content, not code -- but compiled in for now on purpose: they are
   Sunburst's own three modes rather than anything this workspace authored, and inventing a console
   screen to edit somebody else's product's vocabulary is a setting nobody would ever change. */
const SUNBURST_PROMPTS = [
  { kind: "Review", title: "Walk me through last week",
    note: "Activity, conversion, and the two things that actually moved.",
    prompt: "Walk me through last week." },
  { kind: "Plan", title: "Build this week's business plan",
    note: "Targets for calls, appointments and follow-up, set against your goal.",
    prompt: "Build this week's business plan against my annual goal." },
  { kind: "Diagnose", title: "Where am I leaking deals?",
    note: "Which stage loses people, and what to change first.",
    prompt: "Where am I leaking deals?" },
];

/* NO MARK HERE ON PURPOSE, until Sunburst's actual artwork is in the repo.
 *
 * This drew a broken ring with stroke-dasharray and called it their logo. It was not: the gaps
 * were on the wrong axis and the wordmark beside it was DM Sans, while theirs is custom
 * lettering. A traced approximation of somebody else's trademark is wrong even when it is close,
 * and worse when it is nearly right -- it ships as if it were the real thing.
 *
 * So the name renders as plain text, which is just naming a product, and the mark returns when
 * the file does. Drop `sunburst-mark.svg` into src/intranet/assets/ and this becomes an <img>.
 */

/* Sunburst.
 *
 * Sunburst ships with Sisu and Sisu ships with this product's customers, so it is part of the
 * platform: every workspace gets it and there is nothing to configure. The link is DERIVED per
 * member on the server (services/sunburst) and handed over ready to use -- this page never builds
 * a URL, which is why there is no template to interpolate here.
 *
 * Sisu's link opens Sunburst with an empty box today, so a prompt card copies its question to the
 * clipboard and opens Sunburst for you to paste -- honest, and one keystroke from the real thing.
 * `carries_prompt` flips to true the day Sisu ships a link that takes the question, and every card
 * becomes one click with no change here.
 */
function SunburstPage({ config, me }) {
  const numbers = config?.numbers || {};
  const url = (config?.sunburst?.url || "").trim();
  const carries = Boolean(config?.sunburst?.carries_prompt);
  const [copied, setCopied] = useState("");
  const [typed, setTyped] = useState("");

  // Whether a figure is missing and whether it is zero are different facts. An unmatched agent
  // gets an em dash and a line telling them why; a quiet week gets a nought.
  const stat = (value) => (numbers.sisu_connected && value !== null && value !== undefined
    ? String(value) : "—");

  function open(prompt) {
    if (!url) return;
    if (!carries && prompt) {
      // Best effort: a blocked clipboard must not stop the link from opening.
      try {
        navigator.clipboard?.writeText(prompt);
        setCopied(prompt);
        setTimeout(() => setCopied(""), 4000);
      } catch { /* the window still opens */ }
    }
    window.open(url, "_blank", "noreferrer,noopener");
  }

  // The only way there is no link: the viewer is not on the roster at all, so there is no member
  // to derive one for. An owner who never added themselves is the real case.
  if (!url) {
    return (
      <Page title="Sunburst" subtitle="Your AI business partner, built into Sisu.">
        <Panel title="You are not on the roster">
          <p className="ut-empty">
            Sunburst opens a conversation for a person, and this account is not on the workspace
            roster yet. Add yourself under People &amp; Roster in the console and it will be here.
          </p>
        </Panel>
      </Page>
    );
  }

  return (
    <Page title="Sunburst"
          subtitle={"Your AI business partner, built into Sisu. It reads your real activity, so "
                    + "every link on this page opens a conversation that already knows your week."}>
      <section className="ut-sb-hero">
        <div className="ut-sb-copy">
          <div className="ut-sb-brand">Sunburst</div>
          <span className="ut-kicker">This week{"\u2019"}s check-in</span>
          <h2>Last week, then next week.</h2>
          <p>
            One conversation, two halves. Sunburst walks your last seven days of activity, names
            where the pipeline actually leaked, then builds the coming week{"\u2019"}s plan
            against your goal.
          </p>
          <div className="ut-sb-actions">
            <button type="button" className="ut-button inverse" onClick={() => open("")}>
              Open my check-in in Sunburst
            </button>
          </div>
        </div>

        <div className="ut-sb-knows">
          <span className="ut-kicker">What it already knows</span>
          <dl>
            <div><dt>Appointments set</dt><dd>{stat(numbers.appointments_set)}</dd></div>
            <div><dt>Appointments held</dt><dd>{stat(numbers.appointments_held)}</dd></div>
            <div><dt>New contracts</dt><dd>{stat(numbers.new_contracts)}</dd></div>
            <div><dt>Conversations logged</dt><dd>{stat(numbers.conversations_logged)}</dd></div>
            <div>
              <dt>Pace to annual goal</dt>
              <dd>{numbers.pace_percent === null || numbers.pace_percent === undefined
                ? "—" : `${numbers.pace_percent}%`}</dd>
            </div>
          </dl>
          {/* Said out loud rather than shown as zeroes. An agent whose CRM address differs cannot
              work out why their own page is empty, and an admin cannot fix what nobody reports. */}
          {!numbers.sisu_connected ? (
            <p className="ut-sb-note">
              We could not match {me?.email || "your account"} to an agent in Sisu, so these are
              blank rather than zero. An admin can set your Sisu address on the roster.
            </p>
          ) : numbers.conversations_logged === null ? (
            <p className="ut-sb-note">
              Conversations come from your CRM{"\u2019"}s call log, which is not connected yet.
            </p>
          ) : null}
        </div>
      </section>

      <Panel title="Ask Sunburst">
        <form className="ut-sb-ask" onSubmit={(e) => { e.preventDefault(); open(typed.trim()); }}>
          <input value={typed} placeholder="What can I help you with?"
                 onChange={(e) => setTyped(e.target.value)} />
          <span className="ut-sb-opens">{carries ? "Opens in Sunburst" : "Copies, then opens"}</span>
          <button type="submit" className="ut-button primary" disabled={!typed.trim()}>Ask</button>
        </form>

        <div className="ut-sb-cards">
          {SUNBURST_PROMPTS.map((p) => (
            <button type="button" className="ut-sb-card" key={p.kind}
                    onClick={() => open(p.prompt)}>
              <span className="ut-kicker">{p.kind}</span>
              <strong>{p.title}</strong>
              <em>{p.note}</em>
              <span className="ut-sb-open">
                {copied === p.prompt ? "Copied — paste it in ↗" : "Open in Sunburst →"}
              </span>
            </button>
          ))}
        </div>

        {!carries ? (
          <p className="ut-note">
            Sunburst opens with an empty box, so we copy your question to the clipboard for you
            to paste. When Sisu offers a link that carries the question, these become one click.
          </p>
        ) : null}
      </Panel>
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
        <Route path="/training" element={<Training state={training} config={boot.config} />} />
        <Route path="/training/:courseId" element={<CourseDetail state={training} setState={setTraining} config={boot.config} />} />
        <Route path="/training/:courseId/:lessonId" element={<LessonPlayer state={training} setState={setTraining} config={boot.config} />} />
        <Route path="/p/:pageKey" element={<AuthoredPage config={boot.config} />} />
        <Route path="/onboarding" element={<Onboarding state={onboarding} setState={setOnboarding} />} />
        <Route path="/sops" element={<Sops config={boot.config} />} />
        <Route path="/numbers" element={<Numbers config={boot.config} />} />
        <Route path="/calendar" element={<Calendar config={boot.config} canConfigure={boot.canConfigure} saveConfig={boot.saveConfig} />} />
        <Route path="/marketing" element={<Marketing config={boot.config} canConfigure={boot.canConfigure} />} />
        <Route path="/directory" element={<Directory config={boot.config} />} />
        <Route path="/brand" element={<BrandKit config={boot.config} />} />
        <Route path="/ask" element={<Ask config={boot.config} me={boot.me} />} />
        <Route path="/sunburst" element={<SunburstPage config={boot.config} me={boot.me} />} />
        {/* On The Phone, Listing Marketing and JV Partners were empty PlaceholderPage shells --
            one customer's screen names with nothing behind them. They are what the page builder
            replaces: a workspace writes its own under /p/<key>, in whichever sidebar group it
            chooses, with content it controls. Sunburst stays because it is a real panel gated on
            the integrations map, which is a different mechanism. */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
