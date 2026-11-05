/* Allocation rules (ALLOCATION-RULES-SPEC 7.1, 7.3).

   The shell: a segmented sub-view over Rules, Exceptions, Drivers and Reconciliation, following
   `BooksPayables.jsx` SegNav. Everything below reads the payload and renders it. NO ARITHMETIC
   LIVES HERE, the same rule the Statement states about itself: every figure on screen is the
   server's, because two places computing one number is how they come to disagree.

   No raw hex and no font family names anywhere in this folder: colours come from LEDGER and
   type from LEDGER_FONT, and theme.js says the folder is grepped for both. */
import { useEffect, useState } from "react";
import { LEDGER as L, LEDGER_FONT as F } from "../theme.js";
import { StatePanel } from "./ui.jsx";
import { useAllocRules, useAllocExceptions, useAllocDrivers, useAllocRecon,
         useAllocPartner, useAllocSummary } from "./useBooks.js";
import RulesView from "./allocations/RulesView.jsx";
import ExceptionsView from "./allocations/ExceptionsView.jsx";
import DriversView from "./allocations/DriversView.jsx";
import ReconView from "./allocations/ReconView.jsx";
import PartnerView, { EntityPicker } from "./allocations/PartnerView.jsx";
import RuleDrawer from "./allocations/RuleDrawer.jsx";
import { DarkBar, ViewTabs, NeedsYou } from "./allocations/Shell.jsx";
import { PageFrame, Skeleton } from "./allocations/controls.jsx";

/* The month the page is on, remembered for the session.
 *
 * NOT the dashboard's period (D1). Allocation work is month-grained and the dashboard period
 * is built for KPIs: `mtd` on the 4th showed four days, so 39 of 46 rules read as doing
 * nothing (U9). And never written back to the dashboard's own selector -- two controls
 * setting one value is how a page starts disagreeing with itself.
 */
const KEY = "alloc.month";

function useAllocMonth(fallback, months) {
  const [month, setMonth] = useState(() => {
    try { return sessionStorage.getItem(KEY) || null; } catch { return null; }
  });
  // The default comes from the SERVER and arrives after the first render, so it is adopted
  // once, and only when the reader has not already chosen (0.1 item 1 and item 6).
  //
  // A REMEMBERED MONTH THE SERVER NO LONGER OFFERS IS DROPPED. A `<select>` whose value
  // matches no option silently displays the FIRST one while the state holds the old value,
  // so the control said January while the page showed September and nothing was wrong on
  // either side on its own. Reachable in production whenever the option list changes under
  // a live session: the fiscal year rolls over, or somebody switches workspace.
  useEffect(() => {
    if (!fallback) return;
    const known = !months?.length || month === "ytd" || months.includes(month);
    if (!month || !known) {
      setMonth(fallback);
      try { sessionStorage.setItem(KEY, fallback); } catch { /* private mode */ }
    }
  }, [month, fallback, months]);
  const choose = (m) => {
    setMonth(m);
    try { sessionStorage.setItem(KEY, m); } catch { /* private mode: the session is enough */ }
  };
  return [month || fallback || null, choose];
}

export default function BooksAllocations({ period = "mtd", isCFO = false }) {
  const [view, setView] = useState("rules");
  // One drawer, three entry points (7.4). `mode` says which, so the drawer does not have to
  // guess from whether `rule` or `prefill` happens to be set.
  const [drawer, setDrawer] = useState(null);
  // Which entity the Partner view is answering for. Held HERE rather than inside that
  // view so leaving the tab and coming back does not silently reset to no entity.
  const [partnerId, setPartnerId] = useState(null);
  // Which entity filters the ledger, and which driver the strip sent us to.
  const [entity, setEntity] = useState("");
  const [driverId, setDriverId] = useState(null);

  // The summary is asked for the dashboard period only until the month is known; everything
  // else waits for the month, so no view ever renders a figure for a span the control is not
  // showing. One request answers the strip, the badges and the control's options.
  const summary = useAllocSummary(period);
  const [month, setMonth] = useAllocMonth(summary.data?.default_month,
                                          summary.data?.months);
  const span = month || period;
  const monthSummary = useAllocSummary(span);

  const rules = useAllocRules(span);
  const exceptions = useAllocExceptions(span);
  const drivers = useAllocDrivers();
  const recon = useAllocRecon(span);
  const partner = useAllocPartner(partnerId, span);

  const sum = monthSummary.data;
  // The badge counts come from the summary, not from counting rows: a view that has not
  // loaded yet has no rows, and a badge that reads zero until its tab is opened is worse
  // than no badge (7.5).
  const counts = {
    exceptions: sum?.unclaimed_count || 0,
    recon: sum?.pending_rules || 0,
    drivers: (sum?.drivers || []).filter((d) => !d.committed).length || 0,
  };

  const go = (next, driver = null) => {
    setView(next);
    if (driver) setDriverId(driver);
  };

  const active = { rules, exceptions, drivers, recon, partner }[view];

  return (
    <div>
      <PageFrame>
        <DarkBar businesses={rules.data?.businesses || []} entity={entity}
                 onEntity={setEntity} month={month}
                 months={sum?.months || summary.data?.months || []} onMonth={setMonth} />
        <ViewTabs view={view} setView={setView} counts={counts} />
        <NeedsYou summary={sum} month={month} go={go} />

        <div style={{ padding: "22px clamp(14px,3vw,26px) 26px", display: "grid", gap: 16 }}>
          {/* OUTSIDE StatePanel on purpose. StatePanel replaces its children with an error
              card, and the partner route answers 409 for an unmapped account or a failed
              tie-out, so a picker rendered inside it disappears exactly when somebody needs
              it to choose a different entity. Retry would only ask for the failing one. */}
          {view === "partner" && (
            <EntityPicker businesses={rules.data?.businesses || []} value={partnerId}
                          onPick={setPartnerId} />
          )}

          {active.loading && !active.data ? <Skeleton /> : (
            <StatePanel loading={false} error={active.error}
                        retry={active.retry} empty={false}>
              {view === "rules" && (
                <RulesView data={rules.data} month={month} entity={entity} isCFO={isCFO}
                           refresh={rules.refresh}
                           onNew={() => setDrawer({ mode: "new" })}
                           onSupersede={(r) => setDrawer({ mode: "supersede", rule: r })} />
              )}
              {view === "exceptions" && (
                <ExceptionsView data={exceptions.data} month={month} isCFO={isCFO}
                                businesses={rules.data?.businesses || []}
                                refresh={() => { exceptions.refresh(); rules.refresh(); }}
                                onCreateRule={(txn, group, keepOn) =>
                                  setDrawer({ mode: "exception", prefill: txn, group,
                                              keepOn: !!keepOn })} />
              )}
              {view === "drivers" && (
                <DriversView data={drivers.data} isCFO={isCFO} month={month}
                             selected={driverId} onSelect={setDriverId}
                             businesses={rules.data?.businesses || []}
                             refresh={() => { drivers.refresh(); monthSummary.refresh(); }} />
              )}
              {view === "recon" && <ReconView data={recon.data} month={month} go={go} />}
              {view === "partner" && (
                <PartnerView data={partner.data} period={span} businessId={partnerId} />
              )}
            </StatePanel>
          )}
        </div>
      </PageFrame>
      {/* New rule used to render HERE, after StatePanel and below all 46 rows, so on a real
          tenant there was no way to create a rule without scrolling past the whole ledger
          (U10). It lives in the Rules toolbar now. */}

      <RuleDrawer open={!!drawer} mode={drawer?.mode || "new"} rule={drawer?.rule || null}
                  prefill={drawer?.prefill || null} group={drawer?.group || null}
                  keepOn={!!drawer?.keepOn} rules={rules.data?.applied || []}
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
        /* FOUR tracks, four children: Rule, In the books, Resolved, Not resolved.
           Variance left the view in Phase 6, so a fifth track would be an empty
           column and the figures would sit under the wrong headers again. */
        .al-recon-row { grid-template-columns: minmax(0,2fr) repeat(3, minmax(0,1fr)); }
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
        /* A figure INSIDE a sentence. The rule above makes every money figure a
           right-aligned block, which is what a ledger column needs and what puts a number
           in running text on its own line with the words under it. Two class selectors, so
           this wins over the single one above without an inline style that a media query
           could not then beat.
           (Still no backticks anywhere in this block. One of them ends the template
           literal, and the text after it parses as JS: a comment written here with
           backticks around a class name became a property access minus an undefined
           variable, which BUILT CLEANLY and threw a blank page at runtime.) */
        .al-money.al-inline { display: inline; text-align: inherit; }

        /* The driver matrix. Below 660 it stops being a table: the header row goes, each
           subject becomes a card, and the entity inputs sit in a two-column grid with the
           entity NAME above each one -- because a column header a reader has scrolled past
           is not a label (6.3, 5.3). The track count still matches the children at every
           width; that is the bug family this file's grid comment is about. */
        .al-mx-head { }
        @media (max-width: 660px) {
          .al-mx { grid-template-columns: minmax(0,1fr) !important; gap: 8px !important; }
          .al-mx > span { justify-content: flex-start !important; }
          .al-mx-head { display: none !important; }
          .al-mx-wrap {
            border: 1px solid ${L.ruleInner}; border-radius: 11; margin-bottom: 10px;
            padding: 4px 10px;
          }
          .al-mx-cells {
            display: grid !important; grid-template-columns: repeat(2, minmax(0,1fr));
            gap: 8px;
          }
          .al-mx-cell-label { display: block !important; }
        }
        .al-mx-cell-label { display: none; }

        /* INTERACTION STATES (9.5). The page had a pointer cursor and nothing else: no
           hover, no visible keyboard focus, no pressed state (U24). A control with no focus
           ring cannot be used without a mouse, which is not a polish item. */
        .al-ctl:focus-visible {
          outline: 2px solid ${L.accentInk}; outline-offset: 2px; border-radius: 8px;
        }
        .al-btn-primary:hover:not(:disabled) { background: ${L.deepHover} !important;
                                               border-color: ${L.deepHover} !important; }
        .al-btn-secondary:hover:not(:disabled) { border-color: ${L.accentInk} !important;
                                                 color: ${L.accentInk} !important; }
        .al-btn-quiet:hover:not(:disabled) { color: ${L.deep} !important; }
        .al-chip:hover { border-color: ${L.accentInk}; }
        .al-tab:hover { color: ${L.deep}; }
        .al-strip-item:hover { text-decoration: underline; }
        .al-row-hover:hover { background: ${L.totalBand}; }
        .al-row-hover.al-t3:hover { background: ${L.flagBgHover}; }

        /* Three bars where a row will be, rather than the words "Reading this month." */
        .al-skel { animation: al-pulse 1.4s ease-in-out infinite; }
        @keyframes al-pulse { 0%,100% { opacity: 1; } 50% { opacity: .45; } }

        /* 9.5: everything above is motion or hint, so all of it goes. */
        @media (prefers-reduced-motion: reduce) {
          .al-skel { animation: none; }
        }
        @media (max-width: 900px) {
          .al-cards { grid-template-columns: repeat(2, minmax(0,1fr)); }
        }
        @media (max-width: 820px) {
          .al-rules-row { grid-template-columns: 22px minmax(0,1.6fr) minmax(0,1fr); }
          .al-rules-row > .al-hide-narrow { display: none; }
          /* Resolved hides here, so three children and three tracks. */
          .al-recon-row { grid-template-columns: minmax(0,1.6fr) minmax(0,1fr) minmax(0,1fr); }
          .al-recon-row > .al-hide-narrow { display: none; }
          .al-partner-figs { grid-template-columns: repeat(3, minmax(0,1fr)); }
        }
        @media (max-width: 520px) {
          .al-cards { grid-template-columns: minmax(0,1fr); }
          /* THREE tracks, because three children are still visible here: the marker, the
             name, and In the books. The comment above says dropping a track while the row
             still renders the child is how this repo's min-size bugs happen, and these two
             rules were doing exactly that -- at 375px the figure wrapped into an implicit
             row and took the 22px marker track, so "1,000.00" rendered 22px wide. Measured,
             not guessed: 2 tracks against 3 visible children. */
          .al-rules-row { grid-template-columns: 22px minmax(0,1fr) 84px; }
          .al-recon-row { grid-template-columns: minmax(0,1fr) 76px 76px; }
          .al-partner-row { grid-template-columns: minmax(0,1.4fr) minmax(0,1fr); }
          .al-partner-row > .al-hide-narrow { display: none; }
          .al-partner-figs { grid-template-columns: repeat(2, minmax(0,1fr)); }
          .al-drv-row { grid-template-columns: minmax(0,1fr) minmax(0,0.9fr) 72px; }
        }
      `}</style>
    </div>
  );
}
