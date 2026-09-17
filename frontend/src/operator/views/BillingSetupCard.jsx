import React, { useState } from "react";
import { api } from "../api.js";
import { ago } from "../format.js";
import { Btn, Card, Chip, Field, inputStyle, Loading, LoadError, Mono, Notice, Row, useAction, useApi } from "../primitives.jsx";
import { A, TYPE } from "../tokens.js";

/* Acumyn's own Stripe account: the one that charges workspaces. Keys are pasted here by an
   operator, verified against Stripe, stored encrypted and never shown again. Nothing in this card
   touches a workspace's own Stripe connection, which lives in that workspace's Settings. */
export default function BillingSetupCard() {
  const cfg = useApi(() => api.billingConfig(), []);
  const [key, setKey] = useState("");
  const [hook, setHook] = useState("");
  const action = useAction();
  if (cfg.loading && !cfg.data) return <Card title="Platform billing"><Loading label="Reading billing" /></Card>;
  if (cfg.error) return <LoadError error={cfg.error} onRetry={cfg.reload} />;

  const c = cfg.data;
  const apiBase = String(import.meta.env.VITE_API_BASE || "http://localhost:8000/api/v1").replace(/\/api\/v1\/?$/, "");
  const save = async (body, said) => {
    const out = await action.run("save", () => api.setBillingConfig(body), said);
    if (out) { setKey(""); setHook(""); cfg.reload(); }
  };

  return (
    <Card title="Platform billing"
      sub="Acumyn's own Stripe account, which charges workspaces for their plans. Not any workspace's Stripe: those are revenue sources connected inside each workspace.">
      <Row k="Account">
        {c.connected ? <><Mono c={A.ink}>{c.account_name || c.account_id}</Mono> {c.livemode ? <Chip state="healthy">Live</Chip> : <Chip state="watch">Test mode</Chip>}</>
          : <span style={{ color: A.mute }}>Not connected</span>}
      </Row>
      <Row k="Charging">{c.enabled ? <Chip state="healthy">On</Chip> : <Chip>Off</Chip>}</Row>
      <Row k="Webhook">{c.webhook_configured ? <Chip state="healthy">Signing secret set</Chip> : <Chip state="watch">Not set</Chip>}</Row>
      {c.updated_by ? <Row k="Last changed"><span>{c.updated_by} · {ago(c.updated_at)}</span></Row> : null}

      <div className="ac-form2" style={{ marginTop: 14 }}>
        <Field label={c.connected ? "Replace the secret key" : "Secret key"} htmlFor="bs-key"
          note="From Stripe, Developers, API keys. Verified with Stripe before it is saved, then stored encrypted and never shown again.">
          <input id="bs-key" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)}
            placeholder="sk_live_…" style={{ ...inputStyle, fontFamily: TYPE.data }} />
        </Field>
        <Field label={c.webhook_configured ? "Replace the webhook signing secret" : "Webhook signing secret"} htmlFor="bs-hook"
          note={`Add an endpoint in Stripe for ${apiBase}${c.webhook_path}, listening for: ${c.events.join(", ")}. Then paste its signing secret here.`}>
          <input id="bs-hook" type="password" autoComplete="off" value={hook} onChange={(e) => setHook(e.target.value)}
            placeholder="whsec_…" style={{ ...inputStyle, fontFamily: TYPE.data }} />
        </Field>
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap", alignItems: "center" }}>
        <Btn small kind="solid" disabled={!key.trim() && !hook.trim()} busy={action.busy === "save"}
          onClick={() => save({ ...(key.trim() ? { secret_key: key.trim() } : {}), ...(hook.trim() ? { webhook_secret: hook.trim() } : {}) }, "Saved and verified with Stripe.")}>
          {c.connected ? "Save" : "Connect"}
        </Btn>
        {c.connected ? (
          <Btn small kind={c.enabled ? "danger" : "ghost"} busy={action.busy === "save"}
            onClick={() => save({ enabled: !c.enabled }, c.enabled ? "Switched off. No route will call Stripe until it is on again." : "Switched on.")}>
            {c.enabled ? "Switch charging off" : "Switch charging on"}
          </Btn>
        ) : null}
      </div>
      {action.result ? <div style={{ marginTop: 10 }}><Notice tone={action.result.tone}>{action.result.text}</Notice></div> : null}
    </Card>
  );
}
