/* Rules (ALLOCATION-RULES-SPEC 7.3).

   Tier summary cards, filters, ledger rows, and a row that expands to why the rule exists, how
   it resolves, and its version history. NO EDIT AND NO DELETE anywhere: an applied rule is
   superseded, never changed, and offering a control the API answers 409 to would be a lie in
   the shape of a button.

   Every figure is the server's. */
import { useMemo, useState } from "react";
import { LEDGER as L, LEDGER_FONT as F } from "../../theme.js";
import { TierMarker, TierCode, tierRowStyle } from "./TierMarker.jsx";

const NUM = { fontFamily: F.num, fontVariantNumeric: "tabular-nums", textAlign: "right",
              whiteSpace: "nowrap" };
const EYEBROW = { fontFamily: F.num, fontSize: 10, letterSpacing: ".14em",
                  textTransform: "uppercase", color: L.faint };

function Money({ v, tone }) {
  const n = Number(v || 0);
  const body = Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2,
                                                       maximumFractionDigits: 2 });
  return <span style={{ ...NUM, color: tone || L.body }}>{n < 0 ? `(${body})` : body}</span>;
}

const METHOD_LINE = {
  direct: "The whole cost goes to one entity.",
  split: "Split between entities by a reviewed share.",
  driven: "Split by a number committed each month.",
  traced: "Split by a ratio taken from an outside source.",
  retained: "Stays on the entity that books it.",
  ended: "Claims nothing from its effective month.",
};

const REASON = {
  no_driver_values: "No driver numbers committed",
  driver_subject_missing: "The driver has no numbers for this person",
  fixed_exceeds_pool: "Fixed amounts exceed the pool",
  allocation_exceeds_pool: "Shares and fixed amounts exceed the pool",
  source_not_connected: "No spend from the source this month",
  source_unmapped_spend: "Some spend matched no entity",
  double_count_risk: "The target already books this as a shared charge",
  multi_line: "A multi-account transaction, left for review",
  unmapped_account: "An account with no mapping",
  vendor_rule_collision: "Two rules claim the same payee",
};

/* A name and its dot, both from the payload. Deriving either from a business key is what F11
   was about, and amendment 21 says to feed them from the payload instead. */
function Entity({ id, businesses }) {
  const b = (businesses || []).find((x) => x.id === id);
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, minWidth: 0 }}>
      <span aria-hidden style={{ width: 7, height: 7, borderRadius: "50%", flex: "0 0 auto",
                                 background: b?.accent || L.faint }} />
      <span style={{ fontFamily: F.body, fontSize: 12, color: L.body, overflow: "hidden",
                     textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {b?.name || "Unknown entity"}
      </span>
    </span>
  );
}

function SummaryCards({ rules, tiers, filter, setFilter }) {
  const byTier = useMemo(() => {
    const out = {};
    for (const r of rules) {
      const t = r.current?.tier ?? 0;
      const row = out[t] || (out[t] = { count: 0, pool: 0 });
      row.count += 1;
      row.pool += Number(r.pool_amount || 0);
    }
    return out;
  }, [rules]);

  return (
    <div className="al-cards" style={{ display: "grid", gap: 10,
                  gridTemplateColumns: "repeat(auto-fit,minmax(170px,1fr))",
                  marginBottom: 16 }}>
      {(tiers || []).map((t) => {
        const row = byTier[t.tier] || { count: 0, pool: 0 };
        const on = filter === t.tier;
        return (
          <button key={t.tier} onClick={() => setFilter(on ? null : t.tier)}
            style={{ textAlign: "left", cursor: "pointer", borderRadius: 12, padding: "12px 14px",
                     background: on ? L.paper : L.card,
                     border: `1px solid ${on ? L.ruleInner : L.cardEdge}` }}>
            <span style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
              <TierMarker tier={t.tier} />
              <TierCode tier={t.tier} />
              <span style={{ fontFamily: F.body, fontSize: 12, fontWeight: 600,
                             color: L.deep }}>{t.label}</span>
            </span>
            <span style={{ ...NUM, display: "block", fontSize: 15, color: L.deep }}>
              <Money v={row.pool} />
            </span>
            <span style={{ fontFamily: F.body, fontSize: 11, color: L.muted, display: "block",
                           marginTop: 2 }}>
              {row.count} rule{row.count === 1 ? "" : "s"}
            </span>
            <span style={{ fontFamily: F.body, fontSize: 11, color: L.muted, display: "block",
                           marginTop: 4 }}>{t.gloss}</span>
          </button>
        );
      })}
    </div>
  );
}

function Expanded({ rule, businesses, isCFO, onSupersede }) {
  const v = rule.current;
  const pending = rule.pending || [];
  return (
    <div style={{ padding: "14px 16px 18px", background: L.controls,
                  borderBottom: `1px solid ${L.ruleLine}` }}>
      <div style={{ ...EYEBROW, marginBottom: 6 }}>Why this rule exists</div>
      <div style={{ fontFamily: F.body, fontSize: 13, color: L.body, marginBottom: 16,
                    maxWidth: 680 }}>
        {v?.rationale || "No reason recorded."}
      </div>

      <div style={{ ...EYEBROW, marginBottom: 8 }}>How it resolves</div>
      {pending.length ? (
        <div style={{ background: L.flagBg, border: `1px solid ${L.flagEdge}`,
                      borderRadius: 10, padding: "10px 12px", marginBottom: 16 }}>
          {pending.map((p, i) => (
            <div key={i} style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
              <span style={{ fontFamily: F.body, fontSize: 12, color: L.flagInk }}>
                {p.month}: {REASON[p.reason] || "Not resolved"}
              </span>
              {p.amount != null && <Money v={p.amount} tone={L.flagInk} />}
            </div>
          ))}
        </div>
      ) : (
        <div style={{ display: "grid", gap: 6, marginBottom: 16, maxWidth: 520 }}>
          {(v?.targets || []).map((t, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ flex: "0 0 auto", minWidth: 150 }}>
                <Entity id={t.business_id} businesses={businesses} />
              </span>
              <span style={{ flex: 1, height: 6, borderRadius: 3, background: L.track,
                             overflow: "hidden" }}>
                {/* The share arrives as a fraction and CSS scales it. Deliberately `calc`
                    rather than multiplying by 100 here: the browser computes no shares, and a
                    bar width is the one place that rule is easy to break without noticing,
                    because the result looks right either way. */}
                <span style={{ display: "block", height: "100%", background: L.deep,
                               width: `calc(${t.share || 0} * 100%)` }} />
              </span>
            </div>
          ))}
          {!v?.targets?.length && (
            <div style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>
              Nothing leaves this entity under the current version.
            </div>
          )}
        </div>
      )}

      <div style={{ ...EYEBROW, marginBottom: 8 }}>Version history</div>
      <div style={{ display: "grid", gap: 6, marginBottom: 16 }}>
        {(rule.versions || []).map((ver) => (
          <div key={ver.id} style={{ display: "flex", gap: 12, alignItems: "baseline",
                                     flexWrap: "wrap" }}>
            <span style={{ fontFamily: F.num, fontSize: 11, color: L.faint, minWidth: 28 }}>
              v{ver.version}
            </span>
            <span style={{ fontFamily: F.body, fontSize: 12, color: L.body, minWidth: 90 }}>
              From {ver.effective_from}
            </span>
            <span style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted, minWidth: 70 }}>
              {ver.method}
            </span>
            <span style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted }}>
              {ver.approved_by || "Unknown"}
            </span>
            <span style={{ fontFamily: F.num, fontSize: 10, letterSpacing: ".08em",
                           textTransform: "uppercase",
                           color: ver.is_current ? L.deep : L.faint }}>
              {ver.is_current ? "Current" : "Superseded"}
            </span>
          </div>
        ))}
      </div>

      {isCFO && (
        <button onClick={() => onSupersede(rule)} style={{ fontFamily: F.body, fontSize: 12.5,
          fontWeight: 600, color: L.deep, background: L.paper,
          border: `1px solid ${L.ruleInner}`, borderRadius: 9, padding: "7px 14px",
          cursor: "pointer" }}>
          Supersede with a new version
        </button>
      )}
    </div>
  );
}

export default function RulesView({ data, period, isCFO, refresh, onSupersede = () => {} }) {
  const [tier, setTier] = useState(null);
  const [entity, setEntity] = useState("");
  const [open, setOpen] = useState(null);

  const businesses = data?.businesses || [];
  const tiers = data?.meta?.tiers || [];
  // `applied` on purpose: `rules` is the route's OBSERVED list and this page renders the
  // applied ones. A test pinned that contract when a swap of the two keys broke it.
  const all = data?.applied || [];

  const rules = all.filter((r) => {
    if (tier != null && (r.current?.tier ?? 0) !== tier) return false;
    if (!entity) return true;
    if (r.pool_business_id === entity) return true;
    return (r.current?.targets || []).some((t) => t.business_id === entity);
  });

  if (!all.length) {
    return (
      <div style={{ background: L.card, border: `1px solid ${L.cardEdge}`, borderRadius: 14,
                    padding: 28, textAlign: "center" }}>
        <div style={{ fontFamily: F.body, fontSize: 13, color: L.body }}>
          No allocation rules yet.
        </div>
        <div style={{ fontFamily: F.body, fontSize: 12, color: L.muted, marginTop: 6 }}>
          Start from the Exceptions view: it lists the shared costs no rule claims.
        </div>
      </div>
    );
  }

  return (
    <div>
      <SummaryCards rules={all} tiers={tiers} filter={tier} setFilter={setTier} />

      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12,
                    flexWrap: "wrap" }}>
        <select value={entity} onChange={(e) => setEntity(e.target.value)}
          style={{ fontFamily: F.body, fontSize: 12.5, color: L.body, background: L.paper,
                   border: `1px solid ${L.cardEdge}`, borderRadius: 9, padding: "6px 10px" }}>
          <option value="">Every entity</option>
          {businesses.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>
        {(tier != null || entity) && (
          <button onClick={() => { setTier(null); setEntity(""); }}
            style={{ fontFamily: F.body, fontSize: 12, color: L.muted, background: "transparent",
                     border: "none", cursor: "pointer" }}>Clear filters</button>
        )}
        <span style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted, marginLeft: "auto" }}>
          {rules.length} of {all.length} shown
        </span>
      </div>

      <div style={{ background: L.paper, border: `1px solid ${L.cardEdge}`, borderRadius: 14,
                    overflow: "hidden" }}>
        <div className="al-rules-row" style={{ display: "grid",
                      gap: 10, padding: "11px 16px", background: L.colHead,
                      borderBottom: `1px solid ${L.ruleHeader}` }}>
          <span />
          <span style={EYEBROW}>Pool</span>
          <span className="al-hide-narrow" style={EYEBROW}>Method</span>
          <span style={{ ...EYEBROW, textAlign: "right" }}>In the books</span>
          <span className="al-hide-narrow" style={{ ...EYEBROW, textAlign: "right" }}>To partners</span>
          <span className="al-hide-narrow" style={{ ...EYEBROW, textAlign: "right" }}>Retained</span>
        </div>

        {rules.map((r) => {
          const t = r.current?.tier ?? 0;
          const isOpen = open === r.id;
          const anyPending = (r.pending || []).length > 0;
          return (
            <div key={r.id}>
              <div role="button" tabIndex={0}
                onClick={() => setOpen(isOpen ? null : r.id)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setOpen(isOpen ? null : r.id); }}
                className="al-rules-row"
                style={{ display: "grid",
                         gap: 10, padding: "12px 16px", cursor: "pointer",
                         borderBottom: `1px solid ${L.ruleLine}`, ...tierRowStyle(t) }}>
                <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <TierMarker tier={t} />
                </span>
                <span style={{ minWidth: 0 }}>
                  <span style={{ fontFamily: F.body, fontSize: 13, color: L.deep,
                                 fontWeight: "inherit" }}>{r.pool_name}</span>
                  <span style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted,
                                 display: "block", overflow: "hidden",
                                 textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    <TierCode tier={t} /> {r.scope_kind} {r.pattern}
                  </span>
                </span>
                <span className="al-hide-narrow"
                      style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted,
                               minWidth: 0 }}>
                  {METHOD_LINE[r.current?.method] || "No current version."}
                </span>
                <Money v={r.pool_amount} />
                <span className="al-hide-narrow"><Money v={r.to_partners} /></span>
                <span className="al-hide-narrow">
                  <Money v={r.retained} tone={anyPending ? L.flagInk : undefined} />
                </span>
              </div>
              {isOpen && (
                <Expanded rule={r} businesses={businesses} isCFO={isCFO}
                          onSupersede={onSupersede} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
