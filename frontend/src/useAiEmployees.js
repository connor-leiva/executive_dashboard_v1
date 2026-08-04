import { useEffect, useState } from "react";
import { getJSON } from "./api";

const API = import.meta.env.VITE_API_BASE;

/* The AI Employees list ({employees, awaiting_total, writeback_env_open, can_manage}).
   `enabled` gates the fetch so the shell only calls it when the tab is granted (the badge
   needs the count even when the tab isn't the active view). 403/404 → an empty, harmless
   payload (feature off or no grant); `reload` refetches after a create/mutation. */
export function useAiEmployees(enabled = true) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [nonce, setNonce] = useState(0);
  const reload = () => setNonce((n) => n + 1);

  useEffect(() => {
    let alive = true;
    if (!API || !enabled) {
      setData(null);
      return () => { alive = false; };
    }
    setError(null);
    getJSON("/ai/employees")
      .then((d) => { if (alive) setData(d); })
      .catch((e) => {
        if (!alive) return;
        if (e && e.status === 401) {
          localStorage.removeItem("cc_token");
          window.location.reload();
          return;
        }
        if (e && (e.status === 404 || e.status === 403)) {   // feature off / no grant → empty
          setData({ employees: [], awaiting_total: 0, writeback_env_open: false, can_manage: false });
          return;
        }
        setError(e);
      });
    return () => { alive = false; };
  }, [enabled, nonce]);

  return { data, error, loading: enabled && !data && !error, reload };
}
