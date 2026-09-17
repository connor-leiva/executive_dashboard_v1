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

