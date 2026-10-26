import { useEffect, useMemo, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";

import { getPublic, getTenantHost, hasToken, logout, postPublic, setTenantHost } from "./api.js";
import { AuthShell, ErrorNote, Field as AuthField, Handoff, PasswordField, PrimaryButton,
         RememberMe, useChrome } from "../auth/AuthShell.jsx";
import { ForgotPassword } from "../auth/ForgotPassword.jsx";
import { GoogleSignIn, googleErrorFromUrl } from "../auth/GoogleSignIn.jsx";
// The derived action colour, so a workspace that chose its own five gets its own link colour
// for free. AuthShell's rule: colour comes from the palette, never from a component.
import { T } from "../theme.js";
import { palette } from "./brand.js";
import {
  COPY,
  NAV_SECTIONS,
  ROLE_OPTIONS,
  ROUTE_TITLES,
} from "./constants.js";
import {
  useDiscardPending,
  useLogin,
  useOverview,
  usePatchSetupTask,
  usePendingChanges,
  usePreview,
  usePublish,
  useSetupTasks,
  useWorkspace,
} from "./queries.js";
import { Button, ErrorState, Field, LoadingState } from "./ui.jsx";
import AiAssistant from "./pages/AiAssistant.jsx";
import AuditLog from "./pages/AuditLog.jsx";
import BrandIdentity from "./pages/BrandIdentity.jsx";
import Integrations from "./pages/Integrations.jsx";
import MarketingRequests from "./pages/MarketingRequests.jsx";
import Overview from "./pages/Overview.jsx";
import RolesPermissions from "./pages/RolesPermissions.jsx";
import Roster from "./pages/Roster.jsx";
import SopLibrary from "./pages/SopLibrary.jsx";
import TeamCalendar from "./pages/TeamCalendar.jsx";
import Pages from "./pages/Pages.jsx";
import ToolLaunchpad from "./pages/ToolLaunchpad.jsx";
import Training from "./pages/Training.jsx";
import WinTheDay from "./pages/WinTheDay.jsx";
import WhosWho from "./pages/WhosWho.jsx";

function cssVars() {
  return Object.fromEntries(Object.entries(palette).map(([key, value]) => [`--console-${key}`, value]));
}

function titleFor(pathname) {
  return ROUTE_TITLES[pathname] || ROUTE_TITLES["/"];
}

function filterSections(search) {
  const value = search.trim().toLowerCase();
  if (!value) return NAV_SECTIONS;
  return NAV_SECTIONS
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => item.label.toLowerCase().includes(value)),
    }))
    .filter((section) => section.items.length);
}

/* SIGNING IN TO THE CONSOLE IS SIGNING IN, not a different thing that happens to need a password.
 *
 * This was its own small card: email, password, submit. No "remember me", no way back from a
 * forgotten password, no Google -- so a workspace admin who forgot their password had no route
 * at all from the only screen they could see, and the console's session quietly followed
 * different rules from the dashboard's on the same host (see session.js).
 *
 * It is the SHARED shell now, which is the point of that module: the sign-in, the forgotten
 * password, the invite and the reset are one moment in a person's day, and built separately they
 * drift. The console was the fifth implementation.
 *
 * THE WORKSPACE FIELD IS ONLY SHOWN WHEN IT CANNOT BE DERIVED. In production the console is
 * served from the workspace's own host, so asking which workspace is asking a question the
 * address bar already answered -- and the main sign-in page does not ask it. It stays for local
 * development, where the host is localhost and something has to say which workspace is meant.
 */
function workspaceIsAmbiguous() {
  if (typeof window === "undefined") return true;
  const host = window.location.hostname || "";
  return !host || host === "localhost" || host === "127.0.0.1" || host.endsWith(".localhost");
}

function LoginScreen({ onLogin }) {
  const loginMutation = useLogin();
  const chrome = useChrome();
  const [tenantHost, setHost] = useState(getTenantHost());
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(googleErrorFromUrl);
  // Separate from `error`: the two fields must only go red when THEY are what was wrong. A failed
  // Google round trip outlining an empty email and password points at the wrong thing entirely.
  const [rejected, setRejected] = useState(false);
  const [phase, setPhase] = useState("idle");          // idle | busy | done
  // A workspace can hide the control. Hidden means remembered -- see RememberMe.
  const offerRemember = !chrome || chrome.remember_me !== false;
  const [remember, setRemember] = useState(false);
  const askWorkspace = workspaceIsAmbiguous();

  async function submit(event) {
    event.preventDefault();
    setPhase("busy");
    setError(null);
    setRejected(false);
    try {
      await loginMutation.mutateAsync({
        email, password, tenantHost, remember: offerRemember ? remember : true,
      });
      setPhase("done");
      onLogin();
    } catch (err) {
      // The API answers 401 for a wrong password, an unknown address, an account not yet accepted
      // and a disabled one -- all the same, on purpose. This sentence covers all four without
      // hinting which, and still says what to try next.
      const locked = err && err.status === 423;
      const suspended = err && err.status === 403;
      setError(suspended
        ? (err.detail || "This workspace is suspended. Contact your administrator.")
        : locked
          ? "Too many attempts. Wait a few minutes and try again."
          : "That email and password don't match. Check both, or reset your password.");
      setRejected(!suspended && !locked);
      setPhase("idle");
    }
  }

  return (
    <AuthShell
      chrome={chrome}
      title={COPY.consoleName}
      sub="Sign in with the account that administers this workspace."
      overlay={phase === "done"
        ? <Handoff title="Signed in" sub="Opening the console." />
        : null}
    >
      {error ? <ErrorNote>{error}</ErrorNote> : null}
      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 18 }}>
        {askWorkspace ? (
          <AuthField id="console-host" label={COPY.tenantHostLabel} autoComplete="organization"
                     hint="Only asked here because this address does not name a workspace."
                     value={tenantHost} onChange={(e) => setHost(e.target.value)} />
        ) : null}
        <AuthField id="console-email" label={COPY.emailLabel} type="email" autoComplete="username"
                   required autoFocus value={email} invalid={rejected}
                   onChange={(e) => setEmail(e.target.value)} />
        <PasswordField id="console-password" label={COPY.passwordLabel}
                       autoComplete="current-password" required value={password} invalid={rejected}
                       onChange={(e) => setPassword(e.target.value)} />
        <div style={{ display: "flex", alignItems: "center", gap: 16, marginTop: 2 }}>
          {offerRemember ? <RememberMe checked={remember} onChange={setRemember} /> : null}
          <NavLink to="/forgot-password" style={{
            marginLeft: "auto", fontFamily: "var(--font-text)", fontSize: 13.5, fontWeight: 500,
            color: T.poppy, textDecoration: "none",
          }}>Forgot password?</NavLink>
        </div>
        <PrimaryButton type="submit" busy={phase !== "idle"} busyLabel="Signing in">
          {COPY.loginButton}
        </PrimaryButton>
      </form>
      {/* Outside the form on purpose: a separate way in, not another way to submit this one, and
          a <button> inside a form is one stray `type` away from being its submit. */}
      <GoogleSignIn onError={setError} get={getPublic} app="console" />
    </AuthShell>
  );
}

function NoAccess({ onSignOut }) {
  return (
    <main className="console-gate" style={cssVars()}>
      <section className="console-login">
        <div className="console-wordmark">
          <strong>{COPY.productName}</strong>
          <span>{COPY.consoleName}</span>
        </div>
        <ErrorState title={COPY.noAccess} />
        <Button type="button" onClick={onSignOut}>{COPY.signOut}</Button>
      </section>
    </main>
  );
}

function PublishStrip({ pendingCount, pendingLoading, publishMutation, discardMutation }) {
  const busy = publishMutation.isPending || discardMutation.isPending;
  const statusText = pendingCount ? `${pendingCount} ${COPY.pending}` : COPY.noPending;
  return (
    <div className="publish-strip">
      <div>
        <span>{COPY.draftChanges}</span>
        <strong>{pendingLoading ? COPY.loading : statusText}</strong>
      </div>
      <div className="publish-actions">
        <Button type="button" disabled={!pendingCount || busy} busy={discardMutation.isPending} onClick={() => discardMutation.mutate()}>
          {COPY.discard}
        </Button>
        <Button type="button" tone="primary" disabled={!pendingCount || busy} busy={publishMutation.isPending} onClick={() => publishMutation.mutate({})}>
          {COPY.publish}
        </Button>
      </div>
    </div>
  );
}

/* THE WORKSPACE NAMES ITSELF HERE TOO. This rail read "Utah Life / Powered by PLACE" from
   constants, so every customer who opened their own admin console was shown the first
   customer's company and their brokerage. The portal was fixed for this months ago; the console
   was not, and it is the screen an admin spends the most time in.

   `poweredBy` is the PLATFORM, which is Axcion for every tenant -- that line is ours, not
   theirs, and it is the same line the portal's footer carries. */
function Sidebar({ sections, workspaceName }) {
  return (
    <aside className="console-rail">
      <div className="console-brand-lockup">
        <strong>{workspaceName || COPY.productName}</strong>
        <span>{COPY.poweredBy}</span>
        <small>{COPY.consoleName}</small>
      </div>
      <nav>
        {sections.map((section) => (
          <div className="nav-section" key={section.label}>
            <p>{section.label}</p>
            {section.items.map((item) => (
              <NavLink className="nav-item" key={item.to} to={item.to}>
                <span>{item.label.slice(0, 1)}</span>
                {item.label}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>
    </aside>
  );
}

function Shell({
  children,
  currentTitle,
  workspaceName,
  overview,
  pendingChanges,
  pendingLoading,
  preview,
  previewLoading,
  selectedRole,
  setSelectedRole,
  publishMutation,
  discardMutation,
  onTenantChange,
  onSignOut,
}) {
  const [search, setSearch] = useState("");
  const [tenantValue, setTenantValue] = useState(getTenantHost());
  const filteredSections = useMemo(() => filterSections(search), [search]);
  const pendingCount = pendingChanges?.total ?? overview?.counts?.pending_changes ?? 0;
  const tileCount = preview?.tiles?.total ?? 0;
  const calendarCount = preview?.calendar_categories?.total ?? 0;

  function submitTenant(event) {
    event.preventDefault();
    onTenantChange(tenantValue);
  }

  return (
    <div className="console-app" style={cssVars()}>
      <Sidebar sections={filteredSections} workspaceName={workspaceName} />
      <main className="console-main">
        <header className="console-topbar">
          <input
            className="console-search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={COPY.search}
          />
          <NavLink className="ask-link" to="/assistant">{COPY.ask}</NavLink>
          <div className="role-switch" aria-label={COPY.previewingAs}>
            <span>{COPY.previewingAs}</span>
            {ROLE_OPTIONS.map((role) => (
              <button
                type="button"
                key={role.key}
                className={role.key === selectedRole ? "active" : ""}
                onClick={() => setSelectedRole(role.key)}
              >
                {role.label}
              </button>
            ))}
          </div>
          <form className="tenant-switch" onSubmit={submitTenant}>
            <label>
              <span>{COPY.tenant}</span>
              <input value={tenantValue} onChange={(event) => setTenantValue(event.target.value)} />
            </label>
            <Button type="submit">{COPY.updateTenant}</Button>
          </form>
          <Button type="button" onClick={onSignOut}>{COPY.signOut}</Button>
        </header>

        <section className="console-page-head">
          <div>
            <p>{COPY.consoleName}</p>
            <h1>{currentTitle}</h1>
          </div>
          <div className="preview-status">
            <span>{previewLoading ? COPY.loading : `${tileCount} / ${calendarCount}`}</span>
            <small>{selectedRole.replace(/_/g, " ")}</small>
          </div>
        </section>

        <PublishStrip
          pendingCount={pendingCount}
          pendingLoading={pendingLoading}
          publishMutation={publishMutation}
          discardMutation={discardMutation}
        />

        <section className="console-content">
          {children}
        </section>
      </main>
    </div>
  );
}

export default function Console() {
  const [signedIn, setSignedIn] = useState(hasToken());
  const [selectedRole, setSelectedRole] = useState(ROLE_OPTIONS[0].key);
  const [togglingKey, setTogglingKey] = useState("");
  const location = useLocation();
  const overviewQuery = useOverview(signedIn);
  const setupTasksQuery = useSetupTasks(signedIn);
  const pendingQuery = usePendingChanges(signedIn);
  const previewQuery = usePreview(selectedRole, signedIn);
  // So the rail can name the workspace instead of the first customer. GET /console/workspace
  // returns the row directly, not wrapped.
  const workspaceQuery = useWorkspace(signedIn);
  const setupMutation = usePatchSetupTask();
  const publishMutation = usePublish();
  const discardMutation = useDiscardPending();

  useEffect(() => {
    if (overviewQuery.error?.status === 401) {
      logout();
      setSignedIn(false);
    }
  }, [overviewQuery.error]);

  function signOut() {
    logout();
    setSignedIn(false);
  }

  function changeTenant(host) {
    setTenantHost(host);
    signOut();
  }

  async function toggleTask(key, completed) {
    setTogglingKey(key);
    try {
      await setupMutation.mutateAsync({ key, completed });
    } finally {
      setTogglingKey("");
    }
  }

  // SIGNED OUT IS A PLACE WITH MORE THAN ONE SCREEN. This branch used to return the login card
  // whatever the address was, so a "Forgot password?" link had nowhere to go -- which is why
  // there was not one. Routed here rather than in the main table below, because everything there
  // assumes a session.
  if (!signedIn) {
    return (
      <Routes>
        <Route path="/forgot-password" element={<ForgotPassword post={postPublic} />} />
        <Route path="*" element={<LoginScreen onLogin={() => setSignedIn(true)} />} />
      </Routes>
    );
  }
  if (overviewQuery.error?.status === 403) return <NoAccess onSignOut={signOut} />;
  if (overviewQuery.error && overviewQuery.error.status !== 401) {
    return (
      <Shell
        currentTitle={titleFor(location.pathname)}
        workspaceName={workspaceQuery.data?.portal_name}
        overview={overviewQuery.data}
        pendingChanges={pendingQuery.data}
        pendingLoading={pendingQuery.isPending}
        preview={previewQuery.data}
        previewLoading={previewQuery.isLoading}
        selectedRole={selectedRole}
        setSelectedRole={setSelectedRole}
        publishMutation={publishMutation}
        discardMutation={discardMutation}
        onTenantChange={changeTenant}
        onSignOut={signOut}
      >
        <ErrorState onRetry={() => overviewQuery.refetch()} />
      </Shell>
    );
  }

  return (
    <Shell
      currentTitle={titleFor(location.pathname)}
      overview={overviewQuery.data}
      pendingChanges={pendingQuery.data}
      pendingLoading={pendingQuery.isPending}
      preview={previewQuery.data}
      previewLoading={previewQuery.isLoading}
      selectedRole={selectedRole}
      setSelectedRole={setSelectedRole}
      publishMutation={publishMutation}
      discardMutation={discardMutation}
      onTenantChange={changeTenant}
      onSignOut={signOut}
    >
      {overviewQuery.isPending ? (
        <LoadingState />
      ) : (
        <Routes>
          <Route
            path="/"
            element={(
              <Overview
                overview={overviewQuery.data}
                setupTasks={setupTasksQuery.data}
                setupTasksError={setupTasksQuery.error}
                setupTasksLoading={setupTasksQuery.isPending}
                onRetrySetup={() => setupTasksQuery.refetch()}
                onToggleTask={toggleTask}
                togglingKey={togglingKey}
              />
            )}
          />
          <Route path="/brand" element={<BrandIdentity />} />
          <Route path="/roster" element={<Roster overview={overviewQuery.data} />} />
          <Route path="/perms" element={<RolesPermissions />} />
          <Route path="/training" element={<Training />} />
          <Route path="/sops" element={<SopLibrary />} />
          <Route path="/wtd" element={<WinTheDay />} />
          <Route path="/whos-who" element={<WhosWho />} />
          <Route
            path="/launchpad"
            element={(
              <ToolLaunchpad
                selectedRole={selectedRole}
                preview={previewQuery.data}
                previewLoading={previewQuery.isLoading}
              />
            )}
          />
          <Route path="/pages" element={<Pages />} />
          <Route path="/calendar" element={<TeamCalendar />} />
          <Route path="/integrations" element={<Integrations />} />
          <Route path="/marketing" element={<MarketingRequests />} />
          <Route path="/assistant" element={<AiAssistant />} />
          <Route path="/audit" element={<AuditLog />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      )}
    </Shell>
  );
}
