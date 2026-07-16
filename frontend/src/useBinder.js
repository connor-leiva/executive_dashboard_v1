import { useEffect, useState, useCallback } from "react";
import { getJSON } from "./api";
import sampleBinder from "./sampleBinder.js";

const API = import.meta.env.VITE_API_BASE;

/* Binder entity list. Mirrors useForum: sample fallback in dev (no VITE_API_BASE),
   surfaces real API errors, and exposes reload() so mutations can refetch. */
export function useBinder() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [usingSample, setUsingSample] = useState(false);

  const load = useCallback(() => {
    if (!API) {
      setData(sampleBinder);
      setUsingSample(true);
      setError(null);
      return;
    }
    setError(null);
    getJSON("/binder/entities")
      .then((d) => { setData(d); setUsingSample(false); })
      .catch((e) => {
        if (e && e.status === 401) {   // bad/expired session — 403 (no tab access) is not a logout
          localStorage.removeItem("cc_token");
          window.location.reload();
          return;
        }
        setError(e);
      });
  }, []);

  useEffect(() => { load(); }, [load]);

  return { data, error, loading: !data && !error, usingSample, reload: load };
}
