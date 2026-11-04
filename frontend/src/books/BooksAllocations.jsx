/* Allocation rules (ALLOCATION-RULES-SPEC 7.1, 7.3).

   The shell: a segmented sub-view over Rules, Exceptions, Drivers and Reconciliation, following
   `BooksPayables.jsx` SegNav. Everything below reads the payload and renders it. NO ARITHMETIC
   LIVES HERE, the same rule the Statement states about itself: every figure on screen is the
   server's, because two places computing one number is how they come to disagree.

   No raw hex and no font family names anywhere in this folder: colours come from LEDGER and
   type from LEDGER_FONT, and theme.js says the folder is grepped for both. */
import { useState } from "react";
import { LEDGER as L, LEDGER_FONT as F } from "../theme.js";
import { StatePanel } from "./ui.jsx";
import { useAllocRules, useAllocExceptions, useAllocDrivers, useAllocRecon,
         useAllocPartner } from "./useBooks.js";
import RulesView from "./allocations/RulesView.jsx";
import ExceptionsView from "./allocations/ExceptionsView.jsx";
import DriversView from "./allocations/DriversView.jsx";
import ReconView from "./allocations/ReconView.jsx";
import PartnerView, { EntityPicker } from "./allocations/PartnerView.jsx";
import RuleDrawer from "./allocations/RuleDrawer.jsx";

const VIEWS = [["rules", "Rules"], ["exceptions", "Exceptions"],
               ["drivers", "Drivers"], ["recon", "Reconciliation"],
               ["partner", "Partner"]];

function SegNav({ view, setView, counts }) {
  return (
    <div style={{ display: "inline-flex", gap: 2, background: L.controls,
                  border: `1px solid ${L.cardEdge}`, borderRadius: 10, padding: 3,
                  marginBottom: 18, flexWrap: "wrap" }}>
      {VIEWS.map(([k, label]) => (
        <button key={k} onClick={() => setView(k)} style={{ fontFamily: F.body, fontSize: 12.5,
          fontWeight: 600, borderRadius: 8, border: "none", padding: "7px 15px",
          cursor: "pointer", color: view === k ? L.deep : L.muted,
          background: view === k ? L.paper : "transparent" }}>
          {label}{counts[k] ? ` · ${counts[k]}` : ""}
        </button>
      ))}
    </div>
  );
}

export default function BooksAllocations({ period = "mtd", isCFO = false }) {
  const [view, setView] = useState("rules");
  // One drawer, three entry points (7.4). `mode` says which, so the drawer does not have to
  // guess from whether `rule` or `prefill` happens to be set.
  const [drawer, setDrawer] = useState(null);
  // Which entity the Partner view is answering for. Held HERE rather than inside that
  // view so leaving the tab and coming back does not silently reset to no entity.
  const [partnerId, setPartnerId] = useState(null);
  const rules = useAllocRules(period);
  const exceptions = useAllocExceptions(period);
  const drivers = useAllocDrivers();
  const recon = useAllocRecon(period);
  const partner = useAllocPartner(partnerId, period);

  // The badge counts are the two things somebody is waiting on: unclaimed cost, and rules that
  // resolved nothing. Both come from the payload; neither is derived by counting rows here.
  const counts = {
    exceptions: exceptions.data?.rows?.length || 0,
    recon: (recon.data?.rules || []).filter((r) => r.unresolved_amount).length || 0,
  };

  const active = { rules, exceptions, drivers, recon, partner }[view];

  return (
    <div>
      <SegNav view={view} setView={setView} counts={counts} />

      {/* OUTSIDE StatePanel on purpose. StatePanel replaces its children with an error card,
          and the partner route answers 409 for an unmapped account or a failed tie-out, so a
          picker rendered inside it disappears exactly when somebody needs it to choose a
          different entity. Retry would only ask for the failing one again. */}
      {view === "partner" && (
        <EntityPicker businesses={rules.data?.businesses || []} value={partnerId}
                      onPick={setPartnerId} />
      )}

      <StatePanel loading={active.loading} error={active.error}
                  retry={active.retry} empty={false}>
        {view === "rules" && (
          <RulesView data={rules.data} period={period} isCFO={isCFO}
                     refresh={rules.refresh}
                     onSupersede={(r) => setDrawer({ mode: "supersede", rule: r })} />
        )}
        {view === "exceptions" && (
          <ExceptionsView data={exceptions.data} period={period} isCFO={isCFO}
                          businesses={rules.data?.businesses || []}
                          refresh={() => { exceptions.refresh(); rules.refresh(); }}
                          onCreateRule={(txn) => setDrawer({ mode: "exception", prefill: txn })} />
        )}
        {view === "drivers" && (
          <DriversView data={drivers.data} isCFO={isCFO}
                       businesses={rules.data?.businesses || []}
                       refresh={drivers.refresh} />
        )}
        {view === "recon" && <ReconView data={recon.data} period={period} />}
        {view === "partner" && (
          <PartnerView data={partner.data} period={period} businessId={partnerId} />
        )}
      </StatePanel>

      {isCFO && view === "rules" && (
        <button onClick={() => setDrawer({ mode: "new" })}
          style={{ marginTop: 16, fontFamily: F.body, fontSize: 12.5, fontWeight: 600,
                   color: L.paper, background: L.deep, border: "none", borderRadius: 9,
                   padding: "8px 15px", cursor: "pointer" }}>
          New allocation rule
        </button>
      )}

      <RuleDrawer open={!!drawer} mode={drawer?.mode || "new"} rule={drawer?.rule || null}
                  prefill={drawer?.prefill || null}
                  businesses={rules.data?.businesses || []}
                  tiers={rules.data?.meta?.tiers || []}
                  onClose={() => setDrawer(null)}
                  onSaved={() => { rules.refresh(); exceptions.refresh(); recon.refresh(); }} />

      {/* Inline styles cannot carry a media query, so the responsive rules live here and the
          grids below are given class names to be overridden by. The ledger grids are the whole
          reason: six columns at 375px is not a narrow table, it is an unreadable one, and the
          repo has a family of bugs from exactly this (a grid needs `minmax(0,...)` or `1fr`
          rather than `auto` to be allowed to shrink at all). */}
      <style>{`
        /* The grid templates live HERE, not in an inline style, and that is the whole point.
           An inline style beats a stylesheet rule on specificity, so a media query cannot
           override one: the first version set these inline and the six-column ledger stayed
           six columns at phone width, with three tracks empty and the pool name crushed to
           62px. The build passed, the media queries were present and correct, and the layout
           was still broken. Only looking at it at 375px found that. */
        .al-rules-row {
          grid-template-columns: 28px minmax(0,2fr) minmax(0,1.3fr) repeat(3,minmax(0,1fr));
        }
        .al-recon-row { grid-template-columns: minmax(0,2fr) repeat(4, minmax(0,1fr)); }
        .al-partner-row { grid-template-columns: minmax(0,2fr) repeat(2, minmax(0,1fr)); }
        .al-partner-figs { grid-template-columns: repeat(5, minmax(0,1fr)); }
        /* The driver commit form: entity, how it is answered, the number. The track count
           never changes with width, only the sizes. Dropping a track while the row still
           renders three children is how this repo's min-size bugs happen: the third child
           wraps into a track meant for one thing and the row overflows. */
        .al-drv-row {
          display: grid; gap: 8px; align-items: center;
          grid-template-columns: minmax(0,1.5fr) minmax(0,1fr) 104px;
        }
        /* A bare inline span shrinks to its text, so a right-aligned column header sits above
           a number flush LEFT. The wrapper is the grid item, so the alignment belongs on it --
           and HERE rather than inline, so the narrow-width display:none below can still win.
           (No backticks in this block: it is a template literal, and one ends the string.)
           Both ledgers carry the same wrapper and had the same misalignment.

           .al-rules-row was LEFT OUT of this list, and that was U1: on the Rules ledger the
           To partners figure sat against In the books and the Retained figure sat under the
           To partners header, so a retained 1,666.67 read as 1,666.67 sent to partners. The
           comment above described the bug on the other two ledgers while the third still had
           it. Listing the third is the smaller half of the fix; .al-money below is the half
           that stops it recurring, because every figure now right-aligns itself and no new
           wrapper anywhere can undo it. */
        .al-rules-row > .al-hide-narrow,
        .al-partner-row > .al-hide-narrow,
        .al-recon-row > .al-hide-narrow { display: block; text-align: right; }
        /* Every Money on this page. Here and not inline FOR THE SAME REASON the grid
           templates are: an inline display beats a media query, so an inline block here
           would defeat the display:none rules below that drop these columns at 820 and 520. */
        .al-money { display: block; text-align: right; }
        @media (max-width: 900px) {
          .al-cards { grid-template-columns: repeat(2, minmax(0,1fr)); }
        }
        @media (max-width: 820px) {
          .al-rules-row { grid-template-columns: 22px minmax(0,1.6fr) minmax(0,1fr); }
          .al-rules-row > .al-hide-narrow { display: none; }
          .al-recon-row { grid-template-columns: minmax(0,1.6fr) minmax(0,1fr) minmax(0,1fr); }
          .al-recon-row > .al-hide-narrow { display: none; }
          .al-partner-figs { grid-template-columns: repeat(3, minmax(0,1fr)); }
        }
        @media (max-width: 520px) {
          .al-cards { grid-template-columns: minmax(0,1fr); }
          .al-rules-row { grid-template-columns: 22px minmax(0,1fr); }
          .al-recon-row { grid-template-columns: minmax(0,1fr) minmax(0,1fr); }
          .al-partner-row { grid-template-columns: minmax(0,1.4fr) minmax(0,1fr); }
          .al-partner-row > .al-hide-narrow { display: none; }
          .al-partner-figs { grid-template-columns: repeat(2, minmax(0,1fr)); }
          .al-drv-row { grid-template-columns: minmax(0,1fr) minmax(0,0.9fr) 72px; }
        }
      `}</style>
    </div>
  );
}
