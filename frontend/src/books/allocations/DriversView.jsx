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
import { postJSON } from "../../api";
import { useAllocDriverMonth } from "../useBooks.js";

const NUM = { fontFamily: F.num, fontVariantNumeric: "tabular-nums", textAlign: "right",
              whiteSpace: "nowrap" };
const EYEBROW = { fontFamily: F.num, fontSize: 10, letterSpacing: ".14em",
                  textTransform: "uppercase", color: L.faint };

const MONTHS = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"];
const MONTH_LABEL = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function Entity({ id, businesses }) {
  const b = (businesses || []).find((x) => x.id === id);
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, minWidth: 0 }}>
      <span aria-hidden style={{ width: 7, height: 7, borderRadius: "50%", flex: "0 0 auto",
                                 background: b?.accent || L.faint }} />
      <span style={{ fontFamily: F.body, fontSize: 12.5, color: L.body, overflow: "hidden",
                     textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {b?.name || "Unknown entity"}
      </span>
    </span>
  );
}

const KIND_WORD = {
  share: "share of the pool",
  amount: "a flat amount",
  remainder: "whatever the others leave",
  count: "a count",
};

function ValueRow({ v, businesses }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.4fr) minmax(0,1fr) auto",
                  gap: 10, alignItems: "center", padding: "9px 14px",
                  borderBottom: `1px solid ${L.ruleLine}` }}>
      <Entity id={v.business_id} businesses={businesses} />
      <span style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted }}>
        {KIND_WORD[v.value_kind] || v.value_kind}
      </span>
      <span style={{ ...NUM, fontSize: 12.5, color: L.body }}>
        {v.value == null ? "the rest" : v.value}
      </span>
    </div>
  );
}

function NotSet({ month }) {
  return (
    <div style={{ background: L.flagBg, border: `1px solid ${L.flagEdge}`, borderRadius: 11,
                  padding: "14px 16px" }}>
      <div style={{ fontFamily: F.body, fontSize: 13, color: L.flagInk, fontWeight: 600 }}>
        Not set for {month}
      </div>
      <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.body, marginTop: 6 }}>
        Nothing is allocated by this driver until the month is committed. It is not carried
        forward from last month, because a number nobody looked at would read as one somebody
        decided.
      </div>
    </div>
  );
}

function DriverCard({ driver, businesses, isCFO, year }) {
  const [month, setMonth] = useState(`${year}-07-01`);
  const { data, loading, refresh } = useAllocDriverMonth(driver.id, month);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const commit = data?.commit;
  const prefill = data?.prefill;

  const commitPrefill = async () => {
    setBusy(true);
    setErr(null);
    try {
      await postJSON(`/books/allocations/drivers/${driver.id}/commits`,
                     { period: month, rows: prefill.rows });
      refresh();
    } catch (e) {
      setErr(e?.message || "That did not commit.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ background: L.paper, border: `1px solid ${L.cardEdge}`, borderRadius: 14,
                  overflow: "hidden" }}>
      <div style={{ padding: "13px 16px", background: L.colHead,
                    borderBottom: `1px solid ${L.ruleHeader}` }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12,
                      alignItems: "baseline", flexWrap: "wrap" }}>
          <span>
            <span style={{ fontFamily: F.body, fontSize: 13.5, fontWeight: 600,
                           color: L.deep }}>{driver.label}</span>
            <span style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted,
                           display: "block" }}>
              {driver.kind === "split"
                ? "Answered per person, per entity"
                : "Answered per entity, and each share is its count over the total"}
              {driver.unit ? ` · ${driver.unit}` : ""}
            </span>
          </span>
          <select value={month} onChange={(e) => setMonth(e.target.value)}
            style={{ fontFamily: F.body, fontSize: 12.5, color: L.body, background: L.paper,
                     border: `1px solid ${L.cardEdge}`, borderRadius: 9, padding: "5px 9px" }}>
            {MONTHS.map((m, i) => (
              <option key={m} value={`${year}-${m}-01`}>{MONTH_LABEL[i]} {year}</option>
            ))}
          </select>
        </div>
      </div>

      {loading && (
        <div style={{ padding: "16px", fontFamily: F.body, fontSize: 12, color: L.muted }}>
          Reading this month.
        </div>
      )}

      {!loading && !commit && (
        <div style={{ padding: 16, display: "grid", gap: 12 }}>
          <NotSet month={month} />
          {prefill?.from_month && (
            <div style={{ background: L.controls, border: `1px solid ${L.cardEdge}`,
                          borderRadius: 11, padding: "12px 14px" }}>
              <div style={{ ...EYEBROW, marginBottom: 6 }}>From {prefill.from_month}</div>
              <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.body,
                            marginBottom: 10 }}>
                Last committed month, offered to start from. Nothing is allocated until this is
                committed for {month}.
              </div>
              {(prefill.rows || []).map((v, i) => (
                <ValueRow key={i} v={v} businesses={businesses} />
              ))}
              {isCFO && !!prefill.rows?.length && (
                <button onClick={commitPrefill} disabled={busy}
                  style={{ marginTop: 10, fontFamily: F.body, fontSize: 12.5, fontWeight: 600,
                           color: L.paper, background: L.deep, border: "none",
                           borderRadius: 9, padding: "7px 14px",
                           cursor: busy ? "default" : "pointer" }}>
                  {busy ? "Committing" : `Commit these figures for ${month}`}
                </button>
              )}
            </div>
          )}
          {err && (
            <div style={{ fontFamily: F.body, fontSize: 12, color: L.flagInk }}>{err}</div>
          )}
        </div>
      )}

      {!loading && commit && (
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 10,
                        padding: "9px 14px", background: L.totalBand,
                        borderBottom: `1px solid ${L.ruleLine}` }}>
            <span style={{ fontFamily: F.body, fontSize: 11.5, color: L.body }}>
              Committed {commit.committed_at ? commit.committed_at.slice(0, 10) : ""}
            </span>
            <span style={{ fontFamily: F.num, fontSize: 10, letterSpacing: ".08em",
                           textTransform: "uppercase", color: L.deep }}>
              {commit.is_current ? "Current" : "Superseded"}
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
              {vals.map((v, i) => <ValueRow key={i} v={v} businesses={businesses} />)}
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

export default function DriversView({ data, isCFO, businesses, refresh, year = 2026 }) {
  const drivers = data?.drivers || [];

  if (!drivers.length) {
    return (
      <div style={{ background: L.card, border: `1px solid ${L.cardEdge}`, borderRadius: 14,
                    padding: 28, textAlign: "center" }}>
        <div style={{ fontFamily: F.body, fontSize: 13.5, color: L.deep, fontWeight: 600 }}>
          No drivers defined.
        </div>
        <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.muted, marginTop: 6 }}>
          A driver is a question answered once a month, such as how a person split their time or
          how many members each entity had. A Tier 3 rule reads one to decide its split.
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gap: 14 }}>
      {drivers.map((d) => (
        <DriverCard key={d.id} driver={d} businesses={businesses} isCFO={isCFO} year={year} />
      ))}
    </div>
  );
}
