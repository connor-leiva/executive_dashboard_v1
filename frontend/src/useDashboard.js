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
      .catch((e) => {
        if (!alive) return;
        if (e && (e.status === 401 || e.status === 403)) {
          // Token no longer valid (e.g. after a re-seed changed the tenant) →
          // clear it and reload so the app shows the login screen.
          localStorage.removeItem("cc_token");
          window.location.reload();
          return;
        }
        // A real API error (500, network) — surface it instead of silently
        // showing sample data, which hides prod problems.
        setError(e);
      });

    return () => {
      alive = false;
    };
  }, [period]);

  return { data, error, loading: !data && !error, usingSample };
}
