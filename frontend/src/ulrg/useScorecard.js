import { useEffect, useState } from "react";
import { getJSON, getPublic } from "../api.js";

const API = import.meta.env.VITE_API_BASE;

/* Fetches the L10 Scorecard payload (SPEC Part 5.1). `weeks` = the trailing window the API
   returns; the panel/window math is all server-side, so this hook only fetches + reloads.
   `shareToken` (optional) switches to the public, no-auth token endpoint for a ClickUp embed. */
export function useScorecard(weeks = 13, shareToken = null) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [nonce, setNonce] = useState(0);
  const reload = () => setNonce((n) => n + 1);

  useEffect(() => {
    let alive = true;
    if (!API) { setData(null); return () => { alive = false; }; }
    setError(null);
    const req = shareToken
      ? getPublic(`/share/${shareToken}/scorecard?weeks=${weeks}`)   // embed: no login, token-scoped
      : getJSON(`/ulrg/scorecard?weeks=${weeks}`);
    req.then((d) => { if (alive) setData(d); })
      .catch((e) => {
        if (!alive) return;
        if (e && e.status === 404) { setData({ groups: [] }); return; }   // not configured / dead link
        setError(e);
      });
    return () => { alive = false; };
  }, [weeks, nonce, shareToken]);

  return { data, error, reload };
}
