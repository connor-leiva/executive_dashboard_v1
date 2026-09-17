import React, { useMemo, useState } from "react";
import { api } from "../api.js";
import { ago } from "../format.js";
import { collapseBy, describe, target } from "../phrases.js";
import { Card, Chip, Empty, Loading, LoadError, Mono, Seg, useApi } from "../primitives.jsx";
import { A, TYPE } from "../tokens.js";

const KIND = { user: "Their team", acumyn: "Acumyn", system: "System" };

/* Machine events collapse: the same action, by the same actor, doing the same thing. */
const collapse = (events) => collapseBy(events, (e) => [e.action, e.actor, e.actor_label, describe(e), target(e)].join("|"));

export default function ActivityPane({ w }) {
  const events = useApi(() => api.tenantAudit(w.slug, 200), [w.slug]);
  const [show, setShow] = useState("all");
  const rows = useMemo(() => collapse(events.data ? events.data.events : []), [events.data]);

  if (events.loading && !events.data) return <Loading label="Reading the audit log" />;
  if (events.error) return <LoadError error={events.error} onRetry={events.reload} />;

  const shown = show === "all" ? rows : rows.filter((r) => r.actor === show);
  return (
    <Card title="Activity" pad={0}
      sub="The workspace's own audit log, newest first. Repeated events collapse to one line with a count."
      right={<Seg label="Filter activity" value={show} onChange={setShow}
        options={[["all", "All"], ["user", "Their team"], ["acumyn", "Acumyn"], ["system", "System"]]} />}>
      {shown.length === 0 ? (
        <Empty title={rows.length ? "Nothing in this bucket" : "Nothing has happened here yet"}>
          {rows.length ? "No events of this kind in the latest 200. Choose another filter."
            : "The workspace exists and nobody has used it."}
        </Empty>
      ) : shown.map((r, i) => {
        const bad = r.action === "tenant.suspended" || /failed|error|locked/.test(r.action);
        return (
          <div key={`${r.at}-${i}`} style={{ display: "flex", borderTop: `1px solid ${A.lineSoft}` }}>
            <span style={{ width: 3, background: bad ? A.stop : "transparent", flexShrink: 0 }} aria-hidden />
            <div style={{ flex: 1, minWidth: 0, padding: "11px 16px" }}>
              <div style={{ display: "flex", gap: 12, justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap" }}>
                <div style={{ minWidth: 0, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                  <Chip state={r.actor === "acumyn" ? "trial" : undefined}>{KIND[r.actor]}</Chip>
                  <span style={{ fontFamily: TYPE.text, fontSize: 12.5, fontWeight: 500, color: bad ? A.stop : A.ink }}>{describe(r)}</span>
                  {r.count > 1 ? <Mono size={10.5} c={A.mute}>×{r.count}</Mono> : null}
                </div>
                <Mono size={10.5} c={A.mute}>{ago(r.at)}</Mono>
              </div>
              <div style={{ fontFamily: TYPE.text, fontSize: 11.5, color: A.mute, marginTop: 3, overflowWrap: "anywhere" }}>
                {r.actor_label || KIND[r.actor]}{target(r) ? <> · <Mono size={11} c={A.body}>{target(r)}</Mono></> : null}
              </div>
            </div>
          </div>
        );
      })}
    </Card>
  );
}
