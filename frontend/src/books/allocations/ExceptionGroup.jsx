/* One card per payee in the exception queue (UI spec 6.2, D8).
 *
 * A RULE IS PER PAYEE, so the queue is too. Per charge, a vendor billing monthly filled the
 * screen with its own name and a reader answered the same question once per charge (U15).
 *
 * The card says what is known from the DATA: payee, account, how many charges, the latest
 * date, and the gaps. The mockup's version of this line is a written sentence about each
 * transaction ("the amount is round, which usually means it was a manually keyed transfer"),
 * which is designer inference and is exactly what ALLOCATION-RULES-SPEC 7.8 forbids shipping.
 * Nothing in the payload can write it, so the card does not pretend to.
 */
import { useState } from "react";
import { LEDGER as L, LEDGER_FONT as F } from "../../theme.js";
import Money from "./Money.jsx";
import { Button } from "./controls.jsx";
import { fmtDay, gapLabel } from "./words.js";

function Gap({ code, meta }) {
  return (
    <span style={{ fontFamily: F.body, fontSize: 12, fontWeight: 600, color: L.flagInk,
                   background: L.flagBg, border: `1px solid ${L.flagEdge}`,
                   borderRadius: 5, padding: "3px 9px", whiteSpace: "nowrap" }}>
      {gapLabel(meta, code)}
    </span>
  );
}

export default function ExceptionGroup({ group, meta, businesses, poolName, isCFO,
                                         rows, onCreateRule, onKeepOn }) {
  const [open, setOpen] = useState(false);
  const charges = (rows || []).filter((r) => group.txn_ids.includes(r.txn_id));
  const suggested = group.suggestion
    ? businesses.find((b) => b.id === group.suggestion.business_id)
    : null;

  return (
    <div style={{ background: L.paper, border: `1px solid ${L.ruleInner}`, borderRadius: 13,
                  overflow: "hidden" }}>
      <div style={{ padding: "14px 16px 15px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 14,
                      alignItems: "baseline", flexWrap: "wrap" }}>
          <span style={{ minWidth: 0 }}>
            <span style={{ fontFamily: F.body, fontSize: 14, fontWeight: 600,
                           color: L.deep }}>
              {group.payee || "No payee on these charges"}
            </span>
            <span style={{ fontFamily: F.num, fontSize: 12, color: L.code,
                           display: "block", marginTop: 2 }}>
              {group.accounts.join(", ")}
              {poolName ? ` · on ${poolName}` : ""}
              {group.latest_date ? ` · latest ${fmtDay(group.latest_date)}` : ""}
            </span>
          </span>
          <span style={{ textAlign: "right" }}>
            <Money v={group.total} size={20} weight={600} />
            <span style={{ fontFamily: F.body, fontSize: 13, color: L.muted,
                           display: "block" }}>
              {group.count} charge{group.count === 1 ? "" : "s"}
            </span>
          </span>
        </div>

        {suggested && (
          <div style={{ fontFamily: F.body, fontSize: 12, color: L.accentInk, marginTop: 9,
                        display: "flex", alignItems: "center", gap: 6 }}>
            <span aria-hidden style={{ width: 8, height: 8, borderRadius: 1.5,
                                       background: L.deep, flex: "0 0 auto" }} />
            Suggested: the whole cost to {suggested.name}, from the Payables vendor record
          </div>
        )}

        <div style={{ display: "flex", gap: 6, marginTop: 10, flexWrap: "wrap" }}>
          {(group.gaps || []).map((g) => <Gap key={g} code={g} meta={meta} />)}
          {group.count > 1 && (
            <span style={{ fontFamily: F.body, fontSize: 12, fontWeight: 600,
                           color: L.flagInk, background: L.flagBg,
                           border: `1px solid ${L.flagEdge}`, borderRadius: 5,
                           padding: "3px 9px" }}>
              {group.count} this month
            </span>
          )}
        </div>
      </div>

      {isCFO && (
        <div style={{ borderTop: `1px solid ${L.ruleGroup}`, background: L.controls,
                      padding: "11px 16px", display: "flex", gap: 8, alignItems: "center",
                      flexWrap: "wrap" }}>
          <Button kind="primary" size="card"
                  onClick={() => onCreateRule(group)}>Create rule</Button>
          {/* Keep on still opens the drawer, at Tier 4, with everything filled but the
              reason. One click to the drawer, not one click to a rule: the reason is the
              feature (D7 of this document). */}
          <Button size="card" onClick={() => onKeepOn(group)}>
            Keep on {poolName || "this entity"}
          </Button>
          <span style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>
            Clears {group.count} charge{group.count === 1 ? "" : "s"}
          </span>
        </div>
      )}

      {charges.length > 1 && (
        <button onClick={() => setOpen(!open)} className="al-ctl"
                style={{ width: "100%", textAlign: "left", background: "transparent",
                         border: "none", borderTop: `1px solid ${L.ruleLine}`,
                         padding: "9px 16px", cursor: "pointer", fontFamily: F.body,
                         fontSize: 12, color: L.accentInk, fontWeight: 600 }}>
          <span aria-hidden style={{ display: "inline-block", marginRight: 6,
                                     transform: open ? "rotate(90deg)" : "none",
                                     transition: "transform 130ms" }}>&rsaquo;</span>
          {open ? "Hide charges" : `Show ${charges.length} charges`}
        </button>
      )}

      {open && charges.map((r) => (
        <div key={r.txn_id} style={{ display: "flex", gap: 10, alignItems: "baseline",
                                     padding: "7px 16px", borderTop: `1px solid ${L.ruleLine}`,
                                     fontFamily: F.num, fontSize: 12 }}>
          <span style={{ color: L.code, flex: "0 0 auto" }}>{fmtDay(r.txn_date)}</span>
          <span style={{ color: L.muted, flex: "0 0 auto" }}>
            {(r.account || "").split(":").pop()}
          </span>
          {/* The real memo, ellipsised, with the full text on hover. Not a sentence about
              the charge: the data has a memo and nothing else (7.8). */}
          <span title={r.memo || ""}
                style={{ color: L.faint, flex: "1 1 auto", minWidth: 0, overflow: "hidden",
                         textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {r.memo || "No memo"}
          </span>
          <Money v={r.amount} size={11.5} />
        </div>
      ))}
    </div>
  );
}
