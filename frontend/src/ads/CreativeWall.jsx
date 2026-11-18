/* The creative wall, now the Gallery view of Performance > Ads. SPEC-ads-module.md Part 13, 3.9.
 *
 * The ads themselves. This is the one place on the tab where somebody looks at a THING rather
 * than a number, and it is how a marketer recognises the campaign the figures are about. The
 * five-screen split (7db2f03) stopped rendering it without saying so; this is the same wall,
 * restyled into screenStyles.js, and each tile opens the ad's side panel.
 *
 * Two facts shape it more than the layout does:
 *
 *   * Meta thumbnail links are SIGNED AND SHORT-LIVED. Every picture draws the ad's initials
 *     underneath itself (Thumb, ui.jsx), so an expired link reads as a placeholder rather than a
 *     fault, and the sync re-signs the ads with the most recent spend every day
 *     (ads_sync._rank_for_refresh, ADS_CREATIVE_HOT_COUNT).
 *
 *   * Revenue per ad is NOT here, and the footnote says why rather than leaving a blank figure.
 *     It needs utm_content={{ad.id}} on the ads and a matching CRM field. A tile with an empty
 *     revenue figure invites the reading that the ad earned nothing, which is the specific wrong
 *     conclusion this module exists to prevent.
 */
import { num, pct, usd } from "./adsTokens.js";
import { GALLERY_SORTS, SORT_NOTE, gallerySort } from "./creative.js";
import { Empty, ErrorNote, Segmented, Thumb } from "./ui.jsx";

function Fig({ label, children }) {
  return (
    <span className="ax-tile__fig">
      <span className="ax-tile__label">{label}</span>
      <span className="ax-tile__value">{children}</span>
    </span>
  );
}

function Tile({ ad, on, onOpen }) {
  return (
    <button type="button" className={`ax-card ax-tile ax-lift${on ? " is-on" : ""}`}
            onClick={() => onOpen(ad)}>
      <Thumb src={ad.thumbnail_url} name={ad.name} />
      <span className="ax-tile__text">
        {/* The NAME is the operator's handle on the ad: what they called it in Ads Manager and
            will search for. The headline is what the audience saw. Both matter, and they are
            frequently nothing like each other. */}
        <span className="ax-tile__name" title={ad.name}>{ad.name}</span>
        {ad.headline && <span className="ax-tile__headline" title={ad.headline}>{ad.headline}</span>}
        {ad.fatigue && <span className="ax-tag ax-tile__tag">Fatigue</span>}
      </span>
      <span className="ax-tile__figs">
        <Fig label="Spend">{usd(ad.spend)}</Fig>
        <Fig label="Link CTR">{pct(ad.ctr)}</Fig>
        <Fig label="Frequency">{ad.frequency == null ? "—" : Number(ad.frequency).toFixed(2)}</Fig>
        {/* Meta's lead count is Meta measuring itself, so the label says whose number it is. */}
        <Fig label="Meta cost / lead">{ad.leads ? usd(ad.cpl, 2) : "—"}</Fig>
        <Fig label="Seats">{ad.seats == null ? "—" : num(ad.seats)}</Fig>
        <Fig label="Cost / seat">{usd(ad.cost_per_seat)}</Fig>
      </span>
    </button>
  );
}

function GallerySkeleton() {
  return (
    <div className="ax-gallery" role="status" aria-busy="true">
      <span className="ax-sr">Loading the ads</span>
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="ax-card ax-tile" aria-hidden="true">
          <span className="ax-sk" style={{ height: "auto", aspectRatio: "1 / 1" }} />
          <span className="ax-sk" style={{ width: "70%" }} />
          <span className="ax-sk" style={{ width: "45%" }} />
        </div>
      ))}
    </div>
  );
}

/* `ads` is the list after the screen's brand and name filters; `fetched` is how many the API
   sent (the highest-spending ads in the window, up to AdsView's limit) and `total` is how many
   delivered at all. */
export default function CreativeWall({
  ads, fetched, total, filtered, seatsAvailable, revenueReason,
  sort, onSort, openKey, onOpen, loading, error, onRetry,
}) {
  if (error) return <ErrorNote onRetry={onRetry}>Couldn&apos;t load the ads.</ErrorNote>;
  if (loading) return <GallerySkeleton />;
  if (!fetched) {
    // An empty gallery is normal for a window with no ad-level delivery, and it is not the same
    // thing as a failed sync. Say which one this is.
    return (
      <Empty>
        No ads delivered in this window. Widen the period, or check the sync in Settings if you
        expected spend here.
      </Empty>
    );
  }

  const shown = gallerySort(ads, seatsAvailable ? sort : "spend");
  const count = filtered
    ? `${shown.length} of the ${fetched} highest-spending ads match.`
    : total > fetched
      ? `The ${fetched} highest-spending of ${total} ads that delivered.`
      : null;

  return (
    <div className="ax-stack">
      <div className="ax-spread">
        {seatsAvailable
          ? <Segmented label="Order the gallery by" value={sort} onChange={onSort} options={GALLERY_SORTS} />
          : <span className="ax-meta">Ranked by spend. Seats per ad need utm_content on the ads.</span>}
        <span className="ax-facts ax-meta">
          {seatsAvailable && <span>{SORT_NOTE[sort]}</span>}
          {count && <span>{count}</span>}
        </span>
      </div>
      {shown.length === 0 ? <Empty>Nothing matches this filter.</Empty> : (
        <div className="ax-gallery">
          {shown.map((ad) => {
            const key = ad.external_id || ad.id;
            return <Tile key={key} ad={ad} on={openKey === key} onOpen={onOpen} />;
          })}
        </div>
      )}
      {revenueReason && <p className="ax-meta">Revenue per ad is not shown. {revenueReason}</p>}
    </div>
  );
}
