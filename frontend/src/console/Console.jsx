import { useEffect, useMemo, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";

import { getTenantHost, hasToken, logout, setTenantHost } from "./api.js";
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
} from "./queries.js";
import { Button, ErrorState, Field, LoadingState } from "./ui.jsx";
import AiAssistant from "./pages/AiAssistant.jsx";
import AuditLog from "./pages/AuditLog.jsx";
import BrandIdentity from "./pages/BrandIdentity.jsx";
import Integrations from "./pages/Integrations.jsx";
import Overview from "./pages/Overview.jsx";
import RolesPermissions from "./pages/RolesPermissions.jsx";
import Roster from "./pages/Roster.jsx";
import SopLibrary from "./pages/SopLibrary.jsx";
import TeamCalendar from "./pages/TeamCalendar.jsx";
import ToolLaunchpad from "./pages/ToolLaunchpad.jsx";
import Training from "./pages/Training.jsx";
import WinTheDay from "./pages/WinTheDay.jsx";

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

function LoginScreen({ onLogin }) {
  const loginMutation = useLogin();
  const [tenantHost, setHost] = useState(getTenantHost());
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");

  async function submit(event) {
    event.preventDefault();
    setMessage("");
    try {
      await loginMutation.mutateAsync({ email, password, tenantHost });
      onLogin();
    } catch (err) {
      setMessage(err.detail || err.message);
    }
  }

  return (
    <main className="console-gate" style={cssVars()}>
      <section className="console-login">
        <div className="console-wordmark">
          <strong>{COPY.productName}</strong>
          <span>{COPY.consoleName}</span>
        </div>
        <h1>{COPY.loginTitle}</h1>
        <form onSubmit={submit}>
          <Field label={COPY.tenantHostLabel}>
            <input value={tenantHost} onChange={(event) => setHost(event.target.value)} autoComplete="organization" />
          </Field>
          <Field label={COPY.emailLabel}>
            <input value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" />
          </Field>
          <Field label={COPY.passwordLabel}>
            <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" autoComplete="current-password" />
          </Field>
          {message ? <p className="console-form-error">{message}</p> : null}
          <Button type="submit" tone="primary" busy={loginMutation.isPending}>{COPY.loginButton}</Button>
        </form>
      </section>
    </main>
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

function Sidebar({ sections }) {
  return (
    <aside className="console-rail">
      <div className="console-brand-lockup">
        <strong>{COPY.productName}</strong>
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
      <Sidebar sections={filteredSections} />
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

  if (!signedIn) return <LoginScreen onLogin={() => setSignedIn(true)} />;
  if (overviewQuery.error?.status === 403) return <NoAccess onSignOut={signOut} />;
  if (overviewQuery.error && overviewQuery.error.status !== 401) {
    return (
      <Shell
        currentTitle={titleFor(location.pathname)}
        overview={overviewQuery.data}
        pendingChanges={pendingQuery.data}
        pendingLoading={pendingQuery.isPending}
        preview={previewQuery.data}
        previewLoading={previewQuery.isPending}
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
      previewLoading={previewQuery.isPending}
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
          <Route
            path="/launchpad"
            element={(
              <ToolLaunchpad
                selectedRole={selectedRole}
                preview={previewQuery.data}
                previewLoading={previewQuery.isPending}
              />
            )}
          />
          <Route path="/calendar" element={<TeamCalendar />} />
          <Route path="/integrations" element={<Integrations />} />
          <Route path="/assistant" element={<AiAssistant />} />
          <Route path="/audit" element={<AuditLog />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      )}
    </Shell>
  );
}
