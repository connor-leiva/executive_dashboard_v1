/* Audit events, said the way a person would say them.
 *
 * The API records an action as a dotted key (`auth.login`, `tenant.portal_created`) and, for
 * events nobody wrote a summary for, hands the same key back as the summary. Shown raw, the
 * console's activity read like a log file. Known keys get a sentence; anything else is turned
 * into words from the key itself, so a new event never shows up as code. */

const SENTENCES = {
  "auth.login": "Signed in",
  "auth.logout": "Signed out",
  "auth.password_changed": "Password changed",
  "tenant.portal_created": "Portal created",
  "coa.seed_standard_chart": "Standard chart of accounts loaded",
};

function looksLikeKey(text) {
  return /^[a-z0-9_]+(\.[a-z0-9_]+)+$/.test(String(text || ""));
}

function humanise(key) {
  const words = String(key).split(".").slice(-1)[0].replace(/_/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : String(key);
}

export function describeEvent(item) {
  const action = item?.action || "";
  const summary = item?.summary || "";
  if (summary && !looksLikeKey(summary) && summary !== action) return summary;
  return SENTENCES[action] || humanise(action || summary);
}

/** "Unknown" is what the API says when no person acted, which is a system event. */
export function actorName(label) {
  if (!label || /^unknown$/i.test(String(label).trim())) return "System";
  return label;
}
