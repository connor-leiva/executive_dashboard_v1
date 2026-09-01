/* The ladder. SPEC-ads-module.md Part 13.4. The centrepiece of the tab.
 *
 * WHY THE BAR IS A PERCENTAGE, NOT A COUNT. The rungs span five orders of magnitude - millions of
 * impressions down to tens of enrollments. Absolute-width bars make everything below impressions
 * invisible; log-scaled bars understate the drop while looking like a gentle taper. So each bar
 * shows CONVERSION FROM THE PRIOR RUNG, 0 to 100. That is the number you scan for the leak, it is
 * readable at every scale, and it is honest. Counts sit beside it as figures.
 *
 * THE CROSSING. Rungs above `registered` are Meta measuring itself; everything below is Acumyn's
 * own record. That separation is the module's entire thesis, so it is rendered as a crossing
 * rather than a rule.
 */
import { C, FIG, FONT, HEAD, band, compact, num, pct, usd } from "./adsTokens.js";

export default function Funnel({ rungs, spend, onDrill }) {
  if (!rungs || !rungs.length) return null;

  // The worst-converting rung among those that CAN leak - the Meta rungs and the crossing are
  // excluded, because a conversion there is not a leak in the same sense.
  /* A rung that reads zero while a LATER rung reads more than zero is not a leak - it is a
     stage nobody is recording. Real funnels do not refill. On live data `Applied` sits at 0 with
     21 held calls beneath it, because its source field is false on every record in the system,
     and this component confidently called that the biggest leak in the business: "41 of 41 drop
     here", about a step that had never been recorded at all. Excluded from the running rather
     than hidden, and the drill on that rung explains which kind of zero it is. */
  const unrecorded = (r, i) => r.n === 0 && rungs.slice(i + 1).some(
    (later) => later.zone === "acumyn" && (later.n || 0) > 0);

  const leakCandidates = rungs.filter(
    (r, i) => i > 0 && r.zone === "acumyn" && rungs[i - 1].zone === "acumyn"
              && r.conversion !== null && r.conversion !== undefined
              && !unrecorded(r, i));
  const worst = leakCandidates.length
    ? leakCandidates.reduce((a, b) => (a.conversion <= b.conversion ? a : b))
    : null;

  return (
    <div style={{ background: C.surface, border: `1px solid ${C.line}`, borderRadius: 14,
                  padding: 20 }}>
      <style>{`
        .fn-bar { transition: width .2s ease; }
        @media (prefers-reduced-motion: reduce) { .fn-bar { transition: none; } }
        .fn-row { display: grid; grid-template-columns: 22px minmax(0,1fr); gap: 12px; }
        .fn-figs { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 8px; }
        .fn-figs.has-value { grid-template-columns: repeat(4, minmax(0,1fr)); }
        .fn-open:hover, .fn-open:focus-visible { text-decoration-color: ${C.accent}; }
        .fn-open:focus-visible { outline: 2px solid ${C.accent}; outline-offset: 3px; }
        @media (max-width: 560px) {
          .fn-figs, .fn-figs.has-value { grid-template-columns: repeat(2, minmax(0,1fr)); }
        }
      `}</style>

      {rungs.map((r, i) => {
        const prevRung = i ? rungs[i - 1] : null;
        const zoneChanged = prevRung && prevRung.zone !== r.zone;

        /* THE HEADCOUNT IS WITHHELD ACROSS TWO BOUNDARIES, and this is a correctness rule
           rather than a styling choice:

           - Between impression, click and lead, because an impression is not a person who left.
           - Between lead and registered, because that is a change of MEASUREMENT SYSTEM rather
             than a step. Meta's lead count and Acumyn's matched registrations count overlapping
             populations, and a large part of the gap is people who did register and could not be
             matched - stripped UTM, cross-device, view-through. Asserting they "left" is the same
             class of lie as an absolute-width bar.

           The conversion percentage still renders on those boundaries. Only the headcount claim
           is withheld. Guarded on the zone of BOTH rungs, not just this one. */
        const drop = prevRung && prevRung.zone === "acumyn" && r.zone === "acumyn"
          ? (r.prev ?? 0) - r.n
          : null;

        const isWorst = worst && worst.key === r.key;
        const barPct = r.conversion === null || r.conversion === undefined
          ? null : Math.max(0, Math.min(100, r.conversion));

        return (
          <div key={r.key}>
            {zoneChanged && (
              <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "16px 0 12px" }}>
                <span style={{ flex: 1, height: 1, background: C.line }} />
                <span style={{ fontFamily: FONT, fontSize: 10.5, fontWeight: 600,
                               letterSpacing: ".08em", textTransform: "uppercase",
                               color: C.muted }}>
                  In Acumyn
                </span>
                <span style={{ flex: 1, height: 1, background: C.line }} />
              </div>
            )}
            {i === 0 && (
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
                <span style={{ flex: 1, height: 1, background: C.line }} />
                <span style={{ fontFamily: FONT, fontSize: 10.5, fontWeight: 600,
                               letterSpacing: ".08em", textTransform: "uppercase",
                               color: C.muted }}>
                  On Meta
                </span>
                <span style={{ flex: 1, height: 1, background: C.line }} />
              </div>
            )}

            {/* The "N leave" line, only where a headcount claim is legitimate. */}
            {drop !== null && drop > 0 && (
              <div className="fn-row" style={{ marginBottom: 4 }}>
                <span />
                <span style={{ fontFamily: FONT, fontSize: 11, color: C.muted }}>
                  {num(drop)} leave
                </span>
              </div>
            )}

            <div className="fn-row" style={{ alignItems: "start", paddingBottom: 12 }}>
              {/* The spine */}
              <div style={{ display: "flex", flexDirection: "column", alignItems: "center",
                            alignSelf: "stretch" }}>
                <span style={{ width: 9, height: 9, borderRadius: "50%", marginTop: 5,
                               background: r.zone === "meta" ? C.muted : C.accent }} />
                {i < rungs.length - 1 && (
                  <span style={{ flex: 1, width: 1, background: C.line, minHeight: 22 }} />
                )}
              </div>

              <div style={{ minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                  {/* Only the Acumyn rungs open: those are counts of PEOPLE. An impression is
                      not somebody you can list, and offering to open one would promise a thing
                      that cannot exist. */}
                  {onDrill && r.zone === "acumyn" ? (
                    <button type="button" onClick={() => onDrill(r.key, r.label)}
                            className="fn-open"
                            style={{ fontFamily: HEAD, fontSize: 13.5, fontWeight: 600,
                                     color: C.ink, background: "none", border: "none",
                                     padding: 0, cursor: "pointer",
                                     textDecoration: "underline",
                                     textDecorationColor: C.line,
                                     textUnderlineOffset: 3 }}>
                      {r.label}
                    </button>
                  ) : (
                    <span style={{ fontFamily: HEAD, fontSize: 13.5, fontWeight: 600, color: C.ink }}>
                      {r.label}
                    </span>
                  )}
                  {r.diagnostic && (
                    <span style={{ fontFamily: FONT, fontSize: 10, fontWeight: 600, color: C.muted,
                                   border: `1px solid ${C.line}`, borderRadius: 999,
                                   padding: "1px 7px" }}>
                      diagnostic
                    </span>
                  )}
                  {r.undated > 0 && (
                    <span title="counted, but these rows carry no date so they are never timed"
                          style={{ fontFamily: FONT, fontSize: 10, color: C.warnInk,
                                   background: C.warnBg, borderRadius: 999, padding: "1px 7px" }}>
                      {num(r.undated)} undated
                    </span>
                  )}
                </div>

                {barPct !== null && (
                  <div style={{ background: C.surface2, borderRadius: 4, height: 9, marginTop: 6,
                                overflow: "hidden" }}>
                    <div className="fn-bar"
                         style={{ width: `${barPct}%`, height: "100%",
                                  background: isWorst ? C.badBar : C.accent }} />
                  </div>
                )}

                {/* The two money rungs carry their dollars. A rung called "Cash received" that
                    shows only a headcount leaves the reader to guess the amount, and the guess is
                    what the hero is now totalling. The other rungs get no money column at all
                    rather than an em dash, because there is no dollar figure an impression could
                    ever have. */}
                <div className={`fn-figs${r.value ? " has-value" : ""}`}
                     style={{ marginTop: 7 }}>
                  <Fig label="count" value={compact(r.n)} />
                  {r.value ? (
                    <Fig label={r.key === "closed" ? "contracted" : "cash"}
                         value={usd(r.value, 0)} />
                  ) : null}
                  <Fig label="from previous"
                       value={r.conversion === null || r.conversion === undefined
                         ? "—" : pct(r.conversion, 1)} />
                  <Fig label="cost each"
                       value={r.cost_per === null || r.cost_per === undefined
                         ? "—" : usd(r.cost_per, 0)} />
                </div>

                {/* The leak callout. Deliberately NOT repeated as a finding in section 02 - this
                    is the richer version, with the prior rung named and the headcount stated. */}
                {isWorst && prevRung && (
                  <div style={{ marginTop: 9, background: C.badBg,
                                border: `1px solid ${C.badBar}`, borderRadius: 10,
                                padding: "9px 12px" }}>
                    <span style={{ fontFamily: FONT, fontSize: 12.5, color: C.ink,
                                   lineHeight: 1.5 }}>
                      <strong>Biggest leak.</strong> {pct(r.conversion, 0)} of {prevRung.label
                        .toLowerCase()} reach {r.label.toLowerCase()} — {num((r.prev ?? 0) - r.n)}{" "}
                      of {num(r.prev ?? 0)} drop here.
                    </span>
                  </div>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Fig({ label, value }) {
  return (
    <div>
      <div style={{ fontFamily: FONT, fontSize: 10, fontWeight: 600, letterSpacing: ".06em",
                    textTransform: "uppercase", color: C.muted }}>{label}</div>
      <div style={{ fontFamily: FIG, fontVariantNumeric: "tabular-nums", fontSize: 14,
                    color: C.ink, marginTop: 1 }}>{value}</div>
    </div>
  );
}
