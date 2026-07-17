import { useEffect, useState, useCallback } from "react";
import { getJSON } from "./api";
import sampleBinderRules from "./sampleBinderRules.js";

const API = import.meta.env.VITE_API_BASE;

/* The Binder jurisdiction rules (GET /binder/rules), with freshness flags. */
export function useBinderRules() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [usingSample, setUsingSample] = useState(false);

  const load = useCallback(() => {
    if (!API) { setData(sampleBinderRules); setUsingSample(true); setError(null); return; }
    setError(null);
    getJSON("/binder/rules")
      .then((d) => { setData(d); setUsingSample(false); })
      .catch((e) => {
        if (e && e.status === 401) { localStorage.removeItem("cc_token"); window.location.reload(); return; }
        setError(e);
      });
  }, []);

  useEffect(() => { load(); }, [load]);
  return { data, error, loading: !data && !error, usingSample, reload: load };
}
