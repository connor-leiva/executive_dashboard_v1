import { useEffect, useState } from "react";
import { getJSON } from "./api";
import sampleData from "./sampleData.js";

const API = import.meta.env.VITE_API_BASE;

export function useDashboard(period = "mtd") {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [usingSample, setUsingSample] = useState(false);
  const [nonce, setNonce] = useState(0);
  const retry = () => {
    setError(null);
    setNonce((n) => n + 1);
  };

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

    // Keep the previous data visible while a new period loads (no full-screen
    // flash on switch); only the very first load shows the splash.
    setError(null);

    getJSON(`/dashboard?period=${period}`)
      .then((d) => {
        if (!alive) return;
        setData(d);
        setUsingSample(false);
      })
      .catch((e) => {
        if (!alive) return;
        if (e && e.status === 401) {
          // Session invalid/expired (bad token, token_version bumped, re-seed) →
          // clear it and reload to the login screen. A 403 is a real permission
          // signal (a tab a member lacks), never a logout.
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
  }, [period, nonce]);

  return { data, error, loading: !data && !error, usingSample, retry };
}
