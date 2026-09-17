/* Which of the site's two hosts this is, and the address of the other.
 *
 * Both are derived from the host the page was served on rather than written down, so a local
 * build (localhost:5176 and app.localhost:5176) links to its own finder and never to
 * production's — and a trailing dot, which a browser keeps in location.hostname, is dropped the
 * same way the API drops it before it resolves a host.
 */

const bare = (hostname) => String(hostname || "").toLowerCase().replace(/\.+$/, "");
const platform = (loc) => bare(loc.hostname).replace(/^(app|www)\./, "");
const origin = (loc, host) => `${loc.protocol}//${host}${loc.port ? `:${loc.port}` : ""}`;

/** True on app.<domain>, the workspace finder. */
export function isFrontDoorHost(loc = window.location) {
  return bare(loc.hostname).startsWith("app.");
}

/** The marketing site, from the finder. www rather than the apex: www serves the site today and
 *  redirects to the apex once that is live (frontend/Caddyfile), so it is right on both sides of
 *  the cutover. The bare apex is right on only one. */
export function marketingOrigin(loc = window.location) {
  return origin(loc, `www.${platform(loc)}`);
}

/** The workspace finder, where "Sign in" goes. */
export function frontDoorUrl(loc = window.location) {
  return `${origin(loc, `app.${platform(loc)}`)}/`;
}
