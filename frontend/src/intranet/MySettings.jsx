import { useEffect, useMemo, useRef, useState } from "react";

import { delJSON, getBlob, getJSON, patchJSON, postJSON, replaceToken, uploadFile } from "../api.js";
import { useAuthedImage } from "../useAuthedImage.js";

/* MY SETTINGS: the things a person may change about themselves.
 *
 * Before this a member could change nothing. A new agent's headshot, a wrong pronoun, a headline
 * from a job they no longer do -- every one of them was a message to whoever holds console access.
 *
 * The split is the server's (services/whos_who.SELF_SERVICE_FIELDS), not this file's: a profile
 * field describes a colleague, so its subject is the best source for it; where somebody appears in
 * the directory is the workspace presenting itself and stays with an admin. If this page ever
 * offers something the server refuses, the refusal names the field rather than dropping it.
 *
 * Every route is scoped to the member the SESSION resolves to -- no id is sent -- so this page
 * cannot be pointed at anybody else, and an Axcion support view cannot write through it at all. */

const PRONOUNS = [
  { value: "they", label: "they / them" },
  { value: "she", label: "she / her" },
  { value: "he", label: "he / him" },
];

// The mockup's crop positions, as words rather than percentages. `photo_focus` takes two
// percentages; nobody should have to know that to move their own face out of the crop.
const FOCUS = [
  { value: "", label: "Centred" },
  { value: "50% 25%", label: "Higher" },
  { value: "50% 12%", label: "Highest" },
  { value: "50% 75%", label: "Lower" },
];

const SHORT = [
  { key: "full_name", label: "Name", hint: "How you appear in Who's Who and on anything you own." },
  { key: "headline", label: "Headline", hint: "The line under your name." },
  { key: "title", label: "Title" },
  { key: "tag", label: "Tag", hint: "A short chip on your card." },
  { key: "office", label: "Office" },
  { key: "phone", label: "Phone" },
  { key: "help_line", label: "Ask me about", hint: "What a colleague should come to you for." },
  { key: "message_url", label: "Message link",
    hint: "A web, mailto:, sms:, tel: or slack:// address. Left blank, colleagues email you." },
];

const LONG = [
  { key: "quote", label: "Quote", rows: 2 },
  { key: "owns", label: "What you own", rows: 2 },
  { key: "bio", label: "About you", rows: 5 },
];

const EDITABLE = [...SHORT, ...LONG].map((f) => f.key);

function initials(name) {
  return String(name || "")
    .split(/\s+/).filter(Boolean).slice(0, 2)
    .map((part) => part[0].toUpperCase()).join("") || "?";
}

function Field({ field, value, limit, onChange }) {
  const over = limit && value && value.length > limit;
  return (
    <label className="ut-set-field">
      <span className="ut-set-label">{field.label}</span>
      {field.rows ? (
        <textarea rows={field.rows} value={value || ""}
                  onChange={(e) => onChange(field.key, e.target.value)} />
      ) : (
        <input type="text" value={value || ""}
               onChange={(e) => onChange(field.key, e.target.value)} />
      )}
      <span className={`ut-set-hint${over ? " over" : ""}`}>
        {over ? `${value.length} of ${limit} characters` : field.hint || ""}
      </span>
    </label>
  );
}

function Photo({ profile, onChanged, setError }) {
  const src = useAuthedImage(profile.photo_url || null, getBlob);
  const file = useRef(null);
  const [busy, setBusy] = useState("");

  async function pick(event) {
    const chosen = event.target.files?.[0];
    event.target.value = "";                 // so choosing the same file twice still fires
    if (!chosen) return;
    setError("");
    setBusy("upload");
    try {
      const form = new FormData();
      form.append("file", chosen);
      onChanged(await uploadFile("/intranet/me/photo", form));
    } catch (err) {
      setError(err.detail || err.message);
    } finally {
      setBusy("");
    }
  }

  async function remove() {
    setError("");
    setBusy("remove");
    try {
      onChanged(await delJSON("/intranet/me/photo"));
    } catch (err) {
      setError(err.detail || err.message);
    } finally {
      setBusy("");
    }
  }

  return (
    <section className="ut-set-card">
      <h2 className="ut-set-card-title">Your photo</h2>
      <p className="ut-set-card-lede">
        It appears in Who&rsquo;s Who and beside anything you own. It is turned upright, stripped of
        its metadata and re-encoded before it is stored, so nothing about where it was taken travels
        with it.
      </p>
      <div className="ut-set-photo-row">
        <div className="ut-set-photo" aria-hidden={!src}>
          {src
            ? <img src={src} alt="" style={{ objectPosition: profile.photo_focus || "50% 50%" }} />
            : <span>{initials(profile.full_name)}</span>}
        </div>
        <div className="ut-set-photo-actions">
          <input ref={file} type="file" accept="image/jpeg,image/png,image/webp"
                 className="ut-set-file" onChange={pick} />
          <button type="button" className="ut-button primary" disabled={Boolean(busy)}
                  onClick={() => file.current?.click()}>
            {busy === "upload" ? "Uploading…" : profile.photo_url ? "Replace photo" : "Upload a photo"}
          </button>
          {profile.photo_url ? (
            <button type="button" className="ut-button light" disabled={Boolean(busy)} onClick={remove}>
              {busy === "remove" ? "Removing…" : "Remove"}
            </button>
          ) : null}
          <span className="ut-set-hint">JPEG, PNG or WebP, up to 15&nbsp;MB.</span>
        </div>
      </div>
    </section>
  );
}

function Password({ setNotice }) {
  const [form, setForm] = useState({ current_password: "", new_password: "", confirm: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  async function submit(event) {
    event.preventDefault();
    setError("");
    if (form.new_password !== form.confirm) {
      setError("The two new passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      const r = await postJSON("/auth/change-password", {
        current_password: form.current_password, new_password: form.new_password,
      });
      // Changing a password ends every other session, this one included. The server hands back a
      // fresh token so the tab that just succeeded is not the one signed out.
      replaceToken(r.token);
      setForm({ current_password: "", new_password: "", confirm: "" });
      setNotice("Password changed. Anywhere else you were signed in has been signed out.");
    } catch (err) {
      setError(err.detail || err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="ut-set-card">
      <h2 className="ut-set-card-title">Password</h2>
      <p className="ut-set-card-lede">
        Changing it signs you out everywhere else, which is the point of changing it.
      </p>
      <form className="ut-set-grid" onSubmit={submit}>
        <label className="ut-set-field">
          <span className="ut-set-label">Current password</span>
          <input type="password" autoComplete="current-password" required
                 value={form.current_password} onChange={(e) => set("current_password", e.target.value)} />
        </label>
        <label className="ut-set-field">
          <span className="ut-set-label">New password</span>
          <input type="password" autoComplete="new-password" required minLength={8}
                 value={form.new_password} onChange={(e) => set("new_password", e.target.value)} />
          <span className="ut-set-hint">At least 8 characters.</span>
        </label>
        <label className="ut-set-field">
          <span className="ut-set-label">New password again</span>
          <input type="password" autoComplete="new-password" required
                 value={form.confirm} onChange={(e) => set("confirm", e.target.value)} />
        </label>
        <div className="ut-set-actions">
          <button type="submit" className="ut-button primary" disabled={busy}>
            {busy ? "Changing…" : "Change password"}
          </button>
          {error ? <span className="ut-set-error">{error}</span> : null}
        </div>
      </form>
    </section>
  );
}

export default function MySettings() {
  const [profile, setProfile] = useState(null);
  const [form, setForm] = useState(null);
  const [state, setState] = useState("loading");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let live = true;
    getJSON("/intranet/me/profile").then((data) => {
      if (!live) return;
      setProfile(data);
      setForm(data);
      setState("ready");
    }).catch((err) => {
      if (!live) return;
      setError(err.detail || err.message);
      setState(err.status === 409 ? "off-roster" : "error");
    });
    return () => { live = false; };
  }, []);

  const limits = profile?.limits || {};
  const dirty = useMemo(() => {
    if (!profile || !form) return false;
    if ((form.pronoun || "they") !== (profile.pronoun || "they")) return true;
    if ((form.photo_focus || "") !== (profile.photo_focus || "")) return true;
    if ((form.bring || []).join("\n") !== (profile.bring || []).join("\n")) return true;
    return EDITABLE.some((key) => (form[key] || "") !== (profile[key] || ""));
  }, [profile, form]);

  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  // The photo routes return the whole profile, so a new photo refreshes both copies and cannot
  // leave the form showing a photo that is no longer there.
  function photoChanged(next) {
    setProfile(next);
    setForm((f) => ({ ...next, ...f, photo_url: next.photo_url, photo_focus: f?.photo_focus ?? next.photo_focus }));
    setNotice(next.photo_url ? "Photo updated." : "Photo removed.");
  }

  async function save(event) {
    event.preventDefault();
    setError("");
    setNotice("");
    setSaving(true);
    try {
      const body = { pronoun: form.pronoun || "they", photo_focus: form.photo_focus || null };
      for (const key of EDITABLE) body[key] = form[key] || "";
      body.bring = (form.bring || []).filter(Boolean);
      const next = await patchJSON("/intranet/me/profile", body);
      setProfile(next);
      setForm(next);
      setNotice("Saved. Your colleagues see this straight away.");
    } catch (err) {
      setError(err.detail || err.message);
    } finally {
      setSaving(false);
    }
  }

  if (state === "loading") {
    return <div className="ut-set"><p className="ut-set-lede">Loading your settings&hellip;</p></div>;
  }

  if (state === "off-roster") {
    return (
      <div className="ut-set">
        <div className="ut-set-eyebrow">Workspace</div>
        <h1 className="ut-set-title">My Settings</h1>
        <section className="ut-set-card">
          <h2 className="ut-set-card-title">You are not on the roster yet</h2>
          <p className="ut-set-card-lede">{error}</p>
        </section>
      </div>
    );
  }

  if (state === "error") {
    return (
      <div className="ut-set">
        <h1 className="ut-set-title">My Settings</h1>
        <section className="ut-set-card"><p className="ut-set-card-lede">{error}</p></section>
      </div>
    );
  }

  return (
    <div className="ut-set">
      <div className="ut-set-eyebrow">Workspace</div>
      <h1 className="ut-set-title">My Settings</h1>
      <p className="ut-set-lede">
        Your photo and the things your profile says about you. Everyone in the workspace can see
        these; your email address and where you sit in the directory are set by an admin.
      </p>

      {notice ? <p className="ut-set-notice" role="status">{notice}</p> : null}

      <Photo profile={{ ...profile, photo_focus: form.photo_focus }} onChanged={photoChanged}
             setError={setError} />

      <form className="ut-set-card" onSubmit={save}>
        <h2 className="ut-set-card-title">Your profile</h2>
        <p className="ut-set-card-lede">
          Signed in as <strong>{profile.email}</strong>. An admin changes that one.
        </p>
        <div className="ut-set-grid">
          {SHORT.map((field) => (
            <Field key={field.key} field={field} value={form[field.key]}
                   limit={limits[field.key]} onChange={set} />
          ))}
          <label className="ut-set-field">
            <span className="ut-set-label">Pronoun</span>
            <select value={form.pronoun || "they"} onChange={(e) => set("pronoun", e.target.value)}>
              {PRONOUNS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
            </select>
          </label>
          <label className="ut-set-field">
            <span className="ut-set-label">Photo crop</span>
            <select value={form.photo_focus || ""} onChange={(e) => set("photo_focus", e.target.value)}>
              {FOCUS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
            </select>
            <span className="ut-set-hint">Where your face sits when the photo is cropped square.</span>
          </label>
        </div>

        <div className="ut-set-grid wide">
          {LONG.map((field) => (
            <Field key={field.key} field={field} value={form[field.key]}
                   limit={limits[field.key]} onChange={set} />
          ))}
          <label className="ut-set-field">
            <span className="ut-set-label">What you bring</span>
            <textarea rows={4} value={(form.bring || []).join("\n")}
                      onChange={(e) => set("bring", e.target.value.split("\n").slice(0, 6))} />
            <span className="ut-set-hint">One per line, up to six.</span>
          </label>
        </div>

        <div className="ut-set-actions">
          <button type="submit" className="ut-button primary" disabled={!dirty || saving}>
            {saving ? "Saving…" : "Save changes"}
          </button>
          {dirty && !saving ? <span className="ut-set-hint">Unsaved changes.</span> : null}
          {error ? <span className="ut-set-error">{error}</span> : null}
        </div>
      </form>

      <Password setNotice={setNotice} />
    </div>
  );
}
