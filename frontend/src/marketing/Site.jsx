import React, { useCallback, useEffect, useState } from "react";
import { AcumynLockup, CORE, NEUTRAL, TYPE } from "../brand/acumyn.jsx";
import { SITE, NAV, META } from "./content.js";
import { frontDoorUrl, isFrontDoorHost } from "./hosts.js";
import { wrap, Primary, Styles, WHITE, alpha } from "./ui.jsx";
import Home from "./pages/Home.jsx";
import Features from "./pages/Features.jsx";
import About from "./pages/About.jsx";
import Pricing from "./pages/Pricing.jsx";
import Legal from "./pages/Legal.jsx";
import FrontDoor from "./pages/FrontDoor.jsx";

/* Acumyn's public site, and the one bundle served on two hosts.
 *
 *   www.acumyn.io   the marketing pages: Home, Features, About, Pricing, Privacy, Terms
 *                   (the apex takes over once it can reach Railway — see frontend/Caddyfile)
 *   app.acumyn.io   the workspace finder, and nothing else (see pages/FrontDoor.jsx)
 *
 * One entry rather than two because the finder is a single form that needs this site's type,
 * buttons and mark, and a second Vite entry for one form is a second build to keep in step.
 * Caddy routes both hosts to the same files; the host decides what renders.
 *
 * ROUTING. This uses its own ~30-line history router rather than react-router, which the tenant
 * app depends on. Six known paths and no params is the exact case where a router earns nothing,
 * and it keeps the marketing bundle to React alone. If this ever grows params or nested layouts,
 * swap in react-router and delete this.
 */

const PrivacyPage = (props) => <Legal doc="privacy" {...props} />;
const TermsPage = (props) => <Legal doc="terms" {...props} />;

const ROUTES = {
  "/": Home,
  "/features": Features,
  "/about": About,
  "/pricing": Pricing,
  "/privacy": PrivacyPage,
  "/terms": TermsPage,
};

const normalise = (p) => {
  const clean = (p || "/").split("?")[0].replace(/\/+$/, "") || "/";
  return ROUTES[clean] ? clean : "/";
};

function useRouter() {
  const [path, setPath] = useState(() =>
    normalise(typeof window === "undefined" ? "/" : window.location.pathname));

  useEffect(() => {
    const onPop = () => setPath(normalise(window.location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigate = useCallback((to) => {
    const [target, anchor] = String(to).split("#");
    const next = normalise(target);
    const url = next + (anchor ? `#${anchor}` : "");

    if (next !== normalise(window.location.pathname)) {
      window.history.pushState({}, "", url);
      setPath(next);
      /* A route change should start at the top; an anchor should not. Without this a reader
         who clicks "Features" from halfway down Home lands halfway down Features. */
      if (!anchor) window.scrollTo(0, 0);
    } else if (anchor) {
      window.history.replaceState({}, "", url);
    }

    if (anchor) {
      /* After paint, so the target exists on a page that just mounted. */
      requestAnimationFrame(() => {
        const el = document.getElementById(anchor);
        if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
        window.dispatchEvent(new HashChangeEvent("hashchange"));
      });
    }
  }, []);

  return { path, navigate };
}

/* A real <a href> — so it opens in a new tab on a modifier click, shows a status-bar target,
   and is crawlable — that intercepts a plain left click for client-side navigation. */
function makeLink(navigate) {
  return function Link({ to, children, className, style, onClick }) {
    return (
      <a
        href={to}
        className={className}
        style={style}
        onClick={(e) => {
          if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
          e.preventDefault();
          if (onClick) onClick(e);
          navigate(to);
        }}
      >
        {children}
      </a>
    );
  };
}

function Nav({ path, Link }) {
  const [open, setOpen] = useState(false);
  /* The finder, on app.<this host>. A plain link, not a route: it is another host. */
  const signIn = frontDoorUrl();

  /* Close on resize past the breakpoint. Without this the menu stays open-but-hidden after a
     rotate or a window drag, and the next tap on the burger appears to do nothing. */
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 961px)");
    const close = (e) => { if (e.matches) setOpen(false); };
    mq.addEventListener("change", close);
    return () => mq.removeEventListener("change", close);
  }, []);

  const linkStyle = (to) => ({
    fontFamily: TYPE.text,
    fontWeight: path === to ? 600 : 500,
    fontSize: 14,
    color: path === to ? CORE.ink : NEUTRAL[600],
    textDecoration: "none",
    paddingBottom: 3,
    borderBottom: path === to ? `2px solid ${CORE.cadet}` : "2px solid transparent",
  });

  return (
    <header style={{
      position: "sticky", top: 0, zIndex: 20,
      /* White, not Paper: the sequence hero is on white, and a Paper bar over it reads as a
         grey band across the top of the page — the first thing anyone sees. ClickUp's nav is
         white for the same reason. */
      background: alpha(WHITE, 0.9), backdropFilter: "blur(10px)",
      WebkitBackdropFilter: "blur(10px)", borderBottom: `1px solid ${NEUTRAL[100]}`,
    }}>
      {/* §02 clear space: navigation starts one full x clear of the wordmark, and nothing —
          no divider rule, no menu — enters that field. This lockup is the page's ONE mark
          (§05); the footer and the closing bands deliberately carry none. */}
      <div style={{ ...wrap, height: 68, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <Link to="/" style={{ textDecoration: "none", display: "inline-flex" }}>
          {/* The lockup's svg is titled "Acumyn" and so is its wordmark, which a screen reader
              announces as "Acumyn Acumyn"; the label names the link once, by where it goes. */}
          <span aria-label="Acumyn home" role="img" style={{ display: "inline-flex" }}><AcumynLockup size={26} /></span>
        </Link>

        <nav className="acu-nav" aria-label="Primary">
          {NAV.map((n) => (
            <Link key={n.path} to={n.path} className="acu-navlink" style={linkStyle(n.path)}>
              {n.label}
            </Link>
          ))}
        </nav>

        <div className="acu-navcta" style={{ alignItems: "center", gap: 16 }}>
          <a href={signIn} className="acu-navlink"
            style={{ fontFamily: TYPE.text, fontWeight: 600, fontSize: 14, color: NEUTRAL[700], textDecoration: "none" }}>
            Sign in
          </a>
          <Primary href={SITE.signupUrl}>{SITE.signupLabel}</Primary>
        </div>

        <button
          className="acu-burger" aria-expanded={open} aria-label="Menu"
          onClick={() => setOpen((v) => !v)}
          style={{
            background: "transparent", border: `1px solid ${NEUTRAL[200]}`, borderRadius: 8,
            padding: "8px 10px", cursor: "pointer", color: CORE.ink,
            fontFamily: TYPE.text, fontWeight: 600, fontSize: 13,
          }}
        >
          Menu
        </button>
      </div>

      {open ? (
        <div style={{ borderTop: `1px solid ${NEUTRAL[100]}`, background: WHITE }}>
          <div style={{ ...wrap, padding: "14px 24px 20px", display: "grid", gap: 12 }}>
            {NAV.map((n) => (
              <Link key={n.path} to={n.path} onClick={() => setOpen(false)}
                style={{ ...linkStyle(n.path), fontSize: 15, borderBottom: "none" }}>
                {n.label}
              </Link>
            ))}
            <a href={signIn}
              style={{ fontFamily: TYPE.text, fontWeight: 600, fontSize: 15, color: NEUTRAL[700], textDecoration: "none" }}>
              Sign in
            </a>
            <Primary href={SITE.signupUrl}>{SITE.signupLabel}</Primary>
          </div>
        </div>
      ) : null}
    </header>
  );
}

const footLink = { fontFamily: TYPE.text, fontSize: 13.5, color: NEUTRAL[600], textDecoration: "none" };

function Footer({ Link }) {
  return (
    <footer style={{ background: CORE.paper, borderTop: `1px solid ${NEUTRAL[100]}`, padding: "36px 0" }}>
      <div style={{ ...wrap, display: "flex", flexWrap: "wrap", gap: 20, alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontFamily: TYPE.text, fontSize: 13.5, color: NEUTRAL[500] }}>
          &copy; {new Date().getFullYear()} Acumyn
        </span>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 22, alignItems: "center" }}>
          {NAV.slice(1).map((n) => (
            <Link key={n.path} to={n.path} className="acu-navlink" style={footLink}>{n.label}</Link>
          ))}
          <Link to="/privacy" className="acu-navlink" style={footLink}>Privacy</Link>
          <Link to="/terms" className="acu-navlink" style={footLink}>Terms</Link>
          <a href={`mailto:${SITE.contactEmail}`} className="acu-navlink" style={footLink}>Contact</a>
        </div>
      </div>
    </footer>
  );
}

function MarketingSite() {
  const { path, navigate } = useRouter();
  const Link = React.useMemo(() => makeLink(navigate), [navigate]);
  const Page = ROUTES[path] || Home;

  useEffect(() => {
    document.title = (META[path] || META["/"]).title;
  }, [path]);

  /* White is the page ground and Paper is the accent band, inverting the guide's product
     default. ClickUp's aesthetic is white-dominant with grey separating chapters; on a
     marketing page a full-time Paper ground reads as dim. */
  return (
    <div style={{ background: WHITE, minHeight: "100vh" }}>
      <Styles />
      <a
        href="#main" className="acu-link"
        style={{
          position: "absolute", left: -9999, top: 8,
          fontFamily: TYPE.text, fontWeight: 600, fontSize: 14, background: WHITE,
          padding: "10px 16px", borderRadius: 8, border: `1px solid ${NEUTRAL[200]}`, zIndex: 50,
        }}
        onFocus={(e) => { e.target.style.left = "16px"; }}
        onBlur={(e) => { e.target.style.left = "-9999px"; }}
      >
        Skip to content
      </a>
      <Nav path={path} Link={Link} />
      <main id="main">
        <Page Link={Link} navigate={navigate} />
      </main>
      <Footer Link={Link} />
    </div>
  );
}

/* The host never changes within a page load, so choosing here is stable — and it keeps each
   branch's hooks unconditional inside its own component. */
export default function Site() {
  return isFrontDoorHost() ? <FrontDoor /> : <MarketingSite />;
}
