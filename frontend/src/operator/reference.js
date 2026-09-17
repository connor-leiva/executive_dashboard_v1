/* The platform's reference data: every plan exactly as plans.py defines it, the gated modules, the
   platform domain and the two reserved-host sets. Served by the API rather than copied into this
   bundle, so a limit the console shows is the limit the product enforces. Read once per page. */
import { api } from "./api.js";

let pending = null;

export function loadReference() {
  if (!pending) {
    pending = api.plans().catch((e) => {
      pending = null;
      throw e;
    });
  }
  return pending;
}

export const planByKey = (ref, key) => (ref ? ref.plans.find((p) => p.key === key) : null);
export const planName = (ref, key) => planByKey(ref, key)?.name || key;
