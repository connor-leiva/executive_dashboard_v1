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
import { AXCION_SITE, AxcionLockup, TYPE } from "./axcion.jsx";

/**
 * THE LOCKUP, NOT A MARK BESIDE A WORDMARK.
 *
 * This used to set "Axcion" in Space Grotesk next to a 16px mark, which is an invented lockup —
 * and it showed. The designed mark's X fills only 69% of its own box (the leaf hangs below with
 * a gap between), so centring that box against the text floated the X about two pixels high, the
 * leaf rendered as a sub-3px speck reading as dirt, and the whole thing came out looking like a
 * close button rather than a brand. Every part of that is a relationship the designer had
 * already decided — the brief spends a section on icon-to-wordmark scale and clear space — so
 * this renders the delivered lockup and supplies only the words "Powered by".
 *
 * Quietened with OPACITY rather than colour. An attribution should sit back, but the mark is two
 * tones plus a support colour and there is no way to fade that toward the ground without picking
 * which of the three to sacrifice.
 *
 * tone "dark"  — for light grounds: the primary two-tone lockup.
 * tone "light" — for dark grounds: the guide requires switching off Cadet below Ink 400, so this
 *                takes the all-white knockout rather than tinting the mark toward the ground.
 */
export function PoweredByAxcion({ tone = "dark", align = "center", style }) {
  const onDark = tone === "light";
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
        {/* `size` is the MARK's height inside the lockup, so 20 puts the wordmark at roughly the
            12.5px it was set at before and the footprint barely moves. */}
        <AxcionLockup size={20} treatment={onDark ? "knockout" : "primary"}
                      style={{ opacity: onDark ? 0.72 : 0.78 }} />
      </a>
    </div>
  );
}

export default PoweredByAxcion;
