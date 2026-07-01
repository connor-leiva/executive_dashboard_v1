import { useEffect, useState } from "react";
import { getJSON } from "./api";
import sampleData from "./sampleData.js";

const API = import.meta.env.VITE_API_BASE;

export function useDashboard(period = "mtd") {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [usingSample, setUsingSample] = useState(false);

  useEffect(() => {
    let alive = true;

    // Dev fallback: no API base configured → render bundled sample data.
    if (!API) {
      setData(sampleData);
      setUsingSample(true);
      setError(null);
      return () => {
        alive = false;
      };
    }

    setData(null);
    setError(null);
    setUsingSample(false);

    getJSON(`/dashboard?period=${period}`)
      .then((d) => {
        if (!alive) return;
        setData(d);
        setUsingSample(false);
      })
      .catch(() => {
        // Fetch failed → never leave the UI blank; fall back to sample data.
        if (!alive) return;
        setData(sampleData);
        setUsingSample(true);
      });

    return () => {
      alive = false;
    };
  }, [period]);

  return { data, error, loading: !data && !error, usingSample };
}
