import React from "react";
import { CORE, NEUTRAL, TYPE } from "../../brand/acumyn.jsx";
import { SITE, ABOUT } from "../content.js";
import {
  label, body, h1Type, h2Type, Primary, Ghost, Section, PageHead, Photo, PLATE, WHITE, alpha,
} from "../ui.jsx";

/* This page names a real company. Everything on it is qualitative on purpose — no production
 * figures, no headcount, no founding date, no quotes — because inventing any of those about a
 * real brokerage would be fabricating a record, and it is exactly the kind of claim someone
 * checks first. If numbers belong here, they come from the team, not from the page. */

export default function About({ Link }) {
  return (
    <>
      <Section style={{ paddingBottom: "clamp(32px, 4vw, 48px)" }}>
        <div className="acu-grid-2" style={{ alignItems: "center" }}>
          <PageHead eyebrow={ABOUT.eyebrow} title={ABOUT.headline} sub={ABOUT.standfirst} />
          {/* A plate, not a photograph — hence a caption that says what it is. The portrait
              this page actually wants is further down, and it needs a camera. No alt text: it is
              texture, and describing it to a screen reader (the old text called it "fluted
              glass", which it is not, and which is Spring's treatment) only adds noise. */}
          <Photo
            src={PLATE.detail}
            ratio="4 / 5"
            caption="Light study"
          />
        </div>
      </Section>

      {/* The story. A narrow measure and a generous leading — this is the one page on the site
          that is genuinely meant to be read rather than scanned. */}
      <Section style={{ background: CORE.paper, borderTop: `1px solid ${NEUTRAL[100]}`, borderBottom: `1px solid ${NEUTRAL[100]}` }}>
        <div style={{ display: "grid", gap: "clamp(36px, 5vw, 56px)" }}>
          {ABOUT.story.map((s, i) => (
            <div key={s.h} className="acu-story">
              <div>
                <div style={{
                  fontFamily: TYPE.data, fontWeight: 600, fontSize: 13, color: CORE.cadet,
                  fontVariantNumeric: "tabular-nums",
                }}>
                  {String(i + 1).padStart(2, "0")}
                </div>
                <h2 style={{ ...h1Type, fontSize: "clamp(21px, 2.4vw, 26px)", marginTop: 10, maxWidth: 300 }}>{s.h}</h2>
              </div>
              <p style={{ ...body, fontSize: 16, lineHeight: 1.7, maxWidth: 620 }}>{s.p}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* Principles. Each of these is enforced somewhere in the codebase — that is the reason
          they are worth printing, and the reason none of them is a slogan. */}
      {/* The one slot on the site that no amount of design can fill. */}
      <Section style={{ paddingTop: "clamp(40px, 6vw, 64px)" }}>
        <div className="acu-grid-2" style={{ alignItems: "center" }}>
          <Photo
            ratio="3 / 2"
            brief={"Still needed, now that the wide of the room is on the Home page: two or " +
                   "three closer frames \u2014 people at desks mid-week, a screen being pointed at, " +
                   "someone actually reconciling something. Available light, no studio, no " +
                   "stock-photo handshakes. These are the frames that carry the story below."}
          />
          <div>
            <p style={label}>Who this is</p>
            <h2 style={{ ...h1Type, fontSize: "clamp(22px, 2.6vw, 30px)", marginTop: 14, maxWidth: 340 }}>
              A real brokerage, on a real Monday
            </h2>
            <p style={{ ...body, marginTop: 16, maxWidth: 460 }}>
              Everything on this page happened. The reconciliation, the entities multiplying, the
              dashboard that nobody trusted &mdash; that was our own operation before it was a
              product, and it still is.
            </p>
          </div>
        </div>
      </Section>

      <Section style={{ paddingTop: "clamp(40px, 6vw, 64px)" }}>
        <p style={label}>How we build it</p>
        <h2 style={{ ...h1Type, marginTop: 14, maxWidth: 520 }}>Four rules we do not bend</h2>
        <div style={{ marginTop: 40, display: "grid", gap: 0 }}>
          {ABOUT.principles.map((p, i) => (
            <div key={p.t} style={{
              display: "grid", gridTemplateColumns: "auto 1fr", gap: "clamp(16px, 3vw, 32px)",
              padding: "24px 0",
              borderTop: `1px solid ${NEUTRAL[100]}`,
              borderBottom: i === ABOUT.principles.length - 1 ? `1px solid ${NEUTRAL[100]}` : "none",
            }}>
              {/* Numbered, not iconed. The generic glyph read as a pair of empty brackets at
                  this size, and these are a numbered set of four rules — the same device the
                  story beats above use, which ties the two halves of the page together. */}
              <span style={{
                fontFamily: TYPE.data, fontWeight: 600, fontSize: 13, color: CORE.cadet,
                fontVariantNumeric: "tabular-nums", paddingTop: 3,
              }}>
                {String(i + 1).padStart(2, "0")}
              </span>
              <div className="acu-principle">
                <h3 style={{ ...h2Type, fontSize: 18, maxWidth: 300 }}>{p.t}</h3>
                <p style={{ ...body, fontSize: 15 }}>{p.d}</p>
              </div>
            </div>
          ))}
        </div>
      </Section>

      {/* Closing note, on Ink. */}
      <Section className="acu-inkband">
        <div className="acu-grid-2">
          <div>
            <p style={{ ...label, color: NEUTRAL[200] }}>Who builds it</p>
            <h2 style={{ ...h1Type, color: WHITE, marginTop: 14, maxWidth: 380 }}>{ABOUT.closing.h}</h2>
          </div>
          <div>
            <p style={{ ...body, color: NEUTRAL[300], fontSize: 16, lineHeight: 1.7, maxWidth: 560 }}>{ABOUT.closing.p}</p>
            <div style={{
              marginTop: 28, paddingTop: 24, borderTop: `1px solid ${alpha(WHITE, 0.12)}`,
              display: "flex", flexWrap: "wrap", gap: 12,
            }}>
              <Primary href={SITE.signupUrl} onDark>{SITE.signupLabel}</Primary>
              <Ghost href={`mailto:${SITE.contactEmail}?subject=Hello`} onDark>Say hello</Ghost>
            </div>
          </div>
        </div>
      </Section>

      <Section style={{ paddingTop: "clamp(40px, 6vw, 64px)", paddingBottom: "clamp(40px, 6vw, 64px)" }}>
        <p style={{ ...body, fontSize: 15 }}>
          Curious what it actually does?{" "}
          <Link to="/features" className="acu-link" style={{ fontWeight: 600 }}>
            Read the feature detail &rarr;
          </Link>
        </p>
      </Section>
    </>
  );
}
