/* A brokerage's lead-source reports: Cohorts, Team pipeline (and one agent's), What's Working.
   docs/specs/ulrg-reporting-dashboard-port.md, phases 3-5.

   A sub-tab on the brokerage's page rather than a product tab of its own (spec §9.4): the reports
   are about that business's Follow Up Boss leads and Sisu deals, and they sit beside the page's
   lead-to-close panel so the two are visibly different questions -- that panel counts each step in
   the period it happened, these follow a month of arrivals for as long as they take to close.

   THE DATE BAR DRIVES IT. The app's one date control (PeriodNav) picks the leads every view reads,
   as it does on every other page; there is no second range in here. A cohort is a whole month, so
   the cohort view shows every month the range touches. When the range is shorter than the program,
   the meta line offers to widen it to the program's start.

   THE PICKER IS PER VIEWER. Which source somebody was last looking at is remembered in this browser
   (a convenience, wrapped so a private window just starts on the first group). Everything else
   comes from the server: the groups, the range it applied, the numbers. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { T } from "../theme.js";
import { Icon } from "../Brand.jsx";
import { Segmented } from "../panels.jsx";
import AuditDrawer from "../AuditDrawer.jsx";
import SubTabs from "../SubTabs.jsx";
import Cohorts from "./Cohorts.jsx";
import TeamPipeline from "./TeamPipeline.jsx";
import AgentPipeline from "./AgentPipeline.jsx";
import WhatsWorking from "./WhatsWorking.jsx";
import { useReport } from "./useReport.js";
import { FB, FD, FM, OutlineButton, ReportEmpty, ReportError, ReportSkeleton, dayLabel, downloadCsv, fmt, monthLabel } from "./kit.jsx";

const VIEWS = [["cohorts", "Cohorts"], ["pipeline", "Team pipeline"], ["working", "What's working"]];
const SETTINGS = "/settings/lead-sources";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function remembered(key) {
  try { return window.localStorage.getItem(key); } catch { return null; }
}
function remember(key, value) {
  try { window.localStorage.setItem(key, value); } catch { /* private mode: nothing to remember */ }
}

/* "Feb 1 – Nov 11, 2026", "Dec 29, 2025 – Jan 4, 2026": the server's range in the date bar's words. */
function rangeText(w) {
  if (!w || !w.start || !w.end) return "the whole program";
  const p = (iso) => ({ y: iso.slice(0, 4), m: MONTHS[Number(iso.slice(5, 7)) - 1], d: Number(iso.slice(8, 10)) });
  const a = p(w.start), b = p(w.end);
  return a.y === b.y ? `${a.m} ${a.d} – ${b.m} ${b.d}, ${b.y}` : `${a.m} ${a.d}, ${a.y} – ${b.m} ${b.d}, ${b.y}`;
}

/* The range as the end of a sentence: "this month", "in the last 7 days", "for Feb 10 – Feb 12, 2026". */
function rangePhrase(w) {
  const k = w?.key;
  const words = { mtd: "this month", last_month: "last month", next_month: "next month", last_7: "in the last 7 days",
                  qtd: "this quarter", ytd: "this year", year: w?.end ? `in ${w.end.slice(0, 4)}` : "this year" };
  return words[k] || `for ${rangeText(w)}`;
}

/* "Feb–Nov 2026", "Nov 2026", "Dec 2025–Jan 2026": the cohort months a range touched. */
function monthSpan(first, last) {
  if (!first) return "";
  if (first === last) return monthLabel(first);
  const [fy, ly] = [first.slice(0, 4), last.slice(0, 4)];
  return fy === ly ? `${MONTHS[Number(first.slice(5, 7)) - 1]}–${monthLabel(last)}` : `${monthLabel(first)}–${monthLabel(last)}`;
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

/* The lead source is picked from the title: a native select dressed as the heading, so the report
   keeps one row of toggles and the picker still works from a keyboard and a screen reader. */
function SourceTitle({ list, group, onPick }) {
  const title = { fontFamily: FD, fontSize: 20, fontWeight: 600, color: T.ink };
  if (list.length < 2) return <span style={title}>{group.display_name}</span>;
  return (
    <span style={{ position: "relative", display: "inline-flex", alignItems: "center" }}>
      <select aria-label="Lead source" value={group.key} onChange={(e) => onPick(e.target.value)} className="ls-title-select"
        style={{ ...title, appearance: "none", WebkitAppearance: "none", MozAppearance: "none", background: "transparent",
          border: "none", borderRadius: 6, padding: "0 26px 0 0", margin: 0, cursor: "pointer", maxWidth: "100%" }}>
        {list.map((g) => <option key={g.key} value={g.key}>{g.display_name}</option>)}
      </select>
      <span style={{ position: "absolute", right: 4, pointerEvents: "none", display: "inline-flex" }}>
        <Icon name="chevron_down" size={14} color={T.muted} />
      </span>
    </span>
  );
}

export default function LeadSources({ businessKey, businessName, period = "mtd", onPeriod }) {
  const storeKey = `axcion.leadSource.${businessKey}`;
  const groups = useReport(`/businesses/${businessKey}/lead-sources`);
  const list = groups.data?.groups || [];
  const [source, setSource] = useState(() => remembered(storeKey));
  const [view, setView] = useState("cohorts");
  const [agent, setAgent] = useState(null);
  const [drawer, setDrawer] = useState(null);

  const group = list.find((g) => g.key === source) || list[0];
  useEffect(() => { if (group && group.key !== source) setSource(group.key); }, [group?.key]); // eslint-disable-line react-hooks/exhaustive-deps
  const pick = (k) => { setSource(k); remember(storeKey, k); setAgent(null); };

  const base = group ? `/businesses/${businessKey}/lead-sources/${group.key}` : null;
  const q = `period=${encodeURIComponent(period)}`;
  const path = !base ? null
    : view === "cohorts" ? `${base}/cohorts?${q}`
      : view === "pipeline" && agent ? `${base}/pipeline/agents/${agent}?${q}`
        : view === "pipeline" ? `${base}/pipeline?${q}`
          : `${base}/whats-working?${q}`;
  const report = useReport(path);

  if (!groups.live) {
    return (
      <ReportEmpty title="Lead-source reports run on the live app">
        They read your own Follow Up Boss leads, Sisu deals and QuickBooks spend, so there is nothing to show without them.
      </ReportEmpty>
    );
  }
  if (groups.error) return <ReportError onRetry={groups.retry} error={groups.error} />;
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
  const inAgent = view === "pipeline" && agent;
  const d = report.data && matches(report.data, view, agent) ? report.data : null;
  const prog = d?.program;
  // The program's whole range, for "Show since Feb 2026". Offered whenever the bar shows less of it.
  const widen = prog?.since && onPeriod ? `c:${prog.since}:${prog.until}` : null;
  // Only worth offering when the program has leads to show.
  const narrower = Boolean(widen && prog.leads > 0 && d?.window?.start && d.window.start > prog.since && period !== widen);
  const showSince = widen
    ? <OutlineButton onClick={() => onPeriod(widen)}>Show since {monthLabel(prog.since.slice(0, 7))}</OutlineButton> : null;

  // ONE empty state for every view. No leads at all is a setup question (Settings); none in this
  // range is a date-bar question, and the button that answers it widens the range.
  const name = group.display_name;
  const noLeadsAtAll = (
    <ReportEmpty title={`No ${name} leads yet`}
      action={<Link to={settingsHref} style={{ fontFamily: FB, fontSize: 13, fontWeight: 600, color: T.teal, textDecoration: "none" }}>Check its source variants in Settings ↗</Link>}>
      {d?.group?.matches_nothing || !group.variants.length
        ? `${name} has no source variants, so no Follow Up Boss lead matches it.`
        : `No Follow Up Boss lead on ${businessName} has a source that matches ${name}${prog?.since ? ` since ${monthLabel(prog.since.slice(0, 7))}` : ""}.`}
    </ReportEmpty>
  );
  const noneInRange = (
    <ReportEmpty title={`No ${name} leads ${rangePhrase(d?.window || report.data?.window)}`}
      action={showSince}>
      Its leads arrived outside this date range. Widen the range in the date bar to see them.
    </ReportEmpty>
  );

  let content, outOfRange = false;
  if (report.error) {
    content = report.error.status === 404 && inAgent
      ? <ReportEmpty title="No leads for this agent in this date range"
          action={<OutlineButton onClick={() => setAgent(null)}>Back to the team</OutlineButton>}>
          The agent's leads arrived outside the range in the date bar.
        </ReportEmpty>
      : <ReportError onRetry={report.retry} error={report.error} />;
  } else if (!d) {
    content = <ReportSkeleton />;
  } else if (prog && prog.leads === 0 && !(view === "cohorts" && d.cohort_rows.length)) {
    content = noLeadsAtAll;
  } else if (view === "cohorts") {
    outOfRange = d.cohort_rows.length === 0;
    content = outOfRange ? noneInRange
      : <Cohorts data={d} groupName={name} settingsHref={settingsHref} openDrawer={setDrawer} />;
  } else if (inAgent) {
    content = <AgentPipeline data={d} onBack={() => setAgent(null)} />;
  } else if (view === "pipeline") {
    outOfRange = d.summary.leads === 0;
    content = outOfRange ? noneInRange : <TeamPipeline data={d} onAgent={setAgent} />;
  } else {
    outOfRange = d.agents.length === 0;
    content = outOfRange ? noneInRange : <WhatsWorking data={d} />;
  }

  const variants = group.variants.length;
  const cohortKeys = view === "cohorts" && d ? d.cohort_rows.map((r) => r.cohort_key) : [];
  const scope = !d ? null
    : view === "cohorts"
      ? (cohortKeys.length ? `cohorts ${monthSpan(cohortKeys[0], cohortKeys[cohortKeys.length - 1])}` : null)
      : `leads ${rangeText(d.window)}`;
  const asOf = report.data?.as_of
    ? `as of ${new Date(report.data.as_of).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`
    : null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, flexWrap: "wrap" }}>
        <div style={{ minWidth: 0, flex: "1 1 360px" }}>
          <SourceTitle list={list} group={group} onPick={pick} />
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginTop: 4 }}>
            <span style={{ fontFamily: FB, fontSize: 13, color: T.muted }}>
              {[businessName, `${variants} source variant${variants === 1 ? "" : "s"}`,
                scope ? `${scope}, from the date range above` : null].filter(Boolean).join(" · ")}
              {asOf && <span style={{ fontFamily: FM, fontSize: 12 }}> · {asOf}</span>}
            </span>
            {narrower && !outOfRange && showSince}
          </div>
        </div>
        <Segmented options={VIEWS} value={view} onChange={(v) => { setView(v); setAgent(null); }} label="Report" />
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
export function BrokerageTabs({ overview, businessKey, businessName, sub, onSub, period, onPeriod }) {
  const [own, setOwn] = useState("overview");
  const active = sub ?? own;
  const set = onSub ?? setOwn;
  return (
    <div>
      <SubTabs tabs={[{ k: "overview", label: "Overview" }, { k: "lead_sources", label: "Lead sources" }]}
        active={active} onChange={set} />
      {active === "lead_sources"
        ? <LeadSources businessKey={businessKey} businessName={businessName} period={period} onPeriod={onPeriod} />
        : overview}
    </div>
  );
}
