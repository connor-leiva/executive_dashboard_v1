/* Books data hooks — mirror useFinancials/useDashboard: {data, error, loading, retry},
   falling back to sample payloads when VITE_API_BASE is unset (dev/offline). */
import { useEffect, useState } from "react";
import { getJSON } from "../api";
import { sampleHome, samplePL, sampleQueue, sampleIC, sampleCoaEntities, sampleCoaMap,
         sampleStatement, sampleLineDetail } from "./sampleBooks.js";

const API = import.meta.env.VITE_API_BASE;

function useEndpoint(path, sample, deps) {
  // The payload is stored WITH the path it came from. Without that pairing, switching entity
  // (or mode, or period) leaves the previous entity's figures on screen — fully rendered, with
  // no loading state — until the new request lands. On a financial statement that is not a
  // cosmetic lag: it is one entity's name above another entity's numbers.
  const [loaded, setLoaded] = useState({ path: null, data: null });
  const [error, setError] = useState(null);
  const [nonce, setNonce] = useState(0);
  const retry = () => { setError(null); setNonce((n) => n + 1); };

  useEffect(() => {
    let alive = true;
    // A null path means "nothing to ask for yet" (no entity picked), not an error.
    if (!path) { setLoaded({ path: null, data: null }); setError(null); return () => { alive = false; }; }
    if (!API) { setLoaded({ path, data: sample }); setError(null); return () => { alive = false; }; }
    setError(null);
    getJSON(path)
      .then((d) => { if (alive) setLoaded({ path, data: d }); })
      .catch((e) => { if (alive) setError(e); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  // Only surface data that belongs to the path being asked for. A refresh (same path, bumped
  // nonce) deliberately keeps the old payload on screen rather than flashing a skeleton — the
  // number is still the right number, it is just about to be re-confirmed.
  const fresh = loaded.path === path ? loaded.data : null;

  // `refresh` is `retry` under another name: after a mutation the screen re-reads the
  // server rather than patching its own copy, so the counts in the header can never drift
  // from what the map actually says.
  return { data: fresh, error, loading: !!path && !fresh && !error, retry, refresh: retry };
}

export function useBooksHome(period = "mtd") {
  return useEndpoint(`/books?period=${period}`, sampleHome, [period]);
}
export function useBooksPL(business = "all", period = "mtd") {
  return useEndpoint(`/books/pl?business=${business}&period=${period}`,
                     samplePL[business] || samplePL.all, [business, period]);
}
export function useBooksQueue() {
  return useEndpoint(`/books/queue`, sampleQueue, []);
}
export function useBooksIC() {
  return useEndpoint(`/books/ic`, sampleIC, []);
}

export function useCoaEntities() {
  return useEndpoint(`/books/coa`, sampleCoaEntities, []);
}
export function useCoaMapping(businessId) {
  return useEndpoint(businessId ? `/books/coa/map?business_id=${businessId}` : null,
                     sampleCoaMap, [businessId]);
}

/* The mapped statement. `mode` and `threshold` are session state, never written back to
   coa_settings — the spec is explicit that changing the threshold on screen is an override,
   not a settings edit. */
export function useStatement(businessId, { period, mode = "allocated", threshold } = {}) {
  const q = new URLSearchParams({ business_id: businessId || "", mode });
  if (period) q.set("period", period);
  if (threshold !== undefined && threshold !== null) q.set("threshold_pct", String(threshold));
  return useEndpoint(businessId ? `/books/statement?${q}` : null, sampleStatement,
                     [businessId, period, mode, threshold]);
}

/* The transactions behind one line. Fetched on expand rather than inline: hundreds of rows per
   line have no business riding along on every statement request. */
export function useLineDetail(businessId, standardAccountId, period) {
  const q = new URLSearchParams({ business_id: businessId || "",
                                  standard_account_id: standardAccountId || "" });
  if (period) q.set("period", period);
  return useEndpoint(businessId && standardAccountId ? `/books/statement/line?${q}` : null,
                     sampleLineDetail, [businessId, standardAccountId, period]);
}
