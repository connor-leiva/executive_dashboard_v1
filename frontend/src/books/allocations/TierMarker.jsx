/* Tier, carried three ways (ALLOCATION-RULES-SPEC 7.2).

   Marker shape, two-character code, and row treatment. Never colour alone, and the reason is in
   the data rather than in accessibility good manners: three of the five businesses share one
   accent value (F11, recorded under amendment 23 as Connor's call to accept it), so a reader
   distinguishing tiers by hue would be distinguishing some of them by nothing at all.

   The tier vocabulary comes from `meta.tiers` in the payload, never from a constant in here, so
   adding a tier is a server change rather than a frontend release. Acceptance check A10 greps
   this folder for that constant by name and expects to find none. */
import { LEDGER as L, LEDGER_FONT as F } from "../../theme.js";

const SQUARE = { width: 9, height: 9, borderRadius: 1.5 };
const RING = { width: 9, height: 9, borderRadius: "50%", background: "transparent" };

export function TierMarker({ tier, size = 9 }) {
  const box = { display: "inline-block", flex: "0 0 auto", width: size, height: size };
  if (tier === 1) {
    return <span aria-hidden style={{ ...box, ...SQUARE, width: size, height: size,
                                      background: L.deep }} />;
  }
  if (tier === 2) {
    return <span aria-hidden style={{ ...box, ...RING, width: size, height: size,
                                      border: `2px solid ${L.body}` }} />;
  }
  if (tier === 3) {
    return <span aria-hidden style={{ ...box, ...RING, width: size, height: size,
                                      border: `2px solid ${L.flagRing}` }} />;
  }
  // Tier 4 retained: no marker at all. The absence IS the signal, and the code beside it says
  // so in words for anyone who reads the row rather than scans it.
  return <span aria-hidden style={{ ...box, width: size, height: size }} />;
}

export function TierCode({ tier }) {
  return (
    <span style={{ fontFamily: F.num, fontSize: 10, letterSpacing: ".08em",
                   color: tier === 3 ? L.flagInk : L.faint, fontWeight: 600 }}>
      {tier ? `T${tier}` : "--"}
    </span>
  );
}

/* The row treatment for a tier. Returned as a style object rather than applied here, because
   the caller owns its own layout and a component that reached into it would fight every table
   it appears in. */
export function tierRowStyle(tier) {
  if (tier === 3) {
    return { background: L.flagBg, boxShadow: `inset 0 0 0 3px ${L.flagEdge}`, fontWeight: 500 };
  }
  if (tier === 1) return { background: L.paper, fontWeight: 700 };
  if (tier === 2) return { background: L.paper, fontWeight: 600 };
  return { background: L.paper, color: L.muted, fontWeight: 400 };
}

export function TierLabel({ tier, tiers }) {
  const meta = (tiers || []).find((t) => t.tier === tier);
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <TierMarker tier={tier} />
      <TierCode tier={tier} />
      <span style={{ fontFamily: F.body, fontSize: 12, color: L.body }}>
        {meta ? meta.label : "Unknown"}
      </span>
    </span>
  );
}
