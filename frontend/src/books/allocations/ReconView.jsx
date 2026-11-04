/* Reconciliation (ALLOCATION-RULES-SPEC 5.8, 7.3).

   Per pool: what the books hold, what resolved, what did not, and the variance. BOTH columns
   always show (D10): there is no reading toggle here, because hiding one of two true figures is
   how somebody comes to believe the other is the only one.

   `unresolved` is the column this view exists for. A rule whose driver month was never committed
   resolves nothing while its pool still sits in the books, and a screen showing only `resolved`
   would make that rule look finished. */
import { LEDGER as L, LEDGER_FONT as F } from "../../theme.js";
import Money from "./Money.jsx";
import { reasonOf, scopeLabel, fmtMonth } from "./words.js";

const NUM = { fontFamily: F.num, fontVariantNumeric: "tabular-nums", textAlign: "right",
              whiteSpace: "nowrap" };
const EYEBROW = { fontFamily: F.num, fontSize: 10, letterSpacing: ".14em",
                  textTransform: "uppercase", color: L.faint };

/* Ledger parens for a negative, the convention the rest of the module reads in. Formatting
   only: the number itself arrived from the server and is not touched. */


export default function ReconView({ data, month, go = () => {} }) {
  const meta = data?.meta || {};
  const rules = data?.rules || [];
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
          <span style={{ ...EYEBROW, textAlign: "right" }}>Unresolved</span>
          <span className="al-hide-narrow" style={{ ...EYEBROW, textAlign: "right" }}>Variance</span>
        </div>
        {rules.map((r) => (
          <div key={r.rule_id}>
            <div className="al-recon-row" style={{ display: "grid",
                          gap: 10, padding: "12px 16px",
                          borderBottom: `1px solid ${L.ruleLine}`,
                          background: r.unresolved_amount ? L.flagBg : L.paper }}>
              <span style={{ minWidth: 0 }}>
                <span style={{ fontFamily: F.body, fontSize: 13, fontWeight: 600,
                               color: L.deep }}>{r.pool_name}</span>
                <span style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted,
                               display: "block", overflow: "hidden",
                               textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {scopeLabel(meta, r.scope_kind)} · {r.pattern}
                </span>
              </span>
              <Money v={r.pool_amount} />
              <span className="al-hide-narrow"><Money v={r.resolved_amount} /></span>
              <Money v={r.unresolved_amount}
                     tone={r.unresolved_amount ? L.flagInk : L.muted} />
              <span className="al-hide-narrow"><Money v={r.variance} /></span>
            </div>
            {(r.pending || []).map((p, i) => (
              <div key={i} style={{ display: "flex", justifyContent: "space-between",
                                    gap: 12, padding: "8px 16px 8px 30px",
                                    background: L.flagBg,
                                    borderBottom: `1px solid ${L.ruleLine}` }}>
                <span style={{ fontFamily: F.body, fontSize: 12, color: L.flagInk }}>
                  {fmtMonth(p.month)} · {reasonOf(meta, p.reason)?.label
                    || "Not resolved, and the engine did not say why"}
                </span>
                <Money v={p.amount} tone={L.flagInk} />
              </div>
            ))}
          </div>
        ))}
      </div>

      {check && (
        <div style={{ background: L.card, border: `1px solid ${L.cardEdge}`,
                      borderRadius: 14, padding: 16 }}>
          <div style={{ ...EYEBROW, marginBottom: 8 }}>Intercompany</div>
          <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.body }}>
            {check.zero_sum?.balanced
              ? "Every contribution nets to zero across the portfolio."
              : "The portfolio does not net to zero, which means a row points outside it."}
          </div>
          <div style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted, marginTop: 6 }}>
            This check reads observed rows only: an applied allocation has no intercompany
            movement behind it, so it cannot appear here.
          </div>
        </div>
      )}

      <div style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted }}>
        {months.length
          ? `Covering ${months.map(fmtMonth).join(", ")}.`
          : "No complete months in this span yet."}
      </div>
    </div>
  );
}
