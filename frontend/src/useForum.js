import { useEffect, useState } from "react";
import { getJSON } from "./api";
import sampleForum from "./sampleForum.js";

const API = import.meta.env.VITE_API_BASE;

/* The Forum focused view payload. Mirrors useDashboard: sample fallback in dev,
   keeps prior data visible across period changes, surfaces real API errors. */
export function useForum(period = "mtd", enabled = true) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [usingSample, setUsingSample] = useState(false);

  useEffect(() => {
    let alive = true;
    // Not every tenant runs this program. The API correctly refuses a tab the tenant does not
    // have, so fetching regardless meant a wall of 403s on every load for anyone but the tenant
    // this view was built for — and a "failed" panel where there is simply nothing to show.
    if (!enabled) {
      setData(null);
      setError(null);
      return () => { alive = false; };
    }
    if (!API) {
      setData(sampleForum);
      setUsingSample(true);
      setError(null);
      return () => { alive = false; };
    }
    setError(null);
    getJSON(`/forum?period=${period}`)
      .then((d) => { if (alive) { setData(d); setUsingSample(false); } })
      .catch((e) => {
        if (!alive) return;
        if (e && e.status === 401) {   // bad/expired session — 403 (no tab access) is not a logout
          localStorage.removeItem("cc_token");
          window.location.reload();
          return;
        }
        setError(e);
      });
    return () => { alive = false; };
  }, [period, enabled]);

  return { data, error, loading: enabled && !data && !error, usingSample };
}
