/* Lead sources › What's Working (docs/specs/ulrg-reporting-dashboard-port.md §2.7, §4.5).

   What the agents who convert do differently, in three framings over one payload: an agent
   against the team median, two agents against each other, and the leads that closed against the
   leads that died. Every median carries the count behind it -- a 2-minute median on three calls is
   not the same claim as one on fifty -- and the journeys show the first 72 hours of real leads. */
import { useMemo, useState } from "react";
import { T } from "../theme.js";
import { Icon } from "../Brand.jsx";
import { Card, PanelLabel, Segmented, Source } from "../panels.jsx";
import { CardHead, FB, FM, Note, fmt, small, td, th } from "./kit.jsx";

const GROUPS = { speed: "Speed", channel: "Channels", timing: "Timing", fairness: "The leads themselves" };

function value(def, m) {
  const v = m[def.key];
  if (v == null) return "—";
  switch (def.unit) {
    case "min": return fmt.minutes(v);
    case "sec": return fmt.seconds(v);
    case "days": return fmt.days(v);
    case "pct": return fmt.pct(v);
    case "money": return fmt.plainMoney(v);
    default: return Number(v).toFixed(1).replace(/\.0$/, "");
  }
}

/* Which side is ahead on a row, by the metric's direction. Neutral rows never pick a side. */
function better(def, a, b) {
  const x = a[def.key], y = b[def.key];
  if (x == null || y == null || def.direction === "neutral" || x === y) return null;
  return (def.direction === "lower_better" ? x < y : x > y) ? "a" : "b";
}

const select = { fontFamily: FB, fontSize: 13, color: T.ink, background: T.white, border: `1px solid ${T.line}`,
                 borderRadius: 8, padding: "6px 10px" };

function Compare({ defs, left, right, leftName, rightName, priceAvailable, coverage }) {
  const rows = defs.filter((d) => d.key !== "median_price" || priceAvailable);
  let lastGroup = null;
  return (
    <Card style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ padding: "20px 20px 0" }}>
        <CardHead right={<Source name="Follow Up Boss · Sisu" />}><PanelLabel accent={T.meadow}>Behavior</PanelLabel></CardHead>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", minWidth: 620, borderCollapse: "collapse" }}>
          <thead><tr>
            <th style={{ ...th, textAlign: "left", paddingLeft: 20 }}>Measure</th>
            <th style={th}>{leftName}</th><th style={th}>{rightName}</th>
          </tr></thead>
          <tbody>
            {rows.map((d) => {
              const head = d.group !== lastGroup ? GROUPS[d.group] : null;
              lastGroup = d.group;
              const win = better(d, left, right);
              return [head && (
                <tr key={`${d.group}-h`}><td colSpan={3} style={{ ...td, textAlign: "left", paddingLeft: 20, background: T.parchment,
                  color: T.slate, fontWeight: 600, fontSize: 12 }}>{head}</td></tr>
              ), (
                <tr key={d.key}>
                  <td style={{ ...td, textAlign: "left", paddingLeft: 20, whiteSpace: "normal" }}>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                      {d.label}
                      {d.caveat && <span title={d.caveat} style={{ display: "inline-flex", cursor: "help" }}><Icon name="info" size={13} color={T.muted} /></span>}
                    </span>
                  </td>
                  {[["a", left], ["b", right]].map(([side, m]) => (
                    <td key={side} style={{ ...td, paddingRight: side === "b" ? 20 : 8 }}>
                      <span style={{ fontFamily: FM, fontWeight: win === side ? 600 : 400, color: win === side ? T.meadowInk : T.ink }}>{value(d, m)}</span>
                      <span style={{ fontFamily: FM, fontSize: 12, color: T.muted }}> n={m[d.n_key] ?? 0}</span>
                    </td>
                  ))}
                </tr>
              )];
            })}
          </tbody>
        </table>
      </div>
      <div style={{ ...small, padding: "12px 20px 16px" }}>
        Medians over the leads themselves, never an average of medians; n is how many leads, calls or appointments each one rests on.
        {!priceAvailable && ` Median price is left out: Follow Up Boss has a price on ${coverage}% of these leads, under the 60% it takes to compare.`}
      </div>
    </Card>
  );
}

function Hygiene({ agents }) {
  const flagged = agents.filter((a) => a.metrics.logging_diagnostics.is_hygiene_suspect);
  return (
    <Card>
      <CardHead><PanelLabel accent={T.daffodil}>Logging</PanelLabel></CardHead>
      {flagged.length === 0 ? (
        <div style={{ fontFamily: FB, fontSize: 13, color: T.slate }}>Every agent logs enough in Follow Up Boss for these medians to mean something.</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {flagged.map((a) => (
            <div key={a.key} style={{ display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
              <span style={{ fontFamily: FB, fontSize: 13, fontWeight: 600, color: T.ink, minWidth: 140 }}>{a.name}</span>
              <span style={{ fontFamily: FB, fontSize: 13, color: T.slate, flex: "1 1 280px" }}>{a.metrics.logging_diagnostics.hygiene_reason}</span>
            </div>
          ))}
          <div style={{ ...small, marginTop: 4 }}>Read these agents' rows above with care: the work may be happening, just not where it can be counted.</div>
        </div>
      )}
    </Card>
  );
}

const CHANNEL_SHAPE = { call: 99, text: 2, email: 2, appt: 2 };

function Journey({ j, hours }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0,180px) minmax(0,1fr)", gap: 16, alignItems: "center", padding: "8px 0",
      borderTop: `1px solid ${T.line}` }}>
      <span style={{ minWidth: 0 }}>
        <span style={{ display: "block", fontFamily: FB, fontSize: 13, fontWeight: 600, color: T.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {j.fub_url ? <a href={j.fub_url} target="_blank" rel="noreferrer" style={{ color: "inherit", textDecoration: "none" }}>{j.name}</a> : j.name}
        </span>
        <span style={{ display: "block", fontFamily: FB, fontSize: 12, color: T.muted }}>{j.agent} · {j.touch_count_24h} in first day</span>
      </span>
      <span style={{ position: "relative", height: 18, display: "block" }}>
        <span style={{ position: "absolute", left: 0, right: 0, top: 8, height: 2, background: T.line }} />
        {[24, 48].map((h) => <span key={h} style={{ position: "absolute", left: `${(h / hours) * 100}%`, top: 4, width: 1, height: 10, background: T.line }} />)}
        {j.events.map((e, i) => (
          <span key={i} title={`${e.channel} · ${e.kind === "live" ? "connected" : "attempt"} · ${fmt.minutes(e.hours_in * 60)} in`}
            style={{ position: "absolute", left: `calc(${e.axis_pct}% - 5px)`, top: 4, width: 10, height: 10, boxSizing: "border-box",
              borderRadius: CHANNEL_SHAPE[e.channel] ?? 99, background: e.kind === "live" ? T.evergreen : T.white,
              border: `1.5px solid ${e.channel === "appt" ? T.teal : T.evergreen}` }} />
        ))}
        {j.continues && <span aria-label="continues past 72 hours" style={{ position: "absolute", right: -14, top: 0, fontFamily: FB, fontSize: 12, color: T.muted }}>→</span>}
      </span>
    </div>
  );
}

function Journeys({ title, list, hours }) {
  return (
    <Card style={{ flex: "1 1 440px", minWidth: 300 }}>
      <CardHead><PanelLabel accent={T.meadow}>{title}</PanelLabel></CardHead>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,180px) minmax(0,1fr)", gap: 16, paddingBottom: 6 }}>
        <span />
        <span style={{ display: "flex", justifyContent: "space-between", ...small, paddingRight: 4 }}>
          <span>arrival</span><span>24 hr</span><span>48 hr</span><span>72 hr</span>
        </span>
      </div>
      {list.slice(0, 12).map((j) => <Journey key={j.person_id} j={j} hours={hours} />)}
      {list.length === 0 && <div style={{ ...small, paddingTop: 8 }}>No leads here yet.</div>}
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginTop: 12, ...small }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span style={{ width: 10, height: 10, borderRadius: 99, background: T.evergreen }} />connected call</span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span style={{ width: 10, height: 10, borderRadius: 99, boxSizing: "border-box", border: `1.5px solid ${T.evergreen}` }} />attempt</span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span style={{ width: 10, height: 10, borderRadius: 2, boxSizing: "border-box", border: `1.5px solid ${T.evergreen}` }} />text or email</span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span style={{ width: 10, height: 10, borderRadius: 2, boxSizing: "border-box", border: `1.5px solid ${T.teal}` }} />appointment</span>
      </div>
    </Card>
  );
}

const FRAMINGS = [["team", "Agent vs team"], ["pair", "Agent vs agent"], ["outcome", "Closed vs dead"]];

export default function WhatsWorking({ data }) {
  const agents = data.agents;
  const [framing, setFraming] = useState("team");
  const [a, setA] = useState(agents[0]?.key);
  const [b, setB] = useState(agents[1]?.key || agents[0]?.key);
  const byKey = useMemo(() => Object.fromEntries(agents.map((x) => [x.key, x])), [agents]);
  const A = byKey[a] || agents[0], B = byKey[b] || agents[1] || agents[0];

  if (!agents.length) {
    return <Note tone="plain">No leads from this source in the window, so there is no behavior to compare.</Note>;
  }
  let left, right, leftName, rightName, journeyLeft, journeyRight, jl, jr;
  if (framing === "outcome") {
    left = data.outcome_metrics.closed; right = data.outcome_metrics.dead;
    leftName = `Closed in Sisu · ${left.n_leads}`; rightName = `Dead · ${right.n_leads}`;
    journeyLeft = data.journeys.pooled_closed; journeyRight = data.journeys.pooled_dead; jl = "Leads that closed"; jr = "Leads that died";
  } else if (framing === "pair") {
    left = A.metrics; right = B.metrics; leftName = A.name; rightName = B.name;
    journeyLeft = data.journeys.by_agent_closed[A.key] || []; journeyRight = data.journeys.by_agent_closed[B.key] || [];
    jl = `${A.name} · closed`; jr = `${B.name} · closed`;
  } else {
    left = A.metrics; right = data.team_metrics; leftName = A.name; rightName = "Team median";
    journeyLeft = data.journeys.by_agent_closed[A.key] || []; journeyRight = data.journeys.by_agent_dead[A.key] || [];
    jl = `${A.name} · closed`; jr = `${A.name} · dead`;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <Segmented options={FRAMINGS} value={framing} onChange={setFraming} tone={T.ink} label="Compare" />
        {framing !== "outcome" && (
          <select style={select} value={A?.key} onChange={(e) => setA(e.target.value)} aria-label="Agent">
            {agents.map((x) => <option key={x.key} value={x.key}>{x.name} · {x.metrics.n_leads} leads</option>)}
          </select>
        )}
        {framing === "pair" && (
          <>
            <span style={{ fontFamily: FB, fontSize: 13, color: T.muted }}>against</span>
            <select style={select} value={B?.key} onChange={(e) => setB(e.target.value)} aria-label="Second agent">
              {agents.map((x) => <option key={x.key} value={x.key}>{x.name} · {x.metrics.n_leads} leads</option>)}
            </select>
          </>
        )}
      </div>
      {data.sample_warning.is_small_sample && (
        <Note tone="plain">
          {data.sample_warning.team_closed} close{data.sample_warning.team_closed === 1 ? "" : "s"} in Sisu so far. Read these as patterns
          to look into, not as statistics: under 30 closes, one deal moves a median.
        </Note>
      )}
      <Compare defs={data.metric_definitions} left={left} right={right} leftName={leftName} rightName={rightName}
        priceAvailable={data.price_available} coverage={data.price_coverage_pct} />
      <div className="cc-twocol" style={{ alignItems: "flex-start" }}>
        <Journeys title={jl} list={journeyLeft} hours={data.journey_window_hours} />
        <Journeys title={jr} list={journeyRight} hours={data.journey_window_hours} />
      </div>
      <Hygiene agents={agents} />
    </div>
  );
}

