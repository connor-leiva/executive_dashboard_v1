/* The page's words, all of which come from the server (UI spec 8.2, principle 4).
 *
 * This file holds NO vocabulary of its own. It is a reader for `meta.*`, which
 * `alloc_rules.vocabulary()` builds. The page used to carry three `REASON` maps -- in
 * RulesView, ReconView and RuleDrawer -- that gave the same code different wording, so the
 * same stalled rule was explained three ways depending on which screen you were looking at
 * (U8). A word defined wherever it is first needed ends up defined several times.
 *
 * Every function takes `meta` and degrades to the raw code rather than to an invented
 * sentence: a code on screen is a bug somebody will report, and a plausible wrong sentence is
 * one nobody will.
 */

/** "Split between entities by a reviewed share" for `split`. */
export function methodLabel(meta, code) {
  return (meta?.methods || {})[code] || code || "";
}

/** "Vendor" for `vendor`. */
export function scopeLabel(meta, code) {
  return (meta?.scopes || {})[code] || code || "";
}

/** "No memo" for `no_memo`. */
export function gapLabel(meta, code) {
  return (meta?.gaps || {})[code] || code || "";
}

/** "percent" for `share`. */
export function valueKindLabel(meta, code) {
  return (meta?.value_kinds || {})[code] || code || "";
}

/** `{label, action, target}` for a pending reason, never undefined. */
export function reasonOf(meta, code) {
  if (!code) return null;
  const hit = (meta?.reasons || {})[code];
  return hit ? { code, ...hit } : { code, label: code, action: null, target: null };
}

/** "T3 Driven" and its one-line gloss, from `meta.tiers`. */
export function tierOf(meta, tier) {
  return (meta?.tiers || []).find((t) => t.tier === tier) || null;
}

export function tierLabel(meta, tier) {
  return tierOf(meta, tier)?.label || (tier ? `T${tier}` : "");
}

/* ── dates (8.3) ──────────────────────────────────────────────────────────────────────────
 * `cleanup/kit.jsx` already exports `monthName` and `dayLabel`, and the parent spec's
 * standing rule 17 says to reuse that kit rather than assemble an equivalent. These wrap it
 * so the Allocations page has one import, and add the one rule the kit does not carry: a day
 * shows its year only when it is not the current one.
 */
import { monthName, dayLabel } from "../../cleanup/kit.jsx";

/** "November 2026" from "2026-11-01". Formatting only; it computes nothing.
 *
 * `monthName(m, year)` already appends the year, so this adds none. */
export function fmtMonth(iso) {
  if (!iso) return "";
  const [y, m] = String(iso).split("-");
  return monthName(Number(m), Number(y));
}

/** "Nov 2026". `monthName` with no year gives the bare month to shorten. */
export function fmtMonthShort(iso) {
  if (!iso) return "";
  const [y, m] = String(iso).split("-");
  return `${monthName(Number(m)).slice(0, 3)} ${y}`;
}

/** "Nov 3", or "Nov 3, 2025" when the year is not this one. */
export function fmtDay(iso, now = new Date()) {
  if (!iso) return "";
  const base = dayLabel(iso);
  const year = String(iso).slice(0, 4);
  return year === String(now.getFullYear()) ? base : `${base}, ${year}`;
}
