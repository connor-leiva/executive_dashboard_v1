import { useEffect, useState } from "react";
import { getJSON } from "./api";
import sampleFinancials from "./sampleFinancials.js";

const API = import.meta.env.VITE_API_BASE;

// Fetch the three-lens financials for a business + period. Mirrors useDashboard:
// no API base → bundled sample; keeps prior data visible across a period switch.
export function useFinancials(businessKey, period = "mtd") {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [nonce, setNonce] = useState(0);
  const retry = () => {
    setError(null);
    setNonce((n) => n + 1);
  };

  useEffect(() => {
    let alive = true;
    if (!API) {
      setData(sampleFinancials);
      setError(null);
      return () => { alive = false; };
    }
    setError(null);
    getJSON(`/businesses/${businessKey}/financials?period=${period}`)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError(e); });
    return () => { alive = false; };
  }, [businessKey, period, nonce]);

  return { data, error, loading: !data && !error, retry };
}
