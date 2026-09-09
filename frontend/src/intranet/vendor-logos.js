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

/* Keyed by the product's words, because SUBSTRINGS ARE NOT NAMES. The first version matched
   `squash(name).includes(key)`, which handed Canva's logo to a tool called "Canvas" -- an LMS a
   training-heavy team plausibly links to, wearing a design tool's mark.

   A logo matches when its words are a PREFIX of the tool's words, so "Sisu Dashboard" is Sisu and
   "Canvas" is not Canva. The squashed whole name is tried first, which catches "SkySlope" written
   as "Sky Slope" and vice versa. */
const LOGOS = [
  { words: ["brivity"], src: brivity },
  { words: ["canva"], src: canva },
  { words: ["exp", "world", "campus"], src: expWorldCampus },
  { words: ["exp", "enterprise"], src: expEnterprise },
  { words: ["follow", "up", "boss"], src: followUpBoss },
  { words: ["sisu"], src: sisu },
  { words: ["skool"], src: skool },
  { words: ["skyslope"], src: skyslope },
  { words: ["slack"], src: slack },
  { words: ["sunburst"], src: sunburst },
  { words: ["sympli", "mortgage"], src: sympliMortgage },
  { words: ["sympli"], src: sympliMortgage },
]
  // Longest first, so "eXp World Campus" is never claimed by the shorter "eXp Enterprise" key and
  // "Sympli Mortgage" beats the bare "Sympli".
  .sort((a, b) => b.words.length - a.words.length);

const squash = (value) => String(value || "").toLowerCase().replace(/[^a-z0-9]/g, "");
const words = (value) => String(value || "").toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);

/** The logo for a tool name, or null to keep its initials. */
export function logoFor(name) {
  const flat = squash(name);
  if (!flat) return null;
  const exact = LOGOS.find((l) => squash(l.words.join("")) === flat);
  if (exact) return exact.src;
  const parts = words(name);
  const hit = LOGOS.find((l) => l.words.every((w, i) => parts[i] === w));
  return hit ? hit.src : null;
}
