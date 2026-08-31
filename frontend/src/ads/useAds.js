/* Ads data hooks. Mirrors useBooks, INCLUDING the pattern where the payload is stored with the
 * path it came from.
 *
 * That pairing is not a nicety here, it is the difference between a right and a wrong business
 * decision. Switch account, period, or cohort/period basis and the previous request's numbers
 * would otherwise stay on screen - fully rendered, no loading state - underneath the new
 * context's label. An ads figure under the wrong basis label is somebody deciding to kill a
 * campaign on a number that belongs to a different question. useBooks calls this out in a
 * comment for the same reason; it matters more on this tab.
 */
import { useEffect, useState } from "react";

import { getJSON } from "../api";
import { sampleAdsOverview, sampleAdsCreatives } from "./sampleAds.js";

const API = import.meta.env.VITE_API_BASE;

function useEndpoint(path, sample, deps) {
  const [loaded, setLoaded] = useState({ path: null, data: null });
  const [error, setError] = useState(null);
  const [nonce, setNonce] = useState(0);
  const retry = () => { setError(null); setNonce((n) => n + 1); };

  useEffect(() => {
    let alive = true;
    if (!path) { setLoaded({ path: null, data: null }); setError(null); return () => { alive = false; }; }
    // No API configured: bundled sample payload, which is obviously sample rather than a blank
    // page pretending to be a connected account with no spend.
    if (!API) { setLoaded({ path, data: sample }); setError(null); return () => { alive = false; }; }
    setError(null);
    getJSON(path)
      .then((d) => { if (alive) setLoaded({ path, data: d }); })
      .catch((e) => { if (alive) setError(e); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  // Only surface data belonging to the path currently being asked for.
  const fresh = loaded.path === path ? loaded.data : null;
  return { data: fresh, error, loading: !!path && !fresh && !error, retry };
}

const qs = (o) =>
  Object.entries(o)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
    .join("&");

export function useAdsAccounts() {
  return useEndpoint("/ads/accounts", sampleAdsOverview.accounts_list || [], []);
}

export function useAdsOverview({ account, period, basis, campaign }) {
  // `campaign` is in the deps as well as the path, for the reason this file exists: the payload
  // is stored WITH the path it came from, so switching launches cannot leave the previous
  // launch's fully-rendered figures on screen under the new launch's label.
  const path = `/ads?${qs({ account, period, basis, campaign })}`;
  return useEndpoint(path, sampleAdsOverview, [account, period, basis, campaign]);
}

export function useAdsCreatives({ account, period, campaign, sort, limit }) {
  const path = `/ads/creatives?${qs({ account, period, campaign, sort, limit })}`;
  return useEndpoint(path, sampleAdsCreatives, [account, period, campaign, sort, limit]);
}
