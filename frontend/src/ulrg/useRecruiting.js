import { useCallback, useEffect, useState } from "react";
import { getJSON } from "../api.js";

const API = import.meta.env.VITE_API_BASE;

/* The Recruiting tab's payload (RECRUITING-SPEC §3), same shape as useScorecard.
 *
 * Every number on the tab is computed server-side and this hook only fetches. That is not a
 * style preference: two places computing pace produce two answers, and the one on screen is the
 * one somebody acts on.
 *
 * `as` previews another seat and is owner/admin only — the SERVER enforces that, not this hook.
 * A client-side check here would be a suggestion.
 */
export function useRecruiting({ period = null, as = null } = {}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let alive = true;
    if (!API) { setData(null); return () => { alive = false; }; }
    setError(null);
    const qs = new URLSearchParams();
    if (period) qs.set("period", period);
    if (as) qs.set("as", as);
    getJSON(`/ulrg/recruiting${qs.toString() ? `?${qs}` : ""}`)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => {
        if (!alive) return;
        // 403 is "you do not hold the ULRG tab", which the tab strip should not have offered.
        // Surfaced rather than blanked, because a silently empty tab reads as "no recruits".
        setError(e);
      });
    return () => { alive = false; };
  }, [period, as, nonce]);

  return { data, error, reload };
}

/* One candidate, fetched when the drawer opens rather than carried in the list payload.
   The list is every candidate on the page; the detail carries a person's contact record and
   notes, and the server scopes it per seat. Fetching it eagerly would ship a Team Leader the
   rows the scoping exists to withhold. */
export function useCandidate(candidateId) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    if (!API || !candidateId) return () => { alive = false; };
    getJSON(`/ulrg/recruiting/candidates/${candidateId}`)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError(e); });
    return () => { alive = false; };
  }, [candidateId]);

  return { data, error };
}
