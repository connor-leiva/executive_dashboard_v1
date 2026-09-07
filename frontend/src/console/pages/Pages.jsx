/* Pages a workspace writes for itself.
 *
 * This screen is the whole argument for the feature. JV Partners, Listing Marketing, Sunburst
 * Coaching and On The Phone were four bespoke screens holding one customer's content, and
 * building them that way meant building four more for the next customer. Here a workspace
 * writes its own -- and the fifth page nobody has thought of yet.
 */
import { useEffect, useState } from "react";

import { COPY } from "../constants.js";
import {
  useCreatePage,
  useCreatePageSection,
  useDeletePage,
  useDeletePageSection,
  usePages,
  usePatchPage,
  usePatchPageSection,
  useRoles,
} from "../queries.js";
import { Button, ErrorState, Field, LoadingState, Panel } from "../ui.jsx";

/* The rail's own group names. Offered as suggestions rather than enforced as a list: the server
   accepts any group and the portal renders one it has not seen before as a group of its own, so
   a workspace inventing "Partners & Vendors" gets it. */
const NAV_GROUPS = ["Workspace", "Learn", "Team", "Marketing", "Partners"];

function slugify(title) {
  return title.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);
}

function NewPage({ onCreate, busy }) {
  const [title, setTitle] = useState("");
  const [key, setKey] = useState("");
  const [navGroup, setNavGroup] = useState(NAV_GROUPS[0]);
  const [error, setError] = useState("");
  // Typed once, derived after: the address follows the title until somebody edits it, and then
  // it is theirs. An admin should not have to think about URLs to add a page.
  const [keyTouched, setKeyTouched] = useState(false);

  useEffect(() => {
    if (!keyTouched) setKey(slugify(title));
  }, [title, keyTouched]);

  async function submit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    setError("");
    try {
      await onCreate({ title: title.trim(), key, nav_group: navGroup });
      setTitle("");
      setKey("");
      setKeyTouched(false);
      form.reset();
    } catch (err) {
      setError(err?.detail || err?.message || "Could not create that page.");
    }
  }

  return (
    <Panel title="New page">
      <form className="roster-form" onSubmit={submit}>
        <Field label="Title">
          <input value={title} onChange={(e) => setTitle(e.target.value)} required
                 placeholder="JV Partners" />
        </Field>
        <Field label="Address">
          <input value={key} required
                 onChange={(e) => { setKeyTouched(true); setKey(e.target.value); }} />
        </Field>
        <p className="console-help">
          Members will find it at <code>/intranet/p/{key || "your-page"}</code>. Lowercase
          letters, numbers and hyphens.
        </p>
        <Field label="Sidebar group">
          <select value={navGroup} onChange={(e) => setNavGroup(e.target.value)}>
            {NAV_GROUPS.map((g) => <option key={g} value={g}>{g}</option>)}
          </select>
        </Field>
        {error ? <p className="console-form-error">{error}</p> : null}
        <Button type="submit" tone="primary" busy={busy}>Create page</Button>
      </form>
    </Panel>
  );
}

function SectionEditor({ page, section, onSave, onDelete, busy }) {
  const [heading, setHeading] = useState(section.heading || "");
  const [body, setBody] = useState(section.body || "");
  const [links, setLinks] = useState(section.links || []);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  function setLink(i, field, value) {
    setLinks((rows) => rows.map((row, j) => (j === i ? { ...row, [field]: value } : row)));
  }

  async function save() {
    setError("");
    setSaved(false);
    try {
      // Blank rows are dropped rather than rejected: an admin who added a link box and changed
      // their mind should not have to find and empty it to save the rest of the section.
      const clean = links.filter((l) => (l.label || "").trim() && (l.url || "").trim());
      await onSave({ heading: heading.trim() || null, body: body.trim() || null, links: clean });
      setLinks(clean);
      setSaved(true);
    } catch (err) {
      setError(err?.detail || err?.message || "Could not save that section.");
    }
  }

  return (
    <div className="page-section-editor">
      <Field label="Heading">
        <input value={heading} onChange={(e) => setHeading(e.target.value)}
               placeholder="Who to call" />
      </Field>
      <Field label="Body">
        <textarea value={body} rows={5} onChange={(e) => setBody(e.target.value)}
                  placeholder="Leave a blank line between paragraphs." />
      </Field>
      <p className="console-help">Links</p>
      {links.map((link, i) => (
        <div className="console-actions" key={i}>
          <input value={link.label || ""} placeholder="Label"
                 onChange={(e) => setLink(i, "label", e.target.value)} />
          <input value={link.url || ""} placeholder="https://"
                 onChange={(e) => setLink(i, "url", e.target.value)} />
          <Button type="button" tone="danger"
                  onClick={() => setLinks((rows) => rows.filter((_, j) => j !== i))}>
            Remove
          </Button>
        </div>
      ))}
      <div className="console-actions">
        <Button type="button" onClick={() => setLinks((rows) => [...rows, { label: "", url: "" }])}>
          Add link
        </Button>
        <Button type="button" tone="primary" busy={busy} onClick={save}>Save section</Button>
        <Button type="button" tone="danger" onClick={onDelete}>Delete section</Button>
      </div>
      {error ? <p className="console-form-error">{error}</p> : null}
      {saved && !error ? <p className="console-help">Saved.</p> : null}
    </div>
  );
}

function PageEditor({ page, roles, mutations }) {
  const { patchPage, deletePage, createSection, patchSection, deleteSection } = mutations;
  const [title, setTitle] = useState(page.title);
  const [subtitle, setSubtitle] = useState(page.subtitle || "");
  const [navGroup, setNavGroup] = useState(page.nav_group || "Workspace");
  const [roleIds, setRoleIds] = useState(page.role_ids || []);
  const [error, setError] = useState("");

  useEffect(() => {
    setTitle(page.title);
    setSubtitle(page.subtitle || "");
    setNavGroup(page.nav_group || "Workspace");
    setRoleIds(page.role_ids || []);
  }, [page.id, page.title, page.subtitle, page.nav_group]);

  async function saveDetails() {
    setError("");
    try {
      await patchPage.mutateAsync({
        pageId: page.id,
        body: { title: title.trim(), subtitle: subtitle.trim() || null,
                nav_group: navGroup, role_ids: roleIds },
      });
    } catch (err) {
      setError(err?.detail || err?.message || "Could not save that page.");
    }
  }

  return (
    <Panel title={page.title}
           action={<Button tone="danger" onClick={() => deletePage.mutate(page.id)}>Delete page</Button>}>
      <p className="console-help">
        <code>/intranet/p/{page.key}</code>
        {page.draft_dirty ? " · unpublished changes" : page.published_at ? " · live" : " · never published"}
      </p>
      <Field label="Title">
        <input value={title} onChange={(e) => setTitle(e.target.value)} />
      </Field>
      <Field label="Subtitle">
        <input value={subtitle} onChange={(e) => setSubtitle(e.target.value)} />
      </Field>
      <Field label="Sidebar group">
        <select value={navGroup} onChange={(e) => setNavGroup(e.target.value)}>
          {[...new Set([...NAV_GROUPS, navGroup])].map((g) => (
            <option key={g} value={g}>{g}</option>
          ))}
        </select>
      </Field>
      <p className="console-help">
        Who can see it. Tick nobody and everyone sees it — the same rule as tools and courses.
      </p>
      <div className="chips">
        {(roles || []).map((role) => (
          <label className="check" key={role.id}>
            <input type="checkbox" checked={roleIds.includes(role.id)}
                   onChange={() => setRoleIds((ids) => ids.includes(role.id)
                     ? ids.filter((x) => x !== role.id) : [...ids, role.id])} />
            <span>{role.name}</span>
          </label>
        ))}
      </div>
      {error ? <p className="console-form-error">{error}</p> : null}
      <div className="console-actions">
        <Button tone="primary" busy={patchPage.isPending} onClick={saveDetails}>Save page</Button>
      </div>

      {(page.sections || []).map((section) => (
        <SectionEditor
          key={section.id}
          page={page}
          section={section}
          busy={patchSection.isPending}
          onSave={(body) => patchSection.mutateAsync({ pageId: page.id, sectionId: section.id, body })}
          onDelete={() => deleteSection.mutate({ pageId: page.id, sectionId: section.id })}
        />
      ))}
      <div className="console-actions">
        <Button busy={createSection.isPending}
                onClick={() => createSection.mutate({ pageId: page.id, body: {} })}>
          Add section
        </Button>
      </div>
    </Panel>
  );
}

export default function Pages() {
  const pages = usePages(true);
  const roles = useRoles(true);
  const createPage = useCreatePage();
  const mutations = {
    patchPage: usePatchPage(),
    deletePage: useDeletePage(),
    createSection: useCreatePageSection(),
    patchSection: usePatchPageSection(),
    deleteSection: useDeletePageSection(),
  };

  if (pages.isLoading) return <LoadingState title={COPY.loading} />;
  if (pages.isError) {
    return <ErrorState title="Could not load pages" onRetry={() => pages.refetch()} />;
  }

  const items = pages.data?.items || [];
  return (
    <div className="stack">
      <NewPage busy={createPage.isPending}
               onCreate={(body) => createPage.mutateAsync(body)} />
      {!items.length ? (
        <Panel title="No pages yet">
          <p className="console-help">
            Add one above. Pages appear in your team&rsquo;s sidebar under the group you choose,
            and are the place for anything the built-in screens do not cover — partners,
            a listing process, a vendor you work with.
          </p>
        </Panel>
      ) : items.map((page) => (
        <PageEditor key={page.id} page={page} roles={roles.data?.items} mutations={mutations} />
      ))}
    </div>
  );
}
