/* Rules (ALLOCATION-RULES-SPEC 7.3).

   Tier summary cards, filters, ledger rows, and a row that expands to why the rule exists, how
   it resolves, and its version history. NO EDIT AND NO DELETE anywhere: an applied rule is
   superseded, never changed, and offering a control the API answers 409 to would be a lie in
   the shape of a button.

   Every figure is the server's. */
import { useMemo, useState } from "react";
import { LEDGER as L, LEDGER_FONT as F } from "../../theme.js";
import Money from "./Money.jsx";
import { methodLabel, reasonOf, scopeLabel, fmtMonth } from "./words.js";
import { TierMarker, TierCode, tierRowStyle } from "./TierMarker.jsx";

const NUM = { fontFamily: F.num, fontVariantNumeric: "tabular-nums", textAlign: "right",
              whiteSpace: "nowrap" };
const EYEBROW = { fontFamily: F.num, fontSize: 10, letterSpacing: ".14em",
                  textTransform: "uppercase", color: L.faint };



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

function SummaryCards({ meta, monthLabel, tiers, filter, setFilter }) {
  /* The figures come from `meta.tier_totals`. They used to be summed HERE, over the rule
     rows, against the module's standing rule that every displayed figure is the server's
     (U7) -- and the card showed the result with no label and no period, so a reader could
     not tell what the number was or what span it covered. */
  const byTier = meta?.tier_totals || {};

  return (
    <div className="al-cards" style={{ display: "grid", gap: 10,
                  gridTemplateColumns: "repeat(auto-fit,minmax(170px,1fr))",
                  marginBottom: 16 }}>
      {(tiers || []).map((t) => {
        const row = byTier[String(t.tier)] || { count: 0, pool_amount: 0 };
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
            <Money v={row.pool_amount} size={15} tone={L.deep} />
            <span style={{ fontFamily: F.num, fontSize: 10, letterSpacing: ".12em",
                           textTransform: "uppercase", color: L.ghost, display: "block",
                           marginTop: 2 }}>
              In the books, {monthLabel}
            </span>
            <span style={{ fontFamily: F.body, fontSize: 11, color: L.muted, display: "block",
                           marginTop: 2 }}>
              {row.count} rule{row.count === 1 ? "" : "s"}
              {row.pending_count ? `, ${row.pending_count} not resolved` : ""}
            </span>
            <span style={{ fontFamily: F.body, fontSize: 11, color: L.muted, display: "block",
                           marginTop: 4 }}>{t.gloss}</span>
          </button>
        );
      })}
    </div>
  );
}

function Expanded({ rule, meta, businesses, isCFO, onSupersede }) {
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
                {fmtMonth(p.month)}: {reasonOf(meta, p.reason)?.label || "Not resolved"}
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
              From {fmtMonth(ver.effective_from)}
            </span>
            <span style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted, minWidth: 70 }}>
              {methodLabel(meta, ver.method)}
            </span>
            {/* The approver, which this printed as "Unknown" on every row because the
                payload carried no name at all (U5). Still "Unknown" when a version really
                has no approver, which is a different and far rarer thing. */}
            <span style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted }}>
              {ver.approved_by?.name || "Unknown"}
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
  const meta = data?.meta || {};
  const tiers = meta.tiers || [];
  /* The month these figures cover, in words, from the months the SERVER reconciled rather
     than from the period token: a card headed with a figure and no period is a figure a
     reader cannot place (U7, U17). Phase 2 replaces this with the page's month control. */
  const months = data?.months || [];
  const monthLabel = months.length
    ? (months.length === 1 ? fmtMonth(months[0])
       : `${fmtMonth(months[0])} to ${fmtMonth(months[months.length - 1])}`)
    : "";
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
      <SummaryCards meta={meta} monthLabel={monthLabel} tiers={tiers}
                    filter={tier} setFilter={setTier} />

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
                    <TierCode tier={t} /> {scopeLabel(meta, r.scope_kind)} {r.pattern}
                  </span>
                </span>
                <span className="al-hide-narrow"
                      style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted,
                               minWidth: 0 }}>
                  {r.targets_label || methodLabel(meta, r.current?.method)
                    || "No current version."}
                </span>
                <Money v={r.pool_amount} />
                <span className="al-hide-narrow"><Money v={r.to_partners} /></span>
                <span className="al-hide-narrow">
                  <Money v={r.retained} tone={anyPending ? L.flagInk : undefined} />
                </span>
              </div>
              {isOpen && (
                <Expanded rule={r} meta={meta} businesses={businesses} isCFO={isCFO}
                          onSupersede={onSupersede} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
