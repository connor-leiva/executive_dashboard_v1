import { Fragment, useEffect, useMemo, useState } from "react";

import {
  COPY,
  COURSE_STATE_OPTIONS,
  LESSON_SOURCE_COLORS,
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
import { Button, EmptyState, ErrorState, Field, LoadingState, Panel } from "../ui.jsx";

const NEW_COURSE_ID = "new";

function minutesLabel(minutes) {
  const value = Number(minutes) || 0;
  const hours = Math.floor(value / 60);
  const mins = value % 60;
  if (!hours) return `${mins}m`;
  return `${hours}h ${String(mins).padStart(2, "0")}m`;
}

function courseMeta(course) {
  const lessons = course?.lesson_count ?? 0;
  return `${lessons} ${lessons === 1 ? "lesson" : "lessons"} - ${minutesLabel(course?.total_duration_minutes)}`;
}

function sourceLabel(key) {
  return LESSON_SOURCE_OPTIONS.find((item) => item.key === key)?.label || key;
}

function courseForm(course, roles) {
  return {
    title: course?.title || "",
    category: course?.category || "",
    description: course?.description || "",
    state: course?.state || "Draft",
    track_progress: course?.track_progress ?? true,
    required_for_onboarding: course?.required_for_onboarding ?? false,
    issues_certificate: course?.issues_certificate ?? false,
    sequential: course?.sequential ?? false,
    role_ids: course?.role_ids || roles.map((role) => role.id),
  };
}

function coursePayload(form) {
  return {
    title: form.title.trim(),
    category: form.category.trim(),
    description: form.description.trim() || null,
    state: form.state,
    track_progress: Boolean(form.track_progress),
    required_for_onboarding: Boolean(form.required_for_onboarding),
    issues_certificate: Boolean(form.issues_certificate),
    sequential: Boolean(form.sequential),
  };
}

function lessonPayload(form) {
  return {
    title: form.title.trim(),
    source_type: form.source_type,
    source_ref: form.source_ref.trim() || null,
    source_label: form.source_label.trim() || null,
    // `?? ""` because NewLessonForm's draft has no description field -- a new lesson is created
    // with its source and named, then described in the row that appears. Reading `.trim()` off
    // undefined there would throw on every "Add Lesson".
    description: (form.description ?? "").trim() || null,
    duration_minutes: form.duration_minutes === "" ? null : Number(form.duration_minutes),
    required: Boolean(form.required),
  };
}

function ToggleField({ label, checked, onChange }) {
  return (
    <label className="tile-active">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

function RoleChecks({ roles, selectedIds, onToggle }) {
  const selected = new Set(selectedIds);
  return (
    <div className="training-role-list">
      {roles.map((role) => (
        <label key={role.id}>
          <input
            type="checkbox"
            checked={selected.has(role.id)}
            onChange={() => onToggle(role.id)}
          />
          <span>{role.name}</span>
        </label>
      ))}
    </div>
  );
}

function CourseList({ courses, selectedId, onSelect, onNew }) {
  return (
    <Panel
      title={COPY.trainingTitle}
      action={<Button type="button" onClick={onNew}>{COPY.trainingNewCourse}</Button>}
    >
      {!courses.length ? <EmptyState title={COPY.trainingEmpty} /> : null}
      <div className="training-course-list">
        {courses.map((course) => (
          <button
            type="button"
            key={course.id}
            className={`training-course-row ${course.id === selectedId ? "selected" : ""}`}
            onClick={() => onSelect(course.id)}
          >
            <span>{course.category}</span>
            <strong>{course.title}</strong>
            <small>{courseMeta(course)}</small>
          </button>
        ))}
      </div>
    </Panel>
  );
}

function LessonSourceBadge({ type }) {
  return (
    <span className="lesson-source" style={{ "--lesson-source": LESSON_SOURCE_COLORS[type] || "#395262" }}>
      {sourceLabel(type)}
    </span>
  );
}

function LessonRow({ lesson, index, total, busy, onSave, onRemove, onMove }) {
  const [draft, setDraft] = useState({
    title: lesson.title || "",
    source_type: lesson.source_type || "PLACE",
    source_ref: lesson.source_ref || "",
    source_label: lesson.source_label || "",
    description: lesson.description || "",
    duration_minutes: lesson.duration_minutes === null || lesson.duration_minutes === undefined ? "" : String(lesson.duration_minutes),
    required: Boolean(lesson.required),
  });

  useEffect(() => {
    setDraft({
      title: lesson.title || "",
      source_type: lesson.source_type || "PLACE",
      source_ref: lesson.source_ref || "",
      source_label: lesson.source_label || "",
      description: lesson.description || "",
      duration_minutes: lesson.duration_minutes === null || lesson.duration_minutes === undefined ? "" : String(lesson.duration_minutes),
      required: Boolean(lesson.required),
    });
  }, [lesson]);

  function update(field, value) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  function submit(event) {
    event.preventDefault();
    onSave(lesson.id, lessonPayload(draft));
  }

  return (
    <form className="lesson-row" onSubmit={submit}>
      <div className="lesson-order">
        <strong>{index + 1}</strong>
        <div className="tile-actions">
          <button type="button" title={COPY.moveUp} disabled={busy || index === 0} onClick={() => onMove(index, index - 1)}>
            {COPY.moveUp}
          </button>
          <button type="button" title={COPY.moveDown} disabled={busy || index === total - 1} onClick={() => onMove(index, index + 1)}>
            {COPY.moveDown}
          </button>
        </div>
      </div>
      <Field label={COPY.trainingLessonTitle}>
        <input value={draft.title} onChange={(event) => update("title", event.target.value)} required />
      </Field>
      <Field label={COPY.trainingSourceType}>
        <select value={draft.source_type} onChange={(event) => update("source_type", event.target.value)}>
          {LESSON_SOURCE_OPTIONS.map((option) => (
            <option key={option.key} value={option.key}>{option.label}</option>
          ))}
        </select>
      </Field>
      <Field label={COPY.trainingSourceRef}>
        <input value={draft.source_ref} onChange={(event) => update("source_ref", event.target.value)} />
      </Field>
      <Field label={COPY.trainingSourceLabel}>
        <input value={draft.source_label} onChange={(event) => update("source_label", event.target.value)} />
      </Field>
      <Field label={COPY.trainingDuration}>
        <input
          value={draft.duration_minutes}
          onChange={(event) => update("duration_minutes", event.target.value)}
          inputMode="numeric"
          min="0"
          type="number"
        />
      </Field>
      <label className="tile-active">
        <input checked={draft.required} type="checkbox" onChange={(event) => update("required", event.target.checked)} />
        <span>{COPY.trainingRequiredLesson}</span>
      </label>
      <LessonSourceBadge type={draft.source_type} />
      <Field label="Description">
        {/* The paragraph under the video in the portal. Without it the player shows a title and
            nothing else, which is what the old checklist amounted to. */}
        <textarea
          rows="3"
          value={draft.description}
          placeholder="What this lesson is for, and when to watch it."
          onChange={(event) => update("description", event.target.value)}
        />
      </Field>
      {/* The server already worked out whether this source can play in the page. Saying so HERE
          is the point: an admin who pastes a Skool link finds out now, rather than an agent
          finding a launch card where a video should be. */}
      {lesson.player?.mode === "link" && lesson.source_ref ? (
        <p className="hint">Opens in a new tab rather than playing here. {lesson.player.reason}</p>
      ) : null}
      <div className="lesson-actions">
        <Button type="submit" tone="primary" busy={busy}>{COPY.trainingSaveLesson}</Button>
        <Button type="button" disabled={busy} onClick={() => onRemove(lesson.id)}>{COPY.trainingRemoveLesson}</Button>
      </div>
    </form>
  );
}

/* Handouts for one lesson: the one-pagers, packets and templates that hang off it.
 *
 * Outside LessonRow's <form> deliberately. Nesting an upload inside the lesson form would make
 * one submit do two unrelated things, and an upload failure would discard a text edit the admin
 * had already made. */
function LessonAttachments({ courseId, lesson }) {
  const add = useAddLessonAttachment();
  const remove = useDeleteLessonAttachment();
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
      setTitle(""); setNote(""); setUrl(""); setFile(null);
      node.reset();
    } catch (err) {
      setError(err?.detail || err?.message || "Could not add that.");
    }
  }

  return (
    <div className="lesson-attachments">
      <span className="lesson-attachments-title">Attachments</span>
      {attachments.length ? (
        <ul>
          {attachments.map((a) => (
            <li key={a.id}>
              <span className="attachment-kind">{a.kind === "link" ? "Link" : "File"}</span>
              <strong>{a.title}</strong>
              {a.note ? <em>{a.note}</em> : null}
              <button type="button" disabled={busy}
                      onClick={() => remove.mutate({ courseId, lessonId: lesson.id, attachmentId: a.id })}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      ) : <p className="hint">No attachments on this lesson.</p>}

      <form className="lesson-attachment-add" onSubmit={submit}>
        <select value={kind} onChange={(event) => setKind(event.target.value)}>
          <option value="file">Upload a file</option>
          <option value="link">Link somewhere else</option>
        </select>
        <input value={title} required placeholder="Name"
               onChange={(event) => setTitle(event.target.value)} />
        <input value={note} placeholder="Note — 2 pages, Team Drive…"
               onChange={(event) => setNote(event.target.value)} />
        {kind === "link" ? (
          <input value={url} required placeholder="https://…"
                 onChange={(event) => setUrl(event.target.value)} />
        ) : (
          <input type="file" required accept=".pdf,.png,.jpg,.jpeg,.webp"
                 onChange={(event) => setFile(event.target.files?.[0] || null)} />
        )}
        <Button type="submit" busy={add.isPending}>Add</Button>
      </form>
      {error ? <p className="error">{error}</p> : null}
    </div>
  );
}

function NewLessonForm({ busy, onCreate }) {
  const [draft, setDraft] = useState({
    title: "",
    source_type: "PLACE",
    source_ref: "",
    source_label: "",
    duration_minutes: "",
    required: false,
  });

  function update(field, value) {
    setDraft((current) => ({ ...current, [field]: value }));
  }

  async function submit(event) {
    event.preventDefault();
    await onCreate(lessonPayload(draft));
    setDraft({
      title: "",
      source_type: "PLACE",
      source_ref: "",
      source_label: "",
      duration_minutes: "",
      required: false,
    });
  }

  return (
    <form className="lesson-row lesson-row-new" onSubmit={submit}>
      <div className="lesson-order"><strong>+</strong></div>
      <Field label={COPY.trainingLessonTitle}>
        <input value={draft.title} onChange={(event) => update("title", event.target.value)} required />
      </Field>
      <Field label={COPY.trainingSourceType}>
        <select value={draft.source_type} onChange={(event) => update("source_type", event.target.value)}>
          {LESSON_SOURCE_OPTIONS.map((option) => (
            <option key={option.key} value={option.key}>{option.label}</option>
          ))}
        </select>
      </Field>
      <Field label={COPY.trainingSourceRef}>
        <input value={draft.source_ref} onChange={(event) => update("source_ref", event.target.value)} />
      </Field>
      <Field label={COPY.trainingSourceLabel}>
        <input value={draft.source_label} onChange={(event) => update("source_label", event.target.value)} />
      </Field>
      <Field label={COPY.trainingDuration}>
        <input
          value={draft.duration_minutes}
          onChange={(event) => update("duration_minutes", event.target.value)}
          inputMode="numeric"
          min="0"
          type="number"
        />
      </Field>
      <label className="tile-active">
        <input checked={draft.required} type="checkbox" onChange={(event) => update("required", event.target.checked)} />
        <span>{COPY.trainingRequiredLesson}</span>
      </label>
      <LessonSourceBadge type={draft.source_type} />
      <div className="lesson-actions">
        <Button type="submit" tone="primary" busy={busy}>{COPY.trainingAddLesson}</Button>
      </div>
    </form>
  );
}

function CourseDetail({
  course,
  roles,
  isNew,
  form,
  setForm,
  busy,
  message,
  error,
  onSaveCourse,
  onArchiveCourse,
  onSaveLesson,
  onCreateLesson,
  onRemoveLesson,
  onMoveLesson,
}) {
  const lessons = course?.lessons || [];

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function toggleRole(roleId) {
    update(
      "role_ids",
      form.role_ids.includes(roleId)
        ? form.role_ids.filter((id) => id !== roleId)
        : [...form.role_ids, roleId],
    );
  }

  return (
    <Panel
      title={isNew ? COPY.trainingNewCourse : form.title || COPY.trainingSelectCourse}
      action={(
        <Button type="button" disabled title={COPY.trainingImportUnavailable}>
          {COPY.trainingImportSkool}
        </Button>
      )}
    >
      <form className="training-detail-form" onSubmit={onSaveCourse}>
        <div className="training-fields">
          <Field label={COPY.trainingCourseTitle}>
            <input value={form.title} onChange={(event) => update("title", event.target.value)} required />
          </Field>
          <Field label={COPY.trainingCategory}>
            <input value={form.category} onChange={(event) => update("category", event.target.value)} required />
          </Field>
          <Field label={COPY.trainingState}>
            <select value={form.state} onChange={(event) => update("state", event.target.value)}>
              {COURSE_STATE_OPTIONS.map((option) => (
                <option key={option.key} value={option.key}>{option.label}</option>
              ))}
            </select>
          </Field>
          <Field label={COPY.trainingDescription}>
            <textarea value={form.description} onChange={(event) => update("description", event.target.value)} rows="4" />
          </Field>
        </div>
        <div className="training-toggle-grid">
          <ToggleField label={COPY.trainingTrackProgress} checked={form.track_progress} onChange={(value) => update("track_progress", value)} />
          <ToggleField label={COPY.trainingRequiredOnboarding} checked={form.required_for_onboarding} onChange={(value) => update("required_for_onboarding", value)} />
          <ToggleField label={COPY.trainingCertificate} checked={form.issues_certificate} onChange={(value) => update("issues_certificate", value)} />
          <ToggleField label={COPY.trainingSequential} checked={form.sequential} onChange={(value) => update("sequential", value)} />
        </div>
        <section className="training-role-panel">
          <h3>{COPY.trainingVisibility}</h3>
          <RoleChecks roles={roles} selectedIds={form.role_ids} onToggle={toggleRole} />
        </section>
        {message ? <p className="roster-form-message">{message}</p> : null}
        {error ? <p className="console-form-error">{error}</p> : null}
        <div className="training-actions">
          <Button type="submit" tone="primary" busy={busy}>
            {isNew ? COPY.trainingCreateCourse : COPY.trainingSaveCourse}
          </Button>
          {!isNew ? (
            <Button type="button" disabled={busy} onClick={onArchiveCourse}>{COPY.trainingArchiveCourse}</Button>
          ) : null}
        </div>
      </form>

      {!isNew ? (
        <section className="training-lessons">
          <header>
            <h3>{COPY.trainingLessons}</h3>
            <span>{courseMeta(course)}</span>
          </header>
          <div className="lesson-list">
            {lessons.map((lesson, index) => (
              <Fragment key={lesson.id}>
                <LessonRow
                  lesson={lesson}
                  index={index}
                  total={lessons.length}
                  busy={busy}
                  onSave={onSaveLesson}
                  onRemove={onRemoveLesson}
                  onMove={onMoveLesson}
                />
                <LessonAttachments courseId={course.id} lesson={lesson} />
              </Fragment>
            ))}
            <NewLessonForm busy={busy} onCreate={onCreateLesson} />
          </div>
        </section>
      ) : null}
    </Panel>
  );
}

export default function Training() {
  const coursesQuery = useCourses(true);
  const rolesQuery = useRoles(true);
  const [selectedId, setSelectedId] = useState("");
  const [form, setForm] = useState(() => courseForm(null, []));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
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
  const isNew = selectedId === NEW_COURSE_ID || !selectedId;
  const courseQuery = useCourse(isNew ? "" : selectedId, Boolean(selectedId && !isNew));
  const detail = courseQuery.data || null;
  const busy = createCourse.isPending
    || patchCourse.isPending
    || archiveCourse.isPending
    || saveRoles.isPending
    || createLesson.isPending
    || patchLesson.isPending
    || removeLesson.isPending
    || orderLessons.isPending;

  useEffect(() => {
    if (selectedId || coursesQuery.isPending || coursesQuery.error) return;
    setSelectedId(courses.length ? courses[0].id : NEW_COURSE_ID);
  }, [selectedId, courses, coursesQuery.isPending, coursesQuery.error]);

  useEffect(() => {
    if (!selectedId || selectedId === NEW_COURSE_ID || coursesQuery.isPending || coursesQuery.error) return;
    if (courses.some((course) => course.id === selectedId)) return;
    setSelectedId(courses.length ? courses[0].id : NEW_COURSE_ID);
  }, [selectedId, courses, coursesQuery.isPending, coursesQuery.error]);

  useEffect(() => {
    if (!selectedId) return;
    if (isNew) setForm(courseForm(null, roles));
    else if (detail) setForm(courseForm(detail, roles));
  }, [selectedId, isNew, detail, roles]);

  function selectCourse(courseId) {
    setMessage("");
    setError("");
    setSelectedId(courseId);
  }

  function newCourse() {
    setMessage("");
    setError("");
    setSelectedId(NEW_COURSE_ID);
    setForm(courseForm(null, roles));
  }

  async function saveCourse(event) {
    event.preventDefault();
    setMessage("");
    setError("");
    try {
      let courseId = detail?.id;
      if (isNew) {
        const created = await createCourse.mutateAsync(coursePayload(form));
        courseId = created.item.id;
        await saveRoles.mutateAsync({ courseId, body: { role_ids: form.role_ids } });
        setSelectedId(courseId);
        setMessage(COPY.trainingCourseCreated);
      } else {
        await patchCourse.mutateAsync({ courseId, body: coursePayload(form) });
        await saveRoles.mutateAsync({ courseId, body: { role_ids: form.role_ids } });
        setMessage(COPY.trainingCourseSaved);
      }
    } catch (err) {
      setError(err.detail || err.message);
    }
  }

  async function archiveSelectedCourse() {
    if (!detail) return;
    setMessage("");
    setError("");
    try {
      await archiveCourse.mutateAsync(detail.id);
      setSelectedId("");
      setMessage(COPY.trainingCourseSaved);
    } catch (err) {
      setError(err.detail || err.message);
    }
  }

  async function saveLesson(lessonId, body) {
    if (!detail) return;
    setMessage("");
    setError("");
    try {
      await patchLesson.mutateAsync({ courseId: detail.id, lessonId, body });
      setMessage(COPY.trainingLessonSaved);
    } catch (err) {
      setError(err.detail || err.message);
    }
  }

  async function addLesson(body) {
    if (!detail) return;
    setMessage("");
    setError("");
    try {
      await createLesson.mutateAsync({ courseId: detail.id, body });
      setMessage(COPY.trainingLessonCreated);
    } catch (err) {
      setError(err.detail || err.message);
      throw err;
    }
  }

  async function deleteLesson(lessonId) {
    if (!detail) return;
    setMessage("");
    setError("");
    try {
      await removeLesson.mutateAsync({ courseId: detail.id, lessonId });
      setMessage(COPY.trainingLessonSaved);
    } catch (err) {
      setError(err.detail || err.message);
    }
  }

  async function moveLesson(from, to) {
    const lessons = detail?.lessons || [];
    if (!detail || to < 0 || to >= lessons.length) return;
    const ids = lessons.map((lesson) => lesson.id);
    const [moved] = ids.splice(from, 1);
    ids.splice(to, 0, moved);
    setMessage("");
    setError("");
    try {
      await orderLessons.mutateAsync({ courseId: detail.id, body: { ids } });
      setMessage(COPY.trainingLessonSaved);
    } catch (err) {
      setError(err.detail || err.message);
    }
  }

  if (coursesQuery.isPending || rolesQuery.isPending) return <LoadingState />;
  if (coursesQuery.error || rolesQuery.error) {
    return (
      <ErrorState
        title={COPY.trainingError}
        onRetry={() => {
          coursesQuery.refetch();
          rolesQuery.refetch();
        }}
      />
    );
  }

  return (
    <div className="training-grid">
      <CourseList courses={courses} selectedId={selectedId} onSelect={selectCourse} onNew={newCourse} />
      {courseQuery.isPending && !isNew ? <LoadingState /> : null}
      {courseQuery.error && !isNew ? <ErrorState title={COPY.trainingError} onRetry={() => courseQuery.refetch()} /> : null}
      {/* A DISABLED QUERY IS PERMANENTLY `isPending` in React Query v5 -- pending means "no data
          yet", and a query that is switched off will never have any. courseQuery is switched off
          precisely when isNew, so `!courseQuery.isPending` was false forever on this branch and
          the new-course form could never render: clicking New Course set the state and painted
          nothing. Waiting on a query we deliberately turned off was the mistake; when isNew there
          is nothing to wait for. `detail` already implies the query resolved, so the other two
          guards were doing nothing that it does not do. */}
      {isNew || detail ? (
        <CourseDetail
          course={detail}
          roles={roles}
          isNew={isNew}
          form={form}
          setForm={setForm}
          busy={busy}
          message={message}
          error={error}
          onSaveCourse={saveCourse}
          onArchiveCourse={archiveSelectedCourse}
          onSaveLesson={saveLesson}
          onCreateLesson={addLesson}
          onRemoveLesson={deleteLesson}
          onMoveLesson={moveLesson}
        />
      ) : null}
    </div>
  );
}
