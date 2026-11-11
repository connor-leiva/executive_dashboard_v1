/* Lead sources › Team pipeline (docs/specs/ulrg-reporting-dashboard-port.md §2.5, §4.4).

   Where the channel's leads are in an eight-stage funnel, which agents need coaching, and why.
   Tiers, gaps, the forecast and the stuck counts arrive decided by the server; this file draws them.
   Each agent's reason is the second line of their row in the agents table -- the separate coaching
   card said the same thing in a second place. An agent row opens that agent's pipeline. */
import { T, alpha } from "../theme.js";
import { Card, PanelLabel, Source, StatTile } from "../panels.jsx";
import { Pill } from "../books/ui.jsx";
import { CardHead, ExportButton, FB, FD, TIER_DOT, downloadCsv, fmt, td, th } from "./kit.jsx";

/* The funnel's stage names, short enough never to break in a narrow cell. The full name is the
   cell's tooltip. */
export const SHORT_STAGE = { leads_received: "New", contacted: "Contacted", qualified: "Qualified", appt_set: "Appt set",
                             met_with: "Met", showing: "Showing", offer_written: "Offer", closed: "Closed" };

/* "Contacted, 51 pts below" from the server's bottleneck: the short stage and the gap in words. */
export function gapText(b) {
  if (!b || !b.stage || b.severity === "none") return "—";
  return `${SHORT_STAGE[b.stage] || b.stage}, ${fmt.gap(b.gap_pp)}`;
}

function gapColor(pp) {
  if (pp == null) return T.muted;
  if (pp < -10) return T.poppyText;
  if (pp < 0) return T.daffodilText;
  return T.meadowInk;
}

function FunnelStrip({ funnel, note }) {
  const lead = funnel.stages[0].count || 0;
  const info = `Counts are cumulative: a lead that met with an agent also counts as contacted and set. A Follow Up Boss "Closed" `
    + `stage counts only once Sisu has the close. ${note}`;
  return (
    <Card>
      <CardHead right={<Source name="Follow Up Boss · Sisu" />}><PanelLabel accent={T.meadow} info={info}>Team funnel</PanelLabel></CardHead>
      <div className="ls-funnel">
        {funnel.stages.map((st, i) => (
          <div key={st.key} title={`${st.label}${st.description ? `: ${st.description}` : ""}`} style={{ boxSizing: "border-box", minWidth: 0, background: T.parchment,
            borderRadius: 10, padding: "12px 13px", display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ height: 4, borderRadius: 2, background: alpha(T.evergreen, 0.12), overflow: "hidden", marginBottom: 6 }}>
              <div style={{ width: `${lead ? (st.count / lead) * 100 : 0}%`, height: "100%", background: T.evergreen }} />
            </div>
            <span style={{ fontFamily: FB, fontSize: 13, color: T.slate, fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{SHORT_STAGE[st.key] || st.label}</span>
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
    </Card>
  );
}

const CSV = [
  { key: "agent", label: "Agent" }, { key: "tier_label", label: "Tier" }, { key: "tier_reason", label: "Why" },
  { key: "lead_count", label: "Leads" }, { key: "pickup_rate", label: "Pickup %" }, { key: "uncontacted_count", label: "Uncontacted" },
  { key: "closed_count", label: "Closed (Sisu)" }, { key: "raw_cvr", label: "Close rate %" }, { key: "predicted_cvr", label: "Predicted close %" },
  { key: "stuck_count", label: "Stuck" }, { label: "Biggest gap", value: (r) => (gapText(r.biggest_bottleneck) === "—" ? "" : gapText(r.biggest_bottleneck)) },
  { label: "Speed median (min)", value: (r) => r.speed_median_min ?? "" },
];

function AgentTable({ agents, reasons, onAgent, filename }) {
  const tiers = ["critical", "needs_coaching", "on_track", "strong"];
  const heads = ["Agent", "Leads", "Pickup", "Closed", "Predicted close", "Stuck", "Biggest gap"];
  return (
    <Card style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ padding: "20px 20px 0" }}>
        <CardHead right={<ExportButton onClick={() => downloadCsv(filename, CSV, agents)} disabled={!agents.length} />}>
          <PanelLabel accent={T.meadow}>Agents</PanelLabel>
        </CardHead>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", minWidth: 760, borderCollapse: "collapse" }}>
          <thead>
            <tr>{heads.map((h, i) => (
              <th key={h} style={{ ...th, textAlign: i === 0 || i === 6 ? "left" : "right", paddingLeft: i === 0 ? 20 : 8,
                paddingRight: i === 6 ? 20 : 8 }}>{h}</th>))}</tr>
          </thead>
          {tiers.map((tier) => {
            const rows = agents.filter((a) => a.tier === tier);
            if (!rows.length) return null;
            return (
              <tbody key={tier}>
                <tr><td colSpan={heads.length} style={{ ...td, textAlign: "left", paddingLeft: 20, background: T.parchment, color: T.slate,
                  fontWeight: 600, fontSize: 12 }}>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                    <span style={{ width: 8, height: 8, borderRadius: 2, background: TIER_DOT[tier] }} />{rows[0].tier_label} · {rows.length}
                  </span>
                </td></tr>
                {rows.map((a) => {
                  // Red belongs to the one tier that is an emergency; every other gap reads in ink.
                  const critical = a.tier === "critical";
                  return (
                    <tr key={a.key} className="ls-row" onClick={() => onAgent(a.key)} style={{ cursor: "pointer" }}>
                      <td style={{ ...td, textAlign: "left", paddingLeft: 20, whiteSpace: "normal" }}>
                        <div style={{ fontWeight: 600 }}>{a.agent}</div>
                        <div style={{ fontSize: 12, color: T.slate, marginTop: 2, maxWidth: 520, lineHeight: 1.45 }}>{reasons[a.key] || a.tier_reason}</div>
                      </td>
                      <td style={td}>{a.lead_count}</td>
                      <td style={{ ...td, color: a.pickup_rate < 30 ? T.poppyText : T.ink }}>{fmt.pct(a.pickup_rate)}</td>
                      <td style={td}>{a.closed_count}</td>
                      <td style={td}>{fmt.pct(a.predicted_cvr)}{a.closed_count ? "" : <span style={{ color: T.muted }} title="No close yet: a forecast from the agent's stage rates"> est.</span>}</td>
                      <td style={td}>{a.stuck_count > 0
                        ? <Pill tone={a.stuck_severity === "crit" || a.stuck_severity === "warn" ? "warn" : "muted"}>{a.stuck_count}</Pill> : 0}</td>
                      <td style={{ ...td, textAlign: "left", paddingRight: 20, color: critical ? T.poppyText : T.ink }}
                        title={a.biggest_bottleneck?.label || undefined}>{gapText(a.biggest_bottleneck)}</td>
                    </tr>
                  );
                })}
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
  // The coaching sentence the server wrote for each agent, which says more than the tier's short reason.
  const reasons = Object.fromEntries(Object.values(data.tiers || {}).flatMap((t) => t.agents.map((a) => [a.key, a.reasons?.[0]])));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="cc-score">
        <StatTile label="Leads" source="FUB" value={fmt.count(sm.leads)} sub={`${sm.agents} agent${sm.agents === 1 ? "" : "s"}`} />
        <StatTile label="Pickup rate" source="FUB" value={fmt.pct(sm.pickup_rate_pct)} sub="touched or marked contacted" />
        <StatTile label="Closed" source="Sisu" value={fmt.count(sm.closed)} sub={`${fmt.pct(sm.team_cvr)} of leads · forecast ${fmt.pct(sm.predicted_cvr)}`} />
        <StatTile label="Stuck" source="FUB" value={fmt.count(sm.stuck)} sub="no touch in 7+ days" />
      </div>
      <FunnelStrip funnel={data.team_funnel} note={data.stuck_note} />
      <AgentTable agents={data.agents} reasons={reasons} onAgent={onAgent}
        filename={`${data.group.key}-team-pipeline-${(data.window.start || "all")}-${(data.window.end || "time")}.csv`} />
    </div>
  );
}
