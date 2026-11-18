/* The gallery's orders and the placeholder's initials.
 * Run: node --test src/ads/creative.test.js   (from frontend/)
 */
import assert from "node:assert/strict";
import test from "node:test";

import { gallerySort, initials } from "./creative.js";

const ADS = [
  { id: "a", spend: 900, seats: 1, cost_per_seat: 900 },
  { id: "b", spend: 5000, seats: 0, cost_per_seat: null },
  { id: "c", spend: 300, seats: 3, cost_per_seat: 100 },
  { id: "d", spend: 2000, seats: null, cost_per_seat: null },
];
const ids = (rows) => rows.map((r) => r.id);

test("initials take the first letter of the first two segments", () => {
  assert.equal(initials("Video - ProductionProblem - Justin1"), "VP");
  assert.equal(initials("KB-Shift-30s-motion"), "KS");
  assert.equal(initials("forum_dallas"), "FD");
  assert.equal(initials("Yoga"), "Y");
  assert.equal(initials(""), "?");
  assert.equal(initials(null), "?");
});

test("spend runs highest first", () => {
  assert.deepEqual(ids(gallerySort(ADS, "spend")), ["b", "d", "a", "c"]);
});

test("seats run most first; zero is a figure, missing comes last", () => {
  assert.deepEqual(ids(gallerySort(ADS, "seats")), ["c", "a", "b", "d"]);
});

test("cost per seat runs cheapest first; an ad with no seats is not a free seat", () => {
  // b and d have no cost per seat, so they follow, ordered by spend.
  assert.deepEqual(ids(gallerySort(ADS, "cost_per_seat")), ["c", "a", "b", "d"]);
});

test("ties fall back to spend", () => {
  const tied = [{ id: "low", spend: 10, seats: 2 }, { id: "high", spend: 99, seats: 2 }];
  assert.deepEqual(ids(gallerySort(tied, "seats")), ["high", "low"]);
});

test("the list it was given is left alone", () => {
  const before = ids(ADS);
  gallerySort(ADS, "spend");
  assert.deepEqual(ids(ADS), before);
  assert.deepEqual(gallerySort(null, "spend"), []);
});
