/* Vendor marks for the Integrations page.
 *
 * MATCHED ON THE PROVIDER KEY, not the source name. vendor-logos.js matches a tool's NAME, which
 * is right there — a workspace types "Follow Up Boss" into its launchpad and we recognise it. Here
 * the names are ours and they are not vendor names: "Legacy Stripe · The Forum" starts with the
 * word Legacy, so a word-prefix match would silently miss it. The provider key is exact.
 *
 * A provider with no mark keeps its monogram. That is the point of the fallback: a missing logo
 * should look deliberate, and a WRONG one is worse than none, because a mark reads as a fact
 * while initials read as "we do not have one".
 *
 * WHAT IS NOT HERE, AND WHY:
 *   * Go High Level (ghl, ghl_bc, ghl_legacy). Their terms require written permission before a
 *     commercial product displays their mark, and the file we were sent may be the product icon
 *     rather than the approved logo (their brand kit specifies Space Blue and White). Both are
 *     open questions, so it wears the monogram until they are closed.
 *   * Meta Ads. No mark supplied.
 *
 * ATTRIBUTION. QuickBooks and Stripe are simple-icons (CC0-1.0). The PNGs were supplied by the
 * client. Every mark remains the property of its owner and is shown to identify an integration.
 *
 * The PNGs are 60px. A 21px mark inside a 34px tile wants 63px for a 3x display, so these are
 * three pixels short of perfect there and exact everywhere else; the masters are larger, and
 * swapping them in is a file copy with no code change.
 */
import { T } from "../theme.js";
import arive from "./marks/arive.png";
import followUpBoss from "./marks/follow-up-boss.png";
import sisu from "./marks/sisu.png";

// viewBox 0 0 24 24 for both, so one <svg> wrapper serves them.
const QUICKBOOKS = "M12 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0zm.642 4.1335c.9554 0 1.7296.776 1.7296 1.7332v9.0667h1.6c1.614 0 2.9275-1.3156 2.9275-2.933 0-1.6173-1.3136-2.9333-2.9276-2.9333h-.6654V7.3334h.6654c2.5722 0 4.6577 2.0897 4.6577 4.667 0 2.5774-2.0855 4.6666-4.6577 4.6666H12.642zM7.9837 7.333h3.3291v12.533c-.9555 0-1.73-.7759-1.73-1.7332V9.0662H7.9837c-1.6146 0-2.9277 1.316-2.9277 2.9334 0 1.6175 1.3131 2.9333 2.9277 2.9333h.6654v1.7332h-.6654c-2.5725 0-4.6577-2.0892-4.6577-4.6665 0-2.5771 2.0852-4.6666 4.6577-4.6666Z";
const STRIPE = "M13.976 9.15c-2.172-.806-3.356-1.426-3.356-2.409 0-.831.683-1.305 1.901-1.305 2.227 0 4.515.858 6.09 1.631l.89-5.494C18.252.975 15.697 0 12.165 0 9.667 0 7.589.654 6.104 1.872 4.56 3.147 3.757 4.992 3.757 7.218c0 4.039 2.467 5.76 6.476 7.219 2.585.92 3.445 1.574 3.445 2.583 0 .98-.84 1.545-2.354 1.545-1.875 0-4.965-.921-6.99-2.109l-.9 5.555C5.175 22.99 8.385 24 11.714 24c2.641 0 4.843-.624 6.328-1.813 1.664-1.305 2.525-3.236 2.525-5.732 0-4.128-2.524-5.851-6.594-7.305h.003z";

const MARKS = {
  qbo: { kind: "svg", d: QUICKBOOKS, color: "#2CA01C" },
  stripe_legacy: { kind: "svg", d: STRIPE, color: "#635BFF" },
  stripe_bc: { kind: "svg", d: STRIPE, color: "#635BFF" },
  fub: { kind: "img", src: followUpBoss },
  sisu: { kind: "img", src: sisu },
  // The only non-square mark in the set, 1.66:1 — it is letterboxed rather than stretched.
  arive: { kind: "img", src: arive },
};

/** True when this provider has a real mark. Exported so a guard can assert the set. */
export const hasMark = (provider) => Boolean(MARKS[provider]);

/**
 * A provider's mark in its tile, or the monogram when we have no mark for it.
 *
 * `fallback` is the two-letter monogram the page already computes. It is passed in rather than
 * looked up here so there is exactly one monogram table in the app.
 */
export function IntegrationMark({ provider, fallback = "?", size = 34, title }) {
  const mark = MARKS[provider];
  const inner = Math.round(size * 0.62);
  return (
    <span aria-hidden={title ? undefined : true} role={title ? "img" : undefined} aria-label={title}
      style={{
        width: size, height: size, flexShrink: 0, borderRadius: Math.round(size * 0.26),
        background: T.white, border: `1px solid ${T.line}`, overflow: "hidden",
        display: "inline-flex", alignItems: "center", justifyContent: "center",
      }}>
      {!mark && (
        <span style={{
          fontFamily: "var(--font-display)", fontSize: Math.round(size * 0.37), fontWeight: 700,
          color: T.evergreen,
        }}>{fallback}</span>
      )}
      {mark && mark.kind === "svg" && (
        <svg viewBox="0 0 24 24" width={inner} height={inner} fill={mark.color} aria-hidden="true"
          focusable="false"><path d={mark.d} /></svg>
      )}
      {mark && mark.kind === "img" && (
        <img src={mark.src} alt="" style={{ maxWidth: inner, maxHeight: inner, width: "auto",
          height: "auto", display: "block" }} />
      )}
    </span>
  );
}
