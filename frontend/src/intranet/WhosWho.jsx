import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { getBlob, getJSON } from "../api.js";
import { useAuthedImage } from "../useAuthedImage.js";

/* WHO'S WHO (WHOS-WHO-WIN-THE-DAY-SPEC.md, phase 5).
 *
 * PORTED, NOT REDRAWN. The directory and the profile are the mockup's (`brand-src/mockups/
 * utah-life-intranet/template.html`, lines 1598-1717), their inline styles moved into `ut-who-*`
 * classes value for value and their colours onto the portal's tokens.
 *
 * THE CONTENT IS THE WORKSPACE'S. Who is featured, the team's numbers, who sits in Leadership and
 * in what order, and what each profile says are set in the console (Who's Who, and the profile
 * drawer on People & Roster) and arrive laid out: `content.directory` for the page, and
 * /intranet/directory/:id for a profile (services/whos_who).
 *
 * WHERE THE MOCKUP IS A PICTURE (spec 7.3): Agents can be expanded ("Show all"), an agent card
 * opens that person's profile, and the headings follow each person's pronoun. The agents grid
 * keeps the mockup's initials rather than photos -- a photo there would be one full-size fetch
 * per agent, for a 44px circle. */

function initials(name) {
  const parts = (name || "").trim().split(/\s+/).filter(Boolean);
  return parts.slice(0, 2).map((p) => p[0]).join("").toUpperCase();
}

// "Team Leader — SLC": the leadership card's line, as the mockup writes it.
const leaderLine = (p) => [p.title, p.market].filter(Boolean).join(" — ");
// "Buyer Agent · Salt Lake": the agents grid's.
const agentLine = (p) => [p.title, p.market].filter(Boolean).join(" · ");
// Under a name on the band and the profile: the subtitle they were given, else their market.
const subtitle = (p) => p.headline || p.market || "";

const isPortalPath = (url) => typeof url === "string" && url.startsWith("/");
const isWeb = (url) => /^https?:\/\//i.test(url || "");

/* A colleague's photo, fetched with the session -- the route is behind it and a bare <img src>
   sends none. Nothing until it arrives; `fallback` for somebody with no photo at all. */
function Photo({ person, className, fallback = null }) {
  const src = useAuthedImage(person.photo_url || null, getBlob);
  if (!person.photo_url) return fallback;
  return src
    ? <img className={className} src={src} alt="" style={{ objectPosition: person.photo_focus || "50% 12%" }} />
    : null;
}

function Head({ intro }) {
  return (
    <>
      <div className="ut-who-eyebrow">Team</div>
      {/* A straight apostrophe, as the mockup's title has (the rail's label is the same). */}
      <h1 className="ut-who-title">{"Who's Who"}</h1>
      {intro ? <p className="ut-who-lede">{intro}</p> : null}
    </>
  );
}

function Featured({ person, stats, onOpen }) {
  const sub = subtitle(person);
  return (
    <div className={`ut-who-band${person.photo_url ? "" : " no-photo"}`}>
      <div className="ut-who-band-copy">
        {person.eyebrow ? <div className="ut-who-band-eyebrow">{person.eyebrow}</div> : null}
        <h2 className="ut-who-band-name">{person.name}</h2>
        {sub ? <div className="ut-who-band-sub">{sub}</div> : null}
        {person.quote ? <p className="ut-who-band-quote">{person.quote}</p> : null}
        <div className="ut-who-band-actions">
          <button type="button" className="ut-who-band-button" onClick={onOpen}>View Profile</button>
        </div>
        {stats.length ? (
          <div className="ut-who-stats">
            {stats.map((t) => (
              <div key={t.label}>
                <div className="ut-who-stat-v">{t.value}</div>
                <div className="ut-who-stat-k">{t.label}</div>
              </div>
            ))}
          </div>
        ) : null}
      </div>
      {person.photo_url ? (
        <div className="ut-who-band-photo">
          <Photo person={person} className="ut-who-band-img" />
        </div>
      ) : null}
    </div>
  );
}

function Leader({ person, onOpen }) {
  return (
    <button type="button" className="ut-who-leader" onClick={onOpen}>
      <span className="ut-who-leader-photo">
        <Photo person={person} className="ut-who-leader-img"
               fallback={<span className="ut-who-leader-initials">{initials(person.name)}</span>} />
      </span>
      <span className="ut-who-leader-body">
        {leaderLine(person) ? <span className="ut-who-leader-eyebrow">{leaderLine(person)}</span> : null}
        <span className="ut-who-leader-name">{person.name}</span>
        {person.help_line ? <span className="ut-who-leader-help">{person.help_line}</span> : null}
        <span className="ut-who-leader-more">View profile &rarr;</span>
      </span>
    </button>
  );
}

function Agent({ person, onOpen }) {
  const tag = person.is_you ? "You" : person.tag;
  return (
    <button type="button" className="ut-who-agent" onClick={onOpen}>
      <span className="ut-who-initials">{initials(person.name)}</span>
      <span className="ut-who-agent-text">
        <span className="ut-who-agent-name">{person.name}</span>
        {agentLine(person) ? <span className="ut-who-agent-line">{agentLine(person)}</span> : null}
      </span>
      {tag ? <span className="ut-who-agent-tag">{tag}</span> : null}
    </button>
  );
}

function Section({ title, children }) {
  return (
    <div className="ut-who-section">
      <h2>{title}</h2>
      <span className="ut-who-rule" />
      {children}
    </div>
  );
}

export default function WhosWho({ config, canConfigure }) {
  const dir = config?.content?.directory || {};
  const navigate = useNavigate();
  const [allAgents, setAllAgents] = useState(false);
  const open = (id) => navigate(`/directory/${id}`);

  const featured = dir.featured || null;
  const leaders = dir.leadership || [];
  const agents = dir.agents || [];
  const limit = dir.preview_count || 0;
  const collapsed = limit > 0 && agents.length > limit && !allAgents;
  const shown = collapsed ? agents.slice(0, limit) : agents;

  if (!featured && !leaders.length && !agents.length) {
    return (
      <div className="ut-who">
        <Head intro="" />
        <p className="ut-who-lede">
          {canConfigure
            ? "Nobody is on Who's Who yet. Add the team in the console under People & Roster; everyone not hidden appears here."
            : "Your team has not been added yet."}
        </p>
      </div>
    );
  }

  return (
    <div className="ut-who">
      <Head intro={dir.intro} />

      {featured ? <Featured person={featured} stats={dir.stats || []} onOpen={() => open(featured.id)} /> : null}

      {leaders.length ? (
        <>
          <Section title="Leadership" />
          <div className={`ut-who-leaders${leaders.length < 3 ? " few" : ""}`}>
            {leaders.map((p) => <Leader key={p.id} person={p} onOpen={() => open(p.id)} />)}
          </div>
        </>
      ) : null}

      {agents.length ? (
        <>
          <Section title="Agents">
            <span className="ut-who-count">
              {collapsed ? `Showing ${shown.length} of ${agents.length}`
                : allAgents ? `Showing all ${agents.length}`
                  : `${agents.length} ${agents.length === 1 ? "agent" : "agents"}`}
              {collapsed || allAgents ? (
                <>
                  {" · "}
                  <button type="button" className="ut-who-count-toggle" onClick={() => setAllAgents(!allAgents)}>
                    {allAgents ? "Show fewer" : "Show all"}
                  </button>
                </>
              ) : null}
            </span>
          </Section>
          <div className="ut-who-agents">
            {shown.map((p) => <Agent key={p.id} person={p} onOpen={() => open(p.id)} />)}
          </div>
        </>
      ) : null}
    </div>
  );
}

// ── one person ─────────────────────────────────────────────────────────────────────────────

function Owned({ item }) {
  if (isPortalPath(item.url)) return <Link className="ut-who-owned" to={item.url}>{item.label}</Link>;
  if (isWeb(item.url)) {
    return <a className="ut-who-owned" href={item.url} target="_blank" rel="noreferrer noopener">{item.label}</a>;
  }
  return <span className="ut-who-owned plain">{item.label}</span>;
}

function paragraphs(text) {
  return (text || "").split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
}

export function Profile({ config }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const dir = config?.content?.directory || {};
  // The card the directory already sent draws the header at once; the rest follows.
  const known = [dir.featured, ...(dir.leadership || []), ...(dir.agents || [])].find((p) => p && p.id === id);
  const [state, setState] = useState({ id: null, person: null, missing: false });

  useEffect(() => {
    let live = true;
    getJSON(`/intranet/directory/${id}`)
      .then((person) => { if (live) setState({ id, person, missing: false }); })
      .catch(() => { if (live) setState({ id, person: null, missing: true }); });
    return () => { live = false; };
  }, [id]);

  const loaded = state.id === id ? state : { person: null, missing: false };
  const person = loaded.person || known || null;
  const back = (
    <button type="button" className="ut-who-back" onClick={() => navigate("/directory")}>
      &larr; Team directory
    </button>
  );

  if (!person) {
    return (
      <div className="ut-who">
        {back}
        {loaded.missing ? <p className="ut-who-lede">This person is not on Who&rsquo;s Who.</p> : null}
      </div>
    );
  }

  const full = loaded.person;
  const headings = full?.headings || {};
  const bio = paragraphs(full?.bio);
  // The card's one line of what to bring them stands in for a bring list nobody has written, so
  // the profile never says less than the card that led here.
  const bring = full?.bring?.length ? full.bring : (full && person.help_line ? [person.help_line] : []);
  const owns = full?.owns_items || [];
  const reach = full && (full.phone || full.email || full.office);
  const sub = subtitle(person);
  const message = full?.message_url;
  // Most agents' profiles are how to reach them and nothing to read.
  const readable = Boolean(person.quote || bio.length || bring.length);

  return (
    <div className="ut-who">
      {back}
      <div className="ut-who-profile">
        <div className="ut-who-profile-head">
          <Photo person={person} className="ut-who-profile-photo"
                 fallback={<span className="ut-who-profile-initials">{initials(person.name)}</span>} />
          <div className="ut-who-profile-id">
            {person.title ? <div className="ut-who-profile-eyebrow">{person.title}</div> : null}
            <h1 className="ut-who-profile-name">{person.name}</h1>
            {sub ? <div className="ut-who-profile-sub">{sub}</div> : null}
          </div>
          {message ? (
            <div className="ut-who-profile-actions">
              <a className="ut-who-message" href={message}
                 {...(isWeb(message) ? { target: "_blank", rel: "noreferrer noopener" } : {})}>
                Message
              </a>
            </div>
          ) : null}
        </div>

        {full ? (
          <div className={`ut-who-profile-body${readable ? "" : " side-only"}`}>
            {readable ? (
            <div className="ut-who-profile-main">
              {person.quote ? <p className="ut-who-quote">{person.quote}</p> : null}
              {bio.map((text, i) => <p className="ut-who-para" key={i}>{text}</p>)}
              {bring.length ? (
                <div className="ut-who-bring">
                  <div className="ut-who-label">{headings.bring}</div>
                  <div className="ut-who-bring-list">
                    {bring.map((line, i) => (
                      <div className="ut-who-bring-row" key={i}>
                        <span className="ut-who-dot" />
                        <span>{line}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
            ) : null}
            {reach || owns.length ? (
              <div className="ut-who-profile-side">
                {reach ? (
                  <div className="ut-who-box">
                    <div className="ut-who-box-label">{headings.reach}</div>
                    <div className="ut-who-reach">
                      {full.phone ? (
                        <div>
                          <span className="ut-who-reach-k">Direct</span><br />
                          <a className="ut-who-tel" href={`tel:${full.phone.replace(/[^\d+]/g, "")}`}>{full.phone}</a>
                        </div>
                      ) : null}
                      {full.email ? (
                        <div>
                          <span className="ut-who-reach-k">Email</span><br />
                          <a href={`mailto:${full.email}`}>{full.email}</a>
                        </div>
                      ) : null}
                      {full.office ? (
                        <div>
                          <span className="ut-who-reach-k">Office</span><br />
                          <span>{full.office}</span>
                        </div>
                      ) : null}
                    </div>
                  </div>
                ) : null}
                {owns.length ? (
                  <div className="ut-who-box">
                    <div className="ut-who-box-label">{headings.owns}</div>
                    <div className="ut-who-owns">
                      {owns.map((item, i) => <Owned key={`${item.kind}-${i}`} item={item} />)}
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
