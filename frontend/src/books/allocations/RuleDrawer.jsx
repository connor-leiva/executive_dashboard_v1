/* The rule drawer (ALLOCATION-RULES-SPEC 7.4).

   One drawer, three entry points: New Rule, Supersede, and Create From Exception. Five steps,
   and step three changes with the tier.

   TWO THINGS THIS DRAWER REFUSES TO DO.

   It does not disable Save and leave somebody guessing. It names ONE blocker at a time, in the
   order the steps are in, because a greyed-out button with five silent reasons is a puzzle
   rather than a form.

   It does not compute. The preview above the actions comes from the server on every change, so
   the figures somebody reads before saving are the same figures the engine will write. The one
   arithmetic exception the spec allows is the running total of shares that a person is typing,
   and that is a check on their own input rather than a figure being reported. */
import { useEffect, useMemo, useRef, useState } from "react";
import { LEDGER as L, LEDGER_FONT as F, alpha } from "../../theme.js";
import { reasonOf, fmtMonth } from "./words.js";
import { postJSON } from "../../api";
import { TierMarker, TierCode } from "./TierMarker.jsx";

const NUM = { fontFamily: F.num, fontVariantNumeric: "tabular-nums", textAlign: "right",
              whiteSpace: "nowrap" };
const EYEBROW = { fontFamily: F.num, fontSize: 10, letterSpacing: ".14em",
                  textTransform: "uppercase", color: L.faint };
const FIELD = { fontFamily: F.body, fontSize: 13, color: L.body, background: L.paper,
                border: `1px solid ${L.cardEdge}`, borderRadius: 9, padding: "7px 10px",
                width: "100%", boxSizing: "border-box" };

const MONTHS = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"];
const MONTH_LABEL = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const METHOD_FOR_TIER = { 1: "direct", 2: "traced", 3: "split", 4: "retained" };


function money(v) {
  const n = Number(v || 0);
  const body = Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2,
                                                       maximumFractionDigits: 2 });
  return n < 0 ? `(${body})` : body;
}

function Step({ n, title, children, note }) {
  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 8 }}>
        <span style={{ ...EYEBROW, color: L.muted }}>{n}</span>
        <span style={{ fontFamily: F.body, fontSize: 13, fontWeight: 600, color: L.deep }}>
          {title}
        </span>
      </div>
      {note && (
        <div style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted, marginBottom: 8 }}>
          {note}
        </div>
      )}
      {children}
    </div>
  );
}

export default function RuleDrawer({ open, mode = "new", rule = null, prefill = null,
                                     businesses = [], tiers = [], onClose, onSaved }) {
  const [scopeKind, setScopeKind] = useState("account");
  const [pattern, setPattern] = useState("");
  const [poolBiz, setPoolBiz] = useState("");
  const [tier, setTier] = useState(null);
  const [targets, setTargets] = useState([]);
  const [rationale, setRationale] = useState("");
  const [month, setMonth] = useState(`2026-${MONTHS[0]}-01`);
  const [reach, setReach] = useState("forward");
  const [preview, setPreview] = useState(null);
  const [previewErr, setPreviewErr] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState(null);
  const timer = useRef(null);

  // Prefilled from an exception: the payee becomes a vendor scope and the entity it was booked
  // on becomes the pool, because those are the two facts the row already knows.
  useEffect(() => {
    if (!open) return;
    if (prefill) {
      setScopeKind(prefill.payee ? "vendor" : "account");
      setPattern(prefill.payee || prefill.account || "");
      setPoolBiz(prefill.business_id || "");
      setMonth(`${(prefill.txn_date || "2026-01-01").slice(0, 7)}-01`);
    } else if (rule) {
      setScopeKind(rule.scope_kind);
      setPattern(rule.pattern);
      setPoolBiz(rule.pool_business_id);
      setTier(rule.current?.tier ?? null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, prefill?.txn_id, rule?.id]);

  const method = tier ? METHOD_FOR_TIER[tier] : null;

  // The only arithmetic the spec allows in the browser: the total of shares a person is
  // typing, so the form can tell them it does not reach one yet. It is a check on their input,
  // not a figure being reported, and the server validates it again on save.
  const shareTotal = useMemo(
    () => targets.reduce((a, t) => a + (parseFloat(t.share) || 0), 0),
    [targets],
  );

  const draft = useMemo(() => ({
    scope_kind: scopeKind, pattern, source_business_id: poolBiz, method,
    effective_from: month, reach, rationale,
    targets: method === "direct" || method === "split"
      ? targets.filter((t) => t.business_id).map((t) => ({ business_id: t.business_id,
                                                           share: String(t.share || "0") }))
      : null,
  }), [scopeKind, pattern, poolBiz, method, month, reach, rationale, targets]);

  /* ONE blocker at a time, in step order. A disabled button with five silent reasons is a
     puzzle; this is the next thing to do. */
  const blocker = useMemo(() => {
    if (!pattern.trim()) return "Say what this rule matches.";
    if (pattern.trim().length < 3) return "A rule needs at least three characters to match on.";
    if (!poolBiz) return "Choose the entity this cost is booked on.";
    if (!tier) return "Choose whether this is a fact or a judgment.";
    if (method === "direct" && targets.filter((t) => t.business_id).length !== 1) {
      return "A direct rule sends the whole cost to exactly one entity.";
    }
    if (method === "split") {
      if (targets.filter((t) => t.business_id).length < 2) {
        return "A split needs at least two entities.";
      }
      if (Math.abs(shareTotal - 1) > 0.00005) {
        return `The shares have to add up to 1. They come to ${shareTotal.toFixed(4)}.`;
      }
    }
    if (!rationale.trim()) return "Say why this cost is shared, in your own words.";
    return null;
  }, [pattern, poolBiz, tier, method, targets, shareTotal, rationale]);

  // Debounced, because the drawer asks on every keystroke and a preview is a real query.
  useEffect(() => {
    if (!open || blocker) { setPreview(null); return; }
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      postJSON("/books/allocations/preview", draft)
        .then((d) => { setPreview(d); setPreviewErr(null); })
        .catch((e) => { setPreview(null); setPreviewErr(e?.message || "Could not preview."); });
    }, 350);
    return () => clearTimeout(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, blocker, JSON.stringify(draft)]);

  if (!open) return null;

  const nextVersion = (rule?.versions?.length || 0) + 1;
  const saveLabel = mode === "supersede" ? `Save as v${nextVersion}`
    : prefill ? "Save and clear matches" : "Save rule";

  const save = async () => {
    setSaving(true);
    setSaveErr(null);
    try {
      if (mode === "supersede" && rule) {
        const { scope_kind, pattern: _p, source_business_id, ...version } = draft;
        await postJSON(`/books/allocations/applied/${rule.id}/versions`, version);
      } else {
        await postJSON("/books/allocations/applied", { ...draft, pool_name: pattern.trim() });
      }
      onSaved();
      onClose();
    } catch (e) {
      setSaveErr(e?.message || "That did not save.");
    } finally {
      setSaving(false);
    }
  };

  const setTarget = (i, patch) =>
    setTargets((ts) => ts.map((t, j) => (j === i ? { ...t, ...patch } : t)));

  return (
    <>
      {/* `alpha` is the repo's own helper for this: a theme token is `var(--t-...)` and
          JavaScript cannot read what that resolves to, so it rewrites to the rgb triple
          palette.js emits alongside every token. There is no `scrim` key in LEDGER, and the
          first version of this leaned on one that does not exist behind a `||` fallback, which
          a build cannot catch. An `opacity` on the element would also fade the drawer's own
          edge, so the transparency belongs in the colour. */}
      <div onClick={onClose} className="al-scrim"
           style={{ position: "fixed", inset: 0, background: alpha(L.deep, 0.38),
                    zIndex: 40 }} />
      <aside className="al-drawer" role="dialog" aria-label="Allocation rule"
             style={{ position: "fixed", top: 0, right: 0, bottom: 0, width: 460,
                      maxWidth: "100vw", background: L.paper, zIndex: 41,
                      borderLeft: `1px solid ${L.cardEdge}`, display: "flex",
                      flexDirection: "column" }}>
        <div style={{ padding: "16px 18px", borderBottom: `1px solid ${L.ruleHeader}`,
                      display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
          <span style={{ fontFamily: F.body, fontSize: 14, fontWeight: 600, color: L.deep }}>
            {mode === "supersede" ? `Supersede ${rule?.pool_name || "rule"}`
              : prefill ? "Rule from this transaction" : "New allocation rule"}
          </span>
          <button onClick={onClose} style={{ fontFamily: F.body, fontSize: 12, color: L.muted,
            background: "transparent", border: "none", cursor: "pointer" }}>Close</button>
        </div>

        <div style={{ flex: 1, overflowY: "auto", padding: "18px" }}>
          <Step n="1" title="What it matches"
                note={scopeKind === "vendor"
                  ? "A payee, matched on the normalized name, so company suffixes do not matter."
                  : "The start of an account path. The longest matching rule wins."}>
            <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
              {["account", "program", "vendor"].map((k) => (
                <button key={k} onClick={() => setScopeKind(k)} disabled={mode === "supersede"}
                  style={{ fontFamily: F.body, fontSize: 12, fontWeight: 600, borderRadius: 8,
                           padding: "6px 11px", cursor: mode === "supersede" ? "default" : "pointer",
                           color: scopeKind === k ? L.paper : L.muted,
                           background: scopeKind === k ? L.deep : L.controls,
                           border: `1px solid ${scopeKind === k ? L.deep : L.cardEdge}` }}>
                  {k}
                </button>
              ))}
            </div>
            <input value={pattern} onChange={(e) => setPattern(e.target.value)}
                   disabled={mode === "supersede"} placeholder="Advertising" style={FIELD} />
            <select value={poolBiz} onChange={(e) => setPoolBiz(e.target.value)}
                    disabled={mode === "supersede"} style={{ ...FIELD, marginTop: 8 }}>
              <option value="">Booked on which entity?</option>
              {businesses.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          </Step>

          <Step n="2" title="Is it a fact or a judgment">
            <div style={{ display: "grid", gap: 6 }}>
              {tiers.map((t) => (
                <button key={t.tier} onClick={() => { setTier(t.tier); setTargets([]); }}
                  style={{ textAlign: "left", cursor: "pointer", borderRadius: 10,
                           padding: "10px 12px",
                           background: tier === t.tier ? L.controls : L.paper,
                           border: `1px solid ${tier === t.tier ? L.ruleInner : L.cardEdge}` }}>
                  <span style={{ display: "flex", alignItems: "center", gap: 7 }}>
                    <TierMarker tier={t.tier} />
                    <TierCode tier={t.tier} />
                    <span style={{ fontFamily: F.body, fontSize: 12.5, fontWeight: 600,
                                   color: L.deep }}>{t.label}</span>
                  </span>
                  <span style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted,
                                 display: "block", marginTop: 3 }}>{t.gloss}</span>
                </button>
              ))}
            </div>
          </Step>

          {tier === 4 && (
            <Step n="3" title="Nothing to enter">
              <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.body }}>
                A retained rule sends nothing anywhere. The cost stays on the entity that books
                it, and saying so explicitly is what takes it out of the exception queue.
              </div>
            </Step>
          )}

          {(tier === 1 || tier === 3) && (
            <Step n="3" title={tier === 1 ? "Which entity" : "The reviewed split"}
                  note={tier === 3
                    ? "Shares are fractions between 0 and 1 and must add up to 1. Include the entity it is booked on to leave a share behind."
                    : null}>
              <div style={{ display: "grid", gap: 6 }}>
                {targets.map((t, i) => (
                  <div key={i} style={{ display: "flex", gap: 6 }}>
                    <select value={t.business_id}
                            onChange={(e) => setTarget(i, { business_id: e.target.value })}
                            style={{ ...FIELD, flex: 1 }}>
                      <option value="">Entity</option>
                      {businesses.map((b) => (
                        <option key={b.id} value={b.id}>{b.name}</option>
                      ))}
                    </select>
                    {tier === 3 && (
                      <input value={t.share}
                             onChange={(e) => setTarget(i, { share: e.target.value })}
                             placeholder="0.5" inputMode="decimal"
                             style={{ ...FIELD, ...NUM, width: 86, flex: "0 0 auto" }} />
                    )}
                    <button onClick={() => setTargets((ts) => ts.filter((_, j) => j !== i))}
                      style={{ fontFamily: F.body, fontSize: 12, color: L.muted,
                               background: "transparent", border: "none", cursor: "pointer" }}>
                      Remove
                    </button>
                  </div>
                ))}
              </div>
              <button onClick={() => setTargets((ts) => [...ts,
                      { business_id: "", share: tier === 1 ? "1" : "" }])}
                disabled={tier === 1 && targets.length >= 1}
                style={{ marginTop: 8, fontFamily: F.body, fontSize: 12, fontWeight: 600,
                         color: L.deep, background: L.controls,
                         border: `1px solid ${L.ruleInner}`, borderRadius: 9,
                         padding: "6px 12px", cursor: "pointer" }}>
                Add an entity
              </button>
              {tier === 3 && targets.length > 0 && (
                <div style={{ marginTop: 8, fontFamily: F.body, fontSize: 12,
                              color: Math.abs(shareTotal - 1) > 0.00005 ? L.flagInk : L.body }}>
                  Shares add up to {shareTotal.toFixed(4)}
                  {Math.abs(shareTotal - 1) > 0.00005 ? ", and have to come to 1" : ""}
                </div>
              )}
            </Step>
          )}

          {tier === 2 && (
            <Step n="3" title="The outside source">
              <div style={{ background: L.flagBg, border: `1px solid ${L.flagEdge}`,
                            borderRadius: 10, padding: "11px 13px", fontFamily: F.body,
                            fontSize: 12.5, color: L.flagInk }}>
                A traced rule reads its ratio from a connected source and its campaign map.
                Setting that map up is not in this drawer yet, so choose another tier here or
                add the rule from the API.
              </div>
            </Step>
          )}

          <Step n="4" title="Why"
                note="This is what the Statement shows when somebody asks why a cost reached their entity.">
            <textarea value={rationale} onChange={(e) => setRationale(e.target.value)}
                      rows={3} placeholder="Run centrally and billed to one card."
                      style={{ ...FIELD, resize: "vertical" }} />
          </Step>

          <Step n="5" title="From when">
            <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
              <select value={month.slice(5, 7)}
                      onChange={(e) => setMonth(`${month.slice(0, 4)}-${e.target.value}-01`)}
                      style={{ ...FIELD, flex: 1 }}>
                {MONTHS.map((m, i) => (
                  <option key={m} value={m}>{MONTH_LABEL[i]} {month.slice(0, 4)}</option>
                ))}
              </select>
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {[["forward", "From now on"], ["from_date", "From that month"],
                ["retro", "Back to the start"]].map(([k, label]) => (
                <button key={k} onClick={() => setReach(k)}
                  style={{ fontFamily: F.body, fontSize: 12, fontWeight: 600, borderRadius: 8,
                           padding: "6px 11px", cursor: "pointer",
                           color: reach === k ? L.paper : L.muted,
                           background: reach === k ? L.deep : L.controls,
                           border: `1px solid ${reach === k ? L.deep : L.cardEdge}` }}>
                  {label}
                </button>
              ))}
            </div>
            {!!preview?.would_restate?.length && (
              <div style={{ marginTop: 10, background: L.flagBg,
                            border: `1px solid ${L.flagEdge}`, borderRadius: 10,
                            padding: "11px 13px" }}>
                <div style={{ fontFamily: F.body, fontSize: 12.5, fontWeight: 600,
                              color: L.flagInk }}>
                  This restates {preview.would_restate.length} month
                  {preview.would_restate.length === 1 ? "" : "s"} already reported
                </div>
                <div style={{ fontFamily: F.body, fontSize: 11.5, color: L.body, marginTop: 4 }}>
                  {preview.would_restate.join(", ")}. What those months said before stays
                  readable: the old version is kept, not overwritten.
                </div>
              </div>
            )}
          </Step>
        </div>

        {/* The preview is pinned above the actions, so the figures somebody reads are the last
            thing they see before they commit to them. */}
        <div style={{ borderTop: `1px solid ${L.ruleHeader}`, background: L.controls,
                      padding: "13px 18px" }}>
          <div style={{ ...EYEBROW, marginBottom: 7 }}>What this would do</div>
          {blocker ? (
            <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.muted }}>
              Nothing to preview yet.
            </div>
          ) : previewErr ? (
            <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.flagInk }}>
              {previewErr}
            </div>
          ) : !preview ? (
            <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.muted }}>Working.</div>
          ) : (
            <div style={{ display: "grid", gap: 4 }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ fontFamily: F.body, fontSize: 12, color: L.body }}>
                  In the books, across {preview.months.length} month
                  {preview.months.length === 1 ? "" : "s"}
                </span>
                <span style={{ ...NUM, fontSize: 12.5, color: L.deep }}>
                  {money(preview.pool_amount)}
                </span>
              </div>
              {preview.targets.map((t) => {
                const b = businesses.find((x) => x.id === t.business_id);
                return (
                  <div key={t.business_id}
                       style={{ display: "flex", justifyContent: "space-between" }}>
                    <span style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>
                      {b?.name || "Unknown entity"}
                    </span>
                    <span style={{ ...NUM, fontSize: 12.5, color: L.body }}>
                      {money(t.amount)}
                    </span>
                  </div>
                );
              })}
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>
                  Stays where it is
                </span>
                <span style={{ ...NUM, fontSize: 12.5, color: L.body }}>
                  {money(preview.retained)}
                </span>
              </div>
              {!!preview.claims?.count && (
                <div style={{ fontFamily: F.body, fontSize: 11.5, color: L.body, marginTop: 4 }}>
                  Clears {preview.claims.count} transaction
                  {preview.claims.count === 1 ? "" : "s"} from the exception queue.
                </div>
              )}
              {(preview.pending || []).slice(0, 3).map((p, i) => (
                <div key={i} style={{ fontFamily: F.body, fontSize: 11.5, color: L.flagInk }}>
                  {fmtMonth(p.month)}: {reasonOf(preview.meta, p.reason)?.label}
                </div>
              ))}
            </div>
          )}

          {saveErr && (
            <div style={{ fontFamily: F.body, fontSize: 12, color: L.flagInk, marginTop: 8 }}>
              {saveErr}
            </div>
          )}
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 11 }}>
            <button onClick={save} disabled={!!blocker || saving}
              style={{ fontFamily: F.body, fontSize: 12.5, fontWeight: 600,
                       color: blocker ? L.muted : L.paper,
                       background: blocker ? L.track : L.deep, border: "none",
                       borderRadius: 9, padding: "8px 15px",
                       cursor: blocker || saving ? "default" : "pointer" }}>
              {saving ? "Saving" : saveLabel}
            </button>
            {blocker && (
              <span style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted }}>
                {blocker}
              </span>
            )}
          </div>
        </div>
      </aside>

      <style>{`
        @media (max-width: 560px) {
          .al-drawer { width: 100vw; border-left: none; }
          /* Spec 7.4 asks for an OPAQUE scrim on the full-width sheet. At phone width the
             drawer IS the screen, and letting the page show through behind something that has
             entirely replaced it reads as two screens at once. */
          .al-scrim { background: ${L.card}; }
        }
      `}</style>
    </>
  );
}
