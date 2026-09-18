import { useCallback, useEffect, useMemo, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";

import { API_BASE, endViewAs, fileUrl, getBlob, getJSON, hasToken, logout, patchJSON, postJSON, putJSON, uploadFile, viewingAs, wasViewingAs } from "../api.js";
import { applyPortalPalette } from "./palette.js";
import { search as search_ } from "./search.js";
import { logoFor } from "./vendor-logos.js";
import sunburstLogo from "./assets/sunburst-ondark.png";
import {
  FOLLOW_UP_KINDS,
  NAV_GROUPS,
  ONBOARDING,
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
  // The shape the API sends (services/member_numbers, then `_config_out`). NO ZEROES LIVE HERE.
  // This was eleven of them, most under names the server never sends, and every production card
  // read those names -- so every agent saw noughts whatever Sisu held. `own` and `team` stay null
  // until the server supplies them, and a null renders as a reason rather than as somebody's year.
  numbers: {
    connected: false,
    synced_at: null,
    on_roster: false,
    matched: false,
    annual_unit_goal: 0,
    pace_percent: null,
    own: null,
    team: null,
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
      // A view that has ended (or was opened after its session closed) is not a sign-in problem.
      if (err.status === 401) setStatus(wasViewingAs() ? "view-ended" : "login");
      else if (err.status === 403) setStatus("disabled");
      else {
        setStatus("error");
        setError(err.detail || err.message || "Could not load intranet.");
      }
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  /* AN EXPIRED SESSION ENDS THE PORTAL, NOT JUST THE REQUEST THAT NOTICED.
     The bootstrap above sends a 401 to the sign-in screen, but only on load. A tab left open past
     the 12-hour session kept rendering its first payload while every new request failed, and each
     screen explained the failure its own way -- Ask told an owner their role had no access.
     api.js announces any 401 once; this is the one place that acts on it. Both storages are
     cleared, so the next load does not pick the dead token back up. */
  useEffect(() => {
    function onExpired() {
      // A support view ends with its session. It is not this browser's own sign-in, so ending
      // it must not clear that one -- and "sign in" is not what the operator needs to hear.
      if (wasViewingAs()) {
        endViewAs();
        setStatus("view-ended");
        return;
      }
      logout();
      setStatus("login");
    }
    window.addEventListener("cc:session-expired", onExpired);
    return () => window.removeEventListener("cc:session-expired", onExpired);
  }, []);

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

/* WHOSE PORTAL THIS IS, when it is not the reader's own: an Acumyn support view, opened from the
   operator console inside a support session. Always on screen, because everything below it is
   somebody else's page -- and it says the view is read-only before a click has to. */
function ViewAsBanner({ me }) {
  const view = me?.view_as;
  if (!view) return null;
  const ends = view.expires_at
    ? new Date(view.expires_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    : "";
  return (
    <div className="ut-viewas" role="status">
      <span>
        <strong>{`Viewing ${view.name}'s portal`}</strong>
        {` \u00b7 read-only support view by ${view.by}${ends ? ` \u00b7 ends ${ends}` : ""}. They are not told, and nothing here is saved.`}
      </span>
      <button type="button" onClick={leaveView}>End view</button>
    </div>
  );
}

function leaveView() {
  endViewAs();
  // Opened by the operator console, so the tab it came from can close it. If it cannot, the
  // portal reloads as whoever is signed in on this machine -- which is not the person viewed.
  window.close();
  window.location.replace("/intranet/");
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
  // THEIR OWN ROLE first. This settled on the first role in the list for everybody, so the label
  // under an agent's name read "Owner" -- and in a support view of an agent's portal it named a
  // role they do not have. Their own role when they have one, the list's first otherwise.
  const myRoleName = (config?.content?.roles || []).find((r) => r.key === config?.content?.my_role)?.name || null;
  const [roleView, setRoleView] = useState(null);
  // Settle once the workspace's own roles arrive. Not a default in useState: the config is
  // fetched, so at first render there are no roles to choose from yet.
  useEffect(() => {
    setRoleView((prev) => (prev && roleOptions.includes(prev) ? prev : myRoleName || roleOptions[0] || null));
  }, [roleOptions.join("|"), myRoleName]);
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
    if (viewingAs()) {
      leaveView();
      return;
    }
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
        <ViewAsBanner me={me} />
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

/* WHY THIS PORTAL LOOKS EMPTY, when the reason is the reader rather than the workspace.
 *
 * A course or a launchpad tile with an audience is hidden from somebody with no role, and that is
 * correct -- but it is also invisible, so a person with an account and no roster entry sees a
 * published, populated portal as bare shelves and reasonably concludes the product is broken. It
 * happened the first day somebody was invited from the dashboard's Team screen rather than from
 * People & Roster: the two invites create different things, and only one of them creates the
 * roster entry the portal reads.
 *
 * Says nothing when the reader is on the roster with a role, which is almost everybody.
 */
function rosterGap(config) {
  const content = config?.content;
  if (!content || content.on_roster === undefined) return null;      // an older payload: say nothing
  if (!content.on_roster) return "absent";
  return content.my_role ? null : "roleless";
}

function RosterNotice({ config, canConfigure }) {
  const gap = rosterGap(config);
  if (!gap) return null;
  const hidden = "Courses and tools that are limited to a role are hidden from you, and Sunburst "
    + "has no conversation to open.";
  return (
    <Panel title={gap === "absent" ? "You are not on the workspace roster" : "You have no role yet"}>
      <p className="ut-empty">
        {gap === "absent"
          ? `Your account can sign in, but it is not on the roster this portal reads. ${hidden} `
          : `You are on the roster, but no role has been set. ${hidden} `}
        {canConfigure
          ? "Fix it under People & Roster in the console -- the same place an admin adds anybody else who needs the portal."
          : "Ask an admin to add you under People & Roster."}
      </p>
    </Panel>
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

function Home({ config, wtd, training, onboarding, me, canConfigure }) {
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
          <p>Who is waiting on you today, and how the year is going.</p>
        </div>
        <div className="ut-hero-actions">
          <NavLink className="ut-button light" to="/tools">Open My Tools</NavLink>
          <NavLink className="ut-button primary" to="/numbers">My Numbers</NavLink>
        </div>
      </section>

      <RosterNotice config={config} canConfigure={canConfigure} />

      <ProductionCards numbers={numbers} me={me} canConfigure={canConfigure} />

      <GoalSnapshot numbers={numbers} />

      {/* Ungated: Sunburst ships with the platform. The panel still tells the truth about what it
          knows -- an agent we could not match to Sisu is told so rather than shown zeroes. */}
      <SunburstBanner config={config} me={me} canConfigure={canConfigure} />

      <section className="ut-lower-grid">
        <NeedsYouToday me={me} canConfigure={canConfigure} />
        <QuickLaunch config={config} />
      </section>

      <section className="ut-lower-grid">
        <ProgressPanel label="Training Library" done={lessonsDone} total={lessons.length} />
        <ProgressPanel label="Your First 30 Days" done={onboardDone} total={ONBOARDING.length} secondary={`${doneToday}/${totalTasks} Win the Day`} />
      </section>
    </div>
  );
}

/* WHY THERE ARE NO FIGURES OF YOUR OWN, in words somebody can act on. Four different empties, and
   only the last is about the person reading: a workspace with no Sisu connection, a connection that
   has not synced yet, an account with no roster entry, and a member who matched no agent. They were
   one sentence -- "an admin can set your Sisu address on the roster" -- which sent an owner whose
   workspace had no Sisu at all off to fix an email address. `short` finishes a sentence in the
   Sunburst band, where there is room for one. */
function numbersGap(numbers, me, canConfigure) {
  if (!numbers.connected) {
    return {
      kind: "not_connected",
      title: "Sisu is not connected",
      body: canConfigure
        ? "This workspace has no Sisu connection yet. Connect Sisu in the Acumyn dashboard under Settings, Integrations, and production appears here after its first sync."
        : "This workspace has not connected Sisu yet. Your numbers appear here once an admin connects it.",
      short: "this workspace has not connected Sisu yet.",
    };
  }
  if (!numbers.synced_at) {
    return {
      kind: "not_synced",
      title: "Waiting for the first sync",
      body: "Sisu is connected, but no sync has finished yet. Production appears here after the first one does.",
      short: "Sisu's first sync has not finished yet.",
    };
  }
  if (!numbers.on_roster) {
    return {
      kind: "not_on_roster",
      title: "You are not on the roster",
      body: "Your own numbers come from the Sisu agent matched to your roster entry, and this account does not have one. Add yourself under People & Roster in the console.",
      short: "this account is not on the workspace roster yet.",
    };
  }
  return {
    kind: "unmatched",
    title: "No matching Sisu agent",
    body: `We could not match ${me?.email || "your account"} to an agent in Sisu. An admin can set your Sisu address on the roster.`,
    short: "we have not matched your account to a Sisu agent yet.",
  };
}

/* The four production cards. The viewer's own figures when they have any; the team's for somebody
   who may see the team but does not sell -- an owner, usually; otherwise one panel saying why,
   rather than four cards of zeroes that read as somebody's year. */
function ProductionCards({ numbers, me, canConfigure }) {
  if (numbers.own) return <FigureCards figures={numbers.own} numbers={numbers} />;
  if (numbers.team) return <FigureCards figures={numbers.team} numbers={numbers} team={numbers.team} />;
  const gap = numbersGap(numbers, me, canConfigure);
  return (
    <Panel title={gap.title}>
      <p className="ut-note">{gap.body}</p>
    </Panel>
  );
}

/* One set of figures as four cards, a member's or the team's. The same card reads the same field
   either way, which is why `own` and `team` carry the same names. */
function FigureCards({ figures, numbers, team = null }) {
  const scope = team ? "Team " : "";
  const pace = numbers.pace_percent;
  const closedNote = team
    ? `${formatNumber(team.producing_agents)} ${team.producing_agents === 1 ? "agent" : "agents"} producing`
    : pace === null || pace === undefined
      ? "No annual goal set"
      : `${pace}% of pace to a ${formatNumber(numbers.annual_unit_goal)}-unit goal`;
  return (
    <section className="ut-stat-grid">
      <StatCard
        label={`${scope}Units Closed YTD`}
        value={formatNumber(figures.closed_units_ytd)}
        sub={`${formatMoney(figures.closed_volume_ytd)} in volume`}
        note={closedNote}
      />
      <StatCard
        label={`${scope}Pending`}
        value={formatNumber(figures.pending_units)}
        sub={`${formatMoney(figures.pending_volume)} in volume`}
        note={`Contracts from the last ${numbers.pending_window_days} days`}
      />
      <StatCard
        label={`${scope}Appointments Held`}
        value={formatNumber(figures.appointments_held_mtd)}
        sub="Month to date"
        note={`${formatNumber(figures.appointments_held)} in the last ${numbers.window_days} days`}
      />
      <StatCard
        label={`${scope}GCI YTD`}
        value={formatMoney(figures.gci_ytd)}
        sub="Gross commission income"
        note="Closed sales, before splits"
      />
    </section>
  );
}

/* The member's goal, with the team's year beside it for somebody allowed to see the team. Only for
   a member with figures of their own -- a goal meter for somebody who does not sell is a bar that
   never moves. The year is the server's `as_of`: this read "2026 goal" as a literal. */
function GoalSnapshot({ numbers }) {
  const own = numbers.own;
  if (!own) return null;
  const team = numbers.team;
  const goal = Number(numbers.annual_unit_goal || 0);
  const closed = Number(own.closed_units_ytd || 0);
  const pending = Number(own.pending_units || 0);
  const remaining = Math.max(0, goal - closed - pending);
  const year = (numbers.as_of || "").slice(0, 4);

  return (
    <section className={`ut-goal-card${team ? "" : " single"}`}>
      <div className="ut-goal-main">
        <div className="ut-goal-head">
          <strong>{goal ? `${year} goal · ${formatNumber(goal)} units` : `${year} · no unit goal set`}</strong>
          <span>
            {formatNumber(closed)} closed &middot; {formatNumber(pending)} pending
            {goal ? <> &middot; {formatNumber(remaining)} to go</> : null}
          </span>
        </div>
        {goal ? (
          <>
            <div className="ut-goal-meter" aria-label="Goal progress">
              <span className="closed" style={{ width: `${Math.min(100, (closed / goal) * 100)}%` }} />
              <span className="pending" style={{ width: `${Math.min(100, (pending / goal) * 100)}%` }} />
            </div>
            <div className="ut-goal-legend">
              <span><i className="closed" />Closed</span>
              <span><i className="pending" />Pending</span>
            </div>
          </>
        ) : null}
      </div>
      {team ? (
        <div className="ut-team-ytd">
          <span>Team, Year To Date</span>
          <div>
            <strong>{formatNumber(team.closed_units_ytd)}</strong>
            <em>units</em>
          </div>
          <div>
            <strong>{formatMoney(team.closed_volume_ytd)}</strong>
            <em>volume</em>
          </div>
          <div>
            <strong>{formatNumber(team.producing_agents)}</strong>
            <em>agents producing</em>
          </div>
        </div>
      ) : null}
    </section>
  );
}

/* Nav items that belong to a VENDOR rather than to the product. Each appears only where that
   workspace has the relevant integration connected -- Sunburst is a coaching product sold inside
   Sisu, and a permanent nav entry for it would put one customer's vendor in everybody's rail. */
/* Nothing is gated on a vendor any more. Sunburst was hidden unless Sisu was connected, then
   unless a link was configured; it ships with the platform for every workspace, so both gates are
   gone. Kept as an empty map rather than deleted because the nav filter reads it, and a page that
   genuinely needs a precondition later belongs here rather than in a new mechanism. */
const VENDOR_READY = {};

function SunburstBanner({ config, me, canConfigure }) {
  const numbers = config?.numbers || DEFAULT_CONFIG.numbers;
  const own = numbers.own;
  const { url, copied, open } = useSunburst(config);
  // WAS "Last week: 0 appointments set, 0 held, 0 under contract" -- three literal zeroes and an
  // instruction to configure sources, shown to everybody forever. It is their actual week now,
  // and somebody with no figures is told which reason it is instead of shown a row of noughts.
  const line = own
    ? `Last week: ${own.appointments_set} appointments set, ${own.appointments_held} held, `
      + `${own.new_contracts} under contract. Sunburst walks you through what worked, what `
      + "slipped, and what this week needs to look like."
    : `Sunburst reads your Sisu activity, and ${numbersGap(numbers, me, canConfigure).short}`;
  return (
    <section className="ut-sunburst">
      <div className="ut-sunburst-copy">
        {/* Lockup and eyebrow share a row -- the eyebrow sits BESIDE the mark, not under it. */}
        <div className="ut-sunburst-lockup">
          <img className="ut-sb-logo" src={sunburstLogo} alt="Sunburst" />
          <span className="ut-sunburst-kicker">Your AI Business Partner, Inside Sisu</span>
        </div>
        <h2>Your Weekly Check-in is Ready.</h2>
        <p>{line}</p>
        <div className="ut-sunburst-actions">
          {/* Opens the conversation, not the tab that has a button that opens it. Where there is
              no conversation -- somebody who is not on the roster -- the tab is where the reason
              is written, so it stays a link to there. */}
          {url
            ? <button type="button" className="sun-solid" onClick={() => open("")}>Start My Check-in</button>
            : <NavLink className="sun-solid" to="/sunburst">Start My Check-in</NavLink>}
          <NavLink className="sun-outline" to="/sunburst">All Prompts</NavLink>
        </div>
      </div>
      <div className="ut-sunburst-prompts">
        {SUNBURST_PROMPTS.map((p) => (url ? (
          <button type="button" className="sun-prompt" key={p.kind} onClick={() => open(p.prompt)}>
            {p.title}
            {/* The one place the magenta appears on this band, besides a hover border. */}
            <span>{copied === p.prompt ? "Copied" : "\u2192"}</span>
          </button>
        ) : (
          <NavLink className="sun-prompt" key={p.kind} to="/sunburst">
            {p.title}
            <span>{"\u2192"}</span>
          </NavLink>
        )))}
      </div>
    </section>
  );
}

/* ── Needs You Today and the Follow-ups page ────────────────────────────────────────────────
 *
 * WAS THREE ROWS COMPILED INTO THE PORTAL -- "New lead follow-up · Follow Up Boss · Source not
 * connected" -- under a kicker that compared the payload's "Connected" to "connected" and so said
 * "No CRM connected" whatever the workspace had done. Both read GET /intranet/follow-ups now: the
 * server applies the rules (services/follow_ups) and the permissions, and says which of six empties
 * this is, so nothing here decides who may see what.
 *
 * A ROW OPENS THE PERSON IN FOLLOW UP BOSS. The call happens there, where it is logged and where
 * "contacted" flips; a tel: link from here would dial around the CRM.
 */
const KIND_BY_KEY = Object.fromEntries(FOLLOW_UP_KINDS.map((k) => [k.key, k]));
const HOME_ROWS = 6;
// Re-read while the page is open. The server refreshes from FUB every few minutes, so asking
// more often than that would only re-read the same rows.
const FOLLOW_UP_POLL_MS = 5 * 60 * 1000;

// The offline preview has no server to ask. It shows the not-connected state, never invented
// leads -- check-no-mock-data.sh holds the portal to that.
const OFFLINE_FOLLOW_UPS = {
  connection: { state: "not_connected", synced_at: null, sync_failed: false, error: null },
  rules: null, own: null, own_reason: null, team: null, viewing: null,
};

function useFollowUps(agent) {
  const [state, setState] = useState({ status: "loading", data: null, error: null });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!API_BASE) {
      setState({ status: "ready", data: OFFLINE_FOLLOW_UPS, error: null });
      return undefined;
    }
    let alive = true;
    setState((s) => ({ ...s, status: s.data ? "refreshing" : "loading" }));
    const query = agent ? `?agent=${encodeURIComponent(agent)}` : "";
    getJSON(`/intranet/follow-ups${query}`)
      .then((data) => { if (alive) setState({ status: "ready", data, error: null }); })
      .catch((error) => { if (alive) setState((s) => ({ status: "error", data: s.data, error })); });
    return () => { alive = false; };
  }, [agent, tick]);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), FOLLOW_UP_POLL_MS);
    return () => clearInterval(id);
  }, []);
  return { ...state, reload: () => setTick((t) => t + 1) };
}

function clockLabel(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const today = new Date();
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === today.toDateString()) return time;
  return `${d.toLocaleDateString(undefined, { month: "short", day: "numeric" })}, ${time}`;
}

function agoLabel(iso) {
  if (!iso) return "";
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 60) return `${Math.max(minutes, 1)}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/* The task's day against the VIEWER's calendar, as every date in this portal is. */
function dueLabel(task) {
  if (!task) return "";
  if (task.due_at) {
    const at = new Date(task.due_at);
    const sameDay = at.toDateString() === new Date().toDateString();
    if (sameDay) return at.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }
  const [y, m, d] = String(task.due_on || "").split("-").map(Number);
  if (!y) return "";
  const due = new Date(y, m - 1, d);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.round((today - due) / 86400000);
  if (days === 0) return "Today";
  if (days === 1) return "Due yesterday";
  if (days > 1 && days < 7) return `Due ${days} days ago`;
  return `Due ${due.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
}

function quietLabel(iso) {
  if (!iso) return "";
  const days = Math.round((Date.now() - new Date(iso).getTime()) / 86400000);
  return `${days} days quiet`;
}

function followUpWhen(item) {
  if (item.kind === "new_lead") return agoLabel(item.at);
  if (item.kind === "going_cold") return quietLabel(item.at);
  return dueLabel(item.task);
}

/* When a task is due, as the end of "Call due ...": "today", "at 2:00 PM", "yesterday". */
function duePhrase(task) {
  const label = dueLabel(task);
  if (label.startsWith("Due ")) return label.slice(4);
  if (label === "Today") return "today";
  return label ? `at ${label}` : "";
}

function followUpDetail(item) {
  const person = item.person || {};
  const task = item.task;
  const taskText = task ? (task.name || task.type || "Task") : "";
  if (item.kind === "new_lead") {
    const bits = [person.origin, person.stage].filter(Boolean);
    if (task) bits.push(`${task.type || "Task"} due ${duePhrase(task)}`);
    return bits.join(" · ");
  }
  if (item.kind === "going_cold") return person.stage || "";
  return [task && task.type && task.name && task.type !== task.name ? `${task.type}: ${task.name}` : taskText,
          person.stage].filter(Boolean).join(" · ");
}

// The other reasons a person is on the list, as the end of "also ...". A new lead's due task is
// already in its detail line, so it is not said twice.
const ALSO_PHRASE = { new_lead: "a new lead", overdue: "an overdue task",
                      due_today: "a task due today", going_cold: "going cold" };

function FollowUpRow({ item }) {
  const kind = KIND_BY_KEY[item.kind] || { label: item.kind };
  const also = (item.also || [])
    .filter((k) => !(item.kind === "new_lead" && item.task && (k === "overdue" || k === "due_today")))
    .map((k) => ALSO_PHRASE[k] || k);
  const body = (
    <>
      <span className={`ut-fu-kind ${item.kind}`}>{kind.label}</span>
      <div className="ut-fu-main">
        <strong>{item.person?.name || "Unnamed person"}</strong>
        <span>
          {followUpDetail(item)}
          {also.length ? <em className="ut-fu-also">{` · also ${also.join(", ")}`}</em> : null}
        </span>
      </div>
      <em className="ut-fu-when">{followUpWhen(item)}</em>
    </>
  );
  return item.url ? (
    <a className="ut-fu-row" href={item.url} target="_blank" rel="noreferrer noopener"
       title="Open in Follow Up Boss">{body}</a>
  ) : (
    <div className="ut-fu-row">{body}</div>
  );
}

function FollowUpChips({ counts, rules }) {
  const kinds = FOLLOW_UP_KINDS.filter((k) => k.key !== "going_cold"
    || (rules && rules.cold_enabled) || counts?.going_cold);
  return (
    <div className="ut-fu-chips">
      {kinds.map((k) => (
        <span key={k.key} className={`ut-fu-chip ${k.key} ${counts?.[k.key] ? "" : "zero"}`}>
          <strong>{counts?.[k.key] || 0}</strong> {k.chip}
        </span>
      ))}
    </div>
  );
}

/* Six different empties, each with its own sentence -- only "caught up" is good news, and each of
   the others needs somebody different to act. */
function followUpReason(data, me, canConfigure) {
  const conn = data?.connection || {};
  if (conn.state === "not_connected") {
    return canConfigure
      ? "This workspace has not connected Follow Up Boss. Connect it in the Acumyn dashboard under Settings, Integrations, and the leads waiting on your team appear here after the first sync."
      : "This workspace has not connected Follow Up Boss yet. Your follow-ups appear here once an admin connects it.";
  }
  if (conn.state === "not_synced") {
    return "Follow Up Boss is connected, and the first sync has not finished yet. Your follow-ups appear here when it does.";
  }
  if (data?.own_reason === "not_on_roster") {
    return canConfigure
      ? "Your follow-ups come from the Follow Up Boss user linked to your roster entry, and this account does not have one. Add yourself under People & Roster in the console."
      : "Your follow-ups come from the Follow Up Boss user linked to your roster entry, and this account does not have one yet. An admin can add you.";
  }
  if (data?.own_reason === "unmatched") {
    return canConfigure
      ? `We could not match ${me?.email || "your account"} to a Follow Up Boss user. Link one in the console under People & Roster.`
      : `We could not match ${me?.email || "your account"} to a Follow Up Boss user. An admin can link yours in the console.`;
  }
  if (data?.own_reason === "denied") {
    return "Your role does not include Win the Day, which is where follow-ups live.";
  }
  return "";
}

function SyncWarning({ conn }) {
  if (!conn?.sync_failed) return null;
  return (
    <p className="ut-fu-warning">
      {`The last Follow Up Boss sync failed${conn.error ? `: ${conn.error}` : ""}. `}
      {conn.synced_at ? `Showing what it had at ${clockLabel(conn.synced_at)}.` : ""}
    </p>
  );
}

function followUpKicker(fu) {
  const conn = fu.data?.connection;
  if (!fu.data) return fu.status === "error" ? "Could not load" : "Checking Follow Up Boss";
  if (conn?.state === "not_connected") return "No CRM connected";
  if (conn?.state === "not_synced") return "Waiting for the first sync";
  if (conn?.sync_failed) return "Follow Up Boss · last sync failed";
  return `From Follow Up Boss · ${clockLabel(conn?.synced_at)}`;
}

function TeamGlance({ crew, limit = 4 }) {
  if (!crew) return null;
  const rows = crew.by_agent.filter((r) => r.total).slice(0, limit);
  return (
    <div className="ut-fu-team">
      <FollowUpChips counts={crew.counts} />
      {crew.unassigned_new_leads ? (
        <NavLink className="ut-fu-agent unassigned" to="/follow-ups?agent=unassigned">
          <strong>Unassigned new leads</strong>
          <span>{crew.unassigned_new_leads}</span>
        </NavLink>
      ) : null}
      {rows.map((r) => (
        <NavLink className="ut-fu-agent" key={r.agent_id} to={`/follow-ups?agent=${r.agent_id}`}>
          <strong>{r.name}</strong>
          <span>{teamLine(r)}</span>
        </NavLink>
      ))}
      {!rows.length && !crew.unassigned_new_leads
        ? <p className="ut-empty">Nobody on the team has anything waiting in Follow Up Boss.</p>
        : null}
    </div>
  );
}

function teamLine(r) {
  return [r.new_lead ? `${r.new_lead} new` : "", r.overdue ? `${r.overdue} overdue` : "",
          r.due_today ? `${r.due_today} today` : "", r.going_cold ? `${r.going_cold} cold` : ""]
    .filter(Boolean).join(" · ");
}

function NeedsYouToday({ me, canConfigure }) {
  const fu = useFollowUps(null);
  const data = fu.data;
  const conn = data?.connection;
  const queue = data?.own;
  const ready = conn?.state === "ready";
  let body;
  if (!data) {
    body = fu.status === "error"
      ? (
        <p className="ut-empty">
          Follow-ups could not be loaded. <button type="button" className="ut-link-button" onClick={fu.reload}>Try again</button>
        </p>
      )
      : <p className="ut-empty">Loading your follow-ups…</p>;
  } else if (ready && queue) {
    const items = queue.items.slice(0, HOME_ROWS);
    body = (
      <>
        <SyncWarning conn={conn} />
        <FollowUpChips counts={queue.counts} rules={data.rules} />
        {queue.counts.total ? (
          <div className="ut-fu-list">
            {items.map((item) => <FollowUpRow key={`${item.kind}:${item.person?.id}`} item={item} />)}
          </div>
        ) : (
          <p className="ut-empty">You are caught up: nothing new, overdue or due today in Follow Up Boss.</p>
        )}
        {queue.counts.total > items.length ? (
          <NavLink className="ut-fu-more" to="/follow-ups">{`See all ${queue.counts.total} ->`}</NavLink>
        ) : null}
      </>
    );
  } else if (ready && data.team) {
    body = (
      <>
        <SyncWarning conn={conn} />
        <TeamGlance crew={data.team} />
        {data.own_reason ? <p className="ut-fu-note">{`Showing the team. ${followUpReason(data, me, canConfigure)}`}</p> : null}
      </>
    );
  } else {
    body = <p className="ut-empty">{followUpReason(data, me, canConfigure)}</p>;
  }
  return (
    <Panel title="Needs You Today" kicker={followUpKicker(fu)}
           action={ready && (queue || data?.team) ? <NavLink to="/follow-ups">Open {"->"}</NavLink> : null}>
      {body}
    </Panel>
  );
}

function FollowUpGroups({ queue, rules }) {
  if (!queue) return null;
  if (!queue.counts.total) {
    return <p className="ut-empty">Caught up: nothing new, overdue or due today in Follow Up Boss.</p>;
  }
  return (
    <>
      {FOLLOW_UP_KINDS.map((k) => {
        const items = queue.items.filter((i) => i.kind === k.key);
        if (!items.length) return null;
        return (
          <Panel key={k.key} title={k.plural} kicker={`${items.length}${queue.truncated ? "+" : ""}`}>
            <div className="ut-fu-list">
              {items.map((item) => <FollowUpRow key={`${item.kind}:${item.person?.id}`} item={item} />)}
            </div>
          </Panel>
        );
      })}
      {queue.truncated ? <p className="ut-fu-note">Showing the first 200. Follow Up Boss has the rest.</p> : null}
    </>
  );
}

function FollowUpsPage({ me, canConfigure }) {
  const location = useLocation();
  const navigate = useNavigate();
  const params = new URLSearchParams(location.search);
  const agent = params.get("agent");
  const fu = useFollowUps(agent);
  const data = fu.data;
  const conn = data?.connection;
  const ready = conn?.state === "ready";
  const [tab, setTab] = useState(agent ? "team" : "mine");
  useEffect(() => { if (agent) setTab("team"); }, [agent]);
  const queue = data?.own;
  const crew = data?.team;
  const showTabs = ready && queue && crew;
  const view = showTabs ? tab : (queue ? "mine" : "team");
  const rules = data?.rules;
  const subtitle = ready
    ? `From Follow Up Boss, as of ${clockLabel(conn.synced_at)}. New leads from the last ${rules?.new_lead_days ?? 7} days, and tasks due today or overdue${rules?.overdue_max_days ? ` within ${rules.overdue_max_days} days` : ""}.`
    : "Leads waiting on you in Follow Up Boss.";

  let body;
  if (!data) {
    body = <Panel title="Follow-ups"><p className="ut-empty">{fu.status === "error" ? "Follow-ups could not be loaded." : "Loading…"}</p></Panel>;
  } else if (!ready || (!queue && !crew)) {
    body = <Panel title="Follow-ups"><p className="ut-empty">{followUpReason(data, me, canConfigure)}</p></Panel>;
  } else if (view === "mine") {
    body = (
      <>
        <FollowUpChips counts={queue.counts} rules={rules} />
        <FollowUpGroups queue={queue} rules={rules} />
      </>
    );
  } else if (data.viewing) {
    body = (
      <>
        <p className="ut-crumb"><button type="button" className="ut-link-button" onClick={() => navigate("/follow-ups")}>← The team</button></p>
        <Panel title={data.viewing.agent_id === "unassigned" ? "Unassigned" : data.viewing.name}
               kicker={`${data.viewing.counts.total} waiting`}>
          <FollowUpChips counts={data.viewing.counts} rules={rules} />
        </Panel>
        <FollowUpGroups queue={data.viewing} rules={rules} />
      </>
    );
  } else {
    body = (
      <Panel title="The team" kicker="People waiting, per agent">
        <TeamTable crew={crew} rules={rules} />
        {data.own_reason ? <p className="ut-fu-note">{followUpReason(data, me, canConfigure)}</p> : null}
      </Panel>
    );
  }

  return (
    <Page title="Follow-ups" subtitle={subtitle}>
      <SyncWarning conn={conn} />
      {showTabs ? (
        <div className="ut-fu-tabs" role="tablist">
          <button type="button" role="tab" aria-selected={view === "mine"} className={view === "mine" ? "active" : ""}
                  onClick={() => { setTab("mine"); if (agent) navigate("/follow-ups"); }}>Mine</button>
          <button type="button" role="tab" aria-selected={view === "team"} className={view === "team" ? "active" : ""}
                  onClick={() => setTab("team")}>Team</button>
        </div>
      ) : null}
      {body}
    </Page>
  );
}

function TeamTable({ crew, rules }) {
  const cold = rules?.cold_enabled || crew.counts.going_cold;
  const rows = crew.by_agent;
  return (
    <div className="ut-fu-table-wrap">
      <table className="ut-fu-table">
        <thead>
          <tr>
            <th>Agent</th><th>New</th><th>Overdue</th><th>Today</th>{cold ? <th>Cold</th> : null}<th>People</th>
          </tr>
        </thead>
        <tbody>
          {crew.unassigned_total ? (
            <tr>
              <td><NavLink to="/follow-ups?agent=unassigned">Unassigned</NavLink></td>
              <td>{crew.unassigned_new_leads}</td><td>—</td><td>—</td>{cold ? <td>—</td> : null}
              <td>{crew.unassigned_total}</td>
            </tr>
          ) : null}
          {rows.map((r) => (
            <tr key={r.agent_id}>
              <td><NavLink to={`/follow-ups?agent=${r.agent_id}`}>{r.name}</NavLink></td>
              <td>{r.new_lead}</td><td>{r.overdue}</td><td>{r.due_today}</td>
              {cold ? <td>{r.going_cold}</td> : null}
              <td>{r.total}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length && !crew.unassigned_total ? <p className="ut-empty">Nobody on the team has anything waiting.</p> : null}
    </div>
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
  // The logo sits OVER the initials rather than replacing them, which is the reference's own
  // construction: the square is never empty while the image loads, and a vendor we have no file
  // for keeps a mark that looks deliberate instead of a gap.
  const logo = logoFor(tool.name);
  const body = (
    <>
      <span className="ut-tool-mark">
        {tool.name.slice(0, 2)}
        {logo ? <span style={{ backgroundImage: `url(${logo})` }} /> : null}
      </span>
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

function Tools({ config, canConfigure }) {
  const groups = tilesFrom(config);
  const gap = rosterGap(config);
  return (
    <Page title="Tool Launchpad" subtitle="The tools this workspace runs on.">
      {groups.length === 0 ? (
        <>
          <RosterNotice config={config} canConfigure={canConfigure} />
          <Panel title="Tools">
            <Empty title={gap ? "No tools you can open" : "No tools configured"}>
              {gap
                ? "A tile limited to a role is hidden until you have one, so this workspace may have tools you cannot see yet."
                : "An admin can add tools in the console under Tool Launchpad. They appear here once published."}
            </Empty>
          </Panel>
        </>
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
function Training({ state, config, canConfigure }) {
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
    const gap = rosterGap(config);
    return (
      <Page title="Training Library" subtitle={TRAINING_BLURB}>
        {gap ? <RosterNotice config={config} canConfigure={canConfigure} /> : null}
        <Panel title={denied ? "Not available to your role"
                             : gap ? "Nothing you can see yet" : "Nothing published yet"}>
          <p className="ut-empty">
            {denied
              ? "Your role does not have access to the training library. Ask an admin if that looks wrong."
              : gap
                ? "This workspace may well have published courses; the ones limited to a role are hidden until you have one."
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

/* How long a lesson takes, in the unit its kind measures -- computed once on the server, because
   two frontends formatting three units is three chances to disagree.
   NO FALLBACK TO `duration_minutes`. It was tempting and it was wrong: a lesson the migration
   reclassified from video to document still carries the minutes it had as a video, so falling
   back rendered "15 min" on a PDF whose length is measured in pages. `duration` is absent exactly
   when this kind has no length recorded, and nothing is the honest answer for that. */
function lessonLength(lesson, long) {
  if (!lesson.duration) return "";
  return long ? lesson.duration.long : lesson.duration.short;
}

/* "Day 3 of 5" -- the total is the number of sections, which is only a DAY count when the course
   is grouped by day. Anything else and the phrase would be nonsense, so it returns nothing. */
function dayTotal(sections) {
  return sections.some((x) => (x.label || "").startsWith("Day ")) ? sections.length : 0;
}

const KIND_CHIP = {
  video: ["#E7EFF6", "#2F5B84", "▶", "Video"],
  reading: ["#EEE7F6", "#6B4E9E", "¶", "Reading"],
  document: ["#F6E9E6", "#A44A33", "❐", "Document"],
};

function KindChip({ kind }) {
  const [bg, fg, glyph, label] = KIND_CHIP[kind || "video"] || KIND_CHIP.video;
  return (
    <span className="ut-kind" style={{ background: bg, color: fg }}>
      <span aria-hidden="true">{glyph}</span>{label}
    </span>
  );
}

/* The lesson rows, shared by the flat course and by each section.
 *
 * `sequentialFrom` is the list the ordering rule applies WITHIN. For a sectioned course that is
 * the section's own lessons, which is what stops `sequential` reaching across a section boundary
 * -- Day 2's first lesson should not be locked because Day 1's last one is unfinished when Day 2
 * has already opened. */
function LessonRows({ course, lessons, state, toggle, sequentialFrom, locked }) {
  const scope = sequentialFrom || lessons;
  const firstUndone = scope.findIndex((l) => !state.done?.[l.id]);
  return (
    <div className="ut-lesson-rows">
      {lessons.map((lesson) => {
        const at = scope.indexOf(lesson);
        const shut = locked
          || (course.sequential && firstUndone !== -1 && at > firstUndone);
        const isDone = Boolean(state.done?.[lesson.id]);
        return (
          <div key={lesson.id} className={`ut-lesson-row${shut ? " locked" : ""}`}>
            <button type="button" className={`ut-tick${isDone ? " on" : ""}`}
                    aria-label={isDone ? "Mark not complete" : "Mark complete"}
                    disabled={shut} onClick={() => toggle(lesson.id)}>
              {isDone ? "✓" : ""}
            </button>
            {shut ? (
              <span className="ut-lesson-link locked">
                <strong>{lesson.title}</strong>
                <em>{locked ? "This section has not opened yet"
                            : "Finish the lesson before this one first"}</em>
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
              <KindChip kind={lesson.kind} />
              {lesson.required ? <span className="ut-req">Required</span> : null}
              {lessonLength(lesson, true)}
            </span>
          </div>
        );
      })}
      {lessons.length ? null : <p className="ut-empty">No lessons in this section yet.</p>}
    </div>
  );
}

/* A course in sections.
 *
 * ONE CARD PER SECTION, in three states the server decided: complete, current, locked. It decided
 * because "is this open" depends on when THIS member started the course, and two frontends
 * working that out from rules would eventually disagree with each other and with the API.
 *
 * A LOCKED SECTION STILL EXPANDS. Somebody needs to see what is coming, the same reasoning the
 * lesson list already followed -- what a lock does is stop the lessons opening, not hide that
 * they exist.
 */
function SectionedLessons({ course, sections, lessons, state, toggle }) {
  const [open, setOpen] = useState(null);
  const current = sections.find((x) => x.is_current) || sections[0];
  const shown = open === null ? current?.id : open;
  const loose = lessons.filter((l) => !l.section_id);

  return (
    <>
      {loose.length ? (
        <Panel title="Before you start">
          <LessonRows course={course} lessons={loose} state={state} toggle={toggle} />
        </Panel>
      ) : null}

      {sections.map((section) => {
        const mine = lessons.filter((l) => l.section_id === section.id);
        const expanded = shown === section.id;
        return (
          <section key={section.id} className={`ut-section ${section.state}`}>
            <button type="button" className="ut-section-head"
                    aria-expanded={expanded ? "true" : "false"}
                    onClick={() => setOpen(expanded ? "" : section.id)}>
              {section.label ? (
                <span className="ut-section-chip">
                  <em>{section.label.split(" ")[0]}</em>
                  <strong>{section.label.split(" ").slice(1).join(" ")}</strong>
                </span>
              ) : null}
              <span className="ut-section-title">
                <strong>{section.name || section.label || "Section"}</strong>
                {section.summary ? <em>{section.summary}</em> : null}
              </span>
              <span className="ut-section-right">
                <span className={`ut-section-pill ${section.state}`}>
                  {section.state === "complete" ? "Complete"
                    : section.state === "locked" ? "Locked" : "In progress"}
                </span>
                <em>
                  {section.done_count} of {section.lesson_count} done
                  {sectionMinutes(mine) ? ` · ${sectionMinutes(mine)}m` : ""}
                </em>
                {section.note ? <em className={section.state}>{section.note}</em> : null}
              </span>
              <span className="ut-section-caret" aria-hidden="true">{expanded ? "▲" : "▼"}</span>
            </button>
            {expanded ? (
              <div className="ut-section-body">
                <LessonRows course={course} lessons={mine} state={state} toggle={toggle}
                            sequentialFrom={mine} locked={!section.released} />
              </div>
            ) : null}
          </section>
        );
      })}
    </>
  );
}

/* An authored body, with its links behaving.
 *
 * WHY A CLICK HANDLER AND NOT `target` IN THE MARKUP. Whether a link leaves the portal is a
 * RENDER decision made from the href, so storing `target`/`rel` on the anchor would put that
 * decision in the database as well as here -- and only one of the two would ever be updated. The
 * sanitizer keeps `href` and nothing else for exactly this reason.
 *
 * An internal `/…` link routes client-side, which is the whole point of it being internal: a full
 * page load would drop the member out of the portal shell and back through the login check.
 * Everything else opens in a new tab with `noopener`, so the article they were reading is still
 * there when they come back.
 */
function LessonArticle({ html }) {
  const navigate = useNavigate();

  /* Heading anchors, and a contents list once there are enough of them to be worth one.
   *
   * DONE AT RENDER, not stored. An `id` is a link target, which is a property of the page rather
   * than of the document -- and the sanitizer strips `id` precisely so an author cannot collide
   * with the portal's own. Three is the threshold because one or two headings are already visible
   * on screen, and a contents list of two entries is furniture. */
  const { body, contents } = useMemo(() => {
    const seen = new Map();
    const found = [];
    const withIds = (html || "").replace(/<h2>([\s\S]*?)<\/h2>/g, (whole, inner) => {
      const text = inner.replace(/<[^>]*>/g, "").trim();
      let slug = text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")
        || `section-${found.length + 1}`;
      // Two headings with the same words would otherwise share an id, and the second link would
      // scroll to the first.
      const n = (seen.get(slug) || 0) + 1;
      seen.set(slug, n);
      if (n > 1) slug = `${slug}-${n}`;
      found.push({ slug, text });
      return `<h2 id="${slug}">${inner}</h2>`;
    });
    return { body: withIds, contents: found.length >= 3 ? found : [] };
  }, [html]);

  function onClick(event) {
    const anchor = event.target.closest?.("a[href]");
    if (!anchor || event.defaultPrevented) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    const href = anchor.getAttribute("href") || "";
    if (href.startsWith("/")) {
      event.preventDefault();
      navigate(href);
      return;
    }
    if (/^https?:/i.test(href)) {
      event.preventDefault();
      window.open(href, "_blank", "noopener,noreferrer");
    }
    // mailto: and anything else the sanitizer allowed is left to the browser.
  }

  return (
    <>
      {contents.length ? (
        <nav className="ut-contents" aria-label="On this page">
          <span>On this page</span>
          <ol>
            {contents.map((item) => (
              <li key={item.slug}>
                <a href={`#${item.slug}`} onClick={(e) => {
                  // Not a route change: a hash link that went through the router would remount
                  // the lesson and lose the scroll position it was trying to set.
                  e.preventDefault();
                  document.getElementById(item.slug)
                    ?.scrollIntoView({ behavior: "smooth", block: "start" });
                }}>{item.text}</a>
              </li>
            ))}
          </ol>
        </nav>
      ) : null}
      <article
        className="ut-lesson-body-copy"
        onClick={onClick}
        // eslint-disable-next-line react/no-danger
        dangerouslySetInnerHTML={{ __html: body }}
      />
    </>
  );
}

/* The rail, flattened: section headings interleaved with their lessons, in reading order.
 *
 * Built here rather than nested in the JSX because the LOCK is a per-section question -- a
 * section that has not opened locks all of its lessons, and inside an open one `sequential` locks
 * only what follows the first unfinished lesson OF THAT SECTION. Working that out inline meant
 * three nested ternaries and got it wrong across the boundary. */
function railRows(course, sections, lessons) {
  const rows = [];
  const loose = lessons.filter((l) => !l.section_id);
  const push = (group, sectionLocked) => {
    const firstUndone = group.findIndex((l) => !l.done);
    group.forEach((l, i) => rows.push({
      lesson: l.lesson,
      locked: sectionLocked
        || (Boolean(course.sequential) && firstUndone !== -1 && i > firstUndone),
    }));
  };
  const mark = (list) => list.map((l) => ({ lesson: l, id: l.id, done: false }));

  if (loose.length) push(mark(loose), false);
  sections.forEach((section) => {
    rows.push({ heading: true, id: section.id, label: section.label, name: section.name });
    push(mark(lessons.filter((l) => l.section_id === section.id)), !section.released);
  });
  if (!sections.length && !loose.length) push(mark(lessons), false);
  return rows;
}

function sectionMinutes(lessons) {
  return lessons.reduce((total, l) => total + (l.duration?.value && l.kind !== "document"
    ? l.duration.value : 0), 0);
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

  /* OPENING A COURSE IS STARTING IT. `release_rule = 'day_n'` counts from this date, and the
     server owns it -- the progress blob beside it is client-written, and somebody who could
     backdate their own enrolment would unlock every section at once.

     Idempotent on the server, so a reopen returns the first answer rather than restarting the
     clock. Fire-and-forget: a failed write means the member sees an unpaced course for one
     session, which is a smaller problem than a page that will not open. */
  const started = course?.enrolled_on;
  useEffect(() => {
    if (!courseId || !course || started) return;
    postJSON(`/intranet/courses/${courseId}/start`, {}).catch(() => {});
  }, [courseId, course, started]);

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
  const sections = course.sections || [];
  const done = lessons.filter((l) => state.done?.[l.id]).length;

  return (
    <Page title={course.title}
          subtitle={course.description || "Work through the lessons in order."}>
      <section className="ut-wtd-hero">
        <div>
          <span>
            {course.category || "Course"}
            {course.day_number && sections.length
              ? ` · Day ${course.day_number}${dayTotal(sections) ? ` of ${dayTotal(sections)}` : ""}`
              : ""}
          </span>
          <strong>{done}/{lessons.length} complete</strong>
        </div>
        <Meter value={done} total={lessons.length} />
      </section>

      {sections.length ? (
        <SectionedLessons course={course} sections={sections} lessons={lessons}
                          state={state} toggle={toggle} />
      ) : (
        <Panel title="Lessons"
               kicker={course.sequential ? "In order — finish one to open the next" : null}>
          {/* A lesson OPENS, it is not a checkbox with a link in it. The old row sent people to
              loom.com in a new tab, which left the portal behind along with the description, the
              handouts and the rest of the course. The tick is still here for marking something
              done without watching it again, but the row itself goes to the player. */}
          <LessonRows course={course} lessons={lessons} state={state} toggle={toggle}
                      sequentialFrom={lessons} />
        </Panel>
      )}
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
  const sections = course?.sections || [];
  const section = sections.find((x) => x.id === lesson?.section_id) || null;

  /* SECTIONS GATE FIRST, LESSONS SECOND, and `sequential` no longer reaches across a section
     boundary. Day 2's first lesson must open the moment Day 2 does, whatever is left unfinished
     in Day 1 -- otherwise a course that releases by date is really gated by completion and the
     dates are decoration. */
  const scope = section ? lessons.filter((l) => l.section_id === section.id) : lessons;
  const scopeIndex = scope.indexOf(lesson);
  const firstUndone = scope.findIndex((l) => !doneMap[l.id]);
  const locked = Boolean(section && !section.released)
    || (Boolean(course?.sequential) && firstUndone !== -1 && scopeIndex > firstUndone);

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
    const why = section && !section.released
      ? (section.note || `${section.label || "This section"} has not opened yet.`)
      : "This course runs in order. Finish the lessons before this one first.";
    return (
      <Page title={lesson.title} subtitle={course.title}>
        <Panel title="Not yet">
          <p className="ut-empty">
            {why}{" "}
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
  /* NEXT IS ONLY OFFERED IF IT OPENS. The lesson after this one can sit in a section that has
     not been released yet, and "Next: eXp Onboarding Checklist" leading to "Not yet" is the
     product walking somebody into a wall it built. `sequential` is not checked here: clicking
     Next marks this lesson complete, which is exactly what advances that frontier. */
  const nextSection = next?.section_id
    ? sections.find((x) => x.id === next.section_id)
    : null;
  const nextOpens = Boolean(next) && (!nextSection || nextSection.released);
  const player = lesson.player || { mode: "none" };

  return (
    <Page title={lesson.title}
          subtitle={`${course.title} · Lesson ${index + 1} of ${lessons.length}`}>
      <p className="ut-crumb"><NavLink to={`/training/${course.id}`}>← {course.title}</NavLink></p>

      <div className="ut-lesson-grid">
        <div className="ut-lesson-main">
          {/* NO PLAYER AT ALL FOR A READING LESSON -- not an empty frame, not a placeholder, not
              "no source attached yet". The server sends `player: null` for exactly this, because
              an article has no source and rendering the none-state would apologise for a lesson
              that is finished. */}
          {lesson.kind === "reading" ? null : (
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
          )}

          <div className="ut-lesson-body">
            <span className="ut-kicker">
              <KindChip kind={lesson.kind} />
              {section?.label ? `${section.label} · ` : ""}
              Lesson {index + 1} of {lessons.length}
            </span>
            <h2>{lesson.title}</h2>

            {/* The byline row: who to ask about this, how long it takes, when it last changed. */}
            <div className="ut-byline">
              {lesson.taught_by ? (
                <>
                  <span className="ut-avatar" aria-hidden="true">{initials(lesson.taught_by)}</span>
                  <span className="ut-byline-who">
                    <strong>{lesson.taught_by}</strong>
                    <em>{course.category || "Course"}</em>
                  </span>
                </>
              ) : null}
              <span className="ut-byline-meta">
                {lessonLength(lesson, true)}
                {lesson.word_count
                  ? ` · ${lesson.word_count.toLocaleString()} words`
                  : ""}
              </span>
              {section?.note ? (
                <span className={`ut-section-pill ${section.state}`}>{section.note}</span>
              ) : null}
            </div>

            {lesson.description
              ? <p>{lesson.description}</p>
              : lesson.kind === "reading" ? null
                : <p className="ut-note">No description has been written for this lesson yet.</p>}
          </div>

          {/* THE ARTICLE ITSELF. `body_html` was sanitized on write and again on read by
              services/lesson_richtext -- nh3, a real HTML parser, against a fifteen-tag
              allowlist -- which is what makes this safe to render. Styled by ELEMENT inside
              `.ut-lesson-body-copy`: authored nodes carry no classes, and must not, or an author
              would have to mark up for the console and the portal separately. */}
          {lesson.kind === "reading" && lesson.body_html ? (
            <LessonArticle html={lesson.body_html} />
          ) : null}
          {lesson.kind === "reading" && !lesson.body_html ? (
            <p className="ut-note">This lesson has not been written yet.</p>
          ) : null}

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
            <button type="button"
                    className={`ut-button primary${doneMap[lesson.id] ? " marked" : ""}`}
                    onClick={() => { mark(!doneMap[lesson.id]); }}>
              {/* An article is READ, not watched. The same button doing the same thing, named
                  for what the person actually did. */}
              {doneMap[lesson.id]
                ? (lesson.kind === "reading" ? "✓ Marked as read" : "✓ Marked complete")
                : (lesson.kind === "reading" ? "Mark as read" : "Mark complete")}
            </button>
            {nextOpens ? (
              <NavLink className="ut-button outline-dark"
                       to={`/training/${course.id}/${next.id}`}
                       onClick={() => mark(true)}>
                {/* Moving on IS finishing this one -- asking for two clicks to express one
                    intention is how progress bars end up wrong. */}
                Next: {next.title} →
              </NavLink>
            ) : (
              <NavLink className="ut-button outline-dark" to={`/training/${course.id}`}>
                Back to the course
              </NavLink>
            )}
          </div>
        </div>

        <Panel title={course.title}
               kicker={`${done} of ${lessons.length} lessons`
                 + (course.day_number && sections.length
                   ? ` · Day ${course.day_number}${dayTotal(sections) ? ` of ${dayTotal(sections)}` : ""}`
                   : " complete")}>
          <Meter value={done} total={lessons.length} />
          <div className="ut-lesson-side">
            {railRows(course, sections, lessons).map((row) => {
              if (row.heading) {
                return (
                  <span key={`h-${row.id}`} className="ut-side-head">
                    {row.label || row.name}
                  </span>
                );
              }
              const l = row.lesson;
              const isDone = Boolean(doneMap[l.id]);
              const here = l.id === lesson.id;
              if (row.locked) {
                return (
                  <span key={l.id} className="ut-side-row locked">
                    <span className={`ut-tick${isDone ? " on" : ""}`}>{isDone ? "✓" : ""}</span>
                    <span>{l.title}</span>
                    <em>{lessonLength(l, false)}</em>
                  </span>
                );
              }
              return (
                <NavLink key={l.id} className={`ut-side-row${here ? " here" : ""}`}
                         to={`/training/${course.id}/${l.id}`}>
                  <span className={`ut-tick${isDone ? " on" : ""}`}>{isDone ? "✓" : ""}</span>
                  <span>{l.title}</span>
                  <em>{lessonLength(l, false)}</em>
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

/* Where the figures come from, and how fresh they are. */
function numbersSubtitle(numbers) {
  if (!numbers.synced_at) return "Production from Sisu.";
  const at = new Date(numbers.synced_at).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
  return `Production from Sisu, last synced ${at}.`;
}

/* MY NUMBERS, AND THE TEAM'S WHERE THE ROLE ALLOWS IT. `numbers.team` only arrives for somebody
   whose role has Team Production -- the server decides, see `_may_see_team` -- so an owner who does
   not sell gets the team rather than an empty page, and an agent gets exactly their own. */
function Numbers({ config, me, canConfigure }) {
  const numbers = config.numbers || DEFAULT_CONFIG.numbers;
  const team = numbers.team;
  const gap = numbers.own ? null : numbersGap(numbers, me, canConfigure);
  return (
    <Page title="My Numbers" subtitle={numbersSubtitle(numbers)}>
      {numbers.own ? (
        <>
          <FigureCards figures={numbers.own} numbers={numbers} />
          {/* The team has its own section below, so the goal card does not repeat it. */}
          <GoalSnapshot numbers={{ ...numbers, team: null }} />
        </>
      ) : team ? (
        // Sees the team, but has no agent row of their own -- the usual owner.
        <p className="ut-note">{gap.body} The team{"’"}s figures are below.</p>
      ) : (
        <Panel title={gap.title}>
          <p className="ut-note">{gap.body}</p>
        </Panel>
      )}
      {team ? <TeamProduction team={team} numbers={numbers} /> : null}
    </Page>
  );
}

/* Everyone else's numbers: the team's totals, then who they came from. */
function TeamProduction({ team, numbers }) {
  return (
    <>
      <FigureCards figures={team} numbers={numbers} team={team} />
      <Panel title="By agent" kicker={`${formatNumber(team.by_agent.length)} with activity`}>
        {team.by_agent.length ? (
          <div className="ut-numbers-scroll">
            <table className="ut-numbers-table">
              <thead>
                <tr>
                  <th scope="col">Agent</th>
                  <th scope="col">Closed YTD</th>
                  <th scope="col">Volume YTD</th>
                  <th scope="col">GCI YTD</th>
                  <th scope="col">Pending</th>
                  <th scope="col">Held, last {numbers.window_days} days</th>
                </tr>
              </thead>
              <tbody>
                {team.by_agent.map((producer) => (
                  <tr key={producer.id}>
                    <th scope="row">{producer.name}</th>
                    <td>{formatNumber(producer.closed_units_ytd)}</td>
                    <td>{formatMoney(producer.closed_volume_ytd)}</td>
                    <td>{formatMoney(producer.gci_ytd)}</td>
                    <td>{formatNumber(producer.pending_units)}</td>
                    <td>{formatNumber(producer.appointments_held)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="ut-note">
            No agent has a closing, a pending contract or an appointment in these windows yet.
          </p>
        )}
      </Panel>
    </>
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
    // No API is sample mode: say there is no assistant, and nothing about anybody's role.
    if (!API_BASE) {
      setStatus({ available: false, configured: false, permitted: true, on_plan: true });
      return;
    }
    getJSON("/intranet/assistant")
      .then(setStatus)
      // A FAILED REQUEST IS NOT A DENIAL. This used to set an object with no `permitted` key, and
      // the message below read that absence as "your role does not have access" -- which is what
      // an owner with an expired session was told. A 401 now sends the whole portal to sign-in
      // (see useBootstrap); anything else is reported as the outage it is.
      .catch(() => setStatus({ available: false, failed: true }));
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
    status.failed
      ? "Could not reach the assistant just now. Try again in a moment."
      : !status.permitted
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

/* THEIR ACTUAL LOGO, from the design pack -- assets/logos/sunburst-ondark.png, the variant drawn
 * for a dark panel, which is the only kind of panel we put it on.
 *
 * Two earlier versions of this were wrong in the same way: a CSS-bordered circle, then a ring
 * traced with stroke-dasharray. The second was close enough to read as their mark and was not it
 * -- the gaps sat on the wrong axis and the wordmark was DM Sans where theirs is custom lettering.
 * A trademark is the one thing in a UI you never approximate, because "nearly right" ships
 * looking legitimate.
 *
 * Imported rather than referenced by path so Vite fingerprints it and it cannot 404 after a
 * deploy. Sized by HEIGHT with width auto: the file is 200x56 and hard-coding both would squash
 * it the day they publish a logo with different proportions.
 */

/* Sunburst.
 *
 * Sunburst ships with Sisu and Sisu ships with this product's customers, so it is part of the
 * platform: every workspace gets it and there is nothing to configure. The link is DERIVED per
 * member on the server (services/sunburst) and handed over ready to use -- this page never builds
 * a URL, which is why there is no template to interpolate here.
 *
 * TWO LINKS. "Open my check-in" reopens the person's own ongoing conversation. A prompt card or the
 * Ask box uses Sisu's question link where the server hands one over (`ask_url`): the question
 * travels in the link and is sent on arrival, one click. Where it does not -- the setting is off
 * until Sisu's link is live on the host -- a card copies its question to the clipboard and opens
 * the conversation for you to paste, which works everywhere.
 */
/* Opening Sunburst, for the two screens that do it: the home band and this page.
 *
 * ONE IMPLEMENTATION. The band used to be four NavLinks to /sunburst, so a prompt on the home page
 * opened the tab, where the same prompt had to be clicked again to reach Sunburst. A prompt that
 * deep-links on one screen and navigates on the other is the kind of difference nobody notices
 * until somebody asks why their check-in takes three clicks.
 */
function useSunburst(config) {
  const url = (config?.sunburst?.url || "").trim();
  const askUrl = (config?.sunburst?.ask_url || "").trim();
  const carries = Boolean(config?.sunburst?.carries_prompt);
  const [copied, setCopied] = useState("");

  function open(prompt) {
    const question = (prompt || "").trim();
    // ONE CLICK where Sisu's question link exists: a new chat, asked on arrival. The question is
    // encoded with encodeURIComponent because that is what Sisu decodes -- and the Ask box sends
    // whatever somebody typed, apostrophes, ampersands and line breaks included.
    if (question && askUrl) {
      window.open(`${askUrl}?input=${encodeURIComponent(question)}&autosend=true&view=fullscreen`,
                  "_blank", "noreferrer,noopener");
      return;
    }
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

  return { url, carries, copied, open };
}

function SunburstPage({ config, me, canConfigure }) {
  const numbers = config?.numbers || DEFAULT_CONFIG.numbers;
  const own = numbers.own;
  const { url, carries, copied, open } = useSunburst(config);
  const [typed, setTyped] = useState("");

  // Whether a figure is missing and whether it is zero are different facts. Somebody with no
  // figures gets an em dash and a line saying why; a quiet week gets a nought.
  const stat = (value) => (own && value !== null && value !== undefined ? String(value) : "—");


  // A support view of somebody's portal: their Sunburst link opens their own coaching
  // conversation inside Sisu, so the server withholds it rather than hand it to a viewer.
  if (!url && config?.sunburst?.view_as) {
    return (
      <Page title="Sunburst" subtitle="Your AI business partner, built into Sisu.">
        <Panel title="Not opened from a support view">
          <p className="ut-empty">
            Sunburst opens this person{"\u2019"}s own coaching conversation inside Sisu, so only
            they can open it, from their own sign-in.
          </p>
        </Panel>
      </Page>
    );
  }

  // The only way there is no link: the viewer is not on the roster at all, so there is no member
  // to derive one for. An owner who never added themselves is the real case.
  if (!url) {
    return (
      <Page title="Sunburst" subtitle="Your AI business partner, built into Sisu.">
        <RosterNotice config={config} canConfigure={canConfigure} />
        <Panel title="No conversation to open">
          <p className="ut-empty">
            Sunburst opens a conversation for a person on the roster, and this account is not one
            yet. It appears here as soon as it is.
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
          <img className="ut-sb-logo" src={sunburstLogo} alt="Sunburst" />
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
            <div><dt>Appointments set</dt><dd>{stat(own?.appointments_set)}</dd></div>
            <div><dt>Appointments held</dt><dd>{stat(own?.appointments_held)}</dd></div>
            <div><dt>New contracts</dt><dd>{stat(own?.new_contracts)}</dd></div>
            <div><dt>Conversations logged</dt><dd>{stat(own?.conversations_logged)}</dd></div>
            <div>
              <dt>Pace to annual goal</dt>
              <dd>{numbers.pace_percent === null || numbers.pace_percent === undefined
                ? "—" : `${numbers.pace_percent}%`}</dd>
            </div>
          </dl>
          {/* Said out loud rather than shown as zeroes -- and the right reason out of four. This
              told an owner whose workspace had no Sisu connection that their address could not
              be matched. */}
          {!own ? (
            <p className="ut-sb-note">
              {numbersGap(numbers, me, canConfigure).body} Until then these are blank rather than
              zero.
            </p>
          ) : own.conversations_logged === null ? (
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
  if (boot.status === "view-ended") {
    return <AccessState title="This view has ended" message="A support view of somebody's portal lasts as long as the support session that opened it. Open another from the operator console if you need one." />;
  }
  if (boot.status === "error") {
    return <AccessState title="Could not load intranet" message={boot.error || "Try again shortly."} action={<button className="ut-button primary" onClick={boot.refresh}>Retry</button>} />;
  }

  return (
    <Shell me={boot.me} config={boot.config}>
      <Routes>
        <Route path="/" element={<Home config={boot.config} wtd={wtd} training={training} onboarding={onboarding} me={boot.me} canConfigure={boot.canConfigure} />} />
        <Route path="/tools" element={<Tools config={boot.config} canConfigure={boot.canConfigure} />} />
        <Route path="/wtd" element={<WinTheDay state={wtd} setState={setWtd} config={boot.config} />} />
        <Route path="/follow-ups" element={<FollowUpsPage me={boot.me} canConfigure={boot.canConfigure} />} />
        <Route path="/training" element={<Training state={training} config={boot.config} canConfigure={boot.canConfigure} />} />
        <Route path="/training/:courseId" element={<CourseDetail state={training} setState={setTraining} config={boot.config} />} />
        <Route path="/training/:courseId/:lessonId" element={<LessonPlayer state={training} setState={setTraining} config={boot.config} />} />
        <Route path="/p/:pageKey" element={<AuthoredPage config={boot.config} />} />
        <Route path="/onboarding" element={<Onboarding state={onboarding} setState={setOnboarding} />} />
        <Route path="/sops" element={<Sops config={boot.config} />} />
        <Route path="/numbers" element={<Numbers config={boot.config} me={boot.me} canConfigure={boot.canConfigure} />} />
        <Route path="/calendar" element={<Calendar config={boot.config} canConfigure={boot.canConfigure} saveConfig={boot.saveConfig} />} />
        <Route path="/marketing" element={<Marketing config={boot.config} canConfigure={boot.canConfigure} />} />
        <Route path="/directory" element={<Directory config={boot.config} />} />
        <Route path="/brand" element={<BrandKit config={boot.config} />} />
        <Route path="/ask" element={<Ask config={boot.config} me={boot.me} />} />
        <Route path="/sunburst" element={<SunburstPage config={boot.config} me={boot.me} canConfigure={boot.canConfigure} />} />
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
