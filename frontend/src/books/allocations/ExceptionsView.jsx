/* Exceptions (ALLOCATION-RULES-SPEC 5.6, 7.3).

   Shared cost on a pool entity that no rule claims. Three states, and the difference between
   two of them matters: an EMPTY queue means the work is done and says so, while an
   UNCONFIGURED one means nobody has said which entities carry shared cost. Rendering the
   second as the first would quietly congratulate somebody for a list that was never built.

   One action per card, Create Rule From This, which opens the drawer prefilled. */
import { useState } from "react";
import { LEDGER as L, LEDGER_FONT as F } from "../../theme.js";
import { putJSON } from "../../api";

const NUM = { fontFamily: F.num, fontVariantNumeric: "tabular-nums", textAlign: "right",
              whiteSpace: "nowrap" };
const EYEBROW = { fontFamily: F.num, fontSize: 10, letterSpacing: ".14em",
                  textTransform: "uppercase", color: L.faint };

function Money({ v, size = 13 }) {
  const n = Number(v || 0);
  const body = Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2,
                                                       maximumFractionDigits: 2 });
  return <span style={{ ...NUM, fontSize: size, color: L.deep }}>
    {n < 0 ? `(${body})` : body}</span>;
}

/* Gold means a judgment or an unresolved item, never an error (7.2). A gap is not a mistake:
   it is the reason this row needs a person. */
const GAP = {
  no_payee: "No payee",
  no_memo: "No memo",
  multi_line: "More than one account",
  recurring: "Repeats this period",
};

function Gap({ code }) {
  return (
    <span style={{ fontFamily: F.body, fontSize: 10.5, color: L.flagInk,
                   background: L.flagBg, border: `1px solid ${L.flagEdge}`,
                   borderRadius: 999, padding: "2px 8px", whiteSpace: "nowrap" }}>
      {GAP[code] || code}
    </span>
  );
}

function SetupState({ businesses, onSaved }) {
  const [picked, setPicked] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const toggle = (id) =>
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await putJSON("/books/coa/settings/alloc-pools", { business_ids: picked });
      onSaved();
    } catch (e) {
      setErr(e?.message || "That did not save.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ background: L.card, border: `1px solid ${L.cardEdge}`, borderRadius: 14,
                  padding: 24, maxWidth: 560 }}>
      <div style={{ ...EYEBROW, marginBottom: 8 }}>Set up</div>
      <div style={{ fontFamily: F.body, fontSize: 13.5, color: L.deep, fontWeight: 600,
                    marginBottom: 6 }}>
        Which entities carry shared cost?
      </div>
      <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.body, marginBottom: 16 }}>
        Their unclaimed expense is what this queue lists. It is the one thing the engine cannot
        work out for itself: an entity with no rule pointing at it looks exactly like one nobody
        has got to yet.
      </div>
      <div style={{ display: "grid", gap: 8, marginBottom: 16 }}>
        {(businesses || []).map((b) => (
          <label key={b.id} style={{ display: "flex", alignItems: "center", gap: 9,
                                     cursor: "pointer" }}>
            <input type="checkbox" checked={picked.includes(b.id)}
                   onChange={() => toggle(b.id)} />
            <span aria-hidden style={{ width: 7, height: 7, borderRadius: "50%",
                                       background: b.accent || L.faint }} />
            <span style={{ fontFamily: F.body, fontSize: 13, color: L.body }}>{b.name}</span>
          </label>
        ))}
        {!businesses?.length && (
          <span style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>
            No entities to choose from yet.
          </span>
        )}
      </div>
      {err && (
        <div style={{ fontFamily: F.body, fontSize: 12, color: L.flagInk, marginBottom: 10 }}>
          {err}
        </div>
      )}
      <button onClick={save} disabled={busy || !picked.length}
        style={{ fontFamily: F.body, fontSize: 12.5, fontWeight: 600,
                 color: picked.length ? L.paper : L.muted,
                 background: picked.length ? L.deep : L.track,
                 border: "none", borderRadius: 9, padding: "8px 16px",
                 cursor: picked.length && !busy ? "pointer" : "default" }}>
        {busy ? "Saving" : "Save"}
      </button>
    </div>
  );
}

export default function ExceptionsView({ data, period, isCFO, businesses, refresh,
                                         onCreateRule = () => {} }) {
  if (data && data.configured === false) {
    return <SetupState businesses={businesses} onSaved={refresh} />;
  }

  const rows = data?.rows || [];

  if (!rows.length) {
    return (
      <div style={{ background: L.card, border: `1px solid ${L.cardEdge}`, borderRadius: 14,
                    padding: 28, textAlign: "center" }}>
        <div style={{ fontFamily: F.body, fontSize: 13.5, color: L.deep, fontWeight: 600 }}>
          Nothing unclaimed this period.
        </div>
        <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.muted, marginTop: 6 }}>
          An empty queue is the goal: every shared cost on a pool entity is covered by a rule.
        </div>
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 14,
                    flexWrap: "wrap" }}>
        <span style={EYEBROW}>Unclaimed</span>
        <Money v={data?.total} size={18} />
        <span style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>
          across {rows.length} transaction{rows.length === 1 ? "" : "s"} in {period}
        </span>
      </div>

      <div style={{ display: "grid", gap: 10 }}>
        {rows.map((r) => (
          <div key={r.txn_id} style={{ background: L.paper, border: `1px solid ${L.cardEdge}`,
                                       borderRadius: 12, padding: "13px 15px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 14,
                          alignItems: "baseline", flexWrap: "wrap" }}>
              <span style={{ minWidth: 0 }}>
                <span style={{ fontFamily: F.body, fontSize: 13.5, fontWeight: 600,
                               color: L.deep }}>
                  {r.payee || "No payee on this transaction"}
                </span>
                <span style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted,
                               display: "block" }}>
                  {r.account} · {r.txn_date} · {r.qbo_type}
                </span>
              </span>
              <Money v={r.amount} size={14} />
            </div>

            {r.memo && (
              <div style={{ fontFamily: F.body, fontSize: 12, color: L.body, marginTop: 8 }}>
                {r.memo}
              </div>
            )}

            <div style={{ display: "flex", gap: 6, marginTop: 10, flexWrap: "wrap",
                          alignItems: "center" }}>
              {(r.gaps || []).map((g) => <Gap key={g} code={g} />)}
              {isCFO && (
                <button onClick={() => onCreateRule(r)}
                  style={{ marginLeft: "auto", fontFamily: F.body, fontSize: 12,
                           fontWeight: 600, color: L.deep, background: L.controls,
                           border: `1px solid ${L.ruleInner}`, borderRadius: 9,
                           padding: "6px 12px", cursor: "pointer" }}>
                  Create a rule from this
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
