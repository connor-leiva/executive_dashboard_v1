/* Books · Payables — AP intake through release. PHASES 1-2: vendors, bills, approvals.

   Four views, not four sub-tabs. Books already carries seven sub-tabs and an eighth costs more
   than it returns, so the segmented control lives inside the page.

   Naming note: this page never says "queue." Books · Queue is the CATEGORIZATION queue — money
   that already cleared, reviewed after the fact. These are bills that have NOT been paid. Two
   things called a queue that mean opposite things is how somebody approves the wrong one.

   Every disable reads `holds` / `can_submit` off the row. The server decides what blocks a
   payment; scattering those conditions through JSX is how one screen ends up enforcing a rule
   another screen forgot, and the forgotten one pays somebody nobody verified. */
import { useState } from "react";
import { postJSON } from "../api";
import { usePayables, useVendors } from "./useBooks.js";
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

const fmtDate = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
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

function ComingSoon() {
  return (
    <Card style={{ padding: "30px 26px" }}>
      <Eyebrow>Not built yet</Eyebrow>
      <div style={{ fontFamily: font.head, fontSize: 17, fontWeight: 700, color: T.ink, marginTop: 6 }}>
        Weekly payment runs</div>
      <div style={{ fontFamily: font.body, fontSize: 12.5, color: T.secondary, marginTop: 6,
                    lineHeight: 1.6, maxWidth: 560 }}>
        A run closes, records who released it, and exports for the bank. Release never originates
        a payment — that stays a person in Treasury Internet Banking.</div>
    </Card>
  );
}

const ROW = "80px minmax(140px,1.4fr) 84px 96px minmax(120px,1fr) minmax(150px,1.2fr)";

function Header({ cols }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: ROW, columnGap: 12, padding: "11px 16px",
      borderBottom: `1px solid ${T.line}`, background: T.parchment }}>
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
      <div onClick={toggle} style={{ display: "grid", gridTemplateColumns: ROW, columnGap: 12,
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

/* ── Inbox: bills that have arrived and not yet gone for approval ─────────────────────── */
function InboxView() {
  const [open, setOpen] = useState(null);
  const [busy, setBusy] = useState(null);
  const [err, setErr] = useState(null);
  const { data, loading, error, retry, refresh } = usePayables({});
  const rows = (data?.payables || []).filter((p) => OPEN_STATES.includes(p.status));
  const ready = rows.filter((p) => p.can_submit).length;

  const submit = async (p) => {
    setBusy(p.id); setErr(null);
    try { await mutate(`/payables/${p.id}/submit`); refresh(); }
    catch (e) { setErr(e?.message || "Could not submit that invoice."); }
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
function ApprovalsView() {
  const [mine, setMine] = useState(true);
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
      <div style={{ display: "flex", gap: 5, marginBottom: 12 }}>
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
  const { data, loading, error, retry } = useVendors(status);
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
      <div style={{ display: "flex", gap: 5, marginBottom: 12, flexWrap: "wrap" }}>
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
            No vendors yet. A vendor needs a W-9 and verified banking before anything can be paid to it.
          </div>
        )}
        {rows.map((v, i) => {
          const [tone, label] = STATUS[v.status] || STATUS.draft;
          return (
            <div key={v.id} style={{ display: "grid", gridTemplateColumns: VEN_COLS, columnGap: 12,
              alignItems: "center", padding: "12px 16px",
              borderBottom: i < rows.length - 1 ? `1px solid ${T.line}` : "none" }}>
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
          );
        })}
      </Card>
      <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted, marginTop: 12,
                    lineHeight: 1.6, maxWidth: 640 }}>
        Status is derived, never typed. A vendor becomes Active on its own once a W-9 is on file
        and banking has been verified by callback — and banking changes add a new record rather
        than overwriting the old one, so where money used to go stays readable.
      </div>
    </StatePanel>
  );
}

export default function BooksPayables() {
  const [view, setView] = useState("inbox");
  return (
    <div>
      <SegNav view={view} setView={setView} counts={{}} />
      {view === "inbox" && <InboxView />}
      {view === "approvals" && <ApprovalsView />}
      {view === "runs" && <ComingSoon />}
      {view === "vendors" && <VendorsView />}
    </div>
  );
}
