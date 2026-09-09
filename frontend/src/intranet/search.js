/* Search across everything a member can see.
 *
 * NO BACKEND ENDPOINT, and that is the design rather than a shortcut.
 *
 * The searchable universe -- courses, lessons, SOPs, tools, people, pages -- is already in the
 * browser: the portal's config payload carries all of it, because every screen needs it. A search
 * endpoint would re-query the same rows, re-implement the same filtering, and give a second
 * chance to get that filtering wrong.
 *
 * SEARCH IS A READ PATH, and the read paths in this product are permission-gated: the SOP file
 * endpoint refuses a role whose sop_library is None, and filtering the listing alone would have
 * been cosmetic. Search has exactly the same hazard -- a result list that names documents you
 * cannot open is a permission leak wearing a search box. Searching the payload the server already
 * filtered makes that impossible BY CONSTRUCTION rather than by remembering: if the server did not
 * send it, it cannot be found.
 *
 * The honest limit: this searches titles and metadata, not the inside of an uploaded SOP
 * document, and it assumes the payload is small enough to ship -- which it is, because the portal
 * already ships it. A workspace with tens of thousands of records would need the endpoint and the
 * index; one with hundreds does not, and building the index first would be paying for a scale
 * nobody has while introducing a second copy of the permission rules.
 */

const MAX_PER_GROUP = 5;

function matches(haystack, needle) {
  return (haystack || "").toLowerCase().includes(needle);
}

/* Tags out of an authored body so a search for a phrase inside an article finds it.
   The server already sanitized this down to a small allowlist, so a regex is enough here --
   this is index text nobody renders, not a security boundary. */
function stripTags(html) {
  return (html || "").replace(/<[^>]*>/g, " ");
}

/** Everything in the payload, flattened into one shape the results list can render. */
function corpus(content) {
  const out = [];
  const c = content || {};

  (c.courses || []).forEach((course) => {
    out.push({
      type: "Training", title: course.title,
      detail: course.category || "Course", to: `/training/${course.id}`,
      text: [course.title, course.category, course.description].join(" "),
    });
    (course.lessons || []).forEach((lesson) => {
      out.push({
        type: "Training", title: lesson.title,
        detail: `Lesson · ${course.title}`, to: `/training/${course.id}/${lesson.id}`,
        // A READING LESSON'S WORDS ARE ITS CONTENT. Indexing the title alone would make an
        // article the one thing in the portal you cannot find by searching for what it says --
        // the body is stripped to text on the server, so this is a plain string, not markup.
        text: [lesson.title, lesson.source_label, course.title,
               lesson.description, stripTags(lesson.body_html)].join(" "),
      });
    });
  });

  (c.sops || []).forEach((sop) => {
    out.push({
      type: "SOPs", title: sop.title,
      detail: [sop.category, sop.version, sop.owner].filter(Boolean).join(" · "),
      to: "/sops",
      text: [sop.title, sop.category, sop.owner, sop.filename].join(" "),
    });
  });

  (c.tool_groups || []).forEach((group) => {
    (group.tools || []).forEach((tool) => {
      out.push({
        type: "Tools", title: tool.name, detail: group.label, to: "/tools",
        text: [tool.name, group.label, tool.url].join(" "),
      });
    });
  });

  (c.directory || []).forEach((person) => {
    out.push({
      type: "People", title: person.name,
      detail: [person.title || person.role, person.market].filter(Boolean).join(" · "),
      to: "/directory",
      // Searchable by what somebody OWNS, not just their name: "who handles compliance" is the
      // question a directory is actually opened for.
      text: [person.name, person.title, person.role, person.market, person.owns,
             person.email].join(" "),
    });
  });

  (c.wtd_lists || []).forEach((list) => {
    out.push({
      type: "Win the Day", title: list.name,
      detail: list.script_name || "Call list", to: "/wtd",
      text: [list.name, list.script_name].join(" "),
    });
  });

  (c.pages || []).forEach((page) => {
    out.push({
      type: "Pages", title: page.title,
      detail: page.subtitle || page.nav_group, to: `/p/${page.key}`,
      text: [page.title, page.subtitle,
             ...(page.sections || []).map((sec) => `${sec.heading || ""} ${sec.body || ""}`)]
        .join(" "),
    });
  });

  return out;
}

/** Grouped results for a query, or [] for a query too short to mean anything. */
export function search(content, query) {
  const needle = (query || "").trim().toLowerCase();
  if (needle.length < 2) return [];

  const hits = corpus(content).filter((item) => matches(item.text, needle));

  // Grouped in a fixed order so the list does not reshuffle as somebody types, and capped per
  // group so one type with many matches cannot bury the others.
  const order = ["People", "SOPs", "Training", "Tools", "Pages", "Win the Day"];
  return order
    .map((type) => ({ type, items: hits.filter((h) => h.type === type).slice(0, MAX_PER_GROUP) }))
    .filter((group) => group.items.length);
}
