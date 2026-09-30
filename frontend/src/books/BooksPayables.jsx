/* Books · Payables — AP intake through release: vendors, bills, approvals, runs.

   Four views, not four sub-tabs. Books already carries seven sub-tabs and an eighth costs more
   than it returns, so the segmented control lives inside the page.

   Naming note: this page never says "queue." Books · Queue is the CATEGORIZATION queue — money
   that already cleared, reviewed after the fact. These are bills that have NOT been paid. Two
   things called a queue that mean opposite things is how somebody approves the wrong one.

   Every disable reads `holds` / `can_submit` off the row. The server decides what blocks a
   payment; scattering those conditions through JSX is how one screen ends up enforcing a rule
   another screen forgot, and the forgotten one pays somebody nobody verified. */
import { useState } from "react";
import { postJSON, putJSON, setStepUp, STEP_UP_STATUS, getBlob, uploadFile } from "../api";
import { usePayables, useVendors, useRuns, useNextRun, useRun, useCoaEntities,
         useCoaMapping, usePolicies, usePayablesEmail } from "./useBooks.js";
import { Card, Eyebrow, Pill, StatePanel, Field, font, usd, T } from "./ui.jsx";

const VIEWS = [["inbox", "Inbox"], ["approvals", "Approvals"], ["runs", "Runs"],
               ["vendors", "Vendors"]];

/* Statuses a bill passes through before anybody is asked to approve it. */
const OPEN_STATES = ["received", "extracted", "coded", "needs_info"];

const STATUS_LABEL = {
  received: "Received", extracted: "Extracted", coded: "Coded", needs_info: "Needs info",
  awaiting_approval: "Awaiting approval", approved: "Approved", rejected: "Rejected",
  scheduled: "Scheduled", released: "Released", reconciled: "Reconciled", on_hold: "On hold",
};

const fmtLong = (iso) => {
  if (!iso) return "—";
  // Parsed LOCAL on purpose: a run date is a calendar day, not an instant, and must not slip a
  // day west. Guarded the same way as fmtDate — only a bare date gets pinned.
  const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T00:00:00` : iso);
  return Number.isNaN(d.getTime()) ? "—"
    : d.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
};

const fmtDate = (iso) => {
  if (!iso) return "—";
  // `new Date("2026-10-05")` is parsed as UTC MIDNIGHT and then rendered in local time, so west
  // of UTC every due date displayed a day early — Oct 5 as "Oct 4". On this screen the due date
  // is what decides which week a bill is paid in, so a DATE is pinned to the local day.
  //
  // Only a date. This helper is also handed real timestamps (a vendor's verified_at), and
  // `"2026-09-25T17:52:20+00:00" + "T00:00:00"` parses as NaN — which rendered every bank
  // verification date on the Vendors screen as an em dash.
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(iso);
  const d = new Date(dateOnly ? `${iso}T00:00:00` : iso);
  return Number.isNaN(d.getTime()) ? "—"
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

async function mutate(path, body) { await postJSON(path, body || {}); }

function btn(kind) {
  const base = { fontFamily: font.head, fontSize: 12, fontWeight: 600, borderRadius: 8,
                 padding: "6px 13px", cursor: "pointer", whiteSpace: "nowrap" };
  if (kind === "primary") return { ...base, color: T.white, background: T.meadow, border: "none" };
  if (kind === "danger") return { ...base, color: T.poppyText, background: T.white, border: `1px solid ${T.line}` };
  if (kind === "disabled") return { ...base, color: T.muted, background: T.parchment,
                                    border: `1px solid ${T.line}`, cursor: "not-allowed" };
  return { ...base, color: T.slate, background: T.white, border: `1px solid ${T.line}` };
}

function SegNav({ view, setView, counts }) {
  return (
    <div style={{ display: "inline-flex", gap: 2, background: T.parchment, border: `1px solid ${T.line}`,
                  borderRadius: 10, padding: 3, marginBottom: 18 }}>
      {VIEWS.map(([k, l]) => (
        <button key={k} onClick={() => setView(k)} style={{ fontFamily: font.head, fontSize: 12.5,
          fontWeight: 600, borderRadius: 8, border: "none", padding: "7px 15px", cursor: "pointer",
          color: view === k ? T.ink : T.muted, background: view === k ? T.white : "transparent" }}>
          {l}{counts[k] ? ` · ${counts[k]}` : ""}
        </button>
      ))}
    </div>
  );
}

function StatCard({ label, value, color, scope }) {
  return (
    <Card style={{ padding: "15px 17px" }}>
      <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.secondary, marginBottom: 7 }}>
        {label}{scope && <span style={{ color: T.muted }}> · {scope}</span>}
      </div>
      <div style={{ fontFamily: font.head, fontSize: 23, fontWeight: 700, color: color || T.ink,
                    fontVariantNumeric: "tabular-nums" }}>{value}</div>
    </Card>
  );
}

const Stats = ({ children }) => (
  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(168px,1fr))",
                gap: 12, marginBottom: 18 }}>{children}</div>
);

/* A hold is not a warning. It stops the row being actionable, and the button reads the same
   list the chip does rather than keeping its own opinion. */
const HoldChips = ({ holds }) => !holds?.length ? null : (
  <span style={{ display: "inline-flex", gap: 5, flexWrap: "wrap" }}>
    {holds.map((h) => <Pill key={h.key} tone={h.hold ? "bad" : "warn"}>{h.label}</Pill>)}
  </span>
);

const ROW = "80px minmax(140px,1.4fr) 84px 96px minmax(120px,1fr) minmax(150px,1.2fr)";

function Header({ cols }) {
  return (
    <div className="pay-head" style={{ display: "grid", gridTemplateColumns: ROW, columnGap: 12,
      padding: "11px 16px", borderBottom: `1px solid ${T.line}`, background: T.parchment }}>
      {cols.map((h, i) => (
        <span key={h} style={{ fontFamily: font.head, fontSize: 10.5, fontWeight: 700,
          letterSpacing: "0.08em", textTransform: "uppercase", color: T.muted,
          textAlign: i === 3 ? "right" : "left" }}>{h}</span>
      ))}
    </div>
  );
}

function BillRow({ p, open, toggle, children, right }) {
  return (
    <div style={{ borderBottom: `1px solid ${T.line}` }}>
      <div className="pay-row" onClick={toggle} style={{ display: "grid",
        gridTemplateColumns: ROW, columnGap: 12,
        alignItems: "center", padding: "12px 16px", cursor: "pointer" }}>
        <span style={{ fontFamily: font.body, fontSize: 12, color: T.muted }}>
          {fmtDate(p.due_date)}</span>
        <span style={{ minWidth: 0, fontFamily: font.body, fontSize: 12.5, fontWeight: 600,
          color: T.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {p.vendor || "—"}</span>
        <span style={{ fontFamily: font.body, fontSize: 12, color: T.secondary, minWidth: 0,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {p.invoice_number}</span>
        <span style={{ fontFamily: font.head, fontSize: 13, fontWeight: 600, color: T.ink,
          textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{usd(p.amount)}</span>
        <span style={{ minWidth: 0, display: "flex", alignItems: "center", gap: 6 }}>
          {p.holds?.length ? <HoldChips holds={p.holds} />
            : <span style={{ fontFamily: font.body, fontSize: 11.5, color: T.secondary }}>
                {STATUS_LABEL[p.status] || p.status}</span>}
        </span>
        <span style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>{right}</span>
      </div>
      {open && (
        <div style={{ padding: "2px 16px 16px 96px", display: "grid", gap: 12 }}>
          <div style={{ display: "grid", gap: 5 }}>
            <Field label="Description" value={p.description} />
            <Field label="Entity" value={p.business_name} />
            <Field label="Invoice date" value={fmtDate(p.invoice_date)} />
            <Field label="Approval band" value={p.band} />
            <Field label="Status" value={STATUS_LABEL[p.status] || p.status} />
          </div>
          {p.holds?.map((h) => (
            <div key={h.key} style={{ background: T.parchment, border: `1px solid ${T.line}`,
              borderRadius: 10, padding: "10px 13px" }}>
              <div style={{ fontFamily: font.head, fontSize: 12.5, fontWeight: 700,
                color: T.poppyText }}>{h.label}</div>
              <div style={{ fontFamily: font.body, fontSize: 12, color: T.secondary, marginTop: 3 }}>
                {h.why}</div>
            </div>
          ))}
          {children}
        </div>
      )}
    </div>
  );
}

/* ── entry forms ───────────────────────────────────────────────────────────────────────────

   Phase 1's acceptance was "a vendor can be created, a W-9 uploaded, banking added with a
   logged callback, and status flips to active on its own". The views rendered all of that and
   offered no way to DO any of it: the module was readable and unusable, and the first person to
   open Vendors on a real workspace had a table, four zeroes and no button.

   Deliberately inline panels rather than modals. Books has no modal anywhere, and a dialog
   system introduced for one form is a second set of focus, escape and scroll rules to keep
   right. */

const input = {
  width: "100%", boxSizing: "border-box",          // no global border-box reset in this app
  fontFamily: font.body, fontSize: 12.5, padding: "7px 9px", color: T.ink,
  background: T.white, border: `1px solid ${T.line}`, borderRadius: 8,
};

function FieldRow({ label, hint, children, span }) {
  return (
    <label style={{ display: "grid", gap: 4, minWidth: 0, gridColumn: span ? "1 / -1" : "auto" }}>
      <span style={{ fontFamily: font.head, fontSize: 10.5, fontWeight: 700,
        letterSpacing: "0.08em", textTransform: "uppercase", color: T.muted }}>{label}</span>
      {children}
      {hint && <span style={{ fontFamily: font.body, fontSize: 11, color: T.muted }}>{hint}</span>}
    </label>
  );
}

const Grid = ({ children }) => (
  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(190px,1fr))",
                gap: 12, marginBottom: 12 }}>{children}</div>
);

function FormPanel({ title, blurb, onCancel, onSave, saving, saveLabel, disabled, err, children }) {
  return (
    <Card style={{ padding: "16px 18px", marginBottom: 14 }}>
      <Eyebrow>{title}</Eyebrow>
      {blurb && (
        <div style={{ fontFamily: font.body, fontSize: 12, color: T.secondary, margin: "6px 0 12px",
                      lineHeight: 1.6, maxWidth: 620 }}>{blurb}</div>
      )}
      {children}
      {err && (
        <div style={{ fontFamily: font.body, fontSize: 12, color: T.poppyText, marginBottom: 10 }}>
          {err}</div>
      )}
      <div style={{ display: "flex", gap: 8 }}>
        <button disabled={saving || disabled} style={btn(saving || disabled ? "disabled" : "primary")}
          onClick={onSave}>{saving ? "Saving…" : (saveLabel || "Save")}</button>
        <button disabled={saving} style={btn()} onClick={onCancel}>Cancel</button>
      </div>
    </Card>
  );
}

/* A vendor is created in a state that cannot be paid. That is the point: `status` is derived
   from the W-9 and a verified bank record, never typed, so this form deliberately has no
   status field to set. */
function NewVendorForm({ onDone, onCancel }) {
  const [f, setF] = useState({ legal_name: "", display_name: "", dba: "",
    vendor_type: "business", terms_days: 30, tin_last4: "", is_1099: false, notes: "" });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState(null);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });

  const save = async () => {
    setSaving(true); setErr(null);
    try {
      const body = { ...f, terms_days: Number(f.terms_days) || 30 };
      for (const k of ["display_name", "dba", "tin_last4", "notes"]) if (!body[k]) body[k] = null;
      const v = await postJSON(`/payables/vendors`, body);
      onDone(v);
    } catch (e) { setErr(e?.detail || e?.message || "That vendor could not be created."); }
    finally { setSaving(false); }
  };

  return (
    <FormPanel title="New vendor" onCancel={onCancel} onSave={save} saving={saving}
      saveLabel="Create vendor" disabled={!f.legal_name.trim()} err={err}
      blurb="Legal name is what appears on the payment file, so it has to match the W-9 rather
             than what you call them. Everything else can be filled in later — the vendor cannot
             be paid until a W-9 and verified banking are on file either way.">
      <Grid>
        <FieldRow label="Legal name">
          <input style={input} value={f.legal_name} onChange={set("legal_name")}
            placeholder="Acme Landscaping LLC" /></FieldRow>
        <FieldRow label="Display name" hint="Optional — what you call them">
          <input style={input} value={f.display_name} onChange={set("display_name")} /></FieldRow>
        <FieldRow label="DBA"><input style={input} value={f.dba} onChange={set("dba")} /></FieldRow>
        <FieldRow label="Type">
          <select style={input} value={f.vendor_type} onChange={set("vendor_type")}>
            <option value="business">Business</option>
            <option value="individual">Individual</option>
          </select></FieldRow>
        <FieldRow label="Terms (days)" hint="Drives the due date on every bill">
          <input style={input} type="number" min="0" value={f.terms_days}
            onChange={set("terms_days")} /></FieldRow>
        {/* Last four only. Full TINs are never stored here — the W-9 itself is the record. */}
        <FieldRow label="TIN last 4" hint="Last four only; the full number is never stored">
          <input style={input} maxLength={4} value={f.tin_last4} onChange={set("tin_last4")} /></FieldRow>
        <FieldRow label="1099">
          <label style={{ display: "flex", alignItems: "center", gap: 7, fontFamily: font.body,
                          fontSize: 12.5, color: T.ink, padding: "7px 0" }}>
            <input type="checkbox" checked={f.is_1099} onChange={set("is_1099")} />
            Issue a 1099 to this vendor
          </label></FieldRow>
        <FieldRow label="Notes" span>
          <input style={input} value={f.notes} onChange={set("notes")} /></FieldRow>
      </Grid>
    </FormPanel>
  );
}

/* W-9 and banking for one vendor. Both are what turn `pending_verification` into `active`, so
   they live together on the row rather than behind separate screens. */
function VendorSetup({ v, onDone }) {
  const [bank, setBank] = useState({ routing_last4: "", account_last4: "", verified: true,
    verification_method: "callback", verification_note: "" });
  const [busy, setBusy] = useState(null);
  const [err, setErr] = useState(null);
  const set = (k) => (e) => setBank({ ...bank, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });

  const upload = async (file) => {
    if (!file) return;
    setBusy("w9"); setErr(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      await uploadFile(`/payables/vendors/${v.id}/documents`, fd);
      onDone();
    } catch (e) { setErr(e?.detail || e?.message || "That file could not be attached."); }
    finally { setBusy(null); }
  };

  const saveBank = async () => {
    setBusy("bank"); setErr(null);
    try {
      await postJSON(`/payables/vendors/${v.id}/bank`, {
        ...bank, verification_note: bank.verification_note || null });
      onDone();
    } catch (e) { setErr(e?.detail || e?.message || "That bank record could not be saved."); }
    finally { setBusy(null); }
  };

  return (
    <div style={{ padding: "4px 16px 18px", display: "grid", gap: 16 }}>
      <div>
        <Eyebrow>W-9</Eyebrow>
        <div style={{ fontFamily: font.body, fontSize: 12, color: T.secondary, margin: "5px 0 8px" }}>
          {v.w9 ? "On file." : "Required before a first payment. Filed with Payables, not in the Binder."}
        </div>
        {!v.w9 && (
          <input type="file" disabled={busy === "w9"} onChange={(e) => upload(e.target.files?.[0])}
            style={{ fontFamily: font.body, fontSize: 12, color: T.secondary }} />
        )}
      </div>

      <div>
        <Eyebrow>Banking</Eyebrow>
        {/* The control is the CALLBACK, not the digits. Adding a record never overwrites the
            previous one — the old row is superseded, so where money used to go stays readable —
            and a brand-new record holds payment for the cooldown window on purpose. */}
        <div style={{ fontFamily: font.body, fontSize: 12, color: T.secondary, margin: "5px 0 10px",
                      lineHeight: 1.6, maxWidth: 620 }}>
          {v.bank?.verified_at
            ? `Verified ${v.bank.verified_by_name ? `by ${v.bank.verified_by_name} ` : ""}on ${fmtDate(v.bank.verified_at)}. Adding a new record supersedes it rather than overwriting it, and holds payment for the cooldown window.`
            : "Call the vendor back on a number you already had — never one from the invoice — and record it here. The callback is the control; the last four digits are not."}
        </div>
        <Grid>
          <FieldRow label="Routing last 4">
            <input style={input} maxLength={4} value={bank.routing_last4}
              onChange={set("routing_last4")} /></FieldRow>
          <FieldRow label="Account last 4">
            <input style={input} maxLength={4} value={bank.account_last4}
              onChange={set("account_last4")} /></FieldRow>
          <FieldRow label="How it was verified">
            <select style={input} value={bank.verification_method} onChange={set("verification_method")}>
              <option value="callback">Callback to a known number</option>
              <option value="in_person">In person</option>
              <option value="portal">Vendor portal</option>
            </select></FieldRow>
          <FieldRow label="Verified">
            <label style={{ display: "flex", alignItems: "center", gap: 7, fontFamily: font.body,
                            fontSize: 12.5, color: T.ink, padding: "7px 0" }}>
              <input type="checkbox" checked={bank.verified} onChange={set("verified")} />
              I confirmed these details with the vendor
            </label></FieldRow>
          <FieldRow label="Note" span hint="Who you spoke to, and on what number">
            <input style={input} value={bank.verification_note}
              onChange={set("verification_note")} /></FieldRow>
        </Grid>
        {err && <div style={{ fontFamily: font.body, fontSize: 12, color: T.poppyText,
                              marginBottom: 10 }}>{err}</div>}
        <button disabled={!!busy || !bank.routing_last4 || !bank.account_last4}
          style={btn(busy || !bank.routing_last4 || !bank.account_last4 ? "disabled" : "primary")}
          onClick={saveBank}>
          {busy === "bank" ? "Saving…" : v.bank ? "Replace banking" : "Add banking"}</button>
      </div>
    </div>
  );
}

/* A bill needs a vendor, an amount, an invoice number and an account before anybody can be
   asked to approve it — `coded` is the state that submit requires, and the account is what
   reaches it. Entity first, because it decides which chart of accounts applies. */
function NewBillForm({ vendors, onDone, onCancel }) {
  const entities = useCoaEntities();
  const list = entities.data?.entities || [];
  const [f, setF] = useState({ vendor_id: "", invoice_number: "", amount: "",
    invoice_date: "", due_date: "", description: "", business_id: "", standard_account_id: "" });
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState(null);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  const mapping = useCoaMapping(f.business_id || null);
  const accounts = (mapping.data?.standard || []).filter((a) => a.is_active !== false);

  const save = async () => {
    setSaving(true); setErr(null);
    try {
      await postJSON(`/payables`, {
        vendor_id: f.vendor_id,
        invoice_number: f.invoice_number.trim(),
        amount: Number(f.amount),
        invoice_date: f.invoice_date || null,
        due_date: f.due_date || null,
        description: f.description || null,
        business_id: f.business_id || null,
        standard_account_id: f.standard_account_id || null,
      });
      onDone();
    } catch (e) { setErr(e?.detail || e?.message || "That bill could not be entered."); }
    finally { setSaving(false); }
  };

  const ready = f.vendor_id && f.invoice_number.trim() && Number(f.amount) > 0;
  return (
    <FormPanel title="New bill" onCancel={onCancel} onSave={save} saving={saving}
      saveLabel="Enter bill" disabled={!ready} err={err}
      blurb="Leave the due date blank to take it from the vendor's terms — an override is a
             deliberate decision about cash, and is recorded as one. A bill needs an account
             before it can go for approval.">
      <Grid>
        <FieldRow label="Vendor">
          <select style={input} value={f.vendor_id} onChange={set("vendor_id")}>
            <option value="">Choose a vendor…</option>
            {vendors.map((v) => (
              <option key={v.id} value={v.id}>
                {v.display_name}{v.status === "active" ? "" : " (not payable yet)"}</option>
            ))}
          </select></FieldRow>
        <FieldRow label="Invoice number">
          <input style={input} value={f.invoice_number} onChange={set("invoice_number")} /></FieldRow>
        <FieldRow label="Amount">
          <input style={input} type="number" step="0.01" min="0" value={f.amount}
            onChange={set("amount")} /></FieldRow>
        <FieldRow label="Invoice date">
          <input style={input} type="date" value={f.invoice_date}
            onChange={set("invoice_date")} /></FieldRow>
        <FieldRow label="Due date" hint="Blank = from the vendor's terms">
          <input style={input} type="date" value={f.due_date} onChange={set("due_date")} /></FieldRow>
        <FieldRow label="Entity">
          <select style={input} value={f.business_id}
            onChange={(e) => setF({ ...f, business_id: e.target.value, standard_account_id: "" })}>
            <option value="">Choose an entity…</option>
            {list.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select></FieldRow>
        <FieldRow label="Account"
          hint={!f.business_id ? "Choose an entity first" : mapping.loading ? "Loading the chart…" : null}>
          <select style={input} value={f.standard_account_id} onChange={set("standard_account_id")}
            disabled={!f.business_id || mapping.loading}>
            <option value="">{f.business_id ? "Choose an account…" : "—"}</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>{a.code ? `${a.code} · ` : ""}{a.name}</option>
            ))}
          </select></FieldRow>
        <FieldRow label="Description" span>
          <input style={input} value={f.description} onChange={set("description")}
            placeholder="What this covers" /></FieldRow>
      </Grid>
    </FormPanel>
  );
}

/* ── Inbox: bills that have arrived and not yet gone for approval ─────────────────────── */
/* ── how an invoice gets in ─────────────────────────────────────────────────────────────────

   Two doors onto the same pipeline: drop a PDF here, or forward one to the workspace's own AP
   address. Either way what lands is a DOCUMENT and a queued reading — never a bill. The clerk
   proposes and a person accepts, so nothing on this panel can move money.

   Payables has its own address rather than sharing the Binder's. They carry different things to
   different people: a supplier invoice, forwarded by whoever happens to bill you, and the legal
   record of the entities, read behind a second factor. */

/* What happened to the thing you just dropped, in a sentence. Every branch says "Filed" first,
   because it was — the reasons below are about the READING, and none of them lose the document. */
const INTAKE_SAID = {
  duplicate: "Already on file — the same document came in before.",
  ai_disabled: "Filed. AI Employees is switched off for this workspace, so nothing read it.",
  no_clerk: "Filed. Nobody here does accounts payable yet — add an AP Clerk under AI Employees.",
  skill_off: "Filed. The clerk's invoice-reading skill is switched off.",
  daily_cap: "Filed. Today's reading limit is reached — it can still be read by hand.",
};

function intakeSaid(r) {
  if (!r) return null;
  const i = r.intake || {};
  if (i.queued) {
    return i.status === "skipped_budget"
      ? "Filed. This month's AI budget is spent, so the reading is queued but will not run."
      : `Filed. ${i.employee || "The clerk"} is reading it — the proposal appears under AI Employees.`;
  }
  return INTAKE_SAID[i.reason] || "Filed.";
}

function ForwardingAddress({ onClose }) {
  const { data, loading, error, retry, refresh } = usePayablesEmail();
  const [local, setLocal] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [copied, setCopied] = useState(false);
  const cfg = data || {};
  const draft = local === null ? (cfg.local_part || "") : local;
  const dirty = local !== null && local !== cfg.local_part;

  const save = async (patch) => {
    setBusy(true); setErr(null);
    try {
      await putJSON(`/payables/email`, patch);
      setLocal(null);
      refresh();
    } catch (e) { setErr(e?.detail || e?.message || "That could not be saved."); }
    finally { setBusy(false); }
  };

  const copy = () => {
    navigator.clipboard?.writeText(cfg.address || "").then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    }).catch(() => {});
  };

  return (
    <Card style={{ padding: "15px 16px", marginBottom: 12 }}>
      <StatePanel loading={loading} error={error} retry={retry}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
          <Eyebrow>Forwarding address</Eyebrow>
          <Pill tone={cfg.enabled ? "good" : "muted"}>{cfg.enabled ? "Open" : "Closed"}</Pill>
          <button style={{ ...btn(), marginLeft: "auto" }} onClick={onClose}>Done</button>
        </div>
        <div style={{ fontFamily: font.body, fontSize: 12.5, color: T.secondary,
                      margin: "7px 0 11px", maxWidth: 620, lineHeight: 1.5 }}>
          Forward supplier invoices here and each attachment is filed and read. Point your existing
          AP inbox at this address — you do not have to change what your vendors send to.
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
                      marginBottom: 11 }}>
          <code style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                         fontSize: 12.5, background: T.parchment, padding: "6px 9px",
                         borderRadius: 6, wordBreak: "break-all" }}>{cfg.address}</code>
          <button style={btn()} onClick={copy}>{copied ? "Copied" : "Copy"}</button>
        </div>
        {!cfg.channel_open && (
          <div style={{ fontFamily: font.body, fontSize: 12, color: T.poppyText,
                        marginBottom: 11, maxWidth: 620, lineHeight: 1.5 }}>
            The platform side of this channel is not set up yet: mail sent to the address above is
            turned away, and the sender is not told. It needs an inbound route at the mail provider
            and an ingest secret on the API before anything forwarded here arrives.
          </div>
        )}
        {cfg.can_manage ? (
          <div style={{ display: "flex", alignItems: "flex-end", gap: 8, flexWrap: "wrap" }}>
            <label style={{ display: "grid", gap: 4 }}>
              <span style={{ fontFamily: font.body, fontSize: 11, color: T.secondary }}>
                Name before the @</span>
              <input value={draft} onChange={(e) => setLocal(e.target.value)}
                style={{ ...input, width: 190 }} spellCheck={false} />
            </label>
            <button disabled={busy || !dirty}
              style={btn(busy || !dirty ? "disabled" : "primary")}
              onClick={() => save({ local_part: draft })}>
              {busy ? "Saving…" : "Rename"}</button>
            <button disabled={busy} style={btn(busy ? "disabled" : undefined)}
              onClick={() => save({ enabled: !cfg.enabled })}>
              {cfg.enabled ? "Close the address" : "Open the address"}</button>
          </div>
        ) : (
          <div style={{ fontFamily: font.body, fontSize: 12, color: T.muted }}>
            An owner or an admin can change this.
          </div>
        )}
        {err && (
          <div style={{ fontFamily: font.body, fontSize: 12.5, color: T.poppyText, marginTop: 9 }}>
            {err}</div>
        )}
        <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted, marginTop: 11,
                      maxWidth: 620, lineHeight: 1.5 }}>
          Anyone can email an address they know, so what arrives is only ever a filed document and
          a draft. A bill exists when a person accepts one.
        </div>
      </StatePanel>
    </Card>
  );
}

function InboxView() {
  const [open, setOpen] = useState(null);
  const [busy, setBusy] = useState(null);
  const [err, setErr] = useState(null);
  const [adding, setAdding] = useState(false);
  const [channel, setChannel] = useState(false);
  const [dropped, setDropped] = useState(null);
  const { data, loading, error, retry, refresh } = usePayables({});
  // Every vendor, not just the payable ones: a bill can be ENTERED against a vendor who is not
  // verified yet — it simply cannot be submitted. Hiding them here would make the gate look
  // like a missing vendor.
  const vendorList = useVendors(null).data?.vendors || [];
  const rows = (data?.payables || []).filter((p) => OPEN_STATES.includes(p.status));
  const ready = rows.filter((p) => p.can_submit).length;

  const submit = async (p) => {
    setBusy(p.id); setErr(null);
    try { await mutate(`/payables/${p.id}/submit`); refresh(); }
    catch (e) { setErr(e?.message || "Could not submit that invoice."); }
    finally { setBusy(null); }
  };

  const drop = async (file) => {
    if (!file) return;
    setBusy("upload"); setErr(null); setDropped(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      setDropped(await uploadFile(`/payables/upload`, fd));
    } catch (e) { setErr(e?.detail || e?.message || "That file could not be filed."); }
    finally { setBusy(null); }
  };

  return (
    <StatePanel loading={loading} error={error} retry={retry}>
      <Stats>
        <StatCard label="In inbox" value={rows.length} />
        <StatCard label="Ready to submit" value={ready} color={T.meadowInk} />
        <StatCard label="Held" value={rows.length - ready}
          color={rows.length - ready ? T.poppyText : T.ink} />
      </Stats>
      {err && (
        <Card style={{ padding: "11px 15px", marginBottom: 12 }}>
          <span style={{ fontFamily: font.body, fontSize: 12.5, color: T.poppyText }}>{err}</span>
        </Card>
      )}
      {channel && <ForwardingAddress onClose={() => setChannel(false)} />}
      {dropped && (
        <Card style={{ padding: "11px 15px", marginBottom: 12 }}>
          <span style={{ fontFamily: font.body, fontSize: 12.5, color: T.secondary }}>
            <strong style={{ color: T.ink }}>{dropped.filename}</strong> — {intakeSaid(dropped)}
          </span>
        </Card>
      )}
      {adding
        ? <NewBillForm vendors={vendorList} onCancel={() => setAdding(false)}
            onDone={() => { setAdding(false); refresh(); }} />
        : <div style={{ display: "flex", gap: 6, marginBottom: 12, flexWrap: "wrap",
                        alignItems: "center" }}>
            <button style={btn("primary")} onClick={() => setAdding(true)}>New bill</button>
            {/* A label, not a button: the file input itself is the control, and hiding it behind
                a styled label is the only way to make it look like the buttons beside it. */}
            <label style={{ ...btn(), cursor: busy === "upload" ? "default" : "pointer" }}>
              {busy === "upload" ? "Filing…" : "Upload an invoice"}
              <input type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.txt"
                disabled={busy === "upload"} style={{ display: "none" }}
                onChange={(e) => { drop(e.target.files?.[0]); e.target.value = ""; }} />
            </label>
            {!channel && (
              <button style={btn()} onClick={() => setChannel(true)}>Forwarding address</button>
            )}
          </div>}
      <Card style={{ padding: 0, overflow: "hidden" }}>
        <Header cols={["Due", "Vendor", "Invoice", "Amount", "Status", ""]} />
        {rows.length === 0 && (
          <div style={{ padding: "26px 16px", fontFamily: font.body, fontSize: 12.5, color: T.secondary }}>
            Nothing waiting. Bills appear here once entered, and go for approval when coded
            against an active vendor.
          </div>
        )}
        {rows.map((p) => (
          <BillRow key={p.id} p={p} open={open === p.id}
            toggle={() => setOpen(open === p.id ? null : p.id)}
            right={
              <button disabled={!p.can_submit || busy === p.id}
                style={btn(p.can_submit && busy !== p.id ? "primary" : "disabled")}
                title={p.can_submit ? "Send for approval" : p.holds?.map((h) => h.label).join(" · ")}
                onClick={(e) => { e.stopPropagation(); if (p.can_submit) submit(p); }}>
                {busy === p.id ? "Submitting…" : "Submit"}</button>
            } />
        ))}
      </Card>
    </StatePanel>
  );
}

/* ── Approvals: routed by band. `mine` is the default for a reason. ───────────────────── */
/* ── the approval matrix ───────────────────────────────────────────────────────────────────

   A bill cannot be submitted until some band covers its amount, and until now there was no way
   to write one: the server read a matrix that nothing could create, so the first real invoice
   parked on "No approval band" with nowhere to go.

   Edited as a WHOLE and saved in one call, because that is how the server stores it. Editing
   bands one at a time invites a moment where two overlap or a gap opens between them, and the
   gap is precisely what lets an invoice through with nobody required to approve it. */

/* The same question the server asks at submit time — "does a band cover this amount?" — asked
   of the whole number line before saving, so a gap is caught while it is still being typed
   rather than by an invoice that cannot move. */
function bandProblems(bands) {
  const live = bands.filter((b) => b.active !== false)
    .map((b) => ({ ...b, min: Number(b.min_amount) || 0,
                   max: b.max_amount === "" || b.max_amount === null ? null : Number(b.max_amount) }))
    .sort((a, b) => a.min - b.min);
  const out = [];
  if (!live.length) return ["No active bands, so nothing can be submitted for approval."];
  if (live[0].min > 0) out.push(`Nothing covers amounts under ${usd(live[0].min)}.`);
  for (let i = 0; i < live.length - 1; i++) {
    if (live[i].max === null) break;                    // an open band swallows everything above
    const gap = live[i + 1].min - live[i].max;
    if (gap <= 0.01) continue;
    // The "2,500 / 2,501" convention leaves the CENTS between them uncovered, and an invoice
    // for 2,500.50 hits the same wall as one for a million. It is a real gap, but naming it the
    // same way as a real hole would teach people to scroll past this box — so it says which it
    // is, and how to close it. Overlapping is safe: the narrower band wins, by rule.
    out.push(gap <= 1
      ? `${usd(live[i].max)} to ${usd(live[i + 1].min)} — the cents between these two bands are uncovered. Start "${live[i + 1].label || "the next band"}" at ${usd(live[i].max)} to close it.`
      : `Nothing covers ${usd(live[i].max)} to ${usd(live[i + 1].min)}.`);
  }
  const top = live[live.length - 1];
  if (top.max !== null) {
    out.push(`Nothing covers amounts over ${usd(top.max)} — the largest band should have no ceiling.`);
  }
  for (const b of live) if (!String(b.label || "").trim()) out.push("Every band needs a label.");
  return out;
}

const BAND_COLS = "minmax(140px,1.6fr) 110px 110px 150px 74px 34px";

function BandEditor({ onClose }) {
  const { data, loading, error, retry, refresh } = usePolicies();
  const [rows, setRows] = useState(null);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState(null);

  // Server state seeds the editor once; after that the draft is the user's, not the server's.
  const bands = rows ?? (data?.bands || []).map((b) => ({
    label: b.label, min_amount: b.min_amount,
    max_amount: b.max_amount === null ? "" : b.max_amount,
    requires_second_approver: b.requires_second_approver, active: b.active }));

  const set = (i, k, v) => setRows(bands.map((b, j) => (j === i ? { ...b, [k]: v } : b)));
  const add = () => setRows([...bands, { label: "", min_amount: bands.length ? "" : 0,
    max_amount: "", requires_second_approver: false, active: true }]);
  const drop = (i) => setRows(bands.filter((_, j) => j !== i));

  const problems = bandProblems(bands);

  const save = async () => {
    setSaving(true); setErr(null);
    try {
      await putJSON(`/payables/policies`, { bands: bands.map((b) => ({
        label: String(b.label).trim(),
        min_amount: Number(b.min_amount) || 0,
        max_amount: b.max_amount === "" || b.max_amount === null ? null : Number(b.max_amount),
        requires_second_approver: !!b.requires_second_approver,
        active: b.active !== false,
      })) });
      setRows(null);
      refresh();
      onClose();
    } catch (e) { setErr(e?.detail || e?.message || "The matrix could not be saved."); }
    finally { setSaving(false); }
  };

  return (
    <StatePanel loading={loading} error={error} retry={retry}>
      <Card style={{ padding: "16px 18px", marginBottom: 14 }}>
        <Eyebrow>Approval bands</Eyebrow>
        <div style={{ fontFamily: font.body, fontSize: 12, color: T.secondary,
                      margin: "6px 0 12px", lineHeight: 1.6, maxWidth: 640 }}>
          Which amounts need whose signature. A bill cannot go for approval until one of these
          covers it, so the bands have to reach from zero upward with no gap — and the largest
          one needs no ceiling, or the next unusually big invoice has nowhere to sit. Edges are
          inclusive on both sides; where two overlap, the narrower one wins.
        </div>

        <div className="band-head" style={{ display: "grid", gridTemplateColumns: BAND_COLS,
                      columnGap: 10, padding: "0 0 7px" }}>
          {["Band", "From", "To", "Signatures", "Active", ""].map((h, i) => (
            <span key={h || i} style={{ fontFamily: font.head, fontSize: 10.5, fontWeight: 700,
              letterSpacing: "0.08em", textTransform: "uppercase", color: T.muted }}>{h}</span>
          ))}
        </div>

        {bands.map((b, i) => (
          <div key={i} className="band-row" style={{ display: "grid",
                                gridTemplateColumns: BAND_COLS, columnGap: 10,
                                alignItems: "center", marginBottom: 8 }}>
            <input style={input} value={b.label} placeholder="Up to 2,500"
              onChange={(e) => set(i, "label", e.target.value)} />
            {/* Placeholders carry the column meaning once the headers are hidden on a phone. */}
            <input style={input} type="number" step="0.01" min="0" value={b.min_amount}
              placeholder="from" onChange={(e) => set(i, "min_amount", e.target.value)} />
            <input style={input} type="number" step="0.01" min="0" value={b.max_amount}
              placeholder="no ceiling" onChange={(e) => set(i, "max_amount", e.target.value)} />
            <select style={input} value={b.requires_second_approver ? "2" : "1"}
              onChange={(e) => set(i, "requires_second_approver", e.target.value === "2")}>
              <option value="1">One approver</option>
              <option value="2">Two — different people</option>
            </select>
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontFamily: font.body,
                            fontSize: 12, color: T.secondary }}>
              <input type="checkbox" checked={b.active !== false}
                onChange={(e) => set(i, "active", e.target.checked)} />
            </label>
            <button title="Remove this band" onClick={() => drop(i)}
              style={{ ...btn(), padding: "5px 9px", fontSize: 13, lineHeight: 1 }}>×</button>
          </div>
        ))}

        <div style={{ display: "flex", gap: 8, marginTop: 4, marginBottom: 12 }}>
          <button style={btn()} onClick={add}>Add a band</button>
        </div>

        {/* The gap is the whole risk, so it is named in full rather than flagged. */}
        {problems.length > 0 && (
          <div style={{ background: T.parchment, border: `1px solid ${T.line}`, borderLeft:
            `3px solid ${T.poppy}`, borderRadius: 10, padding: "10px 13px", marginBottom: 12 }}>
            <div style={{ fontFamily: font.head, fontSize: 12.5, fontWeight: 700,
                          color: T.poppyText }}>
              {problems.length === 1 ? "One amount range is uncovered" : "Some amounts are uncovered"}
            </div>
            {problems.map((p, i) => (
              <div key={i} style={{ fontFamily: font.body, fontSize: 12, color: T.secondary,
                                    marginTop: 3 }}>{p}</div>
            ))}
            <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted, marginTop: 6 }}>
              A bill landing in an uncovered range cannot be submitted at all — it is not that it
              skips approval, it simply stops.
            </div>
          </div>
        )}

        {err && <div style={{ fontFamily: font.body, fontSize: 12, color: T.poppyText,
                              marginBottom: 10 }}>{err}</div>}
        <div style={{ display: "flex", gap: 8 }}>
          <button disabled={saving || !bands.length} style={btn(saving || !bands.length ? "disabled" : "primary")}
            onClick={save}>{saving ? "Saving…" : "Save the matrix"}</button>
          <button disabled={saving} style={btn()}
            onClick={() => { setRows(null); onClose(); }}>Cancel</button>
        </div>
      </Card>
    </StatePanel>
  );
}

function ApprovalsView() {
  const [mine, setMine] = useState(true);
  const [bands, setBands] = useState(false);
  const [open, setOpen] = useState(null);
  const [busy, setBusy] = useState(null);
  const [err, setErr] = useState(null);
  const { data, loading, error, retry, refresh } =
    usePayables({ status: "awaiting_approval", mine });
  const rows = data?.payables || [];
  const total = rows.reduce((a, p) => a + (p.amount || 0), 0);

  const decide = async (p, decision) => {
    setBusy(p.id); setErr(null);
    try { await mutate(`/payables/${p.id}/decide`, { decision }); refresh(); }
    catch (e) { setErr(e?.message || "Could not record that decision."); }
    finally { setBusy(null); }
  };

  return (
    <StatePanel loading={loading} error={error} retry={retry}>
      <Stats>
        <StatCard label="Awaiting approval" value={rows.length}
          scope={mine ? "assigned to me" : "everyone"} />
        <StatCard label="Value" value={usd(total)} />
        <StatCard label="Two-signature" value={rows.filter((p) => p.approvals?.length > 1).length} />
      </Stats>
      {bands && <BandEditor onClose={() => setBands(false)} />}
      <div style={{ display: "flex", gap: 5, marginBottom: 12, alignItems: "center",
                    flexWrap: "wrap" }}>
        {!bands && (
          <button style={{ ...btn(), marginRight: 6 }} onClick={() => setBands(true)}>
            Approval bands</button>
        )}
        {[[true, "Mine"], [false, "Everyone"]].map(([k, l]) => (
          <button key={l} onClick={() => setMine(k)} style={{ fontFamily: font.body, fontSize: 11.5,
            fontWeight: 600, borderRadius: 99, padding: "5px 12px", cursor: "pointer",
            color: mine === k ? T.white : T.secondary,
            background: mine === k ? T.meadow : T.white,
            border: `1px solid ${mine === k ? T.meadow : T.line}` }}>{l}</button>
        ))}
      </div>
      {err && (
        <Card style={{ padding: "11px 15px", marginBottom: 12 }}>
          <span style={{ fontFamily: font.body, fontSize: 12.5, color: T.poppyText }}>{err}</span>
        </Card>
      )}
      <Card style={{ padding: 0, overflow: "hidden" }}>
        <Header cols={["Due", "Vendor", "Invoice", "Amount", "Band", ""]} />
        {rows.length === 0 && (
          <div style={{ padding: "26px 16px", fontFamily: font.body, fontSize: 12.5, color: T.secondary }}>
            {mine ? "Nothing is waiting on you." : "Nothing is awaiting approval."}
          </div>
        )}
        {rows.map((p) => (
          <BillRow key={p.id} p={p} open={open === p.id}
            toggle={() => setOpen(open === p.id ? null : p.id)}
            right={<>
              <button disabled={busy === p.id} style={btn(busy === p.id ? "disabled" : "primary")}
                onClick={(e) => { e.stopPropagation(); decide(p, "approve"); }}>Approve</button>
              <button disabled={busy === p.id} style={btn(busy === p.id ? "disabled" : "danger")}
                onClick={(e) => { e.stopPropagation(); decide(p, "reject"); }}>Reject</button>
            </>}>
            {p.approvals?.length > 1 && (
              <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.secondary }}>
                This band needs {p.approvals.length} signatures —{" "}
                {p.approvals.filter((a) => a.decision === "approve").length} so far. One person
                cannot supply both.
              </div>
            )}
          </BillRow>
        ))}
      </Card>
    </StatePanel>
  );
}

/* ── Runs: the batch, and the one place money is released ─────────────────────────────────

   ONE RUN PER ENTITY (Connor's decision). The five companies have separate QBO realms and
   separate bank accounts, so a combined run would produce an export somebody splits by hand at
   the bank — which is where a line gets keyed against the wrong account. Hence the entity bar:
   the run always belongs to exactly one company.

   Release does NOT originate a payment. It records who released what and hands back a CSV that
   a person carries to Treasury Internet Banking. Nothing in this module talks to a bank. */
const RUN_STATUS = {
  draft: ["muted", "Draft"], released: ["good", "Released"],
  reconciled: ["good", "Reconciled"], cancelled: ["muted", "Cancelled"],
};

const RUN_COLS = "minmax(150px,1.5fr) 92px 74px 80px 96px minmax(130px,1.1fr)";

function RunHeader() {
  return (
    <div className="pay-head" style={{ display: "grid", gridTemplateColumns: RUN_COLS,
      columnGap: 12,
      padding: "11px 16px", borderBottom: `1px solid ${T.line}`, background: T.parchment }}>
      {["Vendor", "Invoice", "Terms", "Due", "Amount", ""].map((h, i) => (
        <span key={h || i} style={{ fontFamily: font.head, fontSize: 10.5, fontWeight: 700,
          letterSpacing: "0.08em", textTransform: "uppercase", color: T.muted,
          textAlign: i === 4 ? "right" : "left" }}>{h}</span>
      ))}
    </div>
  );
}

/* A held line is marked by an accent bar AND its own chips, never by colour alone — the same
   rule the queue follows, so the page survives a monochrome screenshot. */
function RunLine({ line, actions, busy }) {
  const [show, setShow] = useState(false);
  const detail = line.holds?.length > 0 || line.override_reason;
  return (
    <div style={{ borderBottom: `1px solid ${T.line}`,
                  borderLeft: `3px solid ${line.held ? T.poppy : "transparent"}`,
                  background: line.held ? T.parchment : T.white }}>
      <div className="pay-row" onClick={() => setShow((v) => !v)} style={{ display: "grid",
        gridTemplateColumns: RUN_COLS, columnGap: 12, alignItems: "center",
        padding: "12px 16px", cursor: detail ? "pointer" : "default" }}>
        <span style={{ minWidth: 0, fontFamily: font.body, fontSize: 12.5, fontWeight: 600,
          color: T.ink, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {line.vendor || line.vendor_legal_name || "—"}</span>
        <span style={{ minWidth: 0, fontFamily: font.body, fontSize: 12, color: T.secondary,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {line.invoice_number}</span>
        <span style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted }}>
          {line.terms || "—"}</span>
        <span style={{ fontFamily: font.body, fontSize: 12, color: T.muted }}>
          {fmtDate(line.due_date)}</span>
        <span style={{ fontFamily: font.head, fontSize: 13, fontWeight: 600, color: T.ink,
          textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{usd(line.amount)}</span>
        <span style={{ minWidth: 0, display: "flex", alignItems: "center", gap: 6,
                       justifyContent: "flex-end", flexWrap: "wrap" }}>
          {line.holds?.length ? <HoldChips holds={line.holds} />
            : <span style={{ fontFamily: font.body, fontSize: 11.5, color: T.secondary }}>
                {STATUS_LABEL[line.status] || line.status}</span>}
          {detail && (
            /* The hold chip alone reads as a label. This says the row does something. */
            <span style={{ fontFamily: font.body, fontSize: 11, color: T.muted }}>
              {show ? "▴" : "▾"}</span>
          )}
        </span>
      </div>
      {show && detail && (
        <div style={{ padding: "0 16px 14px 16px", display: "grid", gap: 9 }}>
          {line.holds?.map((h) => (
            <div key={h.key} style={{ background: T.white, border: `1px solid ${T.line}`,
              borderRadius: 10, padding: "10px 13px" }}>
              <div style={{ fontFamily: font.head, fontSize: 12.5, fontWeight: 700,
                color: h.hold ? T.poppyText : T.secondary }}>
                {h.label}{h.overridden ? " · overridden" : ""}</div>
              <div style={{ fontFamily: font.body, fontSize: 12, color: T.secondary, marginTop: 3 }}>
                {h.why}</div>
            </div>
          ))}
          {line.override_reason && (
            <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.secondary }}>
              Override on record: {line.override_reason}
            </div>
          )}
          {actions && line.held && (
            <div style={{ display: "flex", gap: 7, flexWrap: "wrap" }}>{actions(busy)}</div>
          )}
        </div>
      )}
    </div>
  );
}

/* Release asks for the code HERE, at the moment of release, rather than at the door. The server
   is the real gate (it answers 428 without a live grant); this makes that legible instead of
   surfacing a status code to somebody about to pay forty thousand dollars. */
function ReleaseButton({ run, onDone }) {
  const [code, setCode] = useState(null);      // null = not asking; "" = asking
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const go = async (value) => {
    setBusy(true); setErr(null);
    try {
      if (value && value.trim()) {
        const r = await postJSON(`/step-up/payments`, { code: value.trim() });
        setStepUp("payments", r.token);
      }
      await postJSON(`/payables/runs/${run.id}/release`, {});
      setCode(null); onDone();
    } catch (e) {
      if (e?.status === STEP_UP_STATUS) { setCode(""); setErr("Enter your code to release."); }
      else setErr(e?.detail || e?.message || "That release did not go through.");
    } finally { setBusy(false); }
  };

  if (!run.can_release) {
    return (
      <button disabled style={btn("disabled")}
        title={run.held_count ? "Clear or push each held line first" : `This run is ${run.status}`}>
        {run.held_count
          ? `${run.held_count} line${run.held_count === 1 ? "" : "s"} held`
          : (RUN_STATUS[run.status]?.[1] || run.status)}
      </button>
    );
  }
  return (
    <div style={{ display: "grid", gap: 6, justifyItems: "end" }}>
      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        {code !== null && (
          <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="000000"
            inputMode="numeric" autoFocus
            style={{ width: 96, boxSizing: "border-box", fontFamily: font.body, fontSize: 13,
              letterSpacing: ".16em", textAlign: "center", padding: "6px 8px",
              border: `1px solid ${T.line}`, borderRadius: 8, color: T.ink, background: T.white }} />
        )}
        <button disabled={busy} style={btn(busy ? "disabled" : "primary")}
          onClick={() => go(code)}>
          {busy ? "Releasing…" : code !== null ? "Confirm release" : "Release run"}
        </button>
      </div>
      {err && <span style={{ fontFamily: font.body, fontSize: 11.5, color: T.poppyText }}>{err}</span>}
    </div>
  );
}

function RunsView() {
  const entities = useCoaEntities();
  const [bizId, setBizId] = useState(null);
  const [openId, setOpenId] = useState(null);
  const [busy, setBusy] = useState(null);
  const [err, setErr] = useState(null);

  const list = entities.data?.entities || [];
  const active = bizId || list[0]?.id || null;
  // Approved bills across every entity, so the chips can say which company has money waiting.
  // Without it you pick an entity, find nothing, and cannot tell whether that means "nothing
  // approved" or "you are looking at the wrong company".
  const approved = usePayables({ status: "approved" }).data?.payables || [];
  const waitingBy = approved.reduce((m, p) => {
    if (p.business_id) m[p.business_id] = (m[p.business_id] || 0) + 1;
    return m;
  }, {});
  const runs = useRuns(active);
  const all = runs.data?.runs || [];
  const draft = all.find((r) => r.status === "draft");
  /* The run on screen: whatever was clicked, else the open draft, else nothing — in which case
     the PROPOSAL is shown instead. The proposal is not requested while a run is displayed, so
     the screen never shows a live proposal beside a batch that already fixed those lines. */
  const shownId = openId || draft?.id || null;
  const detail = useRun(shownId);
  // The proposal is fetched for the entity ALWAYS, not only when no run is open. Its LINES are
  // used only when nothing is open — a live proposal beside a batch that already fixed those
  // lines would show the same invoice twice with two different fates. But the list of what is
  // approved and merely waiting is true either way, and hiding it behind "no open run" is how a
  // bill disappears the moment you create the run it is not in.
  const next = useNextRun(active);
  const run = shownId ? detail.data : null;
  const proposal = shownId ? null : next.data;
  const waiting = next.data?.upcoming || [];
  const waitingTotal = next.data?.upcoming_total || 0;
  const lookahead = next.data?.lookahead_days ?? 6;

  const reload = () => { runs.refresh(); detail.refresh(); next.refresh(); };

  /* Auth here is a bearer token in storage, not a cookie, so a plain <a href> to the API is a
     top-level navigation with no Authorization header: it 401s and takes the SPA with it. The
     file is fetched with the session's headers and handed to the browser as a blob, the way
     every other download in this app works. */
  const download = async (r) => {
    setBusy("export"); setErr(null);
    try {
      const blob = await getBlob(`/payables/runs/${r.id}/export`);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = r.export_ref || `run-${r.run_date}.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr(e?.detail || e?.message || "That file could not be downloaded.");
    } finally { setBusy(null); }
  };

  const act = async (path, body) => {
    setBusy(path); setErr(null);
    try { await postJSON(path, body || {}); reload(); }
    catch (e) { setErr(e?.detail || e?.message || "That did not go through."); }
    finally { setBusy(null); }
  };

  const lines = run?.lines || proposal?.lines || [];
  const draftOpen = run && run.status === "draft";
  const settled = run && run.status !== "draft";
  /* Holds are re-derived live, so a released run still reports whatever is true of its vendors
     today — a banking change made after the payment would otherwise raise a "release is
     blocked" banner over money that has already gone. Once a run is released, what it paid is
     history: every line counts, and nothing about it is blocked any more. */
  const heldCount = settled ? 0 : (run ? run.held_count : (proposal?.held_count || 0));
  const total = settled
    ? lines.reduce((a, l) => a + (l.amount || 0), 0)
    : (run ? run.releasable_total : (proposal?.total || 0));
  const releasable = settled ? lines.length : lines.filter((l) => !l.held).length;

  return (
    <StatePanel loading={entities.loading} error={entities.error} retry={entities.retry}>
      {/* Entity bar. One run per company, so this is not a filter — it is which run you are on. */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
                    marginBottom: 14 }}>
        <span style={{ fontFamily: font.head, fontSize: 10.5, fontWeight: 700,
          letterSpacing: "0.08em", textTransform: "uppercase", color: T.muted }}>Entity</span>
        {list.map((e) => (
          <button key={e.id} onClick={() => { setBizId(e.id); setOpenId(null); }}
            style={{ fontFamily: font.body, fontSize: 11.5, fontWeight: 600, borderRadius: 99,
              padding: "5px 12px", cursor: "pointer",
              color: e.id === active ? T.white : T.secondary,
              background: e.id === active ? T.meadow : T.white,
              border: `1px solid ${e.id === active ? T.meadow : T.line}` }}>
            {e.name}{waitingBy[e.id] ? ` · ${waitingBy[e.id]}` : ""}</button>
        ))}
      </div>

      {err && (
        <Card style={{ padding: "11px 15px", marginBottom: 12 }}>
          <span style={{ fontFamily: font.body, fontSize: 12.5, color: T.poppyText }}>{err}</span>
        </Card>
      )}

      <StatePanel loading={detail.loading || next.loading}
                  error={detail.error || next.error} retry={reload}>
        <Card style={{ padding: 0, overflow: "hidden", marginBottom: 18 }}>
          <div style={{ padding: "18px 22px", display: "flex", alignItems: "center", gap: 18,
                        flexWrap: "wrap" }}>
            <div style={{ flex: 1, minWidth: 210 }}>
              <Eyebrow>{run ? "Payment run" : "Next payment run"}</Eyebrow>
              <div style={{ fontFamily: font.head, fontSize: 21, fontWeight: 700, color: T.ink,
                            marginTop: 4 }}>
                {fmtLong(run?.run_date || proposal?.run_date)}</div>
              <div style={{ fontFamily: font.body, fontSize: 12.5, color: T.muted, marginTop: 3 }}>
                {run
                  ? `${RUN_STATUS[run.status]?.[1] || run.status}${run.released_by ? ` · released by ${run.released_by}` : ""}`
                  : `Everything approved and due through ${fmtDate(proposal?.horizon)} — the next ${proposal?.lookahead_days ?? 6} days`}
              </div>
            </div>
            <div style={{ textAlign: "right" }}>
              <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.secondary }}>
                {releasable} line{releasable === 1 ? "" : "s"} {settled ? "paid" : "releasable"}</div>
              <div style={{ fontFamily: font.head, fontSize: 27, fontWeight: 700, color: T.ink,
                            fontVariantNumeric: "tabular-nums" }}>{usd(total)}</div>
            </div>
            {run
              ? <ReleaseButton run={run} onDone={() => { setOpenId(run.id); reload(); }} />
              : <button disabled={!lines.length || !!busy}
                  onClick={() => act(`/payables/runs`, { business_id: active })}
                  style={btn(!lines.length || busy ? "disabled" : "primary")}
                  title={lines.length ? "Creates the batch — release is a separate step"
                                      : "Nothing is approved and due in this window"}>
                  {busy ? "Creating…" : "Create run"}</button>}
          </div>

          {heldCount > 0 && (
            <div style={{ background: T.parchment, borderTop: `1px solid ${T.line}`,
                          padding: "11px 22px", fontFamily: font.body, fontSize: 12,
                          color: T.poppyText }}>
              Release is blocked while any line is held. <b>Open a held line</b> to push it to the
              next run or override it by name. There is no release-anyway.
            </div>
          )}

          {run?.status === "released" && (
            <div style={{ borderTop: `1px solid ${T.line}`, padding: "11px 22px",
                          display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <span style={{ fontFamily: font.body, fontSize: 12, color: T.secondary, flex: 1,
                             minWidth: 200 }}>
                Released — and nothing has left the bank yet. Take the file to Treasury Internet
                Banking, then mark it reconciled once the bank confirms.</span>
              <button disabled={!!busy} style={btn(busy ? "disabled" : undefined)}
                onClick={() => download(run)}>Export CSV</button>
              <button disabled={!!busy} style={btn(busy ? "disabled" : "primary")}
                onClick={() => act(`/payables/runs/${run.id}/reconcile`, {})}>
                Mark reconciled</button>
            </div>
          )}
        </Card>

        <Eyebrow>{run ? "Run lines" : "Proposed lines"}</Eyebrow>
        <Card style={{ padding: 0, overflow: "hidden", marginTop: 8 }}>
          <RunHeader />
          {lines.length === 0 && (
            <div style={{ padding: "26px 16px", fontFamily: font.body, fontSize: 12.5,
                          color: T.secondary }}>
              Nothing is due inside this window for this entity.
            </div>
          )}
          {lines.map((l) => (
            <RunLine key={l.payable_id} line={l} busy={busy}
              actions={draftOpen ? (b) => (
                <>
                  <button disabled={!!b} style={btn(b ? "disabled" : undefined)}
                    onClick={() => act(`/payables/runs/${run.id}/hold-line`,
                      { payable_id: l.payable_id, reason: "held at run review" })}>
                    Hold to next run</button>
                  <button disabled={!!b} style={btn(b ? "disabled" : undefined)}
                    onClick={() => {
                      const note = window.prompt(
                        "Why is this being paid despite the hold? This note is the only record of it.");
                      if (note && note.trim()) {
                        act(`/payables/runs/${run.id}/override-line`,
                            { payable_id: l.payable_id, note: note.trim() });
                      }
                    }}>
                    Override · second approver</button>
                </>
              ) : null} />
          ))}
        </Card>

        {waiting.length > 0 && (
          <>
            <div style={{ marginTop: 22 }}><Eyebrow>Approved · not due yet</Eyebrow></div>
            <div style={{ fontFamily: font.body, fontSize: 12, color: T.secondary,
                          margin: "5px 0 8px", lineHeight: 1.6, maxWidth: 640 }}>
              Signed off and waiting for the run that covers their due date — {usd(waitingTotal)}{" "}
              in total. A run pays what is due within {lookahead} days, so these are not late
              and not missing.
            </div>
            <Card style={{ padding: 0, overflow: "hidden" }}>
              {waiting.map((l, i) => (
                <div key={l.payable_id} style={{ display: "flex", alignItems: "center", gap: 12,
                  flexWrap: "wrap", padding: "11px 16px",
                  borderBottom: i < waiting.length - 1 ? `1px solid ${T.line}` : "none" }}>
                  <span style={{ flex: 1, minWidth: 140, fontFamily: font.body, fontSize: 12.5,
                    fontWeight: 600, color: T.ink }}>{l.vendor || l.vendor_legal_name || "—"}</span>
                  <span style={{ fontFamily: font.body, fontSize: 12, color: T.secondary,
                    minWidth: 70 }}>{l.invoice_number}</span>
                  <span style={{ fontFamily: font.body, fontSize: 12, color: T.muted,
                    minWidth: 60 }}>due {fmtDate(l.due_date)}</span>
                  <span style={{ fontFamily: font.head, fontSize: 13, fontWeight: 600,
                    color: T.ink, width: 96, textAlign: "right",
                    fontVariantNumeric: "tabular-nums" }}>{usd(l.amount)}</span>
                  <span style={{ fontFamily: font.body, fontSize: 11.5, color: T.secondary,
                    minWidth: 150 }}>
                    {l.picked_up_on ? `in the ${fmtDate(l.picked_up_on)} run` : "no due date set"}</span>
                </div>
              ))}
            </Card>
          </>
        )}

        <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted, marginTop: 12,
                      lineHeight: 1.6, maxWidth: 640 }}>
          Releasing asks for your code and records the batch against the approved list.{" "}
          <b>The person who approved these bills cannot also release them</b> unless this
          workspace allows it — and where it does, the release says so in the audit trail.
        </div>

        {all.length > 0 && (
          <>
            <div style={{ marginTop: 22 }}><Eyebrow>All runs</Eyebrow></div>
            <Card style={{ padding: 0, overflow: "hidden", marginTop: 8 }}>
              {all.map((r, i) => (
                <div key={r.id} onClick={() => setOpenId(r.id)}
                  style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap",
                    padding: "12px 16px", cursor: "pointer",
                    borderBottom: i < all.length - 1 ? `1px solid ${T.line}` : "none",
                    background: r.id === shownId ? T.parchment : T.white }}>
                  <span style={{ fontFamily: font.body, fontSize: 12.5, fontWeight: 600,
                    color: T.ink, flex: 1, minWidth: 140 }}>{fmtLong(r.run_date)}</span>
                  <Pill tone={RUN_STATUS[r.status]?.[0] || "muted"}>
                    {RUN_STATUS[r.status]?.[1] || r.status}</Pill>
                  <span style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted,
                    width: 74 }}>{r.item_count} line{r.item_count === 1 ? "" : "s"}</span>
                  <span style={{ fontFamily: font.head, fontSize: 13, fontWeight: 600,
                    color: T.ink, width: 96, textAlign: "right",
                    fontVariantNumeric: "tabular-nums" }}>{usd(r.total_amount)}</span>
                  <span style={{ fontFamily: font.body, fontSize: 11.5, color: T.secondary,
                    minWidth: 120 }}>{r.released_by ? `by ${r.released_by}` : "not released"}</span>
                </div>
              ))}
            </Card>
          </>
        )}
      </StatePanel>
    </StatePanel>
  );
}

/* ── Vendors: status is DERIVED — a W-9, active banking, and a logged callback ────────── */
const STATUS = {
  active: ["good", "Active"],
  pending_verification: ["bad", "Pending verification"],
  inactive: ["muted", "Inactive"],
  draft: ["muted", "Draft"],
};
const VEN_COLS = "minmax(170px,1.7fr) 92px 82px minmax(130px,1fr) 78px 150px";

function VendorsView() {
  const [status, setStatus] = useState(null);
  const [adding, setAdding] = useState(false);
  const [open, setOpen] = useState(null);
  const { data, loading, error, retry, refresh } = useVendors(status);
  const rows = data?.vendors || [];
  return (
    <StatePanel loading={loading} error={error} retry={retry}>
      <Stats>
        <StatCard label="Active vendors" value={rows.filter((r) => r.status === "active").length} />
        <StatCard label="Pending verification" color={T.poppyText}
          value={rows.filter((r) => r.status === "pending_verification").length} />
        <StatCard label="Missing W-9" color={T.poppyText} value={rows.filter((r) => !r.w9).length} />
        <StatCard label="1099-eligible" value={rows.filter((r) => r.is_1099).length} />
      </Stats>
      {adding && (
        <NewVendorForm onCancel={() => setAdding(false)}
          onDone={(v) => { setAdding(false); refresh(); if (v?.id) setOpen(v.id); }} />
      )}
      <div style={{ display: "flex", gap: 5, marginBottom: 12, flexWrap: "wrap",
                    alignItems: "center" }}>
        {!adding && (
          <button style={{ ...btn("primary"), marginRight: 6 }}
            onClick={() => setAdding(true)}>New vendor</button>
        )}
        {[[null, "All"], ["active", "Active"], ["pending_verification", "Pending"],
          ["inactive", "Inactive"]].map(([k, l]) => (
          <button key={l} onClick={() => setStatus(k)} style={{ fontFamily: font.body, fontSize: 11.5,
            fontWeight: 600, borderRadius: 99, padding: "5px 12px", cursor: "pointer",
            color: status === k ? T.white : T.secondary,
            background: status === k ? T.meadow : T.white,
            border: `1px solid ${status === k ? T.meadow : T.line}` }}>{l}</button>
        ))}
      </div>
      <Card style={{ padding: 0, overflow: "hidden" }}>
        <div style={{ display: "grid", gridTemplateColumns: VEN_COLS, columnGap: 12,
          padding: "11px 16px", borderBottom: `1px solid ${T.line}`, background: T.parchment }}>
          {["Vendor", "Type", "W-9", "Banking", "Terms", "Status"].map((h) => (
            <span key={h} style={{ fontFamily: font.head, fontSize: 10.5, fontWeight: 700,
              letterSpacing: "0.08em", textTransform: "uppercase", color: T.muted }}>{h}</span>
          ))}
        </div>
        {rows.length === 0 && (
          <div style={{ padding: "26px 16px", fontFamily: font.body, fontSize: 12.5, color: T.secondary }}>
            No vendors yet. Add one above — it needs a W-9 and verified banking before anything
            can be paid to it.
          </div>
        )}
        {rows.map((v, i) => {
          const [tone, label] = STATUS[v.status] || STATUS.draft;
          return (
            <div key={v.id} style={{
              borderBottom: i < rows.length - 1 ? `1px solid ${T.line}` : "none" }}>
            <div onClick={() => setOpen(open === v.id ? null : v.id)}
              className="pay-row"
              style={{ display: "grid", gridTemplateColumns: VEN_COLS, columnGap: 12,
              alignItems: "center", padding: "12px 16px", cursor: "pointer" }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontFamily: font.body, fontSize: 12.5, fontWeight: 600, color: T.ink,
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {v.display_name}</div>
                <div style={{ fontFamily: font.body, fontSize: 11, color: T.muted, marginTop: 1,
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {v.dba || v.default_business_name || "no defaults set"}</div>
              </div>
              <span style={{ fontFamily: font.body, fontSize: 11.5, color: T.secondary }}>
                {v.vendor_type === "individual" ? "Individual" : "Business"}</span>
              <span style={{ fontFamily: font.body, fontSize: 11.5, fontWeight: 600,
                color: v.w9 ? T.meadowInk : T.poppyText }}>{v.w9 ? "On file" : "Missing"}</span>
              {/* Banking reads the VERIFICATION, not the account number. Whether somebody rang
                  the vendor back is the control; the last four digits are not. */}
              <span style={{ fontFamily: font.body, fontSize: 11.5, minWidth: 0,
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                color: v.bank?.verified_at ? T.secondary : T.poppyText }}>
                {v.bank?.verified_at
                  ? `${v.bank.verified_by_name || "Verified"} · ${fmtDate(v.bank.verified_at)}`
                  : v.bank ? "Not verified" : "None on file"}</span>
              <span style={{ fontFamily: font.body, fontSize: 11.5, color: T.secondary }}>
                Net {v.terms_days}</span>
              <span><Pill tone={tone}>{label}</Pill></span>
            </div>
            {open === v.id && <VendorSetup v={v} onDone={refresh} />}
            </div>
          );
        })}
      </Card>
      <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted, marginTop: 12,
                    lineHeight: 1.6, maxWidth: 640 }}>
        Open a vendor to attach its W-9 or record its banking. Status is derived, never typed:
        a vendor becomes Active on its own once a W-9 is on file and banking has been verified
        by callback — and banking changes add a new record rather
        than overwriting the old one, so where money used to go stays readable.
      </div>
    </StatePanel>
  );
}

export default function BooksPayables() {
  const [view, setView] = useState("inbox");
  return (
    <div>
      {/* Both row grids are built from FIXED pixel tracks, which do not shrink — on a phone they
          needed 746px inside a 306px card and the Card's overflow:hidden simply CLIPPED them.
          The amount, the status and the Submit button were cut off the screen entirely, and a
          page-level "does anything overflow?" check reported clean, because the clipping is what
          stopped the overflow. Below 720px the rows become a wrapping flex line instead: the
          vendor takes its own line, the amount keeps the right edge, and the column heads go
          away because a heading over nothing is worse than no heading. */}
      <style>{`
        @media (max-width: 720px) {
          .pay-head { display: none !important; }
          .pay-row { display: flex !important; flex-wrap: wrap; align-items: baseline;
                     gap: 4px 12px; }
          .pay-row > * { width: auto !important; min-width: 0; text-align: left !important; }
          .pay-row > :first-child { flex: 1 1 100%; }
          .pay-row > :nth-child(5) { margin-left: auto; text-align: right !important; }
          /* The band editor is a FORM, so it stacks into two columns rather than wrapping into
             a line: three unlabelled number boxes in a row tell you nothing about which is
             which. The headers go, and the placeholders carry their meaning instead. */
          .band-head { display: none !important; }
          .band-row { grid-template-columns: 1fr 1fr !important; row-gap: 7px;
                      padding-bottom: 10px; border-bottom: 1px solid var(--t-line); }
          .band-row > :first-child { grid-column: 1 / -1; }
        }
      `}</style>
      <SegNav view={view} setView={setView} counts={{}} />
      {view === "inbox" && <InboxView />}
      {view === "approvals" && <ApprovalsView />}
      {view === "runs" && <RunsView />}
      {view === "vendors" && <VendorsView />}
    </div>
  );
}
