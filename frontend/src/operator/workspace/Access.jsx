import React, { useState } from "react";
import { api } from "../api.js";
import { ago, plural, titleCase } from "../format.js";
import { Btn, Card, Chip, Confirm, Empty, Loading, LoadError, Mono, Notice, Row, useAction, useApi } from "../primitives.jsx";
import { A, TYPE } from "../tokens.js";

const SCOPES = { ulrg_scorecard: "Scorecard", ulrg_team: "Team room", sd_rep: "Rep desk" };

export function ShareLinksCard({ w, reload }) {
  const data = useApi(() => api.shareLinks(w.slug), [w.slug]);
  const [all, setAll] = useState(false);
  const [arming, setArming] = useState(false);
  const action = useAction();
  if (data.loading && !data.data) return <Loading label="Reading share links" />;
  if (data.error) return <LoadError error={data.error} onRetry={data.reload} />;

  const links = data.data.links;
  const live = links.filter((l) => l.live);
  const shown = all ? links : live;
  const scopes = [...new Set(live.map((l) => SCOPES[l.scope] || titleCase(l.scope)))];

  async function revoke() {
    const out = await action.run("revoke", () => api.revokeShareLinks(w.slug),
      (r) => `Revoked ${plural(r.revoked, "share link")}. They now read as not found everywhere.`);
    setArming(false);
    if (out) { data.reload(); reload && reload(); }
  }

  return (
    <Card title="Public share links" pad={0}
      sub="Links that serve without a login. Suspending the workspace does not stop them, which is why they are listed here."
      right={live.length ? (
        <Btn small kind="danger" disabled={arming} onClick={() => { action.clear(); setArming(true); }}>Revoke all</Btn>
      ) : null}>
      {arming ? (
        <div style={{ padding: "12px 16px" }}>
          <Confirm label={`Revoke ${plural(live.length, "link")}`} busy={action.busy === "revoke"}
            onConfirm={revoke} onCancel={() => setArming(false)}>
            {plural(live.length, "live link")} ({scopes.join(", ")}) stop working at once, for whoever holds them. A revoked link cannot be restored; the workspace can make new ones.
          </Confirm>
        </div>
      ) : null}
      {action.result ? <div style={{ padding: "12px 16px" }}><Notice tone={action.result.tone}>{action.result.text}</Notice></div> : null}
      {shown.length === 0 ? (
        <Empty title={links.length ? "No live links" : "No links have been made"}>
          {links.length ? `${links.length} ${links.length === 1 ? "link was" : "links were"} revoked or expired.` : "Nothing in this workspace is shared publicly."}
        </Empty>
      ) : shown.map((l) => (
        <div key={l.id} style={{ display: "flex", gap: 12, alignItems: "baseline", justifyContent: "space-between", padding: "10px 16px", borderTop: `1px solid ${A.lineSoft}`, flexWrap: "wrap" }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <span style={{ fontFamily: TYPE.text, fontSize: 12.5, fontWeight: 500, color: A.ink }}>{SCOPES[l.scope] || titleCase(l.scope)}</span>
              {l.scope_ref ? <Mono size={11} c={A.mute}>{l.scope_ref}</Mono> : null}
              {l.live ? <Chip state="watch">Serving</Chip> : <Chip>{l.revoked_at ? "Revoked" : "Expired"}</Chip>}
            </div>
            <Mono size={10.5} c={A.mute}>made {ago(l.created_at)}{l.created_by ? ` by ${l.created_by}` : ""}{l.expires_at ? ` · expires ${new Date(l.expires_at).toLocaleDateString()}` : " · never expires"}</Mono>
          </div>
        </div>
      ))}
      {links.length > live.length ? (
        <div style={{ padding: "10px 16px", borderTop: `1px solid ${A.lineSoft}` }}>
          <button type="button" className="ac-link" onClick={() => setAll(!all)} style={{ background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: TYPE.text, fontSize: 11.5, fontWeight: 600, color: A.body }}>
            {all ? "Show live links only" : `Show revoked and expired too (${links.length - live.length})`}
          </button>
        </div>
      ) : null}
    </Card>
  );
}

export function SecurityCard({ w }) {
  const data = useApi(() => api.security(w.slug), [w.slug]);
  if (data.loading && !data.data) return <Loading label="Reading sign-in security" />;
  if (data.error) return <LoadError error={data.error} onRetry={data.reload} />;
  const sec = data.data;
  return (
    <Card title="Sign-in security" sub="Read-only. The workspace sets all of this, not you.">
      <Row k="Two-factor">{sec.two_factor.on} of {sec.two_factor.active} active {sec.two_factor.active === 1 ? "person" : "people"}</Row>
      <Row k="Required">No. The product has no setting that requires two-factor.</Row>
      <Row k="Locked out" top>
        {sec.locked.length ? sec.locked.map((p) => (
          <div key={p.id}><Mono c={A.ink}>{p.email}</Mono> <Mono size={10.5} c={A.mute}>until {new Date(p.locked_until).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</Mono></div>
        )) : "Nobody"}
      </Row>
      <Row k="Second-factor sections">{sec.step_up_sections.length ? sec.step_up_sections.map(titleCase).join(", ") : "None on this plan"}</Row>
      <div style={{ marginTop: 12, fontFamily: TYPE.text, fontSize: 11, color: A.mute, lineHeight: 1.55, textWrap: "pretty" }}>
        You can unlock a locked account. You cannot read, set or bypass anybody's second factor from here.
      </div>
    </Card>
  );
}

export default function AccessPane({ w, reload, children }) {
  return (
    <>
      {children}
      <div className="ac-split">
        <ShareLinksCard w={w} reload={reload} />
        <SecurityCard w={w} />
      </div>
    </>
  );
}
