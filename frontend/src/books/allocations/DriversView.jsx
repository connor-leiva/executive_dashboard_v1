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


function ValueRow({ v, meta, businesses }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.4fr) minmax(0,1fr) auto",
                  gap: 10, alignItems: "center", padding: "9px 14px",
                  borderBottom: `1px solid ${L.ruleLine}` }}>
      <Entity id={v.business_id} businesses={businesses} />
      <span style={{ fontFamily: F.body, fontSize: 11.5, color: L.muted }}>
        {valueKindLabel(meta, v.value_kind)}
      </span>
      <span style={{ ...NUM, fontSize: 12.5, color: L.body }}>
        {v.value == null ? "the rest" : v.value}
      </span>
    </div>
  );
}

const FIELD = { fontFamily: F.num, fontVariantNumeric: "tabular-nums", fontSize: 12.5,
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
function CommitForm({ driver, meta, businesses, month, prefill, isCFO, onCommitted }) {
  const split = driver.kind === "split";
  const subjects = split ? (driver.subjects || []) : [""];
  const kinds = driver.value_kinds || ["share"];

  const seed = () => {
    const out = {};
    for (const v of prefill?.rows || []) {
      out[`${v.subject || ""}|${v.business_id}`] = { kind: v.value_kind, value: v.value ?? "" };
    }
    return out;
  };
  const [cells, setCells] = useState(seed);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  const key = (subject, bid) => `${subject || ""}|${bid}`;
  const cell = (subject, bid) => cells[key(subject, bid)] || { kind: kinds[0], value: "" };
  const set = (subject, bid, patch) =>
    setCells((c) => ({ ...c, [key(subject, bid)]: { ...cell(subject, bid), ...patch } }));

  // A row counts as filled when it carries a number, or when it is the remainder, which is a
  // claim on what is left and so has no number by definition.
  const filled = (c) => c.kind === "remainder" || String(c.value ?? "").trim() !== "";

  const rowsFor = (subject) => (businesses || [])
    .map((b) => ({ b, c: cell(subject, b.id) }))
    .filter(({ c }) => filled(c));

  const shareTotal = (subject) => rowsFor(subject)
    .filter(({ c }) => c.kind === "share")
    .reduce((t, { c }) => t + (Number(c.value) || 0), 0);

  // Only meaningful where the whole subject is shares: mixed with a flat amount or a remainder,
  // the shares are not supposed to reach 1 (5.4 applies them in order against the pool).
  const pureShares = (subject) => {
    const rows = rowsFor(subject);
    return rows.length > 0 && rows.every(({ c }) => c.kind === "share");
  };
  const offBy = (subject) => pureShares(subject)
    && Math.abs(shareTotal(subject) - 1) > 0.00005;

  const payload = () => subjects.flatMap((subject) =>
    rowsFor(subject).map(({ b, c }) => ({
      subject: split ? subject : null,
      business_id: b.id,
      value_kind: c.kind,
      value: c.kind === "remainder" ? null : String(c.value).trim(),
    })));

  const rows = payload();
  const bad = subjects.filter(offBy);

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      await postJSON(`/books/allocations/drivers/${driver.id}/commits`,
                     { period: month, rows });
      onCommitted();
    } catch (e) {
      setErr(e?.message || "That did not commit.");
    } finally {
      setBusy(false);
    }
  };

  if (!isCFO) return null;
  if (split && !subjects.length) {
    return (
      <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.body }}>
        No rule reads this driver yet, so there is nobody to answer for. A driven rule names the
        person it is about, and this form follows that list.
      </div>
    );
  }

  return (
    <div style={{ background: L.controls, border: `1px solid ${L.cardEdge}`, borderRadius: 11,
                  padding: "12px 14px" }}>
      <div style={{ ...EYEBROW, marginBottom: 6 }}>
        {prefill?.from_month ? `Starting from ${prefill.from_month}` : `Answer ${month}`}
      </div>
      <div style={{ fontFamily: F.body, fontSize: 12.5, color: L.body, marginBottom: 12 }}>
        {prefill?.from_month
          ? `Last committed month, offered to start from. Change anything that is different this
             month: nothing is allocated until you commit it for ${month}.`
          : `Nothing has been answered for this driver yet. Fill in what applies and commit it;
             a row left blank is not submitted.`}
      </div>

      {subjects.map((subject) => (
        <div key={subject} style={{ marginBottom: 12 }}>
          {split && (
            <div style={{ display: "flex", justifyContent: "space-between", gap: 10,
                          alignItems: "baseline", flexWrap: "wrap", marginBottom: 4 }}>
              <span style={{ fontFamily: F.body, fontSize: 12.5, fontWeight: 600,
                             color: L.deep }}>{subject}</span>
              {pureShares(subject) && (
                <span style={{ ...NUM, fontSize: 11.5,
                               color: offBy(subject) ? L.flagInk : L.muted }}>
                  {offBy(subject) ? "shares add up to " : "adds up to "}
                  {shareTotal(subject).toFixed(4)}
                </span>
              )}
            </div>
          )}
          {(businesses || []).map((b) => {
            const c = cell(subject, b.id);
            return (
              <div key={b.id} className="al-drv-row" style={{ padding: "5px 0" }}>
                <Entity id={b.id} businesses={businesses} />
                <select value={c.kind} aria-label={`How ${b.name} is answered`}
                  onChange={(e) => set(subject, b.id, { kind: e.target.value })}
                  style={{ fontFamily: F.body, fontSize: 11.5, color: L.body,
                           background: L.paper, border: `1px solid ${L.cardEdge}`,
                           borderRadius: 8, padding: "4px 6px", minWidth: 0 }}>
                  {kinds.map((k) => (
                    <option key={k} value={k}>{valueKindLabel(meta, k)}</option>
                  ))}
                </select>
                <input value={c.kind === "remainder" ? "" : c.value}
                  disabled={c.kind === "remainder"}
                  onChange={(e) => set(subject, b.id, { value: e.target.value })}
                  inputMode="decimal"
                  placeholder={c.kind === "remainder" ? "the rest"
                    : (c.kind === "share" ? "0 to 1" : "")}
                  aria-label={`${b.name}${subject ? `, ${subject}` : ""}`}
                  style={FIELD} />
              </div>
            );
          })}
        </div>
      ))}

      {!!bad.length && (
        <div style={{ fontFamily: F.body, fontSize: 12, color: L.flagInk, marginBottom: 8 }}>
          {bad.join(", ")}: the shares have to add up to 1. They are applied as a fraction of
          the whole pool, so a total above or below it allocates more or less than there is.
        </div>
      )}
      {err && (
        <div style={{ fontFamily: F.body, fontSize: 12, color: L.flagInk, marginBottom: 8 }}>
          {err}
        </div>
      )}

      <button onClick={submit} disabled={busy || !rows.length || !!bad.length}
        style={{ fontFamily: F.body, fontSize: 12.5, fontWeight: 600,
                 color: rows.length && !bad.length ? L.paper : L.muted,
                 background: rows.length && !bad.length ? L.deep : L.track,
                 border: "none", borderRadius: 9, padding: "7px 14px",
                 cursor: busy || !rows.length || bad.length ? "default" : "pointer" }}>
        {busy ? "Committing" : `Commit ${rows.length || "nothing"} for ${month}`}
      </button>
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

/* The month the picker opens on. It was a literal `${year}-07-01`, so the card opened on July
   whatever month it was, and somebody looking at November had to notice and change it before
   anything they did applied to the month they meant. Fixed month literals are a known bug
   family in this repo. */
function openingMonth(year) {
  const now = new Date();
  return `${year}-${MONTHS[now.getFullYear() === year ? now.getMonth() : 0]}-01`;
}

function DriverCard({ driver, meta, businesses, isCFO, year }) {
  const [month, setMonth] = useState(() => openingMonth(year));
  const { data, loading, refresh } = useAllocDriverMonth(driver.id, month);

  const commit = data?.commit;
  const prefill = data?.prefill;

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
          {/* Keyed on the month so switching months re-seeds the form from that month's own
              prefill, rather than carrying the previous month's typing across. */}
          <CommitForm key={month} driver={driver} meta={meta} businesses={businesses} month={month}
                      prefill={prefill} isCFO={isCFO} onCommitted={refresh} />
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

export default function DriversView({ data, isCFO, businesses, refresh,
                                      year = new Date().getFullYear() }) {
  const meta = data?.meta || {};
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
        <DriverCard key={d.id} driver={d} meta={meta} businesses={businesses} isCFO={isCFO}
                    year={year} />
      ))}
    </div>
  );
}
