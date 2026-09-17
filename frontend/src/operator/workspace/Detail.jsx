import React from "react";
import { api } from "../api.js";
import { healthOf } from "../health.js";
import { Btn, Chip, Loading, LoadError, Mono, TabBar, useApi } from "../primitives.jsx";
import { loadReference, planName } from "../reference.js";
import { A, TYPE } from "../tokens.js";
import AccessPane from "./Access.jsx";
import ActivityPane from "./Activity.jsx";
import DangerPane from "./Danger.jsx";
import ModulesPane from "./Modules.jsx";
import OverviewPane from "./Overview.jsx";
import PeoplePane from "./People.jsx";
import SourcesPane from "./Sources.jsx";
import UsagePane from "./Usage.jsx";

/* Panes, in tab order. Each receives the workspace row (with owners), the plan reference, a reload
   for after its own actions, and `open` to move to another pane. */
const PANES = [
  { key: "overview", label: "Overview", Pane: OverviewPane },
  { key: "people", label: "People", Pane: PeoplePane },
  { key: "sources", label: "Sources", Pane: SourcesPane },
  { key: "modules", label: "Modules", Pane: ModulesPane },
  { key: "usage", label: "Usage", Pane: UsagePane },
  { key: "activity", label: "Activity", Pane: ActivityPane },
  { key: "access", label: "Access", Pane: AccessPane },
  { key: "danger", label: "Danger", Pane: DangerPane },
];

export default function WorkspaceDetail({ slug, tab, onTab, onBack, onOpen }) {
  const row = useApi(() => api.tenant(slug), [slug]);
  const reference = useApi(loadReference, []);
  const active = PANES.some((p) => p.key === tab) ? tab : "overview";
  const { Pane } = PANES.find((p) => p.key === active);

  const back = (
    <button type="button" onClick={onBack} className="ac-link" style={{
      background: "none", border: "none", padding: "0 0 12px", cursor: "pointer",
      fontFamily: TYPE.text, fontSize: 12.5, color: A.mute,
    }}>← All workspaces</button>
  );

  if (row.loading && !row.data) return <>{back}<Loading label={`Reading ${slug}`} /></>;
  if (row.error) {
    return (
      <>
        {back}
        <LoadError error={row.error.status === 404 ? new Error(`There is no workspace called "${slug}".`) : row.error}
          onRetry={row.error.status === 404 ? null : row.reload} />
      </>
    );
  }

  const w = row.data;
  const h = healthOf(w);
  return (
    <>
      {back}
      <div style={{
        background: A.ink, borderRadius: 10, padding: "16px 18px", marginBottom: 16,
        display: "flex", gap: 16, alignItems: "flex-start", justifyContent: "space-between", flexWrap: "wrap",
      }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <h1 style={{ margin: 0, fontFamily: TYPE.display, fontSize: 21, fontWeight: 700, color: A.onInk, letterSpacing: "-.02em" }}>{w.name}</h1>
            <Chip state={h.state} />
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6, flexWrap: "wrap" }}>
            <Mono size={11.5} c={A.onInkMute}>{w.slug}</Mono>
            {w.hosts[0] ? <><span style={{ color: A.onInkMute }} aria-hidden>·</span><Mono size={11.5} c={A.onInkMute}>{w.hosts[0]}</Mono></> : null}
            <span style={{ color: A.onInkMute }} aria-hidden>·</span>
            <span style={{ fontFamily: TYPE.text, fontSize: 11.5, color: A.onInkMute }}>
              {planName(reference.data, w.plan)}{w.plan_set ? "" : " (no plan set, defaulted)"}
            </span>
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {w.hosts[0] ? (
            <a href={`${/(^|\.)localhost$/.test(w.hosts[0]) ? "http" : "https"}://${w.hosts[0]}`} target="_blank" rel="noopener noreferrer" className="ac-btn ac-b-onGhost" style={{
              fontFamily: TYPE.text, fontSize: 11, fontWeight: 600, lineHeight: 1.35, borderRadius: 7, padding: "5px 9px",
              color: A.onInk, border: "1px solid rgba(239,245,243,.3)", textDecoration: "none", whiteSpace: "nowrap",
            }}>Open their sign-in page ↗</a>
          ) : null}
          <Btn small kind="onGhost" onClick={row.reload} busy={row.loading}>Refresh</Btn>
        </div>
      </div>

      <TabBar tabs={PANES} active={active} onPick={onTab} />
      <Pane w={w} reference={reference.data} reload={row.reload} open={(pane) => onOpen(w.slug, pane)} />
    </>
  );
}
