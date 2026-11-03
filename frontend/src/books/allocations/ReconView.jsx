/* Reconciliation (ALLOCATION-RULES-SPEC 5.8, 7.3).

   Per pool: what the books hold, what resolved, what did not, and the variance. BOTH columns
   always show (D10): there is no reading toggle here, because hiding one of two true figures is
   how somebody comes to believe the other is the only one.

   `unresolved` is the column this view exists for. A rule whose driver month was never committed
   resolves nothing while its pool still sits in the books, and a screen showing only `resolved`
   would make that rule look finished. */
import { LEDGER as L, LEDGER_FONT as F } from "../../theme.js";

const NUM = { fontFamily: F.num, fontVariantNumeric: "tabular-nums", textAlign: "right",
              whiteSpace: "nowrap" };
const EYEBROW = { fontFamily: F.num, fontSize: 10, letterSpacing: ".14em",
                  textTransform: "uppercase", color: L.faint };

/* Ledger parens for a negative, the convention the rest of the module reads in. Formatting
   only: the number itself arrived from the server and is not touched. */
function Money({ v, tone }) {
  const n = Number(v || 0);
  const body = Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2,
                                                       maximumFractionDigits: 2 });
  return (
    <span style={{ ...NUM, color: tone || (n < 0 ? L.flagInk : L.body) }}>
      {n < 0 ? `(${body})` : body}
    </span>
  );
}

const REASON = {
  no_driver_values: "No driver numbers committed for this month",
  driver_subject_missing: "The driver has no numbers for this person",
  fixed_exceeds_pool: "The fixed amounts come to more than the pool",
  allocation_exceeds_pool: "The shares and fixed amounts together exceed the pool",
  source_not_connected: "No spend from the outside source this month",
  source_unmapped_spend: "Some spend matched no entity in the map",
  double_count_risk: "The target already books this as a shared-service charge",
  multi_line: "A transaction with more than one account, left for review",
  unmapped_account: "An account with no mapping",
  vendor_rule_collision: "Two rules claim the same payee",
};

export default function ReconView({ data, period }) {
  const rules = data?.rules || [];
  const months = data?.months || [];
  const check = data?.check;

  if (!rules.length) {
    return (
      <div style={{ background: L.card, border: `1px solid ${L.cardEdge}`, borderRadius: 14,
                    padding: 28, textAlign: "center" }}>
        <div style={{ fontFamily: F.body, fontSize: 13, color: L.body }}>
          No applied rules yet for this period.
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
                  {r.scope_kind} · {r.pattern}
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
                  {p.month} · {REASON[p.reason] || p.method || "Not resolved"}
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
          ? `Covering ${months.length} month${months.length === 1 ? "" : "s"} of ${period}.`
          : `No complete months inside ${period} yet.`}
      </div>
    </div>
  );
}
