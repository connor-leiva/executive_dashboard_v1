/* Exceptions (ALLOCATION-RULES-SPEC 5.6, 7.3).

   Shared cost on a pool entity that no rule claims. THREE states, and all three say different
   things. An EMPTY queue means the work is done. An UNCONFIGURED one means nobody has said
   which entities carry shared cost. And a CONFIGURED-WITH-NO-POOL one means the queue is
   examining nothing at all, which reads as the first and is the opposite of it. This file's
   header used to claim only two states mattered, and the empty card then congratulated the
   third for a list that was never built, exactly the misread `exceptions` in the engine warns
   an empty list "would render as".

   Which entities are pools is shown, not just stored, and can be changed after the first save.
   It could not before: the picker rendered only while `configured === false`, so one mis-tick
   was permanent as far as the product was concerned.

   One action per card, Create Rule From This, which opens the drawer prefilled. */
import { useState } from "react";
import { LEDGER as L, LEDGER_FONT as F } from "../../theme.js";
import Money from "./Money.jsx";
import { fmtMonth, gapLabel } from "./words.js";

/* The month in words. "in mtd" was a period TOKEN rendered into a sentence. */
const monthWords = (m) => (!m || m === "ytd" ? "the year to date" : fmtMonth(m));
import { putJSON } from "../../api";

const NUM = { fontFamily: F.num, fontVariantNumeric: "tabular-nums", textAlign: "right",
              whiteSpace: "nowrap" };
const EYEBROW = { fontFamily: F.num, fontSize: 10, letterSpacing: ".14em",
                  textTransform: "uppercase", color: L.faint };


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

/* `initial` present means this is a CHANGE, not first setup, so it opens pre-ticked and offers a
   way out. Without it the first save was a one-way door: the picker rendered only while
   `configured === false`, so a mis-tick, or a sixth entity added later, could not be corrected
   from the product at all. */
function SetupState({ businesses, onSaved, initial = null, onCancel = null }) {
  const again = Array.isArray(initial);
  const [picked, setPicked] = useState(again ? initial : []);
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
      <div style={{ ...EYEBROW, marginBottom: 8 }}>{again ? "Change" : "Set up"}</div>
      <div style={{ fontFamily: F.body, fontSize: 13.5, color: L.deep, fontWeight: 600,
                    marginBottom: 6 }}>
        Which entities carry shared cost?
      </div>
      <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.body, marginBottom: 16 }}>
        {again
          ? `This setting decides what the queue below looks at, and nothing else. Rules you
             have already written stay exactly as they are, and no allocated figure moves.
             Un-ticking an entity only stops its unclaimed cost being listed here.`
          : `Their unclaimed expense is what this queue lists. It is the one thing the engine
             cannot work out for itself: an entity with no rule pointing at it looks exactly
             like one nobody has got to yet.`}
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
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <button onClick={save} disabled={busy || !picked.length}
          style={{ fontFamily: F.body, fontSize: 12.5, fontWeight: 600,
                   color: picked.length ? L.paper : L.muted,
                   background: picked.length ? L.deep : L.track,
                   border: "none", borderRadius: 9, padding: "8px 16px",
                   cursor: picked.length && !busy ? "pointer" : "default" }}>
          {busy ? "Saving" : "Save"}
        </button>
        {onCancel && (
          <button onClick={onCancel} disabled={busy}
            style={{ fontFamily: F.body, fontSize: 12.5, fontWeight: 600, color: L.body,
                     background: L.controls, border: `1px solid ${L.ruleInner}`,
                     borderRadius: 9, padding: "8px 14px",
                     cursor: busy ? "default" : "pointer" }}>
            Cancel
          </button>
        )}
      </div>
    </div>
  );
}

/* Which entities this queue is looking at, and the way back into that choice. Named rather than
   counted: "2 entities" does not tell you WHICH, and the whole queue is scoped by them. */
function PoolStrip({ names, isCFO, onChange }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
                  marginBottom: 14 }}>
      <span style={EYEBROW}>Carrying shared cost</span>
      <span style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
                     minWidth: 0 }}>
        {names.map((b) => (
          <span key={b.id} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span aria-hidden style={{ width: 7, height: 7, borderRadius: "50%",
                                       background: b.accent || L.faint }} />
            <span style={{ fontFamily: F.body, fontSize: 12.5, color: L.body }}>{b.name}</span>
          </span>
        ))}
        {!names.length && (
          <span style={{ fontFamily: F.body, fontSize: 12.5, color: L.flagInk }}>
            None
          </span>
        )}
      </span>
      {isCFO && (
        <button onClick={onChange}
          style={{ fontFamily: F.body, fontSize: 11.5, fontWeight: 600, color: L.body,
                   background: L.controls, border: `1px solid ${L.ruleInner}`,
                   borderRadius: 8, padding: "4px 10px", cursor: "pointer" }}>
          Change
        </button>
      )}
    </div>
  );
}

export default function ExceptionsView({ data, month, isCFO, businesses, refresh,
                                         onCreateRule = () => {} }) {
  const [editing, setEditing] = useState(false);

  const poolIds = (data?.pool_business_ids || []).map(String);

  if ((data && data.configured === false) || editing) {
    return <SetupState businesses={businesses}
                       initial={editing ? poolIds : null}
                       onCancel={editing ? () => setEditing(false) : null}
                       onSaved={() => { setEditing(false); refresh(); }} />;
  }

  const poolNames = poolIds
    .map((id) => (businesses || []).find((b) => String(b.id) === id))
    .filter(Boolean);
  const strip = <PoolStrip names={poolNames} isCFO={isCFO}
                           onChange={() => setEditing(true)} />;

  const rows = data?.rows || [];

  if (!rows.length) {
    /* THE THIRD STATE. This file's own header named two, configured and unconfigured, and the
       engine's comment at `exceptions` says an empty list "would render as" nothing-to-do. It
       did: a tenant that is configured with NO pool entity examines nothing, and was told the
       work was finished. An empty queue is only an achievement if something was looked at. */
    const nothingWatched = !poolIds.length;
    return (
      <div>
        {strip}
        <div style={{ background: L.card, border: `1px solid ${L.cardEdge}`, borderRadius: 14,
                      padding: 28, textAlign: "center" }}>
          <div style={{ fontFamily: F.body, fontSize: 13.5, color: L.deep, fontWeight: 600 }}>
            {nothingWatched
              ? "No entity is set as carrying shared cost."
              : `Nothing unclaimed in ${monthWords(month)}.`}
          </div>
          <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.muted, marginTop: 6 }}>
            {nothingWatched
              ? `So this queue is examining nothing, which is not the same as having nothing
                 left to do. Choose the entities that carry shared cost and it will fill.`
              : `An empty queue is the goal: every shared cost on a pool entity is covered by
                 a rule.`}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div>
      {strip}
      <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 14,
                    flexWrap: "wrap" }}>
        <span style={EYEBROW}>Unclaimed</span>
        <Money v={data?.total} size={18} />
        <span style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>
          across {rows.length} transaction{rows.length === 1 ? "" : "s"}
          {month ? ` in ${monthWords(month)}` : ""}
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
