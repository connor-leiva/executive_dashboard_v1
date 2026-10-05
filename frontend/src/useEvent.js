import { useEffect, useState } from "react";
import { getJSON } from "./api";
import sampleEvent from "./sampleEvent.js";

const API = import.meta.env.VITE_API_BASE;

/* The Forum's current quarterly event (mirrors useLaunch exactly).

   A 404 is NOT an error — it means no event is configured, so the sub-tab is simply absent.
   A 401 is swallowed because a global listener handles session expiry; returning early here
   without touching state is what stops a expired session from flashing an error pane.

   `loading` is false once `exists` flips false, so a pane that renders only on `data` must
   handle the not-configured case itself rather than spinning forever.

   `nonce`/`reload` lets the settings drawer refetch after a save. */
export function useEvent(businessKey = "springb") {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [usingSample, setUsingSample] = useState(false);
  const [exists, setExists] = useState(true);   // assume until a 404 says otherwise
  const [nonce, setNonce] = useState(0);
  const reload = () => setNonce((n) => n + 1);

  useEffect(() => {
    let alive = true;
    if (!API) {
      setData(sampleEvent);
      setUsingSample(true);
      setExists(true);
      setError(null);
      return () => { alive = false; };
    }
    setError(null);
    getJSON(`/businesses/${businessKey}/events/current`)
      .then((d) => { if (alive) { setData(d); setExists(true); setUsingSample(false); } })
      .catch((e) => {
        if (!alive) return;
        if (e && e.status === 401) {
          return;
        }
        if (e && (e.status === 404 || e.status === 403)) {  // no event / no access → absent
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
