import React from "react";
import { api } from "../api.js";
import { compact, pct } from "../format.js";
import { Card, Loading, LoadError, Row, useApi } from "../primitives.jsx";
import { A, TYPE } from "../tokens.js";

function Meter({ label, used, cap, fmt = String, note }) {
  const p = cap ? Math.min(100, pct(used, cap)) : 0;
  const hot = cap == null ? A.ink : p >= 90 ? A.stop : p >= 70 ? A.warn : A.ink;
  return (
    <div style={{ padding: "12px 0", borderTop: `1px solid ${A.lineSoft}` }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "baseline" }}>
        <span style={{ fontFamily: TYPE.text, fontSize: 12.5, fontWeight: 500, color: A.ink }}>{label}</span>
        <span style={{ fontFamily: TYPE.data, fontSize: 13, fontWeight: 600, color: hot, fontVariantNumeric: "tabular-nums" }}>
          {fmt(used)}<span style={{ color: A.mute, fontSize: 11.5 }}> / {cap == null ? "unlimited" : fmt(cap)}</span>
        </span>
      </div>
      {cap != null ? (
        <div role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={cap} aria-valuenow={Math.min(used, cap)}
          style={{ height: 5, borderRadius: 99, background: A.lineSoft, overflow: "hidden", margin: "7px 0 5px" }}>
          <span style={{ display: "block", height: "100%", width: `${p}%`, background: hot, borderRadius: 99, transition: "width .3s ease" }} />
        </div>
      ) : null}
      <div style={{ fontFamily: TYPE.text, fontSize: 11, color: A.mute, lineHeight: 1.5, marginTop: cap == null ? 6 : 0, textWrap: "pretty" }}>{note}</div>
    </div>
  );
}

export default function UsagePane({ w, open }) {
  const data = useApi(() => api.usage(w.slug), [w.slug]);
  if (data.loading && !data.data) return <Loading label="Reading usage" />;
  if (data.error) return <LoadError error={data.error} onRetry={data.reload} />;
  const u = data.data;

  return (
    <>
      <Card title="Usage" sub="What this workspace consumes, against the caps its plan enforces. Caps are read from the plan table, never written here." style={{ marginBottom: 16 }}>
        <Meter label="Businesses · the meter" used={u.businesses.used} cap={u.businesses.cap}
          note="Plans price on businesses, not seats. Existing businesses keep working above a cap; adding one is refused." />
        <Meter label="Seats" used={u.seats.used} cap={u.seats.cap}
          note={u.seats.invited ? `Invited seats count, and ${u.seats.invited} ${u.seats.invited === 1 ? "is" : "are"} unaccepted.` : "Invited seats count. None outstanding."} />
        <Meter label="AI employee tokens" used={u.tokens.used} cap={u.tokens.budget} fmt={compact}
          note={u.tokens.budget
            ? `This calendar month. Over budget, AI employee runs land skipped rather than failing.${u.tokens.override ? " A workspace-specific budget." : " The platform default."}`
            : "This calendar month. No cap is set, so no run is ever skipped for budget."} />
        <Meter label="Live share links" used={u.share_links.live} cap={u.share_links.cap}
          note="Links that serve without a login. A plan limits how many may exist." />
        <Meter label="Sync runs" used={u.sync_runs_7d} cap={null}
          note={`Rolling 7 days across every source. No quota: every workspace syncs on the same ${u.sync_interval_minutes}-minute schedule.`} />
      </Card>
      <Card title="Documents">
        <Row k="Binder documents">{u.documents.count}</Row>
        <Row k="Storage used"><span style={{ color: A.mute }}>No figure: uploads are counted, never sized</span></Row>
      </Card>
    </>
  );
}
