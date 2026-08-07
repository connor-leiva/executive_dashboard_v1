import { useEffect, useState } from "react";
import { getJSON } from "../api.js";

const API = import.meta.env.VITE_API_BASE;

/* Fetches the L10 Scorecard payload (SPEC Part 5.1). `weeks` = the trailing window the API
   returns; the panel/window math is all server-side, so this hook only fetches + reloads. */
export function useScorecard(weeks = 13) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [nonce, setNonce] = useState(0);
  const reload = () => setNonce((n) => n + 1);

  useEffect(() => {
    let alive = true;
    if (!API) { setData(null); return () => { alive = false; }; }
    setError(null);
    getJSON(`/ulrg/scorecard?weeks=${weeks}`).then((d) => { if (alive) setData(d); })
      .catch((e) => {
        if (!alive) return;
        if (e && e.status === 404) { setData({ groups: [] }); return; }   // no scorecard configured
        setError(e);
      });
    return () => { alive = false; };
  }, [weeks, nonce]);

  return { data, error, reload };
}
