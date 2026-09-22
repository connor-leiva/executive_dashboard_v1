/* "Powered by Axcion" — the one piece of identity that is not a workspace's to configure.
 *
 * Everything else in this app whitelabels: a workspace supplies its own colours, type, wordmark
 * and login imagery, and after that nothing on screen says Axcion. That is the product working
 * as intended, and it is also how a platform becomes invisible to the people using it every day.
 * This is the attribution that survives it.
 *
 * DELIBERATELY NOT CONFIGURABLE, at any tier. There is no prop, no brand key and no plan flag
 * that removes it — the only way to take it off a page is to delete the component from that
 * page, which is a code review rather than a setting. That is the point: a switch that hides it
 * would eventually get flipped by someone who was not deciding platform strategy.
 *
 * It renders Axcion's OWN mark in Axcion's own colours, never the workspace's tokens. A
 * "powered by" that adopts the host brand attributes nothing.
 *
 * Placement is the footer of the app shell AND of every share surface. The share links matter
 * most: those are opened by a customer's own clients, who have no other reason to encounter the
 * platform, and they are the widest audience the product has.
 */
import { AXCION_SITE, AxcionMark, TYPE } from "./axcion.jsx";

/**
 * tone "dark"  — for light grounds: the primary two-tone mark, Ink wordmark.
 * tone "light" — for dark grounds: the guide requires switching off Cadet below Ink 400, so this
 *                takes the all-white knockout rather than tinting the mark toward the ground.
 */
export function PoweredByAxcion({ tone = "dark", align = "center", style }) {
  const onDark = tone === "light";
  const wordColor = onDark ? "rgba(255,255,255,.62)" : "rgba(22,32,31,.52)";
  const labelColor = onDark ? "rgba(255,255,255,.40)" : "rgba(22,32,31,.34)";
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: align === "center" ? "center" : align,
      gap: 7, padding: "18px 0 22px", ...style,
    }}>
      <span style={{
        fontFamily: TYPE.text, fontSize: 11, fontWeight: 500, letterSpacing: ".04em",
        color: labelColor,
      }}>Powered by</span>
      <a href={AXCION_SITE} target="_blank" rel="noopener noreferrer"
         aria-label="Powered by Axcion"
         style={{ display: "inline-flex", alignItems: "center", gap: 5, textDecoration: "none" }}>
        {/* Treatment, not colour: the primary cut is two-tone (Cadet and Ink blades, Sage leaf)
            and there is no colour prop to collapse it with — each approved cut is its own file.
            16px rather than 14: the designed mark is finer than the drawn one it replaced, and
            at 14 the leaf disappears into the antialiasing. */}
        <AxcionMark size={16} treatment={onDark ? "knockout" : "primary"} />
        <span style={{
          fontFamily: TYPE.display, fontWeight: 700, fontSize: 12.5, letterSpacing: "-.02em",
          lineHeight: 1, color: wordColor,
        }}>Axcion</span>
      </a>
    </div>
  );
}

export default PoweredByAxcion;
