import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { getBlob, getJSON, postJSON } from "../api.js";
import { useAuthedImage } from "../useAuthedImage.js";

/* THE SOP LIBRARY (SOP-LIBRARY-SPEC.md, phases 3-4).
 *
 * PORTED, NOT REDRAWN. Both screens are the mockup's (`brand-src/mockups/utah-life-intranet/
 * template.html`, lines 791-865 and 867-930), their inline styles moved into `ut-sop-*` classes
 * value for value and their colours onto the portal's tokens.
 *
 * A PROCEDURE IS SOMETHING YOU READ. It was a row in a table whose title downloaded a file; it is
 * now a page with numbered steps, the one thing not to skip, the tools it needs and the person to
 * ask -- and, for the ones that are still a document, the document, read in the page where it can
 * be (D1). */

const initials = (name) => (name || "").trim().split(/\s+/).filter(Boolean)
  .slice(0, 2).map((p) => p[0]).join("").toUpperCase();

const firstName = (name) => (name || "").trim().split(/\s+/)[0] || "";

function shortDate(value) {
  if (!value) return "";
  const date = new Date(`${String(value).slice(0, 10)}T00:00:00`);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

// ── the library ────────────────────────────────────────────────────────────────────────────

function Card({ sop, onOpen }) {
  return (
    <button type="button" className="ut-sop-card" onClick={onOpen}>
      <span className="ut-sop-card-top">
        {sop.department ? <span className="ut-sop-chip">{sop.department}</span> : null}
        {sop.version ? <span className="ut-sop-card-v">{sop.version}</span> : null}
      </span>
      <span className="ut-sop-card-title">{sop.title}</span>
      {sop.summary ? <span className="ut-sop-card-sum">{sop.summary}</span> : null}
      <span className="ut-sop-card-foot">
        <span className="ut-sop-card-who">{initials(sop.owner?.name)}</span>
        <span className="ut-sop-card-owner">{sop.owner?.name || "Unassigned"}</span>
        <span className="ut-sop-card-when">{shortDate(sop.updated_on)}</span>
      </span>
    </button>
  );
}

export default function Sops({ config, canConfigure }) {
  const sops = config?.content?.sops || [];
  const departments = config?.content?.sop_departments || [];
  const navigate = useNavigate();
  const [department, setDepartment] = useState("");
  const [query, setQuery] = useState("");

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return sops.filter((s) => (!department || s.department === department)
      && (!q || [s.title, s.summary, s.owner?.name, ...(s.steps || [])]
        .filter(Boolean).join(" ").toLowerCase().includes(q)));
  }, [sops, department, query]);

  // The mockup's dark panel. Derived from the revision dates, and not drawn at all when nothing
  // changed this month -- a panel headed "Changed This Month" listing April is a lie.
  const changed = useMemo(() => {
    const month = new Date().toISOString().slice(0, 7);
    return sops.filter((s) => (s.updated_on || "").startsWith(month))
      .sort((a, b) => b.updated_on.localeCompare(a.updated_on)).slice(0, 3);
  }, [sops]);

  if (!sops.length) {
    // Denied and empty are different noes: sending somebody to ask an admin why the library is
    // empty, when their role simply may not open it, is the wrong question.
    const denied = (config?.content?.capabilities || {}).sop_library === "None";
    return (
      <div className="ut-sop">
        <div className="ut-sop-eyebrow">Learn</div>
        <h1 className="ut-sop-title">Standard Operating Procedures</h1>
        <p className="ut-sop-lede">
          {denied
            ? "Your role does not have access to the SOP library. Ask an admin if that looks wrong."
            : canConfigure
              ? "Nothing is published yet. Write the procedures in the console under SOP Library, then publish."
              : "Your team has not published any procedures yet."}
        </p>
      </div>
    );
  }

  return (
    <div className="ut-sop">
      <div className="ut-sop-eyebrow">Learn</div>
      <h1 className="ut-sop-title">Standard Operating Procedures</h1>
      <p className="ut-sop-lede">
        The way we do it here. Every procedure has one owner and a version number, so you always
        know who to ask and whether you&rsquo;re reading the current one.
      </p>

      <div className="ut-sop-cols">
        <aside className="ut-sop-rail">
          <input className="ut-sop-search" value={query} placeholder="Find a procedure…"
                 aria-label="Find a procedure" onChange={(e) => setQuery(e.target.value)} />
          <div>
            <div className="ut-sop-rail-label">Departments</div>
            <div className="ut-sop-depts">
              {departments.map((d) => (
                <button type="button" key={d.name} onClick={() => setDepartment(d.key)}
                        className={d.key === department ? "on" : ""}>
                  <span>{d.name}</span><span className="ut-sop-dept-n">{d.count}</span>
                </button>
              ))}
            </div>
          </div>
          <div className="ut-sop-suggest">
            <div className="ut-sop-suggest-t">Something out of date?</div>
            <div className="ut-sop-suggest-d">
              Tell the owner. Procedures change because someone in the field said so.
            </div>
          </div>
        </aside>

        <div className="ut-sop-main">
          {changed.length ? (
            <div className="ut-sop-changed">
              <div className="ut-sop-changed-label">Changed This Month</div>
              <div className="ut-sop-changed-list">
                {changed.map((s) => (
                  <button type="button" key={s.id} onClick={() => navigate(`/sops/${s.id}`)}>
                    <span className="ut-sop-changed-v">{s.version}</span>
                    <span className="ut-sop-changed-mid">
                      <span className="ut-sop-changed-t">{s.title}</span>
                      <span className="ut-sop-changed-m">
                        {[s.owner?.name, shortDate(s.updated_on)].filter(Boolean).join(" · ")}
                      </span>
                    </span>
                    <span className="ut-sop-changed-go">Open &rarr;</span>
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          <div className="ut-sop-listhead">
            <h2>{department || "All Procedures"}</h2>
            <span>{`${shown.length} ${shown.length === 1 ? "procedure" : "procedures"}`}</span>
          </div>
          <div className="ut-sop-cards">
            {shown.map((sop) => (
              <Card key={sop.id} sop={sop} onOpen={() => navigate(`/sops/${sop.id}`)} />
            ))}
          </div>
          {!shown.length ? <p className="ut-sop-none">Nothing matches that.</p> : null}
        </div>
      </div>
    </div>
  );
}

// ── one procedure ──────────────────────────────────────────────────────────────────────────

function Document({ document: doc }) {
  // The bytes are behind the session, so the file is fetched with it and shown from an object
  // URL -- a plain src would 401, exactly as the directory photos did.
  const src = useAuthedImage(doc?.url ? `${doc.url}?disposition=inline` : null, getBlob);
  if (!doc) return null;
  return (
    <div className="ut-sop-doc-file">
      <div className="ut-sop-label">The document</div>
      {doc.inline && src ? (
        <iframe className="ut-sop-embed" src={src} title={doc.filename || "The document"} />
      ) : null}
      <a className="ut-sop-download" href={src || undefined} download={doc.filename || "procedure"}>
        {doc.filename ? `Download ${doc.filename}` : "Download the document"}
      </a>
    </div>
  );
}

export function Procedure({ config }) {
  const { id } = useParams();
  const navigate = useNavigate();
  const known = (config?.content?.sops || []).find((s) => s.id === id) || null;
  const [state, setState] = useState({ id: null, sop: null, missing: false });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    getJSON(`/intranet/sops/${id}`)
      .then((sop) => { if (live) setState({ id, sop, missing: false }); })
      .catch(() => { if (live) setState({ id, sop: null, missing: true }); });
    return () => { live = false; };
  }, [id]);

  const loaded = state.id === id ? state : { sop: null, missing: false };
  const sop = loaded.sop || known;
  const back = (
    <button type="button" className="ut-sop-back" onClick={() => navigate("/sops")}>
      &larr; All SOPs
    </button>
  );

  async function acknowledge() {
    setSaving(true);
    setError("");
    try {
      const done = await postJSON(`/intranet/sops/${id}/acknowledge`, {});
      setState((s) => ({ ...s, sop: { ...s.sop, acknowledged_at: done.acknowledged_at,
                                      acknowledged_count: (s.sop.acknowledged_count || 0) + 1 } }));
    } catch {
      setError("That didn't save. Try again in a moment.");
    } finally {
      setSaving(false);
    }
  }

  if (!sop) {
    return (
      <div className="ut-sop">
        {back}
        {loaded.missing ? <p className="ut-sop-lede">This procedure is not in the library.</p> : null}
      </div>
    );
  }

  const full = loaded.sop;
  const steps = full?.steps || [];
  const owner = sop.owner;
  const acknowledged = sop.acknowledged_at;
  const note = acknowledged
    ? `Logged ${shortDate(acknowledged)}.${owner?.name ? ` ${firstName(owner.name)} can see it.` : ""}`
    : (full && full.acknowledged_count !== undefined && sop.version
      ? `${full.acknowledged_count} of ${full.team_size} people have acknowledged ${sop.version}.`
      : "");

  return (
    <div className="ut-sop">
      {back}
      <div className="ut-sop-page">
        <div className="ut-sop-doc">
          <div className="ut-sop-doc-eyebrow">
            {[sop.department, sop.version].filter(Boolean).join(" · ")}
          </div>
          <h1 className="ut-sop-doc-title">{sop.title}</h1>
          <div className="ut-sop-meta">
            {owner?.name ? (
              <div><div className="ut-sop-meta-k">Owner</div><div className="ut-sop-meta-v">{owner.name}</div></div>
            ) : null}
            {sop.updated_on ? (
              <div><div className="ut-sop-meta-k">Last updated</div><div className="ut-sop-meta-v">{shortDate(sop.updated_on)}</div></div>
            ) : null}
            {full?.applies_to ? (
              <div><div className="ut-sop-meta-k">Applies to</div><div className="ut-sop-meta-v">{full.applies_to}</div></div>
            ) : null}
          </div>

          {full?.intro ? <p className="ut-sop-intro">{full.intro}</p> : null}

          {steps.length ? (
            <div className="ut-sop-steps">
              {steps.map((step) => (
                <div className="ut-sop-step" id={step.anchor} key={step.anchor}>
                  <span className="ut-sop-step-n">{step.index}</span>
                  <div>
                    <div className="ut-sop-step-t">{step.title}</div>
                    {step.text ? <div className="ut-sop-step-d">{step.text}</div> : null}
                  </div>
                </div>
              ))}
            </div>
          ) : null}

          {full?.callout ? (
            <div className="ut-sop-callout">
              <div className="ut-sop-callout-label">{full.callout.label}</div>
              <div className="ut-sop-callout-text">{full.callout.text}</div>
            </div>
          ) : null}

          {full?.document ? <Document document={full.document} /> : null}

          {sop.version ? (
            <div className="ut-sop-ack">
              <button type="button" className="ut-sop-ack-btn" disabled={saving || Boolean(acknowledged)}
                      onClick={acknowledge}>
                {acknowledged ? "Acknowledged ✓" : "I’ve read this"}
              </button>
              <span className="ut-sop-ack-note">{error || note}</span>
            </div>
          ) : null}
        </div>

        <aside className="ut-sop-side">
          {steps.length ? (
            <div className="ut-sop-side-card">
              <div className="ut-sop-label">On This Page</div>
              <div className="ut-sop-toc">
                {steps.map((step) => (
                  <a key={step.anchor} href={`#${step.anchor}`}>{step.index} · {step.title}</a>
                ))}
              </div>
            </div>
          ) : null}
          {full?.tools?.length ? (
            <div className="ut-sop-side-card">
              <div className="ut-sop-label">Tools you&rsquo;ll need</div>
              <div className="ut-sop-tools">
                {full.tools.map((tool) => (
                  <a key={tool.id} href={tool.url} target="_blank" rel="noreferrer noopener">{tool.name}</a>
                ))}
              </div>
            </div>
          ) : null}
          {owner?.name ? (
            <div className="ut-sop-side-card">
              <div className="ut-sop-label">Questions On This?</div>
              <div className="ut-sop-owner">
                <span className="ut-sop-owner-who">{initials(owner.name)}</span>
                <div>
                  <div className="ut-sop-owner-n">{owner.name}</div>
                  {owner.title ? <div className="ut-sop-owner-r">{owner.title}</div> : null}
                </div>
              </div>
              {owner.message_url ? (
                <a className="ut-sop-owner-go" href={owner.message_url}
                   {...(/^https?:/i.test(owner.message_url)
                     ? { target: "_blank", rel: "noreferrer noopener" } : {})}>
                  Send a Message
                </a>
              ) : null}
              {owner.profile_url ? (
                <button type="button" className="ut-sop-owner-profile"
                        onClick={() => navigate(owner.profile_url)}>
                  Their profile
                </button>
              ) : null}
            </div>
          ) : null}
        </aside>
      </div>
    </div>
  );
}
