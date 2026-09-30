import { useCallback, useEffect, useRef, useState } from "react";
import { getJSON } from "./api";

const API = import.meta.env.VITE_API_BASE;

/* The Onboarding tab's payload: {plans, plan}. One request for all five views, because they are
   five views over the SAME plan and five endpoints would be five round trips for one screen.

   `enabled` gates the fetch so the shell only calls it when the tab is granted. 403/404 resolve
   to an EMPTY payload rather than an error: no grant and no plan yet are both ordinary states,
   and a red banner is the wrong answer to "this workspace has not written one".

   `apply` is the optimistic half, and it does NOT refetch. A checkbox must respond to the click,
   but every number on screen is computed by the server from the rows, so the caller patches the
   row it touched, sends the write, and reloads when the write has COMMITTED. Reloading first --
   which is what this did -- races the write: ticking a block and judging its outcome in quick
   succession showed "2 hit, 0 missed" against a database holding one of each, and it stayed
   wrong until something else refetched. Nothing recomputes a total in the browser; that is how
   the two would drift for good rather than for a second.

   Out-of-order responses are dropped. Working through a day means clicking quickly, several
   reads end up in flight at once, and without the sequence check whichever one the network
   returned LAST won -- which is not the same as the newest. */
export function useOnboarding(enabled = true, planId = null) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [nonce, setNonce] = useState(0);
  const seq = useRef(0);
  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let alive = true;
    const mine = ++seq.current;
    if (!enabled) { setData(null); return () => { alive = false; }; }
    // No API configured is the OFFLINE preview, and the honest thing to draw there is the empty
    // state -- which is a real state, the one a workspace sees before anybody writes a plan.
    // Returning null instead leaves `loading` true forever and the tab spins on a spinner.
    if (!API) { setData({ plans: [], plan: null }); return () => { alive = false; }; }
    setError(null);
    getJSON(`/onboarding${planId ? `?plan_id=${encodeURIComponent(planId)}` : ""}`)
      .then((d) => { if (alive && mine === seq.current) setData(d); })
      .catch((e) => {
        if (!alive || mine !== seq.current || (e && e.status === 401)) return;
        if (e && (e.status === 404 || e.status === 403)) { setData({ plans: [], plan: null }); return; }
        setError(e);
      });
    return () => { alive = false; };
  }, [enabled, planId, nonce]);

  /* Edit one row in place so the control responds to the click. `fn` receives a copy of the plan
     and mutates the row that was touched; it must not touch a count, and it must not refetch --
     the caller reloads once its write has landed. */
  const apply = useCallback((fn) => {
    setData((prev) => {
      if (!prev || !prev.plan) return prev;
      const plan = JSON.parse(JSON.stringify(prev.plan));
      fn(plan);
      return { ...prev, plan };
    });
  }, []);

  return { data, error, loading: enabled && !data && !error, reload, apply };
}
