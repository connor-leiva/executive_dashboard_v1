/* Books · Payables — AP intake through release. PHASE 1: the vendor master only.

   Four views, not four sub-tabs. Books already carries six sub-tabs and a seventh costs more
   than it returns, so the segmented control lives inside the page.

   Naming note: this page never says "queue." Books · Queue is the CATEGORIZATION queue — money
   that already cleared, reviewed after the fact. These are bills that have NOT been paid. Two
   things called a queue that mean opposite things is how somebody approves the wrong one.

   Inbox, Approvals and Runs render an empty state rather than the mockup's sample rows. A view
   that looks finished and does nothing is worse than one that says what it is waiting for. */
import { useState } from "react";
import { useVendors } from "./useBooks.js";
import { Card, Eyebrow, Pill, StatePanel, font, T } from "./ui.jsx";

const VIEWS = [["inbox", "Inbox"], ["approvals", "Approvals"], ["runs", "Runs"],
               ["vendors", "Vendors"]];

/* What each view is waiting on. Named by phase so the page is honest about being unfinished
   instead of implying the feature exists and is empty. */
const COMING = {
  inbox: ["Invoice intake", "Bills arrive here to be coded and confirmed. Nothing is entered in QuickBooks until a person confirms it."],
  approvals: ["Threshold-routed approval", "Invoices route to an approver by amount band. The matrix is data, not a deploy."],
  runs: ["Weekly payment runs", "A run closes, records who released it, and exports for the bank. Release never originates a payment."],
};

const fmtDate = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—"
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

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

function Stats({ children }) {
  return <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(168px,1fr))",
                       gap: 12, marginBottom: 18 }}>{children}</div>;
}

function ComingSoon({ view }) {
  const [title, body] = COMING[view] || ["Not built yet", ""];
  return (
    <Card style={{ padding: "30px 26px" }}>
      <Eyebrow>Not built yet</Eyebrow>
      <div style={{ fontFamily: font.head, fontSize: 17, fontWeight: 700, color: T.ink, marginTop: 6 }}>
        {title}</div>
      <div style={{ fontFamily: font.body, fontSize: 12.5, color: T.secondary, marginTop: 6,
                    lineHeight: 1.6, maxWidth: 560 }}>{body}</div>
      <div style={{ fontFamily: font.body, fontSize: 11.5, color: T.muted, marginTop: 12 }}>
        Vendors ships first because every one of these depends on it.</div>
    </Card>
  );
}

/* Status is DERIVED on the server — a W-9, an active bank row, and a logged verification. It is
   the gate on a first payment, so it is deliberately not something this screen can set. */
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
  const [view, setView] = useState("vendors");     // the only view that does anything yet
  return (
    <div>
      <SegNav view={view} setView={setView} counts={{}} />
      {view === "vendors" ? <VendorsView /> : <ComingSoon view={view} />}
    </div>
  );
}
