/* A brokerage's lead-source reports: Cohorts, Team pipeline (and one agent's), What's Working.
   docs/specs/ulrg-reporting-dashboard-port.md, phases 3-5.

   A sub-tab on the brokerage's page rather than a product tab of its own (spec §9.4): the reports
   are about that business's Follow Up Boss leads and Sisu deals, and they sit beside the page's
   lead-to-close panel so the two are visibly different questions -- that panel counts each step in
   the period it happened, these follow a month of arrivals for as long as they take to close.

   THE PICKER IS PER VIEWER. Which source somebody was last looking at is remembered in this browser
   (a convenience, wrapped so a private window just starts on the first group). Everything else
   comes from the server: the groups, the months that exist, the windows, the numbers. */
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { T } from "../theme.js";
import { Segmented } from "../panels.jsx";
import AuditDrawer from "../AuditDrawer.jsx";
import SubTabs from "../SubTabs.jsx";
import Cohorts from "./Cohorts.jsx";
import TeamPipeline from "./TeamPipeline.jsx";
import AgentPipeline from "./AgentPipeline.jsx";
import WhatsWorking from "./WhatsWorking.jsx";
import { useReport } from "./useReport.js";
import { FB, FD, FM, ReportEmpty, ReportError, ReportSkeleton, dayLabel, downloadCsv, fmt, monthLabel } from "./kit.jsx";

const VIEWS = [["cohorts", "Cohorts"], ["pipeline", "Team pipeline"], ["working", "What's working"]];
const SETTINGS = "/settings/lead-sources";

function remembered(key) {
  try { return window.localStorage.getItem(key); } catch { return null; }
}
function remember(key, value) {
  try { window.localStorage.setItem(key, value); } catch { /* private mode: nothing to remember */ }
}

const select = { fontFamily: FB, fontSize: 13, color: T.ink, background: T.white, border: `1px solid ${T.line}`,
                 borderRadius: 8, padding: "6px 10px" };

function MonthRange({ months, start, end, onChange }) {
  if (!months.length) return null;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      <select aria-label="First cohort" style={select} value={start || months[0]}
        onChange={(e) => onChange(e.target.value, end && end < e.target.value ? e.target.value : end)}>
        {months.map((m) => <option key={m} value={m}>{monthLabel(m)}</option>)}
      </select>
      <span style={{ fontFamily: FB, fontSize: 13, color: T.muted }}>to</span>
      <select aria-label="Last cohort" style={select} value={end || months[months.length - 1]}
        onChange={(e) => onChange(start && start > e.target.value ? e.target.value : start, e.target.value)}>
        {months.map((m) => <option key={m} value={m}>{monthLabel(m)}</option>)}
      </select>
      {(start || end) && (
        <button type="button" onClick={() => onChange(null, null)} style={{ fontFamily: FB, fontSize: 12, fontWeight: 600, color: T.teal,
          background: "none", border: "none", cursor: "pointer", padding: 0 }}>All months</button>
      )}
    </span>
  );
}

const LEAD_CSV = [
  { key: "name", label: "Lead" }, { key: "created", label: "Arrived" }, { key: "agent", label: "Agent" },
  { key: "stage", label: "Follow Up Boss stage" }, { key: "source", label: "Source" },
  { key: "minutes_to_first_touch", label: "Minutes to first touch" }, { key: "sisu_matched", label: "Matched in Sisu" },
  { key: "sisu_state", label: "Sisu state" }, { key: "fub_person_id", label: "Follow Up Boss id" },
];

/* The per-lead drawer for one cohort month: fetched on open, then the same AuditDrawer. */
function CohortLeadsDrawer({ base, month, label, onClose }) {
  const { data, error } = useReport(`${base}/cohorts/leads?month=${month}`);
  const preset = data ? {
    label, source: "Follow Up Boss", count: data.count,
    computed_as: "Every lead that arrived this month from this source, newest first, with how long its first touch took.",
    rows: data.rows.map((r) => ({
      id: r.fub_person_id, name: r.name || "Unnamed lead", seg: undefined,
      l2: [dayLabel(r.created), r.agent, r.stage].filter(Boolean).join(" · "),
      r1: r.minutes_to_first_touch == null ? "never" : fmt.minutes(r.minutes_to_first_touch),
      r2: r.sisu_state ? r.sisu_state.replace("_", " ") : r.sisu_matched ? "in Sisu" : "not in Sisu",
      tone: r.minutes_to_first_touch == null ? "watch" : undefined, source_url: r.fub_url,
    })),
  } : error ? { label, source: "Follow Up Boss", computed_as: "Couldn't load these leads.", rows: [] }
    : { label, source: "Follow Up Boss", computed_as: "Loading…", rows: [] };
  return <AuditDrawer preset={preset} onClose={onClose}
    onExport={data ? () => downloadCsv(`leads-${month}.csv`, LEAD_CSV, data.rows) : undefined} />;
}

export default function LeadSources({ businessKey, businessName }) {
  const storeKey = `axcion.leadSource.${businessKey}`;
  const groups = useReport(`/businesses/${businessKey}/lead-sources`);
  const list = groups.data?.groups || [];
  const [source, setSource] = useState(() => remembered(storeKey));
  const [view, setView] = useState("cohorts");
  const [range, setRange] = useState({ start: null, end: null });
  const [win, setWin] = useState("all_time");
  const [agent, setAgent] = useState(null);
  const [drawer, setDrawer] = useState(null);

  const group = list.find((g) => g.key === source) || list[0];
  useEffect(() => { if (group && group.key !== source) setSource(group.key); }, [group?.key]); // eslint-disable-line react-hooks/exhaustive-deps
  const pick = (k) => { setSource(k); remember(storeKey, k); setRange({ start: null, end: null }); setAgent(null); };

  const base = group ? `/businesses/${businessKey}/lead-sources/${group.key}` : null;
  const q = new URLSearchParams();
  if (range.start) q.set("start", range.start);
  if (range.end) q.set("end", range.end);
  const path = !base ? null
    : view === "cohorts" ? `${base}/cohorts${q.toString() ? `?${q}` : ""}`
      : view === "pipeline" && agent ? `${base}/pipeline/agents/${agent}?window=${win}`
        : view === "pipeline" ? `${base}/pipeline?window=${win}`
          : `${base}/whats-working?window=${win}`;
  const report = useReport(path);
  const allMonths = useMemo(() => report.data?.available_months || [], [report.data]);

  if (!groups.live) {
    return (
      <ReportEmpty title="Lead-source reports run on the live app">
        They read your own Follow Up Boss leads, Sisu deals and QuickBooks spend, so there is nothing to show without them.
      </ReportEmpty>
    );
  }
  if (groups.error) return <ReportError onRetry={groups.retry} />;
  if (groups.loading && !groups.data) return <ReportSkeleton />;
  if (!list.length) {
    return (
      <ReportEmpty title="No lead sources yet"
        action={groups.data?.can_edit
          ? <Link to={SETTINGS} style={{ fontFamily: FB, fontSize: 13, fontWeight: 600, color: T.teal, textDecoration: "none" }}>Set one up in Settings ↗</Link>
          : null}>
        A lead source names the Follow Up Boss source strings that are one channel, such as Realtor.com, and where its spend
        lives in QuickBooks. {groups.data?.can_edit ? "" : "An owner or admin sets them up."}
      </ReportEmpty>
    );
  }

  const settingsHref = `${SETTINGS}?business=${businessKey}&group=${group.key}`;
  const windows = report.data?.windows || [{ key: "all_time", label: "All time" }];
  const inAgent = view === "pipeline" && agent;

  let content;
  if (report.error) {
    content = report.error.status === 404 && inAgent
      ? <ReportEmpty title="No leads for this agent in the window">Choose a longer window, or go back to the team.</ReportEmpty>
      : <ReportError onRetry={report.retry} />;
  } else if (!report.data || !matches(report.data, view, agent)) {
    content = <ReportSkeleton />;
  } else if (view === "cohorts") {
    content = <Cohorts data={report.data} groupName={group.display_name} settingsHref={settingsHref} openDrawer={setDrawer} />;
  } else if (inAgent) {
    content = <AgentPipeline data={report.data} onBack={() => setAgent(null)} />;
  } else if (view === "pipeline") {
    content = report.data.summary.leads === 0
      ? <ReportEmpty title={`No ${group.display_name} leads in this window`}>Choose a longer window to see the pipeline.</ReportEmpty>
      : <TeamPipeline data={report.data} onAgent={setAgent} />;
  } else {
    content = <WhatsWorking data={report.data} />;
  }

  const variants = group.variants.length;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, flexWrap: "wrap" }}>
        <div style={{ minWidth: 0, flex: "1 1 320px" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
            <span style={{ fontFamily: FD, fontSize: 20, fontWeight: 600, color: T.ink }}>{group.display_name}</span>
            <span style={{ fontFamily: FB, fontSize: 13, color: T.muted }}>
              {businessName} · {variants} source variant{variants === 1 ? "" : "s"}
              {group.program_start ? ` · since ${monthLabel(group.program_start.slice(0, 7))}` : ""}
            </span>
          </div>
          {report.data?.as_of && (
            <div style={{ fontFamily: FM, fontSize: 12, color: T.muted, marginTop: 4 }}>
              as of {new Date(report.data.as_of).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}
            </div>
          )}
        </div>
        {list.length > 1 && (
          <Segmented options={list.map((g) => [g.key, g.display_name])} value={group.key} onChange={pick} tone={T.ink} label="Lead source" />
        )}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", paddingBottom: 4 }}>
        <Segmented options={VIEWS} value={view} onChange={(v) => { setView(v); setAgent(null); }} label="Report" />
        <span style={{ flex: 1 }} />
        {view === "cohorts"
          ? <MonthRange months={allMonths.length ? allMonths : []} start={range.start} end={range.end}
              onChange={(start, end) => setRange({ start, end })} />
          : <Segmented options={windows.map((w) => [w.key, w.label])} value={win} onChange={setWin} label="Window" />}
      </div>

      {content}

      {drawer?.preset && <AuditDrawer preset={drawer.preset} onExport={drawer.onExport} onClose={() => setDrawer(null)} />}
      {drawer?.leadsMonth && <CohortLeadsDrawer base={base} month={drawer.leadsMonth} label={drawer.label} onClose={() => setDrawer(null)} />}
    </div>
  );
}

/* Whether a payload on screen belongs to the view now selected -- a previous view's data must not
   render under the next view's title while the next one loads. */
function matches(d, view, agent) {
  if (view === "cohorts") return Boolean(d.cohort_rows);
  if (view === "pipeline") return agent ? Boolean(d.agent) : Boolean(d.team_funnel);
  return Boolean(d.metric_definitions);
}

/* Any OTHER brokerage's page (a business of kind real_estate the tenant named anything at all) gets
   the same two sub-tabs: its overview, and its lead sources. ULRG's page has these inside UlrgTabs,
   beside its Scorecard and Recruiting. */
export function BrokerageTabs({ overview, businessKey, businessName, sub, onSub }) {
  const [own, setOwn] = useState("overview");
  const active = sub ?? own;
  const set = onSub ?? setOwn;
  return (
    <div>
      <SubTabs tabs={[{ k: "overview", label: "Overview" }, { k: "lead_sources", label: "Lead sources" }]}
        active={active} onChange={set} />
      {active === "lead_sources" ? <LeadSources businessKey={businessKey} businessName={businessName} /> : overview}
    </div>
  );
}
