/* What a triage or health action does when pressed.
 *
 * The server attaches actions to every signal (services/fleet_health.py) by key, and this maps each
 * key to the call that performs it. `open` goes to a pane. A key with no call here also goes to its
 * pane, so a button never claims to do something it does not do, and a backend test fails if the
 * server attaches a key this file cannot perform.
 *
 * `confirm` marks an action that cannot be undone. Its button asks first, saying what will happen,
 * and only the second press runs it.
 */
import { api } from "./api.js";
import { joinAnd, plural } from "./format.js";

const CALLS = {
  resend_owner_invite: {
    run: async (slug) => {
      const r = await api.resendOwnerInvite(slug);
      return `A fresh invite went to ${r.owner_email}.`;
    },
  },
  reconnect_link: {
    run: async (slug, action) => {
      const r = await api.reconnectLink(slug, action.source_id);
      return `A link to reconnect ${r.provider_name} went to ${joinAnd(r.sent_to)}. Only they can reauthorise it.`;
    },
  },
  send_setup_link: {
    run: async (slug) => {
      const r = await api.setupLink(slug);
      return `A link to connect a first source went to ${joinAnd(r.sent_to)}.`;
    },
  },
  sync_source: {
    run: async (slug, action) => {
      const r = await api.syncSource(slug, action.source_id);
      return `${r.provider_name} is syncing now. Refresh in a minute to see how it went.`;
    },
  },
  resend_idle_invites: {
    run: async (slug) => {
      const r = await api.resendIdleInvites(slug);
      return `Fresh invites went to ${joinAnd(r.sent_to)}.`;
    },
  },
  payment_link: {
    run: async (slug) => {
      const r = await api.paymentLink(slug);
      return `Stripe's payment page for ${r.invoice} went to ${r.sent_to}.`;
    },
  },
  revoke_share_links: {
    confirm: "Every live share link in this workspace stops working at once, and a revoked link cannot be restored. The workspace can make new ones.",
    run: async (slug) => {
      const r = await api.revokeShareLinks(slug);
      return `Revoked ${plural(r.revoked, "share link")}. They now read as not found.`;
    },
  },
};

export const canPerform = (action) => Boolean(action && CALLS[action.action]);

/** The warning an irreversible action shows before it runs, or null. */
export const confirmFor = (action) => (canPerform(action) && CALLS[action.action].confirm) || null;

/** Run an action. Returns a sentence describing what happened, or null after navigating. */
export async function perform(action, slug, open) {
  if (!action) return null;
  if (CALLS[action.action]) return CALLS[action.action].run(slug, action);
  open(slug, action.pane || "overview");
  return null;
}

/** The label a button shows: the action's own, or where it will take you. */
export function labelFor(action) {
  if (!action) return null;
  if (canPerform(action) || action.action === "open") return action.label;
  return action.pane ? `Open ${action.pane}` : "Open workspace";
}
