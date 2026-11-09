/* Drivers (ALLOCATION-RULES-SPEC 4.3, 7.3).

   The monthly numbers a driven rule reads. Per driver: the month, a per-subject table for a
   split driver or a per-entity table for a count driver, the status of each row, and Commit.

   NOT SET USES THE GOLD WASH AND IS NOT AN ERROR (7.2). A month nobody has answered yet is a
   month nobody has answered yet; the engine reports `no_driver_values` and allocates nothing,
   which is the designed behaviour rather than a failure.

   The prefill is offered BESIDE the month's own values, never merged into them. A figure
   carried forward because nobody looked at it is indistinguishable on the Statement from one
   somebody decided, so the screen keeps the two apart exactly as the API does. */
import { useState } from "react";
import { LEDGER as L, LEDGER_FONT as F } from "../../theme.js";
import { valueKindLabel, fmtMonth, fmtDay } from "./words.js";
import { Button, Skeleton } from "./controls.jsx";
import DriverMatrix from "./DriverMatrix.jsx";
import { postJSON } from "../../api";
import { useAllocDriverMonth } from "../useBooks.js";

const NUM = { fontFamily: F.num, fontVariantNumeric: "tabular-nums", textAlign: "right",
              whiteSpace: "nowrap" };
const EYEBROW = { fontFamily: F.num, fontSize: 12,
                  color: L.faint };


function Entity({ id, businesses }) {
  const b = (businesses || []).find((x) => x.id === id);
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, minWidth: 0 }}>
      <span aria-hidden style={{ width: 7, height: 7, borderRadius: "50%", flex: "0 0 auto",
                                 background: b?.accent || L.faint }} />
      <span style={{ fontFamily: F.body, fontSize: 13, color: L.body, overflow: "hidden",
                     textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {b?.name || "Unknown entity"}
      </span>
    </span>
  );
}


function ValueRow({ v, meta, businesses }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.4fr) minmax(0,1fr) auto",
                  gap: 10, alignItems: "center", padding: "9px 14px",
                  borderBottom: `1px solid ${L.ruleLine}` }}>
      <Entity id={v.business_id} businesses={businesses} />
      <span style={{ fontFamily: F.body, fontSize: 12, color: L.muted }}>
        {valueKindLabel(meta, v.value_kind)}
      </span>
      {/* THE PERCENT, not the stored fraction. A committed 70% read back as "0.7000" is
          the same mismatch the input had, in the other direction: a reader comparing the
          screen with what they typed sees a different number (6.3, 7.6). `pct` comes from
          the server; the browser does not divide. */}
      <span style={{ ...NUM, fontSize: 13, color: L.body }}>
        {v.value_kind === "remainder" ? "the rest"
          : v.value_kind === "share" ? `${v.pct ?? ""}%`
          : v.value}
      </span>
    </div>
  );
}

const FIELD = { fontFamily: F.num, fontVariantNumeric: "tabular-nums", fontSize: 13,
                color: L.deep, background: L.paper, border: `1px solid ${L.cardEdge}`,
                borderRadius: 8, padding: "5px 8px", textAlign: "right", width: "100%",
                minWidth: 0 };

/* THE FORM THE FIRST MONTH NEEDS.

   The commit control used to live inside the "start from last month" card, which renders only
   when a previous commit exists. A driver with no history therefore showed the Not set panel
   and nothing else: no inputs, no button, no way forward, and its first month could never be
   committed. The same shape as the empty-library bug already fixed in this repo, where the only
   path to a thing ran through already having one.

   The prefill now seeds this form rather than being committed verbatim. That was the other half
   of it: last month's figures could be accepted whole or not at all, so somebody who needed
   November to differ from October had no way to say so, on the one screen whose entire purpose
   is that the month is decided rather than carried forward (D8). */
function DriverCard({ driver, meta, businesses, isCFO, month }) {
  const { data, loading, refresh } = useAllocDriverMonth(driver.id, month);

  const commit = data?.commit;
  const prefill = data?.prefill;
  const subjects = data?.subjects;
  const [changing, setChanging] = useState(false);

  const showMatrix = !loading && (!commit || changing);
  return (
    <div style={{ background: L.paper, border: `1px solid ${L.cardEdge}`, borderRadius: 14,
                  overflow: "hidden" }}>
      {/* NO HEADER HERE WHEN THE MATRIX IS OPEN. The matrix draws its own, with Commit at
          the right where the mockup has it; a card header above it repeated the driver's
          name and the month, and the two together were four stacked lines of chrome over
          a four-row table. */}
      {!showMatrix && (
        <div style={{ padding: "13px 16px", background: L.colHead,
                      borderBottom: `1px solid ${L.ruleHeader}` }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12,
                        alignItems: "baseline", flexWrap: "wrap" }}>
            <span>
              <span style={{ ...EYEBROW, display: "block", marginBottom: 3 }}>
                {driver.label}
              </span>
              <span style={{ fontFamily: F.body, fontSize: 13, color: L.muted,
                             display: "block" }}>
                {driver.kind === "split"
                  ? "One row per person, one column per entity."
                  : "One row, one column per entity."}
              </span>
            </span>
            <span style={{ fontFamily: F.body, fontSize: 13, color: L.bodyDim }}>
              {fmtMonth(month)}
            </span>
          </div>
        </div>
      )}

      {loading && <div style={{ padding: 16 }}><Skeleton rows={2} /></div>}

      {showMatrix && (
        <div style={{ padding: 16 }}>
          {/* Keyed on the month so switching months re-seeds the form from that month's own
              prefill, rather than carrying the previous month's typing across. */}
          <DriverMatrix key={`${month}:${changing}`} driver={driver} month={month}
                        businesses={businesses} subjects={subjects} prefill={prefill}
                        isCFO={isCFO} committed={!!commit}
                        onCommitted={() => { setChanging(false); refresh(); }} />
        </div>
      )}

      {!loading && commit && !changing && (
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 10,
                        padding: "9px 14px", background: L.totalBand,
                        borderBottom: `1px solid ${L.ruleLine}` }}>
            <span style={{ fontFamily: F.body, fontSize: 12, color: L.body }}>
              Committed {commit.committed_at ? fmtDay(commit.committed_at.slice(0, 10)) : ""}
              {commit.committed_by?.name ? ` by ${commit.committed_by.name}` : ""}
            </span>
            <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <span style={{ fontFamily: F.num, fontSize: 12,
                             color: L.deep }}>
                {commit.is_current ? "Current" : "Superseded"}
              </span>
              {/* Reopens the matrix seeded with what is committed, and saves a NEW commit
                  that supersedes it. Append-only: the old month keeps its figures and what
                  it was allocated on (6.3). */}
              {isCFO && (
                <Button kind="quiet" size="card" onClick={() => setChanging(true)}>
                  Change {fmtMonth(month)}
                </Button>
              )}
            </span>
          </div>
          {Object.entries(
            (commit.values || []).reduce((acc, v) => {
              const k = v.subject || "";
              (acc[k] = acc[k] || []).push(v);
              return acc;
            }, {}),
          ).map(([subject, vals]) => (
            <div key={subject}>
              {subject && (
                <div style={{ padding: "9px 14px 5px", fontFamily: F.body, fontSize: 12,
                              fontWeight: 600, color: L.deep }}>{subject}</div>
              )}
              {vals.map((v, i) => <ValueRow key={i} v={v} meta={meta} businesses={businesses} />)}
            </div>
          ))}
          {commit.note && (
            <div style={{ padding: "10px 14px", fontFamily: F.body, fontSize: 12,
                          color: L.muted }}>{commit.note}</div>
          )}
        </div>
      )}
    </div>
  );
}

export default function DriversView({ data, isCFO, businesses, refresh, month,
                                      selected = null, onSelect = () => {} }) {
  const meta = data?.meta || {};
  const drivers = data?.drivers || [];

  if (!drivers.length) {
    return (
      <div style={{ background: L.card, border: `1px solid ${L.cardEdge}`, borderRadius: 14,
                    padding: 28, textAlign: "center" }}>
        <div style={{ fontFamily: F.body, fontSize: 14, color: L.deep, fontWeight: 600 }}>
          No drivers defined.
        </div>
        <div style={{ fontFamily: F.body, fontSize: 13, color: L.muted, marginTop: 6 }}>
          A driver is a question answered once a month, such as how a person split their time or
          how many members each entity had. A Tier 3 rule reads one to decide its split.
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gap: 14 }}>
      {drivers.map((d) => (
        <DriverCard key={d.id} driver={d} meta={meta} businesses={businesses} isCFO={isCFO}
                    month={month} />
      ))}
    </div>
  );
}
