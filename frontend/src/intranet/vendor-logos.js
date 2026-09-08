/* Vendor logos for tool tiles.
 *
 * The design pack ships eleven of these and the portal was rendering every tool as two initials in
 * a grey square, so a launchpad of real products looked like a list of placeholders.
 *
 * MATCHED ON THE TOOL'S NAME, because that is all a workspace gives us -- the console's Tool
 * Launchpad has a name and a URL, and asking an admin to also pick a logo from a list only we can
 * see would be a third field to get wrong. Matching is on a squashed form of the name, so "Follow
 * Up Boss", "follow-up-boss" and "FollowUpBoss" all land on the same file.
 *
 * A tool we have no logo for keeps its initials. That is the point of the fallback: this list is
 * eleven products out of everything a team might link to, and a missing logo should look
 * deliberate rather than broken.
 */
import brivity from "./assets/logos/brivity.png";
import canva from "./assets/logos/canva.png";
import expEnterprise from "./assets/logos/exp-enterprise.png";
import expWorldCampus from "./assets/logos/exp-world-campus.png";
import followUpBoss from "./assets/logos/follow-up-boss.png";
import sisu from "./assets/logos/sisu.png";
import skool from "./assets/logos/skool.png";
import skyslope from "./assets/logos/skyslope.png";
import slack from "./assets/logos/slack.png";
import sunburst from "./assets/logos/sunburst.png";
import sympliMortgage from "./assets/logos/sympli-mortgage.png";

/* Keys are the squashed name. Order matters for the prefix pass below: the longer eXp key has to
   be tried before the shorter one, or "exp world campus" matches "expenterprise" first. */
const LOGOS = {
  brivity,
  canva,
  expworldcampus: expWorldCampus,
  expenterprise: expEnterprise,
  followupboss: followUpBoss,
  sisu,
  skool,
  skyslope,
  slack,
  sunburst,
  symplimortgage: sympliMortgage,
  sympli: sympliMortgage,
};

function squash(name) {
  return String(name || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

/** The logo for a tool name, or null to keep its initials. */
export function logoFor(name) {
  const key = squash(name);
  if (!key) return null;
  if (LOGOS[key]) return LOGOS[key];
  // A workspace names a tile "Sisu Dashboard" or "Slack — #help" far more often than it names it
  // exactly. Longest key first so "eXp World Campus" cannot be claimed by a shorter match.
  const hit = Object.keys(LOGOS)
    .sort((a, b) => b.length - a.length)
    .find((k) => key.startsWith(k) || key.includes(k));
  return hit ? LOGOS[hit] : null;
}
