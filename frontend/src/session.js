/* WHERE THE SESSION LIVES. One implementation, because there is one session.
 *
 * The dashboard, the portal and the admin console are three bundles served from the SAME host,
 * and they share one key -- so they are one sign-in whether or not anybody wrote that down. They
 * did not behave like it: the dashboard read sessionStorage then localStorage, and the console
 * read only localStorage, with its own copy of the rule. Three things followed, all of them real:
 *
 *   - Signing in to the dashboard WITHOUT "remember me" put the token in sessionStorage, so
 *     opening /console on the same host asked you to sign in again, to the same account.
 *   - The console's sign-out cleared only localStorage, so a dashboard session in sessionStorage
 *     SURVIVED signing out of the console.
 *   - The console always wrote to localStorage and never asked the server to remember, so the
 *     browser kept a token past the tab closing that the server had already given a short life:
 *     the storage promise and the expiry disagreed, on the shared machine the promise is for.
 *
 * "Remember me" is a promise about a shared machine, so honouring it only on the server would be
 * honouring half of it: a short-lived token still sitting in localStorage is readable by whoever
 * opens the browser next. Unchecked, the token goes in sessionStorage and dies with the tab;
 * checked, it goes in localStorage and the server gives it a month.
 *
 * Reads try sessionStorage FIRST, because it is the more recent decision: signing in without the
 * box on a machine where somebody once ticked it must not resurrect the old long session. Writes
 * clear both stores before writing, for the same reason.
 *
 * Every access goes through here. The comment this replaces said "two components used to call
 * localStorage.getItem directly, which would have worked for exactly as long as there was only
 * one place to look" -- and then a whole second app did it again.
 */
export const TOKEN_KEY = "cc_token";
const STEP_UP_PREFIX = "cc_stepup_";

/** The stored session, or null. Never the view-as token: that layers on top, in api.js. */
export function readToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;                     // private window, or storage blocked entirely
  }
}

export function writeToken(token, remember) {
  try { localStorage.removeItem(TOKEN_KEY); } catch { /* nothing to clear */ }
  try { sessionStorage.removeItem(TOKEN_KEY); } catch { /* nothing to clear */ }
  try {
    (remember ? localStorage : sessionStorage).setItem(TOKEN_KEY, token);
  } catch {
    // Storage refused. The in-memory session still works until the page reloads, which is a
    // better outcome than throwing out of a successful sign-in.
  }
}

/** Replace the token, keeping where it was kept.
 *
 * Changing a password bumps token_version, which is exactly what makes every other session stop
 * working -- including the tab it was changed from. The server hands back a fresh token for that
 * reason, and dropping it signs the person out of the page they just succeeded on. */
export function refreshToken(token) {
  if (!token) return;
  let remember = false;
  try {
    remember = Boolean(localStorage.getItem(TOKEN_KEY));
  } catch {
    remember = false;               // storage blocked: keep it in memory for this page's life
  }
  writeToken(token, remember);
}

/** Clear the session from BOTH stores, and any step-up grant with it: never leave a section
 *  unlocked for whoever signs in next on this machine. */
export function clearToken() {
  try { localStorage.removeItem(TOKEN_KEY); } catch { /* nothing to clear */ }
  try { sessionStorage.removeItem(TOKEN_KEY); } catch { /* nothing to clear */ }
  try {
    Object.keys(sessionStorage).filter((k) => k.startsWith(STEP_UP_PREFIX))
      .forEach((k) => sessionStorage.removeItem(k));
  } catch { /* nothing to clear */ }
}
