/* Audit actions in words, shared by a workspace's Activity pane and the fleet-wide Audit view so an
   action reads the same on both. */
import { titleCase } from "./format.js";

/* The actions worth a sentence. Anything else falls back to the entry's own summary, then to its
   action name made readable, so an action added later still shows up as something. */
export const PHRASES = {
  "tenant.created": "Created the workspace",
  "tenant.suspended": "Suspended the workspace",
  "tenant.resumed": "Resumed the workspace",
  "tenant.invite_resent": "Reissued the owner's invite",
  "tenant.sync_requested": "Started a sync",
  "tenant.reconnect_link_sent": "Sent a reconnect link",
  "tenant.setup_link_sent": "Sent a link to connect a first source",
  "tenant.syncs_frozen": "Froze syncs",
  "tenant.syncs_unfrozen": "Unfroze syncs",
  "tenant.share_links_revoked": "Revoked every live share link",
  "auth.login": "Signed in",
  "auth.login_failed": "Failed sign-in",
  "auth.login_blocked": "Sign-in refused",
  "auth.google_denied": "Google sign-in refused",
  "auth.forgot_password": "Asked for a password reset",
  "auth.password_reset": "Reset a password",
  "auth.find_workspace": "Looked up their workspaces",
  "user.invited": "Invited a person",
  "user.reinvited": "Resent an invite",
  "user.accepted_invite": "Accepted an invite",
  "user.role_changed": "Changed a role",
  "user.disabled": "Disabled a person",
  "user.enabled": "Re-enabled a person",
  "user.reset_link": "Sent a password reset link",
  "user.unlocked": "Unlocked an account",
  "totp.enabled": "Turned on two-factor",
  "totp.disabled": "Turned off two-factor",
  "step_up.granted": "Unlocked a protected section",
  "step_up.failed": "Failed a second-factor check",
  "integration.connected": "Connected a source",
  "integration.disconnected": "Disconnected a source",
  "access.member.activated": "Became active on the roster",
  "assistant.asked": "Asked the assistant a question",
  "support.access_opened": "Opened support access",
  "support.access_ended": "Ended support access",
  "support.access_expired": "Support access expired",
  "tenant.exported": "Exported the workspace's metadata",
  "tenant.ownership_transferred": "Transferred ownership",
  "tenant.deleted": "Deleted the workspace",
  "billing.plan_changed": "Changed the plan",
  "billing.budget_changed": "Changed the AI token budget",
  "billing.contact_changed": "Changed the billing contact",
  "billing.po_changed": "Changed the PO reference",
  "billing.customer_created": "Created the Stripe customer",
  "billing.payment_link_sent": "Sent a payment link",
  "billing.retried": "Retried a charge",
  "billing.retry_failed": "Retried a charge, which failed",
  "billing.past_due": "Subscription went past due",
  "billing.canceled": "Subscription was canceled",
  "billing.incomplete": "Subscription is incomplete",
  "billing.config_changed": "Changed the platform billing connection",
  "billing.disconnected": "Disconnected platform billing",
};

export function describe(e) {
  if (PHRASES[e.action]) return PHRASES[e.action];
  if (e.summary) return e.summary;
  return titleCase(e.action);
}

/** The detail line under an action. `withReason: false` leaves the reason out for a view that shows
    it on its own line. */
export function target(e, { withReason = true } = {}) {
  const d = e.detail || {};
  const bits = [];
  if (withReason && d.reason) bits.push(`Reason: ${d.reason}`);
  if (d.plan) bits.push(`plan ${d.plan}`);
  if (d.email) bits.push(d.email);
  if (d.provider) bits.push(d.provider);
  if (Array.isArray(d.sent_to) && d.sent_to.length) bits.push(`to ${d.sent_to.join(", ")}`);
  if (typeof d.count === "number") bits.push(`${d.count} ${d.count === 1 ? "link" : "links"}`);
  if (!bits.length && e.target_type) bits.push(e.target_type);
  return bits.join(" · ");
}

/* Repeated events collapse. Consecutive entries that share a key become one line with a count, so a
   run of identical rows (an autosave, a retried sync) can never bury the one action that mattered. */
export function collapseBy(events, key) {
  const out = [];
  for (const e of events) {
    const prev = out[out.length - 1];
    if (prev && key(prev) === key(e)) {
      prev.count += 1;
      continue;
    }
    out.push({ ...e, count: 1 });
  }
  return out;
}
