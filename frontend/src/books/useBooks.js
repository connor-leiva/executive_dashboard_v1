/* Books data hooks — mirror useFinancials/useDashboard: {data, error, loading, retry},
   falling back to sample payloads when VITE_API_BASE is unset (dev/offline). */
import { useEffect, useState } from "react";
import { getJSON } from "../api";
import { sampleHome, samplePL, sampleQueue, sampleIC } from "./sampleBooks.js";

const API = import.meta.env.VITE_API_BASE;

function useEndpoint(path, sample, deps) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [nonce, setNonce] = useState(0);
  const retry = () => { setError(null); setNonce((n) => n + 1); };

  useEffect(() => {
    let alive = true;
    if (!API) { setData(sample); setError(null); return () => { alive = false; }; }
    setError(null);
    getJSON(path)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError(e); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { data, error, loading: !data && !error, retry };
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
