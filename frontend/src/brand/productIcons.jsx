/* The platform modules' icons — Books, Binder, Agents, Campaigns, Production.
 *
 * Ported from the Acumyn site, which is where they were drawn. They are not a general icon set
 * and there is deliberately no way to ask for an arbitrary glyph: five products have a mark,
 * anything else falls back to the blade motif. An icon set with an open door becomes a
 * dependency, and then the rail's chrome is negotiating with whatever a component felt like
 * importing.
 *
 * WHY THESE AND NOT THE PNG ICON LIBRARY. `Icon` in Brand.jsx masks a PNG from /brand/icons,
 * which is right for the hundreds of small utility glyphs — but a module's mark is the product's
 * identity, and it has to survive being tinted, scaled and put on an evergreen rail. Drawn from
 * the same geometry as the Acumyn mark, it does.
 *
 * CONSTRUCTION, and every number here is load-bearing:
 *   32x32 artboard · stroke 2.6 · caps BUTT · no fill except where a mark is genuinely solid.
 * Butt caps are the reason these read as one family with the mark, whose blades are specified
 * with radial butt terminals. Round caps would be softer and would belong to a different set.
 */

const ART = 32;
const STROKE = 2.6;

/* One attribute bag, so a glyph added later cannot quietly use a different weight. `tone`
   defaults to currentColor, matching Brand.jsx's Icon — an icon beside a label should take the
   label's colour unless somebody has a reason to say otherwise. */
function frame(size, tone) {
  return {
    width: size, height: size, viewBox: `0 0 ${ART} ${ART}`,
    fill: "none", stroke: tone, strokeWidth: STROKE, strokeLinecap: "butt",
    "aria-hidden": true, focusable: "false",
  };
}

/* The glyphs. Each is a function of the resolved tone because two of them fill a shape, and a
   filled shape has to be told the colour that the stroke gets from the frame. */
const GLYPHS = {
  // Bars at four heights: production is a quantity that varies, so the mark is a comparison.
  //
  // CURRENTLY UNMAPPED, deliberately. On the site this marks the Production product, which here
  // is the scorecard and the per-agent volume inside a BUSINESS tab -- and a business is
  // identified by its accent, not by a shape (see BY_MODULE). Kept because it is part of the
  // family as drawn, and because a Production module in its own right is a plausible next tab.
  production: () => <path d="M5 24V14M12 24V8M19 24V17M26 24V11" />,

  // Ledger rules, the last one short, with a check leaving the block. Books is not a book — it
  // is lines that have been agreed, which is what the tick is doing there.
  books: () => (
    <>
      <path d="M6 8h20M6 15h20M6 22h13" />
      <path d="M22 25.5l3 3 5-6" />
    </>
  ),

  // Two opposing quarter-arcs around a solid centre: a cycle with something at the middle of it.
  campaigns: (tone) => (
    <>
      <path d="M5 16a11 11 0 0 1 11-11" />
      <path d="M27 16a11 11 0 0 1-11 11" />
      <circle cx="16" cy="16" r="3.2" fill={tone} stroke="none" />
    </>
  ),

  // A spine with two tabbed sections — the ring binder, read edge-on.
  binder: () => (
    <>
      <path d="M8 5v22M8 5h16v9H8" />
      <path d="M8 18h13v9H8" />
    </>
  ),

  // A figure with a check at the shoulder. The check is the whole point of the module: an agent
  // drafts, a person approves, and nothing ships without that second mark.
  agents: () => (
    <>
      <circle cx="16" cy="11" r="5" />
      <path d="M6 27a10 10 0 0 1 20 0" />
      <path d="M24 6l2.5 2.5L31 4" strokeWidth="2.2" />
    </>
  ),

  // The fallback, and the Acumyn mark's own motif reduced to two blades: a loop that has not
  // closed. It is what an unrecognised module gets, and it is right for a flywheel.
  loop: () => (
    <>
      <path d="M9 6a11 11 0 0 0 0 20" />
      <path d="M23 6a11 11 0 0 1 0 20" />
    </>
  ),
};

/* Rail key -> glyph. The keys are the platform's own module keys (app/plans.py `_FEATURE_TABS`
 * plus `ads`), never a tenant's.
 *
 * THAT IS WHAT MAKES THIS SAFE TO LOOK UP BY KEY. A business tab is named by the customer —
 * "The Forum", "Sympli Mortgage" — and its key is tenant data, so it will never collide with one
 * of these and will never accidentally inherit a module's mark. A module has a fixed key chosen
 * by us. So `iconFor(key)` returning null is a real answer meaning "this is not a module", and
 * the rail uses it to decide between an icon and the business's accent dot.
 */
const BY_MODULE = {
  books: "books",
  binder: "binder",
  ai_employees: "agents",
  ads: "campaigns",
  flywheel: "loop",
};

/** The glyph name for a rail/module key, or null if the key is not a platform module. */
export function iconFor(key) {
  return BY_MODULE[key] || null;
}

/**
 * A module's mark.
 *
 * `name` is a glyph name (see GLYPHS) — pass `iconFor(key)` to go from a rail key. An unknown
 * name draws the loop rather than throwing or rendering nothing: a missing icon in a rail is a
 * hole somebody has to notice, and the fallback is a real Acumyn shape, not a placeholder.
 */
export function ProductIcon({ name, size = 22, tone = "currentColor", title, style }) {
  const draw = GLYPHS[name] || GLYPHS.loop;
  const attrs = frame(size, tone);
  if (title) { attrs["aria-hidden"] = undefined; attrs.role = "img"; }
  return (
    <svg {...attrs} style={style}>
      {title ? <title>{title}</title> : null}
      {draw(tone)}
    </svg>
  );
}
