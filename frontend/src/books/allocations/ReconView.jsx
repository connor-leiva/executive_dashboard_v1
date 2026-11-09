/* Reconciliation (ALLOCATION-RULES-SPEC 5.8, 7.3).

   Per pool: what the books hold, what resolved, what did not, and the variance. BOTH columns
   always show (D10): there is no reading toggle here, because hiding one of two true figures is
   how somebody comes to believe the other is the only one.

   `unresolved` is the column this view exists for. A rule whose driver month was never committed
   resolves nothing while its pool still sits in the books, and a screen showing only `resolved`
   would make that rule look finished. */
import { useState } from "react";
import { LEDGER as L, LEDGER_FONT as F } from "../../theme.js";
import Money from "./Money.jsx";
import { reasonOf, scopeLabel, fmtMonth } from "./words.js";
import { Button } from "./controls.jsx";

/* Where a reason's fix lives. `meta.reasons[code].target` is the server's word for it; this
   maps the ones the Allocations page itself can reach to a sub-view. Targets outside the
   page (mapping, integrations, QuickBooks) fall back to Rules rather than pretending to
   navigate somewhere this component cannot reach. */
const TARGET_VIEW = { drivers: "drivers", rule: "rules", rules: "rules" };

const NUM = { fontFamily: F.num, fontVariantNumeric: "tabular-nums", textAlign: "right",
              whiteSpace: "nowrap" };
const EYEBROW = { fontFamily: F.num, fontSize: 12,
                  color: L.faint };

/* Ledger parens for a negative, the convention the rest of the module reads in. Formatting
   only: the number itself arrived from the server and is not touched. */


export default function ReconView({ data, month, go = () => {} }) {
  const [foldOpen, setFoldOpen] = useState(false);
  const meta = data?.meta || {};
  const rules = data?.rules || [];
  const totals = data?.totals || {};
  const months = data?.months || [];
  const check = data?.check;

  if (!rules.length) {
    return (
      <div style={{ background: L.card, border: `1px solid ${L.cardEdge}`, borderRadius: 14,
                    padding: 28, textAlign: "center" }}>
        <div style={{ fontFamily: F.body, fontSize: 13, color: L.body }}>
          No applied rules yet for this month.
        </div>
        <div style={{ fontFamily: F.body, fontSize: 12, color: L.muted, marginTop: 6 }}>
          Nothing to reconcile until a rule claims a pool.
        </div>
      </div>
    );
  }

  /* Pending first by amount, then rules with activity, then the quiet ones folded away
     (6.4, principle 2). A pending row offered no way to fix it, so the action comes from
     the reason and lands on the view that fixes it. */
  const sorted = [...rules].sort((a, b) =>
    Number(!!b.pending?.length) - Number(!!a.pending?.length)
    || Number(b.unresolved_amount || 0) - Number(a.unresolved_amount || 0)
    || Number(b.pool_amount || 0) - Number(a.pool_amount || 0)
    || String(a.pool_name).localeCompare(String(b.pool_name)));
  const live = sorted.filter((r) => Number(r.pool_amount || 0) !== 0 || r.pending?.length);
  const quiet = sorted.filter((r) => !live.includes(r));
  const shown = foldOpen ? [...live, ...quiet] : live;

  return (
    <div style={{ display: "grid", gap: 18 }}>
      <div style={{ background: L.paper, border: `1px solid ${L.cardEdge}`, borderRadius: 14,
                    overflow: "hidden" }}>
        <div className="al-recon-row" style={{ display: "grid",
                      gap: 10, padding: "11px 16px", background: L.colHead,
                      borderBottom: `1px solid ${L.ruleHeader}` }}>
          <span style={EYEBROW}>Pool</span>
          <span style={{ ...EYEBROW, textAlign: "right" }}>In the books</span>
          <span className="al-hide-narrow" style={{ ...EYEBROW, textAlign: "right" }}>Resolved</span>
          {/* VARIANCE IS GONE (U6, D3). It was `resolved - pool` beside `pool - resolved`:
              the same figure with the sign flipped, so every pending pool was reported
              twice, once in gold and once in parentheses. The API field stays. */}
          <span style={{ ...EYEBROW, textAlign: "right" }}>Not resolved</span>
        </div>
        {shown.map((r) => (
          <div key={r.rule_id}>
            <div className="al-recon-row" style={{ display: "grid",
                          gap: 10, padding: "12px 16px",
                          borderBottom: `1px solid ${L.ruleLine}`,
                          background: r.unresolved_amount ? L.flagBg : L.paper }}>
              <span style={{ minWidth: 0 }}>
                <span style={{ fontFamily: F.body, fontSize: 13, fontWeight: 600,
                               color: L.deep }}>{r.pool_name}</span>
                <span style={{ fontFamily: F.body, fontSize: 12, color: L.muted,
                               display: "block", overflow: "hidden",
                               textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {scopeLabel(meta, r.scope_kind)} · {r.pattern}
                </span>
              </span>
              <Money v={r.pool_amount} />
              <span className="al-hide-narrow"><Money v={r.resolved_amount} /></span>
              <Money v={r.unresolved_amount}
                     tone={r.unresolved_amount ? L.flagInk : undefined} />
            </div>
            {(r.pending || []).map((p, i) => (
              <div key={i} style={{ display: "flex", justifyContent: "space-between",
                                    gap: 12, padding: "8px 16px 8px 30px",
                                    background: L.flagBg,
                                    borderBottom: `1px solid ${L.ruleLine}` }}>
                <span style={{ fontFamily: F.body, fontSize: 12, color: L.flagInk,
                               flex: "1 1 auto", minWidth: 0 }}>
                  {fmtMonth(p.month)} · {reasonOf(meta, p.reason)?.label
                    || "Not resolved, and the engine did not say why"}
                </span>
                <Money v={p.amount} size={12.5} tone={L.flagInk} />
                {/* EVERY PENDING ITEM LINKS TO ITS FIX (principle 5). The action and the
                    view it lands on come from `meta.reasons`, so a new reason arrives as a
                    server change with no edit here. */}
                {reasonOf(meta, p.reason)?.action && (
                  <Button kind="quiet" size="card"
                          onClick={() => go(TARGET_VIEW[reasonOf(meta, p.reason).target]
                                            || "rules")}>
                    {reasonOf(meta, p.reason).action}
                    {reasonOf(meta, p.reason).target === "drivers"
                      ? ` ${fmtMonth(p.month)}` : ""} &rsaquo;
                  </Button>
                )}
              </div>
            ))}
          </div>
        ))}

        {!!quiet.length && (
          <button onClick={() => setFoldOpen(!foldOpen)} className="al-ctl"
                  style={{ display: "flex", alignItems: "center", gap: 8, width: "100%",
                           padding: "11px 16px", background: L.controls, border: "none",
                           borderTop: `1px solid ${L.ruleLine}`, cursor: "pointer",
                           fontFamily: F.body, fontSize: 13, color: L.muted,
                           textAlign: "left" }}>
            <span aria-hidden style={{ display: "inline-block",
                                       transform: foldOpen ? "rotate(90deg)" : "none",
                                       transition: "transform 130ms" }}>&rsaquo;</span>
            {quiet.length} rule{quiet.length === 1 ? "" : "s"} with nothing booked in{" "}
            {month && month !== "ytd" ? fmtMonth(month) : "this span"}
          </button>
        )}

        {/* The foot, on the deep ground the mockup uses here. Summed by the server. */}
        <div className="al-recon-row"
             style={{ display: "grid", gap: 10, padding: "13px 16px", background: L.deep }}>
          <span style={{ fontFamily: F.body, fontSize: 14, fontWeight: 600,
                         color: L.onDeep }}>
            {totals.count || rules.length} rule
            {(totals.count || rules.length) === 1 ? "" : "s"}
          </span>
          <Money v={totals.pool_amount} size={15} weight={700} tone={L.paper} />
          <span className="al-hide-narrow">
            <Money v={totals.resolved_amount} size={15} weight={600} tone={L.onDeepPct} />
          </span>
          <Money v={totals.unresolved_amount} size={13} weight={700} tone={L.onDeep} />
        </div>
      </div>

      {check && (
        <div style={{ background: L.card, border: `1px solid ${L.cardEdge}`,
                      borderRadius: 14, padding: 16 }}>
          <div style={{ ...EYEBROW, marginBottom: 8 }}>Intercompany</div>
          <div style={{ fontFamily: F.body, fontSize: 13, color: L.body }}>
            {check.zero_sum?.balanced
              ? "Every contribution nets to zero across the portfolio."
              : "The portfolio does not net to zero, which means a row points outside it."}
          </div>
          <div style={{ fontFamily: F.body, fontSize: 12, color: L.muted, marginTop: 6 }}>
            This check reads observed rows only: an applied allocation has no intercompany
            movement behind it, so it cannot appear here.
          </div>
        </div>
      )}

      <div style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>
        {months.length
          ? `Covering ${months.map(fmtMonth).join(", ")}.`
          : "No complete months in this span yet."}
      </div>
    </div>
  );
}
