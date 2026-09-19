import { useEffect, useRef, useState } from "react";

import { useAuthedImage } from "../useAuthedImage.js";
import { getMemberPhoto, namedError } from "./api.js";
import { useDeleteMemberPhoto, usePatchProfile, useUploadMemberPhoto } from "./queries.js";
import { Button, Field } from "./ui.jsx";

/* WHAT WHO'S WHO SAYS ABOUT SOMEBODY (WHOS-WHO-WIN-THE-DAY-SPEC.md, phase 4).
 *
 * One drawer, opened from Who's Who and from People & Roster, so a profile is edited in exactly
 * one place whichever screen somebody started from. Everything here is immediate -- the roster is
 * not staged content -- and the server checks every field (services/whos_who), naming the one it
 * refuses.
 *
 * THE PHOTO'S FOCUS is a click on the photo. The same point centres every crop the portal draws:
 * the tall leadership card, the round avatar on the profile, and the featured band. */

// `path` is "<member id>#<version>": the version changes after an upload, so the preview
// refetches instead of showing the photo that was just replaced.
const photoBlob = (path) => getMemberPhoto(path.split("#")[0]);

const PRONOUNS = {
  they: "They (Bring Them, They Own)",
  she: "She (Bring Her, She Owns)",
  he: "He (Bring Him, He Owns)",
};
const PLACEMENTS = {
  auto: "Automatic (by role)",
  leadership: "Leadership",
  agents: "Agents",
  hidden: "Hidden from Who's Who",
};

// The server names a refused field by its key ("owns_items.1.url"); say it the way this drawer
// labels it ("Owned item 2, link"), so the message points at a box somebody can see.
const FIELD_LABEL = {
  title: "Title", market: "Market", tag: "Tag", headline: "Subtitle", pronoun: "Pronoun",
  help_line: "What to bring them", quote: "Quote", bio: "Bio", phone: "Direct phone",
  office: "Office", message_url: "Message button", photo_focus: "Photo focus",
  directory_placement: "Shown in", file: "Photo", bring: "Bring them", owns_items: "Owned items",
};

function fieldName(key) {
  const [head, index, part] = String(key || "").split(".");
  if (index === undefined) return FIELD_LABEL[head] || null;
  if (head === "bring") return `Bring them, line ${Number(index) + 1}`;
  if (head === "owns_items") return `Owned item ${Number(index) + 1}${part === "url" ? ", link" : ""}`;
  return null;
}

const errorText = (err) => namedError(err, fieldName);

function draftOf(member) {
  return {
    title: member.title || "", market: member.market || "", headline: member.headline || "",
    tag: member.tag || "", pronoun: member.pronoun || "they", help_line: member.help_line || "",
    quote: member.quote || "", bio: member.bio || "", bring: [...(member.bring || [])],
    phone: member.phone || "", office: member.office || "", message_url: member.message_url || "",
    owns_items: (member.owns_items || []).map((o) => ({ label: o.label || "", url: o.url || "" })),
    photo_focus: member.photo_focus || "50% 12%",
    directory_placement: member.directory_placement || "auto",
  };
}

function focusParts(value) {
  const m = /^(\d{1,3})% (\d{1,3})%$/.exec(value || "");
  return m ? [Number(m[1]), Number(m[2])] : [50, 12];
}

function Photo({ member, focus, onFocus }) {
  const upload = useUploadMemberPhoto();
  const remove = useDeleteMemberPhoto();
  const [version, setVersion] = useState(0);
  const [error, setError] = useState("");
  const fileRef = useRef(null);
  const src = useAuthedImage(member.has_photo ? `${member.id}#${version}` : null, photoBlob);
  const [x, y] = focusParts(focus);

  async function pick(event) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setError("");
    try {
      await upload.mutateAsync({ memberId: member.id, file });
      setVersion((v) => v + 1);
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function drop() {
    setError("");
    try {
      await remove.mutateAsync(member.id);
      setVersion((v) => v + 1);
    } catch (err) {
      setError(errorText(err));
    }
  }

  function setFocus(event) {
    const box = event.currentTarget.getBoundingClientRect();
    const px = Math.round(((event.clientX - box.left) / box.width) * 100);
    const py = Math.round(((event.clientY - box.top) / box.height) * 100);
    onFocus(`${Math.max(0, Math.min(100, px))}% ${Math.max(0, Math.min(100, py))}%`);
  }

  return (
    <div className="pe-photo">
      <div className="pe-photo-main">
        {src ? (
          <button type="button" className="pe-photo-pick" onClick={setFocus}
                  title="Click the face: every crop centres here"
                  aria-label="Photo: click where every crop should centre">
            <img src={src} alt="" />
            <span className="pe-focus" style={{ left: `${x}%`, top: `${y}%` }} aria-hidden />
          </button>
        ) : (
          <div className="pe-photo-empty">No photo. Their initials show instead.</div>
        )}
        <div className="followup-actions">
          <Button type="button" onClick={() => fileRef.current?.click()} busy={upload.isPending}>
            {member.has_photo ? "Replace photo" : "Upload a photo"}
          </Button>
          {member.has_photo ? <Button type="button" onClick={drop} busy={remove.isPending}>Remove</Button> : null}
          <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp" hidden onChange={pick} />
        </div>
        <p className="followup-hint">
          JPEG, PNG or WebP, up to 15 MB. It is resized and its location data removed before it is
          kept. {src ? "Click the photo where the crops should centre." : ""}
        </p>
        {error ? <p className="console-form-error">{error}</p> : null}
      </div>
      {src ? (
        <div className="pe-crops" aria-label="How the portal will crop it">
          <div className="pe-crop card"><img src={src} alt="" style={{ objectPosition: `${x}% ${y}%` }} /><span>Leadership card</span></div>
          <div className="pe-crop round"><img src={src} alt="" style={{ objectPosition: `${x}% ${y}%` }} /><span>Profile</span></div>
          <div className="pe-crop band"><img src={src} alt="" style={{ objectPosition: `${x}% ${y}%` }} /><span>Featured</span></div>
        </div>
      ) : null}
    </div>
  );
}

function Lines({ label, items, max, onChange, placeholder }) {
  return (
    <fieldset className="pe-lines">
      <legend>{label}</legend>
      {items.map((line, i) => (
        <div className="pe-line" key={i}>
          <input value={line} maxLength={140} placeholder={placeholder}
                 onChange={(e) => onChange(items.map((l, j) => (j === i ? e.target.value : l)))} />
          <button type="button" onClick={() => onChange(items.filter((_, j) => j !== i))}>Remove</button>
        </div>
      ))}
      {items.length < max ? (
        <button type="button" className="pe-add" onClick={() => onChange([...items, ""])}>Add a line</button>
      ) : null}
    </fieldset>
  );
}

export default function ProfileEditor({ member, onClose }) {
  const save = usePatchProfile();
  const [draft, setDraft] = useState(() => draftOf(member));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const panelRef = useRef(null);
  const set = (field, value) => setDraft((d) => ({ ...d, [field]: value }));

  // Focus once, on opening. The parent passes onClose inline, so an effect keyed on it re-ran on
  // every parent render -- after each save's refetch -- and pulled focus out of the field being
  // typed in. The key handler reads the latest onClose through a ref instead.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    panelRef.current?.focus();
    const onKey = (event) => { if (event.key === "Escape") closeRef.current(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  async function submit(event) {
    event.preventDefault();
    setMessage("");
    setError("");
    try {
      await save.mutateAsync({ memberId: member.id, body: {
        title: draft.title, market: draft.market || null, headline: draft.headline, tag: draft.tag,
        pronoun: draft.pronoun, help_line: draft.help_line, quote: draft.quote, bio: draft.bio,
        // Sent as shown, blanks included: the server skips blank lines itself, and a line it
        // refuses is then numbered as the drawer numbers it.
        bring: draft.bring, phone: draft.phone, office: draft.office,
        message_url: draft.message_url,
        owns_items: draft.owns_items.map((o) => ({ label: o.label, url: o.url || null })),
        photo_focus: draft.photo_focus, directory_placement: draft.directory_placement,
      } });
      setMessage("Saved. Who's Who shows it on the next page load.");
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <div className="pe-overlay" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <aside className="pe-drawer" role="dialog" aria-modal="true" aria-label={`${member.full_name}'s profile`}
             tabIndex={-1} ref={panelRef}>
        <header className="pe-head">
          <div>
            <span>Who&rsquo;s Who profile</span>
            <strong>{member.full_name}</strong>
          </div>
          <button type="button" className="pe-close" onClick={onClose} aria-label="Close">&times;</button>
        </header>
        <form className="pe-body wtdc-form" onSubmit={submit}>
          <Photo member={member} focus={draft.photo_focus} onFocus={(v) => set("photo_focus", v)} />

          <h3 className="wtdc-subhead">On their card</h3>
          <div className="wtdc-grid-2">
            <Field label="Title"><input value={draft.title} maxLength={200} placeholder="Buyer Agent" onChange={(e) => set("title", e.target.value)} /></Field>
            <Field label="Market"><input value={draft.market} maxLength={200} placeholder="Salt Lake" onChange={(e) => set("market", e.target.value)} /></Field>
            <Field label="Tag (agents grid)"><input value={draft.tag} maxLength={24} placeholder="Bilingual" onChange={(e) => set("tag", e.target.value)} /></Field>
            <Field label="Shown in">
              <select value={draft.directory_placement} onChange={(e) => set("directory_placement", e.target.value)}>
                {Object.entries(PLACEMENTS).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
              </select>
            </Field>
          </div>
          <Field label="What to bring them (leadership card)">
            <input value={draft.help_line} maxLength={140} placeholder="Pipeline, conversion and the deal you think is dead."
                   onChange={(e) => set("help_line", e.target.value)} />
          </Field>

          <h3 className="wtdc-subhead">On their profile</h3>
          <Field label="Subtitle">
            <input value={draft.headline} maxLength={120} placeholder="Associate Broker · Utah Life Real Estate Group"
                   onChange={(e) => set("headline", e.target.value)} />
          </Field>
          <Field label="Pronoun, for the headings">
            <select value={draft.pronoun} onChange={(e) => set("pronoun", e.target.value)}>
              {Object.entries(PRONOUNS).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </select>
          </Field>
          <Field label="Quote">
            <textarea value={draft.quote} maxLength={240} rows={2} onChange={(e) => set("quote", e.target.value)} />
          </Field>
          <Field label="Bio (a blank line starts a new paragraph)">
            <textarea value={draft.bio} maxLength={4000} rows={5} onChange={(e) => set("bio", e.target.value)} />
          </Field>
          <Lines label="Bring them (up to six lines)" items={draft.bring} max={6}
                 placeholder="A goal you want to raise, and what it would take."
                 onChange={(v) => set("bring", v)} />

          <h3 className="wtdc-subhead">How to reach them</h3>
          <div className="wtdc-grid-2">
            <Field label="Direct phone"><input value={draft.phone} maxLength={200} onChange={(e) => set("phone", e.target.value)} /></Field>
            <Field label="Office"><input value={draft.office} maxLength={200} onChange={(e) => set("office", e.target.value)} /></Field>
          </div>
          <Field label="Message button goes to (blank: their email)">
            <input value={draft.message_url} maxLength={500} placeholder="https://… or mailto:, sms:, slack://"
                   onChange={(e) => set("message_url", e.target.value)} />
          </Field>

          <h3 className="wtdc-subhead">What they own</h3>
          <p className="followup-hint">SOPs they own in the SOP library are listed first, by themselves. Add anything else here.</p>
          <fieldset className="pe-lines">
            <legend>Owned items (up to eight)</legend>
            {draft.owns_items.map((item, i) => (
              <div className="pe-line two" key={i}>
                <input value={item.label} maxLength={120} placeholder="Growth Planning · Quarterly"
                       onChange={(e) => set("owns_items", draft.owns_items.map((o, j) => (j === i ? { ...o, label: e.target.value } : o)))} />
                <input value={item.url} maxLength={500} placeholder="Link (optional): https:// or /calendar"
                       onChange={(e) => set("owns_items", draft.owns_items.map((o, j) => (j === i ? { ...o, url: e.target.value } : o)))} />
                <button type="button" onClick={() => set("owns_items", draft.owns_items.filter((_, j) => j !== i))}>Remove</button>
              </div>
            ))}
            {draft.owns_items.length < 8 ? (
              <button type="button" className="pe-add" onClick={() => set("owns_items", [...draft.owns_items, { label: "", url: "" }])}>Add an item</button>
            ) : null}
          </fieldset>

          <div className="followup-actions pe-actions">
            <Button type="submit" tone="primary" busy={save.isPending}>Save profile</Button>
            <Button type="button" onClick={onClose}>Close</Button>
            {message ? <span className="roster-form-message">{message}</span> : null}
            {error ? <span className="console-form-error">{error}</span> : null}
          </div>
        </form>
      </aside>
    </div>
  );
}
