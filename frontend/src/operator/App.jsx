/* Acumyn · Operator — the console Acumyn staff run the platform from.
 *
 * THE ONE LAW: no status without its reason. Every chip carries the derivation that produced it and
 * the action that clears it; the API supplies both (services/fleet_health.py).
 *
 * THE OTHER LAW: operational metadata only. Who was invited, what connected, what synced, what broke.
 * Never a tenant's numbers.
 *
 * It must never be mistaken for a customer's dashboard. It lives on its own origin
 * (admin.acumyn.io) and in its own bundle, and every screen sits under the ink bar reading
 * "Acumyn | Operator", which no workspace surface has.
 */
import React, { useCallback, useEffect, useState } from "react";
import { AcumynLockup } from "../brand/acumyn.jsx";
import { api, hasSession, operatorName, signIn, signOut } from "./api.js";
import { Btn, Card, Field, inputStyle, Notice } from "./primitives.jsx";
import { css } from "./styles.js";
import { A, TYPE } from "./tokens.js";
import FleetView from "./views/Fleet.jsx";
import WorkspacesView from "./views/Workspaces.jsx";
import IncidentsView from "./views/Incidents.jsx";
import ProvisionView from "./views/Provision.jsx";
import AuditView from "./views/Audit.jsx";
import SystemView from "./views/System.jsx";
import WorkspaceDetail from "./workspace/Detail.jsx";

/* Sections, in rail order. `path` is the first URL segment. */
const NAV = [
  { key: "fleet", label: "Fleet", title: "Fleet", sub: "Everything wrong across every workspace, in the order it should be dealt with." },
  { key: "workspaces", label: "Workspaces", title: "Workspaces", sub: "Every workspace, with the worst open signal on each and the reason for it." },
  { key: "new", label: "New workspace", title: "New workspace", sub: "Live at its own address the moment you press create." },
  { key: "incidents", label: "Incidents", title: "Incidents", sub: "Errors grouped by cause, with what clears each one." },
  { key: "audit", label: "Audit", title: "Audit", sub: "Every change across the platform, by Acumyn staff and by each workspace's own team." },
  { key: "system", label: "System", title: "System", sub: "What is deployed, whether the API, the worker and the database are well, and the flags that change every workspace at once." },
];

function parse(pathname) {
  const parts = pathname.replace(/^\/+|\/+$/g, "").split("/").filter(Boolean);
  const section = NAV.some((n) => n.key === parts[0]) ? parts[0] : "fleet";
  return { section, slug: section === "workspaces" ? parts[1] || null : null, tab: parts[2] || null };
}

function useRoute() {
  const [route, setRoute] = useState(() => parse(window.location.pathname));
  useEffect(() => {
    const onPop = () => setRoute(parse(window.location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const go = useCallback((to, { replace = false } = {}) => {
    if (to === window.location.pathname) return;
    window.history[replace ? "replaceState" : "pushState"]({}, "", to);
    setRoute(parse(to));
    if (!replace) window.scrollTo(0, 0);
  }, []);
  return { route, go };
}

function SignIn({ onDone }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(email.trim(), password);
      onDone();
    } catch (err) {
      /* The server's own words. A locked account (423) and a wrong password (401) are different
         problems, and collapsing them is how an outage once reached a person as a mystery. */
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ minHeight: "100vh", background: A.ground, display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>
      <div style={{ width: "100%", maxWidth: 380 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 18 }}>
          <AcumynLockup size={24} />
          <span aria-hidden style={{ width: 1, height: 14, background: A.lineMid }} />
          <span style={{ fontFamily: TYPE.text, fontSize: 13, fontWeight: 600, color: A.body }}>Operator</span>
        </div>
        <Card title="Sign in" sub="Acumyn staff only. Operator accounts are created from the deployment, never from this page.">
          <form onSubmit={submit} style={{ display: "grid", gap: 14 }}>
            <Field label="Email" htmlFor="op-email">
              <input id="op-email" type="email" autoComplete="username" required value={email}
                onChange={(e) => setEmail(e.target.value)} style={inputStyle} />
            </Field>
            <Field label="Password" htmlFor="op-password">
              <input id="op-password" type="password" autoComplete="current-password" required value={password}
                onChange={(e) => setPassword(e.target.value)} style={inputStyle} />
            </Field>
            {error ? <Notice tone="error">{error}</Notice> : null}
            <Btn kind="primary" type="submit" busy={busy}>Sign in</Btn>
          </form>
        </Card>
      </div>
    </div>
  );
}

export default function OperatorApp() {
  const [authed, setAuthed] = useState(hasSession());
  const [name, setName] = useState(operatorName());
  const { route, go } = useRoute();

  useEffect(() => {
    const onOut = () => setAuthed(false);
    window.addEventListener("op:signed-out", onOut);
    return () => window.removeEventListener("op:signed-out", onOut);
  }, []);

  /* Confirm the stored session is still one the API accepts; a stale token signs out quietly. */
  useEffect(() => {
    if (!authed) return;
    api.me().then((me) => setName(me.name || me.email), () => {});
  }, [authed]);

  useEffect(() => {
    const nav = NAV.find((n) => n.key === route.section);
    document.title = route.slug ? `${route.slug} · Acumyn Operator` : `${nav ? nav.title : "Fleet"} · Acumyn Operator`;
  }, [route]);

  const content = (
    <style>{css}</style>
  );

  if (!authed) {
    return <div className="ac-console">{content}<SignIn onDone={() => { setAuthed(true); setName(operatorName()); }} /></div>;
  }

  const open = (slug, tab) => go(`/workspaces/${encodeURIComponent(slug)}${tab ? `/${tab}` : ""}`);
  const heading = route.slug ? null : NAV.find((n) => n.key === route.section);

  return (
    <div className="ac-console" style={{ minHeight: "100vh", background: A.ground, color: A.ink }}>
      {content}
      <header style={{
        background: A.ink, padding: "0 24px", position: "sticky", top: 0, zIndex: 20,
        borderBottom: `1px solid ${A.ink80}`,
      }}>
        <div style={{ maxWidth: 1240, margin: "0 auto", height: 52, display: "flex", alignItems: "center", gap: 14, justifyContent: "space-between" }}>
          <a href="/fleet" onClick={(e) => { e.preventDefault(); go("/fleet"); }}
            style={{ display: "flex", alignItems: "center", minWidth: 0, textDecoration: "none" }} aria-label="Acumyn Operator, fleet">
            <AcumynLockup size={20} treatment="reversed" />
            <span aria-hidden style={{ width: 1, height: 12, background: A.onInkMute, opacity: 0.45, margin: "0 9px", flexShrink: 0 }} />
            <span style={{ fontFamily: TYPE.text, fontSize: 11.5, fontWeight: 500, color: A.onInkMute, whiteSpace: "nowrap" }}>Operator</span>
          </a>
          <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
            <span className="ac-headname" style={{ fontFamily: TYPE.text, fontSize: 12, color: A.onInkMute, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{name}</span>
            <Btn small kind="onGhost" onClick={() => signOut()}>Sign out</Btn>
          </div>
        </div>
      </header>

      <div className="ac-shell">
        <nav className="ac-rail" aria-label="Console sections">
          {NAV.map((n) => {
            const on = route.section === n.key && !(route.slug && n.key !== "workspaces");
            return (
              <a key={n.key} href={`/${n.key}`} onClick={(e) => { e.preventDefault(); go(`/${n.key}`); }}
                aria-current={on ? "page" : undefined}
                className={`ac-btn ac-nav${on ? " on" : ""}`} style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8,
                  fontFamily: TYPE.text, fontSize: 12.5, fontWeight: on ? 600 : 500, cursor: "pointer",
                  textAlign: "left", whiteSpace: "nowrap", borderRadius: 7, padding: "8px 11px", textDecoration: "none",
                  background: on ? A.paper : "transparent", color: on ? A.ink : A.body,
                  border: `1px solid ${on ? A.line : "transparent"}`, boxShadow: on ? A.lift : "none",
                  transition: "background .12s ease, color .12s ease, border-color .12s ease",
                }}>{n.label}</a>
            );
          })}
          <div style={{ marginTop: 14, padding: "0 11px" }} className="ac-hidesm">
            <div style={{ fontFamily: TYPE.text, fontSize: 10, fontWeight: 600, letterSpacing: ".14em", textTransform: "uppercase", color: A.mute }}>Boundary</div>
            <div style={{ fontFamily: TYPE.text, fontSize: 10.5, color: A.mute, marginTop: 6, lineHeight: 1.55, textWrap: "pretty" }}>
              This console shows operational metadata only: who, what connected, what broke. Never a
              workspace's numbers.
            </div>
          </div>
        </nav>

        <main style={{ minWidth: 0 }}>
          {heading ? (
            <div style={{ marginBottom: 16 }}>
              <h1 style={{ margin: 0, fontFamily: TYPE.display, fontSize: 22, fontWeight: 700, letterSpacing: "-.02em", color: A.ink }}>{heading.title}</h1>
              <p style={{ margin: "5px 0 0", fontFamily: TYPE.text, fontSize: 12.5, color: A.mute, lineHeight: 1.55, maxWidth: 620, textWrap: "pretty" }}>{heading.sub}</p>
            </div>
          ) : null}
          {route.slug ? (
            <WorkspaceDetail key={route.slug} slug={route.slug} tab={route.tab}
              onTab={(t) => go(`/workspaces/${encodeURIComponent(route.slug)}/${t}`, { replace: true })}
              onBack={() => go("/workspaces")} onOpen={open} />
          ) : route.section === "workspaces" ? <WorkspacesView onOpen={open} onNew={() => go("/new")} />
            : route.section === "new" ? <ProvisionView onOpen={open} />
              : route.section === "incidents" ? <IncidentsView onOpen={open} />
                : route.section === "audit" ? <AuditView onOpen={open} />
                  : route.section === "system" ? <SystemView />
                    : <FleetView onOpen={open} onAudit={() => go("/audit")} />}
        </main>
      </div>
    </div>
  );
}
