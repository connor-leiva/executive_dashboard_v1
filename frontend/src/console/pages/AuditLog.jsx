import { useEffect, useMemo, useState } from "react";

import { AUDIT_FILTERS, COPY } from "../constants.js";
import { useAudit, useLoadAudit } from "../queries.js";
import { Button, EmptyState, ErrorState, LoadingState, Panel } from "../ui.jsx";

const PAGE_LIMIT = 25;

function queryFor(filter, cursor) {
  return {
    category: filter === "Everything" ? undefined : filter,
    before: cursor || undefined,
    limit: PAGE_LIMIT,
  };
}

function formatDate(value) {
  if (!value) return "";
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function categoryClass(category) {
  return String(category || "event")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

function AuditTable({ items }) {
  if (!items.length) return <EmptyState title={COPY.auditEmpty} />;
  return (
    <div className="audit-table-wrap">
      <table className="audit-table">
        <thead>
          <tr>
            <th>{COPY.auditTimestamp}</th>
            <th>{COPY.auditActor}</th>
            <th>{COPY.auditSummary}</th>
            <th>{COPY.auditCategory}</th>
            <th>{COPY.auditAction}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td><time>{formatDate(item.created_at)}</time></td>
              <td>{item.actor_label || "System"}</td>
              <td>{item.summary || item.action}</td>
              <td>
                <span className={`audit-chip ${categoryClass(item.category)}`}>{item.category}</span>
              </td>
              <td><code>{item.action}</code></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function AuditLog() {
  const [filter, setFilter] = useState("Everything");
  const [items, setItems] = useState([]);
  const [cursor, setCursor] = useState(null);
  const params = useMemo(() => queryFor(filter), [filter]);
  const audit = useAudit(params, true);
  const loadMore = useLoadAudit();

  useEffect(() => {
    if (!audit.data) return;
    setItems(audit.data.items || []);
    setCursor(audit.data.cursor || null);
  }, [audit.data]);

  async function more() {
    if (!cursor) return;
    const page = await loadMore.mutateAsync(queryFor(filter, cursor));
    setItems((current) => [...current, ...(page.items || [])]);
    setCursor(page.cursor || null);
  }

  if (audit.isLoading) return <LoadingState title={COPY.loading} />;
  if (audit.isError) return <ErrorState title={COPY.auditError} onRetry={() => audit.refetch()} />;

  return (
    <div className="audit-grid">
      <Panel title={COPY.auditTitle}>
        <div className="audit-filterbar">
          {AUDIT_FILTERS.map((option) => (
            <button
              type="button"
              key={option.key}
              className={option.key === filter ? "active" : ""}
              onClick={() => setFilter(option.key)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <AuditTable items={items} />
        <div className="audit-actions">
          <Button type="button" busy={loadMore.isPending} disabled={!cursor} onClick={more}>
            {COPY.auditLoadMore}
          </Button>
        </div>
      </Panel>
    </div>
  );
}
