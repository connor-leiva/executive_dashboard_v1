import React, { useMemo, useState } from "react";
import { api } from "../api.js";
import { listPrice } from "../format.js";
import { Btn, Card, Chip, Eyebrow, Field, inputStyle, Loading, LoadError, Mono, Notice, Row, useApi } from "../primitives.jsx";
import { loadReference, planByKey } from "../reference.js";
import { A, TYPE } from "../tokens.js";

const SLUG = /^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$/;

const MODULE_NAMES = { books: "Books", flywheel: "Flywheel", binder: "Binder", ai_employees: "AI employees" };

function CopyLink({ url }) {
  const [copied, setCopied] = useState(false);
  return (
    <Btn small kind="primary" onClick={() => {
      navigator.clipboard?.writeText(url).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000); }, () => {});
    }}>{copied ? "Copied" : "Copy link"}</Btn>
  );
}

export default function ProvisionView({ onOpen }) {
  const reference = useApi(loadReference, []);
  const tenants = useApi(() => api.tenants(), []);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [email, setEmail] = useState("");
  /* Plan, not modules. Modules follow the tier; a per-workspace switch would be a setting the
     product ignores, because the API refuses a module the plan does not include. And the plan is
     required here even though the API once let it default: the default was `team`, which has no
     team portal, and a customer who bought the portal got a workspace without it. */
  const [plan, setPlan] = useState("");
  const [biz, setBiz] = useState([{ key: "main", name: "", tag: "Business" }]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(null);

  const auto = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "").slice(0, 24);
  const effective = slug || auto;
  const ref = reference.data;
  const domain = ref ? ref.platform_domain : "axcion.io";
  const taken = useMemo(() => (tenants.data ? tenants.data.tenants : []).find((w) => w.slug === effective), [tenants.data, effective]);

  if (reference.loading && !ref) return <Loading label="Reading plans" />;
  if (reference.error) return <LoadError error={reference.error} onRetry={reference.reload} />;

  const platformHosts = ref.reserved.platform_hosts;
  const wildcard = ref.reserved.wildcard_reserved;
  const slugErr = !effective ? null
    : platformHosts.includes(effective) ? `"${effective}" names a platform host and can never be a workspace.`
      : wildcard.includes(effective) ? `"${effective}" cannot be claimed through the wildcard. An operator can still point it at a workspace with an explicit domain row.`
        : taken ? `"${effective}" already belongs to ${taken.name}.`
          : effective.length < 3 ? "Three characters minimum."
            : !SLUG.test(effective) ? "Lowercase letters, digits and inner hyphens only."
              : null;
  const tier = planByKey(ref, plan);
  const bizOver = tier && tier.max_businesses != null && biz.length > tier.max_businesses;
  const bizIncomplete = biz.some((b) => !b.key.trim() || !b.name.trim());
  const ready = name.trim() && effective && !slugErr && tier && !bizOver && !bizIncomplete && /.+@.+\..+/.test(email.trim());

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.createTenant({
        slug: effective, name: name.trim(), owner_email: email.trim(), plan,
        businesses: biz.map((b) => ({ key: b.key.trim(), name: b.name.trim(), tag: b.tag.trim() || "Business" })),
      });
      setDone(r);
      tenants.reload();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <Card title="Workspace created" sub={`${name.trim()} is live at ${done.hostname} now. No DNS or deploy step.`}>
        <div style={{ padding: "14px 16px", background: A.cadetBg, border: `1px solid ${A.cadetLine}`, borderRadius: 9, borderLeft: `3px solid ${A.cadet}` }}>
          <Eyebrow style={{ color: A.cadetInk }}>The owner's invite</Eyebrow>
          <div style={{ marginTop: 8, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <Mono size={11.5} c={A.ink} style={{ background: A.paper, border: `1px solid ${A.line}`, borderRadius: 6, padding: "7px 10px", wordBreak: "break-all" }}>
              {done.invite_url}
            </Mono>
            <CopyLink url={done.invite_url} />
          </div>
          <div style={{ fontFamily: TYPE.text, fontSize: 11.5, color: A.body, marginTop: 10, lineHeight: 1.6, textWrap: "pretty" }}>
            Also emailed to <b style={{ fontWeight: 600 }}>{done.owner_email}</b>. Valid for 7 days, single use, and this
            is the only time the link can be read: afterwards it can only be reissued. Until it is accepted the workspace
            has no active owner and the fleet shows it as <b style={{ fontWeight: 600 }}>Stalled</b> after three days,
            which is how you will remember to chase it.
          </div>
        </div>
        <div style={{ marginTop: 14, display: "flex", gap: 6, flexWrap: "wrap" }}>
          <Btn kind="solid" onClick={() => { setDone(null); setName(""); setSlug(""); setEmail(""); setPlan(""); setBiz([{ key: "main", name: "", tag: "Business" }]); }}>Create another</Btn>
          <Btn onClick={() => onOpen(done.slug)}>Open the workspace</Btn>
        </div>
      </Card>
    );
  }

  return (
    <div className="ac-split">
      <Card title="New workspace" sub="Creates the tenant, its subdomain, its businesses and an invited owner in one step.">
        <div style={{ display: "grid", gap: 14 }}>
          <Field label="Company name" htmlFor="ac-name">
            <input id="ac-name" value={name} onChange={(e) => setName(e.target.value)} style={inputStyle} placeholder="Harbor Lane Group" />
          </Field>

          <Field label="Web address" htmlFor="ac-slug" error={slugErr}
            note={effective ? <>Their team will sign in at <Mono size={11} c={A.ink}>{effective}.{domain}</Mono>.</> : "Derived from the company name unless you set it. Permanent once anyone has signed in."}>
            <div style={{ display: "flex", alignItems: "stretch" }}>
              <input id="ac-slug" value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))}
                placeholder={auto || "harborlane"} aria-invalid={Boolean(slugErr)}
                style={{ ...inputStyle, borderRadius: "7px 0 0 7px", borderRight: "none", fontFamily: TYPE.data, borderColor: slugErr ? A.stop : A.line }} />
              <span style={{
                fontFamily: TYPE.data, fontSize: 12.5, color: A.mute, background: A.chip, display: "flex", alignItems: "center",
                border: `1px solid ${slugErr ? A.stop : A.line}`, borderRadius: "0 7px 7px 0", padding: "0 11px", whiteSpace: "nowrap",
              }}>.{domain}</span>
            </div>
          </Field>

          <Field label="Owner email" htmlFor="ac-email" note="They get the only owner seat and a 7-day invite link. You never set their password.">
            <input id="ac-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} style={inputStyle} placeholder="dana@harborlane.co" />
          </Field>

          <Field label="Plan" htmlFor="ac-plan"
            note="Required. A workspace created without a plan landed on Team, and a customer who bought the portal got a workspace without it.">
            <select id="ac-plan" value={plan} onChange={(e) => setPlan(e.target.value)} style={{ ...inputStyle, cursor: "pointer" }}>
              <option value="" disabled>Choose a plan</option>
              {ref.plans.map((p) => <option key={p.key} value={p.key}>{p.name} · {listPrice(p.price_monthly)}/mo</option>)}
            </select>
          </Field>

          {tier ? (
            <div style={{ padding: "11px 13px", background: A.ground, borderRadius: 8 }}>
              <Eyebrow>What {tier.name} includes</Eyebrow>
              <div style={{ marginTop: 6 }}>
                <Row k="Businesses · the meter">{tier.max_businesses == null ? "Unlimited" : tier.max_businesses}</Row>
                <Row k="Seats">{tier.max_users == null ? "Unlimited" : tier.max_users}</Row>
                <Row k="Modules">{tier.extra_tabs.length ? tier.extra_tabs.map((k) => MODULE_NAMES[k] || k).join(", ") : "None beyond Portfolio"}</Row>
                <Row k="Team portal">{tier.intranet ? "Included" : "Not included"}</Row>
                <Row k="AI assistant">{tier.ai_assistant ? "Included" : "Not included"}</Row>
                <Row k="History">{tier.history_months == null ? "Unlimited" : `${tier.history_months} months`}</Row>
              </div>
              <div style={{ fontFamily: TYPE.text, fontSize: 10.5, color: A.mute, marginTop: 9, lineHeight: 1.5, textWrap: "pretty" }}>
                Modules follow the tier. They are not per-workspace switches, and Portfolio is always on: a workspace
                without it has no product.
              </div>
            </div>
          ) : null}

          <div>
            <span style={{ display: "block", fontFamily: TYPE.text, fontSize: 11, fontWeight: 600, color: A.body, marginBottom: 6, letterSpacing: ".02em" }}>Businesses</span>
            {biz.map((b, i) => (
              <div key={i} style={{ display: "flex", gap: 6, marginBottom: 6, flexWrap: "wrap" }}>
                <input value={b.key} onChange={(e) => setBiz(biz.map((x, j) => (j === i ? { ...x, key: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "") } : x)))}
                  aria-label={`Business ${i + 1} key`} placeholder="key" style={{ ...inputStyle, width: 96, fontFamily: TYPE.data, fontSize: 12 }} />
                <input value={b.name} onChange={(e) => setBiz(biz.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))}
                  aria-label={`Business ${i + 1} name`} placeholder="Display name" style={{ ...inputStyle, flex: "1 1 130px", width: "auto" }} />
                <input value={b.tag} onChange={(e) => setBiz(biz.map((x, j) => (j === i ? { ...x, tag: e.target.value } : x)))}
                  aria-label={`Business ${i + 1} category`} placeholder="Real estate" style={{ ...inputStyle, width: 120 }} />
                {biz.length > 1 ? <Btn small kind="quiet" onClick={() => setBiz(biz.filter((_, j) => j !== i))}>Remove</Btn> : null}
              </div>
            ))}
            <Btn small onClick={() => setBiz([...biz, { key: "", name: "", tag: "Business" }])}
              disabled={Boolean(tier && tier.max_businesses != null && biz.length >= tier.max_businesses)}>Add a business</Btn>
            <div style={{ fontFamily: TYPE.text, fontSize: 11, color: bizOver ? A.stop : A.mute, marginTop: 7, lineHeight: 1.55, textWrap: "pretty" }}>
              {bizOver
                ? `${tier.name} allows ${tier.max_businesses}. Remove one or choose a larger plan.`
                : `Each business becomes a tab and a QuickBooks connection point.${tier ? (tier.max_businesses == null ? " This plan sets no limit." : ` ${tier.name} allows ${tier.max_businesses}.`) : ""}`}
            </div>
          </div>

          {error ? <Notice tone="error">{error}</Notice> : null}
          <div style={{ display: "flex", gap: 8, alignItems: "center", paddingTop: 6, flexWrap: "wrap" }}>
            <Btn kind="primary" disabled={!ready} busy={busy} onClick={create}>Create workspace</Btn>
            {!ready ? (
              <span style={{ fontFamily: TYPE.text, fontSize: 11.5, color: A.mute }}>
                {bizOver ? "Too many businesses for this plan." : "A name, a free address, a valid owner email, a plan, and a key and name for each business."}
              </span>
            ) : null}
          </div>
        </div>
      </Card>

      <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
        <Card title="What this does" sub="So there is no gap between the button and the script it replaces.">
          {[
            ["Creates the tenant row", "One row. Everything else in the platform is scoped to its id."],
            ["Sets the plan", `${tier ? tier.name : "The chosen plan"}, validated against the plan table, so a typo is a 400 rather than a workspace on a tier that does not exist.`],
            ["Claims the hostname", `${effective || "slug"}.${domain} resolves on the next request; the wildcard DNS record already covers it.`],
            ["Seeds the businesses", "Each gets a key, a display name and a tab. More than the plan allows is refused."],
            ["Bootstraps the workspace", "Roles, capabilities and the owner membership. A workspace nobody can administer is not provisioned."],
            ["Invites the owner", "Status invited, no password, a one-time token that expires in 7 days."],
            ["Writes the audit entry", "In the workspace's own log, naming you and the plan."],
          ].map(([k, v], i) => (
            <div key={k} style={{ display: "flex", gap: 11, padding: "9px 0", borderTop: i ? `1px solid ${A.lineSoft}` : "none" }}>
              <Mono size={11} c={A.mute} style={{ flexShrink: 0, paddingTop: 1 }}>{String(i + 1).padStart(2, "0")}</Mono>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontFamily: TYPE.text, fontSize: 12.5, color: A.ink, fontWeight: 500 }}>{k}</div>
                <div style={{ fontFamily: TYPE.text, fontSize: 11.5, color: A.mute, marginTop: 2, lineHeight: 1.55, textWrap: "pretty" }}>{v}</div>
              </div>
            </div>
          ))}
        </Card>

        <Card title="Reserved addresses" sub="Two classes, and the difference matters when a customer asks for one.">
          <Eyebrow>Platform hosts · never a workspace</Eyebrow>
          <div style={{ display: "flex", gap: 5, flexWrap: "wrap", margin: "7px 0 14px" }}>
            {platformHosts.map((r) => <Chip key={r} mono>{r}</Chip>)}
          </div>
          <Eyebrow>Not claimable by slug · assignable by an operator</Eyebrow>
          <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginTop: 7 }}>
            {wildcard.map((r) => <Chip key={r} mono>{r}</Chip>)}
          </div>
          <div style={{ marginTop: 13, padding: "10px 12px", background: A.ground, borderRadius: 8, fontFamily: TYPE.text, fontSize: 11.5, color: A.body, lineHeight: 1.6, textWrap: "pretty" }}>
            <b style={{ fontWeight: 600 }}>www is in the second set, not the first.</b> It was once treated as a platform host, which made
            every API call from the real site fail tenant resolution and took production down. A name in the first set is
            unreachable by design, which is only right for names nobody would serve an app from.
          </div>
        </Card>
      </div>
    </div>
  );
}
