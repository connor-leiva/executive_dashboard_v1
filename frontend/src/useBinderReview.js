import { useEffect, useState, useCallback } from "react";
import { getJSON } from "./api";
import sampleBinderReview from "./sampleBinderReview.js";

const API = import.meta.env.VITE_API_BASE;

/* The Binder review queue (GET /binder/review). Sample fallback in dev; exposes reload()
   so confirm/dismiss can refetch. Mirrors useBinder. */
export function useBinderReview() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [usingSample, setUsingSample] = useState(false);

  const load = useCallback(() => {
    if (!API) {
      setData(sampleBinderReview);
      setUsingSample(true);
      setError(null);
      return;
    }
    setError(null);
    getJSON("/binder/review")
      .then((d) => { setData(d); setUsingSample(false); })
      .catch((e) => {
        if (e && e.status === 401) {
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
