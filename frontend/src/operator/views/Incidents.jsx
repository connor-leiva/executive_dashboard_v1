import React from "react";
import { api } from "../api.js";
import { ago, plural } from "../format.js";
import { Btn, Card, Chip, Empty, Loading, LoadError, Mono, Stat, useApi } from "../primitives.jsx";
import { A, STATE, TYPE } from "../tokens.js";

export default function IncidentsView({ onOpen }) {
  const data = useApi(() => api.incidents(), []);
  if (data.loading && !data.data) return <Loading label="Grouping errors by cause" />;
  if (data.error) return <LoadError error={data.error} onRetry={data.reload} />;

  const { incidents, window_days: windowDays } = data.data;
  const workspaces = new Set(incidents.flatMap((i) => i.workspaces.map((w) => w.slug)));
  const oldest = incidents.map((i) => i.first_seen).filter(Boolean).sort()[0];
  const shared = incidents.filter((i) => i.workspaces.length > 1).length;

  return (
    <>
      <div className="ac-tiles ac-t4" style={{ marginBottom: 16 }}>
        <Stat label="Open incidents" value={incidents.length} state={incidents.length ? "broken" : "healthy"}
          note={incidents.length ? "each one a cause, not an occurrence" : "no connection is reporting an error"} />
        <Stat label="Workspaces affected" value={workspaces.size}
          note={shared ? `${plural(shared, "incident")} spans more than one` : "no incident spans more than one"} />
        <Stat label="Oldest failure" value={oldest ? ago(oldest).replace(" ago", "") : "—"}
          note={oldest ? `first failed run inside the ${windowDays}-day window` : "no failed runs recorded"} />
        <Stat label="Failed runs" value={incidents.reduce((a, i) => a + i.hits, 0)}
          note={`in ${windowDays} days, belonging to these incidents`} />
      </div>

      <Card title="Open incidents" pad={0}
        sub="Grouped by cause. Nine failed runs from one expired credential are one incident, and clearing the credential clears all nine.">
        {incidents.length === 0 ? (
          <Empty title="No open incidents">Every connected source's last sync succeeded, or the workspace has nothing connected.</Empty>
        ) : incidents.map((inc) => {
          const s = STATE[inc.severity];
          return (
            <div key={inc.key} style={{ display: "flex", borderTop: `1px solid ${A.lineSoft}` }}>
              <span style={{ width: 3, background: s.c, flexShrink: 0 }} aria-hidden />
              <div style={{ flex: 1, minWidth: 0, padding: "14px 16px" }}>
                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <span style={{ fontFamily: TYPE.text, fontSize: 13.5, fontWeight: 600, color: A.ink }}>{inc.title}</span>
                  <Chip state={inc.severity}>{plural(inc.hits, "failed run")}</Chip>
                  <Mono size={10.5} c={A.mute}>{inc.provider_name}</Mono>
                </div>
                <Mono size={11} c={A.stop} style={{ display: "block", margin: "8px 0", background: A.stopBg, borderRadius: 6, padding: "6px 9px", wordBreak: "break-word" }}>
                  {inc.error}
                </Mono>
                <div style={{ fontFamily: TYPE.text, fontSize: 12, color: A.body, lineHeight: 1.6, textWrap: "pretty" }}>
                  <b style={{ fontWeight: 600 }}>Why:</b> {inc.cause || "No known cause matches this message yet. The provider's own text is above."}
                </div>
                <div style={{ fontFamily: TYPE.text, fontSize: 12, color: A.body, lineHeight: 1.6, marginTop: 4, textWrap: "pretty" }}>
                  <b style={{ fontWeight: 600 }}>What clears it:</b> {inc.fix || "A successful sync of each affected connection."}
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 9, flexWrap: "wrap" }}>
                  <Mono size={10.5} c={A.mute}>
                    {inc.first_seen ? `first ${ago(inc.first_seen)} · latest ${ago(inc.last_seen)}` : "no failed runs in the window"}
                  </Mono>
                  {inc.workspaces.map((w) => (
                    <button key={w.slug} type="button" onClick={() => onOpen(w.slug, "sources")} className="ac-tag" title={w.name} style={{
                      fontFamily: TYPE.data, fontSize: 10.5, color: A.body, background: A.chip, border: "none",
                      borderRadius: 4, padding: "2px 6px", cursor: "pointer", transition: "background .12s ease, color .12s ease",
                    }}>{w.slug}</button>
                  ))}
                </div>
              </div>
            </div>
          );
        })}
      </Card>
      <div style={{ marginTop: 12 }}><Btn small kind="quiet" onClick={data.reload} busy={data.loading}>Refresh</Btn></div>
    </>
  );
}
