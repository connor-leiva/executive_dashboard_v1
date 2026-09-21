import React from "react";
import { CORE, NEUTRAL, TYPE } from "../../brand/axcion.jsx";
import { ProductIcon } from "../../brand/productIcons.jsx";
import { SITE, FEATURE_DETAIL, SOURCES, PLATFORM, TRUST } from "../content.js";
import {
  label, body, card, h1Type, h2Type, Primary, Ghost, Section, PageHead, EarlyPill, WHITE,
} from "../ui.jsx";

export default function Features() {
  return (
    <>
      <Section style={{ paddingBottom: 0 }}>
        <PageHead
          eyebrow="Features"
          title="Five modules, one set of connections"
          sub="Connect your systems once. Every module reads the same reconciled data, so production, books and campaigns cannot drift apart the way three separate reports do."
        />
      </Section>

      {/* Each module gets its own band, alternating ground so the page has a rhythm to scroll
          through rather than sixteen identical cards. */}
      {FEATURE_DETAIL.map((f, i) => (
        <Section
          key={f.name}
          id={f.name.toLowerCase()}
          style={{
            background: i % 2 ? CORE.paper : "transparent",
            borderTop: `1px solid ${NEUTRAL[100]}`,
            borderBottom: i % 2 ? `1px solid ${NEUTRAL[100]}` : "none",
            paddingTop: "clamp(44px, 6vw, 72px)",
            paddingBottom: "clamp(44px, 6vw, 72px)",
          }}
        >
          <div className="acu-feature">
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                <ProductIcon name={f.name.toLowerCase()} size={28} tone={CORE.cadet} />
                <h2 style={{ ...h1Type, fontSize: "clamp(22px, 2.6vw, 28px)" }}>{f.name}</h2>
                {f.status === "early" ? <EarlyPill /> : null}
              </div>
              <p style={{ ...body, fontSize: 16, marginTop: 14, maxWidth: 340 }}>{f.lead}</p>
            </div>

            <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 12 }}>
              {f.points.map((pt, i) => (
                <li key={pt} style={{
                  display: "grid", gridTemplateColumns: "auto 1fr", gap: 14, alignItems: "start",
                  paddingBottom: 12,
                  /* No rule under the last item — a trailing hairline reads as a row that
                     failed to load rather than as the end of the list. */
                  borderBottom: i === f.points.length - 1 ? "none" : `1px solid ${NEUTRAL[100]}`,
                }}>
                  <span aria-hidden style={{ width: 6, height: 6, borderRadius: "50%", background: CORE.cadet, marginTop: 9 }} />
                  <span style={{ ...body, fontSize: 14.5 }}>{pt}</span>
                </li>
              ))}
            </ul>
          </div>
        </Section>
      ))}

      <Section style={{ background: CORE.paper, borderTop: `1px solid ${NEUTRAL[100]}`, borderBottom: `1px solid ${NEUTRAL[100]}` }}>
        <p style={label}>Across every module</p>
        <h2 style={{ ...h1Type, marginTop: 14, maxWidth: 520 }}>The parts you only notice when they are missing</h2>
        <div className="acu-grid-4" style={{ marginTop: 40 }}>
          {PLATFORM.map((p) => (
            <div key={p.t} style={{ ...card, background: WHITE }}>
              <h3 style={{ ...h2Type, fontSize: 16 }}>{p.t}</h3>
              <p style={{ ...body, fontSize: 14, marginTop: 8 }}>{p.d}</p>
            </div>
          ))}
        </div>
      </Section>

      <Section>
        <p style={label}>Sources</p>
        <h2 style={{ ...h1Type, marginTop: 14, maxWidth: 520 }}>What it reads from</h2>
        <p style={{ ...body, marginTop: 16, maxWidth: 560 }}>
          Every connection is read-only. Axcion pulls data and never writes it back, so your
          books and your CRM stay the source of truth.
        </p>
        <div className="acu-sources" style={{ background: NEUTRAL[100], border: `1px solid ${NEUTRAL[100]}`, borderRadius: 14, overflow: "hidden", marginTop: 32 }}>
          {SOURCES.map((s) => (
            <div key={s.name} style={{ background: WHITE, padding: "20px 22px" }}>
              <div style={{ fontFamily: TYPE.text, fontWeight: 600, fontSize: 15, color: CORE.ink }}>{s.name}</div>
              <div style={{ ...body, fontSize: 13, color: NEUTRAL[500], marginTop: 4 }}>{s.detail}</div>
            </div>
          ))}
        </div>
      </Section>

      <Section id="security" style={{ background: CORE.paper, borderTop: `1px solid ${NEUTRAL[100]}` }}>
        <p style={label}>Security</p>
        <h2 style={{ ...h1Type, marginTop: 14, maxWidth: 560 }}>Connecting your books should not be a risk</h2>
        <div className="acu-grid-3" style={{ marginTop: 40 }}>
          {TRUST.map((t) => (
            <div key={t.t}>
              <div style={{ height: 2, width: 28, background: CORE.cadet, borderRadius: 2 }} />
              <h3 style={{ ...h2Type, fontSize: 16, marginTop: 14 }}>{t.t}</h3>
              <p style={{ ...body, fontSize: 14, marginTop: 8 }}>{t.d}</p>
            </div>
          ))}
        </div>
      </Section>

      <Section className="acu-inkband">
        <div style={{ textAlign: "center", maxWidth: 620, margin: "0 auto" }}>
          <h2 style={{ ...h1Type, color: WHITE }}>Start with one entity</h2>
          <p style={{ ...body, color: NEUTRAL[300], marginTop: 14 }}>
            Connect QuickBooks and one production system. You do not have to move everything at
            once, and nothing you connect can be changed by us.
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12, justifyContent: "center", marginTop: 28 }}>
            <Primary href={SITE.signupUrl} onDark>{SITE.signupLabel}</Primary>
            <Ghost href={`mailto:${SITE.contactEmail}?subject=Axcion%20demo`} onDark>Book a demo</Ghost>
          </div>
        </div>
      </Section>
    </>
  );
}
