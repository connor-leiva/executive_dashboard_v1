/* The ad pictures' pure logic: the placeholder's initials and the gallery's three orders.
 * No React and no tokens, so `node --test src/ads/creative.test.js` checks it directly.
 */

/* Initials from an ad name, for the placeholder under a picture. Meta names are structured
   ("Video - ProductionProblem - Justin1"), so the first letter of the first two segments tells
   ads apart far better than the first two characters of the whole string. */
export function initials(name) {
  const parts = String(name || "").split(/[\s\-_]+/).filter(Boolean);
  return (parts.slice(0, 2).map((p) => p[0]).join("") || "?").toUpperCase();
}

/* Spend and seats run highest first. Cost per seat runs CHEAPEST first, because that is the
   question it answers: which picture buys a seat for the least. */
export const GALLERY_SORTS = [
  ["spend", "Spend"],
  ["seats", "Seats"],
  ["cost_per_seat", "Cost per seat"],
];

export const SORT_NOTE = {
  spend: "Highest spend first",
  seats: "Most seats first",
  cost_per_seat: "Cheapest seat first",
};

const figure = (v) => (v === null || v === undefined || v === "" ? null : Number(v));

/* A copy, never the array it was given. An ad with no figure for the order sorts after every ad
   that has one (a missing cost per seat is not a free seat), and ties fall back to spend. */
export function gallerySort(ads, key) {
  const cheapestFirst = key === "cost_per_seat";
  const bySpend = (a, b) => (figure(b.spend) || 0) - (figure(a.spend) || 0);
  return [...(ads || [])].sort((a, b) => {
    const av = figure(a?.[key]);
    const bv = figure(b?.[key]);
    const aMissing = av === null || !Number.isFinite(av);
    const bMissing = bv === null || !Number.isFinite(bv);
    if (aMissing !== bMissing) return aMissing ? 1 : -1;
    if (aMissing) return bySpend(a, b);
    return (cheapestFirst ? av - bv : bv - av) || bySpend(a, b);
  });
}
