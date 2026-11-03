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
import { useAllocRules, useAllocExceptions, useAllocDrivers, useAllocRecon } from "./useBooks.js";
import RulesView from "./allocations/RulesView.jsx";
import ExceptionsView from "./allocations/ExceptionsView.jsx";
import DriversView from "./allocations/DriversView.jsx";
import ReconView from "./allocations/ReconView.jsx";
import RuleDrawer from "./allocations/RuleDrawer.jsx";

const VIEWS = [["rules", "Rules"], ["exceptions", "Exceptions"],
               ["drivers", "Drivers"], ["recon", "Reconciliation"]];

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
  const rules = useAllocRules(period);
  const exceptions = useAllocExceptions(period);
  const drivers = useAllocDrivers();
  const recon = useAllocRecon(period);

  // The badge counts are the two things somebody is waiting on: unclaimed cost, and rules that
  // resolved nothing. Both come from the payload; neither is derived by counting rows here.
  const counts = {
    exceptions: exceptions.data?.rows?.length || 0,
    recon: (recon.data?.rules || []).filter((r) => r.unresolved_amount).length || 0,
  };

  const active = { rules, exceptions, drivers, recon }[view];

  return (
    <div>
      <SegNav view={view} setView={setView} counts={counts} />
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
        @media (max-width: 900px) {
          .al-cards { grid-template-columns: repeat(2, minmax(0,1fr)); }
        }
        @media (max-width: 820px) {
          .al-rules-row { grid-template-columns: 22px minmax(0,1.6fr) minmax(0,1fr); }
          .al-rules-row > .al-hide-narrow { display: none; }
          .al-recon-row { grid-template-columns: minmax(0,1.6fr) minmax(0,1fr) minmax(0,1fr); }
          .al-recon-row > .al-hide-narrow { display: none; }
        }
        @media (max-width: 520px) {
          .al-cards { grid-template-columns: minmax(0,1fr); }
          .al-rules-row { grid-template-columns: 22px minmax(0,1fr); }
          .al-recon-row { grid-template-columns: minmax(0,1fr) minmax(0,1fr); }
        }
      `}</style>
    </div>
  );
}
