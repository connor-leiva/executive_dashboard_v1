/* What a triage or health action does when pressed.
 *
 * The server attaches actions to every signal (services/fleet_health.py) by key. This maps each key
 * to the call that performs it. A key with no call yet navigates to the pane where the operator can
 * act, so a button never claims to do something it does not do.
 */
import { api } from "./api.js";

const CALLS = {
  resend_owner_invite: async (slug) => {
    const r = await api.resendOwnerInvite(slug);
    return `A fresh 7-day invite went to ${r.owner_email}.`;
  },
};

export const canPerform = (action) => Boolean(action && CALLS[action.action]);

/** Run an action. Returns a sentence describing what happened, or null after navigating. */
export async function perform(action, slug, open) {
  if (!action) return null;
  if (CALLS[action.action]) return CALLS[action.action](slug, action);
  open(slug, action.pane || "overview");
  return null;
}

/** The label a button shows: the action's own, or where it will take you. */
export function labelFor(action) {
  if (!action) return null;
  if (canPerform(action) || action.action === "open") return action.label;
  return action.pane ? `Open ${action.pane}` : "Open workspace";
}
