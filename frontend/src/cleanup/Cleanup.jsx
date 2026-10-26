/* Books Cleanup -- a one-off tab for a workspace taking an audited year apart and putting it back
   together in QuickBooks (backend/app/routers/books_cleanup.py). It replaced the audit workbook: the
   same statements, live, with the decisions editable and the work trackable.

   The tab exists only while the workspace has a loaded file, so the server's nav is what puts it in
   the rail; there is no offline sample of it.

   `rev` counts saves. Every page reads with it, so a change made on one page re-reads the figures on
   all of them: a split moves money between three companies at once. */
import { useRef, useState } from "react";
import { ProductIcon, iconFor } from "../brand/productIcons.jsx";
import { StatePanel, font, T } from "../books/ui.jsx";
import { downloadSavedChanges, uploadCleanupFile, useCleanupOverview } from "./useCleanup.js";
import { Button, CleanupCtx, L, F, OkPill, SaveLine, reasonOf } from "./kit.jsx";
import CleanupStatement from "./CleanupStatement.jsx";
import CleanupTransactions from "./CleanupTransactions.jsx";
import CleanupForecast from "./CleanupForecast.jsx";
import CleanupBudget from "./CleanupBudget.jsx";
import CleanupCash from "./CleanupCash.jsx";
import CleanupDecisions from "./CleanupDecisions.jsx";
import CleanupCorrections from "./CleanupCorrections.jsx";
import CleanupBalance from "./CleanupBalance.jsx";
import CleanupInterco from "./CleanupInterco.jsx";
import { CleanupFindings, CleanupTodo } from "./CleanupItems.jsx";

const PAGES = [["statement", "Statement"], ["transactions", "Transactions"], ["forecast", "Forecast"],
               ["budget", "Budget"], ["cash", "Cash flow"], ["decisions", "Decisions"], ["corrections", "Corrections"],
               ["balance", "Balance sheet"], ["interco", "Intercompany"], ["todo", "To do"],
               ["findings", "Findings"]];

// Forecast and Cash flow are a projected company's: without one (from the file, or from the members' records)
// there are no such pages.
const FORWARD_ONLY = new Set(["forecast", "cash"]);

function SubNav({ page, setPage, counts, forward }) {
  return (
    <div style={{ display: "flex", gap: 4, borderBottom: `1px solid ${T.line}`, marginBottom: 18, flexWrap: "wrap" }}>
      {PAGES.filter(([k]) => forward || !FORWARD_ONLY.has(k)).map(([k, l]) => (
        <button key={k} onClick={() => setPage(k)} aria-current={page === k ? "page" : undefined} style={{
          fontFamily: font.head, fontSize: 13, fontWeight: 600, color: page === k ? T.ink : T.muted,
          background: "transparent", border: "none",
          borderBottom: page === k ? `2.5px solid ${T.meadow}` : "2.5px solid transparent",
          padding: "9px 15px 11px", cursor: "pointer", marginBottom: -1 }}>
          {l}{counts[k] ? ` · ${counts[k]}` : ""}</button>
      ))}
    </div>
  );
}

function Upload({ onLoaded }) {
  const ref = useRef(null);
  const [state, setState] = useState(null);
  const pick = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setState({ busy: true });
    try {
      const r = await uploadCleanupFile(file);
      setState({ saved: `Loaded ${r.label}. The decisions already made are kept.` });
      onLoaded();
    } catch (err) { setState({ error: reasonOf(err) }); }
  };
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      <input ref={ref} type="file" accept="application/json,.json" onChange={pick} style={{ display: "none" }} />
      <Button onClick={() => ref.current?.click()} disabled={state?.busy}
              title="A new audit file (axcion.books_cleanup/1). The decisions made here stay.">Load a new file</Button>
      <SaveLine state={state} />
    </span>
  );
}

/* Every change saved on the tab as one file, for an analysis outside the app (owners and admins). */
function DownloadSaved({ version }) {
  const [state, setState] = useState(null);
  const go = async () => {
    setState({ busy: true });
    try {
      const day = new Date().toISOString().slice(0, 10);
      await downloadSavedChanges(`books-cleanup-saved-changes-v${version || 0}-${day}.json`);
      setState({ saved: "Downloaded." });
    } catch (err) { setState({ error: reasonOf(err) }); }
  };
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      <Button onClick={go} disabled={state?.busy}
              title="Every decision and change saved here, as a JSON file. With the loaded file it rebuilds every figure.">
        {state?.busy ? "Preparing…" : "Download saved changes"}</Button>
      <SaveLine state={state?.busy ? null : state} />
    </span>
  );
}

export default function Cleanup({ role }) {
  const [page, setPage] = useState("statement");
  const [rev, setRev] = useState(0);
  const ov = useCleanupOverview(rev);
  const d = ov.data;
  const onSaved = () => setRev((r) => r + 1);
  const failing = d ? d.checks.filter((c) => !c.ok) : [];
  const counts = d ? { todo: d.open_items || 0 } : {};
  const isOwner = !role || role === "owner" || role === "admin";
  const title = PAGES.find(([k]) => k === page)?.[1];
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap", marginBottom: 10 }}>
        <ProductIcon name={iconFor("cleanup")} size={24} tone={T.meadow} />
        <span style={{ fontFamily: font.head, fontSize: 22, fontWeight: 600, color: T.ink }}>
          Books Cleanup<span style={{ color: T.muted, fontWeight: 500 }}> / {title}</span></span>
      </div>
      {d && (
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap", marginBottom: 14 }}>
          <span style={{ fontFamily: F.body, fontSize: 12.5, color: L.muted }}>
            {d.label}{d.uploaded_at ? ` · loaded ${d.uploaded_at.slice(0, 10)}` : ""}</span>
          <OkPill ok={!failing.length} title={failing.map((c) => c.label).join("\n") || undefined}>
            {failing.length ? `${failing.length} ${failing.length === 1 ? "check fails" : "checks fail"}` : "Every check passes"}</OkPill>
          <span style={{ fontFamily: F.body, fontSize: 12.5, color: L.muted }}>
            {d.checklist.done} of {d.checklist.total} steps done · {d.open_items} open {d.open_items === 1 ? "item" : "items"}</span>
          <div style={{ flex: 1 }} />
          {isOwner && d.can_decide && <DownloadSaved version={d.version} />}
          {isOwner && d.can_decide && <Upload onLoaded={onSaved} />}
        </div>
      )}
      <SubNav page={page} setPage={setPage} counts={counts} forward={!!(d?.forward_companies?.length || d?.forward_company)} />
      <StatePanel loading={ov.loading} error={ov.error} retry={ov.retry}>
        {d && (
          // A transaction's card changes the books from deep inside a page: it needs the overview, whether
          // this person may (owners and admins), the save counter, and the way to Decisions.
          <CleanupCtx.Provider value={{ ov: d, canDecide: isOwner && d.can_decide, onSaved, goto: setPage }}>
            {page === "statement" && <CleanupStatement ov={d} rev={rev} />}
            {page === "transactions" && <CleanupTransactions ov={d} rev={rev} />}
            {page === "forecast" && <CleanupForecast ov={d} rev={rev} onSaved={onSaved} />}
            {page === "budget" && <CleanupBudget ov={d} rev={rev} onSaved={onSaved} />}
            {page === "cash" && <CleanupCash ov={d} rev={rev} onSaved={onSaved} />}
            {page === "decisions" && <CleanupDecisions ov={d} rev={rev} onSaved={onSaved} />}
            {page === "corrections" && <CleanupCorrections ov={d} rev={rev} onSaved={onSaved} />}
            {page === "balance" && <CleanupBalance ov={d} rev={rev} />}
            {page === "interco" && <CleanupInterco ov={d} rev={rev} />}
            {page === "todo" && <CleanupTodo rev={rev} onSaved={onSaved} />}
            {page === "findings" && <CleanupFindings rev={rev} />}
          </CleanupCtx.Provider>
        )}
      </StatePanel>
    </div>
  );
}
