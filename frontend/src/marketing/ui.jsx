import React from "react";
import { CORE, CADET, NEUTRAL, TYPE } from "../brand/axcion.jsx";
import plateLight from "./assets/aperture-light.jpg";
import plateInk from "./assets/aperture-ink.jpg";
import plateDetail from "./assets/aperture-detail.jpg";
import utahLifeTeam from "./assets/utah-life-team.jpg";

/* Shared primitives for the marketing site. Extracted when the single landing page became four
 * pages: Home, Features, About and Pricing each need the same type scale, the same one Cadet
 * button and the same section rhythm, and three copies of a scale is how two of them drift.
 *
 * House style, matching the rest of frontend/src: inline style objects, module-level consts for
 * shared fragments, and one <style> block per document for what inline styles cannot express.
 * No CSS files, no styling dependencies.
 *
 * The palette, type families and mark all come from ../brand/axcion.jsx, which the product's
 * "Powered by Axcion" also draws from. This site shipped with its own copy of the tokens and the
 * mark geometry; two copies of a mark are two marks the day one of them is corrected.
 *
 * A note on the muted grey. The identity guide's product spread sets small labels in a very
 * light neutral, which is fine inside an authenticated app on a good screen. On a public page it
 * fails WCAG AA: Ink 400 on Paper is 3.04:1 against a 4.5:1 requirement, and Ink 300 is 1.84:1.
 * Muted text is therefore Ink 500 (5.30:1 on Paper, 6.06:1 on white) and Ink 300 on Ink
 * (7.90:1). The palette is unchanged — only which step carries text.
 */

/* MARKETING AESTHETIC — a deliberate, scoped departure from the identity guide.
 *
 * The guide sets 70% neutral / 25% Ink / 5% Cadet and "one Cadet element per view". That is
 * right for the PRODUCT, which is a data surface people stare at all day. It is wrong for a
 * marketing page competing for attention against ClickUp and Sisu, and Connor made the call
 * to relax it here (31 Aug 2026). What changed, and what did not:
 *
 *   Display type is far larger (up to ~86px) with tighter tracking, and headlines use the
 *   two-tone device — claim in Ink, qualifier dropped to Ink 500.
 *   Primary buttons are INK, not Cadet, matching ClickUp's black pill.
 *   Cadet is therefore freed from being "the button colour" and given a better job: it marks
 *   LIVE DATA. On these pages a Cadet element means "this number is real and traceable",
 *   which is the product's whole argument doing work as a colour rule.
 *
 * Unchanged: the palette itself, the three typefaces, and every rule about the mark.
 * The product surfaces still follow the guide exactly — this applies to marketing/ only.
 */
/* White is not one of the guide's five core colours; it is the page ground here. */
export const WHITE = "#FFFFFF";

/* A token (or any #rrggbb) as rgba(), so translucent overlays derive from the palette instead of
   hardcoding triples. axcion.jsx has no equivalent, and only this site needs one. */
export const alpha = (hex, a) => {
  const h = String(hex).replace("#", "");
  const n = parseInt(h.length === 3 ? h.replace(/./g, "$&$&") : h, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
};

/* ── Imagery ───────────────────────────────────────────────────────────────────────────
 *
 * PLATES are generated abstract light studies in ./assets. Out-of-focus highlights on
 * them are curved triangles because that is what a THREE-BLADED iris renders, and §01 defines
 * the mark as exactly that — "three blades opening on a fixed point: an aperture". The
 * texture is derived from Axcion's own mark rather than borrowed from anyone.
 *
 * Deliberately not the fluted-glass treatment in Spring's brand photos. That is Spring's
 * visual identity; Axcion is a different brand, and adopting it would repeat the mistake of
 * treating tenant #1's look as the platform's.
 *
 * They are grounds and texture. They are NOT photographs: never caption one as a place, a
 * person or an event, and never let one stand in for the customer photography the site
 * actually needs. GENERATOR.py beside them rebuilds the set deterministically.
 *
 * They are IMPORTED from ./assets rather than served from frontend/public, for two reasons.
 * Only what a page imports is bundled, so none of it rides along in the dashboard's build. And
 * this entry needs no public directory at all — which matters, because frontend/public holds
 * Spring's /brand/photos and other dashboard assets (a different company, a different product,
 * and one identifiable person). Nothing in it may appear on an Axcion surface.
 *
 * PHOTO is the slot for real photography when it exists. It fixes the aspect ratio, applies
 * a consistent treatment and reserves the caption, so a shoot can be dropped in without
 * redesigning anything. Until then it renders its own brief.
 */
/* Real photography. One frame so far — a team meeting at Utah Life Real Estate Group, the
   brokerage this was built inside. Graded lightly toward the palette (warm floor pulled back,
   shadows cooled toward Ink) and nothing else: the entire value of the frame is that it looks
   like what it is rather than like stock. The wall displays are illegible at any crop, checked
   at 4x, so no customer data is on show. */
export const PHOTOS = {
  utahLifeTeam,
};

/* aperture-paper.jpg is part of the generated set but no page uses it, so it is not imported
   and does not ship. */
export const PLATE = {
  light: plateLight,
  ink: plateInk,
  detail: plateDetail,
};

export const MAXW = 1120;
export const wrap = { maxWidth: MAXW, margin: "0 auto", padding: "0 24px" };

export const label = {
  fontFamily: TYPE.text, fontWeight: 600, fontSize: 11, letterSpacing: "0.14em",
  textTransform: "uppercase", color: NEUTRAL[500], margin: 0,
};
export const body = { fontFamily: TYPE.text, fontWeight: 400, fontSize: 15, lineHeight: 1.6, color: NEUTRAL[600], margin: 0 };
export const card = { background: WHITE, border: `1px solid ${NEUTRAL[100]}`, borderRadius: 14, padding: 24 };

/* §07 steps, clamped so they hold at desktop and degrade on a phone. Tracking follows the
   guide: -0.035em above 40px, -0.02em from 24px up. */
export const displayType = {
  fontFamily: TYPE.display, fontWeight: 700, fontSize: "clamp(38px, 6.6vw, 74px)",
  lineHeight: 1.02, letterSpacing: "-0.042em", color: CORE.ink, margin: 0,
};
export const h1Type = {
  fontFamily: TYPE.display, fontWeight: 700, fontSize: "clamp(24px, 3.2vw, 34px)",
  lineHeight: 1.15, letterSpacing: "-0.02em", color: CORE.ink, margin: 0,
};
export const h2Type = {
  fontFamily: TYPE.display, fontWeight: 700, fontSize: 20, lineHeight: 1.2,
  letterSpacing: "-0.02em", color: CORE.ink, margin: 0,
};

/* The single Cadet action. §06: one primary action per screen — read here as one per section,
   since a page this long has more than one place a reader can decide. Everything else is a
   ghost or a text link, so Cadet stays scarce and keeps meaning "the thing to do". */
export function Primary({ href, onClick, children, block = false, onDark = false }) {
  /* The primary is Ink, so on an Ink section it has to invert or it disappears into the
     ground. White-on-Ink, which is also what ClickUp does in their dark bands. */
  return (
    <a
      href={href} onClick={onClick}
      className={`acu-btn ${onDark ? "acu-btn-primary-dark" : "acu-btn-primary"}`}
      style={{
        display: block ? "flex" : "inline-flex", alignItems: "center", justifyContent: "center",
        fontFamily: TYPE.text, fontWeight: 600, fontSize: 15, textDecoration: "none",
        color: onDark ? CORE.ink : WHITE, background: onDark ? WHITE : CORE.ink,
        border: `1px solid ${onDark ? WHITE : CORE.ink}`,
        borderRadius: 999, padding: "14px 28px", cursor: "pointer",
      }}
    >
      {children}
    </a>
  );
}

export function Ghost({ href, onClick, children, onDark = false, block = false }) {
  return (
    <a
      href={href} onClick={onClick} className="acu-btn acu-btn-ghost"
      style={{
        display: block ? "flex" : "inline-flex", alignItems: "center", justifyContent: "center",
        fontFamily: TYPE.text, fontWeight: 600, fontSize: 15, textDecoration: "none",
        color: onDark ? WHITE : CORE.ink, background: "transparent",
        border: `1px solid ${onDark ? alpha(WHITE, 0.3) : NEUTRAL[200]}`,
        borderRadius: 999, padding: "14px 28px", cursor: "pointer",
      }}
    >
      {children}
    </a>
  );
}

export function Section({ id, children, style, className }) {
  return (
    <section id={id} className={className} style={{ padding: "clamp(56px, 8vw, 96px) 0", ...style }}>
      <div style={wrap}>{children}</div>
    </section>
  );
}

/* A page heading block, so the four pages open the same way. */
export function PageHead({ eyebrow, title, sub, children }) {
  return (
    <div style={{ maxWidth: 720 }}>
      <p style={{ ...label, color: CORE.cadet }}>{eyebrow}</p>
      <h1 style={{ ...displayType, marginTop: 18 }}>{title}</h1>
      {sub ? <p style={{ ...body, fontSize: 17, marginTop: 20, maxWidth: 620 }}>{sub}</p> : null}
      {children}
    </div>
  );
}

/**
 * A photography slot.
 *
 * Pass `src` and it renders the photograph with the house treatment: fixed ratio, Ink scrim
 * from the foot so overlaid type stays legible, subtle grain so a photo does not sit on the
 * page looking cleaner than everything around it, and a caption rail.
 *
 * Pass no `src` and it renders the BRIEF for that slot instead — ratio, subject, and what the
 * shot has to do. That is deliberate: an empty slot that states its own requirement is more
 * useful than a grey box, and it cannot be mistaken for finished work.
 */
export function Photo({ src, alt, brief, ratio = "4 / 3", caption, overlay = null, rounded = 14 }) {
  return (
    <figure style={{ margin: 0 }}>
      <div style={{
        position: "relative", aspectRatio: ratio, borderRadius: rounded, overflow: "hidden",
        background: src ? NEUTRAL[100] : "transparent",
        border: src ? "none" : `1px dashed ${NEUTRAL[300]}`,
      }}>
        {src ? (
          <>
            <img src={src} alt={alt || ""} loading="lazy"
              style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }} />
            {overlay ? (
              <div style={{
                position: "absolute", inset: 0, display: "flex", alignItems: "flex-end", padding: 22,
                background: `linear-gradient(to top, ${alpha(CORE.ink, .82)} 0%, ${alpha(CORE.ink, .35)} 38%, ${alpha(CORE.ink, 0)} 70%)`,
              }}>
                {overlay}
              </div>
            ) : null}
          </>
        ) : (
          <div style={{
            position: "absolute", inset: 0, padding: 22, display: "flex",
            flexDirection: "column", justifyContent: "space-between",
          }}>
            <span style={{ ...label, color: CORE.cadet }}>Photography &mdash; to shoot</span>
            <span style={{ ...body, fontSize: 14, color: NEUTRAL[600], maxWidth: "40ch" }}>{brief}</span>
          </div>
        )}
      </div>
      {caption ? (
        <figcaption style={{ ...label, marginTop: 10, color: NEUTRAL[500] }}>{caption}</figcaption>
      ) : null}
    </figure>
  );
}

/* Marks a capability that is implemented but gated off in config.py, so the page can describe
   it without implying it ships today. */
export function EarlyPill() {
  return (
    <span style={{
      fontFamily: TYPE.text, fontWeight: 600, fontSize: 10, letterSpacing: "0.1em",
      textTransform: "uppercase", color: CADET[700], background: CADET[100],
      border: `1px solid ${CADET[200]}`, borderRadius: 999, padding: "3px 9px",
    }}>
      Early access
    </span>
  );
}

/* One <style> element for the whole site. Rules are `acu-` prefixed apart from three
   deliberately global ones (scroll-behavior, the reduced-motion override and the <summary>
   focus ring); this component owns the entire document on its host, so that is safe here and
   would not be inside the tenant app. */
export function Styles() {
  return (
    <>
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden>
      <filter id="acu-grain-f">
        <feTurbulence type="fractalNoise" baseFrequency=".8" numOctaves="3" stitchTiles="stitch" />
        <feColorMatrix type="saturate" values="0" />
      </filter>
    </svg>
    <style>{`
      .acu-btn { transition: background .15s ease, border-color .15s ease, color .15s ease; }
      .acu-btn-primary:hover { background: ${NEUTRAL[800]}; border-color: ${NEUTRAL[800]}; }
      .acu-btn-primary-dark:hover { background: ${NEUTRAL[100]}; border-color: ${NEUTRAL[100]}; }
      .acu-btn-ghost:hover { border-color: ${NEUTRAL[400]}; background: ${alpha(CORE.ink, 0.04)}; }
      .acu-navlink:hover { color: ${CORE.cadet}; }
      .acu-link { color: ${CORE.cadet}; text-decoration: none; }
      .acu-link:hover { text-decoration: underline; }

      /* The guide defines no focus token, so this uses Cadet — the action colour — at 2px offset. */
      .acu-btn:focus-visible, .acu-navlink:focus-visible, .acu-link:focus-visible,
      .acu-burger:focus-visible, summary:focus-visible {
        outline: 2px solid ${CORE.cadet}; outline-offset: 2px; border-radius: 8px;
      }
      .acu-field:focus { outline: 2px solid ${CORE.cadet}; outline-offset: 1px; border-color: ${CORE.cadet}; }
      .acu-field::placeholder { color: ${NEUTRAL[500]}; }
      .acu-btn:disabled { cursor: progress; }

      .acu-nav { display: flex; align-items: center; gap: 26px; }
      .acu-navcta { display: flex; }
      .acu-burger { display: none; }
      .acu-grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; }
      .acu-grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; }
      /* The About principles: a narrow title column, so the rule and its explanation stay in
         one another's company instead of drifting to opposite edges of a 1120px page. */
      .acu-principle { display: grid; grid-template-columns: minmax(0, 300px) minmax(0, 1fr); gap: clamp(16px, 4vw, 48px); align-items: baseline; }
      .acu-grid-2 { display: grid; grid-template-columns: 1.05fr .95fr; gap: 48px; align-items: start; }
      .acu-sources { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; }
      .acu-modules { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; }
      .acu-pricing { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; align-items: stretch; }
      .acu-feature { display: grid; grid-template-columns: .8fr 1.2fr; gap: 40px; align-items: start; }
      .acu-story { display: grid; grid-template-columns: .8fr 1.2fr; gap: 40px; align-items: start; }

      @media (max-width: 960px) {
        .acu-nav, .acu-navcta { display: none; }
        .acu-burger { display: inline-flex; }
        .acu-grid-2, .acu-feature, .acu-story { grid-template-columns: 1fr; gap: 32px; }
        .acu-grid-3, .acu-grid-4, .acu-modules { grid-template-columns: 1fr 1fr; }
        .acu-principle { grid-template-columns: 1fr; gap: 8px; }
        /* Three tiers into two columns leaves an orphan; go straight to one and cap the width. */
        .acu-pricing { grid-template-columns: 1fr; max-width: 460px; }
      }
      @media (max-width: 640px) {
        .acu-grid-3, .acu-grid-4, .acu-modules, .acu-sources { grid-template-columns: 1fr; }
      }
      @media (prefers-reduced-motion: reduce) {
        * { animation: none !important; transition: none !important; scroll-behavior: auto !important; }
      }
      html { scroll-behavior: smooth; }

      /* ── scroll sequence ─────────────────────────────────────────────────────────── */
      .acu-track { position: relative; height: 360vh; }
      .acu-stage { position: sticky; top: 0; height: 100vh; overflow: hidden;
        display: grid; place-items: center; background: ${WHITE}; }

      /* The Ink bands take a real plate rather than flat colour, with the fill blended so
         type keeps its contrast. Checked: body text on the darkest point of the plate is
         still above 7:1. */
      .acu-inkband { background-color: ${CORE.ink};
        background-image: linear-gradient(${alpha(CORE.ink, .78)}, ${alpha(CORE.ink, .88)}), url("${PLATE.ink}");
        background-size: cover; background-position: 42% center; }

      .acu-bloom { position: absolute; left: 50%; top: 50%; width: min(1000px, 96vw);
        height: min(760px, 84vh); transform: translate(-50%,-50%); pointer-events: none;
        z-index: 0; opacity: 0;
        /* A real plate now, masked to a soft circle so it dissolves into the white ground
           instead of ending at a rectangle. The radial gradient underneath keeps the colour
           if the image has not decoded yet. */
        background-image: url("${PLATE.light}");
        background-size: cover; background-position: center;
        -webkit-mask-image: radial-gradient(closest-side circle at 50% 50%,
          #000 0%, rgba(0,0,0,.85) 42%, rgba(0,0,0,.28) 64%, transparent 80%);
        mask-image: radial-gradient(closest-side circle at 50% 50%,
          #000 0%, rgba(0,0,0,.85) 42%, rgba(0,0,0,.28) 64%, transparent 80%); }

      /* z-index 1 — above the bloom, BELOW the scene. mix-blend-mode blends with what is
         painted beneath it, and .acu-scene is its own compositing layer; blending across
         that edge draws a hard rectangular seam. Grain belongs on the ground. */
      .acu-grain { position: absolute; inset: 0; z-index: 1; pointer-events: none;
        opacity: .5; mix-blend-mode: overlay; }

      .acu-seq-copy { position: absolute; top: 14vh; left: 0; right: 0; z-index: 3;
        text-align: center; padding: 0 28px; will-change: transform, opacity; }
      .acu-seq-copy .acu-display { max-width: 16ch; margin: 14px auto 0; }
      .acu-seq-sub { font-family: ${TYPE.text}; font-size: clamp(16px,1.5vw,19px);
        color: ${NEUTRAL[500]}; max-width: 52ch; margin: 22px auto 0; }
      .acu-seq-cta { display: flex; flex-wrap: wrap; align-items: center;
        justify-content: center; gap: 20px; margin-top: 30px; }
      .acu-seq-support { font-family: ${TYPE.text}; font-size: 14px; color: ${NEUTRAL[500]};
        line-height: 1.35; margin: 0; max-width: 22ch; text-align: left; }
      .acu-btn-ink { display: inline-flex; align-items: center; justify-content: center;
        font-family: ${TYPE.text}; font-weight: 600; font-size: 16px; text-decoration: none;
        color: ${WHITE}; background: ${CORE.ink}; border-radius: 999px; padding: 15px 30px;
        transition: transform .2s, opacity .2s; }
      .acu-btn-ink:hover { transform: translateY(-1px); opacity: .9; }
      .acu-btn-ink:focus-visible { outline: 2px solid ${CORE.cadet}; outline-offset: 3px; }

      .acu-display { font-family: ${TYPE.display}; font-weight: 700; color: ${CORE.ink};
        font-size: clamp(40px,7.4vw,86px); line-height: 1.02; letter-spacing: -.042em;
        margin: 0; text-wrap: balance; }
      .acu-eyebrow { font-family: ${TYPE.data}; font-size: 11px; font-weight: 600;
        letter-spacing: .16em; text-transform: uppercase; color: ${NEUTRAL[500]}; margin: 0; }

      .acu-scene { position: relative; z-index: 2; width: min(940px, 90vw);
        will-change: transform; }
      /* Chrome on a ::before layer so fading it never fades the figure inside it. */
      .acu-panel { position: relative; border-radius: 18px; --panel-a: 0; }
      .acu-panel::before { content: ""; position: absolute; inset: 0; border-radius: 18px;
        background: ${WHITE}; border: 1px solid ${NEUTRAL[200]};
        box-shadow: 0 2px 4px ${alpha(CORE.ink,.05)}, 0 40px 80px -44px ${alpha("#1F3A36",.55)};
        opacity: var(--panel-a); z-index: 0; }
      .acu-panel > * { position: relative; z-index: 1; }

      .acu-p-top { display: flex; align-items: center; gap: 16px; padding: 14px 18px;
        border-bottom: 1px solid ${NEUTRAL[100]}; background: ${NEUTRAL[50]};
        border-radius: 18px 18px 0 0; }
      .acu-p-tabs { display: flex; gap: 18px; font-family: ${TYPE.text}; font-size: 12.5px;
        color: ${NEUTRAL[500]}; }
      .acu-p-tabs b { color: ${CORE.ink}; font-weight: 600; }
      .acu-p-cta { margin-left: auto; font-family: ${TYPE.text}; font-size: 11.5px;
        font-weight: 600; color: ${WHITE}; background: ${CORE.ink}; border-radius: 999px;
        padding: 6px 14px; white-space: nowrap; }
      /* NO background on the body: an opaque one here sits over the bloom from the first
         frame and cuts a hard horizontal seam across it. */
      .acu-p-body { padding: 18px; display: grid; gap: 14px; }

      .acu-kpis { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
      .acu-kpi { border: 1px solid ${NEUTRAL[100]}; border-radius: 12px; padding: 16px;
        background: ${NEUTRAL[50]}; will-change: transform, opacity; }
      .acu-kpi-d { font-family: ${TYPE.text}; font-size: 11.5px; color: ${NEUTRAL[500]}; margin-top: 5px; }
      .acu-hero-kpi { position: relative; border: none; background: transparent; --kpi-a: 0; }
      .acu-hero-kpi::before { content: ""; position: absolute; inset: 0; border-radius: 12px;
        background: ${WHITE}; border: 1px solid ${CADET[200]}; opacity: var(--kpi-a); z-index: 0; }
      .acu-hero-kpi > * { position: relative; z-index: 1; }
      .acu-fly { color: ${CORE.cadet}; transform-origin: center center; display: inline-block;
        position: relative; z-index: 7; will-change: transform; }

      .acu-rows { position: relative; border-radius: 12px; overflow: hidden; --rows-a: 0; }
      .acu-rows::before { content: ""; position: absolute; inset: 0; border-radius: 12px;
        border: 1px solid ${NEUTRAL[100]}; opacity: var(--rows-a); pointer-events: none; z-index: 2; }
      .acu-row { display: grid; grid-template-columns: 1.4fr .8fr 1fr .8fr; gap: 10px;
        padding: 11px 16px; border-top: 1px solid ${NEUTRAL[100]}; font-family: ${TYPE.text};
        font-size: 12.5px; will-change: transform, opacity; }
      .acu-row:first-child { border-top: none; background: ${NEUTRAL[50]}; }
      .acu-row-n { font-family: ${TYPE.data}; font-variant-numeric: tabular-nums;
        color: ${NEUTRAL[700]}; text-align: right; }
      .acu-row-s { color: ${NEUTRAL[500]}; text-align: right; font-size: 11.5px; }

      .acu-anno { position: absolute; z-index: 6; pointer-events: none; opacity: 0;
        will-change: opacity; }
      .acu-anno-lbl { font-family: ${TYPE.data}; font-size: 10.5px; font-weight: 600;
        letter-spacing: .15em; text-transform: uppercase; color: ${CADET[700]};
        white-space: nowrap; opacity: 0; }
      .acu-anno svg { overflow: visible; display: block; }
      .acu-anno path { stroke: ${CORE.cadet}; stroke-width: 1; fill: none; }

      .acu-scroll-hint { position: absolute; bottom: 26px; left: 50%;
        transform: translateX(-50%); z-index: 6; font-family: ${TYPE.data}; font-size: 10.5px;
        font-weight: 600; letter-spacing: .16em; text-transform: uppercase; color: ${NEUTRAL[500]}; }

      @media (max-width: 820px) {
        .acu-track { height: 300vh; }
        /* The illustrated app bar's button does not fit beside the tabs on a phone, and the
           stage clips rather than scrolls — so it would be cut off at the edge, not wrapped. */
        .acu-p-cta { display: none; }
        .acu-kpis { grid-template-columns: 1fr; }
        .acu-row { grid-template-columns: 1.4fr 1fr; }
        .acu-row-s, .acu-row > span:nth-child(4) { display: none; }
        .acu-anno { display: none; }
      }
      @media (prefers-reduced-motion: reduce) {
        .acu-track { height: auto; }
        .acu-stage { position: relative; height: auto; padding: 80px 0; }
        .acu-seq-copy { position: relative; top: auto; opacity: 1 !important;
          transform: none !important; margin-bottom: 56px; }
        .acu-fly { transform: none !important; }
        .acu-scene { transform: none !important; }
        .acu-panel { --panel-a: 1 !important; }
        .acu-hero-kpi { --kpi-a: 1 !important; }
        .acu-rows { --rows-a: 1 !important; }
        .acu-p-top, [data-tilepart], .acu-kpi, .acu-row { opacity: 1 !important;
          transform: none !important; }
        .acu-anno, .acu-anno-lbl { opacity: 1 !important; }
        .acu-anno path { stroke-dashoffset: 0 !important; }
        .acu-bloom { opacity: .5 !important; }
        .acu-scroll-hint { display: none; }
      }
    `}</style>
    </>
  );
}
