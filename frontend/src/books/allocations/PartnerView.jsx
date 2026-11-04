/* Partner view (ALLOCATION-RULES-SPEC 7.3, Phase 10).

   One entity: what it earned after shared cost, the figure it moved from, why it moved, and
   what has been drawn against it. Appendix C listed this row as "Phase 10, blocked" while every
   other mockup row named a file. The endpoint shipped and the view did not, which made Phase 10
   finished in the tests and invisible on screen.

   ALLOCATED LEADS (Connor's answer). The headline is the figure after shared cost has been
   pushed in and out, because that is what a partner is owed against. Booked sits beside it
   rather than behind a toggle, which is D10 and not a style choice: a partner asking why their
   number moved needs to see the figure it moved from.

   NO ARITHMETIC HERE, the rule the whole folder states about itself. Every figure is the
   server's. `shared_cost_effect` and `undrawn` arrive computed precisely because a browser
   recomputing them is how two screens come to disagree about one number. */
import { LEDGER as L, LEDGER_FONT as F } from "../../theme.js";
import Money from "./Money.jsx";
import { fmtMonth } from "./words.js";

const NUM = { fontFamily: F.num, fontVariantNumeric: "tabular-nums", textAlign: "right",
              whiteSpace: "nowrap" };
const EYEBROW = { fontFamily: F.num, fontSize: 10, letterSpacing: ".14em",
                  textTransform: "uppercase", color: L.faint };

/* Ledger parens for a negative, the convention the rest of the module reads in. Formatting
   only: the number arrived from the server and is not touched. */

function Figure({ label, value, size = 15, weight = 600, tone, note }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ ...EYEBROW, marginBottom: 5 }}>{label}</div>
      <Money v={value} size={size} weight={weight} tone={tone} />
      {note && (
        <div style={{ fontFamily: F.body, fontSize: 11, color: L.muted, marginTop: 4 }}>
          {note}
        </div>
      )}
    </div>
  );
}

/* Which entity. The names and accents come from the payload, never from a key map (A11, D15),
   and because three of five entities currently share one accent the colour is a marker beside
   the name rather than the only way to tell them apart (amendment 23).

   EXPORTED, and the shell renders it OUTSIDE StatePanel. It used to live in this file's own
   output, which put it inside StatePanel's children, and StatePanel REPLACES its children with
   an error card. The partner route answers 409 for an unmapped account or a failed tie-out, so
   picking one entity in that state deleted the only control for picking a different one, and
   Retry only re-requested the entity that had just failed. The tab was stuck until a page
   reload. A control that chooses WHAT to load cannot live inside the thing that failed. */
export function EntityPicker({ businesses, value, onPick }) {
  if (!businesses.length) return null;
  return (
    <div style={{ display: "flex", gap: 7, flexWrap: "wrap", marginBottom: 16 }}>
      {businesses.map((b) => {
        const on = b.id === value;
        return (
          <button key={b.id} onClick={() => onPick(b.id)}
            style={{ fontFamily: F.body, fontSize: 12.5, fontWeight: 600, cursor: "pointer",
                     borderRadius: 9, padding: "7px 13px", display: "inline-flex",
                     alignItems: "center", gap: 7,
                     color: on ? L.paper : L.body,
                     background: on ? L.deep : L.controls,
                     border: `1px solid ${on ? L.deep : L.cardEdge}` }}>
            <span style={{ width: 7, height: 7, borderRadius: 7,
                           background: b.accent || L.muted, flex: "0 0 auto" }} />
            {b.name}
          </button>
        );
      })}
    </div>
  );
}

/* One contribution under the line it moved. Direction is a WORD as well as a sign, which is
   7.4's rule: an allocated-out row renders negative and also says so, because a reader should
   never have to infer direction from a minus sign alone. */
function Contribution({ c }) {
  const rule = c.rule || null;
  const months = rule?.months || [];
  const where = c.direction === "in"
    ? `in from ${c.counterparty_business}`
    : `out to ${c.counterparty_business}`;
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12,
                  padding: "9px 16px 9px 30px", background: L.card,
                  borderBottom: `1px solid ${L.ruleLine}` }}>
      <span style={{ minWidth: 0 }}>
        <span style={{ fontFamily: F.body, fontSize: 12.5, color: L.body }}>
          {c.pool_name || "Allocation"}, {where}
        </span>
        <span style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted, display: "block" }}>
          {[rule?.method && `Tier ${rule.tier}, ${rule.method}`,
            rule?.version && `v${rule.version}`,
            c.basis,
            months.length === 1 ? months[0]
              : months.length > 1 ? `${months[0]} to ${months[months.length - 1]}`
              : null,
          ].filter(Boolean).join(" · ")}
        </span>
        {rule?.rationale && (
          <span style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted, display: "block",
                         marginTop: 3, fontStyle: "italic" }}>
            {rule.rationale}
          </span>
        )}
      </span>
      <Money v={c.amount} size={12.5} />
    </div>
  );
}

export default function PartnerView({ data, businessId, period }) {
  if (!businessId) {
    return (
      <div>
        <div style={{ background: L.card, border: `1px solid ${L.cardEdge}`, borderRadius: 14,
                      padding: 28, textAlign: "center" }}>
          <div style={{ fontFamily: F.body, fontSize: 13, color: L.body }}>
            Pick an entity.
          </div>
          <div style={{ fontFamily: F.body, fontSize: 12, color: L.muted, marginTop: 6 }}>
            This view answers one entity at a time: what it earned after shared cost, and what
            has been drawn against it.
          </div>
        </div>
      </div>
    );
  }
  // The shell draws the picker, so there is nothing to hold here while a load is in flight.
  if (!data) return null;

  const lines = data.shared_lines || [];
  const moved = Number(data.shared_cost_effect || 0);
  const under = Number(data.undrawn || 0);
  const tie = data.tie_out || null;

  return (
    <div style={{ display: "grid", gap: 18 }}>
      <div style={{ background: L.paper, border: `1px solid ${L.cardEdge}`, borderRadius: 14,
                    padding: 18 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12,
                      flexWrap: "wrap", marginBottom: 16 }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontFamily: F.body, fontSize: 17, fontWeight: 700, color: L.deep }}>
              {data.business?.name || "Entity"}
            </div>
            <div style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted, marginTop: 3 }}>
              {/* The statement's period carries `start`, `end`, `books_closed` and `synced_at`
                  and no label: the first version read `period.label`, which is undefined, so
                  the dates silently did not render at all. */}
              {data.basis === "accrual" ? "Accrual basis" : data.basis}
              {data.period?.start ? ` · ${data.period.start} to ${data.period.end}` : ""}
              {data.period?.books_closed ? " · Closed" : ""}
            </div>
          </div>
          {/* `status` is only ever "tied" here: a statement that does not tie RAISES rather
              than returning, so a "does not tie" branch would be unreachable code pretending
              to be a safeguard. What CAN be non-zero is `excluded`, money the statement left
              out while still tying, and that is the number worth a reader's attention. */}
          {tie && (
            <span style={{ fontFamily: F.num, fontSize: 10.5, letterSpacing: ".1em",
                           textTransform: "uppercase", alignSelf: "flex-start",
                           borderRadius: 7, padding: "5px 9px",
                           color: Number(tie.excluded || 0) ? L.flagInk : L.accentInk,
                           background: Number(tie.excluded || 0) ? L.flagBg : L.accentBg }}>
              {Number(tie.excluded || 0) ? "Ties, with money left out" : "Ties to the books"}
            </span>
          )}
        </div>

        {/* Earned leads. Booked and the effect sit beside it, never behind a toggle (D10). */}
        <div className="al-partner-figs" style={{ display: "grid", gap: 16 }}>
          <Figure label="Earned, after shared cost" value={data.earned_allocated} size={22}
                  weight={700} tone={L.deep} />
          <Figure label="As booked" value={data.earned_booked}
                  note="Before any shared cost moved" />
          <Figure label="Effect of shared cost" value={moved}
                  tone={moved < 0 ? L.flagInk : moved > 0 ? L.accentInk : L.muted}
                  note={moved === 0 ? "Nothing moved this period"
                        : moved < 0 ? "Cost came in from another entity"
                        : "Cost went out to another entity"} />
          <Figure label="Drawn" value={data.drawn} note="Taken out this period" />
          <Figure label={under < 0 ? "Drawn beyond earned" : "Earned, not yet drawn"}
                  value={under} tone={under < 0 ? L.flagInk : L.body} />
        </div>

        {/* Said on the page, not only in the spec. A partner who is owed a PERCENTAGE of an
            entity would otherwise read this headline as their own figure, and nothing in the
            schema models a partner share (SPEC 11 item 5 is still open). */}
        <div style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted, marginTop: 16,
                      borderTop: `1px solid ${L.ruleLine}`, paddingTop: 12 }}>
          Earned is this entity&apos;s whole profit after shared cost moved in and out. It is not
          a partner&apos;s percentage of it: no partner share is recorded anywhere yet, so a
          half interest in this entity is half of the figure above and the halving is not done
          here.
        </div>
      </div>

      <div style={{ background: L.paper, border: `1px solid ${L.cardEdge}`, borderRadius: 14,
                    overflow: "hidden" }}>
        <div className="al-partner-row" style={{ display: "grid", gap: 10,
                      padding: "11px 16px", background: L.colHead,
                      borderBottom: `1px solid ${L.ruleHeader}` }}>
          <span style={EYEBROW}>Line shared cost reached</span>
          <span className="al-hide-narrow" style={{ ...EYEBROW, textAlign: "right" }}>
            As booked
          </span>
          <span style={{ ...EYEBROW, textAlign: "right" }}>As allocated</span>
        </div>

        {!lines.length && (
          <div style={{ padding: "20px 16px", textAlign: "center" }}>
            <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.body }}>
              No shared cost reached this entity in this period.
            </div>
            <div style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted, marginTop: 5 }}>
              Earned and booked agree, which is what the two equal figures above mean.
            </div>
          </div>
        )}

        {lines.map((line) => (
          <div key={line.standard_account_id || line.code}>
            <div className="al-partner-row" style={{ display: "grid", gap: 10,
                          padding: "12px 16px", borderBottom: `1px solid ${L.ruleLine}` }}>
              <span style={{ minWidth: 0 }}>
                <span style={{ fontFamily: F.body, fontSize: 13, fontWeight: 600,
                               color: L.deep }}>{line.name}</span>
                <span style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted,
                               display: "block" }}>{line.code}</span>
              </span>
              {/* Alignment lives in the STYLESHEET, never inline. An inline `display: block`
                  here fixed the alignment and broke the phone layout in the same stroke,
                  because it outranks the media query that hides this column at 520px: three
                  children then sat in a two-column grid and wrapped. The repo has a family of
                  bugs from inline styles beating media queries, and this is one more. */}
              <span className="al-hide-narrow"><Money v={line.as_booked} size={13} weight={500} /></span>
              <Money v={line.as_allocated} size={13} weight={600} />
            </div>
            {(line.contributions || []).map((c, i) => (
              <Contribution key={i} c={c} />
            ))}
          </div>
        ))}
      </div>

      <div style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted }}>
        Read from the same statement the Statement page renders for{" "}
        {period && period !== "ytd" ? fmtMonth(period) : "the year to date"}, so the two
        cannot disagree about a figure they both show.
      </div>
    </div>
  );
}
