import { useEffect, useMemo, useState } from "react";

import {
  CALENDAR_VIEW_OPTIONS,
  COPY,
  TIMEZONE_OPTIONS,
  WEEK_START_OPTIONS,
} from "../constants.js";
import {
  useCalendarCategories,
  useCreateCalendarCategory,
  usePatchCalendarCategory,
  usePatchWorkspace,
  useRemoveCalendarCategory,
  useRoles,
  useWorkspace,
} from "../queries.js";
import { Button, EmptyState, ErrorState, Field, LoadingState, Panel } from "../ui.jsx";

function defaultsForm(workspace) {
  return {
    timezone: workspace?.timezone || "America/Denver",
    week_starts_on: workspace?.week_starts_on ?? 1,
    default_calendar_view: workspace?.default_calendar_view || "week",
  };
}

function categoryForm(category, roles) {
  return {
    name: category?.name || "",
    color: category?.color || "#C9A227",
    calendar_address: category?.calendar_address || "",
    active: category?.active ?? true,
    role_ids: category?.role_ids || roles.map((role) => role.id),
  };
}

function categoryPayload(form) {
  return {
    name: form.name.trim(),
    color: form.color,
    calendar_address: form.calendar_address.trim() || null,
    active: Boolean(form.active),
    role_ids: form.role_ids,
  };
}

function dayParts(date, timezone) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: timezone,
    year: "numeric",
    month: "numeric",
    day: "numeric",
  }).formatToParts(date);
  return {
    year: Number(parts.find((part) => part.type === "year")?.value),
    month: Number(parts.find((part) => part.type === "month")?.value),
    day: Number(parts.find((part) => part.type === "day")?.value),
  };
}

function addDays(date, days) {
  const next = new Date(date);
  next.setUTCDate(next.getUTCDate() + days);
  return next;
}

function weekDays(timezone, weekStartsOn) {
  const todayParts = dayParts(new Date(), timezone);
  const today = new Date(Date.UTC(todayParts.year, todayParts.month - 1, todayParts.day));
  const offset = (today.getUTCDay() - Number(weekStartsOn) + 7) % 7;
  const start = addDays(today, -offset);
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC",
    weekday: "short",
    month: "short",
    day: "numeric",
  });
  return Array.from({ length: 7 }, (_, index) => ({
    key: addDays(start, index).toISOString(),
    label: formatter.format(addDays(start, index)),
  }));
}

function RoleChecks({ roles, selectedIds, onToggle }) {
  const selected = new Set(selectedIds);
  return (
    <div className="calendar-role-list">
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

function DefaultsPanel({ workspace, onSave, busy }) {
  const [form, setForm] = useState(() => defaultsForm(workspace));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setForm(defaultsForm(workspace));
  }, [workspace]);

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  async function submit(event) {
    event.preventDefault();
    setMessage("");
    setError("");
    try {
      await onSave({
        timezone: form.timezone,
        week_starts_on: Number(form.week_starts_on),
        default_calendar_view: form.default_calendar_view,
      });
      setMessage(COPY.calendarDefaultsSaved);
    } catch (err) {
      setError(err.message || COPY.loadFailed);
    }
  }

  return (
    <Panel title={COPY.calendarDefaults}>
      <form className="calendar-default-form" onSubmit={submit}>
        <Field label={COPY.calendarTimezone}>
          <select value={form.timezone} onChange={(event) => update("timezone", event.target.value)}>
            {TIMEZONE_OPTIONS.map((timezone) => (
              <option key={timezone} value={timezone}>{timezone}</option>
            ))}
          </select>
        </Field>
        <Field label={COPY.calendarWeekStart}>
          <select value={form.week_starts_on} onChange={(event) => update("week_starts_on", event.target.value)}>
            {WEEK_START_OPTIONS.map((option) => (
              <option key={option.key} value={option.key}>{option.label}</option>
            ))}
          </select>
        </Field>
        <Field label={COPY.calendarDefaultView}>
          <select value={form.default_calendar_view} onChange={(event) => update("default_calendar_view", event.target.value)}>
            {CALENDAR_VIEW_OPTIONS.map((option) => (
              <option key={option.key} value={option.key}>{option.label}</option>
            ))}
          </select>
        </Field>
        <div className="calendar-actions">
          <Button type="submit" tone="primary" busy={busy}>{COPY.calendarSaveDefaults}</Button>
          {message ? <span>{message}</span> : null}
          {error ? <span className="brand-error">{error}</span> : null}
        </div>
      </form>
    </Panel>
  );
}

function CategoryRow({ category, roles, busy, onSave, onRemove }) {
  const [form, setForm] = useState(() => categoryForm(category, roles));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setForm(categoryForm(category, roles));
  }, [category, roles]);

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function toggleRole(roleId) {
    setForm((current) => {
      const selected = new Set(current.role_ids);
      if (selected.has(roleId)) selected.delete(roleId);
      else selected.add(roleId);
      return { ...current, role_ids: Array.from(selected) };
    });
  }

  async function submit(event) {
    event.preventDefault();
    setMessage("");
    setError("");
    try {
      await onSave(category.id, categoryPayload(form));
      setMessage(COPY.calendarSaved);
    } catch (err) {
      setError(err.message || COPY.loadFailed);
    }
  }

  return (
    <form className={`calendar-row ${form.active ? "" : "inactive"}`} onSubmit={submit}>
      <div className="calendar-category-fields">
        <Field label={COPY.calendarName}>
          <input value={form.name} onChange={(event) => update("name", event.target.value)} required />
        </Field>
        <Field label={COPY.calendarColor}>
          <div className="brand-color-control">
            <input type="color" value={form.color} onChange={(event) => update("color", event.target.value.toUpperCase())} />
            <input value={form.color} onChange={(event) => update("color", event.target.value)} pattern="^#[0-9A-Fa-f]{6}$" required />
          </div>
        </Field>
        <Field label={COPY.calendarAddress}>
          <input value={form.calendar_address} onChange={(event) => update("calendar_address", event.target.value)} />
        </Field>
        <label className="tile-active">
          <input type="checkbox" checked={form.active} onChange={(event) => update("active", event.target.checked)} />
          <span>{COPY.calendarActive}</span>
        </label>
      </div>
      <div className="calendar-role-panel">
        <h3>{COPY.calendarVisibility}</h3>
        <RoleChecks roles={roles} selectedIds={form.role_ids} onToggle={toggleRole} />
      </div>
      <div className="calendar-actions">
        <Button type="submit" tone="primary" busy={busy}>{COPY.calendarSave}</Button>
        <Button type="button" disabled={busy} onClick={() => onRemove(category.id)}>{COPY.calendarRemove}</Button>
        {message ? <span>{message}</span> : null}
        {error ? <span className="brand-error">{error}</span> : null}
      </div>
    </form>
  );
}

function NewCategory({ roles, busy, onCreate }) {
  const [form, setForm] = useState(() => categoryForm(null, roles));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    setForm((current) => ({ ...current, role_ids: current.role_ids.length ? current.role_ids : roles.map((role) => role.id) }));
  }, [roles]);

  function update(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function toggleRole(roleId) {
    setForm((current) => {
      const selected = new Set(current.role_ids);
      if (selected.has(roleId)) selected.delete(roleId);
      else selected.add(roleId);
      return { ...current, role_ids: Array.from(selected) };
    });
  }

  async function submit(event) {
    event.preventDefault();
    setMessage("");
    setError("");
    try {
      await onCreate(categoryPayload(form));
      setForm(categoryForm(null, roles));
      setMessage(COPY.calendarCreated);
    } catch (err) {
      setError(err.message || COPY.loadFailed);
    }
  }

  return (
    <form className="calendar-row calendar-row-new" onSubmit={submit}>
      <div className="calendar-category-fields">
        <Field label={COPY.calendarName}>
          <input value={form.name} onChange={(event) => update("name", event.target.value)} required />
        </Field>
        <Field label={COPY.calendarColor}>
          <div className="brand-color-control">
            <input type="color" value={form.color} onChange={(event) => update("color", event.target.value.toUpperCase())} />
            <input value={form.color} onChange={(event) => update("color", event.target.value)} pattern="^#[0-9A-Fa-f]{6}$" required />
          </div>
        </Field>
        <Field label={COPY.calendarAddress}>
          <input value={form.calendar_address} onChange={(event) => update("calendar_address", event.target.value)} />
        </Field>
        <label className="tile-active">
          <input type="checkbox" checked={form.active} onChange={(event) => update("active", event.target.checked)} />
          <span>{COPY.calendarActive}</span>
        </label>
      </div>
      <div className="calendar-role-panel">
        <h3>{COPY.calendarVisibility}</h3>
        <RoleChecks roles={roles} selectedIds={form.role_ids} onToggle={toggleRole} />
      </div>
      <div className="calendar-actions">
        <Button type="submit" tone="primary" busy={busy}>{COPY.calendarCreate}</Button>
        {message ? <span>{message}</span> : null}
        {error ? <span className="brand-error">{error}</span> : null}
      </div>
    </form>
  );
}

function CalendarPreview({ workspace, categories }) {
  const days = useMemo(
    () => weekDays(workspace?.timezone || "America/Denver", workspace?.week_starts_on ?? 1),
    [workspace?.timezone, workspace?.week_starts_on],
  );
  const configured = categories.filter((category) => category.active && category.calendar_address);

  return (
    <Panel title={COPY.calendarPreview}>
      <div className="calendar-preview">
        <div className="calendar-preview-meta">
          <span>{workspace?.timezone || "America/Denver"}</span>
          <strong>{workspace?.default_calendar_view || "week"}</strong>
          <span>{configured.length} configured</span>
        </div>
        <div className="calendar-week-grid" aria-label={COPY.calendarPreview}>
          {days.map((day) => (
            <div className="calendar-day" key={day.key}>
              <strong>{day.label}</strong>
              <span>{COPY.calendarNoEvents}</span>
            </div>
          ))}
        </div>
        <div className="calendar-disconnect">
          <strong>{COPY.calendarDisconnected}</strong>
        </div>
      </div>
    </Panel>
  );
}

export default function TeamCalendar() {
  const workspace = useWorkspace(true);
  const categories = useCalendarCategories(true);
  const roles = useRoles(true);
  const saveDefaults = usePatchWorkspace();
  const createCategory = useCreateCalendarCategory();
  const patchCategory = usePatchCalendarCategory();
  const removeCategory = useRemoveCalendarCategory();

  const loading = workspace.isLoading || categories.isLoading || roles.isLoading;
  const error = workspace.isError || categories.isError || roles.isError;
  const roleItems = roles.data?.items || [];
  const categoryItems = categories.data?.items || [];

  if (loading) return <LoadingState title={COPY.loading} />;
  if (error) {
    return (
      <ErrorState
        title={COPY.calendarError}
        onRetry={() => {
          workspace.refetch();
          categories.refetch();
          roles.refetch();
        }}
      />
    );
  }

  return (
    <div className="calendar-grid">
      <DefaultsPanel
        workspace={workspace.data}
        busy={saveDefaults.isPending}
        onSave={(body) => saveDefaults.mutateAsync(body)}
      />
      <CalendarPreview workspace={workspace.data} categories={categoryItems} />
      <Panel title={COPY.calendarCategories}>
        {!categoryItems.length ? <EmptyState title={COPY.calendarNoEvents} /> : null}
        <div className="calendar-category-list">
          {categoryItems.map((category) => (
            <CategoryRow
              key={category.id}
              category={category}
              roles={roleItems}
              busy={patchCategory.isPending || removeCategory.isPending}
              onSave={(categoryId, body) => patchCategory.mutateAsync({ categoryId, body })}
              onRemove={(categoryId) => removeCategory.mutateAsync(categoryId)}
            />
          ))}
          <NewCategory
            roles={roleItems}
            busy={createCategory.isPending}
            onCreate={(body) => createCategory.mutateAsync(body)}
          />
        </div>
      </Panel>
    </div>
  );
}
