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
import { postJSON, setStepUp, STEP_UP_STATUS, getBlob } from "../api";
import { usePayables, useVendors, useRuns, useNextRun, useRun, useCoaEntities } from "./useBooks.js";
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
  const runs = useRuns(active);
  const all = runs.data?.runs || [];
  const draft = all.find((r) => r.status === "draft");
  /* The run on screen: whatever was clicked, else the open draft, else nothing — in which case
     the PROPOSAL is shown instead. The proposal is not requested while a run is displayed, so
     the screen never shows a live proposal beside a batch that already fixed those lines. */
  const shownId = openId || draft?.id || null;
  const detail = useRun(shownId);
  const next = useNextRun(shownId ? null : active);
  const run = shownId ? detail.data : null;
  const proposal = shownId ? null : next.data;

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
              border: `1px solid ${e.id === active ? T.meadow : T.line}` }}>{e.name}</button>
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
              Release is blocked while any line is held. Push each one to the next run, or have a
              second person override it by name. There is no release-anyway.
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
              Nothing is approved and due inside this window for this entity.
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
