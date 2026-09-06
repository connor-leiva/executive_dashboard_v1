/* The signed-out surface: sign in, forgot password, accept invite, set a new password.
 *
 * One shell for all four, because they are one moment in a person's day. Somebody who mistypes
 * a password, asks for a link, opens their email and sets a new one has crossed four screens,
 * and if each was built separately they would drift — different card widths, different button
 * radii, the brokerage's logo in three sizes. The previous versions had exactly that: the login
 * card and the invite card were two independent implementations of the same 400px box.
 *
 * THE PLATE IS THE TENANT'S. Its background is the workspace's own hero image and its logo is
 * the workspace's own mark, because this page is the first thing a person sees and it should
 * say the name of the company that asked them to sign in, not ours. Acumyn's attribution sits
 * at the bottom of it, once, and is not removable.
 *
 * COLOUR COMES FROM THE PALETTE, NEVER FROM HERE. The design names #3F6B66 for the accent, which
 * is Acumyn's brand seed — so it is T.poppy, the derived action colour, and a workspace that has
 * chosen its own five colours gets its own sign-in page for free. Same for type: the design's
 * Space Grotesk / Instrument Sans / Archivo is the `acumyn` pairing, reached through the
 * --font-* variables so a workspace on a different pairing is not overridden by this file.
 */
import { useEffect, useRef, useState } from "react";

import { T, alpha } from "../theme.js";
import { getJSON, API_BASE } from "../api.js";
import { applyBrand } from "../palette.js";
import { BrandSignature, setBrand, ribbedHero } from "../Brand.jsx";
import { PoweredByAcumyn } from "../brand/PoweredBy.jsx";

const FONT = "var(--font-text)";
const HEAD = "var(--font-display)";

/* Focus rings, hovers and the spinner — the things inline styles cannot express. Scoped to
   .acu-auth so nothing here leaks into the app behind it. */
function ShellCss() {
  return (
    <style>{`
      @keyframes acu-spin { to { transform: rotate(360deg); } }
      .acu-auth ::placeholder { color: ${T.muted}; opacity: .75; }
      .acu-auth .acu-input {
        transition: border-color .15s, box-shadow .15s;
      }
      .acu-auth .acu-input:focus {
        outline: none;
        border-color: ${T.poppy};
        box-shadow: 0 0 0 3px ${alpha(T.poppy, 0.18)};
      }
      .acu-auth .acu-input[readonly]:focus { box-shadow: none; }
      .acu-auth .acu-input.acu-bad:focus { border-color: ${T.poppyText};
        box-shadow: 0 0 0 3px ${alpha(T.poppyText, 0.16)}; }
      .acu-auth .acu-primary { transition: filter .15s, transform .15s; }
      .acu-auth .acu-primary:hover:not(:disabled) { filter: brightness(1.08); transform: translateY(-1px); }
      .acu-auth .acu-primary:disabled { cursor: default; filter: brightness(.94); }
      .acu-auth .acu-quiet { transition: border-color .15s, background .15s; }
      .acu-auth .acu-quiet:hover { border-color: ${T.muted}; background: ${T.parchment}; }
      .acu-auth .acu-peek:hover { background: ${T.parchment}; color: ${T.ink}; }
      .acu-auth a:hover { text-decoration: underline; }
      .acu-auth :focus-visible { outline: 2px solid ${T.teal}; outline-offset: 2px; }
      /* The plate is decoration carrying identity, not content. Below the fold of a phone it
         costs a screenful before the form, so it becomes a band. */
      /* column, never column-reverse: on a phone the plate belongs above the form whichever
         side it takes on a desktop, because the form is what you came for. */
      @media (max-width: 860px) {
        .acu-auth { flex-direction: column !important; }
        .acu-plate { flex: none !important; min-height: 188px; padding: 24px 26px !important; }
        .acu-plate-headline { display: none; }
        .acu-panel { padding: 34px 24px 92px !important; }
      }
    `}</style>
  );
}

/* One side of the fold: the workspace's image, its mark, and Acumyn's attribution. */
function Plate({ chrome }) {
  const hero = chrome && chrome.hero_image;
  const tagline = (chrome && chrome.tagline) || "";
  return (
    <div className="acu-plate" style={{
      flex: "0 0 46%", position: "relative", overflow: "hidden", background: T.evergreen,
      display: "flex", flexDirection: "column", justifyContent: "space-between",
      padding: "54px 50px",
    }}>
      {/* The workspace's own image if it has one; otherwise the neutral ribbed ground over its
          own ink, which is a real answer for any brand rather than a placeholder for a missing
          file. */}
      <div aria-hidden style={{
        position: "absolute", inset: 0, backgroundSize: "cover", backgroundPosition: "center",
        ...(hero ? { backgroundImage: `url(${hero})` } : ribbedHero("evergreen")),
      }} />
      {/* Always, over whatever is underneath. A photo nobody vetted sits behind white text, and
          this is what keeps the logo legible on a bright one. */}
      <div aria-hidden style={{
        position: "absolute", inset: 0,
        background: `linear-gradient(180deg, ${alpha(T.evergreen, 0.30)} 0%, `
                    + `${alpha(T.evergreen, 0.62)} 62%, ${alpha(T.evergreen, 0.88)} 100%)`,
      }} />
      <div style={{ position: "relative" }}>
        <BrandSignature tone="light" height={34} />
      </div>
      <div style={{ position: "relative", display: "flex", flexDirection: "column", gap: 30 }}>
        {tagline ? (
          <h2 className="acu-plate-headline" style={{
            fontFamily: HEAD, fontWeight: 700, fontSize: 34, lineHeight: 1.12,
            letterSpacing: "-.03em", color: T.onDark, margin: 0, maxWidth: "15ch",
            textWrap: "balance",
          }}>{tagline}</h2>
        ) : null}
        {/* "light" is the treatment FOR a dark ground — the guide's white knockout, because
            below Ink 400 the two-tone mark loses its Cadet blades against the plate. */}
        <PoweredByAcumyn tone="light" align="flex-start" style={{ padding: 0 }} />
      </div>
    </div>
  );
}

/**
 * The full-height split. `title`/`sub` head the form column; `back` renders the return link
 * above them and `above` renders a mark between the two (the mail glyph on "check your email");
 * `children` is the form itself; `overlay` covers the panel entirely (the signed-in handoff),
 * which is why it is a separate slot rather than something a caller composes.
 */
export function AuthShell({ chrome, title, sub, back, above, children, overlay }) {
  // The design carried these as --acu-* variables and so do we: the button lives three
  // components away from the workspace's choice, and threading a prop through each of them
  // would mean every new control has to remember to ask.
  const side = (chrome && chrome.plate_side) === "right" ? "row-reverse" : "row";
  const radius = (chrome && chrome.button_shape) === "square" ? "10px" : "999px";
  return (
    <div className="acu-auth" style={{
      minHeight: "100vh", display: "flex", background: T.white, alignItems: "stretch",
      flexDirection: side, "--acu-btn-r": radius,
    }}>
      <ShellCss />
      <Plate chrome={chrome} />
      <div className="acu-panel" style={{
        flex: 1, position: "relative", background: T.white, display: "flex",
        alignItems: "center", justifyContent: "center", padding: "56px 60px 104px",
      }}>
        <div style={{ width: "100%", maxWidth: 372, display: "flex", flexDirection: "column" }}>
          {back}
          {above}
          <div style={{ marginBottom: 28 }}>
            <h1 style={{ fontFamily: HEAD, fontWeight: 700, fontSize: 31, letterSpacing: "-.026em",
                         color: T.ink, margin: 0 }}>{title}</h1>
            {sub ? (
              <p style={{ fontFamily: FONT, fontSize: 14.5, lineHeight: 1.5, color: T.muted,
                          margin: "10px 0 0", textWrap: "pretty" }}>{sub}</p>
            ) : null}
          </div>
          {children}
        </div>
        <div style={{
          position: "absolute", left: 0, right: 0, bottom: 34, display: "flex",
          justifyContent: "center", gap: 16, fontFamily: FONT, fontSize: 12.5, color: T.muted,
        }}>
          <a href="/privacy.html" style={{ color: T.muted, textDecoration: "none" }}>Privacy Policy</a>
          <span aria-hidden>·</span>
          <a href="/eula.html" style={{ color: T.muted, textDecoration: "none" }}>Terms</a>
        </div>
        {overlay}
      </div>
    </div>
  );
}

/* ── form primitives ──────────────────────────────────────────────── */

const inputBase = {
  height: 46, width: "100%", boxSizing: "border-box", borderRadius: 10, background: T.white,
  padding: "0 14px", fontFamily: FONT, fontSize: 14.5, color: T.ink,
};

export function Field({ id, label, invalid, hint, ...rest }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
      <label htmlFor={id} style={{ fontFamily: FONT, fontWeight: 600, fontSize: 12.5,
                                   color: T.secondary }}>{label}</label>
      <input id={id} className={`acu-input${invalid ? " acu-bad" : ""}`} {...rest}
             style={{ ...inputBase, border: `1px solid ${invalid ? T.poppyText : T.line}`,
                      // A read-only field is information, not an invitation to type. It still
                      // has to LOOK like a field — password managers ignore what they cannot
                      // see, and this one exists so the credential gets saved against a
                      // username — it just must not look answerable. Inline, because inputBase
                      // sets `background` inline and a stylesheet rule would lose to it.
                      ...(rest.readOnly
                          ? { background: T.parchment, color: T.secondary } : null) }} />
      {hint ? <div style={{ fontFamily: FONT, fontSize: 11.5, color: T.muted }}>{hint}</div> : null}
    </div>
  );
}

/** A password field with a Show/Hide control, because a person who has just been told their
 *  password is wrong needs to see what they typed. */
export function PasswordField({ id, label, invalid, ...rest }) {
  const [shown, setShown] = useState(false);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
      <label htmlFor={id} style={{ fontFamily: FONT, fontWeight: 600, fontSize: 12.5,
                                   color: T.secondary }}>{label}</label>
      <div style={{ position: "relative", display: "flex" }}>
        <input id={id} type={shown ? "text" : "password"}
               className={`acu-input${invalid ? " acu-bad" : ""}`} {...rest}
               style={{ ...inputBase, paddingRight: 66,
                        border: `1px solid ${invalid ? T.poppyText : T.line}` }} />
        <button type="button" className="acu-peek" onClick={() => setShown((v) => !v)}
                aria-label={shown ? "Hide password" : "Show password"}
                style={{ position: "absolute", right: 6, top: 6, height: 34, padding: "0 11px",
                         border: "none", background: "transparent", borderRadius: 8,
                         fontFamily: FONT, fontWeight: 600, fontSize: 12.5, color: T.muted,
                         cursor: "pointer" }}>
          {shown ? "Hide" : "Show"}
        </button>
      </div>
    </div>
  );
}

export function PrimaryButton({ busy, busyLabel, children, ...rest }) {
  return (
    <button className="acu-primary" disabled={busy} {...rest} style={{
      height: 50, marginTop: 6, border: "none", borderRadius: "var(--acu-btn-r, 999px)",
      background: T.poppy,
      color: T.white, fontFamily: FONT, fontWeight: 600, fontSize: 15, cursor: "pointer",
      display: "flex", alignItems: "center", justifyContent: "center", gap: 10,
    }}>
      {busy ? <Spinner /> : null}
      <span>{busy ? (busyLabel || children) : children}</span>
    </button>
  );
}

/** "Remember me", which is a promise rather than a preference: ticked, the token goes to
 *  localStorage and the server gives it a month; unticked, it goes to sessionStorage and dies
 *  with the tab. A workspace can hide the control entirely, and hiding it means remembered —
 *  the alternative silently shortens every session in that workspace with no way to opt out. */
export function RememberMe({ checked, onChange }) {
  return (
    <label style={{ display: "flex", alignItems: "center", gap: 9, fontFamily: FONT,
                    fontSize: 13.5, color: T.secondary, cursor: "pointer", userSelect: "none" }}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)}
             style={{ width: 16, height: 16, margin: 0, accentColor: T.poppy, cursor: "pointer" }} />
      Remember me
    </label>
  );
}

export function QuietButton({ children, ...rest }) {
  return (
    <button className="acu-quiet" {...rest} style={{
      height: 48, border: `1px solid ${T.line}`, borderRadius: "var(--acu-btn-r, 999px)",
      background: T.white,
      color: T.ink, fontFamily: FONT, fontWeight: 600, fontSize: 14.5, cursor: "pointer",
    }}>{children}</button>
  );
}

export function Spinner({ size = 15, on = "light" }) {
  const ring = on === "light" ? alpha(T.white, 0.35) : alpha(T.ink, 0.25);
  return (
    <span aria-hidden style={{
      width: size, height: size, borderRadius: 999, border: `2px solid ${ring}`,
      borderTopColor: on === "light" ? T.white : T.ink, animation: "acu-spin .7s linear infinite",
      flex: "none",
    }} />
  );
}

/** The rejected-credentials note. An icon and a sentence that says what to do next, not "Error". */
export function ErrorNote({ children }) {
  return (
    <div role="alert" style={{
      display: "flex", gap: 10, alignItems: "flex-start", padding: "13px 14px", borderRadius: 10,
      background: alpha(T.poppyText, 0.07), border: `1px solid ${alpha(T.poppyText, 0.3)}`,
      marginBottom: 20,
    }}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={T.poppyText}
           strokeWidth="2" strokeLinecap="round" aria-hidden
           style={{ flex: "none", marginTop: 1 }}>
        <circle cx="12" cy="12" r="9" /><path d="M12 7.5v5.5" /><path d="M12 16.4v.2" />
      </svg>
      <p style={{ fontFamily: FONT, fontSize: 13.5, lineHeight: 1.45, color: T.poppyText,
                  margin: 0, textWrap: "pretty" }}>{children}</p>
    </div>
  );
}

export function BackLink({ to, onClick, children }) {
  return (
    <a href={to} onClick={onClick} style={{
      display: "inline-flex", alignItems: "center", gap: 7, fontFamily: FONT, fontSize: 13,
      fontWeight: 500, color: T.muted, textDecoration: "none", marginBottom: 24,
      alignSelf: "flex-start",
    }}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M15 5 L8 12 L15 19" />
      </svg>
      {children}
    </a>
  );
}

/** A round accent-tinted badge — the mail glyph and the success tick both sit in one. */
export function Badge({ children, radius = 999, size = 54 }) {
  return (
    <div aria-hidden style={{
      width: size, height: size, borderRadius: radius, display: "flex", alignItems: "center",
      justifyContent: "center", background: alpha(T.poppy, 0.12), flex: "none",
    }}>{children}</div>
  );
}

export function Tick({ size = 24 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={T.poppy}
         strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M4 12.5 L9.5 18 L20 6.5" />
    </svg>
  );
}

/** The handoff: the panel goes solid while the dashboard loads behind it. Without it the screen
 *  sits on a spinner in a form the person has already finished with. */
export function Handoff({ title, sub }) {
  return (
    <div style={{
      position: "absolute", inset: 0, zIndex: 3, background: T.white, display: "flex",
      flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 24,
      padding: 56,
    }}>
      <Badge><Tick /></Badge>
      <div style={{ textAlign: "center" }}>
        <div style={{ fontFamily: HEAD, fontWeight: 700, fontSize: 26, letterSpacing: "-.022em",
                      color: T.ink }}>{title}</div>
        <p style={{ fontFamily: FONT, fontSize: 14.5, color: T.muted, margin: "9px 0 0" }}>{sub}</p>
      </div>
      <div style={{ width: 210, height: 3, borderRadius: 999, background: T.page,
                    overflow: "hidden" }}>
        <div style={{ width: "64%", height: "100%", background: T.poppy }} />
      </div>
    </div>
  );
}

/** The workspace's public branding, cached per host.
 *
 * /public/brand is a round-trip, so the honest first paint is Acumyn's identity and the
 * workspace's arrives a few hundred milliseconds later — which reads as the page changing its
 * mind in front of you. The last answer for THIS host is applied synchronously, so only a
 * genuinely first visit sees the swap.
 *
 * Safe to cache per host because every workspace is its own subdomain and therefore its own
 * origin: one workspace's storage is unreadable from another's, and none of it is private — it
 * is the branding painted on the page a moment later.
 */
export function useChrome() {
  const key = `acu:brand:${window.location.hostname}`;
  const [chrome, setChrome] = useState(() => {
    try {
      const raw = window.localStorage.getItem(key);
      if (!raw) return null;
      const b = JSON.parse(raw);
      applyBrand(b);
      setBrand(b);
      return b;
    } catch { return null; }            // private window, cleared storage, corrupt value
  });
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    if (!API_BASE) return undefined;
    getJSON("/public/brand")
      .then((b) => {
        if (!live.current || !b) return;
        applyBrand(b);
        setBrand(b);
        setChrome(b);
        try { window.localStorage.setItem(key, JSON.stringify(b)); } catch { /* fine */ }
      })
      .catch(() => { /* unreachable API: keep whatever is already painted */ });
    return () => { live.current = false; };
  }, []);
  return chrome;
}
