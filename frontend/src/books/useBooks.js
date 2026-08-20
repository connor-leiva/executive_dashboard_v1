/* Books data hooks — mirror useFinancials/useDashboard: {data, error, loading, retry},
   falling back to sample payloads when VITE_API_BASE is unset (dev/offline). */
import { useEffect, useState } from "react";
import { getJSON } from "../api";
import { sampleHome, samplePL, sampleQueue, sampleIC, sampleCoaEntities, sampleCoaMap } from "./sampleBooks.js";

const API = import.meta.env.VITE_API_BASE;

function useEndpoint(path, sample, deps) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [nonce, setNonce] = useState(0);
  const retry = () => { setError(null); setNonce((n) => n + 1); };

  useEffect(() => {
    let alive = true;
    // A null path means "nothing to ask for yet" (no entity picked), not an error.
    if (!path) { setData(null); setError(null); return () => { alive = false; }; }
    if (!API) { setData(sample); setError(null); return () => { alive = false; }; }
    setError(null);
    getJSON(path)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError(e); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  // `refresh` is `retry` under another name: after a mutation the screen re-reads the
  // server rather than patching its own copy, so the counts in the header can never drift
  // from what the map actually says.
  return { data, error, loading: !!path && !data && !error, retry, refresh: retry };
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
