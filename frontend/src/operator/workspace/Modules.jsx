import React from "react";
import { api } from "../api.js";
import { Card, Chip, Dot, Loading, LoadError, Mono, useApi } from "../primitives.jsx";
import { A, TYPE } from "../tokens.js";

/* Two groups, because plans.py draws exactly two. The plan's modules are the tier's decision and
   carry no switch: the API refuses a module the plan excludes, so a toggle here would be a
   setting the product ignores. The workspace's own tabs are its businesses and programmes, which
   a plan limits in number and never hides. */
export default function ModulesPane({ w }) {
  const data = useApi(() => api.modules(w.slug), [w.slug]);
  if (data.loading && !data.data) return <Loading label="Reading modules" />;
  if (data.error) return <LoadError error={data.error} onRetry={data.reload} />;
  const m = data.data;

  return (
    <>
      <Card title="Plan modules" pad={0} style={{ marginBottom: 16 }}
        sub={`Decided by the ${m.plan_name} plan${m.plan_set ? "" : " (defaulted, no plan set)"}, not by this workspace. There is no per-workspace switch, so there is nothing here to toggle.`}
        right={<Chip>{m.plan_name}</Chip>}>
        {m.gated.map((mod) => (
          <div key={mod.key} style={{ display: "flex", gap: 14, alignItems: "center", padding: "11px 16px", borderTop: `1px solid ${A.lineSoft}`, flexWrap: "wrap" }}>
            <div style={{ width: 20, flexShrink: 0, display: "flex", justifyContent: "center" }}>
              <Dot c={mod.included ? A.ink : A.lineMid} size={8} />
            </div>
            <div style={{ minWidth: 0, flex: "1 1 190px" }}>
              <div style={{ fontFamily: TYPE.text, fontSize: 13, fontWeight: 500, color: mod.included ? A.ink : A.mute }}>{mod.name}</div>
              <Mono size={10.5} c={A.mute}>{mod.key}</Mono>
            </div>
            {/* The answer this card exists to give. It was once the faintest text on it; it takes the
                readable grey here, never the disabled one. */}
            <div style={{ flex: "1 1 210px", minWidth: 0, fontFamily: TYPE.text, fontSize: 12, color: mod.included ? A.body : A.mute, textWrap: "pretty" }}>
              {mod.included ? "Included in this plan" : `Included from ${mod.lowest_tier_name} upward`}
            </div>
            <div style={{ width: 96, display: "flex", justifyContent: "flex-end", flexShrink: 0 }}>
              {mod.included ? <Chip state="healthy">Included</Chip> : <Chip title={`Requires the ${mod.lowest_tier_name} plan`}>{mod.lowest_tier_name}+</Chip>}
            </div>
          </div>
        ))}
        <div style={{ padding: "12px 16px", borderTop: `1px solid ${A.lineSoft}`, fontFamily: TYPE.text, fontSize: 11.5, color: A.mute, lineHeight: 1.6, textWrap: "pretty" }}>
          A module the plan excludes is refused by the API, not merely hidden. That is why this list is read-only.
        </div>
      </Card>

      <Card title="This workspace's own tabs" pad={0}
        sub="Its businesses and programmes. A plan limits how many it may have, never whether it may look at the ones it has.">
        {m.own.map((tab) => (
          <div key={tab.key} style={{ display: "flex", gap: 14, alignItems: "center", padding: "11px 16px", borderTop: `1px solid ${A.lineSoft}`, flexWrap: "wrap" }}>
            <div style={{ width: 20, flexShrink: 0, display: "flex", justifyContent: "center" }}><Dot c={A.ink} size={8} /></div>
            <div style={{ minWidth: 0, flex: "1 1 190px" }}>
              <div style={{ fontFamily: TYPE.text, fontSize: 13, fontWeight: 500, color: A.ink }}>{tab.name}</div>
              <Mono size={10.5} c={A.mute}>{tab.key}</Mono>
            </div>
            {tab.always ? <Chip title="A workspace with no portfolio view has no product">Not optional</Chip> : <Chip>Their tab</Chip>}
          </div>
        ))}
      </Card>
    </>
  );
}
