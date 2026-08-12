import { useEffect, useState } from "react";
import { getJSON } from "./api";
import sampleSalesDesk from "./sampleSalesDesk.js";

const API = import.meta.env.VITE_API_BASE;

/* Sales Desk payload for the active launch. Same conventions as useLaunch: sample mode when
   no backend; 401 → logout; 404/403 → absent (not an error). */
export function useSalesDesk(businessKey = "springb") {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [usingSample, setUsingSample] = useState(false);
  const [exists, setExists] = useState(true);
  const [nonce, setNonce] = useState(0);
  const reload = () => setNonce((n) => n + 1);

  useEffect(() => {
    let alive = true;
    if (!API) {                                  // no backend → sample mode
      setData(sampleSalesDesk); setUsingSample(true); setExists(true); setError(null);
      return () => { alive = false; };
    }
    setError(null);
    getJSON(`/businesses/${businessKey}/launches/active/sales-desk`)
      .then((d) => { if (alive) { setData(d); setExists(true); setUsingSample(false); } })
      .catch((e) => {
        if (!alive) return;
        if (e && e.status === 401) { localStorage.removeItem("cc_token"); window.location.reload(); return; }
        if (e && (e.status === 404 || e.status === 403)) { setExists(false); setData(null); return; }
        setError(e);
      });
    return () => { alive = false; };
  }, [businessKey, nonce]);

  return { data, error, loading: !data && !error && exists, usingSample, exists, reload };
}
