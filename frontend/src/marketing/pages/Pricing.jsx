import React, { useCallback, useEffect, useState } from "react";
import { CORE, CADET, NEUTRAL, TYPE } from "../../brand/axcion.jsx";
import { SITE, PRICING, PRICING_IS_PLACEHOLDER, FAQ } from "../content.js";
import { label, body, card, h1Type, h2Type, Primary, Ghost, Section, PageHead, WHITE } from "../ui.jsx";

export default function Pricing() {
  /* "Sign in" in the nav points at #signin — the FAQ entry explaining that each workspace has
     its own address — because there is no single sign-in host to send people to. A <details>
     cannot be opened by a CSS :target rule, so the hash is tracked here and the matching entry
     is rendered open. */
  const [hash, setHash] = useState(() => (typeof window === "undefined" ? "" : window.location.hash));
  useEffect(() => {
    const onHash = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const isOpen = useCallback((id) => Boolean(id) && hash === `#${id}`, [hash]);

  return (
    <>
      <Section style={{ paddingBottom: "clamp(28px, 4vw, 40px)" }}>
        <PageHead
          eyebrow="Pricing"
          title="Priced by how much of the portfolio you run"
          sub="One brokerage and one set of books is a different problem from six entities that have to roll up. Pricing follows that, not seat count alone."
        />
      </Section>

      <Section style={{ paddingTop: 0 }}>
        {PRICING_IS_PLACEHOLDER ? (
          /* Renders only while the figures are placeholders, so an unfinished pricing table
             cannot reach production unnoticed. Flip PRICING_IS_PLACEHOLDER in content.js. */
          <p style={{
            ...body, fontSize: 13.5, color: CADET[700], background: CADET[50],
            border: `1px solid ${CADET[200]}`, borderRadius: 10, padding: "12px 16px", marginBottom: 32,
          }}>
            <strong style={{ fontWeight: 600 }}>Placeholder pricing.</strong>{" "}
            These tiers show the layout only — the figures are not final. Set the real numbers in
            <code style={{ fontFamily: TYPE.data, fontSize: 12.5, margin: "0 4px" }}>content.js</code>
            and switch <code style={{ fontFamily: TYPE.data, fontSize: 12.5 }}>PRICING_IS_PLACEHOLDER</code> to false.
          </p>
        ) : null}

        <div className="acu-pricing">
          {PRICING.map((p) => (
            <div key={p.name} style={{
              ...card,
              background: p.featured ? CORE.ink : WHITE,
              border: p.featured ? `1px solid ${CORE.ink}` : `1px solid ${NEUTRAL[100]}`,
              /* No height:100% — align-items:stretch on .acu-pricing already equalises the
                 cards, and an explicit 100% resolves against the grid row rather than growing
                 it, so taller content overflows the track and the paragraph below rides up
                 into the cards. */
              padding: 26, display: "flex", flexDirection: "column",
            }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
                <span style={{ ...h2Type, fontSize: 17, color: p.featured ? WHITE : CORE.ink }}>{p.name}</span>
                {p.featured ? (
                  <span style={{
                    fontFamily: TYPE.text, fontWeight: 600, fontSize: 10, letterSpacing: "0.1em",
                    textTransform: "uppercase", color: CORE.ink, background: CORE.sage,
                    borderRadius: 999, padding: "3px 9px",
                  }}>Most teams</span>
                ) : null}
              </div>

              <p style={{ ...body, fontSize: 13.5, color: p.featured ? NEUTRAL[300] : NEUTRAL[600], marginTop: 8, minHeight: 40 }}>{p.blurb}</p>

              <div style={{ display: "flex", alignItems: "baseline", gap: 7, marginTop: 12, minHeight: 34 }}>
                <span style={
                  /* A price that is not a figure must not be set in the figure face. Archivo at
                     30px exists to make a number reconcilable against another system; "Talk to
                     us" is a sentence, and in tabular figures it shouted over the numbers. */
                  /^[$\d]/.test(p.price)
                    ? { fontFamily: TYPE.data, fontWeight: 600, fontSize: 30, lineHeight: 1.1,
                        fontVariantNumeric: "tabular-nums", color: p.featured ? WHITE : CORE.ink }
                    : { fontFamily: TYPE.display, fontWeight: 700, fontSize: 20, lineHeight: 1.7,
                        letterSpacing: "-0.02em", color: p.featured ? WHITE : CORE.ink }
                }>{p.price}</span>
                {p.priceNote ? (
                  <span style={{ fontFamily: TYPE.text, fontSize: 13, color: p.featured ? NEUTRAL[300] : NEUTRAL[500] }}>{p.priceNote}</span>
                ) : null}
              </div>

              <ul style={{ listStyle: "none", padding: 0, margin: "20px 0 24px", display: "grid", gap: 9 }}>
                {p.features.map((f) => (
                  <li key={f} style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: 10, alignItems: "start" }}>
                    <span aria-hidden style={{
                      width: 5, height: 5, borderRadius: "50%", marginTop: 8,
                      background: p.featured ? CORE.sage : CORE.cadet,
                    }} />
                    <span style={{ ...body, fontSize: 13.5, color: p.featured ? NEUTRAL[200] : NEUTRAL[600] }}>{f}</span>
                  </li>
                ))}
              </ul>

              <div style={{ marginTop: "auto" }}>
                {p.cta === "primary" ? (
                  <Primary href={SITE.signupUrl} block onDark={p.featured}>{SITE.signupLabel}</Primary>
                ) : (
                  <Ghost href={`mailto:${SITE.contactEmail}?subject=Axcion%20${encodeURIComponent(p.name)}`} onDark={p.featured} block>
                    Talk to us
                  </Ghost>
                )}
              </div>
            </div>
          ))}
        </div>

        <p style={{ ...body, fontSize: 13.5, color: NEUTRAL[500], marginTop: 24 }}>
          Every plan includes all source connections, drill-down on every number, and read-only
          access to your systems. We do not charge for the data you already own.
        </p>
      </Section>

      <Section style={{ background: CORE.paper, borderTop: `1px solid ${NEUTRAL[100]}` }}>
        <div className="acu-grid-2">
          <div>
            <p style={label}>Questions</p>
            <h2 style={{ ...h1Type, marginTop: 14, maxWidth: 340 }}>Answered plainly</h2>
          </div>
          <div>
            {FAQ.map((f) => (
              <details
                key={f.q}
                id={f.id}
                open={isOpen(f.id) || undefined}
                style={{ borderBottom: `1px solid ${NEUTRAL[100]}`, padding: "16px 0", scrollMarginTop: 88 }}
              >
                <summary style={{
                  fontFamily: TYPE.text, fontWeight: 600, fontSize: 15, color: CORE.ink,
                  cursor: "pointer", listStyle: "none", display: "flex",
                  justifyContent: "space-between", gap: 16, alignItems: "center",
                }}>
                  {f.q}
                  <span aria-hidden style={{ color: CORE.cadet, fontSize: 18, lineHeight: 1, flexShrink: 0 }}>+</span>
                </summary>
                <p style={{ ...body, fontSize: 14, marginTop: 10, maxWidth: 560 }}>{f.a}</p>
              </details>
            ))}
          </div>
        </div>
      </Section>

      <Section className="acu-inkband">
        <div style={{ textAlign: "center", maxWidth: 620, margin: "0 auto" }}>
          <h2 style={{ ...h1Type, color: WHITE }}>Not sure which one?</h2>
          <p style={{ ...body, color: NEUTRAL[300], marginTop: 14 }}>
            Tell us how many entities you run and which systems they sit in, and we will tell you
            which plan fits — including if the answer is that you do not need us yet.
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12, justifyContent: "center", marginTop: 28 }}>
            <Primary href={SITE.signupUrl} onDark>{SITE.signupLabel}</Primary>
            <Ghost href={`mailto:${SITE.contactEmail}?subject=Which%20plan`} onDark>Ask us</Ghost>
          </div>
        </div>
      </Section>
    </>
  );
}
