import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { ago, dollars, listPrice, titleCase } from "../format.js";
import { Btn, Card, Chip, Empty, Field, inputStyle, Loading, LoadError, Mono, Notice, Row, useAction, useApi } from "../primitives.jsx";
import { A, STATE, TYPE } from "../tokens.js";

/* Stripe's own vocabulary, mapped onto the console's severities so the Billing pane and the fleet
   list agree: a past-due subscription is broken in both places, or one of them is lying. */
const SUB_STATE = {
  active: { label: "Active", tone: "healthy" },
  trialing: { label: "Trialing", tone: "trial" },
  past_due: { label: "Past due", tone: "broken" },
  unpaid: { label: "Unpaid", tone: "broken" },
  incomplete: { label: "Incomplete", tone: "stalled" },
  incomplete_expired: { label: "Incomplete, expired", tone: "stalled" },
  canceled: { label: "Canceled", tone: "suspended" },
  paused: { label: "Paused", tone: "watch" },
  none: { label: "Not billed", tone: undefined },
};
const INV_TONE = { paid: "healthy", open: "watch", uncollectible: "broken", void: undefined, draft: undefined };

const days = (iso) => (iso ? Math.round((Date.parse(iso) - Date.now()) / 86400000) : null);

/* "MM/YYYY" from Stripe's card expiry, and whether that month has already ended. */
function expired(exp) {
  const m = /^(\d{1,2})\/(\d{4})$/.exec(exp || "");
  if (!m) return false;
  return new Date(Number(m[2]), Number(m[1]), 1) <= new Date();
}

function verdict(b, reference) {
  const sub = b.subscription;
  if (!sub) {
    return ["No Stripe customer exists for this workspace.",
      b.billing.connected ? "Nothing is charged until one is created. A workspace Axcion runs for itself should stay this way."
        : "Platform billing is not connected yet, so no workspace can be charged. Connect it on the System page."];
  }
  const amount = sub.amount_cents != null ? `${dollars(sub.amount_cents)} ${sub.interval === "year" ? "a year" : "a month"}` : "an unknown amount";
  const renew = days(sub.current_period_end);
  const openInv = b.invoices.find((i) => i.status === "open");
  switch (sub.status) {
    case "past_due":
    case "unpaid":
      return [`${openInv ? `${openInv.attempt_count} charge ${openInv.attempt_count === 1 ? "attempt has" : "attempts have"} failed` : "Stripe could not collect the last invoice"}.`,
        sub.payment_method ? `The payment method on file is ${sub.payment_method}${sub.payment_method_exp ? `, ${expired(sub.payment_method_exp) ? "which expired" : "expiring"} ${sub.payment_method_exp}` : ""}. A new one has to come from the customer.` : "There is no payment method on file. The customer has to add one."];
    case "incomplete":
      return ["The subscription was created but never paid.", "Stripe is holding an open invoice. Until it is paid the subscription does nothing, which is why this workspace reads as stalled."];
    case "trialing":
      return [`Trial converts ${days(sub.trial_end) != null ? `in ${days(sub.trial_end)} days` : "at its end date"}.`,
        sub.payment_method ? "A payment method is on file, so conversion charges it automatically." : `No payment method is on file, so the first charge of ${amount} will fail unless one is added.`];
    case "canceled":
      return ["The subscription is canceled.", "Nothing more will be charged. Access is decided by the workspace's status, not by Stripe."];
    default:
      return [`Paying ${amount}.`, `${dollars(sub.collected_cents)} collected so far.${renew != null ? ` Renews in ${renew} days.` : ""}`];
  }
}

function SubscriptionCard({ b }) {
  const sub = b.subscription;
  return (
    <Card title="Subscription" sub="Read-only. Stripe owns every value here; an editable copy would drift from the real charge."
      right={sub ? <a href={sub.subscription_url || sub.customer_url} target="_blank" rel="noopener noreferrer" className="ac-link"
        style={{ fontFamily: TYPE.text, fontSize: 11.5, fontWeight: 600, color: A.body }}>Open in Stripe ↗</a> : null}>
      {!sub ? (
        <Empty title="Not billed">This workspace has never had a Stripe customer, so it is not charged and adds nothing to MRR.</Empty>
      ) : (
        <>
          <Row k="Customer"><Mono c={A.ink}>{sub.customer_id}</Mono></Row>
          <Row k="Subscription">{sub.subscription_id ? <Mono c={A.ink}>{sub.subscription_id}</Mono> : "None"}</Row>
          <Row k="Price">{sub.price_id ? <Mono c={A.ink}>{sub.price_id}</Mono> : "Unknown"}</Row>
          <Row k="Amount">{sub.amount_cents != null ? <>{dollars(sub.amount_cents)} <span style={{ color: A.mute }}>/ {sub.interval || "period"}</span></> : "Unknown"}</Row>
          <Row k="Payment method" top>
            {sub.payment_method ? (
              <>
                <div>{sub.payment_method}</div>
                {sub.payment_method_exp ? (
                  <Mono size={10.5} c={expired(sub.payment_method_exp) ? A.stop : A.mute}>
                    {expired(sub.payment_method_exp) ? "expired" : "expires"} {sub.payment_method_exp}
                  </Mono>
                ) : null}
              </>
            ) : <Chip state="broken">None on file</Chip>}
          </Row>
          <Row k={days(sub.current_period_end) != null && days(sub.current_period_end) < 0 ? "Period ended" : "Renews"}>
            {sub.current_period_end ? new Date(sub.current_period_end).toLocaleDateString() : "Not started"}
          </Row>
          <Row k="Collected, lifetime"><Mono c={A.ink}>{dollars(sub.collected_cents)}</Mono></Row>
          <Row k="Mirrored">{sub.synced_at ? ago(sub.synced_at) : "never"}</Row>
        </>
      )}
    </Card>
  );
}

function InvoicesCard({ b }) {
  const rows = b.invoices;
  return (
    <Card title="Invoices" sub="Straight from Stripe. Amounts are what the customer was charged." pad={rows.length ? 0 : 16}>
      {rows.length === 0 ? (
        <Empty title="No invoices yet">{b.subscription ? "Nothing has been raised against this subscription." : "There is no Stripe customer to invoice."}</Empty>
      ) : rows.map((inv) => (
        <div key={inv.id} className="ac-row2" style={{ display: "flex", gap: 12, alignItems: "center", justifyContent: "space-between", padding: "11px 16px", borderTop: `1px solid ${A.lineSoft}` }}>
          <div style={{ minWidth: 0 }}>
            <Mono size={11.5} c={A.ink} style={{ display: "block" }}>{inv.number || inv.id}</Mono>
            <Mono size={10.5} c={A.mute}>{ago(inv.created_at)}{inv.attempt_count > 1 ? ` · ${inv.attempt_count} attempts` : ""}</Mono>
          </div>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexShrink: 0 }}>
            <Mono size={12.5} c={A.ink}>{dollars(inv.status === "paid" ? inv.amount_paid_cents : inv.amount_due_cents)}</Mono>
            <span style={{ width: 96, display: "flex", justifyContent: "flex-end" }}><Chip state={INV_TONE[inv.status]}>{titleCase(inv.status)}</Chip></span>
            {inv.hosted_invoice_url ? <a href={inv.hosted_invoice_url} target="_blank" rel="noopener noreferrer" className="ac-link" style={{ fontFamily: TYPE.text, fontSize: 11, color: A.body }}>View ↗</a> : null}
          </div>
        </div>
      ))}
    </Card>
  );
}

function EnforcedCard({ w, b, reference, onSaved }) {
  const e = b.enforced;
  const initial = { plan: e.plan, budget: e.token_budget_own ? String(e.token_budget ?? 0) : "", contact: e.billing_contact_email || "", po: e.po_reference || "" };
  const [f, setF] = useState(initial);
  const action = useAction();
  useEffect(() => { setF(initial); }, [b]); // eslint-disable-line react-hooks/exhaustive-deps
  const set = (k) => (ev) => setF({ ...f, [k]: ev.target.value });
  const plans = reference ? reference.plans : [];
  const tier = plans.find((p) => p.key === f.plan);
  const sub = b.subscription;
  const listCents = tier && tier.price_monthly != null ? tier.price_monthly * 100 : null;
  const differs = sub && sub.amount_cents != null && sub.interval === "month" && listCents != null && sub.amount_cents !== listCents;
  const dirty = Object.keys(initial).some((k) => initial[k] !== f[k]);

  async function save() {
    const body = {};
    if (f.plan !== initial.plan) body.plan = f.plan;
    if (f.budget !== initial.budget) {
      if (f.budget.trim() === "") body.token_budget_default = true;
      else body.token_budget = Math.max(0, parseInt(f.budget, 10) || 0);
    }
    if (f.contact !== initial.contact) body.billing_contact_email = f.contact;
    if (f.po !== initial.po) body.po_reference = f.po;
    const out = await action.run("save", () => api.patchBilling(w.slug, body), "Saved. The plan and budget apply on the workspace's next request; Stripe's charge is unchanged.");
    if (out) onSaved();
  }

  return (
    <Card title="What Axcion enforces"
      sub="Stripe decides whether they have paid. These decide what the workspace can do, on its next request. Changing the plan does not change what Stripe charges.">
      <div className="ac-form2">
        <Field label="Plan" htmlFor="bl-plan" wide
          note={differs ? `Stripe charges ${dollars(sub.amount_cents)} a month; ${tier.name} lists at ${listPrice(tier.price_monthly)}. Change the price in Stripe, not here.`
            : "Gates businesses, seats, sources, history and the portal. The one field here that changes what the workspace can do."}>
          <select id="bl-plan" value={f.plan} onChange={set("plan")} style={{ ...inputStyle, cursor: "pointer", borderColor: differs ? A.warn : A.line }}>
            {plans.map((p) => <option key={p.key} value={p.key}>{p.name} · {listPrice(p.price_monthly)}/month</option>)}
          </select>
        </Field>
        <Field label="AI token budget, per month" htmlFor="bl-budget"
          note={f.budget.trim() === "" ? `Blank follows the platform default (${e.token_budget_platform ? e.token_budget_platform.toLocaleString() : "unlimited"}).`
            : Number(f.budget) === 0 ? "Zero means unlimited, not no AI." : "Runs past the budget are skipped until the month rolls over."}>
          <input id="bl-budget" inputMode="numeric" value={f.budget} onChange={set("budget")} placeholder="Platform default"
            style={{ ...inputStyle, fontFamily: TYPE.data, fontVariantNumeric: "tabular-nums" }} />
        </Field>
        <Field label="Billing contact" htmlFor="bl-contact" note={sub ? "Where payment links go." : "Needs a Stripe customer first."}>
          <input id="bl-contact" type="email" value={f.contact} onChange={set("contact")} disabled={!sub} style={inputStyle} />
        </Field>
        <Field label="PO reference" htmlFor="bl-po" note={sub ? "Recorded for the customer's accounts team. Not sent to Stripe." : "Needs a Stripe customer first."}>
          <input id="bl-po" value={f.po} onChange={set("po")} disabled={!sub} style={inputStyle} />
        </Field>
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 14, flexWrap: "wrap" }}>
        <Btn kind="solid" small disabled={!dirty} busy={action.busy === "save"} onClick={save}>Save changes</Btn>
        {dirty ? <Btn kind="quiet" small onClick={() => setF(initial)}>Discard</Btn> : null}
      </div>
      {action.result ? <div style={{ marginTop: 10 }}><Notice tone={action.result.tone}>{action.result.text}</Notice></div> : null}
    </Card>
  );
}

export default function BillingPane({ w, reference, reload }) {
  const data = useApi(() => api.billing(w.slug), [w.slug]);
  const action = useAction();
  if (data.loading && !data.data) return <Loading label="Reading billing" />;
  if (data.error) return <LoadError error={data.error} onRetry={data.reload} />;

  const b = data.data;
  const sub = b.subscription;
  const ss = SUB_STATE[sub ? sub.status : "none"] || { label: titleCase(sub.status), tone: undefined };
  const [head, detail] = verdict(b, reference);
  const connected = b.billing.connected && b.billing.enabled;
  const openInvoice = b.invoices.some((i) => i.status === "open");
  const done = () => { data.reload(); reload(); };
  const run = async (key, fn, said) => { if (await action.run(key, fn, said)) done(); };

  return (
    <>
      <div style={{
        background: ss.tone ? STATE[ss.tone].bg : A.chip, border: `1px solid ${A.line}`, borderLeft: `3px solid ${ss.tone ? STATE[ss.tone].c : A.lineMid}`,
        borderRadius: 10, padding: 15, marginBottom: 16, boxShadow: A.lift,
      }}>
        <div style={{ display: "flex", gap: 14, justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap" }}>
          <div style={{ minWidth: 0, flex: "1 1 320px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontFamily: TYPE.text, fontSize: 10, fontWeight: 600, letterSpacing: ".14em", textTransform: "uppercase", color: ss.tone ? STATE[ss.tone].c : A.mute }}>Stripe subscription</span>
              <Chip state={ss.tone}>{ss.label}</Chip>
              {b.billing.livemode === false ? <Chip>Test mode</Chip> : null}
            </div>
            <div style={{ fontFamily: TYPE.text, fontSize: 13.5, fontWeight: 600, color: A.ink, marginTop: 7, lineHeight: 1.5, textWrap: "pretty" }}>{head}</div>
            <div style={{ fontFamily: TYPE.text, fontSize: 12, color: A.body, marginTop: 3, lineHeight: 1.6, textWrap: "pretty" }}>{detail}</div>
            {action.result ? <div style={{ marginTop: 10 }}><Notice tone={action.result.tone}>{action.result.text}</Notice></div> : null}
          </div>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", flexShrink: 0 }}>
            {sub && connected ? (
              <Btn small busy={action.busy === "sync"} onClick={() => run("sync", () => api.syncBilling(w.slug),
                (r) => (r.corrected.length ? `Corrected from Stripe: ${r.corrected.join(", ")}.` : "Already matched Stripe."))}>Sync from Stripe</Btn>
            ) : null}
            {sub && connected && openInvoice ? (
              <>
                <Btn small busy={action.busy === "retry"} onClick={() => run("retry", () => api.retryBilling(w.slug), (r) => `Stripe retried the charge: the invoice is now ${r.status}.`)}>Retry charge</Btn>
                <Btn small kind="primary" busy={action.busy === "link"} onClick={() => run("link", () => api.paymentLink(w.slug), (r) => `Stripe's payment page for ${r.invoice} went to ${r.sent_to}.`)}>Send payment link</Btn>
              </>
            ) : null}
            {!sub ? (
              <Btn small kind="primary" disabled={!connected} title={connected ? "Creates the customer and a subscription on this plan's price" : "Connect platform billing on the System page first"}
                busy={action.busy === "create"} onClick={() => run("create", () => api.createCustomer(w.slug, {}), "Created. Stripe raised the first invoice; send the payment link when the customer is ready.")}>
                Create Stripe customer
              </Btn>
            ) : null}
          </div>
        </div>
      </div>

      <div className="ac-split" style={{ marginBottom: 16 }}>
        <SubscriptionCard b={b} />
        <InvoicesCard b={b} />
      </div>
      <EnforcedCard w={w} b={b} reference={reference} onSaved={done} />
    </>
  );
}
