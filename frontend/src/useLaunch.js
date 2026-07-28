import { useEffect, useState } from "react";
import { getJSON } from "./api";
import sampleLaunch from "./sampleLaunch.js";

const API = import.meta.env.VITE_API_BASE;

/* The active beCollective launch for a business (mirrors useForum/useBecollective).
   A 404 is NOT an error — it means no active launch, so the sub-tab is simply absent.
   `nonce`/`reload` lets the settings drawer refetch after a save. */
export function useLaunch(businessKey = "springb") {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [usingSample, setUsingSample] = useState(false);
  const [exists, setExists] = useState(true);   // assume until a 404 says otherwise
  const [nonce, setNonce] = useState(0);
  const reload = () => setNonce((n) => n + 1);

  useEffect(() => {
    let alive = true;
    if (!API) {
      setData(sampleLaunch);
      setUsingSample(true);
      setExists(true);
      setError(null);
      return () => { alive = false; };
    }
    setError(null);
    getJSON(`/businesses/${businessKey}/launches/active`)
      .then((d) => { if (alive) { setData(d); setExists(true); setUsingSample(false); } })
      .catch((e) => {
        if (!alive) return;
        if (e && e.status === 401) {
          localStorage.removeItem("cc_token");
          window.location.reload();
          return;
        }
        if (e && (e.status === 404 || e.status === 403)) {  // no launch / no access → absent, not an error
          setExists(false);
          setData(null);
          return;
        }
        setError(e);
      });
    return () => { alive = false; };
  }, [businessKey, nonce]);

  return { data, error, loading: !data && !error && exists, usingSample, exists, reload };
}
