import { useEffect, useState, useCallback } from "react";
import { getJSON } from "./api";
import sampleBinderMatrix from "./sampleBinderMatrix.js";

const API = import.meta.env.VITE_API_BASE;

/* The Binder obligations matrix (GET /binder). Sample fallback in dev; reload() refetches
   after a confirm/complete elsewhere. Mirrors useBinder. */
export function useBinderMatrix() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [usingSample, setUsingSample] = useState(false);

  const load = useCallback(() => {
    if (!API) {
      setData(sampleBinderMatrix);
      setUsingSample(true);
      setError(null);
      return;
    }
    setError(null);
    getJSON("/binder")
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
