import React from "react";
import { CORE, NEUTRAL, TYPE } from "../../brand/axcion.jsx";
import { ProductIcon } from "../../brand/productIcons.jsx";
import Sequence from "../Sequence.jsx";
import { SITE, SOURCES, PROBLEM, STEPS, MODULES, TRACE } from "../content.js";
import {
  label, body, card, h1Type, h2Type,
  Primary, Ghost, Section, EarlyPill, Photo, PHOTOS, WHITE, alpha,
} from "../ui.jsx";

/* The old side-by-side hero and its <ProductStill> lived here. Both are gone: Sequence.jsx
   carries the hero and draws its own panel, and keeping a second, near-identical still around
   would have been two places to fix the same markup. Git has it if it is ever wanted back. */

export default function Home({ Link }) {
  return (
    <>
      {/* The scroll sequence IS the hero now — see Sequence.jsx. */}
      <Sequence />

      <Section style={{ paddingTop: 0 }}>
        <p style={{ ...label, marginBottom: 20 }}>Connects to</p>
        <div className="acu-sources" style={{ background: NEUTRAL[100], border: `1px solid ${NEUTRAL[100]}`, borderRadius: 14, overflow: "hidden" }}>
          {SOURCES.map((s) => (
            <div key={s.name} style={{ background: WHITE, padding: "20px 22px" }}>
              <div style={{ fontFamily: TYPE.text, fontWeight: 600, fontSize: 15, color: CORE.ink }}>{s.name}</div>
              <div style={{ ...body, fontSize: 13, color: NEUTRAL[500], marginTop: 4 }}>{s.detail}</div>
            </div>
          ))}
        </div>
      </Section>

      {/* Proof, in the position Sisu gives its logo wall. Empty until there is a photograph
          to put here, and stating its own brief rather than pretending with a grey box. */}
      <Section style={{ paddingTop: 0 }}>
        <div className="acu-grid-2" style={{ alignItems: "center" }}>
          <Photo
            src={PHOTOS.utahLifeTeam}
            alt="A team meeting at Utah Life Real Estate Group, with dashboards on the wall displays."
            ratio="3 / 2"
            caption="Utah Life Real Estate Group"
          />
          <div>
            <p style={label}>Built by operators</p>
            <h2 style={{ ...h1Type, fontSize: "clamp(22px, 2.6vw, 30px)", marginTop: 14, maxWidth: 380 }}>
              We run our own brokerage on this, every week
            </h2>
            <p style={{ ...body, marginTop: 16, maxWidth: 440 }}>
              Axcion started as an internal tool at Utah Life Real Estate Group and never stopped
              being one. It is the only product feedback loop we have ever fully trusted.
            </p>
          </div>
        </div>
      </Section>

      <Section style={{ background: CORE.paper, borderTop: `1px solid ${NEUTRAL[100]}`, borderBottom: `1px solid ${NEUTRAL[100]}` }}>
        <div className="acu-grid-2">
          <div>
            <h2 style={{ ...h1Type, maxWidth: 440 }}>{PROBLEM.heading}</h2>
            <p style={{ ...body, marginTop: 18, maxWidth: 460 }}>{PROBLEM.body}</p>
          </div>
          <div style={{ display: "grid", gap: 14 }}>
            {PROBLEM.points.map((p) => (
              <div key={p.k} style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: 14, alignItems: "start" }}>
                <span aria-hidden style={{ width: 6, height: 6, borderRadius: "50%", background: CORE.cadet, marginTop: 8 }} />
                <div>
                  <div style={{ fontFamily: TYPE.text, fontWeight: 600, fontSize: 15, color: CORE.ink }}>{p.k}</div>
                  <div style={{ ...body, fontSize: 14, marginTop: 3 }}>{p.v}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </Section>

      <Section id="how">
        <p style={label}>How it works</p>
        <h2 style={{ ...h1Type, marginTop: 14, maxWidth: 520 }}>Three steps, and the third is the one that matters</h2>
        <div className="acu-grid-3" style={{ marginTop: 40 }}>
          {STEPS.map((s) => (
            <div key={s.n} style={card}>
              <div style={{ fontFamily: TYPE.data, fontWeight: 600, fontSize: 13, color: CORE.cadet, fontVariantNumeric: "tabular-nums" }}>{s.n}</div>
              <h3 style={{ ...h2Type, marginTop: 12 }}>{s.t}</h3>
              <p style={{ ...body, fontSize: 14, marginTop: 10 }}>{s.d}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* Lineage — the dark band. §04: on Ink the mark reverses to Sage blades. */}
      <Section className="acu-inkband">
        <div className="acu-grid-2">
          <div>
            {/* Ink 200, not Sage: §06 lists "Sage / Haze as text — Never", and that is a rule
                about text rather than about contrast (Sage on Ink is a comfortable 7.32:1).
                Sage stays where §04 puts it — the mark on a dark ground — and on fills. */}
            <p style={{ ...label, color: NEUTRAL[200] }}>{TRACE.eyebrow}</p>
            <h2 style={{ ...h1Type, color: WHITE, marginTop: 14, maxWidth: 420 }}>{TRACE.heading}</h2>
            <p style={{ ...body, color: NEUTRAL[300], marginTop: 18, maxWidth: 470 }}>{TRACE.body}</p>
          </div>

          <div style={{ background: alpha(WHITE, 0.04), border: `1px solid ${alpha(WHITE, 0.12)}`, borderRadius: 14, padding: 22 }}>
            <div style={{ ...label, color: NEUTRAL[200] }}>{TRACE.example.metric}</div>
            <div style={{
              fontFamily: TYPE.data, fontWeight: 600, fontSize: 34, lineHeight: 1.1,
              fontVariantNumeric: "tabular-nums", color: WHITE, marginTop: 8,
            }}>{TRACE.example.value}</div>
            <p style={{ ...body, fontSize: 13.5, color: NEUTRAL[300], marginTop: 12, paddingBottom: 16, borderBottom: `1px solid ${alpha(WHITE, 0.12)}` }}>
              {TRACE.example.computed}
            </p>
            <div style={{ marginTop: 4 }}>
              {TRACE.example.rows.map((r) => (
                <div key={r[0]} style={{
                  display: "grid", gridTemplateColumns: "1fr auto", gap: 12,
                  padding: "11px 0", borderBottom: `1px solid ${alpha(WHITE, 0.07)}`,
                }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontFamily: TYPE.data, fontWeight: 500, fontSize: 12, color: NEUTRAL[300], fontVariantNumeric: "tabular-nums" }}>{r[0]}</div>
                    <div style={{ fontFamily: TYPE.text, fontSize: 13.5, color: WHITE, marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r[1]}</div>
                  </div>
                  <div style={{ textAlign: "right" }}>
                    <div style={{ fontFamily: TYPE.data, fontWeight: 500, fontSize: 13.5, color: WHITE, fontVariantNumeric: "tabular-nums" }}>{r[2]}</div>
                    <div style={{ fontFamily: TYPE.text, fontSize: 11.5, color: NEUTRAL[300], marginTop: 2 }}>{r[3]}</div>
                  </div>
                </div>
              ))}
            </div>
            <p style={{ ...label, color: NEUTRAL[300], marginTop: 14, fontSize: 10 }}>Illustrative data</p>
          </div>
        </div>
      </Section>

      <Section style={{ background: CORE.paper, borderTop: `1px solid ${NEUTRAL[100]}`, borderBottom: `1px solid ${NEUTRAL[100]}` }}>
        <p style={label}>The product</p>
        <h2 style={{ ...h1Type, marginTop: 14, maxWidth: 560 }}>Five modules on one set of connections</h2>
        <p style={{ ...body, marginTop: 16, maxWidth: 560 }}>
          Connect your systems once. Each module reads the same reconciled data, so production,
          books and campaigns cannot drift apart the way three separate reports do.
        </p>
        <div className="acu-modules" style={{ marginTop: 40 }}>
          {MODULES.map((m) => (
            <div key={m.name} style={{ ...card, background: WHITE, display: "flex", flexDirection: "column" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                {/* Not the mark: §05 forbids it as a repeating element, and one per card
                    across five cards is exactly that. See brand/productIcons.jsx. */}
                <ProductIcon name={m.name.toLowerCase()} size={24} tone={CORE.cadet} />
                <span style={{ ...h2Type, fontSize: 17 }}>{m.name}</span>
                {m.status === "early" ? <EarlyPill /> : null}
              </div>
              <p style={{ ...body, fontSize: 14, marginTop: 12 }}>{m.d}</p>
            </div>
          ))}
        </div>
        <div style={{ marginTop: 32 }}>
          <Link to="/features" className="acu-link" style={{ fontFamily: TYPE.text, fontWeight: 600, fontSize: 15 }}>
            See what each one does &rarr;
          </Link>
        </div>
      </Section>

      <Section className="acu-inkband">
        <div style={{ textAlign: "center", maxWidth: 620, margin: "0 auto" }}>
          <h2 style={{ ...h1Type, color: WHITE }}>See your own numbers in one view</h2>
          <p style={{ ...body, color: NEUTRAL[300], marginTop: 14 }}>
            Connect QuickBooks and one production system, and the first reconciled view is
            usually ready the same day.
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
