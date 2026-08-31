/* The creative wall. SPEC-ads-module.md Part 13 and 3.9.
 *
 * The ads themselves, ranked by spend. This is the one place on the tab where somebody is
 * looking at a THING rather than a number, and it is how a marketer recognises the campaign
 * they are being shown figures about.
 *
 * Two facts shape it more than the layout does:
 *
 *   * Meta thumbnail URLs are SIGNED AND SHORT-LIVED (3.9). They expire, and an expired one
 *     reads as a fault in this dashboard rather than an expiry upstream. Every tile therefore
 *     renders its own placeholder UNDERNEATH the image, so missing, expired, unreachable and
 *     transparent all degrade to the same legible thing. Phase 5 caches the bytes to R2 and the
 *     problem goes away; until then this is the difference between "stale" and "broken".
 *
 *   * Revenue per ad is NOT here, and its absence is stated rather than left as a blank column.
 *     It needs utm_content={{ad.id}} on the ad URLs and a matching CRM field - measured at 0 of
 *     56 ads. A wall with an empty revenue column invites the reading that these ads earned
 *     nothing, which is the specific wrong conclusion this module exists to prevent.
 */
import { useState } from "react";

import { C, FIG, FONT, HEAD, compact, pct, usd } from "./adsTokens.js";

/* Initials from an ad name, for the placeholder. Meta names are structured
   ("Video - ProductionProblem - Justin1"), so the first letter of the first two segments
   distinguishes far better than the first two characters of the whole string. */
function initials(name) {
  const parts = String(name || "?").split(/[\s\-_]+/).filter(Boolean);
  return (parts.slice(0, 2).map((p) => p[0]).join("") || "?").toUpperCase();
}

function Thumb({ src, name }) {
  const [failed, setFailed] = useState(false);

  /* The initials sit UNDERNEATH the image rather than instead of it. Swapping on `onError`
     alone covers an expired URL that answers, and misses the one that does not: a request to an
     unreachable host stays pending forever, no error event ever fires, and the tile holds an
     empty box for the life of the page. Found by pointing this at a dead URL and watching it
     hang rather than fail. Layering covers expired, missing, unreachable and transparent with
     one rule, and a loaded opaque image covers the initials the moment it paints. */
  return (
    <div style={{ position: "relative", width: "100%", aspectRatio: "1 / 1", borderRadius: 10,
                  background: C.surface2, border: `1px solid ${C.line}`, overflow: "hidden",
                  display: "flex", alignItems: "center", justifyContent: "center" }}
         title={src && !failed ? name : "No image available for this ad"}>
      <span aria-hidden="true"
            style={{ fontFamily: HEAD, fontSize: 20, fontWeight: 600, color: C.muted }}>
        {initials(name)}
      </span>
      {src && !failed && (
        <img src={src} alt="" loading="lazy" onError={() => setFailed(true)}
             style={{ position: "absolute", inset: 0, width: "100%", height: "100%",
                      objectFit: "cover", display: "block" }} />
      )}
    </div>
  );
}

/* One figure and its label. Deliberately not the Stat card from AdsView - inside a 200px tile
   the card's padding costs more than the border earns. */
function Cell({ label, children }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontFamily: FONT, fontSize: 9.5, fontWeight: 600, letterSpacing: ".06em",
                    textTransform: "uppercase", color: C.muted }}>{label}</div>
      <div style={{ fontFamily: FIG, fontVariantNumeric: "tabular-nums", fontSize: 13,
                    color: C.ink, marginTop: 2, whiteSpace: "nowrap", overflow: "hidden",
                    textOverflow: "ellipsis" }}>{children}</div>
    </div>
  );
}

function AdTile({ ad }) {
  return (
    <div className="ads-tile"
         style={{ background: C.surface, border: `1px solid ${C.line}`, borderRadius: 14,
                  padding: 12, display: "grid", gap: 10, alignContent: "start" }}>
      <Thumb src={ad.thumbnail_url} name={ad.name} />

      <div style={{ minWidth: 0 }}>
        {/* The ad NAME is the operator's handle on it - it is what they renamed in Ads Manager
            and what they will search for. The headline is what the audience saw. Both matter,
            and they are frequently nothing like each other. */}
        <div title={ad.name}
             style={{ fontFamily: FONT, fontSize: 12.5, fontWeight: 600, color: C.ink,
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {ad.name}
        </div>
        {ad.headline && (
          <div title={ad.headline}
               style={{ fontFamily: FONT, fontSize: 11.5, color: C.slate, marginTop: 3,
                        lineHeight: 1.45, display: "-webkit-box", WebkitLineClamp: 2,
                        WebkitBoxOrient: "vertical", overflow: "hidden" }}>
            {ad.headline}
          </div>
        )}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "9px 10px",
                    borderTop: `1px solid ${C.line}`, paddingTop: 9 }}>
        <Cell label="Spend">{usd(ad.spend)}</Cell>
        <Cell label="Link CTR">{pct(ad.ctr)}</Cell>
        <Cell label="Link clicks">{compact(ad.link_clicks)}</Cell>
        {/* Cost per lead against META's lead count, which is Meta measuring itself. Named that
            way here for the same reason the strip names it: it is not the owned funnel. */}
        <Cell label="Cost / lead · Meta">{ad.leads ? usd(ad.cpl) : "—"}</Cell>
      </div>
    </div>
  );
}

export default function CreativeWall({ data, loading }) {
  const ads = data?.ads || [];

  if (loading && !ads.length) {
    return (
      <div style={{ fontFamily: FONT, fontSize: 12.5, color: C.muted, padding: "10px 0" }}>
        Loading the creative…
      </div>
    );
  }

  if (!ads.length) {
    // An empty wall is normal for a window with no ad-level delivery, and it is NOT the same as
    // a failed sync. Say which one this is.
    return (
      <div style={{ background: C.surface, border: `1px solid ${C.line}`, borderRadius: 14,
                    padding: 18, fontFamily: FONT, fontSize: 12.5, color: C.slate,
                    lineHeight: 1.55 }}>
        No ads delivered in this window. Widen the period, or check the sync in Settings if you
        expected spend here.
      </div>
    );
  }

  return (
    <>
      <style>{`
        .ads-wall { display: grid; gap: 12px;
                    grid-template-columns: repeat(auto-fill, minmax(196px, 1fr)); }
        .ads-tile { transition: border-color .15s ease; }
        @media (hover: hover) { .ads-tile:hover { border-color: ${C.accent}; } }
        @media (prefers-reduced-motion: reduce) { .ads-tile { transition: none; } }
        @media (max-width: 560px) {
          .ads-wall { grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); }
        }
      `}</style>
      <div className="ads-wall">
        {ads.map((ad) => <AdTile key={ad.id} ad={ad} />)}
      </div>
      {data?.total > ads.length && (
        <div style={{ fontFamily: FONT, fontSize: 11.5, color: C.muted, marginTop: 10 }}>
          Showing the {ads.length} highest-spending of {data.total} ads that delivered.
        </div>
      )}
    </>
  );
}
