import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  COURSE_STATE_OPTIONS,
  LESSON_SOURCE_OPTIONS,
} from "../constants.js";
import {
  useAddLessonAttachment,
  useArchiveCourse,
  useCourse,
  useCourses,
  useCreateCourse,
  useCreateLesson,
  useDeleteLessonAttachment,
  useOrderLessons,
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
  const save = useCallback((values) => {
    onSave(lesson.id, {
      title: (values.title || "").trim() || lesson.title,
      source_type: values.source_type,
      source_ref: (values.source_ref || "").trim() || null,
      taught_by: (values.taught_by || "").trim() || null,
      description: (values.description || "").trim() || null,
      duration_minutes: values.duration_minutes === "" ? null : Number(values.duration_minutes),
      required: Boolean(values.required),
    });
  }, [lesson.id, lesson.title, onSave]);

  const [draft, update, flush] = useAutosave({
    title: lesson.title || "",
    source_type: lesson.source_type || "HERE",
    source_ref: lesson.source_ref || "",
    taught_by: lesson.taught_by || "",
    description: lesson.description || "",
    duration_minutes: lesson.duration_minutes ?? "",
    required: Boolean(lesson.required),
  }, lesson.id, save);

  const attachments = lesson.attachments || [];
  const attachLabel = attachments.length === 1
    ? "1 file"
    : attachments.length ? `${attachments.length} files` : "";
  const subtitle = [draft.taught_by, draft.source_ref].filter(Boolean).join(" · ");

  return (
    <div className={`cb-lesson${dragging ? " dragging" : ""}`}
         draggable
         onDragStart={(e) => onDragStart(e, index)}
         onDragOver={(e) => onDragOver(e, index)}
         onDrop={(e) => onDrop(e, index)}>
      <div className="cb-lesson-row" onClick={onToggle}>
        <span className="cb-grip" aria-hidden="true">⠿</span>
        <span className="cb-num">{index + 1}</span>
        <span className="cb-lesson-title">
          <strong>{draft.title || "Untitled lesson"}</strong>
          <em>{subtitle}</em>
        </span>
        <span className="cb-tag" style={chip(SOURCE_CHIP, draft.source_type)}>
          {sourceLabel(draft.source_type)}
        </span>
        <span className="cb-dur">{draft.duration_minutes === "" ? "" : `${draft.duration_minutes}m`}</span>
        <span className={`cb-pill${draft.required ? " req" : ""}`}>
          {draft.required ? "Required" : "Optional"}
        </span>
        <span className="cb-att">{attachLabel}</span>
        <span className="cb-caret" aria-hidden="true">{open ? "▲" : "▼"}</span>
      </div>

      {open ? (
        <div className="cb-open">
          <div className="cb-card">
            <div className="cb-grid">
              <Field span={7} label="Lesson title">
                <input value={draft.title} onBlur={flush}
                       onChange={(e) => update({ title: e.target.value })} />
              </Field>
              <Field span={3} label="Source">
                <Select value={draft.source_type} options={LESSON_SOURCE_OPTIONS}
                        onChange={(e) => update({ source_type: e.target.value })} />
              </Field>
              <Field span={2} label="Minutes">
                <input value={draft.duration_minutes} inputMode="numeric" onBlur={flush}
                       onChange={(e) => update({ duration_minutes: e.target.value })} />
              </Field>

              <Field span={7} label="Video URL or storage key"
                     hint="Paste a link or a storage key. Loom, YouTube, Vimeo and PDFs play inside the portal; Skool, PLACE and eXp open in a new tab.">
                <input value={draft.source_ref} onBlur={flush}
                       onChange={(e) => update({ source_ref: e.target.value })} />
              </Field>
              <Field span={5} label="Taught by" hint="Shown as the byline on the lesson.">
                <input value={draft.taught_by} onBlur={flush}
                       onChange={(e) => update({ taught_by: e.target.value })} />
              </Field>

              <Field span={12} label="Description"
                     hint="What the lesson is for and when to watch it. Appears under the title in the portal and feeds the AI assistant.">
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
              <Button tone="danger" onClick={() => onRemove(lesson.id)}>Delete lesson</Button>
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

function LessonsTab({ detail, onSaveLesson, onAddLesson, onRemoveLesson, onReorder }) {
  const lessons = detail.lessons || [];
  const [open, setOpen] = useState(null);
  // THE GRABBED INDEX LIVES IN A REF, and the state beside it exists only to grey the row out.
  // Reading it from state made `drop` depend on a re-render happening between dragstart and drop
  // to refresh its closure -- which a real drag usually gives you and nothing guarantees, so the
  // drop silently did nothing whenever it did not. A ref is current the moment it is set.
  const from = useRef(null);
  const [dragIndex, setDragIndex] = useState(null);

  function dragStart(event, index) {
    from.current = index;
    setDragIndex(index);
    event.dataTransfer.effectAllowed = "move";
    // Firefox refuses to start a drag unless something is on the transfer.
    event.dataTransfer.setData("text/plain", String(index));
  }

  function dragOver(event) {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }

  function drop(event, to) {
    event.preventDefault();
    const start = from.current;
    from.current = null;
    setDragIndex(null);
    if (start === null || start === to) return;
    const ids = lessons.map((l) => l.id);
    const [moved] = ids.splice(start, 1);
    ids.splice(to, 0, moved);
    onReorder(ids);
  }

  return (
    <div>
      <div className="cb-toolbar">
        <span className="cb-label">Lessons · drag to reorder</span>
        <Button onClick={() => onAddLesson()}>Add lesson</Button>
      </div>

      {lessons.map((lesson, index) => (
        <LessonRow
          key={lesson.id}
          courseId={detail.id}
          lesson={lesson}
          index={index}
          total={lessons.length}
          open={open === lesson.id}
          dragging={dragIndex === index}
          onToggle={() => setOpen((cur) => (cur === lesson.id ? null : lesson.id))}
          onSave={onSaveLesson}
          onRemove={(id) => { setOpen(null); onRemoveLesson(id); }}
          onDragStart={dragStart}
          onDragOver={dragOver}
          onDrop={drop}
        />
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

  const courses = coursesQuery.data?.items || [];
  const roles = rolesQuery.data?.items || [];
  const isNew = selectedId === NEW_COURSE_ID;
  const courseQuery = useCourse(isNew ? "" : selectedId, Boolean(selectedId && !isNew));
  const detail = courseQuery.data || null;

  const saving = patchCourse.isPending || patchLesson.isPending || saveRoles.isPending
    || createLesson.isPending || removeLesson.isPending || orderLessons.isPending;

  useEffect(() => {
    if (selectedId || coursesQuery.isPending || coursesQuery.error) return;
    setSelectedId(courses.length ? courses[0].id : NEW_COURSE_ID);
  }, [selectedId, courses, coursesQuery.isPending, coursesQuery.error]);

  useEffect(() => {
    if (!selectedId || isNew || coursesQuery.isPending || coursesQuery.error) return;
    if (courses.some((c) => c.id === selectedId)) return;
    setSelectedId(courses.length ? courses[0].id : NEW_COURSE_ID);
  }, [selectedId, isNew, courses, coursesQuery.isPending, coursesQuery.error]);

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
                  onReorder={(ids) => run(
                    () => orderLessons.mutateAsync({ courseId: detail.id, body: { ids } }))}
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
