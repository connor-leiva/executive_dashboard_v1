import { useEffect, useState } from "react";
import { getJSON } from "./api";

const API = import.meta.env.VITE_API_BASE;

// The transactions panel for one business + period (GET /businesses/{key}/skyslope). Mirrors
// useFinancials, except that with no API base there is no bundled sample: the panel is only ever
// shown for a business the server says is SkySlope-sourced, so there is nothing to fake here.
export function useSkyslope(businessKey, period = "mtd", enabled = true) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [nonce, setNonce] = useState(0);
  const retry = () => {
    setError(null);
    setNonce((n) => n + 1);
  };

  useEffect(() => {
    let alive = true;
    if (!API || !enabled || !businessKey) return () => { alive = false; };
    setError(null);
    getJSON(`/businesses/${businessKey}/skyslope?period=${encodeURIComponent(period)}`)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError(e); });
    return () => { alive = false; };
  }, [businessKey, period, enabled, nonce]);

  return { data, error, loading: enabled && !data && !error, retry };
}
