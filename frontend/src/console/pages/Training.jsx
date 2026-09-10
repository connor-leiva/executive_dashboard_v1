import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  COURSE_STATE_OPTIONS,
  GROUPING_OPTIONS,
  LESSON_KIND_CHIP,
  LESSON_KIND_OPTIONS,
  LESSON_SOURCE_OPTIONS,
  SECTION_DUE_OPTIONS,
  SECTION_RELEASE_OPTIONS,
} from "../constants.js";
import LessonBody from "../LessonBody.jsx";
import {
  useAddLessonAttachment,
  useArchiveCourse,
  useCourse,
  useCourses,
  useCreateCourse,
  useCreateLesson,
  useCreateSection,
  useDeleteLessonAttachment,
  useDeleteSection,
  useOrderLessons,
  useOrderSections,
  usePatchSection,
  usePatchCourse,
  usePatchLesson,
  useRemoveLesson,
  useRoles,
  useSaveCourseRoles,
} from "../queries.js";
import { Button, ErrorState, LoadingState } from "../ui.jsx";

/* The course builder.
 *
 * ONE SAVE PATH. Every field autosaves as you type and the whole workspace publishes once; there
 * are no per-lesson Save buttons any more. The old screen had five of them plus a course Save,
 * which meant an admin who edited three lessons and pressed Save on two had silently lost the
 * third -- and no amount of care makes that visible, because the unsaved one looks exactly like
 * the saved ones.
 *
 * LESSONS ARE THE LANDING TAB. Title, category, state and description moved to Course settings,
 * because the thing somebody opens a course to work on is its lessons, and burying them under a
 * form they already filled in months ago is backwards.
 *
 * Lessons collapse to one line and one opens at a time. A dozen simultaneously-expanded forms is
 * not an editor, it is a wall.
 */

const NEW_COURSE_ID = "new";
const AUTOSAVE_MS = 700;

/* Source and state chips. Two maps rather than one because they answer different questions and
   colour on different scales -- a Draft course and a PDF lesson have nothing to do with one
   another. Every key the console can store is present; see the test that asserts it. */
const SOURCE_CHIP = {
  HERE: ["#E6F0E9", "#2F6444"],
  LOOM: ["#EEE7F6", "#6B4E9E"],
  SKOOL: ["#E7EFF6", "#2F5B84"],
  PDF: ["#F6E9E6", "#A44A33"],
  PLACE: ["#EDEEF0", "#41484B"],
  EXP: ["#E9F0F2", "#2F5B84"],
};
const STATE_CHIP = {
  Live: ["#E6F0E9", "#2F6444"],
  Draft: ["#EFEBEA", "#6E767B"],
  "Needs Review": ["#F6F1DC", "#7A6215"],
};

function chip(map, key) {
  const [bg, fg] = map[key] || ["#EFEBEA", "#6E767B"];
  return { background: bg, color: fg };
}

function sourceLabel(key) {
  return LESSON_SOURCE_OPTIONS.find((item) => item.key === key)?.label || key;
}

function kindLabel(key) {
  return LESSON_KIND_OPTIONS.find((item) => item.key === key)?.label || "Video";
}

/* One length, in whichever unit the kind actually measures. The server sends `duration` on every
   lesson for exactly this, but the collapsed row reads the live DRAFT so a number reflects the
   keystroke rather than the last save. */
function lessonLength(draft) {
  if (draft.kind === "reading") return draft.read_minutes === "" ? "" : `${draft.read_minutes}m read`;
  if (draft.kind === "document") return draft.page_count === "" ? "" : `${draft.page_count} pp`;
  return draft.duration_minutes === "" ? "" : `${draft.duration_minutes}m`;
}

function runtime(minutes) {
  const total = Number(minutes) || 0;
  const hours = Math.floor(total / 60);
  const mins = total % 60;
  return hours ? `${hours}h ${String(mins).padStart(2, "0")}m` : `${mins}m`;
}

function courseMeta(course) {
  const n = course?.lesson_count ?? (course?.lessons || []).length;
  return `${n} ${n === 1 ? "lesson" : "lessons"} · ${runtime(course?.total_duration_minutes)}`;
}

/* "2 min ago", "Aug 28". Relative while it is recent enough to mean something, absolute after --
   "13 days ago" is a date somebody has to do arithmetic on. */
function edited(iso) {
  if (!iso) return null;
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return null;
  const mins = Math.round((Date.now() - then.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 60 * 24) return `${Math.round(mins / 60)}h ago`;
  if (mins < 60 * 24 * 7) return `${Math.round(mins / 1440)}d ago`;
  return then.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/* Autosave.
 *
 * The draft lives here and the server catches up. Two rules make that safe:
 *
 *   The timer is per FIELD SET, not per keystroke -- one pending payload, replaced as you type, so
 *   a fast typist produces one PATCH rather than thirty.
 *
 *   The draft resyncs from the server only when the ROW IDENTITY changes. Saving invalidates the
 *   course query, the refetch returns the row we just wrote, and resyncing on every object change
 *   would overwrite whatever had been typed in the meantime -- the classic autosave bug where the
 *   cursor jumps and the last two characters vanish.
 */
function useAutosave(initial, identity, save) {
  const [draft, setDraft] = useState(initial);
  const timer = useRef(null);
  const latest = useRef(initial);

  useEffect(() => {
    setDraft(initial);
    latest.current = initial;
    // Identity only. See the note above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [identity]);

  useEffect(() => () => clearTimeout(timer.current), []);

  const update = useCallback((patch) => {
    setDraft((current) => {
      const next = { ...current, ...patch };
      latest.current = next;
      clearTimeout(timer.current);
      timer.current = setTimeout(() => save(latest.current), AUTOSAVE_MS);
      return next;
    });
  }, [save]);

  /* Commit anything still pending. Called on blur and before a destructive action, so closing a
     lesson within the debounce window does not throw the last edit away. */
  const flush = useCallback(() => {
    if (!timer.current) return;
    clearTimeout(timer.current);
    timer.current = null;
    save(latest.current);
  }, [save]);

  return [draft, update, flush];
}

function Field({ span, label, hint, children }) {
  return (
    <label className="cb-field" style={{ gridColumn: `span ${span}` }}>
      <span className="cb-label">{label}</span>
      {children}
      {hint ? <span className="cb-hint">{hint}</span> : null}
    </label>
  );
}

/* The same caption and spacing, WITHOUT the <label>.
 *
 * A <label> with no `for` forwards every click inside it to its first labelable descendant. That
 * is what makes `Field` right for an input -- clicking the caption focuses it -- and actively
 * hostile around anything else. Wrapping the rich-text editor in one made the first TOOLBAR
 * BUTTON its control, so double-clicking a word to select it bolded the word, and any click in
 * the body bounced focus to the toolbar and scrolled the page back up to it. Two symptoms, one
 * element.
 *
 * Use this for any field whose content is not a single control.
 */
function FieldBlock({ span, label, hint, children }) {
  return (
    <div className="cb-field" style={{ gridColumn: `span ${span}` }}>
      <span className="cb-label">{label}</span>
      {children}
      {hint ? <span className="cb-hint">{hint}</span> : null}
    </div>
  );
}

function Select({ value, onChange, options }) {
  return (
    <span className="cb-select">
      <select value={value} onChange={onChange}>
        {options.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
      </select>
      <span aria-hidden="true">▾</span>
    </span>
  );
}

function Toggle({ on, onClick, label }) {
  return (
    <button type="button" className={`cb-switch${on ? " on" : ""}`} onClick={onClick}
            role="switch" aria-checked={on} aria-label={label}>
      <span />
    </button>
  );
}

/* ── one lesson ────────────────────────────────────────────────────────────────────────── */

function LessonRow({ courseId, lesson, index, total, open, onToggle, onSave, onRemove,
                    onDragStart, onDragOver, onDrop, dragging }) {
  /* EVERY KIND'S FIELDS ARE SENT EVERY TIME, not just the visible ones. An author who flips
     Reading -> Video to check something and flips back must find their article still there, and
     omitting the hidden fields here would clear them on the very next keystroke. `body_html` is
     the exception: it saves on its own path -- see below. */
  const save = useCallback((values) => {
    onSave(lesson.id, {
      title: (values.title || "").trim() || lesson.title,
      kind: values.kind,
      source_type: values.source_type,
      source_ref: (values.source_ref || "").trim() || null,
      taught_by: (values.taught_by || "").trim() || null,
      description: (values.description || "").trim() || null,
      duration_minutes: values.duration_minutes === "" ? null : Number(values.duration_minutes),
      read_minutes: values.read_minutes === "" ? null : Number(values.read_minutes),
      page_count: values.page_count === "" ? null : Number(values.page_count),
      required: Boolean(values.required),
    });
  }, [lesson.id, lesson.title, onSave]);

  const [draft, update, flush] = useAutosave({
    title: lesson.title || "",
    kind: lesson.kind || "video",
    source_type: lesson.source_type || "HERE",
    source_ref: lesson.source_ref || "",
    taught_by: lesson.taught_by || "",
    description: lesson.description || "",
    duration_minutes: lesson.duration_minutes ?? "",
    read_minutes: lesson.read_minutes ?? "",
    page_count: lesson.page_count ?? "",
    required: Boolean(lesson.required),
  }, lesson.id, save);

  /* The body has its OWN save, at its own debounce. In the same payload as the title, every
     keystroke would ship the whole document -- and a slow save of a long article would hold up
     a one-character rename. */
  const saveBody = useCallback((html) => {
    onSave(lesson.id, { body_html: html || "" });
  }, [lesson.id, onSave]);

  // Live from the editor, so the Read field can show what the body currently works out to
  // without waiting for a save to come back.
  const [counts, setCounts] = useState({ words: 0, minutes: 0 });
  // Only the delete path reaches into the editor: it has to throw the pending body save away
  // before the row goes, or the PATCH races the DELETE.
  const bodyRef = useRef(null);
  const kind = draft.kind || "video";
  const reading = kind === "reading";
  const document_ = kind === "document";

  const attachments = lesson.attachments || [];
  const attachLabel = attachments.length === 1
    ? "1 file"
    : attachments.length ? `${attachments.length} files` : "";
  const subtitle = [draft.taught_by, draft.source_ref].filter(Boolean).join(" · ");

  return (
    /* DRAGGABLE IS ON THE GRIP, NOT THE ROW. With it on the row, every drag anywhere inside --
       including selecting a sentence in the rich-text editor, which lives in this same box --
       started a lesson reorder: the browser lifted the whole card like an image, and dropping it
       back into the editor inserted the dataTransfer payload as text. The grip has had
       `cursor: grab` since it was drawn; this makes it mean something.
       dragstart bubbles, so the handler stays here and only the source moves. */
    <div className={`cb-lesson${dragging ? " dragging" : ""}`}
         onDragStart={(e) => onDragStart(e, index)}
         onDragOver={(e) => onDragOver(e, index)}
         onDrop={(e) => onDrop(e, index)}>
      <div className="cb-lesson-row" onClick={onToggle}>
        <span className="cb-grip" draggable title="Drag to reorder" aria-hidden="true">⠿</span>
        <span className="cb-num">{index + 1}</span>
        <span className="cb-lesson-title">
          <strong>{draft.title || "Untitled lesson"}</strong>
          <em>{subtitle}</em>
        </span>
        <span className="cb-tag" style={chip(LESSON_KIND_CHIP, kind)}>
          {kindLabel(kind)}
        </span>
        {/* The source chip is dropped for a reading lesson: it has no host, and "Hosted" beside
            an article is a statement about nothing. */}
        {reading ? null : (
          <span className="cb-tag" style={chip(SOURCE_CHIP, draft.source_type)}>
            {sourceLabel(draft.source_type)}
          </span>
        )}
        <span className="cb-dur">{lessonLength(draft)}</span>
        <span className={`cb-pill${draft.required ? " req" : ""}`}>
          {draft.required ? "Required" : "Optional"}
        </span>
        <span className="cb-att">{attachLabel}</span>
        <span className="cb-caret" aria-hidden="true">{open ? "▲" : "▼"}</span>
      </div>

      {open ? (
        <div className="cb-open">
          <div className="cb-card">
            <div className="cb-kindpick">
              {LESSON_KIND_OPTIONS.map((option) => (
                <button
                  key={option.key}
                  type="button"
                  className={`cb-kindbtn${kind === option.key ? " on" : ""}`}
                  aria-pressed={kind === option.key ? "true" : "false"}
                  onClick={() => update({ kind: option.key })}
                >
                  <span aria-hidden="true">{option.glyph}</span>{option.label}
                </button>
              ))}
              <span className="cb-kindnote">
                {reading
                  ? "An article members read in the portal. No video, no player."
                  : document_
                    ? "A file members open or download."
                    : "A video members watch in the portal."}
              </span>
            </div>

            <div className="cb-grid">
              <Field span={7} label="Lesson title">
                <input value={draft.title} onBlur={flush}
                       onChange={(e) => update({ title: e.target.value })} />
              </Field>
              <Field span={3} label="Taught by" hint="Shown as the byline on the lesson.">
                <input value={draft.taught_by} onBlur={flush}
                       onChange={(e) => update({ taught_by: e.target.value })} />
              </Field>
              {reading ? (
                <Field span={2} label="Read">
                  {/* Derived from the body as you type, and overridable: the estimate is a
                      reading pace, and an author who knows their audience beats a constant. */}
                  <input value={draft.read_minutes} inputMode="numeric" onBlur={flush}
                         placeholder={counts.minutes ? String(counts.minutes) : ""}
                         onChange={(e) => update({ read_minutes: e.target.value })} />
                </Field>
              ) : document_ ? (
                <Field span={2} label="Pages">
                  <input value={draft.page_count} inputMode="numeric" onBlur={flush}
                         onChange={(e) => update({ page_count: e.target.value })} />
                </Field>
              ) : (
                <Field span={2} label="Minutes">
                  <input value={draft.duration_minutes} inputMode="numeric" onBlur={flush}
                         onChange={(e) => update({ duration_minutes: e.target.value })} />
                </Field>
              )}

              {reading ? (
                <FieldBlock span={12} label="Lesson body"
                            hint="This is the lesson. Bold, headings, lists, callouts, links and images.">
                  <LessonBody
                    ref={bodyRef}
                    courseId={courseId}
                    lessonId={lesson.id}
                    value={lesson.body_html || ""}
                    onChange={saveBody}
                    onCounts={setCounts}
                  />
                </FieldBlock>
              ) : (
                <>
                  <Field span={8}
                         label={document_ ? "Document URL or storage key" : "Video URL or storage key"}
                         hint="Paste a link or a storage key. Loom, YouTube, Vimeo and PDFs play inside the portal; Skool, PLACE and eXp open in a new tab.">
                    <input value={draft.source_ref} onBlur={flush}
                           onChange={(e) => update({ source_ref: e.target.value })} />
                  </Field>
                  <Field span={4} label="Source">
                    <Select value={draft.source_type} options={LESSON_SOURCE_OPTIONS}
                            onChange={(e) => update({ source_type: e.target.value })} />
                  </Field>
                </>
              )}

              <Field span={12} label={reading ? "Summary" : "Description"}
                     hint={reading
                       ? "One or two sentences for the course list. The body above is the lesson itself."
                       : "What the lesson is for and when to watch it. Appears under the title in the portal and feeds the AI assistant."}>
                <textarea rows="3" value={draft.description} onBlur={flush}
                          onChange={(e) => update({ description: e.target.value })} />
              </Field>

              <div className="cb-span-12">
                <button type="button"
                        className={`cb-reqbtn${draft.required ? " on" : ""}`}
                        onClick={() => { update({ required: !draft.required }); }}>
                  <span className={`cb-switch${draft.required ? " on" : ""}`}><span /></span>
                  Required to complete the course
                </button>
              </div>

              <div className="cb-span-12">
                <div className="cb-label">Attachments</div>
                <LessonAttachments courseId={courseId} lesson={lesson} />
              </div>
            </div>

            <div className="cb-foot">
              <span>Saved automatically · draft until you publish</span>
              <Button tone="danger"
                      onClick={() => { bodyRef.current?.discard(); onRemove(lesson.id); }}>
                Delete lesson
              </Button>
              <Button tone="primary" onClick={() => { flush(); onToggle(); }}>Done</Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/* Handouts: chips for what is there, one dashed target to add more.
 *
 * Outside the lesson's own fields deliberately -- an upload is not a text edit, and folding it
 * into the autosave would mean a failed upload rolling back a title somebody had already typed. */
function LessonAttachments({ courseId, lesson }) {
  const add = useAddLessonAttachment();
  const remove = useDeleteLessonAttachment();
  const [adding, setAdding] = useState(false);
  const [kind, setKind] = useState("file");
  const [title, setTitle] = useState("");
  const [note, setNote] = useState("");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState(null);
  const [error, setError] = useState("");
  const attachments = lesson.attachments || [];
  const busy = add.isPending || remove.isPending;

  async function submit(event) {
    event.preventDefault();
    // Captured BEFORE the await: React clears a synthetic event's currentTarget once the handler
    // yields, so resetting it afterwards throws on a form that submitted perfectly well.
    const node = event.currentTarget;
    setError("");
    try {
      await add.mutateAsync({
        courseId,
        lessonId: lesson.id,
        fields: kind === "link"
          ? { title: title.trim(), kind, note: note.trim(), url: url.trim() }
          : { title: title.trim(), kind, note: note.trim(), file },
      });
      setTitle(""); setNote(""); setUrl(""); setFile(null); setAdding(false);
      node.reset();
    } catch (err) {
      setError(err?.detail || err?.message || "Could not add that.");
    }
  }

  return (
    <div className="cb-atts">
      <div className="cb-chips">
        {attachments.map((a) => (
          <span className="cb-attchip" key={a.id}>
            <span className="cb-attkind">{a.kind === "link" ? "Link" : "File"}</span>
            <span>
              <strong>{a.title}</strong>
              {a.note ? <em>{a.note}</em> : null}
            </span>
            <button type="button" disabled={busy} aria-label={`Remove ${a.title}`}
                    onClick={() => remove.mutate({ courseId, lessonId: lesson.id,
                                                   attachmentId: a.id })}>✕</button>
          </span>
        ))}
        {!adding ? (
          <button type="button" className="cb-drop" onClick={() => setAdding(true)}>
            ＋ Add a file or link a doc
          </button>
        ) : null}
      </div>

      {adding ? (
        <form className="cb-attform" onSubmit={submit}>
          <Select value={kind} onChange={(e) => setKind(e.target.value)}
                  options={[{ key: "file", label: "Upload a file" },
                            { key: "link", label: "Link somewhere else" }]} />
          <input value={title} required placeholder="Name"
                 onChange={(e) => setTitle(e.target.value)} />
          <input value={note} placeholder="Note — 2 pages, Team Drive…"
                 onChange={(e) => setNote(e.target.value)} />
          {kind === "link" ? (
            <input value={url} required placeholder="https://…"
                   onChange={(e) => setUrl(e.target.value)} />
          ) : (
            <input type="file" required accept=".pdf,.png,.jpg,.jpeg,.webp"
                   onChange={(e) => setFile(e.target.files?.[0] || null)} />
          )}
          <Button type="submit" tone="primary" busy={add.isPending}>Add</Button>
          <Button type="button" onClick={() => { setAdding(false); setError(""); }}>Cancel</Button>
        </form>
      ) : null}
      {error ? <p className="error">{error}</p> : null}
    </div>
  );
}

/* ── the three tabs ────────────────────────────────────────────────────────────────────── */

/* ── one section ───────────────────────────────────────────────────────────────────────── */

function SectionHeader({ detail, section, lessons, onSave, onDelete, dragProps, dragging }) {
  const [editing, setEditing] = useState(false);
  const save = useCallback((values) => {
    onSave(section.id, {
      name: (values.name || "").trim(),
      summary: (values.summary || "").trim() || null,
      release_rule: values.release_rule,
      release_day: values.release_day === "" ? null : Number(values.release_day),
      due_rule: values.due_rule,
      due_day: values.due_day === "" ? null : Number(values.due_day),
    });
  }, [section.id, onSave]);

  const [draft, update, flush] = useAutosave({
    name: section.name || "",
    summary: section.summary || "",
    release_rule: section.release_rule || "immediate",
    release_day: section.release_day ?? "",
    due_rule: section.due_rule || "none",
    due_day: section.due_day ?? "",
  }, section.id, save);

  const minutes = lessons.reduce((total, l) => total + lessonMinutes(l), 0);
  const dueLabel = dueText(draft);
  const needsDay = draft.release_rule === "day_n";
  const needsDueDay = draft.due_rule === "end_of_day_n" || draft.due_rule === "end_of_week_n";

  return (
    /* On the grip, for the same reason as the lesson row above: this box holds the section's
       own name and summary fields, and a draggable ancestor turns selecting text in them into a
       section reorder. */
    <div className={`cb-section${dragging ? " dragging" : ""}`} {...dragProps}>
      <div className="cb-section-row">
        <span className="cb-grip" draggable title="Drag to reorder" aria-hidden="true">⠿</span>
        {section.label ? <span className="cb-section-chip">{section.label}</span> : null}
        <span className="cb-section-name">{draft.name || "Untitled section"}</span>
        {dueLabel ? <span className="cb-section-due">{dueLabel}</span> : null}
        <span className="cb-section-meta">
          {lessons.length} {lessons.length === 1 ? "lesson" : "lessons"} · {runtime(minutes)}
        </span>
        <button type="button" className="cb-section-edit"
                onClick={() => setEditing((v) => !v)}>
          {editing ? "Close" : "Edit"}
        </button>
      </div>

      {editing ? (
        <div className="cb-section-panel">
          <div className="cb-grid">
            <Field span={2} label="Label">
              {/* Generated, not typed: it comes from the course's scheme and this section's
                  position, so switching the scheme renames every section at once. */}
              <input value={section.label || "—"} readOnly className="cb-derived" />
            </Field>
            <Field span={6} label="Section name">
              <input value={draft.name} onBlur={flush} placeholder="Systems and access"
                     onChange={(e) => update({ name: e.target.value })} />
            </Field>
            <Field span={needsDay ? 2 : 4} label="Opens">
              <Select value={draft.release_rule} options={SECTION_RELEASE_OPTIONS}
                      onChange={(e) => update({ release_rule: e.target.value })} />
            </Field>
            {needsDay ? (
              <Field span={2} label="Day" hint="Day 1 is the day they start.">
                <input value={draft.release_day} inputMode="numeric" onBlur={flush}
                       onChange={(e) => update({ release_day: e.target.value })} />
              </Field>
            ) : null}

            <Field span={8} label="Summary" hint="One line under the section name in the portal.">
              <input value={draft.summary} onBlur={flush}
                     onChange={(e) => update({ summary: e.target.value })} />
            </Field>
            <Field span={needsDueDay ? 2 : 4} label="Due">
              <Select value={draft.due_rule} options={SECTION_DUE_OPTIONS}
                      onChange={(e) => update({ due_rule: e.target.value })} />
            </Field>
            {needsDueDay ? (
              <Field span={2} label={draft.due_rule === "end_of_week_n" ? "Week" : "Day"}>
                <input value={draft.due_day} inputMode="numeric" onBlur={flush}
                       onChange={(e) => update({ due_day: e.target.value })} />
              </Field>
            ) : null}
          </div>

          <div className="cb-foot">
            <span>Saved automatically · draft until you publish</span>
            <Button tone="danger" onClick={() => { flush(); onDelete(section.id); }}>
              Delete section
            </Button>
            <Button tone="primary" onClick={() => { flush(); setEditing(false); }}>Done</Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/* Minutes this lesson costs, in the unit its kind measures. Pages are deliberately not minutes --
   there is no honest conversion. Mirrors services/course_sections.lesson_minutes. */
function lessonMinutes(lesson) {
  if ((lesson.kind || "video") === "reading") {
    return Number(lesson.read_minutes || lesson.duration_minutes || 0);
  }
  return Number(lesson.duration_minutes || lesson.read_minutes || 0);
}

function dueText(draft) {
  if (draft.due_rule === "end_of_day_n" && draft.due_day) return `Due end of day ${draft.due_day}`;
  if (draft.due_rule === "end_of_week_n" && draft.due_day) return `Due end of week ${draft.due_day}`;
  if (draft.due_rule === "before_next_section") return "Due before the next section";
  return "";
}

function LessonsTab({ detail, onSaveLesson, onAddLesson, onRemoveLesson, onReorder,
                     onAddSection, onSaveSection, onRemoveSection, onReorderSections }) {
  const lessons = detail.lessons || [];
  const sections = detail.sections || [];
  const [open, setOpen] = useState(null);
  // THE GRABBED INDEX LIVES IN A REF, and the state beside it exists only to grey the row out.
  // Reading it from state made `drop` depend on a re-render happening between dragstart and drop
  // to refresh its closure -- which a real drag usually gives you and nothing guarantees, so the
  // drop silently did nothing whenever it did not. A ref is current the moment it is set.
  const from = useRef(null);
  const sectionFrom = useRef(null);
  const [dragIndex, setDragIndex] = useState(null);

  /* The screen order, and the groups drawn from it.
   *
   * The SERVER already ordered `detail.lessons` -- ungrouped first, then section by section, by
   * each section's position. Grouping here just walks that order and cuts it, so the console and
   * the portal cannot disagree about where a lesson sits. An empty section still gets a group,
   * because otherwise there is nothing on screen to drag a lesson into. */
  const ordered = lessons;
  const groups = useMemo(() => {
    const bySection = new Map();
    const loose = [];
    ordered.forEach((lesson) => {
      if (!lesson.section_id) return loose.push(lesson);
      if (!bySection.has(lesson.section_id)) bySection.set(lesson.section_id, []);
      bySection.get(lesson.section_id).push(lesson);
    });
    const out = [];
    if (loose.length || !sections.length) {
      out.push({ key: "loose", section: null, index: -1, lessons: loose });
    }
    sections.forEach((section, index) => {
      out.push({ key: section.id, section, index,
                 lessons: bySection.get(section.id) || [] });
    });
    return out;
  }, [ordered, sections]);

  /* lessonId -> the section it starts. Used to work out which section a dragged row landed in:
     the nearest boundary at or above it. */
  const boundaries = useMemo(() => {
    const map = new Map();
    groups.forEach((group) => {
      group.lessons.forEach((lesson, i) => {
        if (i === 0) map.set(lesson.id, group.section ? group.section.id : null);
      });
    });
    return map;
  }, [groups]);

  function dragStart(event, index) {
    from.current = index;
    setDragIndex(index);
    event.dataTransfer.effectAllowed = "move";
    // Firefox refuses to start a drag unless something is on the transfer.
    // Firefox refuses to start a drag unless something is on the transfer. The TITLE rather
    // than the index: a drop the browser handles itself inserts text/plain wherever it landed,
    // and `String(index)` meant dragging the first lesson typed a bare "0" into whatever was
    // under the cursor.
    event.dataTransfer.setData("text/plain", lessons[index]?.title || "Lesson");
    // The ROW is what the cursor should carry, even though the grip is what started the drag --
    // otherwise you drag a lone six-dot glyph and cannot see what is being moved.
    const row = event.target.closest?.(".cb-lesson-row");
    if (row) event.dataTransfer.setDragImage(row, 24, row.offsetHeight / 2);
  }

  /* ONLY CLAIM OUR OWN DRAG. `preventDefault` on dragover is what says "you may drop here", and
     doing it unconditionally made the whole card -- editor included -- a drop target for
     anything: text dragged from another tab, a file from the desktop. The card would show a
     "move" cursor it could not honour and then swallow the drop, because `drop` cancelled the
     browser's default before checking whose drag it was. Now a drag we did not start passes
     straight through to whatever is under it, which for the editor means ProseMirror. */
  function ours() {
    return from.current !== null || sectionFrom.current !== null;
  }

  function dragOver(event) {
    if (!ours()) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }

  /* THE MOVE CARRIES A SECTION AS WELL AS A POSITION, and goes in ONE request. Splitting it into
     a reorder plus a reassignment leaves a window where the lesson sits in the new section at the
     old position, and whichever request lost the race decides where it ends up. */
  function drop(event, to) {
    if (from.current === null) return;      // not ours: let it fall through
    event.preventDefault();
    const start = from.current;
    from.current = null;
    setDragIndex(null);
    if (start === null || start === to) return;
    const moved = ordered.slice();
    const [taken] = moved.splice(start, 1);
    moved.splice(to, 0, taken);
    onReorder(payloadFor(moved));
  }

  /* Dropping onto a section HEADER means "put it at the top of this section" -- the only way to
     reach an empty one, which otherwise has no row to aim at. */
  function dropIntoSection(event, sectionId) {
    if (from.current === null) return;
    event.preventDefault();
    const start = from.current;
    from.current = null;
    setDragIndex(null);
    if (start === null) return;
    const moved = ordered.slice();
    const [taken] = moved.splice(start, 1);
    const at = moved.findIndex((l) => (l.section_id || null) === sectionId);
    moved.splice(at < 0 ? moved.length : at, 0, { ...taken, section_id: sectionId });
    onReorder(payloadFor(moved, taken.id, sectionId));
  }

  /* The order of `list` is the order on screen; a lesson's section is whichever group it now
     sits in. `sort` restarts per section so it means "position within this section". */
  function payloadFor(list, forcedId, forcedSection) {
    const counters = new Map();
    return list.map((lesson) => {
      const sectionId = lesson.id === forcedId
        ? forcedSection
        : sectionOf(list, lesson);
      const key = sectionId || "";
      const next = (counters.get(key) || 0);
      counters.set(key, next + 1);
      return { id: lesson.id, section_id: sectionId, sort: next };
    });
  }

  /* Which section a lesson has landed in: the one belonging to the nearest lesson above it that
     has one. Dragging past a section header is how somebody expects to change section, and the
     row itself carries no boundary information. */
  function sectionOf(list, lesson) {
    const at = list.indexOf(lesson);
    for (let i = at; i >= 0; i -= 1) {
      const found = boundaries.get(list[i].id);
      if (found !== undefined) return found;
    }
    return lesson.section_id || null;
  }

  function sectionDragStart(event, index) {
    sectionFrom.current = index;
    event.dataTransfer.effectAllowed = "move";
    // See dragStart: a readable payload, because the browser will paste it somewhere if a drop
    // ever escapes our own handler, and the row as the thing the cursor carries.
    event.dataTransfer.setData("text/plain", sections[index]?.name || "Section");
    const row = event.target.closest?.(".cb-section-row");
    if (row) event.dataTransfer.setDragImage(row, 24, row.offsetHeight / 2);
  }

  function sectionDrop(event, to) {
    const start = sectionFrom.current;
    sectionFrom.current = null;
    if (start === null || start === to) return;
    event.preventDefault();
    event.stopPropagation();
    const ids = sections.map((x) => x.id);
    const [moved] = ids.splice(start, 1);
    ids.splice(to, 0, moved);
    onReorderSections(ids);
  }

  return (
    <div>
      <div className="cb-toolbar">
        <span className="cb-label">
          {sections.length ? "Sections and lessons · drag to reorder" : "Lessons · drag to reorder"}
        </span>
        <Button onClick={() => onAddSection()}>Add section</Button>
        <Button onClick={() => onAddLesson()}>Add lesson</Button>
      </div>

      {groups.map((group) => (
        <Fragment key={group.key}>
          {group.section ? (
            <SectionHeader
              detail={detail}
              section={group.section}
              lessons={group.lessons}
              onSave={onSaveSection}
              onDelete={onRemoveSection}
              dragging={false}
              dragProps={{
                onDragStart: (e) => sectionDragStart(e, group.index),
                onDragOver: dragOver,
                onDrop: (e) => {
                  if (sectionFrom.current !== null) return sectionDrop(e, group.index);
                  return dropIntoSection(e, group.section.id);
                },
              }}
            />
          ) : null}

          <div className={group.section ? "cb-section-lessons" : ""}>
            {group.lessons.map((lesson) => {
              const index = ordered.indexOf(lesson);
              return (
                <LessonRow
                  key={lesson.id}
                  courseId={detail.id}
                  lesson={lesson}
                  index={index}
                  total={ordered.length}
                  open={open === lesson.id}
                  dragging={dragIndex === index}
                  onToggle={() => setOpen((cur) => (cur === lesson.id ? null : lesson.id))}
                  onSave={onSaveLesson}
                  onRemove={(id) => { setOpen(null); onRemoveLesson(id); }}
                  onDragStart={dragStart}
                  onDragOver={dragOver}
                  onDrop={drop}
                />
              );
            })}
            {group.section && !group.lessons.length ? (
              <div className="cb-section-empty">Drag a lesson here, or add one.</div>
            ) : null}
          </div>
        </Fragment>
      ))}

      <div className="cb-listfoot">
        <button type="button" className="cb-drop" onClick={() => onAddLesson()}>
          ＋ Add lesson
        </button>
        <span />
        <span className="cb-meta">{courseMeta(detail)}</span>
      </div>
    </div>
  );
}

function SettingsTab({ detail, onSave, onArchive }) {
  const save = useCallback((values) => {
    onSave({
      title: (values.title || "").trim() || detail.title,
      category: (values.category || "").trim(),
      description: (values.description || "").trim() || null,
      state: values.state,
      track_progress: Boolean(values.track_progress),
      required_for_onboarding: Boolean(values.required_for_onboarding),
      issues_certificate: Boolean(values.issues_certificate),
      sequential: Boolean(values.sequential),
      grouping_scheme: values.grouping_scheme,
      lock_sections: Boolean(values.lock_sections),
    });
  }, [detail.title, onSave]);

  const [draft, update, flush] = useAutosave({
    title: detail.title || "",
    category: detail.category || "",
    description: detail.description || "",
    state: detail.state || "Draft",
    track_progress: detail.track_progress ?? true,
    required_for_onboarding: detail.required_for_onboarding ?? false,
    issues_certificate: detail.issues_certificate ?? false,
    sequential: detail.sequential ?? false,
    grouping_scheme: detail.grouping_scheme || "none",
    lock_sections: detail.lock_sections ?? false,
  }, detail.id, save);

  const flags = [
    ["track_progress", "Track progress",
     "Agents see a completion bar, and it counts toward their onboarding."],
    ["required_for_onboarding", "Required for onboarding",
     "Appears in the first 30 days path."],
    ["issues_certificate", "Issue a certificate",
     "Logs completion on the member's record when every lesson is done."],
    ["sequential", "Lock lessons in order",
     "Each lesson unlocks only after the one before it is finished."],
    ["lock_sections", "Lock sections in order",
     "A section opens only once the one before it is complete. Separate from the rule above — a course can lock sections, lessons, both or neither."],
  ];

  return (
    <div className="cb-pane">
      <div className="cb-grid">
        <Field span={7} label="Course title">
          <input value={draft.title} onBlur={flush}
                 onChange={(e) => update({ title: e.target.value })} />
        </Field>
        <Field span={3} label="Category">
          <input value={draft.category} onBlur={flush} placeholder="Listing, Buyer, Systems…"
                 onChange={(e) => update({ category: e.target.value })} />
        </Field>
        <Field span={2} label="State">
          <Select value={draft.state} options={COURSE_STATE_OPTIONS}
                  onChange={(e) => update({ state: e.target.value })} />
        </Field>
        <Field span={12} label="Description"
               hint="One or two sentences. Shown on the course card in the portal.">
          <textarea rows="3" value={draft.description} onBlur={flush}
                    onChange={(e) => update({ description: e.target.value })} />
        </Field>
      </div>

      <div className="cb-grouping">
        <div className="cb-label">How this course is grouped</div>
        <p className="cb-hint">
          The label is generated from the scheme and the section’s position, so switching
          scheme renames every section at once and moves no lessons.
        </p>
        <div className="cb-schemes">
          {GROUPING_OPTIONS.map((option) => (
            <button
              key={option.key}
              type="button"
              className={`cb-scheme${draft.grouping_scheme === option.key ? " on" : ""}`}
              aria-pressed={draft.grouping_scheme === option.key ? "true" : "false"}
              onClick={() => update({ grouping_scheme: option.key })}
            >
              <strong>{option.label}</strong>
              <em>{option.example}</em>
            </button>
          ))}
        </div>
      </div>

      <div className="cb-flags">
        {flags.map(([key, name, note]) => (
          <div className="cb-flag" key={key}>
            <div>
              <strong>{name}</strong>
              <em>{note}</em>
            </div>
            <Toggle on={Boolean(draft[key])} label={name}
                    onClick={() => update({ [key]: !draft[key] })} />
          </div>
        ))}
      </div>

      <div className="cb-foot">
        <span>Saved automatically · draft until you publish</span>
        <Button tone="danger" onClick={onArchive}>Archive course</Button>
      </div>
    </div>
  );
}

function VisibilityTab({ detail, roles, onSave }) {
  // Absence means "everybody", which is what an admin who never opened this tab intends -- so an
  // empty stored list shows every role ticked rather than a course nobody can see.
  const stored = detail.role_ids || [];
  const [selected, setSelected] = useState(
    () => new Set(stored.length ? stored : roles.map((r) => r.id)));

  useEffect(() => {
    setSelected(new Set(stored.length ? stored : roles.map((r) => r.id)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail.id]);

  function flip(roleId) {
    const next = new Set(selected);
    if (next.has(roleId)) next.delete(roleId);
    else next.add(roleId);
    setSelected(next);
    onSave([...next]);
  }

  return (
    <div className="cb-pane">
      <p className="cb-intro">
        Who sees this course in the portal. Roles come from the workspace roster, so a role added
        later inherits whatever is set here.
      </p>
      <div className="cb-flags">
        {roles.map((role) => {
          const on = selected.has(role.id);
          return (
            <div className="cb-flag" key={role.id}>
              <div>
                <strong>{role.name}</strong>
                {role.is_leadership ? <em>Leadership</em> : null}
              </div>
              <button type="button" className={`cb-see${on ? " on" : ""}`}
                      onClick={() => flip(role.id)}>
                <span aria-hidden="true">{on ? "✓" : ""}</span>
                {on ? "Can see it" : "Hidden"}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ── the page ──────────────────────────────────────────────────────────────────────────── */

export default function Training() {
  const coursesQuery = useCourses(true);
  const rolesQuery = useRoles(true);
  const [selectedId, setSelectedId] = useState("");
  const [tab, setTab] = useState("lessons");
  const [query, setQuery] = useState("");
  const [stateFilter, setStateFilter] = useState("All");
  const [error, setError] = useState("");
  const [savedAt, setSavedAt] = useState(null);

  const createCourse = useCreateCourse();
  const patchCourse = usePatchCourse();
  const archiveCourse = useArchiveCourse();
  const saveRoles = useSaveCourseRoles();
  const createLesson = useCreateLesson();
  const patchLesson = usePatchLesson();
  const removeLesson = useRemoveLesson();
  const orderLessons = useOrderLessons();
  const createSection = useCreateSection();
  const patchSection = usePatchSection();
  const removeSection = useDeleteSection();
  const orderSections = useOrderSections();

  const courses = coursesQuery.data?.items || [];
  const roles = rolesQuery.data?.items || [];
  const isNew = selectedId === NEW_COURSE_ID;
  const courseQuery = useCourse(isNew ? "" : selectedId, Boolean(selectedId && !isNew));
  const detail = courseQuery.data || null;

  const saving = patchCourse.isPending || patchLesson.isPending || saveRoles.isPending
    || createLesson.isPending || removeLesson.isPending || orderLessons.isPending
    || createSection.isPending || patchSection.isPending || removeSection.isPending
    || orderSections.isPending;

  useEffect(() => {
    if (selectedId || coursesQuery.isPending || coursesQuery.error) return;
    setSelectedId(courses.length ? courses[0].id : NEW_COURSE_ID);
  }, [selectedId, courses, coursesQuery.isPending, coursesQuery.error]);

  useEffect(() => {
    // isFetching, not just isPending. This effect exists to recover from a course being archived
    // out from under the selection, and it was also firing DURING the refetch that follows a
    // create -- at which point `courses` is still the old list, the brand-new id is legitimately
    // absent from it, and the effect helpfully threw the selection away. The course appeared in
    // the rail a moment later with the editor still showing "choose a course", which reads
    // exactly like the button not working. isPending is false on a refetch of an already-loaded
    // query, so it never covered this.
    if (!selectedId || isNew || coursesQuery.isPending || coursesQuery.isFetching
        || coursesQuery.error) return;
    if (courses.some((c) => c.id === selectedId)) return;
    setSelectedId(courses.length ? courses[0].id : NEW_COURSE_ID);
  }, [selectedId, isNew, courses, coursesQuery.isPending, coursesQuery.isFetching,
      coursesQuery.error]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return courses.filter((c) => {
      const hit = !q || (c.title || "").toLowerCase().includes(q)
        || (c.category || "").toLowerCase().includes(q);
      return hit && (stateFilter === "All" || c.state === stateFilter);
    });
  }, [courses, query, stateFilter]);

  const stats = useMemo(() => {
    const lessons = courses.reduce((n, c) => n + (c.lesson_count || 0), 0);
    const mins = courses.reduce((n, c) => n + (c.total_duration_minutes || 0), 0);
    return [
      { v: String(courses.length), k: "Courses" },
      { v: String(lessons), k: "Lessons" },
      { v: runtime(mins), k: "Total run time" },
    ];
  }, [courses]);

  const run = useCallback(async (fn) => {
    setError("");
    try {
      await fn();
      setSavedAt(Date.now());
    } catch (err) {
      setError(err?.detail || err?.message || "Could not save.");
    }
  }, []);

  const saveCourse = useCallback((body) => run(
    () => patchCourse.mutateAsync({ courseId: selectedId, body })), [run, patchCourse, selectedId]);
  const saveLesson = useCallback((lessonId, body) => run(
    () => patchLesson.mutateAsync({ courseId: selectedId, lessonId, body })),
    [run, patchLesson, selectedId]);
  const saveVisibility = useCallback((roleIds) => run(
    () => saveRoles.mutateAsync({ courseId: selectedId, body: { role_ids: roleIds } })),
    [run, saveRoles, selectedId]);

  async function addLesson() {
    await run(() => createLesson.mutateAsync({
      courseId: selectedId,
      body: { title: "New lesson", source_type: "HERE", required: false },
    }));
  }

  async function startCourse() {
    await run(async () => {
      const created = await createCourse.mutateAsync({
        title: "Untitled course", category: "", state: "Draft",
      });
      setSelectedId(created.item.id);
      setTab("settings");
    });
  }

  if (coursesQuery.isPending || rolesQuery.isPending) return <LoadingState />;
  if (coursesQuery.error || rolesQuery.error) {
    return <ErrorState onRetry={() => { coursesQuery.refetch(); rolesQuery.refetch(); }} />;
  }

  const tabs = [
    { key: "lessons", label: "Lessons", n: (detail?.lessons || []).length },
    { key: "settings", label: "Course settings", n: "" },
    { key: "visibility", label: "Visibility", n: "" },
  ];

  return (
    <div className="cb">
      {/* NO SECOND H1. The reference draws its own page header, but that is the console SHELL's
          job here -- it already renders "Admin Console / Training Library" and the draft-changes
          strip above every screen. Repeating them is exactly the duplication the reference set out
          to remove, so this keeps the half the shell does not provide: what the screen is for, and
          the counts. */}
      <div className="cb-head">
        <div>
          <p>
            Courses, the lessons inside them, and who each one is for. Edits save as you type and
            stay in draft until you publish.
          </p>
        </div>
        <div className="cb-stats">
          {stats.map((s) => (
            <div key={s.k}>
              <strong>{s.v}</strong>
              <span>{s.k}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="cb-shell">
        <div className="cb-rail">
          <div className="cb-railhead">
            <div className="cb-railtop">
              <span className="cb-label">Courses</span>
              <span className="cb-count">{shown.length} of {courses.length}</span>
            </div>
            <input className="cb-search" value={query} placeholder="Search courses"
                   onChange={(e) => setQuery(e.target.value)} />
            <div className="cb-filters">
              {["All", "Live", "Draft", "Needs Review"].map((name) => (
                <button type="button" key={name}
                        className={`cb-filter${stateFilter === name ? " on" : ""}`}
                        onClick={() => setStateFilter(name)}>
                  {name}
                </button>
              ))}
            </div>
          </div>

          <div className="cb-raillist">
            {shown.map((course) => (
              <button type="button" key={course.id}
                      className={`cb-railrow${course.id === selectedId ? " on" : ""}`}
                      onClick={() => { setSelectedId(course.id); setTab("lessons"); }}>
                <span className="cb-railbar" />
                <span className="cb-railbody">
                  <span className="cb-railcat">{course.category || "Uncategorised"}</span>
                  <span className="cb-railtitle">{course.title}</span>
                  <span className="cb-railmeta">{courseMeta(course)}</span>
                </span>
                <span className="cb-tag" style={chip(STATE_CHIP, course.state)}>{course.state}</span>
              </button>
            ))}
            {!shown.length ? (
              <p className="cb-railempty">
                {courses.length ? "No course matches that." : "No courses yet."}
              </p>
            ) : null}
          </div>

          <div className="cb-railfoot">
            <Button tone="primary" busy={createCourse.isPending} onClick={startCourse}>
              New course
            </Button>
          </div>
        </div>

        <div className="cb-detail">
          {!detail ? (
            <div className="cb-pane">
              <p className="cb-intro">
                {courses.length
                  ? "Choose a course on the left, or start a new one."
                  : "Create your first course to get started."}
              </p>
            </div>
          ) : (
            <>
              <div className="cb-detailhead">
                <div className="cb-detailtitle">
                  <div className="cb-detailtags">
                    <span className="cb-tag" style={chip(STATE_CHIP, detail.state)}>
                      {detail.state}
                    </span>
                    <span className="cb-railcat">{detail.category || "Uncategorised"}</span>
                  </div>
                  <h2>{detail.title}</h2>
                  <div className="cb-meta">
                    {courseMeta(detail)}
                    {edited(detail.updated_at) ? ` · Last edited ${edited(detail.updated_at)}` : ""}
                    {detail.last_editor ? ` by ${detail.last_editor}` : ""}
                  </div>
                </div>
                <div className="cb-detailactions">
                  {/* Three states, not two. "Saved" claimed by a screen that has never written
                      anything is the claim people stop believing. */}
                  <span className={`cb-saved${error ? " bad" : ""}`}>
                    <span />
                    {error ? error : saving ? "Saving…" : savedAt ? "All changes saved" : "Draft"}
                  </span>
                  {/* Opens the member's view of this course. New tab on purpose -- an admin
                      checking their work has not finished editing it. */}
                  <a className="console-button" target="_blank" rel="noreferrer noopener"
                     href={`/intranet/training/${detail.id}`}>
                    Preview in portal
                  </a>
                </div>
              </div>

              <div className="cb-tabs">
                {tabs.map((t) => (
                  <button type="button" key={t.key}
                          className={`cb-tab${tab === t.key ? " on" : ""}`}
                          onClick={() => setTab(t.key)}>
                    {t.label}
                    {t.n !== "" ? <span>{t.n}</span> : null}
                  </button>
                ))}
              </div>

              {tab === "lessons" ? (
                <LessonsTab
                  detail={detail}
                  onSaveLesson={saveLesson}
                  onAddLesson={addLesson}
                  onRemoveLesson={(lessonId) => run(
                    () => removeLesson.mutateAsync({ courseId: detail.id, lessonId }))}
                  /* `lessons`, not `ids`: a drag can change a lesson's section as well as its
                     position, and both belong in the same request. */
                  onReorder={(moves) => run(
                    () => orderLessons.mutateAsync({
                      courseId: detail.id, body: { lessons: moves } }))}
                  onAddSection={() => run(
                    () => createSection.mutateAsync({ courseId: detail.id, body: {} }))}
                  onSaveSection={(sectionId, body) => run(
                    () => patchSection.mutateAsync({ courseId: detail.id, sectionId, body }))}
                  onRemoveSection={(sectionId) => run(
                    () => removeSection.mutateAsync({ courseId: detail.id, sectionId }))}
                  onReorderSections={(ids) => run(
                    () => orderSections.mutateAsync({
                      courseId: detail.id, body: { ids } }))}
                />
              ) : null}

              {tab === "settings" ? (
                <SettingsTab
                  detail={detail}
                  onSave={saveCourse}
                  onArchive={() => run(async () => {
                    await archiveCourse.mutateAsync(detail.id);
                    setSelectedId("");
                  })}
                />
              ) : null}

              {tab === "visibility" ? (
                <VisibilityTab detail={detail} roles={roles} onSave={saveVisibility} />
              ) : null}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
