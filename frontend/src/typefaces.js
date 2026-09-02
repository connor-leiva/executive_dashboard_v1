/* Type, as a small set of PAIRINGS rather than three font pickers.
 *
 * A colour picked badly looks wrong. A typeface picked badly stops people reading — and the
 * specific failure is predictable: somebody chooses a display face they like, applies it to body
 * text, and every table in the product becomes work to scan. Three free-text font fields would
 * make that the most likely outcome rather than an unlucky one.
 *
 * So: four pairings, each already balanced, each loading only the weights the app uses.
 *
 * THE DATA FACE IS NOT A CHOICE. Every pairing sets numbers in Archivo, because it has tabular
 * figures and that is the entire reason a ledger column lines up. A workspace that set its
 * figures in a proportional face would get columns that drift by a pixel per row — legible, and
 * wrong in a way that takes an afternoon to diagnose because nothing is broken.
 *
 * WHY THIS FILE EXISTS AT ALL: the palette work set --font-display and --font-text to Space
 * Grotesk and Instrument Sans, and nothing ever loaded them. index.html requested Archivo and
 * Outfit; Poppins and Inter were @imported inline from two components. So the platform default
 * had been silently falling back to Helvetica since the day it was introduced. Declaring a
 * typeface and loading it are two different jobs and this does both.
 */

const GOOGLE = "https://fonts.googleapis.com/css2";

export const PAIRINGS = {
  acumyn: {
    label: "Acumyn",
    note: "The platform's own. Geometric display, quiet text.",
    display: '"Space Grotesk","Helvetica Neue",Arial,sans-serif',
    text: '"Instrument Sans","Helvetica Neue",Arial,sans-serif',
    families: ["Space+Grotesk:wght@500;700", "Instrument+Sans:wght@400;500;600;700"],
  },
  classic: {
    label: "Classic",
    note: "Rounded and warm. What this product shipped with.",
    display: "Poppins,sans-serif",
    text: "Inter,sans-serif",
    families: ["Poppins:wght@400;500;600;700;800", "Inter:wght@400;500;600"],
  },
  neutral: {
    label: "Neutral",
    note: "One face throughout. Gets out of the way.",
    display: "Inter,sans-serif",
    text: "Inter,sans-serif",
    families: ["Inter:wght@400;500;600;700;800"],
  },
  editorial: {
    label: "Editorial",
    note: "A serif for headings. Reads as considered rather than technical.",
    display: '"Fraunces","Iowan Old Style",Georgia,serif',
    text: '"Instrument Sans","Helvetica Neue",Arial,sans-serif',
    families: ["Fraunces:opsz,wght@9..144,600;9..144,700", "Instrument+Sans:wght@400;500;600;700"],
  },
};

export const DEFAULT_PAIRING = "acumyn";

// Every pairing sets numbers in the same face. See the note above — this is not an oversight.
const DATA_FAMILY = "Archivo:wght@400;500;600;700";
const DATA_STACK = 'Archivo,"Helvetica Neue",Arial,sans-serif';

/**
 * Which pairing a legacy three-stack `type` object corresponds to.
 *
 * Workspaces configured before pairings existed store raw stacks. Applying those directly sets
 * the variables and loads NOTHING — which is precisely how a workspace ended up naming Poppins
 * while the browser had only fetched Space Grotesk, so every surface fell back to a generic sans.
 * Matching to a pairing means the fonts get requested.
 */
export function pairingFromStacks(type) {
  const display = String((type && type.display) || "").toLowerCase();
  if (!display) return null;
  for (const [name, p] of Object.entries(PAIRINGS)) {
    const first = p.display.toLowerCase().replace(/["']/g, "").split(",")[0].trim();
    if (display.replace(/["']/g, "").startsWith(first)) return name;
  }
  return null;
}

export function pairing(name) {
  return PAIRINGS[name] || PAIRINGS[DEFAULT_PAIRING];
}

/** The three stacks a pairing resolves to, in the shape applyType expects. */
export function stacks(name) {
  const p = pairing(name);
  return { display: p.display, text: p.text, data: DATA_STACK };
}

const LINK_ID = "acu-typeface";

/**
 * Request the webfonts a pairing needs.
 *
 * `display=swap`, deliberately: text renders immediately in the fallback and reflows when the
 * face arrives. The alternative is a page that is blank until a third party answers, which is a
 * worse failure on a dashboard somebody opens forty times a day.
 *
 * One <link>, replaced rather than appended, so switching pairings in the settings preview does
 * not accumulate a stylesheet per click.
 */
export function loadTypeface(name) {
  if (typeof document === "undefined") return;
  const p = pairing(name);
  const href = `${GOOGLE}?${[...p.families, DATA_FAMILY]
    .map((f) => `family=${f}`).join("&")}&display=swap`;
  let link = document.getElementById(LINK_ID);
  if (link && link.getAttribute("href") === href) return;
  if (!link) {
    link = document.createElement("link");
    link.id = LINK_ID;
    link.rel = "stylesheet";
    document.head.appendChild(link);
  }
  link.setAttribute("href", href);
}
