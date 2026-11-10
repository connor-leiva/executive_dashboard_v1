import { useEffect, useState } from "react";
import { getJSON } from "../api.js";

const API = import.meta.env.VITE_API_BASE;

/* One report fetch, shaped like useDashboard: the previous payload stays on screen while the next
   loads (no flash on a filter change), a 401 is left to api.js's session handling, and any other
   failure is surfaced rather than swallowed. `path` null means "nothing to ask for yet". */
export function useReport(path) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(Boolean(path));
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!API || !path) { setLoading(false); return undefined; }
    let alive = true;
    setLoading(true);
    setError(null);
    getJSON(path)
      .then((d) => { if (alive) { setData(d); setLoading(false); } })
      .catch((e) => {
        if (!alive || (e && e.status === 401)) return;
        setError(e);
        setLoading(false);
      });
    return () => { alive = false; };
  }, [path, nonce]);

  return { data, error, loading, retry: () => setNonce((n) => n + 1), live: Boolean(API) };
}
