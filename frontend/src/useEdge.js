import { useEffect, useState } from "react";
import { getJSON } from "./api";
import sampleEdge from "./sampleEdge.js";

const API = import.meta.env.VITE_API_BASE;

/* The Edge focused-view payload (mirrors useForum / useBecollective). */
export function useEdge(period = "mtd") {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [usingSample, setUsingSample] = useState(false);

  useEffect(() => {
    let alive = true;
    if (!API) {
      setData(sampleEdge);
      setUsingSample(true);
      setError(null);
      return () => { alive = false; };
    }
    setError(null);
    getJSON(`/edge?period=${period}`)
      .then((d) => { if (alive) { setData(d); setUsingSample(false); } })
      .catch((e) => {
        if (!alive) return;
        if (e && e.status === 401) {   // bad/expired session — 403 (no tab access) is not a logout
          localStorage.removeItem("cc_token");
          window.location.reload();
          return;
        }
        setError(e);
      });
    return () => { alive = false; };
  }, [period]);

  return { data, error, loading: !data && !error, usingSample };
}
