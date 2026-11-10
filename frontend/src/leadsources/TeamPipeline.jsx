/* Lead sources › Team pipeline (docs/specs/ulrg-reporting-dashboard-port.md §2.5, §4.4).

   Where the channel's leads are in an eight-stage funnel, which agents need coaching, and why.
   Tiers, gaps, the forecast and the stuck counts arrive decided by the server; this file draws them.
   An agent row opens that agent's pipeline. */
import { T, alpha } from "../theme.js";
import { Card, PanelLabel, Source, StatTile } from "../panels.jsx";
import { Pill } from "../books/ui.jsx";
import { CardHead, ExportButton, FB, FD, FM, TIER_DOT, downloadCsv, fmt, small, td, th } from "./kit.jsx";

function gapColor(pp) {
  if (pp == null) return T.muted;
  if (pp < -10) return T.poppyText;
  if (pp < 0) return T.daffodilText;
  return T.meadowInk;
}

function FunnelStrip({ funnel, note }) {
  const lead = funnel.stages[0].count || 0;
  return (
    <Card>
      <CardHead right={<Source name="Follow Up Boss · Sisu" />}><PanelLabel accent={T.meadow}>Team funnel</PanelLabel></CardHead>
      <div className="ls-funnel">
        {funnel.stages.map((st, i) => (
          <div key={st.key} title={st.description} style={{ boxSizing: "border-box", minWidth: 0, background: T.parchment,
            borderRadius: 10, padding: "12px 13px", display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ height: 4, borderRadius: 2, background: alpha(T.evergreen, 0.12), overflow: "hidden", marginBottom: 6 }}>
              <div style={{ width: `${lead ? (st.count / lead) * 100 : 0}%`, height: "100%", background: T.evergreen }} />
            </div>
            <span style={{ fontFamily: FB, fontSize: 12, color: T.slate, fontWeight: 500, minHeight: 32, lineHeight: 1.35 }}>{st.label}</span>
            <span style={{ fontFamily: FD, fontSize: 22, fontWeight: 600, color: T.ink, fontVariantNumeric: "tabular-nums" }}>{fmt.count(st.count)}</span>
            <span style={{ fontFamily: FB, fontSize: 12, color: T.muted }}>{i === 0 ? "the baseline" : `${fmt.pct(st.conv_pct_from_prev)} of previous`}</span>
            {i > 0 && (
              <span style={{ fontFamily: FB, fontSize: 12, fontWeight: 600, color: gapColor(st.vs_benchmark_pp) }}
                    title={`Benchmark median ${st.benchmark_median_pct}%`}>
                {fmt.pp(st.vs_benchmark_pp)} vs {st.benchmark_median_pct}%
              </span>
            )}
            <span style={{ fontFamily: FB, fontSize: 12, color: st.stuck_count ? T.daffodilText : T.muted }}>
              {st.stuck_count ? `${st.stuck_count} stuck` : "none stuck"}
            </span>
            {st.is_bottleneck && <span style={{ marginTop: 4 }}><Pill tone="bad">Biggest drop</Pill></span>}
          </div>
        ))}
      </div>
      <div style={{ ...small, marginTop: 12 }}>
        Counts are cumulative: a lead that met with an agent also counts as contacted and set. A Follow Up Boss "Closed"
        stage counts only once Sisu has the close. {note}
      </div>
    </Card>
  );
}

function Coaching({ tiers, onAgent }) {
  return (
    <Card>
      <CardHead><PanelLabel accent={T.meadow}>Coaching</PanelLabel></CardHead>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 220px), 1fr))", gap: 16 }}>
        {Object.entries(tiers).map(([k, t]) => (
          <div key={k} style={{ minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, paddingBottom: 8, borderBottom: `1px solid ${T.line}` }}>
              <span style={{ width: 8, height: 8, borderRadius: 2, background: TIER_DOT[k] }} />
              <span style={{ fontFamily: FB, fontSize: 13, fontWeight: 600, color: T.ink }}>{t.label}</span>
              <span style={{ fontFamily: FM, fontSize: 12, color: T.muted, marginLeft: "auto" }}>{t.count}</span>
            </div>
            {t.agents.length === 0 && <div style={{ ...small, paddingTop: 8 }}>Nobody</div>}
            {t.agents.map((a) => (
              <button key={a.key} type="button" onClick={() => onAgent(a.key)} className="cc-card"
                style={{ display: "block", width: "100%", textAlign: "left", font: "inherit", border: "none", background: "transparent",
                  borderBottom: `1px solid ${T.line}`, padding: "8px 4px", margin: "0 -4px", cursor: "pointer", borderRadius: 6 }}>
                <span style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                  <span style={{ fontFamily: FB, fontSize: 13, fontWeight: 600, color: T.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.name}</span>
                  <span style={{ fontFamily: FM, fontSize: 12, color: T.muted, marginLeft: "auto", whiteSpace: "nowrap" }}>{a.lead_count} leads</span>
                </span>
                <span style={{ display: "block", fontFamily: FB, fontSize: 12, color: T.slate, marginTop: 2, lineHeight: 1.45 }}>{a.reasons[0]}</span>
              </button>
            ))}
          </div>
        ))}
      </div>
    </Card>
  );
}

/* Eight tiny bars, each the stage's share of the agent's leads -- the row's funnel at a glance. */
function MiniFunnel({ counts, order }) {
  const top = counts[order[0]] || 0;
  return (
    <span aria-hidden style={{ display: "inline-flex", alignItems: "flex-end", gap: 2, height: 22 }}>
      {order.map((k) => (
        <span key={k} title={`${k.replace(/_/g, " ")}: ${counts[k]}`} style={{ width: 6, borderRadius: 1,
          height: `${Math.max(2, top ? (counts[k] / top) * 22 : 2)}px`, background: counts[k] ? T.evergreen : T.line }} />
      ))}
    </span>
  );
}

const CSV = [
  { key: "agent", label: "Agent" }, { key: "tier_label", label: "Tier" }, { key: "tier_reason", label: "Why" },
  { key: "lead_count", label: "Leads" }, { key: "pickup_rate", label: "Pickup %" }, { key: "uncontacted_count", label: "Uncontacted" },
  { key: "closed_count", label: "Closed (Sisu)" }, { key: "raw_cvr", label: "Close rate %" }, { key: "predicted_cvr", label: "Predicted close %" },
  { key: "stuck_count", label: "Stuck" }, { label: "Biggest gap", value: (r) => r.biggest_bottleneck?.label || "" },
  { label: "Speed median (min)", value: (r) => r.speed_median_min ?? "" },
];

function AgentTable({ agents, order, onAgent, filename }) {
  const tiers = ["critical", "needs_coaching", "on_track", "strong"];
  return (
    <Card style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ padding: "20px 20px 0" }}>
        <CardHead right={<ExportButton onClick={() => downloadCsv(filename, CSV, agents)} disabled={!agents.length} />}>
          <PanelLabel accent={T.meadow}>Agents</PanelLabel>
        </CardHead>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", minWidth: 860, borderCollapse: "collapse" }}>
          <thead>
            <tr>{["Agent", "Funnel", "Leads", "Pickup", "Closed", "Predicted close", "Stuck", "Biggest gap"].map((h, i) => (
              <th key={h} style={{ ...th, textAlign: i < 2 || i === 7 ? "left" : "right", paddingLeft: i === 0 ? 20 : 8 }}>{h}</th>))}</tr>
          </thead>
          {tiers.map((tier) => {
            const rows = agents.filter((a) => a.tier === tier);
            if (!rows.length) return null;
            return (
              <tbody key={tier}>
                <tr><td colSpan={8} style={{ ...td, textAlign: "left", paddingLeft: 20, background: T.parchment, color: T.slate,
                  fontWeight: 600, fontSize: 12 }}>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                    <span style={{ width: 8, height: 8, borderRadius: 2, background: TIER_DOT[tier] }} />{rows[0].tier_label} · {rows.length}
                  </span>
                </td></tr>
                {rows.map((a) => (
                  <tr key={a.key} className="ls-row" onClick={() => onAgent(a.key)} style={{ cursor: "pointer" }} title={a.tier_reason}>
                    <td style={{ ...td, textAlign: "left", paddingLeft: 20, fontWeight: 600 }}>{a.agent}</td>
                    <td style={{ ...td, textAlign: "left" }}><MiniFunnel counts={a.stage_counts} order={order} /></td>
                    <td style={td}>{a.lead_count}</td>
                    <td style={{ ...td, color: a.pickup_rate < 30 ? T.poppyText : T.ink }}>{fmt.pct(a.pickup_rate)}</td>
                    <td style={td}>{a.closed_count}</td>
                    <td style={td}>{fmt.pct(a.predicted_cvr)}{a.closed_count ? "" : <span style={{ color: T.muted }} title="No close yet: a forecast from the agent's stage rates"> est.</span>}</td>
                    <td style={td}>{a.stuck_count > 0
                      ? <Pill tone={a.stuck_severity === "crit" ? "bad" : a.stuck_severity === "warn" ? "warn" : "muted"}>{a.stuck_count}</Pill> : 0}</td>
                    <td style={{ ...td, textAlign: "left", color: a.biggest_bottleneck?.severity === "crit" ? T.poppyText : T.slate }}>
                      {a.biggest_bottleneck?.severity !== "none" ? a.biggest_bottleneck?.label : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            );
          })}
        </table>
      </div>
    </Card>
  );
}

export default function TeamPipeline({ data, onAgent }) {
  const sm = data.summary;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="cc-score">
        <StatTile label="Leads" source="FUB" value={fmt.count(sm.leads)} sub={`${sm.agents} agent${sm.agents === 1 ? "" : "s"} · ${data.window.label.toLowerCase()}`} />
        <StatTile label="Pickup rate" source="FUB" value={fmt.pct(sm.pickup_rate_pct)} sub="touched or marked contacted" />
        <StatTile label="Closed" source="Sisu" value={fmt.count(sm.closed)} sub={`${fmt.pct(sm.team_cvr)} of leads · forecast ${fmt.pct(sm.predicted_cvr)}`} />
        <StatTile label="Stuck" source="FUB" value={fmt.count(sm.stuck)} sub="no touch in 7+ days" />
      </div>
      <FunnelStrip funnel={data.team_funnel} note={data.stuck_note} />
      <Coaching tiers={data.tiers} onAgent={onAgent} />
      <AgentTable agents={data.agents} order={data.stage_order} onAgent={onAgent}
        filename={`${data.group.key}-team-pipeline-${data.window.key}.csv`} />
    </div>
  );
}
