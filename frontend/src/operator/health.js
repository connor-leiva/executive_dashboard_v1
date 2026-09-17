/* A workspace's state and the reasons for it.
 *
 * THE CONSOLE'S LAW: no status without its reason. Every state below arrives with the plain-English
 * lines that produced it, and nothing is stored: a state is derived from facts on every read, so
 * fixing the cause clears the label on the next load.
 *
 * The server derives it (services/fleet_health.py) and sends `health` on every workspace row.
 * This module only orders and describes what the server decided, so the list, the detail header
 * and the triage queue cannot disagree with each other or with the API.
 */
import { STATE } from "./tokens.js";

export const RANK = Object.fromEntries(Object.entries(STATE).map(([k, v]) => [k, v.rank]));

export function healthOf(row) {
  const h = row && row.health;
  if (h && STATE[h.state]) return h;
  return { state: "healthy", why: [], derivation: "" };
}

export const byTrouble = (a, b) =>
  RANK[healthOf(a).state] - RANK[healthOf(b).state] || String(a.name).localeCompare(String(b.name));
