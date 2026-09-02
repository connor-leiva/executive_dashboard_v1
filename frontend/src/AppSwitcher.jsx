import { useState } from "react";

const DEFAULT_APPS = [{ id: "dashboard", name: "Dashboard", href: "/" }];

export function appsForUser(user) {
  return Array.isArray(user?.apps) && user.apps.length ? user.apps : DEFAULT_APPS;
}

function currentAppId() {
  return window.location.pathname.startsWith("/intranet") ? "intranet" : "dashboard";
}

export default function AppSwitcher({ user, tone = "light" }) {
  const [open, setOpen] = useState(false);
  const apps = appsForUser(user);
  if (apps.length < 2) return null;
  const current = currentAppId();
  const active = apps.find((app) => app.id === current) || apps[0];
  const dark = tone === "dark";
  const ink = dark ? "var(--intra-paper, #F9F6EF)" : "var(--t-slate, #395262)";
  const bg = dark ? "rgba(255,255,255,.08)" : "var(--t-parchment, #F6F0E9)";
  const line = dark ? "rgba(255,255,255,.18)" : "var(--t-line, #EAE1D6)";

  return (
    <div className="app-switcher" style={{ position: "relative" }}>
      <style>{`
        .app-switcher button:hover,
        .app-switcher a:hover { filter: brightness(.98); }
        .app-switcher button:focus-visible,
        .app-switcher a:focus-visible { outline: 2px solid var(--t-teal, #227175); outline-offset: 2px; }
      `}</style>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        title="Switch app"
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "inline-flex", alignItems: "center", gap: 8, height: 34,
          border: `1px solid ${line}`, borderRadius: 8, background: bg, color: ink,
          padding: "0 10px", cursor: "pointer", fontSize: 12.5, fontWeight: 700,
        }}
      >
        <span aria-hidden style={{
          display: "grid", gridTemplateColumns: "repeat(2, 5px)", gap: 2,
          width: 12, height: 12,
        }}>
          {[0, 1, 2, 3].map((i) => (
            <span key={i} style={{ width: 5, height: 5, borderRadius: 2, background: ink }} />
          ))}
        </span>
        <span>{active.name}</span>
      </button>
      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: "fixed", inset: 0, zIndex: 80 }} />
          <div role="menu" style={{
            position: "absolute", right: 0, top: "calc(100% + 8px)", zIndex: 81,
            minWidth: 190, background: "var(--intra-white, var(--t-white, #FFFFFF))",
            border: `1px solid ${line}`, borderRadius: 8, padding: 6,
            boxShadow: "0 18px 44px rgba(25,34,40,.16)",
          }}>
            {apps.map((app) => {
              const isActive = app.id === current;
              return (
                <a
                  key={app.id}
                  href={app.href}
                  role="menuitem"
                  onClick={() => setOpen(false)}
                  style={{
                    display: "flex", alignItems: "center", justifyContent: "space-between",
                    gap: 10, textDecoration: "none", borderRadius: 6, padding: "9px 10px",
                    color: "var(--intra-ink, var(--t-ink, #171E22))", fontSize: 13,
                    fontWeight: isActive ? 700 : 600, background: isActive ? "rgba(174,203,212,.22)" : "transparent",
                  }}
                >
                  <span>{app.name}</span>
                  {isActive && <span aria-hidden style={{
                    width: 7, height: 7, borderRadius: 99, background: "var(--intra-accent, var(--t-teal, #227175))",
                  }} />}
                </a>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
