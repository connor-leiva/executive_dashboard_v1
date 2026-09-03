import { NavLink } from "react-router-dom";

import { COPY } from "./constants.js";

export function Button({ children, tone = "default", busy = false, ...props }) {
  return (
    <button {...props} className={`console-button console-button-${tone}`} disabled={busy || props.disabled}>
      {children}
    </button>
  );
}

export function Field({ label, children }) {
  return (
    <label className="console-field">
      <span>{label}</span>
      {children}
    </label>
  );
}

export function EmptyState({ title }) {
  return (
    <div className="console-state console-empty">
      <div className="console-state-mark" />
      <p>{title}</p>
    </div>
  );
}

export function ErrorState({ title = COPY.loadFailed, action, onRetry }) {
  return (
    <div className="console-state console-error">
      <div className="console-state-mark" />
      <p>{title}</p>
      {onRetry ? <Button type="button" onClick={onRetry}>{action || COPY.retry}</Button> : null}
    </div>
  );
}

export function LoadingState({ title = COPY.loading }) {
  return (
    <div className="console-loading" aria-label={title}>
      <div />
      <div />
      <div />
    </div>
  );
}

export function ProgressBar({ value }) {
  const width = `${Math.max(0, Math.min(100, Number(value) || 0))}%`;
  return (
    <div className="console-progress" aria-hidden="true">
      <span style={{ width }} />
    </div>
  );
}

export function MetricCard({ label, value, detail, to }) {
  return (
    <NavLink className="console-metric" to={to}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </NavLink>
  );
}

export function Panel({ title, action, children }) {
  return (
    <section className="console-panel">
      <header className="console-panel-header">
        <h2>{title}</h2>
        {action}
      </header>
      {children}
    </section>
  );
}

export function ParkedScreen({ title }) {
  return (
    <Panel title={title}>
      <EmptyState title={COPY.incompleteScreen} />
    </Panel>
  );
}
