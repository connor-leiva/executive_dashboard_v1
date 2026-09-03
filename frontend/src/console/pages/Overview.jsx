import { COUNT_ROUTES, COPY, OVERVIEW_COUNTS } from "../constants.js";
import { Button, EmptyState, ErrorState, LoadingState, MetricCard, Panel, ProgressBar } from "../ui.jsx";

function formatRelative(iso) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  if (days > 0) return `${days}d ago`;
  if (hours > 0) return `${hours}h ago`;
  if (minutes > 0) return `${minutes}m ago`;
  return "Just now";
}

export default function Overview({
  overview,
  setupTasks,
  setupTasksError,
  setupTasksLoading,
  onRetrySetup,
  onToggleTask,
  togglingKey,
}) {
  const counts = overview?.counts || {};
  const setup = overview?.setup || {};
  const visibleTotal = OVERVIEW_COUNTS.reduce((total, item) => total + (Number(counts[item.key]) || 0), 0);
  const pendingChanges = Number(counts.pending_changes) || 0;
  const setupItems = setupTasks?.items || [];
  const activity = overview?.recent_activity || [];
  const progress = Number(setup.percent) || 0;

  if (!visibleTotal && !setup.total) {
    return <EmptyState title={COPY.overviewEmpty} />;
  }

  return (
    <div className="overview-grid">
      <div className="overview-metrics">
        {OVERVIEW_COUNTS.map((item) => (
          <MetricCard
            key={item.key}
            label={item.label}
            value={Number(counts[item.key]) || 0}
            detail={`${pendingChanges} ${COPY.pending}`}
            to={COUNT_ROUTES[item.key]}
          />
        ))}
      </div>

      <Panel title={COPY.setupTitle}>
        <div className="setup-summary">
          <strong>{progress}%</strong>
          <span>{COPY.complete}</span>
        </div>
        <ProgressBar value={progress} />
        {setupTasksLoading ? <LoadingState /> : null}
        {setupTasksError ? <ErrorState onRetry={onRetrySetup} /> : null}
        {!setupTasksLoading && !setupTasksError && !setupItems.length ? <EmptyState title={COPY.emptySetup} /> : null}
        <div className="setup-list">
          {setupItems.map((task) => {
            const complete = Boolean(task.completed_at);
            // A verifiable task whose configuration is absent cannot be ticked -- the server
            // refuses it -- so the box is disabled and says why, rather than letting somebody
            // click it and receive a 422 they have to interpret. A task that cannot be derived
            // (`verifiable: false`) stays a human judgement and is always tickable.
            const blocked = task.verifiable && task.satisfied === false && !complete;
            return (
              <label className={`setup-row${blocked ? " is-blocked" : ""}`} key={task.key}>
                <input
                  type="checkbox"
                  checked={complete}
                  disabled={togglingKey === task.key || blocked}
                  onChange={() => onToggleTask(task.key, !complete)}
                />
                <span>{task.label}</span>
                {blocked && <em className="setup-hint">nothing configured yet</em>}
                {complete && task.satisfied === false && (
                  /* Ticked, but the configuration behind it has since gone. Said out loud
                     because this is the state the checklist exists to catch. */
                  <em className="setup-hint is-warn">marked done, but not configured</em>
                )}
              </label>
            );
          })}
        </div>
      </Panel>

      <Panel title={COPY.recentTitle}>
        {!activity.length ? <EmptyState title={COPY.emptyActivity} /> : null}
        <div className="activity-list">
          {activity.slice(0, 6).map((item) => (
            <article className="activity-row" key={item.id}>
              <div>
                <strong>{item.summary || item.action}</strong>
                <span>{item.actor_label || item.category || item.target_type}</span>
              </div>
              <time>{formatRelative(item.created_at)}</time>
            </article>
          ))}
        </div>
      </Panel>
    </div>
  );
}
