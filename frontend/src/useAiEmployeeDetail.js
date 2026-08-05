import { useEffect, useState } from "react";
import { getJSON } from "./api";

const API = import.meta.env.VITE_API_BASE;

/* Employee detail data: run history + roster + briefs, plus the selected run's detail
   (defaults to the latest run) with polling while it's queued/running (SPEC run-surface
   §4.2 — 4s interval, paused when the tab is hidden). `setRunId` switches the surface to
   a historical run; `reload` refetches everything after an approve/mutation. */
export function useAiEmployeeDetail(employeeId, enabled = true) {
  const [runs, setRuns] = useState(null);      // history (summaries)
  const [roster, setRoster] = useState(null);
  const [briefs, setBriefs] = useState(null);
  const [mediaById, setMediaById] = useState({});   // asset id → { url, … } so previews show real photos
  const [runId, setRunId] = useState(null);    // the run shown on the surface
  const [detail, setDetail] = useState(null);  // { run, artifacts }
  const [error, setError] = useState(null);
  const [nonce, setNonce] = useState(0);
  const reload = () => setNonce((n) => n + 1);

  // history + roster + briefs (and pick the latest run as the default surface)
  useEffect(() => {
    let alive = true;
    if (!API || !enabled || !employeeId) return () => { alive = false; };
    setError(null);
    Promise.all([
      getJSON(`/ai/employees/${employeeId}/runs?size=25`),
      getJSON(`/ai/employees/${employeeId}/roster`),
      getJSON(`/ai/employees/${employeeId}/briefs`),
      getJSON(`/ai/employees/${employeeId}/media`).catch(() => ({ assets: [] })),
    ]).then(([r, ro, b, m]) => {
      if (!alive) return;
      setRuns(r.runs || []);
      setRoster(ro.roster || []);
      setBriefs(b.briefs || []);
      setMediaById(Object.fromEntries((m.assets || []).map((a) => [a.id, a])));
      setRunId((prev) => prev || (r.runs && r.runs[0] && r.runs[0].id) || null);
    }).catch((e) => {
      if (!alive) return;
      if (e && e.status === 401) { localStorage.removeItem("cc_token"); window.location.reload(); return; }
      setError(e);
    });
    return () => { alive = false; };
  }, [employeeId, enabled, nonce]);

  // selected run detail, polled while non-terminal
  useEffect(() => {
    let alive = true, timer = null;
    if (!API || !runId) { setDetail(null); return () => { alive = false; }; }
    const tick = () => {
      getJSON(`/ai/runs/${runId}`).then((d) => {
        if (!alive) return;
        setDetail(d);
        const st = d.run && d.run.status;
        if ((st === "queued" || st === "running") && !document.hidden) {
          timer = setTimeout(tick, 4000);
        }
      }).catch((e) => { if (alive) setError(e); });
    };
    tick();
    const onVis = () => { if (!document.hidden && alive) tick(); };
    document.addEventListener("visibilitychange", onVis);
    return () => { alive = false; if (timer) clearTimeout(timer); document.removeEventListener("visibilitychange", onVis); };
  }, [runId, nonce]);

  return { runs, roster, briefs, mediaById, detail, runId, setRunId, error, reload };
}
