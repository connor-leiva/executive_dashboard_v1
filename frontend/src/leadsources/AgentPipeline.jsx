/* Lead sources › Team pipeline › one agent (docs/specs/ulrg-reporting-dashboard-port.md §2.6, §4.4).

   The agent's own funnel against the benchmark, how fast they reach a new lead against the team,
   and the stuck list -- the leads to call today, each one link away from Follow Up Boss. Choosing a
   stage in the funnel filters the stuck list to it. */
import { useEffect, useState } from "react";
import { T, alpha } from "../theme.js";
import { Card, PanelLabel, Segmented, Source, StatTile } from "../panels.jsx";
import { Pill } from "../books/ui.jsx";
import { CardHead, ExportButton, FB, FD, TIER_TONE, dayLabel, downloadCsv, fmt, small, td, th } from "./kit.jsx";

const SEV = { crit: T.poppyText, warn: T.daffodilText, mild: T.slate, none: T.meadowInk };
const AGE_TINT = { hot: T.meadowBg, cool: T.parchment, warm: T.daffodilBg, cold: alpha(T.poppy, 0.14) };

function VerticalFunnel({ stages, selected, onSelect }) {
  const top = stages[0].count || 0;
  return (
    <Card style={{ flex: "1 1 420px", minWidth: 300 }}>
      <CardHead right={<Source name="Follow Up Boss · Sisu" />}><PanelLabel accent={T.meadow}>Funnel</PanelLabel></CardHead>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {stages.map((st, i) => {
          const filterKey = i < stages.length - 1 ? `stuck_at_${st.stage_key}` : null;
          const on = selected === filterKey;
          const Tag = filterKey ? "button" : "div";
          return (
            <Tag key={st.stage_key} type={filterKey ? "button" : undefined} onClick={filterKey ? () => onSelect(on ? "all_active" : filterKey) : undefined}
              aria-pressed={filterKey ? on : undefined} className={filterKey ? "cc-card" : undefined}
              style={{ display: "grid", gridTemplateColumns: "minmax(0,150px) minmax(0,1fr) 44px", gap: 12, alignItems: "center",
                width: "100%", textAlign: "left", font: "inherit", border: "none", borderRadius: 8, padding: "6px 8px", margin: "0 -8px",
                background: on ? T.parchment : "transparent", cursor: filterKey ? "pointer" : "default" }}>
              <span style={{ minWidth: 0 }}>
                <span style={{ display: "block", fontFamily: FB, fontSize: 13, color: T.ink, fontWeight: on ? 600 : 500 }}>{st.stage_label}</span>
                <span style={{ display: "block", fontFamily: FB, fontSize: 12, color: SEV[st.severity] || T.muted }}>
                  {i === 0 ? "the baseline" : `${fmt.pct(st.conv_pct_from_prev)} · ${fmt.pp(st.vs_benchmark_pp)} vs ${st.benchmark_median_pct}%`}
                </span>
              </span>
              <span style={{ height: 16, background: T.parchment, borderRadius: 5, overflow: "hidden", display: "block" }}>
                <span style={{ display: "block", width: `${top ? (st.count / top) * 100 : 0}%`, height: "100%", borderRadius: 5,
                  background: st.is_bottleneck ? T.poppy : alpha(T.evergreen, 0.3 + 0.55 * (top ? st.count / top : 0)) }} />
              </span>
              <span style={{ fontFamily: FD, fontSize: 13, fontWeight: 600, color: T.ink, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                {st.count}{st.stuck_count ? <span style={{ display: "block", fontFamily: FB, fontSize: 12, fontWeight: 400, color: T.daffodilText }}>{st.stuck_count} stuck</span> : null}
              </span>
            </Tag>
          );
        })}
      </div>
      <div style={{ ...small, marginTop: 12 }}>Select a stage to list the leads stuck there. The bar in the accent is the agent's biggest gap to benchmark.</div>
    </Card>
  );
}

function SpeedCard({ speed }) {
  const max = Math.max(...speed.buckets.map((b) => b.count), 1);
  return (
    <Card style={{ flex: "1 1 300px", minWidth: 280 }}>
      <CardHead right={<Source name="Follow Up Boss" />}><PanelLabel accent={T.meadow}>Speed to first contact</PanelLabel></CardHead>
      <div style={{ display: "flex", gap: 24, marginBottom: 16, flexWrap: "wrap" }}>
        <div>
          <div style={{ fontFamily: "var(--font-data)", fontSize: 28, fontWeight: 500, color: T.ink, lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>{fmt.minutes(speed.median_min)}</div>
          <div style={{ ...small, marginTop: 6 }}>median · team {fmt.minutes(speed.team_median_min)}</div>
        </div>
        <div>
          <div style={{ fontFamily: "var(--font-data)", fontSize: 28, fontWeight: 500, color: T.ink, lineHeight: 1, fontVariantNumeric: "tabular-nums" }}>{speed.pct_under_5min}%</div>
          <div style={{ ...small, marginTop: 6 }}>under 5 min · team {speed.team_pct_under_5min}%</div>
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {speed.buckets.map((b) => (
          <div key={b.key} style={{ display: "grid", gridTemplateColumns: "84px minmax(0,1fr) 28px", gap: 12, alignItems: "center" }}>
            <span style={{ fontFamily: FB, fontSize: 12, color: T.slate, textAlign: "right" }}>{b.label}</span>
            <span style={{ height: 14, background: T.parchment, borderRadius: 5, overflow: "hidden", display: "block" }}>
              <span style={{ display: "block", width: `${(b.count / max) * 100}%`, height: "100%", background: T.evergreen, borderRadius: 5 }} />
            </span>
            <span style={{ fontFamily: FD, fontSize: 13, fontWeight: 600, color: T.ink, textAlign: "right" }}>{b.count}</span>
          </div>
        ))}
      </div>
      <div style={{ ...small, marginTop: 12 }}>{speed.n} lead{speed.n === 1 ? "" : "s"} with a first touch.</div>
    </Card>
  );
}

function TrendCard({ trend }) {
  const max = Math.max(...trend.weekly_leads, 1);
  return (
    <Card style={{ flex: "1 1 300px", minWidth: 280 }}>
      <CardHead right={<Source name="Sisu" />}><PanelLabel accent={T.meadow}>Last 12 weeks</PanelLabel></CardHead>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height: 72 }} role="img"
           aria-label={`Leads per week: ${trend.weekly_leads.join(", ")}`}>
        {trend.weekly_leads.map((n, i) => (
          <span key={i} title={`${n} lead${n === 1 ? "" : "s"} · ${trend.sparkline_points[i]}% closed`}
            style={{ flex: 1, height: `${Math.max(3, (n / max) * 72)}px`, borderRadius: "3px 3px 0 0",
              background: trend.sparkline_points[i] > 0 ? T.meadow : alpha(T.evergreen, 0.28) }} />
        ))}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", ...small, marginTop: 6 }}><span>12 weeks ago</span><span>this week</span></div>
      <div style={{ fontFamily: FB, fontSize: 13, color: T.slate, marginTop: 12, lineHeight: 1.5 }}>
        Leads per week by arrival; green weeks already have a close. Predicted close rate {fmt.pct(trend.predicted_pct)}.
      </div>
    </Card>
  );
}

const TOUCH = { live: T.evergreen, try: "transparent", none: "transparent" };

function Touches({ list }) {
  const cells = [...list, ...Array(Math.max(0, 5 - list.length)).fill(null)].slice(0, 5);
  return (
    <span style={{ display: "inline-flex", gap: 4 }} aria-label={`${list.length} recent touches`}>
      {cells.map((t, i) => (
        <span key={i} title={t ? `${t.label} · ${t.kind === "live" ? "connected" : "attempt"}` : "no touch"}
          style={{ width: 9, height: 9, borderRadius: 99, boxSizing: "border-box", background: t ? TOUCH[t.kind] : "transparent",
            border: `1.5px ${t ? "solid" : "dashed"} ${t ? T.evergreen : T.line}` }} />
      ))}
    </span>
  );
}

const STUCK_CSV = [
  { key: "name", label: "Lead" }, { key: "stage_label", label: "Stage" }, { key: "fub_stage", label: "Follow Up Boss stage" },
  { key: "routed", label: "Arrived" }, { key: "age_days", label: "Days since arrival" }, { key: "last_touch", label: "Last touch" },
  { key: "person_id", label: "Follow Up Boss id" }, { key: "fub_url", label: "Link" },
];

function StuckList({ filters, active, onChange, note, filename }) {
  const f = filters[active] || filters.all_active;
  const options = Object.entries(filters).map(([k, v]) => [k, `${v.label} ${v.count}`]);
  return (
    <Card style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ padding: "20px 20px 0" }}>
        <CardHead right={<ExportButton onClick={() => downloadCsv(filename, STUCK_CSV, f.leads)} disabled={!f.leads.length} />}>
          <PanelLabel accent={T.daffodil}>Stuck leads</PanelLabel>
        </CardHead>
        <Segmented options={options} value={active} onChange={onChange} tone={T.ink} label="Stuck at" />
      </div>
      <div style={{ overflowX: "auto", marginTop: 12 }}>
        <table style={{ width: "100%", minWidth: 720, borderCollapse: "collapse" }}>
          <thead><tr>{["Lead", "Stage", "Arrived", "Days", "Last touch", "Last five touches", ""].map((h, i) => (
            <th key={i} style={{ ...th, textAlign: i < 2 || i === 5 ? "left" : "right", paddingLeft: i === 0 ? 20 : 8 }}>{h}</th>))}</tr></thead>
          <tbody>
            {f.leads.map((l) => (
              <tr key={l.person_id}>
                <td style={{ ...td, textAlign: "left", paddingLeft: 20, fontWeight: 600, maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis" }}>{l.name}</td>
                <td style={{ ...td, textAlign: "left", color: T.slate }}>{l.stage_label}{l.fub_stage ? <span style={{ color: T.muted }}> · {l.fub_stage}</span> : null}</td>
                <td style={{ ...td, color: T.slate }}>{dayLabel(l.routed)}</td>
                <td style={td}><span style={{ background: AGE_TINT[l.age_severity], borderRadius: 6, padding: "1px 6px" }}>{l.age_days}</span></td>
                <td style={{ ...td, color: T.slate }}>{l.last_touch ? `${dayLabel(l.last_touch)} · ${l.days_since_touch}d` : "never"}</td>
                <td style={{ ...td, textAlign: "left" }}><Touches list={l.last_5_touches} /></td>
                <td style={{ ...td, paddingRight: 20 }}>{l.fub_url
                  ? <a href={l.fub_url} target="_blank" rel="noreferrer" title="Open in Follow Up Boss" style={{ color: T.teal, textDecoration: "none", fontWeight: 600 }}>↗</a> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {f.leads.length === 0 && <div style={{ ...small, padding: "16px 20px" }}>Nothing stuck here.</div>}
        {f.count > f.leads.length && <div style={{ ...small, padding: "8px 20px" }}>The oldest {f.leads.length} of {f.count}.</div>}
      </div>
      <div style={{ ...small, padding: "12px 20px 16px" }}>{note}</div>
    </Card>
  );
}

export default function AgentPipeline({ data, onBack }) {
  const [filter, setFilter] = useState(data.default_stuck_filter);
  useEffect(() => { setFilter(data.default_stuck_filter); }, [data.agent.key, data.window.key]);
  const a = data.agent;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div>
        <button type="button" onClick={onBack} style={{ fontFamily: FB, fontSize: 13, fontWeight: 600, color: T.slate, background: "none",
          border: "none", padding: 0, cursor: "pointer" }}>← Team pipeline</button>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginTop: 12 }}>
          <span style={{ fontFamily: FD, fontSize: 20, fontWeight: 600, color: T.ink }}>{a.name}</span>
          <Pill tone={TIER_TONE[a.tier]}>{a.tier_label}</Pill>
          <span style={{ fontFamily: FB, fontSize: 13, color: T.muted }}>
            {a.lead_count} leads · {a.rank} of {a.rank_of} by predicted close · {a.tier_reason}
          </span>
        </div>
      </div>
      <div className="cc-score">
        {data.highlights.map((h) => (
          <StatTile key={h.key} label={h.label} value={h.value}
            sub={<>{h.context}{h.delta ? <span style={{ color: h.direction === "neg" ? T.poppyText : T.meadowInk }}> · {h.delta}</span> : null}</>} />
        ))}
      </div>
      <div className="cc-twocol">
        <VerticalFunnel stages={data.funnel} selected={filter} onSelect={setFilter} />
        <div style={{ flex: "1 1 320px", minWidth: 280, display: "flex", flexDirection: "column", gap: 16 }}>
          <SpeedCard speed={data.speed_to_first_contact} />
          <TrendCard trend={data.conversion_trend} />
        </div>
      </div>
      <StuckList filters={data.stuck_leads_by_filter} active={filter} onChange={setFilter} note={data.stuck_note}
        filename={`${data.group.key}-${a.name.toLowerCase().replace(/\W+/g, "-")}-stuck.csv`} />
    </div>
  );
}
